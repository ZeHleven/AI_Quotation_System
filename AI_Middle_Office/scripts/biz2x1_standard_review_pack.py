from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.quantity_standard_library import (  # noqa: E402
    QuantityStandardLibraryError,
    build_quantity_standard_review_rows,
    load_quantity_standard_library,
    quantity_standard_review_summary,
    write_quantity_standard_review_pack,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-1 GB/T 50854-2024 标准库人工校对包生成")
    parser.add_argument(
        "--standard-file",
        default=str(BACKEND_ROOT / "data" / "standards" / "gbtn50854_2024_min_seed.json"),
        help="标准库 JSON 文件路径",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x1"),
        help="校对包输出目录，默认 outputs/biz2x1",
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

    rows = build_quantity_standard_review_rows(library)
    result: dict[str, object] = {
        "ok": True,
        "summary": quantity_standard_review_summary(library, rows),
    }

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir)
        try:
            outputs = write_quantity_standard_review_pack(
                library,
                output_dir,
                stem=f"BIZ2x1_GBT50854标准库人工校对表_{timestamp}",
            )
        except OSError as exc:
            fallback_dir = BACKEND_ROOT / "biz2x1_reports"
            outputs = write_quantity_standard_review_pack(
                library,
                fallback_dir,
                stem=f"BIZ2x1_GBT50854标准库人工校对表_{timestamp}",
            )
            outputs["fallback_reason"] = f"默认输出目录不可写，已回退到 {fallback_dir}: {exc}"
        result["outputs"] = outputs

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
