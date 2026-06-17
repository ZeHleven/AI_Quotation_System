from __future__ import annotations

import json

from app.services.drawing_project_geometry_binder import (
    build_project_geometry_binding_report,
    write_project_geometry_binding_outputs,
)


def test_biz2x_project_geometry_binder_matches_area_and_length_candidates(tmp_path):
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-001",
                "图纸项目名称": "餐厅地面铺装",
                "项目名称": "块料楼地面",
                "项目特征": "面层材料：地砖；结合层：水泥砂浆",
                "单位": "㎡",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "地面铺装",
            },
            {
                "识别项目编号": "P-002",
                "图纸项目名称": "踢脚线",
                "项目名称": "金属踢脚线",
                "项目特征": "材料种类：金属踢脚线",
                "单位": "m",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "踢脚线",
            },
            {
                "识别项目编号": "P-003",
                "图纸项目名称": "成品门",
                "项目名称": "金属门",
                "项目特征": "门类型：成品门",
                "单位": "樘",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "成品门",
            },
        ]
    }
    geometry_report = {
        "files": [
            {
                "file_name": "餐厅平面.dxf",
                "area_candidates": [
                    {
                        "area": 12_000_000,
                        "layer": "A-地面铺装",
                        "entity_type": "LWPOLYLINE",
                        "line_number": 10,
                        "quantity_hint": "possible_area",
                    }
                ],
                "length_candidates": [
                    {
                        "length": 12_500,
                        "layer": "A-踢脚线",
                        "entity_type": "LINE",
                        "line_number": 20,
                        "quantity_hint": "possible_length",
                    }
                ],
                "count_candidates": [],
            }
        ]
    }

    report = build_project_geometry_binding_report(
        project_report=project_report,
        geometry_report=geometry_report,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    by_id = {row["识别项目编号"]: row for row in report["binding_rows"]}
    assert by_id["P-001"]["绑定状态"] == "建议绑定，需复核"
    assert by_id["P-001"]["建议工程量"] == 12
    assert by_id["P-001"]["建议单位"] == "㎡"
    assert by_id["P-002"]["绑定状态"] == "建议绑定，需复核"
    assert by_id["P-002"]["建议工程量"] == 12.5
    assert by_id["P-002"]["建议单位"] == "m"
    assert by_id["P-003"]["绑定状态"] == "未找到可绑定CAD候选"
    assert report["summary"]["binding_ready_project_count"] == 2
    assert report["summary"]["unbound_project_count"] == 1

    outputs = write_project_geometry_binding_outputs(report, tmp_path, stem="project_binding")
    assert set(outputs) == {"json", "markdown", "binding_csv", "candidate_csv"}
    assert json.loads((tmp_path / "project_binding.json").read_text(encoding="utf-8"))["summary"][
        "binding_ready_project_count"
    ] == 2
