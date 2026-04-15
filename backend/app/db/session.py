from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from app.core.config import settings
from app.core.roles import UserRole
from app.core.security import get_password_hash

_mongo_client: MongoClient | None = None


def get_mongo_client() -> MongoClient:
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
    return _mongo_client


def get_database() -> Database:
    return get_mongo_client()[settings.MONGO_DB_NAME]


def init_mongodb() -> None:
    db = get_database()
    db["users"].create_index([("email", ASCENDING)], unique=True, name="uq_users_email")
    db["users"].create_index([("role", ASCENDING)], name="idx_users_role")
    db["users"].create_index([("created_at", DESCENDING)], name="idx_users_created_at")
    db["properties"].create_index([("host_id", ASCENDING)], name="idx_properties_host_id")
    db["properties"].create_index([("created_at", DESCENDING)], name="idx_properties_created_at")
    db["properties"].create_index([("city", ASCENDING), ("country", ASCENDING)], name="idx_properties_city_country")
    db["properties"].create_index(
        [("name", "text"), ("city", "text"), ("address", "text"), ("country", "text")],
        name="txt_properties_search",
    )
    db["rooms"].create_index([("property_id", ASCENDING)], name="idx_rooms_property_id")
    db["rooms"].create_index([("created_at", DESCENDING)], name="idx_rooms_created_at")
    db["room_availability"].create_index(
        [("room_id", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_room_availability_room_date",
    )
    db["room_availability"].create_index([("date", ASCENDING), ("available_units", ASCENDING)], name="idx_availability_date_units")
    db["bookings"].create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="idx_bookings_user_created_at")
    db["bookings"].create_index([("property_id", ASCENDING), ("created_at", DESCENDING)], name="idx_bookings_property_created_at")
    db["bookings"].create_index([("room_id", ASCENDING), ("check_in", ASCENDING)], name="idx_bookings_room_checkin")
    db["bookings"].create_index([("status", ASCENDING), ("expires_at", ASCENDING)], name="idx_bookings_status_expires_at")
    db["bookings"].create_index(
        [("user_id", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$exists": True}},
        name="uq_bookings_user_idempotency_key",
    )
    db["payments"].create_index([("txn_ref", ASCENDING)], unique=True, name="uq_payments_txn_ref")
    db["payments"].create_index([("booking_id", ASCENDING), ("created_at", DESCENDING)], name="idx_payments_booking_created_at")
    db["payment_webhook_events"].create_index(
        [("provider", ASCENDING), ("event_key", ASCENDING)],
        unique=True,
        name="uq_payment_webhook_provider_event",
    )
    db["payment_webhook_events"].create_index(
        [("provider", ASCENDING), ("created_at", DESCENDING)],
        name="idx_payment_webhook_provider_created_at",
    )
    db["payment_webhook_events"].create_index(
        [("created_at", ASCENDING)],
        expireAfterSeconds=max(1, int(settings.PAYMENT_WEBHOOK_EVENTS_TTL_DAYS)) * 24 * 60 * 60,
        name="ttl_payment_webhook_events_created_at",
    )
    db["payment_audit_logs"].create_index(
        [("payment_id", ASCENDING), ("created_at", DESCENDING)],
        name="idx_payment_audit_payment_created_at",
    )
    db["payment_audit_logs"].create_index(
        [("provider", ASCENDING), ("event", ASCENDING), ("created_at", DESCENDING)],
        name="idx_payment_audit_provider_event_created_at",
    )
    db["payment_audit_logs"].create_index(
        [("created_at", ASCENDING)],
        expireAfterSeconds=max(1, int(settings.PAYMENT_AUDIT_LOGS_TTL_DAYS)) * 24 * 60 * 60,
        name="ttl_payment_audit_logs_created_at",
    )
    db["dead_letters"].create_index(
        [("category", ASCENDING), ("source", ASCENDING), ("created_at", DESCENDING)],
        name="idx_dead_letters_category_source_created_at",
    )
    db["dead_letters"].create_index(
        [("created_at", ASCENDING)],
        expireAfterSeconds=max(1, int(settings.JOB_DEAD_LETTERS_TTL_DAYS)) * 24 * 60 * 60,
        name="ttl_dead_letters_created_at",
    )
    db["money_backfill_jobs"].create_index(
        [("status", ASCENDING), ("created_at", DESCENDING)],
        name="idx_money_backfill_jobs_status_created_at",
    )
    db["money_backfill_jobs"].create_index(
        [("created_at", ASCENDING)],
        name="idx_money_backfill_jobs_created_at",
    )
    db["money_backfill_audit_logs"].create_index(
        [("job_id", ASCENDING), ("created_at", DESCENDING)],
        name="idx_money_backfill_audit_job_created_at",
    )
    db["money_backfill_audit_logs"].create_index(
        [("admin_user_id", ASCENDING), ("created_at", DESCENDING)],
        name="idx_money_backfill_audit_admin_created_at",
    )
    db["distributed_locks"].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_distributed_locks_expires_at",
    )
    db["coupons"].create_index([("code", ASCENDING)], unique=True, name="uq_coupons_code")
    db["coupons"].create_index([("active", ASCENDING), ("start_at", ASCENDING), ("end_at", ASCENDING)], name="idx_coupons_active_window")
    db["refunds"].create_index([("booking_id", ASCENDING), ("created_at", DESCENDING)], name="idx_refunds_booking_created_at")
    db["refunds"].create_index([("status", ASCENDING), ("created_at", DESCENDING)], name="idx_refunds_status_created_at")
    db["refunds"].create_index(
        [("external_refund_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"external_refund_id": {"$exists": True, "$ne": None}},
        name="uq_refunds_external_refund_id",
    )
    db["auth_sessions"].create_index([("refresh_token_hash", ASCENDING)], unique=True, name="uq_auth_sessions_refresh_hash")
    db["auth_sessions"].create_index([("user_id", ASCENDING), ("expires_at", DESCENDING)], name="idx_auth_sessions_user_exp")
    db["media_assets"].create_index([("property_id", ASCENDING), ("created_at", DESCENDING)], name="idx_media_property_created_at")
    db["media_assets"].create_index([("room_id", ASCENDING), ("created_at", DESCENDING)], name="idx_media_room_created_at")
    db["media_assets"].create_index([("storage_key", ASCENDING)], unique=True, name="uq_media_storage_key")
    db["reviews"].create_index([("property_id", ASCENDING), ("created_at", DESCENDING)], name="idx_reviews_property_created_at")
    db["reviews"].create_index(
        [("property_id", ASCENDING), ("user_id", ASCENDING)],
        unique=True,
        name="uq_reviews_property_user",
    )
    db["wishlists"].create_index(
        [("user_id", ASCENDING), ("property_id", ASCENDING)],
        unique=True,
        name="uq_wishlist_user_property",
    )
    db["wishlists"].create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="idx_wishlist_user_created_at")
    seed_default_admin(db)


def seed_default_admin(db: Database) -> None:
    """
    Seed one initial admin account in a safe idempotent way.
    """
    admin_email = settings.ADMIN_EMAIL.strip().lower()
    existing = db["users"].find_one({"email": admin_email})
    if existing:
        return

    db["users"].insert_one(
        {
            "email": admin_email,
            "full_name": settings.ADMIN_FULL_NAME.strip(),
            "hashed_password": get_password_hash(settings.ADMIN_PASSWORD),
            "is_active": True,
            "role": UserRole.ADMIN.value,
            "created_at": datetime.now(timezone.utc),
        }
    )


def ping_mongodb() -> bool:
    client = get_mongo_client()
    client.admin.command("ping")
    return True


def close_mongodb() -> None:
    global _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None
