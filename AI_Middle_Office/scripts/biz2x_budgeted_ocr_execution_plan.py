from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_highres_region_renderer import build_highres_region_render_report  # noqa: E402
from app.services.drawing_ocr_budget_scheduler import build_budgeted_ocr_execution_plan  # noqa: E402
from app.services.drawing_ocr_quality_scorer import build_highres_ocr_quality_report  # noqa: E402
from app.services.drawing_ocr_result_reviewer import build_ocr_result_review_report  # noqa: E402


DEFAULT_PDF = ROOT / "tmp" / "xinda_staff_canteen_drawing.pdf"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a budgeted OCR execution plan with selected + fallback regions.")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Source drawing PDF.")
    parser.add_argument("--discovery-run-dir", default="", help="A text_region_discovery run directory.")
    parser.add_argument("--text-region-plan", default="", help="Path to text_region_plan.json.")
    parser.add_argument("--text-region-rejected", default="", help="Path to text_region_rejected.json.")
    parser.add_argument("--text-region-overflow", default="", help="Path to text_region_overflow.json.")
    parser.add_argument("--output-dir", default="", help="Defaults to outputs/biz2x_trial/ocr_execution/<run_id>.")
    parser.add_argument("--total-budget", type=int, default=40)
    parser.add_argument("--overflow-reserve", type=int, default=5)
    parser.add_argument("--recoverable-rejected-reserve", type=int, default=3)
    parser.add_argument("--render-crops", action="store_true", help="Render high-resolution region crops for the plan.")
    parser.add_argument("--run-ocr", action="store_true", help="Run OCR quality scoring after rendering crops.")
    parser.add_argument("--default-scale", type=float, default=32.0)
    parser.add_argument("--max-scale", type=float, default=96.0)
    parser.add_argument("--max-pixels", type=int, default=32_000_000)
    parser.add_argument("--min-width-px", type=int, default=900)
    parser.add_argument("--min-height-px", type=int, default=96)
    parser.add_argument("--max-image-side", type=int, default=1200)
    parser.add_argument("--paddlex-cache-dir", default="", help="Optional PADDLE_PDX_CACHE_HOME override for local PaddleOCR.")
    parser.add_argument("--ocr-engine", default="paddleocr")
    parser.add_argument("--business-screenshot-dir", default="", help="Optional directory for copied business review screenshots.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if args.paddlex_cache_dir:
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(args.paddlex_cache_dir).resolve())

    plan_path, rejected_path, overflow_path = _resolve_discovery_inputs(args)
    selected_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rejected_regions = json.loads(rejected_path.read_text(encoding="utf-8"))
    overflow_regions = json.loads(overflow_path.read_text(encoding="utf-8")) if overflow_path.exists() else []

    execution_plan = build_budgeted_ocr_execution_plan(
        selected_regions=[row for row in selected_plan.get("regions") or [] if isinstance(row, Mapping)],
        rejected_regions=[row for row in rejected_regions if isinstance(row, Mapping)],
        overflow_regions=[row for row in overflow_regions if isinstance(row, Mapping)],
        total_budget=args.total_budget,
        overflow_reserve=args.overflow_reserve,
        recoverable_rejected_reserve=args.recoverable_rejected_reserve,
    )

    if args.output_dir:
        run_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_budgeted_ocr_execution")
        run_dir = ROOT / "outputs" / "biz2x_trial" / "ocr_execution" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    execution_plan_path = run_dir / "budgeted_ocr_execution_plan.json"
    execution_plan_path.write_text(json.dumps(execution_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    execution_plan_csv = run_dir / "budgeted_ocr_execution_plan.csv"
    _write_execution_plan_csv(execution_plan_csv, execution_plan.get("regions") or [])

    outputs: dict[str, Any] = {
        "budgeted_ocr_execution_plan_json": str(execution_plan_path.resolve()),
        "budgeted_ocr_execution_plan_csv": str(execution_plan_csv.resolve()),
    }
    highres_report: dict[str, Any] | None = None
    quality_report: dict[str, Any] | None = None
    result_review_report: dict[str, Any] | None = None
    if args.render_crops or args.run_ocr:
        referenced_pages = {
            int(row.get("page") or 0)
            for row in execution_plan.get("regions") or []
            if str(row.get("source_file") or "").strip() == pdf_path.name and int(row.get("page") or 0) > 0
        }
        parse_report = _build_fast_pdf_page_parse_report(pdf_path, referenced_pages=referenced_pages)
        highres_report = build_highres_region_render_report(
            parse_report=parse_report,
            layout_plan_report=execution_plan,
            output_dir=run_dir / "highres_crops",
            max_regions=len(execution_plan.get("regions") or []),
            default_scale=args.default_scale,
            max_scale=args.max_scale,
            max_pixels=args.max_pixels,
            min_width_px=args.min_width_px,
            min_height_px=args.min_height_px,
            min_area_ratio=0.000001,
            max_area_ratio=0.22,
        )
        outputs.update(dict(highres_report.get("outputs") or {}))
    if args.run_ocr:
        crop_manifest = (highres_report or {}).get("crop_manifest") or []
        quality_report = build_highres_ocr_quality_report(
            crop_manifest=crop_manifest,
            output_dir=run_dir / "ocr_quality",
            ocr_engine=args.ocr_engine,
            max_crops=len(crop_manifest),
            max_image_side=args.max_image_side,
        )
        outputs.update(dict(quality_report.get("outputs") or {}))
        result_review_report = build_ocr_result_review_report(
            execution_plan=execution_plan,
            quality_report=quality_report,
            output_dir=run_dir / "ocr_result_review",
            business_screenshot_dir=args.business_screenshot_dir or None,
        )
        outputs.update(dict(result_review_report.get("outputs") or {}))

    summary = {
        "run_dir": str(run_dir.resolve()),
        "pdf_path": str(pdf_path.resolve()),
        "source_text_region_plan": str(plan_path.resolve()),
        "source_text_region_rejected": str(rejected_path.resolve()),
        "source_text_region_overflow": str(overflow_path.resolve()) if overflow_path else "",
        "execution_summary": execution_plan.get("summary"),
        "highres_summary": (highres_report or {}).get("summary"),
        "ocr_quality_summary": (quality_report or {}).get("summary"),
        "ocr_result_review_summary": (result_review_report or {}).get("summary"),
        "outputs": outputs,
    }
    summary_path = run_dir / "budgeted_ocr_execution_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def _write_execution_plan_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "region_id",
        "original_region_id",
        "ocr_execution_bucket",
        "ocr_execution_bucket_cn",
        "ocr_execution_rank",
        "ocr_execution_reason_cn",
        "ocr_execution_budget_decision_cn",
        "candidate_decision_cn",
        "candidate_reason_cn",
        "candidate_signal_cn",
        "candidate_risk_cn",
        "next_action_cn",
        "source_file",
        "page",
        "priority",
        "confidence",
        "budget_bucket",
        "budget_bucket_cn",
        "rejected_layer",
        "rejected_layer_cn",
        "overflow_reason",
        "overflow_reason_cn",
        "region_subtype",
        "bbox_ratio",
        "quality_flags",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "region_id": row.get("region_id", ""),
                    "original_region_id": row.get("original_region_id", ""),
                    "ocr_execution_bucket": row.get("ocr_execution_bucket", ""),
                    "ocr_execution_bucket_cn": row.get("ocr_execution_bucket_cn", ""),
                    "ocr_execution_rank": row.get("ocr_execution_rank", ""),
                    "ocr_execution_reason_cn": row.get("ocr_execution_reason_cn", ""),
                    "ocr_execution_budget_decision_cn": row.get("ocr_execution_budget_decision_cn", ""),
                    "candidate_decision_cn": row.get("candidate_decision_cn", ""),
                    "candidate_reason_cn": row.get("candidate_reason_cn", ""),
                    "candidate_signal_cn": row.get("candidate_signal_cn", ""),
                    "candidate_risk_cn": row.get("candidate_risk_cn", ""),
                    "next_action_cn": row.get("next_action_cn", ""),
                    "source_file": row.get("source_file", ""),
                    "page": row.get("page", ""),
                    "priority": row.get("priority", ""),
                    "confidence": row.get("confidence", ""),
                    "budget_bucket": row.get("budget_bucket", ""),
                    "budget_bucket_cn": row.get("budget_bucket_cn", ""),
                    "rejected_layer": row.get("rejected_layer", ""),
                    "rejected_layer_cn": row.get("rejected_layer_cn", ""),
                    "overflow_reason": row.get("overflow_reason", ""),
                    "overflow_reason_cn": row.get("overflow_reason_cn", ""),
                    "region_subtype": row.get("region_subtype", ""),
                    "bbox_ratio": json.dumps(row.get("bbox_ratio") or [], ensure_ascii=False),
                    "quality_flags": "|".join(str(flag) for flag in row.get("quality_flags") or []),
                }
            )


