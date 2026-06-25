from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_highres_region_renderer import build_highres_region_render_report  # noqa: E402


DEFAULT_PDF = ROOT / "tmp" / "xinda_staff_canteen_drawing.pdf"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render high-resolution crops from text_region_plan.json.")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Source drawing PDF.")
    parser.add_argument(
        "--text-region-plan",
        default="",
        help="Path to text_region_plan.json. Defaults to the latest outputs/biz2x_trial/text_region_discovery run.",
    )
    parser.add_argument("--output-dir", default="", help="Output directory. Defaults to outputs/biz2x_trial/text_region_highres/<run_id>.")
    parser.add_argument("--max-regions", type=int, default=80)
    parser.add_argument("--default-scale", type=float, default=32.0)
    parser.add_argument("--max-scale", type=float, default=96.0)
    parser.add_argument("--max-pixels", type=int, default=32_000_000)
    parser.add_argument("--min-width-px", type=int, default=900)
    parser.add_argument("--min-height-px", type=int, default=96)
    parser.add_argument("--min-area-ratio", type=float, default=0.000001)
    parser.add_argument("--max-area-ratio", type=float, default=0.22)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    plan_path = Path(args.text_region_plan) if args.text_region_plan else _latest_text_region_plan()
    if plan_path is None or not plan_path.exists() or not plan_path.is_file():
        raise FileNotFoundError("text_region_plan.json not found; pass --text-region-plan")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    regions = list(plan.get("regions") or [])
    if args.output_dir:
        run_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_text_region_highres")
        run_dir = ROOT / "outputs" / "biz2x_trial" / "text_region_highres" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if regions:
        referenced_pages = {
            int(row.get("page") or 0)
            for row in regions
            if str(row.get("source_file") or "").strip() == pdf_path.name and int(row.get("page") or 0) > 0
        }
        parse_report = _build_fast_pdf_page_parse_report(pdf_path, referenced_pages=referenced_pages)
    else:
        parse_report = _empty_pdf_page_parse_report(pdf_path)
    highres_report = build_highres_region_render_report(
        parse_report=parse_report,
        layout_plan_report={"regions": regions},
        output_dir=run_dir / "highres_text_regions",
        max_regions=args.max_regions,
        default_scale=args.default_scale,
        max_scale=args.max_scale,
        max_pixels=args.max_pixels,
        min_width_px=args.min_width_px,
        min_height_px=args.min_height_px,
        min_area_ratio=args.min_area_ratio,
        max_area_ratio=args.max_area_ratio,
    )
    summary = {
        "run_dir": str(run_dir.resolve()),
        "pdf_path": str(pdf_path.resolve()),
        "text_region_plan": str(plan_path.resolve()),
        "input_region_count": len(regions),
        "parse_summary": parse_report.get("summary"),
        "highres_summary": highres_report.get("summary"),
        "sample_crops": (highres_report.get("crop_manifest") or [])[:8],
        "outputs": highres_report.get("outputs"),
    }
    summary_path = run_dir / "text_region_highres_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def _empty_pdf_page_parse_report(pdf_path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "phase": "PDF-2-fast-page-size-parse",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dependency_status": {},
        "summary": {
            "pdf_file_count": 1,
            "page_count": 0,
            "text_row_count": 0,
            "text_page_count": 0,
            "scanned_or_visual_page_count": 0,
            "parse_engine": "skipped_no_text_regions",
        },
        "file_rows": [
            {
                "file_name": pdf_path.name,
                "path": str(pdf_path.resolve()),
                "size_bytes": pdf_path.stat().st_size,
                "sha256": "",
                "page_count": 0,
                "parse_engine": "skipped_no_text_regions",
                "parse_status": "skipped_no_text_regions",
            }
        ],
        "page_rows": [],
        "text_rows": [],
    }


def _latest_text_region_plan() -> Path | None:
    root = ROOT / "outputs" / "biz2x_trial" / "text_region_discovery"
    candidates = [path for path in root.glob("*/text_regions/text_region_plan.json") if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


def _build_fast_pdf_page_parse_report(pdf_path: Path, *, referenced_pages: set[int]) -> dict[str, Any]:
    content = pdf_path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    page_rows: list[dict[str, Any]] = []
    engine = "pypdf_page_size_only"
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        wanted = referenced_pages or set(range(1, len(reader.pages) + 1))
        for page_index, page in enumerate(reader.pages, start=1):
            if page_index not in wanted:
                continue
            media_box = page.mediabox
            width = float(media_box.width or 0)
            height = float(media_box.height or 0)
            rotation = int(page.get("/Rotate", 0) or 0)
            page_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_index,
                    "width_pt": round(width, 3),
                    "height_pt": round(height, 3),
                    "rotation": rotation,
                    "text_length": 0,
                    "needs_visual_recognition": True,
                    "parse_status": "parsed_page_size_only",
                }
            )
            if len(page_rows) >= len(wanted):
                break
    except Exception:
        engine = "regex_page_size_only"
        page_count = max(referenced_pages) if referenced_pages else (len(re.findall(rb"/Type\s*/Page\b", content)) or 1)
        media_box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", content)
        width = float(media_box.group(1)) if media_box else 595.0
        height = float(media_box.group(2)) if media_box else 842.0
        wanted = referenced_pages or set(range(1, page_count + 1))
        for page_index in sorted(wanted):
            page_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_index,
                    "width_pt": round(width, 3),
                    "height_pt": round(height, 3),
                    "rotation": 0,
                    "text_length": 0,
                    "needs_visual_recognition": True,
                    "parse_status": "parsed_page_size_by_regex",
                }
            )
    return {
        "ok": True,
        "phase": "PDF-2-fast-page-size-parse",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dependency_status": {},
        "summary": {
            "pdf_file_count": 1,
            "page_count": len(page_rows),
            "text_row_count": 0,
            "text_page_count": 0,
            "scanned_or_visual_page_count": len(page_rows),
            "parse_engine": engine,
        },
        "file_rows": [
            {
                "file_name": pdf_path.name,
                "path": str(pdf_path.resolve()),
                "size_bytes": pdf_path.stat().st_size,
                "sha256": file_hash,
                "page_count": len(page_rows),
                "parse_engine": engine,
                "parse_status": "parsed_page_size_only",
            }
        ],
        "page_rows": page_rows,
        "text_rows": [],
    }


if __name__ == "__main__":
    main()
