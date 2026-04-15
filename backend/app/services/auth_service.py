from datetime import datetime, timezone

from fastapi import HTTPException, status
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.roles import UserRole
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.user import UserCreate


def get_user_by_email(db: Database, email: str) -> User | None:
    normalized_email = email.strip().lower()
    return db["users"].find_one({"email": normalized_email})


def register_user(db: Database, payload: UserCreate) -> User:
    existing = get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user: User = {
        "email": payload.email.strip().lower(),
        "full_name": payload.full_name.strip(),
        "hashed_password": get_password_hash(payload.password),
        "is_active": True,
        "role": UserRole.GUEST.value,
        "created_at": datetime.now(timezone.utc),
        "_id": "",
    }
    user.pop("_id", None)

    try:
        insert_result = db["users"].insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        ) from exc

    created_user = db["users"].find_one({"_id": insert_result.inserted_id})
    if not created_user:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create user")
    return created_user


def authenticate_user(db: Database, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email=email)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    if not user["is_active"]:
        return None
    return user
