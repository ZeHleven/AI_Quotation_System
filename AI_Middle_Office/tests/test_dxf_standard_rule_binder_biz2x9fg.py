from __future__ import annotations

import json

from app.services.dxf_standard_rule_binder import (
    build_standard_rule_binding_report,
    build_trace_csv_rows,
    write_standard_rule_binding_outputs,
)
from app.services.quantity_standard_library import QuantityStandardLibrary


def _library() -> QuantityStandardLibrary:
    raw = {
        "version": "biz2x-gbt50854-2024-standard-v0",
        "standard": {
            "code": "GBT50854-2024",
            "name": "房屋建筑与装饰工程工程量计算标准",
            "source_text_status": "word_verified",
            "strict_rules": [],
        },
        "items": [
            _item("011302001", "平面吊顶 | 天棚", "area", ["m²", "㎡"], "按设计图示尺寸以水平投影面积计算。"),
            _item("011404002", "天棚喷刷涂料", "expanded_area", ["m²", "㎡"], "按设计图示尺寸以展开面积计算。"),
            _item("010801001", "木质门", "area", ["m²", "㎡"], "按设计图示洞口尺寸以面积计算。"),
            _item("011502001", "成品装饰 | 线条", "length", ["m", "米"], "按设计图示尺寸以长度计算。"),
        ],
        "out_of_scope_policy": {},
    }
    return QuantityStandardLibrary.from_dict(raw, source_path="memory")


def _item(code: str, name: str, formula_type: str, units: list[str], rule: str) -> dict[str, object]:
    return {
        "item_code": code,
        "item_name": name,
        "chapter_name": "样例章节",
        "status": "active",
        "verification_status": "verified_against_standard",
        "feature_fields": [{"name": "材料品种", "required": True, "order": 1}],
        "unit_options": units,
        "quantity_rule": {
            "rule_text": rule,
            "formula_type": formula_type,
            "rule_status": "verified_against_standard",
            "required_evidence": ["图纸几何"],
        },
        "drawing_evidence_requirements": ["图纸几何"],
        "keywords": [name],
        "exclusion_keywords": [],
        "source_note": "测试标准条目",
    }


def _quantity_report() -> dict[str, object]:
    return {
        "ok": True,
        "summary": {},
        "suggestions": [
            {
                "suggestion_key": "S-area",
                "source_key": "sample|面积候选|D-顶面造型轮廓|",
                "source_file": "sample.dxf",
                "layer": "D-顶面造型轮廓",
                "block_name": "",
                "business_hint": "天棚/吊顶面积候选",
                "quantity_kind": "area",
                "suggestion_status": "suggestion_ready_for_manual_review",
                "suggested_quantity": 12.5,
                "suggested_unit": "㎡",
                "formula": "sum(CAD_area_mm2) * area_to_square_meter_factor",
                "calculation_trace": {"source_key": "sample|面积候选|D-顶面造型轮廓|"},
            },
            {
                "suggestion_key": "S-socket",
                "source_key": "sample|数量候选|C-平面插座|P-普通插座",
                "source_file": "sample.dxf",
                "layer": "C-平面插座",
                "block_name": "P-普通插座",
                "business_hint": "插座数量候选",
                "quantity_kind": "count",
                "suggestion_status": "suggestion_ready_for_manual_review",
                "suggested_quantity": 3,
                "suggested_unit": "个",
                "calculation_trace": {},
            },
            {
                "suggestion_key": "S-door",
                "source_key": "sample|数量候选|P-平面门|P-单开门800",
                "source_file": "sample.dxf",
                "layer": "P-平面门",
                "block_name": "P-单开门800",
                "business_hint": "门数量候选",
                "quantity_kind": "count",
                "suggestion_status": "suggestion_ready_for_manual_review",
                "suggested_quantity": 2,
                "suggested_unit": "个",
                "calculation_trace": {},
            },
        ],
    }


def _standard_match_report() -> dict[str, object]:
    return {
        "ok": True,
        "summary": {},
        "standard_item_candidates": [
            {
                "standard_item_code": "011302001",
                "standard_item_name": "平面吊顶 | 天棚",
                "source_name": "石膏板吊顶做法",
                "source_spec_or_method": "轻钢龙骨",
            },
            {
                "standard_item_code": "011404002",
                "standard_item_name": "天棚喷刷涂料",
                "source_name": "天棚无机涂料",
                "source_spec_or_method": "展开面积",
            },
        ],
    }


def test_biz2x9fg_binds_geometry_to_standard_rule_trace():
    report = build_standard_rule_binding_report(
        quantity_suggestion_report=_quantity_report(),
        standard_match_report=_standard_match_report(),
        library=_library(),
    )
    by_key = {item["suggestion_key"]: item for item in report["bindings"]}

    assert report["safe_for_final_quantity_list"] is False
    assert by_key["S-area"]["binding_status"] == "blocked_multiple_standard_candidates_need_selection"
    assert by_key["S-area"]["compatible_trace_count"] == 1
    compatible = [row for row in by_key["S-area"]["standard_rule_traces"] if row["ready_for_manual_review"]]
    assert compatible[0]["item_code"] == "011302001"
    assert compatible[0]["standard_rule_suggested_quantity"] == 12.5
    assert compatible[0]["is_final_quantity"] is False
    assert "按设计图示尺寸" in compatible[0]["quantity_rule_text"]


def test_biz2x9fg_blocks_out_of_scope_and_incompatible_rule():
    report = build_standard_rule_binding_report(
        quantity_suggestion_report=_quantity_report(),
        standard_match_report=_standard_match_report(),
        library=_library(),
    )
    by_key = {item["suggestion_key"]: item for item in report["bindings"]}

    assert by_key["S-socket"]["binding_status"] == "blocked_out_of_scope_or_no_active_standard_candidate"
    assert by_key["S-socket"]["standard_candidate_count"] == 0
    assert by_key["S-door"]["binding_status"] == "blocked_standard_rule_incompatible_with_geometry_kind"
    assert by_key["S-door"]["standard_rule_traces"][0]["trace_status"] == "blocked_standard_rule_incompatible_with_geometry_kind"
    assert "洞口尺寸" in by_key["S-door"]["standard_rule_traces"][0]["quantity_rule_text"]


def test_biz2x9fg_writes_outputs(tmp_path):
    report = build_standard_rule_binding_report(
        quantity_suggestion_report=_quantity_report(),
        standard_match_report=_standard_match_report(),
        library=_library(),
    )
    rows = build_trace_csv_rows(report)
    outputs = write_standard_rule_binding_outputs(report, tmp_path, stem="binding")

    assert rows
    assert set(outputs) == {"json", "markdown", "binding_csv", "trace_csv"}
    assert json.loads((tmp_path / "binding.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9f-9g-standard-item-binding-and-rule-trace"
    assert (tmp_path / "binding_标准规则trace.csv").read_text(encoding="utf-8-sig").startswith("建议编号")
