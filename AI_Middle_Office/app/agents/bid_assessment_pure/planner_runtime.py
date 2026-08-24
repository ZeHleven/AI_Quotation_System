"""Bounded, provider-neutral Planner Runtime for B04-1.

Planner is one structured capability invocation inside the Main Agent runtime.
It validates a finite rolling plan, never executes a tool, and only creates a
new revision when the caller supplies a material replan reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from pydantic import Field, ValidationError, model_validator

from .common import Reference, StrictContract, ToolName
from .planning import (
    ComplexityDecision,
    ExecutionMode,
    IntentUnderstanding,
    PlanRevision,
    TaskPlan,
)
from .runtime import ContextAssemblyResult, ContextAssemblyStatus, ContextConsumer
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import RegistrySnapshot, canonical_hash, canonical_json


class PlanRevisionReason(str, Enum):
    GOAL_CHANGED = "goal_changed"
    SCOPE_CHANGED = "scope_changed"
    ASSUMPTION_INVALIDATED = "assumption_invalidated"
    NEW_SUBGOAL = "new_subgoal"
    EVIDENCE_CONFLICT = "evidence_conflict"
    PATH_UNAVAILABLE = "path_unavailable"
    EXPLICIT_REPLAN = "explicit_replan"


class PlannerRuntimeError(RuntimeError):
    """Safe base error for planner-runtime boundary failures."""


class PlannerProviderUnavailable(PlannerRuntimeError):
    """No authorized planner provider is configured."""


class PlannerInvocationRejected(PlannerRuntimeError):
    """Planner invocation violates task, context, or revision guards."""


class PlannerContractRejected(PlannerRuntimeError):
    """Provider output failed the authoritative runtime contract."""


class PlannerGenerationRequest(StrictContract):
    request_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    understanding: IntentUnderstanding
    complexity: ComplexityDecision
    visible_tool_names: tuple[ToolName, ...] = Field(default_factory=tuple, max_length=32)
    registry_snapshot_ref: Reference
    registry_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    previous_plan: PlanRevision | None
    revision_reasons: tuple[PlanRevisionReason, ...] = Field(
        default_factory=tuple,
        max_length=7,
    )

    @model_validator(mode="after")
    def validate_unique_values(self) -> "PlannerGenerationRequest":
        if len(self.visible_tool_names) != len(set(self.visible_tool_names)):
            raise ValueError("visible_tool_names must be unique")
        if len(self.revision_reasons) != len(set(self.revision_reasons)):
            raise ValueError("revision_reasons must be unique")
        return self


class PlannerProvider(Protocol):
    """Replaceable structured-output provider bridged by B04-3."""

    async def generate(
        self,
        request: PlannerGenerationRequest,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
    ) -> TaskPlan | Mapping[str, Any]: ...


class DisabledPlannerProvider:
    """Fail-closed default that can never initiate a model call."""

    async def generate(
        self,
        request: PlannerGenerationRequest,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
    ) -> TaskPlan:
        del request, task, context, registry_snapshot
        raise PlannerProviderUnavailable("planner provider is disabled")


class StaticPlannerProvider:
    """In-memory fixture provider for later authorized contract tests only."""

    def __init__(self, responses: Mapping[str, TaskPlan | Mapping[str, Any]]):
        self._responses = MappingProxyType(dict(responses))

    async def generate(
        self,
        request: PlannerGenerationRequest,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
    ) -> TaskPlan | Mapping[str, Any]:
        del task, context, registry_snapshot
        try:
            return self._responses[request.context_snapshot_ref]
        except KeyError as exc:
            raise PlannerProviderUnavailable(
                "no static planner response is configured for this context"
            ) from exc


@dataclass(frozen=True, slots=True)
class PlannerLimits:
    max_steps: int = 12
    max_plan_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= 64:
            raise ValueError("max_steps must be between 1 and 64")
        if not 1024 <= self.max_plan_bytes <= 1024 * 1024:
            raise ValueError("max_plan_bytes must be between 1 KiB and 1 MiB")


class PlannerRuntime:
    """Create or materially revise a validated finite rolling plan."""

    def __init__(
        self,
        provider: PlannerProvider | None = None,
        *,
        limits: PlannerLimits | None = None,
    ) -> None:
        self._provider = provider or DisabledPlannerProvider()
        self._limits = limits or PlannerLimits()

    async def create_or_revise(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
        complexity: ComplexityDecision,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
        previous_plan: PlanRevision | None = None,
        revision_reasons: tuple[PlanRevisionReason, ...] = (),
    ) -> PlanRevision:
        self._validate_invocation(
            task=task,
            understanding=understanding,
            complexity=complexity,
            context=context,
            registry_snapshot=registry_snapshot,
            previous_plan=previous_plan,
            revision_reasons=revision_reasons,
        )
        if previous_plan is not None and not revision_reasons:
            return previous_plan

        next_version = 1 if previous_plan is None else previous_plan.plan_version + 1
        request_projection = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "context_snapshot_ref": context.snapshot.snapshot_ref,
            "registry_snapshot_ref": registry_snapshot.snapshot_ref,
            "previous_plan_ref": None if previous_plan is None else previous_plan.plan_id,
            "next_version": next_version,
            "revision_reasons": [reason.value for reason in revision_reasons],
        }
        request = PlannerGenerationRequest(
            request_ref=(
                "planner-request:"
                + canonical_hash(request_projection).removeprefix("sha256:")
            ),
            task_ref=task.task_id,
            state_version=task.state_version,
            context_snapshot_ref=context.snapshot.snapshot_ref,
            understanding=understanding,
            complexity=complexity,
            visible_tool_names=registry_snapshot.visible_tool_names,
            registry_snapshot_ref=registry_snapshot.snapshot_ref,
            registry_snapshot_hash=registry_snapshot.snapshot_hash,
            previous_plan=previous_plan,
            revision_reasons=revision_reasons,
        )
        raw_plan = await self._provider.generate(
            request,
            task=task,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        plan = self._validate_plan(raw_plan, registry_snapshot=registry_snapshot)

        if previous_plan is not None and canonical_hash(plan) == canonical_hash(
            previous_plan.plan
        ):
            return previous_plan

        plan_identity = canonical_hash(
            {
                "task_ref": task.task_id,
                "plan_version": next_version,
                "plan_hash": canonical_hash(plan),
            }
        ).removeprefix("sha256:")
        return PlanRevision(
            plan_id=f"plan:{plan_identity}",
            plan_version=next_version,
            task_id=task.task_id,
            plan=plan,
            supersedes_ref=None if previous_plan is None else previous_plan.plan_id,
        )

    def _validate_invocation(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
        complexity: ComplexityDecision,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
        previous_plan: PlanRevision | None,
        revision_reasons: tuple[PlanRevisionReason, ...],
    ) -> None:
        if task.status is not AgentTaskStatus.RUNNING:
            raise PlannerInvocationRejected("planning requires a running task")
        if complexity.execution_mode is not ExecutionMode.PLANNED:
            raise PlannerInvocationRejected("Complexity Gate did not request planning")
        if understanding.clarification_needed:
            raise PlannerInvocationRejected("blocking clarification must be resolved first")
        if set(complexity.preserves_observation_refs) != set(task.observation_refs):
            raise PlannerInvocationRejected(
                "complexity decision must preserve every accepted observation"
            )
        snapshot = context.snapshot
        if snapshot.task_ref != task.task_id or snapshot.state_version != task.state_version:
            raise PlannerInvocationRejected("planner context is stale or belongs to another task")
        if snapshot.consumer is not ContextConsumer.PLANNER:
            raise PlannerInvocationRejected("context was not assembled for Planner")
        if snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            raise PlannerInvocationRejected("planner context is not ready for consumption")
        if task.execution_mode is ExecutionMode.PLANNED:
            if previous_plan is None or task.plan_ref != previous_plan.plan_id:
                raise PlannerInvocationRejected(
                    "planned task must revise its currently committed plan"
                )
        elif previous_plan is not None or task.plan_ref is not None:
            raise PlannerInvocationRejected(
                "direct task may only request its initial plan"
            )
        if previous_plan is None and revision_reasons:
            raise PlannerInvocationRejected(
                "initial planning cannot declare revision reasons"
            )
        if previous_plan is not None:
            if previous_plan.task_id != task.task_id:
                raise PlannerInvocationRejected("previous plan belongs to another task")
            if len(revision_reasons) != len(set(revision_reasons)):
                raise PlannerInvocationRejected("revision reasons must be unique")
        if registry_snapshot.visible_tool_names and not set(
            registry_snapshot.visible_tool_names
        ).issubset({entry.name for entry in registry_snapshot.entries}):
            raise PlannerInvocationRejected("registry snapshot visibility is invalid")

    def _validate_plan(
        self,
        raw_plan: TaskPlan | Mapping[str, Any],
        *,
        registry_snapshot: RegistrySnapshot,
    ) -> TaskPlan:
        try:
            plan = (
                raw_plan
                if isinstance(raw_plan, TaskPlan)
                else TaskPlan.model_validate_json(canonical_json(raw_plan))
            )
            serialized = canonical_json(plan).encode("utf-8")
            if len(plan.steps) > self._limits.max_steps:
                raise ValueError("plan exceeds the rolling step limit")
            if len(serialized) > self._limits.max_plan_bytes:
                raise ValueError("plan exceeds the serialized size limit")
            plan.validate_tool_hints(registry_snapshot.visible_tool_names)
            return plan
        except (TypeError, ValueError, ValidationError) as exc:
            raise PlannerContractRejected(
                "planner output does not satisfy the bounded TaskPlan contract"
            ) from exc
