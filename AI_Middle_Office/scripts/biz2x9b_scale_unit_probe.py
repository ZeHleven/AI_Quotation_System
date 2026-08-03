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
    build_scale_unit_probe_report,
    load_json_report,
    load_text_records_csv,
    write_scale_unit_probe_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9b 图框、比例、单位校验")
    parser.add_argument("--text-report", default="", help="BIZ-2x-3 DXF 文本 JSON 报告；不填则自动取最新")
    parser.add_argument("--text-csv", default="", help="BIZ-2x-3 DXF 文本 CSV；不填则尝试使用 text-report 同名 CSV")
    parser.add_argument("--geometry-report", default="", help="BIZ-2x-9a 几何探测 JSON 报告；不填则自动取最新")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9b"))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        text_report_path = Path(args.text_report) if args.text_report else _latest(WORKSPACE_ROOT / "outputs" / "biz2x3", "BIZ2x3_DXF图纸文本图层提取_*.json")
        text_csv_path = Path(args.text_csv) if args.text_csv else text_report_path.with_suffix(".csv")
        geometry_report_path = Path(args.geometry_report) if args.geometry_report else _latest(WORKSPACE_ROOT / "outputs" / "biz2x9a", "BIZ2x9a_CAD几何图元探测_*.json")

        text_report = load_json_report(text_report_path)
        geometry_report = load_json_report(geometry_report_path)
        text_records = load_text_records_csv(text_csv_path) if text_csv_path.exists() else None
        report = build_scale_unit_probe_report(text_report=text_report, geometry_report=geometry_report, text_records=text_records)
        report["inputs"] = {
            "text_report": str(text_report_path),
            "text_csv": str(text_csv_path) if text_csv_path.exists() else "",
            "geometry_report": str(geometry_report_path),
        }
        if not args.no_write:
            stem = f"BIZ2x9b_图框比例单位校验_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            report["outputs"] = write_scale_unit_probe_outputs(report, args.output_dir, stem=stem)
    except (DxfScaleUnitProbeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.print_summary_only:
        print(json.dumps({"ok": True, "summary": report["summary"], "inputs": report.get("inputs", {}), "outputs": report.get("outputs", {})}, ensure_ascii=False, indent=2))
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
