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

from app.services.dwg_preview_probe import (  # noqa: E402
    DwgPreviewProbeError,
    build_dwg_preview_probe_report,
    collect_dwg_files,
    write_dwg_probe_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-2 DWG 转换与图纸预览能力探测")
    parser.add_argument("--dwg-dir", default="", help="DWG 文件目录")
    parser.add_argument("--dwg-file", action="append", default=[], help="可重复传入单个 DWG 文件")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x2"),
        help="探测报告输出目录，默认 outputs/biz2x2",
    )
    parser.add_argument(
        "--extra-tool-dir",
        action="append",
        default=[],
        help="额外转换工具目录，例如 ODAFileConverter.exe 所在目录，可重复传入",
    )
    parser.add_argument(
        "--no-system-tools",
        action="store_true",
        help="只检查 --extra-tool-dir，不检查 PATH 和常见安装目录",
    )
    parser.add_argument("--no-write", action="store_true", help="只打印摘要，不写报告文件")
    args = parser.parse_args()

    try:
        dwg_paths = collect_dwg_files(args.dwg_dir or None, args.dwg_file)
        if not dwg_paths:
            raise DwgPreviewProbeError("未找到 DWG 文件，请传入 --dwg-dir 或 --dwg-file")
        report = build_dwg_preview_probe_report(
            dwg_paths,
            extra_search_paths=args.extra_tool_dir,
            include_system_tools=not args.no_system_tools,
        )
    except DwgPreviewProbeError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "DWG_PREVIEW_PROBE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir)
        try:
            outputs = write_dwg_probe_outputs(
                report,
                output_dir,
                stem=f"BIZ2x2_DWG转换预览探测报告_{timestamp}",
            )
        except OSError as exc:
            fallback_dir = BACKEND_ROOT / "biz2x2_reports"
            outputs = write_dwg_probe_outputs(
                report,
                fallback_dir,
                stem=f"BIZ2x2_DWG转换预览探测报告_{timestamp}",
            )
            outputs["fallback_reason"] = f"默认输出目录不可写，已回退到 {fallback_dir}: {exc}"
        report["outputs"] = outputs

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
