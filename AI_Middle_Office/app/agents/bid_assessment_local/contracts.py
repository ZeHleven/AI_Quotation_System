"""Closed model-action and local state contracts for Phase 4A-2."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


TASK_ACTION_SCHEMA = "bid.task.action.v1"
LOCAL_AGENT_STATE_SCHEMA = "bid.local_agent.state.v1"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
MONEY_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{4}$")
RATIO_PATTERN = re.compile(r"^(0(\.[0-9]{1,6})?|1(\.0{1,6})?)$")
ReasonCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ReasonedAction(_ClosedModel):
    reason_codes: list[ReasonCode] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_unique_reason_codes(self):
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("BID_MODEL_ACTION_REASON_CODE_DUPLICATE")
        return self


class FactScope(_ClosedModel):
    type: Literal["assessment", "lot"]
    id: str = Field(min_length=1, max_length=80, pattern=ID_PATTERN.pattern)


class FactCandidate(_ClosedModel):
    fact_slot: str = Field(min_length=1, max_length=160)
    value: Any
    value_type: Literal[
        "string", "text", "boolean", "enum", "integer", "money", "percentage",
        "datetime", "date", "duration", "location", "endpoint", "requirement_list",
        "clause_list", "scoring_item_list", "deliverable_list", "payment_milestone_list",
        "security", "project_identity",
    ]
    scope: FactScope
    source_type: Literal["document", "enterprise", "owner_answer", "system", "system_scope"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    confidence: Literal["high", "medium", "low"]
    asserted_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")

    @model_validator(mode="after")
    def validate_typed_value_and_evidence(self):
        if self.source_type == "document" and not self.evidence_ids:
            raise ValueError("BID_MODEL_FACT_DOCUMENT_EVIDENCE_REQUIRED")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("BID_MODEL_FACT_EVIDENCE_DUPLICATE")
        if any(
            len(value) > 80 or ID_PATTERN.fullmatch(value) is None
            for value in self.evidence_ids
        ):
            raise ValueError("BID_MODEL_FACT_EVIDENCE_ID_INVALID")
        expected = {
            "string": str,
            "text": str,
            "enum": str,
            "boolean": bool,
            "integer": int,
            "money": dict,
            "duration": dict,
            "location": dict,
            "endpoint": dict,
            "security": dict,
            "project_identity": dict,
            "requirement_list": list,
            "clause_list": list,
            "scoring_item_list": list,
            "deliverable_list": list,
            "payment_milestone_list": list,
        }.get(self.value_type)
        if expected is not None and (
            not isinstance(self.value, expected)
            or (expected is int and isinstance(self.value, bool))
        ):
            raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH")
        if self.value_type == "money" and (
            set(self.value) != {"amount", "currency"}
            or not isinstance(self.value.get("amount"), str)
            or MONEY_PATTERN.fullmatch(self.value["amount"]) is None
            or self.value.get("currency") != "CNY"
        ):
            raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH")
        if self.value_type == "percentage" and (
            not isinstance(self.value, str)
            or RATIO_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH")
        if self.value_type == "datetime":
            if not isinstance(self.value, str) or not self.value.endswith("Z"):
                raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH")
            try:
                datetime.fromisoformat(self.value[:-1] + "+00:00")
            except ValueError as exc:
                raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH") from exc
        if self.value_type == "date":
            if not isinstance(self.value, str):
                raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH")
            try:
                date.fromisoformat(self.value)
            except ValueError as exc:
                raise ValueError("BID_MODEL_FACT_VALUE_TYPE_MISMATCH") from exc
        try:
            datetime.fromisoformat(self.asserted_at[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("BID_MODEL_FACT_ASSERTED_AT_INVALID") from exc
        return self


class ClaimCandidate(_ClosedModel):
    claim_type: Literal["fact", "calculation", "inference", "recommendation"]
    text: str = Field(min_length=1, max_length=4000)
    support_ids: list[str] = Field(min_length=1, max_length=100)
    premise_or_trigger: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_unique_supports(self):
        if len(self.support_ids) != len(set(self.support_ids)):
            raise ValueError("BID_MODEL_CLAIM_SUPPORT_DUPLICATE")
        if any(
            len(value) > 80 or ID_PATTERN.fullmatch(value) is None
            for value in self.support_ids
        ):
            raise ValueError("BID_MODEL_CLAIM_SUPPORT_ID_INVALID")
        return self


class QuestionCandidate(_ClosedModel):
    fact_slot: str = Field(min_length=1, max_length=160)
    question_text: str = Field(min_length=1, max_length=500)
    impact: str = Field(min_length=1, max_length=500)
    priority: Literal["critical", "important", "contextual"]


class RequestToolAction(_ReasonedAction):
    action_type: Literal["request_tool"]
    tool_call_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,80}$")
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]


class SubmitFactCandidatesAction(_ReasonedAction):
    action_type: Literal["submit_fact_candidates"]
    candidates: list[FactCandidate] = Field(min_length=1, max_length=100)


class SubmitClaimCandidatesAction(_ReasonedAction):
    action_type: Literal["submit_claim_candidates"]
    candidates: list[ClaimCandidate] = Field(min_length=1, max_length=100)


class RequestTaskInputAction(_ReasonedAction):
    action_type: Literal["request_task_input"]
    questions: list[QuestionCandidate] = Field(min_length=1, max_length=20)


class FinishAction(_ReasonedAction):
    action_type: Literal["finish"]
    completion_summary: str = Field(min_length=1, max_length=2000)
    output_candidate: dict[str, Any] | None = None


TaskAction = Annotated[
    Union[
        RequestToolAction,
        SubmitFactCandidatesAction,
        SubmitClaimCandidatesAction,
        RequestTaskInputAction,
        FinishAction,
    ],
    Field(discriminator="action_type"),
]
TASK_ACTION_ADAPTER = TypeAdapter(TaskAction)


class LocalAgentState(_ClosedModel):
    schema: Literal["bid.local_agent.state.v1"] = LOCAL_AGENT_STATE_SCHEMA
    run_id: str = Field(min_length=1, max_length=80, pattern=ID_PATTERN.pattern)
    task_id: str = Field(min_length=1, max_length=80, pattern=ID_PATTERN.pattern)
    task_attempt_id: str = Field(min_length=1, max_length=80, pattern=ID_PATTERN.pattern)
    fencing_token: int = Field(ge=1)
    task_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    phase: Literal[
        "hydrate",
        "await_model",
        "await_tool",
        "candidate_ready",
        "input_candidate_ready",
        "finish_ready",
        "stopped",
    ]
    action_seq: int = Field(ge=0)
    observed_model_result_refs: list[str] = Field(default_factory=list, max_length=100)
    observed_tool_result_refs: list[str] = Field(default_factory=list, max_length=100)
    candidate_refs: list[str] = Field(default_factory=list, max_length=200)
    missing_slots: list[str] = Field(default_factory=list, max_length=200)
    outstanding_operation_ref: str | None = Field(default=None, max_length=512)
    stop_reason: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_lineage_and_phase(self):
        collections = (
            self.observed_model_result_refs,
            self.observed_tool_result_refs,
            self.candidate_refs,
            self.missing_slots,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("BID_LOCAL_AGENT_STATE_REFERENCE_DUPLICATE")
        if any(
            not value.startswith("model-result:")
            or ID_PATTERN.fullmatch(value.split(":", 1)[1]) is None
            for value in self.observed_model_result_refs
        ):
            raise ValueError("BID_LOCAL_AGENT_STATE_MODEL_REF_INVALID")
        if any(
            not value.startswith("tool-result:")
            or ID_PATTERN.fullmatch(value.split(":", 1)[1]) is None
            for value in self.observed_tool_result_refs
        ):
            raise ValueError("BID_LOCAL_AGENT_STATE_TOOL_REF_INVALID")
        if not set(self.candidate_refs) <= set(self.observed_model_result_refs):
            raise ValueError("BID_LOCAL_AGENT_STATE_CANDIDATE_REF_INVALID")
        outstanding = self.outstanding_operation_ref or ""
        expected_prefix = {
            "await_model": "model-call:",
            "await_tool": "tool-invocation:",
        }.get(self.phase)
        if expected_prefix is not None:
            if not outstanding.startswith(expected_prefix):
                raise ValueError("BID_LOCAL_AGENT_STATE_OUTSTANDING_REF_INVALID")
            outstanding_id = outstanding.split(":", 1)[1]
            if ID_PATTERN.fullmatch(outstanding_id) is None:
                raise ValueError("BID_LOCAL_AGENT_STATE_OUTSTANDING_REF_INVALID")
        if (
            self.phase == "hydrate"
            and outstanding
            and (
                not outstanding.startswith(("model-call:", "tool-invocation:"))
                or ID_PATTERN.fullmatch(outstanding.split(":", 1)[1]) is None
            )
        ):
            raise ValueError("BID_LOCAL_AGENT_STATE_OUTSTANDING_REF_INVALID")
        if self.phase not in {"hydrate", "await_model", "await_tool"} and outstanding:
            raise ValueError("BID_LOCAL_AGENT_STATE_OUTSTANDING_REF_INVALID")
        observed_models = len(self.observed_model_result_refs)
        expected_action_seq = observed_models + (
            1 if outstanding.startswith("model-call:") else 0
        )
        action_seq_valid = self.action_seq == expected_action_seq
        if self.phase == "stopped":
            action_seq_valid = self.action_seq in {
                observed_models,
                observed_models + 1,
            }
        if not action_seq_valid:
            raise ValueError("BID_LOCAL_AGENT_STATE_ACTION_SEQUENCE_INVALID")
        if (self.phase == "stopped") != bool(self.stop_reason):
            raise ValueError("BID_LOCAL_AGENT_STATE_STOP_REASON_INVALID")
        return self


def normalize_task_action(
    value: dict[str, Any],
    *,
    allowed_tools: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    action = TASK_ACTION_ADAPTER.validate_python(value)
    normalized = action.model_dump(mode="json", exclude_none=False)
    if normalized["action_type"] == "request_tool" and allowed_tools is not None:
        if str(normalized["tool_name"]) not in set(allowed_tools):
            raise ValueError("BID_MODEL_ACTION_TOOL_NOT_ALLOWED")
    return normalized


def normalize_local_agent_state(value: dict[str, Any]) -> dict[str, Any]:
    return LocalAgentState.model_validate(value).model_dump(mode="json", exclude_none=False)
