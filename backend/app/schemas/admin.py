from datetime import datetime

from pydantic import BaseModel


class RoleCount(BaseModel):
    role: str
    count: int


class RecentUser(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    created_at: datetime


class DashboardOverview(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    users_by_role: list[RoleCount]
    recent_users: list[RecentUser]


class MoneyBackfillRunRequest(BaseModel):
    dry_run: bool = True
    batch_size: int = 500


class MoneyBackfillRunResponse(BaseModel):
    dry_run: bool
    batch_size: int
    duration_ms: int
    bookings_scanned: int
    bookings_updated: int
    payments_scanned: int
    payments_updated: int
    refunds_scanned: int
    refunds_updated: int
    total_updated: int


class MoneyBackfillJobRequest(BaseModel):
    dry_run: bool = True
    batch_size: int = 500
    run_now: bool = True
    max_batches: int | None = None


class MoneyBackfillJobRunRequest(BaseModel):
    max_batches: int | None = None


class MoneyBackfillForceRetryRequest(BaseModel):
    run_now: bool = True
    max_batches: int | None = None


class MoneyBackfillJobRead(BaseModel):
    id: str
    status: str
    dry_run: bool
    batch_size: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    last_error: str | None = None
    last_error_type: str | None = None
    retry_count: int = 0
    next_retry_at: datetime | None = None
    bookings_scanned: int
    bookings_updated: int
    payments_scanned: int
    payments_updated: int
    refunds_scanned: int
    refunds_updated: int
    total_updated: int


class MoneyBackfillAuditLogRead(BaseModel):
    id: str
    job_id: str
    action: str
    admin_user_id: str | None = None
    admin_email: str | None = None
    metadata: dict
    created_at: datetime


class MoneyBackfillAuditLogListResponse(BaseModel):
    items: list[MoneyBackfillAuditLogRead]
    total: int
    page: int
    page_size: int
    has_next: bool
    next_offset: int | None = None
    prev_offset: int | None = None
