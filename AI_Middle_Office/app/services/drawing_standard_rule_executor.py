from __future__ import annotations

import re
from typing import Any


READY_STATUS = "standard_rule_execution_ready_for_manual_review"


TEMPLATE_NAMES = {
    "area_horizontal_projection": "水平投影面积",
    "area_floor_boundary": "楼地面/地面边界面积",
    "length_wall_perimeter": "墙边净长度",
    "wall_area_by_perimeter_height": "墙面周长乘高度面积",
    "count_by_block": "图块数量",
    "expanded_area_requires_manual_review": "展开面积",
    "unsupported_standard_rule": "暂不支持的标准规则",
}


def execute_standard_quantity_rule(
    *,
    rule_text: str,
    formula_type: str,
    template_context: str,
    geometry_inputs: dict[str, Any],
    item_code: str = "",
    item_name: str = "",
    result_unit: str = "",
) -> dict[str, Any]:
    template_id = infer_standard_rule_template(
        rule_text=rule_text,
        formula_type=formula_type,
        template_context=template_context,
    )
    base = {
        "ok": False,
        "template_id": template_id,
        "template_name": TEMPLATE_NAMES.get(template_id, template_id),
        "standard_item_code": item_code,
        "standard_item_name": item_name,
        "standard_quantity_rule_text": _clean_text(rule_text),
        "standard_formula_type": _clean_text(formula_type),
        "standard_rule_execution_status": "",
        "formula": "",
        "input_values": _compact_inputs(geometry_inputs),
        "result_quantity": None,
        "result_unit": "",
        "block_reason": "",
        "unresolved_requirements": [
            "standard_item_manual_confirmation",
            "feature_values_need_review",
            "deduction_and_merge_rules_need_review",
        ],
    }

    if template_id == "area_horizontal_projection":
        return _execute_single_input_quantity(
            base,
            geometry_inputs,
            input_keys=("horizontal_projection_area_sqm", "region_area_sqm", "area_sqm"),
            formula="绑定区域 CAD 面积",
            unit=result_unit or "㎡",
            missing_status="blocked_missing_horizontal_projection_area",
            missing_reason="标准规则要求水平投影/面积计算，但缺少可用 CAD 面积证据。",
        )
    if template_id == "area_floor_boundary":
        return _execute_single_input_quantity(
            base,
            geometry_inputs,
            input_keys=("floor_area_sqm", "region_area_sqm", "area_sqm"),
            formula="绑定区域 CAD 面积",
            unit=result_unit or "㎡",
            missing_status="blocked_missing_floor_boundary_area",
            missing_reason="标准规则要求楼地面/地面面积计算，但缺少封闭区域面积证据。",
        )
    if template_id == "wall_area_by_perimeter_height":
        return _execute_wall_area(base, geometry_inputs, result_unit or "㎡")
    if template_id == "length_wall_perimeter":
        return _execute_single_input_quantity(
            base,
            geometry_inputs,
            input_keys=("net_perimeter_m", "length_m"),
            formula="房间净周长候选",
            unit=result_unit or "m",
            missing_status="blocked_missing_wall_perimeter_length",
            missing_reason="标准规则要求按长度计算，但缺少墙边净长度/净周长证据。",
        )
    if template_id == "count_by_block":
        return _execute_single_input_quantity(
            base,
            geometry_inputs,
            input_keys=("count", "block_count"),
            formula="图块数量",
            unit=result_unit or "个",
            missing_status="blocked_missing_count_evidence",
            missing_reason="标准规则要求按数量计算，但缺少可用图块数量证据。",
        )
    if template_id == "expanded_area_requires_manual_review":
        expanded_area = _float_or_none(geometry_inputs.get("expanded_area_sqm"))
        if expanded_area is not None and expanded_area > 0:
            return _ready(
                base,
                formula="展开面积",
                quantity=expanded_area,
                unit=result_unit or "㎡",
                extra_unresolved=["expanded_area_deduction_and_merge_rules_need_review"],
            )
        return _blocked(
            base,
            "blocked_standard_rule_requires_expanded_area",
            "标准规则要求展开面积，当前证据只有平面/水平区域面积或缺少洞口侧壁并入依据。",
            extra_unresolved=["expanded_area_geometry_missing"],
        )
    return _blocked(
        base,
        "blocked_unsupported_standard_rule_template",
        "标准库工程量计算规则暂未映射为可执行模板。",
        extra_unresolved=["standard_rule_template_not_implemented"],
    )


