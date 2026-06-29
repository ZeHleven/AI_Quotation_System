from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.services.enterprise_quota_import import (  # noqa: E402
    EnterpriseQuotaImportError,
    save_enterprise_quota_draft_from_file,
)
from app.services.enterprise_quota_phase0 import EnterpriseQuotaPhase0Error  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BIZ-2x enterprise quota Phase 2: save Phase 0 result as a draft quota version."
    )
    parser.add_argument("quota_file", help="Enterprise quota workbook: .xls/.xlsx/.xlsm")
    parser.add_argument("--version-code", default=None, help="Optional unique version code, max 64 chars.")
    parser.add_argument("--version-name", default=None, help="Optional human-readable version name.")
    parser.add_argument("--created-by", type=int, default=None, help="Optional users.id for audit fields.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist the draft version. Omit this flag for a dry run that rolls back.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        result = save_enterprise_quota_draft_from_file(
            db,
            args.quota_file,
            version_code=args.version_code,
            version_name=args.version_name,
            created_by=args.created_by,
        )
        if args.commit:
            db.commit()
            result["dry_run"] = False
            result["message"] = "Draft enterprise quota version saved."
        else:
            db.rollback()
            result["dry_run"] = True
            result["message"] = "Dry run only; transaction was rolled back."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (EnterpriseQuotaPhase0Error, EnterpriseQuotaImportError) as exc:
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
