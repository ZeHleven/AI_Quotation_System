import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.quote_cost_evidence import QuoteCostEvidence
from app.models.quote_feedback import QuoteFeedback
from app.services.quote_history import json_loads, parse_amount, project_details, text_or_none


logger = logging.getLogger(__name__)

TOTAL_SOURCE_AI_QUOTE = "ai_quote"
TOTAL_SOURCE_COST_REFERENCE_FALLBACK = "cost_reference_fallback"
TOTAL_SOURCE_MANUAL_FINAL = "manual_final"
TOTAL_SOURCE_MIXED = "mixed"
TOTAL_SOURCE_LABELS = {
    TOTAL_SOURCE_AI_QUOTE: "AI报价计算",
    TOTAL_SOURCE_COST_REFERENCE_FALLBACK: "成本库兜底计算",
    TOTAL_SOURCE_MANUAL_FINAL: "人工确认价",
    TOTAL_SOURCE_MIXED: "混合来源",
}

AI_PRICE_SOURCE_LABELS = {
    "pre_quote_cost_adopted": "采纳前置成本库",
    "pre_quote_cost_deviated": "偏离前置成本库",
    "model_estimate": "无成本库参考，AI估算",
    "cost_reference_fallback": "成本库兜底",
    "manual_selected": "人工切换成本条目",
    "unknown": "来源不足",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_dt(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _load_json(raw_value: Optional[str]) -> Any:
    if not raw_value:
        return None
    loaded = json_loads(raw_value)
    return loaded if loaded is not None else raw_value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _round_money(value: Any) -> Optional[float]:
    amount = parse_amount(value)
    return round(amount, 2) if amount is not None else None


def _parse_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _row_project_name(row: dict[str, Any]) -> Optional[str]:
    return text_or_none(_row_value(row, "project_name", "item", "item_name", "name"), 255)


def _row_unit(row: dict[str, Any]) -> Optional[str]:
    return text_or_none(_row_value(row, "unit", "计量单位"), 64)


def _cost_reference(row: dict[str, Any]) -> dict[str, Any]:
    reference = row.get("cost_reference") or row.get("costReference") or {}
    return reference if isinstance(reference, dict) else {}


def _quote_explanation(row: dict[str, Any]) -> dict[str, Any]:
    explanation = row.get("quote_explanation") or row.get("quoteExplanation") or {}
    return explanation if isinstance(explanation, dict) else {}


def _ai_price_source_payload(
    reference: dict[str, Any],
    explanation: dict[str, Any],
    *,
    ai_unit_price: Any = None,
    reference_price: Any = None,
    fallback_applied: bool = False,
    cost_item_id: Optional[int] = None,
    cost_item_name: Optional[str] = None,
    unit: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    source = text_or_none(explanation.get("ai_price_source") or reference.get("ai_price_source"), 64)
    if not source and reference.get("match_type") == "manual_selected":
        source = "manual_selected"
    reason = text_or_none(explanation.get("ai_price_source_reason") or reference.get("ai_price_source_reason"))
    if source and source != "unknown":
        label = text_or_none(
            explanation.get("ai_price_source_label")
            or reference.get("ai_price_source_label")
            or AI_PRICE_SOURCE_LABELS.get(source),
            64,
        )
        return source, label, reason

    if fallback_applied or reference.get("fallback_applied"):
        source = "cost_reference_fallback"
        label = AI_PRICE_SOURCE_LABELS[source]
        reason = reason or "AI 原始单价为空或为 0，系统使用成本库参考价兜底生成报价。"
        return source, label, reason

    matched = bool(reference.get("matched")) or cost_item_id is not None
    ai_price = _round_money(ai_unit_price if ai_unit_price is not None else reference.get("ai_unit_price"))
    ref_price = _round_money(reference_price if reference_price is not None else reference.get("reference_price"))
    if matched and ai_price is not None and ref_price not in (None, 0):
        item_text = f"成本库 #{cost_item_id}" if cost_item_id else "命中的成本库条目"
        if cost_item_name:
            item_text += f" “{cost_item_name}”"
        unit_text = unit or reference.get("unit") or "-"
        if ai_price == ref_price:
            source = "pre_quote_cost_adopted"
            label = AI_PRICE_SOURCE_LABELS[source]
            reason = reason or f"FastAPI 已在报价请求进入 AI 前传入 {item_text} 的参考价 {ref_price:.2f} 元/{unit_text}；AI 返回单价与该参考价一致。"
            return source, label, reason
        source = "pre_quote_cost_deviated"
        label = AI_PRICE_SOURCE_LABELS[source]
        reason = reason or f"FastAPI 已在报价请求进入 AI 前传入 {item_text} 的参考价 {ref_price:.2f} 元/{unit_text}；AI 返回单价 {ai_price:.2f} 元，需人工复核偏离原因。"
        return source, label, reason

    if not matched:
        source = "model_estimate"
        label = AI_PRICE_SOURCE_LABELS[source]
        reason = reason or "本行未命中 cost_items.active 成本条目，AI 单价来自 N8N/Dify 工作流返回结果。"
        return source, label, reason

    label = text_or_none(
        explanation.get("ai_price_source_label")
        or reference.get("ai_price_source_label")
        or AI_PRICE_SOURCE_LABELS.get(source or ""),
        64,
    )
    return source, label, reason


def _source_cost_item(reference: dict[str, Any]) -> dict[str, Any]:
    source_item = reference.get("source_cost_item") or {}
    if not isinstance(source_item, dict):
        source_item = {}
    return {
        "id": _parse_int(source_item.get("id") or reference.get("cost_item_id")),
        "item_name": source_item.get("item_name") or reference.get("item_name"),
        "category": source_item.get("category") or reference.get("category"),
        "subcategory": source_item.get("subcategory") or reference.get("subcategory"),
        "unit": source_item.get("unit") or reference.get("unit"),
        "status": source_item.get("status"),
        "price_type": source_item.get("price_type") or reference.get("price_type"),
        "spec": source_item.get("spec") or reference.get("spec"),
        "notes": source_item.get("notes"),
        "price_breakdown": reference.get("price_breakdown"),
        "reference_source": source_item.get("reference_source") or reference.get("reference_source"),
        "source_type": source_item.get("source_type") or reference.get("source_type"),
        "enterprise_quota_version_id": source_item.get("enterprise_quota_version_id") or reference.get("enterprise_quota_version_id"),
        "enterprise_quota_version_code": source_item.get("enterprise_quota_version_code") or reference.get("enterprise_quota_version_code"),
        "enterprise_quota_item_id": source_item.get("enterprise_quota_item_id") or reference.get("enterprise_quota_item_id"),
        "quota_code": source_item.get("quota_code") or reference.get("quota_code"),
        "section_code": source_item.get("section_code") or reference.get("section_code"),
        "section_name": source_item.get("section_name") or reference.get("section_name"),
    }


def _normalized_row_value(row: dict[str, Any], *keys: str) -> Any:
    value = _row_value(row, *keys)
    amount = parse_amount(value)
    if amount is not None:
        return round(amount, 4)
    if isinstance(value, str):
        return value.strip()
    return value


def _manual_modified(ai_row: Optional[dict[str, Any]], final_row: Optional[dict[str, Any]]) -> bool:
    if not ai_row and not final_row:
        return False
    if not ai_row or not final_row:
        return True
    key_groups = (
        ("project_name", "item", "item_name", "name"),
        ("quantity", "qty", "count"),
        ("unit",),
        ("unit_price", "price"),
        ("total_price", "amount", "subtotal"),
        ("notes", "remark", "description"),
        ("spec", "specification"),
    )
    return any(_normalized_row_value(ai_row, *keys) != _normalized_row_value(final_row, *keys) for keys in key_groups)


def _adopted_cost_reference(final_row: Optional[dict[str, Any]], reference_price: Optional[float]) -> Optional[bool]:
    if not final_row or reference_price is None:
        return None
    final_unit_price = _round_money(_row_value(final_row, "unit_price", "price"))
    if final_unit_price is None:
        return None
    return final_unit_price == round(float(reference_price), 2)


def _reference_total(quantity: Optional[float], reference_price: Optional[float]) -> Optional[float]:
    if quantity is None or reference_price is None:
        return None
    return round(float(quantity) * float(reference_price), 2)


def _same_money(left: Any, right: Any) -> bool:
    left_amount = _round_money(left)
    right_amount = _round_money(right)
    return left_amount == right_amount


def _preview_line_total_source(row: dict[str, Any], reference: dict[str, Any]) -> str:
    if reference.get("fallback_applied") or row.get("cost_reference_fallback"):
        return TOTAL_SOURCE_COST_REFERENCE_FALLBACK
    return TOTAL_SOURCE_AI_QUOTE


def _final_line_total_source(
    evidence: QuoteCostEvidence,
    final_row: dict[str, Any],
) -> str:
    final_total = _round_money(_row_value(final_row, "total_price", "amount", "subtotal"))
    final_unit = _round_money(_row_value(final_row, "unit_price", "price"))
    if (
        final_total is not None
        and evidence.ai_total_price is not None
        and not _same_money(final_total, evidence.ai_total_price)
    ) or (
        final_unit is not None
        and evidence.ai_unit_price is not None
        and not _same_money(final_unit, evidence.ai_unit_price)
    ):
        return TOTAL_SOURCE_MANUAL_FINAL
    if evidence.fallback_applied:
        return TOTAL_SOURCE_COST_REFERENCE_FALLBACK
    return TOTAL_SOURCE_AI_QUOTE


def _quote_total_source(evidence_rows: list[QuoteCostEvidence]) -> Optional[str]:
    sources = {item.line_total_source for item in evidence_rows if item.line_total_source}
    if not sources:
        return None
    if TOTAL_SOURCE_MANUAL_FINAL in sources:
        return TOTAL_SOURCE_MANUAL_FINAL
    if len(sources) == 1:
        return next(iter(sources))
    return TOTAL_SOURCE_MIXED


def _total_source_label(source: Optional[str]) -> Optional[str]:
    return TOTAL_SOURCE_LABELS.get(source or "")


def _apply_quote_totals(evidence_rows: list[QuoteCostEvidence]) -> None:
    quote_total = round(sum(item.line_total_price or 0.0 for item in evidence_rows), 2) if evidence_rows else None
    reference_totals = [item.reference_total for item in evidence_rows if item.reference_total is not None]
    quote_reference_total = round(sum(reference_totals), 2) if reference_totals else None
    quote_source = _quote_total_source(evidence_rows)
    for evidence in evidence_rows:
        evidence.quote_total_price = quote_total
        evidence.quote_reference_total_price = quote_reference_total
        evidence.quote_total_source = quote_source


def _evidence_from_ai_row(feedback: QuoteFeedback, index: int, row: dict[str, Any]) -> QuoteCostEvidence:
    reference = _cost_reference(row)
    explanation = _quote_explanation(row)
    source_item = _source_cost_item(reference)
    quantity = _round_money(_row_value(row, "quantity", "qty", "count"))
    reference_price = _round_money(reference.get("reference_price"))
    ai_total_price = _round_money(_row_value(row, "total_price", "amount", "subtotal"))
    fallback_applied = bool(reference.get("fallback_applied") or row.get("cost_reference_fallback"))

    return QuoteCostEvidence(
        feedback_id=feedback.id,
        quote_id=feedback.quote_id,
        quote_job_id=feedback.quote_job_id,
        quote_history_id=feedback.quote_history_id,
        trace_id=feedback.trace_id,
        username=feedback.username,
        source=feedback.source or "preview",
        status=feedback.status or "pending_review",
        item_index=index,
        project_name=_row_project_name(row),
        quantity=quantity,
        unit=_row_unit(row),
        ai_unit_price=_round_money(_row_value(row, "unit_price", "price")),
        ai_total_price=ai_total_price,
        line_total_price=ai_total_price,
        line_total_source=_preview_line_total_source(row, reference),
        cost_item_id=source_item["id"],
        cost_item_name_snapshot=text_or_none(source_item.get("item_name"), 255),
        cost_item_category_snapshot=text_or_none(source_item.get("category"), 128),
        cost_item_subcategory_snapshot=text_or_none(source_item.get("subcategory"), 128),
        cost_item_unit_snapshot=text_or_none(source_item.get("unit"), 64),
        cost_item_status_snapshot=text_or_none(source_item.get("status"), 24),
        reference_price=reference_price,
        reference_total=_reference_total(quantity, reference_price),
        reference_price_source=text_or_none(reference.get("reference_price_source"), 64),
        reference_price_source_label=text_or_none(reference.get("reference_price_source_label"), 64),
        match_type=text_or_none(reference.get("match_type"), 64),
        match_type_label=text_or_none(reference.get("match_type_label"), 64),
        match_reason=text_or_none(reference.get("match_reason")),
        price_delta=_round_money(reference.get("price_delta")),
        price_delta_rate=parse_amount(reference.get("price_delta_rate")),
        fallback_applied=fallback_applied,
        ai_basis=text_or_none(explanation.get("ai_basis")),
        cost_context_basis=text_or_none(explanation.get("cost_context_basis")),
        comparison=text_or_none(explanation.get("comparison")),
        cost_item_url=text_or_none(explanation.get("cost_item_url") or reference.get("cost_item_url"), 255),
        cost_reference_json=_json_dumps(reference) if reference else None,
        quote_explanation_json=_json_dumps(explanation) if explanation else None,
        cost_item_snapshot_json=_json_dumps(source_item) if any(value is not None for value in source_item.values()) else None,
    )


def _apply_final_reference_override(evidence: QuoteCostEvidence, final_row: dict[str, Any]) -> None:
    reference = _cost_reference(final_row)
    if not reference.get("matched"):
        return

    source_item = _source_cost_item(reference)
    final_cost_item_id = source_item.get("id")
    manual_selected = reference.get("match_type") == "manual_selected"
    if not manual_selected and (not final_cost_item_id or final_cost_item_id == evidence.cost_item_id):
        return

    explanation = _quote_explanation(final_row)
    quantity = _round_money(_row_value(final_row, "quantity", "qty", "count"))
    if quantity is not None:
        evidence.quantity = quantity
    reference_price = _round_money(reference.get("reference_price"))
    final_unit_price = _round_money(_row_value(final_row, "unit_price", "price"))
    if final_unit_price is not None and reference_price not in (None, 0):
        price_delta = round(float(final_unit_price) - float(reference_price), 2)
        price_delta_rate = round(price_delta / float(reference_price), 4)
    else:
        price_delta = _round_money(reference.get("price_delta"))
        price_delta_rate = parse_amount(reference.get("price_delta_rate"))

    evidence.cost_item_id = final_cost_item_id
    evidence.cost_item_name_snapshot = text_or_none(source_item.get("item_name"), 255)
    evidence.cost_item_category_snapshot = text_or_none(source_item.get("category"), 128)
    evidence.cost_item_subcategory_snapshot = text_or_none(source_item.get("subcategory"), 128)
    evidence.cost_item_unit_snapshot = text_or_none(source_item.get("unit"), 64)
    evidence.cost_item_status_snapshot = text_or_none(source_item.get("status"), 24)
    evidence.reference_price = reference_price
    evidence.reference_total = _reference_total(evidence.quantity, reference_price)
    evidence.reference_price_source = text_or_none(reference.get("reference_price_source"), 64)
    evidence.reference_price_source_label = text_or_none(reference.get("reference_price_source_label"), 64)
    evidence.match_type = text_or_none(reference.get("match_type"), 64)
    evidence.match_type_label = text_or_none(reference.get("match_type_label"), 64)
    evidence.match_reason = text_or_none(reference.get("match_reason"))
    evidence.price_delta = price_delta
    evidence.price_delta_rate = price_delta_rate
    evidence.fallback_applied = bool(reference.get("fallback_applied") or final_row.get("cost_reference_fallback"))
    evidence.cost_context_basis = text_or_none(explanation.get("cost_context_basis")) or evidence.cost_context_basis
    evidence.comparison = text_or_none(explanation.get("comparison")) or evidence.comparison
    evidence.cost_item_url = text_or_none(explanation.get("cost_item_url") or reference.get("cost_item_url"), 255)
    evidence.cost_reference_json = _json_dumps(reference)
    evidence.quote_explanation_json = _json_dumps(explanation) if explanation else evidence.quote_explanation_json
    evidence.cost_item_snapshot_json = _json_dumps(source_item) if any(value is not None for value in source_item.values()) else None


def _apply_final_row(
    evidence: QuoteCostEvidence,
    final_row: Optional[dict[str, Any]],
    *,
    ai_row: Optional[dict[str, Any]] = None,
    quote_history_id: Optional[int],
) -> None:
    if final_row:
        _apply_final_reference_override(evidence, final_row)
        final_quantity = _round_money(_row_value(final_row, "quantity", "qty", "count"))
        if final_quantity is not None:
            evidence.quantity = final_quantity
        evidence.final_unit_price = _round_money(_row_value(final_row, "unit_price", "price"))
        evidence.final_total_price = _round_money(_row_value(final_row, "total_price", "amount", "subtotal"))
        fallback_ai_row = {
            "project_name": evidence.project_name,
            "quantity": evidence.quantity,
            "unit": evidence.unit,
            "unit_price": evidence.ai_unit_price,
            "total_price": evidence.ai_total_price,
        }
        evidence.manual_modified = _manual_modified(ai_row or fallback_ai_row, final_row)
        evidence.adopted_cost_reference = _adopted_cost_reference(final_row, evidence.reference_price)
        evidence.line_total_price = evidence.final_total_price if evidence.final_total_price is not None else evidence.ai_total_price
        evidence.line_total_source = _final_line_total_source(evidence, final_row)
    evidence.quote_history_id = quote_history_id
    evidence.status = "confirmed"
    evidence.confirmed_at = _utcnow()


def record_preview_cost_evidence(
    db: Session,
    *,
    feedback: QuoteFeedback,
    payload: Any,
    replace: bool = True,
) -> list[QuoteCostEvidence]:
    existing = (
        db.query(QuoteCostEvidence)
        .filter(QuoteCostEvidence.feedback_id == feedback.id)
        .order_by(QuoteCostEvidence.item_index.asc(), QuoteCostEvidence.id.asc())
        .all()
    )
    if existing and feedback.status in {"confirmed", "rejected"} and replace:
        return existing
    if existing and not replace:
        return existing
    if replace:
        db.query(QuoteCostEvidence).filter(QuoteCostEvidence.feedback_id == feedback.id).delete(synchronize_session=False)
        db.flush()

    rows = project_details(payload)
    evidence_rows: list[QuoteCostEvidence] = []
    for index, row in enumerate(rows):
        evidence = _evidence_from_ai_row(feedback, index, row)
        db.add(evidence)
        evidence_rows.append(evidence)
    _apply_quote_totals(evidence_rows)
    db.flush()
    return evidence_rows


def record_confirmed_cost_evidence(
    db: Session,
    *,
    feedback: QuoteFeedback,
    ai_payload: Any,
    final_payload: Any,
) -> list[QuoteCostEvidence]:
    evidence_rows = record_preview_cost_evidence(db, feedback=feedback, payload=ai_payload, replace=False)
    evidence_by_index = {item.item_index: item for item in evidence_rows}
    ai_rows = project_details(ai_payload)
    final_rows = project_details(final_payload)

    for index, final_row in enumerate(final_rows):
        evidence = evidence_by_index.get(index)
        if not evidence:
            evidence = QuoteCostEvidence(
                feedback_id=feedback.id,
                quote_id=feedback.quote_id,
                quote_job_id=feedback.quote_job_id,
                quote_history_id=feedback.quote_history_id,
                trace_id=feedback.trace_id,
                username=feedback.username,
                source=feedback.source or "confirm_push",
                status="confirmed",
                item_index=index,
                project_name=_row_project_name(final_row),
                quantity=_round_money(_row_value(final_row, "quantity", "qty", "count")),
                unit=_row_unit(final_row),
                manual_modified=True,
            )
            db.add(evidence)
            evidence_rows.append(evidence)
        ai_row = ai_rows[index] if index < len(ai_rows) else None
        _apply_final_row(evidence, final_row, ai_row=ai_row, quote_history_id=feedback.quote_history_id)

    for evidence in evidence_rows:
        if evidence.item_index >= len(final_rows):
            evidence.status = "confirmed"
            evidence.manual_modified = True
            evidence.line_total_price = 0.0
            evidence.line_total_source = TOTAL_SOURCE_MANUAL_FINAL
            evidence.quote_history_id = feedback.quote_history_id
            evidence.confirmed_at = _utcnow()

    _apply_quote_totals(evidence_rows)
    db.flush()
    return evidence_rows


def record_rejected_cost_evidence(
    db: Session,
    *,
    feedback: QuoteFeedback,
    ai_payload: Any = None,
) -> list[QuoteCostEvidence]:
    evidence_rows = record_preview_cost_evidence(db, feedback=feedback, payload=ai_payload, replace=False) if ai_payload else (
        db.query(QuoteCostEvidence)
        .filter(QuoteCostEvidence.feedback_id == feedback.id)
        .order_by(QuoteCostEvidence.item_index.asc(), QuoteCostEvidence.id.asc())
        .all()
    )
    rejected_at = _utcnow()
    for evidence in evidence_rows:
        evidence.status = "rejected"
        evidence.rejected_at = rejected_at
    _apply_quote_totals(evidence_rows)
    db.flush()
    return evidence_rows


def safe_record_preview_cost_evidence(db: Session, *, feedback: QuoteFeedback, payload: Any) -> None:
    try:
        record_preview_cost_evidence(db, feedback=feedback, payload=payload)
    except Exception:
        logger.exception("quote_cost_evidence_preview_record_failed", extra={"event": "quote_cost_evidence_preview_record_failed"})


def safe_record_confirmed_cost_evidence(
    db: Session,
    *,
    feedback: QuoteFeedback,
    ai_payload: Any,
    final_payload: Any,
) -> None:
    try:
        record_confirmed_cost_evidence(db, feedback=feedback, ai_payload=ai_payload, final_payload=final_payload)
    except Exception:
        logger.exception("quote_cost_evidence_confirm_record_failed", extra={"event": "quote_cost_evidence_confirm_record_failed"})


def safe_record_rejected_cost_evidence(db: Session, *, feedback: QuoteFeedback, ai_payload: Any = None) -> None:
    try:
        record_rejected_cost_evidence(db, feedback=feedback, ai_payload=ai_payload)
    except Exception:
        logger.exception("quote_cost_evidence_reject_record_failed", extra={"event": "quote_cost_evidence_reject_record_failed"})


def serialize_cost_evidence(item: QuoteCostEvidence) -> dict[str, Any]:
    cost_reference = _load_json(item.cost_reference_json)
    quote_explanation = _load_json(item.quote_explanation_json)
    if not isinstance(cost_reference, dict):
        cost_reference = {}
    if not isinstance(quote_explanation, dict):
        quote_explanation = {}
    ai_price_source, ai_price_source_label, ai_price_source_reason = _ai_price_source_payload(
        cost_reference,
        quote_explanation,
        ai_unit_price=item.ai_unit_price,
        reference_price=item.reference_price,
        fallback_applied=bool(item.fallback_applied),
        cost_item_id=item.cost_item_id,
        cost_item_name=item.cost_item_name_snapshot,
        unit=item.cost_item_unit_snapshot or item.unit,
    )
    return {
        "id": item.id,
        "feedback_id": item.feedback_id,
        "quote_id": item.quote_id,
        "quote_job_id": item.quote_job_id,
        "quote_history_id": item.quote_history_id,
        "trace_id": item.trace_id,
        "username": item.username,
        "source": item.source,
        "status": item.status,
        "item_index": item.item_index,
        "project_name": item.project_name,
        "quantity": item.quantity,
        "unit": item.unit,
        "ai_unit_price": item.ai_unit_price,
        "ai_total_price": item.ai_total_price,
        "final_unit_price": item.final_unit_price,
        "final_total_price": item.final_total_price,
        "line_total_price": item.line_total_price,
        "line_total_source": item.line_total_source,
        "line_total_source_label": _total_source_label(item.line_total_source),
        "quote_total_price": item.quote_total_price,
        "quote_total_source": item.quote_total_source,
        "quote_total_source_label": _total_source_label(item.quote_total_source),
        "quote_reference_total_price": item.quote_reference_total_price,
        "manual_modified": item.manual_modified,
        "adopted_cost_reference": item.adopted_cost_reference,
        "cost_item_id": item.cost_item_id,
        "cost_item_name": item.cost_item_name_snapshot,
        "cost_item_category": item.cost_item_category_snapshot,
        "cost_item_subcategory": item.cost_item_subcategory_snapshot,
        "cost_item_unit": item.cost_item_unit_snapshot,
        "cost_item_status": item.cost_item_status_snapshot,
        "reference_price": item.reference_price,
        "reference_total": item.reference_total,
        "reference_price_source": item.reference_price_source,
        "reference_price_source_label": item.reference_price_source_label,
        "match_type": item.match_type,
        "match_type_label": item.match_type_label,
        "match_reason": item.match_reason,
        "price_delta": item.price_delta,
        "price_delta_rate": item.price_delta_rate,
        "fallback_applied": item.fallback_applied,
        "ai_price_source": ai_price_source,
        "ai_price_source_label": ai_price_source_label,
        "ai_price_source_reason": ai_price_source_reason,
        "ai_basis": item.ai_basis,
        "cost_context_basis": item.cost_context_basis,
        "comparison": item.comparison,
        "cost_item_url": item.cost_item_url,
        "reference_source": cost_reference.get("reference_source"),
        "source_type": cost_reference.get("source_type"),
        "enterprise_quota_version_id": cost_reference.get("enterprise_quota_version_id"),
        "enterprise_quota_version_code": cost_reference.get("enterprise_quota_version_code"),
        "enterprise_quota_item_id": cost_reference.get("enterprise_quota_item_id"),
        "quota_code": cost_reference.get("quota_code"),
        "cost_reference": cost_reference,
        "quote_explanation": quote_explanation,
        "cost_item_snapshot": _load_json(item.cost_item_snapshot_json),
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
        "confirmed_at": _format_dt(item.confirmed_at),
        "rejected_at": _format_dt(item.rejected_at),
    }
