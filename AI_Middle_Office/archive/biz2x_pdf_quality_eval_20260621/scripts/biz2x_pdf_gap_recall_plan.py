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

from app.services.drawing_pdf_gap_recall_plan import (  # noqa: E402
    build_gap_recall_plan,
    write_gap_recall_plan_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BIZ-2x gap-driven PDF visual recall plan")
    parser.add_argument("--gap-pack-json", required=True, help="Gap review evidence pack JSON")
    parser.add_argument(
        "--source-report-dir",
        default="",
        help="Directory containing source PDF visual evidence JSON files referenced by the gap pack",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "gap_recall_plan"),
    )
    parser.add_argument(
        "--priority-prefixes",
        default="P1,P2",
        help="Comma/semicolon separated prefixes, for example P1,P2. Use all to include every gap.",
    )
    parser.add_argument("--max-gaps", type=int, default=0, help="Optional cap on selected gaps; 0 means no cap")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    gap_pack = json.loads(Path(args.gap_pack_json).read_text(encoding="utf-8"))
    prefixes = _parse_priority_prefixes(args.priority_prefixes)
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_gap_recall_plan_{timestamp}"
    plan = build_gap_recall_plan(
        gap_pack,
        source_report_dir=Path(args.source_report_dir) if args.source_report_dir else None,
        priority_prefixes=prefixes,
        max_gaps=args.max_gaps or None,
    )
    outputs = write_gap_recall_plan_outputs(plan, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": plan["ok"],
                "phase": plan["phase"],
                "summary": plan["summary"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_priority_prefixes(raw_value: str) -> list[str]:
    if raw_value.strip().lower() in {"all", "*"}:
        return [""]
    return [item.strip() for item in raw_value.replace(";", ",").split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
