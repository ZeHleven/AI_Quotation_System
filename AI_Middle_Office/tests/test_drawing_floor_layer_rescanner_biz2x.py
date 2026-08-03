from __future__ import annotations

import json

from app.services.drawing_floor_layer_rescanner import (
    build_floor_layer_rescan_report,
    write_floor_layer_rescan_outputs,
)


def _write_line_dxf(path, rows):
    chunks = ["0", "SECTION", "2", "ENTITIES"]
    for layer, x1, y1, x2, y2 in rows:
        chunks.extend(
            [
                "0",
                "LINE",
                "8",
                layer,
                "10",
                str(x1),
                "20",
                str(y1),
                "11",
                str(x2),
                "21",
                str(y2),
            ]
        )
    chunks.extend(["0", "ENDSEC", "0", "EOF"])
    path.write_text("\n".join(chunks), encoding="utf-8")


def _floor_report(*, x=500, y=500, source_file="floor.dxf"):
    return {
        "floor_project_rows": [
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
            }
        ]
    }


def test_biz2x_floor_layer_rescan_blocks_small_line_package(tmp_path):
    dxf_path = tmp_path / "floor.dxf"
    _write_line_dxf(
        dxf_path,
        [
            ("F-地面材料分界线", 0, 0, 1000, 0),
            ("F-地面材料分界线", 0, 1000, 1000, 1000),
            ("F-地面灯具", 0, 0, 3000, 3000),
        ],
    )

    report = build_floor_layer_rescan_report(
        dxf_files=[dxf_path],
        floor_paving_locator_report=_floor_report(),
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
        search_radius=900,
    )

    package = report["floor_package_rows"][0]
    assert report["summary"]["floor_segment_count"] == 2
    assert report["summary"]["floor_package_count"] == 1
    assert report["summary"]["small_or_layout_floor_package_count"] == 1
    assert package["包络面积"] == 1
    assert "floor_line_package_area_too_small" in package["风险标记"]
    assert package["候选状态"] == "线段包络面积过小，疑似图例/局部排版，继续阻断"


def test_biz2x_floor_layer_rescan_builds_ready_package_and_outputs(tmp_path):
    dxf_path = tmp_path / "floor.dxf"
    _write_line_dxf(
        dxf_path,
        [
            ("F-地面材料分界线", 0, 0, 3000, 0),
            ("F-地面材料分界线", 3000, 0, 3000, 2000),
            ("F-地面材料分界线", 3000, 2000, 0, 2000),
            ("F-地面材料分界线", 0, 2000, 0, 0),
            ("F-地面材料填充", 500, 0, 500, 2000),
            ("F-地面材料填充", 1000, 0, 1000, 2000),
            ("F-地面材料填充", 1500, 0, 1500, 2000),
            ("F-地面材料填充", 2000, 0, 2000, 2000),
        ],
    )

    report = build_floor_layer_rescan_report(
        dxf_files=[dxf_path],
        floor_paving_locator_report=_floor_report(x=1500, y=1000),
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
        search_radius=2500,
    )

    package = report["floor_package_rows"][0]
    assert report["summary"]["floor_segment_count"] == 8
    assert report["summary"]["ready_floor_package_count"] == 1
    assert package["包络面积"] == 6
    assert package["候选状态"] == "已生成地面线段包络候选，待人工确认/R4规则计算"

    outputs = write_floor_layer_rescan_outputs(report, tmp_path, stem="floor_layer")
    assert set(outputs) == {"json", "markdown", "segment_csv", "package_csv"}
    assert json.loads((tmp_path / "floor_layer.json").read_text(encoding="utf-8"))["summary"][
        "ready_floor_package_count"
    ] == 1
