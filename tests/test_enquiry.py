import os
import unittest
from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

from aiosmtplib.errors import SMTPAuthenticationError
from fastapi.testclient import TestClient

from email_service import EmailDeliveryError, send_enquiry_email
from main import app


class EnquiryEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_openapi_exposes_individual_form_fields(self) -> None:
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(
            {path for path in schema["paths"] if path.startswith("/api/enquiry")},
            {"/api/enquiry"},
        )
        form_request = schema["paths"]["/api/enquiry"]["post"][
            "requestBody"
        ]["content"]["application/x-www-form-urlencoded"]["schema"]
        form_schema = schema["components"]["schemas"][
            form_request["$ref"].rsplit("/", maxsplit=1)[-1]
        ]

        self.assertEqual(
            set(form_schema["required"]),
            {"name", "email", "phone", "description"},
        )
        self.assertEqual(
            {
                field: form_schema["properties"][field]["title"]
                for field in ("name", "email", "phone", "description")
            },
            {
                "name": "Your Name",
                "email": "Your Email",
                "phone": "Phone Number",
                "description": "Project Description",
            },
        )

    @patch("enquiry.send_enquiry_email", new_callable=AsyncMock)
    def test_valid_enquiry_form_fields_are_emailed(
        self, send_email: AsyncMock
    ) -> None:
        response = self.client.post(
            "/api/enquiry",
            data={
                "name": " Jane Applicant ",
                "email": "jane@example.com",
                "phone": " +1 555 010 1234 ",
                "description": " A project enquiry. ",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        send_email.assert_awaited_once_with(
            name="Jane Applicant",
            email="jane@example.com",
            phone="+1 555 010 1234",
            description="A project enquiry.",
        )

    @patch("enquiry.send_enquiry_email", new_callable=AsyncMock)
    def test_invalid_form_fields_are_rejected(
        self, send_email: AsyncMock
    ) -> None:
        response = self.client.post(
            "/api/enquiry",
            data={
                "name": "Jane Applicant",
                "email": "invalid",
                "phone": "123",
                "description": "A project enquiry.",
            },
        )

        self.assertEqual(response.status_code, 422)
        send_email.assert_not_awaited()

    @patch("enquiry.send_enquiry_email", new_callable=AsyncMock)
    def test_invalid_or_missing_fields_are_rejected(
        self, send_email: AsyncMock
    ) -> None:
        invalid_requests = (
            {"name": "Jane", "email": "invalid", "phone": "123", "description": "Project"},
            {
                "email": "jane@example.com",
                "phone": "123",
                "description": "Project",
            },
            {
                "name": "   ",
                "email": "jane@example.com",
                "phone": "123",
                "description": "Project",
            },
            {
                "name": "Jane",
                "email": "jane@example.com",
                "phone": "123",
                "description": "   ",
            },
        )
        for payload in invalid_requests:
            with self.subTest(payload=payload):
                response = self.client.post("/api/enquiry", data=payload)
                self.assertEqual(response.status_code, 422)
        send_email.assert_not_awaited()

    @patch("enquiry.send_enquiry_email", new_callable=AsyncMock)
    def test_delivery_failure_does_not_return_success(
        self, send_email: AsyncMock
    ) -> None:
        send_email.side_effect = EmailDeliveryError(
            "The SMTP server timed out before accepting the email."
        )
        response = self.client.post(
            "/api/enquiry",
            data={
                "name": "Jane Applicant",
                "email": "jane@example.com",
                "phone": "123",
                "description": "A project enquiry.",
            },
        )

        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()["success"])


class EnquiryEmailServiceTests(unittest.IsolatedAsyncioTestCase):
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
    async def test_sends_enquiry_to_configured_recipient_with_reply_to(
        self, smtp_factory
    ) -> None:
        smtp = AsyncMock()
        smtp.__aenter__.return_value = smtp
        smtp.__aexit__.return_value = None
        smtp_factory.return_value = smtp

        await send_enquiry_email(
            name="Jane Applicant",
            email="jane@example.com",
            phone="+1 555 010 1234",
            description="A project enquiry.",
        )

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
        self.assertEqual(message["Reply-To"], "jane@example.com")
        body = message.get_content()
        self.assertIn("Jane Applicant", body)
        self.assertIn("jane@example.com", body)
        self.assertIn("+1 555 010 1234", body)
        self.assertIn("A project enquiry.", body)

    @patch("email_service.SMTP")
    async def test_smtp_authentication_failure_is_reported(self, smtp_factory) -> None:
        smtp = AsyncMock()
        smtp.__aenter__.return_value = smtp
        smtp.__aexit__.return_value = None
        smtp.login.side_effect = SMTPAuthenticationError(535, "bad credentials")
        smtp_factory.return_value = smtp

        with self.assertRaisesRegex(
            EmailDeliveryError, "Gmail rejected SMTP authentication"
        ):
            await send_enquiry_email(
                name="Jane Applicant",
                email="jane@example.com",
                phone="123",
                description="A project enquiry.",
            )

        smtp.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
