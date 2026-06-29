from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.enterprise_quota import (  # noqa: E402
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
)
from app.services.enterprise_quota_units import normalize_enterprise_quota_unit  # noqa: E402


TARGETS = (
    ("enterprise_quota_items", EnterpriseQuotaItem),
    ("enterprise_quota_components", EnterpriseQuotaComponent),
    ("enterprise_cost_resources", EnterpriseCostResource),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize enterprise quota unit aliases, e.g. m2 -> ㎡.")
    parser.add_argument("--commit", action="store_true", help="Persist changes. Omit for dry-run.")
    parser.add_argument("--sample-limit", type=int, default=20, help="Maximum changed rows to include per table.")
    return parser.parse_args()


def _normalize_table(db, model, *, sample_limit: int) -> dict[str, Any]:
    rows = db.query(model).all()
    changed = []
    for row in rows:
        old_unit = row.unit
        new_unit = normalize_enterprise_quota_unit(old_unit)
        if new_unit != old_unit:
            row.unit = new_unit
            changed.append(
                {
                    "id": row.id,
                    "old_unit": old_unit,
                    "new_unit": new_unit,
                }
            )
    return {
        "scanned_count": len(rows),
        "changed_count": len(changed),
        "samples": changed[: max(0, sample_limit)],
    }


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        tables = {
            table_name: _normalize_table(db, model, sample_limit=args.sample_limit)
            for table_name, model in TARGETS
        }
        changed_count = sum(item["changed_count"] for item in tables.values())
        if args.commit:
            db.commit()
        else:
            db.rollback()
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": not args.commit,
                    "changed_count": changed_count,
                    "tables": tables,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(
            json.dumps(
                {
                    "ok": False,
                    "dry_run": not args.commit,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
