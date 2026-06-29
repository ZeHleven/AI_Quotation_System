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

from app.services.drawing_pdf_three_field_defect_router import (  # noqa: E402
    build_three_field_defect_router_report,
    write_three_field_defect_router_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a repair routing workbook for BIZ-2x PDF three-field defects")
    parser.add_argument("--three-field-review-json", required=True, help="three_field_review JSON path")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "three_field_defect_router"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    review_report = json.loads(Path(args.three_field_review_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_three_field_defect_router_{timestamp}"
    report = build_three_field_defect_router_report(review_report)
    outputs = write_three_field_defect_router_outputs(report, Path(args.output_dir), stem=stem)
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
