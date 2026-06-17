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
    extract_sample_answer_rows,
    load_project_lexicon,
)
from app.services.drawing_project_standard_mapping import (
    DEFAULT_PROJECT_STANDARD_MAPPING_PATH,
    build_project_standard_mapping_from_answer_rows,
    write_project_standard_mapping_outputs,
)
from app.services.drawing_standard_matcher import DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH
from app.services.quantity_standard_library import load_quantity_standard_library


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R2-1 从样例人工清单生成标准项目映射表")
    parser.add_argument("--manual-xlsx", default=str(DEFAULT_SAMPLE_ANSWER_XLSX), help="人工四字段清单 .xlsx")
    parser.add_argument("--standard-file", default=str(DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH), help="GB/T 50854-2024 active 标准库 JSON")
    parser.add_argument("--lexicon-json", default=str(DEFAULT_PROJECT_LEXICON_PATH), help="R1 项目识别词库 JSON")
    parser.add_argument("--mapping-json", default=str(DEFAULT_PROJECT_STANDARD_MAPPING_PATH), help="R2 标准映射 JSON 输出")
    parser.add_argument("--report-dir", default="../outputs/biz2x_rule_reverse", help="映射报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    answer_rows, sheet_summaries = extract_sample_answer_rows(args.manual_xlsx)
    library = load_quantity_standard_library(args.standard_file or None)
    lexicon = load_project_lexicon(args.lexicon_json)
    mapping = build_project_standard_mapping_from_answer_rows(answer_rows, library=library, lexicon=lexicon)
    mapping["source"]["manual_xlsx"] = str(Path(args.manual_xlsx).resolve())
    mapping["source"]["sheet_summaries"] = sheet_summaries
    mapping["source"]["standard_file"] = str(Path(library.source_path).resolve()) if library.source_path else ""
    mapping["source"]["lexicon_json"] = str(Path(args.lexicon_json).resolve())

    mapping_path = Path(args.mapping_json)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    report_outputs = write_project_standard_mapping_outputs(
        mapping,
        args.report_dir,
        stem=f"BIZ2x_R2_样例答案标准映射表_{timestamp}",
    )

    print(
        json.dumps(
            {
                "manual_answer_row_count": mapping["summary"]["manual_answer_row_count"],
                "mapping_entry_count": mapping["summary"]["mapping_entry_count"],
                "mapping_status_counts": mapping["summary"]["mapping_status_counts"],
                "unit_check_counts": mapping["summary"]["unit_check_counts"],
                "unique_standard_item_count": mapping["summary"]["unique_standard_item_count"],
                "mapping_json": str(mapping_path),
                "report_outputs": report_outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
