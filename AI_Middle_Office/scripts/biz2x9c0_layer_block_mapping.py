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

from app.services.dxf_layer_block_mapper import (  # noqa: E402
    DxfLayerBlockMappingError,
    build_layer_block_mapping_report,
    load_json_report,
    write_layer_block_mapping_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9c0 低风险图层/块名映射")
    parser.add_argument("--geometry-report", default="", help="BIZ-2x-9a 几何探测 JSON；不填则取最新")
    parser.add_argument("--confirmation-report", default="", help="BIZ-2x-9b-1 比例单位确认 JSON；不填则取最新")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9c0"))
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        geometry_report_path = Path(args.geometry_report) if args.geometry_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9a",
            "BIZ2x9a_CAD几何图元探测_*.json",
        )
        confirmation_report_path = Path(args.confirmation_report) if args.confirmation_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9b",
            "BIZ2x9b_比例单位人工确认配置_*.json",
        )
        geometry_report = load_json_report(geometry_report_path)
        confirmation_report = load_json_report(confirmation_report_path)
        report = build_layer_block_mapping_report(geometry_report=geometry_report, confirmation_report=confirmation_report)
        report["inputs"] = {
            "geometry_report": str(geometry_report_path),
            "confirmation_report": str(confirmation_report_path),
        }
        stem = f"BIZ2x9c0_低风险图层块名映射_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report["outputs"] = write_layer_block_mapping_outputs(report, args.output_dir, stem=stem)
    except (DxfLayerBlockMappingError, OSError) as exc:
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
        raise DxfLayerBlockMappingError(f"No file matched {pattern} in {directory}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
