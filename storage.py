from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_CV_BYTES = 5 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class InvalidCVError(ValueError):
    pass


class CVTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedCV:
    data: bytes


async def validate_cv_upload(upload: UploadFile) -> ValidatedCV:
    filename = upload.filename or ""
    if Path(filename).suffix.lower() != ".pdf":
        raise InvalidCVError("The uploaded CV must be a PDF file.")
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in {
        "",
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    }:
        raise InvalidCVError("The uploaded CV must have PDF content type.")

    size = 0
    header = bytearray()
    tail = bytearray()
    content = bytearray()

    while True:
        chunk = await upload.read(CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_CV_BYTES:
            raise CVTooLargeError
        if len(header) < 1024:
            header.extend(chunk[: 1024 - len(header)])
        tail.extend(chunk)
        if len(tail) > 1024:
            del tail[:-1024]
        content.extend(chunk)

    if b"%PDF-" not in header or b"%%EOF" not in tail:
        raise InvalidCVError("The uploaded file is not a valid PDF document.")

    pdf_data = bytes(content)
    try:
        reader = PdfReader(BytesIO(pdf_data), strict=True)
        reader.trailer
    except (PdfReadError, EOFError, ValueError, OSError) as exc:
        raise InvalidCVError(
            "The uploaded file is not a valid PDF document."
        ) from exc

    return ValidatedCV(data=pdf_data)
