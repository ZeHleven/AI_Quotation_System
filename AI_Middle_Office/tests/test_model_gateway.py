import uuid
import asyncio
from types import SimpleNamespace

import httpx

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User
from app.services.model_gateway import (
    call_dashscope_cad_view_detail_plan,
    call_dashscope_drawing_layout_plan,
    call_dashscope_vision_extract,
    call_dashscope_pdf_agent_bill_summarize,
    call_dashscope_pdf_agent_evidence_extract,
    call_openai_pdf_agent_bill_summarize,
    call_openai_pdf_agent_evidence_extract,
    call_glm_drawing_tile_extract,
    call_glm_pdf_drawing_itemize,
    call_glm_pdf_quantity_suggest,
    call_glm_vision_extract,
    call_quote_vision_extract,
    drawing_tile_vision_prompt_for_mode,
    parse_drawing_tile_vision_json,
    parse_pdf_drawing_itemization_json,
    parse_pdf_quantity_suggestion_json,
    post_json_via_gateway,
    record_model_call,
    record_model_call_async,
    quote_vision_model_label,
    reset_circuit_breakers,
)


def _fake_openai_settings():
    return SimpleNamespace(
        openai_api_key="sk-test-openai-key",
        openai_vision_model="gpt-4.1",
        openai_responses_url="https://api.openai.test/v1/responses",
        openai_drawing_agent_timeout_seconds=120,
        https_proxy="http://127.0.0.1:7897",
        http_proxy="http://127.0.0.1:7897",
    )


def _fake_dashscope_settings():
    return SimpleNamespace(
        quote_vision_provider="dashscope",
        dashscope_api_key="sk-test-dashscope-key",
        dashscope_vision_model="qwen3.7-plus",
        dashscope_evidence_model="qwen-vl-max",
        dashscope_bill_summary_model="qwen-vl-max",
        drawing_layout_planner_model="qwen-layout-test",
        drawing_cad_view_detail_planner_model="qwen-cad-detail-test",
        dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        dashscope_timeout_seconds=120,
        dashscope_temperature=0.2,
    )


def _admin_headers(client):
    username = f"gateway_admin_{uuid.uuid4().hex[:10]}"
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


def test_model_gateway_stats_returns_logged_calls(client):
    reset_circuit_breakers()
    headers = _admin_headers(client)
    record_model_call(
        provider="zhipu",
        model="glm-4v-flash",
        endpoint_type="vision_extract",
        status="success",
        username="tester",
        trace_id="trace-test",
        http_status=200,
        latency_ms=123.4,
        input_chars=100,
        output_chars=20,
    )

    response = client.get("/api/v1/admin/model_gateway/stats", headers=headers)

    assert response.status_code == 200
    rows = response.json()["data"]
    assert any(
        row["provider"] == "zhipu"
        and row["model"] == "glm-4v-flash"
        and row["endpoint_type"] == "vision_extract"
        and row["status"] == "success"
        for row in rows
    )


