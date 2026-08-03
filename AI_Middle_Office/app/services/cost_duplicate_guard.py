from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from app.models.cost_item import COST_STATUS_ACTIVE, COST_STATUS_ARCHIVED, COST_STATUS_DRAFT, CostItem, PRICE_TYPE_COMBINED


DUPLICATE_CONFLICT_CODE = "COST_ACTIVE_DUPLICATE_CONFLICT"


def normalize_cost_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return re.sub(r"[\s\-_.,;:，。；：、/\\|+&()\[\]{}（）【】<>《》\"'“”‘’]+", "", text)


def item_snapshot(item: CostItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "category": item.category,
        "subcategory": item.subcategory,
        "item_name": item.item_name,
        "spec": item.spec,
        "unit": item.unit,
        "price": item.price,
        "price_type": item.price_type,
        "status": item.status,
        "source": item.source,
        "updated_at": item.updated_at.isoformat(timespec="seconds") if item.updated_at else None,
    }


def active_duplicate_conflicts_for_item(db: Session, item: CostItem) -> list[dict[str, Any]]:
    candidate = {
        "id": item.id,
        "item_name": item.item_name,
        "spec": item.spec,
        "unit": item.unit,
        "price": item.price,
        "price_type": item.price_type,
    }
    return active_duplicate_conflicts(db, candidate, exclude_item_id=item.id)


def active_duplicate_conflicts(
    db: Session,
    candidate: Mapping[str, Any] | Any,
    *,
    exclude_item_id: int | None = None,
) -> list[dict[str, Any]]:
    candidate_name = normalize_cost_text(_field(candidate, "item_name"))
    candidate_unit = normalize_cost_text(_field(candidate, "unit"))
    candidate_price_type = _field(candidate, "price_type") or PRICE_TYPE_COMBINED
    if not candidate_name or not candidate_unit:
        return []

    items = (
        db.query(CostItem)
        .filter(CostItem.status == COST_STATUS_ACTIVE)
        .order_by(CostItem.id.desc())
        .all()
    )
    conflicts: list[dict[str, Any]] = []
    for item in items:
        if exclude_item_id and item.id == exclude_item_id:
            continue
        if normalize_cost_text(item.item_name) != candidate_name:
            continue
        if normalize_cost_text(item.unit) != candidate_unit:
            continue
        if (item.price_type or PRICE_TYPE_COMBINED) != candidate_price_type:
            continue

        conflict_type = _conflict_type(candidate, item)
        if conflict_type:
            conflicts.append(
                {
                    "code": conflict_type,
                    "severity": "block",
                    "message": _conflict_message(conflict_type),
                    "existing_item": item_snapshot(item),
                }
            )
    return conflicts


def find_existing_duplicate_item(
    db: Session,
    candidate: Mapping[str, Any] | Any,
    *,
    statuses: Iterable[str] = (COST_STATUS_ACTIVE, COST_STATUS_DRAFT, COST_STATUS_ARCHIVED),
) -> CostItem | None:
    candidate_name = normalize_cost_text(_field(candidate, "item_name"))
    candidate_unit = normalize_cost_text(_field(candidate, "unit"))
    candidate_price_type = _field(candidate, "price_type") or PRICE_TYPE_COMBINED
    if not candidate_name or not candidate_unit:
        return None

    allowed_statuses = list(statuses)
    items = (
        db.query(CostItem)
        .filter(CostItem.status.in_(allowed_statuses))
        .order_by(CostItem.id.desc())
        .all()
    )
    matches: list[tuple[int, int, CostItem]] = []
    status_priority = {
        COST_STATUS_ACTIVE: 300,
        COST_STATUS_DRAFT: 200,
        COST_STATUS_ARCHIVED: 100,
    }
    for item in items:
        if normalize_cost_text(item.item_name) != candidate_name:
            continue
        if normalize_cost_text(item.unit) != candidate_unit:
            continue
        if (item.price_type or PRICE_TYPE_COMBINED) != candidate_price_type:
            continue
        conflict_type = _conflict_type(candidate, item)
        if not conflict_type:
            continue
        exact_priority = 20 if conflict_type == "exact_active_duplicate" else 10
        matches.append((status_priority.get(item.status, 0), exact_priority, item))

    if not matches:
        return None
    matches.sort(key=lambda row: (row[0], row[1], row[2].id or 0), reverse=True)
    return matches[0][2]


def _field(candidate: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(name)
    return getattr(candidate, name, None)


def _conflict_type(candidate: Mapping[str, Any] | Any, existing: CostItem) -> str | None:
    candidate_spec = normalize_cost_text(_field(candidate, "spec"))
    existing_spec = normalize_cost_text(existing.spec)
    if candidate_spec == existing_spec:
        return "exact_active_duplicate"
    if not candidate_spec or not existing_spec:
        return "same_name_unit_missing_spec"
    if _specs_similar(candidate_spec, existing_spec):
        return "similar_active_duplicate"
    return None


def _specs_similar(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left in right or right in left:
        shorter = min(len(left), len(right))
        return shorter >= 4
    if SequenceMatcher(None, left, right).ratio() >= 0.82:
        return True
    left_tokens = _spec_tokens(left)
    right_tokens = _spec_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    return overlap >= 2 and overlap / min(len(left_tokens), len(right_tokens)) >= 0.72


def _spec_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[\s,;，。；、/\\|]+", value) if len(token) >= 2}


def _conflict_message(conflict_type: str) -> str:
    if conflict_type == "exact_active_duplicate":
        return "已存在相同 active 成本项，请先归档旧条目或修改规格/特征后再启用。"
    if conflict_type == "same_name_unit_missing_spec":
        return "已存在同名同单位 active 成本项，且至少一条规格/特征为空，需补充规格或归档旧条目后再启用。"
    return "已存在同名同单位且规格/特征高度相似的 active 成本项，请先确认是否重复。"
