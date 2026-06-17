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

from app.services.drawing_standard_matcher import (  # noqa: E402
    DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH,
    match_drawing_fields_to_standard,
    write_standard_match_outputs,
)
from app.services.quantity_standard_library import (  # noqa: E402
    QuantityStandardLibraryError,
    load_quantity_standard_library,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-4 图纸字段匹配 GB/T 标准项目候选预览")
    parser.add_argument("--field-report", required=True, help="BIZ-2x-3 字段收敛 JSON 报告路径")
    parser.add_argument(
        "--standard-file",
        default=str(DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH),
        help="active GB/T 标准库 JSON，默认使用当前 Word active 标准库",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x4"),
        help="报告输出目录，默认 outputs/biz2x4",
    )
    parser.add_argument("--limit-per-source", type=int, default=5, help="每条图纸线索最多输出多少个标准项目候选")
    parser.add_argument("--min-confidence", type=float, default=0.45, help="最低候选置信度")
    parser.add_argument("--no-write", action="store_true", help="只打印摘要，不写报告")
    parser.add_argument("--print-summary-only", action="store_true", help="只打印 summary 和输出文件路径")
    args = parser.parse_args()

    try:
        field_report_path = Path(args.field_report)
        field_report = json.loads(field_report_path.read_text(encoding="utf-8"))
        library = load_quantity_standard_library(args.standard_file)
        report = match_drawing_fields_to_standard(
            field_report,
            library,
            limit_per_source=args.limit_per_source,
            min_confidence=args.min_confidence,
        )
        report["inputs"] = {
            "field_report": str(field_report_path),
            "standard_file": str(Path(args.standard_file)),
        }
    except (OSError, json.JSONDecodeError, QuantityStandardLibraryError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "BIZ2X4_STANDARD_MATCH_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outputs = write_standard_match_outputs(
            report,
            args.output_dir,
            stem=f"BIZ2x4_GBT标准项目候选匹配_{timestamp}",
        )
        report["outputs"] = outputs

    if args.print_summary_only:
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "phase": report["phase"],
                    "generated_at": report["generated_at"],
                    "safe_for_final_quantity_list": report["safe_for_final_quantity_list"],
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
