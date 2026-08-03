from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.drawing_pdf_direct_itemizer import run_pdf_direct_itemization  # noqa: E402
from app.services.drawing_pdf_evidence_pipeline import run_pdf_evidence_pipeline  # noqa: E402
from app.services.drawing_pdf_v2_takeoff import (  # noqa: E402
    build_pdf_v2_takeoff_report,
    write_pdf_v2_takeoff_outputs,
)
from app.services.drawing_three_field_acceptance import load_answer_rows_from_workbook  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x PDF V2 evidence-driven takeoff pipeline")
    parser.add_argument("--pdf-direct-json", default="", help="Existing PDF direct itemization JSON")
    parser.add_argument("--pdf-evidence-json", default="", help="Existing PDF visual evidence pipeline JSON")
    parser.add_argument(
        "--pdf-evidence-json-list",
        default="",
        help="Semicolon-separated evidence JSON files to merge before V2 itemization",
    )
    parser.add_argument("--pdf-dir", default="", help="Optional PDF directory; calls PDF direct itemization first")
    parser.add_argument(
        "--pdf-dir-mode",
        choices=("direct-itemization", "evidence-extraction"),
        default="direct-itemization",
        help="When --pdf-dir is used, choose whether GLM should directly list items or only extract evidence.",
    )
    parser.add_argument("--answer-xlsx", default="", help="Optional manual answer workbook for three-field acceptance")
    parser.add_argument("--style-prompt", default="", help="Optional human-listing style prompt markdown")
    parser.add_argument("--output-dir", default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff"))
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--render-dpi", type=int, default=220)
    parser.add_argument("--tile-grid-size", type=int, default=3)
    parser.add_argument("--max-visual-images", type=int, default=20)
    parser.add_argument(
        "--vision-passes",
        default="general",
        help=(
            "Comma/semicolon separated tile evidence passes: general, finish_schedule, "
            "electrical_mep, plumbing_fixture, fixture_valve_schedule, demolition_node, "
            "door_window_demolition, table_legend, node_detail"
        ),
    )
    parser.add_argument("--enable-quantity-ai", action="store_true", help="Keep disabled unless quantity stage is being tested")
    args = parser.parse_args()

    provided_inputs = [
        bool(args.pdf_direct_json),
        bool(args.pdf_evidence_json),
        bool(args.pdf_evidence_json_list),
        bool(args.pdf_dir),
    ]
    if sum(1 for item in provided_inputs if item) != 1:
        raise SystemExit("Provide exactly one of --pdf-direct-json, --pdf-evidence-json, --pdf-evidence-json-list, or --pdf-dir")

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_direct_report = _load_or_run_pdf_direct(args, timestamp)
    answer_rows = None
    if args.answer_xlsx:
        answer_rows, _ = load_answer_rows_from_workbook(Path(args.answer_xlsx))
    style_prompt_text = Path(args.style_prompt).read_text(encoding="utf-8") if args.style_prompt else ""
    report = build_pdf_v2_takeoff_report(
        pdf_direct_report,
        answer_rows=answer_rows,
        style_prompt_text=style_prompt_text,
    )
    report["inputs"] = {
        "pdf_direct_json": args.pdf_direct_json,
        "pdf_evidence_json": args.pdf_evidence_json,
        "pdf_evidence_json_list": args.pdf_evidence_json_list,
        "pdf_dir": args.pdf_dir,
        "answer_xlsx": args.answer_xlsx,
        "style_prompt": args.style_prompt,
        "vision_passes": args.vision_passes,
        "quantity_acceptance_enabled": False,
    }
    outputs = write_pdf_v2_takeoff_outputs(
        report,
        Path(args.output_dir),
        stem=f"BIZ2x_PDF_V2证据驱动列项_{timestamp}",
    )
    report["outputs"] = outputs
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


def _load_or_run_pdf_direct(args: argparse.Namespace, timestamp: str) -> dict[str, Any]:
    if args.pdf_direct_json:
        return json.loads(Path(args.pdf_direct_json).read_text(encoding="utf-8"))
    if args.pdf_evidence_json:
        return json.loads(Path(args.pdf_evidence_json).read_text(encoding="utf-8"))
    if args.pdf_evidence_json_list:
        paths = _split_input_paths(args.pdf_evidence_json_list)
        if not paths:
            raise SystemExit("--pdf-evidence-json-list did not contain any JSON paths")
        reports = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
        return merge_pdf_evidence_reports(reports, source_paths=paths)

    if args.pdf_dir_mode == "evidence-extraction":
        return run_pdf_evidence_pipeline(
            pdf_dir=Path(args.pdf_dir),
            output_dir=Path(args.output_dir) / "pdf_evidence",
            timestamp=timestamp,
            render_dpi=args.render_dpi,
            tile_grid_size=args.tile_grid_size,
            enable_llm_visual=True,
            max_visual_tiles=args.max_visual_images,
            vision_passes=args.vision_passes,
        )

    old_quantity_flag = settings.feature_pdf_ai_quantity_suggestion
    if not args.enable_quantity_ai:
        object.__setattr__(settings, "feature_pdf_ai_quantity_suggestion", False)
    try:
        return run_pdf_direct_itemization(
            pdf_dir=Path(args.pdf_dir),
            output_dir=Path(args.output_dir) / "pdf_direct",
            timestamp=timestamp,
            render_dpi=args.render_dpi,
            tile_grid_size=args.tile_grid_size,
            max_visual_images=args.max_visual_images,
            style_prompt_text=Path(args.style_prompt).read_text(encoding="utf-8") if args.style_prompt else "",
        )
    finally:
        object.__setattr__(settings, "feature_pdf_ai_quantity_suggestion", old_quantity_flag)


def _split_input_paths(raw_value: str) -> list[str]:
    return [item.strip().strip('"') for item in raw_value.split(";") if item.strip()]


def merge_pdf_evidence_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    source_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    if not reports:
        raise ValueError("At least one evidence report is required")

    merged_rows: list[dict[str, Any]] = []
    source_report_rows: list[dict[str, Any]] = []
    for report_index, report in enumerate(reports, start=1):
        rows = _report_evidence_rows(report)
        source_path = source_paths[report_index - 1] if source_paths and report_index - 1 < len(source_paths) else ""
        source_report_rows.append(
            {
                "index": report_index,
                "path": source_path,
                "file_name": Path(source_path).name if source_path else "",
                "phase": report.get("phase", ""),
                "evidence_row_count": len(rows),
                "summary": dict(report.get("summary") or {}),
            }
        )
        for row_index, row in enumerate(rows, start=1):
            merged = dict(row)
            existing_id = str(merged.get("evidence_id") or "").strip()
            merged["evidence_id"] = f"R{report_index:02d}-{existing_id or f'E{row_index:06d}'}"
            merged["source_report_index"] = report_index
            if source_path:
                merged["source_report_file"] = Path(source_path).name
            merged_rows.append(merged)

    summaries = [dict(report.get("summary") or {}) for report in reports]
    summary = {
        "pdf_file_count": _max_summary_number(summaries, "pdf_file_count"),
        "pdf_page_count": _max_summary_number(summaries, "pdf_page_count"),
        "pdf_render_status": _first_summary_text(summaries, "pdf_render_status"),
        "ensemble_report_count": len(reports),
        "ensemble_evidence_input_count": len(merged_rows),
    }
    return {
        "ok": all(bool(report.get("ok", True)) for report in reports),
        "phase": "BIZ-2x-pdf-evidence-ensemble",
        "summary": summary,
        "source_reports": source_report_rows,
        "visual_evidence_report": {
            "evidence_rows": merged_rows,
        },
    }


def _report_evidence_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("pdf_direct_item_rows", "item_rows", "drawing_items"):
        rows = report.get(key)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, Mapping)]

    visual_report = report.get("visual_evidence_report")
    if isinstance(visual_report, Mapping):
        rows = visual_report.get("evidence_rows")
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, Mapping)]

    for key in ("evidence_rows", "pdf_evidence_rows"):
        rows = report.get(key)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def _max_summary_number(summaries: Sequence[Mapping[str, Any]], key: str) -> int:
    values: list[int] = []
    for summary in summaries:
        try:
            values.append(int(summary.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return max(values, default=0)


def _first_summary_text(summaries: Sequence[Mapping[str, Any]], key: str) -> str:
    for summary in summaries:
        value = str(summary.get(key) or "").strip()
        if value:
            return value
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
