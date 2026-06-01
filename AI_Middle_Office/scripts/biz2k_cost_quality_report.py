from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.cost_item import COST_STATUS_ACTIVE, CostItem, CostRagSyncRun  # noqa: E402
from app.services.cost_data_quality import (  # noqa: E402
    analyze_cost_items_quality,
    build_demo_regression_pack,
    build_markdown_report,
    write_issues_csv,
    write_xlsx_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate BIZ-2k read-only cost_items.active quality reports.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for generated Markdown/CSV/XLSX reports. Defaults to outputs/biz2k with a local fallback.",
    )
    parser.add_argument(
        "--max-similar-pairs",
        type=int,
        default=200,
        help="Maximum similar active item pairs to include in the report.",
    )
    return parser.parse_args()


def _prepare_output_dir(output_dir_arg: str | None) -> tuple[Path, dict[str, str]]:
    if output_dir_arg:
        output_dir = Path(output_dir_arg).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir, {"requested_output_dir": str(output_dir)}

    preferred = (REPO_ROOT / "outputs" / "biz2k").resolve()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred, {"requested_output_dir": str(preferred)}
    except PermissionError:
        fallback = (BACKEND_ROOT / "biz2k_reports").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback, {"requested_output_dir": str(preferred), "fallback_output_dir": str(fallback)}


def _load_active_items_and_sync_runs() -> tuple[list[CostItem], list[CostRagSyncRun]]:
    db = SessionLocal()
    try:
        items = (
            db.query(CostItem)
            .filter(CostItem.status == COST_STATUS_ACTIVE)
            .order_by(CostItem.id.asc())
            .all()
        )
        sync_runs = (
            db.query(CostRagSyncRun)
            .order_by(CostRagSyncRun.started_at.desc(), CostRagSyncRun.id.desc())
            .limit(5)
            .all()
        )
        return items, sync_runs
    finally:
        db.close()


def main() -> int:
    args = _parse_args()
    output_dir, output_meta = _prepare_output_dir(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    items, sync_runs = _load_active_items_and_sync_runs()
    result = analyze_cost_items_quality(
        items,
        sync_runs=sync_runs,
        max_similar_pairs=max(0, args.max_similar_pairs),
    )

    markdown_path = output_dir / f"cost_quality_{timestamp}.md"
    csv_path = output_dir / f"cost_quality_issues_{timestamp}.csv"
    xlsx_path = output_dir / f"cost_quality_{timestamp}.xlsx"
    demo_path = output_dir / f"demo_regression_pack_{timestamp}.md"

    markdown_path.write_text(build_markdown_report(result), encoding="utf-8")
    demo_path.write_text(build_demo_regression_pack(result), encoding="utf-8")
    write_issues_csv(result, csv_path)
    write_xlsx_report(result, xlsx_path)

    print(
        json.dumps(
            {
                "status": "ok",
                "scope": result["scope"],
                "active_count": result["active_count"],
                "issue_count": result["issue_count"],
                "severity_counts": result["severity_counts"],
                **output_meta,
                "outputs": {
                    "markdown": str(markdown_path),
                    "csv": str(csv_path),
                    "xlsx": str(xlsx_path),
                    "demo_regression_pack": str(demo_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
