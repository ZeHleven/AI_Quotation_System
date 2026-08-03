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

from app.services.drawing_pdf_closed_loop_stage_report import (  # noqa: E402
    build_closed_loop_stage_report,
    load_json_report,
    write_closed_loop_stage_report_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BIZ-2x PDF seven-stage closed-loop status report")
    parser.add_argument("--pipeline-json", default="", help="Optional combined pipeline JSON")
    parser.add_argument("--v2-json", default="", help="Optional V2 takeoff JSON")
    parser.add_argument("--template-status-json", default="", help="Optional external/object recall template status JSON")
    parser.add_argument("--import-json", default="", help="Optional external import JSON")
    parser.add_argument("--eval-json", default="", help="Optional gap recall evaluation JSON")
    parser.add_argument("--review-json", default="", help="Optional three-field review JSON")
    parser.add_argument("--object-recall-json", default="", help="Optional object recall pack JSON")
    parser.add_argument("--gate-json", default="", help="Optional three-field gate JSON")
    parser.add_argument("--standard-bill-json", default="", help="Optional standard bill preview JSON")
    parser.add_argument("--quantity-json", default="", help="Optional quantity placeholder JSON")
    parser.add_argument("--artifact", action="append", default=[], help="Artifact mapping name=path; may be repeated")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "closed_loop_stage_report"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    report = build_closed_loop_stage_report(
        pipeline_report=load_json_report(args.pipeline_json),
        v2_report=load_json_report(args.v2_json),
        template_status_report=load_json_report(args.template_status_json),
        import_report=load_json_report(args.import_json),
        evaluation_report=load_json_report(args.eval_json),
        review_report=load_json_report(args.review_json),
        object_recall_report=load_json_report(args.object_recall_json),
        gate_report=load_json_report(args.gate_json),
        standard_bill_report=load_json_report(args.standard_bill_json),
        quantity_report=load_json_report(args.quantity_json),
        artifacts=_parse_artifacts(args.artifact),
    )
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_closed_loop_stage_report_{timestamp}"
    outputs = write_closed_loop_stage_report_outputs(report, args.output_dir, stem=stem)
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


def _parse_artifacts(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            continue
        key, path = value.split("=", 1)
        key = key.strip()
        path = path.strip()
        if key and path:
            result[key] = path
    return result


if __name__ == "__main__":
    raise SystemExit(main())
