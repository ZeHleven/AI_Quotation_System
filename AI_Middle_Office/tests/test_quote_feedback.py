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


def _create_admin_headers(client):
    username = f"feedback_admin_{uuid.uuid4().hex[:8]}"
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


def _create_feedback_record(username: str) -> int:
    quote_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        feedback = QuoteFeedback(
            quote_id=quote_id,
            quote_job_id=str(uuid.uuid4()),
            username=username,
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            source="async_job",
            status="confirmed",
            ai_total_amount=100,
            final_total_amount=130,
            amount_delta=30,
            amount_delta_ratio=0.3,
            ai_item_count=1,
            final_item_count=1,
            was_modified=True,
            pushed_to_dingtalk=True,
            dify_prompt_version="prompt-admin-test",
            rag_collection_alias="test_collection_alias",
            ai_payload_json=json.dumps({"project_details": [{"project_name": "paint", "total_price": 100}]}),
            final_payload_json=json.dumps({"project_details": [{"project_name": "paint", "total_price": 130}]}),
        )
        db.add(feedback)
        db.flush()
        db.add(
            QuoteCorrection(
                feedback_id=feedback.id,
                quote_id=feedback.quote_id,
                quote_job_id=feedback.quote_job_id,
                trace_id=feedback.trace_id,
                item_index=0,
                project_name="paint",
                field_path="project_details[0].total_price",
                before_value="100",
                after_value="130",
                delta_amount=30,
                reason_category="unit_price_adjustment",
            )
        )
        db.add(
            QuoteRagTrace(
                feedback_id=feedback.id,
                quote_id=feedback.quote_id,
                quote_job_id=feedback.quote_job_id,
                trace_id=feedback.trace_id,
                material_id="mat-admin-001",
                item_name="paint base",
                rank=1,
                score=0.95,
                collection_alias="test_collection_alias",
                sent_to_prompt=True,
            )
        )
        db.commit()
        return feedback.id
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


def test_admin_quote_feedback_summary_list_and_detail(client):
    admin_headers = _create_admin_headers(client)
    username = f"feedback_subject_{uuid.uuid4().hex[:8]}"
    feedback_id = _create_feedback_record(username)

    summary_response = client.get(
        f"/api/v1/admin/quote_feedback/summary?days=7&username={username}",
        headers=admin_headers,
    )
    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["total_count"] == 1
    assert summary["confirmed_count"] == 1
    assert summary["modified_count"] == 1
    assert summary["correction_count"] == 1
    assert summary["rag_trace_count"] == 1
    assert summary["top_correction_fields"][0]["field_path"] == "project_details[0].total_price"
    assert summary["top_rag_materials"][0]["material_id"] == "mat-admin-001"

    list_response = client.get(
        f"/api/v1/admin/quote_feedback?username={username}",
        headers=admin_headers,
    )
    assert list_response.status_code == 200
    items = list_response.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == feedback_id
    assert items[0]["correction_count"] == 1
    assert items[0]["rag_trace_count"] == 1

    detail_response = client.get(f"/api/v1/admin/quote_feedback/{feedback_id}", headers=admin_headers)
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["corrections"][0]["delta_amount"] == 30
    assert detail["rag_traces"][0]["material_id"] == "mat-admin-001"
    assert detail["ai_payload"]["project_details"][0]["total_price"] == 100
