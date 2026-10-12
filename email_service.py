import os
from io import BytesIO

from dotenv import load_dotenv
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from starlette.datastructures import Headers, UploadFile as AttachmentUploadFile

from models import Application
from storage import resolve_storage_path

load_dotenv()


def _required_mail_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing email configuration in .env: {name}")
    return value


def _boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


async def send_application_email(application: Application) -> None:
    try:
        port = int(_required_mail_setting("MAIL_PORT"))
    except ValueError as exc:
        raise RuntimeError("MAIL_PORT must be a valid TCP port number.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("MAIL_PORT must be between 1 and 65535.")

    config = ConnectionConfig(
        MAIL_USERNAME=_required_mail_setting("MAIL_USERNAME"),
        MAIL_PASSWORD=_required_mail_setting("MAIL_PASSWORD"),
        MAIL_FROM=_required_mail_setting("MAIL_FROM"),
        MAIL_PORT=port,
        MAIL_SERVER=_required_mail_setting("MAIL_SERVER"),
        MAIL_STARTTLS=_boolean_setting("MAIL_STARTTLS", True),
        MAIL_SSL_TLS=_boolean_setting("MAIL_SSL_TLS", False),
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )
    recipient = _required_mail_setting("MAIL_TO")
    body = (
        "A new interview application has been received.\n\n"
        f"Name: {application.full_name}\n"
        f"Email: {application.email}\n"
        f"Phone: {application.phone}\n"
        f"Position: {application.position}\n\n"
        "Cover letter:\n"
        f"{application.cover_letter or '(Not provided)'}\n\n"
        f"Application ID: {application.id}"
    )
    if application.cv_data is not None:
        attachments = [
            AttachmentUploadFile(
                filename=f"application-{application.id}-cv.pdf",
                file=BytesIO(application.cv_data),
                headers=Headers({"content-type": "application/pdf"}),
            )
        ]
    elif application.cv_storage_path is not None:
        attachments = [str(resolve_storage_path(application.cv_storage_path))]
    else:
        raise RuntimeError(
            f"Application {application.id} does not have a stored CV."
        )

    message = MessageSchema(
        subject=f"Interview application: {application.position}",
        recipients=[recipient],
        body=body,
        subtype=MessageType.plain,
        attachments=attachments,
    )
    await FastMail(config).send_message(message)
