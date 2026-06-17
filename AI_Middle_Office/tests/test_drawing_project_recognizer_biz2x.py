from __future__ import annotations

import json

from app.services.drawing_project_recognizer import (
    build_drawing_project_recognition_report,
    write_drawing_project_recognition_outputs,
)


def _match_report() -> dict[str, object]:
    return {
        "summary": {
            "source_signal_count": 3,
            "matched_signal_count": 3,
            "standard_candidate_count": 3,
        },
        "candidate_groups": [
            {
                "candidate_key": "BIZ2x4-0001",
                "source_signal": {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "01_平面图.dxf",
                    "source_name": "餐厅地面铺装玻化砖",
                    "evidence_text": "餐厅地面铺装玻化砖",
                    "source_row_number": 10,
                },
                "standard_candidates": [
                    {
                        "item_code": "011102003",
                        "item_name": "块料楼地面",
                        "unit_options": ["㎡", "m²"],
                        "feature_fill_candidates": [
                            {"field_name": "面层材料品种、规格", "candidate_value": "玻化砖"},
                            {"field_name": "结合层厚度、材料种类及强度等级", "candidate_value": ""},
                        ],
                        "match_confidence": 0.9,
                        "match_reasons": ["图纸出现地砖/玻化砖/块料地面做法，按块料楼地面作为候选"],
                    }
                ],
            },
            {
                "candidate_key": "BIZ2x4-0002",
                "source_signal": {
                    "source_kind": "material",
                    "source_kind_label": "材料",
                    "source_file": "02_材料表.dxf",
                    "source_name": "玻化砖材料说明",
                    "evidence_text": "玻化砖材料说明",
                    "source_row_number": 20,
                },
                "standard_candidates": [
                    {
                        "item_code": "011102003",
                        "item_name": "块料楼地面",
                        "unit_options": ["㎡"],
                        "feature_fill_candidates": [
                            {"field_name": "面层材料品种、规格", "candidate_value": "玻化砖"},
                            {"field_name": "结合层厚度、材料种类及强度等级", "candidate_value": ""},
                        ],
                        "match_confidence": 0.84,
                        "match_reasons": ["图纸出现地砖/玻化砖/块料地面做法，按块料楼地面作为候选"],
                    }
                ],
            },
            {
                "candidate_key": "BIZ2x4-0003",
                "source_signal": {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "01_平面图.dxf",
                    "source_name": "轻钢龙骨石膏板吊顶",
                    "evidence_text": "轻钢龙骨石膏板吊顶",
                    "source_row_number": 30,
                },
                "standard_candidates": [
                    {
                        "item_code": "011302001",
                        "item_name": "平面吊顶 | 天棚",
                        "unit_options": ["㎡"],
                        "feature_fill_candidates": [
                            {"field_name": "面板材料品种、规格", "candidate_value": "石膏板"},
                            {"field_name": "龙骨材料种类、规格、中距", "candidate_value": "轻钢龙骨"},
                        ],
                        "match_confidence": 0.88,
                        "match_reasons": ["图纸出现石膏板/轻钢龙骨/普通吊顶线索，按平面吊顶天棚作为候选"],
                    }
                ],
            },
        ],
    }


def test_biz2x_project_recognition_merges_sources_and_keeps_standard_features():
    report = build_drawing_project_recognition_report(_match_report())

    assert report["summary"]["recognized_project_count"] == 2
    floor = next(row for row in report["project_rows"] if row["标准项目编码"] == "011102003")
    assert floor["项目名称"] == "块料楼地面"
    assert floor["单位"] == "㎡"
    assert "面层材料品种、规格：玻化砖" in floor["项目特征"]
    assert "结合层厚度、材料种类及强度等级：待补充" in floor["项目特征"]
    assert floor["工程量"] == ""
    assert floor["来源线索数"] == 1
    assert "餐厅地面铺装玻化砖" in floor["图纸项目名称"]
    assert "玻化砖材料说明" not in floor["图纸项目名称"]


