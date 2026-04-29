import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User
from app.services.model_gateway import record_model_call, reset_circuit_breakers


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
