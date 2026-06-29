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

from app.services.drawing_pdf_object_recall_workbench_prefill import (  # noqa: E402
    build_object_recall_workbench_prefill_report,
    load_object_recall_workbench_rows,
    write_object_recall_workbench_prefill_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely prefill object recall workbench from local PDF V2 evidence")
    parser.add_argument("--object-workbench", required=True, help="Object recall workbench XLSX/CSV/JSON")
    parser.add_argument("--v2-json", required=True, help="PDF V2 takeoff JSON containing local evidence_rows")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "object_recall_workbench_prefill"),
    )
    parser.add_argument("--match-mode", choices=["exact_tile", "source_page"], default="source_page")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing evidence_* fields")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    workbench_rows = load_object_recall_workbench_rows(args.object_workbench)
    v2_report = json.loads(Path(args.v2_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_object_recall_workbench_prefill_{timestamp}"
    report = build_object_recall_workbench_prefill_report(
        workbench_rows,
        v2_report,
        match_mode=args.match_mode,
        overwrite=args.overwrite,
    )
    outputs = write_object_recall_workbench_prefill_outputs(report, args.output_dir, stem=stem)
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