def test_biz2x_project_recognition_writes_outputs(tmp_path):
    report = build_drawing_project_recognition_report(_match_report())
    outputs = write_drawing_project_recognition_outputs(report, tmp_path, stem="project")

    assert set(outputs) == {"json", "markdown", "project_csv", "draft_four_field_xlsx"}
    assert json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))["summary"]["recognized_project_count"] == 2
    assert (tmp_path / "project_项目识别清单.csv").read_text(encoding="utf-8-sig").startswith("识别项目编号")
    assert (tmp_path / "project_标准列项草稿四字段.xlsx").read_bytes().startswith(b"PK")


def test_biz2x_project_recognition_prefers_coating_candidate_for_latex_paint():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 1},
            "candidate_groups": [
                {
                    "source_signal": {
                        "source_kind": "drawing_annotation",
                        "source_kind_label": "平面/立面文字标注",
                        "source_file": "03_立面图.dxf",
                        "source_name": "石膏板白色乳胶漆",
                        "evidence_text": "石膏板白色乳胶漆",
                    },
                    "standard_candidates": [
                        {
                            "item_code": "011302001",
                            "item_name": "平面吊顶 | 天棚",
                            "unit_options": ["㎡"],
                            "feature_fill_candidates": [],
                            "match_confidence": 0.8,
                            "match_reasons": ["图纸出现石膏板线索"],
                        },
                        {
                            "item_code": "011404002",
                            "item_name": "天棚喷刷涂料",
                            "unit_options": ["㎡"],
                            "feature_fill_candidates": [],
                            "match_confidence": 0.72,
                            "match_reasons": ["图纸出现乳胶漆线索"],
                        },
                    ],
                }
            ],
        }
    )

    assert report["summary"]["recognized_project_count"] == 1
    assert report["project_rows"][0]["项目名称"] == "天棚喷刷涂料"


def test_biz2x_project_recognition_filters_demolition_text_from_standard_floor_candidate():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 1},
            "candidate_groups": [
                {
                    "source_signal": {
                        "source_kind": "material",
                        "source_kind_label": "材料",
                        "source_file": "拆除平面.dxf",
                        "source_name": "拆除地面800x800地砖",
                        "evidence_text": "拆除地面800x800地砖",
                    },
                    "standard_candidates": [
                        {
                            "item_code": "011102003",
                            "item_name": "块料楼地面",
                            "unit_options": ["㎡"],
                            "feature_fill_candidates": [],
                            "match_confidence": 0.76,
                            "match_reasons": ["图纸出现地砖线索"],
                        }
                    ],
                }
            ],
        },
        project_lexicon={"summary": {"lexicon_entry_count": 0}, "entries": []},
    )

    assert report["summary"]["recognized_project_count"] == 0


def test_biz2x_project_recognition_filters_title_only_drawing_labels():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 1},
            "candidate_groups": [
                {
                    "source_signal": {
                        "source_kind": "drawing_annotation",
                        "source_kind_label": "平面/立面文字标注",
                        "source_file": "天花图.dxf",
                        "source_name": "职工餐厅天花造型尺寸图",
                        "evidence_text": "职工餐厅天花造型尺寸图",
                    },
                    "standard_candidates": [
                        {
                            "item_code": "011302003",
                            "item_name": "艺术造型 | 吊顶天棚",
                            "unit_options": ["㎡"],
                            "feature_fill_candidates": [],
                            "match_confidence": 0.68,
                            "match_reasons": ["图纸出现天花造型线索"],
                        }
                    ],
                }
            ],
        },
        project_lexicon={"summary": {"lexicon_entry_count": 0}, "entries": []},
    )

    assert report["summary"]["recognized_project_count"] == 0


