from __future__ import annotations

import json

import pytest

from app.services.dxf_layer_block_mapper import (
    DxfLayerBlockMappingError,
    build_layer_block_mapping_csv_rows,
    build_layer_block_mapping_report,
    write_layer_block_mapping_outputs,
)


def _geometry_report() -> dict[str, object]:
    return {
        "files": [
            {
                "file_name": "sample.dxf",
                "area_candidates": [
                    {"layer": "F-地面材料分界线", "entity_type": "LWPOLYLINE", "line_number": 10},
                    {"layer": "D-顶面造型轮廓", "entity_type": "LWPOLYLINE", "line_number": 20},
                    {"layer": "天花灯具", "entity_type": "LWPOLYLINE", "line_number": 25},
                    {"layer": "0", "entity_type": "LWPOLYLINE", "line_number": 30},
                ],
                "length_candidates": [
                    {"layer": "PM-线脚", "entity_type": "LINE", "line_number": 40},
                    {"layer": "W-尺寸、标高", "entity_type": "LINE", "line_number": 50},
                ],
                "count_candidates": [
                    {"layer": "天花灯具", "block_name": "方形射灯", "entity_type": "INSERT", "line_number": 60, "count": 1},
                    {"layer": "P-窗帘", "block_name": "P-双层窗帘", "entity_type": "INSERT", "line_number": 65, "count": 1},
                    {"layer": "W-尺寸、标高", "block_name": "_ArchTick", "entity_type": "INSERT", "line_number": 70, "count": 1},
                ],
            }
        ]
    }


def _confirmation_report(ready: bool = True) -> dict[str, object]:
    return {
        "ready_for_geometry_quantity_probe": ready,
        "manual_confirmation": {
            "drawing_unit": "mm",
            "unit_to_meter_factor": 0.001,
            "title_block_scale_usage": "plot_scale_only_not_quantity_multiplier",
        },
    }


def test_biz2x9c0_builds_low_risk_mapping():
    report = build_layer_block_mapping_report(geometry_report=_geometry_report(), confirmation_report=_confirmation_report())
    allowed = [row for row in report["mapping_rows"] if row["allow_quantity_candidate_probe"]]

    assert report["safe_for_auto_quantity"] is False
    assert report["ready_for_geometry_quantity_probe"] is True
    assert report["unit_conversion"]["area_to_square_meter_factor"] == 0.000001
    assert {row["quantity_kind"] for row in allowed} == {"area", "length", "count"}
    assert any(row["layer"] == "F-地面材料分界线" and row["business_hint"] == "地面/楼地面面积候选" for row in allowed)
    assert any(row["layer"] == "天花灯具" and row["candidate_type"] == "面积候选" and not row["allow_quantity_candidate_probe"] for row in report["mapping_rows"])
    assert any(row["layer"] == "P-窗帘" and not row["allow_quantity_candidate_probe"] for row in report["mapping_rows"])
    assert any(row["layer"] == "W-尺寸、标高" and not row["allow_quantity_candidate_probe"] for row in report["mapping_rows"])


def test_biz2x9c0_blocks_when_scale_unit_not_confirmed():
    with pytest.raises(DxfLayerBlockMappingError):
        build_layer_block_mapping_report(geometry_report=_geometry_report(), confirmation_report=_confirmation_report(ready=False))


def test_biz2x9c0_writes_outputs(tmp_path):
    report = build_layer_block_mapping_report(geometry_report=_geometry_report(), confirmation_report=_confirmation_report())
    rows = build_layer_block_mapping_csv_rows(report)
    outputs = write_layer_block_mapping_outputs(report, tmp_path, stem="layer_block")

    assert rows
    assert set(outputs) == {"json", "markdown", "mapping_csv"}
    assert json.loads((tmp_path / "layer_block.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9c0-layer-block-low-risk-mapping"
    assert (tmp_path / "layer_block_映射清单.csv").read_text(encoding="utf-8-sig").startswith("状态")
