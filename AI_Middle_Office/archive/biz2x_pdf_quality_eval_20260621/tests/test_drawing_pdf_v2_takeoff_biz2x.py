from __future__ import annotations

import json
from pathlib import Path

from app.services.drawing_pdf_v2_takeoff import (
    build_pdf_v2_takeoff_report,
    extract_evidence_rows,
    normalize_evidence_rows,
    write_pdf_v2_takeoff_outputs,
)
from app.services.drawing_three_field_acceptance import ThreeFieldAnswerRow
from scripts.biz2x_pdf_v2_takeoff import merge_pdf_evidence_reports


def _pdf_direct_report() -> dict[str, object]:
    return {
        "summary": {
            "pdf_file_count": 1,
            "pdf_page_count": 1,
            "pdf_render_status": "rendered",
        },
        "item_rows": [
            {
                "PDF文件": "03.pdf",
                "页码": 1,
                "tile_id": "p001_g03_r02_c02",
                "图纸项目名称": "瓷砖地面",
                "空间/部位": "餐厅",
                "材料编号": "CT-01",
                "规格/做法": "600x600地砖，水泥砂浆结合层",
                "证据文本": "餐厅 CT-01 600x600 地砖",
                "建议单位": "m²",
                "置信度": 0.86,
                "需人工复核": True,
            },
            {
                "PDF文件": "04.pdf",
                "页码": 1,
                "tile_id": "p001_g03_r02_c03",
                "图纸项目名称": "给水管",
                "材料编号": "DN40",
                "规格/做法": "SUS304薄壁不锈钢管 DN40",
                "证据文本": "给水管 SUS304 DN40",
                "建议单位": "m",
                "置信度": 0.82,
                "需人工复核": True,
            },
        ],
    }


def test_biz2x_pdf_v2_extracts_and_normalizes_evidence():
    evidence_rows = extract_evidence_rows(_pdf_direct_report())
    normalized_rows = normalize_evidence_rows(evidence_rows)

    assert len(evidence_rows) == 2
    assert evidence_rows[0]["evidence_type"] == "floor"
    assert "CT-01" in evidence_rows[0]["material_codes"]
    assert normalized_rows[0]["evidence_group_id"].startswith("PDFGRP-")


def test_biz2x_pdf_v2_builds_human_style_rows_and_acceptance():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装修工程量清单",
            row_no=3,
            section="楼地面工程",
            seq="1",
            item_code="",
            item_name="瓷砖地面 CT-01",
            feature="600x600地砖",
            unit="㎡",
        )
    ]

    report = build_pdf_v2_takeoff_report(_pdf_direct_report(), answer_rows=answer_rows, style_prompt_text="人工清单 材料编号 拆分")

    assert report["summary"]["evidence_count"] == 2
    assert report["summary"]["human_style_row_count"] == 2
    assert report["summary"]["standard_mapped_count"] >= 1
    assert report["summary"]["three_field_candidate_count"] == 2
    assert report["summary"]["three_field_gap_count"] >= 0
    assert report["stage_results"][0]["stage"] == "S1_pdf_render_and_tile"
    assert report["stage_results"][-1]["status"] == "deferred"
    assert any(row["item_name"] == "瓷砖地面 CT-01" for row in report["human_style_rows"])


def test_biz2x_pdf_v2_enriches_common_decoration_features_for_three_field_acceptance():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "text": "窗帘盒",
                    "item_hint": "窗帘盒",
                    "spec_or_method": "窗帘盒",
                    "suggested_unit": "m",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c03",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "text": "铝扣板",
                    "item_hint": "铝扣板吊顶",
                    "spec_or_method": "铝扣板",
                    "suggested_unit": "㎡",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装饰工程量清单",
            row_no=1,
            section="天棚工程",
            seq="1",
            item_code="",
            item_name="窗帘盒",
            feature="1、尺寸：200mm宽 2、15厚阻燃板基层，防腐、防蛀处理，面贴单层9.5mm石膏板；",
            unit="m",
        ),
        ThreeFieldAnswerRow(
            sheet_name="装饰工程量清单",
            row_no=2,
            section="天棚工程",
            seq="2",
            item_code="",
            item_name="铝扣板吊顶",
            feature="1、区域：厨房 2、专用镀锌轻钢龙骨，主龙骨间距800以内； 3、600*600铝扣板安装",
            unit="㎡",
        ),
    ]

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=answer_rows, style_prompt_text="人工清单 项目特征")

    assert takeoff["summary"]["three_field_matched_count"] == 2
    assert {row["status"] for row in takeoff["three_field_acceptance_report"]["comparison_rows"]} == {
        "matched_three_fields"
    }
    features = "\n".join(row["feature"] for row in takeoff["human_style_rows"])
    assert "15厚阻燃板基层" in features
    assert "600*600铝扣板安装" in features


