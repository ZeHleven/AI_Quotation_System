"""Five-state Pure Agent task contract."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .planning import ExecutionMode
from .slots import PendingContext, ResumeProof


class AgentTaskStatus(str, Enum):
    RUNNING = "running"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {
        AgentTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED,
        AgentTaskStatus.CANCELLED,
    }
)


class TaskEventType(str, Enum):
    ACTION_ACCEPTED = "action.accepted"
    OBSERVATION_ACCEPTED = "observation.accepted"
    EXECUTION_MODE_CHANGED = "execution_mode.changed"
    INFORMATION_REQUIRED = "information.required"
    SLOT_VALIDATION_STARTED = "slot.validation_started"
    SLOT_FORMAT_ACCEPTED = "slot.format_accepted"
    SLOT_VALIDATION_REJECTED = "slot.validation_rejected"
    SLOT_RESOLVED = "slot.resolved"
    COMPLETION_ACCEPTED = "completion.accepted"
    FATAL_ERROR = "fatal_error"
    CANCEL_REQUESTED = "cancel.requested"


class AgentTaskState(StrictContract):
    task_id: Reference
    session_id: Reference
    state_version: int = Field(ge=1)
    status: AgentTaskStatus
    execution_mode: ExecutionMode
    goal_ref: Reference
    plan_ref: Reference | None
    pending_context: PendingContext | None
    in_flight_action_ref: Reference | None
    observation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=500)
    last_error_ref: Reference | None

    @model_validator(mode="after")
    def validate_status_context(self) -> "AgentTaskState":
        if self.status is AgentTaskStatus.PENDING and self.pending_context is None:
            raise ValueError("pending task requires pending_context")
        if self.status is not AgentTaskStatus.PENDING and self.pending_context is not None:
            raise ValueError("only pending task may carry pending_context")
        if self.status in TERMINAL_STATUSES and self.in_flight_action_ref is not None:
            raise ValueError("terminal task forbids in_flight_action_ref")
        if len(self.observation_refs) != len(set(self.observation_refs)):
            raise ValueError("observation_refs must be unique")
        return self


class TaskTransitionEvent(StrictContract):
    event_id: Reference
    task_id: Reference
    expected_state_version: int = Field(ge=1)
    event_type: TaskEventType
    effect_idempotency_key: Reference | None
    action_ref: Reference | None
    pending_context: PendingContext | None
    resume_proof: ResumeProof | None
    execution_mode: ExecutionMode | None
    plan_ref: Reference | None
    observation_ref: Reference | None
    result_committed: bool = False
    error_ref: Reference | None
    cancellation_fence_ref: Reference | None


class TaskTransitionDecision(StrictContract):
    event_id: Reference
    task_id: Reference
    previous_status: AgentTaskStatus
    next_state: AgentTaskState
