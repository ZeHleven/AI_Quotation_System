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

from app.services.drawing_pdf_gap_recall_importer import (  # noqa: E402
    build_gap_recall_external_import_report,
    load_external_recall_results,
    write_gap_recall_external_import_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import externally generated BIZ-2x PDF gap-recall evidence")
    parser.add_argument("--external-results", required=True, help="External recall result JSON or CSV")
    parser.add_argument("--recall-plan-json", default="", help="Optional gap recall plan JSON for call matching")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "gap_recall_import"),
    )
    parser.add_argument("--source-name", default="external_recall")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    external_results = load_external_recall_results(args.external_results)
    recall_plan = {}
    if args.recall_plan_json:
        recall_plan = json.loads(Path(args.recall_plan_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_gap_recall_external_import_{timestamp}"
    report = build_gap_recall_external_import_report(
        external_results,
        recall_plan=recall_plan,
        source_name=args.source_name,
    )
    outputs = write_gap_recall_external_import_outputs(report, Path(args.output_dir), stem=stem)
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
