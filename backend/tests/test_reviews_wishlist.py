from datetime import datetime, timezone

from bson import ObjectId


def _create_property(client, state, host_user) -> str:
    state["current_user"] = host_user
    resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Review Homestay",
            "description": "Mo ta homestay cho review",
            "address": "3 Main Street",
            "city": "HCM",
            "country": "Vietnam",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_guest_with_confirmed_booking_can_create_review_once(test_context):
    client = test_context["client"]
    db = test_context["db"]
    state = test_context["state"]
    guest_user = test_context["guest_user"]
    host_user = test_context["host_user"]

    property_id = _create_property(client, state, host_user)
    property_oid = ObjectId(property_id)
    db["bookings"].insert_one(
        {
            "user_id": guest_user["_id"],
            "property_id": property_oid,
            "room_id": ObjectId(),
            "status": "confirmed",
            "check_in": "2026-01-01",
            "check_out": "2026-01-03",
            "created_at": datetime.now(timezone.utc),
        }
    )

    state["current_user"] = guest_user
    create_resp = client.post(
        f"/api/v1/reviews/properties/{property_id}",
        json={"rating": 5, "title": "Rat tot", "comment": "Phong dep va sach"},
    )
    assert create_resp.status_code == 201

    duplicate_resp = client.post(
        f"/api/v1/reviews/properties/{property_id}",
        json={"rating": 4, "title": "Lan 2", "comment": "Khong hop le"},
    )
    assert duplicate_resp.status_code == 400


def test_guest_without_confirmed_booking_cannot_review(test_context):
    client = test_context["client"]
    state = test_context["state"]
    guest_user = test_context["guest_user"]
    host_user = test_context["host_user"]

    property_id = _create_property(client, state, host_user)
    state["current_user"] = guest_user
    resp = client.post(
        f"/api/v1/reviews/properties/{property_id}",
        json={"rating": 5, "comment": "Try review"},
    )
    assert resp.status_code == 400


def test_wishlist_add_list_remove_flow(test_context):
    client = test_context["client"]
    state = test_context["state"]
    guest_user = test_context["guest_user"]
    host_user = test_context["host_user"]

    property_id = _create_property(client, state, host_user)
    state["current_user"] = guest_user

    add_resp = client.post(f"/api/v1/wishlist/properties/{property_id}")
    assert add_resp.status_code == 201

    duplicate_resp = client.post(f"/api/v1/wishlist/properties/{property_id}")
    assert duplicate_resp.status_code == 400

    list_resp = client.get("/api/v1/wishlist")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    delete_resp = client.delete(f"/api/v1/wishlist/properties/{property_id}")
    assert delete_resp.status_code == 204

    list_after_delete = client.get("/api/v1/wishlist")
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["total"] == 0
