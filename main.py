import logging
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import EmailStr
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db, init_db
from email_service import send_application_email
from models import Application
from storage import CVTooLargeError, InvalidCVError, ValidatedCV, validate_cv_upload

logger = logging.getLogger(__name__)

app = FastAPI(title="Interview Application API")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.on_event("startup")
def create_tables() -> None:
    init_db()


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Invalid application data",
            "errors": errors,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    _request: Request, exc: HTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
        headers=exc.headers,
    )


def _error(message: str, status_code: int, application_id: Optional[int] = None):
    content = {"success": False, "message": message}
    if application_id is not None:
        content["application_id"] = application_id
    return JSONResponse(status_code=status_code, content=content)


def _matches_existing(
    application: Application,
    *,
    full_name: str,
    email: str,
    phone: str,
    position: str,
    cover_letter: Optional[str],
    cv_sha256: str,
) -> bool:
    return (
        application.full_name == full_name
        and application.email == email
        and application.phone == phone
        and application.position == position
        and application.cover_letter == cover_letter
        and application.cv_sha256 == cv_sha256
    )


def _claim_email_delivery(db: Session, application: Application) -> bool:
    claimed = (
        db.query(Application)
        .filter(
            Application.id == application.id,
            Application.email_status.in_(("pending", "failed")),
        )
        .update(
            {Application.email_status: "sending"},
            synchronize_session=False,
        )
    )
    db.commit()
    db.refresh(application)
    return claimed == 1


async def _deliver_application_email(
    db: Session, application: Application
) -> Optional[JSONResponse]:
    try:
        claimed = _claim_email_delivery(db, application)
    except SQLAlchemyError:
        db.rollback()
        return _error(
            "The application was saved, but its email status could not be updated.",
            503,
            application.id,
        )

    if not claimed:
        if application.email_status == "sent":
            return None
        return _error(
            "This application is already being processed. Its email was not sent again.",
            503,
            application.id,
        )

    try:
        await send_application_email(application)
    except Exception as exc:
        logger.error(
            "Application email delivery failed for application %s (%s)",
            application.id,
            type(exc).__name__,
        )
        application.email_status = "failed"
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            return _error(
                "The application was saved, but email delivery failed and its status "
                "could not be recorded. Contact the company with the application ID.",
                503,
                application.id,
            )
        return _error(
            "The application was saved, but its notification email could not be sent. "
            "If you submitted an Idempotency-Key, retry with that same key; "
            "otherwise contact the company with the application ID.",
            502,
            application.id,
        )

    application.email_status = "sent"
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return _error(
            "The application was saved, but email delivery could not be confirmed. "
            "Contact the company with the application ID before retrying.",
            503,
            application.id,
        )
    return None


@app.post("/applications", status_code=201)
async def create_application(
    full_name: str = Form(..., min_length=1, max_length=200),
    email: EmailStr = Form(...),
    phone: str = Form(..., min_length=1, max_length=40),
    position: str = Form(..., min_length=1, max_length=120),
    cover_letter: Optional[str] = Form(None, max_length=5000),
    cv: UploadFile = File(...),
    idempotency_key: Optional[str] = Header(
        None, alias="Idempotency-Key", max_length=255
    ),
    db: Session = Depends(get_db),
):
    full_name = full_name.strip()
    phone = phone.strip()
    position = position.strip()
    cover_letter = cover_letter.strip() if cover_letter else None
    if not full_name or not phone or not position:
        raise HTTPException(
            status_code=422,
            detail="Full name, phone, and position must not be blank.",
        )
    if any(
        ord(character) < 32 or ord(character) == 127
        for value in (full_name, phone, position)
        for character in value
    ):
        raise HTTPException(
            status_code=422,
            detail="Full name, phone, and position must not contain control characters.",
        )

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise HTTPException(
                status_code=422, detail="Idempotency-Key must not be blank."
            )

    try:
        validated_cv = await validate_cv_upload(cv)
    except CVTooLargeError:
        return _error("The CV exceeds the 5 MB file size limit.", 413)
    except InvalidCVError as exc:
        return _error(str(exc), 400)
    except OSError:
        logger.exception("Could not read an uploaded CV")
        return _error("The CV could not be read. Please try again.", 500)

    application: Optional[Application] = None
    try:
        if idempotency_key:
            application = (
                db.query(Application)
                .filter(Application.idempotency_key == idempotency_key)
                .first()
            )
            if application is not None:
                if not _matches_existing(
                    application,
                    full_name=full_name,
                    email=str(email),
                    phone=phone,
                    position=position,
                    cover_letter=cover_letter,
                    cv_sha256=validated_cv.sha256,
                ):
                    return _error(
                        "This Idempotency-Key was already used for different "
                        "application data.",
                        409,
                    )

        if application is None:
            application = Application(
                full_name=full_name,
                email=str(email),
                phone=phone,
                position=position,
                cover_letter=cover_letter,
                cv_data=validated_cv.data,
                cv_sha256=validated_cv.sha256,
                idempotency_key=idempotency_key,
                email_status="pending",
            )
            db.add(application)
            try:
                db.commit()
                db.refresh(application)
            except IntegrityError:
                db.rollback()
                if not idempotency_key:
                    raise
                application = (
                    db.query(Application)
                    .filter(Application.idempotency_key == idempotency_key)
                    .first()
                )
                if application is None or not _matches_existing(
                    application,
                    full_name=full_name,
                    email=str(email),
                    phone=phone,
                    position=position,
                    cover_letter=cover_letter,
                    cv_sha256=validated_cv.sha256,
                ):
                    return _error(
                        "This Idempotency-Key was already used for different "
                        "application data.",
                        409,
                    )
            except SQLAlchemyError:
                db.rollback()
                logger.exception("Could not save an application to the database")
                return _error(
                    "The application could not be saved to the database. "
                    "Please try again.",
                    503,
                )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not look up an idempotent application")
        return _error(
            "The application could not be processed because of a database error.",
            503,
        )

    email_error = await _deliver_application_email(db, application)
    if email_error is not None:
        return email_error

    status_code = 200 if application.idempotency_key else 201
    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "message": "Application received",
            "application_id": application.id,
        },
    )