def test_biz2x_pdf_v2_prefers_switch_when_spec_overrides_socket_hint():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "开关",
                    "item_hint": "插座安装",
                    "spec_or_method": "开关",
                    "suggested_unit": "个",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c03",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "灯具",
                    "item_hint": "灯具安装",
                    "spec_or_method": "灯具",
                    "suggested_unit": "套",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="电气工程量清单",
            row_no=1,
            section="电气部分",
            seq="1",
            item_code="",
            item_name="开关安装",
            feature="1、名称、型号:双联单控开关 2、型号:86型10A",
            unit="个",
        ),
        ThreeFieldAnswerRow(
            sheet_name="电气工程量清单",
            row_no=2,
            section="电气部分",
            seq="2",
            item_code="",
            item_name="灯具安装",
            feature="1、名称：LED 筒灯 2、规格、型号：5W/6W/8W/10W/3000K色温",
            unit="套",
        ),
    ]

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=answer_rows, style_prompt_text="人工清单 电气")

    assert [row["item_name"] for row in takeoff["human_style_rows"]] == ["开关安装", "灯具安装"]
    assert takeoff["summary"]["three_field_matched_count"] == 1
    status_by_name = {
        row["answer_item_name"]: row["status"]
        for row in takeoff["three_field_acceptance_report"]["comparison_rows"]
    }
    assert status_by_name["开关安装"] == "matched_three_fields"
    assert status_by_name["灯具安装"] == "matched_name_unit_feature_review"
    lamp_gap = next(row for row in takeoff["three_field_gap_rows"] if row["answer_item_name"] == "灯具安装")
    assert lamp_gap["gap_type"] == "matched_name_unit_feature_review"
    assert "补细分特征证据" in lamp_gap["suggested_next_action"]
    features = "\n".join(row["feature"] for row in takeoff["human_style_rows"])
    assert "86型10A开关" in features
    assert "LED灯具" in features


def test_biz2x_pdf_v2_prefers_explicit_suggested_unit_over_keyword_fallback():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "detail17",
                    "evidence_role": "node_detail_material_evidence",
                    "discipline": "decoration",
                    "text": "PM01 白色人造石",
                    "item_hint": "人造石窗台石PM-01",
                    "spec_or_method": "PM01 白色人造石",
                    "suggested_unit": "㎡",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=[], style_prompt_text="人工清单 项目特征")

    assert takeoff["human_style_rows"][0]["unit"] == "㎡"


def test_biz2x_pdf_v2_normalizes_exhaust_fan_and_water_heater():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_legend",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "D-顶面设备（空调、新风、换气扇）",
                    "item_hint": "白色/250",
                    "spec_or_method": "",
                    "suggested_unit": "",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_plan",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "电热水器电源",
                    "item_hint": "电热水器",
                    "spec_or_method": "电热水器电源",
                    "suggested_unit": "",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="机电工程量清单",
            row_no=109,
            section="二、电气部分",
            seq="109",
            item_code="",
            item_name="排气扇安装",
            feature="1、名称、型号:排气扇 2、型号:",
            unit="个",
        ),
        ThreeFieldAnswerRow(
            sheet_name="机电工程量清单",
            row_no=110,
            section="二、电气部分",
            seq="110",
            item_code="",
            item_name="电热水器供货及安装",
            feature="1、名称、型号:电热水器 2、型号:",
            unit="套",
        ),
    ]

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=answer_rows, style_prompt_text="人工清单 电气设备")

    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}
    assert by_name["排气扇安装"]["unit"] == "个"
    assert by_name["电热水器供货及安装"]["unit"] == "套"
    features = "\n".join(row["feature"] for row in takeoff["human_style_rows"])
    assert "排气扇安装，型号按图纸" in features
    assert "电热水器供货及安装，型号按图纸" in features
    statuses = {
        row["answer_item_name"]: row["status"]
        for row in takeoff["three_field_acceptance_report"]["comparison_rows"]
    }
    assert statuses["排气扇安装"] == "matched_three_fields"
    assert statuses["电热水器供货及安装"] == "matched_three_fields"


