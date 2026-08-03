from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.dxf_scale_unit_probe import (  # noqa: E402
    DxfScaleUnitProbeError,
    build_manual_scale_unit_confirmation_report,
    load_json_report,
    write_manual_scale_unit_confirmation_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9b-1 比例/单位人工确认配置生成")
    parser.add_argument("--scale-unit-report", default="", help="BIZ-2x-9b 图框比例单位校验 JSON；不填则取最新")
    parser.add_argument("--geometry-report", default="", help="BIZ-2x-9a 几何探测 JSON；不填则取最新")
    parser.add_argument("--drawing-unit", default="mm")
    parser.add_argument("--model-space-scale", default="1:1")
    parser.add_argument("--title-block-scale-usage", default="plot_scale_only_not_quantity_multiplier")
    parser.add_argument("--title-block-scale-varies-by-drawing", action="store_true", default=True)
    parser.add_argument("--disallow-all-files", action="store_true")
    parser.add_argument("--confirmation-note", default="用户确认：一般都是 mm；模型空间按真实尺寸 1:1；每张图标题栏比例可不同；基本都可以进入几何算量。")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9b"))
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        scale_unit_report_path = Path(args.scale_unit_report) if args.scale_unit_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9b",
            "BIZ2x9b_图框比例单位校验_*.json",
        )
        geometry_report_path = Path(args.geometry_report) if args.geometry_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9a",
            "BIZ2x9a_CAD几何图元探测_*.json",
        )
        scale_unit_report = load_json_report(scale_unit_report_path)
        geometry_report = load_json_report(geometry_report_path)
        report = build_manual_scale_unit_confirmation_report(
            scale_unit_report=scale_unit_report,
            geometry_report=geometry_report,
            drawing_unit=args.drawing_unit,
            model_space_scale=args.model_space_scale,
            title_block_scale_usage=args.title_block_scale_usage,
            title_block_scale_varies_by_drawing=args.title_block_scale_varies_by_drawing,
            allow_geometry_quantity_for_all_files=not args.disallow_all_files,
            confirmation_note=args.confirmation_note,
        )
        report["inputs"] = {
            "scale_unit_report": str(scale_unit_report_path),
            "geometry_report": str(geometry_report_path),
        }
        stem = f"BIZ2x9b_比例单位人工确认配置_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report["outputs"] = write_manual_scale_unit_confirmation_outputs(report, args.output_dir, stem=stem)
    except (DxfScaleUnitProbeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.print_summary_only:
        print(json.dumps({"ok": True, "summary": report["summary"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    if not matches:
        raise DxfScaleUnitProbeError(f"No file matched {pattern} in {directory}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