def infer_standard_rule_template(*, rule_text: str, formula_type: str, template_context: str = "") -> str:
    text = _normalize(rule_text)
    formula = _clean_text(formula_type).lower()
    context = _clean_text(template_context).lower()

    if "展开面积" in text or formula == "expanded_area":
        return "expanded_area_requires_manual_review"
    if context in {"wall_waterproof", "wall_area_by_perimeter_height"}:
        return "wall_area_by_perimeter_height"
    if context in {"baseboard", "length_wall_perimeter"}:
        return "length_wall_perimeter"
    if context in {"floor_area", "area_floor_boundary"}:
        return "area_floor_boundary"
    if context in {"ceiling_area", "ceiling_paint_area", "area_horizontal_projection"}:
        return "area_horizontal_projection"
    if formula in {"length"} or "长度计算" in text or "中心线长度" in text:
        return "length_wall_perimeter"
    if formula in {"count"} or "数量计算" in text:
        return "count_by_block"
    if formula in {"area", "ceiling_area"}:
        if "水平投影面积" in text or "正投影面积" in text:
            return "area_horizontal_projection"
        return "area_floor_boundary"
    if formula in {"vertical_area"}:
        return "unsupported_standard_rule"
    return "unsupported_standard_rule"


def _execute_wall_area(base: dict[str, Any], geometry_inputs: dict[str, Any], unit: str) -> dict[str, Any]:
    perimeter = _float_or_none(geometry_inputs.get("net_perimeter_m"))
    height = _float_or_none(geometry_inputs.get("height_m"))
    if perimeter is None or perimeter <= 0:
        return _blocked(
            base,
            "blocked_missing_net_perimeter",
            "墙面面积计算缺少房间净周长证据。",
            extra_unresolved=["net_perimeter_missing"],
        )
    if height is None or height <= 0:
        return _blocked(
            base,
            "blocked_missing_height",
            "墙面面积计算缺少高度证据。",
            extra_unresolved=["height_missing"],
        )
    return _ready(
        base,
        formula="房间净周长候选 × 防水高度",
        quantity=perimeter * height,
        unit=unit,
        extra_unresolved=["door_opening_deduction_need_review"],
    )


def _execute_single_input_quantity(
    base: dict[str, Any],
    geometry_inputs: dict[str, Any],
    *,
    input_keys: tuple[str, ...],
    formula: str,
    unit: str,
    missing_status: str,
    missing_reason: str,
) -> dict[str, Any]:
    for key in input_keys:
        quantity = _float_or_none(geometry_inputs.get(key))
        if quantity is not None and quantity > 0:
            return _ready(base, formula=formula, quantity=quantity, unit=unit)
    return _blocked(base, missing_status, missing_reason)


def _ready(
    base: dict[str, Any],
    *,
    formula: str,
    quantity: float,
    unit: str,
    extra_unresolved: list[str] | None = None,
) -> dict[str, Any]:
    unresolved = list(base["unresolved_requirements"])
    for item in extra_unresolved or []:
        if item not in unresolved:
            unresolved.append(item)
    return {
        **base,
        "ok": True,
        "standard_rule_execution_status": READY_STATUS,
        "formula": formula,
        "result_quantity": round(quantity, 4),
        "result_unit": unit,
        "unresolved_requirements": unresolved,
    }


def _blocked(
    base: dict[str, Any],
    status: str,
    reason: str,
    *,
    extra_unresolved: list[str] | None = None,
) -> dict[str, Any]:
    unresolved = list(base["unresolved_requirements"])
    for item in extra_unresolved or []:
        if item not in unresolved:
            unresolved.append(item)
    return {
        **base,
        "ok": False,
        "standard_rule_execution_status": status,
        "block_reason": reason,
        "unresolved_requirements": unresolved,
    }


def _compact_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in inputs.items() if value not in (None, "")}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