def test_model_gateway_stats_requires_admin(client):
    username = f"gateway_user_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    token = response.json()["access_token"]

    response = client.get(
        "/api/v1/admin/model_gateway/stats",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_record_model_call_async_uses_threadpool(monkeypatch):
    calls = []

    def fake_record_model_call(**kwargs):
        calls.append({"recorded": kwargs})

    async def fake_to_thread(fn, **kwargs):
        calls.append({"threaded_fn": fn, "threaded_kwargs": kwargs})
        return fn(**kwargs)

    monkeypatch.setattr("app.services.model_gateway.record_model_call", fake_record_model_call)
    monkeypatch.setattr("app.services.model_gateway.asyncio.to_thread", fake_to_thread)

    asyncio.run(
        record_model_call_async(
            provider="zhipu",
            model="glm-4v-flash",
            endpoint_type="vision_extract",
            status="success",
        )
    )

    assert calls[0]["threaded_fn"] is fake_record_model_call
    assert calls[0]["threaded_kwargs"]["provider"] == "zhipu"
    assert calls[1]["recorded"]["status"] == "success"


def test_post_json_via_gateway_uses_async_httpx(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        post_json_via_gateway(
            provider="n8n",
            model="dify-deepseek",
            endpoint_type="quote_calc",
            url="http://example.test/webhook",
            json_payload={"hello": "world"},
            headers={"X-Test": "1"},
            timeout=12,
            username="tester",
            trace_id="trace-httpx",
        )
    )

    assert response.status_code == 200
    assert calls[0]["client_kwargs"]["timeout"] == 12
    assert calls[0]["url"] == "http://example.test/webhook"
    assert calls[0]["post_kwargs"]["json"] == {"hello": "world"}
    assert calls[0]["post_kwargs"]["headers"] == {"X-Test": "1"}


def test_post_json_via_gateway_zero_timeout_means_unlimited(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        post_json_via_gateway(
            provider="deepseek",
            model="deepseek-v4-pro",
            endpoint_type="bidding_tender_important_info_extract",
            url="http://example.test/chat",
            json_payload={"messages": []},
            headers={},
            timeout=0,
        )
    )

    assert response.status_code == 200
    assert calls[0]["client_kwargs"]["timeout"] is None


def test_call_openai_pdf_agent_evidence_extract_builds_responses_payload(monkeypatch):
    reset_circuit_breakers()
    calls = []
    records = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"drawing_evidence":[{"view_id":"p001_view001","view_type":"floor_plan","methods":["地面瓷砖铺贴"],"confidence":0.8}]}',
                                }
                            ]
                        }
                    ]
                },
            )

    async def fake_record_model_call_async(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_openai_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    result = asyncio.run(
        call_openai_pdf_agent_evidence_extract(
            [
                {
                    "view_id": "p001_view001",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "cad_view",
                    "selection_role": "local_cad_view",
                    "bbox_pixel": [0, 0, 100, 100],
                    "image_base64": "ZmFrZQ==",
                    "mime_type": "image/png",
                }
            ],
            username="tester",
            trace_id="trace-openai-evidence",
        )
    )

    payload = calls[0]["post_kwargs"]["json"]
    content = payload["input"][0]["content"]
    assert calls[0]["url"] == "https://api.openai.test/v1/responses"
    assert calls[0]["client_kwargs"]["trust_env"] is False
    assert calls[0]["client_kwargs"]["proxy"] == "http://127.0.0.1:7897"
    assert payload["model"] == "gpt-4.1"
    assert content[0]["type"] == "input_text"
    assert "view_manifest" in content[0]["text"]
    assert "selection_role" in content[0]["text"]
    assert any("selection_role=local_cad_view" in item.get("text", "") for item in content if item.get("type") == "input_text")
    assert any(item.get("type") == "input_image" and item.get("image_url", "").startswith("data:image/png;base64,") for item in content)
    assert calls[0]["post_kwargs"]["headers"]["Authorization"] == "Bearer sk-test-openai-key"
    assert result["drawing_evidence"][0]["view_id"] == "p001_view001"
    assert records[0]["provider"] == "openai"
    assert records[0]["endpoint_type"] == "pdf_agent_evidence_extract"


def test_call_openai_pdf_agent_bill_summarize_parses_bill_items(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "output_text": (
                        '{"bill_items":[{'
                        '"concrete_item_name":"餐厅地面瓷砖铺贴CT系列",'
                        '"feature":"餐厅主要区域地面块料铺装",'
                        '"unit":"m2",'
                        '"rough_quantity":"约135",'
                        '"quantity_note":"待复核",'
                        '"source_view_ids":["p001_view001"],'
                        '"source_evidence":["地面铺装图可见CT标注"],'
                        '"confidence":0.78,'
                        '"needs_manual_review":true'
                        "}]} "
                    )
                },
            )

    async def fake_record_model_call_async(**kwargs):
        return None

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_openai_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    result = asyncio.run(
        call_openai_pdf_agent_bill_summarize(
            {
                "phase": "pdf-agent-evidence-merge",
                "merged_materials": [{"code": "CT-01", "source_view_ids": ["p001_view001"]}],
                "merged_methods": [{"method": "地面瓷砖铺贴", "source_view_ids": ["p001_view001"]}],
            },
            username="tester",
            trace_id="trace-openai-bill",
        )
    )

    prompt = calls[0]["post_kwargs"]["json"]["input"][0]["content"][0]["text"]
    assert "图纸具体做法名称生成规则" in prompt
    assert "合并后的图纸证据" in prompt
    assert result["bill_items"][0]["concrete_item_name"] == "餐厅地面瓷砖铺贴CT系列"
    assert result["bill_items"][0]["source_view_ids"] == ["p001_view001"]


