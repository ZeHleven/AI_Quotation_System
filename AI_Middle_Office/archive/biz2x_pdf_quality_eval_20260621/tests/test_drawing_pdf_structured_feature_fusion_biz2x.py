from __future__ import annotations

import json
from pathlib import Path

from app.services.drawing_pdf_structured_feature_fusion import (
    build_structured_feature_fusion_report,
    write_structured_feature_fusion_outputs,
)


def _diameter_results() -> dict[str, object]:
    return {
        "evidence_rows": [
            {
                "evidence_id": "PDFCAP-DN40",
                "source_file": "04_water_electrical_page1",
                "page": "1",
                "tile_id": "dn_de_rows_04_06",
                "vision_pass": "fixture_valve_schedule",
                "item_hint": "DN40",
                "spec_or_method": "De50",
                "text": "DN40 | De50 |",
                "confidence": 0.78,
            },
            {
                "evidence_id": "PDFCAP-DN20",
                "source_file": "04_water_electrical_page1",
                "page": "1",
                "tile_id": "dn_de_rows_01_03",
                "vision_pass": "fixture_valve_schedule",
                "item_hint": "DN20",
                "spec_or_method": "De25",
                "text": "DN20 | De25 | 6分",
                "confidence": 0.78,
            },
        ]
    }


def test_structured_feature_fusion_builds_supply_pipe_rows_from_visible_note_and_table():
    report = build_structured_feature_fusion_report(
        _diameter_results(),
        note_texts=[
            "8. 室内给水支管采用SUS304薄壁不锈钢管，DN小于100时采用双卡压连接。"
        ],
        source_name="unit-test",
    )

    assert report["summary"]["answer_rows_used"] is False
    assert report["summary"]["target_fields_sent_to_model"] is False
    assert report["summary"]["quantity_status"] == "deferred_until_three_fields_accepted"
    assert report["summary"]["diameter_pair_count"] == 2
    assert report["summary"]["supply_row_count"] == 2
    assert report["summary"]["drain_row_count"] == 0

    by_spec = {row["spec_or_method"]: row for row in report["evidence_rows"]}
    row = by_spec["材质：SUS304薄壁不锈钢管；规格、型号：DN40"]
    assert row["item_hint"] == "给水管"
    assert row["suggested_unit"] == "m"
    assert row["source_kind"] == "pdf_feature_precision_structured_table_fusion"
    assert row["task_nos"] == "84;85;86"
    assert "PDFCAP-DN40" in row["source_evidence_ids"]
    assert "工程量" not in row["spec_or_method"]


def test_structured_feature_fusion_can_limit_drain_rows_to_de_values_visible_in_note():
    report = build_structured_feature_fusion_report(
        {
            "evidence_rows": [
                {"evidence_id": "A", "text": "DN50 | De63 |"},
                {"evidence_id": "B", "text": "DN100 | De110 |"},
            ]
        },
        note_texts=[
            "9. 污水横管及支管采用柔性铸铁管，不锈钢卡箍连接。排水支管坡度如下：De110 i=0.02。"
        ],
        emit_supply=False,
        emit_drain="note_de",
    )

    assert report["summary"]["supply_row_count"] == 0
    assert report["summary"]["drain_row_count"] == 1
    assert report["summary"]["status_counts"] == {"drain_cast_iron_note_plus_note_de": 1}
    assert report["evidence_rows"][0]["item_hint"] == "排水管"
    assert "De110" in report["evidence_rows"][0]["spec_or_method"]
    assert all("De63" not in row["spec_or_method"] for row in report["evidence_rows"])


def test_structured_feature_fusion_outputs_json_csv_markdown_and_xlsx(tmp_path: Path):
    report = build_structured_feature_fusion_report(
        _diameter_results(),
        note_texts=["8. 室内给水支管采用SUS304薄壁不锈钢管，DN小于100时采用双卡压连接。"],
    )

    outputs = write_structured_feature_fusion_outputs(report, tmp_path, stem="fusion")

    for key in ("json", "evidence_csv", "markdown", "xlsx"):
        assert Path(outputs[key]).exists()
    payload = json.loads(Path(outputs["json"]).read_text(encoding="utf-8"))
    assert payload["summary"]["evidence_count"] == 2
    assert payload["evidence_rows"][0]["item_hint"] == "给水管"
