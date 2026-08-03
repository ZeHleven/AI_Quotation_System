from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_pdf_gap_recall_eval import (  # noqa: E402
    build_gap_recall_v2_evaluation,
    write_gap_recall_v2_evaluation_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a BIZ-2x PDF gap recall run by rebuilding V2 takeoff")
    parser.add_argument("--base-v2-json", required=True, help="Base PDF V2 takeoff JSON")
    parser.add_argument("--recall-run-json", required=True, help="Gap recall run JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "gap_recall_eval"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    base_v2_report = json.loads(Path(args.base_v2_json).read_text(encoding="utf-8"))
    recall_run_report = json.loads(Path(args.recall_run_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_gap_recall_v2_eval_{timestamp}"
    evaluation = build_gap_recall_v2_evaluation(
        base_v2_report,
        recall_run_report,
        style_prompt_text="gap_recall_v2_evaluation",
    )
    outputs = write_gap_recall_v2_evaluation_outputs(evaluation, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": evaluation["ok"],
                "phase": evaluation["phase"],
                "summary": evaluation["summary"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
