from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_three_field_acceptance import (  # noqa: E402
    build_three_field_acceptance_report,
    load_answer_rows_from_workbook,
    load_candidate_rows_from_report,
    write_three_field_acceptance_outputs,
)
from app.core.config import settings  # noqa: E402
from app.services.drawing_pdf_direct_itemizer import run_pdf_direct_itemization  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BIZ-2x PDF drawing three-field acceptance: item name, feature, unit only"
    )
    parser.add_argument("--answer-xlsx", required=True, help="Manual answer workbook")
    parser.add_argument(
        "--candidate-json",
        default="",
        help="Optional system-recognized candidate report JSON. If omitted, only the manual answer baseline is exported.",
    )
    parser.add_argument(
        "--candidate-source",
        choices=("auto", "quantity-list", "raw-pdf-items"),
        default="auto",
        help="Rows to validate from a candidate report. auto keeps current report order; raw-pdf-items validates direct vision rows.",
    )
    parser.add_argument(
        "--pdf-dir",
        default="",
        help="Optional directory of PDF drawings. When provided, run PDF direct itemization first and accept its rows.",
    )
    parser.add_argument("--render-dpi", type=int, default=350, help="PDF render DPI when --pdf-dir is used")
    parser.add_argument("--tile-grid-size", type=int, default=3, help="PDF tile grid size when --pdf-dir is used")
    parser.add_argument(
        "--max-visual-images",
        type=int,
        default=None,
        help="Maximum rendered images sent to the vision model when --pdf-dir is used",
    )
    parser.add_argument(
        "--enable-quantity-ai",
        action="store_true",
        help="Allow optional PDF AI quantity suggestion. Default is off for this three-field acceptance.",
    )
    parser.add_argument(
        "--sheet",
        action="append",
        default=[],
        help="Sheet name to include. Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x_pdf_three_field_acceptance"),
        help="Output directory",
    )
    parser.add_argument("--timestamp", default="", help="Output timestamp/stem suffix")
    parser.add_argument("--no-write", action="store_true", help="Only print summary")
    parser.add_argument("--print-report", action="store_true", help="Print full report JSON")
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    answer_path = Path(args.answer_xlsx)
    sheet_names = _parse_sheet_names(args.sheet)
    answer_rows, sheet_summaries = load_answer_rows_from_workbook(answer_path, sheet_names=sheet_names or None)

    candidate_rows = []
    candidate_source = ""
    pdf_direct_summary: dict[str, Any] = {}
    pdf_direct_outputs: dict[str, str] = {}
    if args.candidate_json and args.pdf_dir:
        raise SystemExit("--candidate-json and --pdf-dir cannot be used together")
    if args.pdf_dir:
        old_quantity_flag = settings.feature_pdf_ai_quantity_suggestion
        if not args.enable_quantity_ai:
            object.__setattr__(settings, "feature_pdf_ai_quantity_suggestion", False)
        try:
            pdf_direct_report = run_pdf_direct_itemization(
                pdf_dir=Path(args.pdf_dir),
                output_dir=Path(args.output_dir) / "pdf_direct",
                timestamp=timestamp,
                render_dpi=args.render_dpi,
                tile_grid_size=args.tile_grid_size,
                max_visual_images=args.max_visual_images,
            )
        finally:
            object.__setattr__(settings, "feature_pdf_ai_quantity_suggestion", old_quantity_flag)
        candidate_source = str(Path(args.pdf_dir))
        candidate_rows = _load_candidates_by_source(pdf_direct_report, args.candidate_source)
        pdf_direct_summary = dict(pdf_direct_report.get("summary") or {})
        pdf_direct_outputs = dict(pdf_direct_report.get("outputs") or {})
    elif args.candidate_json:
        candidate_source = str(Path(args.candidate_json))
        candidate_rows = _load_candidates_by_source(_read_json(Path(args.candidate_json)), args.candidate_source)

    report = build_three_field_acceptance_report(
        answer_rows=answer_rows,
        candidate_rows=candidate_rows,
        source_name=f"{answer_path.name}" + (f" + {Path(candidate_source).name}" if candidate_source else ""),
        sheet_summaries=sheet_summaries,
    )
    report["inputs"] = {
        "answer_xlsx": str(answer_path),
        "candidate_json": candidate_source,
        "pdf_dir": str(Path(args.pdf_dir)) if args.pdf_dir else "",
        "sheets": sheet_names,
        "quantity_acceptance_enabled": False,
        "pdf_ai_quantity_suggestion_enabled": bool(args.enable_quantity_ai),
    }
    if pdf_direct_summary:
        report["pdf_direct_summary"] = pdf_direct_summary
    if pdf_direct_outputs:
        report["pdf_direct_outputs"] = pdf_direct_outputs

    outputs: dict[str, str] = {}
    if not args.no_write:
        stem = f"BIZ2x_PDF三字段验收_{timestamp}"
        outputs = write_three_field_acceptance_outputs(report, Path(args.output_dir), stem=stem)

    payload: dict[str, Any] = {
        "ok": report["ok"],
        "phase": report["phase"],
        "summary": report["summary"],
        "outputs": outputs,
        "pdf_direct_summary": pdf_direct_summary,
        "pdf_direct_outputs": pdf_direct_outputs,
    }
    if args.print_report:
        payload["report"] = report
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_sheet_names(values: list[str]) -> list[str]:
    names: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            cleaned = part.strip()
            if cleaned:
                names.append(cleaned)
    return names


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_candidates_by_source(report: Any, source: str):
    if source == "raw-pdf-items" and isinstance(report, dict):
        rows = report.get("pdf_direct_item_rows") or report.get("item_rows") or []
        return load_candidate_rows_from_report(rows)
    if source == "quantity-list" and isinstance(report, dict):
        rows = report.get("quantity_list_rows") or report.get("base_quantity_list_rows") or []
        return load_candidate_rows_from_report(rows)
    return load_candidate_rows_from_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
