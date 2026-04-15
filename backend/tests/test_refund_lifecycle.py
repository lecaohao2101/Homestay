import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from bson import ObjectId

from app.core.config import settings


def _setup_property_room_with_availability(client, state, host_user):
    state["current_user"] = host_user
    property_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Refund Lifecycle Villa",
            "description": "Property for refund lifecycle",
            "address": "100 Tran Phu",
            "city": "Da Nang",
            "country": "Vietnam",
        },
    )
    assert property_resp.status_code == 201
    property_id = property_resp.json()["id"]
    room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={"name": "Sea View", "capacity": 2, "price_per_night": 1000000, "quantity": 2},
    )
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]
    availability_resp = client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={"start_date": "2026-10-01", "end_date": "2026-10-05", "available_units": 2, "price_per_night": 1000000},
    )
    assert availability_resp.status_code == 204
    return property_id, room_id


def _vnpay_signed_query(params: dict[str, str]) -> dict[str, str]:
    if "vnp_PayDate" not in params and "vnp_CreateDate" not in params:
        params = {**params, "vnp_PayDate": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}
    raw = urlencode(sorted(params.items()))
    secure_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    return {**params, "vnp_SecureHash": secure_hash}


def _create_pending_refund(client, state, db, guest_user, host_user):
    property_id, room_id = _setup_property_room_with_availability(client, state, host_user)
    state["current_user"] = guest_user
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-refund-life-001"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-10-03", "check_out": "2026-10-05", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]
    client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "00",
                "vnp_TransactionStatus": "00",
                "vnp_TransactionNo": "888999",
                "vnp_Amount": "200000000",
                "vnp_CurrCode": "VND",
            }
        ),
    )
    cancel_resp = client.patch(f"/api/v1/bookings/{booking_id}/cancel")
    assert cancel_resp.status_code == 200
    refund_doc = db["refunds"].find_one({"booking_id": ObjectId(booking_id)})
    assert refund_doc is not None
    return str(refund_doc["_id"])


def test_admin_can_approve_refund_and_set_processing(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    approve_resp = client.patch(
        f"/api/v1/refunds/{refund_id}/approve",
        json={"provider": "vnpay", "external_refund_id": "RFN-2026-0001"},
    )
    assert approve_resp.status_code == 200
    payload = approve_resp.json()
    assert payload["status"] == "processing"
    assert payload["external_refund_id"] == "RFN-2026-0001"


def test_non_admin_cannot_approve_refund(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["guest_user"]
    approve_resp = client.patch(
        f"/api/v1/refunds/{refund_id}/approve",
        json={"provider": "vnpay", "external_refund_id": "RFN-2026-0002"},
    )
    assert approve_resp.status_code == 403


def test_admin_can_reject_pending_refund(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    reject_resp = client.patch(
        f"/api/v1/refunds/{refund_id}/reject",
        json={"reason": "Invalid chargeback evidence"},
    )
    assert reject_resp.status_code == 200
    payload = reject_resp.json()
    assert payload["status"] == "rejected"
    assert payload["reject_reason"] == "Invalid chargeback evidence"


def test_refund_webhook_updates_processing_to_succeeded(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    client.patch(
        f"/api/v1/refunds/{refund_id}/approve",
        json={"provider": "vnpay", "external_refund_id": "RFN-2026-0003"},
    )

    webhook_resp = client.post(
        "/api/v1/refunds/webhook",
        headers={"X-Refund-Webhook-Secret": settings.REFUND_WEBHOOK_SECRET},
        json={
            "external_refund_id": "RFN-2026-0003",
            "status": "succeeded",
            "gateway_ref": "GW-OK-1",
            "raw_payload": {"event": "refund.succeeded"},
        },
    )
    assert webhook_resp.status_code == 200
    payload = webhook_resp.json()
    assert payload["status"] == "succeeded"
    assert payload["gateway_ref"] == "GW-OK-1"


def test_refund_webhook_rejects_invalid_secret(test_context):
    client = test_context["client"]
    db = test_context["db"]
    resp = client.post(
        "/api/v1/refunds/webhook",
        headers={"X-Refund-Webhook-Secret": "wrong-secret"},
        json={"external_refund_id": "RFN-UNKNOWN", "status": "failed"},
    )
    assert resp.status_code == 401
    dead_letter = db["dead_letters"].find_one({"category": "refund_webhook", "reason": "invalid_webhook_secret"})
    assert dead_letter is not None


def test_refund_webhook_rejects_disallowed_ip(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    client.patch(
        f"/api/v1/refunds/{refund_id}/approve",
        json={"provider": "vnpay", "external_refund_id": "RFN-2026-IP-BLOCK"},
    )

    previous_allowed_ips = settings.REFUND_WEBHOOK_ALLOWED_IPS
    settings.REFUND_WEBHOOK_ALLOWED_IPS = ["127.0.0.1"]
    try:
        webhook_resp = client.post(
            "/api/v1/refunds/webhook",
            headers={"X-Refund-Webhook-Secret": settings.REFUND_WEBHOOK_SECRET},
            json={
                "external_refund_id": "RFN-2026-IP-BLOCK",
                "status": "succeeded",
                "gateway_ref": "GW-OK-IP-BLOCK",
                "raw_payload": {"event": "refund.succeeded"},
            },
        )
    finally:
        settings.REFUND_WEBHOOK_ALLOWED_IPS = previous_allowed_ips

    assert webhook_resp.status_code == 403


def test_admin_can_reconcile_stale_processing_refunds(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    client.patch(
        f"/api/v1/refunds/{refund_id}/approve",
        json={"provider": "vnpay", "external_refund_id": "RFN-2026-0004"},
    )

    stale_at = datetime.now(timezone.utc) - timedelta(minutes=settings.REFUND_RECONCILE_TIMEOUT_MINUTES + 10)
    db["refunds"].update_one(
        {"_id": ObjectId(refund_id)},
        {"$set": {"updated_at": stale_at, "raw_callback": {"gateway_status": "succeeded"}}},
    )
    reconcile_resp = client.post("/api/v1/refunds/reconcile-processing")
    assert reconcile_resp.status_code == 200
    payload = reconcile_resp.json()
    assert payload["scanned"] == 1
    assert payload["updated"] == 1
    assert payload["succeeded"] == 1
    assert payload["failed"] == 0
    assert db["refunds"].find_one({"_id": ObjectId(refund_id)})["status"] == "succeeded"


def test_reconcile_marks_stale_processing_refund_failed_without_gateway_signal(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    refund_id = _create_pending_refund(client, state, db, test_context["guest_user"], test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    client.patch(
        f"/api/v1/refunds/{refund_id}/approve",
        json={"provider": "vnpay", "external_refund_id": "RFN-2026-0005"},
    )

    stale_at = datetime.now(timezone.utc) - timedelta(minutes=settings.REFUND_RECONCILE_TIMEOUT_MINUTES + 10)
    db["refunds"].update_one({"_id": ObjectId(refund_id)}, {"$set": {"updated_at": stale_at}})

    reconcile_resp = client.post("/api/v1/refunds/reconcile-processing")
    assert reconcile_resp.status_code == 200
    assert db["refunds"].find_one({"_id": ObjectId(refund_id)})["status"] == "failed"


def test_non_admin_cannot_run_reconcile(test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["guest_user"]
    resp = client.post("/api/v1/refunds/reconcile-processing")
    assert resp.status_code == 403
