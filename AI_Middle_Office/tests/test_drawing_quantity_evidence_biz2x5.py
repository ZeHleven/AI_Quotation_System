from __future__ import annotations

import json

from app.services.drawing_quantity_evidence import (
    build_quantity_candidate_csv_rows,
    build_quantity_evidence_csv_rows,
    extract_quantity_evidence_for_standard_matches,
    write_quantity_evidence_outputs,
)


def _standard_match_report() -> dict[str, object]:
    return {
        "phase": "BIZ-2x-4-standard-match-preview",
        "summary": {"standard_candidate_count": 3},
        "standard_item_candidates": [
            {
                "candidate_key": "BIZ2x4-0001",
                "source_file": "03.dxf",
                "source_row_number": 10,
                "source_name": "地砖地面做法",
                "source_spec_or_method": "玻化砖地面",
                "evidence_text": "地砖地面做法；玻化砖地面",
                "standard_item_code": "011102003",
                "standard_item_name": "块料楼地面",
                "chapter_name": "测试",
                "unit_options": ["m2", "㎡"],
                "quantity_rule_text": "按设计图示尺寸以面积计算",
                "quantity_formula_type": "area",
                "quantity_required_evidence": ["设计图示尺寸", "面积"],
            },
            {
                "candidate_key": "BIZ2x4-0002",
                "source_file": "03.dxf",
                "source_row_number": 12,
                "source_name": "轻钢龙骨吊顶",
                "source_spec_or_method": "600x600吊顶龙骨",
                "evidence_text": "轻钢龙骨吊顶；600x600吊顶龙骨",
                "standard_item_code": "011302001",
                "standard_item_name": "平面吊顶 天棚",
                "chapter_name": "测试",
                "unit_options": ["m2", "㎡"],
                "quantity_rule_text": "按设计图示尺寸以水平投影面积计算",
                "quantity_formula_type": "area",
                "quantity_required_evidence": ["设计图示尺寸", "水平投影面积"],
            },
            {
                "candidate_key": "BIZ2x4-0003",
                "source_file": "03.dxf",
                "source_row_number": 14,
                "source_name": "窗帘盒做法",
                "source_spec_or_method": "阻燃板基层",
                "evidence_text": "窗帘盒做法；阻燃板基层",
                "standard_item_code": "010810002",
                "standard_item_name": "窗帘盒",
                "chapter_name": "测试",
                "unit_options": ["m"],
                "quantity_rule_text": "按设计图示尺寸以长度计算",
                "quantity_formula_type": "length",
                "quantity_required_evidence": ["设计图示尺寸", "长度"],
            },
        ],
    }


def _text_records() -> list[dict[str, object]]:
    return [
        {
            "source_file": "03.dxf",
            "entity_type": "TEXT",
            "text": "地砖地面铺贴面积12.5㎡",
            "layer": "地面铺装",
            "layout": "",
            "block_name": "",
            "x": 10,
            "y": 20,
            "line_number": 100,
            "role_tags": ["plan"],
        },
        {
            "source_file": "03.dxf",
            "entity_type": "TEXT",
            "text": "轻钢龙骨吊顶600x600",
            "layer": "天花",
            "layout": "",
            "block_name": "",
            "x": 11,
            "y": 21,
            "line_number": 101,
            "role_tags": ["plan"],
        },
        {
            "source_file": "03.dxf",
            "entity_type": "TEXT",
            "text": "窗帘盒长度8.4m",
            "layer": "窗帘盒",
            "layout": "",
            "block_name": "",
            "x": 12,
            "y": 22,
            "line_number": 102,
            "role_tags": ["detail"],
        },
    ]


def test_biz2x5_extracts_direct_and_partial_quantity_evidence():
    report = extract_quantity_evidence_for_standard_matches(_standard_match_report(), _text_records())

    rows = {row["candidate_key"]: row for row in report["quantity_candidates"]}
    assert rows["BIZ2x4-0001"]["quantity_status"] == "direct_quantity_candidate_needs_manual_review"
    assert rows["BIZ2x4-0001"]["suggested_quantity"] == "12.5"
    assert rows["BIZ2x4-0001"]["quantity_can_be_final_without_manual_review"] is False

    assert rows["BIZ2x4-0002"]["quantity_status"] == "partial_quantity_evidence_needs_manual_measurement"
    assert rows["BIZ2x4-0002"]["suggested_quantity"] == ""

    assert rows["BIZ2x4-0003"]["quantity_status"] == "direct_quantity_candidate_needs_manual_review"
    assert rows["BIZ2x4-0003"]["suggested_quantity"] == "8.4"
    assert report["summary"]["quantity_ready_without_manual_review_count"] == 0


def test_biz2x5_writes_quantity_evidence_outputs(tmp_path):
    report = extract_quantity_evidence_for_standard_matches(_standard_match_report(), _text_records())

    outputs = write_quantity_evidence_outputs(report, tmp_path, stem="quantity_evidence")
    candidate_rows = build_quantity_candidate_csv_rows(report)
    evidence_rows = build_quantity_evidence_csv_rows(report)

    assert set(outputs) == {"json", "markdown", "quantity_candidate_csv", "quantity_evidence_csv"}
    assert candidate_rows
    assert evidence_rows
    assert json.loads((tmp_path / "quantity_evidence.json").read_text(encoding="utf-8"))["ok"] is True
    assert (tmp_path / "quantity_evidence_工程量候选判断.csv").read_text(encoding="utf-8-sig").startswith("候选编号")
