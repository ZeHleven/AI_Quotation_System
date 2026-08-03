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

from app.services.dxf_trace_review_pack import (  # noqa: E402
    build_trace_review_pack,
    load_json_report,
    write_trace_review_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9h-2 标准规则 trace 自动初判复核包生成")
    parser.add_argument("--standard-rule-binding-report", default="", help="BIZ-2x-9f/9g 标准规则绑定 trace JSON；不填则取最新")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9h"))
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        binding_report_path = Path(args.standard_rule_binding_report) if args.standard_rule_binding_report else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9fg",
            "BIZ2x9fg_标准规则绑定trace_*.json",
        )
        pack = build_trace_review_pack(load_json_report(binding_report_path))
        pack["inputs"] = {"standard_rule_binding_report": str(binding_report_path)}
        stem = f"BIZ2x9h2_标准规则trace自动初判复核包_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pack["outputs"] = write_trace_review_outputs(pack, args.output_dir, stem=stem)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.print_summary_only:
        print(json.dumps({"ok": True, "summary": pack["summary"], "outputs": pack["outputs"]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    return 0


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    if not matches:
        raise ValueError(f"No file matched {pattern} in {directory}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
