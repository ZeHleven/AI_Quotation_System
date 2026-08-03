from __future__ import annotations

import json

from app.services.drawing_project_material_binder import (
    build_project_material_binding_report,
    extract_material_codes,
    write_project_material_binding_outputs,
)


def test_biz2x_material_code_extraction_ignores_drawing_noise_codes():
    assert extract_material_codes("CT-01 ST－02 EL-30 P-4 UTF-8 A-1") == ["CT-01", "ST-02"]


def test_biz2x_project_material_binder_links_project_to_region_and_geometry(tmp_path):
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-001",
                "图纸项目名称": "餐厅地面 CT-01",
                "项目名称": "块料楼地面",
                "项目特征": "面层材料：CT-01 地砖",
                "单位": "㎡",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "餐厅地面铺装 CT-01",
            }
        ]
    }
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-00001",
                "来源文件": "餐厅平面.dxf",
                "CAD面积": 12.5,
                "CAD周长": 14.0,
                "图层": "A-FLOOR",
                "实体类型": "LWPOLYLINE",
                "源行号": 10,
                "_geometry_key": "餐厅平面.dxf|LWPOLYLINE|10",
                "区域内文字": "餐厅 CT-01 地砖",
                "附近文字": "",
                "房间/空间标签": "餐厅",
                "项目标签": "地面",
                "区域类型建议": "地面铺装区域候选",
            }
        ]
    }
    geometry_report = {
        "files": [
            {
                "file_name": "餐厅平面.dxf",
                "area_candidates": [
                    {
                        "area": 12_500_000,
                        "layer": "A-FLOOR-CT-01",
                        "entity_type": "LWPOLYLINE",
                        "line_number": 10,
                        "quantity_hint": "possible_floor_area",
                    }
                ],
                "length_candidates": [],
                "count_candidates": [],
            }
        ]
    }
    field_report = {
        "drawing_catalog_rows": [
            {
                "source_file": "01.前言文件.dxf",
                "source_row_number": 15,
                "drawing_name": "图例与材料表",
                "raw_row_text": "CT | 01 | 精品灰色地砖750x1500 | 750x1500 | PB | 01 | 防水石膏板",
            }
        ]
    }

    report = build_project_material_binding_report(
        project_report=project_report,
        region_label_report=region_label_report,
        geometry_report=geometry_report,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
        field_report=field_report,
    )

    row = report["project_binding_rows"][0]
    assert row["材料编号"] == "CT-01"
    assert "精品灰色地砖750x1500" in row["材料表证据"]
    assert row["材料绑定状态"] == "材料编号已绑定 CAD 证据，待 R4 规则计算"
    assert row["推荐区域编号"] == "BIZ2xR-00001"
    assert row["推荐CAD候选编号"] == "BIZ2xM-G00001"
    assert row["建议工程量"] == 12.5
    assert row["建议单位"] == "㎡"
    assert report["summary"]["project_with_material_code_count"] == 1
    assert report["summary"]["material_region_bound_project_count"] == 1
    assert report["summary"]["material_geometry_bound_project_count"] == 1
    assert report["summary"]["material_table_entry_count"] == 2
    assert report["summary"]["project_material_table_bound_count"] == 1
    assert report["material_index_rows"][0]["材料编号"] == "CT-01"
    assert report["material_index_rows"][0]["材料表命中数"] == 1

    outputs = write_project_material_binding_outputs(report, tmp_path, stem="material_binding")
    assert set(outputs) == {
        "json",
        "markdown",
        "project_binding_csv",
        "material_index_csv",
        "material_table_csv",
        "material_inheritance_csv",
    }
    assert json.loads((tmp_path / "material_binding.json").read_text(encoding="utf-8"))["summary"][
        "material_geometry_bound_project_count"
    ] == 1


