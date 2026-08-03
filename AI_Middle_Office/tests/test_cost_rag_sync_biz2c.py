import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.api.v1 import cost_items as cost_items_api
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_item import COST_STATUS_ACTIVE, COST_STATUS_DRAFT, CostItem, CostRagSyncRun
from app.models.user import User, UserRole
from app.services import cost_rag_sync
from app.services.cost_rag_sync import (
    active_cost_items_rag_payload,
    cost_rag_sync_status_summary,
    sync_active_cost_items_to_rag,
)
from app.services.enterprise_quota_units import normalize_enterprise_quota_unit


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


def _reset_sync_status_test_state(db, *, active_updated_at: datetime) -> None:
    db.query(CostRagSyncRun).filter(CostRagSyncRun.source == "cost_items.active").delete(synchronize_session=False)
    db.query(CostItem).filter(CostItem.status == COST_STATUS_ACTIVE).update(
        {CostItem.updated_at: active_updated_at},
        synchronize_session=False,
    )
    db.commit()


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
    assert row["unit"] == normalize_enterprise_quota_unit("m2")
    assert row["is_draft"] is False
    assert "cost_items.active" in row["notes"]
    assert "client_tax_excluded_price" not in row
    assert "\u5bf9\u7532\u7a0e\u524d\u7efc\u5408\u5355\u4ef7: 50.0" in row["notes"]
    assert "\u52b3\u52a1\u53d1\u5305\u7efc\u5408\u5355\u4ef7: 42.5" in row["notes"]
    assert "\u73ed\u7ec4\u6807\u5e95\u7a0e\u524d\u4ef7: 38.0" in row["notes"]


