import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from bson import ObjectId

from app.core.config import settings


def _setup_property_room_with_availability(client, state, host_user):
    state["current_user"] = host_user
    create_property_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Homestay Booking Base",
            "description": "Noi o thoai mai, sach se, gan trung tam",
            "address": "88 Le Loi",
            "city": "Hue",
            "country": "Vietnam",
        },
    )
    assert create_property_resp.status_code == 201
    property_id = create_property_resp.json()["id"]
    create_room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={"name": "Suite", "capacity": 3, "price_per_night": 1000000, "quantity": 2},
    )
    assert create_room_resp.status_code == 201
    room_id = create_room_resp.json()["id"]
    availability_resp = client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "available_units": 2,
            "price_per_night": 1000000,
        },
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


def _momo_signed_query(params: dict[str, str]) -> dict[str, str]:
    if "responseTime" not in params:
        params = {**params, "responseTime": str(int(datetime.now(timezone.utc).timestamp() * 1000))}
    raw = urlencode(sorted(params.items()))
    signature = hmac.new(
        settings.MOMO_SECRET_KEY.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**params, "signature": signature}


def test_guest_can_create_booking_with_idempotency_key(test_context):
    client = test_context["client"]
    state = test_context["state"]
    host_user = test_context["host_user"]
    guest_user = test_context["guest_user"]
    property_id, room_id = _setup_property_room_with_availability(client, state, host_user)
    state["current_user"] = guest_user
    create_booking_resp = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-001"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-04", "units": 1},
    )
    assert create_booking_resp.status_code == 201
    booking_payload = create_booking_resp.json()
    assert booking_payload["status"] == "pending_payment"
    duplicate_resp = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-001"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-04", "units": 1},
    )
    assert duplicate_resp.status_code == 201
    assert duplicate_resp.json()["id"] == booking_payload["id"]


def test_create_booking_fails_when_not_enough_units(test_context):
    client = test_context["client"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    fail_resp = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-overflow"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-04", "units": 3},
    )
    assert fail_resp.status_code == 400


def test_user_cannot_cancel_other_user_booking(test_context):
    client = test_context["client"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-cancel-1"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    state["current_user"] = test_context["outsider_user"]
    forbidden_resp = client.patch(f"/api/v1/bookings/{booking_id}/cancel")
    assert forbidden_resp.status_code == 403


def test_vnpay_success_callback_confirms_booking(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-payment-ok"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    payment_resp = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id})
    txn_ref = payment_resp.json()["txn_ref"]
    ipn_resp = client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "00",
                "vnp_TransactionStatus": "00",
                "vnp_TransactionNo": "123456",
                "vnp_Amount": "200000000",
                "vnp_CurrCode": "VND",
            }
        ),
    )
    assert ipn_resp.status_code == 200
    assert db["bookings"].find_one({"_id": ObjectId(booking_id)})["status"] == "confirmed"


def test_vnpay_failed_callback_cancels_booking_and_releases_inventory(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-payment-fail"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]
    client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "24",
                "vnp_TransactionStatus": "02",
                "vnp_TransactionNo": "123457",
                "vnp_Amount": "200000000",
                "vnp_CurrCode": "VND",
            }
        ),
    )
    assert db["bookings"].find_one({"_id": ObjectId(booking_id)})["status"] == "cancelled"
    assert db["room_availability"].find_one({"room_id": ObjectId(room_id), "date": "2026-08-01"})["available_units"] == 2


def test_admin_can_expire_pending_booking_and_restore_inventory(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-expire"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    db["bookings"].update_one(
        {"_id": ObjectId(booking_id)},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}},
    )
    state["current_user"] = test_context["admin_user"]
    expire_resp = client.post("/api/v1/bookings/expire-pending")
    assert expire_resp.status_code == 200
    updated_booking = db["bookings"].find_one({"_id": ObjectId(booking_id)})
    assert updated_booking["status"] == "expired"
    assert updated_booking["cancel_reason"] == "payment_timeout"
    assert db["room_availability"].find_one({"room_id": ObjectId(room_id), "date": "2026-08-01"})["available_units"] == 2


def test_list_payment_providers_returns_vnpay_and_momo(test_context):
    client = test_context["client"]
    resp = client.get("/api/v1/payments/providers")
    assert resp.status_code == 200
    payload = resp.json()
    assert [item["code"] for item in payload["items"]] == ["vnpay", "momo"]
    assert all("create_endpoint" in item for item in payload["items"])
    assert all("display_order" in item for item in payload["items"])
    assert all("maintenance_message" in item for item in payload["items"])
    assert all("icon_url" in item for item in payload["items"])


