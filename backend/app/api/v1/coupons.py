from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_db, require_roles
from app.core.roles import UserRole
from app.schemas.coupon import CouponCreate, CouponListResponse, CouponRead, CouponUpdate

router = APIRouter(prefix="/coupons", tags=["Coupons"])


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _to_coupon_read(doc: dict[str, Any]) -> CouponRead:
    return CouponRead(
        id=str(doc["_id"]),
        code=doc["code"],
        discount_type=doc["discount_type"],
        discount_value=float(doc["discount_value"]),
        min_booking_amount=float(doc.get("min_booking_amount", 0)),
        max_uses=doc.get("max_uses"),
        used_count=int(doc.get("used_count", 0)),
        start_at=doc.get("start_at"),
        end_at=doc.get("end_at"),
        active=bool(doc.get("active", True)),
        created_at=doc["created_at"],
    )


@router.post("", response_model=CouponRead, status_code=status.HTTP_201_CREATED)
def create_coupon(
    payload: CouponCreate,
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> CouponRead:
    if payload.discount_type == "percent" and payload.discount_value > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Percent discount cannot exceed 100")
    if payload.start_at and payload.end_at and payload.start_at >= payload.end_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_at must be before end_at")

    doc = {
        "code": payload.code.strip().upper(),
        "discount_type": payload.discount_type,
        "discount_value": float(payload.discount_value),
        "min_booking_amount": float(payload.min_booking_amount),
        "max_uses": payload.max_uses,
        "used_count": 0,
        "start_at": payload.start_at,
        "end_at": payload.end_at,
        "active": payload.active,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = db["coupons"].insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coupon code already exists") from exc
    created = db["coupons"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create coupon")
    return _to_coupon_read(created)


@router.get("", response_model=CouponListResponse)
def list_coupons(
    active: bool | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> CouponListResponse:
    filters: dict[str, Any] = {}
    if active is not None:
        filters["active"] = active
    total = db["coupons"].count_documents(filters)
    rows = list(db["coupons"].find(filters).sort("created_at", -1).skip(skip).limit(limit))
    return CouponListResponse(items=[_to_coupon_read(row) for row in rows], total=total)


@router.patch("/{coupon_id}", response_model=CouponRead)
def update_coupon(
    coupon_id: str,
    payload: CouponUpdate,
    db: Database = Depends(get_db),
    _: dict[str, Any] = Depends(require_roles(UserRole.ADMIN)),
) -> CouponRead:
    coupon_oid = _parse_object_id(coupon_id, "Coupon not found")
    coupon_doc = db["coupons"].find_one({"_id": coupon_oid})
    if not coupon_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    updates = payload.model_dump(exclude_unset=True)
    if "discount_value" in updates and coupon_doc["discount_type"] == "percent" and updates["discount_value"] > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Percent discount cannot exceed 100")
    if "start_at" in updates or "end_at" in updates:
        start = updates.get("start_at", coupon_doc.get("start_at"))
        end = updates.get("end_at", coupon_doc.get("end_at"))
        if start and end and start >= end:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_at must be before end_at")
    if updates:
        db["coupons"].update_one({"_id": coupon_oid}, {"$set": updates})
    updated = db["coupons"].find_one({"_id": coupon_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    return _to_coupon_read(updated)