def test_biz2x_project_material_binder_creates_region_inheritance_candidate():
    report = build_project_material_binding_report(
        project_report={
            "project_rows": [
                {
                    "识别项目编号": "P-003",
                    "图纸项目名称": "开放餐厅 CT-02",
                    "项目名称": "块料楼地面",
                    "项目特征": "灰色地砖600x1200 CT-02",
                    "单位": "㎡",
                    "来源文件": "03.施工图.dxf",
                    "识别证据": "600X1200灰色地砖 CT-02",
                }
            ]
        },
        region_label_report={
            "region_rows": [
                {
                    "区域编号": "R-CT",
                    "来源文件": "03.施工图.dxf",
                    "CAD面积": 18.5,
                    "CAD周长": 19.0,
                    "图层": "F-地面材料分界线",
                    "区域内文字": "开放餐厅 CT玻化砖",
                    "附近文字": "",
                    "房间/空间标签": "开放餐厅",
                    "项目标签": "地面",
                    "区域类型建议": "地面铺装区域候选",
                }
            ]
        },
        geometry_report={"files": []},
        field_report={
            "material_method_rows": [
                {
                    "source_file": "03.施工图.dxf",
                    "source_row_number": 18,
                    "material_or_method_name": "600X1200灰色地砖",
                    "raw_row_text": "开放餐厅 | 600X1200灰色地砖 | CT | 02",
                }
            ]
        },
    )

    row = report["project_binding_rows"][0]
    assert row["材料绑定状态"] == "项目材料编号已生成区域/房间继承候选，待人工确认/R4规则校验"
    assert row["推荐区域编号"] == "R-CT"
    assert row["建议工程量"] == 18.5
    assert report["summary"]["material_inheritance_candidate_count"] >= 2
    assert report["summary"]["material_inherited_region_candidate_project_count"] == 1
    assert any(
        item["候选类型"] == "区域/房间文字继承候选" and item["候选区域编号"] == "R-CT"
        for item in report["material_inheritance_rows"]
    )


def test_biz2x_project_material_binder_flags_legend_like_inheritance_candidate():
    report = build_project_material_binding_report(
        project_report={
            "project_rows": [
                {
                    "识别项目编号": "P-004",
                    "图纸项目名称": "餐厅 CT-01",
                    "项目名称": "块料楼地面",
                    "项目特征": "精品灰色地砖750x1500 CT-01",
                    "单位": "㎡",
                    "来源文件": "03.施工图.dxf",
                }
            ]
        },
        region_label_report={
            "region_rows": [
                {
                    "区域编号": "R-LEGEND",
                    "来源文件": "03.施工图.dxf",
                    "CAD面积": 0.8,
                    "CAD周长": 3.6,
                    "图层": "0",
                    "区域内文字": "01序号；CT玻化砖；----页码1",
                    "区域类型建议": "未分类闭合区域",
                }
            ]
        },
        geometry_report={"files": []},
        field_report={
            "drawing_catalog_rows": [
                {
                    "source_file": "01.前言文件.dxf",
                    "source_row_number": 15,
                    "raw_row_text": "CT | 01 | 精品灰色地砖750x1500 | 750x1500",
                }
            ]
        },
    )

    inherited_rows = report["material_inheritance_rows"]
    legend_rows = [row for row in inherited_rows if row["候选区域编号"] == "R-LEGEND"]
    assert legend_rows
    assert "图例" in legend_rows[0]["候选状态"]
    assert report["project_binding_rows"][0]["推荐区域编号"] == ""
    assert report["project_binding_rows"][0]["材料绑定状态"] == "项目材料编号已命中材料表，但未绑定 CAD 区域/几何"
    assert report["summary"]["material_legend_risk_candidate_count"] == 1


def test_biz2x_project_material_binder_keeps_no_material_code_unbound():
    report = build_project_material_binding_report(
        project_report={
            "project_rows": [
                {
                    "识别项目编号": "P-002",
                    "图纸项目名称": "餐厅踢脚线",
                    "项目名称": "金属踢脚线",
                    "项目特征": "成品金属踢脚线",
                    "单位": "m",
                    "来源文件": "餐厅平面.dxf",
                }
            ]
        },
        region_label_report={"region_rows": []},
        geometry_report={"files": []},
    )

    row = report["project_binding_rows"][0]
    assert row["材料编号"] == ""
    assert row["材料绑定状态"] == "未识别材料编号，待 R3 继续从材料表/做法表补证据"
    assert report["summary"]["unbound_project_count"] == 1
