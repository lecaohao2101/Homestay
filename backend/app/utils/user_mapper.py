from typing import Any


def to_public_user(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document["_id"]),
        "email": document["email"],
        "full_name": document["full_name"],
        "is_active": document["is_active"],
        "role": document["role"],
        "created_at": document["created_at"],
    }