def test_biz2x_pdf_v2_keeps_shower_partition_and_stainless_glass_door_names():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "elevation_shower",
                    "evidence_role": "finish_elevation_callout_evidence",
                    "discipline": "decoration",
                    "item_hint": "淋浴隔断",
                    "spec_or_method": "玻璃隔断/玻璃门，GL01 10mm钢化玻璃",
                    "suggested_unit": "㎡",
                    "text": "淋浴区域可见玻璃隔断/玻璃门图形及 GL01 玻璃相关标注",
                    "confidence": 0.7,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "elevation_door",
                    "evidence_role": "finish_elevation_callout_evidence",
                    "discipline": "decoration",
                    "item_hint": "成品玻璃门",
                    "material_codes": ["GL01", "MT01"],
                    "spec_or_method": "GL01 10mm钢化玻璃；MT01 黑色拉丝不锈钢",
                    "suggested_unit": "樘",
                    "text": "门洞及玻璃区域可见 GL01 10mm钢化玻璃、MT01 黑色拉丝不锈钢标注",
                    "confidence": 0.72,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "elevation_line",
                    "evidence_role": "finish_elevation_callout_evidence",
                    "discipline": "decoration",
                    "item_hint": "金属线条",
                    "material_codes": ["MT01"],
                    "spec_or_method": "MT01 黑色拉丝不锈钢线条",
                    "suggested_unit": "m",
                    "text": "墙面收口和分格线位置多处可见 MT01 金属材料索引",
                    "confidence": 0.66,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "ceiling_finish",
                    "evidence_role": "finish_ceiling_callout_evidence",
                    "discipline": "decoration",
                    "item_hint": "防水石膏板吊顶",
                    "spec_or_method": "防潮无机涂料饰面",
                    "suggested_unit": "㎡",
                    "text": "顶面红色引线可见：防水石膏板吊顶，防潮无机涂料饰面",
                    "confidence": 0.72,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单 装饰")
    by_id = {row["evidence_ids"]: row for row in takeoff["human_style_rows"]}

    assert by_id["PDFEV-000001"]["item_name"] == "淋浴隔断 GL01"
    assert by_id["PDFEV-000001"]["unit"] == "㎡"
    assert by_id["PDFEV-000002"]["item_name"] == "成品不锈钢玻璃门 GL01"
    assert by_id["PDFEV-000002"]["unit"] == "樘"
    assert by_id["PDFEV-000003"]["item_name"] == "金属线条 MT01"
    assert by_id["PDFEV-000003"]["unit"] == "m"
    assert by_id["PDFEV-000004"]["item_name"] == "防潮无机涂料"
    assert by_id["PDFEV-000004"]["unit"] == "㎡"
    assert "防水腻子" in by_id["PDFEV-000004"]["feature"]
    assert by_id["PDFEV-000004"]["standard_item_name"] == "天棚喷刷涂料"


def test_biz2x_pdf_v2_preserves_round_light_trough_and_tile_column_names():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "ceiling_round_trough",
                    "evidence_role": "finish_ceiling_callout_evidence",
                    "discipline": "decoration",
                    "item_hint": "圆形灯槽",
                    "spec_or_method": "圆形灯槽，圆形造型区域",
                    "suggested_unit": "m",
                    "text": "顶面图可见圆形灯槽/圆形造型区域及节点索引",
                    "confidence": 0.62,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "elevation_tile_column",
                    "evidence_role": "finish_elevation_callout_evidence",
                    "discipline": "decoration",
                    "item_hint": "瓷砖包柱",
                    "material_codes": ["CT04"],
                    "spec_or_method": "CT04 墙面砖作美缝，柱面/包柱区域",
                    "suggested_unit": "㎡",
                    "text": "立面图柱面/包柱附近可见 CT04 墙面砖作美缝标注",
                    "confidence": 0.66,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单 装饰")
    by_id = {row["evidence_ids"]: row for row in takeoff["human_style_rows"]}

    assert by_id["PDFEV-000001"]["item_name"] == "圆形灯槽"
    assert by_id["PDFEV-000001"]["unit"] == "m"
    assert by_id["PDFEV-000002"]["item_name"] == "瓷砖包柱 CT04"
    assert by_id["PDFEV-000002"]["unit"] == "㎡"


def test_biz2x_pdf_v2_gap_rows_include_candidate_trace_fields():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "灯具",
                    "item_hint": "灯具安装",
                    "spec_or_method": "灯具",
                    "suggested_unit": "套",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                }
            ]
        },
    }
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="电气工程量清单",
            row_no=2,
            section="电气部分",
            seq="2",
            item_code="",
            item_name="灯具安装",
            feature="1、名称：LED 筒灯 2、规格、型号：5W/6W/8W/10W/3000K色温",
            unit="套",
        )
    ]

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=answer_rows, style_prompt_text="人工清单 电气")

    assert takeoff["summary"]["three_field_gap_count"] == 1
    gap = takeoff["three_field_gap_rows"][0]
    assert gap["candidate_row_no"] == 1
    assert gap["candidate_source"] == "04.pdf"
    assert gap["candidate_source_files"] == "04.pdf"
    assert gap["candidate_evidence_ids"] == "PDFEV-000001"
    assert gap["candidate_standard_item_code"]
    assert gap["gap_priority"] == "P1_specificity"


def test_biz2x_pdf_v2_keeps_door_demolition_as_set_unit():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "construction_note",
                    "discipline": "decoration",
                    "text": "拆除不锈钢玻璃门，含门套、门扇及五金拆除并清运",
                    "item_hint": "拆除不锈钢玻璃门",
                    "spec_or_method": "不锈钢地弹门，含门套、门扇及五金",
                    "suggested_unit": "㎡",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=[], style_prompt_text="")
    row = takeoff["human_style_rows"][0]

    assert row["item_name"] == "拆除不锈钢玻璃门"
    assert row["unit"] == "套"
    assert "门套、门扇及五金" in row["feature"]


