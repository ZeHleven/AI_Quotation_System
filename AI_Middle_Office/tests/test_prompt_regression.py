import json
import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.prompt_regression import PromptRegressionCase, PromptRegressionRun
from app.models.quote_feedback import QuoteCorrection, QuoteFeedback
from app.models.quote_job import QuoteJob
from app.models.user import User


def _create_admin_headers(client):
    username = f"prompt_admin_{uuid.uuid4().hex[:8]}"
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


def _create_feedback_pair() -> tuple[int, int, str, str]:
    username = f"prompt_subject_{uuid.uuid4().hex[:8]}"
    prompt_a = f"prompt-a-{uuid.uuid4().hex[:8]}"
    prompt_b = f"prompt-b-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        job = QuoteJob(
            job_id=str(uuid.uuid4()),
            username=username,
            status="succeeded",
            stage="completed",
            message="paint 10 sqm",
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            result_json=json.dumps({"project_details": [{"project_name": "paint", "total_price": 100}]}),
        )
        db.add(job)
        db.flush()

        confirmed = QuoteFeedback(
            quote_id=str(uuid.uuid4()),
            quote_job_id=job.job_id,
            username=username,
            trace_id=job.trace_id,
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
            dify_prompt_version=prompt_a,
            dify_workflow_version="workflow-a",
            dify_release_id="release-a",
            ai_payload_json=json.dumps({"project_details": [{"project_name": "paint", "total_price": 100}]}),
            final_payload_json=json.dumps({"project_details": [{"project_name": "paint", "total_price": 130}]}),
        )
        db.add(confirmed)
        db.flush()
        db.add(
            QuoteCorrection(
                feedback_id=confirmed.id,
                quote_id=confirmed.quote_id,
                quote_job_id=confirmed.quote_job_id,
                trace_id=confirmed.trace_id,
                item_index=0,
                project_name="paint",
                field_path="project_details[0].total_price",
                before_value="100",
                after_value="130",
                delta_amount=30,
                reason_category="unit_price_adjustment",
            )
        )

        rejected = QuoteFeedback(
            quote_id=str(uuid.uuid4()),
            quote_job_id=str(uuid.uuid4()),
            username=username,
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            source="async_job",
            status="rejected",
            ai_total_amount=80,
            ai_item_count=1,
            rejected=True,
            rejection_reason="missing demolition",
            dify_prompt_version=prompt_b,
            ai_payload_json=json.dumps({"project_details": [{"project_name": "tile", "total_price": 80}]}),
        )
        db.add(rejected)
        db.commit()
        return confirmed.id, rejected.id, prompt_a, prompt_b
    finally:
        db.close()


def test_prompt_regression_build_cases_and_run_report(client):
    _, headers = _create_admin_headers(client)
    confirmed_id, rejected_id, prompt_a, _ = _create_feedback_pair()

    build_response = client.post(
        "/api/v1/admin/prompt_regression/cases/build",
        headers=headers,
        json={"limit": 20, "include_rejected": True},
    )
    assert build_response.status_code == 200
    build_data = build_response.json()["data"]
    assert build_data["created"] >= 2

    db = SessionLocal()
    try:
        confirmed_case = (
            db.query(PromptRegressionCase)
            .filter(PromptRegressionCase.source_feedback_id == confirmed_id)
            .one()
        )
        rejected_case = (
            db.query(PromptRegressionCase)
            .filter(PromptRegressionCase.source_feedback_id == rejected_id)
            .one()
        )
        assert confirmed_case.request_text == "paint 10 sqm"
        assert confirmed_case.source_prompt_version == prompt_a
        assert confirmed_case.correction_count == 1
        assert rejected_case.rejected is True
    finally:
        db.close()

    list_response = client.get(
        f"/api/v1/admin/prompt_regression/cases?prompt_version={prompt_a}",
        headers=headers,
    )
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["total"] == 1
    assert list_data["data"][0]["source_feedback_id"] == confirmed_id

    run_response = client.post(
        "/api/v1/admin/prompt_regression/runs",
        headers=headers,
        json={"prompt_version": prompt_a, "amount_tolerance": 1},
    )
    assert run_response.status_code == 200
    run_data = run_response.json()["data"]
    assert run_data["case_count"] == 1
    assert run_data["confirmed_count"] == 1
    assert run_data["avg_abs_amount_delta"] == 30
    assert run_data["exact_total_match_rate"] == 0
    assert run_data["metrics"]["by_prompt_version"][0]["prompt_version"] == prompt_a

    latest_response = client.get("/api/v1/admin/prompt_regression/runs/latest", headers=headers)
    assert latest_response.status_code == 200
    assert latest_response.json()["data"]["id"] == run_data["id"]

    db = SessionLocal()
    try:
        assert db.query(PromptRegressionRun).filter(PromptRegressionRun.id == run_data["id"]).count() == 1
    finally:
        db.close()


def test_prompt_regression_run_requires_matching_cases(client):
    _, headers = _create_admin_headers(client)

    response = client.post(
        "/api/v1/admin/prompt_regression/runs",
        headers=headers,
        json={"prompt_version": f"missing-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 400
    assert "no prompt regression cases" in response.json()["detail"]
