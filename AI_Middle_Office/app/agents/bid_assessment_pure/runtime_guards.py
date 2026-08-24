"""D408 runtime-governance contracts for one open Action boundary.

The guards in this module are deterministic and side-effect free.  They decide
what may be atomically reserved or accepted by the persistence layer; they do
not execute an Agent, model, Tool, recovery worker, or database transaction.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from .action_runtime import ActionReservationIntent
from .common import Reference, StrictContract
from .state import AgentTaskState, AgentTaskStatus, TERMINAL_STATUSES
from .tool_runtime import Sha256Digest, canonical_hash


class RuntimeGuardError(RuntimeError):
    """Safe base error for malformed or cross-scoped governance inputs."""


class RuntimeGuardRejected(RuntimeGuardError):
    """The proposed governance operation cannot be evaluated safely."""


class RuntimeResourceType(str, Enum):
    ACTIVE_DURATION_MS = "active_duration_ms"
    MODEL_CALLS = "model_calls"
    TOOL_CALLS = "tool_calls"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST_MICROUNITS = "cost_microunits"
    REPLANS = "replans"
    ANSWER_REPAIRS = "answer_repairs"


class RuntimeActionClass(str, Enum):
    MODEL = "model"
    TOOL = "tool"
    LOCAL = "local"


class ActionExecutionKind(str, Enum):
    DIRECT = "direct"
    DURABLE = "durable"


class EffectReplayPolicy(str, Enum):
    SAFE_IDEMPOTENT = "safe_idempotent"
    RECONCILE_REQUIRED = "reconcile_required"
    NO_REPLAY = "no_replay"


class EffectFenceStatus(str, Enum):
    RESERVED = "reserved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"
    IGNORED_LATE = "ignored_late"


class EffectDirective(str, Enum):
    RESERVE_NEW = "reserve_new"
    REUSE_RESULT = "reuse_result"
    AWAIT_EXISTING = "await_existing"
    RECONCILE = "reconcile"
    REJECT = "reject"


class LoopDisposition(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    STOP = "stop"


class AdmissionDisposition(str, Enum):
    ADMIT = "admit"
    REUSE_RESULT = "reuse_result"
    AWAIT_EXISTING = "await_existing"
    RECONCILE_REQUIRED = "reconcile_required"
    GUARD_OBSERVATION_REQUIRED = "guard_observation_required"
    SAFE_CLOSEOUT_REQUIRED = "safe_closeout_required"
    REJECT = "reject"


class LateResultDisposition(str, Enum):
    ACCEPT = "accept"
    REJECT_LATE = "reject_late"


class RecoveryDirective(str, Enum):
    NO_ACTION = "no_action"
    WAIT_FOR_LEASE = "wait_for_lease"
    WAIT_FOR_USER = "wait_for_user"
    CONSUME_PERSISTED_RESULT = "consume_persisted_result"
    CONTINUE_FROM_CHECKPOINT = "continue_from_checkpoint"
    RETRY_SAFE = "retry_safe"
    RECONCILE = "reconcile"
    BLOCKED = "blocked"


class RuntimeLimitSet(StrictContract):
    max_active_duration_ms: int = Field(ge=1_000)
    max_model_calls: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_total_input_tokens: int = Field(ge=0)
    max_total_output_tokens: int = Field(ge=0)
    max_cost_microunits: int = Field(ge=0)
    max_replans: int = Field(ge=0)
    max_answer_repairs: int = Field(ge=0)
    max_no_progress_actions: int = Field(ge=1)
    max_retry_attempts: int = Field(ge=0)
    max_parallel_read_calls: int = Field(ge=1)
    model_timeout_ms: int = Field(ge=100)
    tool_timeout_ms: int = Field(ge=100)

    def resource_limit(self, resource: RuntimeResourceType) -> int:
        mapping = {
            RuntimeResourceType.ACTIVE_DURATION_MS: self.max_active_duration_ms,
            RuntimeResourceType.MODEL_CALLS: self.max_model_calls,
            RuntimeResourceType.TOOL_CALLS: self.max_tool_calls,
            RuntimeResourceType.INPUT_TOKENS: self.max_total_input_tokens,
            RuntimeResourceType.OUTPUT_TOKENS: self.max_total_output_tokens,
            RuntimeResourceType.COST_MICROUNITS: self.max_cost_microunits,
            RuntimeResourceType.REPLANS: self.max_replans,
            RuntimeResourceType.ANSWER_REPAIRS: self.max_answer_repairs,
        }
        return mapping[resource]


class RuntimePolicyCeiling(StrictContract):
    policy_ref: Reference
    policy_hash: Sha256Digest
    limits: RuntimeLimitSet

    @classmethod
    def build(
        cls,
        *,
        policy_ref: str,
        limits: RuntimeLimitSet,
    ) -> "RuntimePolicyCeiling":
        body = {"policy_ref": policy_ref, "limits": limits.model_dump(mode="json")}
        return cls(**body, policy_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_hash(self) -> "RuntimePolicyCeiling":
        body = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_hash(body):
            raise ValueError("policy_hash does not match Runtime policy ceiling")
        return self


class RuntimeProfileSnapshot(StrictContract):
    profile_ref: Reference
    profile_hash: Sha256Digest
    policy_ref: Reference
    policy_hash: Sha256Digest
    limits: RuntimeLimitSet

    @classmethod
    def build(
        cls,
        *,
        profile_ref: str,
        policy: RuntimePolicyCeiling,
        limits: RuntimeLimitSet,
    ) -> "RuntimeProfileSnapshot":
        body = {
            "profile_ref": profile_ref,
            "policy_ref": policy.policy_ref,
            "policy_hash": policy.policy_hash,
            "limits": limits.model_dump(mode="json"),
        }
        return cls(**body, profile_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_hash(self) -> "RuntimeProfileSnapshot":
        body = self.model_dump(mode="json", exclude={"profile_hash"})
        if self.profile_hash != canonical_hash(body):
            raise ValueError("profile_hash does not match Runtime profile")
        return self


class BudgetBalance(StrictContract):
    resource_type: RuntimeResourceType
    unit: str = Field(min_length=1, max_length=32)
    limit_amount: int = Field(ge=0)
    reserved_amount: int = Field(ge=0)
    spent_amount: int = Field(ge=0)
    row_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_balance(self) -> "BudgetBalance":
        if self.reserved_amount + self.spent_amount > self.limit_amount:
            raise ValueError("budget balance exceeds its limit")
        return self

    @property
    def available_amount(self) -> int:
        return self.limit_amount - self.reserved_amount - self.spent_amount


class RuntimeBudgetSnapshot(StrictContract):
    snapshot_ref: Reference
    snapshot_hash: Sha256Digest
    task_ref: Reference
    profile_ref: Reference
    profile_hash: Sha256Digest
    balances: tuple[BudgetBalance, ...] = Field(default_factory=tuple, max_length=32)

    @classmethod
    def build(
        cls,
        *,
        snapshot_ref: str,
        task_ref: str,
        profile: RuntimeProfileSnapshot,
        balances: tuple[BudgetBalance, ...],
    ) -> "RuntimeBudgetSnapshot":
        body = {
            "snapshot_ref": snapshot_ref,
            "task_ref": task_ref,
            "profile_ref": profile.profile_ref,
            "profile_hash": profile.profile_hash,
            "balances": [item.model_dump(mode="json") for item in balances],
        }
        return cls(**body, snapshot_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_snapshot(self) -> "RuntimeBudgetSnapshot":
        resources = tuple(item.resource_type for item in self.balances)
        if len(resources) != len(set(resources)):
            raise ValueError("budget resources must be unique")
        body = self.model_dump(mode="json", exclude={"snapshot_hash"})
        if self.snapshot_hash != canonical_hash(body):
            raise ValueError("snapshot_hash does not match Budget snapshot")
        return self


class BudgetDemand(StrictContract):
    resource_type: RuntimeResourceType
    amount: int = Field(ge=1)


class BudgetReservationDirective(StrictContract):
    resource_type: RuntimeResourceType
    amount: int = Field(ge=1)
    idempotency_key: Reference


class BudgetGuardDecision(StrictContract):
    allowed: bool
    reservations: tuple[BudgetReservationDirective, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    exhausted_resources: tuple[RuntimeResourceType, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_shape(self) -> "BudgetGuardDecision":
        if self.allowed and (self.exhausted_resources or not self.reservations):
            raise ValueError("allowed Budget decision requires reservations only")
        if not self.allowed and (self.reservations or not self.exhausted_resources):
            raise ValueError("rejected Budget decision requires exhausted resources")
        return self


class BudgetUsage(StrictContract):
    resource_type: RuntimeResourceType
    actual_amount: int = Field(ge=0)
    verified: bool


class BudgetSettlementDirective(StrictContract):
    resource_type: RuntimeResourceType
    reservation_idempotency_key: Reference
    settle_amount: int = Field(ge=0)
    usage_unverified: bool


class RuntimeBudgetGuard:
    def evaluate(
        self,
        *,
        task: AgentTaskState,
        profile: RuntimeProfileSnapshot,
        snapshot: RuntimeBudgetSnapshot,
        demands: tuple[BudgetDemand, ...],
        reservation_seed: str,
    ) -> BudgetGuardDecision:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or snapshot.task_ref != task.task_id
            or snapshot.profile_ref != profile.profile_ref
            or snapshot.profile_hash != profile.profile_hash
            or not demands
        ):
            raise RuntimeGuardRejected("Budget inputs do not match the running Task")
        resources = tuple(item.resource_type for item in demands)
        if len(resources) != len(set(resources)):
            raise RuntimeGuardRejected("Budget demands must be unique")
        balances = {item.resource_type: item for item in snapshot.balances}
        exhausted: list[RuntimeResourceType] = []
        reservations: list[BudgetReservationDirective] = []
        for demand in demands:
            balance = balances.get(demand.resource_type)
            expected_limit = profile.limits.resource_limit(demand.resource_type)
            if balance is None or balance.limit_amount != expected_limit:
                raise RuntimeGuardRejected(
                    "Budget account is missing or exceeds the frozen Runtime Profile"
                )
            if demand.amount > balance.available_amount:
                exhausted.append(demand.resource_type)
                continue
            identity = canonical_hash(
                {
                    "seed": reservation_seed,
                    "resource_type": demand.resource_type.value,
                    "amount": demand.amount,
                    "row_version": balance.row_version,
                }
            ).removeprefix("sha256:")
            reservations.append(
                BudgetReservationDirective(
                    resource_type=demand.resource_type,
                    amount=demand.amount,
                    idempotency_key=f"budget-reservation:{identity}",
                )
            )
        if exhausted:
            return BudgetGuardDecision(
                allowed=False,
                exhausted_resources=tuple(exhausted),
                reason_codes=("BUDGET_EXHAUSTED",),
            )
        return BudgetGuardDecision(
            allowed=True,
            reservations=tuple(reservations),
            reason_codes=("BUDGET_AVAILABLE",),
        )

    def settle(
        self,
        *,
        reservations: tuple[BudgetReservationDirective, ...],
        usage: tuple[BudgetUsage, ...],
    ) -> tuple[BudgetSettlementDirective, ...]:
        usage_by_resource = {item.resource_type: item for item in usage}
        if len(usage_by_resource) != len(usage):
            raise RuntimeGuardRejected("Budget usage resources must be unique")
        directives: list[BudgetSettlementDirective] = []
        for reservation in reservations:
            item = usage_by_resource.get(reservation.resource_type)
            if item is None or not item.verified:
                amount = reservation.amount
                unverified = True
            else:
                if item.actual_amount > reservation.amount:
                    raise RuntimeGuardRejected(
                        "verified usage exceeds its Budget reservation"
                    )
                amount = item.actual_amount
                unverified = False
            directives.append(
                BudgetSettlementDirective(
                    resource_type=reservation.resource_type,
                    reservation_idempotency_key=reservation.idempotency_key,
                    settle_amount=amount,
                    usage_unverified=unverified,
                )
            )
        return tuple(directives)


class ActionExecutionRequirements(StrictContract):
    expected_duration_ms: int = Field(ge=0)
    requires_worker_isolation: bool = False
    requires_restart_recovery: bool = False
    requires_heartbeat: bool = False
    remote_completion_receipt: bool = False
    effect_may_outlive_request: bool = False


class ActionRuntimeBinding(StrictContract):
    binding_ref: Reference
    binding_hash: Sha256Digest
    action_class: RuntimeActionClass
    effect_type: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    replay_policy: EffectReplayPolicy
    reconciliation_supported: bool
    required_budget_resources: tuple[RuntimeResourceType, ...] = Field(
        default_factory=tuple,
        max_length=16,
    )
    requirements: ActionExecutionRequirements

    @classmethod
    def build(cls, **values: Any) -> "ActionRuntimeBinding":
        body = dict(values)
        body["action_class"] = RuntimeActionClass(body["action_class"]).value
        body["replay_policy"] = EffectReplayPolicy(body["replay_policy"]).value
        body["required_budget_resources"] = [
            RuntimeResourceType(item).value
            for item in body.get("required_budget_resources", ())
        ]
        requirements = body["requirements"]
        if isinstance(requirements, ActionExecutionRequirements):
            body["requirements"] = requirements.model_dump(mode="json")
        return cls(**body, binding_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_binding(self) -> "ActionRuntimeBinding":
        if len(self.required_budget_resources) != len(
            set(self.required_budget_resources)
        ):
            raise ValueError("required Budget resources must be unique")
        if (
            self.replay_policy is EffectReplayPolicy.RECONCILE_REQUIRED
            and not self.reconciliation_supported
        ):
            raise ValueError("reconcile-required binding must support reconciliation")
        required = set(self.required_budget_resources)
        mandatory = {RuntimeResourceType.ACTIVE_DURATION_MS}
        if self.action_class is RuntimeActionClass.MODEL:
            mandatory.update(
                {
                    RuntimeResourceType.MODEL_CALLS,
                    RuntimeResourceType.INPUT_TOKENS,
                    RuntimeResourceType.OUTPUT_TOKENS,
                    RuntimeResourceType.COST_MICROUNITS,
                }
            )
        elif self.action_class is RuntimeActionClass.TOOL:
            mandatory.add(RuntimeResourceType.TOOL_CALLS)
        if not mandatory.issubset(required):
            raise ValueError("binding omits mandatory Runtime Budget resources")
        body = self.model_dump(mode="json", exclude={"binding_hash"})
        if self.binding_hash != canonical_hash(body):
            raise ValueError("binding_hash does not match Action binding")
        return self


class RuntimeActionCandidate(StrictContract):
    candidate_ref: Reference
    candidate_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    action_type: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    action_intent_ref: Reference
    arguments_hash: Sha256Digest
    effect_key: Reference
    context_snapshot_ref: Reference
    context_snapshot_hash: Sha256Digest
    registry_snapshot_ref: Reference | None
    registry_snapshot_hash: Sha256Digest | None
    visible_tools_hash: Sha256Digest | None
    binding_ref: Reference
    binding_hash: Sha256Digest
    profile_ref: Reference
    profile_hash: Sha256Digest
    policy_ref: Reference
    policy_hash: Sha256Digest
    budget_snapshot_ref: Reference
    budget_snapshot_hash: Sha256Digest
    progress_window_ref: Reference
    progress_window_hash: Sha256Digest
    semantic_basis_hash: Sha256Digest
    scope_snapshot_hash: Sha256Digest | None
    expected_output_hash: Sha256Digest | None
    cancellation_state_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> "RuntimeActionCandidate":
        missing = self.registry_snapshot_ref is None
        if missing != (self.registry_snapshot_hash is None) or missing != (
            self.visible_tools_hash is None
        ):
            raise ValueError("candidate Registry binding is incomplete")
        body = self.model_dump(
            mode="json",
            exclude={"candidate_ref", "candidate_hash"},
        )
        digest = canonical_hash(body)
        if self.candidate_hash != digest:
            raise ValueError("candidate_hash does not match Action candidate")
        if self.candidate_ref != f"runtime-action:{digest.removeprefix('sha256:')}":
            raise ValueError("candidate_ref does not match Action candidate")
        return self


class ExecutionGuardDecision(StrictContract):
    allowed: bool
    execution_kind: ActionExecutionKind | None
    attempt_timeout_ms: int | None = Field(default=None, ge=100)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_shape(self) -> "ExecutionGuardDecision":
        if self.allowed != (
            self.execution_kind is not None and self.attempt_timeout_ms is not None
        ):
            raise ValueError("Execution decision shape is inconsistent")
        return self


class DirectDurableGuard:
    def decide(
        self,
        *,
        binding: ActionRuntimeBinding,
        profile: RuntimeProfileSnapshot,
        active_parallel_reads: int = 0,
    ) -> ExecutionGuardDecision:
        if active_parallel_reads < 0:
            raise RuntimeGuardRejected("active parallel read count cannot be negative")
        if (
            binding.action_class is RuntimeActionClass.TOOL
            and active_parallel_reads >= profile.limits.max_parallel_read_calls
        ):
            return ExecutionGuardDecision(
                allowed=False,
                execution_kind=None,
                attempt_timeout_ms=None,
                reason_codes=("PARALLEL_READ_LIMIT_REACHED",),
            )
        timeout = (
            profile.limits.model_timeout_ms
            if binding.action_class is RuntimeActionClass.MODEL
            else profile.limits.tool_timeout_ms
        )
        requirements = binding.requirements
        durable_reasons: list[str] = []
        if requirements.expected_duration_ms > timeout:
            durable_reasons.append("EXPECTED_DURATION_EXCEEDS_DIRECT_TIMEOUT")
        if requirements.requires_worker_isolation:
            durable_reasons.append("WORKER_ISOLATION_REQUIRED")
        if requirements.requires_restart_recovery:
            durable_reasons.append("RESTART_RECOVERY_REQUIRED")
        if requirements.requires_heartbeat:
            durable_reasons.append("HEARTBEAT_REQUIRED")
        if requirements.remote_completion_receipt:
            durable_reasons.append("REMOTE_COMPLETION_RECEIPT_REQUIRED")
        if requirements.effect_may_outlive_request:
            durable_reasons.append("EFFECT_MAY_OUTLIVE_REQUEST")
        if durable_reasons:
            return ExecutionGuardDecision(
                allowed=True,
                execution_kind=ActionExecutionKind.DURABLE,
                attempt_timeout_ms=timeout,
                reason_codes=tuple(durable_reasons),
            )
        return ExecutionGuardDecision(
            allowed=True,
            execution_kind=ActionExecutionKind.DIRECT,
            attempt_timeout_ms=timeout,
            reason_codes=("BOUNDED_DIRECT_EXECUTION",),
        )


class EffectFenceSnapshot(StrictContract):
    effect_fence_ref: Reference
    task_ref: Reference
    action_ref: Reference
    effect_key: Reference
    request_hash: Sha256Digest
    replay_policy: EffectReplayPolicy
    status: EffectFenceStatus
    fencing_token: int = Field(ge=1)
    result_ref: Reference | None = None
    result_hash: Sha256Digest | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "EffectFenceSnapshot":
        if (self.result_ref is None) != (self.result_hash is None):
            raise ValueError("Effect result ref and hash must appear together")
        if self.status is EffectFenceStatus.SUCCEEDED and self.result_ref is None:
            raise ValueError("succeeded Effect requires a result")
        return self


class EffectGuardDecision(StrictContract):
    directive: EffectDirective
    effect_key: Reference
    replay_policy: EffectReplayPolicy
    existing_effect_fence_ref: Reference | None = None
    reusable_result_ref: Reference | None = None
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_shape(self) -> "EffectGuardDecision":
        if self.directive is EffectDirective.RESERVE_NEW:
            if self.existing_effect_fence_ref is not None:
                raise ValueError("new Effect reservation cannot reference an old Fence")
        elif self.existing_effect_fence_ref is None:
            raise ValueError("existing Effect directive requires its Fence reference")
        if (self.directive is EffectDirective.REUSE_RESULT) != (
            self.reusable_result_ref is not None
        ):
            raise ValueError("only reusable Effect result may expose result_ref")
        return self


class RuntimeEffectGuard:
    def evaluate(
        self,
        *,
        candidate: RuntimeActionCandidate,
        binding: ActionRuntimeBinding,
        existing: EffectFenceSnapshot | None,
    ) -> EffectGuardDecision:
        if existing is None:
            return EffectGuardDecision(
                directive=EffectDirective.RESERVE_NEW,
                effect_key=candidate.effect_key,
                replay_policy=binding.replay_policy,
                reason_codes=("NEW_EFFECT_RESERVATION",),
            )
        if (
            existing.task_ref != candidate.task_ref
            or existing.effect_key != candidate.effect_key
            or existing.request_hash != candidate.arguments_hash
            or existing.replay_policy is not binding.replay_policy
        ):
            return EffectGuardDecision(
                directive=EffectDirective.REJECT,
                effect_key=candidate.effect_key,
                replay_policy=binding.replay_policy,
                existing_effect_fence_ref=existing.effect_fence_ref,
                reason_codes=("EFFECT_SCOPE_OR_REQUEST_MISMATCH",),
            )
        common = {
            "effect_key": candidate.effect_key,
            "replay_policy": binding.replay_policy,
            "existing_effect_fence_ref": existing.effect_fence_ref,
        }
        if existing.status is EffectFenceStatus.SUCCEEDED:
            return EffectGuardDecision(
                **common,
                directive=EffectDirective.REUSE_RESULT,
                reusable_result_ref=existing.result_ref,
                reason_codes=("AUTHORITATIVE_EFFECT_RESULT_EXISTS",),
            )
        if existing.status in {
            EffectFenceStatus.RESERVED,
            EffectFenceStatus.RUNNING,
        }:
            return EffectGuardDecision(
                **common,
                directive=EffectDirective.AWAIT_EXISTING,
                reason_codes=("EFFECT_ALREADY_IN_FLIGHT",),
            )
        if existing.status is EffectFenceStatus.UNCERTAIN:
            return EffectGuardDecision(
                **common,
                directive=EffectDirective.RECONCILE,
                reason_codes=("EFFECT_OUTCOME_UNCERTAIN",),
            )
        if binding.replay_policy is EffectReplayPolicy.RECONCILE_REQUIRED:
            return EffectGuardDecision(
                **common,
                directive=EffectDirective.RECONCILE,
                reason_codes=("EFFECT_RECONCILIATION_REQUIRED",),
            )
        return EffectGuardDecision(
            **common,
            directive=EffectDirective.REJECT,
            reason_codes=("EFFECT_CANNOT_BE_REPLAYED",),
        )


class ActionFingerprint(StrictContract):
    fingerprint_ref: Reference
    exact_hash: Sha256Digest
    semantic_hash: Sha256Digest


class ActionProgressRecord(StrictContract):
    record_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    fingerprint: ActionFingerprint
    observation_hash: Sha256Digest | None = None
    material_progress: bool
    progress_signal_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_progress(self) -> "ActionProgressRecord":
        if len(self.progress_signal_refs) != len(set(self.progress_signal_refs)):
            raise ValueError("progress signal refs must be unique")
        if self.material_progress and not self.progress_signal_refs:
            raise ValueError("material progress requires governed signal refs")
        return self


class ProgressWindow(StrictContract):
    window_ref: Reference
    window_hash: Sha256Digest
    task_ref: Reference
    records: tuple[ActionProgressRecord, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )

    @classmethod
    def build(
        cls,
        *,
        window_ref: str,
        task_ref: str,
        records: tuple[ActionProgressRecord, ...] = (),
    ) -> "ProgressWindow":
        body = {
            "window_ref": window_ref,
            "task_ref": task_ref,
            "records": [record.model_dump(mode="json") for record in records],
        }
        return cls(**body, window_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_window(self) -> "ProgressWindow":
        if any(record.task_ref != self.task_ref for record in self.records):
            raise ValueError("Progress records must belong to one Task")
        body = self.model_dump(mode="json", exclude={"window_hash"})
        if self.window_hash != canonical_hash(body):
            raise ValueError("window_hash does not match Progress window")
        return self


class LoopGuardDecision(StrictContract):
    disposition: LoopDisposition
    no_progress_streak: int = Field(ge=0)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)


class ProgressLoopGuard:
    @staticmethod
    def fingerprint(candidate: RuntimeActionCandidate) -> ActionFingerprint:
        semantic_hash = canonical_hash(
            {
                "action_type": candidate.action_type,
                "semantic_basis_hash": candidate.semantic_basis_hash,
                "scope_snapshot_hash": candidate.scope_snapshot_hash,
                "expected_output_hash": candidate.expected_output_hash,
                "registry_snapshot_hash": candidate.registry_snapshot_hash,
                "visible_tools_hash": candidate.visible_tools_hash,
            }
        )
        exact_hash = canonical_hash(
            {
                "candidate_hash": candidate.candidate_hash,
                "arguments_hash": candidate.arguments_hash,
                "context_snapshot_hash": candidate.context_snapshot_hash,
            }
        )
        identity = exact_hash.removeprefix("sha256:")
        return ActionFingerprint(
            fingerprint_ref=f"action-fingerprint:{identity}",
            exact_hash=exact_hash,
            semantic_hash=semantic_hash,
        )

    def before_action(
        self,
        *,
        fingerprint: ActionFingerprint,
        window: ProgressWindow,
        profile: RuntimeProfileSnapshot,
    ) -> LoopGuardDecision:
        streak = self._no_progress_streak(window.records)
        reasons: list[str] = []
        if window.records:
            last = window.records[-1]
            if (
                not last.material_progress
                and last.fingerprint.semantic_hash == fingerprint.semantic_hash
            ):
                reasons.append("REPEATED_SEMANTIC_ACTION_WITHOUT_PROGRESS")
        if len(window.records) >= 2:
            previous = window.records[-2]
            last = window.records[-1]
            if (
                not previous.material_progress
                and not last.material_progress
                and previous.fingerprint.semantic_hash == fingerprint.semantic_hash
                and last.fingerprint.semantic_hash != fingerprint.semantic_hash
            ):
                reasons.append("ACTION_CYCLE_WITHOUT_PROGRESS")
        if streak > profile.limits.max_no_progress_actions:
            return LoopGuardDecision(
                disposition=LoopDisposition.STOP,
                no_progress_streak=streak,
                reason_codes=tuple(reasons or ("NO_PROGRESS_LIMIT_EXCEEDED",)),
            )
        if reasons:
            return LoopGuardDecision(
                disposition=LoopDisposition.WARNING,
                no_progress_streak=streak,
                reason_codes=tuple(reasons),
            )
        return LoopGuardDecision(
            disposition=LoopDisposition.PASS,
            no_progress_streak=streak,
        )

    def after_observation(
        self,
        *,
        task_ref: str,
        state_version: int,
        fingerprint: ActionFingerprint,
        observation_hash: str,
        material_progress: bool,
        progress_signal_refs: tuple[str, ...],
        window: ProgressWindow,
        profile: RuntimeProfileSnapshot,
    ) -> tuple[ActionProgressRecord, LoopGuardDecision]:
        body = {
            "task_ref": task_ref,
            "state_version": state_version,
            "fingerprint": fingerprint.model_dump(mode="json"),
            "observation_hash": observation_hash,
            "material_progress": material_progress,
            "progress_signal_refs": list(progress_signal_refs),
        }
        record = ActionProgressRecord(
            **body,
            record_ref=(
                "progress-record:"
                + canonical_hash(body).removeprefix("sha256:")
            ),
        )
        records = (*window.records, record)
        streak = self._no_progress_streak(records)
        reasons: list[str] = []
        if not material_progress:
            reasons.append("ACTION_ADDED_NO_GOVERNED_INFORMATION")
            if window.records and (
                window.records[-1].observation_hash == observation_hash
            ):
                reasons.append("OBSERVATION_HASH_REPEATED")
        if streak > profile.limits.max_no_progress_actions:
            disposition = LoopDisposition.STOP
        elif streak == profile.limits.max_no_progress_actions:
            disposition = LoopDisposition.WARNING
        else:
            disposition = LoopDisposition.PASS
        return record, LoopGuardDecision(
            disposition=disposition,
            no_progress_streak=streak,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _no_progress_streak(records: tuple[ActionProgressRecord, ...]) -> int:
        count = 0
        for record in reversed(records):
            if record.material_progress:
                break
            count += 1
        return count


class CancellationSnapshot(StrictContract):
    task_ref: Reference
    state_version: int = Field(ge=1)
    cancellation_fence_ref: Reference | None = None


class ResultAcceptanceCandidate(StrictContract):
    task_ref: Reference
    action_ref: Reference
    accepted_state_version: int = Field(ge=1)
    effect_fence_ref: Reference
    fencing_token: int = Field(ge=1)
    request_hash: Sha256Digest


class LateResultDecision(StrictContract):
    disposition: LateResultDisposition
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)


class CancellationLateResultGuard:
    @staticmethod
    def assert_before_action(
        *,
        task: AgentTaskState,
        cancellation: CancellationSnapshot,
    ) -> None:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or cancellation.task_ref != task.task_id
            or cancellation.state_version != task.state_version
            or cancellation.cancellation_fence_ref is not None
        ):
            raise RuntimeGuardRejected(
                "cancelled, stale, or non-running Task cannot admit an Action"
            )

    @staticmethod
    def evaluate_result(
        *,
        task: AgentTaskState,
        cancellation: CancellationSnapshot,
        candidate: ResultAcceptanceCandidate,
        effect: EffectFenceSnapshot,
    ) -> LateResultDecision:
        accepted = (
            task.status is AgentTaskStatus.RUNNING
            and cancellation.task_ref == task.task_id == candidate.task_ref
            and cancellation.state_version == task.state_version
            and cancellation.cancellation_fence_ref is None
            and task.in_flight_action_ref == candidate.action_ref
            and task.state_version == candidate.accepted_state_version
            and effect.effect_fence_ref == candidate.effect_fence_ref
            and effect.task_ref == task.task_id
            and effect.action_ref == candidate.action_ref
            and effect.fencing_token == candidate.fencing_token
            and effect.request_hash == candidate.request_hash
            and effect.status
            not in {
                EffectFenceStatus.CANCELLED,
                EffectFenceStatus.IGNORED_LATE,
            }
        )
        if accepted:
            return LateResultDecision(
                disposition=LateResultDisposition.ACCEPT,
                reason_codes=("ACTIVE_STATE_AND_FENCE_MATCH",),
            )
        return LateResultDecision(
            disposition=LateResultDisposition.REJECT_LATE,
            reason_codes=("LATE_OR_FENCED_RESULT",),
        )


class RuntimeCheckpointSnapshot(StrictContract):
    checkpoint_ref: Reference
    checkpoint_hash: Sha256Digest
    task_ref: Reference
    state_version: int = Field(ge=1)
    status: str = Field(pattern=r"^(open|consumed|invalidated)$")
    context_snapshot_ref: Reference
    profile_ref: Reference
    profile_hash: Sha256Digest
    registry_snapshot_ref: Reference | None
    registry_snapshot_hash: Sha256Digest | None
    action_ref: Reference | None
    effect_fence_ref: Reference | None
    result_persisted: bool = False
    observation_accepted: bool = False

    @classmethod
    def build(cls, **values: Any) -> "RuntimeCheckpointSnapshot":
        body = dict(values)
        return cls(**body, checkpoint_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_checkpoint(self) -> "RuntimeCheckpointSnapshot":
        if (self.registry_snapshot_ref is None) != (
            self.registry_snapshot_hash is None
        ):
            raise ValueError("checkpoint Registry ref/hash must appear together")
        if self.observation_accepted and not self.result_persisted:
            raise ValueError("accepted Observation requires a persisted result")
        body = self.model_dump(mode="json", exclude={"checkpoint_hash"})
        if self.checkpoint_hash != canonical_hash(body):
            raise ValueError("checkpoint_hash does not match Runtime checkpoint")
        return self


class RecoveryGuardDecision(StrictContract):
    directive: RecoveryDirective
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)


class RuntimeRecoveryGuard:
    def decide(
        self,
        *,
        task: AgentTaskState,
        checkpoint: RuntimeCheckpointSnapshot,
        profile: RuntimeProfileSnapshot,
        current_registry_snapshot_hash: str | None,
        cancellation: CancellationSnapshot,
        effect: EffectFenceSnapshot | None,
        lease_expired: bool,
        authorization_valid: bool,
        source_heads_valid: bool,
        retry_attempt_count: int,
    ) -> RecoveryGuardDecision:
        if retry_attempt_count < 0:
            raise RuntimeGuardRejected("retry attempt count cannot be negative")
        if task.status in TERMINAL_STATUSES:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.NO_ACTION,
                reason_codes=("TERMINAL_TASK_NOT_RECOVERABLE",),
            )
        if (
            checkpoint.task_ref != task.task_id
            or cancellation.task_ref != task.task_id
            or cancellation.state_version != task.state_version
            or checkpoint.state_version > task.state_version
            or checkpoint.profile_ref != profile.profile_ref
            or checkpoint.profile_hash != profile.profile_hash
        ):
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("RECOVERY_SCOPE_OR_PROFILE_MISMATCH",),
            )
        if cancellation.cancellation_fence_ref is not None:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.NO_ACTION,
                reason_codes=("CANCELLATION_FENCE_PRESENT",),
            )
        if not lease_expired:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.WAIT_FOR_LEASE,
                reason_codes=("ACTIVE_LEASE_NOT_EXPIRED",),
            )
        if checkpoint.status != "open":
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("CHECKPOINT_NOT_OPEN",),
            )
        if not authorization_valid or not source_heads_valid:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("AUTHORIZATION_OR_SOURCE_REVALIDATION_FAILED",),
            )
        if checkpoint.registry_snapshot_hash != current_registry_snapshot_hash:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("REGISTRY_SNAPSHOT_CHANGED",),
            )
        if task.status is AgentTaskStatus.PENDING:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.WAIT_FOR_USER,
                reason_codes=("PENDING_REMAINS_USER_INPUT_ONLY",),
            )
        if (
            effect is not None
            and (
                effect.task_ref != task.task_id
                or effect.effect_fence_ref != checkpoint.effect_fence_ref
                or (
                    checkpoint.action_ref is not None
                    and effect.action_ref != checkpoint.action_ref
                )
            )
        ):
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("RECOVERY_EFFECT_SCOPE_MISMATCH",),
            )
        if (
            task.in_flight_action_ref is not None
            and checkpoint.action_ref != task.in_flight_action_ref
        ):
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("RECOVERY_ACTION_SCOPE_MISMATCH",),
            )
        if checkpoint.observation_accepted:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.CONTINUE_FROM_CHECKPOINT,
                reason_codes=("AUTHORITATIVE_OBSERVATION_ALREADY_ACCEPTED",),
            )
        if checkpoint.result_persisted:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.CONSUME_PERSISTED_RESULT,
                reason_codes=("PERSISTED_RESULT_MUST_NOT_BE_REEXECUTED",),
            )
        if checkpoint.effect_fence_ref is not None and effect is None:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.BLOCKED,
                reason_codes=("CHECKPOINT_EFFECT_FENCE_MISSING",),
            )
        if effect is None:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.CONTINUE_FROM_CHECKPOINT,
                reason_codes=("NO_EFFECT_STARTED",),
            )
        if effect.status is EffectFenceStatus.SUCCEEDED:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.CONSUME_PERSISTED_RESULT,
                reason_codes=("SUCCEEDED_EFFECT_MUST_NOT_BE_REEXECUTED",),
            )
        if effect.status is EffectFenceStatus.FAILED:
            return RecoveryGuardDecision(
                directive=RecoveryDirective.CONSUME_PERSISTED_RESULT,
                reason_codes=("FAILED_EFFECT_IS_AN_AUTHORITATIVE_OBSERVATION",),
            )
        if effect.status is EffectFenceStatus.UNCERTAIN or (
            effect.replay_policy is EffectReplayPolicy.RECONCILE_REQUIRED
        ):
            return RecoveryGuardDecision(
                directive=RecoveryDirective.RECONCILE,
                reason_codes=("EFFECT_RECONCILIATION_REQUIRED",),
            )
        if (
            effect.replay_policy is EffectReplayPolicy.SAFE_IDEMPOTENT
            and effect.status
            in {EffectFenceStatus.RESERVED, EffectFenceStatus.RUNNING}
            and retry_attempt_count < profile.limits.max_retry_attempts
        ):
            return RecoveryGuardDecision(
                directive=RecoveryDirective.RETRY_SAFE,
                reason_codes=("SAFE_REPLAY_WITHIN_ATTEMPT_LIMIT",),
            )
        return RecoveryGuardDecision(
            directive=RecoveryDirective.BLOCKED,
            reason_codes=("EFFECT_NOT_SAFELY_RECOVERABLE",),
        )


class ActionAdmissionDecision(StrictContract):
    decision_ref: Reference
    decision_hash: Sha256Digest
    disposition: AdmissionDisposition
    candidate: RuntimeActionCandidate
    fingerprint: ActionFingerprint
    execution: ExecutionGuardDecision | None
    budget: BudgetGuardDecision | None
    effect: EffectGuardDecision
    loop: LoopGuardDecision
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_decision(self) -> "ActionAdmissionDecision":
        if self.disposition is AdmissionDisposition.ADMIT:
            if (
                self.execution is None
                or not self.execution.allowed
                or self.budget is None
                or not self.budget.allowed
                or self.effect.directive is not EffectDirective.RESERVE_NEW
                or self.loop.disposition is not LoopDisposition.PASS
            ):
                raise ValueError("admitted Action requires every Guard to pass")
        elif self.disposition is AdmissionDisposition.REUSE_RESULT:
            if (
                self.effect.directive is not EffectDirective.REUSE_RESULT
                or self.execution is not None
                or self.budget is not None
            ):
                raise ValueError("reuse disposition requires an existing Effect result")
        elif self.disposition is AdmissionDisposition.AWAIT_EXISTING:
            if (
                self.effect.directive is not EffectDirective.AWAIT_EXISTING
                or self.execution is not None
                or self.budget is not None
            ):
                raise ValueError("await disposition requires an in-flight Effect")
        elif self.disposition is AdmissionDisposition.RECONCILE_REQUIRED:
            if (
                self.effect.directive is not EffectDirective.RECONCILE
                or self.execution is not None
                or self.budget is not None
            ):
                raise ValueError("reconcile disposition requires uncertain Effect state")
        elif self.disposition is AdmissionDisposition.GUARD_OBSERVATION_REQUIRED:
            if (
                self.loop.disposition is not LoopDisposition.WARNING
                or self.execution is not None
                or self.budget is not None
            ):
                raise ValueError("guard Observation requires a Loop warning")
        elif self.disposition is AdmissionDisposition.SAFE_CLOSEOUT_REQUIRED:
            if not (
                self.loop.disposition is LoopDisposition.STOP
                or (self.budget is not None and not self.budget.allowed)
            ):
                raise ValueError("safe closeout requires Loop or Budget exhaustion")
        body = self.model_dump(
            mode="json",
            exclude={"decision_ref", "decision_hash"},
        )
        digest = canonical_hash(body)
        if self.decision_hash != digest:
            raise ValueError("decision_hash does not match Action admission")
        if self.decision_ref != f"guard-decision:{digest.removeprefix('sha256:')}":
            raise ValueError("decision_ref does not match Action admission")
        return self


class RuntimeGuardSuite:
    """Apply governance to one Action candidate; never advances the Agent loop."""

    def __init__(
        self,
        *,
        budget: RuntimeBudgetGuard | None = None,
        progress: ProgressLoopGuard | None = None,
        effects: RuntimeEffectGuard | None = None,
        execution: DirectDurableGuard | None = None,
        cancellation: CancellationLateResultGuard | None = None,
        recovery: RuntimeRecoveryGuard | None = None,
    ) -> None:
        self.budget = budget or RuntimeBudgetGuard()
        self.progress = progress or ProgressLoopGuard()
        self.effects = effects or RuntimeEffectGuard()
        self.execution = execution or DirectDurableGuard()
        self.cancellation = cancellation or CancellationLateResultGuard()
        self.recovery = recovery or RuntimeRecoveryGuard()

    def admit_action(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        binding: ActionRuntimeBinding,
        profile: RuntimeProfileSnapshot,
        policy: RuntimePolicyCeiling,
        budget_snapshot: RuntimeBudgetSnapshot,
        budget_demands: tuple[BudgetDemand, ...],
        progress_window: ProgressWindow,
        cancellation: CancellationSnapshot,
        existing_effect: EffectFenceSnapshot | None = None,
        semantic_basis_hash: str,
        scope_snapshot_hash: str | None = None,
        expected_output_hash: str | None = None,
        active_parallel_reads: int = 0,
    ) -> ActionAdmissionDecision:
        self._validate_profile(profile=profile, policy=policy)
        if (
            task.task_id != intent.task_ref
            or task.state_version != intent.state_version
            or task.in_flight_action_ref is not None
            or progress_window.task_ref != task.task_id
            or budget_snapshot.task_ref != task.task_id
            or budget_snapshot.profile_ref != profile.profile_ref
            or budget_snapshot.profile_hash != profile.profile_hash
        ):
            raise RuntimeGuardRejected(
                "Action intent does not match its safe reservation boundary"
            )
        self.cancellation.assert_before_action(
            task=task,
            cancellation=cancellation,
        )
        demanded = {item.resource_type for item in budget_demands}
        if not set(binding.required_budget_resources).issubset(demanded):
            raise RuntimeGuardRejected(
                "Action Budget demands omit a binding-required resource"
            )
        if (
            binding.action_class is RuntimeActionClass.TOOL
            and scope_snapshot_hash is None
        ):
            raise RuntimeGuardRejected(
                "Tool Action requires a governed Scope Snapshot hash"
            )
        candidate = self._candidate(
            intent=intent,
            binding=binding,
            profile=profile,
            policy=policy,
            budget_snapshot=budget_snapshot,
            progress_window=progress_window,
            semantic_basis_hash=semantic_basis_hash,
            scope_snapshot_hash=scope_snapshot_hash,
            expected_output_hash=expected_output_hash,
            cancellation_state_version=cancellation.state_version,
        )
        fingerprint = self.progress.fingerprint(candidate)
        effect = self.effects.evaluate(
            candidate=candidate,
            binding=binding,
            existing=existing_effect,
        )
        loop = self.progress.before_action(
            fingerprint=fingerprint,
            window=progress_window,
            profile=profile,
        )

        if effect.directive is EffectDirective.REUSE_RESULT:
            return self._decision(
                disposition=AdmissionDisposition.REUSE_RESULT,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=None,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=("REUSE_AUTHORITATIVE_EFFECT_RESULT",),
            )
        if effect.directive is EffectDirective.AWAIT_EXISTING:
            return self._decision(
                disposition=AdmissionDisposition.AWAIT_EXISTING,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=None,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=("AWAIT_EXISTING_EFFECT",),
            )
        if effect.directive is EffectDirective.RECONCILE:
            return self._decision(
                disposition=AdmissionDisposition.RECONCILE_REQUIRED,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=None,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=("RECONCILE_BEFORE_REPLAY",),
            )
        if effect.directive is EffectDirective.REJECT:
            return self._decision(
                disposition=AdmissionDisposition.REJECT,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=None,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=effect.reason_codes,
            )
        if loop.disposition is LoopDisposition.STOP:
            return self._decision(
                disposition=AdmissionDisposition.SAFE_CLOSEOUT_REQUIRED,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=None,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=("NO_PROGRESS_LIMIT_EXCEEDED",),
            )
        if loop.disposition is LoopDisposition.WARNING:
            return self._decision(
                disposition=AdmissionDisposition.GUARD_OBSERVATION_REQUIRED,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=None,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=("NO_PROGRESS_OBSERVATION_REQUIRED",),
            )
        execution = self.execution.decide(
            binding=binding,
            profile=profile,
            active_parallel_reads=active_parallel_reads,
        )
        if not execution.allowed:
            return self._decision(
                disposition=AdmissionDisposition.REJECT,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=execution,
                budget=None,
                effect=effect,
                loop=loop,
                reasons=execution.reason_codes,
            )
        budget = self.budget.evaluate(
            task=task,
            profile=profile,
            snapshot=budget_snapshot,
            demands=budget_demands,
            reservation_seed=candidate.candidate_ref,
        )
        if not budget.allowed:
            return self._decision(
                disposition=AdmissionDisposition.SAFE_CLOSEOUT_REQUIRED,
                candidate=candidate,
                fingerprint=fingerprint,
                execution=execution,
                budget=budget,
                effect=effect,
                loop=loop,
                reasons=("BUDGET_EXHAUSTED_SAFE_CLOSEOUT",),
            )
        return self._decision(
            disposition=AdmissionDisposition.ADMIT,
            candidate=candidate,
            fingerprint=fingerprint,
            execution=execution,
            budget=budget,
            effect=effect,
            loop=loop,
            reasons=("ACTION_GUARDS_ACCEPTED",),
        )

    @staticmethod
    def _validate_profile(
        *,
        profile: RuntimeProfileSnapshot,
        policy: RuntimePolicyCeiling,
    ) -> None:
        if profile.policy_ref != policy.policy_ref or profile.policy_hash != policy.policy_hash:
            raise RuntimeGuardRejected("Runtime Profile is outside its Policy ceiling")
        profile_values = profile.limits.model_dump(mode="python")
        policy_values = policy.limits.model_dump(mode="python")
        if any(profile_values[name] > policy_values[name] for name in profile_values):
            raise RuntimeGuardRejected("Runtime Profile exceeds its Policy ceiling")

    @staticmethod
    def _candidate(
        *,
        intent: ActionReservationIntent,
        binding: ActionRuntimeBinding,
        profile: RuntimeProfileSnapshot,
        policy: RuntimePolicyCeiling,
        budget_snapshot: RuntimeBudgetSnapshot,
        progress_window: ProgressWindow,
        semantic_basis_hash: str,
        scope_snapshot_hash: str | None,
        expected_output_hash: str | None,
        cancellation_state_version: int,
    ) -> RuntimeActionCandidate:
        body = {
            "task_ref": intent.task_ref,
            "state_version": intent.state_version,
            "action_type": intent.action_kind.value,
            "action_intent_ref": intent.intent_ref,
            "arguments_hash": intent.arguments_hash,
            "effect_key": (
                "action-effect:"
                + canonical_hash(
                    {
                        "task_ref": intent.task_ref,
                        "action_type": intent.action_kind.value,
                        "arguments_hash": intent.arguments_hash,
                        "binding_hash": binding.binding_hash,
                        "scope_snapshot_hash": scope_snapshot_hash,
                    }
                ).removeprefix("sha256:")
            ),
            "context_snapshot_ref": intent.context_snapshot_ref,
            "context_snapshot_hash": intent.context_snapshot_hash,
            "registry_snapshot_ref": intent.registry_snapshot_ref,
            "registry_snapshot_hash": intent.registry_snapshot_hash,
            "visible_tools_hash": intent.visible_tools_hash,
            "binding_ref": binding.binding_ref,
            "binding_hash": binding.binding_hash,
            "profile_ref": profile.profile_ref,
            "profile_hash": profile.profile_hash,
            "policy_ref": policy.policy_ref,
            "policy_hash": policy.policy_hash,
            "budget_snapshot_ref": budget_snapshot.snapshot_ref,
            "budget_snapshot_hash": budget_snapshot.snapshot_hash,
            "progress_window_ref": progress_window.window_ref,
            "progress_window_hash": progress_window.window_hash,
            "semantic_basis_hash": semantic_basis_hash,
            "scope_snapshot_hash": scope_snapshot_hash,
            "expected_output_hash": expected_output_hash,
            "cancellation_state_version": cancellation_state_version,
        }
        digest = canonical_hash(body)
        return RuntimeActionCandidate(
            **body,
            candidate_ref=f"runtime-action:{digest.removeprefix('sha256:')}",
            candidate_hash=digest,
        )

    @staticmethod
    def _decision(
        *,
        disposition: AdmissionDisposition,
        candidate: RuntimeActionCandidate,
        fingerprint: ActionFingerprint,
        execution: ExecutionGuardDecision | None,
        budget: BudgetGuardDecision | None,
        effect: EffectGuardDecision,
        loop: LoopGuardDecision,
        reasons: tuple[str, ...],
    ) -> ActionAdmissionDecision:
        body = {
            "disposition": disposition.value,
            "candidate": candidate.model_dump(mode="json"),
            "fingerprint": fingerprint.model_dump(mode="json"),
            "execution": (
                None if execution is None else execution.model_dump(mode="json")
            ),
            "budget": None if budget is None else budget.model_dump(mode="json"),
            "effect": effect.model_dump(mode="json"),
            "loop": loop.model_dump(mode="json"),
            "reason_codes": list(reasons),
        }
        digest = canonical_hash(body)
        return ActionAdmissionDecision(
            **body,
            decision_ref=f"guard-decision:{digest.removeprefix('sha256:')}",
            decision_hash=digest,
        )
