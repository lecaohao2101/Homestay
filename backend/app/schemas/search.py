from datetime import date

from pydantic import BaseModel


class SearchRoomResult(BaseModel):
    room_id: str
    room_name: str
    capacity: int
    price_per_night: float
    available_units: int
    total_price: float | None = None


class SearchPropertyResult(BaseModel):
    property_id: str
    name: str
    city: str
    country: str
    address: str
    description: str
    min_price: float
    max_capacity: int
    available: bool
    matched_rooms: list[SearchRoomResult]


class PublicSearchResponse(BaseModel):
    items: list[SearchPropertyResult]
    total: int
    skip: int
    limit: int
    check_in: date | None = None
    check_out: date | None = None
