import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_job import QuoteJob
from app.models.user import User


def _login_headers(client):
    username = f"job_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _admin_headers(client):
    username = f"job_admin_{uuid.uuid4().hex[:10]}"
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


def test_create_quote_job_returns_queued_status(client):
    headers = _login_headers(client)

    response = client.post(
        "/api/v1/quote/jobs",
        data={"message": "客厅地砖10平米"},
        headers=headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert body["trace_id"]
    assert body["events"][0]["status"] == "queued"

    status_response = client.get(f"/api/v1/quote/jobs/{body['job_id']}", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["job_id"] == body["job_id"]


def test_quote_job_requires_auth(client):
    response = client.get("/api/v1/quote/jobs/missing")

    assert response.status_code == 401


def test_list_and_cancel_quote_job(client):
    headers = _login_headers(client)
    create_response = client.post("/api/v1/quote/jobs", data={"message": "卧室刷漆20平米"}, headers=headers)
    assert create_response.status_code == 202
    job_id = create_response.json()["job_id"]

    list_response = client.get("/api/v1/quote/jobs?status=queued", headers=headers)
    assert list_response.status_code == 200
    assert any(item["job_id"] == job_id for item in list_response.json()["data"])

    cancel_response = client.post(f"/api/v1/quote/jobs/{job_id}/cancel", headers=headers)
    assert cancel_response.status_code == 200
    body = cancel_response.json()
    assert body["status"] == "canceled"
    assert body["stage"] == "canceled"
    assert body["error_message"] == "任务已取消"


def test_retry_canceled_quote_job_creates_new_job(client):
    headers = _login_headers(client)
    create_response = client.post("/api/v1/quote/jobs", data={"message": "厨房吊顶5平米"}, headers=headers)
    job_id = create_response.json()["job_id"]
    cancel_response = client.post(f"/api/v1/quote/jobs/{job_id}/cancel", headers=headers)
    assert cancel_response.status_code == 200

    retry_response = client.post(f"/api/v1/quote/jobs/{job_id}/retry", headers=headers)
    assert retry_response.status_code == 202
    body = retry_response.json()
    assert body["job_id"] != job_id
    assert body["status"] == "queued"
    assert body["events"][0]["source_job_id"] == job_id


def test_admin_can_list_all_jobs_and_mark_timeouts(client):
    user_headers = _login_headers(client)
    admin_headers = _admin_headers(client)
    create_response = client.post("/api/v1/quote/jobs", data={"message": "卫生间防水8平米"}, headers=user_headers)
    job_id = create_response.json()["job_id"]

    db = SessionLocal()
    try:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).first()
        job.status = "running"
        job.stage = "n8n"
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=90)
        db.commit()
    finally:
        db.close()

    mark_response = client.post("/api/v1/admin/quote/jobs/mark_timeouts?timeout_minutes=30", headers=admin_headers)
    assert mark_response.status_code == 200
    body = mark_response.json()
    assert body["marked_count"] >= 1
    assert any(item["job_id"] == job_id for item in body["data"])

    list_response = client.get("/api/v1/quote/jobs?status=timed_out", headers=admin_headers)
    assert list_response.status_code == 200
    assert any(item["job_id"] == job_id for item in list_response.json()["data"])
