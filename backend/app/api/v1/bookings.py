from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_current_active_user, get_db
from app.core.config import settings
from app.core.roles import UserRole
from app.schemas.booking import BookingCreate, BookingListResponse, BookingRead
from app.utils.money import from_vnd_minor, to_vnd_minor

router = APIRouter(prefix="/bookings", tags=["Bookings"])

BOOKING_STATUS_PENDING_PAYMENT = "pending_payment"
BOOKING_STATUS_CONFIRMED = "confirmed"
BOOKING_STATUS_CANCELLED = "cancelled"
BOOKING_STATUS_EXPIRED = "expired"


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _iter_stay_dates(check_in: date, check_out: date):
    start_day = check_in if isinstance(check_in, date) else date.fromisoformat(check_in)
    end_day = check_out if isinstance(check_out, date) else date.fromisoformat(check_out)
    current = start_day
    while current < end_day:
        yield current
        current += timedelta(days=1)


def _to_booking_read(document: dict[str, Any]) -> BookingRead:
    check_in = document["check_in"] if isinstance(document["check_in"], date) else date.fromisoformat(document["check_in"])
    check_out = document["check_out"] if isinstance(document["check_out"], date) else date.fromisoformat(document["check_out"])
    total_minor = int(document.get("total_price_minor", to_vnd_minor(document["total_price"])))
    original_minor = int(document.get("original_price_minor", total_minor))
    discount_minor = int(document.get("discount_amount_minor", max(0, original_minor - total_minor)))
    refund_minor_raw = document.get("refund_amount_minor")
    return BookingRead(
        id=str(document["_id"]),
        user_id=str(document["user_id"]),
        property_id=str(document["property_id"]),
        room_id=str(document["room_id"]),
        check_in=check_in,
        check_out=check_out,
        units=document["units"],
        nights=document["nights"],
        total_price=from_vnd_minor(total_minor),
        original_price=from_vnd_minor(original_minor),
        discount_amount=from_vnd_minor(discount_minor),
        coupon_code=document.get("coupon_code"),
        status=document["status"],
        idempotency_key=document.get("idempotency_key"),
        created_at=document["created_at"],
        expires_at=document.get("expires_at"),
        cancelled_at=document.get("cancelled_at"),
        cancel_reason=document.get("cancel_reason"),
        refund_rate=float(document["refund_rate"]) if document.get("refund_rate") is not None else None,
        refund_amount=from_vnd_minor(int(refund_minor_raw)) if refund_minor_raw is not None else None,
    )


@contextmanager
def _mongo_transaction(db: Database):
    try:
        with db.client.start_session() as session:
            with session.start_transaction():
                yield session
    except Exception:
        # mongomock/local fallback where transactions are unavailable
        yield None


def _reserve_inventory_atomic(
    db: Database,
    *,
    room_id: ObjectId,
    check_in: date,
    check_out: date,
    units: int,
    session: Any = None,
) -> tuple[int, int]:
    reserved_dates: list[str] = []
    total_price_minor = 0
    for day in _iter_stay_dates(check_in, check_out):
        day_key = day.isoformat()
        availability_doc = db["room_availability"].find_one({"room_id": room_id, "date": day_key}, session=session)
        if not availability_doc:
            _release_inventory(db, room_id=room_id, check_in=check_in, check_out=day, units=units, session=session)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Room is not available on {day_key}")

        result = db["room_availability"].update_one(
            {"room_id": room_id, "date": day_key, "available_units": {"$gte": units}},
            {"$inc": {"available_units": -units}},
            session=session,
        )
        if result.modified_count != 1:
            _release_inventory(db, room_id=room_id, check_in=check_in, check_out=day, units=units, session=session)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Insufficient availability on {day_key}")

        reserved_dates.append(day_key)
        total_price_minor += to_vnd_minor(float(availability_doc["price_per_night"]) * units)
    return len(reserved_dates), total_price_minor


def _release_inventory(
    db: Database,
    *,
    room_id: ObjectId,
    check_in: date,
    check_out: date,
    units: int,
    session: Any = None,
) -> None:
    for day in _iter_stay_dates(check_in, check_out):
        db["room_availability"].update_one(
            {"room_id": room_id, "date": day.isoformat()},
            {"$inc": {"available_units": units}},
            session=session,
        )