def test_momo_success_callback_confirms_booking(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-momo-ok"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    payment_resp = client.post("/api/v1/payments/momo/create", json={"booking_id": booking_id})
    txn_ref = payment_resp.json()["txn_ref"]
    ipn_resp = client.get(
        "/api/v1/payments/momo/ipn",
        params=_momo_signed_query({"orderId": txn_ref, "resultCode": "0", "transId": "MOMO123", "amount": "2000000"}),
    )
    assert ipn_resp.status_code == 200
    assert db["bookings"].find_one({"_id": ObjectId(booking_id)})["status"] == "confirmed"


def test_momo_failed_callback_cancels_booking_and_releases_inventory(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-momo-fail"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/momo/create", json={"booking_id": booking_id}).json()["txn_ref"]
    client.get(
        "/api/v1/payments/momo/ipn",
        params=_momo_signed_query({"orderId": txn_ref, "resultCode": "1006", "transId": "MOMO124", "amount": "2000000"}),
    )
    assert db["bookings"].find_one({"_id": ObjectId(booking_id)})["status"] == "cancelled"
    assert db["room_availability"].find_one({"room_id": ObjectId(room_id), "date": "2026-08-01"})["available_units"] == 2


def test_vnpay_callback_rejects_amount_mismatch(test_context):
    client = test_context["client"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-vnpay-amount-mismatch"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]
    resp = client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "00",
                "vnp_TransactionStatus": "00",
                "vnp_TransactionNo": "123458",
                "vnp_Amount": "100",
                "vnp_CurrCode": "VND",
            }
        ),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Amount mismatch"


def test_momo_callback_duplicate_event_is_idempotent(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-momo-replay"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/momo/create", json={"booking_id": booking_id}).json()["txn_ref"]
    callback_params = _momo_signed_query({"orderId": txn_ref, "resultCode": "0", "transId": "MOMO125", "amount": "2000000"})
    first = client.get("/api/v1/payments/momo/ipn", params=callback_params)
    second = client.get("/api/v1/payments/momo/ipn", params=callback_params)
    assert first.status_code == 200
    assert second.status_code == 200
    event_count = db["payment_webhook_events"].count_documents({"provider": "momo"})
    assert event_count == 1


def test_vnpay_callback_rejects_stale_timestamp(test_context):
    client = test_context["client"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-vnpay-stale"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]
    resp = client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "00",
                "vnp_TransactionStatus": "00",
                "vnp_TransactionNo": "123499",
                "vnp_Amount": "200000000",
                "vnp_CurrCode": "VND",
                "vnp_PayDate": "20000101000000",
            }
        ),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Callback timestamp is outside tolerance window"


def test_vnpay_callback_rejects_provider_mismatch_txn_ref(test_context):
    client = test_context["client"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-provider-mismatch"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    momo_ref = client.post("/api/v1/payments/momo/create", json={"booking_id": booking_id}).json()["txn_ref"]
    resp = client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": momo_ref,
                "vnp_ResponseCode": "00",
                "vnp_TransactionStatus": "00",
                "vnp_TransactionNo": "123500",
                "vnp_Amount": "200000000",
                "vnp_CurrCode": "VND",
            }
        ),
    )
    assert resp.status_code == 404


def test_payment_webhook_rejects_disallowed_ip(test_context):
    client = test_context["client"]
    state = test_context["state"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])
    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-booking-webhook-ip-block"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-08-01", "check_out": "2026-08-03", "units": 1},
    ).json()["id"]
    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]

    previous_allowed_ips = settings.PAYMENT_WEBHOOK_ALLOWED_IPS
    settings.PAYMENT_WEBHOOK_ALLOWED_IPS = ["127.0.0.1"]
    try:
        resp = client.get(
            "/api/v1/payments/vnpay/ipn",
            params=_vnpay_signed_query(
                {
                    "vnp_TxnRef": txn_ref,
                    "vnp_ResponseCode": "00",
                    "vnp_TransactionStatus": "00",
                    "vnp_TransactionNo": "123501",
                    "vnp_Amount": "200000000",
                    "vnp_CurrCode": "VND",
                }
            ),
        )
    finally:
        settings.PAYMENT_WEBHOOK_ALLOWED_IPS = previous_allowed_ips

    assert resp.status_code == 403


def test_payment_webhook_invalid_signature_writes_dead_letter(test_context):
    client = test_context["client"]
    db = test_context["db"]
    resp = client.get(
        "/api/v1/payments/vnpay/ipn",
        params={"vnp_TxnRef": "ANY", "vnp_SecureHash": "invalid"},
    )
    assert resp.status_code == 400
    dead_letter = db["dead_letters"].find_one({"category": "payment_webhook", "source": "vnpay", "reason": "invalid_signature"})
    assert dead_letter is not None
