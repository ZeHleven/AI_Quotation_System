import json
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO

import httpx
from openpyxl import Workbook

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.client_inquiry import ClientInquiry
from app.models.cost_item import COST_STATUS_ACTIVE, CostItem
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User
from app.services import quote_job_runner


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


def _quote_excel_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["工作内容", "工程量", "计量单位", "特征描述"])
    sheet.append(["拆除复合木地板", 20, "㎡", "不含清运"])
    sheet.append(["窗帘盒/灯槽拆除", 18, "m", "拆除至指定堆放点"])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _seed_cost_item(db, *, item_name: str, unit: str = "m", price: float = 6.0) -> CostItem:
    item = CostItem(
        category="BIZ-2h 测试类",
        subcategory="成本前置",
        item_name=item_name,
        unit=unit,
        price=price,
        subcontract_composite_price=price,
        price_type="combined",
        status=COST_STATUS_ACTIVE,
        source="manual",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


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


def test_admin_quote_job_list_includes_operational_context_and_filters(client):
    user_headers = _login_headers(client)
    admin_headers = _admin_headers(client)
    username = client.get("/api/v1/auth/me", headers=user_headers).json()["username"]
    job_id = str(uuid.uuid4())
    inquiry_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        inquiry = ClientInquiry(
            inquiry_id=inquiry_id,
            source="微信",
            client_name="张三",
            client_phone="13800001111",
            inquiry_time=datetime.now(timezone.utc) - timedelta(minutes=20),
            first_response_time=datetime.now(timezone.utc),
            time_source="manual",
            responder_id=user.id,
            notes="需要局部翻新",
            first_quote_job_id=job_id,
        )
        job = QuoteJob(
            job_id=job_id,
            username=username,
            status="succeeded",
            stage="completed",
            message="厨房局部翻新",
            request_summary="厨房局部翻新",
            source_file_name="kitchen.png",
            result_total_amount=1200,
            result_item_count=3,
            client_inquiry_id=inquiry_id,
            duration_ms=1500,
            finished_at=datetime.now(timezone.utc),
        )
        history = QuoteHistory(
            username=username,
            quote_id=job_id,
            quote_job_id=job_id,
            confirmed_by=username,
            pushed_to_dingtalk=True,
            total_amount=1200,
            item_count=3,
            display_title="厨房局部翻新",
            project_summary="3 items",
            payload_json="{}",
        )
        db.add_all([inquiry, job, history])
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/v1/quote/jobs?source=微信&keyword=13800001111&page_size=5",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    row = next(item for item in data if item["job_id"] == job_id)
    assert row["client_inquiry"]["client_name"] == "张三"
    assert row["client_inquiry"]["source"] == "微信"
    assert row["history"]["pushed_to_dingtalk"] is True
    assert row["history"]["total_amount"] == 1200


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


def test_quote_job_runner_records_runtime_duration(monkeypatch):
    username = f"runner_user_{uuid.uuid4().hex[:10]}"
    job_id = str(uuid.uuid4())

    async def fake_load_file_content(job, db):
        return None

    async def fake_quote_events(**kwargs):
        yield (
            "preview",
            "AI preview ready",
            {
                "stage": "completed",
                "data": {"project_details": [{"project_name": "墙面刷新", "total_price": 100}]},
            },
        )

    perf_values = iter([100.0, 102.345])
    monkeypatch.setattr(quote_job_runner.time, "perf_counter", lambda: next(perf_values))
    monkeypatch.setattr(quote_job_runner, "_load_job_file_content", fake_load_file_content)
    monkeypatch.setattr(quote_job_runner, "_iter_quote_events", fake_quote_events)
    monkeypatch.setattr(quote_job_runner, "safe_record_ai_preview", lambda *args, **kwargs: None)

    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password=get_password_hash("secret123"), role="user", quota=5))
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="queued",
                stage="queued",
                message="客厅墙面刷新",
                trace_id="trace-runtime-duration",
            )
        )
        db.commit()
    finally:
        db.close()

    asyncio.run(quote_job_runner.run_quote_job_async(job_id))

    db = SessionLocal()
    try:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).one()
        user = db.query(User).filter(User.username == username).one()
        assert job.status == "succeeded"
        assert job.duration_ms == 2345
        assert job.result_item_count == 1
        assert user.quota == 4
    finally:
        db.close()


