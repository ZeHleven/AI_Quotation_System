import json
import uuid

import httpx

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User


def _create_user_headers(client, quota: int = 5):
    username = f"chat_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    db = SessionLocal()
    try:
        db.add(
            User(
                username=username,
                hashed_password=get_password_hash(password),
                role="user",
                quota=quota,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _sse_events(response_text: str) -> list[dict]:
    events = []
    for block in response_text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_chat_sse_text_quote_reaches_preview_and_decrements_quota(client, monkeypatch):
    username, headers = _create_user_headers(client, quota=3)
    gateway_calls = []

    async def fake_post_json_via_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return httpx.Response(
            200,
            json={
                "project_details": [
                    {
                        "project_name": "墙面刷新",
                        "unit_price": 20,
                        "total_price": 20,
                        "notes": "标准工艺",
                    }
                ],
                "customer_questions_answered": "无",
            },
        )

    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", fake_post_json_via_gateway)

    response = client.post(
        "/api/v1/chat",
        data={"message": "墙面刷新 1 平米"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert [event["status"] for event in events][-1] == "preview"
    assert events[-1]["data"]["project_details"][0]["project_name"] == "墙面刷新"
    assert gateway_calls[0]["json_payload"]["text"]["content"] == "墙面刷新 1 平米"
    assert gateway_calls[0]["json_payload"]["conversationId"]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        assert user.quota == 2
    finally:
        db.close()


def test_chat_sse_normalizes_numbered_text_before_gateway(client, monkeypatch):
    _, headers = _create_user_headers(client, quota=3)
    gateway_calls = []

    async def fake_post_json_via_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return httpx.Response(
            200,
            json={
                "project_details": [
                    {"project_name": "拆除复合木地板", "unit_price": 7, "total_price": 245},
                    {"project_name": "拆除木脚线", "unit_price": 1, "total_price": 42},
                ]
            },
        )

    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", fake_post_json_via_gateway)

    response = client.post(
        "/api/v1/chat",
        data={
            "message": (
                "请生成报价明细，只包含以下两项：\n"
                "1. 拆除复合木地板，35平方米\n"
                "2. 拆除木脚线，42米"
            )
        },
        headers=headers,
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1]["status"] == "preview"
    content = gateway_calls[0]["json_payload"]["text"]["content"]
    assert "\n" not in content
    assert "1." not in content
    assert "拆除复合木地板，35平方米；拆除木脚线，42米" in content


def test_chat_sse_rejects_pdf_before_gateway_call(client, monkeypatch):
    _, headers = _create_user_headers(client)
    gateway_called = False

    async def fake_post_json_via_gateway(**kwargs):
        nonlocal gateway_called
        gateway_called = True
        return httpx.Response(200, json={})

    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", fake_post_json_via_gateway)

    response = client.post(
        "/api/v1/chat",
        data={"message": "请识别附件"},
        files={"file": ("quote.pdf", b"%PDF-1.4", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1]["status"] == "error"
    assert "暂只支持图片输入" in events[-1]["message"]
    assert gateway_called is False
