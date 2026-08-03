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

from app.services.drawing_quote_candidate_assembler import (  # noqa: E402
    DEFAULT_CABINET_JSON,
    build_quote_candidates,
    read_classified_cabinet_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BIZ-2x stage-4 quote candidates from OCR cabinet classifications.")
    parser.add_argument(
        "--classified-cabinet-json",
        default=str(ROOT / DEFAULT_CABINET_JSON),
        help="Path to stage-3 classified_ocr_cabinet.json.",
    )
    parser.add_argument("--output-dir", default="", help="Defaults to outputs/biz2x_trial/quote_candidates/<run_id>.")
    parser.add_argument(
        "--max-attachments-per-type",
        type=int,
        default=8,
        help="Maximum attached evidence rows per attachment category for each candidate.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cabinet_path = Path(args.classified_cabinet_json)
    classifications = read_classified_cabinet_json(cabinet_path)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_stage4_quote_candidates")
        output_dir = ROOT / "outputs" / "biz2x_trial" / "quote_candidates" / run_id

    report = build_quote_candidates(
        classifications=classifications,
        output_dir=output_dir,
        max_attachments_per_type=args.max_attachments_per_type,
    )
    print(json.dumps({"summary": report["summary"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
