import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_job import QuoteJob
from app.models.user import User


def _admin_headers(client):
    username = f"ops_admin_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash(password), role="admin", quota=20))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_ops_dashboard_requires_admin(client):
    username = f"ops_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    response = client.get("/api/v1/admin/ops/dashboard", headers=headers)

    assert response.status_code == 403


def test_ops_dashboard_reports_services_and_stuck_jobs(client):
    headers = _admin_headers(client)
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username="ops-admin",
                status="running",
                stage="n8n",
                message="卡住任务测试",
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=90),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/admin/ops/dashboard", headers=headers)

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["overall_status"] in {"ready", "degraded"}
    assert {"mysql", "redis", "celery", "rag", "minio", "n8n"}.issubset({item["key"] for item in body["services"]})
    assert body["jobs"]["stuck_count"] >= 1
    assert any(item["job_id"] == job_id for item in body["jobs"]["stuck_jobs"])
    assert any(alert["title"] == "报价任务可能卡住" for alert in body["alerts"])
