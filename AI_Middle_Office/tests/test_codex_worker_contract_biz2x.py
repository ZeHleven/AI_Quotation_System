from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

from app.services.codex_worker_contract import (
    CODEX_WORKER_SCHEMA_VERSION,
    run_codex_worker_contract,
    validate_codex_worker_result,
    write_codex_worker_contract_outputs,
)


def test_codex_worker_contract_exports_valid_four_field_excel(tmp_path):
    payload = _valid_codex_result()
    input_path = tmp_path / "codex_result.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = run_codex_worker_contract(input_path, tmp_path / "output")

    assert result["ok"] is True
    assert result["status"] == "exported"
    assert Path(result["validation_report"]).exists()
    assert Path(result["quantity_list_xlsx"]).exists()
    assert Path(result["quantity_list_csv"]).exists()
    assert result["quantity_list_row_count"] == 2

    workbook = load_workbook(result["quantity_list_xlsx"])
    sheet = workbook.active
    assert [cell.value for cell in sheet[1]] == ["项目名称", "项目特征", "单位", "工程量"]
    assert sheet["A2"].value == "职工餐厅墙面 CT-1 地砖湿贴（块料墙、柱面）"
    assert sheet["C2"].value == "m²"


def test_codex_worker_contract_blocks_missing_four_field_value(tmp_path):
    payload = _valid_codex_result()
    payload["quantity_list_rows"][0]["单位"] = ""

    result = write_codex_worker_contract_outputs(payload, tmp_path)

    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert not (tmp_path / "four_field.xlsx").exists()
    report = json.loads(Path(result["validation_report"]).read_text(encoding="utf-8"))
    assert report["errors"][0]["code"] == "REQUIRED_FIELD_EMPTY"
    assert report["errors"][0]["field"] == "单位"


def test_codex_worker_contract_blocks_non_construction_in_quantity_rows(tmp_path):
    payload = _valid_codex_result()
    payload["quantity_list_rows"][1]["itemizability_status"] = "非施工项"

    result = write_codex_worker_contract_outputs(payload, tmp_path)

    assert result["ok"] is False
    assert not (tmp_path / "four_field.xlsx").exists()
    report = json.loads(Path(result["validation_report"]).read_text(encoding="utf-8"))
    assert any(error["code"] == "NON_CONSTRUCTION_IN_QUANTITY_ROWS" for error in report["errors"])


def test_codex_worker_contract_warns_missing_evidence_but_exports(tmp_path):
    payload = _valid_codex_result()
    payload["quantity_list_rows"][1]["evidence_refs"] = ["EV-NOT-FOUND"]

    result = write_codex_worker_contract_outputs(payload, tmp_path)

    assert result["ok"] is True
    assert Path(result["quantity_list_xlsx"]).exists()
    report = json.loads(Path(result["validation_report"]).read_text(encoding="utf-8"))
    assert report["summary"]["warning_count"] == 1
    assert report["warnings"][0]["code"] == "EVIDENCE_REF_NOT_FOUND"


def test_codex_worker_contract_validates_top_level_shape():
    report = validate_codex_worker_result(
        {
            "schema_version": "wrong",
            "status": "failed",
            "quantity_list_rows": {},
        }
    )

    assert report["ok"] is False
    assert {error["code"] for error in report["errors"]} >= {
        "INVALID_SCHEMA_VERSION",
        "INVALID_STATUS",
        "QUANTITY_ROWS_NOT_ARRAY",
    }


def _valid_codex_result() -> dict:
    return {
        "schema_version": CODEX_WORKER_SCHEMA_VERSION,
        "job_id": "codexpdf_test",
        "status": "succeeded",
        "source_files": [{"file_name": "source.pdf", "page_count": 1, "sha256": "fake"}],
        "summary": {
            "view_count": 2,
            "evidence_count": 2,
            "quantity_list_row_count": 2,
            "manual_review_count": 2,
            "filtered_non_construction_count": 1,
        },
        "quantity_list_rows": [
            {
                "row_id": "CODPDF-ITEM-000001",
                "项目名称": "职工餐厅墙面 CT-1 地砖湿贴（块料墙、柱面）",
                "项目特征": "空间：职工餐厅；材料：CT-1；做法：墙面湿贴；来源：EV-000001",
                "单位": "m²",
                "工程量": "约20.5，待复核",
                "itemizability_status": "施工项",
                "needs_manual_review": True,
                "evidence_refs": ["EV-000001"],
            },
            {
                "row_id": "CODPDF-ITEM-000002",
                "项目名称": "职工餐厅门套 MR-1 木饰面收边安装（木门窗套）",
                "项目特征": "空间：职工餐厅；材料：MR-1；做法：门套木饰面收边安装；来源：EV-000002",
                "单位": "m²",
                "工程量": "待复核",
                "itemizability_status": "安装项",
                "needs_manual_review": True,
                "evidence_refs": ["EV-000002"],
            },
        ],
        "filtered_items": [
            {
                "item_id": "CODPDF-FILTER-000001",
                "name": "职工餐厅餐椅布置",
                "itemizability_status": "非施工项",
                "filter_reason": "识别为活动家具摆放，不进入施工清单",
                "evidence_refs": ["EV-000003"],
            }
        ],
        "evidence_index": [
            {"evidence_id": "EV-000001", "view_id": "p001_view008", "text": "墙面 CT-1 湿贴"},
            {"evidence_id": "EV-000002", "view_id": "p001_view008", "text": "门套 MR-1"},
            {"evidence_id": "EV-000003", "view_id": "p001_view001", "text": "餐椅布置"},
        ],
        "standard_mapping_rows": [],
        "issues": [],
        "metrics": {},
    }