def _resolve_discovery_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.text_region_plan and args.text_region_rejected:
        overflow_path = Path(args.text_region_overflow) if args.text_region_overflow else Path(args.text_region_plan).parent / "text_region_overflow.json"
        return Path(args.text_region_plan), Path(args.text_region_rejected), overflow_path
    run_dir = Path(args.discovery_run_dir) if args.discovery_run_dir else _latest_discovery_run_dir()
    text_regions_dir = run_dir / "text_regions"
    overflow_path = Path(args.text_region_overflow) if args.text_region_overflow else text_regions_dir / "text_region_overflow.json"
    return text_regions_dir / "text_region_plan.json", text_regions_dir / "text_region_rejected.json", overflow_path


def _latest_discovery_run_dir() -> Path:
    root = ROOT / "outputs" / "biz2x_trial" / "text_region_discovery"
    candidates = [
        path
        for path in root.glob("*")
        if path.is_dir()
        and (path / "text_regions" / "text_region_plan.json").is_file()
        and (path / "text_regions" / "text_region_rejected.json").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("No text_region_discovery run with plan and rejected JSON was found.")
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


def _build_fast_pdf_page_parse_report(pdf_path: Path, *, referenced_pages: set[int]) -> dict[str, Any]:
    content = pdf_path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    page_rows: list[dict[str, Any]] = []
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        wanted = referenced_pages or set(range(1, len(reader.pages) + 1))
        for page_index, page in enumerate(reader.pages, start=1):
            if page_index not in wanted:
                continue
            media_box = page.mediabox
            page_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_index,
                    "width_pt": float(media_box.width),
                    "height_pt": float(media_box.height),
                    "text": "",
                    "is_scanned_or_visual": True,
                }
            )
        page_count = len(reader.pages)
        engine = "pypdf_page_size_only"
    except Exception:
        page_count = max(referenced_pages or {1})
        engine = "page_size_unknown_fallback"
        page_rows = [
            {
                "source_file": pdf_path.name,
                "page": page_index,
                "width_pt": 0,
                "height_pt": 0,
                "text": "",
                "is_scanned_or_visual": True,
            }
            for page_index in sorted(referenced_pages or {1})
        ]
    return {
        "schema_version": "drawing_pdf_parse_report_v1",
        "parse_engine": engine,
        "file_rows": [
            {
                "file_name": pdf_path.name,
                "path": str(pdf_path.resolve()),
                "file_hash": file_hash,
                "page_count": page_count,
            }
        ],
        "page_rows": page_rows,
        "summary": {
            "pdf_file_count": 1,
            "page_count": page_count,
            "scanned_or_visual_page_count": page_count,
            "parse_engine": engine,
        },
    }


if __name__ == "__main__":
    main()
