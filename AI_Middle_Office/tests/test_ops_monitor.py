import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def test_ops_probe_retries_transient_http_failure(monkeypatch):
    calls = {"count": 0}

    def flaky_http_probe(url, timeout_seconds):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("transient timeout")
        return {"http_status": 200}

    monkeypatch.setattr(ops_monitor, "_http_probe", flaky_http_probe)
    monkeypatch.setattr(ops_monitor.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        ops_monitor,
        "settings",
        SimpleNamespace(
            rag_service_url="http://rag.test",
            ops_probe_timeout_seconds=0.1,
            ops_probe_attempts=2,
            ops_probe_retry_delay_seconds=0.01,
        ),
    )

    result = ops_monitor.check_rag_service()

    assert result["ok"] is True
    assert result["meta"]["http_status"] == 200
    assert calls["count"] == 2


def test_collect_error_logs_ignores_stale_timestamped_errors(monkeypatch):
    log_dir = Path(__file__).resolve().parent / ".test_ops_logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()
    try:
        now = datetime.now()
        old_at = now - timedelta(days=1)
        recent_at = now - timedelta(minutes=5)
        log_file = log_dir / "celery_worker_test.log"
        log_file.write_text(
            "\n".join(
                [
                    f"[{old_at:%Y-%m-%d %H:%M:%S},000: ERROR/MainProcess] Cannot connect to redis://broker:6379/0",
                    f"[{recent_at:%Y-%m-%d %H:%M:%S},000: ERROR/MainProcess] quote_job_crashed",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ops_monitor, "LOG_DIR", log_dir)
        monkeypatch.setattr(
            ops_monitor,
            "settings",
            SimpleNamespace(
                ops_log_max_files=2,
                ops_log_scan_lines=50,
                ops_log_lookback_minutes=180,
            ),
        )

        result = ops_monitor.collect_error_logs(limit=10)

        assert result["total_matches"] == 1
        assert result["total_events"] == 1
        assert "quote_job_crashed" in result["items"][0]["message"]
        assert "Cannot connect to redis" not in result["items"][0]["message"]
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


def test_collect_error_logs_aggregates_tracebacks_into_events(monkeypatch):
    log_dir = Path(__file__).resolve().parent / ".test_ops_logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()
    try:
        first_at = datetime.now() - timedelta(minutes=8)
        second_at = datetime.now() - timedelta(minutes=6)
        log_file = log_dir / "celery_worker_test.log"
        log_file.write_text(
            "\n".join(
                [
                    f"[{first_at:%Y-%m-%d %H:%M:%S},000: ERROR/MainProcess] quote_request_timeout",
                    "Traceback (most recent call last):",
                    "Traceback (most recent call last):",
                    f"[{second_at:%Y-%m-%d %H:%M:%S},000: ERROR/MainProcess] quote_request_timeout",
                    "Traceback (most recent call last):",
                    "Traceback (most recent call last):",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ops_monitor, "LOG_DIR", log_dir)
        monkeypatch.setattr(
            ops_monitor,
            "settings",
            SimpleNamespace(
                ops_log_max_files=2,
                ops_log_scan_lines=50,
                ops_log_lookback_minutes=180,
                ops_log_current_minutes=30,
            ),
        )

        result = ops_monitor.collect_error_logs(limit=10)

        assert result["total_matches"] == 6
        assert result["total_events"] == 2
        assert result["current_event_count"] == 0
        assert result["historical_event_count"] == 2
        assert {item["category"] for item in result["items"]} == {"quote_timeout"}
        assert {item["match_count"] for item in result["items"]} == {3}
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


def test_build_alerts_ignores_historical_log_events_when_services_are_ok():
    services = [{"name": "Celery Worker", "ok": True}]
    jobs = {"stuck_count": 0}
    logs = {
        "total_matches": 6,
        "total_events": 2,
        "current_event_count": 0,
        "historical_event_count": 2,
        "items": [{"category": "quote_timeout", "status": "historical"}],
    }

    alerts = ops_monitor.build_alerts(services, jobs, logs)

    assert not any(alert.get("kind") == "current_log_events" for alert in alerts)


def test_build_alerts_reports_current_log_events():
    services = [{"name": "Celery Worker", "ok": True}]
    jobs = {"stuck_count": 0}
    logs = {
        "total_matches": 1,
        "total_events": 1,
        "current_event_count": 1,
        "historical_event_count": 0,
        "items": [{"category": "quote_job_crashed", "status": "current"}],
    }

    alerts = ops_monitor.build_alerts(services, jobs, logs)

    assert any(alert.get("kind") == "current_log_events" for alert in alerts)


def test_acknowledge_error_logs_suppresses_current_alert_but_keeps_history(monkeypatch):
    log_dir = Path(__file__).resolve().parent / ".test_ops_logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()
    try:
        recent_at = datetime.now() - timedelta(minutes=5)
        log_file = log_dir / "celery_worker_test.log"
        log_file.write_text(
            f"[{recent_at:%Y-%m-%d %H:%M:%S},000: ERROR/MainProcess] "
            "consumer: Cannot connect to redis://broker:6379/0: Timeout connecting to server.",
            encoding="utf-8",
        )
        monkeypatch.setattr(ops_monitor, "LOG_DIR", log_dir)
        monkeypatch.setattr(
            ops_monitor,
            "settings",
            SimpleNamespace(
                ops_log_max_files=2,
                ops_log_scan_lines=50,
                ops_log_lookback_minutes=180,
                ops_log_current_minutes=30,
            ),
        )

        before = ops_monitor.collect_error_logs(limit=10)
        assert before["current_event_count"] == 1

        ack_result = ops_monitor.acknowledge_error_logs(username="admin")

        assert ack_result["acknowledged_count"] == 1
        after = ops_monitor.collect_error_logs(limit=10)
        assert after["current_event_count"] == 0
        assert after["historical_event_count"] == 1
        assert after["acknowledged_event_count"] == 1
        assert after["items"][0]["status"] == "acknowledged"
        assert after["items"][0]["acknowledged_by"] == "admin"
        alerts = ops_monitor.build_alerts(
            [{"name": "Celery Worker", "ok": True}],
            {"stuck_count": 0},
            after,
        )
        assert not any(alert.get("kind") == "current_log_events" for alert in alerts)
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


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
