import os
from dataclasses import dataclass
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

from aiosmtplib import SMTP
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPDataError,
    SMTPException,
    SMTPRecipientRefused,
    SMTPRecipientsRefused,
    SMTPSenderRefused,
    SMTPServerDisconnected,
    SMTPTimeoutError,
)
from dotenv import load_dotenv

from models import Application

load_dotenv(Path(__file__).resolve().with_name(".env"))


class EmailConfigurationError(RuntimeError):
    pass


class EmailDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _SMTPSettings:
    server: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    starttls: bool
    ssl_tls: bool


def _required_mail_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EmailConfigurationError(f"Missing email configuration: {name}.")
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
    raise EmailConfigurationError(f"{name} must be true or false.")


def _smtp_settings() -> _SMTPSettings:
    try:
        port = int(_required_mail_setting("MAIL_PORT"))
    except ValueError as exc:
        raise EmailConfigurationError(
            "MAIL_PORT must be a valid TCP port number."
        ) from exc
    if not 1 <= port <= 65535:
        raise EmailConfigurationError("MAIL_PORT must be between 1 and 65535.")

    mail_starttls = _boolean_setting("MAIL_STARTTLS", True)
    mail_ssl_tls = _boolean_setting("MAIL_SSL_TLS", False)
    if mail_starttls and mail_ssl_tls:
        raise EmailConfigurationError(
            "MAIL_STARTTLS and MAIL_SSL_TLS cannot both be true."
        )

    server = _required_mail_setting("MAIL_SERVER")
    username = _required_mail_setting("MAIL_USERNAME")
    password = "".join(_required_mail_setting("MAIL_PASSWORD").split())
    sender = _required_mail_setting("MAIL_FROM")
    recipient = _required_mail_setting("MAIL_TO")
    if server.lower() == "smtp.gmail.com" and (
        port != 587 or not mail_starttls or mail_ssl_tls
    ):
        raise EmailConfigurationError(
            "Gmail SMTP requires port 587 with MAIL_STARTTLS=true and "
            "MAIL_SSL_TLS=false."
        )
    if server.lower() == "smtp.gmail.com" and sender.casefold() != username.casefold():
        raise EmailConfigurationError(
            "For Gmail SMTP, MAIL_FROM must match MAIL_USERNAME."
        )

    return _SMTPSettings(
        server=server,
        port=port,
        username=username,
        password=password,
        sender=sender,
        recipient=recipient,
        starttls=mail_starttls,
        ssl_tls=mail_ssl_tls,
    )


async def _deliver_message(message: EmailMessage, settings: _SMTPSettings) -> None:
    try:
        async with SMTP(
            hostname=settings.server,
            port=settings.port,
            start_tls=settings.starttls,
            use_tls=settings.ssl_tls,
            validate_certs=True,
            timeout=20,
        ) as smtp:
            try:
                await smtp.login(settings.username, settings.password)
            except SMTPAuthenticationError as exc:
                raise EmailDeliveryError(
                    "Gmail rejected SMTP authentication. Verify MAIL_USERNAME and "
                    "use a current Gmail App Password in MAIL_PASSWORD."
                ) from exc
            except SMTPServerDisconnected as exc:
                raise EmailDeliveryError(
                    "Gmail closed the connection during SMTP authentication. "
                    "Verify MAIL_USERNAME, the Gmail App Password, and the "
                    "account's SMTP access."
                ) from exc
            await smtp.send_message(message)
    except SMTPAuthenticationError as exc:
        raise EmailDeliveryError(
            "Gmail rejected SMTP authentication. Verify MAIL_USERNAME and use a "
            "current Gmail App Password in MAIL_PASSWORD."
        ) from exc
    except EmailDeliveryError:
        raise
    except SMTPTimeoutError as exc:
        raise EmailDeliveryError(
            "The SMTP server timed out before accepting the email."
        ) from exc
    except (SMTPConnectError, SMTPServerDisconnected, OSError) as exc:
        raise EmailDeliveryError(
            "The SMTP connection closed before the email was accepted. Check "
            "network access and Gmail SMTP settings."
        ) from exc
    except (SMTPSenderRefused, SMTPRecipientRefused, SMTPRecipientsRefused) as exc:
        raise EmailDeliveryError(
            "The SMTP server rejected the sender or recipient address."
        ) from exc
    except SMTPDataError as exc:
        raise EmailDeliveryError(
            "The SMTP server rejected the email content or CV attachment."
        ) from exc
    except SMTPException as exc:
        raise EmailDeliveryError(
            "The SMTP server could not accept the email. Check its response in "
            "the server logs."
        ) from exc


async def send_application_email(application: Application) -> None:
    settings = _smtp_settings()
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message["Subject"] = f"Interview application: {application.position}"
    message.set_content(
        "A new interview application has been received.\n\n"
        f"Name: {application.full_name}\n"
        f"Email: {application.email}\n"
        f"Phone: {application.phone}\n"
        f"Position: {application.position}\n\n"
        "Cover letter:\n"
        f"{application.cover_letter or '(Not provided)'}\n\n"
        f"Application ID: {application.id}"
    )
    try:
        message.add_attachment(
            application.cv_data,
            maintype="application",
            subtype="pdf",
            filename=f"application-{application.id}-cv.pdf",
        )
    except (TypeError, ValueError) as exc:
        raise EmailDeliveryError(
            "The CV attachment could not be added to the email."
        ) from exc

    await _deliver_message(message, settings)


async def send_enquiry_email(
    *, name: str, email: str, phone: str, description: str
) -> None:
    settings = _smtp_settings()
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message["Reply-To"] = email
    message["Subject"] = "New project enquiry"
    message.set_content(
        "A new project enquiry has been received.\n\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Phone: {phone}\n\n"
        "Project description:\n"
        f"{description}"
    )
    await _deliver_message(message, settings)
