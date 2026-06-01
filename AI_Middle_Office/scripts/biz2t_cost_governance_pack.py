from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.cost_item import CostItem, CostRagSyncRun  # noqa: E402
from app.models.quote_cost_evidence import QuoteCostEvidence  # noqa: E402
from app.services.cost_data_quality import analyze_cost_items_quality  # noqa: E402
from app.services.cost_governance import (  # noqa: E402
    build_cost_governance_pack,
    build_governance_summary_markdown,
    write_governance_actions_csv,
    write_governance_actions_xlsx,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate BIZ-2t read-only cost governance execution pack.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to reports/biz2t/<timestamp>.",
    )
    parser.add_argument(
        "--max-similar-pairs",
        type=int,
        default=200,
        help="Maximum similar active item pairs inherited from the BIZ-2k quality scan.",
    )
    return parser.parse_args()


def _prepare_output_dir(output_dir_arg: str | None, timestamp: str) -> Path:
    if output_dir_arg:
        output_dir = Path(output_dir_arg).resolve()
    else:
        output_dir = (REPO_ROOT / "reports" / "biz2t" / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _load_inputs():
    db = SessionLocal()
    try:
        items = db.query(CostItem).order_by(CostItem.id.asc()).all()
        sync_runs = (
            db.query(CostRagSyncRun)
            .order_by(CostRagSyncRun.started_at.desc(), CostRagSyncRun.id.desc())
            .limit(5)
            .all()
        )
        usage_rows = (
            db.query(
                QuoteCostEvidence.cost_item_id,
                func.count(QuoteCostEvidence.id),
                func.max(QuoteCostEvidence.created_at),
            )
            .filter(QuoteCostEvidence.cost_item_id.isnot(None))
            .group_by(QuoteCostEvidence.cost_item_id)
            .all()
        )
        quote_usage = {
            int(cost_item_id): {"count": int(count or 0), "latest_used_at": latest_used_at}
            for cost_item_id, count, latest_used_at in usage_rows
            if cost_item_id
        }
        return items, sync_runs, quote_usage
    finally:
        db.close()


def main() -> int:
    args = _parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _prepare_output_dir(args.output_dir, timestamp)

    items, sync_runs, quote_usage = _load_inputs()
    quality_result = analyze_cost_items_quality(
        items,
        sync_runs=sync_runs,
        max_similar_pairs=max(0, args.max_similar_pairs),
    )
    pack = build_cost_governance_pack(items, quality_result, quote_usage=quote_usage)

    summary_path = output_dir / "cost_governance_summary.md"
    actions_csv_path = output_dir / "cost_governance_actions.csv"
    actions_xlsx_path = output_dir / "cost_governance_actions.xlsx"
    raw_json_path = output_dir / "cost_governance_raw.json"

    summary_path.write_text(build_governance_summary_markdown(pack), encoding="utf-8")
    write_governance_actions_csv(pack, actions_csv_path)
    write_governance_actions_xlsx(pack, actions_xlsx_path)
    raw_json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "output_dir": str(output_dir),
                "summary": pack["summary"],
                "trial_readiness": pack["trial_readiness"],
                "outputs": {
                    "summary": str(summary_path),
                    "actions_csv": str(actions_csv_path),
                    "actions_xlsx": str(actions_xlsx_path),
                    "raw_json": str(raw_json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
