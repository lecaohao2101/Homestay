def _seed_property_room_availability(client, state, host_user, *, city: str, room_name: str, nightly_price: int):
    state["current_user"] = host_user
    prop_resp = client.post(
        "/api/v1/properties",
        json={
            "name": f"Homestay {city}",
            "description": "Homestay dep va tien nghi day du",
            "address": "1 Main Street",
            "city": city,
            "country": "Vietnam",
        },
    )
    assert prop_resp.status_code == 201
    property_id = prop_resp.json()["id"]

    room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={
            "name": room_name,
            "capacity": 2,
            "price_per_night": nightly_price,
            "quantity": 3,
        },
    )
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    avail_resp = client.put(
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        json={
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "available_units": 2,
            "price_per_night": nightly_price,
        },
    )
    assert avail_resp.status_code == 204
    return property_id


def test_public_search_by_city_and_price_sort(test_context):
    client = test_context["client"]
    state = test_context["state"]
    host_user = test_context["host_user"]

    _seed_property_room_availability(
        client,
        state,
        host_user,
        city="Da Lat",
        room_name="Standard",
        nightly_price=700000,
    )
    _seed_property_room_availability(
        client,
        state,
        host_user,
        city="Da Lat",
        room_name="Deluxe",
        nightly_price=1200000,
    )

    resp = client.get(
        "/api/v1/search/properties",
        params={"city": "Da Lat", "sort": "price_asc"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    assert payload["items"][0]["min_price"] <= payload["items"][1]["min_price"]


def test_public_search_with_date_range_guests_and_price_filter(test_context):
    client = test_context["client"]
    state = test_context["state"]
    host_user = test_context["host_user"]

    _seed_property_room_availability(
        client,
        state,
        host_user,
        city="Nha Trang",
        room_name="Budget",
        nightly_price=500000,
    )

    resp = client.get(
        "/api/v1/search/properties",
        params={
            "city": "Nha Trang",
            "check_in": "2026-09-01",
            "check_out": "2026-09-04",
            "guests": 2,
            "price_min": 400000,
            "price_max": 600000,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["matched_rooms"][0]["total_price"] == 1500000.0


def test_public_search_requires_checkin_checkout_together(test_context):
    client = test_context["client"]
    resp = client.get(
        "/api/v1/search/properties",
        params={"check_in": "2026-09-01"},
    )
    assert resp.status_code == 400