def test_biz2x_project_recognition_supplements_answer_lexicon_candidates():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "拆除平面.dxf",
                    "source_name": "拆除单开实木门",
                    "evidence_text": "拆除单开实木门",
                    "source_row_number": 8,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0001",
                    "manual_item_name": "拆除单开实木门",
                    "manual_feature": "",
                    "manual_unit": "樘",
                    "category": "demolition",
                    "category_label": "拆除类",
                    "material_codes": [],
                    "strong_terms": ["拆除单开实木门"],
                    "weak_terms": ["拆除", "单开实木门"],
                    "standard_scope": "supplemental_or_other_specialty",
                    "standard_item_code": "",
                    "standard_item_name": "",
                    "standard_unit_options": [],
                    "standard_feature_fields": [],
                    "recognition_priority": 90,
                }
            ],
        },
        project_standard_mapping={"summary": {"mapping_entry_count": 0}, "rows": []},
    )

    assert report["summary"]["recognized_project_count"] == 1
    assert report["summary"]["lexicon_supplemental_project_count"] == 1
    row = report["project_rows"][0]
    assert row["项目名称"] == "拆除单开实木门"
    assert row["单位"] == "樘"
    assert row["工程量"] == ""
    assert row["工程量状态"] == "待 R2 标准映射确认，不进入最终算量"
    assert row["来源类型"] == "样例答案词库补充候选"
    assert "补充清单类别：拆除类" in row["项目特征"]


def test_biz2x_project_recognition_uses_r2_standard_mapping_for_lexicon_hit():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "地面材料.dxf",
                    "source_name": "餐厅瓷砖地面CT-01",
                    "evidence_text": "餐厅瓷砖地面CT-01 800*800地砖",
                    "source_row_number": 30,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0001",
                    "source_row_no": 30,
                    "manual_item_name": "瓷砖地面CT-01",
                    "manual_feature": "800*800地砖",
                    "manual_unit": "㎡",
                    "category": "floor",
                    "category_label": "地面类",
                    "material_codes": ["CT-01"],
                    "strong_terms": ["瓷砖地面CT-01", "CT-01"],
                    "weak_terms": ["瓷砖", "地面"],
                    "standard_scope": "supplemental_or_scope_pending",
                    "standard_item_code": "",
                    "standard_item_name": "",
                    "standard_unit_options": [],
                    "standard_feature_fields": [],
                    "recognition_priority": 86,
                }
            ],
        },
        project_standard_mapping={
            "summary": {
                "mapping_entry_count": 1,
                "mapping_status_counts": {"GB/T 可归并项目": 1},
            },
            "rows": [
                {
                    "source_row_no": 30,
                    "manual_item_name": "瓷砖地面CT-01",
                    "manual_unit": "㎡",
                    "mapping_status": "GB/T 可归并项目",
                    "standard_item_code": "011102003",
                    "standard_item_name": "块料楼地面",
                    "standard_unit_options": ["㎡"],
                    "standard_feature_fields": ["面层材料品种、规格"],
                    "feature_text_template": "面层材料品种、规格：瓷砖地面CT-01（材料编号：CT-01）",
                    "quantity_status": "待 CAD 区域/边界绑定后按标准规则计算",
                    "mapping_reason": "R2 映射测试",
                    "allowed_for_project_candidate": True,
                }
            ],
        },
    )

    assert report["summary"]["recognized_project_count"] == 1
    assert report["summary"]["standard_mapping_entry_count"] == 1
    row = report["project_rows"][0]
    assert row["标准项目编码"] == "011102003"
    assert row["项目名称"] == "块料楼地面"
    assert row["单位"] == "㎡"
    assert "材料编号：CT-01" in row["项目特征"]
    assert "R2映射状态：GB/T 可归并项目" in row["匹配理由"]


