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

from app.services.drawing_project_scope_review import (
    DEFAULT_PROJECT_SCOPE_REVIEW_PATH,
    build_project_scope_review,
    write_project_scope_review_outputs,
)
from app.services.drawing_project_standard_mapping import (
    DEFAULT_PROJECT_STANDARD_MAPPING_PATH,
    load_project_standard_mapping,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R2-2 单位冲突与补充清单边界复核")
    parser.add_argument("--mapping-json", default=str(DEFAULT_PROJECT_STANDARD_MAPPING_PATH), help="R2-1 标准映射 JSON")
    parser.add_argument("--scope-review-json", default=str(DEFAULT_PROJECT_SCOPE_REVIEW_PATH), help="R2-2 复核规则 JSON 输出")
    parser.add_argument("--report-dir", default="../outputs/biz2x_rule_reverse", help="复核报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    mapping = load_project_standard_mapping(args.mapping_json)
    review = build_project_scope_review(mapping)
    review["source"]["mapping_json"] = str(Path(args.mapping_json).resolve())

    review_path = Path(args.scope_review_json)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    report_outputs = write_project_scope_review_outputs(
        review,
        args.report_dir,
        stem=f"BIZ2x_R2_单位冲突与补充清单边界复核_{timestamp}",
    )

    print(
        json.dumps(
            {
                "scope_review_entry_count": review["summary"]["scope_review_entry_count"],
                "issue_row_count": review["summary"]["issue_row_count"],
                "recognition_allowed_count": review["summary"]["recognition_allowed_count"],
                "business_confirmation_required_count": review["summary"]["business_confirmation_required_count"],
                "review_action_counts": review["summary"]["review_action_counts"],
                "scope_bucket_counts": review["summary"]["scope_bucket_counts"],
                "scope_review_json": str(review_path),
                "report_outputs": report_outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