def test_biz2x_pdf_v2_names_plumbing_fixtures_as_supply_install_items():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_whole",
                    "evidence_role": "plumbing_spec",
                    "discipline": "管道工程",
                    "text": "DN25铜阀门",
                    "item_hint": "阀门",
                    "spec_or_method": "DN25 铜质阀门",
                    "suggested_unit": "",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_whole",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "text": "台盆",
                    "item_hint": "台盆",
                    "spec_or_method": "台盆及下水五金",
                    "suggested_unit": "",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_whole",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "text": "冷热水龙头",
                    "item_hint": "龙头",
                    "spec_or_method": "冷热水龙头",
                    "suggested_unit": "",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_whole",
                    "evidence_role": "卫生设施",
                    "discipline": "管道工程",
                    "text": "图中可见坐便器平面符号",
                    "item_hint": "坐便器",
                    "spec_or_method": "",
                    "suggested_unit": "个",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, answer_rows=[], style_prompt_text="")
    rows_by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert "金属阀门供货及安装 DN25" in rows_by_name
    assert rows_by_name["金属阀门供货及安装 DN25"]["unit"] == "个"
    assert "台盆供货及安装" in rows_by_name
    assert rows_by_name["台盆供货及安装"]["unit"] == "套"
    assert "冷热水龙头供货及安装" in rows_by_name
    assert rows_by_name["冷热水龙头供货及安装"]["unit"] == "套"
    assert "马桶供货及安装" in rows_by_name
    assert rows_by_name["马桶供货及安装"]["unit"] == "套"
    assert "阀门供货及安装" in rows_by_name["金属阀门供货及安装 DN25"]["feature"]
    assert "台盆供货及安装" in rows_by_name["台盆供货及安装"]["feature"]


def test_biz2x_pdf_v2_writes_outputs(tmp_path: Path):
    report = build_pdf_v2_takeoff_report(_pdf_direct_report(), style_prompt_text="人工清单")
    outputs = write_pdf_v2_takeoff_outputs(report, tmp_path, stem="pdf_v2")

    assert set(outputs) == {
        "json",
        "markdown",
        "evidence_csv",
        "normalized_evidence_csv",
        "human_style_csv",
        "three_field_gap_csv",
        "xlsx",
    }
    assert json.loads((tmp_path / "pdf_v2.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-pdf-v2-evidence-driven-takeoff"
    assert (tmp_path / "pdf_v2_三字段缺口复核.csv").exists()
    assert (tmp_path / "pdf_v2.xlsx").read_bytes().startswith(b"PK")


def test_biz2x_pdf_v2_accepts_visual_evidence_pipeline_rows():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": "餐厅 CT-01 600x600 地砖",
                    "item_hint": "块料楼地面",
                    "space": "餐厅",
                    "material_codes": ["CT-01"],
                    "spec_or_method": "600x600 地砖",
                    "suggested_unit": "㎡",
                    "confidence": 0.88,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单 材料编号")

    assert takeoff["summary"]["evidence_count"] == 1
    assert takeoff["evidence_rows"][0]["evidence_id"] == "PDFEV-000001"
    assert takeoff["human_style_rows"][0]["item_name"] == "瓷砖地面 CT-01"


def test_biz2x_pdf_v2_skips_context_only_room_name_rows():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "room_name",
                    "text": "餐厅",
                    "confidence": 0.78,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c03",
                    "evidence_role": "electrical_spec",
                    "discipline": "electrical",
                    "text": "LED筒灯 12W",
                    "item_hint": "LED筒灯",
                    "spec_or_method": "12W",
                    "suggested_unit": "套",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    assert takeoff["summary"]["evidence_count"] == 2
    assert takeoff["summary"]["human_style_row_count"] == 1
    assert takeoff["human_style_rows"][0]["item_name"] == "LED筒灯"
    assert takeoff["human_style_rows"][0]["unit"] == "套"


def test_biz2x_pdf_v2_uses_visible_text_before_reason_and_dedupes_missing_spec():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": "墙布",
                    "item_hint": "墙面装饰板",
                    "spec_or_method": "墙布",
                    "suggested_unit": "㎡",
                    "reason": "清晰可见",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c01",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": "墙布",
                    "item_hint": "墙面装饰板",
                    "spec_or_method": "",
                    "suggested_unit": "㎡",
                    "reason": "清晰可见",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    assert takeoff["summary"]["normalized_evidence_count"] == 1
    assert takeoff["summary"]["human_style_row_count"] == 1
    assert "墙布" in takeoff["human_style_rows"][0]["feature"]
    assert "清晰可见" not in takeoff["human_style_rows"][0]["feature"]


def test_biz2x_pdf_v2_classifies_ceiling_boards_and_lamp_unit_by_human_list_style():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": "防水石膏板",
                    "item_hint": "墙面装饰板",
                    "spec_or_method": "防水石膏板",
                    "suggested_unit": "㎡",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "灯具",
                    "item_hint": "灯具安装",
                    "spec_or_method": "灯具",
                    "suggested_unit": "个",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}
    assert by_name["轻钢龙骨防水石膏板平级吊顶"]["division"] == "天棚工程"
    assert by_name["轻钢龙骨防水石膏板平级吊顶"]["unit"] == "㎡"
    assert by_name["轻钢龙骨防水石膏板平级吊顶"]["standard_item_name"] == "平面吊顶 | 天棚"
    assert by_name["灯具安装"]["unit"] == "套"
    assert by_name["灯具安装"]["standard_item_name"] == "普通灯具"


def test_biz2x_pdf_v2_standard_mapping_filters_obvious_wrong_installation_matches():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": "石材",
                    "item_hint": "墙面装饰板",
                    "spec_or_method": "石材",
                    "suggested_unit": "㎡",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "evidence_role": "plumbing_spec",
                    "discipline": "管道工程",
                    "text": "DN25",
                    "item_hint": "给水管安装",
                    "spec_or_method": "DN25",
                    "suggested_unit": "m",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}
    assert by_name["墙面石材湿贴"]["standard_item_name"] == "石材墙、柱面"
    assert by_name["给水管 DN25"]["standard_item_name"] == ""
    assert by_name["给水管 DN25"]["standard_item_code"] == ""


