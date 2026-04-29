import json
import shutil
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
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


def test_material_save_creates_snapshot_and_rollback_restores_it(client):
    _reset_material_store()
    headers = _create_admin_headers(client)

    original = [
        {"id": "m1", "item_name": "Original item", "unit_price": 10.0, "unit": "m2", "notes": "old", "is_draft": False}
    ]
    updated = [
        {"id": "m2", "item_name": "Updated item", "unit_price": 25.0, "unit": "m2", "notes": "new", "is_draft": False}
    ]
    settings.materials_file.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    save_response = client.post("/api/v1/admin/materials", json=updated, headers=headers)
    assert save_response.status_code == 200
    snapshot = save_response.json()["snapshot"]
    assert snapshot["action"] == "save_before_overwrite"
    assert snapshot["item_count"] == 1

    audit_response = client.get("/api/v1/admin/materials/audit", headers=headers)
    assert audit_response.status_code == 200
    assert audit_response.json()["data"][0]["snapshot_id"] == snapshot["snapshot_id"]

    rollback_response = client.post(
        f"/api/v1/admin/materials/rollback/{snapshot['snapshot_id']}",
        headers=headers,
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["restored_snapshot"]["snapshot_id"] == snapshot["snapshot_id"]

    materials_response = client.get("/api/v1/admin/materials", headers=headers)
    assert materials_response.status_code == 200
    assert materials_response.json()["data"] == original
