from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_quote_candidate_assembler import (  # noqa: E402
    read_quote_candidates_json,
    write_system_processed_candidates,
)


DEFAULT_QUOTE_CANDIDATES_JSON = ROOT / (
    "outputs/biz2x_trial/quote_candidates/"
    "20260626_stage4_quote_candidates_full2624_mvp/quote_candidates.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply system suggestions to BIZ-2x stage-4 quote candidates.")
    parser.add_argument(
        "--quote-candidates-json",
        default=str(DEFAULT_QUOTE_CANDIDATES_JSON),
        help="Path to stage-4 quote_candidates.json.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Defaults to the same directory as quote_candidates.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.quote_candidates_json)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    parsed = read_quote_candidates_json(input_path)
    report = write_system_processed_candidates(output_dir, parsed["quote_candidates"])
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
