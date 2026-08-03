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

from app.services.drawing_pdf_object_recall_capture_runner import (  # noqa: E402
    run_object_recall_capture_pack,
    write_object_recall_capture_run_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run answer-blind object recall capture tasks")
    parser.add_argument("--capture-pack-json", required=True, help="Object recall capture pack JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "object_recall_capture_run"),
    )
    parser.add_argument("--execute", action="store_true", help="Call the configured external vision model")
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--start-call-no", type=int, default=1)
    parser.add_argument("--end-call-no", type=int, default=None)
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    capture_pack = json.loads(Path(args.capture_pack_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_object_recall_capture_run_{timestamp}"
    report = run_object_recall_capture_pack(
        capture_pack,
        execute=args.execute,
        max_calls=args.max_calls,
        start_call_no=args.start_call_no,
        end_call_no=args.end_call_no,
        trace_id=args.trace_id or None,
    )
    outputs = write_object_recall_capture_run_outputs(report, args.output_dir, stem=stem)
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
