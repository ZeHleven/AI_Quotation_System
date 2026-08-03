from __future__ import annotations

import uuid
from pathlib import Path

from app.api.v1 import codex_worker
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User


PASSWORD = "secret123"


def _create_user(role: str = "staff") -> User:
    db = SessionLocal()
    try:
        user = User(
            username=f"codexworker_{role}_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(PASSWORD),
            role=role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_codex_worker_fake_job_upload_query_and_downloads_excel(client, tmp_path, monkeypatch):
    monkeypatch.setattr(codex_worker, "JOB_ROOT", tmp_path / "codex_worker_jobs")
    headers = _login(client, _create_user())

    response = client.post(
        "/api/v1/admin/codex-worker/jobs/fake",
        headers=headers,
        files=[("pdf_files", ("source.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] == "succeeded"
    assert data["summary"]["quantity_list_row_count"] == 2
    assert data["input_files"][0]["file_name"] == "source.pdf"

    job_id = data["job_id"]
    status_response = client.get(f"/api/v1/admin/codex-worker/jobs/{job_id}", headers=headers)
    assert status_response.status_code == 200
    status_data = status_response.json()["data"]
    assert status_data["status"] == "succeeded"

    files_by_key = {item["key"]: item for item in status_data["files"]}
    assert {"codex_result_json", "validation_report", "four_field_xlsx", "four_field_csv"} <= set(files_by_key)
    download_response = client.get(files_by_key["four_field_xlsx"]["download_url"], headers=headers)
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")


def test_codex_worker_fake_job_validation_failure_keeps_report_without_excel(client, tmp_path, monkeypatch):
    monkeypatch.setattr(codex_worker, "JOB_ROOT", tmp_path / "codex_worker_jobs")
    headers = _login(client, _create_user())

    response = client.post(
        "/api/v1/admin/codex-worker/jobs/fake?sample=missing-field",
        headers=headers,
        files=[("pdf_files", ("source.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] == "validation_failed"
    assert data["summary"]["error_count"] >= 1
    files_by_key = {item["key"]: item for item in data["files"]}
    assert "validation_report" in files_by_key
    assert "four_field_xlsx" not in files_by_key

    report_response = client.get(files_by_key["validation_report"]["download_url"], headers=headers)
    assert report_response.status_code == 200
    assert b"REQUIRED_FIELD_EMPTY" in report_response.content


def test_codex_worker_fake_job_rejects_non_pdf_and_traversal_download(client, tmp_path, monkeypatch):
    monkeypatch.setattr(codex_worker, "JOB_ROOT", tmp_path / "codex_worker_jobs")
    headers = _login(client, _create_user())

    reject_response = client.post(
        "/api/v1/admin/codex-worker/jobs/fake",
        headers=headers,
        files=[("pdf_files", ("source.txt", b"not a pdf", "text/plain"))],
    )
    assert reject_response.status_code == 400

    response = client.post(
        "/api/v1/admin/codex-worker/jobs/fake",
        headers=headers,
        files=[("pdf_files", ("source.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )
    assert response.status_code == 202
    job_id = response.json()["data"]["job_id"]
    traversal_response = client.get(
        f"/api/v1/admin/codex-worker/jobs/{job_id}/files/../job_status.json",
        headers=headers,
    )
    assert traversal_response.status_code == 404

    job_root = Path(codex_worker.JOB_ROOT)
    assert job_root.exists()


def test_codex_worker_openai_job_converts_agent_report_to_contract_and_excel(client, tmp_path, monkeypatch):
    monkeypatch.setattr(codex_worker, "JOB_ROOT", tmp_path / "codex_worker_jobs")

    def fake_run_pdf_agent_itemization_openai(**kwargs):
        assert Path(kwargs["pdf_dir"]).exists()
        assert kwargs["max_views"] == 4
        return {
            "summary": {
                "selected_view_count": 4,
                "agent_evidence_count": 1,
                "agent_bill_item_count": 1,
                "itemizability_manual_review_count": 0,
                "standard_mapped_count": 1,
            },
            "quantity_list_rows": [
                {
                    "项目名称": "职工餐厅墙面 CT-1 地砖湿贴（块料墙、柱面）",
                    "项目特征": "空间：职工餐厅；材料：CT-1；来源视图：p001_view001；复核提示：AI识图草稿",
                    "单位": "m2",
                    "工程量": "约20.5，待复核",
                }
            ],
            "standard_mapping_rows": [
                {
                    "识别编号": "PDFAGITEM-000001",
                    "列项判断": "施工项",
                    "标准项目名称": "块料墙、柱面",
                    "标准项目编码": "011204003",
                    "来源视图": "p001_view001",
                    "source_item": {
                        "item_id": "PDFAGITEM-000001",
                        "itemizability_status": "施工项",
                        "source_view_ids": ["p001_view001"],
                        "source_evidence": ["立面图可见 CT-1 墙面湿贴"],
                    },
                }
            ],
            "agent_evidence_rows": [
                {
                    "view_id": "p001_view001",
                    "view_title": "立面图",
                    "view_type": "elevation",
                    "visible_texts": ["CT-1 墙面湿贴"],
                    "material_codes": [{"code": "CT-1", "name_or_hint": "地砖", "spec_or_method": "湿贴"}],
                    "objects": [{"name": "墙面", "space": "职工餐厅", "method": "湿贴"}],
                    "methods": ["墙面地砖湿贴"],
                    "confidence": 0.8,
                    "needs_manual_review": True,
                }
            ],
            "agent_filtered_items": [
                {
                    "item_id": "PDFAGFILTER-000001",
                    "concrete_item_name": "职工餐厅餐椅布置",
                    "itemizability_reason": "活动家具，不进入施工清单",
                    "source_view_ids": ["p001_view001"],
                }
            ],
            "issues": [],
        }

    monkeypatch.setattr(codex_worker, "run_pdf_agent_itemization_openai", fake_run_pdf_agent_itemization_openai)
    headers = _login(client, _create_user())

    response = client.post(
        "/api/v1/admin/codex-worker/jobs/openai?max_views=4",
        headers=headers,
        files=[("pdf_files", ("source.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["mode"] == "openai_codex_style"
    assert data["status"] == "succeeded"
    assert data["summary"]["quantity_list_row_count"] == 1
    assert data["summary"]["agent_selected_view_count"] == 4

    files_by_key = {item["key"]: item for item in data["files"]}
    assert {"codex_result_json", "validation_report", "worker_report_json", "four_field_xlsx"} <= set(files_by_key)
    excel_response = client.get(files_by_key["four_field_xlsx"]["download_url"], headers=headers)
    assert excel_response.status_code == 200
    assert excel_response.content.startswith(b"PK")
