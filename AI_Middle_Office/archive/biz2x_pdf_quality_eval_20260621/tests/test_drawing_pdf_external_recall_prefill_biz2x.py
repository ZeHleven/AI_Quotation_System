from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import load_workbook

from app.services.drawing_pdf_external_recall_prefill import (
    build_external_recall_prefill_report,
    load_external_recall_template_rows,
    write_external_recall_prefill_outputs,
)
from app.services.drawing_pdf_external_recall_template import (
    build_external_recall_template,
    write_external_recall_template_outputs,
)
from app.services.drawing_pdf_external_recall_template_status import build_external_recall_template_status
from app.services.drawing_pdf_gap_recall_importer import (
    build_gap_recall_external_import_report,
    load_external_recall_results,
)
from scripts import biz2x_pdf_external_recall_prefill


def _recall_plan(tmp_path: Path) -> dict[str, object]:
    image = tmp_path / "page.png"
    image.write_bytes(b"fake image bytes")
    return {
        "plan_rows": [
            {
                "task_no": 1,
                "gap_no": 11,
                "gap_priority": "P1_missing_core",
                "gap_type": "missing_candidate",
                "recommended_pass": "door_window_demolition",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_id": "p001_whole",
                "tile_type": "whole_page_preview",
                "image_path": str(image),
                "answer_item_name": "answer-only door name",
                "answer_feature": "answer-only feature",
                "answer_unit": "set",
            },
            {
                "task_no": 2,
                "gap_no": 12,
                "gap_priority": "P2_missing_mep",
                "gap_type": "missing_candidate",
                "recommended_pass": "electrical_mep",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_id": "p001_other",
                "tile_type": "grid",
                "image_path": str(image),
                "answer_item_name": "answer-only electrical",
                "answer_feature": "answer-only electrical feature",
                "answer_unit": "m",
            },
        ]
    }


def _v2_report() -> dict[str, object]:
    return {
        "evidence_rows": [
            {
                "evidence_id": "LOCAL-001",
                "source_file": "drawing.pdf",
                "page": "1",
                "tile_id": "p001_whole",
                "evidence_type": "demolition",
                "discipline": "decoration",
                "raw_item_name": "local stainless glass door demolition",
                "space": "hall",
                "material_codes": [],
                "spec_or_method": "remove frame, leaf, and hardware",
                "suggested_unit": "set",
                "evidence_text": "visible note: stainless glass door demolition",
                "confidence": 0.91,
                "raw": {"evidence_role": "construction_note"},
            }
        ]
    }


def _template_xlsx(tmp_path: Path) -> str:
    report = build_external_recall_template(_recall_plan(tmp_path))
    return write_external_recall_template_outputs(report, tmp_path / "template", stem="template")["xlsx"]


def test_external_recall_prefill_uses_local_evidence_not_answer_columns(tmp_path: Path):
    rows = load_external_recall_template_rows(_template_xlsx(tmp_path))

    report = build_external_recall_prefill_report(rows, _v2_report())

    assert report["summary"]["template_row_count"] == 2
    assert report["summary"]["prefilled_row_count"] == 1
    assert report["summary"]["unmatched_row_count"] == 1
    assert report["summary"]["answer_columns_used_for_prefill"] is False
    first = report["template_rows"][0]
    assert first["answer_item_name"] == "answer-only door name"
    assert first["item_hint"] == "local stainless glass door demolition"
    assert first["spec_or_method"] == "remove frame, leaf, and hardware"
    assert first["text"] == "visible note: stainless glass door demolition"
    assert first["reason"] == "local_prefill_exact_tile:LOCAL-001"


def test_external_recall_prefill_outputs_are_importable(tmp_path: Path):
    report = build_external_recall_prefill_report(
        load_external_recall_template_rows(_template_xlsx(tmp_path)),
        _v2_report(),
    )
    outputs = write_external_recall_prefill_outputs(report, tmp_path / "out", stem="prefilled")

    assert Path(outputs["xlsx"]).exists()
    workbook = load_workbook(outputs["xlsx"])
    assert workbook["external_recall_template"]["P2"].value == "local stainless glass door demolition"
    assert workbook["external_recall_template"]["U2"].value == "visible note: stainless glass door demolition"

    external_results = load_external_recall_results(outputs["xlsx"])
    status = build_external_recall_template_status(external_results)
    imported = build_gap_recall_external_import_report(
        external_results,
        recall_plan=_recall_plan(tmp_path),
        source_name="local-prefill",
    )

    assert status["summary"]["importable_row_count"] == 1
    assert imported["summary"]["evidence_count"] == 1
    assert imported["evidence_rows"][0]["item_hint"] == "local stainless glass door demolition"


def test_external_recall_source_page_prefill_filters_generic_evidence(tmp_path: Path):
    rows = load_external_recall_template_rows(_template_xlsx(tmp_path))
    v2_report = {
        "evidence_rows": [
            {
                "evidence_id": "LOCAL-GENERIC",
                "source_file": "drawing.pdf",
                "page": "1",
                "tile_id": "p001_unrelated",
                "evidence_type": "demolition",
                "discipline": "decoration",
                "raw_item_name": "拆除",
                "spec_or_method": "拆除",
                "suggested_unit": "㎡",
                "evidence_text": "拆除",
                "confidence": 1.0,
                "raw": {"evidence_role": "demolition"},
            }
        ]
    }

    report = build_external_recall_prefill_report(rows, v2_report, match_mode="source_page")

    assert report["summary"]["prefilled_row_count"] == 0
    assert report["summary"]["not_prefilled_row_count"] == 2
    assert report["summary"]["filtered_low_quality_count"] == 1
    assert report["summary"]["status_counts"]["filtered_low_quality_local_evidence"] == 1
    assert report["template_rows"][0].get("item_hint", "") == ""


def test_external_recall_source_page_prefill_accepts_object_evidence(tmp_path: Path):
    rows = load_external_recall_template_rows(_template_xlsx(tmp_path))
    v2_report = {
        "evidence_rows": [
            {
                "evidence_id": "LOCAL-DOOR",
                "source_file": "drawing.pdf",
                "page": "1",
                "tile_id": "p001_unrelated",
                "evidence_type": "demolition",
                "discipline": "decoration",
                "raw_item_name": "拆除不锈钢玻璃门",
                "spec_or_method": "拆除门套、门扇及五金",
                "suggested_unit": "套",
                "evidence_text": "拆除不锈钢玻璃门",
                "confidence": 0.75,
                "raw": {"evidence_role": "demolition"},
            }
        ]
    }

    report = build_external_recall_prefill_report(rows, v2_report, match_mode="source_page")

    assert report["summary"]["prefilled_row_count"] == 1
    assert report["template_rows"][0]["item_hint"] == "拆除不锈钢玻璃门"
    assert report["template_rows"][0]["reason"] == "local_prefill_source_page:LOCAL-DOOR"


def test_external_recall_prefill_cli(tmp_path: Path, monkeypatch, capsys):
    template_xlsx = _template_xlsx(tmp_path)
    v2_json = tmp_path / "v2.json"
    v2_json.write_text(json.dumps(_v2_report(), ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "cli_out"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "biz2x_pdf_external_recall_prefill.py",
            "--external-template",
            template_xlsx,
            "--v2-json",
            str(v2_json),
            "--output-dir",
            str(output_dir),
            "--timestamp",
            "fixed",
        ],
    )

    assert biz2x_pdf_external_recall_prefill.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["summary"]["prefilled_row_count"] == 1
    assert Path(payload["outputs"]["xlsx"]).exists()
