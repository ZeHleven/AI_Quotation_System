from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.drawing_project_geometry_binder import build_project_geometry_binding_report
from app.services.dxf_region_label_binder import build_region_label_binding_report, write_region_label_binding_outputs


@dataclass(frozen=True)
class _TextRecord:
    source_file: str
    text: str
    x: float
    y: float
    layer: str = "TEXT"
    line_number: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "layer": self.layer,
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class _ParsedTextFile:
    file_name: str
    text_records: tuple[_TextRecord, ...]


def test_biz2x_region_label_binder_links_text_inside_closed_area(tmp_path):
    geometry_report = {
        "files": [
            {
                "file_name": "餐厅平面.dxf",
                "area_candidates": [
                    {
                        "source_file": "餐厅平面.dxf",
                        "entity_type": "LWPOLYLINE",
                        "line_number": 100,
                        "layer": "C-天花区域",
                        "area": 12_000_000,
                        "length": 14_000,
                        "bbox": {"min_x": 0, "min_y": 0, "max_x": 4000, "max_y": 3000},
                    }
                ],
            }
        ]
    }
    parsed_text_files = [
        _ParsedTextFile(
            file_name="餐厅平面.dxf",
            text_records=(
                _TextRecord(source_file="餐厅平面.dxf", text="石膏板饰面吊顶", x=1800, y=1500),
                _TextRecord(source_file="餐厅平面.dxf", text="餐厅", x=1200, y=1000),
            ),
        )
    ]

    report = build_region_label_binding_report(
        geometry_report=geometry_report,
        parsed_text_files=parsed_text_files,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
    )

    assert report["summary"]["region_candidate_count"] == 1
    row = report["region_rows"][0]
    assert row["CAD面积"] == 12
    assert row["绑定状态"] == "已绑定区域文字"
    assert "餐厅" in row["房间/空间标签"]
    assert "吊顶" in row["项目标签"]
    assert "石膏板饰面吊顶" in row["区域内文字"]

    outputs = write_region_label_binding_outputs(report, tmp_path, stem="region_label")
    assert set(outputs) == {"json", "markdown", "region_label_csv"}
    assert json.loads((tmp_path / "region_label.json").read_text(encoding="utf-8"))["summary"]["labeled_region_count"] == 1


def test_biz2x_project_geometry_binder_uses_region_text_for_scoring():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-001",
                "图纸项目名称": "石膏板饰面吊顶",
                "项目名称": "平面吊顶 | 天棚",
                "项目特征": "面板材料品种、规格：石膏板",
                "单位": "㎡",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "石膏板饰面吊顶",
            }
        ]
    }
    geometry_report = {
        "files": [
            {
                "file_name": "餐厅平面.dxf",
                "area_candidates": [
                    {
                        "source_file": "餐厅平面.dxf",
                        "entity_type": "LWPOLYLINE",
                        "line_number": 100,
                        "layer": "0",
                        "area": 12_000_000,
                        "length": 14_000,
                        "bbox": {"min_x": 0, "min_y": 0, "max_x": 4000, "max_y": 3000},
                    }
                ],
            }
        ]
    }
    region_label_report = {
        "region_index_rows": [
            {
                "_geometry_key": "餐厅平面.dxf|LWPOLYLINE|100",
                "区域编号": "BIZ2xR-00001",
                "区域内文字": "石膏板饰面吊顶",
                "附近文字": "",
                "房间/空间标签": "餐厅",
                "项目标签": "吊顶；石膏板",
                "区域类型建议": "吊顶/天棚区域候选",
                "绑定状态": "已绑定区域文字",
            }
        ]
    }

    report = build_project_geometry_binding_report(
        project_report=project_report,
        geometry_report=geometry_report,
        unit_conversion={"unit_to_meter_factor": 0.001, "area_to_square_meter_factor": 0.000001},
        region_label_report=region_label_report,
    )

    row = report["binding_rows"][0]
    assert row["绑定状态"] == "建议绑定，需复核"
    assert row["建议工程量"] == 12
    assert report["candidate_rows"][0]["CAD区域文字"]
