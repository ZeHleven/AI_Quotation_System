import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.agent import (
    AgentFinding,
    AgentRun,
    AgentSchedulerRun,
    AgentSuggestion,
    AgentSuggestionEvent,
    AgentToolCall,
)
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.quote_requirement_row import QuoteRequirementRow
from app.models.user import User, UserRole
from app.services.agent_daily_scheduler import run_due_quote_review_scheduler_once
from app.services.agent_market_price_search import query_market_price_web_search


@pytest.fixture(autouse=True)
def _disable_market_web_search_by_default():
    old_feature = _set_flag("feature_agent_market_web_search", False)
    old_provider = _set_flag("market_search_provider", "tavily")
    old_key = _set_flag("market_search_api_key", "")
    old_endpoint = _set_flag("market_search_endpoint", "https://api.tavily.com/search")
    try:
        yield
    finally:
        _set_flag("feature_agent_market_web_search", old_feature)
        _set_flag("market_search_provider", old_provider)
        _set_flag("market_search_api_key", old_key)
        _set_flag("market_search_endpoint", old_endpoint)


def _role_headers(client, roles: list[str], *, username_prefix: str = "agent_user"):
    username = f"{username_prefix}_{uuid.uuid4().hex[:10]}"
    password = "secret123"
    legacy_role = "admin" if {"admin", "system_admin"} & set(roles) else "user"
    db = SessionLocal()
    try:
        user = User(username=username, hashed_password=get_password_hash(password), role=legacy_role, quota=20)
        db.add(user)
        db.flush()
        for role in roles:
            db.add(UserRole(user_id=user.id, role=role, created_by=None, note="agent phase1 test"))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200
    return username, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _response_data(response):
    return response.json()["data"]


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _seed_quote_job_with_missing_row(username: str) -> str:
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="succeeded",
                stage="completed",
                message="agent review test",
                request_summary="agent review test",
                result_json=json.dumps(
                    {
                        "project_details": [
                            {
                                "requirement_row_key": "Decor:10:0",
                                "source_sheet": "Decor",
                                "raw_row_index": 10,
                                "project_name": "wall paint",
                                "quantity": 12,
                                "unit": "m2",
                                "unit_price": 18,
                                "total_price": 216,
                                "cost_reference": {"matched": False},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.add_all(
            [
                QuoteRequirementRow(
                    quote_job_id=job_id,
                    requirement_row_key="Decor:10:0",
                    source_sheet="Decor",
                    raw_row_index=10,
                    item_name="wall paint",
                    quantity=12,
                    unit="m2",
                    sort_order=1,
                ),
                QuoteRequirementRow(
                    quote_job_id=job_id,
                    requirement_row_key="Decor:11:0",
                    source_sheet="Decor",
                    raw_row_index=11,
                    item_name="floor tile",
                    quantity=8,
                    unit="m2",
                    sort_order=2,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()
    return job_id


def _seed_quote_job_with_saving_suggestion(username: str, *, cost_item_id: int = 101) -> str:
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="succeeded",
                stage="completed",
                message="agent saving suggestion test",
                request_summary="agent saving suggestion test",
                result_total_amount=300,
                result_item_count=1,
                result_json=json.dumps(
                    {
                        "project_details": [
                            {
                                "requirement_row_key": "Decor:20:0",
                                "source_sheet": "Decor",
                                "raw_row_index": 20,
                                "project_name": "墙面乳胶漆",
                                "quantity": 10,
                                "unit": "m2",
                                "unit_price": 30,
                                "total_price": 300,
                                "notes": "常规施工",
                                "cost_reference": {
                                    "matched": True,
                                    "match_type": "exact_item_spec",
                                    "cost_item_id": cost_item_id,
                                    "item_name": "墙面乳胶漆",
                                    "spec": "常规",
                                    "unit": "m2",
                                    "reference_price": 20,
                                    "reference_price_source": "price",
                                    "reference_price_source_label": "主参考价",
                                    "ai_unit_price": 30,
                                    "price_delta": 10,
                                    "price_delta_rate": 0.5,
                                    "alternative_cost_items": [
                                        {
                                            "id": cost_item_id + 1,
                                            "item_name": "墙面乳胶漆经济型",
                                            "spec": "经济型",
                                            "unit": "m2",
                                            "reference_price": 18,
                                            "reference_price_source_label": "主参考价",
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            )
        )
        db.add(
            QuoteRequirementRow(
                quote_job_id=job_id,
                requirement_row_key="Decor:20:0",
                source_sheet="Decor",
                raw_row_index=20,
                item_name="墙面乳胶漆",
                quantity=10,
                unit="m2",
                sort_order=1,
            )
        )
        db.commit()
    finally:
        db.close()
    return job_id


def _seed_confirmed_quote_history(username: str, job_id: str, *, created_at: datetime) -> int:
    db = SessionLocal()
    try:
        history = QuoteHistory(
            username=username,
            quote_id=f"Q-{uuid.uuid4().hex[:8]}",
            quote_job_id=job_id,
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            confirmed_by=username,
            pushed_to_dingtalk=True,
            created_at=created_at,
            total_amount=300,
            item_count=1,
            payload_json=json.dumps({"quote_job_id": job_id, "total_amount": 300}, ensure_ascii=False),
        )
        db.add(history)
        db.commit()
        db.refresh(history)
        return history.id
    finally:
        db.close()


def test_market_price_web_search_uses_tavily_and_deepseek(monkeypatch):
    requests = []

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers=None, json=None):
            requests.append({"url": url, "headers": headers or {}, "json": json or {}})
            if "tavily" in url:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "title": "深圳墙面乳胶漆市场报价",
                                "url": "https://example.com/sz-paint",
                                "content": "深圳墙面乳胶漆人工材料综合单价约 22 元/m2。",
                                "score": 0.9,
                            }
                        ],
                        "response_time": 0.12,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": __import__("json").dumps(
                                    {
                                        "cities": {
                                            "深圳": {"price_range": {"min": 21, "max": 23}, "unit": "m2", "summary": "深圳约 21-23 元/m2"},
                                            "东莞": {"price_range": {"min": 19, "max": 21}, "unit": "m2", "summary": "东莞约 19-21 元/m2"},
                                        },
                                        "sources": [
                                            {
                                                "city": "深圳",
                                                "title": "深圳墙面乳胶漆市场报价",
                                                "url": "https://example.com/sz-paint",
                                                "price_text": "22 元/m2",
                                                "date": "2026-06-09",
                                            }
                                        ],
                                        "confidence": "medium",
                                        "explanation": "深圳公开报价低于本次下发价。",
                                    }
                                )
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr("app.services.agent_market_price_search.httpx.Client", FakeHttpClient)
    old_feature = _set_flag("feature_agent_market_web_search", True)
    old_provider = _set_flag("market_search_provider", "tavily")
    old_key = _set_flag("market_search_api_key", "test-tavily-key")
    old_endpoint = _set_flag("market_search_endpoint", "https://api.tavily.com/search")
    old_deepseek_key = _set_flag("deepseek_api_key", "test-deepseek-key")
    try:
        result = query_market_price_web_search(
            [
                {
                    "target_ref": "quote_job:test:line:1",
                    "target_label": "墙面乳胶漆",
                    "project_name": "墙面乳胶漆",
                    "unit": "m2",
                    "original_preview": {"cost_reference": {"item_name": "墙面乳胶漆", "spec": "常规", "unit": "m2"}},
                    "confirmed_quote": {"unit_price": 30},
                }
            ],
            audit_date="2026-06-09",
            username="agent-test",
            trace_id="trace-test",
        )
    finally:
        _set_flag("feature_agent_market_web_search", old_feature)
        _set_flag("market_search_provider", old_provider)
        _set_flag("market_search_api_key", old_key)
        _set_flag("market_search_endpoint", old_endpoint)
        _set_flag("deepseek_api_key", old_deepseek_key)

    assert result["tool"] == "market_price_web_search_v1"
    assert result["provider"] == "tavily"
    assert result["summary"]["status"] == "ok"
    assert result["summary"]["result_count"] == 1
    assert result["summary"]["deepseek_used"] is True
    assert result["items"][0]["confidence"] == "medium"
    assert result["items"][0]["sources"][0]["url"] == "https://example.com/sz-paint"
    tavily_calls = [item for item in requests if "tavily" in item["url"]]
    assert tavily_calls
    assert tavily_calls[0]["json"]["api_key"] == "test-tavily-key"


def test_agent_catalog_requires_feature_flag(client):
    _, headers = _role_headers(client, ["quote_operator"], username_prefix="agent_operator")
    old_value = _set_flag("feature_agent_assistants", False)
    try:
        response = client.get("/api/v1/admin/agents/catalog", headers=headers)
    finally:
        _set_flag("feature_agent_assistants", old_value)

    assert response.status_code == 403
    assert response.json()["detail"] == "FEATURE_DISABLED"


def test_quote_review_agent_run_persists_trace_and_findings(client):
    username, headers = _role_headers(client, ["staff"], username_prefix="agent_owner")
    job_id = _seed_quote_job_with_missing_row(username)
    old_value = _set_flag("feature_agent_assistants", True)
    old_llm_value = _set_flag("feature_agent_llm_explanation", True)
    try:
        response = client.post(
            "/api/v1/admin/agents/quote-review/runs",
            json={"quote_job_id": job_id},
            headers=headers,
        )
        assert response.status_code == 200
        data = _response_data(response)
        explanation = client.get(
            f"/api/v1/admin/agents/runs/{data['run_id']}/llm-explanation",
            headers=headers,
        )
    finally:
        _set_flag("feature_agent_assistants", old_value)
        _set_flag("feature_agent_llm_explanation", old_llm_value)

    assert data["agent_type"] == "quote_review_assistant"
    assert data["target_id"] == job_id
    assert data["status"] == "completed"
    assert data["risk_level"] == "high"
    assert data["recommendation"] == "post_audit_recorded"
    assert data["output"]["agent_engine"] == "rule_graph_v1"
    assert data["output"]["llm_mode"] == "disabled_by_default"
    assert data["output"]["audit_mode"] == "confirmed_quote_risk_audit"
    assert data["output"]["metrics"]["missing_count"] == 1
    assert len(data["tool_calls"]) == 6
    assert data["suggestions"] == []
    assert data["suggestion_events"] == []
    assert data["output"]["market_search_summary"]["status"] == "disabled"
    assert data["output"]["knowledge_sources"]["rag"] == "not_used"
    assert data["output"]["knowledge_sources"]["memory"] == "not_used"
    assert data["output"]["knowledge_sources"]["market_search_tool"] == "market_price_web_search_v1"
    assert {finding["finding_type"] for finding in data["findings"]} >= {
        "missing_requirement_rows",
        "missing_requirement_row",
        "no_cost_reference",
    }
    row_finding = next(item for item in data["findings"] if item["finding_type"] == "missing_requirement_row")
    assert row_finding["target_label"]
    assert row_finding["target_label"] != row_finding["target_ref"]
    assert explanation.status_code == 200, explanation.text
    explanation_data = _response_data(explanation)
    assert explanation_data["read_only"] is True
    assert explanation_data["mode"] == "rule_based_fallback"
    assert explanation_data["risk_explanations"]
    assert "guardrails" not in explanation_data

    db = SessionLocal()
    try:
        stored_run = db.query(AgentRun).filter(AgentRun.run_id == data["run_id"]).one()
        assert stored_run.status == "completed"
        assert stored_run.target_id == job_id
        assert db.query(AgentToolCall).filter(AgentToolCall.run_id == data["run_id"]).count() == 6
        assert db.query(AgentFinding).filter(AgentFinding.run_id == data["run_id"]).count() >= 3
        assert db.query(AgentSuggestion).filter(AgentSuggestion.run_id == data["run_id"]).count() == 0
    finally:
        db.close()


def test_manual_quote_audit_requires_and_binds_pushed_history(client):
    username, headers = _role_headers(client, ["quote_operator"], username_prefix="agent_manual_audit")
    job_id = _seed_quote_job_with_saving_suggestion(username)
    old_value = _set_flag("feature_agent_assistants", True)
    try:
        missing_history = client.post(
            "/api/v1/admin/agents/quote-review/runs",
            json={"quote_job_id": job_id, "confirmed_only": True},
            headers=headers,
        )
        assert missing_history.status_code == 409, missing_history.text
        assert missing_history.json()["detail"] == "QUOTE_NOT_PUSHED"

        history_id = _seed_confirmed_quote_history(username, job_id, created_at=datetime(2038, 1, 2, 10, 30, 0))
        response = client.post(
            "/api/v1/admin/agents/quote-review/runs",
            json={"quote_job_id": job_id, "quote_history_id": history_id, "confirmed_only": True},
            headers=headers,
        )
    finally:
        _set_flag("feature_agent_assistants", old_value)

    assert response.status_code == 200, response.text
    data = _response_data(response)
    assert data["quote_history_id"] == history_id
    assert data["trigger_source"] == "manual_audit"
    assert data["trigger_ref_type"] == "quote_history"
    assert data["trigger_ref_id"] == str(history_id)
    assert data["status"] == "completed"


def test_quote_review_agent_no_longer_creates_suggestion_loop(client):
    username, headers = _role_headers(client, ["staff"], username_prefix="agent_saving")
    job_id = _seed_quote_job_with_saving_suggestion(username)
    old_value = _set_flag("feature_agent_assistants", True)
    old_llm_value = _set_flag("feature_agent_llm_explanation", True)
    try:
        response = client.post(
            "/api/v1/admin/agents/quote-review/runs",
            json={"quote_job_id": job_id},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = _response_data(response)
        saving = data["output"]["saving_summary"]
        assert data["output"]["audit_mode"] == "confirmed_quote_risk_audit"
        assert data["recommendation"] == "post_audit_recorded"
        assert data["suggestions"] == []
        assert data["suggestion_events"] == []
        assert saving["estimated_total_saving_amount"] == 0
        explanation = client.get(
            f"/api/v1/admin/agents/runs/{data['run_id']}/llm-explanation",
            headers=headers,
        )
        assert explanation.status_code == 200, explanation.text
        explanation_data = _response_data(explanation)
        assert explanation_data["saving_explanation"]["estimated_total_saving_amount"] == 0
        assert explanation_data["suggestion_priorities"] == []
        assert explanation_data["manual_handling"][0].startswith("本次为已下发报价的事后审计")

        decision = client.post(
            f"/api/v1/admin/agents/suggestions/{uuid.uuid4()}/decision",
            json={"decision": "approve", "note": "采纳调价建议"},
            headers=headers,
        )
        assert decision.status_code == 410, decision.text
        assert decision.json()["detail"] == "AGENT_SUGGESTION_LOOP_DISABLED"

        executed = client.post(
            f"/api/v1/admin/agents/suggestions/{uuid.uuid4()}/execute",
            json={"note": "生成草案"},
            headers=headers,
        )
        assert executed.status_code == 410, executed.text

        final = client.post(
            f"/api/v1/admin/agents/suggestions/{uuid.uuid4()}/final-confirm",
            json={"accepted_agent_result": True, "final_result": {"operator_checked": True}, "note": "人工终确认"},
            headers=headers,
        )
        assert final.status_code == 410, final.text
    finally:
        _set_flag("feature_agent_assistants", old_value)
        _set_flag("feature_agent_llm_explanation", old_llm_value)

    db = SessionLocal()
    try:
        assert db.query(AgentSuggestion).filter(AgentSuggestion.target_id == job_id).count() == 0
        assert db.query(AgentSuggestionEvent).filter(AgentSuggestionEvent.run_id == data["run_id"]).count() == 0
    finally:
        db.close()


def test_quote_review_agent_llm_explanation_uses_deepseek_when_configured(client, monkeypatch):
    calls = []

    async def fake_post_json_via_gateway(**kwargs):
        calls.append(kwargs)
        user_payload = json.loads(kwargs["json_payload"]["messages"][1]["content"])
        assert user_payload["suggestions"] == []
        assert user_payload["agent_output"]["audit_records"]
        first_record = user_payload["agent_output"]["audit_records"][0]
        content = json.dumps(
            {
                "headline": "DeepSeek 已基于后审计结果生成解释。",
                "business_summary": "这张报价单的核心问题是已下发价格与预审风险之间是否有足够留痕。",
                "review_focus": ["先看修改前后记录", "再核对无成本库参考的报价行"],
                "risk_explanations": [
                    {
                        "severity": "medium",
                        "title": "DeepSeek 风险解释",
                        "explanation": "需要人工关注价格依据。",
                        "evidence_ref": "预审第 1 行",
                        "handling_advice": "先核对成本依据。",
                    }
                ],
                "before_after_explanations": [
                    {
                        "target_label": first_record["target_label"],
                        "original_risk": "已命中成本库但偏离底价过大",
                        "confirmed_state": "已下发，保留审计",
                        "explanation": "DeepSeek 说明：下发后只做审计解释，不生成二次确认。",
                        "manual_modified": False,
                    }
                ],
                "suggestion_priorities": [],
                "saving_opportunities": [],
                "saving_explanation": {"text": "后审计不生成省钱建议。"},
                "decision_checklist": [],
                "manual_handling": ["查看后审计留痕即可。"],
                "uncertainties": ["替代项仍需人工核对。"],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        "app.services.agent_llm_explanation.post_json_via_gateway",
        fake_post_json_via_gateway,
    )
    username, headers = _role_headers(client, ["staff"], username_prefix="agent_deepseek")
    job_id = _seed_quote_job_with_saving_suggestion(username)
    old_agent_value = _set_flag("feature_agent_assistants", True)
    old_llm_value = _set_flag("feature_agent_llm_explanation", True)
    old_provider = _set_flag("agent_llm_provider", "deepseek")
    old_key = _set_flag("deepseek_api_key", "test-deepseek-key")
    old_model = _set_flag("deepseek_model", "deepseek-chat-test")
    try:
        response = client.post(
            "/api/v1/admin/agents/quote-review/runs",
            json={"quote_job_id": job_id},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = _response_data(response)

        explanation_url = f"/api/v1/admin/agents/runs/{data['run_id']}/llm-explanation"
        default_explanation = client.get(
            explanation_url,
            headers=headers,
        )
        explanation = client.get(
            explanation_url,
            params={"mode": "deepseek"},
            headers=headers,
        )
    finally:
        _set_flag("feature_agent_assistants", old_agent_value)
        _set_flag("feature_agent_llm_explanation", old_llm_value)
        _set_flag("agent_llm_provider", old_provider)
        _set_flag("deepseek_api_key", old_key)
        _set_flag("deepseek_model", old_model)

    assert default_explanation.status_code == 200, default_explanation.text
    assert _response_data(default_explanation)["mode"] == "rule_based_fallback"
    assert explanation.status_code == 200, explanation.text
    explanation_data = _response_data(explanation)
    assert explanation_data["mode"] == "deepseek"
    assert explanation_data["llm_provider"] == "deepseek"
    assert explanation_data["llm_model"] == "deepseek-chat-test"
    assert explanation_data["headline"] == "DeepSeek 已基于后审计结果生成解释。"
    assert "已下发价格" in explanation_data["business_summary"]
    assert explanation_data["review_focus"]
    assert explanation_data["decision_checklist"] == []
    assert explanation_data["suggestion_priorities"] == []
    assert explanation_data["saving_explanation"]["estimated_total_saving_amount"] == 0
    assert explanation_data["before_after_explanations"][0]["explanation"].startswith("DeepSeek 说明")
    assert len(calls) == 1
    assert calls[0]["provider"] == "deepseek"
    assert calls[0]["model"] == "deepseek-chat-test"
    assert calls[0]["endpoint_type"] == "agent_quote_review_explanation"
    assert calls[0]["json_payload"]["response_format"] == {"type": "json_object"}
    assert "agent_output" in json.loads(calls[0]["json_payload"]["messages"][1]["content"])


def test_daily_quote_review_scans_confirmed_history_and_deduplicates(client, monkeypatch):
    username, headers = _role_headers(client, ["quote_operator"], username_prefix="agent_daily")
    cost_item_id = int(uuid.uuid4().hex[:6], 16)
    job_id = _seed_quote_job_with_saving_suggestion(username, cost_item_id=cost_item_id)
    review_day = datetime(2036, 1, 1, 10, 30, 0) + timedelta(days=int(uuid.uuid4().hex[:4], 16))
    history_id = _seed_confirmed_quote_history(username, job_id, created_at=review_day)
    review_date = review_day.date().isoformat()
    fake_search_calls = []

    def fake_market_search(audit_records, *, audit_date=None, username=None, trace_id=None):
        fake_search_calls.append(
            {"audit_record_count": len(audit_records), "audit_date": audit_date, "username": username, "trace_id": trace_id}
        )
        target_ref = audit_records[0]["target_ref"]
        item = {
            "target_ref": target_ref,
            "target_label": audit_records[0]["target_label"],
            "item_name": "墙面乳胶漆",
            "spec": "常规",
            "unit": "m2",
            "confirmed_unit_price": 30,
            "query_date": review_day.date().isoformat(),
            "provider": "bing",
            "cities": {
                "东莞": {"price_range": {"min": 19.5, "max": 20.5}, "unit": "m2", "summary": "东莞公开报价约 19.5-20.5 元/m2"},
                "深圳": {"price_range": {"min": 21.2, "max": 23.0}, "unit": "m2", "summary": "深圳公开报价约 21.2-23.0 元/m2"},
            },
            "sources": [
                {"city": "东莞", "title": "东莞墙面乳胶漆报价", "url": "https://example.com/dg", "price_text": "19.5 元/m2", "date": review_day.date().isoformat()},
                {"city": "深圳", "title": "深圳墙面乳胶漆报价", "url": "https://example.com/sz", "price_text": "21.2 元/m2", "date": review_day.date().isoformat()},
            ],
            "confidence": "medium",
            "explanation": "东莞/深圳公开网页报价低于本次下发单价，需结合来源可信度理解。",
            "llm_mode": "deepseek",
        }
        return {
            "tool": "market_price_web_search_v1",
            "scope": "quote_audit.live_web_market_reference",
            "provider": "bing",
            "query_date": review_day.date().isoformat(),
            "cities": ["东莞", "深圳"],
            "summary": {
                "status": "ok",
                "searched_line_count": 1,
                "covered_line_count": 1,
                "result_count": 2,
                "max_results_per_city": 5,
                "deepseek_used": True,
                "snapshot_only": True,
            },
            "items": [item],
            "by_target_ref": {target_ref: item},
        }

    monkeypatch.setattr("app.services.agent_quote_review.query_market_price_web_search", fake_market_search)
    old_agent_value = _set_flag("feature_agent_assistants", True)
    old_daily_value = _set_flag("feature_agent_daily_review", True)
    try:
        response = client.post(
            "/api/v1/admin/agents/quote-review/daily-runs",
            json={"review_date": review_date},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = _response_data(response)
        assert data["candidate_count"] == 1
        assert data["created_run_count"] == 1
        assert data["skipped_duplicate_count"] == 0
        assert data["runs"][0]["trigger_source"] == "scheduled_daily"
        assert data["runs"][0]["trigger_ref_type"] == "quote_history"
        assert data["runs"][0]["trigger_ref_id"] == str(history_id)
        assert data["runs"][0]["output"]["audit_mode"] == "confirmed_quote_risk_audit"
        assert data["runs"][0]["output"]["audit_summary"]["audit_record_count"] >= 1
        assert data["runs"][0]["output"]["audit_summary"]["market_search_result_count"] == 2
        assert data["runs"][0]["output"]["market_search_summary"]["result_count"] == 2
        assert data["runs"][0]["output"]["market_search_summary"]["deepseek_used"] is True
        assert "联网市场价参考" in data["runs"][0]["output"]["audit_records"][0]["market_search_explanation"]

        duplicate = client.post(
            "/api/v1/admin/agents/quote-review/daily-runs",
            json={"review_date": review_date},
            headers=headers,
        )
        assert duplicate.status_code == 200, duplicate.text
        duplicate_data = _response_data(duplicate)
        assert duplicate_data["candidate_count"] == 1
        assert duplicate_data["created_run_count"] == 0
        assert duplicate_data["skipped_duplicate_count"] == 1

        summary = client.get(
            "/api/v1/admin/agents/quote-review/daily-summary",
            params={"review_date": review_date},
            headers=headers,
        )
        assert summary.status_code == 200, summary.text
        summary_data = _response_data(summary)
        assert summary_data["candidate_count"] == 1
        assert summary_data["run_count"] == 1
        assert summary_data["audit_record_count"] >= 1
        assert summary_data["audit_manual_modified_count"] == 0
        assert summary_data["audit_market_search_result_count"] == 2
        assert summary_data["audit_market_search_covered_line_count"] == 1
        assert summary_data["open_suggestion_count"] == 0
        assert summary_data["open_estimated_saving_amount"] == 0

        pending = client.get(
            "/api/v1/admin/agents/suggestions/pending",
            params={"review_date": review_date, "status": "open"},
            headers=headers,
        )
        assert pending.status_code == 200, pending.text
        pending_rows = _response_data(pending)
        assert pending_rows == []
    finally:
        _set_flag("feature_agent_assistants", old_agent_value)
        _set_flag("feature_agent_daily_review", old_daily_value)

    db = SessionLocal()
    try:
        stored_run = (
            db.query(AgentRun)
            .filter(
                AgentRun.trigger_source == "scheduled_daily",
                AgentRun.trigger_ref_type == "quote_history",
                AgentRun.trigger_ref_id == str(history_id),
            )
            .one()
        )
        assert stored_run.target_id == job_id
        assert db.query(AgentSuggestion).filter(AgentSuggestion.run_id == stored_run.run_id).count() == 0
        output = json.loads(stored_run.output_json)
        assert output["audit_mode"] == "confirmed_quote_risk_audit"
        assert output["audit_records"]
        assert output["market_search_summary"]["result_count"] == 2
        assert (
            db.query(AgentToolCall)
            .filter(
                AgentToolCall.run_id == stored_run.run_id,
                AgentToolCall.tool_name == "market_price_web_search",
            )
            .count()
            == 1
        )
    finally:
        db.close()
    assert len(fake_search_calls) == 1


def test_daily_quote_review_scheduler_runs_once_and_exposes_status(client):
    username, headers = _role_headers(client, ["quote_operator"], username_prefix="agent_scheduler")
    job_id = _seed_quote_job_with_saving_suggestion(username)
    review_day = datetime(2037, 1, 1, 10, 30, 0) + timedelta(days=int(uuid.uuid4().hex[:4], 16))
    history_id = _seed_confirmed_quote_history(username, job_id, created_at=review_day)
    review_date = review_day.date().isoformat()
    old_agent_value = _set_flag("feature_agent_assistants", True)
    old_daily_value = _set_flag("feature_agent_daily_review", True)
    old_run_time = _set_flag("agent_daily_review_run_time", "18:30")
    old_catchup = _set_flag("agent_daily_review_catchup_minutes", 120)
    try:
        db = SessionLocal()
        try:
            scheduler_now = review_day.replace(
                hour=18,
                minute=31,
                second=0,
                microsecond=0,
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
            first = run_due_quote_review_scheduler_once(
                db,
                now=scheduler_now,
            )
            assert first["status"] == "success"
            assert first["executed"] is True
            assert first["run"]["candidate_count"] == 1
            assert first["run"]["created_run_count"] == 1

            second = run_due_quote_review_scheduler_once(
                db,
                now=scheduler_now.replace(minute=32),
            )
            assert second["status"] == "success"
            assert second["executed"] is False
            assert second["skip_reason"] == "already_success"
            assert (
                db.query(AgentSchedulerRun)
                .filter(
                    AgentSchedulerRun.run_date == review_day.date(),
                    AgentSchedulerRun.triggered_by == "system_scheduler",
                )
                .count()
                == 1
            )
        finally:
            db.close()

        status = client.get(
            "/api/v1/admin/agents/quote-review/scheduler-runs",
            params={"review_date": review_date},
            headers=headers,
        )
        assert status.status_code == 200, status.text
        status_data = _response_data(status)
        assert status_data["status"] == "success"
        assert status_data["run"]["created_run_count"] == 1
        assert status_data["next_action"] == "check_result"

        history = client.get(
            "/api/v1/admin/agents/quote-review/scheduler-runs/history",
            params={"date_from": review_date, "date_to": review_date},
            headers=headers,
        )
        assert history.status_code == 200, history.text
        history_rows = _response_data(history)
        assert history.json()["total"] == 1
        assert history_rows[0]["run_date"] == review_date
        assert history_rows[0]["daily_summary"]["run_count"] == 1
        assert history_rows[0]["daily_summary"]["audit_record_count"] >= 1
        assert history_rows[0]["daily_summary"]["open_suggestion_count"] == 0
        assert history_rows[0]["daily_summary"]["open_estimated_saving_amount"] == 0
        assert history_rows[0]["manual_rescan_available"] is False

        todos = client.get(
            "/api/v1/admin/agents/quote-review/todos",
            params={"review_date": review_date},
            headers=headers,
        )
        assert todos.status_code == 200, todos.text
        todo_data = _response_data(todos)
        assert todo_data["status"] == "clear"
        assert todo_data["todo_count"] == 0
        assert todo_data["primary_action"] == "none"
        assert todo_data["todos"] == []
        assert todo_data["metrics"]["audit_record_count"] >= 1
        assert todo_data["metrics"]["open_suggestion_count"] == 0

        closure = client.get(
            "/api/v1/admin/agents/quote-review/closure-summary",
            params={"date_from": review_date, "date_to": review_date},
            headers=headers,
        )
        assert closure.status_code == 200, closure.text
        closure_data = _response_data(closure)
        assert closure_data["metrics"]["open_count"] == 0
        assert closure_data["metrics"]["handled_count"] == 0
        assert closure_data["metrics"]["confirmed_saving_amount"] == 0

        pending = client.get(
            "/api/v1/admin/agents/suggestions/pending",
            params={"review_date": review_date, "status": "open"},
            headers=headers,
        )
        assert pending.status_code == 200, pending.text
        assert _response_data(pending) == []
        db_check = SessionLocal()
        try:
            scheduled_run = (
                db_check.query(AgentRun)
                .filter(
                    AgentRun.trigger_source == "scheduled_daily",
                    AgentRun.trigger_ref_type == "quote_history",
                    AgentRun.trigger_ref_id == str(history_id),
                    AgentRun.target_id == job_id,
                )
                .one()
            )
            assert db_check.query(AgentSuggestion).filter(AgentSuggestion.run_id == scheduled_run.run_id).count() == 0
            assert json.loads(scheduled_run.output_json)["audit_records"]
        finally:
            db_check.close()
        return
        approve = client.post(
            f"/api/v1/admin/agents/suggestions/{suggestion_id}/decision",
            json={"decision": "approve", "note": "采纳调价建议"},
            headers=headers,
        )
        assert approve.status_code == 200, approve.text
        execute = client.post(
            f"/api/v1/admin/agents/suggestions/{suggestion_id}/execute",
            json={"note": "生成执行草案"},
            headers=headers,
        )
        assert execute.status_code == 200, execute.text
        final = client.post(
            f"/api/v1/admin/agents/suggestions/{suggestion_id}/final-confirm",
            json={"accepted_agent_result": True, "final_result": {"accepted": True}, "note": "确认节省"},
            headers=headers,
        )
        assert final.status_code == 200, final.text

        closed = client.get(
            "/api/v1/admin/agents/quote-review/closure-summary",
            params={"date_from": review_date, "date_to": review_date},
            headers=headers,
        )
        closed_data = _response_data(closed)
        assert closed_data["metrics"]["handled_count"] >= 1
        assert closed_data["metrics"]["final_confirmed_count"] >= 1
        assert closed_data["metrics"]["confirmed_saving_amount"] >= 100
    finally:
        _set_flag("feature_agent_assistants", old_agent_value)
        _set_flag("feature_agent_daily_review", old_daily_value)
        _set_flag("agent_daily_review_run_time", old_run_time)
        _set_flag("agent_daily_review_catchup_minutes", old_catchup)

    db = SessionLocal()
    try:
        assert (
            db.query(AgentRun)
            .filter(
                AgentRun.trigger_source == "scheduled_daily",
                AgentRun.trigger_ref_type == "quote_history",
                AgentRun.trigger_ref_id == str(history_id),
                AgentRun.target_id == job_id,
            )
            .count()
            == 1
        )
    finally:
        db.close()


def test_quote_review_agent_hides_other_users_quote_jobs(client):
    owner_username, _ = _role_headers(client, ["staff"], username_prefix="agent_owner")
    _, other_headers = _role_headers(client, ["staff"], username_prefix="agent_other")
    job_id = _seed_quote_job_with_missing_row(owner_username)
    old_value = _set_flag("feature_agent_assistants", True)
    try:
        response = client.post(
            "/api/v1/admin/agents/quote-review/runs",
            json={"quote_job_id": job_id},
            headers=other_headers,
        )
    finally:
        _set_flag("feature_agent_assistants", old_value)

    assert response.status_code == 404
    assert response.json()["detail"] == "QUOTE_JOB_NOT_FOUND"
