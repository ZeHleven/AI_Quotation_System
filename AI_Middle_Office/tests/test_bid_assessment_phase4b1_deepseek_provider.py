from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.config import settings
from app.services.bid_mvp1_local_lab import build_local_model_profile_payload
from app.services.bid_mvp1_model_provider import (
    BidMvp1ModelProviderConfigurationError,
    ControlledChatCompletionsProvider,
    _normalize_control_plane_fields,
    _rfc3339_utc,
    deepseek_v4_flash_cost_microunits,
)


def _provider(**overrides: Any) -> ControlledChatCompletionsProvider:
    values = {
        "session_factory": lambda: None,
        "provider_ref": "deepseek",
        "model_ref": "deepseek-v4-flash",
        "api_key": "test-secret-key",
        "chat_url": "https://api.deepseek.com/chat/completions",
        "thinking_mode": "disabled",
        "timeout_seconds": 120,
    }
    values.update(overrides)
    provider = ControlledChatCompletionsProvider(**values)
    provider._prompt_context = lambda _envelope: {  # type: ignore[method-assign]
        "runtime_tools": ["evidence.search", "evidence.read"],
        "allowed_fact_slots": ["project.name"],
        "retrieval_guidance": {
            "primary_query": "项目概况",
            "field_aliases": ["项目概况", "工程概况"],
        },
    }
    return provider


def test_deepseek_v4_flash_cost_uses_cache_breakdown_and_conservative_fallback() -> None:
    assert deepseek_v4_flash_cost_microunits(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 25,
            "prompt_cache_miss_tokens": 75,
        }
    ) == 17
    assert deepseek_v4_flash_cost_microunits(
        {"prompt_tokens": 100, "completion_tokens": 20}
    ) == 20


def test_deepseek_context_time_is_always_rfc3339_utc() -> None:
    assert _rfc3339_utc(datetime(2026, 8, 16, 6, 0, 20)) == (
        "2026-08-16T06:00:20Z"
    )
    assert _rfc3339_utc(
        datetime(2026, 8, 16, 14, 0, 20, tzinfo=timezone(timedelta(hours=8)))
    ) == "2026-08-16T06:00:20Z"


def test_tool_call_id_is_gateway_owned_stable_and_ascii() -> None:
    action = {
        "action_type": "request_tool",
        "tool_call_id": "搜索项目概况",
        "tool_name": "evidence.search",
        "arguments": {
            "query": "项目概况",
            "top_k": 8,
            "field_aliases": ["项目概况", "工程概况"],
        },
        "reason_codes": ["NEED_EVIDENCE"],
    }
    envelope = {"model_call_id": "call-1", "action_seq": 1}
    first = _normalize_control_plane_fields(action, envelope)
    replay = _normalize_control_plane_fields(action, envelope)
    assert first == replay
    assert first["tool_call_id"].startswith("tc_")
    assert first["tool_call_id"].isascii()
    assert len(first["tool_call_id"]) == 35
    assert first["arguments"] == {"query": "项目概况", "top_k": 8}
    assert action["tool_call_id"] == "搜索项目概况"
    assert "field_aliases" in action["arguments"]


def test_cny_money_is_losslessly_canonicalized_without_guessing() -> None:
    action = {
        "action_type": "submit_fact_candidates",
        "candidates": [
            {
                "fact_slot": "tender.guarantee.requirements",
                "value_type": "money",
                "value": {"amount": 10000, "currency": "CNY"},
            },
            {
                "fact_slot": "tender.fees.requirements",
                "value_type": "money",
                "value": {"amount": "壹万元", "currency": "CNY"},
            },
        ],
    }
    normalized = _normalize_control_plane_fields(
        action,
        {"model_call_id": "call-1", "action_seq": 3},
    )
    assert normalized["candidates"][0]["value"]["amount"] == "10000.0000"
    assert normalized["candidates"][1]["value"]["amount"] == "壹万元"
    assert action["candidates"][0]["value"]["amount"] == 10000


