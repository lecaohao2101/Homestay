from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_db, require_roles
from app.core.config import settings
from app.core.dead_letter import write_dead_letter
from app.core.observability import record_business_event
from app.core.rate_limit import consume_request_limit
from app.core.request_security import assert_ip_allowed, get_client_ip
from app.core.roles import UserRole
from app.schemas.refund import (
    RefundApproveRequest,
    RefundListResponse,
    RefundReconcileResponse,
    RefundRead,
    RefundRejectRequest,
    RefundWebhookRequest,
)
from app.utils.money import from_vnd_minor, to_vnd_minor

router = APIRouter(prefix="/refunds", tags=["Refunds"])

REFUND_STATUS_PENDING = "pending"
REFUND_STATUS_REJECTED = "rejected"
REFUND_STATUS_PROCESSING = "processing"
REFUND_STATUS_SUCCEEDED = "succeeded"
REFUND_STATUS_FAILED = "failed"
TERMINAL_REFUND_STATUSES = {REFUND_STATUS_REJECTED, REFUND_STATUS_SUCCEEDED, REFUND_STATUS_FAILED}


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _to_refund_read(doc: dict[str, Any]) -> RefundRead:
    amount_minor = int(doc.get("amount_minor", to_vnd_minor(doc["amount"])))
    return RefundRead(
        id=str(doc["_id"]),
        booking_id=str(doc["booking_id"]),
        payment_id=str(doc["payment_id"]) if doc.get("payment_id") else None,
        amount=from_vnd_minor(amount_minor),
        currency=doc["currency"],
        rate=float(doc.get("rate", 0)),
        reason=doc.get("reason", ""),
        status=doc["status"],
        provider=doc.get("provider"),
        external_refund_id=doc.get("external_refund_id"),
        gateway_ref=doc.get("gateway_ref"),
        reject_reason=doc.get("reject_reason"),
        created_at=doc["created_at"],
        updated_at=doc.get("updated_at"),
        processed_at=doc.get("processed_at"),
    )


def _resolve_gateway_status_from_document(refund_doc: dict[str, Any]) -> str:
    """
    Resolve gateway state from persisted callback metadata when available.
    This avoids speculative status changes while still allowing reconciliation.
    """
    raw_callback = refund_doc.get("raw_callback") or {}
    gateway_status = str(raw_callback.get("gateway_status", "")).strip().lower()
    if gateway_status in {REFUND_STATUS_SUCCEEDED, REFUND_STATUS_FAILED}:
        return gateway_status
    return REFUND_STATUS_FAILED


def reconcile_processing_refunds(db: Database, now: datetime | None = None) -> RefundReconcileResponse:
    checkpoint = now or datetime.now(timezone.utc)
    timeout_minutes = max(1, int(settings.REFUND_RECONCILE_TIMEOUT_MINUTES))
    stale_before = checkpoint.timestamp() - timeout_minutes * 60
    candidates = list(db["refunds"].find({"status": REFUND_STATUS_PROCESSING}))

    scanned = 0
    updated = 0
    succeeded = 0
    failed = 0

    for refund_doc in candidates:
        updated_at = refund_doc.get("updated_at") or refund_doc.get("created_at")
        if not updated_at:
            continue
        if updated_at.timestamp() > stale_before:
            continue
        scanned += 1

        target_status = _resolve_gateway_status_from_document(refund_doc)
        lock_result = db["refunds"].update_one(
            {"_id": refund_doc["_id"], "status": REFUND_STATUS_PROCESSING},
            {
                "$set": {
                    "status": target_status,
                    "updated_at": checkpoint,
                    "processed_at": checkpoint,
                    "reconcile_reason": "timeout_reconcile",
                }
            },
        )
        if lock_result.modified_count != 1:
            continue
        updated += 1
        if target_status == REFUND_STATUS_SUCCEEDED:
            succeeded += 1
        else:
            failed += 1

    return RefundReconcileResponse(
        scanned=scanned,
        updated=updated,
        succeeded=succeeded,
        failed=failed,
    )


