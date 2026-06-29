from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.drawing_pdf_external_recall_template import write_external_recall_template_outputs
from app.services.drawing_pdf_gap_recall_importer import load_external_recall_results


PHASE = "BIZ-2x-pdf-external-recall-prefill"

PASS_EVIDENCE_TYPES = {
    "door_window_demolition": {"demolition"},
    "demolition_node": {"demolition"},
    "electrical_mep": {"electrical"},
    "finish_schedule": {"floor", "wall", "ceiling"},
    "fixture_valve_schedule": {"plumbing"},
    "table_legend": {"plumbing", "electrical", "general"},
}

PASS_REQUIRED_OBJECT_TOKENS = {
    "door_window_demolition": (
        "门",
        "窗",
        "门套",
        "玻璃",
        "售卖",
        "窗口",
        "台阶",
        "洗手台",
        "隔断",
    ),
    "fixture_valve_schedule": (
        "阀",
        "水表",
        "地漏",
        "马桶",
        "台盆",
        "面盆",
        "洗脸盆",
        "小便器",
        "大便器",
        "洗涤盆",
        "花洒",
        "龙头",
        "洁具",
        "管",
    ),
}

GENERIC_EVIDENCE_TEXTS = {
    "拆",
    "拆除",
    "清运",
    "原有",
    "原有构件",
    "安装",
    "施工",
    "图纸",
    "材料",
    "设备",
    "洁具",
    "灯具",
}


def load_external_recall_template_rows(path: str | Path) -> list[dict[str, Any]]:
    payload = load_external_recall_results(path)
    rows = payload.get("evidence_rows") or payload.get("template_rows") or payload.get("rows") or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def build_external_recall_prefill_report(
    template_rows: Sequence[Mapping[str, Any]],
    v2_report: Mapping[str, Any],
    *,
    match_mode: str = "exact_tile",
    overwrite: bool = False,
) -> dict[str, Any]:
    evidence_rows = [dict(row) for row in v2_report.get("evidence_rows") or [] if isinstance(row, Mapping)]
    prefilled_rows: list[dict[str, Any]] = []
    prefill_status_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    pass_counts: Counter[str] = Counter()

    for row_no, row in enumerate(template_rows, start=1):
        output_row = dict(row)
        if _has_evidence_fields(output_row) and not overwrite:
            status = "kept_existing_evidence"
            match = None
        else:
            match, status = _best_match(output_row, evidence_rows, match_mode=match_mode)
            if match:
                _apply_match(output_row, match, match_mode=match_mode)
                status = "prefilled"
        status_counts[status] += 1
        pass_counts[str(output_row.get("recommended_pass") or "")] += 1
        prefilled_rows.append(output_row)
        prefill_status_rows.append(
            {
                "row_no": row_no,
                "task_no": output_row.get("task_no", ""),
                "gap_no": output_row.get("gap_no", ""),
                "recommended_pass": output_row.get("recommended_pass", ""),
                "source_file": output_row.get("source_file", ""),
                "page": output_row.get("page", ""),
                "tile_id": output_row.get("tile_id", ""),
                "prefill_status": status,
                "matched_evidence_id": (match or {}).get("evidence_id", ""),
                "matched_item_hint": (match or {}).get("raw_item_name", ""),
                "matched_evidence_type": (match or {}).get("evidence_type", ""),
                "matched_confidence": (match or {}).get("confidence", ""),
            }
        )

    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "template_row_count": len(prefilled_rows),
            "local_evidence_count": len(evidence_rows),
            "prefilled_row_count": status_counts.get("prefilled", 0),
            "kept_existing_evidence_count": status_counts.get("kept_existing_evidence", 0),
            "not_prefilled_row_count": len(prefilled_rows)
            - status_counts.get("prefilled", 0)
            - status_counts.get("kept_existing_evidence", 0),
            "unmatched_row_count": status_counts.get("no_local_evidence_match", 0),
            "filtered_low_quality_count": status_counts.get("filtered_low_quality_local_evidence", 0),
            "no_allowed_type_count": status_counts.get("no_allowed_local_evidence_type", 0),
            "match_mode": match_mode,
            "overwrite": overwrite,
            "status_counts": dict(status_counts),
            "pass_counts": dict(pass_counts),
            "answer_columns_used_for_prefill": False,
            "safe_to_import_without_review": False,
        },
        "prefill_status_rows": prefill_status_rows,
        "template_rows": prefilled_rows,
    }