def test_biz2x_pdf_v2_filters_single_letter_visual_symbols():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "T",
                    "confidence": 0.0,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c02",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "text": "灯具",
                    "item_hint": "灯具安装",
                    "suggested_unit": "个",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    assert takeoff["summary"]["evidence_count"] == 1
    assert takeoff["human_style_rows"][0]["item_name"] == "灯具安装"


def test_biz2x_pdf_v2_merges_multiple_evidence_reports_for_recall():
    first_report = {
        "ok": True,
        "phase": "first-pass",
        "summary": {"pdf_file_count": 2, "pdf_page_count": 18, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "A1",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "electrical_spec",
                    "discipline": "electrical",
                    "item_hint": "LED light",
                    "spec_or_method": "12W",
                    "suggested_unit": "set",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ]
        },
    }
    second_report = {
        "ok": True,
        "phase": "second-pass",
        "summary": {"pdf_file_count": 2, "pdf_page_count": 18, "pdf_render_status": "rendered"},
        "evidence_rows": [
            {
                "evidence_id": "B1",
                "source_file": "04.pdf",
                "page": 1,
                "tile_id": "p001_g03_r02_c02",
                "evidence_type": "plumbing",
                "discipline": "plumbing",
                "raw_item_name": "Pipe DN25",
                "spec_or_method": "DN25",
                "suggested_unit": "m",
                "confidence": 0.7,
                "needs_review": True,
            }
        ],
    }

    merged = merge_pdf_evidence_reports(
        [first_report, second_report],
        source_paths=["first.json", "second.json"],
    )
    takeoff = build_pdf_v2_takeoff_report(merged, style_prompt_text="human listing")

    assert merged["summary"]["ensemble_report_count"] == 2
    assert merged["summary"]["ensemble_evidence_input_count"] == 2
    assert {row["evidence_id"] for row in takeoff["evidence_rows"]} == {"R01-A1", "R02-B1"}
    assert takeoff["summary"]["evidence_count"] == 2
    assert takeoff["summary"]["human_style_row_count"] == 2
    assert {row["item_name"] for row in takeoff["human_style_rows"]} == {"LED light", "Pipe DN25"}


def test_biz2x_pdf_v2_filters_prompt_echo_code_only_and_terminal_symbols():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "item_hint": "可能形成的清单项目提示，不确定则为空",
                    "spec_or_method": "规格、材料、做法、安装方式或构造说明",
                    "suggested_unit": "㎡/m/个/套/台/樘；不确定则为空",
                    "text": "识别到的原文",
                    "confidence": 0.2,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "item_hint": "CT-02",
                    "material_codes": ["CT-02"],
                    "spec_or_method": "CT-02",
                    "suggested_unit": "㎡",
                    "text": "CT-02",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "02.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c03",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "item_hint": "接地端子",
                    "spec_or_method": "T",
                    "suggested_unit": "个",
                    "text": "T",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c01",
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "item_hint": "墙面装饰板",
                    "spec_or_method": "墙布",
                    "suggested_unit": "㎡，",
                    "text": "墙布",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000005",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r02_c01",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "item_hint": "A",
                    "spec_or_method": "A",
                    "suggested_unit": "台",
                    "text": "A",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    assert takeoff["summary"]["evidence_count"] == 1
    assert takeoff["summary"]["human_style_row_count"] == 1
    assert takeoff["human_style_rows"][0]["item_name"] == "墙布墙面"
    assert takeoff["human_style_rows"][0]["unit"] == "㎡"


def test_biz2x_pdf_v2_maps_plastic_pipe_but_keeps_unknown_dn_unmapped():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "item_hint": "排水管",
                    "spec_or_method": "De50",
                    "suggested_unit": "m",
                    "text": "排水管 De50",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "item_hint": "给水管",
                    "spec_or_method": "DN25",
                    "suggested_unit": "m",
                    "text": "给水管 DN25",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["排水管 De50"]["standard_item_code"] == "031001008"
    assert by_name["排水管 De50"]["standard_item_name"] == "塑料管"
    assert by_name["给水管 DN25"]["standard_item_code"] == ""


def test_biz2x_pdf_v2_maps_cast_iron_drain_pipe_before_de_plastic_fallback():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-CASTIRON",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c03",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "item_hint": "排水管",
                    "spec_or_method": "材质：柔性铸铁管；规格、型号：De110；不锈钢卡箍连接",
                    "suggested_unit": "m",
                    "text": "排水管 柔性铸铁管 De110 不锈钢卡箍连接",
                    "confidence": 0.82,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    row = takeoff["human_style_rows"][0]
    assert row["standard_item_code"] == "031001001"
    assert row["standard_item_name"] == "铸铁管"


