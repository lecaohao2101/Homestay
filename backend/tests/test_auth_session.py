from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.security import hash_refresh_token


def test_refresh_token_rotation_and_logout(test_context):
    client = test_context["client"]
    db = test_context["db"]
    user = test_context["guest_user"]

    refresh_token = "seed-refresh-token-001"
    db["auth_sessions"].insert_one(
        {
            "user_id": user["_id"],
            "refresh_token_hash": hash_refresh_token(refresh_token),
            "device_info": "pytest",
            "ip": "127.0.0.1",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
            "created_at": datetime.now(timezone.utc),
            "revoked_at": None,
        }
    )

    refresh_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_resp.status_code == 200
    payload = refresh_resp.json()
    assert payload["access_token"]
    assert payload["refresh_token"] != refresh_token

    old_reuse_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert old_reuse_resp.status_code == 401

    logout_resp = client.post("/api/v1/auth/logout", json={"refresh_token": payload["refresh_token"]})
    assert logout_resp.status_code == 200
    post_logout_reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": payload["refresh_token"]})
    assert post_logout_reuse.status_code == 401


def test_refresh_token_rate_limit(test_context):
    client = test_context["client"]
    db = test_context["db"]
    user = test_context["guest_user"]

    refresh_token = "seed-refresh-token-rate-limit"
    db["auth_sessions"].insert_one(
        {
            "user_id": user["_id"],
            "refresh_token_hash": hash_refresh_token(refresh_token),
            "device_info": "pytest",
            "ip": "127.0.0.1",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
            "created_at": datetime.now(timezone.utc),
            "revoked_at": None,
        }
    )

    previous_limit = settings.AUTH_REFRESH_RATE_LIMIT
    previous_window = settings.AUTH_REFRESH_RATE_WINDOW_SECONDS
    settings.AUTH_REFRESH_RATE_LIMIT = 1
    settings.AUTH_REFRESH_RATE_WINDOW_SECONDS = 300
    try:
        first = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        second = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    finally:
        settings.AUTH_REFRESH_RATE_LIMIT = previous_limit
        settings.AUTH_REFRESH_RATE_WINDOW_SECONDS = previous_window

    assert first.status_code == 200
    assert second.status_code == 429
