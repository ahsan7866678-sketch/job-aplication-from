import hashlib
import logging
import os
from typing import Generator

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base
from storage import resolve_storage_path

load_dotenv()
logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def _get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    required = ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            "Missing MySQL configuration in .env: " + ", ".join(missing)
        )

    try:
        port = int(os.getenv("MYSQL_PORT", "3306"))
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT must be a valid TCP port number.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("MYSQL_PORT must be between 1 and 65535.")

    url = URL.create(
        drivername="mysql+pymysql",
        username=os.environ["MYSQL_USER"],
        password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.environ["MYSQL_HOST"],
        port=port,
        database=os.environ["MYSQL_DATABASE"],
        query={"charset": "utf8mb4"},
    )
    _engine = create_engine(url, pool_pre_ping=True)
    _session_factory = sessionmaker(
        bind=_engine, autoflush=False, autocommit=False
    )
    return _engine


def init_db() -> None:
    engine = _get_engine()
    if "applications" in inspect(engine).get_table_names():
        _migrate_applications_table(engine)
    Base.metadata.create_all(bind=engine)
    _migrate_legacy_cv_files(engine)


def _migrate_applications_table(engine: Engine) -> None:
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("applications")
    }
    with engine.begin() as connection:
        for old_name, new_name, column_type in (
            ("name", "full_name", "VARCHAR(200)"),
            ("apply_for", "position", "VARCHAR(120)"),
        ):
            if old_name in columns and new_name in columns:
                raise RuntimeError(
                    "The applications table contains both "
                    f"{old_name!r} and {new_name!r} columns; resolve the "
                    "conflicting schema before starting the API."
                )
            if old_name in columns:
                connection.execute(
                    text(
                        "ALTER TABLE applications CHANGE COLUMN "
                        f"{old_name} {new_name} {column_type} NOT NULL"
                    )
                )
                columns.remove(old_name)
                columns.add(new_name)
            elif new_name not in columns:
                raise RuntimeError(
                    "The applications table is missing the expected "
                    f"{old_name!r} or {new_name!r} column."
                )

        new_columns = {
            "phone": "VARCHAR(40) NOT NULL DEFAULT ''",
            "cover_letter": "TEXT NULL",
            "cv_storage_path": "VARCHAR(500) NULL",
            "cv_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
            "cv_data": "LONGBLOB NULL",
            "idempotency_key": "VARCHAR(255) NULL",
            "email_status": "VARCHAR(20) NOT NULL DEFAULT 'pending'",
            "submitted_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        }
        for name, definition in new_columns.items():
            if name not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE applications ADD COLUMN "
                        f"{name} {definition}"
                    )
                )
                columns.add(name)

        connection.execute(
            text(
                "ALTER TABLE applications MODIFY COLUMN "
                "cv_storage_path VARCHAR(500) NULL"
            )
        )

    inspector = inspect(engine)
    unique_keys = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("applications")
    }
    unique_keys.update(
        tuple(index["column_names"])
        for index in inspector.get_indexes("applications")
        if index["unique"]
    )
    with engine.begin() as connection:
        if ("idempotency_key",) not in unique_keys:
            connection.execute(
                text(
                    "ALTER TABLE applications ADD CONSTRAINT "
                    "uq_applications_idempotency_key "
                    "UNIQUE (idempotency_key)"
                )
            )

        indexes = {
            index["name"]
            for index in inspector.get_indexes("applications")
        }
        if "ix_applications_email" not in indexes:
            connection.execute(
                text(
                    "ALTER TABLE applications ADD INDEX "
                    "ix_applications_email (email)"
                )
            )


def _migrate_legacy_cv_files(engine: Engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, cv_storage_path, cv_sha256 FROM applications "
                "WHERE cv_data IS NULL "
                "AND cv_storage_path IS NOT NULL "
                "AND cv_storage_path <> ''"
            )
        ).all()

    for application_id, storage_path, expected_sha256 in rows:
        path = resolve_storage_path(storage_path)
        try:
            pdf_data = path.read_bytes()
        except FileNotFoundError:
            logger.error(
                "Cannot migrate CV for application %s: source file is missing",
                application_id,
            )
            continue

        actual_sha256 = hashlib.sha256(pdf_data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"CV integrity check failed while migrating application "
                f"{application_id}; the local file was left unchanged."
            )

        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE applications "
                    "SET cv_data = :cv_data, cv_storage_path = NULL "
                    "WHERE id = :application_id AND cv_data IS NULL"
                ),
                {"cv_data": pdf_data, "application_id": application_id},
            )

        if result.rowcount != 1:
            continue

        with engine.connect() as connection:
            references = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM applications "
                    "WHERE cv_storage_path = :storage_path"
                ),
                {"storage_path": storage_path},
            )
        if references:
            continue

        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception(
                "CV for application %s was migrated to MySQL, "
                "but its local copy could not be removed",
                application_id,
            )


def get_db() -> Generator[Session, None, None]:
    try:
        _get_engine()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if _session_factory is None:
        raise HTTPException(
            status_code=503, detail="The database session is not configured."
        )

    db = _session_factory()
    try:
        yield db
    finally:
        db.close()
