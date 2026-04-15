from datetime import date, datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.database import Database

from app.api.deps import get_current_active_user, get_db, require_roles
from app.core.roles import UserRole
from app.schemas.property import (
    AvailabilityCheckResponse,
    AvailabilityItem,
    AvailabilityUpsertRequest,
    PropertyCreate,
    PropertyListResponse,
    PropertyRead,
    PropertyUpdate,
    RoomCreate,
    RoomRead,
    RoomUpdate,
)

router = APIRouter(prefix="/properties", tags=["Properties"])


def _parse_object_id(value: str, message: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return ObjectId(value)


def _property_to_read(document: dict[str, Any]) -> PropertyRead:
    return PropertyRead(
        id=str(document["_id"]),
        host_id=str(document["host_id"]),
        name=document["name"],
        description=document["description"],
        address=document["address"],
        city=document["city"],
        country=document["country"],
        created_at=document["created_at"],
    )


def _room_to_read(document: dict[str, Any]) -> RoomRead:
    return RoomRead(
        id=str(document["_id"]),
        property_id=str(document["property_id"]),
        name=document["name"],
        capacity=document["capacity"],
        price_per_night=document["price_per_night"],
        quantity=document["quantity"],
        description=document.get("description"),
        created_at=document["created_at"],
    )


def _ensure_property_permission(current_user: dict[str, Any], property_doc: dict[str, Any]) -> None:
    is_admin = current_user["role"] == UserRole.ADMIN.value
    is_owner = str(property_doc["host_id"]) == str(current_user["_id"])
    if not (is_admin or is_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied for this property")


def _iter_dates(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


@router.post("", response_model=PropertyRead, status_code=status.HTTP_201_CREATED)
def create_property(
    payload: PropertyCreate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> PropertyRead:
    if current_user["role"] == UserRole.HOST.value and payload.host_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Host cannot assign property ownership",
        )

    host_id = str(current_user["_id"])
    if current_user["role"] == UserRole.ADMIN.value and payload.host_id:
        host_id = payload.host_id

    host_object_id = _parse_object_id(host_id, "Host not found")
    if not db["users"].find_one({"_id": host_object_id, "role": UserRole.HOST.value, "is_active": True}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host must be an active host account")

    document = {
        "host_id": host_object_id,
        "name": payload.name.strip(),
        "description": payload.description.strip(),
        "address": payload.address.strip(),
        "city": payload.city.strip(),
        "country": payload.country.strip(),
        "created_at": datetime.now(timezone.utc),
    }
    result = db["properties"].insert_one(document)
    created = db["properties"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create property")
    return _property_to_read(created)


@router.get("", response_model=PropertyListResponse)
def list_properties(
    city: str | None = Query(default=None, min_length=2, max_length=100),
    host_id: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> PropertyListResponse:
    filters: dict[str, Any] = {}
    if city:
        filters["city"] = city.strip()
    if host_id:
        filters["host_id"] = _parse_object_id(host_id, "Host not found")

    # Host can only view their own properties in management scope.
    if current_user["role"] == UserRole.HOST.value:
        filters["host_id"] = current_user["_id"]

    total = db["properties"].count_documents(filters)
    rows = list(db["properties"].find(filters).sort("created_at", -1).skip(skip).limit(limit))
    return PropertyListResponse(
        items=[_property_to_read(doc) for doc in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{property_id}", response_model=PropertyRead)
def get_property_detail(
    property_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> PropertyRead:
    property_doc = db["properties"].find_one({"_id": _parse_object_id(property_id, "Property not found")})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)
    return _property_to_read(property_doc)


@router.patch("/{property_id}", response_model=PropertyRead)
def update_property(
    property_id: str,
    payload: PropertyUpdate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> PropertyRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    updates = payload.model_dump(exclude_unset=True)
    sanitized = {k: (v.strip() if isinstance(v, str) else v) for k, v in updates.items()}
    if sanitized:
        db["properties"].update_one({"_id": property_oid}, {"$set": sanitized})

    updated = db["properties"].find_one({"_id": property_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return _property_to_read(updated)


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_property(
    property_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> Response:
    property_oid = _parse_object_id(property_id, "Property not found")
    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    db["rooms"].delete_many({"property_id": property_oid})
    db["properties"].delete_one({"_id": property_oid})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{property_id}/rooms", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
def create_room(
    property_id: str,
    payload: RoomCreate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> RoomRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    room_doc = {
        "property_id": property_oid,
        "name": payload.name.strip(),
        "capacity": payload.capacity,
        "price_per_night": float(payload.price_per_night),
        "quantity": payload.quantity,
        "description": payload.description.strip() if payload.description else None,
        "created_at": datetime.now(timezone.utc),
    }
    result = db["rooms"].insert_one(room_doc)
    created = db["rooms"].find_one({"_id": result.inserted_id})
    if not created:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot create room")
    return _room_to_read(created)


@router.get("/{property_id}/rooms", response_model=list[RoomRead])
def list_rooms(
    property_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> list[RoomRead]:
    property_oid = _parse_object_id(property_id, "Property not found")
    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    rooms = list(db["rooms"].find({"property_id": property_oid}).sort("created_at", -1))
    return [_room_to_read(room) for room in rooms]


@router.patch("/{property_id}/rooms/{room_id}", response_model=RoomRead)
def update_room(
    property_id: str,
    room_id: str,
    payload: RoomUpdate,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> RoomRead:
    property_oid = _parse_object_id(property_id, "Property not found")
    room_oid = _parse_object_id(room_id, "Room not found")

    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    room_doc = db["rooms"].find_one({"_id": room_oid, "property_id": property_oid})
    if not room_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    updates = payload.model_dump(exclude_unset=True)
    sanitized = {
        k: (v.strip() if isinstance(v, str) and v is not None else v)
        for k, v in updates.items()
    }
    if sanitized:
        db["rooms"].update_one({"_id": room_oid}, {"$set": sanitized})

    updated = db["rooms"].find_one({"_id": room_oid})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return _room_to_read(updated)


@router.delete("/{property_id}/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_room(
    property_id: str,
    room_id: str,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> Response:
    property_oid = _parse_object_id(property_id, "Property not found")
    room_oid = _parse_object_id(room_id, "Room not found")

    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    result = db["rooms"].delete_one({"_id": room_oid, "property_id": property_oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{property_id}/rooms/{room_id}/availability", status_code=status.HTTP_204_NO_CONTENT)
def upsert_room_availability(
    property_id: str,
    room_id: str,
    payload: AvailabilityUpsertRequest,
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_roles(UserRole.ADMIN, UserRole.HOST)),
) -> Response:
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be <= end_date")
    if (payload.end_date - payload.start_date).days > 365:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date range must not exceed 366 days",
        )

    property_oid = _parse_object_id(property_id, "Property not found")
    room_oid = _parse_object_id(room_id, "Room not found")
    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    room_doc = db["rooms"].find_one({"_id": room_oid, "property_id": property_oid})
    if not room_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    if payload.available_units > int(room_doc["quantity"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="available_units cannot exceed room quantity",
        )

    for day in _iter_dates(payload.start_date, payload.end_date):
        db["room_availability"].update_one(
            {"room_id": room_oid, "date": day.isoformat()},
            {
                "$set": {
                    "property_id": property_oid,
                    "available_units": payload.available_units,
                    "price_per_night": float(payload.price_per_night),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{property_id}/rooms/{room_id}/availability/check", response_model=AvailabilityCheckResponse)
def check_room_availability(
    property_id: str,
    room_id: str,
    check_in: date,
    check_out: date,
    units: int = Query(default=1, ge=1, le=500),
    db: Database = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_active_user),
) -> AvailabilityCheckResponse:
    if check_in >= check_out:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="check_in must be before check_out")
    if (check_out - check_in).days > 365:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stay range too large")

    property_oid = _parse_object_id(property_id, "Property not found")
    room_oid = _parse_object_id(room_id, "Room not found")
    property_doc = db["properties"].find_one({"_id": property_oid})
    if not property_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    _ensure_property_permission(current_user, property_doc)

    room_doc = db["rooms"].find_one({"_id": room_oid, "property_id": property_oid})
    if not room_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    date_keys: list[str] = []
    current_day = check_in
    while current_day < check_out:
        date_keys.append(current_day.isoformat())
        current_day += timedelta(days=1)

    docs = list(
        db["room_availability"].find(
            {
                "room_id": room_oid,
                "date": {"$gte": date_keys[0], "$lt": check_out.isoformat()},
            }
        )
    )
    doc_by_date = {row["date"]: row for row in docs}

    nightly_details: list[AvailabilityItem] = []
    missing_dates: list[date] = []
    total_price = 0.0
    is_available = True

    for key in date_keys:
        row = doc_by_date.get(key)
        day_obj = date.fromisoformat(key)
        if not row:
            is_available = False
            missing_dates.append(day_obj)
            continue

        nightly_details.append(
            AvailabilityItem(
                date=day_obj,
                available_units=int(row["available_units"]),
                price_per_night=float(row["price_per_night"]),
            )
        )
        if int(row["available_units"]) < units:
            is_available = False
            missing_dates.append(day_obj)
        total_price += float(row["price_per_night"])

    return AvailabilityCheckResponse(
        room_id=room_id,
        property_id=property_id,
        check_in=check_in,
        check_out=check_out,
        requested_units=units,
        is_available=is_available,
        total_price=round(total_price, 2),
        available_nights=len(nightly_details),
        missing_dates=missing_dates,
        nightly_details=nightly_details,
    )
