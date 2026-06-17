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

from app.services.quantity_standard_library import (  # noqa: E402
    QuantityStandardLibraryError,
    load_quantity_standard_library,
    quantity_standard_summary,
    search_quantity_standard_items,
    write_quantity_standard_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-1 GB/T 50854-2024 标准库最小可用版只读预览")
    parser.add_argument(
        "--standard-file",
        default=str(BACKEND_ROOT / "data" / "standards" / "gbtn50854_2024_min_seed.json"),
        help="标准库 JSON 文件路径",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x1"),
        help="预览输出目录，默认 outputs/biz2x1",
    )
    parser.add_argument(
        "--query",
        default="",
        help="可选：按关键词搜索标准库候选项目，例如 墙面乳胶漆",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="只打印摘要，不写 Markdown/CSV/JSON 输出文件",
    )
    args = parser.parse_args()

    try:
        library = load_quantity_standard_library(args.standard_file)
    except QuantityStandardLibraryError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "STANDARD_LIBRARY_LOAD_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    summary = quantity_standard_summary(library)
    result: dict[str, object] = {
        "ok": True,
        "summary": summary,
    }

    if args.query:
        result["query"] = args.query
        result["matches"] = search_quantity_standard_items(library, args.query, include_draft=True, limit=10)

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir)
        try:
            outputs = write_quantity_standard_outputs(
                library,
                output_dir,
                stem=f"biz2x1_quantity_standard_{timestamp}",
            )
        except OSError as exc:
            fallback_dir = BACKEND_ROOT / "biz2x1_reports"
            outputs = write_quantity_standard_outputs(
                library,
                fallback_dir,
                stem=f"biz2x1_quantity_standard_{timestamp}",
            )
            outputs["fallback_reason"] = f"默认输出目录不可写，已回退到 {fallback_dir}: {exc}"
        result["outputs"] = outputs

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
