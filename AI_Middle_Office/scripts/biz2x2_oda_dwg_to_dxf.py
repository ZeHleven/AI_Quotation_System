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

from app.services.dwg_oda_converter import (  # noqa: E402
    DEFAULT_ODA_OUTPUT_VERSION,
    DwgOdaConversionError,
    convert_dwg_directory_to_dxf_with_oda,
    write_oda_conversion_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-2 使用 ODA File Converter 将 DWG 批量转换为 DXF")
    parser.add_argument("--dwg-dir", required=True, help="输入 DWG 目录")
    parser.add_argument("--oda-exe", required=True, help="ODAFileConverter.exe 路径")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x2" / f"oda_dxf_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        help="DXF 输出目录",
    )
    parser.add_argument(
        "--report-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x2"),
        help="转换报告输出目录",
    )
    parser.add_argument(
        "--output-version",
        default=DEFAULT_ODA_OUTPUT_VERSION,
        help=f"ODA 输出版本，默认 {DEFAULT_ODA_OUTPUT_VERSION}",
    )
    parser.add_argument("--recursive", action="store_true", help="递归处理子目录")
    parser.add_argument("--no-audit", action="store_true", help="关闭 ODA 审计")
    parser.add_argument("--timeout-seconds", type=int, default=300, help="转换超时时间")
    parser.add_argument("--no-write-report", action="store_true", help="只打印结果，不写报告")
    args = parser.parse_args()

    try:
        result = convert_dwg_directory_to_dxf_with_oda(
            args.dwg_dir,
            args.output_dir,
            args.oda_exe,
            output_version=args.output_version,
            recursive=args.recursive,
            audit=not args.no_audit,
            timeout_seconds=args.timeout_seconds,
        )
    except (DwgOdaConversionError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "ODA_DWG_TO_DXF_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload = {
        "ok": result.status in {"converted", "partial_converted"},
        "result": result.as_dict(),
    }

    if not args.no_write_report:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outputs = write_oda_conversion_outputs(
            result,
            args.report_dir,
            stem=f"BIZ2x2_ODA_DWG转DXF结果_{timestamp}",
        )
        payload["outputs"] = outputs

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status in {"converted", "partial_converted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
