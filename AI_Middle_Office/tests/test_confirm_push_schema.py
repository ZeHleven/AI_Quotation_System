import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User


def _create_user_headers(client):
    username = f"push_user_{uuid.uuid4().hex[:8]}"
    password = "secret123"
    db = SessionLocal()
    try:
        db.add(
            User(
                username=username,
                hashed_password=get_password_hash(password),
                role="user",
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


def test_confirm_push_accepts_typed_payload_and_extra_fields(client, monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    async def fake_post_json_via_gateway(**kwargs):
        payload = kwargs["json_payload"]
        assert payload["project_details"][0]["project_name"] == "墙面刷新"
        assert payload["workflow_extra"] == "kept"
        assert payload["excel_base64"]
        return FakeResponse()

    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", fake_post_json_via_gateway)

    response = client.post(
        "/api/v1/confirm_push",
        headers=_create_user_headers(client),
        json={
            "project_details": [
                {
                    "project_name": "墙面刷新",
                    "unit_price": 20,
                    "total_price": 200,
                    "notes": "标准工艺",
                    "extra_col": "kept",
                }
            ],
            "workflow_extra": "kept",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"]


def test_confirm_push_rejects_invalid_project_details(client):
    response = client.post(
        "/api/v1/confirm_push",
        headers=_create_user_headers(client),
        json={"project_details": "not-a-list"},
    )

    assert response.status_code == 422
