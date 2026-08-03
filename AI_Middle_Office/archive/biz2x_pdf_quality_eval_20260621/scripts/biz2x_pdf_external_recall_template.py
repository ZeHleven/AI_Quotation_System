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

from app.services.drawing_pdf_external_recall_template import (  # noqa: E402
    build_external_recall_template,
    write_external_recall_template_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a safe BIZ-2x PDF external recall filling template from a gap recall plan"
    )
    parser.add_argument("--recall-plan-json", required=True, help="Gap recall plan JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "external_recall_template"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    recall_plan = json.loads(Path(args.recall_plan_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_external_recall_template_{timestamp}"
    report = build_external_recall_template(recall_plan)
    outputs = write_external_recall_template_outputs(report, Path(args.output_dir), stem=stem)
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
