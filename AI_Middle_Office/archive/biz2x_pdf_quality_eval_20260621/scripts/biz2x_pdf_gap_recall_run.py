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

from app.services.drawing_pdf_gap_recall_runner import (  # noqa: E402
    run_gap_recall_plan,
    write_gap_recall_run_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or dry-run a BIZ-2x PDF gap recall visual plan")
    parser.add_argument("--recall-plan-json", required=True, help="Gap recall plan JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "gap_recall_run"),
    )
    parser.add_argument("--execute", action="store_true", help="Actually call the configured vision model")
    parser.add_argument("--max-calls", type=int, default=0, help="Optional cap on unique visual calls; 0 means no cap")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    recall_plan = json.loads(Path(args.recall_plan_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "execute" if args.execute else "dry_run"
    stem = args.stem or f"BIZ2x_PDF_gap_recall_{mode}_{timestamp}"
    report = run_gap_recall_plan(
        recall_plan,
        execute=args.execute,
        max_calls=args.max_calls or None,
        trace_id=f"biz2x-gap-recall-{timestamp}",
    )
    outputs = write_gap_recall_run_outputs(report, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "phase": report["phase"],
                "summary": report["summary"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
