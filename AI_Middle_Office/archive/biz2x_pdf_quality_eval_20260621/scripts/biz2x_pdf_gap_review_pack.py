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

from app.services.drawing_pdf_gap_review_pack import (  # noqa: E402
    build_gap_review_evidence_pack,
    write_gap_review_evidence_pack,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BIZ-2x PDF three-field gap review evidence pack")
    parser.add_argument("--v2-json", required=True, help="PDF V2 takeoff report JSON with three_field_gap_rows")
    parser.add_argument(
        "--source-report-dir",
        default="",
        help="Directory containing source PDF visual evidence JSON files used by the V2 report",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "gap_review_pack"),
    )
    parser.add_argument(
        "--priority-prefixes",
        default="P1",
        help="Comma/semicolon separated prefixes, for example P1 or P1,P2. Use all to include every gap.",
    )
    parser.add_argument("--max-gaps", type=int, default=0, help="Optional cap on selected gaps; 0 means no cap")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    parser.add_argument("--no-copy-images", action="store_true")
    args = parser.parse_args()

    v2_report = json.loads(Path(args.v2_json).read_text(encoding="utf-8"))
    prefixes = _parse_priority_prefixes(args.priority_prefixes)
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_gap_review_evidence_pack_{timestamp}"
    pack = build_gap_review_evidence_pack(
        v2_report,
        source_report_dir=Path(args.source_report_dir) if args.source_report_dir else None,
        priority_prefixes=prefixes,
        max_gaps=args.max_gaps or None,
    )
    outputs = write_gap_review_evidence_pack(
        pack,
        Path(args.output_dir),
        stem=stem,
        copy_images=not args.no_copy_images,
    )
    print(
        json.dumps(
            {
                "ok": pack["ok"],
                "phase": pack["phase"],
                "summary": {**pack["summary"], "copy_images": not args.no_copy_images},
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
