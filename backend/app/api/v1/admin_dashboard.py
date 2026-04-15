from datetime import datetime, timezone
from time import perf_counter

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

from app.api.deps import get_db, require_roles
from app.core.roles import UserRole
from app.core.observability import record_business_event
from app.models.user import User
from app.schemas.admin import (
    DashboardOverview,
    MoneyBackfillAuditLogListResponse,
    MoneyBackfillAuditLogRead,
    MoneyBackfillForceRetryRequest,
    MoneyBackfillJobRead,
    MoneyBackfillJobRequest,
    MoneyBackfillJobRunRequest,
    MoneyBackfillRunRequest,
    MoneyBackfillRunResponse,
    RecentUser,
    RoleCount,
)
from app.services.money_backfill import backfill_money_minor_fields, create_money_backfill_job, run_money_backfill_job
from app.services.money_backfill import force_retry_money_backfill_job

router = APIRouter(prefix="/admin/dashboard", tags=["Admin Dashboard"])


@router.get("/overview", response_model=DashboardOverview)
def get_dashboard_overview(
    db: Database = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> DashboardOverview:
    total_users = db["users"].count_documents({})
    active_users = db["users"].count_documents({"is_active": True})
    inactive_users = max(total_users - active_users, 0)

    role_rows = list(
        db["users"].aggregate(
            [
                {"$group": {"_id": "$role", "count": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ]
        )
    )
    users_by_role = [RoleCount(role=row["_id"], count=row["count"]) for row in role_rows]

    recent_rows = list(db["users"].find({}).sort("created_at", -1).limit(5))
    recent_users = [
        RecentUser(
            id=str(user["_id"]),
            email=user["email"],
            full_name=user["full_name"],
            role=user["role"],
            created_at=user["created_at"],
        )
        for user in recent_rows
    ]

    return DashboardOverview(
        total_users=total_users,
        active_users=active_users,
        inactive_users=inactive_users,
        users_by_role=users_by_role,
        recent_users=recent_users,
    )


@router.post("/backfill-money-minor", response_model=MoneyBackfillRunResponse)
def run_money_minor_backfill(
    payload: MoneyBackfillRunRequest,
    db: Database = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> MoneyBackfillRunResponse:
    if payload.batch_size < 1 or payload.batch_size > 5000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="batch_size must be between 1 and 5000")

    started = perf_counter()
    metrics = backfill_money_minor_fields(
        db,
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
    )
    duration_ms = int((perf_counter() - started) * 1000)
    return MoneyBackfillRunResponse(
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
        duration_ms=duration_ms,
        **metrics,
    )


def _to_job_read(job: dict) -> MoneyBackfillJobRead:
    return MoneyBackfillJobRead(
        id=str(job["_id"]),
        status=job["status"],
        dry_run=bool(job["dry_run"]),
        batch_size=int(job["batch_size"]),
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        duration_ms=job.get("duration_ms"),
        last_error=job.get("last_error"),
        last_error_type=job.get("last_error_type"),
        retry_count=int(job.get("retry_count", 0)),
        next_retry_at=job.get("next_retry_at"),
        bookings_scanned=int(job.get("bookings_scanned", 0)),
        bookings_updated=int(job.get("bookings_updated", 0)),
        payments_scanned=int(job.get("payments_scanned", 0)),
        payments_updated=int(job.get("payments_updated", 0)),
        refunds_scanned=int(job.get("refunds_scanned", 0)),
        refunds_updated=int(job.get("refunds_updated", 0)),
        total_updated=int(job.get("total_updated", 0)),
    )


def _write_backfill_audit(
    db: Database,
    *,
    job_id: str,
    action: str,
    admin_user: User,
    metadata: dict | None = None,
) -> None:
    db["money_backfill_audit_logs"].insert_one(
        {
            "job_id": job_id,
            "action": action,
            "admin_user_id": str(admin_user.get("_id")),
            "admin_email": admin_user.get("email"),
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc),
        }
    )


def _to_audit_read(item: dict) -> MoneyBackfillAuditLogRead:
    return MoneyBackfillAuditLogRead(
        id=str(item["_id"]),
        job_id=str(item.get("job_id", "")),
        action=str(item.get("action", "")),
        admin_user_id=item.get("admin_user_id"),
        admin_email=item.get("admin_email"),
        metadata=item.get("metadata") or {},
        created_at=item["created_at"],
    )


@router.post("/backfill-money-minor/jobs", response_model=MoneyBackfillJobRead)
def create_or_run_money_minor_backfill_job(
    payload: MoneyBackfillJobRequest,
    db: Database = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
) -> MoneyBackfillJobRead:
    if payload.batch_size < 1 or payload.batch_size > 5000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="batch_size must be between 1 and 5000")
    if payload.max_batches is not None and payload.max_batches < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_batches must be >= 1 when provided")

    job_id = create_money_backfill_job(
        db,
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
    )
    if payload.run_now:
        try:
            job_doc = run_money_backfill_job(db, job_id=job_id, max_batches=payload.max_batches)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        record_business_event("backfill.job.create_and_run")
        _write_backfill_audit(
            db,
            job_id=job_id,
            action="create_and_run",
            admin_user=current_user,
            metadata={"max_batches": payload.max_batches},
        )
    else:
        job_doc = db["money_backfill_jobs"].find_one({"_id": ObjectId(job_id)})
        if not job_doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backfill job not found")
        record_business_event("backfill.job.create")
        _write_backfill_audit(
            db,
            job_id=job_id,
            action="create",
            admin_user=current_user,
        )
    return _to_job_read(job_doc)


@router.get("/backfill-money-minor/jobs/{job_id}", response_model=MoneyBackfillJobRead)
def get_money_minor_backfill_job(
    job_id: str,
    db: Database = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> MoneyBackfillJobRead:
    if not ObjectId.is_valid(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backfill job not found")
    job_doc = db["money_backfill_jobs"].find_one({"_id": ObjectId(job_id)})
    if not job_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backfill job not found")
    return _to_job_read(job_doc)


@router.post("/backfill-money-minor/jobs/{job_id}/run", response_model=MoneyBackfillJobRead)
def run_existing_money_minor_backfill_job(
    job_id: str,
    payload: MoneyBackfillJobRunRequest,
    db: Database = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
) -> MoneyBackfillJobRead:
    if payload.max_batches is not None and payload.max_batches < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_batches must be >= 1 when provided")
    try:
        job_doc = run_money_backfill_job(db, job_id=job_id, max_batches=payload.max_batches)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    record_business_event("backfill.job.run")
    _write_backfill_audit(
        db,
        job_id=job_id,
        action="run",
        admin_user=current_user,
        metadata={"max_batches": payload.max_batches},
    )
    return _to_job_read(job_doc)


@router.post("/backfill-money-minor/jobs/{job_id}/force-retry", response_model=MoneyBackfillJobRead)
def force_retry_existing_money_minor_backfill_job(
    job_id: str,
    payload: MoneyBackfillForceRetryRequest,
    db: Database = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
) -> MoneyBackfillJobRead:
    if payload.max_batches is not None and payload.max_batches < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_batches must be >= 1 when provided")
    try:
        job_doc = force_retry_money_backfill_job(db, job_id=job_id)
        record_business_event("backfill.job.force_retry")
        _write_backfill_audit(
            db,
            job_id=job_id,
            action="force_retry",
            admin_user=current_user,
            metadata={"run_now": payload.run_now, "max_batches": payload.max_batches},
        )
        if payload.run_now:
            job_doc = run_money_backfill_job(db, job_id=job_id, max_batches=payload.max_batches)
            record_business_event("backfill.job.run_after_force_retry")
            _write_backfill_audit(
                db,
                job_id=job_id,
                action="run_after_force_retry",
                admin_user=current_user,
                metadata={"max_batches": payload.max_batches},
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_job_read(job_doc)


@router.get("/backfill-money-minor/audit-logs", response_model=MoneyBackfillAuditLogListResponse)
def list_money_minor_backfill_audit_logs(
    job_id: str | None = None,
    admin_user_id: str | None = None,
    action: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort_direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
    db: Database = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> MoneyBackfillAuditLogListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0")
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="created_from must be <= created_to")
    if sort_direction not in {"asc", "desc"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sort_direction must be 'asc' or 'desc'")

    filters: dict = {}
    if job_id is not None:
        filters["job_id"] = job_id
    if admin_user_id is not None:
        filters["admin_user_id"] = admin_user_id
    if action is not None:
        filters["action"] = action
    if created_from is not None or created_to is not None:
        time_filter: dict = {}
        if created_from is not None:
            time_filter["$gte"] = created_from
        if created_to is not None:
            time_filter["$lte"] = created_to
        filters["created_at"] = time_filter

    total = db["money_backfill_audit_logs"].count_documents(filters)
    sort_order = 1 if sort_direction == "asc" else -1
    rows = list(
        db["money_backfill_audit_logs"]
        .find(filters)
        .sort("created_at", sort_order)
        .skip(offset)
        .limit(limit)
    )
    page = (offset // limit) + 1
    has_next = (offset + len(rows)) < total
    next_offset = (offset + limit) if has_next else None
    prev_offset = (offset - limit) if offset >= limit else None
    return MoneyBackfillAuditLogListResponse(
        items=[_to_audit_read(row) for row in rows],
        total=total,
        page=page,
        page_size=limit,
        has_next=has_next,
        next_offset=next_offset,
        prev_offset=prev_offset,
    )
