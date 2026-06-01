import json
import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.models.quote_cost_evidence import QuoteCostEvidence
from app.models.quote_job import QuoteJob
from app.models.quote_requirement_row import QuoteRequirementRow
from app.services.quote_history import parse_amount, project_details, text_or_none


REVIEW_DELTA_THRESHOLD = 0.2
MANUAL_CHANGE_THRESHOLD = 0.25
MAX_REQUIREMENT_ROWS = 2000

PROJECT_NAME_KEYS = (
    "project_name",
    "name",
    "item_name",
    "item",
    "project",
    "project_title",
    "material",
    "material_name",
    "施工项目",
    "项目名称",
    "工作内容",
    "清单名称",
    "材料名称",
    "分部分项工程",
    "名称",
)
QUANTITY_KEYS = ("quantity", "qty", "count", "工程量", "数量", "计量数量", "工程数量", "预估工程量")
UNIT_KEYS = ("unit", "measurement_unit", "计量单位", "单位")
UNIT_PRICE_KEYS = (
    "unit_price",
    "price",
    "综合单价",
    "综合单价(元)",
    "综合单价（元）",
    "单价",
    "单价(元)",
    "单价（元）",
    "报价单价",
    "报价单价(元)",
    "报价单价（元）",
    "预审参考单价",
    "AI估算",
    "AI估算单价",
    "AI核准单价",
    "AI核准单价(元)",
)
TOTAL_PRICE_KEYS = (
    "total_price",
    "amount",
    "subtotal",
    "合价",
    "合价(元)",
    "合价（元）",
    "总价",
    "总价(元)",
    "总价（元）",
    "合计",
    "合计(元)",
    "合计（元）",
    "小计",
    "金额",
    "金额(元)",
    "金额（元）",
    "项目合计",
    "项目合计(元)",
    "项目合计（元）",
)
SPEC_KEYS = ("spec", "specification", "feature", "features", "project_feature", "规格", "规格/特征", "项目特征", "特征描述")
NOTES_KEYS = ("notes", "remark", "remarks", "description", "craft", "工艺备注", "工艺与避坑备注", "备注", "说明", *SPEC_KEYS)
SOURCE_SHEET_KEYS = ("source_sheet", "sheet_name", "sheet", "来源Sheet", "来源表", "来源工作表")
RAW_ROW_INDEX_KEYS = ("raw_row_index", "source_row_index", "row_index", "原始行号", "来源行号", "行号")
REQUIREMENT_KEY_KEYS = (
    "requirement_row_key",
    "source_requirement_row_key",
    "requirement_key",
    "req_key",
    "row_key",
    "source_row_key",
    "确认行key",
    "确认清单key",
    "需求行key",
)

PREVIEW_CANONICAL_ALIASES = {
    "project_name": PROJECT_NAME_KEYS,
    "quantity": QUANTITY_KEYS,
    "unit": UNIT_KEYS,
    "unit_price": UNIT_PRICE_KEYS,
    "total_price": TOTAL_PRICE_KEYS,
    "spec": SPEC_KEYS,
    "notes": NOTES_KEYS,
    "source_sheet": SOURCE_SHEET_KEYS,
    "raw_row_index": RAW_ROW_INDEX_KEYS,
    "requirement_row_key": REQUIREMENT_KEY_KEYS,
}


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(raw_value: str | None, fallback: Any = None) -> Any:
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except Exception:
        return fallback


def parse_requirement_rows_payload(raw_value: str | None) -> list[dict[str, Any]]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except Exception as exc:
        raise ValueError("requirement_rows_json must be valid JSON") from exc
    if not isinstance(value, list):
        raise ValueError("requirement_rows_json must be a JSON array")
    rows = [row for row in value if isinstance(row, dict)]
    return rows[:MAX_REQUIREMENT_ROWS]


