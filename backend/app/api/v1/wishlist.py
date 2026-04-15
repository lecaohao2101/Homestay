from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.api.deps import get_current_active_user, get_db
from app.schemas.wishlist import WishlistItemRead, WishlistListResponse

router = APIRouter(prefix="/wishlist", tags=["Wishlist"])


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _to_wishlist_read(document: dict[str, Any]) -> WishlistItemRead:
    return WishlistItemRead(
        id=str(document["_id"]),
        user_id=str(document["user_id"]),
        property_id=str(document["property_id"]),
        created_at=document["created_at"],
    )


@router.post("/properties/{property_id}", response_model=WishlistItemRead, status_code=status.HTTP_201_CREATED)
def add_property_to_wishlist(
    property_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> WishlistItemRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    if not db["properties"].find_one({"_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    if db["wishlists"].find_one({"user_id": current_user["_id"], "property_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Property already in wishlist")

    doc = {
        "user_id": current_user["_id"],
        "property_id": property_oid,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = db["wishlists"].insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Property already in wishlist") from exc

    created = db["wishlists"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot add wishlist item")
    return _to_wishlist_read(created)


@router.get("", response_model=WishlistListResponse)
def list_my_wishlist(
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> WishlistListResponse:
    filters = {"user_id": current_user["_id"]}
    rows = list(db["wishlists"].find(filters).sort("created_at", -1))
    return WishlistListResponse(items=[_to_wishlist_read(r) for r in rows], total=len(rows))


@router.delete("/properties/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_property_from_wishlist(
    property_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> None:
    property_oid = _parse_object_id(property_id, "Property not found")
    result = db["wishlists"].delete_one({"user_id": current_user["_id"], "property_id": property_oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wishlist item not found")
    return None
