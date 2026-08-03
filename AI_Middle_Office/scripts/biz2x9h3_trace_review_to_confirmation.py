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

from app.services.dxf_trace_review_converter import (  # noqa: E402
    build_trace_review_conversion,
    read_trace_review_workbook,
    write_trace_review_conversion_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-9h-3 trace 复核通过行转 BIZ-2x-6 确认行")
    parser.add_argument("--trace-review-workbook", default="", help="业务员填写后的 BIZ-2x-9h-2 trace 复核工作簿；不填则取最新")
    parser.add_argument("--output-dir", default=str(WORKSPACE_ROOT / "outputs" / "biz2x9h3"))
    parser.add_argument("--print-summary-only", action="store_true")
    args = parser.parse_args()

    try:
        workbook_path = Path(args.trace_review_workbook) if args.trace_review_workbook else _latest(
            WORKSPACE_ROOT / "outputs" / "biz2x9h",
            "BIZ2x9h2_标准规则trace自动初判复核包_*.xlsx",
        )
        rows = read_trace_review_workbook(workbook_path)
        conversion = build_trace_review_conversion(rows)
        conversion["inputs"] = {"trace_review_workbook": str(workbook_path)}
        stem = f"BIZ2x9h3_trace复核转确认行_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        conversion["outputs"] = write_trace_review_conversion_outputs(conversion, args.output_dir, stem=stem)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "BIZ2X9H3_TRACE_REVIEW_CONVERSION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if args.print_summary_only:
        print(
            json.dumps(
                {
                    "ok": conversion.get("ok"),
                    "phase": conversion.get("phase"),
                    "generated_at": conversion.get("generated_at"),
                    "summary": conversion.get("summary"),
                    "outputs": conversion.get("outputs", {}),
                    "issues": conversion.get("issues", [])[:10],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(conversion, ensure_ascii=False, indent=2))
    return 0


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    if not matches:
        raise ValueError(f"No file matched {pattern} in {directory}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
