import argparse
import json

from app.db.session import get_database
from app.services.money_backfill import backfill_money_minor_fields


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill *_minor money fields for legacy documents.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates to database. Default is dry-run.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Bulk update batch size (reserved for future tuning).",
    )
    args = parser.parse_args()

    db = get_database()
    metrics = backfill_money_minor_fields(db, dry_run=not args.apply, batch_size=max(1, args.batch_size))
    mode = "apply" if args.apply else "dry-run"
    print(json.dumps({"mode": mode, **metrics}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
