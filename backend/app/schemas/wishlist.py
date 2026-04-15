from datetime import datetime

from pydantic import BaseModel


class WishlistItemRead(BaseModel):
    id: str
    user_id: str
    property_id: str
    created_at: datetime


class WishlistListResponse(BaseModel):
    items: list[WishlistItemRead]
    total: int
