from datetime import datetime
from typing import TypedDict


class User(TypedDict):
    _id: object
    email: str
    full_name: str
    hashed_password: str
    is_active: bool
    role: str
    created_at: datetime