def test_gateway_drops_uncitable_fact_candidate_but_keeps_atom_backed_candidate() -> None:
    action = {
        "action_type": "submit_fact_candidates",
        "candidates": [
            {
                "fact_slot": "tender.submission.deadline",
                "value_type": "date",
                "value": "2026-04-07",
                "scope": {"type": "lot", "id": "lot-1"},
                "source_type": "document",
                "evidence_ids": ["child-search-only"],
                "confidence": "low",
                "asserted_at": "2026-08-17T08:26:52Z",
            },
            {
                "fact_slot": "tender.opening.datetime",
                "value_type": "text",
                "value": "未在已检索文档中明确开标时间",
                "scope": {"type": "lot", "id": "lot-1"},
                "source_type": "document",
                "evidence_ids": ["atom-context-read"],
                "confidence": "low",
                "asserted_at": "2026-08-17T08:26:52Z",
            },
        ],
        "reason_codes": ["FACT_CANDIDATES_SUBMITTED"],
    }

    normalized = _normalize_control_plane_fields(
        action,
        {"model_call_id": "call-citation-filter", "action_seq": 5},
        citable_evidence_ids={"atom-context-read"},
    )

    assert normalized["action_type"] == "submit_fact_candidates"
    assert normalized["candidates"] == [action["candidates"][1]]
    assert normalized["reason_codes"] == [
        "FACT_CANDIDATES_SUBMITTED",
        "GATEWAY_DROPPED_UNCITABLE_FACT_CANDIDATE",
    ]
    assert len(action["candidates"]) == 2


def test_gateway_finishes_safely_when_all_fact_candidates_are_uncitable() -> None:
    normalized = _normalize_control_plane_fields(
        {
            "action_type": "submit_fact_candidates",
            "candidates": [
                {
                    "fact_slot": "tender.submission.deadline",
                    "evidence_ids": ["child-search-only"],
                }
            ],
            "reason_codes": ["FACT_CANDIDATES_SUBMITTED"],
        },
        {"model_call_id": "call-citation-filter-empty", "action_seq": 5},
        citable_evidence_ids={"atom-context-read"},
    )

    assert normalized["action_type"] == "finish"
    assert normalized["output_candidate"] is None
    assert normalized["reason_codes"] == [
        "FACT_CANDIDATES_SUBMITTED",
        "GATEWAY_DROPPED_UNCITABLE_FACT_CANDIDATE",
        "EVIDENCE_INSUFFICIENT",
    ]


@pytest.mark.parametrize(
    "action_type", ["submit_fact_candidates", "submit_claim_candidates"]
)
def test_empty_candidate_submission_is_normalized_to_evidence_insufficient_finish(
    action_type: str,
) -> None:
    normalized = _normalize_control_plane_fields(
        {"action_type": action_type, "candidates": [], "reason_codes": []},
        {"model_call_id": "call-empty", "action_seq": 5},
    )

    assert normalized == {
        "action_type": "finish",
        "completion_summary": (
            "No governed candidate is supported by the available evidence."
        ),
        "output_candidate": None,
        "reason_codes": ["EVIDENCE_INSUFFICIENT"],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_ref", "other"),
        ("model_ref", "deepseek-v4-pro"),
        ("chat_url", "https://example.com/chat/completions"),
        ("chat_url", "http://api.deepseek.com/chat/completions"),
        ("thinking_mode", "enabled"),
        ("api_key", ""),
    ],
)
def test_deepseek_provider_configuration_fails_closed(field: str, value: str) -> None:
    with pytest.raises(BidMvp1ModelProviderConfigurationError):
        _provider(**{field: value})