def test_biz2x_pdf_v2_maps_plumbing_mirror_and_paper_holder_to_fixture_items():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-MIRROR",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "fixture_node",
                    "evidence_role": "sanitary_fixture_or_accessory",
                    "discipline": "plumbing",
                    "item_hint": "镜面",
                    "spec_or_method": "",
                    "suggested_unit": "个",
                    "text": "图中右侧人物后方有镜面",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-PAPER",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "fixture_node",
                    "evidence_role": "sanitary_fixture_or_accessory",
                    "discipline": "plumbing",
                    "item_hint": "纸巾架",
                    "spec_or_method": "",
                    "suggested_unit": "个",
                    "text": "卫生间立面可见纸巾架",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["梳妆镜供货及安装"]["unit"] == "个"
    assert "梳妆镜供货及安装" in by_name["梳妆镜供货及安装"]["feature"]
    assert by_name["厕纸架供货及安装"]["unit"] == "个"
    assert "厕纸架供货及安装" in by_name["厕纸架供货及安装"]["feature"]
    assert by_name["厕纸架供货及安装"]["standard_item_code"] == "031003014"
    assert by_name["厕纸架供货及安装"]["standard_item_name"] == "给、排水附件"


def test_biz2x_pdf_v2_keeps_generic_faucet_feature_generic_without_hot_cold_evidence():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-FAUCET",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "fixture_node",
                    "evidence_role": "sanitary_fixture_or_accessory",
                    "discipline": "plumbing",
                    "item_hint": "水龙头",
                    "spec_or_method": "",
                    "suggested_unit": "套",
                    "text": "图中可见水龙头",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    row = takeoff["human_style_rows"][0]
    assert row["item_name"] == "龙头供货及安装"
    assert "龙头供货及安装" in row["feature"]
    assert "冷热水龙头供货及安装" not in row["feature"]


def test_biz2x_pdf_v2_keeps_single_cold_faucet_out_of_hot_cold_item_name():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-SINGLE-COLD",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "fixture_node",
                    "evidence_role": "sanitary_fixture_or_accessory",
                    "discipline": "plumbing",
                    "item_hint": "单冷水龙头",
                    "spec_or_method": "单冷",
                    "suggested_unit": "套",
                    "text": "图纸标注单冷水龙头",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    row = takeoff["human_style_rows"][0]
    assert row["item_name"] == "龙头供货及安装"
    assert "单冷" in row["feature"]
    assert "冷热水龙头供货及安装" not in row["feature"]


def test_biz2x_pdf_v2_maps_shower_head_to_shower_fixture_item():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-SHOWER-HEAD",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "fixture_node",
                    "evidence_role": "sanitary_fixture_or_accessory",
                    "discipline": "plumbing",
                    "item_hint": "淋浴喷头",
                    "spec_or_method": "",
                    "suggested_unit": "",
                    "text": "图中可见淋浴喷头的平面符号",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    row = takeoff["human_style_rows"][0]
    assert row["item_name"] == "淋浴花洒供货及安装"
    assert row["unit"] == "套"
    assert "淋浴花洒供货及安装" in row["feature"]


def test_biz2x_pdf_v2_does_not_map_demolition_to_unrelated_items():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "铲除",
                    "spec_or_method": "铲除原墙面",
                    "suggested_unit": "㎡",
                    "text": "铲除原墙面",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")

    assert takeoff["human_style_rows"][0]["item_name"] == "铲除"
    assert takeoff["human_style_rows"][0]["standard_item_code"] == ""


def test_biz2x_pdf_v2_maps_electrical_conduit_wire_and_cable_directly():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "electrical_spec",
                    "discipline": "electrical",
                    "item_hint": "电气配线",
                    "spec_or_method": "JDG20",
                    "suggested_unit": "m",
                    "text": "JDG20",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "evidence_role": "electrical_spec",
                    "discipline": "electrical",
                    "item_hint": "电气配线",
                    "spec_or_method": "WDZC-BYJ-2.5",
                    "suggested_unit": "m",
                    "text": "WDZC-BYJ-2.5",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c03",
                    "evidence_role": "electrical_spec",
                    "discipline": "electrical",
                    "item_hint": "电缆敷设",
                    "spec_or_method": "WDZC-YJY-5*10",
                    "suggested_unit": "m",
                    "text": "WDZC-YJY-5*10",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["电气配管 JDG20"]["standard_item_code"] == "030412001"
    assert by_name["电气配管 JDG20"]["standard_item_name"] == "配管"
    assert by_name["电气配线 WDZC-BYJ-2.5"]["standard_item_code"] == "030412004"
    assert by_name["电气配线 WDZC-BYJ-2.5"]["standard_item_name"] == "配线"
    assert by_name["电缆敷设 WDZC-YJY-5*10"]["standard_item_code"] == "030409001"
    assert by_name["电缆敷设 WDZC-YJY-5*10"]["standard_item_name"] == "电力电缆"


