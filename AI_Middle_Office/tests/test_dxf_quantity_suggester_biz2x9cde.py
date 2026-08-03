from __future__ import annotations

import json

import pytest

from app.services.dxf_quantity_suggester import (
    DxfQuantitySuggestionError,
    build_low_risk_quantity_suggestion_report,
    build_quantity_suggestion_csv_rows,
    write_quantity_suggestion_outputs,
)


def _geometry_report() -> dict[str, object]:
    return {
        "files": [
            {
                "file_name": "sample.dxf",
                "area_candidates": [
                    {"layer": "F-地面材料分界线", "entity_type": "LWPOLYLINE", "area": 2_000_000, "line_number": 10},
                    {"layer": "F-地面材料分界线", "entity_type": "LWPOLYLINE", "area": 3_000_000, "line_number": 20},
                ],
                "length_candidates": [
                    {"layer": "PM-线脚", "entity_type": "LINE", "length": 2000, "line_number": 30},
                    {"layer": "PM-线脚", "entity_type": "LINE", "length": 3000, "line_number": 40},
                ],
                "count_candidates": [
                    {"layer": "P-平面门", "block_name": "P-单开门800", "entity_type": "INSERT", "count": 1, "line_number": 50},
                    {"layer": "P-平面门", "block_name": "P-单开门800", "entity_type": "INSERT", "count": 1, "line_number": 60},
                ],
            }
        ]
    }


def _mapping_report(ready: bool = True) -> dict[str, object]:
    return {
        "ready_for_geometry_quantity_probe": ready,
        "unit_conversion": {
            "drawing_unit": "mm",
            "unit_to_meter_factor": 0.001,
            "area_to_square_meter_factor": 0.000001,
        },
        "mapping_rows": [
            {
                "source_key": "sample.dxf|面积候选|F-地面材料分界线|",
                "source_file": "sample.dxf",
                "candidate_type": "面积候选",
                "quantity_kind": "area",
                "layer": "F-地面材料分界线",
                "block_name": "",
                "business_hint": "地面/楼地面面积候选",
                "matched_reason": "图层/块名包含地面或地台关键词",
                "allow_quantity_candidate_probe": True,
            },
            {
                "source_key": "sample.dxf|长度候选|PM-线脚|",
                "source_file": "sample.dxf",
                "candidate_type": "长度候选",
                "quantity_kind": "length",
                "layer": "PM-线脚",
                "block_name": "",
                "business_hint": "装饰线条长度候选",
                "matched_reason": "图层/块名包含线脚/线条关键词",
                "allow_quantity_candidate_probe": True,
            },
            {
                "source_key": "sample.dxf|数量候选|P-平面门|P-单开门800",
                "source_file": "sample.dxf",
                "candidate_type": "数量候选",
                "quantity_kind": "count",
                "layer": "P-平面门",
                "block_name": "P-单开门800",
                "business_hint": "门数量候选",
                "matched_reason": "图层/块名包含门关键词",
                "allow_quantity_candidate_probe": True,
            },
        ],
    }


def test_biz2x9cde_generates_area_length_and_count_suggestions():
    report = build_low_risk_quantity_suggestion_report(geometry_report=_geometry_report(), mapping_report=_mapping_report())
    by_kind = {item["quantity_kind"]: item for item in report["suggestions"]}

    assert report["safe_for_auto_quantity"] is False
    assert report["standard_quantity_rule_applied"] is False
    assert by_kind["area"]["suggested_quantity"] == 5
    assert by_kind["area"]["suggested_unit"] == "㎡"
    assert by_kind["length"]["suggested_quantity"] == 5
    assert by_kind["length"]["suggested_unit"] == "m"
    assert by_kind["count"]["suggested_quantity"] == 2
    assert by_kind["count"]["standard_rule_status"] == "pending_standard_item_rule_binding"
    assert by_kind["count"]["requires_manual_review"] is True


def test_biz2x9cde_filters_tiny_geometry_fragments():
    geometry_report = {
        "files": [
            {
                "file_name": "sample.dxf",
                "area_candidates": [
                    {"layer": "F-地面材料分界线", "entity_type": "LWPOLYLINE", "area": 100, "line_number": 10},
                ],
                "length_candidates": [
                    {"layer": "PM-线脚", "entity_type": "LINE", "length": 10, "line_number": 30},
                ],
                "count_candidates": [],
            }
        ]
    }

    report = build_low_risk_quantity_suggestion_report(geometry_report=geometry_report, mapping_report=_mapping_report())
    by_kind = {item["quantity_kind"]: item for item in report["suggestions"]}

    assert by_kind["area"]["suggested_quantity"] == 0
    assert by_kind["area"]["suggestion_status"] == "blocked_no_usable_geometry_value"
    assert by_kind["area"]["tiny_candidate_count"] == 1
    assert "tiny_geometry_filtered_below_review_threshold" in by_kind["area"]["risk_flags"]
    assert by_kind["length"]["suggested_quantity"] == 0
    assert by_kind["length"]["suggestion_status"] == "blocked_no_usable_geometry_value"


def test_biz2x9cde_blocks_when_mapping_not_ready():
    with pytest.raises(DxfQuantitySuggestionError):
        build_low_risk_quantity_suggestion_report(geometry_report=_geometry_report(), mapping_report=_mapping_report(ready=False))


def test_biz2x9cde_writes_outputs(tmp_path):
    report = build_low_risk_quantity_suggestion_report(geometry_report=_geometry_report(), mapping_report=_mapping_report())
    rows = build_quantity_suggestion_csv_rows(report)
    outputs = write_quantity_suggestion_outputs(report, tmp_path, stem="suggestions")

    assert rows
    assert set(outputs) == {"json", "markdown", "suggestion_csv"}
    assert json.loads((tmp_path / "suggestions.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9cde-low-risk-geometry-quantity-suggestions"
    assert (tmp_path / "suggestions_建议量清单.csv").read_text(encoding="utf-8-sig").startswith("建议编号")
