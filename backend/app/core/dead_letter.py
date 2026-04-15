from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database


def write_dead_letter(
    db: Database,
    *,
    category: str,
    source: str,
    reason: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    db["dead_letters"].insert_one(
        {
            "category": category,
            "source": source,
            "reason": reason,
            "payload": payload,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc),
        }
    )
