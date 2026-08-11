import asyncio
import json
import uuid

import pytest

from app.api.v1 import quote as quote_api
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob, QuotePushAttempt, QuoteQuotaReservation
from app.models.quote_requirement_row import QuoteRequirementRow
from app.models.user import User
from app.services import quote_job_runner
from app.services.quote_consistency import (
    QuoteConsistencyError,
    claim_quote_push_for_n8n,
    mark_quote_push_external_delivered,
    mark_quote_push_failed,
    push_idempotency_key,
    start_quote_push_attempt,
)


def _user_headers(client, *, quota: int = 5) -> tuple[str, dict[str, str]]:
    username = f"consistency_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        user.quota = quota
        db.commit()
    finally:
        db.close()
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _push_payload(job_id: str) -> dict:
    return {
        "quote_job_id": job_id,
        "project_details": [
            {
                "project_name": "wall paint",
                "quantity": 10,
                "unit": "sqm",
                "unit_price": 22,
                "total_price": 220,
            }
        ],
    }


def test_push_idempotency_ignores_server_generated_presentation_fields():
    job_id = str(uuid.uuid4())
    first_payload = {
        **_push_payload(job_id),
        "excel_filename": "quote_20260811_114851_user.xlsx",
        "download_filename": "quote_20260811_114851_user.xlsx",
        "filename": "quote_20260811_114851_user.xlsx",
        "fileName": "quote_20260811_114851_user.xlsx",
        "file_name": "quote_20260811_114851_user.xlsx",
        "attachment_name": "quote_20260811_114851_user.xlsx",
        "display_title": "AI报价单-2026-08-11 11:48",
    }
    retry_payload = {
        **_push_payload(job_id),
        "excel_filename": "quote_20260811_114852_user.xlsx",
        "download_filename": "quote_20260811_114852_user.xlsx",
        "filename": "quote_20260811_114852_user.xlsx",
        "fileName": "quote_20260811_114852_user.xlsx",
        "file_name": "quote_20260811_114852_user.xlsx",
        "attachment_name": "quote_20260811_114852_user.xlsx",
        "display_title": "AI报价单-2026-08-11 11:49",
    }

    first_key, first_hash, _ = push_idempotency_key(username="user", payload=first_payload)
    retry_key, retry_hash, _ = push_idempotency_key(username="user", payload=retry_payload)

    assert retry_key == first_key
    assert retry_hash == first_hash


def test_http_200_cannot_finalize_an_attempt_that_n8n_only_claimed(client):
    db = SessionLocal()
    try:
        start = start_quote_push_attempt(
            db,
            username=f"duplicate_gate_{uuid.uuid4().hex[:10]}",
            quote_job_id=None,
            payload={"project_details": [], "phase1_no_delivery_probe": uuid.uuid4().hex},
        )
        key = start.attempt.idempotency_key
        db.commit()

        claimed = claim_quote_push_for_n8n(db, idempotency_key=key)
        assert claimed.attempt.status == "n8n_claimed"
        db.commit()

        with pytest.raises(QuoteConsistencyError, match="^QUOTE_PUSH_NOT_SENDING_n8n_claimed$"):
            mark_quote_push_external_delivered(
                db,
                attempt_id=claimed.attempt.id,
                status_code=200,
                response_text="PHASE1_DUPLICATE_GATE_PROBE",
            )
        db.rollback()

        attempt = db.query(QuotePushAttempt).filter_by(idempotency_key=key).one()
        assert attempt.status == "n8n_claimed"
        assert attempt.external_delivered_at is None
    finally:
        db.close()