def save_quote_requirement_rows(db: Session, *, quote_job_id: str, rows: list[dict[str, Any]]) -> list[QuoteRequirementRow]:
    if not rows:
        return []
    db.query(QuoteRequirementRow).filter(QuoteRequirementRow.quote_job_id == quote_job_id).delete(synchronize_session=False)
    saved_rows: list[QuoteRequirementRow] = []
    for index, row in enumerate(rows, start=1):
        saved = QuoteRequirementRow(
            quote_job_id=quote_job_id,
            requirement_row_key=text_or_none(row.get("requirement_row_key"), 128),
            source_sheet=text_or_none(row.get("source_sheet"), 255),
            raw_row_index=_int_or_none(row.get("raw_row_index")),
            item_name=text_or_none(row.get("item_name"), 255),
            spec=text_or_none(row.get("spec")),
            quantity=parse_amount(row.get("quantity")),
            unit=text_or_none(row.get("unit"), 64),
            remark=text_or_none(row.get("remark")),
            raw_text=text_or_none(row.get("raw_text")),
            raw_cells_json=json_dumps(row.get("raw_cells") or []),
            row_json=json_dumps(row),
            sort_order=index,
        )
        db.add(saved)
        saved_rows.append(saved)
    db.flush()
    return saved_rows


def quote_requirement_rows(db: Session, quote_job_id: str) -> list[QuoteRequirementRow]:
    return (
        db.query(QuoteRequirementRow)
        .filter(QuoteRequirementRow.quote_job_id == quote_job_id)
        .order_by(QuoteRequirementRow.sort_order.asc(), QuoteRequirementRow.id.asc())
        .all()
    )


def requirement_rows_as_source_rows(requirement_rows: list[QuoteRequirementRow]) -> list[dict[str, Any]]:
    return [
        {
            "requirement_row_key": _requirement_row_key(row, index),
            "source_sheet": row.source_sheet,
            "raw_row_index": row.raw_row_index,
            "item_name": row.item_name,
            "spec": row.spec,
            "quantity": row.quantity,
            "unit": row.unit,
            "remark": row.remark,
            "raw_text": row.raw_text,
        }
        for index, row in enumerate(requirement_rows)
    ]


def requirement_row_key_for(row: QuoteRequirementRow, index: int) -> str:
    return _requirement_row_key(row, index)


def missing_requirement_rows_for_preview(
    requirement_rows: list[QuoteRequirementRow],
    preview_rows: list[dict[str, Any]],
) -> list[QuoteRequirementRow]:
    if not requirement_rows:
        return []
    matches = _match_requirement_to_preview(requirement_rows, preview_rows)
    matched_requirement_ids = {item["requirement_index"] for item in matches if item["status"] == "matched"}
    return [row for index, row in enumerate(requirement_rows) if index not in matched_requirement_ids]


