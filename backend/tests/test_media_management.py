from pathlib import Path

from bson import ObjectId


def _create_property_and_room(client, state, host_user):
    state["current_user"] = host_user
    prop_resp = client.post(
        "/api/v1/properties",
        json={
            "name": "Media Homestay",
            "description": "Mo ta cho media test",
            "address": "2 Main Street",
            "city": "Da Nang",
            "country": "Vietnam",
        },
    )
    assert prop_resp.status_code == 201
    property_id = prop_resp.json()["id"]

    room_resp = client.post(
        f"/api/v1/properties/{property_id}/rooms",
        json={
            "name": "Media Room",
            "capacity": 2,
            "price_per_night": 600000,
            "quantity": 2,
        },
    )
    assert room_resp.status_code == 201
    return property_id, room_resp.json()["id"]


def test_host_can_upload_and_list_property_images(test_context):
    client = test_context["client"]
    state = test_context["state"]
    host_user = test_context["host_user"]

    property_id, _ = _create_property_and_room(client, state, host_user)
    upload_resp = client.post(
        f"/api/v1/media/properties/{property_id}/images",
        files={"file": ("property.jpg", b"\xff\xd8\xff\xdb", "image/jpeg")},
    )
    assert upload_resp.status_code == 201
    payload = upload_resp.json()
    assert payload["owner_type"] == "property"
    assert payload["url"].startswith("/api/v1/media/files/")

    list_resp = client.get(f"/api/v1/media/properties/{property_id}/images")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1


def test_outsider_host_cannot_upload_media_to_other_property(test_context):
    client = test_context["client"]
    state = test_context["state"]
    host_user = test_context["host_user"]
    outsider = test_context["outsider_user"]

    property_id, room_id = _create_property_and_room(client, state, host_user)
    state["current_user"] = outsider
    resp = client.post(
        f"/api/v1/media/properties/{property_id}/rooms/{room_id}/images",
        files={"file": ("room.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert resp.status_code == 403


def test_delete_media_removes_file_and_metadata(test_context):
    client = test_context["client"]
    db = test_context["db"]
    media_dir = Path(test_context["media_dir"])
    state = test_context["state"]
    host_user = test_context["host_user"]

    property_id, _ = _create_property_and_room(client, state, host_user)
    upload_resp = client.post(
        f"/api/v1/media/properties/{property_id}/images",
        files={"file": ("delete.webp", b"RIFF....WEBP", "image/webp")},
    )
    media_id = upload_resp.json()["id"]
    storage_key = upload_resp.json()["storage_key"]

    media_path = media_dir / storage_key
    assert media_path.exists()

    delete_resp = client.delete(f"/api/v1/media/{media_id}")
    assert delete_resp.status_code == 204
    assert db["media_assets"].find_one({"_id": ObjectId(media_id)}) is None
    assert not media_path.exists()
