from __future__ import annotations

import json

from app.services.dxf_geometry_probe import (
    build_geometry_candidate_csv_rows,
    build_geometry_probe_report,
    parse_dxf_geometry_file,
    write_geometry_probe_outputs,
)


def _write_sample_dxf(path):
    path.write_text(
        "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "ENTITIES",
                "0",
                "LWPOLYLINE",
                "8",
                "地面铺装",
                "90",
                "4",
                "70",
                "1",
                "10",
                "0",
                "20",
                "0",
                "10",
                "4",
                "20",
                "0",
                "10",
                "4",
                "20",
                "3",
                "10",
                "0",
                "20",
                "3",
                "0",
                "LINE",
                "8",
                "踢脚线",
                "10",
                "0",
                "20",
                "0",
                "11",
                "3",
                "21",
                "4",
                "0",
                "INSERT",
                "8",
                "门窗",
                "2",
                "M-门",
                "10",
                "10",
                "20",
                "20",
                "0",
                "DIMENSION",
                "8",
                "尺寸标注",
                "42",
                "3000",
                "0",
                "ENDSEC",
                "0",
                "EOF",
            ]
        ),
        encoding="utf-8",
    )


def test_biz2x9a_extracts_basic_geometry_candidates(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    _write_sample_dxf(dxf_path)

    parsed = parse_dxf_geometry_file(dxf_path)

    assert parsed.entity_counts["LWPOLYLINE"] == 1
    assert parsed.entity_counts["LINE"] == 1
    assert parsed.entity_counts["INSERT"] == 1
    assert parsed.entity_counts["DIMENSION"] == 1
    assert parsed.area_candidates[0]["area"] == 12
    assert parsed.area_candidates[0]["length"] == 14
    assert parsed.area_candidates[0]["quantity_hint"] == "possible_area"
    assert any(item["entity_type"] == "LINE" and item["length"] == 5 for item in parsed.length_candidates)
    assert parsed.count_candidates[0]["block_name"] == "M-门"
    assert parsed.dimension_candidates[0]["measurement"] == "3000"


def test_biz2x9a_builds_report_and_outputs(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    _write_sample_dxf(dxf_path)
    parsed = parse_dxf_geometry_file(dxf_path)

    report = build_geometry_probe_report([parsed])
    rows = build_geometry_candidate_csv_rows(report)
    outputs = write_geometry_probe_outputs([parsed], tmp_path, stem="geometry_probe")

    assert report["safe_for_auto_quantity"] is False
    assert report["summary"]["area_candidate_count"] == 1
    assert report["summary"]["length_candidate_count"] == 2
    assert report["summary"]["count_candidate_count"] == 1
    assert report["summary"]["dimension_candidate_count"] == 1
    assert any(row["候选类型"] == "面积候选" and row["面积候选"] == 12 for row in rows)
    assert set(outputs) == {"json", "markdown", "geometry_candidate_csv"}
    assert json.loads((tmp_path / "geometry_probe.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9a-cad-geometry-probe"
    assert (tmp_path / "geometry_probe_几何候选.csv").read_text(encoding="utf-8-sig").startswith("文件名")
