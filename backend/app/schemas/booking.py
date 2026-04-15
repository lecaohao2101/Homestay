from datetime import date, datetime

from pydantic import BaseModel, Field


class BookingCreate(BaseModel):
    property_id: str
    room_id: str
    check_in: date
    check_out: date
    units: int = Field(ge=1, le=500)
    coupon_code: str | None = Field(default=None, min_length=3, max_length=40)


class BookingRead(BaseModel):
    id: str
    user_id: str
    property_id: str
    room_id: str
    check_in: date
    check_out: date
    units: int
    nights: int
    total_price: float
    original_price: float | None = None
    discount_amount: float | None = None
    coupon_code: str | None = None
    status: str
    idempotency_key: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    refund_rate: float | None = None
    refund_amount: float | None = None


class BookingListResponse(BaseModel):
    items: list[BookingRead]
    total: int


class BookingStatusUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=500)
