import logging
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import EmailStr

from email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    send_enquiry_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


async def _deliver_enquiry(
    *, name: str, email: str, phone: str, description: str
) -> JSONResponse:
    try:
        await send_enquiry_email(
            name=name,
            email=email,
            phone=phone,
            description=description,
        )
    except EmailConfigurationError as exc:
        logger.error("Email service configuration error: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "message": "The email service is not configured correctly. "
                "Check the server configuration.",
            },
        )
    except EmailDeliveryError as exc:
        logger.warning("Enquiry email delivery failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"success": False, "message": str(exc)},
        )
    except Exception:
        logger.exception("Unexpected enquiry email delivery error")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "An unexpected email delivery error occurred. "
                "Check the server logs.",
            },
        )

    return JSONResponse(
        status_code=200,
        content={"success": True, "message": "Enquiry received and emailed"},
    )


@router.post("/enquiry")
async def create_enquiry(
    name: Annotated[
        str,
        Form(title="Your Name", min_length=1, max_length=200),
    ],
    email: Annotated[
        EmailStr,
        Form(title="Your Email"),
    ],
    phone: Annotated[
        str,
        Form(title="Phone Number", min_length=1, max_length=40),
    ],
    description: Annotated[
        str,
        Form(
            title="Project Description",
            min_length=1,
            max_length=5000,
        ),
    ],
) -> JSONResponse:
    cleaned_values = {
        "name": name.strip(),
        "phone": phone.strip(),
        "description": description.strip(),
    }
    if any(not value for value in cleaned_values.values()):
        raise HTTPException(
            status_code=422,
            detail="Name, phone number, and project description must not be blank.",
        )
    return await _deliver_enquiry(
        name=cleaned_values["name"],
        email=str(email),
        phone=cleaned_values["phone"],
        description=cleaned_values["description"],
    )
