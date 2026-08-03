from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.dxf_standard_rule_binder import (  # noqa: E402
    DxfStandardRuleBindingError,
    build_standard_rule_binding_report,
    load_json_report,
    write_standard_rule_binding_outputs,
)
from app.services.drawing_standard_matcher import DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH  # noqa: E402
from app.services.quantity_standard_library import load_quantity_standard_library  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9f/9g 标准项目绑定与标准规则 trace 生成")
    parser.add_argument("--quantity-suggestion-report", default="", help="BIZ-2x-9cde 几何建议量 JSON；不填则取最新")
    parser.add_argument("--standard-match-report", default="", help="BIZ-2x-4 标准候选匹配 JSON；不填则取最新")
    parser.add_argument("--standard-library", default=str(DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH), help="active GB/T 标准库 JSON")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9fg"))
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        quantity_suggestion_path = Path(args.quantity_suggestion_report) if args.quantity_suggestion_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9cde",
            "BIZ2x9cde_低风险几何建议量_*.json",
        )
        standard_match_path = Path(args.standard_match_report) if args.standard_match_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x4",
            "BIZ2x4_GBT标准项目候选匹配_*.json",
        )
        library = load_quantity_standard_library(args.standard_library)
        report = build_standard_rule_binding_report(
            quantity_suggestion_report=load_json_report(quantity_suggestion_path),
            standard_match_report=load_json_report(standard_match_path),
            library=library,
        )
        report["inputs"] = {
            "quantity_suggestion_report": str(quantity_suggestion_path),
            "standard_match_report": str(standard_match_path),
            "standard_library": str(args.standard_library),
        }
        stem = f"BIZ2x9fg_标准规则绑定trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report["outputs"] = write_standard_rule_binding_outputs(report, args.output_dir, stem=stem)
    except (DxfStandardRuleBindingError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.print_summary_only:
        print(json.dumps({"ok": True, "summary": report["summary"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    if not matches:
        raise DxfStandardRuleBindingError(f"No file matched {pattern} in {directory}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