def test_sync_active_cost_items_to_rag_endpoint_requires_approver(client, monkeypatch):
    _, admin_headers = _headers(client, "admin")
    _, approver_headers = _headers(client, "cost_approver")
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
        approver_response = client.post("/api/v1/admin/cost-items/sync-rag", headers=approver_headers)
        admin_response = client.post("/api/v1/admin/cost-items/sync-rag", headers=admin_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert staff_response.status_code == 403
    assert staff_response.json()["detail"] == "PERMISSION_DENIED"
    assert approver_response.status_code == 200
    assert approver_response.json()["data"]["synced_count"] == 3
    assert admin_response.status_code == 200
    assert admin_response.json()["data"]["synced_count"] == 3
    assert admin_response.json()["data"]["source"] == "cost_items.active"
    assert admin_response.json()["data"]["run"]["status"] == "success"
    assert len(calls) == 2


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


def test_sync_active_cost_items_to_rag_returns_zero_synced_count_on_http_error(monkeypatch):
    suffix = uuid.uuid4().hex[:8]

    class FakeResponse:
        status_code = 500

        def json(self):
            return {"detail": f"rag failed {suffix}"}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr(cost_rag_sync.httpx, "AsyncClient", FakeClient)

    db = SessionLocal()
    try:
        _seed_cost_item(db, item_name=f"BIZ2c sync http error {suffix}", status=COST_STATUS_ACTIVE)
        result = asyncio.run(sync_active_cost_items_to_rag(db, "biz2c-admin", user_id=None))
        run_id = result["run"]["id"]
        run = db.query(CostRagSyncRun).filter(CostRagSyncRun.id == run_id).one()
    finally:
        db.close()

    assert result["success"] is False
    assert result["synced_count"] == 0
    assert result["error"] == f"RAG 服务返回错误: rag failed {suffix}"
    assert run.status == "failed"
    assert run.synced_count == 0
    assert run.http_status == 500


def test_sync_active_cost_items_to_rag_returns_zero_synced_count_on_timeout(monkeypatch):
    suffix = uuid.uuid4().hex[:8]

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(cost_rag_sync.httpx, "AsyncClient", FakeClient)

    db = SessionLocal()
    try:
        _seed_cost_item(db, item_name=f"BIZ2c sync timeout {suffix}", status=COST_STATUS_ACTIVE)
        result = asyncio.run(sync_active_cost_items_to_rag(db, "biz2c-admin", user_id=None))
        run_id = result["run"]["id"]
        run = db.query(CostRagSyncRun).filter(CostRagSyncRun.id == run_id).one()
    finally:
        db.close()

    assert result["success"] is False
    assert result["synced_count"] == 0
    assert result["error"] == "RAG 服务超时，请检查 CentOS 容器状态"
    assert run.status == "failed"
    assert run.synced_count == 0


def test_cost_rag_sync_status_summary_detects_synced_and_stale_states(client):
    suffix = uuid.uuid4().hex[:8]
    sync_finished_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    db = SessionLocal()
    try:
        active_item = _seed_cost_item(db, item_name=f"BIZ2c status summary {suffix}", status=COST_STATUS_ACTIVE)
        _reset_sync_status_test_state(db, active_updated_at=sync_finished_at - timedelta(minutes=30))
        active_count = db.query(CostItem).filter(CostItem.status == COST_STATUS_ACTIVE).count()
        success_run = CostRagSyncRun(
            source="cost_items.active",
            status="success",
            requested_count=active_count,
            synced_count=active_count,
            message="synced",
            rag_service_url="http://127.0.0.1:8001",
            http_status=200,
            started_at=sync_finished_at - timedelta(seconds=5),
            finished_at=sync_finished_at,
        )
        db.add(success_run)
        db.commit()

        synced_summary = cost_rag_sync_status_summary(db)

        active_item.updated_at = sync_finished_at + timedelta(minutes=2)
        db.add(active_item)
        db.commit()
        stale_summary = cost_rag_sync_status_summary(db)
    finally:
        db.close()

    assert synced_summary["status"] == "synced"
    assert synced_summary["needs_sync"] is False
    assert stale_summary["status"] == "stale"
    assert stale_summary["needs_sync"] is True
    assert stale_summary["is_stale"] is True


def test_cost_rag_sync_status_summary_normalizes_database_local_time(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        active_item = _seed_cost_item(db, item_name=f"BIZ2c status timezone {suffix}", status=COST_STATUS_ACTIVE)
        db_utc_offset = timedelta(hours=8)
        success_finished_at = datetime.now(timezone.utc) + timedelta(days=2)
        _reset_sync_status_test_state(
            db,
            active_updated_at=(success_finished_at + db_utc_offset - timedelta(hours=1)).replace(tzinfo=None),
        )
        active_count = db.query(CostItem).filter(CostItem.status == COST_STATUS_ACTIVE).count()
        success_run = CostRagSyncRun(
            source="cost_items.active",
            status="success",
            requested_count=active_count,
            synced_count=active_count,
            message="synced",
            rag_service_url="http://127.0.0.1:8001",
            http_status=200,
            started_at=success_finished_at - timedelta(minutes=3),
            finished_at=success_finished_at,
        )
        db.add(success_run)
        db.commit()

        monkeypatch.setattr(cost_rag_sync, "_database_utc_offset", lambda _db: db_utc_offset)
        active_item.updated_at = (success_finished_at + db_utc_offset - timedelta(minutes=30)).replace(tzinfo=None)
        db.add(active_item)
        db.commit()
        synced_summary = cost_rag_sync_status_summary(db)

        active_item.updated_at = (success_finished_at + db_utc_offset + timedelta(minutes=30)).replace(tzinfo=None)
        db.add(active_item)
        db.commit()
        stale_summary = cost_rag_sync_status_summary(db)
    finally:
        db.close()

    assert synced_summary["status"] == "synced"
    assert synced_summary["needs_sync"] is False
    assert synced_summary["latest_active_updated_at_utc"].startswith(
        (success_finished_at - timedelta(minutes=30)).replace(tzinfo=timezone.utc).isoformat()[:19]
    )
    assert stale_summary["status"] == "stale"
    assert stale_summary["needs_sync"] is True


def test_cost_rag_sync_status_summary_reports_latest_failed_after_success(client):
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc) + timedelta(days=10)
    db = SessionLocal()
    try:
        _seed_cost_item(db, item_name=f"BIZ2c status failed {suffix}", status=COST_STATUS_ACTIVE)
        _reset_sync_status_test_state(db, active_updated_at=now - timedelta(hours=1))
        active_count = db.query(CostItem).filter(CostItem.status == COST_STATUS_ACTIVE).count()
        db.add(
            CostRagSyncRun(
                source="cost_items.active",
                status="success",
                requested_count=active_count,
                synced_count=active_count,
                message="synced",
                rag_service_url="http://127.0.0.1:8001",
                http_status=200,
                started_at=now + timedelta(seconds=5),
                finished_at=now + timedelta(seconds=10),
            )
        )
        db.add(
            CostRagSyncRun(
                source="cost_items.active",
                status="failed",
                requested_count=active_count,
                synced_count=0,
                message="failed",
                error="failed",
                rag_service_url="http://127.0.0.1:8001",
                http_status=500,
                started_at=now + timedelta(seconds=20),
                finished_at=now + timedelta(seconds=25),
            )
        )
        db.commit()

        summary = cost_rag_sync_status_summary(db)
    finally:
        db.close()

    assert summary["status"] == "failed"
    assert summary["needs_sync"] is True
    assert summary["latest_run"]["status"] == "failed"
    assert summary["latest_successful_run"]["status"] == "success"


def test_cost_rag_sync_status_endpoint_requires_cost_view_access(client, monkeypatch):
    _, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    _, viewer_headers = _headers(client, "cost_viewer")

    def fake_summary(db):
        return {"status": "synced", "status_label": "已同步", "active_count": 3}

    monkeypatch.setattr(cost_items_api, "cost_rag_sync_status_summary", fake_summary)
    old_flag = _set_flag("feature_cost_db", True)
    try:
        staff_response = client.get("/api/v1/admin/cost-items/sync-rag/status", headers=staff_headers)
        viewer_response = client.get("/api/v1/admin/cost-items/sync-rag/status", headers=viewer_headers)
        admin_response = client.get("/api/v1/admin/cost-items/sync-rag/status", headers=admin_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert staff_response.status_code == 403
    assert viewer_response.status_code == 200
    assert viewer_response.json()["data"]["status"] == "synced"
    assert admin_response.status_code == 200
    assert admin_response.json()["data"]["active_count"] == 3


def test_cost_rag_sync_runs_endpoint_lists_recent_runs(client):
    admin_user, admin_headers = _headers(client, "admin")
    _, staff_headers = _headers(client, "staff")
    _, viewer_headers = _headers(client, "cost_viewer")
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
        viewer_response = client.get("/api/v1/admin/cost-items/sync-rag/runs", headers=viewer_headers)
        admin_response = client.get("/api/v1/admin/cost-items/sync-rag/runs", headers=admin_headers)
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert staff_response.status_code == 403
    assert viewer_response.status_code == 200
    assert admin_response.status_code == 200
    data = admin_response.json()["data"]
    viewer_data = viewer_response.json()["data"]
    assert any(item["id"] == run_id and item["message"] == f"synced {suffix}" for item in viewer_data)
    assert any(item["id"] == run_id and item["message"] == f"synced {suffix}" for item in data)


def test_sync_active_cost_items_to_rag_endpoint_supports_dry_run(client, monkeypatch):
    _, admin_headers = _headers(client, "admin")
    calls = []

    def fake_preview(db, sample_limit=5):
        calls.append(sample_limit)
        return {
            "success": True,
            "dry_run": True,
            "message": "dry-run completed",
            "requested_count": 2,
            "synced_count": 0,
            "source": "enterprise_quota.active",
            "source_detail": {"payload_count": 2},
            "sample_materials": [{"id": "enterprise_quota_item_1"}],
            "error": None,
            "run": None,
        }

    async def fake_sync(db, username, user_id=None):
        raise AssertionError("sync should not be called for dry-run")

    monkeypatch.setattr(cost_items_api, "preview_active_cost_items_rag_sync", fake_preview)
    monkeypatch.setattr(cost_items_api, "sync_active_cost_items_to_rag", fake_sync)
    old_flag = _set_flag("feature_cost_db", True)
    try:
        response = client.post(
            "/api/v1/admin/cost-items/sync-rag?dry_run=true&sample_limit=2",
            headers=admin_headers,
        )
    finally:
        _set_flag("feature_cost_db", old_flag)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dry_run"] is True
    assert data["source"] == "enterprise_quota.active"
    assert data["requested_count"] == 2
    assert calls == [2]
