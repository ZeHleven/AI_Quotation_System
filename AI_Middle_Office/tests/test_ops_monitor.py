import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services import ops_monitor


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


def test_ops_http_probe_uses_httpx_client(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"client_kwargs": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, **kwargs):
            calls[-1].update({"url": url, "kwargs": kwargs})
            return httpx.Response(204, request=httpx.Request("GET", url))

    monkeypatch.setattr(ops_monitor.httpx, "Client", FakeClient)

    result = ops_monitor._http_probe("http://service.test/health", 0.5)

    assert result == {"http_status": 204}
    assert calls[0]["client_kwargs"] == {"timeout": 0.5, "follow_redirects": False}
    assert calls[0]["kwargs"]["headers"]["User-Agent"] == "ai-middle-office-ops-probe"


def test_dingtalk_alerts_use_httpx_client(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"client_kwargs": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, url, **kwargs):
            calls[-1].update({"url": url, "kwargs": kwargs})
            return httpx.Response(200, json={"errcode": 0}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ops_monitor.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        ops_monitor,
        "settings",
        SimpleNamespace(
            alert_dingtalk_webhook="http://ding.test/webhook",
            alert_dedup_minutes=30,
            alert_rate_limit_window_minutes=5,
            alert_rate_limit_count=3,
        ),
    )
    ops_monitor._dedup_cache.clear()
    ops_monitor._rate_window.clear()

    ops_monitor.send_dingtalk_alerts([{"level": "warning", "title": "Ops test", "message": "hello"}])

    assert calls[0]["client_kwargs"] == {"timeout": 10}
    assert calls[0]["url"] == "http://ding.test/webhook"
    assert calls[0]["kwargs"]["json"]["msgtype"] == "markdown"
    assert "Ops test" in ops_monitor._dedup_cache
