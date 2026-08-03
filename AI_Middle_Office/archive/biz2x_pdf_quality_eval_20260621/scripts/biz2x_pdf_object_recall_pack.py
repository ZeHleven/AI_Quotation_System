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

from app.services.drawing_pdf_object_recall_pack import (  # noqa: E402
    build_object_recall_pack,
    write_object_recall_pack_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BIZ-2x PDF object-level recall pack from three-field review")
    parser.add_argument("--review-json", required=True, help="Three-field review JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "object_recall_pack"),
    )
    parser.add_argument(
        "--statuses",
        default="missing_candidate",
        help="Comma/semicolon separated review statuses. Use all to include every non-matched row.",
    )
    parser.add_argument("--max-rows", type=int, default=0, help="Optional cap on selected rows; 0 means no cap")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    review_report = json.loads(Path(args.review_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_object_recall_pack_{timestamp}"
    pack = build_object_recall_pack(
        review_report,
        statuses=_parse_statuses(args.statuses),
        max_rows=args.max_rows or None,
    )
    outputs = write_object_recall_pack_outputs(pack, args.output_dir, stem=stem)
    print(
        json.dumps(
            {
                "ok": pack["ok"],
                "phase": pack["phase"],
                "summary": pack["summary"],
                "safe_to_import_without_evidence": pack["safe_to_import_without_evidence"],
                "answer_columns_count_as_evidence": pack["answer_columns_count_as_evidence"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_statuses(raw_value: str) -> list[str]:
    if raw_value.strip().lower() in {"all", "*"}:
        return []
    return [item.strip() for item in raw_value.replace(";", ",").split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
