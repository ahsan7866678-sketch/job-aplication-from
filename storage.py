import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_CV_BYTES = 5 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
CV_DIRECTORY = Path(__file__).resolve().parent / "uploads" / "cvs"


class InvalidCVError(ValueError):
    pass


class CVTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedCV:
    data: bytes
    sha256: str


def resolve_storage_path(storage_path: str) -> Path:
    expected_prefix = "uploads/cvs/"
    if not storage_path.startswith(expected_prefix):
        raise ValueError("The stored CV path is invalid.")
    filename = storage_path[len(expected_prefix) :]
    if not filename or Path(filename).name != filename:
        raise ValueError("The stored CV path is invalid.")
    resolved = (CV_DIRECTORY / filename).resolve()
    if resolved.parent != CV_DIRECTORY.resolve():
        raise ValueError("The stored CV path is invalid.")
    return resolved


async def validate_cv_upload(upload: UploadFile) -> ValidatedCV:
    digest = hashlib.sha256()
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
        digest.update(chunk)
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

    return ValidatedCV(data=pdf_data, sha256=digest.hexdigest())
