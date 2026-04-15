import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlencode

from bson import ObjectId

from app.core.config import settings


def _setup_property_room_with_availability(client, state, host_user):
    state["current_user"] = host_user
    property_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Coupon Villa",
            "description": "Space for coupon/refund tests",
            "address": "90 Nguyen Hue",
            "city": "Da Nang",
            "country": "Vietnam",
        },
    )
    assert property_resp.status_code == 201
    property_id = property_resp.json()["id"]

    room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={"name": "Ocean Room", "capacity": 2, "price_per_night": 1000000, "quantity": 2},
    )
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    availability_resp = client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={
            "start_date": "2026-09-01",
            "end_date": "2026-09-06",
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


def test_coupon_applies_discount_on_booking(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    coupon_resp = client.post(
        "/api/v1/coupons",
        json={
            "code": "WELCOME10",
            "discount_type": "percent",
            "discount_value": 10,
            "min_booking_amount": 1000000,
            "max_uses": 2,
            "active": True,
        },
    )
    assert coupon_resp.status_code == 201

    state["current_user"] = test_context["guest_user"]
    booking_resp = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-coupon-001"},
        json={
            "property_id": property_id,
            "room_id": room_id,
            "check_in": "2026-09-02",
            "check_out": "2026-09-04",
            "units": 1,
            "coupon_code": "welcome10",
        },
    )
    assert booking_resp.status_code == 201
    payload = booking_resp.json()
    assert payload["original_price"] == 2000000
    assert payload["discount_amount"] == 200000
    assert payload["total_price"] == 1800000
    assert payload["coupon_code"] == "WELCOME10"
    assert db["coupons"].find_one({"code": "WELCOME10"})["used_count"] == 1


def test_coupon_usage_released_when_payment_failed(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])

    state["current_user"] = test_context["admin_user"]
    client.post(
        "/api/v1/coupons",
        json={"code": "LIMIT1", "discount_type": "fixed", "discount_value": 100000, "max_uses": 1, "active": True},
    )

    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-coupon-fail-001"},
        json={
            "property_id": property_id,
            "room_id": room_id,
            "check_in": "2026-09-02",
            "check_out": "2026-09-04",
            "units": 1,
            "coupon_code": "LIMIT1",
        },
    ).json()["id"]

    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]
    ipn_resp = client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "24",
                "vnp_TransactionStatus": "02",
                "vnp_TransactionNo": "888111",
                "vnp_Amount": "190000000",
                "vnp_CurrCode": "VND",
            }
        ),
    )
    assert ipn_resp.status_code == 200
    booking_doc = db["bookings"].find_one({"_id": ObjectId(booking_id)})
    assert booking_doc["status"] == "cancelled"
    assert booking_doc["coupon_usage_reserved"] is False
    assert db["coupons"].find_one({"code": "LIMIT1"})["used_count"] == 0


def test_confirmed_booking_cancel_creates_refund_record(test_context):
    client = test_context["client"]
    state = test_context["state"]
    db = test_context["db"]
    property_id, room_id = _setup_property_room_with_availability(client, state, test_context["host_user"])

    state["current_user"] = test_context["guest_user"]
    booking_id = client.post(
        "/api/v1/bookings",
        headers={"X-Idempotency-Key": "idem-refund-001"},
        json={"property_id": property_id, "room_id": room_id, "check_in": "2026-09-04", "check_out": "2026-09-06", "units": 1},
    ).json()["id"]

    txn_ref = client.post("/api/v1/payments/vnpay/create", json={"booking_id": booking_id}).json()["txn_ref"]
    client.get(
        "/api/v1/payments/vnpay/ipn",
        params=_vnpay_signed_query(
            {
                "vnp_TxnRef": txn_ref,
                "vnp_ResponseCode": "00",
                "vnp_TransactionStatus": "00",
                "vnp_TransactionNo": "999222",
                "vnp_Amount": "200000000",
                "vnp_CurrCode": "VND",
            }
        ),
    )

    cancel_resp = client.patch(f"/api/v1/bookings/{booking_id}/cancel")
    assert cancel_resp.status_code == 200
    cancelled_payload = cancel_resp.json()
    assert cancelled_payload["refund_rate"] == 1.0
    assert cancelled_payload["refund_amount"] == 2000000

    refund_doc = db["refunds"].find_one({"booking_id": ObjectId(booking_id)})
    assert refund_doc is not None
    assert float(refund_doc["amount"]) == 2000000
    assert refund_doc["status"] == "pending"
