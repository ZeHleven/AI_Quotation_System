from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "AI_Middle_Office"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app.services.drawing_ocr_result_reviewer import build_ocr_result_review_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Chinese OCR result effectiveness review from an OCR execution run.")
    parser.add_argument("--execution-run-dir", default="", help="Directory containing budgeted_ocr_execution_plan.json and ocr_quality/ocr_quality_scores.json.")
    parser.add_argument("--execution-plan", default="", help="Path to budgeted_ocr_execution_plan.json.")
    parser.add_argument("--ocr-quality-scores", default="", help="Path to ocr_quality_scores.json.")
    parser.add_argument("--output-dir", default="", help="Defaults to <execution-run-dir>/ocr_result_review.")
    parser.add_argument("--business-screenshot-dir", default="", help="Optional directory for copied business review screenshots.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan_path, scores_path, output_dir = _resolve_paths(args)
    execution_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    quality_scores = json.loads(scores_path.read_text(encoding="utf-8"))
    quality_report = {"crop_scores": quality_scores, "summary": _try_load_quality_summary(scores_path)}
    report = build_ocr_result_review_report(
        execution_plan=execution_plan,
        quality_report=quality_report,
        output_dir=output_dir,
        business_screenshot_dir=args.business_screenshot_dir or None,
    )
    print(json.dumps({"summary": report.get("summary"), "outputs": report.get("outputs")}, ensure_ascii=False, indent=2), flush=True)


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.execution_plan and args.ocr_quality_scores:
        plan_path = Path(args.execution_plan)
        scores_path = Path(args.ocr_quality_scores)
        output_dir = Path(args.output_dir) if args.output_dir else plan_path.parent / "ocr_result_review"
        return plan_path, scores_path, output_dir
    run_dir = Path(args.execution_run_dir) if args.execution_run_dir else _latest_execution_run_dir()
    plan_path = run_dir / "budgeted_ocr_execution_plan.json"
    scores_path = run_dir / "ocr_quality" / "ocr_quality_scores.json"
    output_dir = Path(args.output_dir) if args.output_dir else run_dir / "ocr_result_review"
    return plan_path, scores_path, output_dir


def _try_load_quality_summary(scores_path: Path) -> dict:
    summary_path = scores_path.parent / "ocr_quality_summary.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _latest_execution_run_dir() -> Path:
    root = ROOT / "outputs" / "biz2x_trial" / "ocr_execution"
    candidates = [
        path
        for path in root.glob("*")
        if path.is_dir()
        and (path / "budgeted_ocr_execution_plan.json").is_file()
        and (path / "ocr_quality" / "ocr_quality_scores.json").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("No OCR execution run with plan and quality scores was found.")
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


if __name__ == "__main__":
    main()