def test_quote_creation_reserves_quota_and_cancel_releases_it(client):
    username, headers = _user_headers(client, quota=1)

    first = client.post("/api/v1/quote/jobs", data={"message": "first quote"}, headers=headers)
    assert first.status_code == 202, first.text
    first_job_id = first.json()["data"]["job_id"]

    second = client.post("/api/v1/quote/jobs", data={"message": "second quote"}, headers=headers)
    assert second.status_code == 403
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["quota"] == 1
    assert me.json()["data"]["quota_reserved"] == 1
    assert me.json()["data"]["quota_available"] == 0

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        reservation = db.query(QuoteQuotaReservation).filter_by(quote_job_id=first_job_id).one()
        assert user.quota == 1
        assert user.quota_reserved == 1
        assert reservation.status == "reserved"
    finally:
        db.close()

    canceled = client.post(f"/api/v1/quote/jobs/{first_job_id}/cancel", headers=headers)
    assert canceled.status_code == 200, canceled.text

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        reservation = db.query(QuoteQuotaReservation).filter_by(quote_job_id=first_job_id).one()
        assert user.quota == 1
        assert user.quota_reserved == 0
        assert reservation.status == "released"
        assert reservation.release_reason == "canceled"
    finally:
        db.close()

    third = client.post("/api/v1/quote/jobs", data={"message": "third quote"}, headers=headers)
    assert third.status_code == 202, third.text


def test_duplicate_worker_delivery_claims_once_and_consumes_quota_once(monkeypatch):
    username = f"runner_consistency_{uuid.uuid4().hex[:10]}"
    job_id = str(uuid.uuid4())
    event_calls = []

    async def fake_load_file_content(job, db):
        return None

    async def fake_quote_events(**kwargs):
        event_calls.append(kwargs["quote_job_id"])
        yield (
            "preview",
            "ready",
            {"stage": "completed", "data": {"project_details": [{"project_name": "paint", "total_price": 100}]}},
        )

    monkeypatch.setattr(quote_job_runner, "_load_job_file_content", fake_load_file_content)
    monkeypatch.setattr(quote_job_runner, "_iter_quote_events", fake_quote_events)
    monkeypatch.setattr(quote_job_runner, "safe_record_ai_preview", lambda *args, **kwargs: None)

    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash("secret123"), role="user", quota=1))
        db.add(QuoteJob(job_id=job_id, username=username, status="queued", stage="queued", message="paint"))
        db.commit()
    finally:
        db.close()

    asyncio.run(quote_job_runner.run_quote_job_async(job_id))
    asyncio.run(quote_job_runner.run_quote_job_async(job_id))

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).one()
        reservation = db.query(QuoteQuotaReservation).filter_by(quote_job_id=job_id).one()
        assert event_calls == [job_id]
        assert job.status == "succeeded"
        assert job.attempt_id
        assert user.quota == 0
        assert user.quota_reserved == 0
        assert reservation.status == "consumed"
    finally:
        db.close()


def test_failed_worker_releases_reserved_quota(monkeypatch):
    username = f"runner_failure_{uuid.uuid4().hex[:10]}"
    job_id = str(uuid.uuid4())

    async def fake_load_file_content(job, db):
        return None

    async def fake_quote_events(**kwargs):
        yield ("error", "n8n failed", {"stage": "n8n"})

    monkeypatch.setattr(quote_job_runner, "_load_job_file_content", fake_load_file_content)
    monkeypatch.setattr(quote_job_runner, "_iter_quote_events", fake_quote_events)

    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash("secret123"), role="user", quota=1))
        db.add(QuoteJob(job_id=job_id, username=username, status="queued", stage="queued", message="paint"))
        db.commit()
    finally:
        db.close()

    asyncio.run(quote_job_runner.run_quote_job_async(job_id))

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).one()
        reservation = db.query(QuoteQuotaReservation).filter_by(quote_job_id=job_id).one()
        assert job.status == "failed"
        assert user.quota == 1
        assert user.quota_reserved == 0
        assert reservation.status == "released"
        assert reservation.release_reason == "n8n"
    finally:
        db.close()


