"""Intent, complexity, and planner contracts without a fixed task path."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Collection

from pydantic import Field, field_validator, model_validator

from .common import Reference, StepId, StrictContract, ToolName


PLAN_SCHEMA_VERSION = "bid.pure_agent.plan.v1"


class ExecutionMode(str, Enum):
    DIRECT = "direct"
    PLANNED = "planned"


class StepRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InformationSourceHint(str, Enum):
    CONVERSATION = "conversation"
    BID_DOCUMENTS = "bid_documents"
    ENTERPRISE_KNOWLEDGE = "enterprise_knowledge"
    EXISTING_EVIDENCE = "existing_evidence"


class IntentUnderstanding(StrictContract):
    """Open understanding of the current user goal, not a label classifier."""

    goal_summary: str = Field(min_length=1, max_length=1000)
    information_needs: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    source_hints: tuple[InformationSourceHint, ...] = Field(
        default_factory=tuple,
        max_length=4,
    )
    clarification_needed: bool
    blocking_slot_name: str | None = Field(default=None, min_length=1, max_length=128)
    execution_mode: ExecutionMode
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_clarification(self) -> "IntentUnderstanding":
        if self.clarification_needed != (self.blocking_slot_name is not None):
            raise ValueError("blocking_slot_name must match clarification_needed")
        if len(self.source_hints) != len(set(self.source_hints)):
            raise ValueError("source_hints must be unique")
        return self


class ComplexityDecision(StrictContract):
    execution_mode: ExecutionMode
    reasons: tuple[str, ...] = Field(min_length=1, max_length=12)
    preserves_observation_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "ComplexityDecision":
        if len(self.preserves_observation_refs) != len(
            set(self.preserves_observation_refs)
        ):
            raise ValueError("preserves_observation_refs must be unique")
        return self


class PlanStep(StrictContract):
    id: StepId
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1200)
    dependencies: tuple[StepId, ...] = Field(default_factory=tuple, max_length=32)
    tool_hint: ToolName | None
    expected_output: str = Field(min_length=1, max_length=1000)
    output_schema: dict[str, Any]
    risk_level: StepRiskLevel

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value or not isinstance(value.get("type"), str):
            raise ValueError("output_schema must declare a JSON Schema type")
        try:
            json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("output_schema must be JSON serializable") from exc
        return value

    @model_validator(mode="after")
    def validate_dependencies(self) -> "PlanStep":
        if self.id in self.dependencies:
            raise ValueError("a step cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("step dependencies must be unique")
        return self


class PlanNextDecision(StrictContract):
    """An open decision projection; ``type`` is not a workflow-stage enum."""

    type: str = Field(min_length=1, max_length=80)
    step_id: StepId | None
    summary: str = Field(min_length=1, max_length=500)


class PlanUserProjection(StrictContract):
    summary: str = Field(min_length=1, max_length=1000)
    visible_step_ids: tuple[StepId, ...] = Field(default_factory=tuple, max_length=64)


class TaskPlan(StrictContract):
    """The six confirmed planner fields, plus cross-field validation."""

    goal_summary: str = Field(min_length=1, max_length=1000)
    completion_criteria: tuple[str, ...] = Field(min_length=1, max_length=32)
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=64)
    next_decision: PlanNextDecision
    replan_conditions: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    user_projection: PlanUserProjection

    @model_validator(mode="after")
    def validate_graph_references(self) -> "TaskPlan":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step ids must be unique")
        known = set(step_ids)
        for step in self.steps:
            unknown = set(step.dependencies) - known
            if unknown:
                raise ValueError(f"unknown step dependencies: {sorted(unknown)}")
        if (
            self.next_decision.step_id is not None
            and self.next_decision.step_id not in known
        ):
            raise ValueError("next_decision.step_id must reference a plan step")
        unknown_visible = set(self.user_projection.visible_step_ids) - known
        if unknown_visible:
            raise ValueError(
                f"unknown user_projection step ids: {sorted(unknown_visible)}"
            )
        self._assert_acyclic_dependencies()
        return self

    def _assert_acyclic_dependencies(self) -> None:
        dependencies = {step.id: set(step.dependencies) for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("plan step dependencies must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for candidate in dependencies:
            visit(candidate)

    def validate_tool_hints(self, registered_tool_names: Collection[str]) -> None:
        registered = set(registered_tool_names)
        unknown = sorted(
            {
                step.tool_hint
                for step in self.steps
                if step.tool_hint is not None and step.tool_hint not in registered
            }
        )
        if unknown:
            raise ValueError(f"unregistered tool hints: {unknown}")


class PlanRevision(StrictContract):
    schema_version: str = Field(
        default=PLAN_SCHEMA_VERSION,
        pattern=r"^bid\.pure_agent\.plan\.v1$",
    )
    plan_id: Reference
    plan_version: int = Field(ge=1)
    task_id: Reference
    plan: TaskPlan
    supersedes_ref: Reference | None
