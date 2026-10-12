from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(200), nullable=False)
    email = Column(String(320), nullable=False, index=True)
    phone = Column(String(40), nullable=False)
    position = Column(String(120), nullable=False)
    cover_letter = Column(Text, nullable=True)
    cv_storage_path = Column(String(500), nullable=True)
    cv_data = Column(LONGBLOB, nullable=True)
    cv_sha256 = Column(String(64), nullable=False)
    idempotency_key = Column(String(255), nullable=True, unique=True)
    email_status = Column(String(20), nullable=False, default="pending")
    submitted_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
