from datetime import datetime

from pydantic import BaseModel


class PaymentCreateRequest(BaseModel):
    booking_id: str


class PaymentCreateResponse(BaseModel):
    payment_id: str
    booking_id: str
    txn_ref: str
    pay_url: str
    status: str


class PaymentRead(BaseModel):
    id: str
    booking_id: str
    user_id: str
    provider: str
    amount: float
    currency: str
    status: str
    txn_ref: str
    gateway_txn_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PaymentProviderRead(BaseModel):
    code: str
    name: str
    enabled: bool
    display_order: int
    maintenance_message: str | None = None
    icon_url: str | None = None
    create_endpoint: str


class PaymentProviderListResponse(BaseModel):
    items: list[PaymentProviderRead]
