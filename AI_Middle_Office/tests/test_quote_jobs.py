import json
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User


def _login_headers(client):
    username = f"job_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _admin_headers(client):
    username = f"job_admin_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash(password), role="admin", quota=20))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _response_data(response):
    return response.json()["data"]


def test_create_quote_job_returns_queued_status(client):
    headers = _login_headers(client)

    response = client.post(
        "/api/v1/quote/jobs",
        data={"message": "客厅地砖10平米"},
        headers=headers,
    )

    assert response.status_code == 202
    body = _response_data(response)
    assert body["job_id"]
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert body["trace_id"]
    assert body["request_summary"] == "客厅地砖10平米"
    assert body["result_item_count"] == 0
    assert body["events"][0]["status"] == "queued"

    status_response = client.get(f"/api/v1/quote/jobs/{body['job_id']}", headers=headers)
    assert status_response.status_code == 200
    detail = _response_data(status_response)
    assert detail["job_id"] == body["job_id"]
    assert detail["events"][0]["event_type"] == "queued"

    db = SessionLocal()
    try:
        event = db.query(QuoteJobEvent).filter(QuoteJobEvent.quote_job_id == body["job_id"]).one()
        assert event.event_type == "queued"
        assert event.stage == "queued"
    finally:
        db.close()


def test_quote_job_requires_auth(client):
    response = client.get("/api/v1/quote/jobs/missing")

    assert response.status_code == 401


def test_list_and_cancel_quote_job(client):
    headers = _login_headers(client)
    create_response = client.post("/api/v1/quote/jobs", data={"message": "卧室刷漆20平米"}, headers=headers)
    assert create_response.status_code == 202
    job_id = _response_data(create_response)["job_id"]

    list_response = client.get("/api/v1/quote/jobs?status=queued", headers=headers)
    assert list_response.status_code == 200
    assert any(item["job_id"] == job_id for item in list_response.json()["data"])

    cancel_response = client.post(f"/api/v1/quote/jobs/{job_id}/cancel", headers=headers)
    assert cancel_response.status_code == 200
    body = _response_data(cancel_response)
    assert body["status"] == "canceled"
    assert body["stage"] == "canceled"
    assert body["failure_stage"] == "canceled"
    assert body["duration_ms"] is not None
    assert body["error_message"] == "任务已取消"


def test_retry_canceled_quote_job_creates_new_job(client):
    headers = _login_headers(client)
    create_response = client.post("/api/v1/quote/jobs", data={"message": "厨房吊顶5平米"}, headers=headers)
    job_id = _response_data(create_response)["job_id"]
    cancel_response = client.post(f"/api/v1/quote/jobs/{job_id}/cancel", headers=headers)
    assert cancel_response.status_code == 200

    retry_response = client.post(f"/api/v1/quote/jobs/{job_id}/retry", headers=headers)
    assert retry_response.status_code == 202
    body = _response_data(retry_response)
    assert body["job_id"] != job_id
    assert body["status"] == "queued"
    assert body["events"][0]["source_job_id"] == job_id


def test_admin_can_list_all_jobs_and_mark_timeouts(client):
    user_headers = _login_headers(client)
    admin_headers = _admin_headers(client)
    create_response = client.post("/api/v1/quote/jobs", data={"message": "卫生间防水8平米"}, headers=user_headers)
    job_id = _response_data(create_response)["job_id"]

    db = SessionLocal()
    try:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).first()
        job.status = "running"
        job.stage = "n8n"
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=90)
        db.commit()
    finally:
        db.close()

    mark_response = client.post("/api/v1/admin/quote/jobs/mark_timeouts?timeout_minutes=30", headers=admin_headers)
    assert mark_response.status_code == 200
    body = mark_response.json()
    assert body["marked_count"] >= 1
    marked = next(item for item in body["data"] if item["job_id"] == job_id)
    assert marked["failure_stage"] == "timeout"
    assert marked["duration_ms"] is not None

    list_response = client.get("/api/v1/quote/jobs?status=timed_out", headers=admin_headers)
    assert list_response.status_code == 200
    assert any(item["job_id"] == job_id for item in list_response.json()["data"])


def test_quote_job_events_stream_replays_terminal_events(client):
    headers = _login_headers(client)
    login_response = client.get("/api/v1/auth/me", headers=headers)
    username = login_response.json()["username"]
    job_id = str(uuid.uuid4())
    events = [
        {"status": "queued", "message": "报价任务已进入队列", "trace_id": "trace-events", "stage": "queued"},
        {"status": "processing", "message": "异步报价任务已开始执行", "trace_id": "trace-events", "stage": "started"},
        {
            "status": "preview",
            "message": "AI 预审数据已就绪",
            "trace_id": "trace-events",
            "stage": "completed",
            "data": {"project_details": [{"project_name": "墙面刷新"}]},
        },
    ]

    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="succeeded",
                stage="completed",
                trace_id="trace-events",
                events_json=json.dumps(events, ensure_ascii=False),
                result_json=json.dumps(events[-1]["data"], ensure_ascii=False),
                finished_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/v1/quote/jobs/{job_id}/events", headers=headers)

    assert response.status_code == 200
    streamed = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["status"] for event in streamed] == ["queued", "processing", "preview"]
    assert streamed[-1]["data"]["project_details"][0]["project_name"] == "墙面刷新"


def test_quote_job_detail_reads_structured_events_and_result_summary(client):
    headers = _login_headers(client)
    username = client.get("/api/v1/auth/me", headers=headers).json()["username"]
    job_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        job = QuoteJob(
            job_id=job_id,
            username=username,
            status="succeeded",
            stage="completed",
            message="living room renovation",
            file_name="plan.png",
            trace_id="trace-structured",
            result_json=json.dumps(
                {
                    "project_details": [
                        {"project_name": "wall paint", "total_price": 220},
                        {"project_name": "floor tile", "total_price": 110},
                    ]
                },
                ensure_ascii=False,
            ),
            request_summary="living room renovation",
            source_file_name="plan.png",
            result_total_amount=330,
            result_item_count=2,
            preview_project_names="wall paint, floor tile",
            duration_ms=1234,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.flush()
        db.add(
            QuoteJobEvent(
                quote_job_id=job_id,
                event_index=1,
                event_type="preview",
                stage="completed",
                message="AI preview ready",
                trace_id="trace-structured",
                payload_json=json.dumps({"data": {"ok": True}}, ensure_ascii=False),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/v1/quote/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["request_summary"] == "living room renovation"
    assert data["source_file_name"] == "plan.png"
    assert data["result_total_amount"] == 330
    assert data["result_item_count"] == 2
    assert data["preview_project_names"] == ["wall paint", "floor tile"]
    assert data["duration_ms"] == 1234
    assert data["events"][0]["event_type"] == "preview"
    assert data["events"][0]["payload"]["data"]["ok"] is True


def test_quote_job_events_hide_other_users_jobs(client):
    owner_headers = _login_headers(client)
    other_headers = _login_headers(client)
    owner = client.get("/api/v1/auth/me", headers=owner_headers).json()["username"]
    job_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=owner,
                status="succeeded",
                stage="completed",
                trace_id="trace-private",
                events_json=json.dumps([{"status": "queued", "message": "private"}], ensure_ascii=False),
                finished_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/v1/quote/jobs/{job_id}/events", headers=other_headers)

    assert response.status_code == 200
    assert "报价任务不存在" in response.text
