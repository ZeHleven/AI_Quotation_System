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

from app.services.dxf_table_reconstructor import (  # noqa: E402
    reconstruct_dxf_tables,
    write_table_reconstruction_outputs,
)
from app.services.dxf_text_extractor import (  # noqa: E402
    DEFAULT_TEXT_RECORD_LIMIT,
    DxfTextExtractionError,
    collect_dxf_files,
    parse_dxf_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-3 DXF 图纸目录、材料表和构造做法表重建")
    parser.add_argument("--dxf-dir", default="", help="DXF 文件目录")
    parser.add_argument("--dxf-file", action="append", default=[], help="可重复传入单个 DXF 文件")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x3"),
        help="重建报告输出目录，默认 outputs/biz2x3",
    )
    parser.add_argument("--text-record-limit", type=int, default=DEFAULT_TEXT_RECORD_LIMIT, help="每个 DXF 保存文字记录上限")
    parser.add_argument("--row-limit-per-table", type=int, default=30, help="每个表格候选最多输出多少行")
    parser.add_argument("--no-write", action="store_true", help="只打印摘要，不写报告")
    parser.add_argument("--print-summary-only", action="store_true", help="只打印 summary 和输出文件路径")
    args = parser.parse_args()

    try:
        dxf_paths = collect_dxf_files(args.dxf_dir or None, args.dxf_file)
        if not dxf_paths:
            raise DxfTextExtractionError("未找到 DXF 文件，请传入 --dxf-dir 或 --dxf-file")
        parsed_files = [parse_dxf_file(path, text_record_limit=args.text_record_limit) for path in dxf_paths]
        report = reconstruct_dxf_tables(parsed_files, row_limit_per_table=args.row_limit_per_table)
    except (DxfTextExtractionError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "DXF_TABLE_RECONSTRUCTION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outputs = write_table_reconstruction_outputs(
            report,
            args.output_dir,
            stem=f"BIZ2x3_DXF表格重建与图纸索引_{timestamp}",
        )
        report["outputs"] = outputs

    if args.print_summary_only:
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "phase": report["phase"],
                    "generated_at": report["generated_at"],
                    "summary": report["summary"],
                    "outputs": report.get("outputs", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