def _claim_coupon(
    db: Database,
    *,
    coupon_code: str,
    base_amount_minor: int,
    now: datetime,
    session: Any = None,
) -> tuple[dict[str, Any], int]:
    normalized_code = coupon_code.strip().upper()
    coupon_doc = db["coupons"].find_one({"code": normalized_code}, session=session)
    if not coupon_doc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon is invalid")
    if not coupon_doc.get("active", True):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon is inactive")
    if coupon_doc.get("start_at") and now < coupon_doc["start_at"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon is not started yet")
    if coupon_doc.get("end_at") and now > coupon_doc["end_at"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon is expired")
    if base_amount_minor < to_vnd_minor(float(coupon_doc.get("min_booking_amount", 0))):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking does not meet coupon minimum amount")
    max_uses = coupon_doc.get("max_uses")
    usage_filter: dict[str, Any] = {"_id": coupon_doc["_id"], "active": True}
    if max_uses is not None:
        usage_filter["used_count"] = {"$lt": max_uses}
    claimed = db["coupons"].update_one(usage_filter, {"$inc": {"used_count": 1}}, session=session)
    if claimed.modified_count != 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon usage limit reached")
    discount_value = float(coupon_doc["discount_value"])
    if coupon_doc["discount_type"] == "percent":
        discount_amount = int(round(base_amount_minor * discount_value / 100))
    else:
        discount_amount = to_vnd_minor(discount_value)
    discount_amount = min(discount_amount, base_amount_minor)
    return coupon_doc, discount_amount


def _release_coupon_usage(db: Database, booking_doc: dict[str, Any], session: Any = None) -> None:
    coupon_id = booking_doc.get("coupon_id")
    if not coupon_id or not booking_doc.get("coupon_usage_reserved", False):
        return
    db["coupons"].update_one(
        {"_id": coupon_id, "used_count": {"$gt": 0}},
        {"$inc": {"used_count": -1}},
        session=session,
    )


def _compute_refund_rate(booking_doc: dict[str, Any], now: datetime) -> float:
    if booking_doc["status"] != BOOKING_STATUS_CONFIRMED:
        return 0.0
    check_in_date = booking_doc["check_in"] if isinstance(booking_doc["check_in"], date) else date.fromisoformat(booking_doc["check_in"])
    check_in_at = datetime.combine(check_in_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    hours_left = (check_in_at - now).total_seconds() / 3600
    if hours_left >= settings.REFUND_FULL_HOURS:
        return 1.0
    if hours_left >= settings.REFUND_PARTIAL_HOURS:
        return round(settings.REFUND_PARTIAL_PERCENT / 100, 2)
    return 0.0


def expire_pending_bookings(db: Database, now: datetime | None = None) -> int:
    checkpoint = now or datetime.now(timezone.utc)
    candidates = list(
        db["bookings"].find(
            {
                "status": BOOKING_STATUS_PENDING_PAYMENT,
                "inventory_reserved": True,
                "expires_at": {"$lte": checkpoint},
            }
        )
    )
    expired_count = 0
    for booking_doc in candidates:
        lock_result = db["bookings"].update_one(
            {
                "_id": booking_doc["_id"],
                "status": BOOKING_STATUS_PENDING_PAYMENT,
                "inventory_reserved": True,
            },
            {"$set": {"inventory_reserved": False}},
        )
        if lock_result.modified_count != 1:
            continue

        _release_inventory(
            db,
            room_id=booking_doc["room_id"],
            check_in=booking_doc["check_in"],
            check_out=booking_doc["check_out"],
            units=booking_doc["units"],
        )
        _release_coupon_usage(db, booking_doc)
        db["bookings"].update_one(
            {"_id": booking_doc["_id"]},
            {
                "$set": {
                    "status": BOOKING_STATUS_EXPIRED,
                    "cancelled_at": checkpoint,
                    "cancel_reason": "payment_timeout",
                    "coupon_usage_reserved": False,
                }
            },
        )
        expired_count += 1
    return expired_count


@router.post("", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
def create_booking(
    payload: BookingCreate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> BookingRead:
    expire_pending_bookings(db)
    if not idempotency_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Idempotency-Key header is required")
    idempotency_key = idempotency_key.strip()
    if len(idempotency_key) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency key is too short")

    existing = db["bookings"].find_one({"user_id": current_user["_id"], "idempotency_key": idempotency_key})
    if existing:
        return _to_booking_read(existing)

    if payload.check_in >= payload.check_out:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="check_in must be before check_out")
    if (payload.check_out - payload.check_in).days > 30:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking length must not exceed 30 nights")

    property_oid = _parse_object_id(payload.property_id, "Property not found")
    room_oid = _parse_object_id(payload.room_id, "Room not found")
    if not db["properties"].find_one({"_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    if not db["rooms"].find_one({"_id": room_oid, "property_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    with _mongo_transaction(db) as session:
        nights, total_price_minor = _reserve_inventory_atomic(
            db,
            room_id=room_oid,
            check_in=payload.check_in,
            check_out=payload.check_out,
            units=payload.units,
            session=session,
        )
        now = datetime.now(timezone.utc)
        original_price_minor = total_price_minor
        coupon_doc: dict[str, Any] | None = None
        discount_amount_minor = 0
        if payload.coupon_code:
            coupon_doc, discount_amount_minor = _claim_coupon(
                db,
                coupon_code=payload.coupon_code,
                base_amount_minor=original_price_minor,
                now=now,
                session=session,
            )
            total_price_minor = max(0, original_price_minor - discount_amount_minor)
        booking_doc = {
            "user_id": current_user["_id"],
            "property_id": property_oid,
            "room_id": room_oid,
            "check_in": payload.check_in.isoformat(),
            "check_out": payload.check_out.isoformat(),
            "units": payload.units,
            "nights": nights,
            "total_price_minor": total_price_minor,
            "original_price_minor": original_price_minor,
            "discount_amount_minor": discount_amount_minor,
            "total_price": from_vnd_minor(total_price_minor),
            "original_price": from_vnd_minor(original_price_minor),
            "discount_amount": from_vnd_minor(discount_amount_minor),
            "coupon_code": coupon_doc["code"] if coupon_doc else None,
            "coupon_id": coupon_doc["_id"] if coupon_doc else None,
            "coupon_usage_reserved": bool(coupon_doc),
            "status": BOOKING_STATUS_PENDING_PAYMENT,
            "inventory_reserved": True,
            "idempotency_key": idempotency_key,
            "expires_at": now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES),
            "created_at": now,
        }
        try:
            result = db["bookings"].insert_one(booking_doc, session=session)
        except DuplicateKeyError:
            duplicate = db["bookings"].find_one({"user_id": current_user["_id"], "idempotency_key": idempotency_key})
            if duplicate:
                return _to_booking_read(duplicate)
            raise
    created = db["bookings"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create booking")
    return _to_booking_read(created)


@router.get("/me", response_model=BookingListResponse)
def list_my_bookings(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> BookingListResponse:
    filters = {"user_id": current_user["_id"]}
    total = db["bookings"].count_documents(filters)
    rows = list(db["bookings"].find(filters).sort("created_at", -1).skip(skip).limit(limit))
    return BookingListResponse(items=[_to_booking_read(row) for row in rows], total=total)


@router.get("", response_model=BookingListResponse)
def list_bookings_for_management(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> BookingListResponse:
    if current_user["role"] == UserRole.ADMIN.value:
        filters: dict[str, Any] = {}
    elif current_user["role"] == UserRole.HOST.value:
        property_ids = [row["_id"] for row in db["properties"].find({"host_id": current_user["_id"]}, {"_id": 1})]
        filters = {"property_id": {"$in": property_ids}} if property_ids else {"property_id": {"$in": []}}
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    total = db["bookings"].count_documents(filters)
    rows = list(db["bookings"].find(filters).sort("created_at", -1).skip(skip).limit(limit))
    return BookingListResponse(items=[_to_booking_read(row) for row in rows], total=total)


@router.patch("/{booking_id}/cancel", response_model=BookingRead)
def cancel_booking(
    booking_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> BookingRead:
    expire_pending_bookings(db)
    booking_oid = _parse_object_id(booking_id, "Booking not found")
    booking_doc = db["bookings"].find_one({"_id": booking_oid})
    if not booking_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if booking_doc["status"] in {BOOKING_STATUS_CANCELLED, BOOKING_STATUS_EXPIRED}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking already closed")

    is_admin = current_user["role"] == UserRole.ADMIN.value
    is_owner = booking_doc["user_id"] == current_user["_id"]
    is_host_owner = False
    if current_user["role"] == UserRole.HOST.value:
        property_doc = db["properties"].find_one({"_id": booking_doc["property_id"]})
        is_host_owner = bool(property_doc and property_doc["host_id"] == current_user["_id"])
    if not (is_admin or is_owner or is_host_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    if booking_doc.get("inventory_reserved", False):
        _release_inventory(
            db,
            room_id=booking_doc["room_id"],
            check_in=booking_doc["check_in"],
            check_out=booking_doc["check_out"],
            units=booking_doc["units"],
        )
    _release_coupon_usage(db, booking_doc)

    refund_rate = _compute_refund_rate(booking_doc, datetime.now(timezone.utc))
    paid_amount_minor = 0
    payment_doc: dict[str, Any] | None = None
    if booking_doc["status"] == BOOKING_STATUS_CONFIRMED:
        payment_doc = db["payments"].find_one({"booking_id": booking_oid, "status": "success"})
        if payment_doc:
            paid_amount_minor = int(payment_doc.get("amount_minor", to_vnd_minor(payment_doc.get("amount", 0))))
    refund_amount_minor = int(round(paid_amount_minor * refund_rate)) if paid_amount_minor > 0 else 0

    db["bookings"].update_one(
        {"_id": booking_oid},
        {
            "$set": {
                "status": BOOKING_STATUS_CANCELLED,
                "cancelled_at": datetime.now(timezone.utc),
                "inventory_reserved": False,
                "cancel_reason": "manual_cancel",
                "refund_rate": refund_rate,
                "refund_amount_minor": refund_amount_minor,
                "refund_amount": from_vnd_minor(refund_amount_minor),
                "coupon_usage_reserved": False,
            }
        },
    )
    if refund_amount_minor > 0:
        db["refunds"].insert_one(
            {
                "booking_id": booking_oid,
                "payment_id": payment_doc["_id"] if payment_doc else None,
                "amount_minor": refund_amount_minor,
                "amount": from_vnd_minor(refund_amount_minor),
                "currency": "VND",
                "rate": refund_rate,
                "status": "pending",
                "reason": "booking_cancelled",
                "created_at": datetime.now(timezone.utc),
            }
        )
    updated = db["bookings"].find_one({"_id": booking_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return _to_booking_read(updated)


@router.post("/expire-pending")
def run_expire_pending_bookings(
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> dict[str, int]:
    if current_user["role"] != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    expired_count = expire_pending_bookings(db)
    return {"expired_count": expired_count}
