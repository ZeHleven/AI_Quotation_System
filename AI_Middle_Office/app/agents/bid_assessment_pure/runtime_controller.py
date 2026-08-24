"""Event-driven Pure Agent Runtime Controller.

The controller advances one accepted Action per pulse.  It does not own a
business stage graph, a fixed node list, or a predeclared workflow path.  The
Main Agent/driver chooses the next Action; this module only enforces durable
reservation, state/version fences, effect settlement, continuation, and safe
publication boundaries.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import logging
from typing import Any, Callable, Literal, Mapping, Protocol
import uuid

from pydantic import Field, model_validator

from .action_runtime import (
    ActionLoopContractRejected,
    ActionLoopDecision,
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
    ActionReservationIntent,
    AgentActionKind,
    DynamicActionLoopRuntime,
)
from .common import Reference, StrictContract
from .repository import (
    PersistedObservationArtifactRow,
    PureAgentConflict,
    PureAgentFenceRejected,
    PureAgentPersistenceError,
    PureAgentRepository,
    canonical_json,
)
from .response_contracts import ResponseCommitDecision
from .runtime import ContextAssemblyResult
from .runtime_guards import (
    ActionAdmissionDecision,
    ActionRuntimeBinding,
    AdmissionDisposition,
    BudgetDemand,
    BudgetUsage,
    CancellationSnapshot,
    EffectFenceSnapshot,
    EffectFenceStatus,
    EffectReplayPolicy,
    ProgressWindow,
    RecoveryDirective,
    RuntimeBudgetSnapshot,
    RuntimeGuardSuite,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeCheckpointSnapshot,
    RuntimeRecoveryGuard,
)
from .slots import ContinuationCheckpoint
from .state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
)
from .tool_runtime import canonical_hash
from .tool_runtime import RegistrySnapshot


logger = logging.getLogger(__name__)

MAX_ACTION_ENVELOPE_BYTES = 2 * 1024 * 1024


class RuntimeWakeReason(str, Enum):
    USER_MESSAGE = "user_message"
    STEERING_MESSAGE = "steering_message"
    SLOT_RESUMED = "slot_resumed"
    ACTION_CONTINUATION = "action_continuation"
    RECOVERY = "recovery"


class RuntimePulseDirective(str, Enum):
    CONTINUE = "continue"
    WAIT = "wait"
    STOP = "stop"


class RuntimePulseDisposition(str, Enum):
    ACTION_RESERVED = "action_reserved"
    ACTION_COMPLETED = "action_completed"
    PENDING = "pending"
    TERMINAL = "terminal"
    STALE_WAKEUP = "stale_wakeup"
    RECOVERY_REQUIRED = "recovery_required"
    DISABLED = "disabled"
    YIELDED = "yielded"
    FAILED = "failed"


class RuntimeWakeup(StrictContract):
    wakeup_ref: Reference
    task_ref: Reference
    conversation_ref: Reference
    observed_state_version: int = Field(ge=1)
    reason: RuntimeWakeReason

    @classmethod
    def build(
        cls,
        *,
        task_ref: str,
        conversation_ref: str,
        observed_state_version: int,
        reason: RuntimeWakeReason,
        seed: str,
    ) -> "RuntimeWakeup":
        digest = hashlib.sha256(
            canonical_json(
                {
                    "task_ref": task_ref,
                    "conversation_ref": conversation_ref,
                    "observed_state_version": observed_state_version,
                    "reason": reason.value,
                    "seed": seed,
                }
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            wakeup_ref=f"runtime-wakeup:{digest}",
            task_ref=task_ref,
            conversation_ref=conversation_ref,
            observed_state_version=observed_state_version,
            reason=reason,
        )


class RuntimePulseOutcome(StrictContract):
    task_ref: Reference
    state_version: int = Field(ge=1)
    task_status: AgentTaskStatus
    disposition: RuntimePulseDisposition
    directive: RuntimePulseDirective
    action_ref: Reference | None = None
    recovery_directive: RecoveryDirective | None = None
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)


class RuntimeActionRecoveryBinding(StrictContract):
    """Immutable policy/scope facts needed to recover one reserved Action."""

    profile: RuntimeProfileSnapshot
    authorization_policy_ref: Reference
    scope_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    binding_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        profile: RuntimeProfileSnapshot,
        authorization_policy_ref: str,
        scope_snapshot_hash: str,
    ) -> "RuntimeActionRecoveryBinding":
        body = {
            "profile": profile.model_dump(mode="json"),
            "authorization_policy_ref": authorization_policy_ref,
            "scope_snapshot_hash": scope_snapshot_hash,
        }
        return cls(**body, binding_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_binding_hash(self) -> "RuntimeActionRecoveryBinding":
        body = self.model_dump(mode="json", exclude={"binding_hash"})
        if self.binding_hash != canonical_hash(body):
            raise ValueError("runtime Action recovery binding hash does not match")
        return self


class RuntimeActionPersistenceEnvelope(StrictContract):
    """Durable controller payload stored in the existing Action JSON column."""

    schema_name: Literal["bid.pure-agent.action-envelope.v1"] = (
        "bid.pure-agent.action-envelope.v1"
    )
    intent: ActionReservationIntent
    driver_payload: dict[str, Any] = Field(default_factory=dict, max_length=64)
    recovery_binding: RuntimeActionRecoveryBinding | None = None
    envelope_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        intent: ActionReservationIntent,
        driver_payload: dict[str, Any] | None = None,
        recovery_binding: RuntimeActionRecoveryBinding | None = None,
    ) -> "RuntimeActionPersistenceEnvelope":
        body = {
            "schema_name": "bid.pure-agent.action-envelope.v1",
            "intent": intent.model_dump(mode="json"),
            "driver_payload": driver_payload or {},
            "recovery_binding": (
                None
                if recovery_binding is None
                else recovery_binding.model_dump(mode="json")
            ),
        }
        encoded = canonical_json(body).encode("utf-8")
        if len(encoded) > MAX_ACTION_ENVELOPE_BYTES:
            raise PureAgentConflict("runtime Action envelope exceeds the size limit")
        return cls(**body, envelope_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_envelope_hash(self) -> "RuntimeActionPersistenceEnvelope":
        body = self.model_dump(mode="json", exclude={"envelope_hash"})
        legacy_body = dict(body)
        legacy_body.pop("recovery_binding", None)
        valid_hashes = {canonical_hash(body)}
        if self.recovery_binding is None:
            valid_hashes.add(canonical_hash(legacy_body))
        if self.envelope_hash not in valid_hashes:
            raise ValueError("runtime Action envelope hash does not match")
        if len(canonical_json(body).encode("utf-8")) > MAX_ACTION_ENVELOPE_BYTES:
            raise ValueError("runtime Action envelope exceeds the size limit")
        return self


@dataclass(frozen=True, slots=True)
class GuardedRuntimeAction:
    intent: ActionReservationIntent
    binding: ActionRuntimeBinding
    admission: ActionAdmissionDecision
    fencing_token: int = 1
    driver_payload: dict[str, Any] | None = None
    recovery_binding: RuntimeActionRecoveryBinding | None = None


class PersistedRuntimeAction(StrictContract):
    action_ref: Reference
    sequence: int = Field(ge=1)
    action_kind: AgentActionKind
    status: Literal[
        "accepted",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "ignored_late",
    ]
    effect_fence_ref: Reference
    effect_status: Literal[
        "reserved",
        "running",
        "succeeded",
        "failed",
        "uncertain",
        "cancelled",
        "ignored_late",
    ]
    fencing_token: int = Field(ge=1)
    effect_key: Reference
    effect_request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    effect_replay_policy: EffectReplayPolicy
    effect_result_ref: Reference | None = None
    effect_result_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    effect_error_code: str | None = Field(default=None, max_length=100)
    envelope: RuntimeActionPersistenceEnvelope

    @model_validator(mode="after")
    def validate_effect_result(self) -> "PersistedRuntimeAction":
        if (self.effect_result_ref is None) != (self.effect_result_hash is None):
            raise ValueError("persisted Effect result ref/hash must appear together")
        if self.effect_status == "succeeded" and self.effect_result_ref is None:
            raise ValueError("succeeded persisted Effect requires a result")
        return self


class RuntimeActionExecution(StrictContract):
    observation: ActionObservation
    effect_status: Literal["succeeded", "failed"]
    result_ref: Reference
    result_payload: Any
    error_code: str | None = Field(default=None, max_length=100)
    budget_usage: tuple[BudgetUsage, ...] = Field(default_factory=tuple, max_length=16)

    @model_validator(mode="after")
    def validate_budget_usage(self) -> "RuntimeActionExecution":
        resources = tuple(item.resource_type for item in self.budget_usage)
        if len(resources) != len(set(resources)):
            raise ValueError("budget usage resources must be unique")
        if (
            self.observation.artifact_ref != self.result_ref
            or self.observation.artifact_hash != canonical_hash(self.result_payload)
        ):
            raise ValueError(
                "Action Observation artifact does not match the persisted result"
            )
        return self


class RunningActionRecoveryContext(StrictContract):
    """Fresh facts supplied at recovery time; no replay authority is implied."""

    profile: RuntimeProfileSnapshot
    current_registry_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    cancellation: CancellationSnapshot
    lease_expired: bool
    authorization_valid: bool
    source_heads_valid: bool
    retry_attempt_count: int = Field(default=0, ge=0)


class RunningActionRecoveryContextProvider(Protocol):
    def for_recovery(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        binding: RuntimeActionRecoveryBinding,
    ) -> RunningActionRecoveryContext: ...


class RunningActionRecoveryUnavailable(RuntimeError):
    pass


class DisabledRunningActionRecoveryContextProvider:
    def for_recovery(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        binding: RuntimeActionRecoveryBinding,
    ) -> RunningActionRecoveryContext:
        del task, action, binding
        raise RunningActionRecoveryUnavailable(
            "Running Action recovery context provider is disabled"
        )


class RunningActionRecoveryPlan(StrictContract):
    directive: RecoveryDirective
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)
    execution: RuntimeActionExecution | None = None

    @model_validator(mode="after")
    def validate_execution_boundary(self) -> "RunningActionRecoveryPlan":
        consumes = self.directive is RecoveryDirective.CONSUME_PERSISTED_RESULT
        if consumes != (self.execution is not None):
            raise ValueError(
                "only persisted-result recovery may expose an execution"
            )
        return self


class RunningActionRecoveryController:
    """Decide recovery from durable facts without executing an Effect.

    RETRY_SAFE and RECONCILE remain explicit outcomes.  This controller never
    turns either directive into a Tool/model/effect call.
    """

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        context_provider: RunningActionRecoveryContextProvider | None = None,
        guard: RuntimeRecoveryGuard | None = None,
    ) -> None:
        self._repository = repository
        self._context_provider = (
            context_provider or DisabledRunningActionRecoveryContextProvider()
        )
        self._guard = guard or RuntimeRecoveryGuard()

    def assess(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RunningActionRecoveryPlan:
        binding = action.envelope.recovery_binding
        if binding is None:
            return self._blocked("RECOVERY_BINDING_UNAVAILABLE")
        try:
            context = self._context_provider.for_recovery(
                task=task,
                action=action,
                binding=binding,
            )
        except RunningActionRecoveryUnavailable:
            return self._blocked("RECOVERY_CONTEXT_PROVIDER_DISABLED")

        try:
            artifact = self._repository.load_running_action_observation_artifact(
                task_id=task.task_id,
                action_id=action.action_ref,
            )
        except PureAgentPersistenceError:
            return self._blocked("PERSISTED_RESULT_RECEIPT_INVALID")
        effect = self._effect_snapshot(task=task, action=action)
        checkpoint = RuntimeCheckpointSnapshot.build(
            checkpoint_ref=self._checkpoint_ref(task=task, action=action),
            task_ref=task.task_id,
            state_version=task.state_version,
            status="open",
            context_snapshot_ref=action.envelope.intent.context_snapshot_ref,
            profile_ref=binding.profile.profile_ref,
            profile_hash=binding.profile.profile_hash,
            registry_snapshot_ref=action.envelope.intent.registry_snapshot_ref,
            registry_snapshot_hash=action.envelope.intent.registry_snapshot_hash,
            action_ref=action.action_ref,
            effect_fence_ref=action.effect_fence_ref,
            result_persisted=artifact is not None,
            observation_accepted=False,
        )
        decision = self._guard.decide(
            task=task,
            checkpoint=checkpoint,
            profile=context.profile,
            current_registry_snapshot_hash=(
                context.current_registry_snapshot_hash
            ),
            cancellation=context.cancellation,
            effect=effect,
            lease_expired=context.lease_expired,
            authorization_valid=context.authorization_valid,
            source_heads_valid=context.source_heads_valid,
            retry_attempt_count=context.retry_attempt_count,
        )
        if decision.directive is not RecoveryDirective.CONSUME_PERSISTED_RESULT:
            return RunningActionRecoveryPlan(
                directive=decision.directive,
                reason_codes=decision.reason_codes,
            )
        if artifact is None:
            return self._blocked("PERSISTED_RESULT_BODY_UNAVAILABLE")
        try:
            self._repository.assert_action_budget_settled(
                task_id=task.task_id,
                action_id=action.action_ref,
            )
            execution = self._execution_from_artifact(
                task=task,
                action=action,
                artifact=artifact,
            )
        except (PureAgentPersistenceError, ValueError):
            return self._blocked("PERSISTED_RESULT_OR_BUDGET_INVALID")
        return RunningActionRecoveryPlan(
            directive=RecoveryDirective.CONSUME_PERSISTED_RESULT,
            reason_codes=decision.reason_codes,
            execution=execution,
        )

    @staticmethod
    def _effect_snapshot(
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> EffectFenceSnapshot:
        return EffectFenceSnapshot(
            effect_fence_ref=action.effect_fence_ref,
            task_ref=task.task_id,
            action_ref=action.action_ref,
            effect_key=action.effect_key,
            request_hash=action.effect_request_hash,
            replay_policy=action.effect_replay_policy,
            status=EffectFenceStatus(action.effect_status),
            fencing_token=action.fencing_token,
            result_ref=action.effect_result_ref,
            result_hash=action.effect_result_hash,
        )

    @staticmethod
    def _execution_from_artifact(
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        artifact: PersistedObservationArtifactRow,
    ) -> RuntimeActionExecution:
        observation = artifact.observation
        if (
            action.effect_status not in {"succeeded", "failed"}
            or action.status != action.effect_status
            or artifact.context_snapshot_ref
            != action.envelope.intent.context_snapshot_ref
            or observation.task_ref != task.task_id
            or observation.source_action_ref != action.action_ref
            or observation.action_sequence != action.sequence
            or observation.state_version != task.state_version
            or observation.artifact_ref != action.effect_result_ref
            or observation.artifact_hash != action.effect_result_hash
        ):
            raise PureAgentFenceRejected(
                "persisted result lost its Running Action recovery fence"
            )
        return RuntimeActionExecution(
            observation=observation,
            effect_status=action.effect_status,
            result_ref=observation.artifact_ref,
            result_payload=artifact.artifact,
            error_code=action.effect_error_code,
            # Budget usage is not reconstructed from memory.  Recovery already
            # required every persisted reservation to have a durable settlement.
            budget_usage=(),
        )

    @staticmethod
    def _checkpoint_ref(
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> str:
        digest = canonical_hash(
            {
                "task_ref": task.task_id,
                "state_version": task.state_version,
                "action_ref": action.action_ref,
                "effect_fence_ref": action.effect_fence_ref,
                "fencing_token": action.fencing_token,
            }
        )
        return f"running-action-recovery:{digest.removeprefix('sha256:')}"

    @staticmethod
    def _blocked(reason_code: str) -> RunningActionRecoveryPlan:
        return RunningActionRecoveryPlan(
            directive=RecoveryDirective.BLOCKED,
            reason_codes=(reason_code,),
        )


class SlotSuspensionDirective(StrictContract):
    name: str = Field(
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
    context_snapshot_ref: Reference
    slot_ref: Reference | None = None
    checkpoint_ref: Reference | None = None


@dataclass(frozen=True, slots=True)
class RuntimePostAction:
    """Driver-selected continuation after one Observation is accepted."""

    directive: RuntimePulseDirective = RuntimePulseDirective.CONTINUE
    transition_events: tuple[TaskTransitionEvent, ...] = ()
    next_action: GuardedRuntimeAction | None = None
    slot: SlotSuspensionDirective | None = None
    response: ResponseCommitDecision | None = None


class RuntimeControllerDriver(Protocol):
    """Cognitive/action adapter; it chooses purpose, never owns persistence."""

    async def prepare_next_action(
        self,
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> GuardedRuntimeAction: ...

    async def execute_active_action(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution: ...

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction: ...


class RuntimeControllerDriverUnavailable(RuntimeError):
    pass


class DisabledRuntimeControllerDriver:
    async def prepare_next_action(
        self,
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> GuardedRuntimeAction:
        del task, wakeup
        raise RuntimeControllerDriverUnavailable(
            "Pure Agent Runtime Controller driver is disabled"
        )

    async def execute_active_action(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        del task, action
        raise RuntimeControllerDriverUnavailable(
            "Pure Agent Runtime Controller driver is disabled"
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        del task, action, execution
        raise RuntimeControllerDriverUnavailable(
            "Pure Agent Runtime Controller driver is disabled"
        )


class MainAgentDecisionBoundary(StrictContract):
    """Frozen inputs required to execute one accepted model-decision Action."""

    turn_ref: Reference
    context: ContextAssemblyResult
    registry_snapshot: RegistrySnapshot | None = None

    @model_validator(mode="after")
    def validate_registry_binding(self) -> "MainAgentDecisionBoundary":
        snapshot = self.context.snapshot
        if self.registry_snapshot is None:
            if snapshot.registry_snapshot_ref is not None:
                raise ValueError("Context requires its Registry Snapshot")
        elif (
            snapshot.registry_snapshot_ref != self.registry_snapshot.snapshot_ref
            or snapshot.registry_snapshot_hash != self.registry_snapshot.snapshot_hash
        ):
            raise ValueError("Context and Registry Snapshot do not match")
        return self


class MainAgentDecisionBoundaryProvider(Protocol):
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> MainAgentDecisionBoundary: ...


class RuntimeActionGovernorPort(Protocol):
    """Apply B04-5 Guard policy to one model-selected Action intent."""

    def govern(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        driver_payload: dict[str, Any],
    ) -> GuardedRuntimeAction: ...


@dataclass(frozen=True, slots=True)
class RuntimeAdmissionContext:
    binding: ActionRuntimeBinding
    profile: RuntimeProfileSnapshot
    policy: RuntimePolicyCeiling
    budget_snapshot: RuntimeBudgetSnapshot
    budget_demands: tuple[BudgetDemand, ...]
    progress_window: ProgressWindow
    cancellation: CancellationSnapshot
    semantic_basis_hash: str
    existing_effect: EffectFenceSnapshot | None = None
    scope_snapshot_hash: str | None = None
    expected_output_hash: str | None = None
    active_parallel_reads: int = 0
    fencing_token: int = 1
    authorization_policy_ref: str | None = None


class RuntimeAdmissionContextProvider(Protocol):
    def for_action(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
    ) -> RuntimeAdmissionContext: ...


class GuardSuiteRuntimeActionGovernor:
    """Use the B04-5 Guard Suite as the sole Action admission authority."""

    def __init__(
        self,
        *,
        context_provider: RuntimeAdmissionContextProvider,
        guards: RuntimeGuardSuite | None = None,
    ) -> None:
        self._context_provider = context_provider
        self._guards = guards or RuntimeGuardSuite()

    def govern(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        driver_payload: dict[str, Any],
    ) -> GuardedRuntimeAction:
        context = self._context_provider.for_action(task=task, intent=intent)
        admission = self._guards.admit_action(
            task=task,
            intent=intent,
            binding=context.binding,
            profile=context.profile,
            policy=context.policy,
            budget_snapshot=context.budget_snapshot,
            budget_demands=context.budget_demands,
            progress_window=context.progress_window,
            cancellation=context.cancellation,
            existing_effect=context.existing_effect,
            semantic_basis_hash=context.semantic_basis_hash,
            scope_snapshot_hash=context.scope_snapshot_hash,
            expected_output_hash=context.expected_output_hash,
            active_parallel_reads=context.active_parallel_reads,
        )
        if admission.disposition is not AdmissionDisposition.ADMIT:
            raise PureAgentFenceRejected("Runtime Guard rejected the proposed Action")
        recovery_binding = None
        if (
            context.authorization_policy_ref is not None
            and context.scope_snapshot_hash is not None
        ):
            recovery_binding = RuntimeActionRecoveryBinding.build(
                profile=context.profile,
                authorization_policy_ref=context.authorization_policy_ref,
                scope_snapshot_hash=context.scope_snapshot_hash,
            )
        return GuardedRuntimeAction(
            intent=intent,
            binding=context.binding,
            admission=admission,
            fencing_token=context.fencing_token,
            driver_payload=driver_payload,
            recovery_binding=recovery_binding,
        )


class CapabilityActionExecutorPort(Protocol):
    """Execute a non-decision Action selected by the Main Agent."""

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution: ...

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction: ...


class DisabledCapabilityActionExecutor:
    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        del task, action
        raise RuntimeControllerDriverUnavailable(
            "Pure Agent capability Action executor is disabled"
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        del task, action, execution
        raise RuntimeControllerDriverUnavailable(
            "Pure Agent capability Action executor is disabled"
        )


class RegisteredCapabilityActionExecutor:
    """Dispatch accepted Action kinds to explicit capability handlers.

    This is deterministic contract dispatch after the Main Agent has selected
    an Action.  It is not an intent classifier, Tool Router, or workflow graph.
    """

    def __init__(
        self,
        handlers: Mapping[AgentActionKind, CapabilityActionExecutorPort],
    ) -> None:
        if AgentActionKind.MAIN_AGENT_DECISION in handlers:
            raise ValueError("Main Agent decision is owned by the controller driver")
        self._handlers = dict(handlers)

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        return await self._handler(action.action_kind).execute(
            task=task,
            action=action,
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        return await self._handler(action.action_kind).after_observation(
            task=task,
            action=action,
            execution=execution,
        )

    def _handler(self, action_kind: AgentActionKind) -> CapabilityActionExecutorPort:
        handler = self._handlers.get(action_kind)
        if handler is None:
            raise RuntimeControllerDriverUnavailable(
                f"no capability executor is registered for {action_kind.value}"
            )
        return handler


class DynamicActionControllerDriver:
    """Bridge the existing single-Action loop into the durable Controller.

    Main-Agent decision Actions are handled here.  Plan, Tool, Slot, and Answer
    Actions remain independently injected capabilities, so this adapter does
    not establish a fixed execution sequence.
    """

    _BOUNDARY_KEY = "main_agent_decision_boundary"

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        boundary_provider: MainAgentDecisionBoundaryProvider,
        governor: RuntimeActionGovernorPort,
        action_loop: DynamicActionLoopRuntime | None = None,
        capability_executor: CapabilityActionExecutorPort | None = None,
    ) -> None:
        self._repository = repository
        self._boundary_provider = boundary_provider
        self._governor = governor
        self._action_loop = action_loop or DynamicActionLoopRuntime()
        self._capability_executor = (
            capability_executor or DisabledCapabilityActionExecutor()
        )

    async def prepare_next_action(
        self,
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> GuardedRuntimeAction:
        boundary = await self._boundary_provider.prepare(task=task, wakeup=wakeup)
        self._repository.store_context_snapshot(boundary.context.snapshot)
        intent = self._action_loop.prepare_decision_reservation_intent(
            task=task,
            turn_ref=boundary.turn_ref,
            context=boundary.context,
            registry_snapshot=boundary.registry_snapshot,
        )
        payload = {
            self._BOUNDARY_KEY: boundary.model_dump(mode="json"),
        }
        return self._governor.govern(
            task=task,
            intent=intent,
            driver_payload=payload,
        )

    async def execute_active_action(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        if action.action_kind is not AgentActionKind.MAIN_AGENT_DECISION:
            return await self._capability_executor.execute(task=task, action=action)
        boundary = self._boundary(action)
        decision = await self._action_loop.decide_next(
            task=task,
            turn_ref=boundary.turn_ref,
            decision_action_ref=action.action_ref,
            decision_sequence=action.sequence,
            context=boundary.context,
            registry_snapshot=boundary.registry_snapshot,
        )
        result_payload = {
            "schema_name": "bid.pure-agent.decision-result.v1",
            "decision": decision.model_dump(mode="json"),
        }
        result_hash = canonical_hash(result_payload)
        result_ref = (
            "decision-result:" + result_hash.removeprefix("sha256:")
        )
        observation = self._action_loop.build_action_observation(
            task=task,
            action_sequence=action.sequence,
            kind=ActionObservationKind.CONTROL_DECISION,
            status=ActionObservationStatus.SUCCEEDED,
            artifact_ref=result_ref,
            artifact_hash=result_hash,
            summary="Main Agent proposed its next Action",
            material_progress=False,
        )
        return RuntimeActionExecution(
            observation=observation,
            effect_status="succeeded",
            result_ref=result_ref,
            result_payload=result_payload,
            error_code=None,
        )

    async def after_observation(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> RuntimePostAction:
        if action.action_kind is not AgentActionKind.MAIN_AGENT_DECISION:
            return await self._capability_executor.after_observation(
                task=task,
                action=action,
                execution=execution,
            )
        payload = execution.result_payload
        if (
            not isinstance(payload, dict)
            or payload.get("schema_name") != "bid.pure-agent.decision-result.v1"
            or not isinstance(payload.get("decision"), dict)
        ):
            raise PureAgentConflict("Main Agent decision result is invalid")
        decision = ActionLoopDecision.model_validate(payload["decision"])
        intent = self._action_loop.prepare_reservation_intent(
            task=task,
            decision=decision,
            decision_observation=execution.observation,
        )
        next_action = self._governor.govern(
            task=task,
            intent=intent,
            driver_payload=dict(action.envelope.driver_payload),
        )
        return RuntimePostAction(
            directive=RuntimePulseDirective.CONTINUE,
            next_action=next_action,
        )

    def _boundary(self, action: PersistedRuntimeAction) -> MainAgentDecisionBoundary:
        raw = action.envelope.driver_payload.get(self._BOUNDARY_KEY)
        if not isinstance(raw, dict):
            raise PureAgentConflict("Main Agent decision boundary is missing")
        boundary = MainAgentDecisionBoundary.model_validate(raw)
        intent = action.envelope.intent
        if (
            boundary.context.snapshot.snapshot_ref != intent.context_snapshot_ref
            or boundary.context.snapshot.snapshot_hash != intent.context_snapshot_hash
            or (
                boundary.registry_snapshot is None
                and intent.registry_snapshot_ref is not None
            )
            or (
                boundary.registry_snapshot is not None
                and (
                    boundary.registry_snapshot.snapshot_ref
                    != intent.registry_snapshot_ref
                    or boundary.registry_snapshot.snapshot_hash
                    != intent.registry_snapshot_hash
                )
            )
        ):
            raise PureAgentFenceRejected("persisted decision boundary drifted")
        return boundary


class ContinuationTokenUnavailable(RuntimeError):
    pass


class ContinuationTokenService:
    """Rebuild an opaque checkpoint token without returning it to the browser."""

    def __init__(self, secret: str | bytes | None = None) -> None:
        if isinstance(secret, str):
            secret_bytes = secret.encode("utf-8")
        else:
            secret_bytes = secret or b""
        self._secret = secret_bytes

    @property
    def available(self) -> bool:
        return len(self._secret) >= 32

    def issue(self, checkpoint: ContinuationCheckpoint) -> str:
        if not self.available:
            raise ContinuationTokenUnavailable(
                "continuation token service is not configured"
            )
        binding = canonical_json(
            {
                "checkpoint_ref": checkpoint.checkpoint_id,
                "task_ref": checkpoint.task_id,
                "slot_ref": checkpoint.slot_ref,
                "suspended_state_version": checkpoint.suspended_state_version,
                "context_snapshot_ref": checkpoint.context_snapshot_ref,
                "suspended_action_ref": checkpoint.suspended_action_ref,
                "effect_fence_ref": checkpoint.effect_fence_ref,
            }
        ).encode("utf-8")
        signature = hmac.new(self._secret, binding, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"crt1.{encoded}"

    def matches(self, checkpoint: ContinuationCheckpoint, candidate: str) -> bool:
        return hmac.compare_digest(self.issue(checkpoint), candidate)


class PureAgentRuntimeController:
    """Advance one durable Action boundary for one Task."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        driver: RuntimeControllerDriver | None = None,
        continuation_tokens: ContinuationTokenService | None = None,
        recovery_context_provider: (
            RunningActionRecoveryContextProvider | None
        ) = None,
    ) -> None:
        self.repository = repository
        self.driver = driver or DisabledRuntimeControllerDriver()
        self.continuation_tokens = continuation_tokens or ContinuationTokenService()
        self.recovery = RunningActionRecoveryController(
            repository,
            context_provider=recovery_context_provider,
        )

    async def advance_once(self, wakeup: RuntimeWakeup) -> RuntimePulseOutcome:
        # Keep the same Conversation -> Task lock order used by cancellation
        # and atomic Answer Commit so a local pulse cannot invert those fences.
        state = self.repository.load_runtime_task_state(
            task_id=wakeup.task_ref,
            conversation_id=wakeup.conversation_ref,
        )
        if state.session_id != wakeup.conversation_ref:
            raise PureAgentFenceRejected("runtime wakeup crossed conversation scope")
        if wakeup.observed_state_version > state.state_version:
            return self._outcome(
                state,
                RuntimePulseDisposition.STALE_WAKEUP,
                RuntimePulseDirective.STOP,
            )
        if state.status is AgentTaskStatus.PENDING:
            return self._outcome(
                state,
                RuntimePulseDisposition.PENDING,
                RuntimePulseDirective.WAIT,
            )
        if state.status in {
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
        }:
            return self._outcome(
                state,
                RuntimePulseDisposition.TERMINAL,
                RuntimePulseDirective.STOP,
            )

        if state.in_flight_action_ref is None:
            prepared = await self.driver.prepare_next_action(
                task=state,
                wakeup=wakeup,
            )
            reserved = self._reserve_action(state=state, prepared=prepared)
            return self._outcome(
                reserved,
                RuntimePulseDisposition.ACTION_RESERVED,
                RuntimePulseDirective.CONTINUE,
                action_ref=reserved.in_flight_action_ref,
            )

        action = self._load_active_action(state)
        if action.status != "accepted" or action.effect_status != "reserved":
            recovery = self.recovery.assess(task=state, action=action)
            if (
                recovery.directive
                is RecoveryDirective.CONSUME_PERSISTED_RESULT
            ):
                execution = recovery.execution
                if execution is None:
                    raise PureAgentConflict(
                        "persisted-result recovery omitted its execution"
                    )
                self._validate_execution(
                    state=state,
                    action=action,
                    execution=execution,
                )
                return await self._accept_settled_execution(
                    state=state,
                    action=action,
                    execution=execution,
                    settle_budget=False,
                )
            return self._outcome(
                state,
                RuntimePulseDisposition.RECOVERY_REQUIRED,
                RuntimePulseDirective.STOP,
                action_ref=action.action_ref,
                recovery_directive=recovery.directive,
                reason_codes=recovery.reason_codes,
            )

        self.repository.mark_effect_running(
            effect_fence_id=action.effect_fence_ref,
            fencing_token=action.fencing_token,
            expected_state_version=state.state_version,
        )
        execution = await self.driver.execute_active_action(
            task=state,
            action=action,
        )
        self._validate_execution(state=state, action=action, execution=execution)
        settlement = self.repository.settle_effect(
            effect_fence_id=action.effect_fence_ref,
            fencing_token=action.fencing_token,
            expected_state_version=state.state_version,
            status=execution.effect_status,
            result_ref=execution.result_ref,
            result=execution.result_payload,
            error_code=execution.error_code,
        )
        if settlement.status != execution.effect_status:
            raise PureAgentFenceRejected("Action effect settlement was not accepted")
        return await self._accept_settled_execution(
            state=state,
            action=action,
            execution=execution,
        )

    async def _accept_settled_execution(
        self,
        *,
        state: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
        settle_budget: bool = True,
    ) -> RuntimePulseOutcome:
        if settle_budget:
            self._settle_action_budget(
                state=state,
                action=action,
                execution=execution,
            )
        self.repository.store_observation_artifact(
            execution.observation,
            artifact=execution.result_payload,
            context_snapshot_ref=action.envelope.intent.context_snapshot_ref,
        )
        observed = self.repository.commit_transition(
            self._observation_event(state, execution.observation)
        ).state
        post = await self.driver.after_observation(
            task=observed,
            action=action,
            execution=execution,
        )
        final_state = self._apply_post_action(
            state=observed,
            action=action,
            post=post,
        )
        disposition = (
            RuntimePulseDisposition.PENDING
            if final_state.status is AgentTaskStatus.PENDING
            else RuntimePulseDisposition.TERMINAL
            if final_state.status
            in {
                AgentTaskStatus.COMPLETED,
                AgentTaskStatus.FAILED,
                AgentTaskStatus.CANCELLED,
            }
            else RuntimePulseDisposition.ACTION_COMPLETED
        )
        directive = post.directive
        if final_state.status is AgentTaskStatus.PENDING:
            directive = RuntimePulseDirective.WAIT
        elif final_state.status is not AgentTaskStatus.RUNNING:
            directive = RuntimePulseDirective.STOP
        return self._outcome(
            final_state,
            disposition,
            directive,
            action_ref=action.action_ref,
        )

    def fail_active_action(
        self,
        wakeup: RuntimeWakeup,
        *,
        error_code: str,
    ) -> RuntimePulseOutcome:
        """Settle one unhandled local Action failure and publish a terminal state.

        The dispatcher calls this only after the failed pulse transaction has
        rolled back.  No exception body, Provider payload, Prompt, or secret is
        persisted; the public event projector continues to expose only its
        generic Task failure message.
        """

        state = self.repository.load_runtime_task_state(
            task_id=wakeup.task_ref,
            conversation_id=wakeup.conversation_ref,
        )
        if state.status in {
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
        }:
            return self._outcome(
                state,
                RuntimePulseDisposition.TERMINAL,
                RuntimePulseDirective.STOP,
            )
        if state.status is not AgentTaskStatus.RUNNING:
            raise PureAgentFenceRejected(
                "only a running Task may accept a Runtime fatal error"
            )

        normalized_code = "".join(
            character
            if character.isalnum() or character in {"_", "-", "."}
            else "_"
            for character in str(error_code).strip().lower()
        )[:100] or "runtime_dispatch_failed"
        failure_body = {
            "schema_name": "bid.pure-agent.runtime-action-failure.v1",
            "error_code": normalized_code,
        }
        failure_hash = canonical_hash(failure_body)
        result_ref = (
            "runtime-action-failure:" + failure_hash.removeprefix("sha256:")
        )
        failed_action_ref = state.in_flight_action_ref

        if failed_action_ref is not None:
            action = self._load_active_action(state)
            if action.status == "accepted" and action.effect_status == "reserved":
                self.repository.mark_effect_running(
                    effect_fence_id=action.effect_fence_ref,
                    fencing_token=action.fencing_token,
                    expected_state_version=state.state_version,
                )
            elif action.status != "running" or action.effect_status != "running":
                raise PureAgentFenceRejected(
                    "Runtime fatal error cannot settle the active Action"
                )

            observation_body = {
                "task_ref": state.task_id,
                "source_action_ref": action.action_ref,
                "action_sequence": action.sequence,
                "state_version": state.state_version,
                "kind": ActionObservationKind.ERROR.value,
                "status": ActionObservationStatus.FAILED.value,
                "artifact_ref": result_ref,
                "artifact_hash": failure_hash,
                "summary": "Runtime Action failed safely",
                "material_progress": False,
                "progress_signal_refs": [],
                "limitation_codes": [normalized_code],
            }
            observation_hash = canonical_hash(observation_body)
            observation = ActionObservation(
                **observation_body,
                observation_ref=(
                    "observation:"
                    + observation_hash.removeprefix("sha256:")
                ),
                observation_hash=observation_hash,
            )
            execution = RuntimeActionExecution(
                observation=observation,
                effect_status="failed",
                result_ref=result_ref,
                result_payload=failure_body,
                error_code=normalized_code,
            )
            settlement = self.repository.settle_effect(
                effect_fence_id=action.effect_fence_ref,
                fencing_token=action.fencing_token,
                expected_state_version=state.state_version,
                status="failed",
                result_ref=result_ref,
                result=failure_body,
                error_code=normalized_code,
            )
            if settlement.status != "failed":
                raise PureAgentFenceRejected(
                    "Runtime fatal Action settlement was not accepted"
                )
            self._settle_action_budget(
                state=state,
                action=action,
                execution=execution,
            )
            self.repository.store_observation_artifact(
                observation,
                artifact=failure_body,
                context_snapshot_ref=action.envelope.intent.context_snapshot_ref,
            )
            state = self.repository.commit_transition(
                self._observation_event(state, observation)
            ).state

        error_ref = "runtime-error:" + canonical_hash(
            {
                "task_ref": state.task_id,
                "state_version": state.state_version,
                "action_ref": failed_action_ref,
                "error_code": normalized_code,
            }
        ).removeprefix("sha256:")
        terminal = self.repository.commit_transition(
            TaskTransitionEvent(
                event_id="runtime-fatal:" + canonical_hash(
                    {
                        "task_ref": state.task_id,
                        "state_version": state.state_version,
                        "error_ref": error_ref,
                    }
                ).removeprefix("sha256:"),
                task_id=state.task_id,
                expected_state_version=state.state_version,
                event_type=TaskEventType.FATAL_ERROR,
                effect_idempotency_key=None,
                action_ref=failed_action_ref,
                pending_context=None,
                resume_proof=None,
                execution_mode=None,
                plan_ref=None,
                observation_ref=None,
                result_committed=False,
                error_ref=error_ref,
                cancellation_fence_ref=None,
            )
        ).state
        return self._outcome(
            terminal,
            RuntimePulseDisposition.FAILED,
            RuntimePulseDirective.STOP,
            action_ref=failed_action_ref,
            reason_codes=(normalized_code,),
        )

    def _reserve_action(
        self,
        *,
        state: AgentTaskState,
        prepared: GuardedRuntimeAction,
    ) -> AgentTaskState:
        if (
            prepared.intent.task_ref != state.task_id
            or prepared.intent.state_version != state.state_version
            or prepared.fencing_token < 1
        ):
            raise PureAgentFenceRejected("next Action is stale or outside the Task")
        envelope = RuntimeActionPersistenceEnvelope.build(
            intent=prepared.intent,
            driver_payload=prepared.driver_payload,
            recovery_binding=prepared.recovery_binding,
        )
        event_digest = prepared.intent.intent_hash.removeprefix("sha256:")
        reservation = self.repository.reserve_governed_action(
            event_id=f"runtime-action-reserve:{event_digest}",
            intent=prepared.intent,
            binding=prepared.binding,
            admission=prepared.admission,
            fencing_token=prepared.fencing_token,
            persisted_action_payload=envelope.model_dump(mode="json"),
        )
        return reservation.action.state

    def _load_active_action(self, state: AgentTaskState) -> PersistedRuntimeAction:
        action_ref = state.in_flight_action_ref
        if action_ref is None:
            raise PureAgentConflict("Task has no active Action")
        row = self.repository.load_task_action(
            task_id=state.task_id,
            action_id=action_ref,
        )
        effect = self.repository.load_action_effect_fence(
            task_id=state.task_id,
            action_id=action_ref,
        )
        try:
            action_kind = AgentActionKind(row.action_type)
            envelope = RuntimeActionPersistenceEnvelope.model_validate(row.arguments_json)
        except (ValueError, TypeError) as exc:
            raise PureAgentConflict("persisted Runtime Action contract is invalid") from exc
        if (
            envelope.intent.task_ref != state.task_id
            or envelope.intent.action_kind is not action_kind
        ):
            raise PureAgentFenceRejected("persisted Runtime Action binding drifted")
        return PersistedRuntimeAction(
            action_ref=row.id,
            sequence=int(row.sequence_no),
            action_kind=action_kind,
            status=row.status,
            effect_fence_ref=effect.effect_fence_id,
            effect_status=effect.status,
            fencing_token=effect.fencing_token,
            effect_key=effect.effect_key,
            effect_request_hash=effect.request_hash,
            effect_replay_policy=EffectReplayPolicy(effect.replay_policy),
            effect_result_ref=effect.result_ref,
            effect_result_hash=effect.result_hash,
            effect_error_code=effect.error_code,
            envelope=envelope,
        )

    @staticmethod
    def _validate_execution(
        *,
        state: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> None:
        observation = execution.observation
        if (
            state.status is not AgentTaskStatus.RUNNING
            or state.in_flight_action_ref != action.action_ref
            or observation.task_ref != state.task_id
            or observation.source_action_ref != action.action_ref
            or observation.action_sequence != action.sequence
            or observation.state_version != state.state_version
        ):
            raise PureAgentFenceRejected("Action Observation lost its Task fence")

    @staticmethod
    def _observation_event(
        state: AgentTaskState,
        observation: ActionObservation,
    ) -> TaskTransitionEvent:
        digest = observation.observation_hash.removeprefix("sha256:")
        return TaskTransitionEvent(
            event_id=f"runtime-observation:{digest}",
            task_id=state.task_id,
            expected_state_version=state.state_version,
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

    def _apply_post_action(
        self,
        *,
        state: AgentTaskState,
        action: PersistedRuntimeAction,
        post: RuntimePostAction,
    ) -> AgentTaskState:
        exclusive = sum(
            item is not None for item in (post.next_action, post.slot, post.response)
        )
        if exclusive > 1:
            raise PureAgentConflict("post-Action continuation is ambiguous")
        current = state
        for event in post.transition_events:
            if (
                event.task_id != current.task_id
                or event.expected_state_version != current.state_version
            ):
                raise PureAgentFenceRejected("post-Action transition is stale")
            current = self.repository.commit_transition(event).state

        if post.next_action is not None:
            if post.directive is not RuntimePulseDirective.CONTINUE:
                raise PureAgentConflict("reserved continuation must continue")
            current = self._reserve_action(state=current, prepared=post.next_action)
        elif post.slot is not None:
            if post.directive is not RuntimePulseDirective.WAIT:
                raise PureAgentConflict("Slot continuation must wait")
            slot_ref = post.slot.slot_ref or str(uuid.uuid4())
            checkpoint_ref = post.slot.checkpoint_ref or str(uuid.uuid4())
            checkpoint = ContinuationCheckpoint(
                checkpoint_id=checkpoint_ref,
                task_id=current.task_id,
                slot_ref=slot_ref,
                suspended_state_version=current.state_version,
                execution_mode=current.execution_mode,
                context_snapshot_ref=post.slot.context_snapshot_ref,
                suspended_action_ref=action.action_ref,
                effect_fence_ref=action.effect_fence_ref,
                resume_token_hash="sha256:" + "0" * 64,
                status="open",
            )
            resume_token = self.continuation_tokens.issue(checkpoint)
            suspension = self.repository.suspend_for_slot(
                task_id=current.task_id,
                event_id=(
                    "runtime-slot:"
                    + hashlib.sha256(
                        f"{current.task_id}:{checkpoint_ref}".encode("utf-8")
                    ).hexdigest()
                ),
                name=post.slot.name,
                request_message=post.slot.request_message,
                input_model_ref=post.slot.input_model_ref,
                business_validator_refs=post.slot.business_validator_refs,
                context_snapshot_ref=post.slot.context_snapshot_ref,
                suspended_action_id=action.action_ref,
                effect_fence_id=action.effect_fence_ref,
                resume_token=resume_token,
                slot_id=slot_ref,
                checkpoint_id=checkpoint_ref,
            )
            current = suspension.state
        elif post.response is not None:
            if post.directive is not RuntimePulseDirective.STOP:
                raise PureAgentConflict("Response commit must stop the Task")
            committed = self.repository.commit_answer_response(
                post.response,
                created_by_ref="pure-agent:runtime-controller",
            )
            current = committed.state
        elif post.directive is RuntimePulseDirective.WAIT:
            raise PureAgentConflict("Runtime may wait only for a persisted Slot")
        elif post.directive is RuntimePulseDirective.STOP:
            if current.status is AgentTaskStatus.RUNNING:
                raise PureAgentConflict("running Task cannot stop without a safe result")
        return current

    @staticmethod
    def _outcome(
        state: AgentTaskState,
        disposition: RuntimePulseDisposition,
        directive: RuntimePulseDirective,
        *,
        action_ref: str | None = None,
        recovery_directive: RecoveryDirective | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> RuntimePulseOutcome:
        return RuntimePulseOutcome(
            task_ref=state.task_id,
            state_version=state.state_version,
            task_status=state.status,
            disposition=disposition,
            directive=directive,
            action_ref=action_ref,
            recovery_directive=recovery_directive,
            reason_codes=reason_codes,
        )

    def _settle_action_budget(
        self,
        *,
        state: AgentTaskState,
        action: PersistedRuntimeAction,
        execution: RuntimeActionExecution,
    ) -> None:
        reservations = self.repository.load_action_budget_reservations(
            task_id=state.task_id,
            action_id=action.action_ref,
        )
        usage_by_resource = {
            item.resource_type.value: item for item in execution.budget_usage
        }
        reserved_resources = {item.resource_type for item in reservations}
        if set(usage_by_resource) - reserved_resources:
            raise PureAgentFenceRejected("Action reported unreserved Budget usage")
        for reservation in reservations:
            usage = usage_by_resource.get(reservation.resource_type)
            actual = (
                reservation.amount
                if usage is None or not usage.verified
                else usage.actual_amount
            )
            if actual > reservation.amount:
                raise PureAgentFenceRejected("Action exceeded its Budget reservation")
            self.repository.settle_budget(
                task_id=state.task_id,
                resource_type=reservation.resource_type,
                reservation_entry_id=reservation.entry_id,
                actual_amount=actual,
                idempotency_key=f"runtime-budget-settle:{reservation.entry_id}",
                action_id=action.action_ref,
            )


class RuntimeDispatchPort(Protocol):
    @property
    def available(self) -> bool: ...

    async def dispatch(self, wakeup: RuntimeWakeup) -> RuntimePulseOutcome: ...


class DisabledRuntimeDispatcher:
    @property
    def available(self) -> bool:
        return False

    async def dispatch(self, wakeup: RuntimeWakeup) -> RuntimePulseOutcome:
        return RuntimePulseOutcome(
            task_ref=wakeup.task_ref,
            state_version=wakeup.observed_state_version,
            task_status=AgentTaskStatus.RUNNING,
            disposition=RuntimePulseDisposition.DISABLED,
            directive=RuntimePulseDirective.STOP,
        )


class LocalRuntimePulseDispatcher:
    """Bounded in-process pulse dispatcher for isolated local development.

    Every pulse gets a fresh transaction.  The loop follows only the
    controller's dynamic directive; it contains no action-kind routing or
    business-stage edges.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        controller_factory: Callable[[Any], PureAgentRuntimeController],
        max_pulses_per_dispatch: int = 64,
    ) -> None:
        if not 1 <= int(max_pulses_per_dispatch) <= 256:
            raise ValueError("max_pulses_per_dispatch must be between 1 and 256")
        self._session_factory = session_factory
        self._controller_factory = controller_factory
        self._max_pulses = int(max_pulses_per_dispatch)

    @property
    def available(self) -> bool:
        return True

    async def dispatch(self, wakeup: RuntimeWakeup) -> RuntimePulseOutcome:
        current_wakeup = wakeup
        last: RuntimePulseOutcome | None = None
        for pulse in range(1, self._max_pulses + 1):
            session = self._session_factory()
            try:
                controller = self._controller_factory(session)
                last = await controller.advance_once(current_wakeup)
                session.commit()
            except Exception as exc:
                session.rollback()
                safe_failure = getattr(exc, "failure", None)
                safe_code = getattr(getattr(safe_failure, "code", None), "value", None)
                safe_detail = (
                    str(exc)[:200]
                    if isinstance(exc, ActionLoopContractRejected)
                    or safe_failure is not None
                    or (
                        type(exc).__name__ == "CapabilityExecutionRejected"
                        and type(exc).__module__.endswith("capability_executors")
                    )
                    else "unavailable"
                )
                logger.error(
                    (
                        "pure_agent_runtime_dispatch_failed task=%s "
                        "error_type=%s safe_code=%s safe_detail=%s"
                    ),
                    wakeup.task_ref,
                    type(exc).__name__,
                    safe_code or "unavailable",
                    safe_detail,
                )
                failure_session = self._session_factory()
                try:
                    failure_controller = self._controller_factory(failure_session)
                    failure_outcome = failure_controller.fail_active_action(
                        current_wakeup,
                        error_code=(
                            safe_code
                            or (
                                "runtime_contract_rejected"
                                if isinstance(exc, ActionLoopContractRejected)
                                else "runtime_dispatch_failed"
                            )
                        ),
                    )
                    failure_session.commit()
                    return failure_outcome
                except Exception:
                    failure_session.rollback()
                    logger.exception(
                        "pure_agent_runtime_failure_settlement_failed task=%s",
                        wakeup.task_ref,
                    )
                    return RuntimePulseOutcome(
                        task_ref=wakeup.task_ref,
                        state_version=(
                            last.state_version
                            if last is not None
                            else wakeup.observed_state_version
                        ),
                        task_status=(
                            last.task_status
                            if last is not None
                            else AgentTaskStatus.RUNNING
                        ),
                        disposition=RuntimePulseDisposition.FAILED,
                        directive=RuntimePulseDirective.STOP,
                    )
                finally:
                    failure_session.close()
            finally:
                session.close()
            if last.directive is not RuntimePulseDirective.CONTINUE:
                return last
            current_wakeup = RuntimeWakeup.build(
                task_ref=wakeup.task_ref,
                conversation_ref=wakeup.conversation_ref,
                observed_state_version=last.state_version,
                reason=RuntimeWakeReason.ACTION_CONTINUATION,
                seed=f"{wakeup.wakeup_ref}:{pulse}:{last.state_version}",
            )
            await asyncio.sleep(0)
        if last is None:
            raise RuntimeError("local Runtime dispatcher produced no pulse")
        return RuntimePulseOutcome(
            task_ref=last.task_ref,
            state_version=last.state_version,
            task_status=last.task_status,
            disposition=RuntimePulseDisposition.YIELDED,
            directive=RuntimePulseDirective.STOP,
            action_ref=last.action_ref,
        )
