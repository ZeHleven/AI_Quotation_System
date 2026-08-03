from __future__ import annotations

import json

from app.services.dxf_table_reconstructor import (
    build_drawing_index_csv_rows,
    build_spatial_rows,
    build_table_rows_csv,
    reconstruct_dxf_tables,
    write_table_reconstruction_outputs,
)
from app.services.dxf_text_extractor import parse_dxf_file


def _text_entity(text: str, x: float, y: float, layer: str = "W-文字") -> list[str]:
    return [
        "  0",
        "TEXT",
        "  8",
        layer,
        " 10",
        str(x),
        " 20",
        str(y),
        " 40",
        "3.0",
        "  1",
        text,
        "410",
        "Model",
    ]


def _sample_dxf() -> str:
    lines = [
        "  0",
        "SECTION",
        "  2",
        "ENTITIES",
    ]
    lines += _text_entity("图纸目录", 0, 100)
    lines += _text_entity("DS-目录", 0, 80)
    lines += _text_entity("职工餐厅平面布置图", 200, 80)
    lines += _text_entity("材料名称", 0, 20)
    lines += _text_entity("编号", 0, 0)
    lines += _text_entity("玻化砖材料说明", 200, 0)
    lines += _text_entity("通用节点（一）", 0, -80)
    lines += _text_entity("做法详图", 200, -80)
    lines += ["  0", "ENDSEC", "  0", "EOF", ""]
    return "\n".join(lines)


def test_biz2x3_reconstructs_table_candidates_and_drawing_index(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    dxf_path.write_text(_sample_dxf(), encoding="utf-8")
    parsed = parse_dxf_file(dxf_path)

    report = reconstruct_dxf_tables([parsed])

    assert report["summary"]["table_candidate_count"] >= 3
    assert {"drawing_catalog", "material_table", "construction_method"} <= set(report["summary"]["table_type_counts"])
    assert report["summary"]["drawing_index_entry_count"] >= 2
    titles = {entry["sheet_title"] for entry in report["drawing_index_entries"]}
    assert "职工餐厅平面布置图" in titles
    assert "图纸目录" in titles


def test_biz2x3_builds_spatial_rows_by_nearby_y(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    dxf_path.write_text(_sample_dxf(), encoding="utf-8")
    parsed = parse_dxf_file(dxf_path)

    rows = build_spatial_rows(list(parsed.text_records))

    assert any("DS-目录 | 职工餐厅平面布置图" in row.row_text for row in rows)


def test_biz2x3_writes_table_reconstruction_outputs(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    dxf_path.write_text(_sample_dxf(), encoding="utf-8")
    parsed = parse_dxf_file(dxf_path)
    report = reconstruct_dxf_tables([parsed])

    outputs = write_table_reconstruction_outputs(report, tmp_path / "outputs", stem="tables")
    table_rows = build_table_rows_csv(report)
    drawing_rows = build_drawing_index_csv_rows(report)

    assert set(outputs) == {"json", "markdown", "table_csv", "drawing_index_csv"}
    assert table_rows
    assert drawing_rows
    assert json.loads((tmp_path / "outputs" / "tables.json").read_text(encoding="utf-8"))["ok"] is True
