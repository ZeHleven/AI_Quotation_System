from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.drawing_pdf_gap_recall_importer import load_external_recall_results
from app.services.drawing_pdf_object_recall_workbench import WORKBENCH_HEADERS, write_object_recall_workbench_outputs


PHASE = "BIZ-2x-pdf-object-recall-workbench-prefill"

STATUS_HEADERS = [
    "row_no",
    "task_no",
    "prefill_status",
    "target_item_name",
    "recommended_pass",
    "source_file",
    "page",
    "tile_id",
    "matched_evidence_id",
    "matched_score",
    "matched_tokens",
    "matched_item_hint",
    "matched_spec_or_method",
    "matched_text",
    "issue",
]

OBJECT_CLASS_EVIDENCE_TYPES = {
    "finish_floor": {"floor"},
    "finish_wall": {"wall"},
    "finish_ceiling": {"ceiling"},
    "electrical_mep": {"electrical"},
    "fixture_valve_schedule": {"plumbing"},
}

PASS_EVIDENCE_TYPES = {
    "door_window_demolition": {"demolition"},
    "demolition_node": {"demolition"},
    "finish_schedule": {"floor", "wall", "ceiling"},
    "electrical_mep": {"electrical"},
    "fixture_valve_schedule": {"plumbing"},
    "table_legend": {"plumbing", "electrical", "general"},
}

GENERIC_TOKENS = {
    "拆除",
    "清运",
    "供货",
    "安装",
    "工程",
    "施工",
    "做法",
    "材料",
    "设备",
    "系统",
    "图例",
    "规格",
    "型号",
    "单位",
    "原有",
    "包含",
}

GENERIC_EVIDENCE = {
    "拆",
    "拆除",
    "清运",
    "安装",
    "施工",
    "图纸",
    "材料",
    "设备",
    "墙面",
    "地面",
    "天花",
    "吊顶",
}


def load_object_recall_workbench_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        for key in ("workbench_rows", "evidence_rows", "rows"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, Mapping)]
        return []
    payload = load_external_recall_results(source)
    rows = payload.get("evidence_rows") or payload.get("rows") or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def build_object_recall_workbench_prefill_report(
    workbench_rows: Sequence[Mapping[str, Any]],
    v2_report: Mapping[str, Any],
    *,
    match_mode: str = "source_page",
    overwrite: bool = False,
) -> dict[str, Any]:
    evidence_rows = [dict(row) for row in v2_report.get("evidence_rows") or [] if isinstance(row, Mapping)]
    output_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for row_no, row in enumerate(workbench_rows, start=1):
        output_row = dict(row)
        if _has_evidence_fields(output_row) and not overwrite:
            match = None
            status = "kept_existing_evidence"
            issue = ""
            matched_tokens: list[str] = []
            score = 0.0
        else:
            match, status, issue, matched_tokens, score = _best_match(output_row, evidence_rows, match_mode=match_mode)
            if match:
                _apply_match(output_row, match)
                status = "prefilled"
        _refresh_fill_state(output_row)
        status_counts[status] += 1
        output_rows.append(output_row)
        status_rows.append(_status_row(row_no, output_row, match, status, issue, matched_tokens, score))

    class_summary = _aggregate(output_rows, group_fields=("object_class",))
    pass_summary = _aggregate(output_rows, group_fields=("recommended_pass",))
    source_page_summary = _aggregate(output_rows, group_fields=("source_file", "page"))
    summary = {
        "object_recall_task_count": len(output_rows),
        "local_evidence_count": len(evidence_rows),
        "prefilled_row_count": status_counts.get("prefilled", 0),
        "kept_existing_evidence_count": status_counts.get("kept_existing_evidence", 0),
        "not_prefilled_row_count": len(output_rows)
        - status_counts.get("prefilled", 0)
        - status_counts.get("kept_existing_evidence", 0),
        "image_link_count": sum(1 for row in output_rows if _truthy(row.get("image_exists"))),
        "missing_image_count": sum(1 for row in output_rows if not _truthy(row.get("image_exists"))),
        "importable_row_count": sum(1 for row in output_rows if str(row.get("fill_status") or "") == "importable"),
        "answer_only_count": sum(1 for row in output_rows if str(row.get("fill_status") or "") == "answer_only_reference"),
        "blank_task_count": sum(1 for row in output_rows if str(row.get("fill_status") or "") == "blank_task"),
        "status_counts": dict(status_counts),
        "object_class_counts": dict(Counter(str(row.get("object_class") or "") for row in output_rows)),
        "recommended_pass_counts": dict(Counter(str(row.get("recommended_pass") or "") for row in output_rows)),
        "match_mode": match_mode,
        "overwrite": overwrite,
        "answer_columns_used_for_prefill": False,
        "safe_to_import_without_review": False,
        "quantity_status": "deferred_until_three_fields_accepted",
    }
    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_to_import_without_evidence": False,
        "answer_columns_count_as_evidence": False,
        "summary": summary,
        "class_summary_rows": class_summary,
        "pass_summary_rows": pass_summary,
        "source_page_summary_rows": source_page_summary,
        "prefill_status_rows": status_rows,
        "workbench_rows": output_rows,
    }


