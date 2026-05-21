from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any

from app.models.cost_item import CostItem


TEXT_SPLITTER = re.compile(r"[\s\-_—–,，、。.:：;；/\\|+&()\[\]{}（）【】<>《》\"'“”‘’\u3000]+")


@dataclass(frozen=True)
class OmissionRule:
    rule_id: str
    trigger_groups: tuple[tuple[str, ...], ...]
    suggested_groups: tuple[tuple[str, ...], ...]
    suggested_label: str
    reason: str
    severity: str = "notice"
    confidence: float = 0.7
    suppress_groups: tuple[tuple[str, ...], ...] | None = None


OMISSION_RULES: tuple[OmissionRule, ...] = (
    OmissionRule(
        rule_id="biz2e_floor_remove_baseboard",
        trigger_groups=(("地板", "拆"),),
        suggested_groups=(("脚线", "拆"), ("踢脚线", "拆")),
        suggested_label="踢脚线拆除",
        reason="地板拆除场景常见同时涉及踢脚线拆除，请确认是否已包含或另计。",
        severity="notice",
        confidence=0.78,
    ),
    OmissionRule(
        rule_id="biz2e_waterproof_protection",
        trigger_groups=(("防水",),),
        suggested_groups=(("防水", "保护"), ("保护层",)),
        suggested_label="防水保护层",
        reason="防水施工后通常需要确认保护层做法，请确认是否已包含或另计。",
        severity="notice",
        confidence=0.74,
    ),
    OmissionRule(
        rule_id="biz2e_tile_remove_disposal",
        trigger_groups=(("墙砖", "拆"), ("地砖", "拆"), ("瓷砖", "拆")),
        suggested_groups=(("垃圾", "清运"), ("垃圾", "外运")),
        suggested_label="垃圾清运",
        reason="墙地砖拆除通常会产生拆除垃圾，请确认清运是否已包含或另计。",
        severity="notice",
        confidence=0.68,
    ),
    OmissionRule(
        rule_id="biz2e_ceiling_remove_disposal",
        trigger_groups=(("吊顶", "拆"),),
        suggested_groups=(("垃圾", "清运"), ("垃圾", "外运")),
        suggested_label="垃圾清运",
        reason="吊顶拆除通常会产生拆除垃圾，请确认清运是否已包含或另计。",
        severity="notice",
        confidence=0.66,
    ),
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).lower()
    return TEXT_SPLITTER.sub("", text)


def _row_text(row: dict[str, Any]) -> str:
    values = (
        row.get("item_name"),
        row.get("project_name"),
        row.get("name"),
        row.get("material"),
        row.get("material_name"),
        row.get("spec"),
        row.get("specification"),
        row.get("feature"),
        row.get("features"),
        row.get("project_feature"),
        row.get("notes"),
        row.get("remark"),
        row.get("remarks"),
    )
    return _canonical_text(" ".join(_clean_text(value) for value in values if value not in (None, "")))


def _row_name(row: dict[str, Any]) -> str:
    for key in ("item_name", "project_name", "name", "material", "material_name"):
        value = row.get(key)
        if value not in (None, ""):
            return _clean_text(value)
    return ""


def _item_text(item: CostItem) -> str:
    values = (item.category, item.subcategory, item.item_name, item.spec, item.notes)
    return _canonical_text(" ".join(_clean_text(value) for value in values if value not in (None, "")))


def _contains_group(text: str, group: tuple[str, ...]) -> bool:
    return all(_canonical_text(keyword) in text for keyword in group)


def _matches_any_group(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    return any(_contains_group(text, group) for group in groups)


def _find_trigger_row(rows: list[dict[str, Any]], rule: OmissionRule) -> tuple[int, dict[str, Any]] | None:
    for index, row in enumerate(rows):
        if _matches_any_group(_row_text(row), rule.trigger_groups):
            return index, row
    return None


def _already_present(row_texts: list[str], rule: OmissionRule) -> bool:
    suppress_groups = rule.suppress_groups or rule.suggested_groups
    return any(_matches_any_group(text, suppress_groups) for text in row_texts)


def _find_suggested_cost_item(active_items: list[CostItem], rule: OmissionRule) -> CostItem | None:
    for item in active_items:
        if _matches_any_group(_item_text(item), rule.suggested_groups):
            return item
    return None


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def detect_quote_omissions(rows: list[dict[str, Any]], active_items: list[CostItem]) -> dict[str, Any]:
    row_texts = [_row_text(row) for row in rows]
    suggestions: list[dict[str, Any]] = []
    suggested_cost_item_ids: set[int] = set()

    for rule in OMISSION_RULES:
        trigger = _find_trigger_row(rows, rule)
        if not trigger or _already_present(row_texts, rule):
            continue

        cost_item = _find_suggested_cost_item(active_items, rule)
        if not cost_item or cost_item.id in suggested_cost_item_ids:
            continue

        trigger_index, trigger_row = trigger
        suggested_cost_item_ids.add(cost_item.id)
        suggestions.append(
            {
                "rule_id": rule.rule_id,
                "severity": rule.severity,
                "confidence": round(rule.confidence, 2),
                "trigger_row_no": trigger_index + 1,
                "trigger_item": _row_name(trigger_row),
                "suggested_item_name": cost_item.item_name or rule.suggested_label,
                "suggested_label": rule.suggested_label,
                "cost_item_id": cost_item.id,
                "unit": cost_item.unit,
                "reference_price": _round_money(cost_item.price),
                "reason": rule.reason,
            }
        )

    return {
        "omission_summary": {
            "enabled": True,
            "rule_count": len(OMISSION_RULES),
            "suggestion_count": len(suggestions),
            "high_confidence_count": sum(1 for item in suggestions if float(item.get("confidence") or 0) >= 0.85),
        },
        "omission_suggestions": suggestions,
    }
