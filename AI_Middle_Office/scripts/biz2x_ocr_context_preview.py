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

from app.services.drawing_ocr_evidence_context_builder import (  # noqa: E402
    build_ocr_context_packages,
    read_raw_evidence_csv,
    read_raw_evidence_jsonl,
)


DEFAULT_RAW_EVIDENCE_JSONL = (
    ROOT
    / "outputs"
    / "biz2x_trial"
    / "ocr_cabinet"
    / "20260625_stage1_raw_evidence"
    / "ocr_raw_evidence.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BIZ-2x OCR current-text plus nearby-text context packages.")
    parser.add_argument("--raw-evidence-jsonl", default=str(DEFAULT_RAW_EVIDENCE_JSONL), help="Path to ocr_raw_evidence.jsonl.")
    parser.add_argument("--raw-evidence-csv", default="", help="Optional fallback path to ocr_raw_evidence.csv.")
    parser.add_argument("--output-dir", default="", help="Defaults to outputs/biz2x_trial/ocr_cabinet/<run_id>.")
    parser.add_argument("--max-nearby", type=int, default=16, help="Maximum nearby OCR evidences attached to each package.")
    parser.add_argument("--max-page-distance", type=float, default=0.08, help="Maximum page-ratio distance for spatial context candidates.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_evidences = _load_raw_evidences(args)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_context_packages")
        output_dir = ROOT / "outputs" / "biz2x_trial" / "ocr_cabinet" / run_id
    report = build_ocr_context_packages(
        raw_evidences=raw_evidences,
        output_dir=output_dir,
        max_nearby=args.max_nearby,
        max_page_distance=args.max_page_distance,
    )
    print(json.dumps({"summary": report["summary"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2), flush=True)


def _load_raw_evidences(args: argparse.Namespace) -> list[dict]:
    jsonl_path = Path(args.raw_evidence_jsonl)
    if jsonl_path.is_file():
        return read_raw_evidence_jsonl(jsonl_path)
    if args.raw_evidence_csv:
        csv_path = Path(args.raw_evidence_csv)
        if csv_path.is_file():
            return read_raw_evidence_csv(csv_path)
    raise FileNotFoundError(jsonl_path)


if __name__ == "__main__":
    main()
