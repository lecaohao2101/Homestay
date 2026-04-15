from datetime import datetime, timedelta, timezone

from bson import ObjectId
from app.api.v1 import admin_dashboard
from app.services import money_backfill


def test_admin_can_run_money_backfill_dry_run(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    db["bookings"].insert_one(
        {
            "_id": ObjectId(),
            "user_id": test_context["guest_user"]["_id"],
            "property_id": ObjectId(),
            "room_id": ObjectId(),
            "total_price": 123456.0,
            "original_price": 123456.0,
            "discount_amount": 0.0,
            "status": "pending_payment",
            "created_at": test_context["admin_user"]["created_at"],
        }
    )
    resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor",
        json={"dry_run": True, "batch_size": 200},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dry_run"] is True
    assert payload["bookings_updated"] >= 1
    assert db["bookings"].find_one({"total_price_minor": {"$exists": True}}) is None


def test_admin_can_run_money_backfill_apply(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    booking_id = ObjectId()
    db["bookings"].insert_one(
        {
            "_id": booking_id,
            "user_id": test_context["guest_user"]["_id"],
            "property_id": ObjectId(),
            "room_id": ObjectId(),
            "total_price": 2000000.0,
            "original_price": 2000000.0,
            "discount_amount": 0.0,
            "status": "pending_payment",
            "created_at": test_context["admin_user"]["created_at"],
        }
    )
    resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor",
        json={"dry_run": False, "batch_size": 200},
    )
    assert resp.status_code == 200
    updated = db["bookings"].find_one({"_id": booking_id})
    assert updated is not None
    assert updated["total_price_minor"] == 2000000


def test_non_admin_cannot_run_money_backfill(test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["guest_user"]
    resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor",
        json={"dry_run": True, "batch_size": 200},
    )
    assert resp.status_code == 403


def test_admin_can_create_job_without_running(test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]
    resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": True, "batch_size": 100, "run_now": False},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "pending"
    assert payload["id"]


def test_admin_can_run_job_with_limited_batches_and_fetch_progress(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    for _ in range(3):
        db["payments"].insert_one({"_id": ObjectId(), "amount": 100000.0})

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": False, "batch_size": 1, "run_now": True, "max_batches": 1},
    )
    assert create_resp.status_code == 200
    created_payload = create_resp.json()
    assert created_payload["status"] in {"running", "completed"}

    get_resp = client.get(f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{created_payload['id']}")
    assert get_resp.status_code == 200
    progress_payload = get_resp.json()
    assert progress_payload["payments_scanned"] >= 1


def test_create_and_run_job_returns_404_when_runner_raises_value_error(monkeypatch, test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    def _raise_not_found(*args, **kwargs):
        raise ValueError("Backfill job not found")

    monkeypatch.setattr(admin_dashboard, "run_money_backfill_job", _raise_not_found)
    resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": False, "batch_size": 100, "run_now": True},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Backfill job not found"


def test_admin_can_resume_existing_job(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    for _ in range(2):
        db["payments"].insert_one({"_id": ObjectId(), "amount": 100000.0})

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": False, "batch_size": 1, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    run_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/run",
        json={"max_batches": 1},
    )
    assert run_resp.status_code == 200
    payload = run_resp.json()
    assert payload["payments_scanned"] >= 1


def test_run_job_returns_conflict_when_lock_is_held(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": True, "batch_size": 100, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]
    db["distributed_locks"].insert_one(
        {
            "_id": "money_minor_backfill_lock",
            "owner": "another-job",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        }
    )
    run_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/run",
        json={},
    )
    assert run_resp.status_code == 409


def test_run_job_can_takeover_expired_lock(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": True, "batch_size": 100, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]
    db["distributed_locks"].insert_one(
        {
            "_id": "money_minor_backfill_lock",
            "owner": "old-owner",
            "expires_at": datetime.now(timezone.utc) - timedelta(minutes=10),
        }
    )
    run_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/run",
        json={},
    )
    assert run_resp.status_code == 200
    payload = run_resp.json()
    assert payload["status"] in {"running", "completed"}


def test_job_transient_error_sets_pending_retry(monkeypatch, test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": False, "batch_size": 100, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    def _raise_timeout(*args, **kwargs):
        raise TimeoutError("temporary database timeout")

    monkeypatch.setattr(money_backfill, "_scan_collection_batch", _raise_timeout)
    run_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/run",
        json={},
    )
    assert run_resp.status_code == 200
    payload = run_resp.json()
    assert payload["status"] == "pending"
    assert payload["last_error_type"] == "transient"
    assert payload["retry_count"] == 1
    assert payload["next_retry_at"] is not None


def test_job_permanent_error_marks_failed(monkeypatch, test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": False, "batch_size": 100, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    def _raise_permanent(*args, **kwargs):
        raise RuntimeError("invalid schema mapping")

    monkeypatch.setattr(money_backfill, "_scan_collection_batch", _raise_permanent)
    run_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/run",
        json={},
    )
    assert run_resp.status_code == 200
    payload = run_resp.json()
    assert payload["status"] == "failed"
    assert payload["last_error_type"] == "permanent"
    assert payload["retry_count"] == 1
    assert payload["next_retry_at"] is None


def test_admin_can_force_retry_and_run_job(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": False, "batch_size": 1, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    db["money_backfill_jobs"].update_one(
        {"_id": ObjectId(job_id)},
        {
            "$set": {
                "status": "pending",
                "last_error": "temporary database timeout",
                "last_error_type": "transient",
                "retry_count": 1,
                "next_retry_at": datetime.now(timezone.utc) + timedelta(minutes=15),
            }
        },
    )
    db["payments"].insert_one({"_id": ObjectId(), "amount": 100000.0})
    force_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/force-retry",
        json={"run_now": True, "max_batches": 1},
    )
    assert force_resp.status_code == 200
    payload = force_resp.json()
    assert payload["status"] in {"running", "completed"}
    assert payload["next_retry_at"] is None

    audit_actions = [item["action"] for item in db["money_backfill_audit_logs"].find({"job_id": job_id})]
    assert "force_retry" in audit_actions
    assert "run_after_force_retry" in audit_actions


def test_cannot_force_retry_completed_job(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    create_resp = client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": True, "batch_size": 100, "run_now": False},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]
    db["money_backfill_jobs"].update_one({"_id": ObjectId(job_id)}, {"$set": {"status": "completed"}})
    force_resp = client.post(
        f"/api/v1/admin/dashboard/backfill-money-minor/jobs/{job_id}/force-retry",
        json={"run_now": False},
    )
    assert force_resp.status_code == 409


def test_admin_can_list_audit_logs_with_filters(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    job_id = str(ObjectId())
    other_job_id = str(ObjectId())
    admin_user_id = str(test_context["admin_user"]["_id"])
    db["money_backfill_audit_logs"].insert_many(
        [
            {
                "_id": ObjectId(),
                "job_id": job_id,
                "action": "force_retry",
                "admin_user_id": admin_user_id,
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {"k": "v"},
                "created_at": datetime.now(timezone.utc),
            },
            {
                "_id": ObjectId(),
                "job_id": other_job_id,
                "action": "run",
                "admin_user_id": admin_user_id,
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {},
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )

    resp = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={"job_id": job_id, "action": "force_retry", "limit": 10, "offset": 0},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    assert payload["has_next"] is False
    assert payload["next_offset"] is None
    assert payload["prev_offset"] is None
    assert len(payload["items"]) == 1
    assert payload["items"][0]["job_id"] == job_id
    assert payload["items"][0]["action"] == "force_retry"


def test_admin_can_filter_audit_logs_by_time_range(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    now = datetime.now(timezone.utc)
    old_log_time = now - timedelta(days=2)
    fresh_log_time = now - timedelta(minutes=10)
    db["money_backfill_audit_logs"].insert_many(
        [
            {
                "_id": ObjectId(),
                "job_id": str(ObjectId()),
                "action": "create",
                "admin_user_id": str(test_context["admin_user"]["_id"]),
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {},
                "created_at": old_log_time,
            },
            {
                "_id": ObjectId(),
                "job_id": str(ObjectId()),
                "action": "run",
                "admin_user_id": str(test_context["admin_user"]["_id"]),
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {},
                "created_at": fresh_log_time,
            },
        ]
    )

    resp = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={
            "created_from": (now - timedelta(hours=1)).isoformat(),
            "created_to": now.isoformat(),
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 50
    assert payload["has_next"] is False
    assert payload["next_offset"] is None
    assert payload["prev_offset"] is None
    assert payload["items"][0]["action"] == "run"


def test_audit_log_time_range_validation(test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    now = datetime.now(timezone.utc)
    resp = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={
            "created_from": now.isoformat(),
            "created_to": (now - timedelta(hours=1)).isoformat(),
        },
    )
    assert resp.status_code == 400


def test_admin_can_list_audit_logs_ascending_order(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    now = datetime.now(timezone.utc)
    first_job_id = str(ObjectId())
    second_job_id = str(ObjectId())
    db["money_backfill_audit_logs"].insert_many(
        [
            {
                "_id": ObjectId(),
                "job_id": second_job_id,
                "action": "run",
                "admin_user_id": str(test_context["admin_user"]["_id"]),
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {},
                "created_at": now,
            },
            {
                "_id": ObjectId(),
                "job_id": first_job_id,
                "action": "create",
                "admin_user_id": str(test_context["admin_user"]["_id"]),
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {},
                "created_at": now - timedelta(hours=1),
            },
        ]
    )

    resp = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={"sort_direction": "asc", "limit": 10},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["items"][0]["job_id"] == first_job_id
    assert payload["items"][1]["job_id"] == second_job_id
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    assert payload["has_next"] is False
    assert payload["next_offset"] is None
    assert payload["prev_offset"] is None


def test_audit_log_sort_direction_validation(test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]
    resp = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={"sort_direction": "invalid"},
    )
    assert resp.status_code == 400


def test_audit_log_pagination_metadata_has_next(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    now = datetime.now(timezone.utc)
    for _ in range(3):
        db["money_backfill_audit_logs"].insert_one(
            {
                "_id": ObjectId(),
                "job_id": str(ObjectId()),
                "action": "run",
                "admin_user_id": str(test_context["admin_user"]["_id"]),
                "admin_email": test_context["admin_user"]["email"],
                "metadata": {},
                "created_at": now,
            }
        )

    resp = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={"limit": 2, "offset": 0},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 3
    assert payload["page"] == 1
    assert payload["page_size"] == 2
    assert payload["has_next"] is True
    assert payload["next_offset"] == 2
    assert payload["prev_offset"] is None

    resp_page_2 = client.get(
        "/api/v1/admin/dashboard/backfill-money-minor/audit-logs",
        params={"limit": 2, "offset": 2},
    )
    assert resp_page_2.status_code == 200
    payload_page_2 = resp_page_2.json()
    assert payload_page_2["has_next"] is False
    assert payload_page_2["next_offset"] is None
    assert payload_page_2["prev_offset"] == 0