def test_call_dashscope_pdf_agent_evidence_extract_builds_chat_completions_payload(monkeypatch):
    reset_circuit_breakers()
    calls = []
    records = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"drawing_evidence":[{"view_id":"p001_view001","view_type":"elevation","methods":["墙面瓷砖湿贴"],"confidence":0.82}]}'
                            }
                        }
                    ]
                },
            )

    async def fake_record_model_call_async(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    result = asyncio.run(
        call_dashscope_pdf_agent_evidence_extract(
            [
                {
                    "view_id": "p001_view001",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "cad_view",
                    "selection_role": "local_cad_view",
                    "bbox_pixel": [0, 0, 100, 100],
                    "image_base64": "ZmFrZQ==",
                    "mime_type": "image/png",
                }
            ],
            username="tester",
            trace_id="trace-dashscope-evidence",
        )
    )

    payload = calls[0]["post_kwargs"]["json"]
    content = payload["messages"][0]["content"]
    assert calls[0]["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert calls[0]["client_kwargs"]["trust_env"] is False
    assert payload["model"] == "qwen-vl-max"
    assert payload["temperature"] == 0.2
    assert content[0]["type"] == "text"
    assert "view_manifest" in content[0]["text"]
    assert "selection_role" in content[0]["text"]
    assert any("selection_role=local_cad_view" in item.get("text", "") for item in content if item.get("type") == "text")
    assert any(item.get("type") == "image_url" and item.get("image_url", {}).get("url", "").startswith("data:image/png;base64,") for item in content)
    assert calls[0]["post_kwargs"]["headers"]["Authorization"] == "Bearer sk-test-dashscope-key"
    assert result["drawing_evidence"][0]["view_id"] == "p001_view001"
    assert records[0]["provider"] == "dashscope"
    assert records[0]["endpoint_type"] == "pdf_agent_evidence_extract"


def test_call_dashscope_drawing_layout_plan_builds_planner_payload(monkeypatch):
    reset_circuit_breakers()
    calls = []
    records = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"schema_version":"drawing_layout_plan_v1",'
                                    '"regions":[{"region_id":"r001","view_id":"layout_p001_001",'
                                    '"region_type":"material_table","bbox_ratio":[0.7,0.1,0.95,0.3],'
                                    '"priority":0.9,"confidence":0.8,"recommended_tools":["ocr"]}]}'
                                )
                            }
                        }
                    ]
                },
            )

    async def fake_record_model_call_async(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    result = asyncio.run(
        call_dashscope_drawing_layout_plan(
            [
                {
                    "view_id": "layout_p001_001",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "grid_size": 4,
                    "image_width_px": 1200,
                    "image_height_px": 900,
                    "image_base64": "ZmFrZQ==",
                    "mime_type": "image/png",
                }
            ],
            username="tester",
            trace_id="trace-layout",
        )
    )

    payload = calls[0]["post_kwargs"]["json"]
    content = payload["messages"][0]["content"]
    assert payload["model"] == "qwen-layout-test"
    assert "工程图纸版面规划器" in content[0]["text"]
    assert "layout_p001_001" in content[0]["text"]
    assert any(item.get("type") == "image_url" for item in content)
    assert result["regions"][0]["region_type"] == "material_table"
    assert records[0]["provider"] == "dashscope"
    assert records[0]["endpoint_type"] == "drawing_layout_planner"


def test_call_dashscope_cad_view_detail_plan_builds_planner_payload(monkeypatch):
    reset_circuit_breakers()
    calls = []
    records = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"schema_version":"cad_view_detail_plan_v1",'
                                    '"regions":[{"region_id":"v001_r001","view_id":"p001_view001",'
                                    '"region_type":"title_block","region_subtype":"right_title_bar",'
                                    '"bbox_ratio":[0.82,0.02,0.98,0.98],'
                                    '"priority":0.9,"confidence":0.8,"recommended_tools":["ocr"]}]}'
                                )
                            }
                        }
                    ]
                },
            )

    async def fake_record_model_call_async(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    result = asyncio.run(
        call_dashscope_cad_view_detail_plan(
            [
                {
                    "view_id": "p001_view001",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "grid_size": 3,
                    "parent_bbox_pixel": [100, 100, 600, 400],
                    "view_image_width_px": 500,
                    "view_image_height_px": 300,
                    "image_base64": "ZmFrZQ==",
                    "mime_type": "image/png",
                }
            ],
            username="tester",
            trace_id="trace-cad-detail",
        )
    )

    payload = calls[0]["post_kwargs"]["json"]
    content = payload["messages"][0]["content"]
    assert payload["model"] == "qwen-cad-detail-test"
    assert "CAD view internal layout planner" in content[0]["text"]
    assert "right-side green title/material bar" in content[0]["text"]
    assert "p001_view001" in content[0]["text"]
    assert any(item.get("type") == "image_url" for item in content)
    assert result["regions"][0]["region_subtype"] == "right_title_bar"
    assert records[0]["provider"] == "dashscope"
    assert records[0]["endpoint_type"] == "cad_view_detail_planner"


def test_call_dashscope_pdf_agent_bill_summarize_parses_bill_items(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"bill_items":[{'
                                    '"concrete_item_name":"餐厅墙面瓷砖湿贴CT系列",'
                                    '"feature":"餐厅立面墙面瓷砖湿贴",'
                                    '"unit":"m2",'
                                    '"rough_quantity":"待复核",'
                                    '"source_view_ids":["p001_view001"],'
                                    '"source_evidence":["立面图可见墙面瓷砖分格"],'
                                    '"confidence":0.76,'
                                    '"needs_manual_review":true'
                                    "}]} "
                                )
                            }
                        }
                    ]
                },
            )

    async def fake_record_model_call_async(**kwargs):
        return None

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    result = asyncio.run(
        call_dashscope_pdf_agent_bill_summarize(
            {"merged_methods": [{"method": "墙面瓷砖湿贴", "source_view_ids": ["p001_view001"]}]},
            username="tester",
            trace_id="trace-dashscope-bill",
        )
    )

    payload = calls[0]["post_kwargs"]["json"]
    prompt = payload["messages"][0]["content"][0]["text"]
    assert payload["temperature"] == 0.2
    assert "图纸具体做法名称生成规则" in prompt
    assert result["bill_items"][0]["concrete_item_name"] == "餐厅墙面瓷砖湿贴CT系列"