def test_biz2x_project_recognition_filters_weak_other_specialty_scope_hits():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "电气平面.dxf",
                    "source_name": "灯具",
                    "evidence_text": "灯具",
                    "source_row_number": 88,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0001",
                    "source_row_no": 88,
                    "source_sheet_name": "安装清单",
                    "manual_item_name": "灯具安装",
                    "manual_feature": "",
                    "manual_unit": "套",
                    "category": "lighting_electrical",
                    "category_label": "灯具电气类",
                    "material_codes": [],
                    "strong_terms": [],
                    "weak_terms": ["灯具"],
                    "recognition_priority": 76,
                }
            ],
        },
        project_standard_mapping={
            "summary": {"mapping_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0001",
                    "source_row_no": 88,
                    "source_sheet_name": "安装清单",
                    "manual_item_name": "灯具安装",
                    "manual_unit": "套",
                    "category": "lighting_electrical",
                    "mapping_status": "补充清单项目",
                    "unit_check_status": "supplemental_uses_manual_unit",
                    "allowed_for_project_candidate": True,
                }
            ],
        },
        project_scope_review={
            "summary": {"scope_review_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0001",
                    "manual_item_name": "灯具安装",
                    "review_action": "hold_other_specialty_until_scope_confirmed",
                    "recognition_allowed": True,
                    "recognition_min_score": 0.82,
                    "final_quantity_status": "其它专业/设备安装候选，待业务确认是否纳入本次报价",
                }
            ],
        },
    )

    assert report["summary"]["recognized_project_count"] == 0
    assert report["summary"]["lexicon_supplemental_project_count"] == 0


def test_biz2x_project_recognition_filters_waterproof_board_from_floor_waterproof_lexicon_hit():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "材料说明.dxf",
                    "source_name": "防水石膏板刷白色防潮无机涂料",
                    "evidence_text": "防水石膏板刷白色防潮无机涂料",
                    "source_row_number": 56,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0003",
                    "source_row_no": 56,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "地面防水",
                    "manual_feature": "1.5厚聚氨酯涂膜防水",
                    "manual_unit": "㎡",
                    "category": "waterproof",
                    "category_label": "防水防潮类",
                    "material_codes": [],
                    "strong_terms": [],
                    "weak_terms": ["防水"],
                    "recognition_priority": 80,
                }
            ],
        },
        project_standard_mapping={
            "summary": {"mapping_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0003",
                    "source_row_no": 56,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "地面防水",
                    "manual_unit": "㎡",
                    "category": "waterproof",
                    "mapping_status": "GB/T 可归并项目",
                    "standard_item_code": "010904002",
                    "standard_item_name": "楼(地) 面涂膜防水",
                    "standard_unit_options": ["㎡"],
                    "unit_check_status": "matched",
                    "allowed_for_project_candidate": True,
                }
            ],
        },
    )

    assert report["summary"]["recognized_project_count"] == 0


def test_biz2x_project_recognition_filters_demolition_text_from_non_demolition_lexicon_hit():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "拆除平面.dxf",
                    "source_name": "拆除石膏板二级圆形吊顶天花及水晶灯",
                    "evidence_text": "拆除石膏板二级圆形吊顶天花及水晶灯",
                    "source_row_number": 66,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0004",
                    "source_row_no": 66,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "石膏板吊顶",
                    "manual_feature": "",
                    "manual_unit": "㎡",
                    "category": "ceiling",
                    "category_label": "吊顶/天棚类",
                    "material_codes": [],
                    "strong_terms": [],
                    "weak_terms": ["石膏板", "吊顶", "天花"],
                    "recognition_priority": 84,
                }
            ],
        },
        project_standard_mapping={
            "summary": {"mapping_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0004",
                    "source_row_no": 66,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "石膏板吊顶",
                    "manual_unit": "㎡",
                    "category": "ceiling",
                    "mapping_status": "GB/T 可归并项目",
                    "standard_item_code": "011302001",
                    "standard_item_name": "平面吊顶 | 天棚",
                    "standard_unit_options": ["㎡"],
                    "unit_check_status": "matched",
                    "allowed_for_project_candidate": True,
                }
            ],
        },
    )

    assert report["summary"]["recognized_project_count"] == 0


