import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.database import Database

from app.api.deps import get_db
from app.schemas.search import PublicSearchResponse, SearchPropertyResult, SearchRoomResult

router = APIRouter(prefix="/search", tags=["Public Search"])


def _iter_nights(check_in: date, check_out: date):
    current = check_in
    while current < check_out:
        yield current
        current += timedelta(days=1)


@router.get("/properties", response_model=PublicSearchResponse)
def search_properties(
    q: str | None = Query(default=None, min_length=1, max_length=100),
    city: str | None = Query(default=None, min_length=2, max_length=100),
    country: str | None = Query(default=None, min_length=2, max_length=100),
    check_in: date | None = None,
    check_out: date | None = None,
    guests: int | None = Query(default=None, ge=1, le=50),
    price_min: float | None = Query(default=None, ge=0),
    price_max: float | None = Query(default=None, ge=0),
    sort: str = Query(default="relevance", pattern="^(relevance|price_asc|price_desc|newest)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
) -> PublicSearchResponse:
    if (check_in is None) != (check_out is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="check_in and check_out must be provided together",
        )
    if check_in and check_out and check_in >= check_out:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="check_in must be before check_out")
    if price_min is not None and price_max is not None and price_min > price_max:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="price_min must be <= price_max")

    filters: dict[str, Any] = {}
    if city:
        filters["city"] = city.strip()
    if country:
        filters["country"] = country.strip()
    if q:
        safe = re.escape(q.strip())
        filters["$or"] = [
            {"name": {"$regex": safe, "$options": "i"}},
            {"city": {"$regex": safe, "$options": "i"}},
            {"address": {"$regex": safe, "$options": "i"}},
            {"country": {"$regex": safe, "$options": "i"}},
        ]

    properties = list(db["properties"].find(filters))
    results: list[SearchPropertyResult] = []

    for prop in properties:
        rooms = list(db["rooms"].find({"property_id": prop["_id"]}))
        if not rooms:
            continue

        matched_rooms: list[SearchRoomResult] = []
        for room in rooms:
            if guests and int(room["capacity"]) < guests:
                continue

            if check_in and check_out:
                date_keys = [d.isoformat() for d in _iter_nights(check_in, check_out)]
                rows = list(
                    db["room_availability"].find(
                        {
                            "room_id": room["_id"],
                            "date": {"$gte": date_keys[0], "$lt": check_out.isoformat()},
                        }
                    )
                )
                if len(rows) != len(date_keys):
                    continue
                row_map = {x["date"]: x for x in rows}
                available_units = min(int(row_map[key]["available_units"]) for key in date_keys)
                if available_units <= 0:
                    continue
                nightly_price = float(row_map[date_keys[0]]["price_per_night"])
                total_price = 0.0
                valid = True
                for key in date_keys:
                    price = float(row_map[key]["price_per_night"])
                    if price_min is not None and price < price_min:
                        valid = False
                        break
                    if price_max is not None and price > price_max:
                        valid = False
                        break
                    total_price += price
                if not valid:
                    continue
                matched_rooms.append(
                    SearchRoomResult(
                        room_id=str(room["_id"]),
                        room_name=room["name"],
                        capacity=int(room["capacity"]),
                        price_per_night=nightly_price,
                        available_units=available_units,
                        total_price=round(total_price, 2),
                    )
                )
            else:
                room_price = float(room["price_per_night"])
                if price_min is not None and room_price < price_min:
                    continue
                if price_max is not None and room_price > price_max:
                    continue
                matched_rooms.append(
                    SearchRoomResult(
                        room_id=str(room["_id"]),
                        room_name=room["name"],
                        capacity=int(room["capacity"]),
                        price_per_night=room_price,
                        available_units=int(room["quantity"]),
                    )
                )

        if not matched_rooms:
            continue

        min_price = min(room.price_per_night for room in matched_rooms)
        max_capacity = max(room.capacity for room in matched_rooms)
        results.append(
            SearchPropertyResult(
                property_id=str(prop["_id"]),
                name=prop["name"],
                city=prop["city"],
                country=prop["country"],
                address=prop["address"],
                description=prop["description"],
                min_price=min_price,
                max_capacity=max_capacity,
                available=True,
                matched_rooms=matched_rooms,
            )
        )

    if sort == "price_asc":
        results.sort(key=lambda x: (x.min_price, x.name.lower()))
    elif sort == "price_desc":
        results.sort(key=lambda x: (-x.min_price, x.name.lower()))
    elif sort == "newest":
        created_map = {str(row["_id"]): row["created_at"] for row in properties}
        results.sort(key=lambda x: created_map.get(x.property_id), reverse=True)

    total = len(results)
    paged = results[skip : skip + limit]
    return PublicSearchResponse(
        items=paged,
        total=total,
        skip=skip,
        limit=limit,
        check_in=check_in,
        check_out=check_out,
    )
