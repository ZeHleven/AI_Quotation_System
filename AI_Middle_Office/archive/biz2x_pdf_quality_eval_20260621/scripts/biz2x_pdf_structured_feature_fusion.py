from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_pdf_gap_recall_importer import load_external_recall_results  # noqa: E402
from app.services.drawing_pdf_structured_feature_fusion import (  # noqa: E402
    build_structured_feature_fusion_report,
    write_structured_feature_fusion_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fuse visible BIZ-2x PDF note evidence and DN/De table evidence into structured item candidates."
    )
    parser.add_argument(
        "--diameter-results",
        action="append",
        required=True,
        help="DN/De table evidence JSON/CSV/XLSX. May repeat; rows are merged.",
    )
    parser.add_argument(
        "--note-results",
        action="append",
        default=[],
        help="Optional visual note evidence JSON/CSV/XLSX. May repeat.",
    )
    parser.add_argument(
        "--note-text",
        action="append",
        default=[],
        help="Visible drawing note text verified from the rendered PDF. May repeat.",
    )
    parser.add_argument(
        "--note-text-file",
        action="append",
        default=[],
        help="UTF-8 text file containing visible drawing note text. May repeat.",
    )
    parser.add_argument("--source-name", default="structured_feature_fusion")
    parser.add_argument(
        "--emit-supply",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Emit water-supply pipe rows when a SUS304 water-supply note is present.",
    )
    parser.add_argument(
        "--emit-drain",
        choices=("none", "note_de", "table_de"),
        default="none",
        help="Emit drain-pipe rows from cast-iron drain notes. note_de only uses De values visible in the note; table_de uses all De table rows.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "structured_feature_fusion"),
    )
    parser.add_argument("--stem", default="BIZ2x_PDF_structured_feature_fusion")
    args = parser.parse_args()

    diameter_results = _merge_payloads([load_external_recall_results(path) for path in args.diameter_results])
    note_results = [load_external_recall_results(path) for path in args.note_results]
    note_texts = [*args.note_text, *_read_text_files(args.note_text_file)]
    report = build_structured_feature_fusion_report(
        diameter_results,
        note_texts=note_texts,
        note_results=note_results,
        source_name=args.source_name,
        emit_supply=args.emit_supply,
        emit_drain=args.emit_drain,
    )
    outputs = write_structured_feature_fusion_outputs(report, args.output_dir, stem=args.stem)
    print(
        json.dumps(
            {
                "ok": True,
                "phase": report.get("phase"),
                "summary": report.get("summary"),
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _merge_payloads(payloads: list[dict[str, object]]) -> dict[str, object]:
    evidence_rows: list[dict[str, object]] = []
    for payload in payloads:
        evidence_rows.extend(_rows(payload))
    return {"evidence_rows": evidence_rows}


def _rows(payload: dict[str, object]) -> list[dict[str, object]]:
    for key in ("evidence_rows", "recall_evidence", "rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
    visual = payload.get("visual_evidence_report")
    if isinstance(visual, dict) and isinstance(visual.get("evidence_rows"), list):
        return [dict(row) for row in visual.get("evidence_rows") or [] if isinstance(row, dict)]
    return []


def _read_text_files(paths: list[str]) -> list[str]:
    texts: list[str] = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8").strip()
        if text:
            texts.append(text)
    return texts


if __name__ == "__main__":
    raise SystemExit(main())
