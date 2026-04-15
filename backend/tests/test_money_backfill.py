import mongomock
from bson import ObjectId

from app.services.money_backfill import backfill_money_minor_fields


def test_backfill_money_minor_fields_dry_run_only_counts():
    db = mongomock.MongoClient().get_database("homestay_backfill_test")
    db["bookings"].insert_one(
        {
            "_id": ObjectId(),
            "total_price": 2000000.0,
            "original_price": 2100000.0,
            "discount_amount": 100000.0,
            "refund_amount": 50000.0,
        }
    )
    db["payments"].insert_one({"_id": ObjectId(), "amount": 1234567.0})
    db["refunds"].insert_one({"_id": ObjectId(), "amount": 700000.0})

    metrics = backfill_money_minor_fields(db, dry_run=True)
    assert metrics["bookings_updated"] == 1
    assert metrics["payments_updated"] == 1
    assert metrics["refunds_updated"] == 1
    assert db["bookings"].find_one({}).get("total_price_minor") is None


def test_backfill_money_minor_fields_apply_updates_documents():
    db = mongomock.MongoClient().get_database("homestay_backfill_test_apply")
    booking_id = ObjectId()
    payment_id = ObjectId()
    refund_id = ObjectId()
    db["bookings"].insert_one(
        {
            "_id": booking_id,
            "total_price": 2000000.0,
            "original_price": 2000000.0,
            "discount_amount": 0.0,
            "refund_amount": 200000.0,
        }
    )
    db["payments"].insert_one({"_id": payment_id, "amount": 2000000.0})
    db["refunds"].insert_one({"_id": refund_id, "amount": 200000.0})

    metrics = backfill_money_minor_fields(db, dry_run=False)
    assert metrics["total_updated"] == 3
    booking = db["bookings"].find_one({"_id": booking_id})
    payment = db["payments"].find_one({"_id": payment_id})
    refund = db["refunds"].find_one({"_id": refund_id})
    assert booking["total_price_minor"] == 2000000
    assert booking["original_price_minor"] == 2000000
    assert booking["discount_amount_minor"] == 0
    assert booking["refund_amount_minor"] == 200000
    assert payment["amount_minor"] == 2000000
    assert refund["amount_minor"] == 200000
