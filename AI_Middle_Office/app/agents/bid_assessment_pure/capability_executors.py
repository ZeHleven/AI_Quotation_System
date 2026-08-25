"""Five transaction-scoped capability executors for C02-3.

Each executor consumes exactly one Action already chosen by the Main Agent.
The executors do not call one another, select a successor, or encode a business
stage graph.  A completed observation returns control to the dynamic Action
Loop unless a persisted Slot or an accepted Response closes the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol
import uuid

from pydantic import Field, ValidationError, model_validator

from .action_runtime import (
    ActionObservationKind,
    ActionObservationStatus,
    AgentActionKind,
    DynamicActionLoopRuntime,
    MainAgentModelActionKind,
    MainAgentModelDecision,
    PlanActionRequest,
)
from .answer_contracts import (
    AnswerDraft,
    AnswerDraftValidationDecision,
    GroundingSnapshot,
)
from .answer_runtime import GroundingIntegrityGuard
from .citation_contracts import (
    CitationAuthoritySnapshot,
    CitationProjectionDecision,
    RenderedAnswerCandidate,
)
from .citation_runtime import AnswerBlockRenderer, CitationProjector
from .common import Reference, StrictContract, ToolName
from .planner_runtime import PlannerRuntime
from .planning import ComplexityDecision, ExecutionMode, PlanRevision
from .repository import PureAgentRepository
from .retrieval_convergence_v2 import (
    semantic_progress_signal_refs_from_tool_batch,
)
from .response_contracts import (
    ResponseSupersedeReason,
    ResponseVersionHead,
)
from .response_runtime import AnswerCommitRuntime
from .runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ToolCallRequest,
)
from .runtime_controller import (
    PersistedRuntimeAction,
    RuntimeActionExecution,
    RuntimePostAction,
    RuntimePulseDirective,
    SlotSuspensionDirective,
)
from .state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
)
from .tool_gateway import ToolGatewayOutcome
from .tool_runtime import (
    CanonicalToolMessage,
    ExecutionDeadline,
    GuardDecision,
    RegistrySnapshot,
    ToolGuardPolicy,
    ToolProvenanceRecord,
    canonical_hash,
    canonical_json,
)
from .tools import ToolExecutionContext, ToolExecutionResult


_CAPABILITY_NAMESPACE = uuid.UUID("34a78404-2a7c-4c6d-9ebf-12c4c8405d96")


class CapabilityExecutionError(RuntimeError):
    """Safe base error for a capability execution boundary."""


class CapabilityExecutionRejected(CapabilityExecutionError):
    """An Action, boundary, or result lost its authoritative binding."""


class CapabilityBoundaryUnavailable(CapabilityExecutionError):
    """No authorized boundary provider is configured for the capability."""


def _validate_active_action(
    *,
    task: AgentTaskState,
    action: PersistedRuntimeAction,
    expected_kind: AgentActionKind,
) -> None:
    intent = action.envelope.intent
    if (
        task.status is not AgentTaskStatus.RUNNING
        or task.in_flight_action_ref != action.action_ref
        or action.action_kind is not expected_kind
        or intent.action_kind is not expected_kind
        or intent.task_ref != task.task_id
        or task.state_version != intent.state_version + 1
    ):
        raise CapabilityExecutionRejected(
            f"active {expected_kind.value} Action lost its Task fence"
        )


def _validate_observed_action(
    *,
    task: AgentTaskState,
    action: PersistedRuntimeAction,
    execution: RuntimeActionExecution,
    expected_kind: AgentActionKind,
) -> None:
    observation = execution.observation
    if (
        task.status is not AgentTaskStatus.RUNNING
        or task.in_flight_action_ref is not None
        or action.action_kind is not expected_kind
        or observation.task_ref != task.task_id
        or observation.source_action_ref != action.action_ref
        or observation.observation_ref not in task.observation_refs
        or task.state_version != observation.state_version + 1
    ):
        raise CapabilityExecutionRejected(
            f"observed {expected_kind.value} Action lost its commit fence"
        )


def _parse_model_decision(
    action: PersistedRuntimeAction,
    *,
    expected_kind: AgentActionKind,
) -> MainAgentModelDecision:
    try:
        decision = MainAgentModelDecision.model_validate(
            action.envelope.intent.arguments
        )
        model_kind = MainAgentModelActionKind(expected_kind.value)
    except (ValidationError, ValueError) as exc:
        raise CapabilityExecutionRejected(
            f"{expected_kind.value} Action arguments are invalid"
        ) from exc
    if decision.action_kind is not model_kind:
        raise CapabilityExecutionRejected(
            f"{expected_kind.value} Action arguments changed kind"
        )
    return decision


def _result_identity(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    digest = canonical_hash(payload)
    return f"{kind}:{digest.removeprefix('sha256:')}", digest


class PlanCapabilityBoundary(StrictContract):
    """Authorized active-state inputs for one Planner invocation."""

    context: ContextAssemblyResult
    registry_snapshot: RegistrySnapshot
    complexity: ComplexityDecision
    previous_plan: PlanRevision | None = None


class PlanCapabilityBoundaryProvider(Protocol):
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        request: PlanActionRequest,
    ) -> PlanCapabilityBoundary: ...


class DisabledPlanCapabilityBoundaryProvider:
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        request: PlanActionRequest,
    ) -> PlanCapabilityBoundary:
        del task, action, request
        raise CapabilityBoundaryUnavailable(
            "Planner capability boundary is not configured"
        )


class PlanCapabilityExecutionResult(StrictContract):
    schema_name: Literal["bid.pure-agent.capability.plan-result.v1"] = (
        "bid.pure-agent.capability.plan-result.v1"
    )
    revision: PlanRevision
    context_snapshot_ref: Reference
    changed: bool


class _PlanningCapabilityExecutor:
    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        action_kind: AgentActionKind,
        boundary_provider: PlanCapabilityBoundaryProvider,
        planner: PlannerRuntime,
        action_loop: DynamicActionLoopRuntime | None = None,
    ) -> None:
        if action_kind not in {AgentActionKind.PLAN, AgentActionKind.REPLAN}:
            raise ValueError("planning executor requires plan or replan kind")
        self._repository = repository
        self._action_kind = action_kind
        self._boundary_provider = boundary_provider
        self._planner = planner
        self._action_loop = action_loop or DynamicActionLoopRuntime()

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        _validate_active_action(
            task=task,
            action=action,
            expected_kind=self._action_kind,
        )
        decision = _parse_model_decision(
            action,
            expected_kind=self._action_kind,
        )
        request = decision.plan_request
        if request is None:
            raise CapabilityExecutionRejected("planning Action has no request")
        boundary = await self._boundary_provider.prepare(
            task=task,
            action=action,
            request=request,
        )
        self._validate_boundary(task=task, action=action, boundary=boundary)
        revision = await self._planner.create_or_revise(
            task=task,
            understanding=request.understanding,
            complexity=boundary.complexity,
            context=boundary.context,
            registry_snapshot=boundary.registry_snapshot,
            previous_plan=boundary.previous_plan,
            revision_reasons=request.revision_reasons,
        )
        changed = (
            boundary.previous_plan is None
            or revision.plan_id != boundary.previous_plan.plan_id
        )
        self._repository.store_context_snapshot(boundary.context.snapshot)
        self._repository.store_plan(
            revision,
            context_snapshot_ref=boundary.context.snapshot.snapshot_ref,
        )
        result = PlanCapabilityExecutionResult(
            revision=revision,
            context_snapshot_ref=boundary.context.snapshot.snapshot_ref,
            changed=changed,
        )
        payload = result.model_dump(mode="json")
        revision_hash = canonical_hash(revision)
        observation = self._action_loop.build_action_observation(
            task=task,
            action_sequence=action.sequence,
            kind=ActionObservationKind.PLAN_REVISION,
            status=ActionObservationStatus.SUCCEEDED,
            artifact_ref=revision.plan_id,
            artifact_hash=revision_hash,
            summary=(
                "Planner committed a new rolling Plan revision"
                if changed
                else "Planner retained the current rolling Plan"
            ),
            material_progress=changed,
            progress_signal_refs=((revision.plan_id,) if changed else ()),
        )
        return RuntimeActionExecution(
            observation=observation,
            effect_status="succeeded",
            result_ref=revision.plan_id,
            result_payload=payload,
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        _validate_observed_action(
            task=task,
            action=action,
            execution=execution,
            expected_kind=self._action_kind,
        )
        try:
            result = PlanCapabilityExecutionResult.model_validate(
                execution.result_payload
            )
        except ValidationError as exc:
            raise CapabilityExecutionRejected(
                "Planner result failed its persisted contract"
            ) from exc
        revision = result.revision
        if (
            revision.task_id != task.task_id
            or execution.result_ref != revision.plan_id
            or execution.observation.artifact_ref != revision.plan_id
            or execution.observation.artifact_hash != canonical_hash(revision)
        ):
            raise CapabilityExecutionRejected("Planner result binding drifted")

        if task.plan_ref == revision.plan_id and task.execution_mode is ExecutionMode.PLANNED:
            return RuntimePostAction(directive=RuntimePulseDirective.CONTINUE)
        event_body = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "action_ref": action.action_ref,
            "plan_ref": revision.plan_id,
        }
        event_hash = canonical_hash(event_body).removeprefix("sha256:")
        event = TaskTransitionEvent(
            event_id=f"runtime-plan-commit:{event_hash}",
            task_id=task.task_id,
            expected_state_version=task.state_version,
            event_type=TaskEventType.EXECUTION_MODE_CHANGED,
            effect_idempotency_key=None,
            action_ref=action.action_ref,
            pending_context=None,
            resume_proof=None,
            execution_mode=ExecutionMode.PLANNED,
            plan_ref=revision.plan_id,
            observation_ref=None,
            result_committed=False,
            error_ref=None,
            cancellation_fence_ref=None,
        )
        return RuntimePostAction(
            directive=RuntimePulseDirective.CONTINUE,
            transition_events=(event,),
        )

    def _validate_boundary(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        boundary: PlanCapabilityBoundary,
    ) -> None:
        intent = action.envelope.intent
        snapshot = boundary.context.snapshot
        previous = boundary.previous_plan
        if (
            snapshot.task_ref != task.task_id
            or snapshot.state_version != task.state_version
            or boundary.complexity.execution_mode is not ExecutionMode.PLANNED
            or set(boundary.complexity.preserves_observation_refs)
            != set(task.observation_refs)
            or intent.registry_snapshot_ref != boundary.registry_snapshot.snapshot_ref
            or intent.registry_snapshot_hash != boundary.registry_snapshot.snapshot_hash
            or intent.visible_tools_hash != boundary.registry_snapshot.visible_tools_hash
        ):
            raise CapabilityExecutionRejected("Planner boundary is stale or unauthorized")
        if self._action_kind is AgentActionKind.PLAN:
            if task.execution_mode is not ExecutionMode.DIRECT or previous is not None:
                raise CapabilityExecutionRejected("initial Plan requires direct mode")
        elif (
            task.execution_mode is not ExecutionMode.PLANNED
            or previous is None
            or task.plan_ref != previous.plan_id
        ):
            raise CapabilityExecutionRejected(
                "Replan must revise the active Plan head"
            )


class PlanCapabilityExecutor(_PlanningCapabilityExecutor):
    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        boundary_provider: PlanCapabilityBoundaryProvider,
        planner: PlannerRuntime,
        action_loop: DynamicActionLoopRuntime | None = None,
    ) -> None:
        super().__init__(
            repository,
            action_kind=AgentActionKind.PLAN,
            boundary_provider=boundary_provider,
            planner=planner,
            action_loop=action_loop,
        )


class ReplanCapabilityExecutor(_PlanningCapabilityExecutor):
    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        boundary_provider: PlanCapabilityBoundaryProvider,
        planner: PlannerRuntime,
        action_loop: DynamicActionLoopRuntime | None = None,
    ) -> None:
        super().__init__(
            repository,
            action_kind=AgentActionKind.REPLAN,
            boundary_provider=boundary_provider,
            planner=planner,
            action_loop=action_loop,
        )


class ToolCallBatchBoundary(StrictContract):
    registry_snapshot: RegistrySnapshot
    execution_context: ToolExecutionContext
    guard_policy: ToolGuardPolicy
    deadline: ExecutionDeadline


class ToolCallBatchBoundaryProvider(Protocol):
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> ToolCallBatchBoundary: ...


class DisabledToolCallBatchBoundaryProvider:
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> ToolCallBatchBoundary:
        del task, action
        raise CapabilityBoundaryUnavailable(
            "Tool Call Batch boundary is not configured"
        )


class ToolBatchGatewayPort(Protocol):
    async def execute(
        self,
        *,
        call: ToolCallRequest,
        task: AgentTaskState,
        snapshot: RegistrySnapshot,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
        deadline: ExecutionDeadline,
    ) -> ToolGatewayOutcome: ...


class ToolCallCapabilityResult(StrictContract):
    call_ref: Reference
    tool_name: ToolName
    result: ToolExecutionResult[Any]
    tool_message: CanonicalToolMessage | None = None
    ledger_call_id: Reference | None = None
    accepted_for_context: bool
    guard_decisions: tuple[GuardDecision, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    replayed: bool
    provenance: tuple[ToolProvenanceRecord, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )


class ToolCallBatchExecutionResult(StrictContract):
    schema_name: Literal["bid.pure-agent.capability.tool-batch-result.v1"] = (
        "bid.pure-agent.capability.tool-batch-result.v1"
    )
    calls: tuple[ToolCallCapabilityResult, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_calls(self) -> "ToolCallBatchExecutionResult":
        refs = tuple(item.call_ref for item in self.calls)
        if len(refs) != len(set(refs)):
            raise ValueError("Tool batch result call refs must be unique")
        return self


class ToolCallBatchCapabilityExecutor:
    def __init__(
        self,
        *,
        boundary_provider: ToolCallBatchBoundaryProvider,
        gateway: ToolBatchGatewayPort,
        action_loop: DynamicActionLoopRuntime | None = None,
    ) -> None:
        self._boundary_provider = boundary_provider
        self._gateway = gateway
        self._action_loop = action_loop or DynamicActionLoopRuntime()

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        _validate_active_action(
            task=task,
            action=action,
            expected_kind=AgentActionKind.TOOL_CALL_BATCH,
        )
        boundary = await self._boundary_provider.prepare(task=task, action=action)
        self._validate_boundary(task=task, action=action, boundary=boundary)
        requests = self._action_loop.bind_tool_call_requests(
            task=task,
            intent=action.envelope.intent,
            action_ref=action.action_ref,
            registry_snapshot=boundary.registry_snapshot,
        )
        results: list[ToolCallCapabilityResult] = []
        for call in requests:
            outcome = await self._gateway.execute(
                call=call,
                task=task,
                snapshot=boundary.registry_snapshot,
                context=boundary.execution_context,
                policy=boundary.guard_policy,
                deadline=boundary.deadline,
            )
            results.append(
                ToolCallCapabilityResult(
                    call_ref=call.call_ref,
                    tool_name=call.tool_name,
                    result=outcome.result,
                    tool_message=outcome.tool_message,
                    ledger_call_id=outcome.ledger_call_id,
                    accepted_for_context=outcome.accepted_for_context,
                    guard_decisions=outcome.guard_decisions,
                    replayed=outcome.replayed,
                    provenance=outcome.provenance,
                )
            )
        result = ToolCallBatchExecutionResult(calls=tuple(results))
        payload = result.model_dump(mode="json")
        result_ref, result_hash = _result_identity("tool-batch-result", payload)
        semantic_progress_refs = semantic_progress_signal_refs_from_tool_batch(
            payload
        )
        accepted_refs = tuple(
            item.call_ref for item in result.calls if item.accepted_for_context
        )
        successful = tuple(
            item.call_ref
            for item in result.calls
            if item.accepted_for_context and item.result.ok
        )
        if len(successful) == len(result.calls):
            status = ActionObservationStatus.SUCCEEDED
        elif accepted_refs:
            status = ActionObservationStatus.DEGRADED
        else:
            status = ActionObservationStatus.NO_RESULT
        limitations: list[str] = []
        for item in result.calls:
            if item.result.error is not None:
                limitations.append(item.result.error.code.value)
            if not item.accepted_for_context:
                limitations.extend(
                    decision.code for decision in item.guard_decisions if not decision.allowed
                )
        if accepted_refs and not semantic_progress_refs:
            limitations.append("retrieval_no_novel_information")
        limitation_codes = tuple(dict.fromkeys(limitations))[:64]
        observation = self._action_loop.build_action_observation(
            task=task,
            action_sequence=action.sequence,
            kind=ActionObservationKind.TOOL_RESULT,
            status=status,
            artifact_ref=result_ref,
            artifact_hash=result_hash,
            summary=(
                f"Tool batch completed with {len(successful)}/{len(result.calls)} "
                "successful context-accepted results and "
                f"{len(semantic_progress_refs)} semantic progress signals"
            ),
            material_progress=bool(semantic_progress_refs),
            progress_signal_refs=semantic_progress_refs,
            limitation_codes=limitation_codes,
        )
        return RuntimeActionExecution(
            observation=observation,
            effect_status="succeeded",
            result_ref=result_ref,
            result_payload=payload,
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        _validate_observed_action(
            task=task,
            action=action,
            execution=execution,
            expected_kind=AgentActionKind.TOOL_CALL_BATCH,
        )
        try:
            result = ToolCallBatchExecutionResult.model_validate_json(
                canonical_json(execution.result_payload)
            )
        except ValidationError as exc:
            issue_summary = [
                {
                    "loc": [str(part) for part in issue.get("loc") or ()],
                    "type": str(issue.get("type") or "validation_error"),
                }
                for issue in exc.errors(
                    include_url=False,
                    include_input=False,
                )[:12]
            ]
            raise CapabilityExecutionRejected(
                "Tool batch result failed its persisted contract: "
                + canonical_json(issue_summary)[:1000]
            ) from exc
        payload = result.model_dump(mode="json")
        result_ref, result_hash = _result_identity("tool-batch-result", payload)
        if (
            execution.result_ref != result_ref
            or execution.observation.artifact_ref != result_ref
            or execution.observation.artifact_hash != result_hash
        ):
            raise CapabilityExecutionRejected("Tool batch result binding drifted")
        return RuntimePostAction(directive=RuntimePulseDirective.CONTINUE)

    @staticmethod
    def _validate_boundary(
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        boundary: ToolCallBatchBoundary,
    ) -> None:
        intent = action.envelope.intent
        context = boundary.execution_context
        policy = boundary.guard_policy
        snapshot = boundary.registry_snapshot
        if (
            intent.registry_snapshot_ref != snapshot.snapshot_ref
            or intent.registry_snapshot_hash != snapshot.snapshot_hash
            or intent.visible_tools_hash != snapshot.visible_tools_hash
            or context.task_ref != task.task_id
            or context.conversation_ref != task.session_id
            or context.state_version != task.state_version
            or context.context_snapshot_ref != intent.context_snapshot_ref
            or policy.task_ref != task.task_id
            or policy.user_ref != context.user_ref
            or policy.tenant_ref != context.tenant_ref
            or policy.authorization_snapshot_ref
            != context.authorization_snapshot_ref
            or not policy.runtime_enabled
        ):
            raise CapabilityExecutionRejected(
                "Tool Call Batch boundary is stale or unauthorized"
            )


class InformationRequestExecutionResult(StrictContract):
    schema_name: Literal["bid.pure-agent.capability.information-request-result.v1"] = (
        "bid.pure-agent.capability.information-request-result.v1"
    )
    slot_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    request_message: str = Field(min_length=1, max_length=2000)
    input_model_ref: Reference
    business_validator_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    blocking_reason: str = Field(min_length=1, max_length=500)
    context_snapshot_ref: Reference


class RequestInformationCapabilityExecutor:
    def __init__(
        self,
        *,
        action_loop: DynamicActionLoopRuntime | None = None,
    ) -> None:
        self._action_loop = action_loop or DynamicActionLoopRuntime()

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        _validate_active_action(
            task=task,
            action=action,
            expected_kind=AgentActionKind.REQUEST_INFORMATION,
        )
        decision = _parse_model_decision(
            action,
            expected_kind=AgentActionKind.REQUEST_INFORMATION,
        )
        request = decision.information_request
        if request is None:
            raise CapabilityExecutionRejected(
                "information request Action has no Slot request"
            )
        result = InformationRequestExecutionResult(
            slot_name=request.slot_name,
            request_message=request.request_message,
            input_model_ref=request.input_model_ref,
            business_validator_refs=request.business_validator_refs,
            blocking_reason=request.blocking_reason,
            context_snapshot_ref=action.envelope.intent.context_snapshot_ref,
        )
        payload = result.model_dump(mode="json")
        result_ref, result_hash = _result_identity("slot-request", payload)
        observation = self._action_loop.build_action_observation(
            task=task,
            action_sequence=action.sequence,
            kind=ActionObservationKind.SLOT_REQUEST,
            status=ActionObservationStatus.SUCCEEDED,
            artifact_ref=result_ref,
            artifact_hash=result_hash,
            summary=f"Agent requested required input for Slot {result.slot_name}",
            material_progress=False,
        )
        return RuntimeActionExecution(
            observation=observation,
            effect_status="succeeded",
            result_ref=result_ref,
            result_payload=payload,
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        _validate_observed_action(
            task=task,
            action=action,
            execution=execution,
            expected_kind=AgentActionKind.REQUEST_INFORMATION,
        )
        try:
            result = InformationRequestExecutionResult.model_validate(
                execution.result_payload
            )
        except ValidationError as exc:
            raise CapabilityExecutionRejected(
                "information request result failed its persisted contract"
            ) from exc
        payload = result.model_dump(mode="json")
        result_ref, result_hash = _result_identity("slot-request", payload)
        if (
            execution.result_ref != result_ref
            or execution.observation.artifact_ref != result_ref
            or execution.observation.artifact_hash != result_hash
        ):
            raise CapabilityExecutionRejected("Slot request result binding drifted")
        seed = (
            f"{task.task_id}:{action.action_ref}:"
            f"{action.envelope.intent.intent_hash}:{result.slot_name}"
        )
        slot_ref = str(uuid.uuid5(_CAPABILITY_NAMESPACE, f"slot:{seed}"))
        checkpoint_ref = str(
            uuid.uuid5(_CAPABILITY_NAMESPACE, f"checkpoint:{seed}")
        )
        return RuntimePostAction(
            directive=RuntimePulseDirective.WAIT,
            slot=SlotSuspensionDirective(
                name=result.slot_name,
                request_message=result.request_message,
                input_model_ref=result.input_model_ref,
                business_validator_refs=result.business_validator_refs,
                context_snapshot_ref=result.context_snapshot_ref,
                slot_ref=slot_ref,
                checkpoint_ref=checkpoint_ref,
            ),
        )


class AnswerCapabilityBoundary(StrictContract):
    """Fresh active-state evidence boundary used to rebind a frozen draft."""

    context: ContextAssemblyResult
    grounding_snapshot: GroundingSnapshot
    citation_authority_snapshot: CitationAuthoritySnapshot
    active_slot_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    previous_response: ResponseVersionHead | None = None
    supersede_reason: ResponseSupersedeReason | None = None

    @model_validator(mode="after")
    def validate_slots(self) -> "AnswerCapabilityBoundary":
        if len(self.active_slot_refs) != len(set(self.active_slot_refs)):
            raise ValueError("active Slot refs must be unique")
        if (self.previous_response is None) != (self.supersede_reason is None):
            raise ValueError(
                "previous response and supersede reason must appear together"
            )
        return self


class AnswerCapabilityBoundaryProvider(Protocol):
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        draft: AnswerDraft,
    ) -> AnswerCapabilityBoundary: ...


class DisabledAnswerCapabilityBoundaryProvider:
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        draft: AnswerDraft,
    ) -> AnswerCapabilityBoundary:
        del task, action, draft
        raise CapabilityBoundaryUnavailable(
            "Answer capability boundary is not configured"
        )


class AnswerCapabilityExecutionResult(StrictContract):
    schema_name: Literal["bid.pure-agent.capability.answer-result.v1"] = (
        "bid.pure-agent.capability.answer-result.v1"
    )
    status: Literal["ready_to_commit", "rejected"]
    source_draft_semantic_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_draft: AnswerDraft
    context: ContextAssemblyResult
    grounding_snapshot: GroundingSnapshot
    citation_authority_snapshot: CitationAuthoritySnapshot
    validation: AnswerDraftValidationDecision
    citation_decision: CitationProjectionDecision | None = None
    rendered: RenderedAnswerCandidate | None = None
    previous_response: ResponseVersionHead | None = None
    supersede_reason: ResponseSupersedeReason | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "AnswerCapabilityExecutionResult":
        semantic_hash = canonical_hash(
            self.execution_draft.model_dump(
                mode="json",
                exclude={"context_snapshot_ref", "state_version"},
            )
        )
        if semantic_hash != self.source_draft_semantic_hash:
            raise ValueError("execution draft content changed after rebinding")
        ready = (
            self.validation.accepted
            and self.citation_decision is not None
            and self.citation_decision.accepted
            and self.rendered is not None
        )
        if (self.status == "ready_to_commit") != ready:
            raise ValueError("ready Answer result requires the complete accepted chain")
        if (self.previous_response is None) != (self.supersede_reason is None):
            raise ValueError(
                "previous response and supersede reason must appear together"
            )
        return self


class AnswerCapabilityExecutor:
    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        boundary_provider: AnswerCapabilityBoundaryProvider,
        guard: GroundingIntegrityGuard | None = None,
        citation_projector: CitationProjector | None = None,
        renderer: AnswerBlockRenderer | None = None,
        committer: AnswerCommitRuntime | None = None,
        action_loop: DynamicActionLoopRuntime | None = None,
    ) -> None:
        self._repository = repository
        self._boundary_provider = boundary_provider
        self._guard = guard or GroundingIntegrityGuard()
        self._citation_projector = citation_projector or CitationProjector()
        self._renderer = renderer or AnswerBlockRenderer()
        self._committer = committer or AnswerCommitRuntime()
        self._action_loop = action_loop or DynamicActionLoopRuntime()

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        _validate_active_action(
            task=task,
            action=action,
            expected_kind=AgentActionKind.ANSWER,
        )
        decision = _parse_model_decision(
            action,
            expected_kind=AgentActionKind.ANSWER,
        )
        answer = decision.answer
        if answer is None:
            raise CapabilityExecutionRejected("Answer Action has no draft")
        source_draft = answer.draft
        intent = action.envelope.intent
        if source_draft.context_snapshot_ref != intent.context_snapshot_ref:
            raise CapabilityExecutionRejected(
                "Answer draft is outside its frozen decision Context"
            )
        boundary = await self._boundary_provider.prepare(
            task=task,
            action=action,
            draft=source_draft,
        )
        self._validate_boundary(task=task, boundary=boundary)
        rebound_payload = source_draft.model_dump(mode="json")
        rebound_payload.update(
            {
                "context_snapshot_ref": boundary.context.snapshot.snapshot_ref,
                "state_version": task.state_version,
            }
        )
        execution_draft = AnswerDraft.model_validate(rebound_payload)
        source_semantic_hash = self._semantic_draft_hash(source_draft)
        if self._semantic_draft_hash(execution_draft) != source_semantic_hash:
            raise CapabilityExecutionRejected(
                "Answer draft rebinding changed model-authored content"
            )
        self._repository.store_context_snapshot(boundary.context.snapshot)
        validation = self._guard.validate(
            task=task,
            context=boundary.context,
            draft=execution_draft,
            grounding_snapshot=boundary.grounding_snapshot,
            active_slot_refs=boundary.active_slot_refs,
        )
        citation_decision: CitationProjectionDecision | None = None
        rendered: RenderedAnswerCandidate | None = None
        if validation.accepted:
            citation_decision = self._citation_projector.project(
                task=task,
                context=boundary.context,
                draft=execution_draft,
                validation=validation,
                grounding_snapshot=boundary.grounding_snapshot,
                authority_snapshot=boundary.citation_authority_snapshot,
            )
            if citation_decision.accepted:
                rendered = self._renderer.render(
                    task=task,
                    draft=execution_draft,
                    validation=validation,
                    citation_decision=citation_decision,
                )
        result = AnswerCapabilityExecutionResult(
            status=(
                "ready_to_commit" if rendered is not None else "rejected"
            ),
            source_draft_semantic_hash=source_semantic_hash,
            execution_draft=execution_draft,
            context=boundary.context,
            grounding_snapshot=boundary.grounding_snapshot,
            citation_authority_snapshot=boundary.citation_authority_snapshot,
            validation=validation,
            citation_decision=citation_decision,
            rendered=rendered,
            previous_response=boundary.previous_response,
            supersede_reason=boundary.supersede_reason,
        )
        payload = result.model_dump(mode="json")
        result_ref, result_hash = _result_identity("answer-result", payload)
        if result.status == "ready_to_commit":
            status = ActionObservationStatus.SUCCEEDED
            # The durable Observation must bind the exact payload persisted by
            # RuntimeActionExecution.  The validated draft remains an explicit
            # progress signal and is re-verified from that payload before the
            # atomic response commit.
            artifact_ref = result_ref
            artifact_hash = result_hash
            progress_refs = tuple(
                dict.fromkeys(
                    (validation.draft_ref, *validation.validated_grounding_refs)
                )
            )[:128]
            limitations = tuple(code.value for code in validation.limitation_codes)
            summary = "AnswerDraft passed Grounding, Citation, and rendering guards"
        else:
            status = ActionObservationStatus.REJECTED
            artifact_ref = result_ref
            artifact_hash = result_hash
            progress_refs = ()
            issue_codes = [issue.code.value for issue in validation.issues]
            if citation_decision is not None:
                issue_codes.extend(issue.code.value for issue in citation_decision.issues)
            limitations = tuple(dict.fromkeys(issue_codes))[:64]
            summary = "AnswerDraft was rejected by an authoritative publication guard"
        observation = self._action_loop.build_action_observation(
            task=task,
            action_sequence=action.sequence,
            kind=ActionObservationKind.ANSWER_DRAFT,
            status=status,
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            summary=summary,
            material_progress=bool(progress_refs),
            progress_signal_refs=progress_refs,
            limitation_codes=limitations,
        )
        return RuntimeActionExecution(
            observation=observation,
            effect_status="succeeded",
            result_ref=result_ref,
            result_payload=payload,
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        _validate_observed_action(
            task=task,
            action=action,
            execution=execution,
            expected_kind=AgentActionKind.ANSWER,
        )
        try:
            result = AnswerCapabilityExecutionResult.model_validate(
                execution.result_payload
            )
        except ValidationError as exc:
            raise CapabilityExecutionRejected(
                "Answer result failed its persisted contract"
            ) from exc
        payload = result.model_dump(mode="json")
        result_ref, result_hash = _result_identity("answer-result", payload)
        if execution.result_ref != result_ref:
            raise CapabilityExecutionRejected("Answer result binding drifted")
        if result.status == "rejected":
            if (
                execution.observation.status is not ActionObservationStatus.REJECTED
                or execution.observation.artifact_ref != result_ref
                or execution.observation.artifact_hash != result_hash
            ):
                raise CapabilityExecutionRejected(
                    "rejected Answer observation binding drifted"
                )
            return RuntimePostAction(directive=RuntimePulseDirective.CONTINUE)
        citation_decision = result.citation_decision
        rendered = result.rendered
        if citation_decision is None or rendered is None:
            raise CapabilityExecutionRejected("accepted Answer chain is incomplete")
        if (
            execution.observation.status is not ActionObservationStatus.SUCCEEDED
            or execution.observation.artifact_ref != result_ref
            or execution.observation.artifact_hash != result_hash
            or result.validation.draft_ref
            not in execution.observation.progress_signal_refs
        ):
            raise CapabilityExecutionRejected(
                "accepted Answer observation binding drifted"
            )
        response = self._committer.prepare(
            task=task,
            context_snapshot=result.context.snapshot,
            draft=result.execution_draft,
            validation=result.validation,
            citation_decision=citation_decision,
            rendered=rendered,
            answer_observation=execution.observation,
            previous_response=result.previous_response,
            supersede_reason=result.supersede_reason,
        )
        if not response.accepted:
            raise CapabilityExecutionRejected(
                "Answer response did not pass the atomic commit boundary"
            )
        return RuntimePostAction(
            directive=RuntimePulseDirective.STOP,
            response=response,
        )

    @staticmethod
    def _semantic_draft_hash(draft: AnswerDraft) -> str:
        return canonical_hash(
            draft.model_dump(
                mode="json",
                exclude={"context_snapshot_ref", "state_version"},
            )
        )

    @staticmethod
    def _validate_boundary(
        *,
        task: AgentTaskState,
        boundary: AnswerCapabilityBoundary,
    ) -> None:
        snapshot = boundary.context.snapshot
        grounding = boundary.grounding_snapshot
        authority = boundary.citation_authority_snapshot
        if (
            snapshot.task_ref != task.task_id
            or snapshot.state_version != task.state_version
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.status
            not in {
                ContextAssemblyStatus.READY,
                ContextAssemblyStatus.READY_WITH_LIMITS,
            }
            or grounding.task_ref != task.task_id
            or grounding.state_version != task.state_version
            or grounding.context_snapshot_ref != snapshot.snapshot_ref
            or grounding.context_snapshot_hash != snapshot.snapshot_hash
            or authority.task_ref != task.task_id
            or authority.state_version != task.state_version
            or authority.context_snapshot_ref != snapshot.snapshot_ref
            or authority.context_snapshot_hash != snapshot.snapshot_hash
            or authority.grounding_snapshot_ref != grounding.snapshot_ref
            or authority.authorization_snapshot_ref
            != snapshot.authorization_snapshot_ref
        ):
            raise CapabilityExecutionRejected(
                "Answer evidence boundary is stale or unauthorized"
            )


@dataclass(frozen=True, slots=True)
class CapabilityExecutorFactories:
    """Transaction-aware factories for the complete five-kind registry.

    This object only creates deterministic kind-to-handler bindings.  It does
    not install a dispatcher or imply an Action order.
    """

    planner: Callable[[], PlannerRuntime]
    plan_boundary: Callable[
        [PureAgentRepository], PlanCapabilityBoundaryProvider
    ]
    tool_boundary: Callable[
        [PureAgentRepository], ToolCallBatchBoundaryProvider
    ]
    tool_gateway: Callable[[PureAgentRepository], ToolBatchGatewayPort]
    answer_boundary: Callable[
        [PureAgentRepository], AnswerCapabilityBoundaryProvider
    ]

    def __post_init__(self) -> None:
        for name in (
            "planner",
            "plan_boundary",
            "tool_boundary",
            "tool_gateway",
            "answer_boundary",
        ):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} capability factory is not callable")

    def handler_factories(self) -> Mapping[AgentActionKind, Callable[..., Any]]:
        """Return all five handler factories for C02-1 Registry freezing."""

        return {
            AgentActionKind.PLAN: lambda repository: PlanCapabilityExecutor(
                repository,
                boundary_provider=self.plan_boundary(repository),
                planner=self.planner(),
            ),
            AgentActionKind.REPLAN: lambda repository: ReplanCapabilityExecutor(
                repository,
                boundary_provider=self.plan_boundary(repository),
                planner=self.planner(),
            ),
            AgentActionKind.TOOL_CALL_BATCH: (
                lambda repository: ToolCallBatchCapabilityExecutor(
                    boundary_provider=self.tool_boundary(repository),
                    gateway=self.tool_gateway(repository),
                )
            ),
            AgentActionKind.REQUEST_INFORMATION: (
                lambda repository: RequestInformationCapabilityExecutor()
            ),
            AgentActionKind.ANSWER: lambda repository: AnswerCapabilityExecutor(
                repository,
                boundary_provider=self.answer_boundary(repository),
            ),
        }
