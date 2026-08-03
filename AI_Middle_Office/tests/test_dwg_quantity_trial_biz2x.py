from __future__ import annotations

import json
import uuid
from dataclasses import replace
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
from app.services.drawing_pdf_direct_itemizer import (
    _direct_itemization_prompt_addition,
    build_four_field_rows,
    build_standard_mapping_rows,
    run_pdf_direct_itemization,
    select_images_for_pdf_itemization,
)
from app.services.drawing_pdf_evidence_pipeline import (
    build_pdf_basic_parse_report,
    build_pdf_render_report,
    build_pdf_tile_report,
    build_dwg_pdf_match_report,
    build_dxf_pdf_fusion_report,
    build_pdf_visual_evidence_report,
    select_tiles_for_llm_visual,
)


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
                "图纸项目名称": "石膏板饰面吊顶",
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
        {"项目名称": "石膏板饰面吊顶（平面吊顶天棚）", "项目特征": "面板材料品种、规格：石膏板", "单位": "㎡", "工程量": "11.0168"},
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
    assert any(item["key"] == "project_region_binding_csv" for item in payload["debug_files"])
    assert payload["room_boundary_rows"][0]["房间编号"] == "BIZ2xROOM-00001"
    assert payload["room_boundary_summary"]["room_boundary_count"] == 1
    assert any(item["key"] == "room_boundary_csv" for item in payload["debug_files"])
    assert payload["special_quantity_trace_rows"][0]["专项算量编号"] == "BIZ2xSQ-00001"
    assert payload["special_quantity_summary"]["ready_for_manual_review_count"] == 1
    assert any(item["key"] == "special_quantity_trace_csv" for item in payload["debug_files"])
    assert any(item["key"] == "special_trace_confirmation_xlsx" for item in payload["debug_files"])
    assert payload["dynamic_itemization_summary"]["itemization_decision_count"] == 1
    assert payload["dynamic_itemization_stage_results"][0]["stage"] == "R4"
    assert payload["dynamic_itemization_decision_rows"][0]["decision_source"] == "deterministic_fallback"
    assert payload["has_dynamic_itemization"] is True
    assert any(item["key"] == "dynamic_itemization_markdown" for item in payload["debug_files"])
    assert any(item["key"] == "dynamic_itemization_confirmation_xlsx" for item in payload["debug_files"])
    assert any(item["key"] == "quantity_list_xlsx" for item in payload["files"])
    assert {item["key"] for item in payload["files"]} == {"quantity_list_xlsx"}
    assert payload["has_quantity_list_excel"] is True
    item_file = next(item for item in payload["debug_files"] if item["key"] == "item_list_xlsx")
    download_response = client.get(item_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_finalizes_low_risk_mvp_and_downloads_backfilled_excel(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    result_dir = tmp_path / "debug" / "20260617_120000"
    result_dir.mkdir(parents=True)
    result_path = result_dir / "BIZ2x_DWG上传列项_20260617_120000.json"
    result_path.write_text(
        json.dumps(
            {
                "ok": True,
                "phase": "BIZ-2x-dwg-upload-item-listing",
                "generated_at": "2026-06-17 12:00:00",
                "item_rows": [
                    {
                        "序号": 1,
                        "标准项目编码": "011102003",
                        "项目名称": "地砖铺贴（块料楼地面）",
                        "单位": "㎡",
                        "匹配置信度": 0.88,
                        "图纸识别名称": "CT-01 地砖铺贴",
                        "图纸识别规格或做法": "餐厅 600x1200 灰色地砖",
                        "项目特征字段": "部位；面层材料品种、规格、颜色",
                        "工程量计算规则": "按设计图示尺寸以面积计算",
                        "来源文件": "sample.dxf",
                        "CAD候选列表": [
                            {
                                "建议编号": "S-floor",
                                "建议工程量": 25.2,
                                "建议单位": "㎡",
                                "trace状态": "standard_rule_trace_ready_for_manual_review",
                                "是否可复核": "是",
                                "绑定置信度": "高",
                                "推荐原因": "同标准项目且地面图层语义匹配",
                                "CAD公式": "sum(CAD_area_mm2) * area_to_square_meter_factor",
                                "CAD来源": "sample.dxf / 面积候选 / F-地面铺装",
                            }
                        ],
                    }
                ],
                "quantity_list_rows": [
                    {
                        "项目名称": "地砖铺贴（块料楼地面）",
                        "项目特征": "餐厅 600x1200 灰色地砖",
                        "单位": "㎡",
                        "工程量": "待算量",
                    }
                ],
                "low_risk_quantity_mvp_rows": [
                    {
                        "mvp_category": "floor_area",
                        "mvp_category_label": "地面面积",
                        "suggestion_key": "S-floor",
                        "source_file": "sample.dxf",
                        "layer": "F-地面铺装",
                        "ready_for_manual_review": True,
                        "suggested_quantity": 25.2,
                        "suggested_unit": "㎡",
                    }
                ],
                "outputs": {},
                "issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/finalize-low-risk-mvp",
        headers=headers,
        json={
            "result_filename": "debug/20260617_120000/BIZ2x_DWG上传列项_20260617_120000.json",
            "reviews": [
                {
                    "MVP绑定编号": "BIZ2xMVPB-0001-01",
                    ADOPT_COLUMN: "是",
                    REVIEW_COLUMN: "通过",
                    MANUAL_QUANTITY_COLUMN: "25.2",
                    MANUAL_UNIT_COLUMN: "㎡",
                    MANUAL_FEATURE_COLUMN: "餐厅 600x1200 灰色地砖",
                    QUANTITY_SOURCE_COLUMN: "按低风险 MVP 地面面积候选复核采用",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["has_final_excel"] is True
    assert payload["summary"]["merged_updated_row_count"] == 1
    final_file = next(item for item in payload["files"] if item["key"] == "validation_final_xlsx")
    download_response = client.get(final_file["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_pdf_direct_itemization_maps_llm_items_to_four_fields(monkeypatch):
    def fake_search_standard_index(query, limit=5):
        assert "CT-02" in query
        return [
            {
                "standard_code": "GBT50854-2024",
                "item_code": "011102003",
                "item_name": "块料楼地面",
                "chapter_name": "楼地面装饰工程",
                "unit_options": ["㎡"],
                "feature_fields": [{"name": "面层材料品种、规格、颜色"}],
                "score": 12.5,
                "match_reason": "standard_search:块料楼地面",
            }
        ]

    monkeypatch.setattr("app.services.drawing_pdf_direct_itemizer.search_standard_index", fake_search_standard_index)

    mapping_rows = build_standard_mapping_rows(
        [
            {
                "识别编号": "PDFITEM-000001",
                "图纸项目名称": "地砖铺贴",
                "空间/部位": "餐厅",
                "材料编号": "CT-02",
                "规格/做法": "600X1200灰色地砖",
                "证据文本": "CT-02 600X1200灰色地砖",
                "建议单位": "",
            }
        ]
    )
    four_field_rows = build_four_field_rows(mapping_rows)

    assert mapping_rows[0]["映射状态"] == "standard_mapped"
    assert mapping_rows[0]["标准项目编码"] == "011102003"
    assert four_field_rows == [
        {
            "项目名称": "地砖铺贴（块料楼地面）",
            "项目特征": "面层材料品种、规格、颜色：空间/部位：餐厅；材料编号：CT-02；规格/做法：600X1200灰色地砖；图纸证据：CT-02 600X1200灰色地砖",
            "单位": "㎡",
            "工程量": "待算量",
        }
    ]


def test_run_pdf_direct_itemization_writes_business_excel(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    (pdf_dir / "drawing.pdf").write_bytes(b"%PDF-1.4 fake pdf")
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"fake png bytes")

    def fake_parse_report(pdf_files):
        return {
            "summary": {"pdf_file_count": len(list(pdf_files)), "page_count": 1, "text_row_count": 0},
            "file_rows": [{"file_name": "drawing.pdf", "path": str(pdf_dir / "drawing.pdf"), "page_count": 1}],
            "page_rows": [{"source_file": "drawing.pdf", "page": 1, "width_pt": 100, "height_pt": 100}],
            "text_rows": [],
        }

    def fake_render_report(parse_report, page_dir, render_dpi=350):
        return {
            "summary": {"render_status": "rendered", "rendered_page_count": 1},
            "render_rows": [
                {
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "png_path": str(image_path),
                    "status": "rendered",
                    "image_width_px": 100,
                    "image_height_px": 100,
                }
            ],
        }

    def fake_tile_report(parse_report, render_report, tile_dir, grid_size=3):
        return {
            "summary": {"tile_count": 1},
            "tile_rows": [
                {
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_id": "p001_whole",
                    "tile_type": "whole_page_preview",
                    "image_path": str(image_path),
                    "priority": 100,
                }
            ],
        }

    def fake_itemization_call(*, image_path, image_row, page_texts, style_prompt_text="", trace_id=None):
        assert "材料编号不同要拆" in style_prompt_text
        return {
            "drawing_items": [
                {
                    "item_name": "地砖铺贴",
                    "space": "餐厅",
                    "material_codes": ["CT-02"],
                    "spec_or_method": "600X1200灰色地砖",
                    "evidence_text": "CT-02 600X1200灰色地砖",
                    "confidence": 0.9,
                    "needs_manual_review": False,
                }
            ]
        }

    def fake_search_standard_index(query, limit=5):
        return [
            {
                "standard_code": "GBT50854-2024",
                "item_code": "011102003",
                "item_name": "块料楼地面",
                "chapter_name": "楼地面装饰工程",
                "unit_options": ["㎡"],
                "feature_fields": [{"name": "面层材料品种、规格、颜色"}],
                "score": 12.5,
                "match_reason": "standard_search:块料楼地面",
            }
        ]

    def fake_ai_quantity_report(*, parse_report, tile_report, mapping_rows, max_visual_images=None, trace_id=None):
        return {
            "ok": True,
            "summary": {
                "ai_quantity_status": "candidate_ready_for_manual_review",
                "selected_image_count": 1,
                "standard_mapping_row_count": len(mapping_rows),
                "ai_quantity_candidate_count": 1,
                "candidate_needs_manual_review_count": 1,
            },
            "suggestion_rows": [
                {
                    "候选量编号": "PDFAQ-000001",
                    "识别编号": "PDFITEM-000001",
                    "项目名称": "地砖铺贴",
                    "国标项目名称": "块料楼地面",
                    "建议工程量": "42.6",
                    "单位": "㎡",
                    "工程量显示值": "AI建议：42.6㎡，待确认",
                    "计算式": "7.10m * 6.00m = 42.60㎡",
                    "国标工程量规则": "按设计图示尺寸以面积计算",
                    "证据文本": "CT-02 600X1200",
                    "PDF页码": 1,
                    "tile_id": "p001_whole",
                    "置信度": 0.72,
                    "复核状态": "candidate_needs_manual_review",
                    "风险提示": "尺寸来自AI视觉推断",
                    "原因": "图中可见材料编号和尺寸",
                }
            ],
            "issues": [],
        }

    monkeypatch.setattr("app.services.drawing_pdf_direct_itemizer.build_pdf_basic_parse_report", fake_parse_report)
    monkeypatch.setattr("app.services.drawing_pdf_direct_itemizer.build_pdf_render_report", fake_render_report)
    monkeypatch.setattr("app.services.drawing_pdf_direct_itemizer.build_pdf_tile_report", fake_tile_report)
    monkeypatch.setattr("app.services.drawing_pdf_direct_itemizer._run_pdf_itemization_call", fake_itemization_call)
    monkeypatch.setattr("app.services.drawing_pdf_direct_itemizer.search_standard_index", fake_search_standard_index)
    monkeypatch.setattr(
        "app.services.drawing_pdf_direct_itemizer.build_pdf_ai_quantity_suggestion_report",
        fake_ai_quantity_report,
    )

    report = run_pdf_direct_itemization(
        pdf_dir=pdf_dir,
        output_dir=tmp_path / "outputs",
        timestamp="20260617_120000",
        style_prompt_text="人工清单列项规则：材料编号不同要拆",
    )

    assert report["phase"] == "BIZ-2x-pdf-direct-itemization"
    assert report["summary"]["quantity_list_row_count"] == 1
    assert report["quantity_list_rows"][0]["项目名称"] == "地砖铺贴（块料楼地面）"
    assert report["quantity_list_rows"][0]["工程量"] == "AI建议：42.6㎡，待确认"
    assert report["summary"]["pdf_ai_quantity_candidate_count"] == 1
    assert Path(report["outputs"]["quantity_list_xlsx"]).exists()
    assert Path(report["outputs"]["pdf_direct_itemization_json"]).exists()
    assert Path(report["outputs"]["pdf_ai_quantity_csv"]).exists()


def test_direct_itemization_style_prompt_keeps_json_schema_guard():
    prompt = """
# 贴近人工清单列项的 LLM 提示词

| 分部 | 序号 | 项目名称 |

## 三、简短版提示词

请按人工预算员列项习惯生成四字段工程量清单。输出字段为：分部、序号、项目名称、项目特征、单位。
"""

    addition = _direct_itemization_prompt_addition(prompt)

    assert "严格 JSON schema" in addition
    assert "drawing_items" in addition
    assert "简短版提示词" not in addition
    assert "| 分部 |" not in addition


def test_pdf_visual_evidence_matches_and_fuses_with_dxf_context():
    parse_report = {
        "text_rows": [
            {
                "source_file": "drawing.pdf",
                "page": 1,
                "text": "CT-02 600X1200灰色地砖 材料表",
                "text_type": "page_text_line",
                "confidence": 0.85,
            }
        ]
    }
    tile_report = {"tile_rows": [{"tile_id": "p001_whole", "source_file": "drawing.pdf", "page": 1}]}
    visual_report = build_pdf_visual_evidence_report(parse_report=parse_report, tile_report=tile_report)
    dxf_context = {
        "field_report": {
            "material_method_rows": [
                {
                    "source_file": "drawing.dxf",
                    "source_row_number": 12,
                    "material_or_method_name": "CT-02",
                    "spec_or_method": "600X1200灰色地砖",
                    "raw_row_text": "CT-02 600X1200灰色地砖",
                    "confidence": 0.9,
                }
            ],
            "drawing_annotation_rows": [],
        },
        "parsed_files": [],
        "conversion": {"output_files": ["drawing.dxf"]},
    }

    match_report = build_dwg_pdf_match_report(
        pdf_report=visual_report,
        dxf_context=dxf_context,
        pdf_files=[Path("drawing.pdf")],
    )
    fusion_report = build_dxf_pdf_fusion_report(
        pdf_evidence_report=visual_report,
        match_report=match_report,
        dxf_context=dxf_context,
    )

    assert visual_report["summary"]["visual_evidence_count"] == 1
    assert visual_report["evidence_rows"][0]["evidence_role"] == "material_legend"
    assert match_report["summary"]["match_status"] in {"auto_matched", "needs_manual_bind"}
    assert fusion_report["summary"]["fusion_link_count"] == 1
    assert fusion_report["project_evidence_signals"][0]["source_kind"] == "pdf_visual_fused_evidence"


def test_pdf_basic_parse_and_render_missing_tool_report(tmp_path, monkeypatch):
    pdf_path = tmp_path / "drawing.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<< /Type /Page /MediaBox [0 0 612 792] >>endobj\n%%EOF")

    parse_report = build_pdf_basic_parse_report([pdf_path])
    monkeypatch.setattr("app.services.drawing_pdf_evidence_pipeline._find_pdftoppm", lambda: None)
    monkeypatch.setattr("app.services.drawing_pdf_evidence_pipeline._render_pdf_pages_with_pypdfium2", lambda *args, **kwargs: [])
    render_report = build_pdf_render_report(parse_report, tmp_path / "pages", render_dpi=400)

    assert parse_report["summary"]["pdf_file_count"] == 1
    assert parse_report["summary"]["page_count"] >= 1
    assert parse_report["file_rows"][0]["sha256"]
    assert render_report["summary"]["render_status"] == "render_tool_missing"
    assert render_report["render_rows"][0]["status"] == "render_tool_missing"


def test_pdf_tile_report_does_not_use_working_directory_when_render_missing(tmp_path):
    parse_report = {
        "page_rows": [
            {
                "source_file": "drawing.pdf",
                "page": 1,
                "width_pt": 612,
                "height_pt": 792,
            }
        ]
    }
    render_report = {
        "render_rows": [
            {
                "source_file": "drawing.pdf",
                "page": 1,
                "png_path": "",
                "image_width_px": "",
                "image_height_px": "",
            }
        ]
    }

    tile_report = build_pdf_tile_report(
        parse_report=parse_report,
        render_report=render_report,
        tile_dir=tmp_path / "tiles",
    )

    whole_page = next(row for row in tile_report["tile_rows"] if row["tile_type"] == "whole_page_preview")
    assert whole_page["image_path"] == ""
    assert whole_page["status"] == "tile_planned_without_render_image"


def test_pdf_tile_report_uses_short_stable_filenames_for_long_pdf_names(tmp_path):
    from PIL import Image

    image_path = tmp_path / "rendered.png"
    Image.new("RGB", (80, 80), "white").save(image_path)
    long_source_file = "03.整理完毕1017信达资产职工餐厅施工图(cad2pdf)(1)_超长文件名用于验证Windows路径长度.pdf"
    parse_report = {
        "page_rows": [
            {
                "source_file": long_source_file,
                "page": 1,
                "width_pt": 612,
                "height_pt": 792,
            }
        ]
    }
    render_report = {
        "render_rows": [
            {
                "source_file": long_source_file,
                "page": 1,
                "png_path": str(image_path),
                "image_width_px": 80,
                "image_height_px": 80,
            }
        ]
    }

    tile_report = build_pdf_tile_report(
        parse_report=parse_report,
        render_report=render_report,
        tile_dir=tmp_path / "tiles",
        grid_size=2,
    )

    grid_tiles = [row for row in tile_report["tile_rows"] if row["tile_type"] == "grid"]
    assert len(grid_tiles) == 4
    for tile in grid_tiles:
        tile_path = Path(tile["image_path"])
        assert tile_path.exists()
        assert len(tile_path.name) < 80
        assert tile["status"] == "tile_image_created"


def test_pdf_direct_itemization_ignores_directory_image_paths(tmp_path):
    selected = select_images_for_pdf_itemization(
        [
            {
                "tile_id": "p001_whole",
                "tile_type": "whole_page_preview",
                "page": 1,
                "image_path": str(tmp_path),
            }
        ],
        max_images=1,
    )

    assert selected == []


def test_pdf_direct_itemization_prefers_non_empty_grid_tiles(tmp_path):
    small_tile = tmp_path / "small.png"
    large_tile = tmp_path / "large.png"
    small_tile.write_bytes(b"x" * 10)
    large_tile.write_bytes(b"x" * 100)

    selected = select_images_for_pdf_itemization(
        [
            {
                "tile_id": "p001_g03_r01_c01",
                "tile_type": "grid",
                "page": 1,
                "priority": 70,
                "image_path": str(small_tile),
            },
            {
                "tile_id": "p001_g03_r02_c02",
                "tile_type": "grid",
                "page": 1,
                "priority": 70,
                "image_path": str(large_tile),
            },
        ],
        max_images=1,
    )

    assert selected[0]["tile_id"] == "p001_g03_r02_c02"


def test_pdf_visual_evidence_prefers_non_empty_grid_tiles(tmp_path):
    small_tile = tmp_path / "small.png"
    large_tile = tmp_path / "large.png"
    small_tile.write_bytes(b"x" * 10)
    large_tile.write_bytes(b"x" * 100)

    selected = select_tiles_for_llm_visual(
        [
            {
                "tile_id": "p001_g03_r01_c01",
                "tile_type": "grid",
                "priority": 70,
                "image_path": str(small_tile),
            },
            {
                "tile_id": "p001_g03_r02_c02",
                "tile_type": "grid",
                "priority": 70,
                "image_path": str(large_tile),
            },
        ],
        max_tiles=1,
    )

    assert selected[0]["tile_id"] == "p001_g03_r02_c02"


def test_pdf_visual_evidence_balances_selected_tiles_across_pdf_files(tmp_path):
    large_a1 = tmp_path / "a1.png"
    large_a2 = tmp_path / "a2.png"
    small_b1 = tmp_path / "b1.png"
    large_a1.write_bytes(b"x" * 300)
    large_a2.write_bytes(b"x" * 200)
    small_b1.write_bytes(b"x" * 20)

    selected = select_tiles_for_llm_visual(
        [
            {
                "tile_id": "a1",
                "source_file": "03.pdf",
                "tile_type": "grid",
                "priority": 70,
                "image_path": str(large_a1),
            },
            {
                "tile_id": "a2",
                "source_file": "03.pdf",
                "tile_type": "grid",
                "priority": 70,
                "image_path": str(large_a2),
            },
            {
                "tile_id": "b1",
                "source_file": "04.pdf",
                "tile_type": "grid",
                "priority": 70,
                "image_path": str(small_b1),
            },
        ],
        max_tiles=2,
    )

    assert [row["source_file"] for row in selected] == ["03.pdf", "04.pdf"]


def test_pdf_visual_evidence_table_pass_prefers_whole_page_then_grid(tmp_path):
    whole_page = tmp_path / "whole.png"
    grid_tile = tmp_path / "grid.png"
    whole_page.write_bytes(b"x" * 20)
    grid_tile.write_bytes(b"x" * 200)

    rows = [
        {
            "tile_id": "p001_g03_r01_c01",
            "source_file": "03.pdf",
            "tile_type": "grid",
            "page": 1,
            "priority": 90,
            "image_path": str(grid_tile),
        },
        {
            "tile_id": "p001_whole",
            "source_file": "03.pdf",
            "tile_type": "whole_page_preview",
            "page": 1,
            "priority": 10,
            "image_path": str(whole_page),
        },
    ]

    selected_for_table = select_tiles_for_llm_visual(rows, max_tiles=2, prompt_mode="table_legend")
    selected_for_general = select_tiles_for_llm_visual(rows, max_tiles=1, prompt_mode="general")

    assert [row["tile_id"] for row in selected_for_table] == ["p001_whole", "p001_g03_r01_c01"]
    assert [row["tile_id"] for row in selected_for_general] == ["p001_g03_r01_c01"]


def test_pdf_visual_evidence_gap_recall_passes_prefer_whole_page_tiles(tmp_path):
    whole_page = tmp_path / "whole.png"
    grid_tile = tmp_path / "grid.png"
    whole_page.write_bytes(b"x" * 20)
    grid_tile.write_bytes(b"x" * 200)

    rows = [
        {
            "tile_id": "p001_g03_r01_c01",
            "source_file": "03.pdf",
            "tile_type": "grid",
            "page": 1,
            "priority": 90,
            "image_path": str(grid_tile),
        },
        {
            "tile_id": "p001_whole",
            "source_file": "03.pdf",
            "tile_type": "whole_page_preview",
            "page": 1,
            "priority": 10,
            "image_path": str(whole_page),
        },
    ]

    selected_for_door = select_tiles_for_llm_visual(rows, max_tiles=1, prompt_mode="door_window_demolition")
    selected_for_fixture = select_tiles_for_llm_visual(rows, max_tiles=1, prompt_mode="fixture_valve_schedule")

    assert [row["tile_id"] for row in selected_for_door] == ["p001_whole"]
    assert [row["tile_id"] for row in selected_for_fixture] == ["p001_whole"]


def test_pdf_visual_evidence_uses_llm_tile_results(tmp_path, monkeypatch):
    tile_image = tmp_path / "tile.png"
    tile_image.write_bytes(b"fake png bytes")

    async def fake_call_glm_drawing_tile_extract(*args, **kwargs):
        return {
            "raw_content": "{}",
            "evidence_items": [
                {
                    "evidence_role": "electrical_spec",
                    "discipline": "electrical",
                    "text": "LED筒灯 12W",
                    "normalized_text": "LED筒灯 12W",
                    "item_hint": "LED筒灯",
                    "space": "餐厅",
                    "material_codes": ["LT-01"],
                    "spec_or_method": "12W",
                    "suggested_unit": "套",
                    "confidence": 0.72,
                    "needs_manual_review": True,
                    "reason": "tile中可见灯具符号和规格",
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.drawing_pdf_evidence_pipeline.call_glm_drawing_tile_extract",
        fake_call_glm_drawing_tile_extract,
    )
    report = build_pdf_visual_evidence_report(
        parse_report={"text_rows": []},
        tile_report={
            "tile_rows": [
                {
                    "tile_id": "p001_g03_r01_c01",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "grid",
                    "image_path": str(tile_image),
                    "bbox_pdf": [0, 0, 100, 100],
                    "bbox_pixel": [0, 0, 500, 500],
                    "priority": 70,
                }
            ]
        },
        enable_llm_visual=True,
        max_visual_tiles=1,
        trace_id="trace-pdf-tile",
    )

    assert report["summary"]["llm_visual_status"] == "success"
    assert report["summary"]["llm_visual_tile_success_count"] == 1
    assert report["evidence_rows"][0]["source_kind"] == "pdf_visual_tile_llm"
    assert report["evidence_rows"][0]["evidence_role"] == "electrical_spec"
    assert report["evidence_rows"][0]["discipline"] == "electrical"
    assert report["evidence_rows"][0]["item_hint"] == "LED筒灯"
    assert report["evidence_rows"][0]["material_codes"] == ["LT-01"]
    assert report["evidence_rows"][0]["suggested_unit"] == "套"
    assert report["evidence_rows"][0]["text"] == "LED筒灯 12W"
    assert report["evidence_rows"][0]["tile_id"] == "p001_g03_r01_c01"


def test_pdf_visual_evidence_concurrent_tile_results_keep_tile_order(tmp_path, monkeypatch):
    first_tile = tmp_path / "tile1.png"
    second_tile = tmp_path / "tile2.png"
    first_tile.write_bytes(b"fake png bytes 1")
    second_tile.write_bytes(b"fake png bytes 2")

    async def fake_call_glm_drawing_tile_extract(*args, **kwargs):
        tile_id = kwargs["tile_context"]["tile_id"]
        return {
            "raw_content": "{}",
            "evidence_items": [
                {
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": f"{tile_id} 600X1200灰色地砖",
                    "item_hint": "块料楼地面",
                    "spec_or_method": "600X1200灰色地砖",
                    "suggested_unit": "㎡",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.drawing_pdf_evidence_pipeline.call_glm_drawing_tile_extract",
        fake_call_glm_drawing_tile_extract,
    )
    report = build_pdf_visual_evidence_report(
        parse_report={"text_rows": []},
        tile_report={
            "tile_rows": [
                {
                    "tile_id": "p001_g03_r01_c01",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "grid",
                    "image_path": str(first_tile),
                    "priority": 70,
                },
                {
                    "tile_id": "p001_g03_r01_c02",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "grid",
                    "image_path": str(second_tile),
                    "priority": 70,
                },
            ]
        },
        enable_llm_visual=True,
        max_visual_tiles=2,
        trace_id="trace-pdf-tile-concurrent",
    )

    assert report["summary"]["llm_visual_status"] == "success"
    assert [row["tile_id"] for row in report["evidence_rows"]] == ["p001_g03_r01_c01", "p001_g03_r01_c02"]


def test_pdf_visual_evidence_runs_multiple_specialized_passes(tmp_path, monkeypatch):
    tile_image = tmp_path / "tile.png"
    tile_image.write_bytes(b"fake png bytes")
    seen_modes: list[str] = []

    async def fake_call_glm_drawing_tile_extract(*args, **kwargs):
        prompt_mode = kwargs["prompt_mode"]
        seen_modes.append(prompt_mode)
        if prompt_mode == "electrical_mep":
            return {
                "raw_content": "{}",
                "evidence_items": [
                    {
                        "evidence_role": "electrical_spec",
                        "discipline": "electrical",
                        "text": "JDG20",
                        "item_hint": "电气配管",
                        "spec_or_method": "JDG20",
                        "suggested_unit": "m",
                        "confidence": 0.8,
                        "needs_manual_review": True,
                    }
                ],
            }
        if prompt_mode == "table_legend":
            return {
                "raw_content": "{}",
                "evidence_items": [
                    {
                        "evidence_role": "equipment_schedule",
                        "discipline": "plumbing",
                        "text": "地漏 DN50",
                        "item_hint": "地漏",
                        "spec_or_method": "DN50",
                        "suggested_unit": "个",
                        "confidence": 0.8,
                        "needs_manual_review": True,
                    }
                ],
            }
        if prompt_mode == "node_detail":
            return {
                "raw_content": "{}",
                "evidence_items": [
                    {
                        "evidence_role": "construction_method",
                        "discipline": "decoration",
                        "text": "灯槽节点",
                        "item_hint": "灯槽",
                        "spec_or_method": "轻钢龙骨灯槽做法",
                        "suggested_unit": "m",
                        "confidence": 0.8,
                        "needs_manual_review": True,
                    }
                ],
            }
        return {
            "raw_content": "{}",
            "evidence_items": [
                {
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": "CT-02 600X1200灰色地砖",
                    "item_hint": "块料楼地面",
                    "material_codes": ["CT-02"],
                    "spec_or_method": "600X1200灰色地砖",
                    "suggested_unit": "㎡",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.drawing_pdf_evidence_pipeline.call_glm_drawing_tile_extract",
        fake_call_glm_drawing_tile_extract,
    )
    monkeypatch.setattr("app.services.drawing_pdf_evidence_pipeline._visual_tile_worker_count", lambda task_count: 1)

    report = build_pdf_visual_evidence_report(
        parse_report={"text_rows": []},
        tile_report={
            "tile_rows": [
                {
                    "tile_id": "p001_g03_r01_c01",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "grid",
                    "image_path": str(tile_image),
                    "priority": 70,
                }
            ]
        },
        enable_llm_visual=True,
        max_visual_tiles=1,
        vision_passes=["general", "electrical_mep", "table_legend", "node_detail"],
        trace_id="trace-pdf-tile-multipass",
    )

    assert seen_modes == ["general", "electrical_mep", "table_legend", "node_detail"]
    assert report["summary"]["llm_visual_passes"] == ["general", "electrical_mep", "table_legend", "node_detail"]
    assert report["summary"]["llm_visual_tile_success_count"] == 4
    assert [row["vision_pass"] for row in report["evidence_rows"]] == [
        "general",
        "electrical_mep",
        "table_legend",
        "node_detail",
    ]
    assert [row["item_hint"] for row in report["evidence_rows"]] == ["块料楼地面", "电气配管", "地漏", "灯槽"]


def test_pdf_visual_evidence_uses_pass_specific_tile_selection(tmp_path, monkeypatch):
    grid_image = tmp_path / "grid.png"
    whole_image = tmp_path / "whole.png"
    grid_image.write_bytes(b"fake grid bytes")
    whole_image.write_bytes(b"fake whole page bytes")
    seen_calls: list[tuple[str, str]] = []

    async def fake_call_glm_drawing_tile_extract(*args, **kwargs):
        prompt_mode = kwargs["prompt_mode"]
        tile_id = kwargs["tile_context"]["tile_id"]
        seen_calls.append((prompt_mode, tile_id))
        return {
            "raw_content": "{}",
            "evidence_items": [
                {
                    "evidence_role": "material_legend",
                    "discipline": "decoration",
                    "text": f"{prompt_mode}:{tile_id}",
                    "item_hint": f"{prompt_mode} item",
                    "suggested_unit": "m",
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.drawing_pdf_evidence_pipeline.call_glm_drawing_tile_extract",
        fake_call_glm_drawing_tile_extract,
    )
    monkeypatch.setattr("app.services.drawing_pdf_evidence_pipeline._visual_tile_worker_count", lambda task_count: 1)

    report = build_pdf_visual_evidence_report(
        parse_report={"text_rows": []},
        tile_report={
            "tile_rows": [
                {
                    "tile_id": "p001_whole",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "whole_page_preview",
                    "image_path": str(whole_image),
                    "priority": 10,
                },
                {
                    "tile_id": "p001_g03_r01_c01",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "grid",
                    "image_path": str(grid_image),
                    "priority": 90,
                },
            ]
        },
        enable_llm_visual=True,
        max_visual_tiles=1,
        vision_passes=["electrical_mep", "table_legend", "fixture_valve_schedule", "door_window_demolition"],
        trace_id="trace-pdf-tile-pass-specific",
    )

    assert seen_calls == [
        ("electrical_mep", "p001_g03_r01_c01"),
        ("table_legend", "p001_whole"),
        ("fixture_valve_schedule", "p001_whole"),
        ("door_window_demolition", "p001_whole"),
    ]
    assert report["summary"]["llm_visual_tile_success_count"] == 4
    assert [(row["vision_pass"], row["tile_id"]) for row in report["evidence_rows"]] == [
        ("electrical_mep", "p001_g03_r01_c01"),
        ("table_legend", "p001_whole"),
        ("fixture_valve_schedule", "p001_whole"),
        ("door_window_demolition", "p001_whole"),
    ]


def test_dwg_quantity_trial_uploads_pdf_only_returns_four_field_list(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        dwg_quantity_trial,
        "settings",
        replace(dwg_quantity_trial.settings, pdf_itemization_provider="glm"),
    )

    def fake_run_pdf_direct_itemization(*, pdf_dir, output_dir, timestamp):
        assert Path(pdf_dir).exists()
        assert list(Path(pdf_dir).glob("*.pdf"))
        quantity_list_xlsx = Path(output_dir) / "quantity_list.xlsx"
        quantity_list_xlsx.write_bytes(b"PK fake quantity xlsx")
        direct_json = Path(output_dir) / "pdf_direct.json"
        direct_json.write_text("{}", encoding="utf-8")
        mapping_csv = Path(output_dir) / "pdf_mapping.csv"
        mapping_csv.write_text("识别编号,标准项目名称\nPDFITEM-000001,块料楼地面\n", encoding="utf-8")
        return {
            "ok": True,
            "phase": "BIZ-2x-pdf-direct-itemization",
            "generated_at": "2026-06-17 10:00:00",
            "summary": {
                "pdf_file_count": 1,
                "pdf_page_count": 1,
                "pdf_render_status": "rendered",
                "pdf_direct_itemization_status": "success",
                "quantity_list_row_count": 1,
            },
            "quantity_list_rows": [
                {"项目名称": "块料楼地面", "项目特征": "材料编号：CT-02", "单位": "㎡", "工程量": "待算量"}
            ],
            "outputs": {
                "quantity_list_xlsx": str(quantity_list_xlsx),
                "pdf_direct_itemization_json": str(direct_json),
                "pdf_direct_standard_mapping_csv": str(mapping_csv),
            },
            "issues": [],
        }

    monkeypatch.setattr(dwg_quantity_trial, "run_pdf_direct_itemization", fake_run_pdf_direct_itemization)

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/list-items-from-pdf",
        headers=headers,
        files=[("pdf_files", ("drawing.pdf", b"%PDF-1.4 fake pdf", "application/pdf"))],
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["phase"] == "BIZ-2x-pdf-direct-itemization"
    assert payload["quantity_list_rows"][0]["工程量"] == "待算量"
    assert payload["has_quantity_list_excel"] is True
    assert {item["key"] for item in payload["files"]} == {"quantity_list_xlsx"}
    assert any(item["key"] == "pdf_direct_itemization_json" for item in payload["debug_files"])
    assert any(item["key"] == "pdf_direct_standard_mapping_csv" for item in payload["debug_files"])
    download_response = client.get(payload["files"][0]["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_dwg_quantity_trial_uploads_pdf_uses_dashscope_agent_provider(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        dwg_quantity_trial,
        "settings",
        replace(
            dwg_quantity_trial.settings,
            pdf_itemization_provider="dashscope_agent",
            dashscope_vision_model="qwen3.7-plus",
            dashscope_evidence_model="qwen3.7-plus",
            dashscope_bill_summary_model="qwen3.7-plus",
        ),
    )

    called = {}

    def fake_run_pdf_agent_itemization_dashscope(*, pdf_dir, output_dir, timestamp):
        called["pdf_dir"] = Path(pdf_dir)
        called["timestamp"] = timestamp
        quantity_list_xlsx = Path(output_dir) / "agent_quantity_list.xlsx"
        quantity_list_xlsx.write_bytes(b"PK fake agent quantity xlsx")
        agent_report = Path(output_dir) / "agent_report.json"
        agent_report.write_text("{}", encoding="utf-8")
        evidence_json = Path(output_dir) / "agent_evidence.json"
        evidence_json.write_text("[]", encoding="utf-8")
        return {
            "ok": True,
            "phase": "BIZ-2x-pdf-agent-itemization",
            "generated_at": "2026-06-23 10:00:00",
            "summary": {
                "selected_view_count": 4,
                "agent_evidence_count": 6,
                "agent_bill_item_count": 2,
                "quantity_list_row_count": 2,
            },
            "quantity_list_rows": [
                {
                    "项目名称": "墙面瓷砖湿贴CT-04（块料墙面）",
                    "项目特征": "来源视图：p001_view008；图纸证据：CT-04",
                    "单位": "m²",
                    "工程量": "待复核",
                }
            ],
            "outputs": {
                "quantity_list_xlsx": str(quantity_list_xlsx),
                "agent_report_json": str(agent_report),
                "agent_evidence_json": str(evidence_json),
            },
            "issues": [],
        }

    monkeypatch.setattr(
        dwg_quantity_trial,
        "run_pdf_agent_itemization_dashscope",
        fake_run_pdf_agent_itemization_dashscope,
    )

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/list-items-from-pdf",
        headers=headers,
        files=[("pdf_files", ("drawing.pdf", b"%PDF-1.4 fake pdf", "application/pdf"))],
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert called["pdf_dir"].exists()
    assert payload["phase"] == "BIZ-2x-pdf-agent-itemization"
    assert payload["summary"]["pdf_itemization_provider"] == "dashscope_agent"
    assert payload["summary"]["pdf_itemization_model"] == "qwen3.7-plus"
    assert payload["quantity_list_rows"][0]["工程量"] == "待复核"
    assert payload["has_quantity_list_excel"] is True
    assert payload["has_pdf_agent_itemization"] is True
    assert any(item["key"] == "agent_report_json" for item in payload["debug_files"])
    assert any(item["key"] == "agent_evidence_json" for item in payload["debug_files"])


def test_dwg_quantity_trial_uploads_dwg_and_pdf_returns_pdf_evidence(client, tmp_path, monkeypatch):
    user = _create_user("staff")
    headers = _login(client, user)
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)

    def fake_run_dwg_item_listing(*, upload_dir, pdf_upload_dir=None, output_dir, timestamp):
        assert Path(upload_dir).exists()
        assert list(Path(upload_dir).glob("*.dwg"))
        assert pdf_upload_dir is not None
        assert list(Path(pdf_upload_dir).glob("*.pdf"))
        item_xlsx = Path(output_dir) / "dwg_items.xlsx"
        item_xlsx.write_bytes(b"PK fake xlsx")
        quantity_list_xlsx = Path(output_dir) / "quantity_list.xlsx"
        quantity_list_xlsx.write_bytes(b"PK fake quantity xlsx")
        pdf_json = Path(output_dir) / "pdf_pipeline.json"
        pdf_json.write_text("{}", encoding="utf-8")
        pdf_evidence_csv = Path(output_dir) / "pdf_evidence.csv"
        pdf_evidence_csv.write_text("证据编号,文本\nPDFEV-000001,CT-02\n", encoding="utf-8")
        match_csv = Path(output_dir) / "dwg_pdf_match.csv"
        match_csv.write_text("匹配项,分数\nmaterial_code_overlap,1\n", encoding="utf-8")
        return {
            "ok": True,
            "phase": "BIZ-2x-dwg-upload-item-listing",
            "generated_at": "2026-06-17 10:00:00",
            "summary": {
                "dwg_file_count": 1,
                "dxf_file_count": 1,
                "item_row_count": 1,
                "pdf_file_count": 1,
                "pdf_page_count": 2,
                "pdf_rendered_page_count": 1,
                "pdf_visual_evidence_count": 1,
                "dwg_pdf_match_status": "auto_matched",
                "dxf_pdf_fusion_status": "ready",
                "dxf_pdf_fusion_link_count": 1,
            },
            "quantity_list_rows": [
                {"项目名称": "块料楼地面", "项目特征": "CT-02 600X1200灰色地砖", "单位": "㎡", "工程量": "待算量"}
            ],
            "item_rows": [],
            "pdf_evidence_summary": {
                "pdf_file_count": 1,
                "pdf_page_count": 2,
                "pdf_rendered_page_count": 1,
                "pdf_visual_evidence_count": 1,
                "dwg_pdf_match_status": "auto_matched",
                "dxf_pdf_fusion_status": "ready",
                "dxf_pdf_fusion_link_count": 1,
            },
            "pdf_page_rows": [{"source_file": "drawing.pdf", "page": 1}],
            "pdf_tile_rows": [{"tile_id": "p001_whole", "source_file": "drawing.pdf", "page": 1}],
            "pdf_visual_evidence_rows": [
                {
                    "evidence_id": "PDFEV-000001",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "evidence_role": "material_legend",
                    "text": "CT-02 600X1200灰色地砖",
                    "confidence": 0.88,
                }
            ],
            "dwg_pdf_match_rows": [{"match_item": "material_code_overlap", "score": 1, "status": "auto_matched"}],
            "dxf_pdf_fusion_rows": [{"fusion_id": "FUS-000001", "fusion_type": "material_code_cross_source"}],
            "outputs": {
                "item_list_xlsx": str(item_xlsx),
                "quantity_list_xlsx": str(quantity_list_xlsx),
                "pdf_pipeline_json": str(pdf_json),
                "pdf_visual_evidence_csv": str(pdf_evidence_csv),
                "dwg_pdf_match_csv": str(match_csv),
            },
            "issues": [],
        }

    monkeypatch.setattr(dwg_quantity_trial, "run_dwg_item_listing", fake_run_dwg_item_listing)

    response = client.post(
        "/api/v1/admin/dwg-quantity-trial/list-items-with-pdf",
        headers=headers,
        files=[
            ("dwg_files", ("drawing.dwg", b"AC1018 fake dwg", "application/octet-stream")),
            ("pdf_files", ("drawing.pdf", b"%PDF-1.4 fake pdf", "application/pdf")),
        ],
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ok"] is True
    assert payload["summary"]["pdf_file_count"] == 1
    assert payload["pdf_evidence_summary"]["dwg_pdf_match_status"] == "auto_matched"
    assert payload["pdf_visual_evidence_rows"][0]["evidence_role"] == "material_legend"
    assert payload["has_pdf_evidence"] is True
    assert payload["pdf_evidence_effective"] is True
    assert {item["key"] for item in payload["files"]} == {"quantity_list_xlsx"}
    assert any(item["key"] == "pdf_pipeline_json" for item in payload["debug_files"])
    assert any(item["key"] == "dwg_pdf_match_csv" for item in payload["debug_files"])


def test_listing_payload_hides_ineffective_pdf_outputs_from_business_files(tmp_path, monkeypatch):
    monkeypatch.setattr(dwg_quantity_trial, "OUTPUT_DIR", tmp_path)
    quantity_list_xlsx = tmp_path / "quantity_list.xlsx"
    quantity_list_xlsx.write_bytes(b"PK fake quantity xlsx")
    pdf_json = tmp_path / "pdf_pipeline.json"
    pdf_json.write_text("{}", encoding="utf-8")
    pdf_csv = tmp_path / "pdf_visual.csv"
    pdf_csv.write_text("证据编号,文本\n", encoding="utf-8")

    payload = dwg_quantity_trial._listing_response_payload(
        {
            "ok": True,
            "phase": "BIZ-2x-dwg-upload-item-listing",
            "generated_at": "2026-06-17 10:00:00",
            "summary": {"pdf_file_count": 1},
            "quantity_list_rows": [
                {"项目名称": "块料楼地面", "项目特征": "CT-02", "单位": "㎡", "工程量": "待算量"}
            ],
            "pdf_evidence_summary": {
                "pdf_file_count": 1,
                "pdf_rendered_page_count": 0,
                "pdf_visual_evidence_count": 0,
                "dwg_pdf_match_status": "blocked",
                "dxf_pdf_fusion_status": "blocked_by_dwg_pdf_match",
                "dxf_pdf_fusion_link_count": 0,
            },
            "outputs": {
                "quantity_list_xlsx": str(quantity_list_xlsx),
                "pdf_pipeline_json": str(pdf_json),
                "pdf_visual_evidence_csv": str(pdf_csv),
            },
            "issues": [],
        }
    )

    assert payload["has_pdf_evidence"] is False
    assert payload["pdf_evidence_effective"] is False
    assert {item["key"] for item in payload["files"]} == {"quantity_list_xlsx"}
    assert any(item["key"] == "pdf_pipeline_json" for item in payload["debug_files"])


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
