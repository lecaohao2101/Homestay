from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.utils.money import to_vnd_minor

BACKFILL_LOCK_ID = "money_minor_backfill_lock"
BACKFILL_LOCK_LEASE_SECONDS = 300


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_booking_set_fields(doc: dict[str, Any]) -> dict[str, int]:
    set_fields: dict[str, int] = {}
    if doc.get("total_price_minor") is None and doc.get("total_price") is not None:
        set_fields["total_price_minor"] = to_vnd_minor(doc["total_price"])
    if doc.get("original_price_minor") is None and doc.get("original_price") is not None:
        set_fields["original_price_minor"] = to_vnd_minor(doc["original_price"])
    if doc.get("discount_amount_minor") is None and doc.get("discount_amount") is not None:
        set_fields["discount_amount_minor"] = to_vnd_minor(doc["discount_amount"])
    if doc.get("refund_amount_minor") is None and doc.get("refund_amount") is not None:
        set_fields["refund_amount_minor"] = to_vnd_minor(doc["refund_amount"])
    return set_fields


def _build_simple_set_fields(doc: dict[str, Any], *, amount_field: str, minor_field: str) -> dict[str, int]:
    if doc.get(minor_field) is None and doc.get(amount_field) is not None:
        return {minor_field: to_vnd_minor(doc[amount_field])}
    return {}


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, PyMongoError)):
        return True
    message = str(exc).lower()
    retryable_signals = ("timed out", "timeout", "temporary", "connection reset", "network")
    return any(signal in message for signal in retryable_signals)


def backfill_money_minor_fields(db: Database, *, dry_run: bool = True, batch_size: int = 500) -> dict[str, int]:
    metrics = {
        "bookings_scanned": 0,
        "bookings_updated": 0,
        "payments_scanned": 0,
        "payments_updated": 0,
        "refunds_scanned": 0,
        "refunds_updated": 0,
        "total_updated": 0,
    }

    for doc in db["bookings"].find({}, {"total_price": 1, "original_price": 1, "discount_amount": 1, "refund_amount": 1, "total_price_minor": 1, "original_price_minor": 1, "discount_amount_minor": 1, "refund_amount_minor": 1}):
        metrics["bookings_scanned"] += 1
        set_fields = _build_booking_set_fields(doc)
        if not set_fields:
            continue
        metrics["bookings_updated"] += 1
        if not dry_run:
            db["bookings"].update_one({"_id": doc["_id"]}, {"$set": set_fields})

    for doc in db["payments"].find({}, {"amount": 1, "amount_minor": 1}):
        metrics["payments_scanned"] += 1
        set_fields = _build_simple_set_fields(doc, amount_field="amount", minor_field="amount_minor")
        if not set_fields:
            continue
        metrics["payments_updated"] += 1
        if not dry_run:
            db["payments"].update_one({"_id": doc["_id"]}, {"$set": set_fields})

    for doc in db["refunds"].find({}, {"amount": 1, "amount_minor": 1}):
        metrics["refunds_scanned"] += 1
        set_fields = _build_simple_set_fields(doc, amount_field="amount", minor_field="amount_minor")
        if not set_fields:
            continue
        metrics["refunds_updated"] += 1
        if not dry_run:
            db["refunds"].update_one({"_id": doc["_id"]}, {"$set": set_fields})

    metrics["total_updated"] = metrics["bookings_updated"] + metrics["payments_updated"] + metrics["refunds_updated"]
    return metrics


