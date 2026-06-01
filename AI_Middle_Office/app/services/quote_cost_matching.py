from __future__ import annotations

import copy
import logging
import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cost_item import COST_STATUS_ACTIVE, CostItem
from app.services.quote_history import extract_project_payload, parse_amount
from app.services.quote_omission_detection import detect_quote_omissions


logger = logging.getLogger(__name__)

TEXT_SPLITTER = re.compile(r"[\s\-_—–,，、。.:：;；/\\|+&()\[\]{}（）【】<>《》\"'“”‘’\u3000]+")
CONNECTOR_TEXT = re.compile(r"(以及|或者|或|和|及|与|同|并)")
GENERIC_NAME_TERMS = ("工程", "施工", "项目", "报价")
PLACEHOLDER_PROJECT_NAME_RE = re.compile(r"^(?:item|project|row|line)[_-]?\d+$", re.IGNORECASE)
ACTION_TERMS = (
    "拆除",
    "拆改",
    "铲除",
    "清拆",
    "安装",
    "铺贴",
    "找平",
    "防水",
    "吊顶",
    "开槽",
    "修补",
    "清运",
    "砌筑",
    "打磨",
    "刷漆",
    "涂刷",
    "布线",
    "改造",
    "封堵",
    "保护",
    "搬运",
    "制作",
    "修复",
)
MIN_TOKEN_MATCH_SCORE = 360

PRICE_SOURCE_LABELS = {
    "client_tax_excluded_price": "对甲税前综合单价",
    "subcontract_composite_price": "劳务发包综合单价",
    "crew_benchmark_price": "班组标底税前价",
    "price": "主参考价",
}

AI_PRICE_SOURCE_COST_ADOPTED = "pre_quote_cost_adopted"
AI_PRICE_SOURCE_COST_DEVIATED = "pre_quote_cost_deviated"
AI_PRICE_SOURCE_MODEL_ESTIMATE = "model_estimate"
AI_PRICE_SOURCE_COST_FALLBACK = "cost_reference_fallback"
AI_PRICE_SOURCE_UNKNOWN = "unknown"
AI_PRICE_SOURCE_LABELS = {
    AI_PRICE_SOURCE_COST_ADOPTED: "采纳前置成本库",
    AI_PRICE_SOURCE_COST_DEVIATED: "偏离前置成本库",
    AI_PRICE_SOURCE_MODEL_ESTIMATE: "无成本库参考，AI估算",
    AI_PRICE_SOURCE_COST_FALLBACK: "成本库兜底",
    AI_PRICE_SOURCE_UNKNOWN: "来源不足",
}

AI_NOTE_MISSING_COST_TERMS = (
    "未包含",
    "未找到",
    "找不到",
    "无对应",
    "无相关",
    "没有对应",
    "没有相关",
    "未检索到",
    "缺少相关",
    "数据集中未包含",
    "知识库未包含",
    "底层数据集中未包含",
)
AI_NOTE_UNAVAILABLE_TERMS = (
    "无法提供报价",
    "无法报价",
    "无法给出报价",
    "暂无法报价",
    "不能提供报价",
    "建议补充",
    "联系客服",
    "定制报价",
    "补充对应施工项",
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", _clean_text(value)).lower()


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _ai_note_conflicts_with_cost_basis(notes: Any) -> bool:
    text = _canonical_text(notes)
    if not text:
        return False
    return _has_any_term(text, AI_NOTE_MISSING_COST_TERMS) and _has_any_term(text, AI_NOTE_UNAVAILABLE_TERMS)


def _normalize_match_text(value: Any) -> str:
    text = _canonical_text(value)
    text = CONNECTOR_TEXT.sub("", text)
    return TEXT_SPLITTER.sub("", text)


def _searchable_name_key(value: Any) -> str:
    text = _normalize_match_text(value)
    for term in GENERIC_NAME_TERMS:
        text = text.replace(term, "")
    return text


def _ngrams(text: str, size: int) -> set[str]:
    if len(text) < size:
        return set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _action_terms(value: Any) -> set[str]:
    key = _searchable_name_key(value)
    return {term for term in ACTION_TERMS if term in key}


def _match_tokens(value: Any) -> set[str]:
    canonical = _canonical_text(value)
    spaced = CONNECTOR_TEXT.sub(" ", canonical)
    parts = [part for part in TEXT_SPLITTER.split(spaced) if part]
    tokens: set[str] = set()
    for part in parts:
        normalized = _searchable_name_key(part)
        if len(normalized) >= 2:
            tokens.add(normalized)

    key = _searchable_name_key(value)
    tokens.update(_action_terms(key))
    tokens.update(_ngrams(key, 2))
    if len(key) >= 5:
        tokens.update(_ngrams(key, 3))
    return {token for token in tokens if len(token) >= 2}


def _unit_family(value: Any) -> str | None:
    canonical = _canonical_text(value).replace("²", "2").replace("³", "3")
    key = _normalize_match_text(canonical)
    if not key:
        return None
    if key in {"m", "米", "延米", "延长米", "米长", "lm", "linear米"}:
        return "length"
    if key in {"m2", "㎡", "平米", "平方米", "平方"}:
        return "area"
    if key in {"m3", "m³", "立方米", "方"}:
        return "volume"
    if key in {"kg", "公斤", "千克", "t", "吨"}:
        return "weight"
    if key in {"项", "套", "个", "件", "只", "处", "组", "根", "块", "张", "台", "樘"}:
        return f"count:{key}"
    return None


def _unit_compatible(row_unit: str, item: CostItem) -> bool:
    row_key = _normalize_match_text(row_unit)
    item_key = _normalize_match_text(item.unit)
    if not row_key or not item_key:
        return True
    if row_key == item_key:
        return True
    row_family = _unit_family(row_unit)
    item_family = _unit_family(item.unit)
    if row_family and item_family:
        return row_family == item_family
    return False


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _row_name(row: dict[str, Any]) -> str:
    return _clean_text(
        _row_value(
            row,
            "project_name",
            "item_name",
            "item",
            "name",
            "project",
            "project_title",
            "material",
            "material_name",
        )
    )


def _is_placeholder_project_name(value: Any) -> bool:
    text = _clean_text(value)
    return bool(text and PLACEHOLDER_PROJECT_NAME_RE.fullmatch(text))


def _row_spec(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "spec", "specification", "feature", "features", "project_feature"))