def test_deepseek_provider_sends_governed_json_request_and_records_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "id": "deepseek-receipt-1",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"action_type":"finish","summary":"done"}',
                            "reasoning_content": "must-not-be-persisted",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 25,
                    "prompt_cache_miss_tokens": 75,
                },
            }

    def _post(url: str, **kwargs: Any) -> _Response:
        captured.update({"url": url, **kwargs})
        return _Response()

    monkeypatch.setattr("app.services.bid_mvp1_model_provider.httpx.post", _post)
    result = _provider().execute(
        request_envelope={
            "provider_ref": "deepseek",
            "model_ref": "deepseek-v4-flash",
            "context_manifest_id": "ctx-1",
            "context_manifest_hash": "a" * 64,
            "task_id": "task-1",
            "run_id": "run-1",
            "action_seq": 1,
            "logical_role": "local_research",
            "input_token_limit": 8000,
            "output_token_limit": 2000,
        },
        provider_request_id="bid-model:call-1:attempt:1",
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-secret-key"
    assert captured["headers"]["X-Idempotency-Key"] == (
        "bid-model:call-1:attempt:1"
    )
    payload = captured["json"]
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    assert '"amount":"10000.0000"' in payload["messages"][0]["content"]
    assert "test-secret-key" not in str(payload)
    user_payload = json.loads(payload["messages"][1]["content"])
    output_contract = user_payload["output_contract"]
    assert output_contract["schema_id"] == "bid.task.action.v1"
    assert output_contract["exclusive_action_branch"] is True
    assert output_contract["omit_unselected_branch_fields"] is True
    assert output_contract["finish_example"] == {
        "action_type": "finish",
        "completion_summary": "No governed action is required.",
        "output_candidate": None,
        "reason_codes": ["NO_ACTION_REQUIRED"],
    }
    assert output_contract["json_schema"]["discriminator"]["propertyName"] == (
        "action_type"
    )
    request_tool_schema = output_contract["json_schema"]["$defs"][
        "RequestToolAction"
    ]
    assert request_tool_schema["properties"]["tool_name"]["enum"] == [
        "evidence.read",
        "evidence.search",
    ]
    assert len(request_tool_schema["allOf"]) == 2
    tool_contracts = user_payload["tool_argument_contracts"]
    assert set(tool_contracts) == {"evidence.read", "evidence.search"}
    search_contract = tool_contracts["evidence.search"]
    assert search_contract["required"] == ["query"]
    assert search_contract["additionalProperties"] is False
    assert "assessment_id" not in search_contract["properties"]
    assert "lot_id" not in search_contract["properties"]
    assert search_contract["properties"]["top_k"]["maximum"] == 8
    read_contract = tool_contracts["evidence.read"]
    assert set(read_contract["required"]) == {"evidence_ids", "expansion"}
    assert read_contract["additionalProperties"] is False
    assert result.action == {"action_type": "finish", "summary": "done"}
    assert result.usage == {"input_tokens": 100, "output_tokens": 20}
    assert result.actual_cost_microunits == 17
    assert result.provider_receipt_id == "deepseek-receipt-1"


def test_deepseek_provider_rejects_model_not_frozen_in_envelope() -> None:
    with pytest.raises(
        BidMvp1ModelProviderConfigurationError,
        match="BID_MVP1_MODEL_REF_NOT_ALLOWED",
    ):
        _provider().execute(
            request_envelope={
                "provider_ref": "deepseek",
                "model_ref": "deepseek-v4-pro",
            },
            provider_request_id="bid-model:call-1:attempt:1",
        )


def test_local_lab_deepseek_profile_freezes_all_logical_roles() -> None:
    previous = settings.bid_mvp1_local_model_mode
    object.__setattr__(settings, "bid_mvp1_local_model_mode", "deepseek-v4-flash")
    try:
        routes, providers, models = build_local_model_profile_payload()
    finally:
        object.__setattr__(settings, "bid_mvp1_local_model_mode", previous)

    assert set(routes) == {
        "local_research",
        "synthesizer",
        "evidence_validator",
        "report_writer",
    }
    assert all(route["provider_ref"] == "deepseek" for route in routes.values())
    assert all(
        route["model_ref"] == "deepseek-v4-flash" for route in routes.values()
    )
    assert all(
        route["reserved_cost_microunits"] == 100_000
        for route in routes.values()
    )
    assert all(route["max_attempts"] == 3 for route in routes.values())
    assert all(route["timeout_seconds"] == 180 for route in routes.values())
    assert providers["deepseek"]["thinking_mode"] == "disabled"
    assert models["deepseek-v4-flash"]["provider_ref"] == "deepseek"