def create_money_backfill_job(
    db: Database,
    *,
    dry_run: bool,
    batch_size: int,
) -> str:
    now = datetime.now(timezone.utc)
    result = db["money_backfill_jobs"].insert_one(
        {
            "status": "pending",
            "dry_run": dry_run,
            "batch_size": batch_size,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "last_error": None,
            "last_error_type": None,
            "retry_count": 0,
            "next_retry_at": None,
            "bookings_scanned": 0,
            "bookings_updated": 0,
            "payments_scanned": 0,
            "payments_updated": 0,
            "refunds_scanned": 0,
            "refunds_updated": 0,
            "total_updated": 0,
            "cursor": {"bookings_last_id": None, "payments_last_id": None, "refunds_last_id": None},
            "cursor_done": {"bookings_done": False, "payments_done": False, "refunds_done": False},
            "created_at": now,
            "updated_at": now,
        }
    )
    return str(result.inserted_id)


def _scan_collection_batch(
    db: Database,
    *,
    collection: str,
    projection: dict[str, int],
    last_id: ObjectId | None,
    batch_size: int,
) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {}
    if last_id is not None:
        filters["_id"] = {"$gt": last_id}
    return list(db[collection].find(filters, projection).sort("_id", 1).limit(batch_size))


def run_money_backfill_job(db: Database, *, job_id: str, max_batches: int | None = None) -> dict[str, Any]:
    if not ObjectId.is_valid(job_id):
        raise ValueError("Invalid job id")
    job_oid = ObjectId(job_id)
    job_doc = db["money_backfill_jobs"].find_one({"_id": job_oid})
    if not job_doc:
        raise ValueError("Job not found")
    if job_doc["status"] == "completed":
        return job_doc
    next_retry_at = _coerce_utc(job_doc.get("next_retry_at"))
    if next_retry_at is not None and next_retry_at > datetime.now(timezone.utc):
        raise RuntimeError(f"Job is scheduled for retry at {next_retry_at.isoformat()}")

    owner = str(job_oid)
    lock = _acquire_backfill_lock(db, owner=owner)
    if not lock:
        raise RuntimeError("Another money backfill job is already running")

    dry_run = bool(job_doc["dry_run"])
    batch_size = int(job_doc["batch_size"])
    cursor_state = job_doc.get("cursor", {})
    done_state = job_doc.get("cursor_done", {"bookings_done": False, "payments_done": False, "refunds_done": False})

    started = perf_counter()
    db["money_backfill_jobs"].update_one(
        {"_id": job_oid},
        {
            "$set": {
                "status": "running",
                "started_at": job_doc.get("started_at") or datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "last_error": None,
                "last_error_type": None,
                "next_retry_at": None,
            }
        },
    )

    batches_left = max_batches if (max_batches is not None and max_batches > 0) else None
    collections_plan = [
        (
            "bookings",
            {"total_price": 1, "original_price": 1, "discount_amount": 1, "refund_amount": 1, "total_price_minor": 1, "original_price_minor": 1, "discount_amount_minor": 1, "refund_amount_minor": 1},
            "bookings_last_id",
            "bookings_done",
            _build_booking_set_fields,
            "bookings_scanned",
            "bookings_updated",
        ),
        (
            "payments",
            {"amount": 1, "amount_minor": 1},
            "payments_last_id",
            "payments_done",
            lambda doc: _build_simple_set_fields(doc, amount_field="amount", minor_field="amount_minor"),
            "payments_scanned",
            "payments_updated",
        ),
        (
            "refunds",
            {"amount": 1, "amount_minor": 1},
            "refunds_last_id",
            "refunds_done",
            lambda doc: _build_simple_set_fields(doc, amount_field="amount", minor_field="amount_minor"),
            "refunds_scanned",
            "refunds_updated",
        ),
    ]

    try:
        for collection, projection, cursor_key, done_key, set_builder, scanned_key, updated_key in collections_plan:
            if done_state.get(done_key):
                continue
            while True:
                if batches_left is not None and batches_left <= 0:
                    break
                _refresh_backfill_lock(db, owner=owner)
                last_id_raw = cursor_state.get(cursor_key)
                last_id = ObjectId(last_id_raw) if last_id_raw else None
                rows = _scan_collection_batch(
                    db,
                    collection=collection,
                    projection=projection,
                    last_id=last_id,
                    batch_size=batch_size,
                )
                if not rows:
                    done_state[done_key] = True
                    db["money_backfill_jobs"].update_one(
                        {"_id": job_oid},
                        {"$set": {f"cursor_done.{done_key}": True, "updated_at": datetime.now(timezone.utc)}},
                    )
                    break

                scanned_inc = 0
                updated_inc = 0
                new_last_id: ObjectId | None = None
                for row in rows:
                    scanned_inc += 1
                    new_last_id = row["_id"]
                    set_fields = set_builder(row)
                    if not set_fields:
                        continue
                    updated_inc += 1
                    if not dry_run:
                        db[collection].update_one({"_id": row["_id"]}, {"$set": set_fields})

                cursor_state[cursor_key] = str(new_last_id) if new_last_id else cursor_state.get(cursor_key)
                db["money_backfill_jobs"].update_one(
                    {"_id": job_oid},
                    {
                        "$inc": {scanned_key: scanned_inc, updated_key: updated_inc},
                        "$set": {f"cursor.{cursor_key}": cursor_state[cursor_key], f"cursor_done.{done_key}": False, "updated_at": datetime.now(timezone.utc)},
                    },
                )
                done_state[done_key] = False
                if batches_left is not None:
                    batches_left -= 1

            if batches_left is not None and batches_left <= 0:
                break

        updated_job = db["money_backfill_jobs"].find_one({"_id": job_oid})
        if not updated_job:
            raise ValueError("Job not found after update")

        now = datetime.now(timezone.utc)
        completed = all(updated_job.get("cursor_done", {}).get(key, False) for key in ("bookings_done", "payments_done", "refunds_done"))
        duration_ms = int((perf_counter() - started) * 1000)
        db["money_backfill_jobs"].update_one(
            {"_id": job_oid},
            {
                "$set": {
                    "status": "completed" if completed else "running",
                    "duration_ms": (updated_job.get("duration_ms") or 0) + duration_ms,
                    "finished_at": now if completed else None,
                    "total_updated": int(updated_job.get("bookings_updated", 0))
                    + int(updated_job.get("payments_updated", 0))
                    + int(updated_job.get("refunds_updated", 0)),
                    "updated_at": now,
                }
            },
        )
        final_job = db["money_backfill_jobs"].find_one({"_id": job_oid})
        if not final_job:
            raise ValueError("Final job not found")
        return final_job
    except Exception as exc:
        now = datetime.now(timezone.utc)
        duration_ms = int((perf_counter() - started) * 1000)
        latest_job = db["money_backfill_jobs"].find_one({"_id": job_oid}) or job_doc
        retry_count = int(latest_job.get("retry_count", 0)) + 1
        max_retries = max(0, int(settings.MONEY_BACKFILL_MAX_RETRIES))
        retryable = _is_retryable_error(exc)
        if retryable and retry_count <= max_retries:
            retry_delay = max(1, int(settings.MONEY_BACKFILL_RETRY_DELAY_SECONDS)) * (2 ** (retry_count - 1))
            next_retry_at = now + timedelta(seconds=retry_delay)
            db["money_backfill_jobs"].update_one(
                {"_id": job_oid},
                {
                    "$set": {
                        "status": "pending",
                        "last_error": str(exc),
                        "last_error_type": "transient",
                        "retry_count": retry_count,
                        "next_retry_at": next_retry_at,
                        "duration_ms": int(latest_job.get("duration_ms") or 0) + duration_ms,
                        "finished_at": None,
                        "updated_at": now,
                    }
                },
            )
            retry_job = db["money_backfill_jobs"].find_one({"_id": job_oid})
            if not retry_job:
                raise ValueError("Job not found after retry update")
            return retry_job

        db["money_backfill_jobs"].update_one(
            {"_id": job_oid},
            {
                "$set": {
                    "status": "failed",
                    "last_error": str(exc),
                    "last_error_type": "transient" if retryable else "permanent",
                    "retry_count": retry_count,
                    "next_retry_at": None,
                    "duration_ms": int(latest_job.get("duration_ms") or 0) + duration_ms,
                    "finished_at": now,
                    "updated_at": now,
                }
            },
        )
        failed_job = db["money_backfill_jobs"].find_one({"_id": job_oid})
        if not failed_job:
            raise ValueError("Job not found after failure update")
        return failed_job
    finally:
        _release_backfill_lock(db, owner=owner)


