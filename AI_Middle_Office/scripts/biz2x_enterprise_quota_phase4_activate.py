from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.services.enterprise_quota_activation import (  # noqa: E402
    EnterpriseQuotaActivationError,
    build_enterprise_quota_activation_plan,
    run_enterprise_quota_activation,
)


DEFAULT_BACKUP_DIR = BACKEND_ROOT / "outputs" / "biz2x_enterprise_quota_phase4_backups"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "BIZ-2x enterprise quota Phase 4A: activation plan, dry-run, "
            "old cost DB backup, and controlled activation."
        )
    )
    parser.add_argument("--version-id", type=int, required=True, help="Target enterprise_quota_versions.id.")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Only print the activation plan. No backup, no mutation.",
    )
    parser.add_argument(
        "--clear-old-cost-db",
        action="store_true",
        help="Clear legacy cost_items/cost_item_history as part of activation.",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help="Directory for old cost DB JSON backup. Required before --commit when clearing old cost DB.",
    )
    parser.add_argument("--actor-id", type=int, default=None, help="Optional users.id for activation audit fields.")
    parser.add_argument("--reason", default=None, help="Optional activation reason written to version notes/backup.")
    parser.add_argument(
        "--acknowledge-warnings",
        action="store_true",
        help="Required with --commit when the activation plan has warning checks.",
    )
    parser.add_argument(
        "--confirm-code",
        default=None,
        help="Required with --commit. The expected value is printed by --plan or dry-run.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist activation. Omit for dry-run; dry-run flushes mutations then rolls back.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        if args.plan:
            plan = build_enterprise_quota_activation_plan(
                db,
                args.version_id,
                clear_old_cost_db=args.clear_old_cost_db,
            )
            print(json.dumps({"ok": True, "mode": "plan", "plan": plan}, ensure_ascii=False, indent=2))
            return 0

        result = run_enterprise_quota_activation(
            db,
            args.version_id,
            clear_old_cost_db=args.clear_old_cost_db,
            backup_dir=args.backup_dir if args.clear_old_cost_db else None,
            confirm_code=args.confirm_code,
            acknowledge_warnings=args.acknowledge_warnings,
            actor_id=args.actor_id,
            reason=args.reason,
            commit=args.commit,
        )
        if args.commit:
            db.commit()
            result["dry_run"] = False
            result["message"] = "Enterprise quota version activated and old cost DB cleanup committed."
        else:
            db.rollback()
            result["dry_run"] = True
            result["message"] = "Dry run only; transaction was rolled back. Backup file, if any, was kept."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except EnterpriseQuotaActivationError as exc:
        db.rollback()
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                    "dry_run": not args.commit,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
