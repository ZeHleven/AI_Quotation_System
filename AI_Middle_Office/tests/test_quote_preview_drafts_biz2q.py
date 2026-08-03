import json
import uuid

from app.core.database import SessionLocal
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.quote_preview_draft import PREVIEW_DRAFT_STATUS_DISCARDED, PREVIEW_DRAFT_STATUS_EDITING, PREVIEW_DRAFT_STATUS_PUSHED, QuotePreviewDraft


PASSWORD = "secret123"


def _login_headers(client):
    username = f"biz2q_user_{uuid.uuid4().hex[:8]}"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_quote_job(username: str, *, status: str = "succeeded") -> str:
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status=status,
                stage="completed",
                trace_id=f"trace-{uuid.uuid4().hex[:8]}",
                request_summary="BIZ-2q preview draft test",
                result_json=json.dumps(
                    {
                        "project_details": [
                            {
                                "project_name": "temporary protection",
                                "quantity": 10,
                                "unit": "m2",
                                "unit_price": 0,
                                "total_price": 0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                result_item_count=1,
            )
        )
        db.commit()
    finally:
        db.close()
    return job_id


class _FakePushSuccess:
    status_code = 200

    def json(self):
        return {"ok": True}


async def _fake_push_success(**kwargs):
    return _FakePushSuccess()


def test_quote_preview_draft_save_get_update_and_discard(client):
    username, headers = _login_headers(client)
    job_id = _seed_quote_job(username)

    draft = {
        "project_details": [
            {
                "project_name": "temporary protection",
                "quantity": 10,
                "unit": "m2",
                "manual_unit_price": 12.5,
                "total_price": 125,
            },
            {
                "project_name": "custom access panel",
                "quantity": 3,
                "unit": "item",
                "manual_unit_price": 0,
                "total_price": 0,
            },
        ],
        "customer_questions_answered": "draft note",
    }

    response = client.put(
        f"/api/v1/quote/jobs/{job_id}/preview-draft",
        headers=headers,
        json={"draft": draft, "quote_id": "Q-BIZ2Q"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["exists"] is True
    assert data["status"] == "editing"
    assert data["row_count"] == 2
    assert data["priced_row_count"] == 1
    assert data["unpriced_row_count"] == 1
    assert data["version"] == 1

    response = client.get(f"/api/v1/quote/jobs/{job_id}/preview-draft", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["draft"]["customer_questions_answered"] == "draft note"
    assert data["draft"]["project_details"][0]["manual_unit_price"] == 12.5

    response = client.get("/api/v1/history", headers=headers)
    assert response.status_code == 200, response.text
    history_rows = response.json()["data"]
    draft_row = next(item for item in history_rows if item.get("quote_job_id") == job_id)
    assert draft_row["record_type"] == "preview_draft"
    assert draft_row["push_status"] == "draft"
    assert draft_row["can_edit_preview_draft"] is True
    assert draft_row["pushed_to_dingtalk"] is False
    assert draft_row["item_count"] == 2
    assert draft_row["total_amount"] == 125

    db = SessionLocal()
    try:
        db.add(
            QuoteHistory(
                username=username,
                quote_job_id=str(uuid.uuid4()),
                display_title="already pushed quote",
                pushed_to_dingtalk=True,
                total_amount=999,
                item_count=1,
                payload_json='{"project_details":[]}',
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/history", headers=headers)
    assert response.status_code == 200, response.text
    response = client.get("/api/v1/history?push_status=draft", headers=headers)
    assert response.status_code == 200, response.text
    draft_rows = response.json()["data"]
    assert draft_rows
    assert all(item["push_status"] == "draft" for item in draft_rows)
    assert any(item.get("quote_job_id") == job_id for item in draft_rows)

    response = client.get("/api/v1/history?push_status=pushed", headers=headers)
    assert response.status_code == 200, response.text
    pushed_rows = response.json()["data"]
    assert pushed_rows
    assert all(item["push_status"] == "pushed" for item in pushed_rows)

    response = client.get("/api/v1/history?keyword=custom%20access", headers=headers)
    assert response.status_code == 200, response.text
    assert any(item.get("quote_job_id") == job_id for item in response.json()["data"])

    response = client.get("/api/v1/history?min_item_count=2&max_item_count=2", headers=headers)
    assert response.status_code == 200, response.text
    assert any(item.get("quote_job_id") == job_id for item in response.json()["data"])

    response = client.get("/api/v1/history?min_total_amount=124&max_total_amount=126", headers=headers)
    assert response.status_code == 200, response.text
    assert any(item.get("quote_job_id") == job_id for item in response.json()["data"])

    draft["project_details"][1]["manual_unit_price"] = 20
    draft["project_details"][1]["total_price"] = 60
    response = client.put(
        f"/api/v1/quote/jobs/{job_id}/preview-draft",
        headers=headers,
        json={"draft": draft},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["version"] == 2
    assert data["priced_row_count"] == 2
    assert data["unpriced_row_count"] == 0

    response = client.post(f"/api/v1/quote/jobs/{job_id}/preview-draft/discard", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "discarded"

    response = client.get("/api/v1/history", headers=headers)
    assert response.status_code == 200, response.text
    history_rows = response.json()["data"]
    assert not any(item.get("record_type") == "preview_draft" and item.get("quote_job_id") == job_id for item in history_rows)


def test_batch_delete_preview_drafts_only_removes_editing_drafts(client):
    username, headers = _login_headers(client)
    job_ids = [_seed_quote_job(username), _seed_quote_job(username), _seed_quote_job(username)]
    draft_ids = []

    for index, job_id in enumerate(job_ids):
        response = client.put(
            f"/api/v1/quote/jobs/{job_id}/preview-draft",
            headers=headers,
            json={
                "draft": {
                    "project_details": [
                        {
                            "project_name": f"draft row {index}",
                            "quantity": 1,
                            "unit": "item",
                            "manual_unit_price": 10 + index,
                            "total_price": 10 + index,
                        }
                    ]
                }
            },
        )
        assert response.status_code == 200, response.text
        draft_ids.append(response.json()["data"]["id"])

    db = SessionLocal()
    try:
        pushed = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.id == draft_ids[2]).one()
        pushed.status = PREVIEW_DRAFT_STATUS_PUSHED
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/v1/quote/preview-drafts/batch-delete",
        headers=headers,
        json={"draft_ids": draft_ids},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["deleted_count"] == 2
    assert set(data["deleted_ids"]) == set(draft_ids[:2])
    assert data["skipped_count"] == 1
    assert data["skipped"][0]["draft_id"] == draft_ids[2]
    assert data["skipped"][0]["reason"] == "not_editing"

    db = SessionLocal()
    try:
        remaining_ids = {
            draft.id
            for draft in db.query(QuotePreviewDraft).filter(QuotePreviewDraft.id.in_(draft_ids)).all()
        }
        remaining_jobs = db.query(QuoteJob).filter(QuoteJob.job_id.in_(job_ids)).count()
        assert remaining_ids == {draft_ids[2]}
        assert remaining_jobs == 3
    finally:
        db.close()

    response = client.get("/api/v1/history?push_status=draft", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert not any(item.get("draft_id") in draft_ids[:2] for item in rows)


def test_rejected_quote_is_visible_in_history_with_reason(client):
    username, headers = _login_headers(client)
    job_id = _seed_quote_job(username)
    reason = "缺少 12mm 石膏板吊顶报价，请打回重填"

    response = client.put(
        f"/api/v1/quote/jobs/{job_id}/preview-draft",
        headers=headers,
        json={
            "draft": {
                "project_details": [
                    {
                        "project_name": "12mm gypsum ceiling",
                        "quantity": 8,
                        "unit": "m2",
                        "manual_unit_price": 0,
                        "total_price": 0,
                    }
                ]
            }
        },
    )
    assert response.status_code == 200, response.text

    response = client.post(
        "/api/v1/quote/feedback/reject",
        headers=headers,
        json={"quote_job_id": job_id, "reason": reason},
    )
    assert response.status_code == 200, response.text

    response = client.post(f"/api/v1/quote/jobs/{job_id}/preview-draft/discard", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == PREVIEW_DRAFT_STATUS_DISCARDED

    response = client.get("/api/v1/history", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    rejected_row = next(item for item in rows if item.get("quote_job_id") == job_id)
    assert rejected_row["record_type"] == "rejected_quote"
    assert rejected_row["push_status"] == "rejected"
    assert rejected_row["rejection_reason"] == reason
    assert rejected_row["can_edit_preview_draft"] is False

    response = client.get("/api/v1/history?push_status=rejected", headers=headers)
    assert response.status_code == 200, response.text
    rejected_rows = response.json()["data"]
    assert any(item.get("quote_job_id") == job_id for item in rejected_rows)
    assert all(item["push_status"] == "rejected" for item in rejected_rows)

    response = client.get("/api/v1/history?keyword=12mm", headers=headers)
    assert response.status_code == 200, response.text
    assert any(item.get("quote_job_id") == job_id for item in response.json()["data"])


def test_confirm_push_marks_preview_draft_pushed(client, monkeypatch):
    username, headers = _login_headers(client)
    job_id = _seed_quote_job(username)
    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", _fake_push_success)

    response = client.put(
        f"/api/v1/quote/jobs/{job_id}/preview-draft",
        headers=headers,
        json={
            "draft": {
                "project_details": [
                    {
                        "project_name": "temporary protection",
                        "quantity": 10,
                        "unit": "m2",
                        "manual_unit_price": 12,
                        "total_price": 120,
                    }
                ]
            }
        },
    )
    assert response.status_code == 200, response.text

    response = client.post(
        "/api/v1/confirm_push",
        headers=headers,
        json={
            "quote_job_id": job_id,
            "project_details": [
                {
                    "project_name": "temporary protection",
                    "quantity": 10,
                    "unit": "m2",
                    "unit_price": 12,
                    "manual_unit_price": 12,
                    "total_price": 120,
                    "cost_reference": {"matched": False},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        draft = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.quote_job_id == job_id).one()
        assert draft.status == PREVIEW_DRAFT_STATUS_PUSHED
        assert draft.pushed_at is not None
    finally:
        db.close()

    response = client.put(
        f"/api/v1/quote/jobs/{job_id}/preview-draft",
        headers=headers,
        json={"draft": {"project_details": []}},
    )
    assert response.status_code == 409


def test_confirm_push_rejects_other_users_quote_job_id(client, monkeypatch):
    owner_username, owner_headers = _login_headers(client)
    intruder_username, intruder_headers = _login_headers(client)
    assert owner_username != intruder_username
    job_id = _seed_quote_job(owner_username)

    response = client.put(
        f"/api/v1/quote/jobs/{job_id}/preview-draft",
        headers=owner_headers,
        json={
            "draft": {
                "project_details": [
                    {
                        "project_name": "temporary protection",
                        "quantity": 10,
                        "unit": "m2",
                        "manual_unit_price": 12,
                        "total_price": 120,
                    }
                ]
            }
        },
    )
    assert response.status_code == 200, response.text

    called = {"push": False}

    async def fake_push_should_not_run(**kwargs):
        called["push"] = True
        return _FakePushSuccess()

    monkeypatch.setattr("app.api.v1.quote.post_json_via_gateway", fake_push_should_not_run)
    response = client.post(
        "/api/v1/confirm_push",
        headers=intruder_headers,
        json={
            "quote_job_id": job_id,
            "project_details": [
                {
                    "project_name": "temporary protection",
                    "quantity": 10,
                    "unit": "m2",
                    "unit_price": 12,
                    "manual_unit_price": 12,
                    "total_price": 120,
                    "cost_reference": {"matched": False},
                }
            ],
        },
    )
    assert response.status_code == 404
    assert called["push"] is False

    db = SessionLocal()
    try:
        draft = db.query(QuotePreviewDraft).filter(QuotePreviewDraft.quote_job_id == job_id).one()
        assert draft.status == PREVIEW_DRAFT_STATUS_EDITING
        assert draft.pushed_at is None
    finally:
        db.close()