def test_biz2x_project_recognition_filters_grout_text_from_stone_floor_lexicon_hit():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "construction_method",
                    "source_kind_label": "构造做法",
                    "source_file": "做法表.dxf",
                    "source_name": "地面地砖作美缝处理",
                    "evidence_text": "地面地砖作美缝处理",
                    "source_row_number": 72,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0005",
                    "source_row_no": 72,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "石材地面",
                    "manual_feature": "",
                    "manual_unit": "㎡",
                    "category": "floor",
                    "category_label": "地面类",
                    "material_codes": [],
                    "strong_terms": [],
                    "weak_terms": ["地面", "地砖"],
                    "recognition_priority": 82,
                }
            ],
        },
        project_standard_mapping={
            "summary": {"mapping_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0005",
                    "source_row_no": 72,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "石材地面",
                    "manual_unit": "㎡",
                    "category": "floor",
                    "mapping_status": "GB/T 可归并项目",
                    "standard_item_code": "011102001",
                    "standard_item_name": "石材楼地面",
                    "standard_unit_options": ["㎡"],
                    "unit_check_status": "matched",
                    "allowed_for_project_candidate": True,
                }
            ],
        },
    )

    assert report["summary"]["recognized_project_count"] == 0


def test_biz2x_project_recognition_marks_unit_conflict_scope_status():
    report = build_drawing_project_recognition_report(
        {
            "summary": {"source_signal_count": 1, "matched_signal_count": 0},
            "source_signals": [
                {
                    "source_kind": "drawing_annotation",
                    "source_kind_label": "平面/立面文字标注",
                    "source_file": "吊顶平面.dxf",
                    "source_name": "灯槽",
                    "evidence_text": "灯槽",
                    "source_row_number": 12,
                }
            ],
            "candidate_groups": [],
        },
        project_lexicon={
            "summary": {"lexicon_entry_count": 1},
            "entries": [
                {
                    "entry_id": "BIZ2xLEX-0002",
                    "source_row_no": 12,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "灯槽",
                    "manual_feature": "",
                    "manual_unit": "m",
                    "category": "ceiling",
                    "category_label": "吊顶/天棚类",
                    "material_codes": [],
                    "strong_terms": ["灯槽"],
                    "weak_terms": ["灯槽"],
                    "recognition_priority": 84,
                }
            ],
        },
        project_standard_mapping={
            "summary": {"mapping_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0002",
                    "source_row_no": 12,
                    "source_sheet_name": "装修清单",
                    "manual_item_name": "灯槽",
                    "manual_unit": "m",
                    "category": "ceiling",
                    "mapping_status": "GB/T 可归并项目",
                    "standard_item_code": "011302003",
                    "standard_item_name": "艺术造型 | 吊顶天棚",
                    "standard_unit_options": ["㎡"],
                    "standard_feature_fields": ["吊顶形式"],
                    "feature_text_template": "吊顶形式：灯槽",
                    "quantity_status": "待 CAD 区域/边界绑定后按标准规则计算",
                    "unit_check_status": "unit_conflict_needs_confirmation",
                    "mapping_reason": "灯槽按艺术造型吊顶候选",
                    "allowed_for_project_candidate": True,
                }
            ],
        },
        project_scope_review={
            "summary": {"scope_review_entry_count": 1},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0002",
                    "manual_item_name": "灯槽",
                    "review_action": "confirm_standard_unit_or_convert_to_supplement",
                    "recognition_allowed": True,
                    "recognition_min_score": 0.72,
                    "final_quantity_status": "待 R2-2 单位口径确认，不进入最终算量",
                    "false_positive_guard": "保留候选，但不得复用系统工程量",
                }
            ],
        },
    )

    assert report["summary"]["recognized_project_count"] == 1
    row = report["project_rows"][0]
    assert row["标准项目编码"] == "011302003"
    assert row["工程量状态"] == "待 R2-2 单位口径确认，不进入最终算量"
    assert "R2-2复核动作：confirm_standard_unit_or_convert_to_supplement" in row["匹配理由"]
    assert "不得复用系统工程量" in row["匹配理由"]
