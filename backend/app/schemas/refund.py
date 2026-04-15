from datetime import datetime

from pydantic import BaseModel, Field


class RefundApproveRequest(BaseModel):
    provider: str = Field(default="vnpay", min_length=2, max_length=20)
    external_refund_id: str = Field(min_length=6, max_length=100)


class RefundRejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RefundWebhookRequest(BaseModel):
    external_refund_id: str = Field(min_length=6, max_length=100)
    status: str = Field(pattern="^(succeeded|failed)$")
    gateway_ref: str | None = Field(default=None, max_length=100)
    raw_payload: dict[str, str] | None = None


class RefundRead(BaseModel):
    id: str
    booking_id: str
    payment_id: str | None = None
    amount: float
    currency: str
    rate: float
    reason: str
    status: str
    provider: str | None = None
    external_refund_id: str | None = None
    gateway_ref: str | None = None
    reject_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    processed_at: datetime | None = None


class RefundListResponse(BaseModel):
    items: list[RefundRead]
    total: int


class RefundReconcileResponse(BaseModel):
    scanned: int
    updated: int
    succeeded: int
    failed: int
