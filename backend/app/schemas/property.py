from datetime import date, datetime

from pydantic import BaseModel, Field


class PropertyCreate(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=2000)
    address: str = Field(min_length=5, max_length=255)
    city: str = Field(min_length=2, max_length=100)
    country: str = Field(min_length=2, max_length=100)
    host_id: str | None = None


class PropertyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, min_length=10, max_length=2000)
    address: str | None = Field(default=None, min_length=5, max_length=255)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    country: str | None = Field(default=None, min_length=2, max_length=100)


class PropertyRead(BaseModel):
    id: str
    host_id: str
    name: str
    description: str
    address: str
    city: str
    country: str
    created_at: datetime


class PropertyListResponse(BaseModel):
    items: list[PropertyRead]
    total: int
    skip: int
    limit: int


class RoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    capacity: int = Field(ge=1, le=50)
    price_per_night: float = Field(gt=0)
    quantity: int = Field(ge=1, le=500)
    description: str | None = Field(default=None, max_length=1000)


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    capacity: int | None = Field(default=None, ge=1, le=50)
    price_per_night: float | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, ge=1, le=500)
    description: str | None = Field(default=None, max_length=1000)


class RoomRead(BaseModel):
    id: str
    property_id: str
    name: str
    capacity: int
    price_per_night: float
    quantity: int
    description: str | None = None
    created_at: datetime


class AvailabilityUpsertRequest(BaseModel):
    start_date: date
    end_date: date
    available_units: int = Field(ge=0, le=500)
    price_per_night: float = Field(gt=0)


class AvailabilityItem(BaseModel):
    date: date
    available_units: int
    price_per_night: float


class AvailabilityCheckResponse(BaseModel):
    room_id: str
    property_id: str
    check_in: date
    check_out: date
    requested_units: int
    is_available: bool
    total_price: float
    available_nights: int
    missing_dates: list[date]
    nightly_details: list[AvailabilityItem]
