import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User


def _admin_headers(client):
    username = f"response_admin_{uuid.uuid4().hex[:10]}"
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


def _assert_standard_response(response):
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert "message" in body
    assert "data" in body
    return body


def test_admin_read_endpoints_use_standard_response_shape(client):
    headers = _admin_headers(client)

    for path in [
        "/api/v1/admin/users",
        "/api/v1/admin/materials",
        "/api/v1/admin/materials/audit",
        "/api/v1/admin/model_gateway/stats",
        "/api/v1/admin/rag_eval/latest",
        "/api/v1/quote/jobs",
        "/api/v1/files",
        "/api/v1/history",
    ]:
        _assert_standard_response(client.get(path, headers=headers))


def test_paginated_responses_keep_compatibility_fields(client):
    headers = _admin_headers(client)

    files_body = _assert_standard_response(client.get("/api/v1/files", headers=headers))
    assert files_body["total"] >= 0
    assert files_body["page"] == 1
    assert files_body["page_size"] == 20

    history_body = _assert_standard_response(client.get("/api/v1/history", headers=headers))
    assert history_body["total"] >= 0
    assert history_body["page"] == 1
    assert history_body["page_size"] == 20


def test_auth_responses_keep_compatibility_fields(client):
    username = f"response_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"

    register_body = _assert_standard_response(
        client.post("/api/v1/auth/register", json={"username": username, "password": password})
    )
    assert register_body["data"]["username"] == username

    login_body = _assert_standard_response(
        client.post("/api/v1/auth/login", data={"username": username, "password": password})
    )
    assert login_body["access_token"] == login_body["data"]["access_token"]

    me_body = _assert_standard_response(
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {login_body['access_token']}"})
    )
    assert me_body["username"] == username
    assert me_body["data"]["username"] == username
