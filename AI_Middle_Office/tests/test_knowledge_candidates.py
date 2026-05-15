import json
import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.knowledge_candidate import KnowledgeCandidate
from app.models.material import Material, MaterialSnapshot
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback, QuoteRagTrace
from app.models.quote_job import QuoteJob
from app.models.user import User


def _create_admin_headers(client):
    username = f"knowledge_admin_{uuid.uuid4().hex[:8]}"
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
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_feedback_materials() -> dict:
    suffix = uuid.uuid4().hex[:8]
    existing_name = f"existing paint {suffix}"
    new_name = f"new craft {suffix}"
    rejected_name = f"missing craft {suffix}"
    material_id = f"mat_{suffix}"
    username = f"knowledge_subject_{suffix}"

    db = SessionLocal()
    try:
        material = Material(
            material_id=material_id,
            item_name=existing_name,
            unit_price=50,
            unit="m2",
            notes="old price",
            is_draft=False,
        )
        db.add(material)

        job = QuoteJob(
            job_id=str(uuid.uuid4()),
            username=username,
            status="succeeded",
            stage="completed",
            message="knowledge candidate request",
            trace_id=f"trace-{suffix}",
            result_json=json.dumps(
                {"project_details": [{"project_name": existing_name, "unit_price": 50, "total_price": 50}]}
            ),
        )
        db.add(job)
        db.flush()

        feedback = QuoteFeedback(
            quote_id=str(uuid.uuid4()),
            quote_job_id=job.job_id,
            username=username,
            trace_id=job.trace_id,
            source="async_job",
            status="confirmed",
            ai_total_amount=50,
            final_total_amount=200,
            amount_delta=150,
            amount_delta_ratio=3,
            ai_item_count=1,
            final_item_count=2,
            was_modified=True,
            pushed_to_dingtalk=True,
            dify_prompt_version=f"prompt-{suffix}",
            rag_collection_alias="test_collection_alias",
            ai_payload_json=json.dumps(
                {"project_details": [{"project_name": existing_name, "unit_price": 50, "total_price": 50}]}
            ),
            final_payload_json=json.dumps(
                {
                    "project_details": [
                        {
                            "project_name": existing_name,
                            "unit_price": 80,
                            "total_price": 80,
                            "unit": "m2",
                            "notes": "reviewed market price",
                        },
                        {
                            "project_name": new_name,
                            "unit_price": 120,
                            "total_price": 120,
                            "unit": "item",
                            "notes": "new accepted craft",
                        },
                    ]
                }
            ),
        )
        db.add(feedback)
        db.flush()
        correction = QuoteCorrection(
            feedback_id=feedback.id,
            quote_id=feedback.quote_id,
            quote_job_id=feedback.quote_job_id,
            trace_id=feedback.trace_id,
            item_index=0,
            project_name=existing_name,
            field_path="project_details[0].unit_price",
            before_value="50",
            after_value="80",
            delta_amount=30,
            reason_category="unit_price_adjustment",
            reason_text="manual market correction",
        )
        db.add(correction)
        trace = QuoteRagTrace(
            feedback_id=feedback.id,
            quote_id=feedback.quote_id,
            quote_job_id=feedback.quote_job_id,
            trace_id=feedback.trace_id,
            material_id=material_id,
            item_name=existing_name,
            rank=1,
            score=0.6,
            collection_alias="test_collection_alias",
            sent_to_prompt=True,
        )
        db.add(trace)

        rejected = QuoteFeedback(
            quote_id=str(uuid.uuid4()),
            quote_job_id=str(uuid.uuid4()),
            username=username,
            trace_id=f"trace-reject-{suffix}",
            source="async_job",
            status="rejected",
            ai_total_amount=30,
            ai_item_count=1,
            rejected=True,
            rejection_reason="missing waterproof craft",
            dify_prompt_version=f"prompt-{suffix}",
            ai_payload_json=json.dumps(
                {"project_details": [{"project_name": rejected_name, "unit_price": 30, "total_price": 30}]}
            ),
        )
        db.add(rejected)
        db.commit()
        return {
            "material_id": material_id,
            "existing_name": existing_name,
            "new_name": new_name,
            "feedback_id": feedback.id,
            "correction_id": correction.id,
            "rejected_id": rejected.id,
        }
    finally:
        db.close()


