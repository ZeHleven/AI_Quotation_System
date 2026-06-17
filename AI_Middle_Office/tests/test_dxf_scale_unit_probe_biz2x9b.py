from __future__ import annotations

import json

from app.services.dxf_scale_unit_probe import (
    build_manual_scale_unit_confirmation_report,
    build_manual_scale_unit_confirmation_csv_rows,
    build_scale_unit_evidence_csv_rows,
    build_scale_unit_probe_report,
    write_manual_scale_unit_confirmation_outputs,
    write_scale_unit_probe_outputs,
)


def _text_report() -> dict[str, object]:
    return {
        "summary": {},
        "files": [
            {
                "file_name": "sample.dxf",
                "top_layers_by_entity_count": [{"name": "W-图框", "count": 12}],
                "layers_sample": ["W-图框", "W-尺寸、标高"],
                "text_samples": [],
                "important_texts": [],
            }
        ],
    }


def _geometry_report() -> dict[str, object]:
    return {
        "summary": {
            "dimension_candidate_count": 6,
            "top_layers_by_geometry_count": [{"name": "W-图框", "count": 20}, {"name": "W-尺寸、标高", "count": 30}],
        },
        "files": [
            {
                "file_name": "sample.dxf",
                "dimension_candidate_count": 6,
                "dimension_candidates": [
                    {"layer": "W-尺寸、标高", "measurement": "3000"},
                    {"layer": "W-尺寸、标高", "measurement": "1200"},
                ],
            }
        ],
    }


def test_biz2x9b_confirms_scale_unit_frame_and_dimension():
    records = [
        {"source_file": "sample.dxf", "text": "比例 1:50", "layer": "W-图框", "layout": "Model", "x": 10, "y": 10, "line_number": 10},
        {"source_file": "sample.dxf", "text": "除注明外尺寸均以毫米为单位", "layer": "设计说明", "layout": "Model", "x": 20, "y": 20, "line_number": 20},
    ]

    report = build_scale_unit_probe_report(text_report=_text_report(), geometry_report=_geometry_report(), text_records=records)

    assert report["safe_for_auto_quantity"] is False
    assert report["ready_for_geometry_quantity_probe"] is True
    assert report["summary"]["scale_status"] == "confirmed_single_scale"
    assert report["summary"]["unit_status"] == "confirmed_drawing_unit"
    assert report["summary"]["frame_status"] == "frame_layer_detected"
    assert report["summary"]["dimension_status"] == "dimension_entities_detected"


def test_biz2x9b_does_not_treat_material_ratio_as_scale():
    records = [
        {"source_file": "sample.dxf", "text": "比例", "layer": "W-图框", "layout": "Model", "x": 10, "y": 10, "line_number": 10},
        {"source_file": "sample.dxf", "text": "1:2水泥砂浆找平层 12mm厚", "layer": "材料", "layout": "Model", "x": 20, "y": 20, "line_number": 20},
    ]

    report = build_scale_unit_probe_report(text_report={"files": []}, geometry_report={"summary": {}}, text_records=records)

    assert report["ready_for_geometry_quantity_probe"] is False
    assert report["summary"]["scale_status"] == "scale_label_only_needs_manual_value"
    assert report["summary"]["unit_status"] == "weak_unit_mentions_only"
    assert "scale_label_without_value" in report["summary"]["risk_flags"]


def test_biz2x9b_writes_outputs(tmp_path):
    records = [
        {"source_file": "sample.dxf", "text": "比例 1:50", "layer": "W-图框", "layout": "Model", "x": 10, "y": 10, "line_number": 10},
        {"source_file": "sample.dxf", "text": "单位：mm", "layer": "W-图框", "layout": "Model", "x": 20, "y": 20, "line_number": 20},
    ]
    report = build_scale_unit_probe_report(text_report=_text_report(), geometry_report=_geometry_report(), text_records=records)

    rows = build_scale_unit_evidence_csv_rows(report)
    outputs = write_scale_unit_probe_outputs(report, tmp_path, stem="scale_unit")

    assert rows
    assert set(outputs) == {"json", "markdown", "evidence_csv"}
    assert json.loads((tmp_path / "scale_unit.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9b-scale-unit-frame-probe"
    assert (tmp_path / "scale_unit_证据清单.csv").read_text(encoding="utf-8-sig").startswith("证据类型")


def test_biz2x9b_manual_confirmation_enables_geometry_probe_but_not_final_quantity(tmp_path):
    scale_unit_report = build_scale_unit_probe_report(text_report=_text_report(), geometry_report=_geometry_report(), text_records=[])
    report = build_manual_scale_unit_confirmation_report(
        scale_unit_report=scale_unit_report,
        geometry_report=_geometry_report(),
        drawing_unit="mm",
        model_space_scale="1:1",
        allow_geometry_quantity_for_all_files=True,
    )
    rows = build_manual_scale_unit_confirmation_csv_rows(report)
    outputs = write_manual_scale_unit_confirmation_outputs(report, tmp_path, stem="manual_confirmation")

    assert report["ready_for_geometry_quantity_probe"] is True
    assert report["safe_for_auto_quantity"] is False
    assert report["manual_confirmation"]["unit_to_meter_factor"] == 0.001
    assert rows[0]["是否允许进入几何建议量探测"] == "是"
    assert set(outputs) == {"json", "markdown", "confirmation_csv"}
    assert json.loads((tmp_path / "manual_confirmation.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9b-1-manual-scale-unit-confirmation"
