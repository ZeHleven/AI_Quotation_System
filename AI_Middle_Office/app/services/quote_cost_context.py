from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.quote_cost_matching import load_active_cost_items, match_quote_row_cost_reference
from app.services.quote_history import parse_amount


logger = logging.getLogger(__name__)

MAX_CONTEXT_ITEMS = 50
MAX_TEXT_ROWS = 80

QUOTE_ITEM_SPLITTER = re.compile(r"[\n\r；;、]+")
QUANTITY_UNIT_PATTERN = re.compile(
    r"(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>m2|m²|㎡|平方米|平方|m3|m³|立方米|立方|米|延米|kg|公斤|千克|t|吨|项|个|套|组|块|张|m(?!m))",
    re.IGNORECASE,
)
MILLIMETER_SPEC_PATTERN = re.compile(r"(?P<spec>\d+(?:\.\d+)?)\s*mm\b", re.IGNORECASE)


@dataclass(frozen=True)
class QuoteCostContext:
    text: str
    matched_count: int = 0
    unmatched_count: int = 0
    active_cost_item_count: int = 0
    references: tuple[dict[str, Any], ...] = ()
    source_rows: tuple[dict[str, Any], ...] = ()


EMPTY_COST_CONTEXT = QuoteCostContext(text="")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _row_name(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "project_name", "item_name", "name", "material", "material_name"))


def _row_spec(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "spec", "specification", "feature", "features", "project_feature"))


def _row_unit(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "unit", "measurement_unit"))


def _row_quantity(row: dict[str, Any]) -> float | None:
    quantity = parse_amount(_row_value(row, "quantity", "qty", "count", "工程量", "数量", "计量数量"))
    if quantity is None or quantity <= 0:
        return None
    return quantity


def _money(value: Any) -> str:
    amount = parse_amount(value)
    if amount is None:
        return ""
    return f"{amount:.2f}"


