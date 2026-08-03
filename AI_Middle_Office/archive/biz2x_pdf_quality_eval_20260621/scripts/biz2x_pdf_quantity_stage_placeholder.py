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

from app.services.drawing_pdf_quantity_stage_placeholder import (  # noqa: E402
    build_quantity_stage_placeholder_report,
    write_quantity_stage_placeholder_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export BIZ-2x PDF quantity-stage placeholder workbook")
    parser.add_argument("--standard-bill-json", required=True, help="Standard bill preview JSON")
    parser.add_argument("--v2-json", default="", help="Optional PDF V2 takeoff JSON with standard quantity rules")
    parser.add_argument("--gate-json", default="", help="Optional three-field quality gate JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "quantity_stage_placeholder"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    standard_bill = json.loads(Path(args.standard_bill_json).read_text(encoding="utf-8"))
    v2_report = json.loads(Path(args.v2_json).read_text(encoding="utf-8")) if args.v2_json else None
    gate_report = json.loads(Path(args.gate_json).read_text(encoding="utf-8")) if args.gate_json else None
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_quantity_stage_placeholder_{timestamp}"
    report = build_quantity_stage_placeholder_report(
        standard_bill,
        v2_report=v2_report,
        gate_report=gate_report,
    )
    outputs = write_quantity_stage_placeholder_outputs(report, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "phase": report["phase"],
                "safe_for_final_quantity_list": report["safe_for_final_quantity_list"],
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
