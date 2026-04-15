from datetime import datetime, timedelta, timezone

import mongomock
from bson import ObjectId

from app.core.config import settings
from app.services.refund_reconcile_scheduler import (
    compute_reconcile_retry_delay_seconds,
    run_reconcile_once,
    should_escalate_reconcile_failure,
)


def test_run_reconcile_once_updates_stale_processing_refund():
    db = mongomock.MongoClient().get_database("homestay_scheduler_test")
    now = datetime.now(timezone.utc)
    db["refunds"].insert_one(
        {
            "_id": ObjectId(),
            "booking_id": ObjectId(),
            "payment_id": ObjectId(),
            "amount": 1000000,
            "currency": "VND",
            "rate": 1.0,
            "status": "processing",
            "external_refund_id": "RFN-SCHED-0001",
            "created_at": now - timedelta(minutes=120),
            "updated_at": now - timedelta(minutes=120),
            "raw_callback": {"gateway_status": "succeeded"},
        }
    )

    metrics = run_reconcile_once(db)
    assert metrics["scanned"] == 1
    assert metrics["updated"] == 1
    assert metrics["succeeded"] == 1
    assert metrics["failed"] == 0
    refund_doc = db["refunds"].find_one({"external_refund_id": "RFN-SCHED-0001"})
    assert refund_doc is not None
    assert refund_doc["status"] == "succeeded"


def test_compute_reconcile_retry_delay_seconds_backoff_and_cap():
    previous_base = settings.REFUND_RECONCILE_RETRY_BASE_DELAY_SECONDS
    previous_max = settings.REFUND_RECONCILE_RETRY_MAX_DELAY_SECONDS
    previous_interval = settings.REFUND_RECONCILE_INTERVAL_SECONDS
    settings.REFUND_RECONCILE_RETRY_BASE_DELAY_SECONDS = 5
    settings.REFUND_RECONCILE_RETRY_MAX_DELAY_SECONDS = 20
    settings.REFUND_RECONCILE_INTERVAL_SECONDS = 60
    try:
        assert compute_reconcile_retry_delay_seconds(0) == 60
        assert compute_reconcile_retry_delay_seconds(1) == 5
        assert compute_reconcile_retry_delay_seconds(2) == 10
        assert compute_reconcile_retry_delay_seconds(3) == 20
        assert compute_reconcile_retry_delay_seconds(10) == 20
    finally:
        settings.REFUND_RECONCILE_RETRY_BASE_DELAY_SECONDS = previous_base
        settings.REFUND_RECONCILE_RETRY_MAX_DELAY_SECONDS = previous_max
        settings.REFUND_RECONCILE_INTERVAL_SECONDS = previous_interval


def test_should_escalate_reconcile_failure_threshold():
    previous_threshold = settings.REFUND_RECONCILE_MAX_CONSECUTIVE_FAILURES
    settings.REFUND_RECONCILE_MAX_CONSECUTIVE_FAILURES = 3
    try:
        assert should_escalate_reconcile_failure(1) is False
        assert should_escalate_reconcile_failure(2) is False
        assert should_escalate_reconcile_failure(3) is True
        assert should_escalate_reconcile_failure(5) is True
    finally:
        settings.REFUND_RECONCILE_MAX_CONSECUTIVE_FAILURES = previous_threshold