@router.get("", response_model=RefundListResponse)
def list_refunds(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> RefundListResponse:
    consume_request_limit(
        key=f"refunds:admin:list:{get_client_ip(request)}",
        max_requests=settings.REFUNDS_ADMIN_RATE_LIMIT,
        window_seconds=settings.REFUNDS_ADMIN_RATE_WINDOW_SECONDS,
    )
    filters: dict[str, Any] = {}
    if status_filter:
        filters["status"] = status_filter
    total = db["refunds"].count_documents(filters)
    rows = list(db["refunds"].find(filters).sort("created_at", -1).skip(skip).limit(limit))
    return RefundListResponse(items=[_to_refund_read(row) for row in rows], total=total)


@router.patch("/{refund_id}/approve", response_model=RefundRead)
def approve_refund(
    refund_id: str,
    payload: RefundApproveRequest,
    request: Request,
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> RefundRead:
    consume_request_limit(
        key=f"refunds:admin:approve:{get_client_ip(request)}",
        max_requests=settings.REFUNDS_ADMIN_RATE_LIMIT,
        window_seconds=settings.REFUNDS_ADMIN_RATE_WINDOW_SECONDS,
    )
    refund_oid = _parse_object_id(refund_id, "Refund not found")
    now = datetime.now(timezone.utc)
    update_filter = {"_id": refund_oid, "status": REFUND_STATUS_PENDING}
    update_data = {
        "status": REFUND_STATUS_PROCESSING,
        "provider": payload.provider.strip().lower(),
        "external_refund_id": payload.external_refund_id.strip(),
        "updated_at": now,
    }
    try:
        result = db["refunds"].update_one(update_filter, {"$set": update_data})
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="External refund id already exists") from exc
    if result.modified_count != 1:
        current = db["refunds"].find_one({"_id": refund_oid})
        if not current:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Refund is not in pending status")
    record_business_event("refund.approve.processing")
    updated = db["refunds"].find_one({"_id": refund_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found")
    return _to_refund_read(updated)


@router.patch("/{refund_id}/reject", response_model=RefundRead)
def reject_refund(
    refund_id: str,
    payload: RefundRejectRequest,
    request: Request,
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> RefundRead:
    consume_request_limit(
        key=f"refunds:admin:reject:{get_client_ip(request)}",
        max_requests=settings.REFUNDS_ADMIN_RATE_LIMIT,
        window_seconds=settings.REFUNDS_ADMIN_RATE_WINDOW_SECONDS,
    )
    refund_oid = _parse_object_id(refund_id, "Refund not found")
    now = datetime.now(timezone.utc)
    result = db["refunds"].update_one(
        {"_id": refund_oid, "status": REFUND_STATUS_PENDING},
        {"$set": {"status": REFUND_STATUS_REJECTED, "reject_reason": payload.reason.strip(), "updated_at": now, "processed_at": now}},
    )
    if result.modified_count != 1:
        current = db["refunds"].find_one({"_id": refund_oid})
        if not current:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Refund is not in pending status")
    record_business_event("refund.reject")
    updated = db["refunds"].find_one({"_id": refund_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found")
    return _to_refund_read(updated)


@router.post("/webhook", response_model=RefundRead)
def process_refund_webhook(
    payload: RefundWebhookRequest,
    request: Request,
    db: Database = Depends(get_db),
    webhook_secret: str | None = Header(default=None, alias="X-Refund-Webhook-Secret"),
) -> RefundRead:
    def _write_refund_dead_letter(reason: str, payload_data: dict[str, Any], refund_doc: dict[str, Any] | None = None) -> None:
        write_dead_letter(
            db,
            category="refund_webhook",
            source="refunds",
            reason=reason,
            payload=payload_data,
            metadata={
                "refund_id": str(refund_doc["_id"]) if refund_doc else None,
                "external_refund_id": payload.external_refund_id.strip() if payload.external_refund_id else None,
            },
        )

    consume_request_limit(
        key=f"refunds:webhook:{get_client_ip(request)}",
        max_requests=settings.REFUNDS_WEBHOOK_RATE_LIMIT,
        window_seconds=settings.REFUNDS_WEBHOOK_RATE_WINDOW_SECONDS,
    )
    assert_ip_allowed(
        request=request,
        allowed_ips=settings.REFUND_WEBHOOK_ALLOWED_IPS,
        detail="IP is not allowed for refund webhook",
    )
    if not webhook_secret or webhook_secret != settings.REFUND_WEBHOOK_SECRET:
        _write_refund_dead_letter("invalid_webhook_secret", payload.model_dump())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")
    external_refund_id = payload.external_refund_id.strip()
    refund_doc = db["refunds"].find_one({"external_refund_id": external_refund_id})
    if not refund_doc:
        _write_refund_dead_letter("refund_not_found", payload.model_dump())
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found")
    if refund_doc["status"] in TERMINAL_REFUND_STATUSES:
        record_business_event("refund.webhook.idempotent_terminal")
        return _to_refund_read(refund_doc)
    if refund_doc["status"] != REFUND_STATUS_PROCESSING:
        _write_refund_dead_letter("invalid_refund_status", payload.model_dump(), refund_doc=refund_doc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Refund is not in processing status")

    target_status = REFUND_STATUS_SUCCEEDED if payload.status == "succeeded" else REFUND_STATUS_FAILED
    now = datetime.now(timezone.utc)
    update_data = {
        "status": target_status,
        "gateway_ref": payload.gateway_ref.strip() if payload.gateway_ref else None,
        "raw_callback": payload.raw_payload or {},
        "updated_at": now,
        "processed_at": now,
    }
    db["refunds"].update_one({"_id": refund_doc["_id"]}, {"$set": update_data})
    if target_status == REFUND_STATUS_SUCCEEDED:
        record_business_event("refund.webhook.succeeded")
    else:
        record_business_event("refund.webhook.failed")
    updated = db["refunds"].find_one({"_id": refund_doc["_id"]})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found")
    return _to_refund_read(updated)


@router.post("/reconcile-processing", response_model=RefundReconcileResponse)
def run_reconcile_processing_refunds(
    request: Request,
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> RefundReconcileResponse:
    consume_request_limit(
        key=f"refunds:admin:reconcile:{get_client_ip(request)}",
        max_requests=settings.REFUNDS_ADMIN_RATE_LIMIT,
        window_seconds=settings.REFUNDS_ADMIN_RATE_WINDOW_SECONDS,
    )
    result = reconcile_processing_refunds(db)
    if result.updated > 0:
        record_business_event("refund.reconcile.updated")
    return result
