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

from app.services.drawing_pdf_standard_bill_export import (  # noqa: E402
    build_standard_bill_preview_report,
    write_standard_bill_preview_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export BIZ-2x PDF rows in a standard bill preview workbook")
    parser.add_argument("--v2-json", required=True, help="PDF V2 takeoff JSON")
    parser.add_argument("--gate-json", default="", help="Optional three-field quality gate JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "standard_bill_preview"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    v2_report = json.loads(Path(args.v2_json).read_text(encoding="utf-8"))
    gate_report = None
    if args.gate_json:
        gate_report = json.loads(Path(args.gate_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_standard_bill_preview_{timestamp}"
    report = build_standard_bill_preview_report(v2_report, gate_report=gate_report)
    outputs = write_standard_bill_preview_outputs(report, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "phase": report["phase"],
                "safe_for_final_standard_bill": report["safe_for_final_standard_bill"],
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