def test_build_approve_reject_and_rag_insights(client, monkeypatch):
    async def fake_sync(db, username):
        return {"success": True, "message": "ok", "eval_triggered": False, "eval_report_id": None, "error": None}

    monkeypatch.setattr("app.api.v1.knowledge_candidates.sync_materials_to_rag", fake_sync)

    admin_username, headers = _create_admin_headers(client)
    seeded = _seed_feedback_materials()

    build_response = client.post(
        "/api/v1/admin/knowledge_candidates/build",
        headers=headers,
        json={"limit": 50, "include_rejected": True, "overwrite": False},
    )
    assert build_response.status_code == 200
    build_data = build_response.json()["data"]
    assert build_data["created"] >= 3

    db = SessionLocal()
    try:
        price_candidate = (
            db.query(KnowledgeCandidate)
            .filter(KnowledgeCandidate.source_correction_id == seeded["correction_id"])
            .one()
        )
        new_candidate = (
            db.query(KnowledgeCandidate)
            .filter(KnowledgeCandidate.item_name == seeded["new_name"])
            .filter(KnowledgeCandidate.candidate_kind == "new_material")
            .one()
        )
        rejected_candidate = (
            db.query(KnowledgeCandidate)
            .filter(KnowledgeCandidate.source_feedback_id == seeded["rejected_id"])
            .one()
        )
        assert price_candidate.candidate_kind == "price_update"
        assert price_candidate.existing_material_id == seeded["material_id"]
        assert new_candidate.unit_price == 120
        assert rejected_candidate.candidate_kind == "missing_knowledge"
    finally:
        db.close()

    list_response = client.get(
        "/api/v1/admin/knowledge_candidates?status=pending&candidate_kind=price_update",
        headers=headers,
    )
    assert list_response.status_code == 200
    assert any(item["source_correction_id"] == seeded["correction_id"] for item in list_response.json()["data"])

    approve_response = client.post(
        f"/api/v1/admin/knowledge_candidates/{price_candidate.id}/approve",
        headers=headers,
        json={"review_note": "accepted correction", "as_draft": True},
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()["data"]
    assert approved["candidate"]["status"] == "approved"
    assert approved["material"]["id"] == seeded["material_id"]
    assert approved["material"]["unit_price"] == 80
    assert approved["material"]["source"] == "knowledge_candidate"
    assert approved["material"]["status"] == "draft"
    assert approved["material"]["last_verified_at"]
    assert approved["snapshot"]["action"] == "knowledge_candidate_approve"
    assert approved["snapshot"]["added_count"] == 0
    assert approved["snapshot"]["updated_count"] == 1
    assert approved["snapshot"]["diff_summary"]["updated"][0]["id"] == seeded["material_id"]

    reject_response = client.post(
        f"/api/v1/admin/knowledge_candidates/{rejected_candidate.id}/reject",
        headers=headers,
        json={"review_note": "too vague"},
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["data"]["status"] == "rejected"
    assert reject_response.json()["data"]["reviewed_by"] == admin_username

    db = SessionLocal()
    try:
        material = db.query(Material).filter(Material.material_id == seeded["material_id"]).one()
        assert material.unit_price == 80
        assert material.is_draft is True
        assert material.source == "knowledge_candidate"
        assert material.status == "draft"
        assert material.last_verified_at is not None
        assert db.query(MaterialSnapshot).filter(MaterialSnapshot.action == "knowledge_candidate_approve").count() >= 1
    finally:
        db.close()

    insights_response = client.get("/api/v1/admin/knowledge_candidates/rag_insights?min_count=1&limit=100", headers=headers)
    assert insights_response.status_code == 200
    insights = insights_response.json()["data"]
    target = [item for item in insights if item["material_id"] == seeded["material_id"]]
    assert target
    assert target[0]["needs_review"] is True


def test_approve_response_includes_sync_fields(client, monkeypatch):
    _, headers = _create_admin_headers(client)
    seeded = _seed_feedback_materials()
    client.post("/api/v1/admin/knowledge_candidates/build", headers=headers, json={"limit": 50})

    db = SessionLocal()
    try:
        candidate = (
            db.query(KnowledgeCandidate)
            .filter(KnowledgeCandidate.source_correction_id == seeded["correction_id"])
            .one()
        )
    finally:
        db.close()

    async def fake_sync_ok(db, username):
        return {"success": True, "message": "ok", "eval_triggered": False, "eval_report_id": None, "error": None}

    monkeypatch.setattr("app.api.v1.knowledge_candidates.sync_materials_to_rag", fake_sync_ok)

    response = client.post(
        f"/api/v1/admin/knowledge_candidates/{candidate.id}/approve",
        headers=headers,
        json={},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sync_triggered"] is True
    assert data["sync_status"] == "success"
    assert data["sync_error"] is None
    assert data["candidate"]["status"] == "approved"


def test_approve_sync_failure_does_not_rollback_approve(client, monkeypatch):
    _, headers = _create_admin_headers(client)
    seeded = _seed_feedback_materials()
    client.post("/api/v1/admin/knowledge_candidates/build", headers=headers, json={"limit": 50})

    db = SessionLocal()
    try:
        candidate = (
            db.query(KnowledgeCandidate)
            .filter(KnowledgeCandidate.source_correction_id == seeded["correction_id"])
            .one()
        )
        candidate_id = candidate.id
    finally:
        db.close()

    async def fake_sync_fail(db, username):
        return {"success": False, "message": "err", "eval_triggered": False, "eval_report_id": None, "error": "RAG timeout"}

    monkeypatch.setattr("app.api.v1.knowledge_candidates.sync_materials_to_rag", fake_sync_fail)

    response = client.post(
        f"/api/v1/admin/knowledge_candidates/{candidate_id}/approve",
        headers=headers,
        json={},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sync_triggered"] is True
    assert data["sync_status"] == "failed"
    assert data["sync_error"] == "RAG timeout"
    # approve and material write are not rolled back
    assert data["candidate"]["status"] == "approved"
    assert data["material"]["unit_price"] == 80


def test_approving_non_pending_candidate_is_rejected(client):
    _, headers = _create_admin_headers(client)
    seeded = _seed_feedback_materials()

    build_response = client.post(
        "/api/v1/admin/knowledge_candidates/build",
        headers=headers,
        json={"limit": 50, "include_rejected": True},
    )
    assert build_response.status_code == 200

    db = SessionLocal()
    try:
        candidate = (
            db.query(KnowledgeCandidate)
            .filter(KnowledgeCandidate.source_correction_id == seeded["correction_id"])
            .one()
        )
    finally:
        db.close()

    first = client.post(f"/api/v1/admin/knowledge_candidates/{candidate.id}/approve", headers=headers, json={})
    assert first.status_code == 200

    second = client.post(f"/api/v1/admin/knowledge_candidates/{candidate.id}/approve", headers=headers, json={})
    assert second.status_code == 400
    assert "not pending" in second.json()["detail"]
