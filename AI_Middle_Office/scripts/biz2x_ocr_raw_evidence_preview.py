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

from app.services.drawing_ocr_raw_evidence import build_ocr_raw_evidence_repository  # noqa: E402


DEFAULT_INPUT_CSV = (
    ROOT
    / "outputs"
    / "biz2x_trial"
    / "full_text_ocr_64_snippets"
    / "20260624_005538_full_text_ocr64_snippets"
    / "outputs"
    / "all_text_evidence_64.csv"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BIZ-2x OCR raw evidence repository from full-page OCR CSV.")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV), help="Path to all_text_evidence_64.csv.")
    parser.add_argument("--output-dir", default="", help="Defaults to outputs/biz2x_trial/ocr_cabinet/<run_id>.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_csv = Path(args.input_csv)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_raw_evidence")
        output_dir = ROOT / "outputs" / "biz2x_trial" / "ocr_cabinet" / run_id
    report = build_ocr_raw_evidence_repository(input_csv=input_csv, output_dir=output_dir)
    print(json.dumps({"summary": report["summary"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
