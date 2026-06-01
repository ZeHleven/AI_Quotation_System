import uuid

from app.api.v1 import cost_items as cost_items_api
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_audit import CostAccessAuditLog
from app.models.cost_item import COST_STATUS_ACTIVE
from app.models.user import User, UserRole


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "staff") -> User:
    username = f"biz2v3_{role}_{uuid.uuid4().hex[:10]}"
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
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz2v3 test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _headers(client, role: str = "staff") -> tuple[User, dict]:
    user = _create_user(role=role)
    return user, _login(client, user)


def _sample_payload(**overrides):
    payload = {
        "category": "BIZ-2v-3",
        "subcategory": "审计",
        "item_name": f"成本审计测试-{uuid.uuid4().hex[:6]}",
        "spec": "标准",
        "unit": "项",
        "price_type": "combined",
        "subcontract_composite_price": 12.5,
        "client_tax_excluded_price": 18.0,
        "crew_benchmark_price": 10.0,
        "notes": "audit test item",
    }
    payload.update(overrides)
    return payload


def _create_item(client, headers: dict, **overrides):
    response = client.post("/api/v1/admin/cost-items", headers=headers, json=_sample_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _latest_audit(action: str, username: str | None = None) -> CostAccessAuditLog | None:
    db = SessionLocal()
    try:
        query = db.query(CostAccessAuditLog).filter(CostAccessAuditLog.action == action)
        if username:
            query = query.filter(CostAccessAuditLog.username == username)
        return query.order_by(CostAccessAuditLog.id.desc()).first()
    finally:
        db.close()


def test_cost_export_requires_exporter_and_records_audit(client):
    _, admin_headers = _headers(client, "admin")
    exporter, exporter_headers = _headers(client, "cost_exporter")
    _, viewer_headers = _headers(client, "cost_viewer")
    _, staff_headers = _headers(client, "staff")
    item_name = f"可导出成本-{uuid.uuid4().hex[:6]}"
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _create_item(client, admin_headers, item_name=item_name)
        staff_response = client.get(f"/api/v1/admin/cost-items/export?keyword={item_name}", headers=staff_headers)
        viewer_response = client.get(f"/api/v1/admin/cost-items/export?keyword={item_name}", headers=viewer_headers)
        exporter_response = client.get(f"/api/v1/admin/cost-items/export?keyword={item_name}", headers=exporter_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert staff_response.status_code == 403
    assert viewer_response.status_code == 403
    assert exporter_response.status_code == 200, exporter_response.text
    assert exporter_response.headers["content-type"].startswith("text/csv")
    assert item_name in exporter_response.content.decode("utf-8-sig")

    audit = _latest_audit("cost_item.export", exporter.username)
    assert audit is not None
    assert audit.result_count == 1
    assert audit.status == "success"
    assert item_name in (audit.filters_json or "")


def test_cost_detail_view_records_audit_and_audit_log_access_is_restricted(client):
    _, admin_headers = _headers(client, "admin")
    viewer, viewer_headers = _headers(client, "cost_viewer")
    _, approver_headers = _headers(client, "cost_approver")
    _, staff_headers = _headers(client, "staff")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, admin_headers)
        detail_response = client.get(f"/api/v1/admin/cost-items/{item['id']}", headers=viewer_headers)
        staff_audit_response = client.get("/api/v1/admin/cost-items/audit-logs", headers=staff_headers)
        viewer_audit_response = client.get("/api/v1/admin/cost-items/audit-logs", headers=viewer_headers)
        approver_audit_response = client.get(
            f"/api/v1/admin/cost-items/audit-logs?action=cost_item.detail&resource_id={item['id']}",
            headers=approver_headers,
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert detail_response.status_code == 200
    assert staff_audit_response.status_code == 403
    assert viewer_audit_response.status_code == 403
    assert approver_audit_response.status_code == 200, approver_audit_response.text
    rows = approver_audit_response.json()["data"]
    assert any(row["action"] == "cost_item.detail" and row["username"] == viewer.username for row in rows)


def test_cost_status_changes_record_audit(client):
    _, admin_headers = _headers(client, "admin")
    old_flag = _set_flag("feature_cost_db", True)
    try:
        item = _create_item(client, admin_headers)
        activate_response = client.post(
            f"/api/v1/admin/cost-items/{item['id']}/activate",
            headers=admin_headers,
            json={"reason": "BIZ-2v-3 审计测试"},
        )
        withdraw_response = client.post(
            f"/api/v1/admin/cost-items/{item['id']}/withdraw",
            headers=admin_headers,
            json={"reason": "BIZ-2v-3 审计测试撤回"},
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert activate_response.status_code == 200, activate_response.text
    assert activate_response.json()["data"]["status"] == COST_STATUS_ACTIVE
    assert withdraw_response.status_code == 200, withdraw_response.text
    assert _latest_audit("cost_item.activate") is not None
    assert _latest_audit("cost_item.withdraw") is not None


def test_cost_rag_sync_records_audit(client, monkeypatch):
    approver, approver_headers = _headers(client, "cost_approver")

    async def fake_sync(db, username, user_id=None):
        return {
            "success": True,
            "message": "synced for audit",
            "synced_count": 2,
            "source": "cost_items.active",
            "error": None,
            "run": {"id": 123, "status": "success"},
        }

    monkeypatch.setattr(cost_items_api, "sync_active_cost_items_to_rag", fake_sync)
    old_flag = _set_flag("feature_cost_db", True)
    try:
        response = client.post("/api/v1/admin/cost-items/sync-rag", headers=approver_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 200, response.text
    audit = _latest_audit("cost_rag.sync", approver.username)
    assert audit is not None
    assert audit.result_count == 2
    assert audit.status == "success"
    assert audit.message == "synced for audit"
