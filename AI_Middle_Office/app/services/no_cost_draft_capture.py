from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.cost_item import (
    CHANGE_TYPE_STATUS,
    COST_SOURCE_AI_SUGGESTED,
    COST_SOURCE_MANUAL,
    COST_STATUS_ACTIVE,
    COST_STATUS_ARCHIVED,
    COST_STATUS_DRAFT,
    PRICE_TYPE_COMBINED,
    CostItem,
    CostItemHistory,
)
from app.models.user import User
from app.services.cost_duplicate_guard import find_existing_duplicate_item, normalize_cost_text
from app.services.quote_history import parse_amount, project_details


logger = logging.getLogger(__name__)

NO_COST_NOTICE = "无成本库参考价，AI 估价，仅供参考，请人工确认价格依据"
NO_COST_DRAFT_CATEGORY = "待审核报价沉淀"
NO_COST_DRAFT_SUBCATEGORY = "无底价项目"
NO_COST_HISTORY_REASON = "BIZ-2m 无底价报价下发后自动生成待审核草稿"


@dataclass(frozen=True)
class NoCostDraftCandidate:
    line_no: int
    item_name: str
    spec: str | None
    unit: str
    unit_price: float
    total_price: float
    quantity: float | None
    ai_unit_price: float | None
    ai_total_price: float | None
    requirement_row_key: str | None
    source_sheet: str | None
    raw_row_index: int | None
    quote_source: str | None
    requirement_placeholder: bool
    draft_price_source: str
    manual_price_action: str | None
    final_price_source: str | None
    price_confirmation_label: str | None
    cost_item_source: str

    def to_item_payload(self, *, quote_job_id: str | None, quote_history_id: int | None) -> dict[str, Any]:
        notes = _candidate_notes(self, quote_job_id=quote_job_id, quote_history_id=quote_history_id)
        return {
            "category": NO_COST_DRAFT_CATEGORY,
            "subcategory": NO_COST_DRAFT_SUBCATEGORY,
            "item_name": self.item_name,
            "spec": self.spec,
            "unit": self.unit,
            "price": round(self.unit_price, 6),
            "price_type": PRICE_TYPE_COMBINED,
            "source": self.cost_item_source,
            "notes": notes,
        }


