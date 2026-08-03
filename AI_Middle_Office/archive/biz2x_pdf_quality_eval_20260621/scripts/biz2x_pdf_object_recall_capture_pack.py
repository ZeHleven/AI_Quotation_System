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

from app.services.drawing_pdf_object_recall_capture_pack import (  # noqa: E402
    build_object_recall_capture_pack,
    write_object_recall_capture_pack_outputs,
)
from app.services.drawing_pdf_object_recall_workbench_prefill import (  # noqa: E402
    load_object_recall_workbench_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an answer-blind capture pack for object recall evidence")
    parser.add_argument("--object-workbench", required=True, help="Object recall workbench XLSX/CSV/JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "object_recall_capture_pack"),
    )
    parser.add_argument("--include-importable", action="store_true", help="Include rows already marked ready_for_import")
    parser.add_argument("--max-tasks-per-call", type=int, default=12)
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    workbench_rows = load_object_recall_workbench_rows(args.object_workbench)
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_object_recall_capture_pack_{timestamp}"
    report = build_object_recall_capture_pack(
        workbench_rows,
        include_importable=args.include_importable,
        max_tasks_per_call=args.max_tasks_per_call,
    )
    outputs = write_object_recall_capture_pack_outputs(report, args.output_dir, stem=stem)
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
