import os

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


async def send_application_email(application: Application) -> None:
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

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
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

    try:
        async with SMTP(
            hostname=server,
            port=port,
            start_tls=mail_starttls,
            use_tls=mail_ssl_tls,
            validate_certs=True,
            timeout=20,
        ) as smtp:
            try:
                await smtp.login(username, password)
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
