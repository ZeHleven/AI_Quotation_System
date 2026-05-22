import json
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_cost_evidence import QuoteCostEvidence
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
            request_text="paint quote request",
            source_file_name="paint.png",
            project_summary="paint; total_items=1",
            change_summary="1 field changes; top fields: 小计; amount delta: 30",
            top_changed_fields="小计",
            reviewed_by=username,
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
                field_label="小计",
                change_type="updated",
                before_value="100",
                after_value="130",
                before_display="100",
                after_display="130",
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
                item_index=0,
                project_name="paint",
                material_id="mat-admin-001",
                item_name="paint base",
                rank=1,
                score=0.95,
                collection_alias="test_collection_alias",
                sent_to_prompt=True,
                used_in_final_quote=True,
                adopted_by_user=True,
                match_reason="appears in final quote",
            )
        )
        db.add(
            QuoteCostEvidence(
                feedback_id=feedback.id,
                quote_id=feedback.quote_id,
                quote_job_id=feedback.quote_job_id,
                trace_id=feedback.trace_id,
                username=username,
                source="async_job",
                status="confirmed",
                item_index=0,
                project_name="paint",
                quantity=10,
                unit="m2",
                ai_unit_price=10,
                ai_total_price=100,
                final_unit_price=13,
                final_total_price=130,
                line_total_price=130,
                line_total_source="manual_final",
                quote_total_price=130,
                quote_total_source="manual_final",
                quote_reference_total_price=110,
                manual_modified=True,
                adopted_cost_reference=False,
                cost_item_id=9001,
                cost_item_name_snapshot="paint base",
                reference_price=11,
                reference_price_source_label="主参考价",
                match_type="name",
                match_type_label="名称匹配",
                match_reason="matched by name",
                price_delta=-1,
                price_delta_rate=-0.0909,
                ai_basis="AI returned the preview price.",
                cost_context_basis="Cost item #9001 was sent before quoting.",
                comparison="AI price differs from cost reference.",
                cost_item_url="/admin/cost-db?cost_item_id=9001",
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
            {
                "project_name": "wall paint",
                "quantity": 10,
                "unit": "m2",
                "unit_price": 20,
                "total_price": 200,
                "notes": "standard",
                "cost_reference": {
                    "matched": True,
                    "cost_item_id": 9101,
                    "item_name": "wall paint base",
                    "unit": "m2",
                    "reference_price": 18,
                    "reference_price_source": "price",
                    "reference_price_source_label": "主参考价",
                    "match_type": "name",
                    "match_type_label": "名称匹配",
                    "match_reason": "报价行与成本库 active 条目名称匹配。",
                    "price_delta": 2,
                    "price_delta_rate": 0.1111,
                    "cost_item_url": "/admin/cost-db?cost_item_id=9101",
                    "source_cost_item": {
                        "id": 9101,
                        "item_name": "wall paint base",
                        "category": "paint",
                        "subcategory": "wall",
                        "unit": "m2",
                        "status": "active",
                    },
                },
                "quote_explanation": {
                    "ai_basis": "AI returned the preview price.",
                    "cost_context_basis": "Cost item #9101 was sent before quoting.",
                    "comparison": "AI price differs from cost reference.",
                    "cost_item_url": "/admin/cost-db?cost_item_id=9101",
                },
            }
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
    final_detail = dict(ai_result["project_details"][0])
    final_detail.update({"unit_price": 22, "total_price": 220, "notes": "upgraded"})

    response = client.post(
        "/api/v1/confirm_push",
        headers=headers,
        json={
            "quote_job_id": job.job_id,
            "trace_id": job.trace_id,
            "project_details": [final_detail],
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
        assert feedback.request_text == "test quote request"
        assert feedback.project_summary == "wall paint; total_items=1"
        assert feedback.change_summary
        assert "单价" in feedback.top_changed_fields
        assert feedback.reviewed_by == username
        assert feedback.dify_prompt_version == settings.dify_prompt_version
        assert feedback.rag_collection_alias == settings.rag_collection_alias

        corrections = db.query(QuoteCorrection).filter(QuoteCorrection.feedback_id == feedback.id).all()
        assert {item.field_path for item in corrections} >= {
            "project_details[0].unit_price",
            "project_details[0].total_price",
            "project_details[0].notes",
        }
        unit_price_correction = next(item for item in corrections if item.field_path.endswith(".unit_price"))
        assert unit_price_correction.field_label == "单价"
        assert unit_price_correction.change_type == "updated"
        assert unit_price_correction.before_display == "20"
        assert unit_price_correction.after_display == "22"

        trace = db.query(QuoteRagTrace).filter(QuoteRagTrace.feedback_id == feedback.id).one()
        assert trace.material_id == "mat-001"
        assert trace.item_index == 0
        assert trace.project_name == "wall paint"
        assert trace.rank == 1
        assert trace.sent_to_prompt is True
        assert trace.used_in_final_quote is True
        assert trace.adopted_by_user is True

        evidence = db.query(QuoteCostEvidence).filter(QuoteCostEvidence.feedback_id == feedback.id).one()
        assert evidence.status == "confirmed"
        assert evidence.quote_job_id == job.job_id
        assert evidence.project_name == "wall paint"
        assert evidence.quantity == 10
        assert evidence.ai_unit_price == 20
        assert evidence.final_unit_price == 22
        assert evidence.line_total_price == 220
        assert evidence.line_total_source == "manual_final"
        assert evidence.quote_total_price == 220
        assert evidence.quote_total_source == "manual_final"
        assert evidence.quote_reference_total_price == 180
        assert evidence.cost_item_id == 9101
        assert evidence.cost_item_name_snapshot == "wall paint base"
        assert evidence.reference_price == 18
        assert evidence.reference_total == 180
        assert evidence.price_delta == 2
        assert evidence.manual_modified is True
        assert evidence.adopted_cost_reference is False
        assert evidence.ai_basis == "AI returned the preview price."
        assert evidence.cost_item_url == "/admin/cost-db?cost_item_id=9101"
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
        assert feedback.reviewed_by == username
        assert feedback.change_summary == "Rejected: missing item"
        assert feedback.request_text == "test quote request"
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
    assert summary["cost_evidence_count"] == 1
    assert summary["top_correction_fields"][0]["field_path"] == "project_details[0].total_price"
    assert summary["top_rag_materials"][0]["material_id"] == "mat-admin-001"
    assert summary["top_cost_items"][0]["cost_item_id"] == 9001

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
    assert items[0]["cost_evidence_count"] == 1
    assert items[0]["request_text"] == "paint quote request"
    assert items[0]["project_summary"] == "paint; total_items=1"
    assert items[0]["change_summary"].startswith("1 field changes")
    assert items[0]["top_changed_fields"] == ["小计"]

    detail_response = client.get(f"/api/v1/admin/quote_feedback/{feedback_id}", headers=admin_headers)
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["corrections"][0]["delta_amount"] == 30
    assert detail["corrections"][0]["field_label"] == "小计"
    assert detail["corrections"][0]["change_type"] == "updated"
    assert detail["corrections"][0]["before_display"] == "100"
    assert detail["corrections"][0]["after_display"] == "130"
    assert detail["rag_traces"][0]["material_id"] == "mat-admin-001"
    assert detail["rag_traces"][0]["project_name"] == "paint"
    assert detail["rag_traces"][0]["used_in_final_quote"] is True
    assert detail["rag_traces"][0]["match_reason"] == "appears in final quote"
    assert detail["cost_evidence"][0]["cost_item_id"] == 9001
    assert detail["cost_evidence"][0]["cost_item_url"] == "/admin/cost-db?cost_item_id=9001"
    assert detail["cost_evidence"][0]["manual_modified"] is True
    assert detail["cost_evidence"][0]["line_total_price"] == 130
    assert detail["cost_evidence"][0]["line_total_source"] == "manual_final"
    assert detail["cost_evidence"][0]["line_total_source_label"] == "人工确认价"
    assert detail["cost_evidence"][0]["quote_total_price"] == 130
    assert detail["cost_evidence"][0]["quote_total_source"] == "manual_final"
    assert detail["cost_evidence"][0]["quote_total_source_label"] == "人工确认价"
    assert detail["cost_evidence"][0]["quote_reference_total_price"] == 110
    assert detail["ai_payload"]["project_details"][0]["total_price"] == 100

    evidence_response = client.get(
        "/api/v1/admin/quote-cost-evidence?cost_item_id=9001",
        headers=admin_headers,
    )
    assert evidence_response.status_code == 200
    evidence_items = evidence_response.json()["data"]
    assert len(evidence_items) == 1
    assert evidence_items[0]["feedback_id"] == feedback_id
    assert evidence_items[0]["match_reason"] == "matched by name"
    assert evidence_items[0]["line_total_source_label"] == "人工确认价"
    assert evidence_items[0]["quote_reference_total_price"] == 110
