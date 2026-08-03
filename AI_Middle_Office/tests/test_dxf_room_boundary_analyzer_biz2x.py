from __future__ import annotations

import json

from app.services.dxf_room_boundary_analyzer import (
    build_room_boundary_analysis_report,
    write_room_boundary_analysis_outputs,
)


def test_biz2x_room_boundary_uses_region_perimeter_when_no_opening(tmp_path):
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-00001",
                "来源文件": "餐厅平面.dxf",
                "CAD面积": 12.0,
                "CAD周长": 14.0,
                "图层": "A-ROOM",
                "实体类型": "LWPOLYLINE",
                "源行号": 10,
                "区域边界": json.dumps({"min_x": 0, "min_y": 0, "max_x": 4000, "max_y": 3000}, ensure_ascii=False),
                "区域内文字": "餐厅",
                "附近文字": "",
                "房间/空间标签": "餐厅",
                "项目标签": "",
                "区域类型建议": "房间边界候选",
                "绑定状态": "已绑定区域文字",
            }
        ]
    }

    report = build_room_boundary_analysis_report(
        region_label_report=region_label_report,
        geometry_report={"files": [{"file_name": "餐厅平面.dxf", "count_candidates": [], "length_candidates": []}]},
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    row = report["room_rows"][0]
    assert row["房间/空间名称"] == "餐厅"
    assert row["CAD面积"] == 12.0
    assert row["净周长候选"] == 14.0
    assert row["净周长状态"] == "未识别门洞，暂按区域周长候选"
    assert report["summary"]["room_with_net_perimeter_candidate_count"] == 1

    outputs = write_room_boundary_analysis_outputs(report, tmp_path, stem="room_boundary")
    assert set(outputs) == {"json", "markdown", "room_csv", "opening_csv"}
    assert json.loads((tmp_path / "room_boundary.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-room-boundary-net-perimeter"


def test_biz2x_room_boundary_deducts_known_door_width_candidate():
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-00002",
                "来源文件": "餐厅平面.dxf",
                "CAD面积": 12.0,
                "CAD周长": 14.0,
                "图层": "A-ROOM",
                "实体类型": "LWPOLYLINE",
                "源行号": 10,
                "区域边界": json.dumps({"min_x": 0, "min_y": 0, "max_x": 4000, "max_y": 3000}, ensure_ascii=False),
                "区域内文字": "餐厅",
                "附近文字": "",
                "房间/空间标签": "餐厅",
                "项目标签": "",
                "区域类型建议": "房间边界候选",
                "绑定状态": "已绑定区域文字",
            }
        ]
    }
    geometry_report = {
        "files": [
            {
                "file_name": "餐厅平面.dxf",
                "count_candidates": [
                    {
                        "entity_type": "INSERT",
                        "line_number": 20,
                        "layer": "A-平面门",
                        "block_name": "平面门900",
                        "x": 2000,
                        "y": 0,
                        "quantity_hint": "possible_count",
                    }
                ],
                "length_candidates": [],
            }
        ]
    }

    report = build_room_boundary_analysis_report(
        region_label_report=region_label_report,
        geometry_report=geometry_report,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    row = report["room_rows"][0]
    assert row["门洞/开口候选数量"] == 1
    assert row["门洞/开口扣减长度候选"] == 0.9
    assert row["净周长候选"] == 13.1
    assert row["净周长状态"] == "已按开口候选扣减，需复核"
    assert report["opening_candidate_rows"][0]["扣减长度候选"] == 0.9


def test_biz2x_room_boundary_blocks_net_perimeter_when_opening_width_missing():
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-00003",
                "来源文件": "洗手间平面.dxf",
                "CAD面积": 2.52,
                "CAD周长": 6.4,
                "图层": "A-ROOM",
                "实体类型": "LWPOLYLINE",
                "源行号": 10,
                "区域边界": json.dumps({"min_x": 0, "min_y": 0, "max_x": 1800, "max_y": 1400}, ensure_ascii=False),
                "区域内文字": "洗手间",
                "附近文字": "",
                "房间/空间标签": "洗手间",
                "项目标签": "防水",
                "区域类型建议": "湿区/防水区域候选",
                "绑定状态": "已绑定区域文字",
            }
        ]
    }
    geometry_report = {
        "files": [
            {
                "file_name": "洗手间平面.dxf",
                "count_candidates": [
                    {
                        "entity_type": "INSERT",
                        "line_number": 30,
                        "layer": "A-平面门",
                        "block_name": "平面门",
                        "x": 900,
                        "y": 0,
                        "quantity_hint": "possible_count",
                    }
                ],
                "length_candidates": [],
            }
        ]
    }

    report = build_room_boundary_analysis_report(
        region_label_report=region_label_report,
        geometry_report=geometry_report,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    row = report["room_rows"][0]
    assert row["净周长候选"] == ""
    assert row["净周长状态"] == "存在开口但缺少宽度证据"
    assert report["summary"]["net_perimeter_blocked_count"] == 1
