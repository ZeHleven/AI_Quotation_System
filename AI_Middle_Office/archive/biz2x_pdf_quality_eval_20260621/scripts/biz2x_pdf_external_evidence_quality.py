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

from app.services.drawing_pdf_external_evidence_quality import (  # noqa: E402
    build_external_evidence_quality_report,
    write_external_evidence_quality_outputs,
)
from app.services.drawing_pdf_gap_recall_importer import load_external_recall_results  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Score and filter external GLM/manual BIZ-2x PDF recall evidence")
    parser.add_argument(
        "--external-results",
        action="append",
        required=True,
        help="External recall result JSON/CSV/XLSX; may repeat to merge multiple batches",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "external_evidence_quality"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="Count review-quality rows as importable in the filtered evidence payload",
    )
    args = parser.parse_args()

    external_results = _merge_external_results([load_external_recall_results(path) for path in args.external_results])
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_external_evidence_quality_{timestamp}"
    report = build_external_evidence_quality_report(
        external_results,
        source_path=";".join(args.external_results),
        include_review=args.include_review,
    )
    outputs = write_external_evidence_quality_outputs(report, Path(args.output_dir), stem=stem)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "phase": report["phase"],
                "summary": report["summary"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _merge_external_results(payloads: list[dict[str, object]]) -> dict[str, object]:
    evidence_rows: list[dict[str, object]] = []
    for payload in payloads:
        evidence_rows.extend(_external_rows(payload))
    return {"evidence_rows": evidence_rows}


def _external_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    for key in ("evidence_rows", "recall_evidence", "rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
    visual_report = payload.get("visual_evidence_report")
    if isinstance(visual_report, dict) and isinstance(visual_report.get("evidence_rows"), list):
        return [dict(row) for row in visual_report.get("evidence_rows") or [] if isinstance(row, dict)]
    source_rows: list[dict[str, object]] = []
    call_groups: list[dict[str, object]] = []
    for key in ("call_results", "calls", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            call_groups.extend(dict(row) for row in value if isinstance(row, dict))
    if not call_groups and isinstance(payload.get("evidence_items"), list):
        call_groups.append(payload)
    for call in call_groups:
        items = (
            call.get("evidence_rows")
            or call.get("evidence_items")
            or call.get("items")
            or call.get("drawing_items")
            or []
        )
        if not isinstance(items, list):
            continue
        call_meta = {
            "call_no": call.get("call_no"),
            "source_file": _first(call, "source_file", "PDF文件"),
            "page": _first(call, "page", "页码"),
            "tile_id": call.get("tile_id"),
            "vision_pass": _first(call, "vision_pass", "recommended_pass", "prompt_mode"),
            "model": call.get("model"),
        }
        for item in items:
            if isinstance(item, dict):
                source_rows.append({**call_meta, **dict(item)})
    if source_rows:
        return source_rows
    return []


def _first(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
