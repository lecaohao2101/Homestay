def test_property_room_availability_success_flow(test_context):
    client = test_context["client"]
    host_user = test_context["host_user"]
    state = test_context["state"]

    state["current_user"] = host_user
    create_property_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Homestay Da Lat",
            "description": "Homestay view dep, gan trung tam thanh pho",
            "address": "12 Nguyen Trai",
            "city": "Da Lat",
            "country": "Vietnam",
        },
    )
    assert create_property_resp.status_code == 201
    property_id = create_property_resp.json()["id"]

    create_room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={
            "name": "Deluxe Double",
            "capacity": 2,
            "price_per_night": 800000,
            "quantity": 5,
            "description": "Phong doi cao cap",
        },
    )
    assert create_room_resp.status_code == 201
    room_id = create_room_resp.json()["id"]

    upsert_resp = client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={
            "start_date": "2026-05-01",
            "end_date": "2026-05-03",
            "available_units": 3,
            "price_per_night": 900000,
        },
    )
    assert upsert_resp.status_code == 204

    check_resp = client.get(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability/check",
        params={"check_in": "2026-05-01", "check_out": "2026-05-04", "units": 2},
    )
    assert check_resp.status_code == 200
    payload = check_resp.json()
    assert payload["is_available"] is True
    assert payload["available_nights"] == 3
    assert payload["total_price"] == 2700000.0
    assert payload["missing_dates"] == []


def test_availability_check_returns_unavailable_when_units_exceeded(test_context):
    client = test_context["client"]
    host_user = test_context["host_user"]
    state = test_context["state"]

    state["current_user"] = host_user
    property_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Homestay Nha Trang",
            "description": "Gan bien, view dep va day du tien nghi",
            "address": "99 Tran Phu",
            "city": "Nha Trang",
            "country": "Vietnam",
        },
    )
    property_id = property_resp.json()["id"]

    room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={
            "name": "Family Room",
            "capacity": 4,
            "price_per_night": 1200000,
            "quantity": 2,
        },
    )
    room_id = room_resp.json()["id"]

    client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={
            "start_date": "2026-06-10",
            "end_date": "2026-06-11",
            "available_units": 1,
            "price_per_night": 1000000,
        },
    )

    check_resp = client.get(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability/check",
        params={"check_in": "2026-06-10", "check_out": "2026-06-12", "units": 2},
    )
    assert check_resp.status_code == 200
    payload = check_resp.json()
    assert payload["is_available"] is False
    assert len(payload["missing_dates"]) == 2


def test_host_cannot_update_other_host_property_availability(test_context):
    client = test_context["client"]
    state = test_context["state"]
    host_user = test_context["host_user"]
    outsider_user = test_context["outsider_user"]

    state["current_user"] = host_user
    property_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Homestay Quy Nhon",
            "description": "Khong gian yen tinh va sach se",
            "address": "1 Nguyen Hue",
            "city": "Quy Nhon",
            "country": "Vietnam",
        },
    )
    property_id = property_resp.json()["id"]
    room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={
            "name": "Standard",
            "capacity": 2,
            "price_per_night": 500000,
            "quantity": 2,
        },
    )
    room_id = room_resp.json()["id"]

    state["current_user"] = outsider_user
    forbidden_resp = client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={
            "start_date": "2026-07-01",
            "end_date": "2026-07-01",
            "available_units": 1,
            "price_per_night": 550000,
        },
    )
    assert forbidden_resp.status_code == 403
