from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_quantity_list_draft import (  # noqa: E402
    DEFAULT_PROCESSED_CANDIDATES_JSON,
    build_quantity_list_draft,
    read_system_processed_candidates_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BIZ-2x stage-5 four-field quantity list draft.")
    parser.add_argument(
        "--processed-candidates-json",
        default=str(ROOT / DEFAULT_PROCESSED_CANDIDATES_JSON),
        help="Path to stage-4 quote_candidates_system_processed.json.",
    )
    parser.add_argument("--output-dir", default="", help="Defaults to outputs/biz2x_trial/quantity_list_drafts/<run_id>.")
    parser.add_argument(
        "--include-decision",
        action="append",
        default=[],
        help="Candidate decision to include. Defaults to only 确认有效.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.processed_candidates_json)
    candidates = read_system_processed_candidates_json(input_path)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_stage5_quantity_list_draft")
        output_dir = ROOT / "outputs" / "biz2x_trial" / "quantity_list_drafts" / run_id
    include_decisions = tuple(args.include_decision) if args.include_decision else ("确认有效",)
    report = build_quantity_list_draft(candidates=candidates, output_dir=output_dir, include_decisions=include_decisions)
    print(json.dumps({"summary": report["summary"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
