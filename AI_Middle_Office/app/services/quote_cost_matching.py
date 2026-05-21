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


logger = logging.getLogger(__name__)

TEXT_SPLITTER = re.compile(r"[\s\-_—–,，、。.:：;；/\\|+&()\[\]{}（）【】<>《》\"'“”‘’\u3000]+")
CONNECTOR_TEXT = re.compile(r"(以及|或者|或|和|及|与|同|并)")
GENERIC_NAME_TERMS = ("工程", "施工", "项目", "报价")
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


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", _clean_text(value)).lower()


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
    return _clean_text(_row_value(row, "item_name", "project_name", "name", "material", "material_name"))


def _row_spec(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "spec", "specification", "feature", "features", "project_feature"))


def _row_unit(row: dict[str, Any]) -> str:
    return _clean_text(_row_value(row, "unit", "measurement_unit"))


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


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _cost_item_reference(item: CostItem, *, match_type: str, ai_unit_price: float | None) -> dict[str, Any]:
    reference_price = _round_money(item.price)
    delta = None
    delta_rate = None
    if ai_unit_price is not None and reference_price not in (None, 0):
        delta = round(float(ai_unit_price) - float(reference_price), 2)
        delta_rate = round(delta / float(reference_price), 4)

    return {
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


def _token_match_score(row_name: str, item: CostItem) -> int:
    if _has_action_conflict(row_name, item):
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


def _find_cost_match(row: dict[str, Any], active_items: list[CostItem]) -> tuple[CostItem | None, str | None]:
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
            return exact_candidates[0], "exact_item_spec"

    fuzzy_candidates: list[tuple[int, CostItem]] = []
    for item in active_items:
        if not _unit_compatible(row_unit, item):
            continue
        if _has_action_conflict(row_name, item):
            continue
        score = _name_match_score(row_name, item.item_name)
        token_score = _token_match_score(row_name, item)
        score = max(score, token_score)
        if score:
            score += _context_bonus(row_name, row_spec, item)
            fuzzy_candidates.append((score + _unit_bonus(row_unit, item), item))
    if fuzzy_candidates:
        fuzzy_candidates.sort(key=lambda pair: (pair[0], pair[1].id or 0), reverse=True)
        return fuzzy_candidates[0][1], "fuzzy_item_name"

    return None, None


def _reference_summary(rows: list[dict[str, Any]], active_count: int) -> dict[str, Any]:
    matched = sum(1 for row in rows if (row.get("cost_reference") or {}).get("matched"))
    return {
        "enabled": True,
        "active_cost_item_count": active_count,
        "matched_count": matched,
        "unmatched_count": max(0, len(rows) - matched),
    }


def _active_cost_items(db: Session) -> list[CostItem]:
    return (
        db.query(CostItem)
        .filter(CostItem.status == COST_STATUS_ACTIVE)
        .order_by(CostItem.updated_at.desc(), CostItem.id.desc())
        .all()
    )


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


def enrich_quote_payload_with_cost_refs(db: Session, payload: Any) -> Any:
    if not settings.feature_cost_db:
        return payload

    active_items = _active_cost_items(db)
    enriched = copy.deepcopy(payload)
    target = extract_project_payload(enriched)

    if isinstance(target, dict) and isinstance(target.get("project_details"), list):
        rows = [copy.deepcopy(item) for item in target["project_details"] if isinstance(item, dict)]
        for row in rows:
            item, match_type = _find_cost_match(row, active_items)
            if item and match_type:
                row["cost_reference"] = _cost_item_reference(
                    item,
                    match_type=match_type,
                    ai_unit_price=parse_amount(row.get("unit_price")),
                )
            else:
                row["cost_reference"] = _no_match_reference(row)
        target["project_details"] = rows
        target["cost_reference_summary"] = _reference_summary(rows, len(active_items))
        return enriched if _contains_project_details_object(enriched) else target

    if isinstance(target, list):
        rows = [copy.deepcopy(item) for item in target if isinstance(item, dict)]
        for row in rows:
            item, match_type = _find_cost_match(row, active_items)
            row["cost_reference"] = (
                _cost_item_reference(item, match_type=match_type, ai_unit_price=parse_amount(row.get("unit_price")))
                if item and match_type
                else _no_match_reference(row)
            )
        return rows

    return enriched


def safe_enrich_quote_payload_with_cost_refs(db: Session, payload: Any) -> Any:
    try:
        return enrich_quote_payload_with_cost_refs(db, payload)
    except Exception:
        logger.exception("quote_cost_reference_enrichment_failed")
        return payload
