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

from app.core.config import settings  # noqa: E402
from app.services.drawing_pdf_direct_itemizer import run_pdf_direct_itemization  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BIZ-2x PDF MVP: generate a presentable four-field bill preview from PDF drawings."
    )
    parser.add_argument("--pdf-dir", required=True, help="Directory containing PDF drawings")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT / "outputs" / "pdf_mvp_preview"),
        help="Output directory for the MVP preview run",
    )
    parser.add_argument("--timestamp", default="", help="Optional run timestamp/stem suffix")
    parser.add_argument("--render-dpi", type=int, default=220, help="PDF render DPI")
    parser.add_argument("--tile-grid-size", type=int, default=3, help="PDF tile grid size")
    parser.add_argument(
        "--max-visual-images",
        type=int,
        default=None,
        help="Maximum rendered images sent to GLM-4V; defaults to configured PDF_DIRECT_ITEMIZATION_MAX_IMAGES",
    )
    parser.add_argument("--style-prompt", default="", help="Optional human-listing style prompt markdown")
    parser.add_argument(
        "--disable-quantity-ai",
        action="store_true",
        help="Keep quantity as pending/review instead of calling the optional AI quantity suggestion step",
    )
    parser.add_argument("--preview-rows", type=int, default=20, help="Number of four-field rows to print in stdout")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.exists() or not pdf_dir.is_dir():
        raise SystemExit(f"PDF directory does not exist: {pdf_dir}")

    style_prompt_text = ""
    if args.style_prompt:
        style_prompt_path = Path(args.style_prompt)
        if not style_prompt_path.exists():
            raise SystemExit(f"Style prompt does not exist: {style_prompt_path}")
        style_prompt_text = style_prompt_path.read_text(encoding="utf-8")

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    old_quantity_flag = settings.feature_pdf_ai_quantity_suggestion
    if args.disable_quantity_ai:
        object.__setattr__(settings, "feature_pdf_ai_quantity_suggestion", False)

    try:
        report = run_pdf_direct_itemization(
            pdf_dir=pdf_dir,
            output_dir=Path(args.output_dir) / f"r16_pdf_mvp_four_field_preview_{timestamp}",
            timestamp=timestamp,
            render_dpi=args.render_dpi,
            tile_grid_size=args.tile_grid_size,
            max_visual_images=args.max_visual_images,
            style_prompt_text=style_prompt_text,
        )
    finally:
        object.__setattr__(settings, "feature_pdf_ai_quantity_suggestion", old_quantity_flag)

    payload = _build_stdout_payload(report, args.preview_rows)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _build_stdout_payload(report: dict[str, Any], preview_rows: int) -> dict[str, Any]:
    outputs = dict(report.get("outputs") or {})
    summary = dict(report.get("summary") or {})
    rows = list(report.get("quantity_list_rows") or [])
    return {
        "ok": bool(report.get("ok")),
        "goal": "pdf_four_field_mvp_preview",
        "phase": report.get("phase", ""),
        "summary": {
            "pdf_file_count": summary.get("pdf_file_count", 0),
            "pdf_page_count": summary.get("pdf_page_count", 0),
            "pdf_render_status": summary.get("pdf_render_status", ""),
            "pdf_tile_count": summary.get("pdf_tile_count", 0),
            "selected_image_count": summary.get("pdf_direct_selected_image_count", 0),
            "raw_item_count": summary.get("pdf_direct_raw_item_count", 0),
            "direct_item_count": summary.get("pdf_direct_item_count", 0),
            "standard_mapped_item_count": summary.get("standard_mapped_item_count", 0),
            "quantity_list_row_count": summary.get("quantity_list_row_count", len(rows)),
            "pdf_ai_quantity_status": summary.get("pdf_ai_quantity_status", ""),
            "needs_manual_review_count": summary.get("pdf_ai_quantity_needs_review_count", 0),
        },
        "outputs": {
            "quantity_list_xlsx": outputs.get("quantity_list_xlsx", ""),
            "quantity_list_csv": outputs.get("quantity_list_csv", ""),
            "pdf_direct_itemization_json": outputs.get("pdf_direct_itemization_json", ""),
            "pdf_direct_itemization_markdown": outputs.get("pdf_direct_itemization_markdown", ""),
        },
        "quantity_list_preview_rows": rows[: max(0, preview_rows)],
    }


if __name__ == "__main__":
    raise SystemExit(main())
