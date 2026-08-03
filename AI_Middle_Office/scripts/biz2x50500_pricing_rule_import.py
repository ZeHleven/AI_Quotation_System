from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.quantity_pricing_rule_docx_parser import (  # noqa: E402
    PricingRuleDocxParseError,
    build_pricing_rule_library,
    write_pricing_rule_library_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x GB/T 50500 计价规则库导入")
    parser.add_argument("--docx-file", required=True, help="GB/T 50500 Word 文件路径")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT / "data" / "standards"),
        help="规则库输出目录",
    )
    parser.add_argument("--standard-code", default="GBT50500-2024", help="标准编号")
    parser.add_argument("--standard-name", default="建设工程工程量清单计价标准", help="标准名称")
    parser.add_argument("--standard-label", default="GB/T 50500-2024", help="标准展示名称")
    parser.add_argument("--no-write", action="store_true", help="只解析并输出摘要，不写文件")
    args = parser.parse_args()

    try:
        library = build_pricing_rule_library(
            args.docx_file,
            standard_code=args.standard_code,
            standard_name=args.standard_name,
            standard_label=args.standard_label,
        )
    except (PricingRuleDocxParseError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "PRICING_RULE_DOCX_PARSE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    result: dict[str, object] = {
        "ok": True,
        "summary": library["summary"],
    }
    if not args.no_write:
        outputs = write_pricing_rule_library_outputs(
            args.docx_file,
            args.output_dir,
            standard_code=args.standard_code,
            standard_name=args.standard_name,
            standard_label=args.standard_label,
        )
        result["outputs"] = outputs

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
