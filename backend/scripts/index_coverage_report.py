import argparse
import json
from typing import Any

from pymongo import MongoClient


def _summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    winning = (plan.get("queryPlanner") or {}).get("winningPlan") or {}
    execution = plan.get("executionStats") or {}
    return {
        "stage": winning.get("stage"),
        "input_stage": (winning.get("inputStage") or {}).get("stage"),
        "index_name": winning.get("indexName") or (winning.get("inputStage") or {}).get("indexName"),
        "total_docs_examined": execution.get("totalDocsExamined"),
        "total_keys_examined": execution.get("totalKeysExamined"),
        "n_returned": execution.get("nReturned"),
        "execution_time_ms": execution.get("executionTimeMillis"),
    }


def explain_query(collection, query: dict[str, Any], sort: dict[str, int] | None = None) -> dict[str, Any]:
    cursor = collection.find(query)
    if sort:
        cursor = cursor.sort(list(sort.items()))
    plan = cursor.explain()
    return _summarize_plan(plan)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mongo index coverage report")
    parser.add_argument("--mongo-uri", required=True, help="Mongo URI")
    parser.add_argument("--db-name", required=True, help="Database name")
    args = parser.parse_args()

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[args.db_name]

    probes = [
        ("users", {"email": "admin@homestay.local"}, None, "Auth lookup by email"),
        ("bookings", {"status": "pending_payment"}, {"expires_at": 1}, "Pending booking expiry scan"),
        ("payments", {"txn_ref": "NON_EXIST_TXN"}, None, "Payment webhook txn ref lookup"),
        ("refunds", {"status": "processing"}, {"created_at": -1}, "Refund processing reconciliation scan"),
        ("payment_webhook_events", {"provider": "vnpay", "event_key": "x"}, None, "Webhook idempotency key lookup"),
    ]

    reports = []
    for collection_name, query, sort, note in probes:
        summary = explain_query(db[collection_name], query=query, sort=sort)
        reports.append(
            {
                "collection": collection_name,
                "note": note,
                "query": query,
                "sort": sort,
                "summary": summary,
            }
        )

    print(json.dumps({"reports": reports}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