def analyze_no_cost_draft_candidates(final_payload: dict[str, Any]) -> dict[str, Any]:
    candidates: list[NoCostDraftCandidate] = []
    skipped: list[dict[str, Any]] = []
    for index, row in enumerate(project_details(final_payload), start=1):
        candidate, reason = _candidate_from_row(row, index)
        if candidate:
            candidates.append(candidate)
            continue
        skipped.append(
            {
                "line_no": index,
                "project_name": _row_text(row, "project_name", "item_name", "name", "item"),
                "reason": reason or "not_no_cost_candidate",
            }
        )
    return {
        "candidate_count": len(candidates),
        "candidates": [_candidate_summary(candidate) for candidate in candidates],
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def create_no_cost_draft_items(
    db: Session,
    user: User,
    final_payload: dict[str, Any],
    *,
    quote_job_id: str | None = None,
    quote_history_id: int | None = None,
) -> dict[str, Any]:
    rows = project_details(final_payload)
    candidates: list[NoCostDraftCandidate] = []
    invalid_skipped: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        candidate, reason = _candidate_from_row(row, index)
        if candidate:
            candidates.append(candidate)
        else:
            invalid_skipped.append(
                {
                    "line_no": index,
                    "project_name": _row_text(row, "project_name", "item_name", "name", "item"),
                    "reason": reason or "not_no_cost_candidate",
                }
            )

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    archived_duplicate_count = 0
    for candidate in candidates:
        duplicate = _find_duplicate(db, candidate)
        if duplicate and duplicate.status == COST_STATUS_ACTIVE:
            skipped.append(_skip_summary(candidate, "skipped_active_duplicate", duplicate.id))
            continue
        if duplicate and duplicate.status == COST_STATUS_DRAFT:
            skipped.append(_skip_summary(candidate, "skipped_existing_draft", duplicate.id))
            continue
        archived_duplicate_id = None
        if duplicate and duplicate.status == COST_STATUS_ARCHIVED:
            archived_duplicate_id = duplicate.id
            archived_duplicate_count += 1

        payload = candidate.to_item_payload(quote_job_id=quote_job_id, quote_history_id=quote_history_id)
        if archived_duplicate_id:
            payload["notes"] = "\n".join(
                [
                    payload["notes"] or "",
                    f"archived_duplicate_cost_item_id: {archived_duplicate_id}",
                ]
            ).strip()
        item = CostItem(
            **payload,
            status=COST_STATUS_DRAFT,
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        db.add(
            CostItemHistory(
                cost_item_id=item.id,
                old_status=None,
                new_status=COST_STATUS_DRAFT,
                change_type=CHANGE_TYPE_STATUS,
                changed_by=user.id,
                change_reason=NO_COST_HISTORY_REASON,
            )
        )
        created.append(
            {
                "cost_item_id": item.id,
                "line_no": candidate.line_no,
                "item_name": item.item_name,
                "unit": item.unit,
                "price": item.price,
                "status": item.status,
                "source": item.source,
                "manual_price_action": candidate.manual_price_action,
                "final_price_source": candidate.final_price_source,
                "archived_duplicate_cost_item_id": archived_duplicate_id,
            }
        )

    result = {
        "enabled": True,
        "row_count": len(rows),
        "candidate_count": len(candidates),
        "created_count": len(created),
        "skipped_count": len(skipped),
        "invalid_skipped_count": len(invalid_skipped),
        "archived_duplicate_count": archived_duplicate_count,
        "created_items": created,
        "skipped": skipped,
        "invalid_skipped": invalid_skipped,
    }
    logger.info("no_cost_draft_capture_result", extra={"summary": _compact_summary(result)})
    return result


def _candidate_from_row(row: dict[str, Any], line_no: int) -> tuple[NoCostDraftCandidate | None, str | None]:
    if _has_cost_reference(row):
        return None, "has_active_cost_reference"
    item_name = _row_text(row, "project_name", "item_name", "name", "item", max_length=255)
    if not item_name:
        return None, "missing_item_name"
    unit = _row_text(row, "unit", max_length=64)
    if not unit:
        return None, "missing_unit"
    total_price = _row_amount(row, "total_price", "amount", "subtotal", "confirmed_total_price", "final_total_price")
    if total_price is None or total_price <= 0:
        return None, "missing_positive_total_price"
    quantity = _row_amount(row, "quantity", "qty", "count")
    unit_price = _row_amount(row, "unit_price", "price", "confirmed_unit_price", "final_unit_price")
    draft_price_source = "confirmed_unit_price"
    if unit_price is None or unit_price <= 0:
        if quantity is not None and quantity > 0:
            unit_price = total_price / quantity
            draft_price_source = "confirmed_total_divided_by_quantity"
        else:
            unit_price = total_price
            draft_price_source = "confirmed_total_price_fallback"

    ai_unit_price = _row_amount(row, "ai_suggested_unit_price", "ai_unit_price", "original_unit_price")
    ai_total_price = _row_amount(row, "ai_suggested_total_price", "ai_total_price", "original_total_price")
    manual_price_action = _normalize_price_action(_row_text(row, "manual_price_action", "price_confirmation_action"))
    manual_price_source = _row_text(row, "manual_price_source")
    final_price_source = _normalize_price_source(_row_text(row, "final_price_source", "confirmed_price_source"))
    cost_item_source = _candidate_cost_item_source(
        row,
        unit_price=unit_price,
        ai_unit_price=ai_unit_price,
        manual_price_action=manual_price_action,
        manual_price_source=manual_price_source,
        final_price_source=final_price_source,
    )
    return (
        NoCostDraftCandidate(
            line_no=line_no,
            item_name=item_name,
            spec=_build_spec(row),
            unit=unit,
            unit_price=unit_price,
            total_price=total_price,
            quantity=quantity,
            ai_unit_price=ai_unit_price if ai_unit_price is not None else unit_price,
            ai_total_price=ai_total_price if ai_total_price is not None else total_price,
            requirement_row_key=_row_text(row, "requirement_row_key"),
            source_sheet=_row_text(row, "source_sheet", "sheet_name"),
            raw_row_index=_row_int(row, "raw_row_index", "source_row_index", "row_index"),
            quote_source=_row_text(row, "quote_source"),
            requirement_placeholder=bool(row.get("requirement_placeholder") or row.get("quote_source") == "requirement_placeholder"),
            draft_price_source=draft_price_source,
            manual_price_action=manual_price_action,
            final_price_source=final_price_source,
            price_confirmation_label=_row_text(row, "price_confirmation_label"),
            cost_item_source=cost_item_source,
        ),
        None,
    )


def _normalize_price_action(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    aliases = {
        "manual_input": "manual_override",
        "manual_total_input": "manual_override",
        "manual": "manual_override",
        "accepted_ai": "accepted_ai_suggestion",
        "ai_suggestion_accepted": "accepted_ai_suggestion",
    }
    return aliases.get(normalized, normalized)


def _normalize_price_source(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    aliases = {
        "ai": "ai_suggested",
        "ai_quote": "ai_suggested",
        "manual_final": "manual",
        "cost_reference_fallback": "cost_reference",
    }
    return aliases.get(normalized, normalized)


def _candidate_cost_item_source(
    row: dict[str, Any],
    *,
    unit_price: float,
    ai_unit_price: float | None,
    manual_price_action: str | None,
    manual_price_source: str | None,
    final_price_source: str | None,
) -> str:
    if manual_price_action == "accepted_ai_suggestion":
        return COST_SOURCE_AI_SUGGESTED
    if manual_price_action in {"manual_override", "manual_existing"}:
        return COST_SOURCE_MANUAL
    if final_price_source == "manual":
        return COST_SOURCE_MANUAL
    if final_price_source == "ai_suggested":
        return COST_SOURCE_AI_SUGGESTED
    if manual_price_source in {"manual_input", "manual_total_input", "manual_existing"}:
        return COST_SOURCE_MANUAL
    if manual_price_source == "accepted_ai_suggestion":
        return COST_SOURCE_AI_SUGGESTED
    if row.get("manual_unit_price") is not None and ai_unit_price is not None:
        try:
            if abs(float(unit_price) - float(ai_unit_price)) >= 0.01:
                return COST_SOURCE_MANUAL
        except (TypeError, ValueError):
            pass
    return COST_SOURCE_AI_SUGGESTED


def _has_cost_reference(row: dict[str, Any]) -> bool:
    reference = row.get("cost_reference") or row.get("costReference") or {}
    if not isinstance(reference, dict):
        return False
    return bool(reference.get("matched"))


def _row_text(row: dict[str, Any], *keys: str, max_length: int | None = None) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        return text[:max_length] if max_length else text
    return None


def _row_amount(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = parse_amount(row.get(key))
        if value is not None:
            return float(value)
    return None


def _row_int(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _build_spec(row: dict[str, Any]) -> str | None:
    parts = []
    for key in ("spec", "project_spec", "feature", "features", "description", "remark", "notes"):
        text = _row_text(row, key)
        if text and text not in parts:
            parts.append(text)
    if not parts:
        return None
    return "；".join(parts)


def _candidate_notes(
    candidate: NoCostDraftCandidate,
    *,
    quote_job_id: str | None,
    quote_history_id: int | None,
) -> str:
    lines = [
        "[BIZ-2m 无底价报价沉淀]",
        f"quote_job_id: {quote_job_id or '-'}",
        f"quote_history_id: {quote_history_id or '-'}",
        f"line_no: {candidate.line_no}",
        f"requirement_row_key: {candidate.requirement_row_key or '-'}",
        f"source_sheet: {candidate.source_sheet or '-'}",
        f"raw_row_index: {candidate.raw_row_index if candidate.raw_row_index is not None else '-'}",
        f"quantity: {candidate.quantity if candidate.quantity is not None else '-'}",
        f"ai_unit_price: {candidate.ai_unit_price if candidate.ai_unit_price is not None else '-'}",
        f"ai_total_price: {candidate.ai_total_price if candidate.ai_total_price is not None else '-'}",
        f"confirmed_unit_price: {candidate.unit_price}",
        f"confirmed_total_price: {candidate.total_price}",
        f"draft_price_source: {candidate.draft_price_source}",
        f"manual_price_action: {candidate.manual_price_action or '-'}",
        f"final_price_source: {candidate.final_price_source or '-'}",
        f"price_confirmation_label: {candidate.price_confirmation_label or '-'}",
        f"cost_item_source: {candidate.cost_item_source}",
        f"quote_source: {candidate.quote_source or '-'}",
        f"requirement_placeholder: {str(candidate.requirement_placeholder).lower()}",
        f"notice: {NO_COST_NOTICE}",
    ]
    return "\n".join(lines)


def _find_duplicate(db: Session, candidate: NoCostDraftCandidate) -> CostItem | None:
    return find_existing_duplicate_item(
        db,
        {
            "item_name": candidate.item_name,
            "spec": candidate.spec,
            "unit": candidate.unit,
            "price": candidate.unit_price,
            "price_type": PRICE_TYPE_COMBINED,
        },
        statuses=(COST_STATUS_ACTIVE, COST_STATUS_DRAFT, COST_STATUS_ARCHIVED),
    )


def _key_text(value: Any) -> str:
    if value is None:
        return ""
    return normalize_cost_text(value)


def _candidate_summary(candidate: NoCostDraftCandidate) -> dict[str, Any]:
    return {
        "line_no": candidate.line_no,
        "item_name": candidate.item_name,
        "spec": candidate.spec,
        "unit": candidate.unit,
        "unit_price": candidate.unit_price,
        "total_price": candidate.total_price,
        "draft_price_source": candidate.draft_price_source,
        "manual_price_action": candidate.manual_price_action,
        "final_price_source": candidate.final_price_source,
        "source": candidate.cost_item_source,
        "requirement_row_key": candidate.requirement_row_key,
        "requirement_placeholder": candidate.requirement_placeholder,
    }


def _skip_summary(candidate: NoCostDraftCandidate, reason: str, cost_item_id: int | None) -> dict[str, Any]:
    return {
        "line_no": candidate.line_no,
        "item_name": candidate.item_name,
        "unit": candidate.unit,
        "reason": reason,
        "cost_item_id": cost_item_id,
    }


def _compact_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_count": result.get("row_count"),
        "candidate_count": result.get("candidate_count"),
        "created_count": result.get("created_count"),
        "skipped_count": result.get("skipped_count"),
        "invalid_skipped_count": result.get("invalid_skipped_count"),
    }
