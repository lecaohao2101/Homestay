import re
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.database import Database

from app.api.deps import get_db, require_roles
from app.core.roles import UserRole
from app.models.user import User
from app.schemas.user import UserAdminUpdate, UserListResponse, UserRead
from app.utils.user_mapper import to_public_user

router = APIRouter(prefix="/users", tags=["Users"])


def _ensure_not_remove_last_admin(
    db: Database,
    target_user: dict[str, Any],
    next_role: str,
    next_is_active: bool,
) -> None:
    if target_user["role"] != UserRole.ADMIN.value:
        return

    if next_role == UserRole.ADMIN.value and next_is_active:
        return

    active_admins = db["users"].count_documents({"role": UserRole.ADMIN.value, "is_active": True})
    if active_admins <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove or deactivate the last active admin",
        )


def _parse_object_id(user_id: str) -> ObjectId:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return ObjectId(user_id)


@router.get("", response_model=UserListResponse)
def list_users(
    q: str | None = Query(default=None, min_length=1, max_length=255),
    role: UserRole | None = None,
    is_active: bool | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> UserListResponse:
    filters: dict[str, Any] = {}
    if q:
        safe_keyword = re.escape(q.strip())
        filters["$or"] = [
            {"email": {"$regex": safe_keyword, "$options": "i"}},
            {"full_name": {"$regex": safe_keyword, "$options": "i"}},
        ]
    if role:
        filters["role"] = role.value
    if is_active is not None:
        filters["is_active"] = is_active

    total = db["users"].count_documents(filters)
    users = list(
        db["users"]
        .find(filters)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    return UserListResponse(
        items=[UserRead.model_validate(to_public_user(user)) for user in users],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{user_id}", response_model=UserRead)
def get_user_detail(
    user_id: str,
    db: Database = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> UserRead:
    user = db["users"].find_one({"_id": _parse_object_id(user_id)})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserRead.model_validate(to_public_user(user))


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    payload: UserAdminUpdate,
    db: Database = Depends(get_db),
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
) -> UserRead:
    parsed_id = _parse_object_id(user_id)
    user = db["users"].find_one({"_id": parsed_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)
    next_role = update_data.get("role", user["role"])
    if isinstance(next_role, UserRole):
        next_role = next_role.value
    next_is_active = update_data.get("is_active", user["is_active"])

    _ensure_not_remove_last_admin(db=db, target_user=user, next_role=next_role, next_is_active=next_is_active)

    if user["_id"] == current_admin["_id"] and next_is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    mongo_updates: dict[str, Any] = {}
    if "full_name" in update_data and update_data["full_name"] is not None:
        mongo_updates["full_name"] = update_data["full_name"].strip()
    if "role" in update_data and update_data["role"] is not None:
        mongo_updates["role"] = next_role
    if "is_active" in update_data and update_data["is_active"] is not None:
        mongo_updates["is_active"] = bool(update_data["is_active"])

    if mongo_updates:
        db["users"].update_one({"_id": parsed_id}, {"$set": mongo_updates})

    updated_user = db["users"].find_one({"_id": parsed_id})
    if not updated_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserRead.model_validate(to_public_user(updated_user))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    db: Database = Depends(get_db),
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
) -> Response:
    parsed_id = _parse_object_id(user_id)
    user = db["users"].find_one({"_id": parsed_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user["_id"] == current_admin["_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )

    _ensure_not_remove_last_admin(
        db=db,
        target_user=user,
        next_role=UserRole.GUEST.value,
        next_is_active=False,
    )

    db["users"].delete_one({"_id": parsed_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
