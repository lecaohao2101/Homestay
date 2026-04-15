from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.observability import snapshot_metrics
from app.db.session import ping_mongodb

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
def health_check() -> dict[str, str]:
    ping_mongodb()
    return {
        "status": "ok",
        "database": "connected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/live")
def liveness_check() -> dict[str, str]:
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
def readiness_check() -> dict[str, str]:
    ping_mongodb()
    return {
        "status": "ready",
        "database": "connected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics")
def metrics_snapshot() -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **snapshot_metrics(),
    }