def test_biz2x_pdf_v2_maps_table_legend_fixture_and_box_items_directly():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "vision_pass": "table_legend",
                    "evidence_role": "equipment_schedule",
                    "discipline": "electrical",
                    "item_hint": "配电箱",
                    "spec_or_method": "AL1",
                    "suggested_unit": "台",
                    "text": "配电箱 AL1",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "vision_pass": "table_legend",
                    "evidence_role": "device_symbol",
                    "discipline": "electrical",
                    "item_hint": "LED灯带",
                    "spec_or_method": "24V",
                    "suggested_unit": "m",
                    "text": "LED灯带 24V",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c03",
                    "vision_pass": "table_legend",
                    "evidence_role": "equipment_schedule",
                    "discipline": "plumbing",
                    "item_hint": "地漏",
                    "spec_or_method": "DN50",
                    "suggested_unit": "个",
                    "text": "地漏 DN50",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c04",
                    "vision_pass": "table_legend",
                    "evidence_role": "equipment_schedule",
                    "discipline": "plumbing",
                    "item_hint": "台盆",
                    "spec_or_method": "陶瓷台盆",
                    "suggested_unit": "组",
                    "text": "台盆",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000005",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c05",
                    "vision_pass": "table_legend",
                    "evidence_role": "equipment_schedule",
                    "discipline": "plumbing",
                    "item_hint": "水表",
                    "spec_or_method": "DN25",
                    "suggested_unit": "组",
                    "text": "水表 DN25",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["配电箱 AL1"]["standard_item_code"] == "030402011"
    assert by_name["配电箱 AL1"]["standard_item_name"] == "成套配电箱"
    assert by_name["配电箱 AL1"]["unit"] == "套"
    assert by_name["LED灯带"]["standard_item_code"] == "030413002"
    assert by_name["LED灯带"]["standard_item_name"] == "装饰灯"
    assert by_name["地漏供货及安装"]["standard_item_code"] == "031003014"
    assert by_name["地漏供货及安装"]["standard_item_name"] == "给、排水附件"
    assert by_name["台盆供货及安装"]["standard_item_code"] == "031003003"
    assert by_name["台盆供货及安装"]["standard_item_name"] == "洗脸盆"
    assert by_name["水表供货及安装 DN25"]["standard_item_code"] == "031002011"
    assert by_name["水表供货及安装 DN25"]["standard_item_name"] == "水表"


def test_biz2x_pdf_v2_keeps_unknown_pipe_material_unmapped_but_maps_known_valves():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "item_hint": "排水管",
                    "spec_or_method": "DN50",
                    "suggested_unit": "m",
                    "text": "排水管 DN50",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "item_hint": "塑料阀门",
                    "spec_or_method": "PPR DN25",
                    "suggested_unit": "个",
                    "text": "PPR塑料阀门 DN25",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "04.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c03",
                    "evidence_role": "plumbing_spec",
                    "discipline": "plumbing",
                    "item_hint": "金属阀门",
                    "spec_or_method": "不锈钢 DN25",
                    "suggested_unit": "个",
                    "text": "不锈钢金属阀门 DN25",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["排水管 DN50"]["standard_item_code"] == ""
    assert by_name["塑料阀门供货及安装 DN25"]["standard_item_code"] == "031002003"
    assert by_name["塑料阀门供货及安装 DN25"]["standard_item_name"] == "塑料阀门"
    assert by_name["金属阀门供货及安装 DN25"]["standard_item_code"] == "031002001"
    assert by_name["金属阀门供货及安装 DN25"]["standard_item_name"] == "金属阀门"


def test_biz2x_pdf_v2_keeps_node_detail_decoration_items_out_of_electrical_and_door_buckets():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c01",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "灯槽",
                    "spec_or_method": "轻钢龙骨灯槽做法",
                    "suggested_unit": "㎡",
                    "text": "灯槽",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c02",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "窗帘盒",
                    "spec_or_method": "窗帘盒",
                    "suggested_unit": "㎡",
                    "text": "窗帘盒",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c03",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "门槛石",
                    "spec_or_method": "石材门槛石",
                    "suggested_unit": "㎡",
                    "text": "门槛石",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "p001_g03_r01_c04",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "踢脚线",
                    "spec_or_method": "踢脚线",
                    "suggested_unit": "m",
                    "text": "踢脚线",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["灯槽"]["division"] == "天棚工程"
    assert by_name["灯槽"]["unit"] == "m"
    assert by_name["灯槽"]["standard_item_name"] != "干燥机"
    assert by_name["窗帘盒"]["division"] == "天棚工程"
    assert by_name["窗帘盒"]["unit"] == "m"
    assert by_name["窗帘盒"]["standard_item_code"] == "010810002"
    assert by_name["门槛石"]["division"] == "楼地面工程"
    assert by_name["门槛石"]["standard_item_code"] == "011102001"
    assert by_name["踢脚线"]["standard_item_code"] == "011105001"


