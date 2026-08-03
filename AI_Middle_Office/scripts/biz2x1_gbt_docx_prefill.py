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

from app.services.quantity_standard_docx_parser import (  # noqa: E402
    QuantityStandardDocxParseError,
    parse_quantity_standard_docx,
    quantity_standard_docx_summary,
    write_docx_standard_library_import_outputs,
    write_docx_prefill_outputs,
)
from app.services.quantity_standard_library import (  # noqa: E402
    QuantityStandardLibraryError,
    load_quantity_standard_library,
    quantity_standard_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-1 GB/T 50854 Word 标准库自动预填表生成")
    parser.add_argument("--docx-file", default="", help="GB/T 标准 Word 文件路径")
    parser.add_argument(
        "--docx-dir",
        default="",
        help="未指定 --docx-file 时，从该目录搜索 Word 文件",
    )
    parser.add_argument(
        "--name-contains",
        default="GBT50854",
        help="按文件名包含关键字搜索 Word，默认 GBT50854",
    )
    parser.add_argument("--standard-code", default="GBT50854-2024", help="标准编号，例如 GBT50856-2024")
    parser.add_argument(
        "--standard-name",
        default="房屋建筑与装饰工程工程量计算标准",
        help="标准名称",
    )
    parser.add_argument("--standard-label", default="GB/T 50854-2024", help="标准展示名称")
    parser.add_argument(
        "--output-stem-prefix",
        default="BIZ2x1_GBT50854标准库",
        help="预填输出文件名前缀",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x1"),
        help="输出目录，默认 outputs/biz2x1",
    )
    parser.add_argument(
        "--standard-output-dir",
        default=str(BACKEND_ROOT / "data" / "standards"),
        help="标准库 JSON 输出目录，默认 data/standards",
    )
    parser.add_argument(
        "--write-standard-library",
        action="store_true",
        help="同时生成可导入标准库 JSON 并用标准库加载器校验",
    )
    parser.add_argument("--no-write", action="store_true", help="只打印摘要，不写输出文件")
    args = parser.parse_args()

    try:
        source = _resolve_docx_source(args.docx_file, args.docx_dir, args.name_contains)
        parsed = parse_quantity_standard_docx(source)
    except (QuantityStandardDocxParseError, FileNotFoundError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "DOCX_STANDARD_PARSE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    result: dict[str, object] = {
        "ok": True,
        "summary": quantity_standard_docx_summary(parsed),
    }

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir)
        try:
            outputs = write_docx_prefill_outputs(
                parsed,
                output_dir,
                stem=f"{args.output_stem_prefix}Word自动预填表_{timestamp}",
            )
        except OSError as exc:
            fallback_dir = BACKEND_ROOT / "biz2x1_reports"
            outputs = write_docx_prefill_outputs(
                parsed,
                fallback_dir,
                stem=f"{args.output_stem_prefix}Word自动预填表_{timestamp}",
            )
            outputs["fallback_reason"] = f"默认输出目录不可写，已回退到 {fallback_dir}: {exc}"
        result["outputs"] = outputs
        if args.write_standard_library:
            standard_outputs = write_docx_standard_library_import_outputs(
                parsed,
                Path(args.standard_output_dir),
                standard_code=args.standard_code,
                standard_name=args.standard_name,
                standard_label=args.standard_label,
            )
            result["standard_library_outputs"] = standard_outputs
            try:
                validated = load_quantity_standard_library(standard_outputs["json"])
            except QuantityStandardLibraryError as exc:
                result["standard_library_validation"] = {
                    "ok": False,
                    "message": str(exc),
                }
            else:
                result["standard_library_validation"] = {
                    "ok": True,
                    "summary": quantity_standard_summary(validated),
                }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _resolve_docx_source(docx_file: str, docx_dir: str, name_contains: str) -> Path:
    if docx_file:
        return Path(docx_file)
    if not docx_dir:
        raise ValueError("请指定 --docx-file 或 --docx-dir")
    directory = Path(docx_dir)
    if not directory.exists():
        raise FileNotFoundError(f"Word 文件目录不存在: {directory}")
    matches = [
        path
        for path in directory.glob("*.docx")
        if not path.name.startswith("~$") and name_contains in path.name
    ]
    if not matches:
        raise FileNotFoundError(f"未在 {directory} 找到包含 {name_contains!r} 的 .docx 文件")
    matches.sort(key=lambda path: path.stat().st_size, reverse=True)
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