def write_object_recall_workbench_prefill_outputs(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    outputs = write_object_recall_workbench_outputs(report, output_dir, stem=stem)
    status_csv = Path(output_dir) / f"{Path(outputs['json']).stem}_prefill_status.csv"
    _write_csv(status_csv, report.get("prefill_status_rows") or [], STATUS_HEADERS)
    outputs["prefill_status_csv"] = str(status_csv)
    payload_path = Path(outputs["json"])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["outputs"] = outputs
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs


def _best_match(
    row: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    match_mode: str,
) -> tuple[Mapping[str, Any] | None, str, str, list[str], float]:
    tokens = _target_tokens(row)
    if not tokens:
        return None, "no_target_tokens", "no usable target tokens", [], 0.0
    allowed_types = _allowed_evidence_types(row)
    source_file = _file_key(row.get("source_file"))
    page = str(row.get("page") or "").strip()
    tile_ids = set(_split_values(row.get("tile_id")))
    candidates: list[tuple[float, list[str], Mapping[str, Any]]] = []
    scoped_count = 0
    type_filtered_count = 0
    token_filtered_count = 0
    for evidence in evidence_rows:
        if _file_key(evidence.get("source_file")) != source_file:
            continue
        if page and str(evidence.get("page") or "").strip() != page:
            continue
        evidence_tile = str(evidence.get("tile_id") or "").strip()
        exact_tile = bool(evidence_tile and evidence_tile in tile_ids)
        if match_mode == "exact_tile" and not exact_tile:
            continue
        scoped_count += 1
        evidence_type = str(evidence.get("evidence_type") or "")
        if allowed_types and evidence_type not in allowed_types:
            type_filtered_count += 1
            continue
        evidence_text = _evidence_search_text(evidence)
        matched_tokens = [token for token in tokens if token in evidence_text]
        if not matched_tokens:
            token_filtered_count += 1
            continue
        if _is_generic_evidence(evidence_text):
            token_filtered_count += 1
            continue
        score = _score_match(evidence, matched_tokens=matched_tokens, exact_tile=exact_tile)
        candidates.append((score, matched_tokens, evidence))
    if not candidates:
        if token_filtered_count:
            return None, "no_object_token_match", "local evidence exists but target object tokens did not match", [], 0.0
        if type_filtered_count:
            return None, "no_allowed_evidence_type", "local evidence exists but type is not allowed for this task", [], 0.0
        if scoped_count:
            return None, "no_usable_local_evidence", "local evidence in scope was too generic or incomplete", [], 0.0
        return None, "no_local_evidence_scope_match", "no local evidence on the same source/page scope", [], 0.0
    score, matched_tokens, match = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    return match, "prefilled", "", matched_tokens, score


def _target_tokens(row: Mapping[str, Any]) -> list[str]:
    raw_parts: list[str] = []
    for key in ("target_object_terms", "required_evidence_keywords"):
        raw_parts.extend(_split_values(row.get(key)))
    raw_parts.extend(_tokenize_text(row.get("target_item_name")))
    raw_parts.extend(_tokenize_text(row.get("target_feature")))
    result: list[str] = []
    seen: set[str] = set()
    for token in raw_parts:
        normalized = _clean_token(token)
        if not normalized or normalized in seen or normalized in GENERIC_TOKENS:
            continue
        if len(normalized) < 2 and not re.search(r"[A-Za-z0-9]{2,}", normalized):
            continue
        seen.add(normalized)
        result.append(normalized)
    result.sort(key=len, reverse=True)
    return result[:24]


def _allowed_evidence_types(row: Mapping[str, Any]) -> set[str]:
    object_class = str(row.get("object_class") or "")
    if object_class in OBJECT_CLASS_EVIDENCE_TYPES:
        return set(OBJECT_CLASS_EVIDENCE_TYPES[object_class])
    return set(PASS_EVIDENCE_TYPES.get(str(row.get("recommended_pass") or ""), set()))


def _score_match(evidence: Mapping[str, Any], *, matched_tokens: Sequence[str], exact_tile: bool) -> float:
    confidence = _float(evidence.get("confidence"), 0.0)
    text = _evidence_search_text(evidence)
    return (
        confidence * 2
        + (3 if exact_tile else 0)
        + len(matched_tokens) * 2
        + min(len(text), 80) / 80
        + max((len(token) for token in matched_tokens), default=0) / 10
    )


def _apply_match(row: dict[str, Any], evidence: Mapping[str, Any]) -> None:
    row["evidence_item_hint"] = _first(evidence, "raw_item_name", "item_hint")
    row["evidence_spec_or_method"] = _first(evidence, "spec_or_method", "evidence_text", "text")
    row["evidence_suggested_unit"] = _first(evidence, "suggested_unit", "unit")
    row["evidence_text"] = _first(evidence, "evidence_text", "text", "normalized_text")


def _refresh_fill_state(row: dict[str, Any]) -> None:
    item_hint = _first(row, "evidence_item_hint")
    spec = _first(row, "evidence_spec_or_method")
    unit = _first(row, "evidence_suggested_unit")
    text = _first(row, "evidence_text")
    if (item_hint or spec) and text:
        status = "importable"
        hint = "证据字段已具备，可回灌验收。"
    elif any((item_hint, spec, unit, text)):
        status = "importable_weak"
        hint = "补齐 evidence_text，并尽量填写对象名称/做法/单位。"
    elif any(_first(row, key) for key in ("target_item_name", "target_feature", "target_unit")):
        status = "answer_only_reference"
        hint = "当前只有目标答案参考；必须从图纸图片中填写真实 evidence_* 字段。"
    else:
        status = "blank_task"
        hint = "补充图纸证据字段。"
    row["ready_for_import"] = "true" if status == "importable" else "false"
    row["fill_status"] = status
    row["fill_hint"] = hint


def _status_row(
    row_no: int,
    row: Mapping[str, Any],
    match: Mapping[str, Any] | None,
    status: str,
    issue: str,
    matched_tokens: Sequence[str],
    score: float,
) -> dict[str, Any]:
    match = match or {}
    return {
        "row_no": row_no,
        "task_no": row.get("task_no", ""),
        "prefill_status": status,
        "target_item_name": row.get("target_item_name", ""),
        "recommended_pass": row.get("recommended_pass", ""),
        "source_file": row.get("source_file", ""),
        "page": row.get("page", ""),
        "tile_id": row.get("tile_id", ""),
        "matched_evidence_id": match.get("evidence_id", ""),
        "matched_score": round(score, 4) if score else "",
        "matched_tokens": "；".join(matched_tokens),
        "matched_item_hint": _first(match, "raw_item_name", "item_hint"),
        "matched_spec_or_method": _first(match, "spec_or_method", "evidence_text", "text"),
        "matched_text": _first(match, "evidence_text", "text", "normalized_text"),
        "issue": issue,
    }


def _aggregate(rows: Sequence[Mapping[str, Any]], *, group_fields: Sequence[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = " | ".join(str(row.get(field) or "") for field in group_fields)
        grouped[key].append(row)
    result: list[dict[str, Any]] = []
    for key, group_rows in sorted(grouped.items(), key=lambda item: item[0]):
        status_counts = Counter(str(row.get("fill_status") or "") for row in group_rows)
        result.append(
            {
                "group_key": key,
                "task_count": len(group_rows),
                "image_link_count": sum(1 for row in group_rows if _truthy(row.get("image_exists"))),
                "missing_image_count": sum(1 for row in group_rows if not _truthy(row.get("image_exists"))),
                "importable_row_count": status_counts.get("importable", 0),
                "answer_only_count": status_counts.get("answer_only_reference", 0),
            }
        )
    return result


def _evidence_search_text(evidence: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("raw_item_name", "item_hint", "spec_or_method", "evidence_text", "text", "normalized_text"):
        value = evidence.get(key)
        if value:
            parts.append(str(value))
    material_codes = evidence.get("material_codes")
    if isinstance(material_codes, Sequence) and not isinstance(material_codes, str):
        parts.extend(str(item) for item in material_codes if item)
    elif material_codes:
        parts.append(str(material_codes))
    raw = evidence.get("raw")
    if isinstance(raw, Mapping):
        for key in ("item_hint", "spec_or_method", "text", "normalized_text"):
            value = raw.get(key)
            if value:
                parts.append(str(value))
    return " ".join(parts)


def _is_generic_evidence(text: str) -> bool:
    compact = re.sub(r"[\s,，。；;:：、\-_/\\()（）\[\]【】<>《》|]+", "", text.lower())
    if not compact:
        return True
    if compact in GENERIC_EVIDENCE:
        return True
    return len(compact) <= 2 and not re.search(r"[a-z0-9]{2,}", compact)


def _tokenize_text(value: Any) -> list[str]:
    text = str(value or "")
    tokens = re.findall(r"[A-Za-z]+-?\d+|DN\d+|\d+[xX*]\d+|[\u4e00-\u9fff]{2,}", text)
    result: list[str] = []
    for token in tokens:
        if len(token) <= 8:
            result.append(token)
            continue
        # Keep long phrases only when they are likely material/object names.
        for marker in ("门", "窗", "地砖", "石材", "墙布", "硬包", "天花", "吊顶", "阀", "水表", "地漏", "龙头", "花洒", "台盆"):
            if marker in token:
                result.append(marker if len(marker) >= 2 else token)
    return result


def _clean_token(value: Any) -> str:
    return re.sub(r"[\s,，。；;:：、\-_/\\()（）\[\]【】<>《》|]+", "", str(value or "")).strip()


def _has_evidence_fields(row: Mapping[str, Any]) -> bool:
    return any(
        _first(row, key)
        for key in ("evidence_item_hint", "evidence_spec_or_method", "evidence_suggested_unit", "evidence_text")
    )


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.replace("；", ";").replace(",", ";").split(";") if item.strip()]


def _file_key(value: Any) -> str:
    return "".join(ch.lower() for ch in Path(str(value or "")).name if ch.isalnum())


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _first(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
