from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_current_active_user, get_db
from app.api.v1.bookings import (
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_CONFIRMED,
    BOOKING_STATUS_PENDING_PAYMENT,
    _release_inventory,
    _release_coupon_usage,
)
from app.core.config import settings
from app.core.dead_letter import write_dead_letter
from app.core.observability import record_business_event
from app.core.payment_momo import create_momo_payment_url, verify_momo_signature
from app.core.payment_vnpay import create_vnpay_payment_url, verify_vnpay_signature
from app.core.rate_limit import consume_request_limit
from app.core.request_security import assert_ip_allowed, get_client_ip
from app.schemas.payment import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentProviderListResponse,
    PaymentProviderRead,
)
from app.utils.money import from_vnd_minor, to_vnd_minor

router = APIRouter(prefix="/payments", tags=["Payments"])


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _load_booking_for_payment(db: Database, booking_id: str, user_id: ObjectId) -> tuple[ObjectId, dict[str, Any]]:
    booking_oid = _parse_object_id(booking_id, "Booking not found")
    booking = db["bookings"].find_one({"_id": booking_oid, "user_id": user_id})
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if booking["status"] != BOOKING_STATUS_PENDING_PAYMENT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking is not pending payment")
    return booking_oid, booking


def _ensure_payment_can_create(db: Database, booking_oid: ObjectId, provider: str) -> dict[str, Any] | None:
    success_payment = db["payments"].find_one({"booking_id": booking_oid, "status": "success"})
    if success_payment:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking payment already completed")
    other_pending = db["payments"].find_one({"booking_id": booking_oid, "status": "pending", "provider": {"$ne": provider}})
    if other_pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Another pending payment exists for this booking",
        )
    return db["payments"].find_one({"booking_id": booking_oid, "status": {"$in": ["pending", "success"]}, "provider": provider})