def test_call_dashscope_pdf_agent_model_overrides_split_evidence_and_bill(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            content = '{"bill_items":[]}' if kwargs["json"]["model"] == "qwen3.7-plus" else '{"drawing_evidence":[]}'
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def fake_record_model_call_async(**kwargs):
        return None

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.model_gateway.record_model_call_async", fake_record_model_call_async)

    asyncio.run(
        call_dashscope_pdf_agent_evidence_extract(
            [
                {
                    "view_id": "p001_view001",
                    "image_base64": "ZmFrZQ==",
                    "mime_type": "image/png",
                }
            ],
            model_override="qwen-vl-max",
        )
    )
    asyncio.run(call_dashscope_pdf_agent_bill_summarize({}, model_override="qwen3.7-plus"))

    assert calls[0]["post_kwargs"]["json"]["model"] == "qwen-vl-max"
    assert calls[1]["post_kwargs"]["json"]["model"] == "qwen3.7-plus"
    assert calls[0]["post_kwargs"]["json"]["temperature"] == 0.2
    assert calls[1]["post_kwargs"]["json"]["temperature"] == 0.2


def test_call_glm_vision_extract_uses_async_httpx_without_env_proxy(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(200, json={"choices": [{"message": {"content": "墙面刷新，20平米"}}]})

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_glm_vision_extract(
            "ZmFrZQ==",
            "image/png",
            username="tester",
            trace_id="trace-vision",
        )
    )

    assert result == "墙面刷新，20平米"
    assert calls[0]["client_kwargs"]["trust_env"] is False
    assert calls[0]["client_kwargs"]["verify"] is False
    assert calls[0]["post_kwargs"]["headers"]["Authorization"].startswith("Bearer ")


def test_call_dashscope_vision_extract_uses_qwen_model(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(200, json={"choices": [{"message": {"content": "墙面刷新，10平方米"}}]})

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_dashscope_vision_extract(
            "ZmFrZQ==",
            "image/png",
            username="tester",
            trace_id="trace-qwen-vision",
        )
    )

    assert result == "墙面刷新，10平方米"
    assert calls[0]["post_kwargs"]["json"]["model"] == "qwen3.7-plus"
    assert calls[0]["post_kwargs"]["json"]["messages"][0]["content"][1]["type"] == "image_url"
    assert calls[0]["post_kwargs"]["headers"]["Authorization"].startswith("Bearer ")
    assert calls[0]["client_kwargs"]["trust_env"] is False


def test_call_quote_vision_extract_routes_to_dashscope(monkeypatch):
    reset_circuit_breakers()
    calls = []

    async def fake_dashscope_vision_extract(base64_image, mime_type, *, username=None, trace_id=None):
        calls.append(
            {
                "base64_image": base64_image,
                "mime_type": mime_type,
                "username": username,
                "trace_id": trace_id,
            }
        )
        return "qwen parsed text"

    async def fake_glm_vision_extract(*args, **kwargs):
        raise AssertionError("GLM should not be used when QUOTE_VISION_PROVIDER=dashscope")

    monkeypatch.setattr("app.services.model_gateway.settings", _fake_dashscope_settings())
    monkeypatch.setattr("app.services.model_gateway.call_dashscope_vision_extract", fake_dashscope_vision_extract)
    monkeypatch.setattr("app.services.model_gateway.call_glm_vision_extract", fake_glm_vision_extract)

    result = asyncio.run(
        call_quote_vision_extract(
            "ZmFrZQ==",
            "image/png",
            username="tester",
            trace_id="trace-quote-qwen",
        )
    )

    assert result == "qwen parsed text"
    assert quote_vision_model_label() == "qwen3.7-plus"
    assert calls == [
        {
            "base64_image": "ZmFrZQ==",
            "mime_type": "image/png",
            "username": "tester",
            "trace_id": "trace-quote-qwen",
        }
    ]


def test_parse_pdf_quantity_suggestion_json_normalizes_items():
    parsed = parse_pdf_quantity_suggestion_json(
        """
```json
{
  "quantity_suggestions": [
    {
      "item_ref": "PDFITEM-000001",
      "project_name": "地砖铺贴",
      "standard_item_name": "块料楼地面",
      "quantity": "42.6",
      "unit": "㎡",
      "formula": "7.10m × 6.00m = 42.60㎡",
      "quantity_rule": "按设计图示尺寸以面积计算",
      "evidence_text": "餐厅 CT-02 600X1200",
      "source_page": 1,
      "source_tile_id": "p001_whole",
      "confidence": 0.72,
      "risk_flags": ["尺寸来自AI视觉推断"],
      "review_status": "candidate_needs_manual_review"
    }
  ]
}
```
"""
    )

    suggestion = parsed["quantity_suggestions"][0]
    assert suggestion["item_ref"] == "PDFITEM-000001"
    assert suggestion["quantity"] == 42.6
    assert suggestion["unit"] == "㎡"
    assert suggestion["review_status"] == "candidate_needs_manual_review"
    assert suggestion["risk_flags"] == ["尺寸来自AI视觉推断"]


def test_parse_pdf_quantity_suggestion_json_recovers_complete_items_from_truncated_response():
    parsed = parse_pdf_quantity_suggestion_json(
        """
```json
{
  "quantity_suggestions": [
    {
      "item_ref": "PDFITEM-000001",
      "project_name": "wall plaster",
      "standard_item_name": "wall work",
      "quantity": 42.6,
      "unit": "m2",
      "formula": "rough MVP estimate",
      "quantity_rule": "area",
      "evidence_text": "visible wall note",
      "source_page": 1,
      "source_tile_id": "p001_g03_r01_c01",
      "confidence": 0.32,
      "risk_flags": ["rough_estimate"],
      "review_status": "candidate_needs_manual_review",
      "reason": "rough quantity for MVP preview"
    },
    {
      "item_ref": "PDFITEM-000002",
      "project_name": "unfinished
```
"""
    )

    suggestions = parsed["quantity_suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["item_ref"] == "PDFITEM-000001"
    assert suggestions[0]["quantity"] == 42.6
    assert suggestions[0]["unit"] == "m2"


def test_drawing_tile_vision_prompt_modes_add_specialized_instructions():
    general_prompt = drawing_tile_vision_prompt_for_mode("general")
    electrical_prompt = drawing_tile_vision_prompt_for_mode("electrical_mep")
    plumbing_prompt = drawing_tile_vision_prompt_for_mode("plumbing_fixture")
    fixture_prompt = drawing_tile_vision_prompt_for_mode("fixture_valve_schedule")
    demolition_prompt = drawing_tile_vision_prompt_for_mode("demolition_node")
    door_window_prompt = drawing_tile_vision_prompt_for_mode("door_window_demolition")
    finish_prompt = drawing_tile_vision_prompt_for_mode("finish_schedule")
    table_prompt = drawing_tile_vision_prompt_for_mode("table_legend")
    node_prompt = drawing_tile_vision_prompt_for_mode("node_detail")

    assert "你是建筑装饰" in general_prompt
    assert "视觉证据提取助手" in general_prompt
    assert '"evidence_items"' in general_prompt
    assert "浣犳槸" not in general_prompt
    assert "涓撻" not in electrical_prompt
    assert "售卖窗口" in finish_prompt
    assert "专项模式：电气系统/设备/线管线缆召回" in electrical_prompt
    assert "SC/MT/JDG" in electrical_prompt
    assert "不同灯具类型要拆开" in electrical_prompt
    assert "专项模式：给排水管线/洁具/阀门召回" in plumbing_prompt
    assert "不能泛化成“管道安装”" in plumbing_prompt
    assert "专项模式：洁具/阀门/水表/地漏表格与图例召回" in fixture_prompt
    assert "每个符号/表格行都要独立 evidence_item" in fixture_prompt
    assert "门窗拆除" in demolition_prompt
    assert "不锈钢玻璃门拆除" in demolition_prompt
    assert "专项模式：门窗/洞口/售卖窗口/拆除对象召回" in door_window_prompt
    assert "不要把门窗、售卖窗口、洁具拆除泛化" in door_window_prompt
    assert "专项模式：表格/图例/材料设备表逐行召回" in table_prompt
    assert "AL/AP/AT" in table_prompt
    assert "专项模式：节点详图/做法说明/构造剖面逐条召回" in node_prompt
    assert "窗台石" in node_prompt
    assert len(electrical_prompt) > len(general_prompt)


def test_call_glm_pdf_quantity_suggest_uses_candidate_quantity_prompt(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"quantity_suggestions":[{'
                                    '"item_ref":"PDFITEM-000001",'
                                    '"project_name":"地砖铺贴",'
                                    '"standard_item_name":"块料楼地面",'
                                    '"quantity":42.6,'
                                    '"unit":"㎡",'
                                    '"formula":"7.10m * 6.00m = 42.60㎡",'
                                    '"quantity_rule":"按设计图示尺寸以面积计算",'
                                    '"evidence_text":"餐厅 CT-02",'
                                    '"confidence":0.7'
                                    "}]} "
                                )
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_glm_pdf_quantity_suggest(
            "ZmFrZQ==",
            "image/png",
            quantity_context={
                "mapped_items": [
                    {
                        "识别编号": "PDFITEM-000001",
                        "标准项目名称": "块料楼地面",
                        "工程量计算规则": "按设计图示尺寸以面积计算",
                    }
                ]
            },
            username="tester",
            trace_id="trace-pdf-quantity",
        )
    )

    prompt_text = calls[0]["post_kwargs"]["json"]["messages"][0]["content"][0]["text"]
    assert "候选工程量" in prompt_text
    assert "candidate_needs_manual_review" in prompt_text
    assert "PDFITEM-000001" in prompt_text
    assert result["quantity_suggestions"][0]["quantity"] == 42.6
    assert calls[0]["client_kwargs"]["trust_env"] is False
    assert calls[0]["client_kwargs"]["verify"] is False
    assert calls[0]["post_kwargs"]["headers"]["Authorization"].startswith("Bearer ")


def test_parse_drawing_tile_vision_json_normalizes_items():
    parsed = parse_drawing_tile_vision_json(
        """
```json
{
  "evidence_items": [
    {
      "evidence_role": "material_legend",
      "discipline": "decoration",
      "text": "CT-02 600X1200灰色地砖",
      "item_hint": "块料楼地面",
      "space": "餐厅",
      "material_codes": ["CT-02"],
      "spec_or_method": "600X1200灰色地砖",
      "suggested_unit": "㎡",
      "confidence": 0.86,
      "needs_manual_review": false
    }
  ]
}
```
"""
    )

    assert parsed["evidence_items"][0]["evidence_role"] == "material_legend"
    assert parsed["evidence_items"][0]["discipline"] == "decoration"
    assert parsed["evidence_items"][0]["text"] == "CT-02 600X1200灰色地砖"
    assert parsed["evidence_items"][0]["item_hint"] == "块料楼地面"
    assert parsed["evidence_items"][0]["material_codes"] == ["CT-02"]
    assert parsed["evidence_items"][0]["suggested_unit"] == "㎡"
    assert parsed["evidence_items"][0]["confidence"] == 0.86
    assert parsed["evidence_items"][0]["needs_manual_review"] is False


def test_parse_drawing_tile_vision_json_keeps_structured_dn_de_rows():
    parsed = parse_drawing_tile_vision_json(
        """
```json
{
  "evidence_items": [
    {
      "public_diameter": "DN40",
      "plastic_pipe_outside_diameter": "De50",
      "inch_label": "",
      "confidence": 0.95,
      "needs_manual_review": false
    }
  ]
}
```
"""
    )

    item = parsed["evidence_items"][0]
    assert item["evidence_role"] == "table_row"
    assert item["item_hint"] == "DN40"
    assert item["spec_or_method"] == "De50"
    assert item["text"] == "DN40 | De50 |"
    assert item["confidence"] == 0.95
    assert item["needs_manual_review"] is False


def test_parse_drawing_tile_vision_json_accepts_root_array_table_rows():
    parsed = parse_drawing_tile_vision_json(
        """
```json
[
  {
    "public diameter": "DN100",
    "plastic pipe outside diameter": "De110"
  }
]
```
"""
    )

    assert parsed["evidence_items"][0]["item_hint"] == "DN100"
    assert parsed["evidence_items"][0]["spec_or_method"] == "De110"
    assert parsed["evidence_items"][0]["text"] == "DN100 | De110 |"


def test_parse_pdf_drawing_itemization_json_normalizes_items():
    parsed = parse_pdf_drawing_itemization_json(
        """
```json
{
  "drawing_items": [
    {
      "item_name": "块料楼地面",
      "space": "餐厅",
      "material_codes": ["CT-02"],
      "spec_or_method": "600X1200灰色地砖",
      "evidence_text": "CT-02 600X1200灰色地砖",
      "confidence": 0.88,
      "needs_manual_review": false
    }
  ]
}
```
"""
    )

    assert parsed["drawing_items"][0]["item_name"] == "块料楼地面"
    assert parsed["drawing_items"][0]["space"] == "餐厅"
    assert parsed["drawing_items"][0]["material_codes"] == ["CT-02"]
    assert parsed["drawing_items"][0]["confidence"] == 0.88
    assert parsed["drawing_items"][0]["needs_manual_review"] is False


def test_call_glm_drawing_tile_extract_uses_drawing_prompt(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"evidence_items":[{"evidence_role":"room_name","text":"餐厅","confidence":0.72,"needs_manual_review":true}]}'
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_glm_drawing_tile_extract(
            "ZmFrZQ==",
            "image/png",
            tile_context={"tile_id": "p001_g03_r01_c01", "page": 1},
            username="tester",
            trace_id="trace-drawing-tile",
        )
    )

    prompt_text = calls[0]["post_kwargs"]["json"]["messages"][0]["content"][0]["text"]
    assert "施工图" in prompt_text
    assert "视觉证据提取助手" in prompt_text
    assert "p001_g03_r01_c01" in prompt_text
    assert result["evidence_items"][0]["text"] == "餐厅"
    assert calls[0]["client_kwargs"]["trust_env"] is False


def test_call_glm_drawing_tile_extract_accepts_prompt_override(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"evidence_items":[{"text":"visible note"}]}'}}]},
            )

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_glm_drawing_tile_extract(
            "ZmFrZQ==",
            "image/png",
            tile_context={"tile_id": "p001_whole"},
            prompt_mode="door_window_demolition",
            prompt_override="CUSTOM ANSWER BLIND PROMPT",
            username="tester",
            trace_id="trace-custom-prompt",
        )
    )

    prompt_text = calls[0]["post_kwargs"]["json"]["messages"][0]["content"][0]["text"]
    assert "CUSTOM ANSWER BLIND PROMPT" in prompt_text
    assert "door_window_demolition" in prompt_text
    assert result["evidence_items"][0]["text"] == "visible note"


