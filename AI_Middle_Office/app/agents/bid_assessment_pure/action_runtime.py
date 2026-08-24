"""Open, one-boundary-at-a-time Action Loop skeleton for B04-4.

There is deliberately no ``run`` method, stage graph, or fixed business order.
One already-reserved Main Agent decision action may propose one next capability;
the Runtime validates and records that proposal, then yields control.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from pydantic import Field, ValidationError, model_validator

from .answer_contracts import AnswerDraft, AnswerDraftValidationDecision
from .common import Reference, StrictContract, ToolName
from .complexity_gate import DefaultComplexityGate
from .planner_runtime import PlanRevisionReason
from .planning import ExecutionMode, IntentUnderstanding
from .provider_runtime import (
    ProviderAdapter,
    ProviderInvocationRequest,
    ProviderModelResult,
    ProviderOutputKind,
    ProviderRuntimeInput,
    ProviderStrictMode,
    ProviderStructuredOutputSpec,
    ProviderToolCallProposal,
    ProviderToolChoice,
    bind_normalized_tool_call_proposals,
)
from .runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ToolCallRequest,
)
from .state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionDecision,
    TaskTransitionEvent,
)
from .state_machine import decide_transition
from .tool_runtime import RegistrySnapshot, canonical_hash, canonical_json


class AgentActionKind(str, Enum):
    MAIN_AGENT_DECISION = "main_agent_decision"
    PLAN = "plan"
    REPLAN = "replan"
    TOOL_CALL_BATCH = "tool_call_batch"
    REQUEST_INFORMATION = "request_information"
    ANSWER = "answer"


class MainAgentModelActionKind(str, Enum):
    """Non-Tool actions allowed in the provider structured-output contract."""

    PLAN = "plan"
    REPLAN = "replan"
    REQUEST_INFORMATION = "request_information"
    ANSWER = "answer"


class ActionObservationKind(str, Enum):
    CONTROL_DECISION = "control_decision"
    PLAN_REVISION = "plan_revision"
    TOOL_RESULT = "tool_result"
    SLOT_REQUEST = "slot_request"
    ANSWER_DRAFT = "answer_draft"
    RUNTIME_LIMIT = "runtime_limit"
    ERROR = "error"


class ActionObservationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    NO_RESULT = "no_result"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    FAILED = "failed"


class ActionLoopError(RuntimeError):
    """Safe base error for dynamic Action Loop boundaries."""


class MainAgentActionProviderUnavailable(ActionLoopError):
    """No authorized Main Agent decision provider is configured."""


class ActionLoopInvocationRejected(ActionLoopError):
    """Task, Context, Registry, action, or observation binding is invalid."""


class ActionLoopContractRejected(ActionLoopError):
    """A model decision or capability result failed the Runtime contract."""


class PlanActionRequest(StrictContract):
    understanding: IntentUnderstanding
    reason: str = Field(min_length=1, max_length=500)
    revision_reasons: tuple[PlanRevisionReason, ...] = Field(
        default_factory=tuple,
        max_length=7,
    )

    @model_validator(mode="after")
    def validate_revision_reasons(self) -> "PlanActionRequest":
        if len(self.revision_reasons) != len(set(self.revision_reasons)):
            raise ValueError("revision_reasons must be unique")
        return self


class InformationRequestAction(StrictContract):
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

    @model_validator(mode="after")
    def validate_validator_refs(self) -> "InformationRequestAction":
        if len(self.business_validator_refs) != len(
            set(self.business_validator_refs)
        ):
            raise ValueError("business_validator_refs must be unique")
        return self


class AnswerAction(StrictContract):
    """One free-form draft; Grounding remains runtime-authoritative."""

    draft: AnswerDraft


class MainAgentModelDecision(StrictContract):
    """Provider-visible non-Tool decision with exactly one typed payload."""

    action_kind: MainAgentModelActionKind
    concise_basis: str = Field(min_length=1, max_length=500)
    plan_request: PlanActionRequest | None = None
    information_request: InformationRequestAction | None = None
    answer: AnswerAction | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "MainAgentModelDecision":
        populated = sum(
            value is not None
            for value in (
                self.plan_request,
                self.information_request,
                self.answer,
            )
        )
        if populated != 1:
            raise ValueError("exactly one action payload is required")
        if self.action_kind in {
            MainAgentModelActionKind.PLAN,
            MainAgentModelActionKind.REPLAN,
        }:
            if self.plan_request is None:
                raise ValueError("plan action requires plan_request")
            if (
                self.action_kind is MainAgentModelActionKind.PLAN
                and self.plan_request.revision_reasons
            ):
                raise ValueError("initial plan cannot declare revision reasons")
            if (
                self.action_kind is MainAgentModelActionKind.REPLAN
                and not self.plan_request.revision_reasons
            ):
                raise ValueError("replan requires a material revision reason")
        elif self.action_kind is MainAgentModelActionKind.REQUEST_INFORMATION:
            if self.information_request is None:
                raise ValueError("information action requires information_request")
        elif self.action_kind is MainAgentModelActionKind.ANSWER:
            if self.answer is None:
                raise ValueError("answer action requires answer payload")
        return self


class ToolCallBatchAction(StrictContract):
    action_kind: Literal["tool_call_batch"] = "tool_call_batch"
    model_turn_ref: Reference
    calls: tuple[ProviderToolCallProposal, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_call_batch(self) -> "ToolCallBatchAction":
        if tuple(call.sequence for call in self.calls) != tuple(
            range(1, len(self.calls) + 1)
        ):
            raise ValueError("Tool Call batch sequences must be contiguous")
        if any(call.model_turn_ref != self.model_turn_ref for call in self.calls):
            raise ValueError("Tool Call batch must belong to one model turn")
        if len({call.provider_tool_call_id for call in self.calls}) != len(self.calls):
            raise ValueError("Tool Call ids must be unique within the batch")
        return self


ActionProposal = MainAgentModelDecision | ToolCallBatchAction


class MainAgentDecisionActionArguments(StrictContract):
    turn_ref: Reference
    execution_mode: ExecutionMode
    plan_ref: Reference | None
    observation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=500)

    @model_validator(mode="after")
    def validate_observations(self) -> "MainAgentDecisionActionArguments":
        if len(self.observation_refs) != len(set(self.observation_refs)):
            raise ValueError("observation_refs must be unique")
        return self


class MainAgentDecisionRequest(StrictContract):
    request_ref: Reference
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_ref: Reference
    turn_ref: Reference
    decision_action_ref: Reference
    decision_sequence: int = Field(ge=1)
    origin_state_version: int = Field(ge=1)
    active_state_version: int = Field(ge=1)
    execution_mode: ExecutionMode
    plan_ref: Reference | None
    context_snapshot_ref: Reference
    context_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registry_snapshot_ref: Reference | None
    registry_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    visible_tools_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    visible_tool_names: tuple[ToolName, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    observation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=500)

    @model_validator(mode="after")
    def validate_request_hash(self) -> "MainAgentDecisionRequest":
        body = self.model_dump(
            mode="json",
            exclude={"request_ref", "request_hash"},
        )
        digest = canonical_hash(body)
        if self.request_hash != digest:
            raise ValueError("request_hash does not match decision request")
        if self.request_ref != f"agent-decision-request:{digest.removeprefix('sha256:')}":
            raise ValueError("request_ref does not match decision request")
        if self.active_state_version not in {
            self.origin_state_version,
            self.origin_state_version + 1,
        }:
            raise ValueError("active state must equal or immediately follow Context state")
        if len(self.visible_tool_names) != len(set(self.visible_tool_names)):
            raise ValueError("visible_tool_names must be unique")
        if len(self.observation_refs) != len(set(self.observation_refs)):
            raise ValueError("observation_refs must be unique")
        registry_missing = self.registry_snapshot_ref is None
        if registry_missing != (self.registry_snapshot_hash is None) or registry_missing != (
            self.visible_tools_hash is None
        ):
            raise ValueError("registry ref, hash, and visible hash must appear together")
        if registry_missing and self.visible_tool_names:
            raise ValueError("visible tools require a registry snapshot")
        return self


class MainAgentProviderOutcome(StrictContract):
    request_ref: Reference
    task_ref: Reference
    origin_state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    registry_snapshot_ref: Reference | None
    provider_result_ref: Reference
    provider_response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_receipt_ref: Reference
    proposal: ActionProposal
    concise_basis: str = Field(min_length=1, max_length=500)
    outcome_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_outcome_hash(self) -> "MainAgentProviderOutcome":
        body = self.model_dump(mode="json", exclude={"outcome_hash"})
        if self.outcome_hash != canonical_hash(body):
            raise ValueError("outcome_hash does not match provider outcome")
        return self


class ActionLoopDecision(StrictContract):
    decision_ref: Reference
    decision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_ref: Reference
    turn_ref: Reference
    decision_action_ref: Reference
    decision_sequence: int = Field(ge=1)
    origin_state_version: int = Field(ge=1)
    active_state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    context_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registry_snapshot_ref: Reference | None
    registry_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    visible_tools_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    proposal: ActionProposal
    proposal_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    concise_basis: str = Field(min_length=1, max_length=500)
    provider_result_ref: Reference
    provider_response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_receipt_ref: Reference

    @model_validator(mode="after")
    def validate_decision_hashes(self) -> "ActionLoopDecision":
        registry_missing = self.registry_snapshot_ref is None
        if registry_missing != (self.registry_snapshot_hash is None) or registry_missing != (
            self.visible_tools_hash is None
        ):
            raise ValueError("registry ref, hash, and visible hash must appear together")
        if self.proposal_hash != canonical_hash(self.proposal):
            raise ValueError("proposal_hash does not match Action proposal")
        body = self.model_dump(
            mode="json",
            exclude={"decision_ref", "decision_hash"},
        )
        digest = canonical_hash(body)
        if self.decision_hash != digest:
            raise ValueError("decision_hash does not match Action decision")
        if self.decision_ref != f"action-decision:{digest.removeprefix('sha256:')}":
            raise ValueError("decision_ref does not match Action decision")
        return self


class ActionObservation(StrictContract):
    observation_ref: Reference
    observation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_ref: Reference
    source_action_ref: Reference
    action_sequence: int = Field(ge=1)
    state_version: int = Field(ge=1)
    kind: ActionObservationKind
    status: ActionObservationStatus
    artifact_ref: Reference
    artifact_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    summary: str = Field(min_length=1, max_length=500)
    material_progress: bool = False
    progress_signal_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    limitation_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=64)

    @model_validator(mode="after")
    def validate_observation(self) -> "ActionObservation":
        if len(self.progress_signal_refs) != len(set(self.progress_signal_refs)):
            raise ValueError("progress_signal_refs must be unique")
        if len(self.limitation_codes) != len(set(self.limitation_codes)):
            raise ValueError("limitation_codes must be unique")
        if self.material_progress and not self.progress_signal_refs:
            raise ValueError("material progress requires a governed signal reference")
        if self.status in {
            ActionObservationStatus.REJECTED,
            ActionObservationStatus.FAILED,
        } and self.material_progress:
            raise ValueError("rejected or failed observations cannot claim progress")
        body = self.model_dump(
            mode="json",
            exclude={"observation_ref", "observation_hash"},
        )
        digest = canonical_hash(body)
        if self.observation_hash != digest:
            raise ValueError("observation_hash does not match observation")
        if self.observation_ref != f"observation:{digest.removeprefix('sha256:')}":
            raise ValueError("observation_ref does not match observation")
        return self


class ActionReservationIntent(StrictContract):
    intent_ref: Reference
    intent_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_ref: Reference
    state_version: int = Field(ge=1)
    decision_ref: Reference | None = None
    decision_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    decision_observation_ref: Reference | None = None
    action_kind: AgentActionKind
    arguments: dict[str, Any]
    arguments_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effect_identity_seed: Reference
    context_snapshot_ref: Reference
    context_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registry_snapshot_ref: Reference | None
    registry_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    visible_tools_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_snapshot_binding(self) -> "ActionReservationIntent":
        missing = self.registry_snapshot_ref is None
        if missing != (self.registry_snapshot_hash is None) or missing != (
            self.visible_tools_hash is None
        ):
            raise ValueError("registry ref, hash, and visible hash must appear together")
        decision_missing = self.decision_ref is None
        if decision_missing != (self.decision_hash is None) or decision_missing != (
            self.decision_observation_ref is None
        ):
            raise ValueError("source Decision fields must appear together")
        if self.action_kind is AgentActionKind.MAIN_AGENT_DECISION:
            if not decision_missing:
                raise ValueError("Main Agent decision Action has no source Decision")
        elif decision_missing:
            raise ValueError("capability Action requires its source Decision")
        return self

    @model_validator(mode="after")
    def validate_intent_hashes(self) -> "ActionReservationIntent":
        if self.arguments_hash != canonical_hash(self.arguments):
            raise ValueError("arguments_hash does not match Action arguments")
        body = self.model_dump(
            mode="json",
            exclude={"intent_ref", "intent_hash"},
        )
        digest = canonical_hash(body)
        if self.intent_hash != digest:
            raise ValueError("intent_hash does not match reservation intent")
        if self.intent_ref != f"action-intent:{digest.removeprefix('sha256:')}":
            raise ValueError("intent_ref does not match reservation intent")
        return self


class MainAgentActionProvider(Protocol):
    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome: ...


class DisabledMainAgentActionProvider:
    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome:
        del request, context, registry_snapshot
        raise MainAgentActionProviderUnavailable(
            "Main Agent action provider is disabled"
        )


class StaticMainAgentActionProvider:
    """In-memory fixture provider for later authorized contract tests only."""

    def __init__(self, outcomes: Mapping[str, MainAgentProviderOutcome]):
        self._outcomes = MappingProxyType(dict(outcomes))

    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome:
        del context, registry_snapshot
        try:
            return self._outcomes[request.request_ref]
        except KeyError as exc:
            raise MainAgentActionProviderUnavailable(
                "no static Main Agent action outcome is configured"
            ) from exc


class ProviderMainAgentActionProvider:
    """Bridge one accepted Main Agent decision action to Provider Adapter."""

    def __init__(self, adapter: ProviderAdapter | None = None) -> None:
        self._adapter = adapter or ProviderAdapter()

    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome:
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=request.request_ref,
            input_kind="main_agent_decision_request",
            payload=request.model_dump(mode="json"),
        )
        output_spec = ProviderStructuredOutputSpec.from_model(
            schema_name="main_agent_decision",
            output_model=MainAgentModelDecision,
            strict_mode=ProviderStrictMode.PREFERRED,
        )
        invocation = ProviderInvocationRequest(
            call_ref=(
                "model-call:"
                + canonical_hash(
                    {
                        "request_ref": request.request_ref,
                        "request_hash": request.request_hash,
                    }
                ).removeprefix("sha256:")
            ),
            task_ref=request.task_ref,
            state_version=request.origin_state_version,
            consumer=ContextConsumer.MAIN_AGENT,
            context=context,
            registry_snapshot=registry_snapshot,
            runtime_input=runtime_input,
            structured_output=output_spec,
            tool_choice=(
                ProviderToolChoice.AUTO
                if request.visible_tool_names
                else ProviderToolChoice.NONE
            ),
            tool_strict_mode=ProviderStrictMode.PREFERRED,
            max_output_tokens=min(
                self._adapter.capabilities.max_output_tokens,
                context.snapshot.reserved_output_tokens,
            ),
        )
        result = await self._adapter.invoke(invocation)
        proposal, concise_basis = self._proposal_from_result(result)
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": result.result_ref,
            "provider_response_hash": result.response_hash,
            "provider_receipt_ref": result.provider_receipt_ref,
            "proposal": proposal.model_dump(mode="json"),
            "concise_basis": concise_basis,
        }
        return MainAgentProviderOutcome(
            **body,
            outcome_hash=canonical_hash(body),
        )

    @staticmethod
    def _proposal_from_result(
        result: ProviderModelResult,
    ) -> tuple[ActionProposal, str]:
        if result.output_kind is ProviderOutputKind.TOOL_CALLS:
            proposal = ToolCallBatchAction(
                model_turn_ref=result.call_ref,
                calls=result.tool_call_proposals,
            )
            return proposal, "selected one or more approved Function Calling tools"
        if (
            result.output_kind is not ProviderOutputKind.STRUCTURED
            or result.structured_payload is None
        ):
            raise ActionLoopContractRejected(
                "Main Agent provider returned neither a decision nor Tool Calls"
            )
        try:
            decision = MainAgentModelDecision.model_validate_json(
                canonical_json(result.structured_payload)
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ActionLoopContractRejected(
                "Main Agent decision failed the authoritative Runtime contract"
            ) from exc
        return decision, decision.concise_basis


@dataclass(frozen=True, slots=True)
class DynamicActionLoopLimits:
    max_tool_calls_per_decision: int = 16
    max_answer_payload_bytes: int = 64 * 1024
    max_observation_refs: int = 500

    def __post_init__(self) -> None:
        if not 1 <= self.max_tool_calls_per_decision <= 64:
            raise ValueError("max_tool_calls_per_decision must be between 1 and 64")
        if not 1024 <= self.max_answer_payload_bytes <= 1024 * 1024:
            raise ValueError("max_answer_payload_bytes must be between 1 KiB and 1 MiB")
        if not 1 <= self.max_observation_refs <= 500:
            raise ValueError("max_observation_refs must be between 1 and 500")


class DynamicActionLoopRuntime:
    """Advance one accepted decision boundary; never owns an outer loop."""

    def __init__(
        self,
        provider: MainAgentActionProvider | None = None,
        *,
        complexity_gate: DefaultComplexityGate | None = None,
        limits: DynamicActionLoopLimits | None = None,
    ) -> None:
        self._provider = provider or DisabledMainAgentActionProvider()
        self._complexity_gate = complexity_gate or DefaultComplexityGate()
        self._limits = limits or DynamicActionLoopLimits()

    def prepare_decision_reservation_intent(
        self,
        *,
        task: AgentTaskState,
        turn_ref: str,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> ActionReservationIntent:
        """Prepare the Main Agent model Action before any provider invocation."""

        snapshot = context.snapshot
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is not None
            or snapshot.task_ref != task.task_id
            or snapshot.state_version != task.state_version
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.status
            not in {
                ContextAssemblyStatus.READY,
                ContextAssemblyStatus.READY_WITH_LIMITS,
            }
        ):
            raise ActionLoopInvocationRejected(
                "Main Agent decision is not at a safe reservation boundary"
            )
        if registry_snapshot is None:
            if snapshot.registry_snapshot_ref is not None:
                raise ActionLoopInvocationRejected(
                    "Main Agent decision Context requires its Registry Snapshot"
                )
        elif (
            snapshot.registry_snapshot_ref != registry_snapshot.snapshot_ref
            or snapshot.registry_snapshot_hash != registry_snapshot.snapshot_hash
        ):
            raise ActionLoopInvocationRejected(
                "Main Agent decision Registry Snapshot does not match Context"
            )
        arguments = MainAgentDecisionActionArguments(
            turn_ref=turn_ref,
            execution_mode=task.execution_mode,
            plan_ref=task.plan_ref,
            observation_refs=task.observation_refs,
        ).model_dump(mode="json")
        arguments_hash = canonical_hash(arguments)
        body = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "decision_ref": None,
            "decision_hash": None,
            "decision_observation_ref": None,
            "action_kind": AgentActionKind.MAIN_AGENT_DECISION.value,
            "arguments": arguments,
            "arguments_hash": arguments_hash,
            "effect_identity_seed": (
                "decision-effect:"
                + canonical_hash(
                    {
                        "task_ref": task.task_id,
                        "state_version": task.state_version,
                        "arguments_hash": arguments_hash,
                        "context_snapshot_hash": snapshot.snapshot_hash,
                    }
                ).removeprefix("sha256:")
            ),
            "context_snapshot_ref": snapshot.snapshot_ref,
            "context_snapshot_hash": snapshot.snapshot_hash,
            "registry_snapshot_ref": (
                None if registry_snapshot is None else registry_snapshot.snapshot_ref
            ),
            "registry_snapshot_hash": (
                None if registry_snapshot is None else registry_snapshot.snapshot_hash
            ),
            "visible_tools_hash": (
                None if registry_snapshot is None else registry_snapshot.visible_tools_hash
            ),
        }
        digest = canonical_hash(body)
        return ActionReservationIntent(
            **body,
            intent_ref=f"action-intent:{digest.removeprefix('sha256:')}",
            intent_hash=digest,
        )

    async def decide_next(
        self,
        *,
        task: AgentTaskState,
        turn_ref: str,
        decision_action_ref: str,
        decision_sequence: int,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> ActionLoopDecision:
        self._validate_decision_invocation(
            task=task,
            decision_action_ref=decision_action_ref,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        request = self._decision_request(
            task=task,
            turn_ref=turn_ref,
            decision_action_ref=decision_action_ref,
            decision_sequence=decision_sequence,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        outcome = await self._provider.decide(
            request=request,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        self._validate_provider_outcome(
            request=request,
            outcome=outcome,
            task=task,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        proposal_hash = canonical_hash(outcome.proposal)
        body = {
            "request_ref": request.request_ref,
            "request_hash": request.request_hash,
            "task_ref": request.task_ref,
            "turn_ref": request.turn_ref,
            "decision_action_ref": request.decision_action_ref,
            "decision_sequence": request.decision_sequence,
            "origin_state_version": request.origin_state_version,
            "active_state_version": request.active_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "context_snapshot_hash": request.context_snapshot_hash,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "registry_snapshot_hash": request.registry_snapshot_hash,
            "visible_tools_hash": request.visible_tools_hash,
            "proposal": outcome.proposal.model_dump(mode="json"),
            "proposal_hash": proposal_hash,
            "concise_basis": outcome.concise_basis,
            "provider_result_ref": outcome.provider_result_ref,
            "provider_response_hash": outcome.provider_response_hash,
            "provider_receipt_ref": outcome.provider_receipt_ref,
        }
        digest = canonical_hash(body)
        return ActionLoopDecision(
            **body,
            decision_ref=f"action-decision:{digest.removeprefix('sha256:')}",
            decision_hash=digest,
        )

    def build_decision_observation(
        self,
        *,
        task: AgentTaskState,
        decision: ActionLoopDecision,
        action_sequence: int,
    ) -> ActionObservation:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.task_id != decision.task_ref
            or task.state_version != decision.active_state_version
            or task.in_flight_action_ref != decision.decision_action_ref
            or action_sequence != decision.decision_sequence
        ):
            raise ActionLoopInvocationRejected(
                "decision result cannot bind to the active decision action"
            )
        return self.build_action_observation(
            task=task,
            action_sequence=action_sequence,
            kind=ActionObservationKind.CONTROL_DECISION,
            status=ActionObservationStatus.SUCCEEDED,
            artifact_ref=decision.decision_ref,
            artifact_hash=decision.decision_hash,
            summary=f"Main Agent proposed {self._proposal_kind(decision.proposal).value}",
            material_progress=False,
        )

    def build_action_observation(
        self,
        *,
        task: AgentTaskState,
        action_sequence: int,
        kind: ActionObservationKind,
        status: ActionObservationStatus,
        artifact_ref: str,
        artifact_hash: str,
        summary: str,
        material_progress: bool,
        progress_signal_refs: tuple[str, ...] = (),
        limitation_codes: tuple[str, ...] = (),
    ) -> ActionObservation:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is None
        ):
            raise ActionLoopInvocationRejected(
                "observation requires one active running Action"
            )
        body = {
            "task_ref": task.task_id,
            "source_action_ref": task.in_flight_action_ref,
            "action_sequence": action_sequence,
            "state_version": task.state_version,
            "kind": kind.value,
            "status": status.value,
            "artifact_ref": artifact_ref,
            "artifact_hash": artifact_hash,
            "summary": summary,
            "material_progress": material_progress,
            "progress_signal_refs": list(progress_signal_refs),
            "limitation_codes": list(limitation_codes),
        }
        digest = canonical_hash(body)
        return ActionObservation(
            **body,
            observation_ref=f"observation:{digest.removeprefix('sha256:')}",
            observation_hash=digest,
        )

    def build_validated_answer_observation(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        action_sequence: int,
        validation: AnswerDraftValidationDecision,
    ) -> ActionObservation:
        """Admit an AnswerDraft Observation only after the B05 Guard accepts it."""

        try:
            proposal = MainAgentModelDecision.model_validate(intent.arguments)
        except ValidationError as exc:
            raise ActionLoopContractRejected(
                "active Answer Action does not contain a valid frozen proposal"
            ) from exc
        answer = proposal.answer
        if (
            not validation.accepted
            or validation.task_ref != task.task_id
            or task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is None
            or intent.action_kind is not AgentActionKind.ANSWER
            or intent.task_ref != task.task_id
            or task.state_version != intent.state_version + 1
            or intent.context_snapshot_ref != validation.context_snapshot_ref
            or proposal.action_kind is not MainAgentModelActionKind.ANSWER
            or answer is None
            or canonical_hash(answer.draft) != validation.draft_hash
        ):
            raise ActionLoopContractRejected(
                "AnswerDraft Observation requires an accepted active Guard decision"
            )
        return self.build_action_observation(
            task=task,
            action_sequence=action_sequence,
            kind=ActionObservationKind.ANSWER_DRAFT,
            status=ActionObservationStatus.SUCCEEDED,
            artifact_ref=validation.draft_ref,
            artifact_hash=validation.draft_hash,
            summary="AnswerDraft passed Grounding Integrity Guard",
            material_progress=True,
            progress_signal_refs=validation.validated_grounding_refs,
            limitation_codes=tuple(code.value for code in validation.limitation_codes),
        )

    def decide_observation_acceptance(
        self,
        *,
        task: AgentTaskState,
        observation: ActionObservation,
        event_id: str,
    ) -> TaskTransitionDecision:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.task_id != observation.task_ref
            or task.state_version != observation.state_version
            or task.in_flight_action_ref != observation.source_action_ref
            or observation.observation_ref in task.observation_refs
        ):
            raise ActionLoopInvocationRejected(
                "observation cannot be accepted by the active Task state"
            )
        if len(task.observation_refs) >= self._limits.max_observation_refs:
            raise ActionLoopInvocationRejected("Task observation reference limit reached")
        event = TaskTransitionEvent(
            event_id=event_id,
            task_id=task.task_id,
            expected_state_version=task.state_version,
            event_type=TaskEventType.OBSERVATION_ACCEPTED,
            effect_idempotency_key=None,
            action_ref=observation.source_action_ref,
            pending_context=None,
            resume_proof=None,
            execution_mode=None,
            plan_ref=None,
            observation_ref=observation.observation_ref,
            result_committed=False,
            error_ref=None,
            cancellation_fence_ref=None,
        )
        return decide_transition(task, event)

    def prepare_reservation_intent(
        self,
        *,
        task: AgentTaskState,
        decision: ActionLoopDecision,
        decision_observation: ActionObservation,
    ) -> ActionReservationIntent:
        wrapped_result = {
            "schema_name": "bid.pure-agent.decision-result.v1",
            "decision": decision.model_dump(mode="json"),
        }
        wrapped_hash = canonical_hash(wrapped_result)
        wrapped_ref = (
            "decision-result:" + wrapped_hash.removeprefix("sha256:")
        )
        artifact_matches = (
            decision_observation.artifact_ref == decision.decision_ref
            and decision_observation.artifact_hash == decision.decision_hash
        ) or (
            decision_observation.artifact_ref == wrapped_ref
            and decision_observation.artifact_hash == wrapped_hash
        )
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is not None
            or task.task_id != decision.task_ref
            or task.state_version != decision.active_state_version + 1
            or decision_observation.observation_ref not in task.observation_refs
            or decision_observation.task_ref != task.task_id
            or decision_observation.source_action_ref != decision.decision_action_ref
            or decision_observation.action_sequence != decision.decision_sequence
            or decision_observation.state_version != decision.active_state_version
            or decision_observation.kind is not ActionObservationKind.CONTROL_DECISION
            or decision_observation.status is not ActionObservationStatus.SUCCEEDED
            or not artifact_matches
        ):
            raise ActionLoopInvocationRejected(
                "Action proposal is not at its decision Observation boundary"
            )
        arguments = decision.proposal.model_dump(mode="json")
        arguments_hash = canonical_hash(arguments)
        action_kind = self._proposal_kind(decision.proposal)
        effect_seed = canonical_hash(
            {
                "task_ref": task.task_id,
                "decision_ref": decision.decision_ref,
                "proposal_hash": decision.proposal_hash,
                "state_version": task.state_version,
            }
        ).removeprefix("sha256:")
        body = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "decision_ref": decision.decision_ref,
            "decision_hash": decision.decision_hash,
            "decision_observation_ref": decision_observation.observation_ref,
            "action_kind": action_kind.value,
            "arguments": arguments,
            "arguments_hash": arguments_hash,
            "effect_identity_seed": f"action-effect:{effect_seed}",
            "context_snapshot_ref": decision.context_snapshot_ref,
            "context_snapshot_hash": decision.context_snapshot_hash,
            "registry_snapshot_ref": decision.registry_snapshot_ref,
            "registry_snapshot_hash": decision.registry_snapshot_hash,
            "visible_tools_hash": decision.visible_tools_hash,
        }
        digest = canonical_hash(body)
        return ActionReservationIntent(
            **body,
            intent_ref=f"action-intent:{digest.removeprefix('sha256:')}",
            intent_hash=digest,
        )

    def bind_tool_call_requests(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        action_ref: str,
        registry_snapshot: RegistrySnapshot,
    ) -> tuple[ToolCallRequest, ...]:
        """Rebind an accepted Tool proposal to its later active Gateway action.

        Provider proposals are frozen against the model Context state.  Gateway
        requests must instead carry the state version created when the Tool
        action was accepted.  This bridge performs only that controlled rebinding;
        it never executes a Tool.
        """

        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.task_id != intent.task_ref
            or task.in_flight_action_ref != action_ref
            or task.state_version != intent.state_version + 1
            or intent.action_kind is not AgentActionKind.TOOL_CALL_BATCH
            or intent.registry_snapshot_ref != registry_snapshot.snapshot_ref
            or intent.registry_snapshot_hash != registry_snapshot.snapshot_hash
            or intent.visible_tools_hash != registry_snapshot.visible_tools_hash
        ):
            raise ActionLoopInvocationRejected(
                "Tool Call intent cannot bind to the active Tool action"
            )
        try:
            batch = ToolCallBatchAction.model_validate(intent.arguments)
        except ValidationError as exc:
            raise ActionLoopContractRejected(
                "Tool Call intent failed the authoritative Runtime contract"
            ) from exc
        if len(batch.calls) > self._limits.max_tool_calls_per_decision:
            raise ActionLoopContractRejected("Tool Call batch exceeds Runtime limit")

        visible_names = set(registry_snapshot.visible_tool_names)
        for proposal in batch.calls:
            if (
                proposal.task_ref != task.task_id
                or proposal.registry_snapshot_ref != registry_snapshot.snapshot_ref
                or proposal.registry_snapshot_hash != registry_snapshot.snapshot_hash
                or proposal.visible_tools_hash != registry_snapshot.visible_tools_hash
                or proposal.tool_name not in visible_names
            ):
                raise ActionLoopContractRejected(
                    "Tool Call proposal is stale, unauthorized, or non-visible"
                )
        return bind_normalized_tool_call_proposals(
            batch.calls,
            proposal_task_ref=intent.task_ref,
            proposal_state_version=batch.calls[0].state_version,
            active_task=task,
            action_ref=action_ref,
        )

    def _validate_decision_invocation(
        self,
        *,
        task: AgentTaskState,
        decision_action_ref: str,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> None:
        snapshot = context.snapshot
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref != decision_action_ref
        ):
            raise ActionLoopInvocationRejected(
                "Main Agent decision requires its accepted in-flight Action"
            )
        if (
            snapshot.task_ref != task.task_id
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.status
            not in {
                ContextAssemblyStatus.READY,
                ContextAssemblyStatus.READY_WITH_LIMITS,
            }
            or task.state_version
            not in {snapshot.state_version, snapshot.state_version + 1}
        ):
            raise ActionLoopInvocationRejected(
                "Main Agent Context does not match the active Task"
            )
        if registry_snapshot is None:
            if snapshot.registry_snapshot_ref is not None:
                raise ActionLoopInvocationRejected(
                    "Main Agent Context requires its Registry Snapshot"
                )
        elif (
            snapshot.registry_snapshot_ref != registry_snapshot.snapshot_ref
            or snapshot.registry_snapshot_hash != registry_snapshot.snapshot_hash
        ):
            raise ActionLoopInvocationRejected(
                "Main Agent Registry Snapshot does not match Context"
            )

    @staticmethod
    def _decision_request(
        *,
        task: AgentTaskState,
        turn_ref: str,
        decision_action_ref: str,
        decision_sequence: int,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentDecisionRequest:
        snapshot = context.snapshot
        body = {
            "task_ref": task.task_id,
            "turn_ref": turn_ref,
            "decision_action_ref": decision_action_ref,
            "decision_sequence": decision_sequence,
            "origin_state_version": snapshot.state_version,
            "active_state_version": task.state_version,
            "execution_mode": task.execution_mode.value,
            "plan_ref": task.plan_ref,
            "context_snapshot_ref": snapshot.snapshot_ref,
            "context_snapshot_hash": snapshot.snapshot_hash,
            "registry_snapshot_ref": (
                None if registry_snapshot is None else registry_snapshot.snapshot_ref
            ),
            "registry_snapshot_hash": (
                None if registry_snapshot is None else registry_snapshot.snapshot_hash
            ),
            "visible_tools_hash": (
                None if registry_snapshot is None else registry_snapshot.visible_tools_hash
            ),
            "visible_tool_names": (
                [] if registry_snapshot is None else list(registry_snapshot.visible_tool_names)
            ),
            "observation_refs": list(task.observation_refs),
        }
        digest = canonical_hash(body)
        return MainAgentDecisionRequest(
            **body,
            request_ref=f"agent-decision-request:{digest.removeprefix('sha256:')}",
            request_hash=digest,
        )

    def _validate_provider_outcome(
        self,
        *,
        request: MainAgentDecisionRequest,
        outcome: MainAgentProviderOutcome,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> None:
        if (
            outcome.request_ref != request.request_ref
            or outcome.task_ref != request.task_ref
            or outcome.origin_state_version != request.origin_state_version
            or outcome.context_snapshot_ref != request.context_snapshot_ref
            or outcome.registry_snapshot_ref != request.registry_snapshot_ref
        ):
            raise ActionLoopContractRejected(
                "Main Agent provider outcome is stale or cross-scoped"
            )
        proposal = outcome.proposal
        if isinstance(proposal, ToolCallBatchAction):
            if len(proposal.calls) > self._limits.max_tool_calls_per_decision:
                raise ActionLoopContractRejected("Tool Call batch exceeds Runtime limit")
            if registry_snapshot is None:
                raise ActionLoopContractRejected(
                    "Tool Call batch requires a Registry Snapshot"
                )
            expected_sequence = tuple(range(1, len(proposal.calls) + 1))
            if tuple(call.sequence for call in proposal.calls) != expected_sequence:
                raise ActionLoopContractRejected("Tool Call sequence is invalid")
            visible = set(registry_snapshot.visible_tool_names)
            for call in proposal.calls:
                if (
                    call.task_ref != task.task_id
                    or call.state_version != context.snapshot.state_version
                    or call.context_snapshot_ref != context.snapshot.snapshot_ref
                    or call.registry_snapshot_ref != registry_snapshot.snapshot_ref
                    or call.registry_snapshot_hash != registry_snapshot.snapshot_hash
                    or call.visible_tools_hash != registry_snapshot.visible_tools_hash
                    or call.authorization_snapshot_ref
                    != context.snapshot.authorization_snapshot_ref
                    or call.tool_name not in visible
                ):
                    raise ActionLoopContractRejected(
                        "Tool Call batch is stale, unauthorized, or non-visible"
                    )
            return
        if outcome.concise_basis != proposal.concise_basis:
            raise ActionLoopContractRejected(
                "Main Agent decision basis does not match its provider outcome"
            )
        plan_request = proposal.plan_request
        if plan_request is not None:
            complexity = self._complexity_gate.decide(
                task=task,
                understanding=plan_request.understanding,
            )
            if (
                plan_request.understanding.clarification_needed
                or complexity.execution_mode is not ExecutionMode.PLANNED
            ):
                raise ActionLoopContractRejected(
                    "planning requires a non-blocked Planned complexity decision"
                )
        if proposal.action_kind is MainAgentModelActionKind.PLAN:
            if task.execution_mode is not ExecutionMode.DIRECT or task.plan_ref is not None:
                raise ActionLoopContractRejected(
                    "initial planning is only available from direct mode without a Plan"
                )
        elif proposal.action_kind is MainAgentModelActionKind.REPLAN:
            if task.execution_mode is not ExecutionMode.PLANNED or task.plan_ref is None:
                raise ActionLoopContractRejected(
                    "replanning requires the active planned mode and Plan"
                )
        elif proposal.action_kind is MainAgentModelActionKind.ANSWER:
            answer = proposal.answer
            if answer is None:
                raise ActionLoopContractRejected("answer proposal is missing its payload")
            if len(canonical_json(answer.draft).encode("utf-8")) > (
                self._limits.max_answer_payload_bytes
            ):
                raise ActionLoopContractRejected("answer proposal exceeds Runtime limit")
            allowed_grounding_refs = {
                ref
                for entry in context.projection_entries
                for ref in (entry.entry_ref, entry.source_ref)
            }
            if not set(answer.draft.referenced_grounding_refs()).issubset(
                allowed_grounding_refs
            ):
                raise ActionLoopContractRejected(
                    "answer proposal selected Grounding outside current Context"
                )
            if (
                answer.draft.context_snapshot_ref != context.snapshot.snapshot_ref
                or answer.draft.state_version != context.snapshot.state_version
            ):
                raise ActionLoopContractRejected(
                    "answer proposal does not bind to the current Context Snapshot"
                )

    @staticmethod
    def _proposal_kind(proposal: ActionProposal) -> AgentActionKind:
        if isinstance(proposal, ToolCallBatchAction):
            return AgentActionKind.TOOL_CALL_BATCH
        return AgentActionKind(proposal.action_kind.value)
