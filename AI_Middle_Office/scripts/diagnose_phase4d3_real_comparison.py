from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.database import SessionLocal
from app.services.bid_hard_gate_fact_verification import (
    BidHardGateFactVerificationError,
    build_hard_gate_comparison_draft,
    preview_hard_gate_comparison_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assessment-id", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--business-baseline-id", required=True)
    parser.add_argument("--actor-id", type=int, default=1)
    parser.add_argument("--promote-overview", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        command = build_hard_gate_comparison_draft(
            db,
            assessment_id=args.assessment_id,
            source_run_id=args.source_run_id,
            business_baseline_id=args.business_baseline_id,
        )
        command.pop("schema", None)
        command["reviewed_as_of"] = datetime.now(timezone.utc)
        command["review_note"] = (
            "Phase 4D-3 real business closure diagnostic; conservative review."
        )
        for fact in command["facts"]:
            if args.promote_overview and fact["fact_slot"] == "tender.overview":
                fact["verification_status"] = "supported"
        try:
            projection = preview_hard_gate_comparison_baseline(
                db,
                actor_id=args.actor_id,
                command=command,
            )
        except BidHardGateFactVerificationError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "promote_overview": args.promote_overview,
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "ok": True,
                    "candidate_hash": projection["candidate_hash"],
                    "status_counts": projection["status_counts"],
                    "follow_up_codes": projection["follow_up_codes"],
                    "promote_overview": args.promote_overview,
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
