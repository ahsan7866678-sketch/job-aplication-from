import os
import unittest
from email.message import EmailMessage
from io import BytesIO
from unittest.mock import AsyncMock, patch

from aiosmtplib.errors import SMTPAuthenticationError, SMTPServerDisconnected
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    send_application_email,
)
from main import app
from models import Application


def make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_application(cv_data: bytes | None = None) -> Application:
    return Application(
        id="test-application",
        full_name="Jane Applicant",
        email="jane@example.com",
        phone="+1 555 010 1234",
        position="Engineer",
        cover_letter="Interested in the role.",
        cv_data=cv_data or make_pdf(),
    )


class ApplicationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_docs_and_openapi_are_available_without_database(self) -> None:
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

    @patch("main.send_application_email", new_callable=AsyncMock)
    def test_valid_pdf_is_passed_to_email_service(
        self, send_email: AsyncMock
    ) -> None:
        cv_data = make_pdf()
        response = self.client.post(
            "/applications",
            data={
                "full_name": "Jane Applicant",
                "email": "jane@example.com",
                "phone": "+1 555 010 1234",
                "position": "Engineer",
                "cover_letter": "Interested in the role.",
            },
            files={"cv": ("resume.pdf", cv_data, "application/pdf")},
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["success"])
        send_email.assert_awaited_once()
        application = send_email.await_args.args[0]
        self.assertEqual(application.cv_data, cv_data)

    @patch("main.send_application_email", new_callable=AsyncMock)
    def test_non_pdf_upload_is_rejected_before_sending(
        self, send_email: AsyncMock
    ) -> None:
        response = self.client.post(
            "/applications",
            data={
                "full_name": "Jane Applicant",
                "email": "jane@example.com",
                "phone": "123",
                "position": "Engineer",
            },
            files={"cv": ("resume.txt", b"not a PDF", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("PDF", response.json()["message"])
        send_email.assert_not_awaited()

    @patch("main.send_application_email", new_callable=AsyncMock)
    def test_non_pdf_content_type_is_rejected_before_sending(
        self, send_email: AsyncMock
    ) -> None:
        response = self.client.post(
            "/applications",
            data={
                "full_name": "Jane Applicant",
                "email": "jane@example.com",
                "phone": "123",
                "position": "Engineer",
            },
            files={"cv": ("resume.pdf", make_pdf(), "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("content type", response.json()["message"])
        send_email.assert_not_awaited()

    @patch("main.send_application_email", new_callable=AsyncMock)
    def test_smtp_failure_returns_actionable_502(
        self, send_email: AsyncMock
    ) -> None:
        send_email.side_effect = EmailDeliveryError(
            "Gmail rejected SMTP authentication. Verify MAIL_USERNAME and use a "
            "current Gmail App Password in MAIL_PASSWORD."
        )
        response = self.client.post(
            "/applications",
            data={
                "full_name": "Jane Applicant",
                "email": "jane@example.com",
                "phone": "123",
                "position": "Engineer",
            },
            files={"cv": ("resume.pdf", make_pdf(), "application/pdf")},
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("App Password", response.json()["message"])
        self.assertFalse(response.json()["success"])

    @patch("main.send_application_email", new_callable=AsyncMock)
    def test_oversized_pdf_is_rejected_before_sending(
        self, send_email: AsyncMock
    ) -> None:
        response = self.client.post(
            "/applications",
            data={
                "full_name": "Jane Applicant",
                "email": "jane@example.com",
                "phone": "123",
                "position": "Engineer",
            },
            files={
                "cv": (
                    "resume.pdf",
                    b"x" * (5 * 1024 * 1024 + 1),
                    "application/pdf",
                )
            },
        )

        self.assertEqual(response.status_code, 413)
        send_email.assert_not_awaited()


class EmailServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {
                "MAIL_SERVER": "smtp.gmail.com",
                "MAIL_PORT": "587",
                "MAIL_USERNAME": "sender@gmail.com",
                "MAIL_PASSWORD": "test-app-password",
                "MAIL_FROM": "sender@gmail.com",
                "MAIL_TO": "recipient@example.com",
                "MAIL_STARTTLS": "true",
                "MAIL_SSL_TLS": "false",
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("email_service.SMTP")
    async def test_uses_starttls_and_attaches_pdf(self, smtp_factory) -> None:
        smtp = AsyncMock()
        smtp.__aenter__.return_value = smtp
        smtp.__aexit__.return_value = None
        smtp_factory.return_value = smtp

        await send_application_email(make_application())

        smtp_factory.assert_called_once_with(
            hostname="smtp.gmail.com",
            port=587,
            start_tls=True,
            use_tls=False,
            validate_certs=True,
            timeout=20,
        )
        smtp.login.assert_awaited_once_with("sender@gmail.com", "test-app-password")
        smtp.send_message.assert_awaited_once()
        message = smtp.send_message.await_args.args[0]
        self.assertIsInstance(message, EmailMessage)
        self.assertEqual(message["To"], "recipient@example.com")
        self.assertEqual(message["Subject"], "Interview application: Engineer")
        attachment = message.iter_attachments().__next__()
        self.assertEqual(attachment.get_content_type(), "application/pdf")
        self.assertEqual(
            attachment.get_filename(),
            "application-test-application-cv.pdf",
        )

    @patch("email_service.SMTP")
    async def test_removes_spaces_from_gmail_app_password(self, smtp_factory) -> None:
        os.environ["MAIL_PASSWORD"] = "abcd efgh ijkl mnop"
        smtp = AsyncMock()
        smtp.__aenter__.return_value = smtp
        smtp.__aexit__.return_value = None
        smtp_factory.return_value = smtp

        await send_application_email(make_application())

        smtp.login.assert_awaited_once_with("sender@gmail.com", "abcdefghijklmnop")

    @patch("email_service.SMTP")
    async def test_rejected_gmail_password_has_actionable_error(
        self, smtp_factory
    ) -> None:
        smtp = AsyncMock()
        smtp.__aenter__.return_value = smtp
        smtp.__aexit__.return_value = None
        smtp.login.side_effect = SMTPAuthenticationError(535, "bad credentials")
        smtp_factory.return_value = smtp

        with self.assertRaisesRegex(EmailDeliveryError, "Gmail rejected SMTP authentication"):
            await send_application_email(make_application())

        smtp.send_message.assert_not_awaited()

    @patch("email_service.SMTP")
    async def test_disconnected_during_authentication_has_actionable_error(
        self, smtp_factory
    ) -> None:
        smtp = AsyncMock()
        smtp.__aenter__.return_value = smtp
        smtp.__aexit__.return_value = None
        smtp.login.side_effect = SMTPServerDisconnected("unexpected EOF")
        smtp_factory.return_value = smtp

        with self.assertRaisesRegex(
            EmailDeliveryError, "closed the connection during SMTP authentication"
        ):
            await send_application_email(make_application())

        smtp.send_message.assert_not_awaited()

    @patch("email_service.SMTP")
    async def test_gmail_requires_starttls_configuration(self, smtp_factory) -> None:
        os.environ["MAIL_STARTTLS"] = "false"

        with self.assertRaisesRegex(EmailConfigurationError, "requires port 587"):
            await send_application_email(make_application())

        smtp_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
