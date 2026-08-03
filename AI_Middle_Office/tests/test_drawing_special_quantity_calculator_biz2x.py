from __future__ import annotations

import json

from app.services.drawing_special_quantity_calculator import (
    build_special_quantity_calculation_report,
    write_special_quantity_calculation_outputs,
)


def test_biz2x_special_quantity_calculator_generates_ceiling_area_trace(tmp_path):
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-001",
                "标准项目编码": "011302001",
                "图纸项目名称": "石膏板饰面吊顶",
                "项目名称": "平面吊顶天棚",
                "项目特征": "吊顶形式：石膏板",
                "单位": "㎡",
                "识别证据": "石膏板饰面吊顶",
            }
        ]
    }
    binding_report = {
        "binding_rows": [
            {
                "识别项目编号": "P-001",
                "区域绑定状态": "建议绑定区域，需复核",
                "推荐区域编号": "BIZ2xR-00001",
                "区域面积": 16.8,
                "区域周长": 18.4,
            }
        ]
    }
    match_report = _match_report("011302001", "按设计图示尺寸以水平投影面积计算", "area")

    report = build_special_quantity_calculation_report(
        project_report=project_report,
        project_region_binding_report=binding_report,
        room_boundary_report={"room_rows": []},
        standard_match_report=match_report,
    )

    row = report["special_quantity_trace_rows"][0]
    assert row["trace状态"] == "special_quantity_trace_ready_for_manual_review"
    assert row["专项类型"] == "吊顶/天棚水平投影面积"
    assert row["建议工程量"] == 16.8
    assert row["建议单位"] == "㎡"
    assert row["标准工程量计算规则"] == "按设计图示尺寸以水平投影面积计算"
    assert row["标准规则模板"] == "area_horizontal_projection"
    assert row["标准规则执行状态"] == "standard_rule_execution_ready_for_manual_review"
    assert report["summary"]["ready_for_manual_review_count"] == 1

    outputs = write_special_quantity_calculation_outputs(report, tmp_path, stem="special_quantity")
    assert set(outputs) == {"json", "markdown", "trace_csv"}
    assert json.loads((tmp_path / "special_quantity.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-special-quantity-calculation-trace"


def test_biz2x_special_quantity_calculator_generates_waterproof_area_from_net_perimeter():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-002",
                "标准项目编码": "011101003",
                "图纸项目名称": "洗手间墙面防水高度1800",
                "项目名称": "墙面防水",
                "项目特征": "防水高度：1800mm",
                "单位": "㎡",
                "识别证据": "洗手间墙面防水高度1800",
            }
        ]
    }
    binding_report = {
        "binding_rows": [
            {
                "识别项目编号": "P-002",
                "区域绑定状态": "建议绑定区域，需复核",
                "推荐区域编号": "BIZ2xR-00002",
                "区域面积": 2.52,
                "区域周长": 6.4,
            }
        ]
    }
    room_boundary_report = {
        "room_rows": [
            {
                "房间编号": "BIZ2xROOM-00001",
                "房间/空间名称": "洗手间",
                "绑定区域编号": "BIZ2xR-00002",
                "CAD周长": 6.4,
                "净周长候选": 5.5,
                "净周长状态": "已按开口候选扣减，需复核",
            }
        ]
    }

    report = build_special_quantity_calculation_report(
        project_report=project_report,
        project_region_binding_report=binding_report,
        room_boundary_report=room_boundary_report,
        standard_match_report=_match_report("011101003", "按设计图示尺寸以面积计算", "area"),
    )

    row = report["special_quantity_trace_rows"][0]
    assert row["专项类型"] == "墙面防水面积"
    assert row["建议工程量"] == 9.9
    assert row["计算公式"] == "房间净周长候选 × 防水高度"
    assert "净周长=5.5m" in row["计算输入"]
    assert row["标准规则模板"] == "wall_area_by_perimeter_height"
    assert row["calculation_trace"]["waterproof_height_m"] == 1.8


def test_biz2x_special_quantity_calculator_blocks_baseboard_when_net_perimeter_missing():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-003",
                "标准项目编码": "011105003",
                "图纸项目名称": "水泥砂浆踢脚线",
                "项目名称": "水泥砂浆踢脚线",
                "项目特征": "材料种类：水泥砂浆",
                "单位": "m",
                "识别证据": "水泥砂浆踢脚线",
            }
        ]
    }
    binding_report = {
        "binding_rows": [
            {
                "识别项目编号": "P-003",
                "区域绑定状态": "建议绑定区域，需复核",
                "推荐区域编号": "BIZ2xR-00003",
                "区域面积": 12.0,
                "区域周长": 14.0,
            }
        ]
    }
    room_boundary_report = {
        "room_rows": [
            {
                "房间编号": "BIZ2xROOM-00002",
                "房间/空间名称": "餐厅",
                "绑定区域编号": "BIZ2xR-00003",
                "CAD周长": 14.0,
                "净周长候选": "",
                "净周长状态": "存在开口但缺少宽度证据",
            }
        ]
    }

    report = build_special_quantity_calculation_report(
        project_report=project_report,
        project_region_binding_report=binding_report,
        room_boundary_report=room_boundary_report,
        standard_match_report=_match_report("011105003", "按设计图示长度计算", "length"),
    )

    row = report["special_quantity_trace_rows"][0]
    assert row["trace状态"] == "blocked_missing_net_perimeter"
    assert row["是否可复核"] == "否"
    assert "净周长不可用" in row["阻断原因"]
    assert report["summary"]["blocked_trace_count"] == 1


def test_biz2x_special_quantity_calculator_blocks_ceiling_paint_when_standard_requires_expanded_area():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-004",
                "标准项目编码": "011404002",
                "图纸项目名称": "天棚喷刷涂料",
                "项目名称": "天棚喷刷涂料",
                "项目特征": "涂料品种：无机涂料",
                "单位": "㎡",
                "识别证据": "天棚喷刷涂料",
            }
        ]
    }
    binding_report = {
        "binding_rows": [
            {
                "识别项目编号": "P-004",
                "区域绑定状态": "建议绑定区域，需复核",
                "推荐区域编号": "BIZ2xR-00004",
                "区域面积": 18.0,
                "区域周长": 18.4,
            }
        ]
    }

    report = build_special_quantity_calculation_report(
        project_report=project_report,
        project_region_binding_report=binding_report,
        room_boundary_report={"room_rows": []},
        standard_match_report=_match_report("011404002", "按设计图示尺寸以展开面积计算。洞口侧壁面积并入相应喷刷部位中计算", "expanded_area"),
    )

    row = report["special_quantity_trace_rows"][0]
    assert row["trace状态"] == "blocked_standard_rule_requires_expanded_area"
    assert row["标准规则模板"] == "expanded_area_requires_manual_review"
    assert row["是否可复核"] == "否"
    assert "展开面积" in row["阻断原因"]


def _match_report(code: str, rule_text: str, formula_type: str) -> dict:
    return {
        "standard_item_candidates": [
            {
                "standard_item_code": code,
                "quantity_rule_text": rule_text,
                "quantity_formula_type": formula_type,
                "feature_fields": [],
            }
        ]
    }
