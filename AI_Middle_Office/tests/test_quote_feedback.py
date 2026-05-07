import json
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback, QuoteRagTrace
from app.models.quote_job import QuoteJob
from app.models.user import User


def _create_user_headers(client):
    username = f"feedback_user_{uuid.uuid4().hex[:8]}"
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


def _create_succeeded_job(username: str, result: dict) -> QuoteJob:
    job = QuoteJob(
        job_id=str(uuid.uuid4()),
        username=username,
        status="succeeded",
        stage="completed",
        message="test quote request",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
        result_json=json.dumps(result, ensure_ascii=False),
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


def test_confirm_push_records_feedback_corrections_and_rag_trace(client, monkeypatch):
    username, headers = _create_user_headers(client)
    ai_result = {
        "project_details": [
            {"project_name": "wall paint", "unit_price": 20, "total_price": 200, "notes": "standard"}
        ],
        "rag_traces": [
            {"material_id": "mat-001", "item_name": "wall paint base", "rank": 1, "score": 0.91}
        ],
    }
    job = _create_succeeded_job(username, ai_result)

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
                {"project_name": "wall paint", "unit_price": 22, "total_price": 220, "notes": "upgraded"}
            ],
            "feedback_reason_category": "unit_price_adjustment",
            "feedback_reason": "manual correction",
        },
    )

    assert response.status_code == 200

    db = SessionLocal()
    try:
        feedback = db.query(QuoteFeedback).filter(QuoteFeedback.quote_job_id == job.job_id).one()
        assert feedback.status == "confirmed"
        assert feedback.username == username
        assert feedback.ai_total_amount == 200
        assert feedback.final_total_amount == 220
        assert feedback.amount_delta == 20
        assert feedback.was_modified is True
        assert feedback.pushed_to_dingtalk is True
        assert feedback.dify_prompt_version == settings.dify_prompt_version
        assert feedback.rag_collection_alias == settings.rag_collection_alias

        corrections = db.query(QuoteCorrection).filter(QuoteCorrection.feedback_id == feedback.id).all()
        assert {item.field_path for item in corrections} >= {
            "project_details[0].unit_price",
            "project_details[0].total_price",
            "project_details[0].notes",
        }

        trace = db.query(QuoteRagTrace).filter(QuoteRagTrace.feedback_id == feedback.id).one()
        assert trace.material_id == "mat-001"
        assert trace.rank == 1
        assert trace.sent_to_prompt is True
    finally:
        db.close()


def test_reject_quote_feedback_records_manual_rejection(client):
    username, headers = _create_user_headers(client)
    job = _create_succeeded_job(
        username,
        {"project_details": [{"project_name": "floor tile", "unit_price": 50, "total_price": 500}]},
    )

    response = client.post(
        "/api/v1/quote/feedback/reject",
        headers=headers,
        json={"quote_job_id": job.job_id, "trace_id": job.trace_id, "reason": "missing item"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"

    db = SessionLocal()
    try:
        feedback = db.query(QuoteFeedback).filter(QuoteFeedback.quote_job_id == job.job_id).one()
        assert feedback.status == "rejected"
        assert feedback.rejected is True
        assert feedback.rejection_reason == "missing item"
        assert feedback.pushed_to_dingtalk is False
    finally:
        db.close()
