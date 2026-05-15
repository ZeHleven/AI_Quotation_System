import json
import shutil
import uuid

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.material import Material, MaterialSnapshot
from app.models.user import User


def _create_admin_headers(client):
    username = f"audit_admin_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    db = SessionLocal()
    try:
        db.add(
            User(
                username=username,
                hashed_password=get_password_hash(password),
                role="admin",
                quota=20,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _reset_material_store():
    if settings.materials_file.exists():
        settings.materials_file.unlink()
    audit_dir = settings.materials_file.parent / "materials_audit"
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    settings.materials_file.parent.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        db.query(MaterialSnapshot).delete()
        db.query(Material).delete()
        db.commit()
    finally:
        db.close()


def _material_core(item):
    return {
        "id": item["id"],
        "item_name": item["item_name"],
        "unit_price": item["unit_price"],
        "unit": item["unit"],
        "notes": item["notes"],
        "is_draft": item["is_draft"],
    }


def test_materials_import_legacy_json_on_first_read(client):
    _reset_material_store()
    headers = _create_admin_headers(client)
    legacy = [
        {"id": "legacy_1", "item_name": "Legacy item", "unit_price": 12.5, "unit": "m2", "notes": "from json", "is_draft": False}
    ]
    settings.materials_file.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    response = client.get("/api/v1/admin/materials", headers=headers)
    assert response.status_code == 200
    material = response.json()["data"][0]
    assert _material_core(material) == legacy[0]
    assert material["source"] == "manual"
    assert material["status"] == "active"
    assert material["usage_count"] == 0

    db = SessionLocal()
    try:
        assert db.query(Material).count() == 1
    finally:
        db.close()


def test_material_save_creates_snapshot_and_rollback_restores_it(client):
    _reset_material_store()
    headers = _create_admin_headers(client)

    original = [
        {"id": "m1", "item_name": "Original item", "unit_price": 10.0, "unit": "m2", "notes": "old", "is_draft": False}
    ]
    updated = [
        {"id": "m2", "item_name": "Updated item", "unit_price": 25.0, "unit": "m2", "notes": "new", "is_draft": False}
    ]
    settings.materials_file.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

    save_response = client.post("/api/v1/admin/materials", json=updated, headers=headers)
    assert save_response.status_code == 200
    snapshot = save_response.json()["snapshot"]
    assert snapshot["action"] == "save_before_overwrite"
    assert snapshot["item_count"] == 1
    assert snapshot["added_count"] == 1
    assert snapshot["updated_count"] == 0
    assert snapshot["deleted_count"] == 1
    assert snapshot["diff_summary"]["added"][0]["id"] == "m2"
    assert snapshot["diff_summary"]["deleted"][0]["id"] == "m1"

    audit_response = client.get("/api/v1/admin/materials/audit", headers=headers)
    assert audit_response.status_code == 200
    assert audit_response.json()["data"][0]["snapshot_id"] == snapshot["snapshot_id"]

    rollback_response = client.post(
        f"/api/v1/admin/materials/rollback/{snapshot['snapshot_id']}",
        headers=headers,
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["restored_snapshot"]["snapshot_id"] == snapshot["snapshot_id"]
    assert rollback_response.json()["current_snapshot"]["added_count"] == 1
    assert rollback_response.json()["current_snapshot"]["deleted_count"] == 1

    materials_response = client.get("/api/v1/admin/materials", headers=headers)
    assert materials_response.status_code == 200
    assert [_material_core(item) for item in materials_response.json()["data"]] == original


def test_material_rollback_rejects_unsafe_snapshot_id(client):
    _reset_material_store()
    headers = _create_admin_headers(client)

    response = client.post("/api/v1/admin/materials/rollback/../bad", headers=headers)

    assert response.status_code == 404

    response = client.post("/api/v1/admin/materials/rollback/20260505_bad", headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid snapshot id"


def test_upload_csv_extracts_new_and_changed_items(client):
    _reset_material_store()
    headers = _create_admin_headers(client)
    client.post(
        "/api/v1/admin/materials",
        json=[
            {
                "id": "existing",
                "item_name": "既有项目",
                "unit_price": 10.0,
                "unit": "项",
                "notes": "旧价格",
                "is_draft": False,
            }
        ],
        headers=headers,
    )
    csv_text = "\n".join(
        [
            "施工项目,AI核准单价(元),单位,规格/工艺/材料说明",
            "既有项目,10,项,价格一致不应提炼",
            "既有项目,13,项,价格异动应提炼",
            "新增项目,8,米,新增应提炼",
            "新增项目,9,米,重复新增应去重",
            "费用汇总,999,项,汇总行应忽略",
            "结算合计,999,项,结算行应忽略",
        ]
    )

    response = client.post(
        "/api/v1/admin/upload_csv",
        files={"file": ("history.csv", csv_text.encode("utf-8-sig"), "text/csv")},
        headers=headers,
    )

    assert response.status_code == 200
    drafts = response.json()["data"]
    names = [item["item_name"] for item in drafts]
    assert names == ["既有项目 (历史异动待审)", "新增项目"]
    assert drafts[0]["unit_price"] == 13.0
    assert drafts[0]["source"] == "csv_import"
    assert drafts[0]["status"] == "draft"
    assert drafts[1]["notes"] == "新增应提炼"


def test_upload_csv_rejects_renamed_excel_file(client):
    _reset_material_store()
    headers = _create_admin_headers(client)

    response = client.post(
        "/api/v1/admin/upload_csv",
        files={"file": ("renamed.csv", b"PK\x03\x04not-real-csv", "text/csv")},
        headers=headers,
    )

    assert response.status_code == 400
    assert "直接修改了 Excel 文件的后缀名" in response.json()["detail"]


def test_sync_milvus_uses_async_httpx(client, monkeypatch):
    _reset_material_store()
    headers = _create_admin_headers(client)
    client.post(
        "/api/v1/admin/materials",
        json=[
            {"id": "m1", "item_name": "Sync item", "unit_price": 10.0, "unit": "m2", "notes": "", "is_draft": False}
        ],
        headers=headers,
    )
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(200, json={"message": "同步完成"})

    monkeypatch.setattr("app.services.material_sync.httpx.AsyncClient", FakeAsyncClient)

    response = client.post("/api/v1/admin/sync_milvus", headers=headers)

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert calls[0]["client_kwargs"]["timeout"] == 120
    assert calls[0]["url"].endswith("/admin/reload")
    assert calls[0]["post_kwargs"]["json"]["materials"][0]["item_name"] == "Sync item"
    assert calls[0]["post_kwargs"]["json"]["materials"][0]["source"] == "manual"
    assert calls[0]["post_kwargs"]["json"]["materials"][0]["status"] == "active"
