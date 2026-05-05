import uuid
import asyncio

import httpx

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User
from app.services.model_gateway import (
    call_glm_vision_extract,
    post_json_via_gateway,
    record_model_call,
    record_model_call_async,
    reset_circuit_breakers,
)


def _admin_headers(client):
    username = f"gateway_admin_{uuid.uuid4().hex[:10]}"
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


def test_model_gateway_stats_returns_logged_calls(client):
    reset_circuit_breakers()
    headers = _admin_headers(client)
    record_model_call(
        provider="zhipu",
        model="glm-4v-flash",
        endpoint_type="vision_extract",
        status="success",
        username="tester",
        trace_id="trace-test",
        http_status=200,
        latency_ms=123.4,
        input_chars=100,
        output_chars=20,
    )

    response = client.get("/api/v1/admin/model_gateway/stats", headers=headers)

    assert response.status_code == 200
    rows = response.json()["data"]
    assert any(
        row["provider"] == "zhipu"
        and row["model"] == "glm-4v-flash"
        and row["endpoint_type"] == "vision_extract"
        and row["status"] == "success"
        for row in rows
    )


def test_model_gateway_stats_requires_admin(client):
    username = f"gateway_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    token = response.json()["access_token"]

    response = client.get(
        "/api/v1/admin/model_gateway/stats",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_record_model_call_async_uses_threadpool(monkeypatch):
    calls = []

    def fake_record_model_call(**kwargs):
        calls.append({"recorded": kwargs})

    async def fake_to_thread(fn, **kwargs):
        calls.append({"threaded_fn": fn, "threaded_kwargs": kwargs})
        return fn(**kwargs)

    monkeypatch.setattr("app.services.model_gateway.record_model_call", fake_record_model_call)
    monkeypatch.setattr("app.services.model_gateway.asyncio.to_thread", fake_to_thread)

    asyncio.run(
        record_model_call_async(
            provider="zhipu",
            model="glm-4v-flash",
            endpoint_type="vision_extract",
            status="success",
        )
    )

    assert calls[0]["threaded_fn"] is fake_record_model_call
    assert calls[0]["threaded_kwargs"]["provider"] == "zhipu"
    assert calls[1]["recorded"]["status"] == "success"


def test_post_json_via_gateway_uses_async_httpx(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        post_json_via_gateway(
            provider="n8n",
            model="dify-deepseek",
            endpoint_type="quote_calc",
            url="http://example.test/webhook",
            json_payload={"hello": "world"},
            headers={"X-Test": "1"},
            timeout=12,
            username="tester",
            trace_id="trace-httpx",
        )
    )

    assert response.status_code == 200
    assert calls[0]["client_kwargs"]["timeout"] == 12
    assert calls[0]["url"] == "http://example.test/webhook"
    assert calls[0]["post_kwargs"]["json"] == {"hello": "world"}
    assert calls[0]["post_kwargs"]["headers"] == {"X-Test": "1"}


def test_call_glm_vision_extract_uses_async_httpx_without_env_proxy(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(200, json={"choices": [{"message": {"content": "墙面刷新，20平米"}}]})

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_glm_vision_extract(
            "ZmFrZQ==",
            "image/png",
            username="tester",
            trace_id="trace-vision",
        )
    )

    assert result == "墙面刷新，20平米"
    assert calls[0]["client_kwargs"]["trust_env"] is False
    assert calls[0]["client_kwargs"]["verify"] is False
    assert calls[0]["post_kwargs"]["headers"]["Authorization"].startswith("Bearer ")
