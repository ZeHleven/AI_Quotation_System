from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.services.drawing_pdf_object_recall_capture_runner import (
    run_object_recall_capture_pack,
    write_object_recall_capture_run_outputs,
)


PHASE = "BIZ-2x-pdf-feature-precision-capture-runner"


def run_feature_precision_capture_pack(
    capture_pack: Mapping[str, Any],
    *,
    execute: bool = False,
    max_calls: int | None = None,
    start_call_no: int = 1,
    end_call_no: int | None = None,
    vision_client: Any = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    transformed_pack, row_lookup = _as_object_capture_pack(capture_pack)
    report = run_object_recall_capture_pack(
        transformed_pack,
        execute=execute,
        max_calls=max_calls,
        start_call_no=start_call_no,
        end_call_no=end_call_no,
        vision_client=vision_client,
        trace_id=trace_id,
    )

    for call_row in report.get("call_rows") or []:
        source = row_lookup.get(_int(call_row.get("call_no"), 0), {})
        call_row["defect_nos"] = source.get("defect_nos", "")
        call_row["feature_gap_families"] = source.get("feature_gap_families", "")

    for evidence in report.get("evidence_rows") or []:
        call_no = _call_no_for_evidence(evidence, row_lookup)
        source = row_lookup.get(call_no, {})
        defect_nos = source.get("defect_nos", evidence.get("task_nos", ""))
        evidence["source_kind"] = "pdf_feature_precision_capture_llm"
        evidence["task_no"] = _first_number(defect_nos)
        evidence["task_nos"] = defect_nos
        evidence["defect_nos"] = defect_nos
        evidence["feature_gap_families"] = source.get("feature_gap_families", "")

    summary = {
        **dict(report.get("summary") or {}),
        "source_feature_capture_call_count": len(capture_pack.get("capture_rows") or []),
        "feature_gap_family_counts": _feature_family_counts(report.get("call_rows") or []),
        "target_fields_sent_to_model": False,
        "answer_columns_count_as_evidence": False,
        "quantity_status": "deferred_until_three_fields_accepted",
    }
    return {
        **dict(report),
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "visual_evidence_report": {"evidence_rows": report.get("evidence_rows") or []},
    }


def write_feature_precision_capture_run_outputs(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    file_stem = stem or f"BIZ2x_PDF_feature_precision_capture_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    outputs = write_object_recall_capture_run_outputs(report, output_dir, stem=file_stem)
    json_path = Path(outputs["json"])
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["phase"] = PHASE
    payload["outputs"] = outputs
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs


def _as_object_capture_pack(capture_pack: Mapping[str, Any]) -> tuple[dict[str, Any], dict[int, Mapping[str, Any]]]:
    rows: list[dict[str, Any]] = []
    lookup: dict[int, Mapping[str, Any]] = {}
    for index, row in enumerate(capture_pack.get("capture_rows") or [], start=1):
        if not isinstance(row, Mapping):
            continue
        capture_no = _int(row.get("capture_no"), index)
        lookup[capture_no] = row
        rows.append(
            {
                "capture_no": capture_no,
                "recommended_pass": row.get("recommended_pass", ""),
                "source_file": row.get("source_file", ""),
                "page": row.get("page", ""),
                "tile_id": row.get("tile_id", ""),
                "image_path": row.get("image_path", ""),
                "task_nos": row.get("defect_nos", ""),
                "object_classes": row.get("object_classes", ""),
                "task_count": row.get("task_count", ""),
                "prompt_text": row.get("prompt_text", ""),
            }
        )
    return {**dict(capture_pack), "capture_rows": rows}, lookup


def _call_no_for_evidence(evidence: Mapping[str, Any], row_lookup: Mapping[int, Mapping[str, Any]]) -> int:
    defect_nos = str(evidence.get("task_nos") or "")
    for call_no, row in row_lookup.items():
        if str(row.get("defect_nos") or "") == defect_nos:
            return call_no
    return 0


def _feature_family_counts(call_rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in call_rows:
        for family in str(row.get("feature_gap_families") or "").replace("；", ";").split(";"):
            family = family.strip()
            if family:
                counter[family] += 1
    return dict(counter)


def _first_number(value: Any) -> str:
    for part in str(value or "").replace("；", ";").replace(",", ";").split(";"):
        part = part.strip()
        if part.isdigit():
            return part
    return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
