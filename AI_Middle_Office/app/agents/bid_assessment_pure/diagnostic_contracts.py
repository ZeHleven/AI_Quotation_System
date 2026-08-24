"""Redacted, administrator-only diagnostic contracts for the Pure Agent.

These views describe persisted control-plane facts.  They intentionally omit
prompts, message and evidence content, Tool arguments/results, authorization
snapshots, provider receipts, resume credentials, Effect keys, and raw errors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Reference, StrictContract
from .planning import ExecutionMode
from .slots import PendingPhase
from .state import AgentTaskStatus


class DiagnosticTaskStatusCounts(StrictContract):
    running: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)


class DiagnosticTaskListItem(StrictContract):
    task_ref: Reference
    conversation_ref: Reference
    status: AgentTaskStatus
    execution_mode: ExecutionMode
    state_version: int = Field(ge=1)
    pending_phase: PendingPhase | None = None
    assessment_bound: bool
    has_open_action: bool
    action_count: int = Field(ge=0)
    call_count: int = Field(ge=0)
    checkpoint_count: int = Field(ge=0)
    budget_account_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class DiagnosticTaskPage(StrictContract):
    items: tuple[DiagnosticTaskListItem, ...] = Field(max_length=100)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    status_counts: DiagnosticTaskStatusCounts


class DiagnosticTaskState(StrictContract):
    task_ref: Reference
    conversation_ref: Reference
    status: AgentTaskStatus
    execution_mode: ExecutionMode
    state_version: int = Field(ge=1)
    pending_phase: PendingPhase | None = None
    has_plan: bool
    has_open_action: bool
    observation_count: int = Field(ge=0)
    plan_version_count: int = Field(ge=0)
    context_snapshot_count: int = Field(ge=0)
    response_version_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None


class DiagnosticStateTransition(StrictContract):
    transition_no: int = Field(ge=1)
    activity: str = Field(min_length=1, max_length=80)
    state_version_before: int = Field(ge=0)
    state_version_after: int = Field(ge=1)
    status_before: AgentTaskStatus | None = None
    status_after: AgentTaskStatus
    action_no: int | None = Field(default=None, ge=1)
    occurred_at: datetime


class DiagnosticCallTrace(StrictContract):
    call_no: int = Field(ge=1)
    kind: Literal["model", "tool"]
    operation: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=24)
    state_version: int = Field(ge=1)
    sequence_no: int = Field(ge=1, le=64)
    action_no: int | None = Field(default=None, ge=1)
    guard_outcome: Literal["passed", "rejected", "not_recorded"]
    error_class: str | None = Field(default=None, min_length=1, max_length=80)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_micro_usd: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    created_at: datetime
    completed_at: datetime | None = None


class DiagnosticBudgetEntry(StrictContract):
    entry_no: int = Field(ge=1)
    kind: Literal["reserve", "settle", "release", "charge"]
    amount: int = Field(ge=0)
    reserved_after: int = Field(ge=0)
    actual_after: int = Field(ge=0)
    action_no: int | None = Field(default=None, ge=1)
    created_at: datetime


class DiagnosticBudgetAccount(StrictContract):
    resource_type: str = Field(min_length=1, max_length=64)
    unit: str = Field(min_length=1, max_length=32)
    limit_amount: int = Field(ge=0)
    reserved_amount: int = Field(ge=0)
    actual_amount: int = Field(ge=0)
    remaining_amount: int = Field(ge=0)
    utilization_percent: float = Field(ge=0, le=100)
    entries: tuple[DiagnosticBudgetEntry, ...] = Field(max_length=500)


class DiagnosticLoopAction(StrictContract):
    action_no: int = Field(ge=1)
    action_type: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=24)
    exact_repeat_of_action_no: int | None = Field(default=None, ge=1)
    repeats_prior_result: bool
    produced_new_result_fingerprint: bool
    created_at: datetime


class DiagnosticLoopTrace(StrictContract):
    coverage: Literal["exact_fingerprint_only"] = "exact_fingerprint_only"
    semantic_progress_decision_available: Literal[False] = False
    exact_repeat_count: int = Field(ge=0)
    repeated_result_count: int = Field(ge=0)
    ignored_late_count: int = Field(ge=0)
    actions: tuple[DiagnosticLoopAction, ...] = Field(max_length=500)


class DiagnosticCancellationTrace(StrictContract):
    present: bool
    cancellation_state_version: int | None = Field(default=None, ge=1)
    requested_at: datetime | None = None
    actor_kind: Literal["user", "system", "service", "unknown"] | None = None
    reason_recorded: bool = False
    cancelled_effect_count: int = Field(ge=0)
    ignored_late_result_count: int = Field(ge=0)


class DiagnosticRecoveryCheckpoint(StrictContract):
    checkpoint_no: int = Field(ge=1)
    status: Literal["open", "consumed", "invalidated"]
    suspended_state_version: int = Field(ge=1)
    execution_mode: ExecutionMode
    effect_status: str = Field(min_length=1, max_length=24)
    replay_policy: Literal["safe_idempotent", "reconcile_required", "no_replay"] | None
    lease_state: Literal["unclaimed", "active", "expired", "released"]
    recovery_claim_count: int = Field(ge=0)
    disposition: Literal[
        "wait_for_user",
        "resume",
        "reconcile",
        "blocked",
        "historical",
    ]
    created_at: datetime
    consumed_at: datetime | None = None


class DiagnosticRecoveryTrace(StrictContract):
    active_checkpoint_present: bool
    checkpoints: tuple[DiagnosticRecoveryCheckpoint, ...] = Field(max_length=500)


class PureAgentDiagnosticSnapshot(StrictContract):
    schema_name: Literal["bid.pure-agent.diagnostic.v1"] = (
        "bid.pure-agent.diagnostic.v1"
    )
    generated_at: datetime
    task: DiagnosticTaskState
    state_trace: tuple[DiagnosticStateTransition, ...] = Field(max_length=501)
    call_trace: tuple[DiagnosticCallTrace, ...] = Field(max_length=500)
    budget_trace: tuple[DiagnosticBudgetAccount, ...] = Field(max_length=32)
    loop_trace: DiagnosticLoopTrace
    cancellation_trace: DiagnosticCancellationTrace
    recovery_trace: DiagnosticRecoveryTrace
    integrity_warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    redacted_fields: tuple[str, ...] = Field(min_length=1, max_length=32)
