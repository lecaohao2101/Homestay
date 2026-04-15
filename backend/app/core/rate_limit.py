from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.core.config import settings

# In-memory rate limit store for login attempts.
# Replace with Redis for multi-instance deployments.
_attempts: dict[str, list[datetime]] = {}
_locked_until: dict[str, datetime] = {}
_request_hits: dict[str, list[datetime]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_not_locked(key: str) -> None:
    locked_until = _locked_until.get(key)
    if locked_until and _now() < locked_until:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )
    if locked_until and _now() >= locked_until:
        _locked_until.pop(key, None)


def register_failed_attempt(key: str) -> None:
    now = _now()
    window_start = now - timedelta(seconds=settings.LOGIN_WINDOW_SECONDS)
    attempt_times = [ts for ts in _attempts.get(key, []) if ts >= window_start]
    attempt_times.append(now)
    _attempts[key] = attempt_times

    if len(attempt_times) >= settings.LOGIN_MAX_ATTEMPTS:
        _locked_until[key] = now + timedelta(seconds=settings.LOGIN_LOCKOUT_SECONDS)
        _attempts.pop(key, None)


def clear_attempts(key: str) -> None:
    _attempts.pop(key, None)
    _locked_until.pop(key, None)


def consume_request_limit(
    *,
    key: str,
    max_requests: int,
    window_seconds: int,
    detail: str = "Too many requests. Try again later.",
) -> None:
    if max_requests <= 0:
        return
    now = _now()
    window_start = now - timedelta(seconds=max(1, window_seconds))
    hit_times = [ts for ts in _request_hits.get(key, []) if ts >= window_start]
    if len(hit_times) >= max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
        )
    hit_times.append(now)
    _request_hits[key] = hit_times


def reset_rate_limit_state() -> None:
    _attempts.clear()
    _locked_until.clear()
    _request_hits.clear()
