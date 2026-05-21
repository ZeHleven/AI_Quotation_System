import uuid
from datetime import datetime, timezone

from app.api.v1 import cost_items as cost_items_api
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_item import COST_STATUS_ACTIVE, COST_STATUS_DRAFT, CostItem, CostRagSyncRun
from app.models.user import User, UserRole
from app.services.cost_rag_sync import active_cost_items_rag_payload


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "staff") -> User:
    username = f"biz2c_{role}_{uuid.uuid4().hex[:10]}"
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
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz2c test seed"))
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


def _seed_cost_item(db, *, item_name: str, status: str, price: float = 42.5) -> CostItem:
    item = CostItem(
        category="BIZ-2c test",
        subcategory="RAG sync",
        item_name=item_name,
        spec="thickness 30mm",
        unit="m2",
        price=price,
        client_tax_excluded_price=50.0,
        client_labor_price=12.5,
        client_main_material_price=20.0,
        client_auxiliary_material_price=8.0,
        subcontract_composite_price=price,
        subcontract_labor_price=10.0,
        subcontract_main_material_price=18.0,
        subcontract_auxiliary_material_price=6.0,
        crew_benchmark_price=38.0,
        price_type="combined",
        status=status,
        source="manual",
        notes="sync test note",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_active_cost_items_rag_payload_uses_active_cost_master_only(client):
    suffix = uuid.uuid4().hex[:8]
    active_name = f"BIZ2c active cost master {suffix}"
    draft_name = f"BIZ2c draft ignored {suffix}"
    db = SessionLocal()
    try:
        active_item = _seed_cost_item(db, item_name=active_name, status=COST_STATUS_ACTIVE)
        _seed_cost_item(db, item_name=draft_name, status=COST_STATUS_DRAFT)

        payload = active_cost_items_rag_payload(db)
    finally:
        db.close()

    active_rows = [item for item in payload if item["item_name"] == active_name]
    draft_rows = [item for item in payload if item["item_name"] == draft_name]
    assert len(active_rows) == 1
    assert draft_rows == []
    row = active_rows[0]
    assert row["id"] == f"cost_item_{active_item.id}"
    assert row["unit_price"] == active_item.price
    assert row["unit"] == "m2"
    assert row["is_draft"] is False
    assert "cost_items.active" in row["notes"]
    assert "client_tax_excluded_price" not in row
    assert "\u5bf9\u7532\u7a0e\u524d\u7efc\u5408\u5355\u4ef7: 50.0" in row["notes"]
    assert "\u52b3\u52a1\u53d1\u5305\u7efc\u5408\u5355\u4ef7: 42.5" in row["notes"]
    assert "\u73ed\u7ec4\u6807\u5e95\u7a0e\u524d\u4ef7: 38.0" in row["notes"]


def test_sync_active_cost_items_to_rag_endpoint_requires_admin(client, monkeypatch):
    _, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    calls = []

    async def fake_sync(db, username, user_id=None):
        calls.append(username)
        return {
            "success": True,
            "message": "synced",
            "synced_count": 3,
            "source": "cost_items.active",
            "error": None,
            "run": {"id": 1, "status": "success"},
        }

    monkeypatch.setattr(cost_items_api, "sync_active_cost_items_to_rag", fake_sync)
    old_flag = _set_flag("feature_cost_db", True)
    try:
        staff_response = client.post("/api/v1/admin/cost-items/sync-rag", headers=staff_headers)
        admin_response = client.post("/api/v1/admin/cost-items/sync-rag", headers=admin_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert staff_response.status_code == 403
    assert staff_response.json()["detail"] == "PERMISSION_DENIED"
    assert admin_response.status_code == 200
    assert admin_response.json()["data"]["synced_count"] == 3
    assert admin_response.json()["data"]["source"] == "cost_items.active"
    assert admin_response.json()["data"]["run"]["status"] == "success"
    assert len(calls) == 1


def test_sync_active_cost_items_to_rag_endpoint_rejects_empty_active_set(client, monkeypatch):
    _, admin_headers = _headers(client, "admin")

    async def fake_sync(db, username, user_id=None):
        return {
            "success": False,
            "message": "no active cost items",
            "synced_count": 0,
            "source": "cost_items.active",
            "error": "NO_ACTIVE_COST_ITEMS",
            "run": {"id": 1, "status": "failed"},
        }

    monkeypatch.setattr(cost_items_api, "sync_active_cost_items_to_rag", fake_sync)
    old_flag = _set_flag("feature_cost_db", True)
    try:
        response = client.post("/api/v1/admin/cost-items/sync-rag", headers=admin_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 400
    assert response.json()["detail"] == "no active cost items"


def test_cost_rag_sync_runs_endpoint_lists_recent_runs(client):
    admin_user, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        run = CostRagSyncRun(
            source="cost_items.active",
            status="success",
            requested_count=190,
            synced_count=190,
            message=f"synced {suffix}",
            error=None,
            rag_service_url="http://127.0.0.1:8001",
            http_status=200,
            duration_ms=1234,
            triggered_by=admin_user.id,
            triggered_by_username=admin_user.username,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
    finally:
        db.close()

    old_flag = _set_flag("feature_cost_db", True)
    try:
        staff_response = client.get("/api/v1/admin/cost-items/sync-rag/runs", headers=staff_headers)
        admin_response = client.get("/api/v1/admin/cost-items/sync-rag/runs", headers=admin_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert staff_response.status_code == 403
    assert admin_response.status_code == 200
    data = admin_response.json()["data"]
    assert any(item["id"] == run_id and item["message"] == f"synced {suffix}" for item in data)
