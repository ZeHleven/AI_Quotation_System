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

from app.services.drawing_pdf_three_field_gate import (  # noqa: E402
    build_three_field_quality_gate,
    write_three_field_quality_gate_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate whether PDF three-field acceptance can unlock quantity takeoff")
    parser.add_argument("--report-json", required=True, help="Three-field review JSON, V2 JSON, or pipeline JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "three_field_gate"),
    )
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    source_report = json.loads(Path(args.report_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_three_field_quality_gate_{timestamp}"
    gate = build_three_field_quality_gate(source_report, min_pass_rate=args.min_pass_rate)
    outputs = write_three_field_quality_gate_outputs(gate, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": gate["ok"],
                "phase": gate["phase"],
                "status": gate["status"],
                "can_enable_quantity": gate["can_enable_quantity"],
                "summary": gate["summary"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
