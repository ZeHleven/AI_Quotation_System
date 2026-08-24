from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_local.contracts import (
    normalize_local_agent_state,
    normalize_task_action,
)
from app.agents.bid_assessment_local.graph import run_bounded_transition
from app.models.bid_model_execution import (
    MODEL_CALL_ATTEMPT_STATES,
    MODEL_CALL_STATES,
    MODEL_REPLAY_POLICIES,
)


def _state(*, outstanding: str | None = None) -> dict:
    return {
        "schema": "bid.local_agent.state.v1",
        "run_id": "run_01",
        "task_id": "task_01",
        "task_attempt_id": "attempt_01",
        "fencing_token": 1,
        "task_contract_hash": "a" * 64,
        "skill_binding_hash": "b" * 64,
        "phase": "hydrate",
        "action_seq": 0,
        "observed_model_result_refs": [],
        "observed_tool_result_refs": [],
        "candidate_refs": [],
        "missing_slots": [],
        "outstanding_operation_ref": outstanding,
        "stop_reason": None,
    }


def test_bounded_langgraph_requests_exactly_one_model_action() -> None:
    result = run_bounded_transition(
        local_state=_state(),
        proposed_action=None,
        allowed_tools=["documents.outline"],
    )
    assert result == {"operation_type": "request_model"}


def test_bounded_langgraph_routes_one_closed_action_and_yields() -> None:
    action = {
        "action_type": "request_tool",
        "tool_call_id": "tc_model_01",
        "tool_name": "documents.outline",
        "arguments": {"document_version_id": "version_01"},
        "reason_codes": ["NEED_DOCUMENT_STRUCTURE"],
    }
    result = run_bounded_transition(
        local_state=_state(),
        proposed_action=action,
        allowed_tools=["documents.outline"],
    )
    assert result == {"operation_type": "request_tool", "action": action}
    assert "next_operation" not in result


def test_model_action_rejects_tool_bypass_and_hidden_reasoning() -> None:
    with pytest.raises(ValueError, match="BID_MODEL_ACTION_TOOL_NOT_ALLOWED"):
        normalize_task_action(
            {
                "action_type": "request_tool",
                "tool_call_id": "tc_model_01",
                "tool_name": "web.search",
                "arguments": {},
                "reason_codes": ["UNAUTHORIZED_TOOL"],
            },
            allowed_tools={"documents.outline"},
        )
    with pytest.raises(ValidationError):
        normalize_task_action(
            {
                "action_type": "finish",
                "completion_summary": "done",
                "output_candidate": None,
                "reason_codes": ["duplicate", "duplicate"],
            }
        )
    with pytest.raises(ValidationError):
        normalize_task_action(
            {
                "action_type": "submit_fact_candidates",
                "candidates": [
                    {
                        "fact_slot": "bid_guarantee",
                        "value": {"amount": "100.00", "currency": "CNY"},
                        "value_type": "money",
                        "scope": {"type": "assessment", "id": "assessment_01"},
                        "source_type": "document",
                        "evidence_ids": ["evidence_01"],
                        "confidence": "high",
                        "asserted_at": "2026-08-13T12:00:00Z",
                    }
                ],
                "reason_codes": ["FACT_FOUND"],
            }
        )
    with pytest.raises(ValidationError):
        normalize_task_action(
            {
                "action_type": "finish",
                "completion_summary": "done",
                "output_candidate": None,
                "reason_codes": [],
                "chain_of_thought": "must not persist",
            }
        )


def test_model_authority_state_sets_are_closed() -> None:
    assert set(MODEL_CALL_STATES) == {
        "accepted", "leased", "sending", "retry_wait", "succeeded",
        "failed", "cancelled", "uncertain", "dead_letter",
    }
    assert set(MODEL_CALL_ATTEMPT_STATES) == {
        "leased", "sending", "succeeded", "failed", "lease_expired",
        "cancelled", "uncertain",
    }
    assert set(MODEL_REPLAY_POLICIES) == {
        "safe_idempotent", "reconcile_required", "no_replay",
    }


def test_local_state_rejects_duplicate_or_cross_kind_result_refs() -> None:
    duplicate = _state()
    duplicate.update(
        {
            "phase": "finish_ready",
            "action_seq": 1,
            "observed_model_result_refs": [
                "model-result:result_01",
                "model-result:result_01",
            ],
            "candidate_refs": ["model-result:result_01"],
        }
    )
    with pytest.raises(ValidationError):
        normalize_local_agent_state(duplicate)

    invalid = _state()
    invalid.update(
        {
            "phase": "finish_ready",
            "action_seq": 1,
            "observed_model_result_refs": ["tool-result:result_01"],
            "candidate_refs": ["tool-result:result_01"],
        }
    )
    with pytest.raises(ValidationError):
        normalize_local_agent_state(invalid)


def test_local_state_keeps_a_valid_wait_across_attempt_transfer() -> None:
    waiting = _state(outstanding="model-call:call_01")
    waiting.update({"phase": "await_model", "action_seq": 1})
    normalized = normalize_local_agent_state(waiting)
    assert normalized["phase"] == "await_model"
    assert normalized["outstanding_operation_ref"] == "model-call:call_01"

    malformed = _state(outstanding="model-call:")
    malformed.update({"phase": "await_model", "action_seq": 1})
    with pytest.raises(ValidationError):
        normalize_local_agent_state(malformed)
