from __future__ import annotations

import json

from app.services.drawing_floor_paving_locator import (
    build_floor_paving_locator_report,
    write_floor_paving_locator_outputs,
)


def test_biz2x_floor_paving_locator_binds_material_text_to_floor_area(tmp_path):
    project_material_report = {
        "project_binding_rows": [
            {
                "识别项目编号": "P-CT01",
                "项目名称": "块料楼地面",
                "单位": "㎡",
                "材料编号": "CT-01",
                "来源文件": "餐厅地面铺装.dxf",
            }
        ],
        "material_table_rows": [
            {
                "材料编号": "CT-01",
                "材料名称": "精品灰色地砖750x1500",
                "规格": "750x1500",
                "来源文件": "材料表.dxf",
            }
        ],
    }
    field_report = {
        "drawing_annotation_rows": [
            {
                "source_file": "餐厅地面铺装.dxf",
                "source_row_number": 120,
                "material_or_method_name": "精品灰色地砖750x1500",
                "spec_or_method": "精品灰色地砖750x1500",
                "raw_row_text": "精品灰色地砖750x1500",
                "layer": "FC-TEXT 地面文字",
                "layout": "",
                "x": 1500,
                "y": 1200,
            }
        ]
    }
    geometry_report = {
        "files": [
            {
                "file_name": "餐厅地面铺装.dxf",
                "area_candidates": [
                    {
                        "entity_type": "LWPOLYLINE",
                        "line_number": 88,
                        "layer": "F-地面材料分界线",
                        "area": 24_000_000,
                        "length": 20_000,
                        "bbox": {"min_x": 0, "min_y": 0, "max_x": 6000, "max_y": 4000},
                    }
                ],
            }
        ]
    }

    report = build_floor_paving_locator_report(
        project_material_binding_report=project_material_report,
        field_report=field_report,
        geometry_report=geometry_report,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    row = report["floor_project_rows"][0]
    assert row["候选状态"] == "已定位地面铺装有效区域候选，待人工确认/R4规则计算"
    assert row["候选CAD编号"] == "BIZ2xF-G00001"
    assert row["建议工程量"] == 24
    assert report["summary"]["floor_project_bound_candidate_count"] == 1
    assert report["summary"]["effective_floor_area_candidate_count"] == 1

    outputs = write_floor_paving_locator_outputs(report, tmp_path, stem="floor_paving")
    assert set(outputs) == {"json", "markdown", "project_csv", "geometry_csv", "text_csv"}
    assert json.loads((tmp_path / "floor_paving.json").read_text(encoding="utf-8"))["summary"][
        "floor_project_bound_candidate_count"
    ] == 1


def test_biz2x_floor_paving_locator_blocks_when_floor_geometry_sample_missing():
    report = build_floor_paving_locator_report(
        project_material_binding_report={
            "project_binding_rows": [
                {
                    "识别项目编号": "P-CT02",
                    "项目名称": "块料楼地面",
                    "单位": "㎡",
                    "材料编号": "CT-02",
                    "来源文件": "餐厅地面铺装.dxf",
                }
            ],
            "material_table_rows": [
                {
                    "材料编号": "CT-02",
                    "材料名称": "600X1200灰色地砖",
                    "规格": "600X1200",
                }
            ],
        },
        field_report={
            "drawing_annotation_rows": [
                {
                    "source_file": "餐厅地面铺装.dxf",
                    "source_row_number": 130,
                    "material_or_method_name": "600X1200灰色地砖",
                    "spec_or_method": "600X1200灰色地砖",
                    "raw_row_text": "600X1200灰色地砖",
                    "layer": "FC-TEXT 地面文字",
                    "x": 2600,
                    "y": 900,
                }
            ]
        },
        geometry_report={"files": [{"file_name": "餐厅地面铺装.dxf", "area_candidates": []}]},
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    row = report["floor_project_rows"][0]
    assert row["候选状态"] == "地面图层面积候选未进入现有几何样本，需定向重扫地面图层"
    assert row["材料文字"] == "600X1200灰色地砖"
    assert row["建议工程量"] == ""
    assert report["summary"]["floor_project_sample_missing_count"] == 1
    assert report["summary"]["final_generation_status"] == "blocked_until_floor_layer_rescan_or_manual_area_review"


def test_biz2x_floor_paving_locator_ignores_legend_block_text_for_project_binding():
    report = build_floor_paving_locator_report(
        project_material_binding_report={
            "project_binding_rows": [
                {
                    "识别项目编号": "P-CT03",
                    "项目名称": "块料楼地面",
                    "单位": "㎡",
                    "材料编号": "CT-03",
                }
            ],
            "material_table_rows": [
                {
                    "材料编号": "CT-03",
                    "材料名称": "300x300吸水地砖",
                    "规格": "300x300",
                }
            ],
        },
        field_report={
            "drawing_annotation_rows": [
                {
                    "source_file": "图例.dxf",
                    "source_row_number": 10,
                    "material_or_method_name": "300x300吸水地砖",
                    "spec_or_method": "300x300吸水地砖",
                    "raw_row_text": "01序号 300x300吸水地砖 ----页码1",
                    "layer": "0",
                    "layout": "BLOCKS",
                    "x": 1,
                    "y": 1,
                }
            ]
        },
        geometry_report={"files": []},
    )

    assert report["floor_material_text_rows"]
    assert "legend_or_detail_text" in report["floor_material_text_rows"][0]["风险标记"]
    assert report["floor_project_rows"][0]["候选状态"] == "未找到材料文字坐标证据，待 R3-3 继续补文字定位"