def force_retry_money_backfill_job(db: Database, *, job_id: str) -> dict[str, Any]:
    if not ObjectId.is_valid(job_id):
        raise ValueError("Invalid job id")
    job_oid = ObjectId(job_id)
    now = datetime.now(timezone.utc)
    job_doc = db["money_backfill_jobs"].find_one({"_id": job_oid})
    if not job_doc:
        raise ValueError("Job not found")
    if job_doc.get("status") == "completed":
        raise RuntimeError("Completed job cannot be force-retried")
    if job_doc.get("status") == "running":
        raise RuntimeError("Running job cannot be force-retried")

    db["money_backfill_jobs"].update_one(
        {"_id": job_oid},
        {
            "$set": {
                "status": "pending",
                "next_retry_at": None,
                "updated_at": now,
            }
        },
    )
    updated = db["money_backfill_jobs"].find_one({"_id": job_oid})
    if not updated:
        raise ValueError("Job not found after force-retry update")
    return updated


def _acquire_backfill_lock(db: Database, *, owner: str, lease_seconds: int = BACKFILL_LOCK_LEASE_SECONDS) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(30, lease_seconds))
    now_ts = now.timestamp()
    expires_at_ts = expires_at.timestamp()
    locks = db["distributed_locks"]
    current = locks.find_one({"_id": BACKFILL_LOCK_ID})
    if current is None:
        try:
            locks.insert_one(
                {
                    "_id": BACKFILL_LOCK_ID,
                    "owner": owner,
                    "expires_at": expires_at,
                    "expires_at_ts": expires_at_ts,
                    "updated_at": now,
                }
            )
            return locks.find_one({"_id": BACKFILL_LOCK_ID})
        except DuplicateKeyError:
            current = locks.find_one({"_id": BACKFILL_LOCK_ID})

    if current and current.get("owner") not in {None, owner}:
        current_expires_ts = current.get("expires_at_ts")
        if current_expires_ts is None:
            current_expires = _coerce_utc(current.get("expires_at"))
            current_expires_ts = current_expires.timestamp() if current_expires else None
        if current_expires_ts is not None and float(current_expires_ts) > now_ts:
            return None

    updated = locks.find_one_and_update(
        {
            "_id": BACKFILL_LOCK_ID,
            "$or": [
                {"owner": owner},
                {"expires_at_ts": {"$lte": now_ts}},
                {"expires_at": {"$lte": now}},
                {"owner": {"$exists": False}},
            ],
        },
        {"$set": {"owner": owner, "expires_at": expires_at, "expires_at_ts": expires_at_ts, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return updated


def _release_backfill_lock(db: Database, *, owner: str) -> None:
    db["distributed_locks"].delete_one({"_id": BACKFILL_LOCK_ID, "owner": owner})


def _refresh_backfill_lock(db: Database, *, owner: str, lease_seconds: int = BACKFILL_LOCK_LEASE_SECONDS) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(30, lease_seconds))
    expires_at_ts = expires_at.timestamp()
    result = db["distributed_locks"].update_one(
        {"_id": BACKFILL_LOCK_ID, "owner": owner},
        {"$set": {"expires_at": expires_at, "expires_at_ts": expires_at_ts, "updated_at": now}},
    )
    if result.modified_count != 1:
        raise RuntimeError("Backfill lock was lost during execution")
