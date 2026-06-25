from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_ocr_quality_scorer import build_ocr_quality_feedback_profile  # noqa: E402
from app.services.drawing_text_region_detector import build_text_region_discovery_report  # noqa: E402


DEFAULT_PDF = ROOT / "tmp" / "xinda_staff_canteen_drawing.pdf"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover medium-resolution text regions in drawing PDF pages.")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Source drawing PDF.")
    parser.add_argument("--output-dir", default="", help="Output directory. Defaults to outputs/biz2x_trial/text_region_discovery/<run_id>.")
    parser.add_argument("--render-dpi", type=int, default=450, help="Medium-resolution render DPI for region discovery.")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit pages. 0 means all pages.")
    parser.add_argument("--max-regions-per-page", type=int, default=80)
    parser.add_argument("--max-regions", type=int, default=240)
    parser.add_argument("--min-score", type=float, default=0.38)
    parser.add_argument(
        "--ocr-quality-feedback",
        default="",
        help="Optional OCR quality feedback profile JSON, quality report JSON, or ocr_quality_scores.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if args.output_dir:
        run_dir = Path(args.output_dir)
    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_text_region_discovery")
        run_dir = ROOT / "outputs" / "biz2x_trial" / "text_region_discovery" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    parse_report = _build_fast_pdf_page_parse_report(pdf_path, max_pages=args.max_pages)
    render_report = _render_selected_pdf_pages(
        parse_report=parse_report,
        page_dir=run_dir / "medium_pages",
        render_dpi=args.render_dpi,
        max_pages=args.max_pages,
    )
    feedback_profile = _load_ocr_quality_feedback_profile(args.ocr_quality_feedback)
    discovery_report = build_text_region_discovery_report(
        render_report=render_report,
        output_dir=run_dir / "text_regions",
        max_pages=args.max_pages,
        max_regions_per_page=args.max_regions_per_page,
        max_regions=args.max_regions,
        min_score=args.min_score,
        ocr_quality_feedback_profile=feedback_profile,
    )
    summary = {
        "run_dir": str(run_dir.resolve()),
        "pdf_path": str(pdf_path.resolve()),
        "render_dpi": args.render_dpi,
        "parse_summary": parse_report.get("summary"),
        "render_summary": render_report.get("summary"),
        "text_region_summary": discovery_report.get("summary"),
        "ocr_quality_feedback": _feedback_summary(feedback_profile),
        "outputs": {
            "text_regions": discovery_report.get("outputs"),
        },
    }
    summary_path = run_dir / "text_region_discovery_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def _render_selected_pdf_pages(
    *,
    parse_report: dict,
    page_dir: Path,
    render_dpi: int,
    max_pages: int,
) -> dict:
    """Render only the page budget requested by this probe script."""

    page_dir.mkdir(parents=True, exist_ok=True)
    page_rows = list(parse_report.get("page_rows") or [])
    if max_pages and max_pages > 0:
        page_rows = page_rows[:max_pages]
    pdf_by_source = {
        str(row.get("file_name") or ""): Path(str(row.get("path") or ""))
        for row in parse_report.get("file_rows") or []
    }
    render_rows: list[dict] = []
    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception as exc:  # noqa: BLE001
        for page in page_rows:
            render_rows.append(
                {
                    "source_file": page.get("source_file", ""),
                    "page": page.get("page", 0),
                    "render_dpi": render_dpi,
                    "png_path": "",
                    "status": "render_tool_missing",
                    "image_width_px": "",
                    "image_height_px": "",
                    "message": f"pypdfium2 unavailable: {exc}",
                }
            )
        return _render_report(render_rows=render_rows, render_dpi=render_dpi, tool_path="")

    documents: dict[Path, object] = {}
    scale = max(1.0, float(render_dpi) / 72.0)
    try:
        for page in page_rows:
            source_file = str(page.get("source_file") or "")
            pdf_path = pdf_by_source.get(source_file)
            page_no = int(page.get("page") or 0)
            if pdf_path is None or not pdf_path.exists() or page_no <= 0:
                continue
            document = documents.get(pdf_path)
            if document is None:
                document = pdfium.PdfDocument(str(pdf_path))
                documents[pdf_path] = document
            if page_no > len(document):  # type: ignore[arg-type]
                continue
            pdf_page = document[page_no - 1]  # type: ignore[index]
            bitmap = pdf_page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            output_path = page_dir / f"{_safe_stem(source_file)}_p{page_no:03d}.png"
            image.save(output_path)
            render_rows.append(
                {
                    "source_file": source_file,
                    "page": page_no,
                    "render_dpi": render_dpi,
                    "png_path": str(output_path.resolve()),
                    "status": "rendered",
                    "image_width_px": image.width,
                    "image_height_px": image.height,
                    "message": "rendered selected page with pypdfium2",
                }
            )
            try:
                pdf_page.close()
            except Exception:
                pass
    finally:
        for document in documents.values():
            try:
                document.close()  # type: ignore[attr-defined]
            except Exception:
                pass
    return _render_report(render_rows=render_rows, render_dpi=render_dpi, tool_path="pypdfium2")


def _load_ocr_quality_feedback_profile(path_value: str) -> dict:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("schema_version") == "drawing_ocr_quality_feedback_profile_v1":
        return payload
    if isinstance(payload, list):
        return build_ocr_quality_feedback_profile(crop_scores=[row for row in payload if isinstance(row, dict)])
    if isinstance(payload, dict) and "crop_scores" in payload:
        return build_ocr_quality_feedback_profile(quality_report=payload)
    raise ValueError(f"Unsupported OCR quality feedback payload: {path}")


def _feedback_summary(profile: dict) -> dict:
    if not profile:
        return {"enabled": False}
    return {
        "enabled": True,
        "schema_version": profile.get("schema_version", ""),
        "positive_sample_count": profile.get("positive_sample_count", 0),
        "negative_sample_count": profile.get("negative_sample_count", 0),
    }


def _build_fast_pdf_page_parse_report(pdf_path: Path, *, max_pages: int) -> dict:
    content = pdf_path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    page_rows: list[dict] = []
    engine = "pypdf_page_size_only"
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        for page_index, page in enumerate(reader.pages, start=1):
            if max_pages and max_pages > 0 and len(page_rows) >= max_pages:
                break
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
    except Exception:
        engine = "regex_page_size_only"
        page_count = len(re.findall(rb"/Type\s*/Page\b", content)) or 1
        if max_pages and max_pages > 0:
            page_count = min(page_count, max_pages)
        media_box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", content)
        width = float(media_box.group(1)) if media_box else 595.0
        height = float(media_box.group(2)) if media_box else 842.0
        for page_index in range(1, page_count + 1):
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


def _render_report(*, render_rows: list[dict], render_dpi: int, tool_path: str) -> dict:
    rendered_count = sum(1 for row in render_rows if row.get("status") == "rendered")
    return {
        "ok": True,
        "phase": "PDF-3-render",
        "summary": {
            "render_dpi": render_dpi,
            "rendered_page_count": rendered_count,
            "render_failed_count": len(render_rows) - rendered_count,
            "pdftoppm_path": tool_path,
        },
        "render_rows": render_rows,
    }


def _safe_stem(value: str) -> str:
    text = Path(value or "drawing").stem
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text).strip("._") or "drawing"


if __name__ == "__main__":
    main()
