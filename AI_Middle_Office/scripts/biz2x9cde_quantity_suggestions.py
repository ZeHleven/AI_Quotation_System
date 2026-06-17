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

from app.services.dxf_quantity_suggester import (  # noqa: E402
    DxfQuantitySuggestionError,
    build_low_risk_quantity_suggestion_report,
    load_json_report,
    write_quantity_suggestion_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9c/9d/9e 低风险几何建议量生成")
    parser.add_argument("--geometry-report", default="", help="BIZ-2x-9a 几何探测 JSON；不填则取最新")
    parser.add_argument("--mapping-report", default="", help="BIZ-2x-9c0 低风险图层块名映射 JSON；不填则取最新")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9cde"))
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        geometry_report_path = Path(args.geometry_report) if args.geometry_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9a",
            "BIZ2x9a_CAD几何图元探测_*.json",
        )
        mapping_report_path = Path(args.mapping_report) if args.mapping_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9c0",
            "BIZ2x9c0_低风险图层块名映射_*.json",
        )
        geometry_report = load_json_report(geometry_report_path)
        mapping_report = load_json_report(mapping_report_path)
        report = build_low_risk_quantity_suggestion_report(geometry_report=geometry_report, mapping_report=mapping_report)
        report["inputs"] = {
            "geometry_report": str(geometry_report_path),
            "mapping_report": str(mapping_report_path),
        }
        stem = f"BIZ2x9cde_低风险几何建议量_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report["outputs"] = write_quantity_suggestion_outputs(report, args.output_dir, stem=stem)
    except (DxfQuantitySuggestionError, OSError) as exc:
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
        raise DxfQuantitySuggestionError(f"No file matched {pattern} in {directory}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