def test_biz2x_pdf_v2_keeps_stone_floor_and_manual_detail_items_split():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "floor_st02",
                    "vision_pass": "finish_schedule",
                    "evidence_role": "finish_callout",
                    "discipline": "decoration",
                    "item_hint": "石材地面ST-02",
                    "material_codes": ["ST02"],
                    "spec_or_method": "ST02 深咖大理石",
                    "suggested_unit": "㎡",
                    "text": "03地面平面图可见 ST02 深咖大理石标注",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "node_dam",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "人造石挡水条",
                    "spec_or_method": "60宽人造石挡水条",
                    "suggested_unit": "m",
                    "text": "节点详图可见 60 宽挡水条构造",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "node_partition_base",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "隔断底座",
                    "spec_or_method": "卡座区隔断底座，钢通结构+15厚阻燃夹板基层，古堡灰大理石ST-1底座，1470*240*200mm",
                    "suggested_unit": "m",
                    "text": "卡座区节点可见隔断底座尺寸 1470*240*200mm",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "glass_column",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "钢化玻璃造型柱",
                    "spec_or_method": "10mm钢化玻璃造型柱，200*200*2366mm，4套",
                    "suggested_unit": "套",
                    "text": "卡座区立面可见 10mm 钢化玻璃造型柱",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["石材地面 ST02"]["division"] == "楼地面工程"
    assert by_name["石材地面 ST02"]["unit"] == "㎡"
    assert by_name["人造石挡水条"]["unit"] == "m"
    assert "60宽人造石挡水条" in by_name["人造石挡水条"]["feature"]
    assert by_name["隔断底座"]["unit"] == "m"
    assert "1470*240*200mm" in by_name["隔断底座"]["feature"]
    assert by_name["钢化玻璃造型柱"]["unit"] == "套"
    assert "200*200*2366mm" in by_name["钢化玻璃造型柱"]["feature"]


def test_biz2x_pdf_v2_preserves_masonry_coating_and_measure_items():
    report = {
        "summary": {"pdf_file_count": 1, "pdf_page_count": 1, "pdf_render_status": "rendered"},
        "visual_evidence_report": {
            "evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "brick_platform",
                    "vision_pass": "node_detail",
                    "evidence_role": "construction_method",
                    "discipline": "decoration",
                    "item_hint": "零星砌筑",
                    "spec_or_method": "过厅砖砌地台，抬高240mm",
                    "suggested_unit": "m³",
                    "text": "节点详图可见砖砌地台抬高 240mm",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000002",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "partition_wall",
                    "vision_pass": "legend",
                    "evidence_role": "legend",
                    "discipline": "decoration",
                    "item_hint": "砖砌隔墙",
                    "spec_or_method": "100mm宽，新建蒸压加气砼砌块墙",
                    "suggested_unit": "m³",
                    "text": "图例可见 N-新增隔墙，节点说明为新建蒸压加气砼砌块墙",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000003",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "black_coating",
                    "vision_pass": "finish_schedule",
                    "evidence_role": "finish_callout",
                    "discipline": "decoration",
                    "item_hint": "黑色防潮无机涂料",
                    "spec_or_method": "防水腻子，面油黑色防潮无机涂料三遍",
                    "suggested_unit": "㎡",
                    "text": "材料标注可见黑色防潮无机涂料",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000004",
                    "source_file": "03.pdf",
                    "page": 1,
                    "tile_id": "white_coating",
                    "vision_pass": "finish_schedule",
                    "evidence_role": "finish_callout",
                    "discipline": "decoration",
                    "item_hint": "白色无机涂料",
                    "spec_or_method": "防水腻子，面油白色无机涂料三遍",
                    "suggested_unit": "㎡",
                    "text": "材料标注可见白色无机涂料",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
                {
                    "evidence_id": "PDFEV-000005",
                    "source_file": "01.pdf",
                    "page": 1,
                    "tile_id": "measure_cleaning",
                    "vision_pass": "style_prompt_measure",
                    "evidence_role": "measure_item",
                    "discipline": "decoration",
                    "item_hint": "开荒精保洁",
                    "spec_or_method": "开荒精保洁",
                    "suggested_unit": "㎡",
                    "text": "人工清单列项规则保留开荒精保洁措施项",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                },
            ]
        },
    }

    takeoff = build_pdf_v2_takeoff_report(report, style_prompt_text="人工清单 措施项目")
    by_name = {row["item_name"]: row for row in takeoff["human_style_rows"]}

    assert by_name["零星砌筑"]["unit"] == "m³"
    assert "抬高240mm" in by_name["零星砌筑"]["feature"]
    assert by_name["砖砌隔墙"]["unit"] == "m³"
    assert "100mm宽" in by_name["砖砌隔墙"]["feature"]
    assert by_name["黑色防潮无机涂料"]["unit"] == "㎡"
    assert "黑色防潮无机涂料三遍" in by_name["黑色防潮无机涂料"]["feature"]
    assert by_name["白色无机涂料"]["unit"] == "㎡"
    assert "白色无机涂料三遍" in by_name["白色无机涂料"]["feature"]
    assert by_name["开荒精保洁"]["unit"] == "㎡"
    assert "规则依据" in by_name["开荒精保洁"]["feature"]
    assert "图纸证据" not in by_name["开荒精保洁"]["feature"]
    assert "措施范围" in by_name["开荒精保洁"]["feature"]
    assert by_name["开荒精保洁"]["standard_item_code"] == ""
    assert any(row["evidence_role"] == "measure_item" for row in takeoff["normalized_evidence_rows"])
