from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.config import settings
from app.core.database import Base, SessionLocal
from app.core.security import get_password_hash
from app.models.enterprise_profile import (
    ENTERPRISE_PROFILE_STATUS_ACTIVE,
    ENTERPRISE_PROFILE_STATUS_ARCHIVED,
    ENTERPRISE_PROFILE_STATUS_DRAFT,
    EnterpriseProfileEvent,
)
from app.models.file_object import FileObject
from app.models.user import User, UserRole


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "enterprise_profile_editor") -> User:
    username = f"biz4c_{role}_{uuid.uuid4().hex[:10]}"
    legacy_role = "admin" if role in {"admin", "system_admin"} else "user"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(PASSWORD),
            role=legacy_role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz4c test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _headers(client, role: str) -> tuple[User, dict]:
    user = _create_user(role)
    return user, _login(client, user)


def _create_file_object(user: User, filename: str = "license.pdf") -> FileObject:
    db = SessionLocal()
    try:
        file_obj = FileObject(
            file_id=str(uuid.uuid4()),
            username=user.username,
            purpose="enterprise_profile",
            bucket="test-bucket",
            object_name=f"enterprise_profile/{uuid.uuid4().hex}/{filename}",
            original_filename=filename,
            content_type="application/pdf",
            size_bytes=1234,
        )
        db.add(file_obj)
        db.commit()
        db.refresh(file_obj)
        return file_obj
    finally:
        db.close()


def _sample_payload(**overrides):
    payload = {
        "category": "certificate",
        "subcategory": "business_license",
        "profile_key": f"license-{uuid.uuid4().hex[:6]}",
        "title": "营业执照",
        "summary": "企业营业执照扫描件",
        "content_text": "统一社会信用代码：91440000TEST",
        "tags": ["证照", "技术标"],
        "valid_until": (date.today() + timedelta(days=365)).isoformat(),
    }
    payload.update(overrides)
    return payload


def _create_item(client, headers: dict, **overrides):
    response = client.post("/api/v1/admin/enterprise-profile/items", headers=headers, json=_sample_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_enterprise_profile_tables_are_registered_in_metadata():
    assert {
        "enterprise_profile_items",
        "enterprise_profile_files",
        "enterprise_profile_events",
    }.issubset(set(Base.metadata.tables))


def test_feature_flag_disabled_returns_feature_disabled(client):
    _, headers = _headers(client, "admin")
    old_flag = _set_flag("feature_enterprise_profile", False)
    try:
        response = client.get("/api/v1/admin/enterprise-profile/items", headers=headers)
    finally:
        _set_flag("feature_enterprise_profile", old_flag)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_editor_can_create_draft_but_not_activate_without_approver_role(client):
    _, editor_headers = _headers(client, "enterprise_profile_editor")
    old_flag = _set_flag("feature_enterprise_profile", True)
    try:
        item = _create_item(client, editor_headers)
        activate_response = client.post(
            f"/api/v1/admin/enterprise-profile/items/{item['item_uuid']}/activate",
            headers=editor_headers,
            json={"reason": "测试启用"},
        )
    finally:
        _set_flag("feature_enterprise_profile", old_flag)

    assert item["status"] == ENTERPRISE_PROFILE_STATUS_DRAFT
    assert activate_response.status_code == 403
    assert activate_response.json()["detail"] == "PERMISSION_DENIED"


def test_certificate_activation_requires_attachment_then_becomes_candidate(client):
    editor, editor_headers = _headers(client, "enterprise_profile_editor")
    _, approver_headers = _headers(client, "enterprise_profile_approver")
    _, staff_headers = _headers(client, "staff")
    file_obj = _create_file_object(editor, "business-license.pdf")
    old_flag = _set_flag("feature_enterprise_profile", True)
    try:
        item = _create_item(client, editor_headers, title=f"营业执照-{uuid.uuid4().hex[:6]}")
        blocked_response = client.post(
            f"/api/v1/admin/enterprise-profile/items/{item['item_uuid']}/activate",
            headers=approver_headers,
            json={"reason": "证照审核通过"},
        )
        attach_response = client.post(
            f"/api/v1/admin/enterprise-profile/items/{item['item_uuid']}/attachments",
            headers=editor_headers,
            json={"file_id": file_obj.file_id, "attachment_type": "license_scan", "is_primary": True},
        )
        activate_response = client.post(
            f"/api/v1/admin/enterprise-profile/items/{item['item_uuid']}/activate",
            headers=approver_headers,
            json={"reason": "证照审核通过"},
        )
        candidate_response = client.get(
            "/api/v1/enterprise-profile/candidates",
            headers=staff_headers,
            params={"category": "certificate", "keyword": item["title"]},
        )
    finally:
        _set_flag("feature_enterprise_profile", old_flag)

    assert blocked_response.status_code == 409
    assert blocked_response.json()["detail"]["code"] == "ENTERPRISE_PROFILE_QUALITY_BLOCKED"
    assert any(issue["code"] == "missing_attachment" for issue in blocked_response.json()["detail"]["issues"])
    assert attach_response.status_code == 200, attach_response.text
    assert attach_response.json()["data"]["file_id"] == file_obj.file_id
    assert activate_response.status_code == 200, activate_response.text
    assert activate_response.json()["data"]["status"] == ENTERPRISE_PROFILE_STATUS_ACTIVE
    assert candidate_response.status_code == 200, candidate_response.text
    assert candidate_response.json()["data"][0]["item_uuid"] == item["item_uuid"]


def test_viewer_can_read_but_not_write_and_archive_records_event(client):
    _, editor_headers = _headers(client, "enterprise_profile_editor")
    _, viewer_headers = _headers(client, "enterprise_profile_viewer")
    _, approver_headers = _headers(client, "enterprise_profile_approver")
    old_flag = _set_flag("feature_enterprise_profile", True)
    try:
        item = _create_item(client, editor_headers, category="technical_solution", title=f"质量保证措施-{uuid.uuid4().hex[:6]}")
        read_response = client.get("/api/v1/admin/enterprise-profile/items", headers=viewer_headers)
        write_response = client.post("/api/v1/admin/enterprise-profile/items", headers=viewer_headers, json=_sample_payload())
        archive_response = client.post(
            f"/api/v1/admin/enterprise-profile/items/{item['item_uuid']}/archive",
            headers=approver_headers,
            json={"reason": "资料失效归档"},
        )
    finally:
        _set_flag("feature_enterprise_profile", old_flag)

    assert read_response.status_code == 200, read_response.text
    assert write_response.status_code == 403
    assert archive_response.status_code == 200, archive_response.text
    assert archive_response.json()["data"]["status"] == ENTERPRISE_PROFILE_STATUS_ARCHIVED

    db = SessionLocal()
    try:
        events = (
            db.query(EnterpriseProfileEvent)
            .filter(EnterpriseProfileEvent.item_id == archive_response.json()["data"]["id"])
            .order_by(EnterpriseProfileEvent.id.asc())
            .all()
        )
        assert [event.event_type for event in events][-1] == "archived"
    finally:
        db.close()
