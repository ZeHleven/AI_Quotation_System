from __future__ import annotations

import uuid
from io import BytesIO

from openpyxl import Workbook

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_job import QuoteJob
from app.models.user import User, UserRole


PASSWORD = "secret123"


def _set_flag(name: str, value: bool) -> bool:
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "user", roles: list[str] | None = None) -> User:
    db = SessionLocal()
    try:
        user = User(
            username=f"biz2l2_{role}_{uuid.uuid4().hex[:8]}",
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


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Manual"
    sheet.append(["Name", "Unit", "Qty", "Remark", "Client price"])
    sheet.append(["Paint wall", "m", "12", "two coats", "99"])
    sheet.append(["Summary", "", "", "", "99"])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_requirement_standardization_requires_feature_flag(client):
    user = _create_user("user")
    headers = _login(client, user)
    old_flag = _set_flag("feature_requirement_standardization", False)
    try:
        response = client.post(
            "/api/v1/admin/requirement-standardization/preview",
            headers=headers,
            files={"file": ("manual.xlsx", _workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    finally:
        _set_flag("feature_requirement_standardization", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_requirement_standardization_remap_and_confirm_are_stateless(client):
    user = _create_user("user")
    headers = _login(client, user)
    old_flag = _set_flag("feature_requirement_standardization", True)
    try:
        preview_response = client.post(
            "/api/v1/admin/requirement-standardization/preview",
            headers=headers,
            files={"file": ("manual.xlsx", _workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]
        assert preview["sheet_mappings"][0]["columns"]
        assert preview["rows"][1]["raw_cells"]

        remap_response = client.post(
            "/api/v1/admin/requirement-standardization/remap",
            headers=headers,
            json={
                "preview": preview,
                "sheet_mappings": [
                    {
                        "sheet_name": "Manual",
                        "field_mapping": {
                            "A": "item_name",
                            "B": "unit",
                            "C": "quantity",
                            "D": "remark",
                            "E": "price_ignored",
                        },
                    }
                ],
            },
        )
        assert remap_response.status_code == 200, remap_response.text
        remapped = remap_response.json()["data"]
        data_rows = [row for row in remapped["rows"] if row["row_type"] == "data_row"]
        assert data_rows[0]["item_name"] == "Paint wall"
        assert data_rows[0]["quantity"] == 12
        assert data_rows[0]["unit"] == "m"
        assert "PRICE_COLUMN_PRESENT" in data_rows[0]["warnings"]

        blocked_response = client.post(
            "/api/v1/admin/requirement-standardization/confirm",
            headers=headers,
            json={
                "rows": [
                    {
                        **data_rows[0],
                        "requirement_row_key": "Manual:2:0",
                        "include": True,
                        "confirmed": False,
                    }
                ]
            },
        )
        assert blocked_response.status_code == 200
        blocked = blocked_response.json()["data"]
        assert blocked["summary"]["confirmed_row_count"] == 0
        assert blocked["summary"]["blocked_row_count"] == 1
        assert "CONFIRMATION_REQUIRED" in blocked["blocked_rows"][0]["errors"]
        assert blocked["blocked_rows"][0]["source_sheet"] == "Manual"
        assert blocked["blocked_rows"][0]["requirement_row_key"] == "Manual:2:0"
        assert blocked["blocked_rows"][0]["raw_row_index"] == 2
        assert blocked["blocked_rows"][0]["quantity"] == 12
        assert blocked["blocked_rows"][0]["unit"] == "m"
        assert blocked["blocked_rows"][0]["error_messages"] == [
            "原表包含价格列，系统不会采用该价格。",
        ]
        assert blocked["blocked_rows"][0]["error_summary"] == "原表包含价格列，系统不会采用该价格。"

        confirm_response = client.post(
            "/api/v1/admin/requirement-standardization/confirm",
            headers=headers,
            json={"rows": [{**data_rows[0], "include": True, "confirmed": True}]},
        )
        assert confirm_response.status_code == 200
        confirmed = confirm_response.json()["data"]
        assert confirmed["summary"]["confirmed_row_count"] == 1
        assert confirmed["rows"][0]["item_name"] == "Paint wall"
        assert "Paint wall" in confirmed["csv"]
        assert "Paint wall" in confirmed["quote_text"]
        assert "数量: 12m" in confirmed["quote_text"]
    finally:
        _set_flag("feature_requirement_standardization", old_flag)


def test_quote_user_can_preview_requirement_standardization(client):
    user = _create_user("user", roles=["quote_user"])
    headers = _login(client, user)
    old_flag = _set_flag("feature_requirement_standardization", True)
    try:
        response = client.post(
            "/api/v1/admin/requirement-standardization/preview",
            headers=headers,
            files={"file": ("manual.xlsx", _workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    finally:
        _set_flag("feature_requirement_standardization", old_flag)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["rows"]


def test_confirmed_requirement_quote_text_can_create_quote_job(client):
    user = _create_user("staff")
    headers = _login(client, user)
    old_flag = _set_flag("feature_requirement_standardization", True)
    try:
        preview_response = client.post(
            "/api/v1/admin/requirement-standardization/preview",
            headers=headers,
            files={"file": ("manual.xlsx", _workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]

        remap_response = client.post(
            "/api/v1/admin/requirement-standardization/remap",
            headers=headers,
            json={
                "preview": preview,
                "sheet_mappings": [
                    {
                        "sheet_name": "Manual",
                        "field_mapping": {
                            "A": "item_name",
                            "B": "unit",
                            "C": "quantity",
                            "D": "remark",
                            "E": "price_ignored",
                        },
                    }
                ],
            },
        )
        assert remap_response.status_code == 200, remap_response.text
        data_rows = [row for row in remap_response.json()["data"]["rows"] if row["row_type"] == "data_row"]

        confirm_response = client.post(
            "/api/v1/admin/requirement-standardization/confirm",
            headers=headers,
            json={"rows": [{**data_rows[0], "include": True, "confirmed": True}]},
        )
        assert confirm_response.status_code == 200, confirm_response.text
        confirmed = confirm_response.json()["data"]
        quote_text = confirmed["quote_text"]
        assert confirmed["summary"]["blocked_row_count"] == 0

        quote_response = client.post(
            "/api/v1/quote/jobs",
            headers=headers,
            data={"message": f"【来源：需求单标准化确认清单】\n{quote_text}", "source": "需求单标准化"},
        )
        assert quote_response.status_code == 202, quote_response.text
        quote_job = quote_response.json()["data"]
        assert quote_job["job_id"]
        assert quote_job["status"] == "queued"

        db = SessionLocal()
        try:
            stored_job = db.query(QuoteJob).filter(QuoteJob.job_id == quote_job["job_id"]).one()
            assert "Paint wall" in stored_job.message
            assert "数量: 12m" in stored_job.message
            assert stored_job.file_name is None
        finally:
            db.close()
    finally:
        _set_flag("feature_requirement_standardization", old_flag)
