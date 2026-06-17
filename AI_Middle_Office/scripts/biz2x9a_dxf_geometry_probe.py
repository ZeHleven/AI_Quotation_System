from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.dxf_geometry_probe import (  # noqa: E402
    DxfGeometryProbeError,
    build_geometry_probe_report,
    collect_dxf_geometry_files,
    parse_dxf_geometry_file,
    write_geometry_probe_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9a DXF CAD geometry probe")
    parser.add_argument("--dxf-dir", default="", help="Directory containing converted DXF files")
    parser.add_argument("--dxf-file", action="append", default=[], help="Single DXF file path; can be provided multiple times")
    parser.add_argument("--output-dir", default=str(BACKEND_ROOT.parent / "outputs" / "biz2x9a"))
    parser.add_argument("--stem", default="", help="Output file stem; defaults to timestamped Chinese file name")
    parser.add_argument("--no-write", action="store_true", help="Only print JSON summary, do not write report files")
    parser.add_argument("--print-summary-only", action="store_true", help="Print compact summary instead of full JSON")
    args = parser.parse_args()

    try:
        dxf_files = collect_dxf_geometry_files(args.dxf_dir or None, args.dxf_file)
        if not dxf_files:
            raise DxfGeometryProbeError("No DXF files provided. Use --dxf-dir or --dxf-file.")
        parsed_files = [parse_dxf_geometry_file(path) for path in dxf_files]
        report = build_geometry_probe_report(parsed_files)
        if not args.no_write:
            stem = args.stem or f"BIZ2x9a_CAD几何图元探测_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            report["outputs"] = write_geometry_probe_outputs(parsed_files, args.output_dir, stem=stem)
    except DxfGeometryProbeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.print_summary_only:
        print(json.dumps({"ok": True, "summary": report["summary"], "outputs": report.get("outputs", {})}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
