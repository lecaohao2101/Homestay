from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pymongo.database import Database

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.password_policy import validate_password_strength
from app.core.rate_limit import clear_attempts, consume_request_limit, ensure_not_locked, register_failed_attempt
from app.core.request_security import get_client_ip
from app.core.security import create_access_token, create_refresh_token, hash_refresh_token
from app.models.user import User
from app.schemas.auth import LogoutRequest, RefreshTokenRequest, Token
from app.schemas.user import UserCreate, UserRead
from app.services.auth_service import authenticate_user, register_user
from app.utils.user_mapper import to_public_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

def _create_auth_session(db: Database, *, user_id: object, refresh_token: str, request: Request) -> None:
    db["auth_sessions"].insert_one(
        {
            "user_id": user_id,
            "refresh_token_hash": hash_refresh_token(refresh_token),
            "device_info": request.headers.get("user-agent", "unknown"),
            "ip": request.client.host if request.client else "unknown",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            "created_at": datetime.now(timezone.utc),
            "revoked_at": None,
        }
    )


def _coerce_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, request: Request, db: Database = Depends(get_db)) -> UserRead:
    consume_request_limit(
        key=f"auth:register:{get_client_ip(request)}",
        max_requests=settings.AUTH_REGISTER_RATE_LIMIT,
        window_seconds=settings.AUTH_REGISTER_RATE_WINDOW_SECONDS,
    )
    validate_password_strength(payload.password)

    user = register_user(db, payload)
    return UserRead.model_validate(to_public_user(user))


@router.post("/login", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Database = Depends(get_db),
) -> Token:
    consume_request_limit(
        key=f"auth:login:{get_client_ip(request)}",
        max_requests=settings.AUTH_LOGIN_RATE_LIMIT,
        window_seconds=settings.AUTH_LOGIN_RATE_WINDOW_SECONDS,
    )
    ip = get_client_ip(request)
    normalized_email = form_data.username.strip().lower()
    throttle_key = f"{normalized_email}:{ip}"

    ensure_not_locked(throttle_key)

    user = authenticate_user(db, email=normalized_email, password=form_data.password)
    if not user:
        register_failed_attempt(throttle_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clear_attempts(throttle_key)
    access_token = create_access_token(subject=user["email"])
    refresh_token = create_refresh_token()
    _create_auth_session(db, user_id=user["_id"], refresh_token=refresh_token, request=request)
    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=Token)
def refresh_token(
    payload: RefreshTokenRequest,
    request: Request,
    db: Database = Depends(get_db),
) -> Token:
    consume_request_limit(
        key=f"auth:refresh:{get_client_ip(request)}",
        max_requests=settings.AUTH_REFRESH_RATE_LIMIT,
        window_seconds=settings.AUTH_REFRESH_RATE_WINDOW_SECONDS,
    )
    refresh_hash = hash_refresh_token(payload.refresh_token)
    session_doc = db["auth_sessions"].find_one({"refresh_token_hash": refresh_hash, "revoked_at": None})
    if not session_doc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if _coerce_utc(session_doc["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    user = db["users"].find_one({"_id": session_doc["user_id"]})
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    # Rotation: revoke old session and mint a new refresh token.
    db["auth_sessions"].update_one(
        {"_id": session_doc["_id"]},
        {"$set": {"revoked_at": datetime.now(timezone.utc)}},
    )
    new_refresh_token = create_refresh_token()
    _create_auth_session(db, user_id=user["_id"], refresh_token=new_refresh_token, request=request)
    access_token = create_access_token(subject=user["email"])
    return Token(access_token=access_token, refresh_token=new_refresh_token)


@router.post("/logout")
def logout(payload: LogoutRequest, request: Request, db: Database = Depends(get_db)) -> dict[str, str]:
    consume_request_limit(
        key=f"auth:logout:{get_client_ip(request)}",
        max_requests=settings.AUTH_LOGOUT_RATE_LIMIT,
        window_seconds=settings.AUTH_LOGOUT_RATE_WINDOW_SECONDS,
    )
    refresh_hash = hash_refresh_token(payload.refresh_token)
    result = db["auth_sessions"].update_one(
        {"refresh_token_hash": refresh_hash, "revoked_at": None},
        {"$set": {"revoked_at": datetime.now(timezone.utc)}},
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserRead)
def read_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(to_public_user(current_user))
