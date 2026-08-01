from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AssessmentState(TypedDict, total=False):
    case_id: str
    assessment_id: str
    agent_run_id: str
    phase: str
    manifest: dict[str, Any]
    analysis_goal: str
    required_dimensions: list[str]
    messages: Annotated[list[AnyMessage], add_messages]
    assessment_draft: dict[str, Any] | None
    report_version: int
    evidence_refs: list[dict[str, Any]]
    policy_evaluation: dict[str, Any] | None
    gate_result: dict[str, Any] | None
    fact_coverage: dict[str, Any] | None
    repair_count: int
    human_decision: dict[str, Any] | None
    reasoning_loop_count: int
    tool_call_count: int
    tool_signature_counts: dict[str, int]
    output_repair_count: int
    output_validation_error: str | None
    termination_reason: str | None
    versions: dict[str, Any]
    errors: list[dict[str, Any]]