def _register_webhook_event(db: Database, *, provider: str, event_key: str, payload: dict[str, str]) -> bool:
    try:
        db["payment_webhook_events"].insert_one(
            {
                "provider": provider,
                "event_key": event_key,
                "payload": payload,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return True
    except DuplicateKeyError:
        return False


def _write_payment_audit(
    db: Database,
    *,
    payment_id: ObjectId | None,
    provider: str,
    event: str,
    status_value: str,
    detail: str,
    payload: dict[str, str],
) -> None:
    db["payment_audit_logs"].insert_one(
        {
            "payment_id": payment_id,
            "provider": provider,
            "event": event,
            "status": status_value,
            "detail": detail,
            "payload": payload,
            "created_at": datetime.now(timezone.utc),
        }
    )


def _write_payment_dead_letter(
    db: Database,
    *,
    provider: str,
    reason: str,
    payload: dict[str, str],
    payment_id: ObjectId | None = None,
    txn_ref: str | None = None,
) -> None:
    write_dead_letter(
        db,
        category="payment_webhook",
        source=provider,
        reason=reason,
        payload=payload,
        metadata={
            "payment_id": str(payment_id) if payment_id else None,
            "txn_ref": txn_ref,
        },
    )


def _ensure_callback_not_stale(callback_time: datetime) -> None:
    max_age = max(30, int(settings.PAYMENT_CALLBACK_MAX_AGE_SECONDS))
    drift_seconds = abs((datetime.now(timezone.utc) - callback_time).total_seconds())
    if drift_seconds > max_age:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Callback timestamp is outside tolerance window")


def _parse_vnpay_callback_time(params: dict[str, str]) -> datetime:
    raw = params.get("vnp_PayDate") or params.get("vnp_CreateDate")
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing callback timestamp")
    try:
        parsed = datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid callback timestamp format") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _parse_momo_callback_time(params: dict[str, str]) -> datetime:
    raw = params.get("responseTime")
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing callback timestamp")
    try:
        millis = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid callback timestamp format") from exc
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


@router.get("/providers", response_model=PaymentProviderListResponse)
def list_payment_providers() -> PaymentProviderListResponse:
    items = [
        PaymentProviderRead(
            code="vnpay",
            name="VNPay",
            enabled=settings.VNPAY_ENABLED,
            display_order=settings.VNPAY_DISPLAY_ORDER,
            maintenance_message=settings.VNPAY_MAINTENANCE_MESSAGE,
            icon_url=settings.VNPAY_ICON_URL,
            create_endpoint="/api/v1/payments/vnpay/create",
        ),
        PaymentProviderRead(
            code="momo",
            name="MoMo",
            enabled=settings.MOMO_ENABLED,
            display_order=settings.MOMO_DISPLAY_ORDER,
            maintenance_message=settings.MOMO_MAINTENANCE_MESSAGE,
            icon_url=settings.MOMO_ICON_URL,
            create_endpoint="/api/v1/payments/momo/create",
        ),
    ]
    items_sorted = sorted(items, key=lambda item: item.display_order)
    return PaymentProviderListResponse(items=items_sorted)


@router.post("/vnpay/create", response_model=PaymentCreateResponse)
def create_vnpay_payment(
    payload: PaymentCreateRequest,
    request: Request,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> PaymentCreateResponse:
    consume_request_limit(
        key=f"payments:create:vnpay:{get_client_ip(request)}",
        max_requests=settings.PAYMENTS_CREATE_RATE_LIMIT,
        window_seconds=settings.PAYMENTS_CREATE_RATE_WINDOW_SECONDS,
    )
    if not settings.VNPAY_ENABLED:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="VNPay is temporarily unavailable")
    booking_oid, booking = _load_booking_for_payment(db, payload.booking_id, current_user["_id"])
    existing_payment = _ensure_payment_can_create(db, booking_oid, "vnpay")

    txn_ref = existing_payment["txn_ref"] if existing_payment else f"BOOK{uuid4().hex[:20].upper()}"
    pay_url = create_vnpay_payment_url(
        txn_ref=txn_ref,
        amount_vnd=int(booking["total_price"]),
        order_info=f"Thanh toan booking {str(booking_oid)}",
        ip_addr=request.client.host if request.client else "127.0.0.1",
    )

    if not existing_payment:
        amount_minor = int(booking.get("total_price_minor", to_vnd_minor(booking["total_price"])))
        result = db["payments"].insert_one(
            {
                "booking_id": booking_oid,
                "user_id": current_user["_id"],
                "provider": "vnpay",
                "amount_minor": amount_minor,
                "amount": from_vnd_minor(amount_minor),
                "currency": "VND",
                "status": "pending",
                "txn_ref": txn_ref,
                "gateway_txn_id": None,
                "raw_callback": None,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        payment_id = str(result.inserted_id)
        record_business_event("payment.create.vnpay.new")
    else:
        payment_id = str(existing_payment["_id"])
        record_business_event("payment.create.vnpay.reuse")

    return PaymentCreateResponse(
        payment_id=payment_id,
        booking_id=str(booking_oid),
        txn_ref=txn_ref,
        pay_url=pay_url,
        status="pending",
    )


@router.post("/momo/create", response_model=PaymentCreateResponse)
def create_momo_payment(
    payload: PaymentCreateRequest,
    request: Request,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> PaymentCreateResponse:
    consume_request_limit(
        key=f"payments:create:momo:{get_client_ip(request)}",
        max_requests=settings.PAYMENTS_CREATE_RATE_LIMIT,
        window_seconds=settings.PAYMENTS_CREATE_RATE_WINDOW_SECONDS,
    )
    if not settings.MOMO_ENABLED:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MoMo is temporarily unavailable")
    booking_oid, booking = _load_booking_for_payment(db, payload.booking_id, current_user["_id"])
    existing_payment = _ensure_payment_can_create(db, booking_oid, "momo")

    txn_ref = existing_payment["txn_ref"] if existing_payment else f"MOMO{uuid4().hex[:20].upper()}"
    pay_url = create_momo_payment_url(
        txn_ref=txn_ref,
        amount_vnd=int(booking["total_price"]),
        order_info=f"Thanh toan booking {str(booking_oid)}",
    )

    if not existing_payment:
        amount_minor = int(booking.get("total_price_minor", to_vnd_minor(booking["total_price"])))
        result = db["payments"].insert_one(
            {
                "booking_id": booking_oid,
                "user_id": current_user["_id"],
                "provider": "momo",
                "amount_minor": amount_minor,
                "amount": from_vnd_minor(amount_minor),
                "currency": "VND",
                "status": "pending",
                "txn_ref": txn_ref,
                "gateway_txn_id": None,
                "raw_callback": None,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        payment_id = str(result.inserted_id)
        record_business_event("payment.create.momo.new")
    else:
        payment_id = str(existing_payment["_id"])
        record_business_event("payment.create.momo.reuse")

    return PaymentCreateResponse(
        payment_id=payment_id,
        booking_id=str(booking_oid),
        txn_ref=txn_ref,
        pay_url=pay_url,
        status="pending",
    )


@router.get("/vnpay/ipn")
def vnpay_ipn_callback(
    request: Request,
    db: Database = Depends(get_db),
) -> dict[str, str]:
    consume_request_limit(
        key=f"payments:webhook:vnpay:{get_client_ip(request)}",
        max_requests=settings.PAYMENTS_WEBHOOK_RATE_LIMIT,
        window_seconds=settings.PAYMENTS_WEBHOOK_RATE_WINDOW_SECONDS,
    )
    assert_ip_allowed(
        request=request,
        allowed_ips=settings.PAYMENT_WEBHOOK_ALLOWED_IPS,
        detail="IP is not allowed for payment webhook",
    )
    params = {k: v for k, v in request.query_params.multi_items()}
    if not verify_vnpay_signature(params):
        _write_payment_dead_letter(db, provider="vnpay", reason="invalid_signature", payload=params)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid VNPay signature")

    txn_ref = params.get("vnp_TxnRef")
    if not txn_ref:
        _write_payment_dead_letter(db, provider="vnpay", reason="missing_txn_ref", payload=params)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing transaction ref")

    payment = db["payments"].find_one({"txn_ref": txn_ref, "provider": "vnpay"})
    if not payment:
        _write_payment_dead_letter(db, provider="vnpay", reason="payment_not_found", payload=params, txn_ref=txn_ref)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    booking = db["bookings"].find_one({"_id": payment["booking_id"]})
    if not booking:
        _write_payment_dead_letter(
            db,
            provider="vnpay",
            reason="booking_not_found",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    if payment["status"] == "success":
        record_business_event("payment.webhook.vnpay.idempotent_success")
        return {"RspCode": "00", "Message": "Confirm Success"}

    callback_time = _parse_vnpay_callback_time(params)
    try:
        _ensure_callback_not_stale(callback_time)
    except HTTPException:
        _write_payment_dead_letter(
            db,
            provider="vnpay",
            reason="stale_callback",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise

    vnp_amount = params.get("vnp_Amount")
    vnp_currency = params.get("vnp_CurrCode")
    if not vnp_amount or not vnp_currency:
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="vnpay",
            event="vnpay_ipn",
            status_value="rejected",
            detail="missing_amount_or_currency",
            payload=params,
        )
        _write_payment_dead_letter(
            db,
            provider="vnpay",
            reason="missing_amount_or_currency",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing payment amount or currency")
    if vnp_currency != payment["currency"]:
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="vnpay",
            event="vnpay_ipn",
            status_value="rejected",
            detail="currency_mismatch",
            payload=params,
        )
        _write_payment_dead_letter(
            db,
            provider="vnpay",
            reason="currency_mismatch",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Currency mismatch")
    amount_minor = int(payment.get("amount_minor", to_vnd_minor(payment["amount"])))
    if int(vnp_amount) != amount_minor * 100:
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="vnpay",
            event="vnpay_ipn",
            status_value="rejected",
            detail="amount_mismatch",
            payload=params,
        )
        _write_payment_dead_letter(
            db,
            provider="vnpay",
            reason="amount_mismatch",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount mismatch")

    event_key = (
        f"{txn_ref}:{params.get('vnp_TransactionNo', '')}:"
        f"{params.get('vnp_ResponseCode', '')}:{params.get('vnp_TransactionStatus', '')}"
    )
    if not _register_webhook_event(db, provider="vnpay", event_key=event_key, payload=params):
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="vnpay",
            event="vnpay_ipn",
            status_value="duplicate",
            detail="duplicate_event",
            payload=params,
        )
        record_business_event("payment.webhook.vnpay.duplicate")
        return {"RspCode": "00", "Message": "Confirm Success"}

    response_code = params.get("vnp_ResponseCode")
    transaction_status = params.get("vnp_TransactionStatus")
    is_success = response_code == "00" and transaction_status == "00"

    db["payments"].update_one(
        {"_id": payment["_id"]},
        {
            "$set": {
                "status": "success" if is_success else "failed",
                "gateway_txn_id": params.get("vnp_TransactionNo"),
                "raw_callback": params,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    _write_payment_audit(
        db,
        payment_id=payment["_id"],
        provider="vnpay",
        event="vnpay_ipn",
        status_value="processed",
        detail="success" if is_success else "failed",
        payload=params,
    )

    if is_success:
        record_business_event("payment.webhook.vnpay.success")
        db["bookings"].update_one(
            {"_id": booking["_id"], "status": BOOKING_STATUS_PENDING_PAYMENT},
            {
                "$set": {
                    "status": BOOKING_STATUS_CONFIRMED,
                    "confirmed_at": datetime.now(timezone.utc),
                    "expires_at": None,
                }
            },
        )
    else:
        record_business_event("payment.webhook.vnpay.failed")
        if booking.get("inventory_reserved", False) and booking["status"] == BOOKING_STATUS_PENDING_PAYMENT:
            _release_inventory(
                db,
                room_id=booking["room_id"],
                check_in=booking["check_in"],
                check_out=booking["check_out"],
                units=booking["units"],
            )
        _release_coupon_usage(db, booking)
        db["bookings"].update_one(
            {"_id": booking["_id"]},
            {
                "$set": {
                    "status": BOOKING_STATUS_CANCELLED,
                    "cancelled_at": datetime.now(timezone.utc),
                    "inventory_reserved": False,
                    "cancel_reason": "payment_failed",
                    "coupon_usage_reserved": False,
                }
            },
        )

    return {"RspCode": "00", "Message": "Confirm Success"}


@router.get("/momo/ipn")
def momo_ipn_callback(
    request: Request,
    db: Database = Depends(get_db),
) -> dict[str, str]:
    consume_request_limit(
        key=f"payments:webhook:momo:{get_client_ip(request)}",
        max_requests=settings.PAYMENTS_WEBHOOK_RATE_LIMIT,
        window_seconds=settings.PAYMENTS_WEBHOOK_RATE_WINDOW_SECONDS,
    )
    assert_ip_allowed(
        request=request,
        allowed_ips=settings.PAYMENT_WEBHOOK_ALLOWED_IPS,
        detail="IP is not allowed for payment webhook",
    )
    params = {k: v for k, v in request.query_params.multi_items()}
    if not verify_momo_signature(params):
        _write_payment_dead_letter(db, provider="momo", reason="invalid_signature", payload=params)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MoMo signature")
    txn_ref = params.get("orderId")
    if not txn_ref:
        _write_payment_dead_letter(db, provider="momo", reason="missing_txn_ref", payload=params)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing transaction ref")

    payment = db["payments"].find_one({"txn_ref": txn_ref, "provider": "momo"})
    if not payment:
        _write_payment_dead_letter(db, provider="momo", reason="payment_not_found", payload=params, txn_ref=txn_ref)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    booking = db["bookings"].find_one({"_id": payment["booking_id"]})
    if not booking:
        _write_payment_dead_letter(
            db,
            provider="momo",
            reason="booking_not_found",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if payment["status"] == "success":
        record_business_event("payment.webhook.momo.idempotent_success")
        return {"resultCode": "0", "message": "Confirm Success"}

    callback_time = _parse_momo_callback_time(params)
    try:
        _ensure_callback_not_stale(callback_time)
    except HTTPException:
        _write_payment_dead_letter(
            db,
            provider="momo",
            reason="stale_callback",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise

    momo_amount = params.get("amount")
    if not momo_amount:
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="momo",
            event="momo_ipn",
            status_value="rejected",
            detail="missing_amount",
            payload=params,
        )
        _write_payment_dead_letter(
            db,
            provider="momo",
            reason="missing_amount",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing payment amount")
    amount_minor = int(payment.get("amount_minor", to_vnd_minor(payment["amount"])))
    if int(momo_amount) != amount_minor:
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="momo",
            event="momo_ipn",
            status_value="rejected",
            detail="amount_mismatch",
            payload=params,
        )
        _write_payment_dead_letter(
            db,
            provider="momo",
            reason="amount_mismatch",
            payload=params,
            payment_id=payment["_id"],
            txn_ref=txn_ref,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount mismatch")

    event_key = f"{txn_ref}:{params.get('transId', '')}:{params.get('resultCode', '')}"
    if not _register_webhook_event(db, provider="momo", event_key=event_key, payload=params):
        _write_payment_audit(
            db,
            payment_id=payment["_id"],
            provider="momo",
            event="momo_ipn",
            status_value="duplicate",
            detail="duplicate_event",
            payload=params,
        )
        record_business_event("payment.webhook.momo.duplicate")
        return {"resultCode": "0", "message": "Confirm Success"}

    is_success = params.get("resultCode") == "0"
    db["payments"].update_one(
        {"_id": payment["_id"]},
        {
            "$set": {
                "status": "success" if is_success else "failed",
                "gateway_txn_id": params.get("transId"),
                "raw_callback": params,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    _write_payment_audit(
        db,
        payment_id=payment["_id"],
        provider="momo",
        event="momo_ipn",
        status_value="processed",
        detail="success" if is_success else "failed",
        payload=params,
    )
    if is_success:
        record_business_event("payment.webhook.momo.success")
        db["bookings"].update_one(
            {"_id": booking["_id"], "status": BOOKING_STATUS_PENDING_PAYMENT},
            {"$set": {"status": BOOKING_STATUS_CONFIRMED, "confirmed_at": datetime.now(timezone.utc), "expires_at": None}},
        )
    else:
        record_business_event("payment.webhook.momo.failed")
        if booking.get("inventory_reserved", False) and booking["status"] == BOOKING_STATUS_PENDING_PAYMENT:
            _release_inventory(
                db,
                room_id=booking["room_id"],
                check_in=booking["check_in"],
                check_out=booking["check_out"],
                units=booking["units"],
            )
        _release_coupon_usage(db, booking)
        db["bookings"].update_one(
            {"_id": booking["_id"]},
            {
                "$set": {
                    "status": BOOKING_STATUS_CANCELLED,
                    "cancelled_at": datetime.now(timezone.utc),
                    "inventory_reserved": False,
                    "cancel_reason": "payment_failed",
                    "coupon_usage_reserved": False,
                }
            },
        )
    return {"resultCode": "0", "message": "Confirm Success"}
