from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.api.v1 import dwg_quantity_trial
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole
from app.services import dxf_trace_review_pack as trace_pack
from app.services.drawing_quantity_confirmation import (
    ADOPT_COLUMN,
    MANUAL_FEATURE_COLUMN,
    MANUAL_NAME_COLUMN,
    MANUAL_QUANTITY_COLUMN,
    MANUAL_UNIT_COLUMN,
    QUANTITY_SOURCE_COLUMN,
    REVIEW_COLUMN,
    write_confirmation_workbook,
)
from app.services.dwg_item_listing import build_item_listing_rows, build_line_quantity_candidate_rows, build_quantity_list_rows


PASSWORD = "secret123"


def _create_user(role: str = "staff", roles: list[str] | None = None) -> User:
    db = SessionLocal()
    try:
        user = User(
            username=f"dwgtrial_{role}_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(PASSWORD),
            role=role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        for assigned_role in roles or []:
            db.add(UserRole(user_id=user.id, role=assigned_role, created_by=None, note="test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _trace_row() -> dict[str, object]:
    row: dict[str, object] = {header: "" for header in trace_pack.TRACE_REVIEW_HEADERS}
    row.update(
        {
            "复核行号": "BIZ2x9h-0001",
            trace_pack.AUTO_ACTION_COLUMN: "建议采用",
            trace_pack.ADOPT_COLUMN: "是",
            trace_pack.REVIEW_COLUMN: "通过",
            trace_pack.MANUAL_QUANTITY_COLUMN: "12.5",
            trace_pack.MANUAL_UNIT_COLUMN: "㎡",
            trace_pack.QUANTITY_SOURCE_COLUMN: "采用 CAD 几何建议量并复核标准扣减规则",
            trace_pack.MANUAL_NAME_COLUMN: "艺术造型 | 吊顶天棚",
            trace_pack.MANUAL_FEATURE_COLUMN: "吊顶部位：餐厅；吊顶形式、吊杆规格、高度：不上人艺术造型吊顶；龙骨材料种类、规格、中距：50龙骨，中距900mm",
            trace_pack.DEDUCTION_REVIEW_COLUMN: "已按标准规则复核扣减和不展开计算，本行无需额外扣减",
            "建议编号": "S-ceiling",
            "标准项目编码": "011302003",
            "标准项目名称": "艺术造型 | 吊顶天棚",
            "标准单位": "㎡",
            "标准规则类型": "area",
            "标准工程量计算规则": "按设计图示尺寸以面积计算",
            "标准规则建议量": "12.5",
            "建议单位": "㎡",
            "CAD几何公式": "sum(CAD_area_mm2) * 0.000001",
            "CAD来源图元行号": "10、20",
        }
    )
    return row


def _trace_review_workbook_bytes(tmp_path: Path) -> bytes:
    workbook_path = tmp_path / "trace-review.xlsx"
    trace_pack.write_trace_review_workbook(
        {
            "summary": {"trace_review_row_count": 1},
            "trace_review_rows": [_trace_row()],
            "blocked_rows": [],
            "trace_detail_rows": [],
        },
        workbook_path,
    )
    return workbook_path.read_bytes()


def _completed_confirmation_workbook_bytes(tmp_path: Path) -> bytes:
    workbook_path = tmp_path / "r0-r9-confirmation.xlsx"
    pack = {
        "summary": {"confirmation_row_count": 1},
        "confirmation_rows": [
            {
                "确认行号": "BIZ2x6-0001",
                ADOPT_COLUMN: "是",
                REVIEW_COLUMN: "通过",
                MANUAL_NAME_COLUMN: "块料楼地面",
                MANUAL_FEATURE_COLUMN: "面层材料品种、规格：CT-01 750x1500灰色地砖",
                MANUAL_UNIT_COLUMN: "m²",
                MANUAL_QUANTITY_COLUMN: "12.5",
                QUANTITY_SOURCE_COLUMN: "业务员按地面铺装图人工确认面积",
            }
        ],
        "feature_rows": [],
        "evidence_rows": [],
    }
    write_confirmation_workbook(pack, workbook_path)
    return workbook_path.read_bytes()


def test_dwg_quantity_trial_upload_converts_and_downloads_final_excel(client, tmp_path):
    user = _create_user("staff")
    headers = _login(client, user)

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/convert",
        headers=headers,
        files={
            "file": (
                "trace-review.xlsx",
                _trace_review_workbook_bytes(tmp_path),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["summary"]["converted_confirmation_row_count"] == 1
    assert payload["summary"]["final_ready_count"] == 1
    final_file = next(item for item in payload["files"] if item["key"] == "validation_final_xlsx")
    assert final_file["download_url"].startswith("/api/v1/admin/dwg-quantity-trial/files/")

    download_response = client.get(final_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_validates_r0_r9_confirmation_and_downloads_final_excel(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/validate-confirmation",
        headers=headers,
        files={
            "file": (
                "r0-r9-confirmation.xlsx",
                _completed_confirmation_workbook_bytes(tmp_path),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["phase"] == "BIZ-2x-r0-r9-confirmation-validation"
    assert payload["summary"]["adopted_final_row_count"] == 1
    final_file = next(item for item in payload["files"] if item["key"] == "validation_final_xlsx")
    download_response = client.get(final_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_rejects_unsupported_file_type(client):
    user = _create_user("staff")
    headers = _login(client, user)

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/convert",
        headers=headers,
        files={"file": ("trace-review.txt", b"bad", "text/plain")},
    )

    assert response.status_code == 400
    assert "仅支持上传" in response.json()["detail"]


def test_build_quantity_list_rows_keeps_frontend_to_four_fields():
    rows = build_quantity_list_rows(
        [
            {
                "识别项目编号": "P-001",
                "项目名称": "平面吊顶天棚",
                "项目特征": "面板材料品种、规格：石膏板",
                "单位": "㎡",
                "工程量": "",
            },
            {
                "识别项目编号": "P-002",
                "项目名称": "墙面防水",
                "项目特征": "防水高度：1800mm",
                "单位": "㎡",
                "工程量": "",
            },
        ],
        {
            "special_quantity_trace_rows": [
                {
                    "识别项目编号": "P-001",
                    "建议工程量": 11.0168,
                    "建议单位": "㎡",
                    "是否可复核": "是",
                },
                {
                    "识别项目编号": "P-002",
                    "建议工程量": "",
                    "建议单位": "㎡",
                    "是否可复核": "否",
                },
            ]
        },
    )

    assert rows == [
        {"项目名称": "平面吊顶天棚", "项目特征": "面板材料品种、规格：石膏板", "单位": "㎡", "工程量": "11.0168"},
        {"项目名称": "墙面防水", "项目特征": "防水高度：1800mm", "单位": "㎡", "工程量": "待算量"},
    ]
    assert set(rows[0]) == {"项目名称", "项目特征", "单位", "工程量"}


def test_dwg_quantity_trial_uploads_dwg_and_returns_item_listing(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)

    def fake_run_dwg_item_listing(*, upload_dir, output_dir, timestamp):
        assert timestamp
        assert Path(upload_dir).exists()
        assert list(Path(upload_dir).glob("*.dwg"))
        item_xlsx = Path(output_dir) / "dwg_items.xlsx"
        item_xlsx.write_bytes(b"PK fake xlsx")
        quantity_list_xlsx = Path(output_dir) / "quantity_list.xlsx"
        quantity_list_xlsx.write_bytes(b"PK fake quantity xlsx")
        region_csv = Path(output_dir) / "project_region.csv"
        region_csv.write_text("识别项目编号,区域绑定状态\nP-001,建议绑定区域，需复核\n", encoding="utf-8")
        room_csv = Path(output_dir) / "room_boundary.csv"
        room_csv.write_text("房间编号,净周长状态\nBIZ2xROOM-00001,已按开口候选扣减，需复核\n", encoding="utf-8")
        special_csv = Path(output_dir) / "special_quantity.csv"
        special_csv.write_text("专项算量编号,trace状态\nBIZ2xSQ-00001,special_quantity_trace_ready_for_manual_review\n", encoding="utf-8")
        special_confirmation_xlsx = Path(output_dir) / "special_confirmation.xlsx"
        special_confirmation_xlsx.write_bytes(b"PK fake special confirmation xlsx")
        dynamic_md = Path(output_dir) / "dynamic_itemization.md"
        dynamic_md.write_text("# R0-R9\n", encoding="utf-8")
        dynamic_json = Path(output_dir) / "dynamic_itemization.json"
        dynamic_json.write_text("{}", encoding="utf-8")
        dynamic_csv = Path(output_dir) / "dynamic_itemization.csv"
        dynamic_csv.write_text("signal_id,item_code\nSIG-0001,011102003\n", encoding="utf-8")
        dynamic_confirmation_xlsx = Path(output_dir) / "dynamic_confirmation.xlsx"
        dynamic_confirmation_xlsx.write_bytes(b"PK fake dynamic confirmation xlsx")
        return {
            "ok": True,
            "phase": "BIZ-2x-dwg-upload-item-listing",
            "generated_at": "2026-06-11 10:00:00",
            "summary": {
                "dwg_file_count": 1,
                "dxf_file_count": 1,
                "item_row_count": 1,
                "unique_standard_item_count": 1,
            },
            "item_rows": [
                {
                    "序号": 1,
                    "标准项目编码": "011302003",
                    "项目名称": "艺术造型吊顶天棚",
                    "单位": "㎡",
                    "工程量状态": "待几何算量或人工补量",
                    "CAD候选列表": [{"建议编号": "BIZ2x9cde-demo", "建议工程量": 12.5, "推荐动作": "建议优先核验"}],
                }
            ],
            "quantity_list_rows": [
                {
                    "项目名称": "墙面防水",
                    "项目特征": "防水部位：洗手间；防水高度：1800mm",
                    "单位": "㎡",
                    "工程量": "9.9",
                }
            ],
            "line_quantity_candidate_rows": [{"列项序号": 1, "建议编号": "BIZ2x9cde-demo"}],
            "project_region_binding_rows": [
                {
                    "识别项目编号": "P-001",
                    "项目名称": "墙面防水",
                    "推荐区域编号": "BIZ2xR-00001",
                    "区域绑定状态": "建议绑定区域，需复核",
                    "工程量计算方式建议": "墙面防水：区域周长 × 防水高度",
                }
            ],
            "project_region_candidate_rows": [{"识别项目编号": "P-001", "区域编号": "BIZ2xR-00001"}],
            "project_region_binding_summary": {"binding_ready_project_count": 1},
            "room_boundary_rows": [
                {
                    "房间编号": "BIZ2xROOM-00001",
                    "房间/空间名称": "洗手间",
                    "CAD周长": 6.4,
                    "净周长候选": 5.5,
                    "净周长状态": "已按开口候选扣减，需复核",
                }
            ],
            "room_opening_candidate_rows": [{"房间编号": "BIZ2xROOM-00001", "扣减长度候选": 0.9}],
            "room_boundary_summary": {"room_boundary_count": 1, "opening_candidate_count": 1},
            "special_quantity_trace_rows": [
                {
                    "专项算量编号": "BIZ2xSQ-00001",
                    "项目名称": "墙面防水",
                    "专项类型": "墙面防水面积",
                    "建议工程量": 9.9,
                    "建议单位": "㎡",
                    "trace状态": "special_quantity_trace_ready_for_manual_review",
                    "是否可复核": "是",
                    "标准规则执行状态": "standard_rule_execution_ready_for_manual_review",
                }
            ],
            "special_quantity_summary": {"special_quantity_trace_count": 1, "ready_for_manual_review_count": 1},
            "dynamic_itemization_summary": {
                "itemization_decision_count": 1,
                "llm_success_count": 0,
                "llm_fallback_count": 1,
            },
            "dynamic_itemization_stage_results": [
                {"stage": "R4", "status": "completed", "message": "deterministic fallback"}
            ],
            "dynamic_itemization_decision_rows": [
                {
                    "signal_id": "SIG-0001",
                    "standard_code": "GBT50854-2024",
                    "item_code": "011102003",
                    "decision_source": "deterministic_fallback",
                }
            ],
            "outputs": {
                "item_list_xlsx": str(item_xlsx),
                "quantity_list_xlsx": str(quantity_list_xlsx),
                "project_region_binding_csv": str(region_csv),
                "room_boundary_csv": str(room_csv),
                "special_quantity_trace_csv": str(special_csv),
                "special_trace_confirmation_xlsx": str(special_confirmation_xlsx),
                "dynamic_itemization_markdown": str(dynamic_md),
                "dynamic_itemization_json": str(dynamic_json),
                "dynamic_itemization_csv": str(dynamic_csv),
                "dynamic_itemization_confirmation_xlsx": str(dynamic_confirmation_xlsx),
            },
            "issues": [],
        }

    monkeypatch.setattr(dwg_quantity_trial, "run_dwg_item_listing", fake_run_dwg_item_listing)

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/list-items",
        headers=headers,
        files={"files": ("drawing.dwg", b"AC1018 fake dwg", "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["summary"]["item_row_count"] == 1
    assert payload["quantity_list_rows"] == [
        {
            "项目名称": "墙面防水",
            "项目特征": "防水部位：洗手间；防水高度：1800mm",
            "单位": "㎡",
            "工程量": "9.9",
        }
    ]
    assert payload["item_rows"][0]["标准项目编码"] == "011302003"
    assert payload["item_rows"][0]["CAD候选列表"][0]["建议编号"] == "BIZ2x9cde-demo"
    assert payload["line_quantity_candidate_rows"][0]["列项序号"] == 1
    assert payload["project_region_binding_rows"][0]["推荐区域编号"] == "BIZ2xR-00001"
    assert payload["project_region_binding_summary"]["binding_ready_project_count"] == 1
    assert any(item["key"] == "project_region_binding_csv" for item in payload["files"])
    assert payload["room_boundary_rows"][0]["房间编号"] == "BIZ2xROOM-00001"
    assert payload["room_boundary_summary"]["room_boundary_count"] == 1
    assert any(item["key"] == "room_boundary_csv" for item in payload["files"])
    assert payload["special_quantity_trace_rows"][0]["专项算量编号"] == "BIZ2xSQ-00001"
    assert payload["special_quantity_summary"]["ready_for_manual_review_count"] == 1
    assert any(item["key"] == "special_quantity_trace_csv" for item in payload["files"])
    assert any(item["key"] == "special_trace_confirmation_xlsx" for item in payload["files"])
    assert payload["dynamic_itemization_summary"]["itemization_decision_count"] == 1
    assert payload["dynamic_itemization_stage_results"][0]["stage"] == "R4"
    assert payload["dynamic_itemization_decision_rows"][0]["decision_source"] == "deterministic_fallback"
    assert payload["has_dynamic_itemization"] is True
    assert any(item["key"] == "dynamic_itemization_markdown" for item in payload["files"])
    assert any(item["key"] == "dynamic_itemization_confirmation_xlsx" for item in payload["files"])
    assert any(item["key"] == "quantity_list_xlsx" for item in payload["files"])
    assert payload["has_quantity_list_excel"] is True
    item_file = next(item for item in payload["files"] if item["key"] == "item_list_xlsx")
    download_response = client.get(item_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_finalize_special_traces_generates_final_excel(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    result_path = tmp_path / "BIZ2x_DWG上传列项_20260615_120000.json"
    result_path.write_text(
        """
{
  "ok": true,
  "phase": "BIZ-2x-dwg-upload-item-listing",
  "generated_at": "2026-06-15 12:00:00",
  "summary": {"special_quantity_trace_count": 1},
  "special_quantity_trace_rows": [
    {
      "专项算量编号": "BIZ2xSQ-00001",
      "识别项目编号": "P-001",
      "标准项目编码": "011302001",
      "项目名称": "平面吊顶天棚",
      "图纸项目名称": "石膏板饰面吊顶",
      "专项类型": "吊顶/天棚水平投影面积",
      "建议工程量": 16.8,
      "建议单位": "㎡",
      "trace状态": "special_quantity_trace_ready_for_manual_review",
      "是否可复核": "是",
      "标准工程量计算规则": "按设计图示尺寸以水平投影面积计算",
      "标准规则模板": "area_horizontal_projection",
      "标准规则执行状态": "standard_rule_execution_ready_for_manual_review",
      "计算公式": "绑定区域 CAD 面积",
      "计算输入": "区域面积=16.8㎡",
      "区域编号": "BIZ2xR-00001",
      "房间编号": "",
      "房间/空间名称": "",
      "阻断原因": "",
      "未解决事项": "",
      "calculation_trace": {
        "project_feature_text": "吊顶形式：石膏板；基层材料种类：轻钢龙骨",
        "region_binding": {"来源文件": "demo.dxf"}
      }
    }
  ],
  "outputs": {}
}
        """.strip(),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/finalize-special-traces",
        headers=headers,
        json={
            "result_filename": result_path.name,
            "reviews": [
                {
                    "专项算量编号": "BIZ2xSQ-00001",
                    "是否采用": "是",
                    "核验结论": "通过",
                    "项目名称": "平面吊顶天棚",
                    "项目特征": "吊顶形式：石膏板；基层材料种类：轻钢龙骨",
                    "单位": "㎡",
                    "工程量": "16.8",
                    "工程量来源说明": "专项trace复核通过，按绑定区域CAD面积",
                    "扣减/合并规则复核": "本项按水平投影面积计算，未发现需扣减洞口",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["summary"]["final_ready_count"] == 1
    final_file = next(item for item in payload["files"] if item["key"] == "validation_final_xlsx")
    download_response = client.get(final_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_finalize_selection_generates_final_excel(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    result_path = tmp_path / "BIZ2x_DWG上传列项_20260614_120000.json"
    result_path.write_text(
        """
{
  "ok": true,
  "phase": "BIZ-2x-dwg-upload-item-listing",
  "generated_at": "2026-06-14 12:00:00",
  "summary": {"item_row_count": 1},
  "item_rows": [
    {
      "序号": 1,
      "标准项目编码": "011302003",
      "项目名称": "艺术造型 | 吊顶天棚",
      "单位": "㎡",
      "项目特征字段": "吊顶部位；吊顶形式、吊杆规格、高度",
      "工程量计算规则": "按设计图示尺寸以面积计算",
      "图纸识别名称": "顶面造型吊顶",
      "图纸识别规格或做法": "餐厅不上人艺术造型吊顶",
      "来源文件": "demo.dxf",
      "来源证据": "材料表：餐厅不上人艺术造型吊顶",
      "CAD候选列表": [
        {
          "建议编号": "BIZ2x9cde-demo",
          "建议工程量": 12.5,
          "建议单位": "㎡",
          "是否可复核": "是",
          "绑定置信度": "高(8.0)",
          "推荐动作": "建议优先核验",
          "推荐原因": "同一DXF来源文件；关键词一致：吊顶",
          "CAD公式": "sum(CAD_area_mm2) * area_to_square_meter_factor",
          "CAD来源图元行号": "10、20",
          "算量证据": "公式：sum(CAD_area_mm2) * area_to_square_meter_factor，CAD行号：10、20"
        }
      ]
    }
  ],
  "outputs": {}
}
        """.strip(),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/finalize-selection",
        headers=headers,
        json={
            "result_filename": result_path.name,
            "selections": [
                {
                    "action": "采纳",
                    "row_no": 1,
                    "suggestion_key": "BIZ2x9cde-demo",
                    "project_feature": "吊顶部位：餐厅；吊顶形式、吊杆规格、高度：不上人艺术造型吊顶",
                    "quantity_source_note": "采用 CAD 几何建议量并复核标准工程量规则",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["summary"]["final_ready_count"] == 1
    final_file = next(item for item in payload["files"] if item["key"] == "validation_final_xlsx")
    download_response = client.get(final_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_regression_report_uses_latest_listing_result(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    result_path = tmp_path / "BIZ2x_DWG上传列项_20260615_130000.json"
    result_path.write_text(
        json.dumps(
            {
                "ok": True,
                "phase": "BIZ-2x-dwg-upload-item-listing",
                "generated_at": "2026-06-15 13:00:00",
                "__source_filename": result_path.name,
                "summary": {"dwg_file_count": 5},
                "project_recognition_summary": {
                    "source_signal_count": 84,
                    "matched_signal_count": 35,
                    "recognized_project_count": 1,
                    "unique_standard_item_count": 1,
                },
                "project_region_binding_summary": {"binding_ready_project_count": 1},
                "room_boundary_summary": {"room_boundary_count": 1},
                "project_rows": [
                    {
                        "识别项目编号": "P-001",
                        "图纸项目名称": "石膏板饰面吊顶",
                        "标准项目编码": "011302001",
                        "项目名称": "平面吊顶天棚",
                        "单位": "㎡",
                    }
                ],
                "special_quantity_trace_rows": [
                    {
                        "专项算量编号": "BIZ2xSQ-00001",
                        "项目名称": "平面吊顶天棚",
                        "trace状态": "special_quantity_trace_ready_for_manual_review",
                        "是否可复核": "是",
                        "标准规则执行状态": "standard_rule_execution_ready_for_manual_review",
                        "建议工程量": 16.8,
                        "建议单位": "㎡",
                    }
                ],
                "outputs": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/regression-report",
        headers=headers,
        json={"limit": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["summary"]["sample_count"] == 1
    assert payload["summary"]["ready_special_trace_count"] == 1
    assert payload["sample_rows"][0]["最终生成准备度"] == "可进入专项 trace 复核"
    workbook_file = next(item for item in payload["files"] if item["key"] == "xlsx")
    download_response = client.get(workbook_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_item_listing_rows_include_quantity_trace_suggestions():
    match_report = {
        "standard_item_candidates": [
            {
                "candidate_key": "signal-1",
                "standard_item_code": "011302003",
                "standard_item_name": "艺术造型 | 吊顶天棚",
                "chapter_name": "天棚工程",
                "unit_options": ["㎡"],
                "match_confidence": 0.82,
                "source_file": "demo.dxf",
                "source_name": "顶面造型吊顶",
                "source_spec_or_method": "艺术造型吊顶",
                "feature_fields": ["吊顶部位", "吊顶形式、吊杆规格、高度"],
                "quantity_rule_text": "按设计图示尺寸以面积计算",
                "quantity_evidence_status": "missing_quantity_measurement_needs_manual_review",
                "evidence_text": "材料表：顶面造型",
                "match_reasons": ["名称命中吊顶"],
            }
        ]
    }
    binding_report = {
        "standard_rule_traces": [
            {
                "suggestion_key": "BIZ2x9cde-demo",
                "item_code": "011302003",
                "item_name": "艺术造型 | 吊顶天棚",
                "trace_status": "standard_rule_trace_ready_for_manual_review",
                "ready_for_manual_review": True,
                "standard_rule_suggested_quantity": 12.5,
                "suggested_unit": "㎡",
                "calculation_trace": {
                    "geometry_source_key": "demo.dxf|面积候选|D-顶面造型轮廓|",
                    "source_calculation_trace": {
                        "source_key": "demo.dxf|面积候选|D-顶面造型轮廓|",
                        "mapping_business_hint": "天棚/吊顶面积候选",
                        "matched_reason": "图层/块名包含顶面/天花/吊顶关键词",
                        "formula": "sum(CAD_area_mm2) * area_to_square_meter_factor",
                        "sample_line_numbers": [10, 20],
                    },
                },
            }
        ]
    }

    rows = build_item_listing_rows(match_report, binding_report)

    assert rows[0]["工程量状态"] == "有CAD几何建议量，需复核"
    assert rows[0]["逐条绑定状态"] == "已逐条绑定CAD建议量，需复核"
    assert rows[0]["绑定建议编号"] == "BIZ2x9cde-demo"
    assert rows[0]["系统建议工程量"] == "12.5㎡"
    assert rows[0]["建议量状态"] == "已按来源线索绑定1条可复核建议量，未作为最终工程量"
    assert "CAD行号：10、20" in rows[0]["算量证据"]
    assert rows[0]["CAD候选数量"] == 1
    assert rows[0]["默认选择建议编号"] == "BIZ2x9cde-demo"
    assert rows[0]["CAD候选列表"][0]["建议编号"] == "BIZ2x9cde-demo"
    assert rows[0]["CAD候选列表"][0]["推荐动作"] == "建议优先核验"
    assert rows[0]["CAD候选列表"][0]["CAD公式"] == "sum(CAD_area_mm2) * area_to_square_meter_factor"
    candidate_rows = build_line_quantity_candidate_rows(rows)
    assert candidate_rows[0]["列项序号"] == 1
    assert candidate_rows[0]["建议编号"] == "BIZ2x9cde-demo"


def test_dwg_item_listing_keeps_uncertain_quantity_candidates_unbound():
    match_report = {
        "standard_item_candidates": [
            {
                "candidate_key": "signal-1",
                "standard_item_code": "011302001",
                "standard_item_name": "平面吊顶 | 天棚",
                "chapter_name": "天棚工程",
                "unit_options": ["㎡"],
                "match_confidence": 0.8,
                "source_file": "02_通用节点.dxf",
                "source_name": "石膏板刮瓷刷无机涂料材料说明",
                "source_spec_or_method": "本图为不上人吊顶详图",
                "feature_fields": ["吊顶形式、吊杆规格、高度"],
                "quantity_rule_text": "按设计图示尺寸以水平投影面积计算",
                "quantity_evidence_status": "missing_quantity_measurement_needs_manual_review",
                "evidence_text": "材料说明",
                "match_reasons": ["名称命中吊顶"],
            }
        ]
    }
    binding_report = {
        "standard_rule_traces": [
            {
                "suggestion_key": "BIZ2x9cde-node",
                "item_code": "011302001",
                "item_name": "平面吊顶 | 天棚",
                "trace_status": "standard_rule_trace_ready_for_manual_review",
                "ready_for_manual_review": True,
                "standard_rule_suggested_quantity": 0.0464,
                "suggested_unit": "㎡",
                "calculation_trace": {
                    "geometry_source_key": "02_通用节点.dxf|面积候选|D-顶面造型轮廓|",
                    "source_calculation_trace": {
                        "source_key": "02_通用节点.dxf|面积候选|D-顶面造型轮廓|",
                        "mapping_business_hint": "天棚/吊顶面积候选",
                        "matched_reason": "图层/块名包含顶面/天花/吊顶关键词",
                        "formula": "sum(CAD_area_mm2) * area_to_square_meter_factor",
                        "sample_line_numbers": [100],
                    },
                },
            }
        ]
    }

    rows = build_item_listing_rows(match_report, binding_report)

    assert rows[0]["工程量状态"] == "有CAD候选量，需选择"
    assert rows[0]["逐条绑定状态"] == "有同类CAD候选，需人工选择"
    assert rows[0]["系统建议工程量"] == ""
    assert "候选建议量：0.0464㎡" in rows[0]["建议量状态"]
    assert "节点/大样" in rows[0]["绑定说明"]
    assert rows[0]["CAD候选数量"] == 1
    assert rows[0]["默认选择建议编号"] == ""
    assert rows[0]["CAD候选列表"][0]["推荐动作"] == "需人工选择"
    assert rows[0]["CAD候选列表"][0]["建议工程量"] == 0.0464
