from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_project_lexicon import (
    DEFAULT_PROJECT_LEXICON_PATH,
    DEFAULT_SAMPLE_ANSWER_XLSX,
    build_project_lexicon_from_answer_rows,
    extract_sample_answer_rows,
    write_project_lexicon_outputs,
)
from app.services.drawing_standard_matcher import DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH
from app.services.quantity_standard_library import load_quantity_standard_library


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R1-1 从样例人工清单生成项目识别词库")
    parser.add_argument("--manual-xlsx", default=str(DEFAULT_SAMPLE_ANSWER_XLSX), help="人工四字段清单 .xlsx")
    parser.add_argument("--standard-file", default=str(DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH), help="GB/T 50854-2024 active 标准库 JSON")
    parser.add_argument("--lexicon-json", default=str(DEFAULT_PROJECT_LEXICON_PATH), help="识别词库 JSON 输出路径")
    parser.add_argument("--report-dir", default="../outputs/biz2x_rule_reverse", help="词库报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    answer_rows, sheet_summaries = extract_sample_answer_rows(args.manual_xlsx)
    library = load_quantity_standard_library(args.standard_file or None)
    lexicon = build_project_lexicon_from_answer_rows(answer_rows, library=library)
    lexicon["source"]["manual_xlsx"] = str(Path(args.manual_xlsx).resolve())
    lexicon["source"]["sheet_summaries"] = sheet_summaries
    lexicon["source"]["standard_file"] = str(Path(library.source_path).resolve()) if library.source_path else ""

    lexicon_path = Path(args.lexicon_json)
    lexicon_path.parent.mkdir(parents=True, exist_ok=True)
    lexicon_path.write_text(json.dumps(lexicon, ensure_ascii=False, indent=2), encoding="utf-8")
    report_outputs = write_project_lexicon_outputs(
        lexicon,
        args.report_dir,
        stem=f"BIZ2x_R1_项目识别词库_{timestamp}",
    )

    print(
        json.dumps(
            {
                "manual_answer_row_count": lexicon["summary"]["manual_answer_row_count"],
                "lexicon_entry_count": lexicon["summary"]["lexicon_entry_count"],
                "category_counts": lexicon["summary"]["category_counts"],
                "standard_scope_counts": lexicon["summary"]["standard_scope_counts"],
                "lexicon_json": str(lexicon_path),
                "report_outputs": report_outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