def test_call_glm_pdf_drawing_itemize_uses_itemization_prompt(monkeypatch):
    reset_circuit_breakers()
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.client_kwargs = kwargs
            calls.append({"client_kwargs": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            calls[-1].update({"url": url, "post_kwargs": kwargs})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"drawing_items":[{"item_name":"灯具","space":"餐厅","evidence_text":"LED灯具","confidence":0.7}]}'
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr("app.services.model_gateway.httpx.AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        call_glm_pdf_drawing_itemize(
            "ZmFrZQ==",
            "image/png",
            page_context={"page": 1, "tile_id": "p001_whole"},
            prompt_addition="人工清单列项规则：灯具按类型拆分",
            username="tester",
            trace_id="trace-pdf-itemize",
        )
    )

    prompt_text = calls[0]["post_kwargs"]["json"]["messages"][0]["content"][0]["text"]
    assert "具体项目名称" in prompt_text
    assert "人工工程量清单" in prompt_text
    assert "国标项目名称、国标编码由系统后续步骤处理" in prompt_text
    assert "人工清单列项规则：灯具按类型拆分" in prompt_text
    assert "p001_whole" in prompt_text
    assert result["drawing_items"][0]["item_name"] == "灯具"
    assert calls[0]["client_kwargs"]["trust_env"] is False