def _row_unit(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "unit", "measurement_unit"))


def _row_quantity(row: dict[str, Any]) -> float | None:
    quantity = parse_amount(_row_value(row, "quantity", "qty", "count", "工程量", "数量", "计量数量"))
    if quantity is None or quantity <= 0:
        return None
    return quantity


def _normalize_quote_row_aliases(row: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(row)

    if not _clean_text(normalized.get("project_name")):
        project_name = _row_name(normalized)
        if project_name:
            normalized["project_name"] = project_name

    if not _clean_text(normalized.get("notes")):
        notes = _row_value(normalized, "remark", "remarks", "note", "description", "craft")
        if notes not in (None, ""):
            normalized["notes"] = _clean_text(notes)

    return normalized


def _apply_source_row_metadata(row: dict[str, Any], source_row: dict[str, Any] | None) -> None:
    if not source_row:
        return
    source_name = _row_name(source_row)
    current_name = _clean_text(row.get("project_name")) or _row_name(row)
    if source_name and (not current_name or _is_placeholder_project_name(current_name)):
        row["project_name"] = source_name
    key_pairs = (
        ("quantity", ("quantity", "qty", "count", "工程量", "数量", "计量数量")),
        ("unit", ("unit", "measurement_unit", "单位", "计量单位")),
        ("spec", ("spec", "specification", "feature", "features", "project_feature", "规格", "特征")),
        ("notes", ("notes", "remark", "remarks", "备注", "说明")),
    )
    for target_key, aliases in key_pairs:
        current_value = row.get(target_key)
        value = _row_value(source_row, *aliases)
        if target_key == "quantity":
            current_quantity = parse_amount(current_value)
            source_quantity = parse_amount(value)
            if (current_quantity is None or current_quantity <= 0) and source_quantity and source_quantity > 0:
                row[target_key] = value
            continue
        if current_value not in (None, ""):
            continue
        if value not in (None, ""):
            row[target_key] = value


def _source_row_for_index(row: dict[str, Any], index: int, source_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not source_rows:
        return None
    if 0 <= index < len(source_rows):
        return source_rows[index]

    row_name_key = _searchable_name_key(_row_name(row))
    if not row_name_key:
        return None
    for source_row in source_rows:
        source_name = _clean_text(_row_value(source_row, "project_name", "item_name", "name"))
        source_name_key = _searchable_name_key(source_name)
        if source_name_key and (source_name_key == row_name_key or source_name_key in row_name_key or row_name_key in source_name_key):
            return source_row
    return None


def _source_row_cost_item_id(source_row: dict[str, Any] | None) -> int | None:
    if not source_row:
        return None
    for key in (
        "locked_cost_item_id",
        "source_cost_item_id",
        "matched_cost_item_id",
        "cost_item_id",
    ):
        value = parse_amount(source_row.get(key))
        if value is not None and value > 0:
            return int(value)
    return None


def _cost_item_by_id(active_items: list[CostItem], item_id: int | None) -> CostItem | None:
    if item_id is None:
        return None
    return next((item for item in active_items if item.id == item_id), None)


def _source_row_cost_match(
    source_row: dict[str, Any] | None,
    active_items: list[CostItem],
) -> tuple[CostItem | None, str | None, list[CostItem]]:
    if not source_row:
        return None, None, []
    locked_item = _cost_item_by_id(active_items, _source_row_cost_item_id(source_row))
    if locked_item:
        match_type = _clean_text(source_row.get("locked_cost_match_type")) or "source_row_locked"
        return locked_item, match_type, [locked_item]
    return _find_cost_match(source_row, active_items)


def _apply_source_priority_payload(
    reference: dict[str, Any],
    row: dict[str, Any],
    source_row: dict[str, Any] | None,
    source_item: CostItem,
    ai_item: CostItem | None,
    ai_match_type: str | None,
) -> None:
    if not source_row:
        return

    source_name = _row_name(source_row)
    source_spec = _row_spec(source_row)
    ai_name = _row_name(row)
    reference.update(
        {
            "cost_reference_source": "source_requirement",
            "source_requirement_project_name": source_name,
            "source_requirement_spec": source_spec,
            "source_requirement_unit": _row_unit(source_row),
            "source_requirement_quantity": _round_money(_row_quantity(source_row)),
            "ai_returned_project_name": ai_name,
            "ai_returned_spec": _row_spec(row),
            "ai_rewrite_risk": False,
            "requires_manual_ai_rewrite_confirmation": False,
            "manual_ai_rewrite_confirmed": False,
        }
    )

    if ai_item and ai_item.id != source_item.id:
        reference.update(
            {
                "ai_rewrite_risk": True,
                "requires_manual_ai_rewrite_confirmation": True,
                "ai_rewrite_reason": "AI 返回项目与原始需求命中的成本依据不一致，需人工确认采用原始成本依据或切换条目。",
                "ai_returned_cost_item_id": ai_item.id,
                "ai_returned_cost_item_name": ai_item.item_name,
                "ai_returned_cost_item_spec": ai_item.spec,
                "ai_returned_match_type": ai_match_type,
            }
        )


def _apply_source_priority_explanation(
    row: dict[str, Any],
    reference: dict[str, Any],
    source_row: dict[str, Any] | None,
    source_item: CostItem,
) -> None:
    if not source_row:
        return

    source_name = _row_name(source_row)
    source_unit = _row_unit(source_row)
    source_match_label = _match_type_label(reference.get("match_type"))
    reference["match_reason"] = (
        f"原始需求“{source_name or '-'}”优先命中成本库 active 条目“{source_item.item_name}”{source_match_label}，"
        f"需求单位“{source_unit or '-'}”与成本库单位“{source_item.unit or '-'}”兼容。"
    )
    if reference.get("requires_manual_cost_candidate_confirmation"):
        reference["match_reason"] = f"{reference['match_reason']} 存在多条 active 成本候选，需人工确认采用哪条成本依据。"
    if reference.get("ai_rewrite_risk"):
        ai_name = _clean_text(reference.get("ai_returned_project_name")) or "-"
        ai_item_name = _clean_text(reference.get("ai_returned_cost_item_name")) or "-"
        reference["match_reason"] = (
            f"{reference['match_reason']} AI 返回项目“{ai_name}”另命中“{ai_item_name}”，"
            "与原始需求成本依据不一致，需人工确认。"
        )

    explanation = row.get("quote_explanation") or {}
    reference_price = parse_amount(reference.get("reference_price")) or 0
    explanation["cost_context_basis"] = (
        f"报价请求进入 N8N/Dify 前，原始需求“{source_name or '-'}”已命中成本库 active 条目 #{source_item.id} "
        f"“{source_item.item_name}”，参考价 {reference_price:.2f} 元/{source_item.unit or '-'} 被作为优先成本依据。"
    )
    row["quote_explanation"] = explanation


def _suggested_note_for_cost_basis_conflict(item: CostItem, reference: dict[str, Any]) -> str:
    reference_price = parse_amount(reference.get("reference_price"))
    price_text = f"{reference_price:.2f}" if reference_price is not None else "-"
    return (
        f"已命中成本库参考 #{item.id} “{item.item_name}”，参考价 {price_text} 元/{item.unit or '-'}；"
        "AI 原始备注与成本依据不一致，请以成本库依据和人工确认价为准。"
    )


def _apply_ai_note_cost_basis_consistency(
    row: dict[str, Any],
    reference: dict[str, Any],
    item: CostItem,
) -> None:
    reference.setdefault("ai_note_cost_basis_conflict", False)
    reference.setdefault("requires_manual_ai_note_confirmation", False)
    reference.setdefault("manual_ai_note_confirmed", False)

    original_notes = _clean_text(row.get("notes") or row.get("remark") or row.get("description"))
    if not original_notes or not _ai_note_conflicts_with_cost_basis(original_notes):
        return

    suggested_notes = _suggested_note_for_cost_basis_conflict(item, reference)
    reason = "AI 原始备注表示未找到或无法报价，但本行已命中成本库 active 参考。"
    reference.update(
        {
            "ai_note_cost_basis_conflict": True,
            "requires_manual_ai_note_confirmation": True,
            "manual_ai_note_confirmed": False,
            "ai_original_notes": original_notes,
            "system_suggested_notes": suggested_notes,
            "ai_note_conflict_reason": reason,
        }
    )
    row["ai_original_notes"] = original_notes
    row["notes"] = suggested_notes

    explanation = row.get("quote_explanation") or {}
    explanation.update(
        {
            "ai_original_notes": original_notes,
            "system_suggested_notes": suggested_notes,
            "ai_note_warning": reason,
        }
    )
    comparison = _clean_text(explanation.get("comparison"))
    note_warning = "AI 原始备注与成本依据不一致，需人工确认备注处理。"
    explanation["comparison"] = f"{comparison} {note_warning}".strip() if comparison else note_warning
    row["quote_explanation"] = explanation


def _enrich_row_with_cost_reference(
    row: dict[str, Any],
    index: int,
    active_items: list[CostItem],
    source_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    source_row = _source_row_for_index(row, index, source_rows)
    _apply_source_row_metadata(row, source_row)

    source_item, source_match_type, source_candidate_items = _source_row_cost_match(source_row, active_items)
    ai_item, ai_match_type, ai_candidate_items = _find_cost_match(row, active_items)

    if source_item and source_match_type:
        reference = _cost_item_reference(
            source_item,
            match_type=source_match_type,
            ai_unit_price=parse_amount(row.get("unit_price")),
            candidate_items=source_candidate_items,
        )
        _apply_source_priority_payload(reference, row, source_row, source_item, ai_item, ai_match_type)
        _apply_cost_reference_fallback(row, reference)
        _attach_quote_explanation(row, source_item, reference)
        _apply_source_priority_explanation(row, reference, source_row, source_item)
        _apply_ai_note_cost_basis_consistency(row, reference, source_item)
        if reference.get("ai_rewrite_risk"):
            explanation = row.get("quote_explanation") or {}
            explanation["ai_rewrite_warning"] = reference.get("ai_rewrite_reason")
            explanation["comparison"] = (
                f"{explanation.get('comparison') or ''} AI 返回项目与原始需求成本依据不一致，需人工确认。"
            ).strip()
            row["quote_explanation"] = explanation
        row["cost_reference"] = reference
        return row

    if ai_item and ai_match_type:
        reference = _cost_item_reference(
            ai_item,
            match_type=ai_match_type,
            ai_unit_price=parse_amount(row.get("unit_price")),
            candidate_items=ai_candidate_items,
        )
        _apply_cost_reference_fallback(row, reference)
        _attach_quote_explanation(row, ai_item, reference)
        _apply_ai_note_cost_basis_consistency(row, reference, ai_item)
        row["cost_reference"] = reference
    else:
        row["cost_reference"] = _no_match_reference(row)
        _attach_no_match_quote_explanation(row)
    return row


def _price_source(item: CostItem) -> str:
    reference = round(float(item.price or 0.0), 6)
    for field_name in (
        "subcontract_composite_price",
        "crew_benchmark_price",
        "client_tax_excluded_price",
    ):
        value = getattr(item, field_name)
        if value is not None and round(float(value), 6) == reference:
            return field_name
    return "price"


def _price_source_label(source: Any) -> str:
    return PRICE_SOURCE_LABELS.get(_clean_text(source), _clean_text(source) or "主参考价")


def _ai_price_source_label(source: Any) -> str:
    return AI_PRICE_SOURCE_LABELS.get(_clean_text(source), AI_PRICE_SOURCE_LABELS[AI_PRICE_SOURCE_UNKNOWN])


def _ai_price_source_for_reference(
    item: CostItem,
    reference: dict[str, Any],
    *,
    ai_unit_price: float | None,
    reference_price: float | None,
) -> tuple[str, str]:
    if reference.get("fallback_applied"):
        source = AI_PRICE_SOURCE_COST_FALLBACK
        reason = (
            f"AI 原始单价为空或为 0，系统已使用成本库 #{item.id} “{item.item_name}”"
            f" 的参考价 {reference_price or 0:.2f} 元/{item.unit or '-'} 兜底生成报价。"
        )
        return source, reason
    if ai_unit_price is None or reference_price in (None, 0):
        source = AI_PRICE_SOURCE_UNKNOWN
        return source, "AI 单价或成本库参考价缺失，无法判断报价来源。"
    if round(float(ai_unit_price), 2) == round(float(reference_price), 2):
        source = AI_PRICE_SOURCE_COST_ADOPTED
        reason = (
            f"报价请求进入 AI 前，FastAPI 已传入成本库 #{item.id} “{item.item_name}”"
            f" 的参考价 {reference_price:.2f} 元/{item.unit or '-'}；AI 返回单价与该参考价一致。"
        )
        return source, reason
    source = AI_PRICE_SOURCE_COST_DEVIATED
    reason = (
        f"报价请求进入 AI 前，FastAPI 已传入成本库 #{item.id} “{item.item_name}”"
        f" 的参考价 {reference_price:.2f} 元/{item.unit or '-'}；AI 返回单价 {ai_unit_price:.2f} 元，"
        "与前置成本库参考价不一致，需人工复核偏离原因。"
    )
    return source, reason


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _cost_item_reference(
    item: CostItem,
    *,
    match_type: str,
    ai_unit_price: float | None,
    candidate_items: list[CostItem] | None = None,
) -> dict[str, Any]:
    reference_price = _round_money(item.price)
    delta = None
    delta_rate = None
    if ai_unit_price is not None and reference_price not in (None, 0):
        delta = round(float(ai_unit_price) - float(reference_price), 2)
        delta_rate = round(delta / float(reference_price), 4)

    reference = {
        "matched": True,
        "match_type": match_type,
        "cost_item_id": item.id,
        "item_name": item.item_name,
        "spec": item.spec,
        "unit": item.unit,
        "category": item.category,
        "subcategory": item.subcategory,
        "price_type": item.price_type,
        "reference_price": reference_price,
        "reference_price_source": _price_source(item),
        "ai_unit_price": _round_money(ai_unit_price),
        "price_delta": delta,
        "price_delta_rate": delta_rate,
        "client_tax_excluded_price": _round_money(item.client_tax_excluded_price),
        "client_labor_price": _round_money(item.client_labor_price),
        "client_main_material_price": _round_money(item.client_main_material_price),
        "client_auxiliary_material_price": _round_money(item.client_auxiliary_material_price),
        "client_direct_fee": _round_money(item.client_direct_fee),
        "client_management_profit": _round_money(item.client_management_profit),
        "subcontract_composite_price": _round_money(item.subcontract_composite_price),
        "subcontract_labor_price": _round_money(item.subcontract_labor_price),
        "subcontract_main_material_price": _round_money(item.subcontract_main_material_price),
        "subcontract_auxiliary_material_price": _round_money(item.subcontract_auxiliary_material_price),
        "crew_benchmark_price": _round_money(item.crew_benchmark_price),
    }
    reference.update(_cost_candidate_ambiguity_payload(item, candidate_items or []))
    return reference


def _no_match_reference(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "matched": False,
        "match_type": None,
        "reference_price": None,
        "ai_unit_price": _round_money(parse_amount(row.get("unit_price"))),
        "price_delta": None,
        "price_delta_rate": None,
        "message": "无底价参考",
    }


def _append_cost_fallback_note(row: dict[str, Any]) -> None:
    fallback_note = "由成本库参考价兜底生成，需人工确认。"
    existing = _clean_text(row.get("notes"))
    if fallback_note.rstrip("。") in existing:
        return
    row["notes"] = f"{existing}；{fallback_note}" if existing else fallback_note


def _apply_cost_reference_fallback(row: dict[str, Any], reference: dict[str, Any]) -> None:
    reference_price = parse_amount(reference.get("reference_price"))
    ai_unit_price = parse_amount(row.get("unit_price"))
    quantity = _row_quantity(row)
    if (
        not reference.get("matched")
        or row.get("requirement_placeholder")
        or row.get("quote_source") == "requirement_placeholder"
        or reference_price is None
        or reference_price <= 0
        or quantity is None
        or (ai_unit_price is not None and ai_unit_price > 0)
    ):
        return

    previous_total = parse_amount(row.get("total_price"))
    fallback_total = round(quantity * reference_price, 2)
    row["unit_price"] = _round_money(reference_price)
    row["total_price"] = fallback_total
    _append_cost_fallback_note(row)
    fallback_payload = {
        "applied": True,
        "reason": "ai_unit_price_empty_or_zero",
        "reference_price": _round_money(reference_price),
        "quantity": _round_money(quantity),
        "unit_price_before": _round_money(ai_unit_price),
        "total_price_before": _round_money(previous_total),
        "total_price_after": fallback_total,
    }
    row["cost_reference_fallback"] = fallback_payload
    reference.update(
        {
            "fallback_applied": True,
            "fallback_reason": fallback_payload["reason"],
            "ai_unit_price_before_fallback": fallback_payload["unit_price_before"],
            "total_price_before_fallback": fallback_payload["total_price_before"],
            "ai_unit_price": _round_money(reference_price),
            "price_delta": 0.0,
            "price_delta_rate": 0.0,
        }
    )


def _match_type_label(match_type: Any) -> str:
    return "精确匹配" if match_type == "exact_item_spec" else "名称匹配"


def _cost_item_url(item: CostItem) -> str:
    return f"/admin/cost-db?cost_item_id={item.id}"


def _cost_candidate_snapshot(item: CostItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "item_name": item.item_name,
        "spec": item.spec,
        "unit": item.unit,
        "category": item.category,
        "subcategory": item.subcategory,
        "price_type": item.price_type,
        "reference_price": _round_money(item.price),
        "reference_price_source": _price_source(item),
        "reference_price_source_label": _price_source_label(_price_source(item)),
        "evidence_url": _cost_item_url(item),
    }


def _unique_cost_candidates(items: list[CostItem]) -> list[CostItem]:
    seen: set[int] = set()
    unique: list[CostItem] = []
    for item in items:
        item_id = int(item.id or 0)
        if item_id in seen:
            continue
        seen.add(item_id)
        unique.append(item)
    return unique


def _cost_candidate_ambiguity_payload(selected: CostItem, candidates: list[CostItem]) -> dict[str, Any]:
    unique = _unique_cost_candidates([selected, *candidates])
    if len(unique) <= 1:
        return {
            "candidate_count": 1,
            "alternative_cost_items": [],
            "requires_manual_cost_candidate_confirmation": False,
            "manual_cost_candidate_confirmed": False,
        }
    return {
        "candidate_count": len(unique),
        "alternative_cost_items": [_cost_candidate_snapshot(item) for item in unique[:8]],
        "requires_manual_cost_candidate_confirmation": True,
        "manual_cost_candidate_confirmed": False,
        "ambiguity_reason": "存在多条 active 成本候选，需人工确认采用哪条成本依据。",
    }


def _price_breakdown(item: CostItem) -> dict[str, dict[str, Any]]:
    fields = (
        "client_tax_excluded_price",
        "client_labor_price",
        "client_main_material_price",
        "client_auxiliary_material_price",
        "client_direct_fee",
        "client_management_profit",
        "subcontract_composite_price",
        "subcontract_labor_price",
        "subcontract_main_material_price",
        "subcontract_auxiliary_material_price",
        "crew_benchmark_price",
        "price",
    )
    labels = {
        **PRICE_SOURCE_LABELS,
        "client_labor_price": "对甲人工费",
        "client_main_material_price": "对甲主材费",
        "client_auxiliary_material_price": "对甲辅材费",
        "client_direct_fee": "对甲直接费小计",
        "client_management_profit": "对甲管理费利润",
        "subcontract_labor_price": "劳务人工费",
        "subcontract_main_material_price": "劳务主材费",
        "subcontract_auxiliary_material_price": "劳务辅材费",
    }
    return {
        field: {
            "label": labels.get(field, field),
            "value": _round_money(getattr(item, field)),
        }
        for field in fields
    }


def _attach_quote_explanation(row: dict[str, Any], item: CostItem, reference: dict[str, Any]) -> None:
    row_name = _row_name(row)
    row_unit = _row_unit(row)
    quantity = _row_quantity(row)
    ai_unit_price = parse_amount(row.get("unit_price"))
    ai_total_price = parse_amount(row.get("total_price"))
    reference_price = parse_amount(reference.get("reference_price"))
    source = _clean_text(reference.get("reference_price_source"))
    source_label = _price_source_label(source)
    delta = parse_amount(reference.get("price_delta"))
    delta_rate = reference.get("price_delta_rate")
    ai_price_source, ai_price_source_reason = _ai_price_source_for_reference(
        item,
        reference,
        ai_unit_price=ai_unit_price,
        reference_price=reference_price,
    )

    reference["reference_price_source_label"] = source_label
    reference["ai_price_source"] = ai_price_source
    reference["ai_price_source_label"] = _ai_price_source_label(ai_price_source)
    reference["ai_price_source_reason"] = ai_price_source_reason
    reference["cost_item_url"] = _cost_item_url(item)
    reference["evidence_url"] = _cost_item_url(item)
    reference["evidence_api_url"] = f"/api/v1/admin/cost-items/{item.id}"
    reference["match_type_label"] = _match_type_label(reference.get("match_type"))
    reference["match_reason"] = (
        f"报价行“{row_name or '-'}”与成本库 active 条目“{item.item_name}”{reference['match_type_label']}，"
        f"报价单位“{row_unit or '-'}”与成本库单位“{item.unit or '-'}”兼容。"
    )
    if reference.get("requires_manual_cost_candidate_confirmation"):
        reference["match_reason"] = f"{reference['match_reason']} 存在多条 active 成本候选，需人工确认采用哪条成本依据。"
    reference["price_source_reason"] = (
        f"参考价取自成本库 #{item.id} 的“{source_label}”字段，当前值为 {reference_price or 0:.2f} 元/{item.unit or '-'}。"
    )
    reference["source_cost_item"] = {
        "id": item.id,
        "item_name": item.item_name,
        "spec": item.spec,
        "unit": item.unit,
        "category": item.category,
        "subcategory": item.subcategory,
        "price_type": item.price_type,
        "status": item.status,
        "notes": item.notes,
    }
    reference["price_breakdown"] = _price_breakdown(item)

    if reference_price is not None and ai_unit_price is not None:
        if round(float(ai_unit_price), 2) == round(float(reference_price), 2):
            comparison = "AI 单价与成本库参考价一致。"
        else:
            rate_text = ""
            if delta_rate is not None:
                rate_text = f"（{float(delta_rate) * 100:+.1f}%）"
            comparison = f"AI 单价相对成本库参考价偏差 {float(delta or 0):+.2f} 元{rate_text}，需人工复核。"
    else:
        comparison = "AI 单价或成本库参考价缺失，需人工复核。"

    row["quote_explanation"] = {
        "ai_unit_price": _round_money(ai_unit_price),
        "ai_total_price": _round_money(ai_total_price),
        "quantity": _round_money(quantity),
        "unit": row_unit,
        "ai_price_source": ai_price_source,
        "ai_price_source_label": _ai_price_source_label(ai_price_source),
        "ai_price_source_reason": ai_price_source_reason,
        "ai_basis": "AI 工作流原始返回的单价与备注；系统不会臆测模型内部推理，只展示可审计输入和结果。",
        "cost_context_basis": (
            f"报价请求进入 N8N/Dify 前，FastAPI 已把成本库 active 命中条目 #{item.id} "
            f"“{item.item_name}”及参考价 {reference_price or 0:.2f} 元/{item.unit or '-'} 作为强参考上下文传给 AI。"
        ),
        "comparison": comparison,
        "cost_item_url": _cost_item_url(item),
    }


def _attach_no_match_quote_explanation(row: dict[str, Any]) -> None:
    row_name = _row_name(row)
    row_unit = _row_unit(row)
    quantity = _row_quantity(row)
    ai_unit_price = parse_amount(row.get("unit_price"))
    ai_total_price = parse_amount(row.get("total_price"))
    source = AI_PRICE_SOURCE_MODEL_ESTIMATE
    reason = (
        f"报价行“{row_name or '-'}”未命中 cost_items.active 成本条目；"
        "本行 AI 单价来自 N8N/Dify 工作流返回结果，需人工确认是否补录成本库参考。"
    )
    row["quote_explanation"] = {
        "ai_unit_price": _round_money(ai_unit_price),
        "ai_total_price": _round_money(ai_total_price),
        "quantity": _round_money(quantity),
        "unit": row_unit,
        "ai_price_source": source,
        "ai_price_source_label": _ai_price_source_label(source),
        "ai_price_source_reason": reason,
        "ai_basis": "AI 工作流原始返回的单价与备注；系统未找到可引用的 active 成本库参考。",
        "cost_context_basis": "本行报价请求进入 N8N/Dify 前未命中 active 成本库条目。",
        "comparison": "无成本库参考价，需人工复核或补充成本库条目。",
    }


def _name_match_score(row_name: str, cost_name: str) -> int:
    row_key = _searchable_name_key(row_name)
    cost_key = _searchable_name_key(cost_name)
    if not row_key or not cost_key:
        return 0
    if row_key == cost_key:
        return 1000
    if len(row_key) < 3 or len(cost_key) < 3:
        return 0
    if cost_key in row_key:
        return 700 + len(cost_key)
    if row_key in cost_key:
        return 500 + len(row_key)
    return 0


def _has_action_conflict(row_name: str, item: CostItem) -> bool:
    row_actions = _action_terms(row_name)
    item_actions = _action_terms(item.item_name)
    return bool(row_actions and item_actions and row_actions.isdisjoint(item_actions))


def _has_exclusion_conflict(row_name: str, cost_name: str) -> bool:
    row_key = _searchable_name_key(row_name)
    cost_key = _searchable_name_key(cost_name)
    if len(row_key) < 3 or len(cost_key) < 5:
        return False
    return any(f"{prefix}{row_key}" in cost_key for prefix in ("不含", "不包含", "不包括", "不计", "不做"))


def _token_match_score(row_name: str, item: CostItem) -> int:
    if _has_action_conflict(row_name, item):
        return 0
    if _has_exclusion_conflict(row_name, item.item_name):
        return 0

    row_tokens = _match_tokens(row_name)
    item_tokens = _match_tokens(item.item_name)
    if not row_tokens or not item_tokens:
        return 0

    shared = row_tokens & item_tokens
    business_shared = {token for token in shared if _has_cjk(token)}
    if len(business_shared) < 2:
        return 0

    coverage = len(shared) / max(1, min(len(row_tokens), len(item_tokens)))
    jaccard = len(shared) / max(1, len(row_tokens | item_tokens))
    if coverage < 0.45 and len(shared) < 4:
        return 0

    score = int(260 + 360 * coverage + 160 * jaccard + min(90, len(shared) * 8))
    return score if score >= MIN_TOKEN_MATCH_SCORE else 0


def _context_bonus(row_name: str, row_spec: str, item: CostItem) -> int:
    bonus = 0
    row_key = _searchable_name_key(row_name)
    if row_key:
        category_key = _searchable_name_key(" ".join(filter(None, [item.category, item.subcategory])))
        if category_key and (_action_terms(row_key) & _action_terms(category_key)):
            bonus += 30

    row_spec_key = _searchable_name_key(row_spec)
    item_spec_key = _searchable_name_key(item.spec)
    if row_spec_key and item_spec_key:
        if row_spec_key == item_spec_key:
            bonus += 120
        elif row_spec_key in item_spec_key or item_spec_key in row_spec_key:
            bonus += 60

    return bonus


def _unit_bonus(row_unit: str, item: CostItem) -> int:
    if not row_unit:
        return 0
    if _normalize_match_text(row_unit) == _normalize_match_text(item.unit):
        return 70
    if _unit_family(row_unit) and _unit_family(row_unit) == _unit_family(item.unit):
        return 55
    return 0


def _find_cost_match(row: dict[str, Any], active_items: list[CostItem]) -> tuple[CostItem | None, str | None, list[CostItem]]:
    row_name = _row_name(row)
    row_spec = _row_spec(row)
    row_unit = _row_unit(row)
    row_name_key = _searchable_name_key(row_name)
    row_spec_key = _searchable_name_key(row_spec)

    if row_name_key and row_spec_key:
        exact_candidates = [
            item
            for item in active_items
            if _unit_compatible(row_unit, item)
            and _searchable_name_key(item.item_name) == row_name_key
            and _searchable_name_key(item.spec) == row_spec_key
        ]
        if exact_candidates:
            exact_candidates.sort(key=lambda item: (_unit_bonus(row_unit, item), item.id or 0), reverse=True)
            return exact_candidates[0], "exact_item_spec", exact_candidates

    fuzzy_candidates: list[tuple[int, CostItem]] = []
    for item in active_items:
        if not _unit_compatible(row_unit, item):
            continue
        if _has_action_conflict(row_name, item):
            continue
        if _has_exclusion_conflict(row_name, item.item_name):
            continue
        score = _name_match_score(row_name, item.item_name)
        token_score = _token_match_score(row_name, item)
        score = max(score, token_score)
        if score:
            score += _context_bonus(row_name, row_spec, item)
            fuzzy_candidates.append((score + _unit_bonus(row_unit, item), item))
    if fuzzy_candidates:
        fuzzy_candidates.sort(key=lambda pair: (pair[0], pair[1].id or 0), reverse=True)
        top_score, top_item = fuzzy_candidates[0]
        top_name_key = _searchable_name_key(top_item.item_name)
        ambiguous_candidates = [
            item
            for score, item in fuzzy_candidates
            if score == top_score
            or (_searchable_name_key(item.item_name) == top_name_key and score >= top_score - 80)
            or (_searchable_name_key(row_name) == _searchable_name_key(item.item_name))
        ]
        return top_item, "fuzzy_item_name", ambiguous_candidates

    return None, None, []


def _reference_summary(rows: list[dict[str, Any]], active_count: int) -> dict[str, Any]:
    matched = sum(1 for row in rows if (row.get("cost_reference") or {}).get("matched"))
    fallback_applied = sum(1 for row in rows if (row.get("cost_reference") or {}).get("fallback_applied"))
    ambiguous = sum(
        1
        for row in rows
        if (row.get("cost_reference") or {}).get("requires_manual_cost_candidate_confirmation")
    )
    ai_rewrite_risk = sum(
        1
        for row in rows
        if (row.get("cost_reference") or {}).get("requires_manual_ai_rewrite_confirmation")
    )
    ai_note_conflict = sum(
        1
        for row in rows
        if (row.get("cost_reference") or {}).get("requires_manual_ai_note_confirmation")
    )
    return {
        "enabled": True,
        "active_cost_item_count": active_count,
        "matched_count": matched,
        "unmatched_count": max(0, len(rows) - matched),
        "fallback_applied_count": fallback_applied,
        "ambiguous_candidate_count": ambiguous,
        "ai_rewrite_risk_count": ai_rewrite_risk,
        "ai_note_conflict_count": ai_note_conflict,
    }


def _active_cost_items(db: Session) -> list[CostItem]:
    return (
        db.query(CostItem)
        .filter(CostItem.status == COST_STATUS_ACTIVE)
        .order_by(CostItem.updated_at.desc(), CostItem.id.desc())
        .all()
    )


def load_active_cost_items(db: Session) -> list[CostItem]:
    return _active_cost_items(db)


def match_quote_row_cost_reference(
    row: dict[str, Any],
    active_items: list[CostItem],
    *,
    source_row: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, CostItem | None]:
    working_row = copy.deepcopy(row)
    _apply_source_row_metadata(working_row, source_row)
    item, match_type, candidate_items = _find_cost_match(working_row, active_items)
    if item and match_type:
        reference = _cost_item_reference(
            item,
            match_type=match_type,
            ai_unit_price=parse_amount(working_row.get("unit_price")),
            candidate_items=candidate_items,
        )
        return working_row, reference, item
    return working_row, None, None


def _contains_project_details_object(value: Any, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, dict):
        if isinstance(value.get("project_details"), list):
            return True
        for key in ("data", "result", "payload", "output", "answer", "message"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)) and _contains_project_details_object(nested, depth + 1):
                return True
    if isinstance(value, list):
        return any(isinstance(item, dict) for item in value)
    return False


def enrich_quote_payload_with_cost_refs(db: Session, payload: Any, *, source_rows: list[dict[str, Any]] | None = None) -> Any:
    if not settings.feature_cost_db:
        return payload

    active_items = _active_cost_items(db)
    enriched = copy.deepcopy(payload)
    target = extract_project_payload(enriched)
    source_rows = [row for row in (source_rows or []) if isinstance(row, dict)]

    if isinstance(target, dict) and isinstance(target.get("project_details"), list):
        rows = [_normalize_quote_row_aliases(item) for item in target["project_details"] if isinstance(item, dict)]
        for index, row in enumerate(rows):
            _enrich_row_with_cost_reference(row, index, active_items, source_rows)
        target["project_details"] = rows
        target["cost_reference_summary"] = _reference_summary(rows, len(active_items))
        target.update(detect_quote_omissions(rows, active_items))
        return enriched if _contains_project_details_object(enriched) else target

    if isinstance(target, list):
        rows = [_normalize_quote_row_aliases(item) for item in target if isinstance(item, dict)]
        for index, row in enumerate(rows):
            _enrich_row_with_cost_reference(row, index, active_items, source_rows)
        return rows

    return enriched


def safe_enrich_quote_payload_with_cost_refs(db: Session, payload: Any, *, source_rows: list[dict[str, Any]] | None = None) -> Any:
    try:
        return enrich_quote_payload_with_cost_refs(db, payload, source_rows=source_rows)
    except Exception:
        logger.exception("quote_cost_reference_enrichment_failed")
        return payload