def write_external_recall_prefill_outputs(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    file_stem = stem or f"BIZ2x_PDF_external_recall_prefill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return write_external_recall_template_outputs(report, output_dir, stem=file_stem)


def _best_match(
    template_row: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    match_mode: str,
) -> tuple[Mapping[str, Any] | None, str]:
    source_file = str(template_row.get("source_file") or "")
    page = str(template_row.get("page") or "")
    tile_id = str(template_row.get("tile_id") or "")
    pass_name = str(template_row.get("recommended_pass") or "")
    allowed_types = PASS_EVIDENCE_TYPES.get(pass_name, set())
    same_scope_count = 0
    filtered_low_quality_count = 0
    candidates = []
    for evidence in evidence_rows:
        if str(evidence.get("source_file") or "") != source_file:
            continue
        if str(evidence.get("page") or "") != page:
            continue
        if match_mode == "exact_tile" and str(evidence.get("tile_id") or "") != tile_id:
            continue
        same_scope_count += 1
        evidence_type = str(evidence.get("evidence_type") or "")
        if allowed_types and evidence_type not in allowed_types:
            continue
        if match_mode == "source_page" and not _source_page_evidence_quality_ok(pass_name, evidence):
            filtered_low_quality_count += 1
            continue
        candidates.append(evidence)
    if not candidates:
        if filtered_low_quality_count:
            return None, "filtered_low_quality_local_evidence"
        if same_scope_count:
            return None, "no_allowed_local_evidence_type"
        return None, "no_local_evidence_match"
    return sorted(candidates, key=_match_sort_key, reverse=True)[0], "prefilled"


def _match_sort_key(evidence: Mapping[str, Any]) -> tuple[float, int, int]:
    confidence = _float(evidence.get("confidence"), 0.0)
    text_len = len(str(evidence.get("evidence_text") or ""))
    spec_len = len(str(evidence.get("spec_or_method") or ""))
    return (confidence, text_len, spec_len)


def _source_page_evidence_quality_ok(pass_name: str, evidence: Mapping[str, Any]) -> bool:
    text = _evidence_search_text(evidence)
    if _is_generic_evidence_text(text):
        return False
    required_tokens = PASS_REQUIRED_OBJECT_TOKENS.get(pass_name)
    if not required_tokens:
        return True
    return any(token in text for token in required_tokens)


def _is_generic_evidence_text(text: str) -> bool:
    compact = re.sub(r"[\s,，。；;:：、\-_/\\()（）\[\]【】<>《》|]+", "", text.lower())
    if not compact:
        return True
    if compact in GENERIC_EVIDENCE_TEXTS:
        return True
    return len(compact) <= 2 and not re.search(r"[a-z0-9]{2,}", compact)


def _evidence_search_text(evidence: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("raw_item_name", "spec_or_method", "evidence_text"):
        value = evidence.get(key)
        if value:
            parts.append(str(value))
    raw = evidence.get("raw")
    if isinstance(raw, Mapping):
        for key in ("item_hint", "spec_or_method", "text", "normalized_text", "evidence_role"):
            value = raw.get(key)
            if value:
                parts.append(str(value))
    material_codes = evidence.get("material_codes")
    if isinstance(material_codes, Sequence) and not isinstance(material_codes, str):
        parts.extend(str(item) for item in material_codes if item)
    elif material_codes:
        parts.append(str(material_codes))
    return " ".join(parts)


def _apply_match(output_row: dict[str, Any], evidence: Mapping[str, Any], *, match_mode: str) -> None:
    output_row["evidence_role"] = _raw_first(evidence, "evidence_role") or evidence.get("evidence_type", "")
    output_row["discipline"] = evidence.get("discipline", "")
    output_row["item_hint"] = evidence.get("raw_item_name") or _raw_first(evidence, "item_hint")
    output_row["space"] = evidence.get("space", "")
    output_row["material_codes"] = _json_text(evidence.get("material_codes") or [])
    output_row["spec_or_method"] = evidence.get("spec_or_method") or _raw_first(evidence, "spec_or_method")
    output_row["suggested_unit"] = evidence.get("suggested_unit", "")
    output_row["text"] = evidence.get("evidence_text") or _raw_first(evidence, "text")
    output_row["normalized_text"] = evidence.get("evidence_text") or _raw_first(evidence, "normalized_text")
    output_row["confidence"] = evidence.get("confidence", "")
    output_row["model"] = "local_v2_evidence_prefill"
    output_row["needs_manual_review"] = "true"
    output_row["reason"] = f"local_prefill_{match_mode}:{evidence.get('evidence_id', '')}"


def _has_evidence_fields(row: Mapping[str, Any]) -> bool:
    return any(str(row.get(key) or "").strip() for key in ("item_hint", "spec_or_method", "suggested_unit", "text"))


def _raw_first(evidence: Mapping[str, Any], key: str) -> str:
    value = evidence.get(key)
    if value:
        return str(value)
    raw = evidence.get("raw")
    if isinstance(raw, Mapping) and raw.get(key):
        return str(raw.get(key))
    return ""


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default
