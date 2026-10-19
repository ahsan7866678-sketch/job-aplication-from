import logging
from typing import Optional
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import EmailStr

from email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    send_application_email,
)
from models import Application
from storage import CVTooLargeError, InvalidCVError, validate_cv_upload

logger = logging.getLogger(__name__)

app = FastAPI(title="Interview Application API")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


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


def _error(message: str, status_code: int, application_id: Optional[str] = None):
    content = {"success": False, "message": message}
    if application_id is not None:
        content["application_id"] = application_id
    return JSONResponse(status_code=status_code, content=content)


@app.post("/applications", status_code=201)
async def create_application(
    full_name: str = Form(..., min_length=1, max_length=200),
    email: EmailStr = Form(...),
    phone: str = Form(..., min_length=1, max_length=40),
    position: str = Form(..., min_length=1, max_length=120),
    cover_letter: Optional[str] = Form(None, max_length=5000),
    cv: UploadFile = File(...),
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

    try:
        validated_cv = await validate_cv_upload(cv)
    except CVTooLargeError:
        return _error("The CV exceeds the 5 MB file size limit.", 413)
    except InvalidCVError as exc:
        return _error(str(exc), 400)
    except OSError:
        logger.exception("Could not read an uploaded CV")
        return _error("The CV could not be read. Please try again.", 500)

    application = Application(
        id=str(uuid4()),
        full_name=full_name,
        email=str(email),
        phone=phone,
        position=position,
        cover_letter=cover_letter,
        cv_data=validated_cv.data,
    )
    try:
        await send_application_email(application)
    except EmailConfigurationError as exc:
        logger.error("Email service configuration error: %s", exc)
        return _error(
            "The email service is not configured correctly. Check the server "
            "configuration.",
            503,
            application.id,
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "Application email delivery failed for application %s: %s",
            application.id,
            exc,
        )
        return _error(str(exc), 502, application.id)
    except Exception:
        logger.exception(
            "Application email delivery failed for application %s",
            application.id,
        )
        return _error(
            "An unexpected email delivery error occurred. Check the server logs.",
            500,
            application.id,
        )

    return JSONResponse(
        status_code=201,
        content={
            "success": True,
            "message": "Application received and emailed",
            "application_id": application.id,
        },
    )
