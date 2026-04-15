from datetime import datetime

from pydantic import BaseModel


class MediaRead(BaseModel):
    id: str
    owner_type: str
    property_id: str
    room_id: str | None = None
    content_type: str
    size_bytes: int
    original_filename: str
    storage_key: str
    url: str
    created_at: datetime


class MediaListResponse(BaseModel):
    items: list[MediaRead]
    total: int
