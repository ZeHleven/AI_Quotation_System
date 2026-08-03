from __future__ import annotations

import json

from app.services.drawing_standard_matcher import (
    build_feature_fill_csv_rows,
    build_standard_match_csv_rows,
    match_drawing_fields_to_standard,
    write_standard_match_outputs,
)
from app.services.quantity_standard_library import (
    ACTIVE_STATUS,
    STANDARD_LIBRARY_VERSION,
    QuantityStandardLibrary,
)


def _standard_item(code: str, name: str, features: list[str]) -> dict[str, object]:
    return {
        "item_code": code,
        "item_name": name,
        "chapter_name": "测试章节",
        "status": ACTIVE_STATUS,
        "verification_status": "verified_against_standard",
        "feature_fields": [
            {"name": feature, "required": True, "source": "test"}
            for feature in features
        ],
        "unit_options": ["m²", "㎡", "m2", "平方米"] if code != "010810002" else ["m", "米"],
        "quantity_rule": {
            "rule_status": "verified_against_standard",
            "rule_text": "按设计图示尺寸以面积计算" if code != "010810002" else "按设计图示尺寸以长度计算",
            "formula_type": "area" if code != "010810002" else "length",
            "required_evidence": ["设计图纸", "尺寸标注"],
        },
        "drawing_evidence_requirements": ["设计图纸", "材料表", "做法表"],
        "keywords": [name],
        "exclusion_keywords": [],
        "source_note": "test",
    }


def _library() -> QuantityStandardLibrary:
    raw = {
        "version": STANDARD_LIBRARY_VERSION,
        "standard": {
            "code": "GBT50854-2024",
            "name": "房屋建筑与装饰工程工程量计算标准",
            "source_text_status": "test",
            "strict_rules": [
                "项目特征必须按标准库 feature_fields 字段口径生成。",
                "工程量必须按标准库 quantity_rule 计算规则生成。",
            ],
        },
        "items": [
            _standard_item("011102003", "块料楼地面", ["结合层厚度、材料种类及强度等级", "面层材料品种、规格"]),
            _standard_item("010904002", "楼(地) 面涂膜防水", ["防水膜品种", "涂膜厚度、遍数", "上翻高度"]),
            _standard_item("011302001", "平面吊顶 | 天棚", ["吊顶形式、吊杆规格、高度", "龙骨材料种类、规格、中距", "面板材料品种、规格"]),
            _standard_item("011404002", "天棚喷刷涂料", ["基层类型", "腻子种类", "涂料品种、喷刷遍数"]),
            _standard_item("010810002", "窗帘盒", ["窗帘盒材质、规格", "防护材料种类"]),
        ],
        "out_of_scope_policy": {},
    }
    return QuantityStandardLibrary.from_dict(raw)


def _field_report() -> dict[str, object]:
    return {
        "summary": {"material_method_row_count": 4},
        "material_method_rows": [
            {
                "source_file": "02.通用节点【一】.dxf",
                "source_table_anchor": "做法详图",
                "source_row_number": 3,
                "row_type": "construction_method",
                "row_type_label": "构造做法",
                "material_or_method_name": "厨卫地面做法",
                "spec_or_method": "20厚1:3水泥砂浆结合层；1.5厚聚氨酯涂膜防水三遍",
                "confidence": 0.8,
                "raw_row_text": "厨卫地面做法 | 20厚1:3水泥砂浆结合层 | 1.5厚聚氨酯涂膜防水三遍",
            },
            {
                "source_file": "02.通用节点【一】.dxf",
                "source_table_anchor": "做法详图",
                "source_row_number": 4,
                "row_type": "construction_method",
                "row_type_label": "构造做法",
                "material_or_method_name": "轻钢龙骨吊顶",
                "spec_or_method": "∅8吊筋；50主龙@900；50副龙@300X600；9.5厚纸面石膏板",
                "confidence": 0.82,
                "raw_row_text": "轻钢龙骨吊顶 | 9.5厚纸面石膏板",
            },
            {
                "source_file": "03.施工图.dxf",
                "source_table_anchor": "材料表",
                "source_row_number": 8,
                "row_type": "material",
                "row_type_label": "材料",
                "material_or_method_name": "玻化砖材料说明",
                "spec_or_method": "",
                "confidence": 0.84,
                "raw_row_text": "玻化砖材料说明",
            },
            {
                "source_file": "03.施工图.dxf",
                "source_table_anchor": "节点",
                "source_row_number": 9,
                "row_type": "construction_method",
                "row_type_label": "构造做法",
                "material_or_method_name": "窗帘盒做法",
                "spec_or_method": "18mm阻燃板；铝板收边",
                "confidence": 0.82,
                "raw_row_text": "窗帘盒做法 | 18mm阻燃板 | 铝板收边",
            },
        ],
        "drawing_annotation_rows": [
            {
                "source_file": "03.施工图.dxf",
                "source_table_anchor": "图纸文字标注",
                "source_row_number": 88,
                "row_type": "drawing_annotation",
                "row_type_label": "平面/立面文字标注",
                "material_or_method_name": "餐厅地面铺装玻化砖",
                "spec_or_method": "餐厅地面铺装玻化砖",
                "confidence": 0.86,
                "raw_row_text": "餐厅地面铺装玻化砖",
            },
        ],
    }


def test_biz2x4_matches_drawing_signals_to_active_standard_items():
    report = match_drawing_fields_to_standard(_field_report(), _library())

    codes = {row["standard_item_code"] for row in report["standard_item_candidates"]}
    assert {"010904002", "011302001", "011102003", "010810002"} <= codes
    assert any(group["source_signal"]["source_kind"] == "drawing_annotation" for group in report["candidate_groups"])
    assert report["safe_for_final_quantity_list"] is False
    assert report["summary"]["quantity_ready_count"] == 0
    assert report["summary"]["quantity_pending_count"] == report["summary"]["standard_candidate_count"]


def test_biz2x4_feature_fill_uses_standard_field_names_and_drawing_evidence():
    report = match_drawing_fields_to_standard(_field_report(), _library())
    ceiling_group = next(
        group
        for group in report["candidate_groups"]
        if group["source_signal"]["source_name"] == "轻钢龙骨吊顶"
    )
    ceiling = next(candidate for candidate in ceiling_group["standard_candidates"] if candidate["item_code"] == "011302001")

    field_names = {feature["field_name"] for feature in ceiling["feature_fill_candidates"]}
    values = "；".join(feature["candidate_value"] for feature in ceiling["feature_fill_candidates"])

    assert {"吊顶形式、吊杆规格、高度", "龙骨材料种类、规格、中距", "面板材料品种、规格"} <= field_names
    assert "轻钢龙骨" in values
    assert "9.5厚纸面石膏板" in values


def test_biz2x4_writes_standard_match_outputs(tmp_path):
    report = match_drawing_fields_to_standard(_field_report(), _library())

    outputs = write_standard_match_outputs(report, tmp_path, stem="standard_match")
    match_rows = build_standard_match_csv_rows(report)
    feature_rows = build_feature_fill_csv_rows(report)

    assert set(outputs) == {"json", "markdown", "standard_match_csv", "feature_fill_csv"}
    assert match_rows
    assert feature_rows
    assert json.loads((tmp_path / "standard_match.json").read_text(encoding="utf-8"))["ok"] is True
    assert (tmp_path / "standard_match_标准项目候选.csv").read_text(encoding="utf-8-sig").startswith("候选编号")