def _display_number(value: float | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _infer_row_from_text(segment: str) -> dict[str, Any] | None:
    text = _clean_text(segment)
    if len(text) < 2:
        return None

    row: dict[str, Any] = {"project_name": text}
    matches = list(QUANTITY_UNIT_PATTERN.finditer(text))
    if matches:
        match = matches[-1]
        row["quantity"] = match.group("quantity")
        row["unit"] = match.group("unit")
        name_text = f"{text[:match.start()]} {text[match.end():]}".strip(" \t,;；，、")
        if name_text:
            row["project_name"] = name_text
    spec_matches = list(MILLIMETER_SPEC_PATTERN.finditer(text))
    if spec_matches:
        row["spec"] = f"{spec_matches[-1].group('spec')}mm"
    return row


def _candidate_rows(query_text: str, source_rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [row for row in (source_rows or []) if isinstance(row, dict) and _row_name(row)]
    if rows:
        return rows[:MAX_TEXT_ROWS]

    inferred_rows: list[dict[str, Any]] = []
    for segment in QUOTE_ITEM_SPLITTER.split(query_text):
        row = _infer_row_from_text(segment)
        if row:
            inferred_rows.append(row)
        if len(inferred_rows) >= MAX_TEXT_ROWS:
            break
    return inferred_rows


def _reference_payload(row: dict[str, Any], reference: dict[str, Any], index: int) -> dict[str, Any]:
    quantity = _row_quantity(row)
    reference_price = parse_amount(reference.get("reference_price"))
    reference_total = round(quantity * reference_price, 2) if quantity and reference_price else None
    return {
        "index": index,
        "demand_item": _row_name(row),
        "spec": _row_spec(row),
        "quantity": quantity,
        "unit": _row_unit(row),
        "match_type": reference.get("match_type"),
        "cost_item_id": reference.get("cost_item_id"),
        "cost_item_name": reference.get("item_name"),
        "cost_item_spec": reference.get("spec"),
        "cost_unit": reference.get("unit"),
        "reference_unit_price": reference.get("reference_price"),
        "reference_price_source": reference.get("reference_price_source"),
        "reference_total": reference_total,
        "reference_source": reference.get("reference_source"),
        "source_type": reference.get("source_type"),
        "enterprise_quota_version_id": reference.get("enterprise_quota_version_id"),
        "enterprise_quota_version_code": reference.get("enterprise_quota_version_code"),
        "enterprise_quota_item_id": reference.get("enterprise_quota_item_id"),
        "quota_code": reference.get("quota_code"),
    }


def _format_context_text(references: list[dict[str, Any]], active_count: int, unmatched_count: int) -> str:
    if not references:
        return ""

    lines = [
        "[成本库底价强参考]",
        "以下条目来自 active cost reference。若需求项与成本库条目匹配，请优先采用 reference_unit_price 作为 AI 原始报价单价；如确需偏离，请在备注说明原因。",
        f"active 成本条目数: {active_count}; 命中参考数: {len(references)}; 未命中需求数: {unmatched_count}。",
    ]
    for ref in references:
        parts = [
            f"{ref['index']}. 需求项: {ref['demand_item']}",
            f"匹配类型: {ref['match_type']}",
            f"成本库项: {ref['cost_item_name']}",
            f"reference_unit_price: {_money(ref['reference_unit_price'])} 元/{ref['cost_unit'] or ref['unit']}",
        ]
        if ref.get("spec"):
            parts.insert(1, f"规格/特征: {ref['spec']}")
        if ref.get("quantity") is not None:
            parts.insert(1, f"数量: {_display_number(ref['quantity'])}")
        if ref.get("unit"):
            parts.insert(2, f"需求单位: {ref['unit']}")
        if ref.get("reference_total") is not None:
            parts.append(f"reference_total: {_money(ref['reference_total'])} 元")
        parts.append(f"cost_item_id: {ref['cost_item_id']}")
        if ref.get("reference_source"):
            parts.append(f"reference_source: {ref['reference_source']}")
        if ref.get("quota_code"):
            parts.append(f"quota_code: {ref['quota_code']}")
        lines.append("; ".join(parts))
    return "\n".join(lines)


def build_quote_cost_context(
    db: Session,
    query_text: str,
    *,
    source_rows: list[dict[str, Any]] | None = None,
    max_items: int = MAX_CONTEXT_ITEMS,
) -> QuoteCostContext:
    if not settings.feature_cost_db:
        return EMPTY_COST_CONTEXT

    active_items = load_active_cost_items(db)
    if not active_items:
        return QuoteCostContext(text="", active_cost_item_count=0)

    rows = _candidate_rows(query_text, source_rows)
    if not rows:
        return QuoteCostContext(text="", active_cost_item_count=len(active_items))

    references: list[dict[str, Any]] = []
    unmatched_count = 0
    for index, row in enumerate(rows, start=1):
        working_row, reference, _ = match_quote_row_cost_reference(row, active_items)
        if reference and reference.get("matched") and parse_amount(reference.get("reference_price")):
            references.append(_reference_payload(working_row, reference, index))
            if len(references) >= max_items:
                break
        else:
            unmatched_count += 1

    context_text = _format_context_text(references, len(active_items), unmatched_count)
    return QuoteCostContext(
        text=context_text,
        matched_count=len(references),
        unmatched_count=unmatched_count,
        active_cost_item_count=len(active_items),
        references=tuple(references),
        source_rows=tuple(dict(row) for row in rows),
    )


def append_quote_cost_context(
    db: Session,
    query_text: str,
    *,
    source_rows: list[dict[str, Any]] | None = None,
) -> tuple[str, QuoteCostContext]:
    context = build_quote_cost_context(db, query_text, source_rows=source_rows)
    if not context.text:
        return query_text, context
    return f"{query_text}\n\n{context.text}", context


def cost_context_references_as_source_rows(context: QuoteCostContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    references_by_index: dict[int, dict[str, Any]] = {}
    for ref in context.references:
        if not isinstance(ref, dict):
            continue
        index = parse_amount(ref.get("index"))
        if index is not None:
            references_by_index[int(index)] = ref

    if context.source_rows:
        for index, source_row in enumerate(context.source_rows, start=1):
            if not isinstance(source_row, dict):
                continue
            row = {
                "project_name": _row_name(source_row),
                "spec": _row_spec(source_row),
                "quantity": _row_quantity(source_row),
                "unit": _row_unit(source_row),
                "locked_cost_source": "pre_quote_context",
            }
            ref = references_by_index.get(index)
            if isinstance(ref, dict) and ref.get("cost_item_id"):
                row.update(
                    {
                        "locked_cost_item_id": ref.get("cost_item_id"),
                        "locked_cost_item_name": ref.get("cost_item_name"),
                        "locked_cost_item_spec": ref.get("cost_item_spec"),
                        "locked_cost_reference_price": ref.get("reference_unit_price"),
                        "locked_cost_match_type": ref.get("match_type"),
                        "locked_cost_reference_source": ref.get("reference_source"),
                        "locked_enterprise_quota_item_id": ref.get("enterprise_quota_item_id"),
                        "locked_enterprise_quota_version_id": ref.get("enterprise_quota_version_id"),
                        "locked_enterprise_quota_version_code": ref.get("enterprise_quota_version_code"),
                        "locked_quota_code": ref.get("quota_code"),
                    }
                )
            rows.append(row)
        return rows

    for ref in context.references:
        if not isinstance(ref, dict) or not ref.get("cost_item_id"):
            continue
        rows.append(
            {
                "project_name": ref.get("demand_item"),
                "spec": ref.get("spec"),
                "quantity": ref.get("quantity"),
                "unit": ref.get("unit"),
                "locked_cost_item_id": ref.get("cost_item_id"),
                "locked_cost_item_name": ref.get("cost_item_name"),
                "locked_cost_item_spec": ref.get("cost_item_spec"),
                "locked_cost_reference_price": ref.get("reference_unit_price"),
                "locked_cost_match_type": ref.get("match_type"),
                "locked_cost_reference_source": ref.get("reference_source"),
                "locked_enterprise_quota_item_id": ref.get("enterprise_quota_item_id"),
                "locked_enterprise_quota_version_id": ref.get("enterprise_quota_version_id"),
                "locked_enterprise_quota_version_code": ref.get("enterprise_quota_version_code"),
                "locked_quota_code": ref.get("quota_code"),
                "locked_cost_source": "pre_quote_context",
            }
        )
    return rows


def safe_append_quote_cost_context(
    db: Session | None,
    query_text: str,
    *,
    source_rows: list[dict[str, Any]] | None = None,
) -> tuple[str, QuoteCostContext]:
    if db is None:
        return query_text, EMPTY_COST_CONTEXT
    try:
        return append_quote_cost_context(db, query_text, source_rows=source_rows)
    except Exception:
        logger.exception("quote_cost_context_build_failed")
        return query_text, EMPTY_COST_CONTEXT


def build_cost_context_fallback_quote(context: QuoteCostContext, *, reason: str) -> dict[str, Any] | None:
    if not context.references or context.unmatched_count:
        return None

    project_details: list[dict[str, Any]] = []
    for ref in context.references:
        quantity = parse_amount(ref.get("quantity"))
        unit_price = parse_amount(ref.get("reference_unit_price"))
        if quantity is None or quantity <= 0 or unit_price is None or unit_price <= 0:
            return None
        total_price = parse_amount(ref.get("reference_total"))
        if total_price is None:
            total_price = round(quantity * unit_price, 2)

        project_details.append(
            {
                "project_name": ref.get("cost_item_name") or ref.get("demand_item") or "成本库参考项",
                "quantity": _display_number(quantity),
                "unit": ref.get("cost_unit") or ref.get("unit") or "",
                "unit_price": _round_money(unit_price),
                "total_price": _round_money(total_price),
                "notes": "N8N 返回空响应，已按 cost_items.active 成本库底价生成预审报价，需人工复核。",
                "cost_context_fallback": {
                    "applied": True,
                    "reason": reason,
                    "demand_item": ref.get("demand_item"),
                    "match_type": ref.get("match_type"),
                    "cost_item_id": ref.get("cost_item_id"),
                    "reference_source": ref.get("reference_source"),
                    "enterprise_quota_item_id": ref.get("enterprise_quota_item_id"),
                    "enterprise_quota_version_code": ref.get("enterprise_quota_version_code"),
                    "quota_code": ref.get("quota_code"),
                    "reference_price_source": ref.get("reference_price_source"),
                },
            }
        )

    if not project_details:
        return None

    return {
        "project_details": project_details,
        "customer_questions_answered": "N8N 返回空响应，本次预审由成本库 active 底价兜底生成。",
        "cost_context_fallback_summary": {
            "applied": True,
            "reason": reason,
            "matched_count": len(project_details),
            "active_cost_item_count": context.active_cost_item_count,
        },
    }
