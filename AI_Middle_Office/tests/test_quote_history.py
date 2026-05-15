import json
import uuid
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_history import QuoteHistory, QuoteHistoryItem
from app.models.quote_job import QuoteJob
from app.models.user import User


def _create_user_headers(client):
    username = f"history_user_{uuid.uuid4().hex[:8]}"
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
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_job(username: str) -> QuoteJob:
    job = QuoteJob(
        job_id=str(uuid.uuid4()),
        username=username,
        status="succeeded",
        stage="completed",
        message="wall paint and floor tile for living room",
        file_name="living-room-plan.png",
        file_mime_type="image/png",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
        result_json=json.dumps(
            {
                "project_details": [
                    {"project_name": "wall paint", "unit_price": 20, "total_price": 200},
                    {"project_name": "floor tile", "unit_price": 60, "total_price": 120},
                ]
            }
        ),
        finished_at=datetime.now(timezone.utc),
    )
    db = SessionLocal()
    try:
        db.add(job)
        db.commit()
        db.refresh(job)
        return job
    finally:
        db.close()


def test_confirm_push_writes_readable_history_and_items(client, monkeypatch):
    username, headers = _create_user_headers(client)
    job = _create_job(username)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    async def fake_post_json_via_gateway(**kwargs):
        return FakeResponse()

    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", fake_post_json_via_gateway)

    response = client.post(
        "/api/v1/confirm_push",
        headers=headers,
        json={
            "quote_job_id": job.job_id,
            "trace_id": job.trace_id,
            "project_details": [
                {
                    "project_name": "wall paint",
                    "space": "living room",
                    "quantity": 10,
                    "unit": "sqm",
                    "unit_price": 22,
                    "total_price": 220,
                    "material": "latex paint",
                    "craft": "two coats",
                    "notes": "upgraded",
                },
                {
                    "project_name": "floor tile",
                    "space": "living room",
                    "quantity": 2,
                    "unit": "sqm",
                    "unit_price": 55,
                    "total_price": 110,
                    "spec": "600x600",
                    "notes": "standard",
                },
            ],
        },
    )

    assert response.status_code == 200

    db = SessionLocal()
    try:
        history = db.query(QuoteHistory).filter(QuoteHistory.quote_job_id == job.job_id).one()
        assert history.quote_id == job.job_id
        assert history.trace_id == job.trace_id
        assert history.request_text == "wall paint and floor tile for living room"
        assert history.source_file_name == "living-room-plan.png"
        assert history.display_title.startswith("AI")
        assert history.project_summary == "wall paint, floor tile; total_items=2"
        assert history.first_project_names == "wall paint, floor tile"
        assert history.confirmed_by == username
        assert history.pushed_to_dingtalk is True
        assert history.total_amount == 330
        assert history.item_count == 2
        assert json.loads(history.payload_json)["excel_base64"].startswith("<base64:")

        items = (
            db.query(QuoteHistoryItem)
            .filter(QuoteHistoryItem.quote_history_id == history.id)
            .order_by(QuoteHistoryItem.line_no.asc())
            .all()
        )
        assert [item.project_name for item in items] == ["wall paint", "floor tile"]
        assert items[0].quantity == 10
        assert items[0].unit_price == 22
        assert items[0].total_price == 220
        assert items[0].material == "latex paint"
        assert items[1].spec == "600x600"
        history_id = history.id
    finally:
        db.close()

    list_response = client.get("/api/v1/history", headers=headers)
    assert list_response.status_code == 200
    row = next(item for item in list_response.json()["data"] if item["id"] == history_id)
    assert row["request_text"] == "wall paint and floor tile for living room"
    assert row["source_file_name"] == "living-room-plan.png"
    assert row["first_project_names"] == ["wall paint", "floor tile"]
    assert row["project_summary"] == "wall paint, floor tile; total_items=2"

    detail_response = client.get(f"/api/v1/history/{history_id}", headers=headers)
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["items"][0]["project_name"] == "wall paint"
    assert detail["items"][0]["space"] == "living room"
    assert detail["payload"]["project_details"][1]["project_name"] == "floor tile"
