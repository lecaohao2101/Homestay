import asyncio
import logging
from datetime import datetime, timezone

from pymongo.database import Database

from app.api.v1.refunds import reconcile_processing_refunds
from app.core.config import settings
from app.core.dead_letter import write_dead_letter
from app.db.session import get_database

logger = logging.getLogger(__name__)


def run_reconcile_once(db: Database | None = None) -> dict[str, int]:
    target_db = db or get_database()
    result = reconcile_processing_refunds(target_db)
    return {
        "scanned": result.scanned,
        "updated": result.updated,
        "succeeded": result.succeeded,
        "failed": result.failed,
    }


def compute_reconcile_retry_delay_seconds(consecutive_failures: int) -> int:
    base_delay = max(1, int(settings.REFUND_RECONCILE_RETRY_BASE_DELAY_SECONDS))
    max_delay = max(base_delay, int(settings.REFUND_RECONCILE_RETRY_MAX_DELAY_SECONDS))
    if consecutive_failures <= 0:
        return max(5, int(settings.REFUND_RECONCILE_INTERVAL_SECONDS))
    retry_delay = base_delay * (2 ** (consecutive_failures - 1))
    return min(max_delay, retry_delay)


def should_escalate_reconcile_failure(consecutive_failures: int) -> bool:
    threshold = max(1, int(settings.REFUND_RECONCILE_MAX_CONSECUTIVE_FAILURES))
    return consecutive_failures >= threshold


async def refund_reconcile_worker() -> None:
    interval_seconds = max(5, int(settings.REFUND_RECONCILE_INTERVAL_SECONDS))
    logger.info("Refund reconcile worker started with interval=%ss", interval_seconds)
    consecutive_failures = 0
    while True:
        try:
            metrics = run_reconcile_once()
            consecutive_failures = 0
            if metrics["updated"] > 0:
                logger.info("Refund reconcile updated=%s succeeded=%s failed=%s", metrics["updated"], metrics["succeeded"], metrics["failed"])
            sleep_seconds = interval_seconds
        except asyncio.CancelledError:
            logger.info("Refund reconcile worker cancelled")
            raise
        except Exception:
            consecutive_failures += 1
            logger.exception("Refund reconcile worker failed")
            sleep_seconds = compute_reconcile_retry_delay_seconds(consecutive_failures)
            try:
                reason = "worker_escalated" if should_escalate_reconcile_failure(consecutive_failures) else "worker_exception"
                write_dead_letter(
                    get_database(),
                    category="job",
                    source="refund_reconcile_worker",
                    reason=reason,
                    payload={"message": "refund reconcile worker failed"},
                    metadata={
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "consecutive_failures": consecutive_failures,
                        "next_retry_delay_seconds": sleep_seconds,
                    },
                )
            except Exception:
                logger.exception("Failed to persist dead letter for refund reconcile worker")
        await asyncio.sleep(sleep_seconds)
