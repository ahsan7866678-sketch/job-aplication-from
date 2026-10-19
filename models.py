from dataclasses import dataclass
from typing import Optional


@dataclass
class Application:
    id: str
    full_name: str
    email: str
    phone: str
    position: str
    cover_letter: Optional[str]
    cv_data: bytes
