from __future__ import annotations

from app.services.drawing_standard_rule_executor import (
    READY_STATUS,
    execute_standard_quantity_rule,
    infer_standard_rule_template,
)


def test_biz2x_a5_executes_horizontal_projection_area_template():
    execution = execute_standard_quantity_rule(
        rule_text="按设计图示尺寸以水平投影面积计算",
        formula_type="area",
        template_context="ceiling_area",
        geometry_inputs={"horizontal_projection_area_sqm": 18.25678},
        item_code="011302001",
        item_name="平面吊顶天棚",
        result_unit="㎡",
    )

    assert execution["ok"] is True
    assert execution["template_id"] == "area_horizontal_projection"
    assert execution["standard_rule_execution_status"] == READY_STATUS
    assert execution["result_quantity"] == 18.2568
    assert execution["formula"] == "绑定区域 CAD 面积"


def test_biz2x_a5_executes_wall_area_by_net_perimeter_and_height():
    execution = execute_standard_quantity_rule(
        rule_text="按设计图示尺寸以面积计算",
        formula_type="vertical_area",
        template_context="wall_waterproof",
        geometry_inputs={"net_perimeter_m": 5.5, "height_m": 1.8},
        item_code="010903002",
        item_name="墙面涂膜防水",
        result_unit="㎡",
    )

    assert execution["ok"] is True
    assert execution["template_id"] == "wall_area_by_perimeter_height"
    assert execution["result_quantity"] == 9.9
    assert execution["formula"] == "房间净周长候选 × 防水高度"


def test_biz2x_a5_blocks_expanded_area_without_expanded_geometry():
    execution = execute_standard_quantity_rule(
        rule_text="按设计图示尺寸以展开面积计算。洞口侧壁面积并入相应喷刷部位中计算",
        formula_type="expanded_area",
        template_context="ceiling_paint_area",
        geometry_inputs={"horizontal_projection_area_sqm": 12.0},
        item_code="011404002",
        item_name="天棚喷刷涂料",
        result_unit="㎡",
    )

    assert infer_standard_rule_template(
        rule_text="按设计图示尺寸以展开面积计算",
        formula_type="expanded_area",
        template_context="ceiling_paint_area",
    ) == "expanded_area_requires_manual_review"
    assert execution["ok"] is False
    assert execution["standard_rule_execution_status"] == "blocked_standard_rule_requires_expanded_area"
    assert "展开面积" in execution["block_reason"]
