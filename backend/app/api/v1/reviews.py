from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_current_active_user, get_db
from app.core.roles import UserRole
from app.schemas.review import ReviewCreate, ReviewListResponse, ReviewRead, ReviewUpdate

router = APIRouter(prefix="/reviews", tags=["Reviews"])


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _to_review_read(document: dict[str, Any]) -> ReviewRead:
    return ReviewRead(
        id=str(document["_id"]),
        property_id=str(document["property_id"]),
        user_id=str(document["user_id"]),
        rating=int(document["rating"]),
        title=document.get("title"),
        comment=document.get("comment"),
        created_at=document["created_at"],
        updated_at=document.get("updated_at"),
    )


def _can_review_property(db: Database, *, user_id: ObjectId, property_id: ObjectId) -> bool:
    booking = db["bookings"].find_one(
        {
            "user_id": user_id,
            "property_id": property_id,
            "status": "confirmed",
        }
    )
    return booking is not None


@router.post("/properties/{property_id}", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
def create_review(
    property_id: str,
    payload: ReviewCreate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> ReviewRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    if not db["properties"].find_one({"_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    if current_user["role"] != UserRole.GUEST.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only guests can create reviews")
    if not _can_review_property(db, user_id=current_user["_id"], property_id=property_oid):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only confirmed guests can review")
    if db["reviews"].find_one({"property_id": property_oid, "user_id": current_user["_id"]}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already reviewed this property")

    doc = {
        "property_id": property_oid,
        "user_id": current_user["_id"],
        "rating": payload.rating,
        "title": payload.title.strip() if payload.title else None,
        "comment": payload.comment.strip() if payload.comment else None,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = db["reviews"].insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already reviewed this property") from exc
    created = db["reviews"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create review")
    return _to_review_read(created)


@router.get("/properties/{property_id}", response_model=ReviewListResponse)
def list_reviews_by_property(
    property_id: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
) -> ReviewListResponse:
    property_oid = _parse_object_id(property_id, "Property not found")
    if not db["properties"].find_one({"_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    filters = {"property_id": property_oid}
    total = db["reviews"].count_documents(filters)
    rows = list(db["reviews"].find(filters).sort("created_at", -1).skip(skip).limit(limit))
    aggregation = list(db["reviews"].aggregate([{"$match": filters}, {"$group": {"_id": None, "avg": {"$avg": "$rating"}}}]))
    avg = round(float(aggregation[0]["avg"]), 2) if aggregation else None
    return ReviewListResponse(items=[_to_review_read(r) for r in rows], total=total, average_rating=avg)


@router.patch("/{review_id}", response_model=ReviewRead)
def update_review(
    review_id: str,
    payload: ReviewUpdate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> ReviewRead:
    review_oid = _parse_object_id(review_id, "Review not found")
    review_doc = db["reviews"].find_one({"_id": review_oid})
    if not review_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

    is_admin = current_user["role"] == UserRole.ADMIN.value
    is_owner = review_doc["user_id"] == current_user["_id"]
    if not (is_admin or is_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    updates = payload.model_dump(exclude_unset=True)
    if "title" in updates and updates["title"] is not None:
        updates["title"] = updates["title"].strip()
    if "comment" in updates and updates["comment"] is not None:
        updates["comment"] = updates["comment"].strip()
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc)
        db["reviews"].update_one({"_id": review_oid}, {"$set": updates})
    updated = db["reviews"].find_one({"_id": review_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return _to_review_read(updated)


@router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(
    review_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> None:
    review_oid = _parse_object_id(review_id, "Review not found")
    review_doc = db["reviews"].find_one({"_id": review_oid})
    if not review_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

    is_admin = current_user["role"] == UserRole.ADMIN.value
    is_owner = review_doc["user_id"] == current_user["_id"]
    if not (is_admin or is_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    db["reviews"].delete_one({"_id": review_oid})
    return None
