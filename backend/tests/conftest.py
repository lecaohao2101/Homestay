from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from typing import Any

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import settings
from app.core.observability import reset_metrics
from app.core.rate_limit import reset_rate_limit_state
from app.main import create_application


@pytest.fixture()
def test_context() -> dict[str, Any]:
    reset_rate_limit_state()
    reset_metrics()
    db = mongomock.MongoClient().get_database("homestay_test")
    with TemporaryDirectory(prefix="homestay-media-test-") as media_dir:
        settings.MEDIA_LOCAL_DIR = media_dir

        admin_id = ObjectId()
        host_id = ObjectId()
        outsider_host_id = ObjectId()
        guest_id = ObjectId()

        admin_user = {
            "_id": admin_id,
            "email": "admin@example.com",
            "full_name": "Admin",
            "hashed_password": "x",
            "is_active": True,
            "role": "admin",
            "created_at": datetime.now(timezone.utc),
        }
        host_user = {
            "_id": host_id,
            "email": "host@example.com",
            "full_name": "Host",
            "hashed_password": "x",
            "is_active": True,
            "role": "host",
            "created_at": datetime.now(timezone.utc),
        }
        outsider_user = {
            "_id": outsider_host_id,
            "email": "host2@example.com",
            "full_name": "Host 2",
            "hashed_password": "x",
            "is_active": True,
            "role": "host",
            "created_at": datetime.now(timezone.utc),
        }
        guest_user = {
            "_id": guest_id,
            "email": "guest@example.com",
            "full_name": "Guest",
            "hashed_password": "x",
            "is_active": True,
            "role": "guest",
            "created_at": datetime.now(timezone.utc),
        }
        db["users"].insert_many([admin_user, host_user, outsider_user, guest_user])

        state: dict[str, Any] = {"current_user": admin_user}

        app = create_application()

        def _override_get_db():
            return db

        def _override_current_user():
            return state["current_user"]

        app.dependency_overrides[deps.get_db] = _override_get_db
        app.dependency_overrides[deps.get_current_user] = _override_current_user
        app.dependency_overrides[deps.get_current_active_user] = _override_current_user

        client = TestClient(app, base_url="http://localhost")
        yield {
            "client": client,
            "db": db,
            "state": state,
            "admin_user": admin_user,
            "host_user": host_user,
            "outsider_user": outsider_user,
            "guest_user": guest_user,
            "media_dir": media_dir,
        }
