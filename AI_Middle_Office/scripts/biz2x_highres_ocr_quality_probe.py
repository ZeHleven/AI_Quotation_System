from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_ocr_quality_scorer import (  # noqa: E402
    build_highres_ocr_quality_report,
    build_ocr_quality_reranked_plan,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OCR quality scoring for high-resolution text-region crops.")
    parser.add_argument(
        "--highres-run-dir",
        default="",
        help="A text_region_highres run directory. Defaults to the latest outputs/biz2x_trial/text_region_highres run.",
    )
    parser.add_argument(
        "--highres-manifest",
        default="",
        help="Path to highres_region_manifest.json. Overrides --highres-run-dir.",
    )
    parser.add_argument(
        "--text-region-plan",
        default="",
        help="Path to original text_region_plan.json. Defaults to the value recorded by text_region_highres_summary.json.",
    )
    parser.add_argument("--output-dir", default="", help="Output directory. Defaults to <highres-run-dir>/ocr_quality.")
    parser.add_argument("--max-crops", type=int, default=20)
    parser.add_argument("--max-image-side", type=int, default=1800)
    parser.add_argument("--paddlex-cache-dir", default="", help="Optional PADDLE_PDX_CACHE_HOME override for local PaddleOCR.")
    parser.add_argument("--ocr-engine", default="paddleocr")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    highres_run_dir = Path(args.highres_run_dir) if args.highres_run_dir else _latest_highres_run_dir()
    manifest_path = Path(args.highres_manifest) if args.highres_manifest else _manifest_path_from_run(highres_run_dir)
    if not manifest_path.exists() or not manifest_path.is_file():
        raise FileNotFoundError(f"highres_region_manifest.json not found: {manifest_path}")
    if args.paddlex_cache_dir:
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(args.paddlex_cache_dir).resolve())

    output_dir = Path(args.output_dir) if args.output_dir else highres_run_dir / "ocr_quality"
    crop_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quality_report = build_highres_ocr_quality_report(
        crop_manifest=crop_manifest,
        output_dir=output_dir,
        ocr_engine=args.ocr_engine,
        max_crops=args.max_crops,
        max_image_side=args.max_image_side,
    )

    text_region_plan_path = Path(args.text_region_plan) if args.text_region_plan else _text_region_plan_from_run(highres_run_dir)
    rerank_report: dict[str, Any] = {}
    if text_region_plan_path and text_region_plan_path.exists() and text_region_plan_path.is_file():
        text_region_plan = json.loads(text_region_plan_path.read_text(encoding="utf-8"))
        rerank_report = build_ocr_quality_reranked_plan(
            text_region_plan=text_region_plan,
            quality_report=quality_report,
            output_dir=output_dir,
        )

    summary = {
        "run_dir": str(output_dir.resolve()),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "highres_run_dir": str(highres_run_dir.resolve()),
        "highres_manifest": str(manifest_path.resolve()),
        "text_region_plan": str(text_region_plan_path.resolve()) if text_region_plan_path else "",
        "ocr_quality_summary": quality_report.get("summary"),
        "rerank_summary": rerank_report.get("summary") if rerank_report else {},
        "top_scores": (quality_report.get("crop_scores") or [])[:10],
        "outputs": {
            **dict(quality_report.get("outputs") or {}),
            **dict(rerank_report.get("outputs") or {}),
        },
    }
    summary_path = output_dir / "highres_ocr_quality_probe_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def _latest_highres_run_dir() -> Path:
    root = ROOT / "outputs" / "biz2x_trial" / "text_region_highres"
    candidates = [path for path in root.glob("*") if path.is_dir() and _manifest_path_from_run(path).is_file()]
    if not candidates:
        raise FileNotFoundError("No text_region_highres run with highres_region_manifest.json was found.")
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


def _manifest_path_from_run(run_dir: Path) -> Path:
    return run_dir / "highres_text_regions" / "highres_region_manifest.json"


def _text_region_plan_from_run(run_dir: Path) -> Path:
    summary_path = run_dir / "text_region_highres_summary.json"
    if not summary_path.exists() or not summary_path.is_file():
        return Path()
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return Path()
    plan = str(summary.get("text_region_plan") or "").strip()
    return Path(plan) if plan else Path()


if __name__ == "__main__":
    main()