def test_retry_copies_requirement_snapshot_and_links_source_job(client):
    username, headers = _user_headers(client, quota=2)
    source_job_id = str(uuid.uuid4())
    raw_row = {
        "requirement_row_key": "sheet-a:12",
        "source_sheet": "Sheet A",
        "raw_row_index": 12,
        "item_name": "wall paint",
        "spec": "two coats",
        "quantity": 18.5,
        "unit": "sqm",
        "remark": "keep trace",
        "raw_cells": ["wall paint", 18.5, "sqm"],
    }
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=source_job_id,
                username=username,
                status="failed",
                stage="n8n",
                message="retry source",
            )
        )
        db.add(
            QuoteRequirementRow(
                quote_job_id=source_job_id,
                requirement_row_key=raw_row["requirement_row_key"],
                source_sheet=raw_row["source_sheet"],
                raw_row_index=raw_row["raw_row_index"],
                item_name=raw_row["item_name"],
                spec=raw_row["spec"],
                quantity=raw_row["quantity"],
                unit=raw_row["unit"],
                remark=raw_row["remark"],
                raw_text="wall paint 18.5 sqm",
                raw_cells_json=json.dumps(raw_row["raw_cells"], ensure_ascii=False),
                row_json=json.dumps(raw_row, ensure_ascii=False, sort_keys=True),
                sort_order=1,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/quote/jobs/{source_job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    retry_job_id = response.json()["data"]["job_id"]

    db = SessionLocal()
    try:
        retry_job = db.query(QuoteJob).filter(QuoteJob.job_id == retry_job_id).one()
        source_row = db.query(QuoteRequirementRow).filter_by(quote_job_id=source_job_id).one()
        retry_row = db.query(QuoteRequirementRow).filter_by(quote_job_id=retry_job_id).one()
        reservation = db.query(QuoteQuotaReservation).filter_by(quote_job_id=retry_job_id).one()
        assert retry_job.source_job_id == source_job_id
        assert retry_row.requirement_row_key == source_row.requirement_row_key
        assert retry_row.source_sheet == source_row.source_sheet
        assert retry_row.raw_row_index == source_row.raw_row_index
        assert retry_row.row_json == source_row.row_json
        assert retry_row.raw_cells_json == source_row.raw_cells_json
        assert reservation.status == "reserved"
    finally:
        db.close()


def test_confirm_push_is_idempotent_and_writes_one_local_unit(client, monkeypatch):
    username, headers = _user_headers(client)
    job_id = str(uuid.uuid4())
    gateway_calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

    async def fake_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(quote_api, "post_json_via_gateway", fake_gateway)
    monkeypatch.setattr(quote_api, "build_excel_base64", lambda details: "eA==")
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="succeeded",
                stage="completed",
                message="wall paint",
                result_json=json.dumps({"project_details": []}),
            )
        )
        db.commit()
    finally:
        db.close()

    payload = _push_payload(job_id)
    first = client.post("/api/v1/confirm_push", headers=headers, json=payload)
    second = client.post("/api/v1/confirm_push", headers=headers, json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["idempotent"] is False
    assert second.json()["data"]["idempotent"] is True
    assert len(gateway_calls) == 1
    assert gateway_calls[0]["json_payload"]["idempotency_key"]

    db = SessionLocal()
    try:
        assert db.query(QuotePushAttempt).filter_by(quote_job_id=job_id).count() == 1
        assert db.query(QuotePushAttempt).filter_by(quote_job_id=job_id).one().status == "delivered"
        assert db.query(QuoteHistory).filter_by(quote_job_id=job_id).count() == 1
    finally:
        db.close()


def test_external_delivery_retries_only_local_finalize(client, monkeypatch):
    username, headers = _user_headers(client)
    job_id = str(uuid.uuid4())
    gateway_calls = []

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

    async def fake_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return FakeResponse()

    original_history_writer = quote_api.create_quote_history_record
    failures = {"remaining": 1}

    def fail_once(*args, **kwargs):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("injected local write failure")
        return original_history_writer(*args, **kwargs)

    monkeypatch.setattr(quote_api, "post_json_via_gateway", fake_gateway)
    monkeypatch.setattr(quote_api, "build_excel_base64", lambda details: "eA==")
    monkeypatch.setattr(quote_api, "create_quote_history_record", fail_once)
    db = SessionLocal()
    try:
        db.add(QuoteJob(job_id=job_id, username=username, status="succeeded", stage="completed", message="paint"))
        db.commit()
    finally:
        db.close()

    payload = _push_payload(job_id)
    first = client.post("/api/v1/confirm_push", headers=headers, json=payload)
    assert first.status_code == 500
    db = SessionLocal()
    try:
        attempt = db.query(QuotePushAttempt).filter_by(quote_job_id=job_id).one()
        assert attempt.status == "external_delivered"
        assert db.query(QuoteHistory).filter_by(quote_job_id=job_id).count() == 0
    finally:
        db.close()

    second = client.post("/api/v1/confirm_push", headers=headers, json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["idempotent"] is True
    assert len(gateway_calls) == 1

    db = SessionLocal()
    try:
        attempt = db.query(QuotePushAttempt).filter_by(quote_job_id=job_id).one()
        assert attempt.status == "delivered"
        assert db.query(QuoteHistory).filter_by(quote_job_id=job_id).count() == 1
    finally:
        db.close()


def test_n8n_callback_state_machine_and_duplicate_delivery(client):
    username = f"n8n_callback_{uuid.uuid4().hex[:10]}"
    job_id = str(uuid.uuid4())
    payload = _push_payload(job_id)
    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash("secret123"), role="user", quota=1))
        db.add(QuoteJob(job_id=job_id, username=username, status="succeeded", stage="completed", message="paint"))
        db.flush()
        from app.services.quote_consistency import start_quote_push_attempt

        start = start_quote_push_attempt(db, username=username, quote_job_id=job_id, payload=payload)
        key = start.attempt.idempotency_key
        db.commit()
    finally:
        db.close()

    callback = {"idempotency_key": key, "quote_job_id": job_id, "execution_id": "exec-1"}
    headers = {"X-Webhook-Secret": "test-webhook-secret"}

    assert client.post("/api/v1/internal/n8n/quote-push/claim", json=callback).status_code == 401
    claimed = client.post("/api/v1/internal/n8n/quote-push/claim", json=callback, headers=headers)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json() == {"action": "claimed", "attempt_status": "n8n_claimed"}

    duplicate_in_progress = client.post(
        "/api/v1/internal/n8n/quote-push/claim", json=callback, headers=headers
    )
    assert duplicate_in_progress.status_code == 200
    assert duplicate_in_progress.json()["action"] == "in_progress"

    dispatching = client.post(
        "/api/v1/internal/n8n/quote-push/dispatch-start", json=callback, headers=headers
    )
    assert dispatching.status_code == 200, dispatching.text
    assert dispatching.json()["attempt_status"] == "n8n_dispatching"

    delivered = client.post("/api/v1/internal/n8n/quote-push/delivered", json=callback, headers=headers)
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["attempt_status"] == "external_delivered"

    duplicate_delivered = client.post(
        "/api/v1/internal/n8n/quote-push/claim", json=callback, headers=headers
    )
    assert duplicate_delivered.status_code == 200
    assert duplicate_delivered.json()["action"] == "delivered"


def test_gateway_timeout_preserves_n8n_dispatching_for_manual_reconciliation():
    username = f"n8n_unknown_{uuid.uuid4().hex[:10]}"
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash("secret123"), role="user", quota=1))
        db.add(QuoteJob(job_id=job_id, username=username, status="succeeded", stage="completed", message="paint"))
        db.flush()
        from app.services.quote_consistency import (
            claim_quote_push_for_n8n,
            mark_quote_push_n8n_dispatching,
            start_quote_push_attempt,
        )

        start = start_quote_push_attempt(db, username=username, quote_job_id=job_id, payload=_push_payload(job_id))
        key = start.attempt.idempotency_key
        attempt_id = start.attempt.id
        claim_quote_push_for_n8n(db, idempotency_key=key, quote_job_id=job_id)
        mark_quote_push_n8n_dispatching(db, idempotency_key=key, quote_job_id=job_id)
        mark_quote_push_failed(db, attempt_id=attempt_id, error_message="gateway timeout")
        db.commit()

        attempt = db.query(QuotePushAttempt).filter_by(id=attempt_id).one()
        assert attempt.status == "n8n_dispatching"
        assert attempt.error_message is None
    finally:
        db.close()
