from datetime import datetime

from pydantic import BaseModel, Field


class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    discount_type: str = Field(pattern="^(percent|fixed)$")
    discount_value: float = Field(gt=0)
    min_booking_amount: float = Field(default=0, ge=0)
    max_uses: int | None = Field(default=None, ge=1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    active: bool = True


class CouponUpdate(BaseModel):
    discount_value: float | None = Field(default=None, gt=0)
    min_booking_amount: float | None = Field(default=None, ge=0)
    max_uses: int | None = Field(default=None, ge=1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    active: bool | None = None


class CouponRead(BaseModel):
    id: str
    code: str
    discount_type: str
    discount_value: float
    min_booking_amount: float
    max_uses: int | None = None
    used_count: int
    start_at: datetime | None = None
    end_at: datetime | None = None
    active: bool
    created_at: datetime


class CouponListResponse(BaseModel):
    items: list[CouponRead]
    total: int
