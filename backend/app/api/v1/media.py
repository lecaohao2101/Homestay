from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pymongo.database import Database

from app.api.deps import get_current_active_user, get_db, require_roles
from app.core.config import settings
from app.core.media_storage import get_media_storage
from app.core.roles import UserRole
from app.schemas.media import MediaListResponse, MediaRead

router = APIRouter(prefix="/media", tags=["Media"])


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _to_media_read(doc: dict[str, Any]) -> MediaRead:
    return MediaRead(
        id=str(doc["_id"]),
        owner_type=doc["owner_type"],
        property_id=str(doc["property_id"]),
        room_id=str(doc["room_id"]) if doc.get("room_id") else None,
        content_type=doc["content_type"],
        size_bytes=doc["size_bytes"],
        original_filename=doc["original_filename"],
        storage_key=doc["storage_key"],
        url=doc["url"],
        created_at=doc["created_at"],
    )


def _ensure_property_permission(db: Database, current_user: dict[str, Any], property_id: ObjectId) -> dict[str, Any]:
    prop = db["properties"].find_one({"_id": property_id})
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    is_admin = current_user["role"] == UserRole.ADMIN.value
    is_owner = str(prop["host_id"]) == str(current_user["_id"])
    if not (is_admin or is_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied for this property")
    return prop


def _validate_upload(file: UploadFile, content: bytes) -> None:
    if file.content_type not in settings.MEDIA_ALLOWED_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported media type")
    max_size = settings.MEDIA_MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large")


@router.post("/properties/{property_id}/images", response_model=MediaRead, status_code=status.HTTP_201_CREATED)
async def upload_property_image(
    property_id: str,
    file: UploadFile = File(...),
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> MediaRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    _ensure_property_permission(db, current_user, property_oid)

    content = await file.read()
    _validate_upload(file, content)

    storage = get_media_storage()
    storage_key = storage.save(content_type=file.content_type or "", data=content)
    media_doc = {
        "owner_type": "property",
        "property_id": property_oid,
        "room_id": None,
        "uploaded_by": current_user["_id"],
        "storage_provider": settings.MEDIA_STORAGE_PROVIDER,
        "storage_key": storage_key,
        "url": f"{settings.MEDIA_BASE_URL}/{storage_key}",
        "content_type": file.content_type,
        "size_bytes": len(content),
        "original_filename": file.filename or "unknown",
        "created_at": datetime.now(timezone.utc),
    }
    result = db["media_assets"].insert_one(media_doc)
    created = db["media_assets"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create media")
    return _to_media_read(created)


@router.post(
    "/properties/{property_id}/rooms/{room_id}/images",
    response_model=MediaRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_room_image(
    property_id: str,
    room_id: str,
    file: UploadFile = File(...),
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> MediaRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    room_oid = _parse_object_id(room_id, "Room not found")
    _ensure_property_permission(db, current_user, property_oid)
    room_doc = db["rooms"].find_one({"_id": room_oid, "property_id": property_oid})
    if not room_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    content = await file.read()
    _validate_upload(file, content)

    storage = get_media_storage()
    storage_key = storage.save(content_type=file.content_type or "", data=content)
    media_doc = {
        "owner_type": "room",
        "property_id": property_oid,
        "room_id": room_oid,
        "uploaded_by": current_user["_id"],
        "storage_provider": settings.MEDIA_STORAGE_PROVIDER,
        "storage_key": storage_key,
        "url": f"{settings.MEDIA_BASE_URL}/{storage_key}",
        "content_type": file.content_type,
        "size_bytes": len(content),
        "original_filename": file.filename or "unknown",
        "created_at": datetime.now(timezone.utc),
    }
    result = db["media_assets"].insert_one(media_doc)
    created = db["media_assets"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create media")
    return _to_media_read(created)


@router.get("/properties/{property_id}/images", response_model=MediaListResponse)
def list_property_images(
    property_id: str,
    db: Database = Depends(get_db),
) -> MediaListResponse:
    property_oid = _parse_object_id(property_id, "Property not found")
    if not db["properties"].find_one({"_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    rows = list(db["media_assets"].find({"owner_type": "property", "property_id": property_oid}).sort("created_at", -1))
    return MediaListResponse(items=[_to_media_read(row) for row in rows], total=len(rows))


@router.get("/properties/{property_id}/rooms/{room_id}/images", response_model=MediaListResponse)
def list_room_images(
    property_id: str,
    room_id: str,
    db: Database = Depends(get_db),
) -> MediaListResponse:
    property_oid = _parse_object_id(property_id, "Property not found")
    room_oid = _parse_object_id(room_id, "Room not found")
    if not db["rooms"].find_one({"_id": room_oid, "property_id": property_oid}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    rows = list(
        db["media_assets"]
        .find({"owner_type": "room", "property_id": property_oid, "room_id": room_oid})
        .sort("created_at", -1)
    )
    return MediaListResponse(items=[_to_media_read(row) for row in rows], total=len(rows))


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    media_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> None:
    media_oid = _parse_object_id(media_id, "Media not found")
    media_doc = db["media_assets"].find_one({"_id": media_oid})
    if not media_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    _ensure_property_permission(db, current_user, media_doc["property_id"])
    get_media_storage().delete(media_doc["storage_key"])
    db["media_assets"].delete_one({"_id": media_oid})
    return None


@router.get("/files/{storage_key}")
def get_media_file(storage_key: str):
    storage = get_media_storage()
    path = storage.get_path(storage_key)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media file not found")
    return FileResponse(path=str(path))