def test_quote_job_runner_normalizes_numbered_text_before_gateway(monkeypatch):
    gateway_calls = []

    async def fake_post_json_via_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return httpx.Response(
            200,
            json={"project_details": [{"project_name": "拆除复合木地板", "unit_price": 7, "total_price": 245}]},
        )

    monkeypatch.setattr(quote_job_runner, "post_json_via_gateway", fake_post_json_via_gateway)

    async def collect_events():
        return [
            event
            async for event in quote_job_runner._iter_quote_events(
                username="runner",
                message=(
                    "请生成报价明细，只包含以下三项：\n"
                    "1. 拆除复合木地板，35平方米\n"
                    "2. 拆除木脚线，42米\n"
                    "3. 拆砖墙（120厚砖墙），8平方米"
                ),
                file_content=None,
                mime_type=None,
                filename=None,
            )
        ]

    events = asyncio.run(collect_events())

    assert events[-1][0] == "preview"
    content = gateway_calls[0]["json_payload"]["text"]["content"]
    assert "\n" not in content
    assert "1." not in content
    assert "拆除复合木地板，35平方米；拆除木脚线，42米；拆砖墙（120厚砖墙），8平方米" in content


def test_quote_job_runner_normalizes_plain_multiline_quote_items_before_gateway(monkeypatch):
    gateway_calls = []

    async def fake_post_json_via_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return httpx.Response(
            200,
            json={"project_details": [{"project_name": "窗帘盒/灯槽拆除", "unit_price": 6, "total_price": 108}]},
        )

    monkeypatch.setattr(quote_job_runner, "post_json_via_gateway", fake_post_json_via_gateway)

    async def collect_events():
        return [
            event
            async for event in quote_job_runner._iter_quote_events(
                username="runner",
                message="拆除复合木地板 20㎡\n拆除复合木地板 20㎡，拆除木脚线 30m\n窗帘盒/灯槽拆除 18m",
                file_content=None,
                mime_type=None,
                filename=None,
            )
        ]

    events = asyncio.run(collect_events())

    assert events[-1][0] == "preview"
    content = gateway_calls[0]["json_payload"]["text"]["content"]
    assert content == "拆除复合木地板 20㎡；拆除复合木地板 20㎡，拆除木脚线 30m；窗帘盒/灯槽拆除 18m"


def test_quote_job_runner_parses_excel_quote_sheet_before_gateway(monkeypatch):
    gateway_calls = []

    async def fake_post_json_via_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return httpx.Response(
            200,
            json={"project_details": [{"project_name": "拆除复合木地板", "unit_price": 12, "total_price": 240}]},
        )

    async def fail_vision_call(*args, **kwargs):
        raise AssertionError("Excel quote sheets should not be sent to GLM-4V")

    monkeypatch.setattr(quote_job_runner, "post_json_via_gateway", fake_post_json_via_gateway)
    monkeypatch.setattr(quote_job_runner, "call_glm_vision_extract", fail_vision_call)

    async def collect_events():
        return [
            event
            async for event in quote_job_runner._iter_quote_events(
                username="runner",
                message="请根据需求单报价",
                file_content=_quote_excel_bytes(),
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename="quote.xlsx",
            )
        ]

    events = asyncio.run(collect_events())

    assert events[-1][0] == "preview"
    content = gateway_calls[0]["json_payload"]["text"]["content"]
    assert "从Excel需求单解析到的内容" in content
    assert "拆除复合木地板，规格/特征：不含清运，数量：20，单位：㎡" in content
    assert "窗帘盒/灯槽拆除，规格/特征：拆除至指定堆放点，数量：18，单位：m" in content


def test_quote_job_runner_attaches_biz2h_cost_context_before_gateway(monkeypatch):
    gateway_calls = []

    async def fake_post_json_via_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return httpx.Response(
            200,
            json={"project_details": [{"project_name": "窗帘盒/灯槽拆除", "unit": "m", "quantity": 18, "unit_price": 6, "total_price": 108}]},
        )

    monkeypatch.setattr(quote_job_runner, "post_json_via_gateway", fake_post_json_via_gateway)

    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name="窗帘盒/灯槽拆除", unit="m", price=6.0)

        async def collect_events():
            return [
                event
                async for event in quote_job_runner._iter_quote_events(
                    username="runner",
                    message="请根据需求单报价",
                    file_content=_quote_excel_bytes(),
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename="quote.xlsx",
                    db=db,
                )
            ]

        events = asyncio.run(collect_events())
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert events[-1][0] == "preview"
    content = gateway_calls[0]["json_payload"]["text"]["content"]
    assert "[成本库底价强参考]" in content
    assert "需求项: 窗帘盒/灯槽拆除" in content
    assert "数量: 18" in content
    assert "匹配类型: fuzzy_item_name" in content
    assert "reference_unit_price: 6.00 元/m" in content
