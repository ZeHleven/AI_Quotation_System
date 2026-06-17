from __future__ import annotations

import json

from app.services.drawing_floor_region_reconstructor import (
    build_floor_region_reconstruction_report,
    write_floor_region_reconstruction_outputs,
)


def _segment(sequence, source_file, x1, y1, x2, y2):
    return {
        "线段编号": f"FL-{sequence:05d}",
        "来源文件": source_file,
        "图层": "F-地面材料分界线",
        "实体类型": "LINE",
        "源行号": sequence * 10,
        "X1": x1,
        "Y1": y1,
        "X2": x2,
        "Y2": y2,
        "长度": abs(x2 - x1) + abs(y2 - y1),
        "长度m": (abs(x2 - x1) + abs(y2 - y1)) * 0.001,
        "bbox": json.dumps({"min_x": min(x1, x2), "min_y": min(y1, y2), "max_x": max(x1, x2), "max_y": max(y1, y2)}),
        "风险标记": "",
    }


def _floor_layer_report(*, segments, source_file="floor.dxf", x=1500, y=1000, radius=2500):
    return {
        "phase": "BIZ-2x-R3-3b-floor-layer-targeted-rescan",
        "inputs": {"unit_conversion": {"area_to_square_meter_factor": 0.000001}},
        "floor_segment_rows": segments,
        "floor_package_rows": [
            {
                "识别项目编号": "BIZ2xP-CT01",
                "项目名称": "块料楼地面",
                "单位": "㎡",
                "材料编号": "CT-01",
                "材料名称": "灰色地砖",
                "规格": "750X1500",
                "材料文字": "750X1500灰色地砖",
                "材料文字来源文件": source_file,
                "材料文字源行号": 120,
                "材料文字X": x,
                "材料文字Y": y,
                "搜索半径": radius,
            }
        ],
    }


def test_biz2x_floor_region_reconstructs_ready_closed_region_and_outputs(tmp_path):
    source_file = "floor.dxf"
    segments = [
        _segment(1, source_file, 0, 0, 3000, 0),
        _segment(2, source_file, 3000, 0, 3000, 2000),
        _segment(3, source_file, 3000, 2000, 0, 2000),
        _segment(4, source_file, 0, 2000, 0, 0),
    ]

    report = build_floor_region_reconstruction_report(floor_layer_rescan_report=_floor_layer_report(segments=segments))

    assert report["summary"]["closed_region_candidate_count"] == 1
    assert report["summary"]["ready_closed_region_candidate_count"] == 1
    assert report["summary"]["project_ready_closed_region_count"] == 1
    closed = report["closed_region_rows"][0]
    project = report["project_region_rows"][0]
    assert closed["面积㎡"] == 6
    assert closed["建议工程量"] == 6
    assert closed["候选状态"] == "已重构地面闭合区域候选，待人工确认/R4规则计算"
    assert project["最佳闭合区编号"] == closed["闭合区编号"]
    assert project["建议工程量"] == 6

    outputs = write_floor_region_reconstruction_outputs(report, tmp_path, stem="floor_region")
    assert set(outputs) == {"json", "markdown", "closed_region_csv", "project_region_csv"}
    assert json.loads((tmp_path / "floor_region.json").read_text(encoding="utf-8"))["summary"][
        "ready_closed_region_candidate_count"
    ] == 1


def test_biz2x_floor_region_blocks_when_segments_do_not_close():
    source_file = "floor.dxf"
    segments = [
        _segment(1, source_file, 0, 0, 3000, 0),
        _segment(2, source_file, 3000, 2000, 0, 2000),
    ]

    report = build_floor_region_reconstruction_report(floor_layer_rescan_report=_floor_layer_report(segments=segments))

    assert report["summary"]["closed_region_candidate_count"] == 0
    assert report["summary"]["project_blocked_no_closed_region_count"] == 1
    project = report["project_region_rows"][0]
    assert project["候选状态"] == "地面线段未形成闭合区域候选，继续阻断"
    assert "floor_segments_not_closed" in project["风险标记"]


def test_biz2x_floor_region_keeps_small_regions_blocked():
    source_file = "floor.dxf"
    segments = [
        _segment(1, source_file, 0, 0, 1000, 0),
        _segment(2, source_file, 1000, 0, 1000, 1000),
        _segment(3, source_file, 1000, 1000, 0, 1000),
        _segment(4, source_file, 0, 1000, 0, 0),
    ]

    report = build_floor_region_reconstruction_report(
        floor_layer_rescan_report=_floor_layer_report(segments=segments, x=500, y=500, radius=900)
    )

    assert report["summary"]["closed_region_candidate_count"] == 1
    assert report["summary"]["ready_closed_region_candidate_count"] == 0
    assert report["summary"]["small_closed_region_candidate_count"] == 1
    assert report["summary"]["project_blocked_small_region_count"] == 1
    closed = report["closed_region_rows"][0]
    assert closed["面积㎡"] == 1
    assert closed["建议工程量"] == ""
    assert "closed_floor_region_area_too_small" in closed["风险标记"]