def merge_requirement_preview_rows(
    requirement_rows: list[QuoteRequirementRow],
    preview_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    preview_rows = [_normalize_preview_row_aliases(row) for row in preview_rows if isinstance(row, dict)]
    if not requirement_rows:
        return list(preview_rows), {
            "required_count": 0,
            "preview_input_count": len(preview_rows),
            "ai_returned_count": len(preview_rows),
            "placeholder_count": 0,
            "ignored_extra_count": 0,
        }

    matches = _match_requirement_to_preview(requirement_rows, preview_rows)
    match_by_requirement = {item["requirement_index"]: item for item in matches if item["status"] == "matched"}
    matched_preview_ids = {item["preview_index"] for item in matches if item["status"] == "matched"}
    merged_rows: list[dict[str, Any]] = []
    placeholder_count = 0
    ai_returned_count = 0

    for index, requirement in enumerate(requirement_rows):
        match = match_by_requirement.get(index)
        if match:
            row = dict(preview_rows[match["preview_index"]])
            _apply_requirement_identity(row, requirement, index)
            if _is_requirement_placeholder(row):
                placeholder_count += 1
            else:
                ai_returned_count += 1
            merged_rows.append(row)
            continue

        merged_rows.append(_build_requirement_placeholder_row(requirement, index))
        placeholder_count += 1

    return merged_rows, {
        "required_count": len(requirement_rows),
        "preview_input_count": len(preview_rows),
        "ai_returned_count": ai_returned_count,
        "placeholder_count": placeholder_count,
        "ignored_extra_count": max(0, len(preview_rows) - len(matched_preview_ids)),
    }


def build_requirement_rows_quote_message(base_message: str, requirement_rows: list[QuoteRequirementRow]) -> str:
    if not requirement_rows:
        return base_message or ""

    lines = []
    for index, row in enumerate(requirement_rows, start=1):
        lines.append(
            " | ".join(
                [
                    f"REQ-{index:04d}",
                    f"requirement_row_key={_requirement_row_key(row, index - 1)}",
                    f"source_sheet={_clean_prompt_value(row.source_sheet)}",
                    f"raw_row_index={row.raw_row_index or ''}",
                    f"item_name={_clean_prompt_value(row.item_name)}",
                    f"spec={_clean_prompt_value(row.spec)}",
                    f"quantity={_clean_prompt_value(row.quantity)}",
                    f"unit={_clean_prompt_value(row.unit)}",
                    f"remark={_clean_prompt_value(row.remark)}",
                ]
            )
        )

    instruction = (
        "\n\n【确认清单逐行报价要求】\n"
        f"本次人工确认清单共 {len(requirement_rows)} 行。请严格按下面每一行逐行生成 project_details，禁止合并、概括、抽样或省略。\n"
        "project_details 的条数必须与确认清单行数一致；每一条都必须保留 requirement_row_key、source_sheet、raw_row_index、project_name、quantity、unit、unit_price、total_price、notes。\n"
        "如果某行成本库无底价参考，仍必须返回该行，并在 notes 中说明“无底价参考，需人工复核”；不能因为无底价而省略。\n"
        "如果某行暂时无法报价，仍必须返回该行，unit_price/total_price 可填 0，并在 notes 中说明原因，等待人工复核。\n"
        "返回时不要新增没有来源于确认清单的报价行。\n"
        "【确认清单结构化明细】\n"
        + "\n".join(lines)
    )
    return f"{(base_message or '').strip()}{instruction}".strip()


def attach_requirement_integrity_summary(
    payload: Any,
    requirement_rows: list[QuoteRequirementRow],
) -> Any:
    if not requirement_rows or not isinstance(payload, dict):
        return payload
    preview_rows = project_details(payload)
    matches = _match_requirement_to_preview(requirement_rows, preview_rows)
    matched_requirement_ids = {item["requirement_index"] for item in matches if item["status"] == "matched"}
    matched_preview_ids = {item["preview_index"] for item in matches if item["status"] == "matched"}
    missing_count = max(0, len(requirement_rows) - len(matched_requirement_ids))
    extra_count = max(0, len(preview_rows) - len(matched_preview_ids))
    placeholder_count = sum(1 for row in preview_rows if _is_requirement_placeholder(row))
    status = "incomplete" if missing_count else ("complete_with_placeholders" if placeholder_count else "complete")
    enriched = dict(payload)
    enriched["requirement_integrity"] = {
        "required": True,
        "status": status,
        "is_complete": missing_count == 0,
        "requirement_row_count": len(requirement_rows),
        "preview_row_count": len(preview_rows),
        "matched_count": len(matched_requirement_ids),
        "missing_count": missing_count,
        "extra_count": extra_count,
        "placeholder_count": placeholder_count,
        "message": (
            f"确认清单 {len(requirement_rows)} 行，AI 预审返回 {len(preview_rows)} 行，"
            f"已匹配 {len(matched_requirement_ids)} 行，未匹配 {missing_count} 行。"
        ),
    }
    return enriched


def build_quote_review_detail(db: Session, job: QuoteJob) -> dict[str, Any]:
    requirement_rows = quote_requirement_rows(db, job.job_id)
    preview_rows = project_details(json_loads(job.result_json, None))
    evidence_rows = (
        db.query(QuoteCostEvidence)
        .filter(QuoteCostEvidence.quote_job_id == job.job_id)
        .order_by(QuoteCostEvidence.item_index.asc(), QuoteCostEvidence.id.asc())
        .all()
    )
    evidence_by_index = {row.item_index: row for row in evidence_rows}
    matches = _match_requirement_to_preview(requirement_rows, preview_rows)
    matched_requirement_ids = {item["requirement_index"] for item in matches if item["status"] == "matched"}
    matched_preview_ids = {item["preview_index"] for item in matches if item["status"] == "matched"}
    requirement_reconciliation = _requirement_reconciliation_rows(requirement_rows, preview_rows, matches)
    preview_reconciliation = {item["preview_index"]: item for item in matches if item.get("preview_index") is not None}

    reviewed_preview_rows = []
    for index, row in enumerate(preview_rows):
        reviewed_preview_rows.append(
            build_preview_review_row(
                row,
                index=index,
                evidence=evidence_by_index.get(index),
                reconciliation=preview_reconciliation.get(index),
            )
        )

    extra_preview_rows = (
        [
            build_preview_review_row(row, index=index, evidence=evidence_by_index.get(index), reconciliation=None)
            for index, row in enumerate(preview_rows)
            if index not in matched_preview_ids
        ]
        if requirement_rows
        else []
    )
    missing_rows = [
        item for item in requirement_reconciliation if item["status"] in {"missing_from_preview", "possibly_merged"}
    ]
    warning_count = sum(1 for row in reviewed_preview_rows if row["risk"]["type"] == "warning")
    high_count = sum(1 for row in reviewed_preview_rows if row["risk"]["type"] == "danger")
    no_cost_reference_count = sum(1 for row in reviewed_preview_rows if not row["checks"]["has_cost_reference"]["passed"])
    fallback_count = sum(1 for row in reviewed_preview_rows if not row["checks"]["cost_fallback_not_used"]["passed"])
    ai_rewrite_count = sum(1 for row in reviewed_preview_rows if not row["checks"]["ai_rewrite_confirmed"]["passed"])
    ai_note_conflict_count = sum(1 for row in reviewed_preview_rows if not row["checks"]["ai_note_confirmed"]["passed"])
    placeholder_count = sum(1 for row in reviewed_preview_rows if row.get("requirement_placeholder"))
    integrity_status = (
        "complete"
        if not requirement_rows
        else ("incomplete" if missing_rows else ("complete_with_placeholders" if placeholder_count else "complete"))
    )

    return {
        "summary": {
            "integrity_status": integrity_status,
            "is_complete": not missing_rows,
            "message": (
                f"确认清单 {len(requirement_rows)} 行，AI 预审返回 {len(preview_rows)} 行，"
                f"已匹配 {len(matched_requirement_ids)} 行，未匹配 {len(missing_rows)} 行。"
            ),
            "requirement_row_count": len(requirement_rows),
            "preview_row_count": len(preview_rows),
            "matched_count": len(matched_requirement_ids),
            "missing_count": len(missing_rows),
            "extra_count": len(extra_preview_rows),
            "placeholder_count": placeholder_count,
            "no_cost_reference_count": no_cost_reference_count,
            "cost_fallback_count": fallback_count,
            "ai_rewrite_risk_count": ai_rewrite_count,
            "ai_note_conflict_count": ai_note_conflict_count,
            "high_risk_count": high_count,
            "review_required_count": warning_count + high_count + len(missing_rows) + len(extra_preview_rows),
        },
        "requirement_rows": [serialize_requirement_row(row) for row in requirement_rows],
        "preview_rows": reviewed_preview_rows,
        "missing_requirement_rows": missing_rows,
        "extra_preview_rows": extra_preview_rows,
        "reconciliation_rows": requirement_reconciliation,
    }


def build_preview_review_row(
    row: dict[str, Any],
    *,
    index: int,
    evidence: QuoteCostEvidence | None = None,
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_name = _row_text(row, *PROJECT_NAME_KEYS)
    quantity = parse_amount(_row_value(row, *QUANTITY_KEYS))
    unit = _row_text(row, *UNIT_KEYS)
    unit_price = parse_amount(_row_value(row, *UNIT_PRICE_KEYS))
    total_price = parse_amount(_row_value(row, *TOTAL_PRICE_KEYS))
    notes = _row_text(row, *NOTES_KEYS)
    requirement_row_key = _preview_requirement_key(row)
    source_sheet = _row_text(row, *SOURCE_SHEET_KEYS)
    raw_row_index = _int_or_none(_row_value(row, *RAW_ROW_INDEX_KEYS))
    requirement_placeholder = _is_requirement_placeholder(row)
    reference = row.get("cost_reference") or row.get("costReference") or {}
    if not isinstance(reference, dict):
        reference = {}

    checks = _preview_checks(
        project_name=project_name,
        unit_price=unit_price,
        total_price=total_price,
        notes=notes,
        reference=reference,
        evidence=evidence,
        reconciliation=reconciliation,
        requirement_placeholder=requirement_placeholder,
    )
    risk = _risk_from_checks(checks)
    return {
        "index": index,
        "line_no": index + 1,
        "project_name": project_name,
        "quantity": quantity,
        "unit": unit,
        "ai_unit_price": unit_price,
        "system_total_price": total_price,
        "notes": notes,
        "requirement_row_key": requirement_row_key,
        "source_sheet": source_sheet,
        "raw_row_index": raw_row_index,
        "requirement_placeholder": requirement_placeholder,
        "cost_reference": reference,
        "reconciliation": reconciliation or {"status": "extra_preview_row", "label": "未匹配确认行", "score": 0},
        "checks": checks,
        "risk": risk,
        "manual_modified": bool(evidence.manual_modified) if evidence else False,
        "final_unit_price": evidence.final_unit_price if evidence else None,
        "final_total_price": evidence.final_total_price if evidence else None,
    }


def serialize_requirement_row(row: QuoteRequirementRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "quote_job_id": row.quote_job_id,
        "requirement_row_key": row.requirement_row_key,
        "source_sheet": row.source_sheet,
        "raw_row_index": row.raw_row_index,
        "item_name": row.item_name,
        "spec": row.spec,
        "quantity": row.quantity,
        "unit": row.unit,
        "remark": row.remark,
        "raw_text": row.raw_text,
        "raw_cells": json_loads(row.raw_cells_json, []),
        "sort_order": row.sort_order,
    }


def _preview_checks(
    *,
    project_name: str,
    unit_price: float | None,
    total_price: float | None,
    notes: str,
    reference: dict[str, Any],
    evidence: QuoteCostEvidence | None,
    reconciliation: dict[str, Any] | None,
    requirement_placeholder: bool = False,
) -> dict[str, dict[str, Any]]:
    matched = bool(reference.get("matched"))
    fallback_applied = bool(reference.get("fallback_applied") or reference.get("fallbackApplied"))
    delta_rate = parse_amount(reference.get("price_delta_rate"))
    manual_large = _manual_change_large(evidence)
    matched_requirement = reconciliation is not None and reconciliation.get("status") == "matched"
    candidate_unconfirmed = bool(reference.get("requires_manual_cost_candidate_confirmation")) and not bool(
        reference.get("manual_cost_candidate_confirmed")
    )
    ai_rewrite_unconfirmed = bool(reference.get("requires_manual_ai_rewrite_confirmation")) and not bool(
        reference.get("manual_ai_rewrite_confirmed")
    )
    ai_note_unconfirmed = bool(reference.get("requires_manual_ai_note_confirmation")) and not bool(
        reference.get("manual_ai_note_confirmed")
    )
    return {
        "project_name_present": _check(bool(project_name), "施工项目不为空", "施工项目为空", severity="danger"),
        "ai_unit_price_positive": _check(
            unit_price is not None and unit_price > 0,
            "AI 单价大于 0",
            "AI 单价为空、0 或负数",
            severity="danger",
        ),
        "system_total_positive": _check(
            total_price is not None and total_price > 0,
            "系统合计大于 0",
            "系统合计为空、0 或负数",
            severity="danger",
        ),
        "notes_present": _check(bool(notes), "备注不为空", "备注为空", severity="warning"),
        "has_cost_reference": _check(matched, "已命中成本库参考", "无底价参考", severity="warning"),
        "cost_candidate_confirmed": _check(
            not candidate_unconfirmed,
            "成本候选已确认",
            "存在多条 active 成本候选，需人工确认采用哪条成本依据",
            severity="danger",
            skipped=not matched,
        ),
        "ai_rewrite_confirmed": _check(
            not ai_rewrite_unconfirmed,
            "AI 返回项目与原始成本依据已确认",
            "AI 返回项目与原始需求命中的成本依据不一致，需人工确认",
            severity="danger",
            skipped=not matched,
        ),
        "ai_note_confirmed": _check(
            not ai_note_unconfirmed,
            "AI 备注与成本依据已确认",
            "AI 备注与成本库依据不一致，需人工确认或修改备注",
            severity="danger",
            skipped=not matched,
        ),
        "cost_delta_in_range": _check(
            (not matched) or delta_rate is None or abs(delta_rate) < REVIEW_DELTA_THRESHOLD,
            "命中成本库时未明显偏离底价",
            "已命中成本库但偏离底价过大",
            severity="warning",
            skipped=not matched,
        ),
        "cost_fallback_not_used": _check(
            not fallback_applied,
            "未使用成本库兜底",
            "使用了成本库兜底",
            severity="warning",
        ),
        "manual_change_not_large": _check(
            not manual_large,
            "人工改动未超过阈值",
            "人工改动过大",
            severity="warning",
            skipped=evidence is None or not evidence.manual_modified,
        ),
        "matched_requirement_row": _check(
            matched_requirement,
            "已匹配确认清单行",
            "未匹配确认清单行",
            severity="warning",
            skipped=reconciliation is None,
        ),
    }


def _check(
    passed: bool,
    passed_label: str,
    failed_label: str,
    *,
    severity: str,
    skipped: bool = False,
) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "skipped": bool(skipped),
        "severity": severity,
        "label": passed_label if passed else failed_label,
    }


def _risk_from_checks(checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    failed = [item for item in checks.values() if not item["passed"] and not item.get("skipped")]
    if not failed:
        return {"label": "正常", "type": "success", "reasons": []}
    high = [item["label"] for item in failed if item["severity"] == "danger"]
    reasons = [item["label"] for item in failed]
    return {"label": "高风险" if high else "需复核", "type": "danger" if high else "warning", "reasons": reasons}


def _manual_change_large(evidence: QuoteCostEvidence | None) -> bool:
    if not evidence or not evidence.manual_modified:
        return False
    for before, after in (
        (evidence.ai_unit_price, evidence.final_unit_price),
        (evidence.ai_total_price, evidence.final_total_price),
    ):
        if before is None or after is None or before <= 0:
            continue
        if abs(after - before) / before >= MANUAL_CHANGE_THRESHOLD:
            return True
    return False


def _match_requirement_to_preview(
    requirement_rows: list[QuoteRequirementRow],
    preview_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for req_index, req in enumerate(requirement_rows):
        for preview_index, preview in enumerate(preview_rows):
            preview_key = _preview_requirement_key(preview)
            if preview_key and preview_key in _requirement_match_keys(req, req_index):
                candidates.append({"requirement_index": req_index, "preview_index": preview_index, "score": 2.0})
                continue
            score = _match_score(req, preview)
            if score >= 0.52:
                candidates.append({"requirement_index": req_index, "preview_index": preview_index, "score": score})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    used_requirements: set[int] = set()
    used_previews: set[int] = set()
    matches: list[dict[str, Any]] = []
    for item in candidates:
        if item["requirement_index"] in used_requirements or item["preview_index"] in used_previews:
            continue
        used_requirements.add(item["requirement_index"])
        used_previews.add(item["preview_index"])
        matches.append({**item, "status": "matched", "label": "已匹配", "score": round(item["score"], 3)})
    return matches


def _requirement_reconciliation_rows(
    requirement_rows: list[QuoteRequirementRow],
    preview_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not requirement_rows:
        return []
    by_requirement = {item["requirement_index"]: item for item in matches}
    by_preview = {item["preview_index"]: item for item in matches}
    rows: list[dict[str, Any]] = []
    for index, req in enumerate(requirement_rows):
        match = by_requirement.get(index)
        serialized = serialize_requirement_row(req)
        if match:
            preview = preview_rows[match["preview_index"]]
            rows.append(
                {
                    "status": "matched",
                    "label": "已匹配预审行",
                    "score": match["score"],
                    "requirement_index": index,
                    "preview_index": match["preview_index"],
                    "requirement_row": serialized,
                    "preview_row": _preview_identity(preview, match["preview_index"]),
                }
            )
            continue
        rows.append(
            {
                "status": "missing_from_preview",
                "label": "疑似未进入预审单",
                "score": 0,
                "requirement_index": index,
                "preview_index": None,
                "requirement_row": serialized,
                "preview_row": None,
            }
        )
    for preview_index, preview in enumerate(preview_rows):
        if preview_index in by_preview:
            continue
        rows.append(
            {
                "status": "extra_preview_row",
                "label": "预审行未匹配确认行",
                "score": 0,
                "requirement_index": None,
                "preview_index": preview_index,
                "requirement_row": None,
                "preview_row": _preview_identity(preview, preview_index),
            }
        )
    return rows


def _preview_identity(row: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "line_no": index + 1,
        "requirement_row_key": _preview_requirement_key(row),
        "source_sheet": _row_text(row, *SOURCE_SHEET_KEYS),
        "raw_row_index": _int_or_none(_row_value(row, *RAW_ROW_INDEX_KEYS)),
        "project_name": _row_text(row, *PROJECT_NAME_KEYS),
        "quantity": parse_amount(_row_value(row, *QUANTITY_KEYS)),
        "unit": _row_text(row, *UNIT_KEYS),
        "unit_price": parse_amount(_row_value(row, *UNIT_PRICE_KEYS)),
        "total_price": parse_amount(_row_value(row, *TOTAL_PRICE_KEYS)),
        "notes": _row_text(row, *NOTES_KEYS),
        "requirement_placeholder": _is_requirement_placeholder(row),
    }


def _is_requirement_placeholder(row: dict[str, Any]) -> bool:
    return bool(row.get("requirement_placeholder") or row.get("quote_source") == "requirement_placeholder")


def _apply_requirement_identity(row: dict[str, Any], requirement: QuoteRequirementRow, index: int) -> None:
    row["requirement_row_key"] = _row_text(row, *REQUIREMENT_KEY_KEYS) or _requirement_row_key(requirement, index)
    _fill_if_missing(row, "source_sheet", requirement.source_sheet)
    _fill_if_missing(row, "raw_row_index", requirement.raw_row_index)
    _fill_if_missing(row, "project_name", requirement.item_name)
    _fill_if_missing(row, "spec", requirement.spec)
    _fill_if_missing(row, "quantity", requirement.quantity)
    _fill_if_missing(row, "unit", requirement.unit)
    if not _row_text(row, *NOTES_KEYS):
        _fill_if_missing(row, "notes", requirement.remark or requirement.raw_text)


def _fill_if_missing(row: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    if row.get(key) in (None, ""):
        row[key] = value


def _build_requirement_placeholder_row(requirement: QuoteRequirementRow, index: int) -> dict[str, Any]:
    project_name = text_or_none(requirement.item_name, 255) or text_or_none(requirement.raw_text, 255)
    notes = "AI 未返回该确认行，系统已保留占位，需人工补价。"
    if requirement.remark:
        notes = f"{notes} {requirement.remark}"
    elif requirement.spec:
        notes = f"{notes} 规格/特征：{requirement.spec}"
    return {
        "requirement_row_key": _requirement_row_key(requirement, index),
        "source_sheet": requirement.source_sheet,
        "raw_row_index": requirement.raw_row_index,
        "project_name": project_name or f"确认清单第 {index + 1} 行",
        "spec": requirement.spec,
        "quantity": requirement.quantity,
        "unit": requirement.unit,
        "unit_price": 0,
        "total_price": 0,
        "notes": notes,
        "requirement_placeholder": True,
        "quote_source": "requirement_placeholder",
        "cost_reference": {
            "matched": False,
            "match_type": "missing_ai_preview",
            "reference_price": None,
            "message": "AI 未返回该确认行，需人工补价",
        },
    }


def _match_score(req: QuoteRequirementRow, preview: dict[str, Any]) -> float:
    req_name = _normalize_match_text(req.item_name)
    preview_name = _normalize_match_text(_row_text(preview, *PROJECT_NAME_KEYS))
    if not req_name or not preview_name:
        return 0.0
    name_score = SequenceMatcher(None, req_name, preview_name).ratio()
    if req_name in preview_name or preview_name in req_name:
        name_score = max(name_score, 0.82)
    req_spec = _normalize_match_text(req.spec)
    preview_notes = _normalize_match_text(_row_text(preview, *NOTES_KEYS))
    spec_score = SequenceMatcher(None, req_spec, preview_notes).ratio() if req_spec and preview_notes else 0.0
    req_quantity = req.quantity
    preview_quantity = parse_amount(_row_value(preview, *QUANTITY_KEYS))
    quantity_score = 0.0
    if req_quantity is not None and preview_quantity is not None:
        quantity_score = 1.0 if abs(req_quantity - preview_quantity) <= max(0.01, abs(req_quantity) * 0.02) else 0.0
    preview_unit = _row_text(preview, *UNIT_KEYS)
    unit_score = 1.0 if req.unit and preview_unit and req.unit == preview_unit else 0.0
    return name_score * 0.7 + spec_score * 0.12 + quantity_score * 0.12 + unit_score * 0.06


def _normalize_match_text(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _requirement_row_key(row: QuoteRequirementRow, index: int) -> str:
    order = _int_or_none(getattr(row, "sort_order", None)) or index + 1
    return text_or_none(row.requirement_row_key, 128) or f"REQ-{order:04d}"


def _requirement_match_keys(row: QuoteRequirementRow, index: int) -> set[str]:
    order = _int_or_none(getattr(row, "sort_order", None)) or index + 1
    keys = {_requirement_row_key(row, index), f"REQ-{order:04d}", f"REQ-{index + 1:04d}"}
    return {key for key in keys if key}


def _preview_requirement_key(row: dict[str, Any]) -> str:
    return _row_text(row, *REQUIREMENT_KEY_KEYS)


def _clean_prompt_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def _normalize_field_key(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[\s:：()（）\[\]【】/\\._\-\u3000]+", "", text)


def _normalize_preview_row_aliases(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for canonical_key, aliases in PREVIEW_CANONICAL_ALIASES.items():
        if normalized.get(canonical_key) not in (None, ""):
            continue
        value = _row_value(normalized, *aliases)
        if value not in (None, ""):
            normalized[canonical_key] = value
    return normalized


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    normalized_keys = {_normalize_field_key(key) for key in keys}
    normalized_keys.discard("")
    if not normalized_keys:
        return None
    for key, value in row.items():
        if value in (None, ""):
            continue
        if _normalize_field_key(key) in normalized_keys:
            return value
    return None


def _row_text(row: dict[str, Any], *keys: str) -> str:
    return text_or_none(_row_value(row, *keys)) or ""


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
