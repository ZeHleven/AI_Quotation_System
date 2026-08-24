"""Safe, user-visible event contracts for Pure Agent task streams."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import Reference, StepId, StrictContentContract, StrictContract
from .response_contracts import PublishedAnswerMessage
from .slots import PendingPhase, SlotValidationIssue
from .state import AgentTaskStatus, TERMINAL_STATUSES


class SafeAgentEventType(str, Enum):
    TASK_STARTED = "task.started"
    PLAN_UPDATED = "plan.updated"
    PROGRESS_UPDATED = "progress.updated"
    INPUT_REQUIRED = "input.required"
    INPUT_VALIDATING = "input.validating"
    INPUT_REJECTED = "input.rejected"
    INPUT_ACCEPTED = "input.accepted"
    ANSWER_PREPARING = "answer.preparing"
    ANSWER_COMPLETED = "answer.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"


class SafeProgressPhase(str, Enum):
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    PREPARING_INPUT_REQUEST = "preparing_input_request"
    PREPARING_ANSWER = "preparing_answer"
    CONTINUING = "continuing"


class SafeTaskStartedPayload(StrictContentContract):
    kind: Literal["task_started"] = "task_started"
    message: str = Field(min_length=1, max_length=500)


class SafePlanStepProjection(StrictContentContract):
    step_id: StepId
    title: str = Field(min_length=1, max_length=160)
    state: Literal["planned"] = "planned"


class SafePlanProjectionPayload(StrictContentContract):
    kind: Literal["plan_projection"] = "plan_projection"
    plan_version: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=1000)
    steps: tuple[SafePlanStepProjection, ...] = Field(max_length=64)
    revised: bool


class SafeProgressPayload(StrictContentContract):
    kind: Literal["progress"] = "progress"
    phase: SafeProgressPhase
    message: str = Field(min_length=1, max_length=500)


class SafeInputRequestPayload(StrictContentContract):
    kind: Literal["input_request"] = "input_request"
    slot_ref: Reference
    phase: PendingPhase
    request_message: str = Field(min_length=1, max_length=2000)


class SafeInputValidationPayload(StrictContentContract):
    kind: Literal["input_validation"] = "input_validation"
    slot_ref: Reference
    result: Literal["validating", "rejected", "accepted"]
    message: str = Field(min_length=1, max_length=500)
    issues: tuple[SlotValidationIssue, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_issues(self) -> "SafeInputValidationPayload":
        if self.result == "rejected" and not self.issues:
            raise ValueError("rejected input requires safe validation issues")
        if self.result != "rejected" and self.issues:
            raise ValueError("only rejected input may expose validation issues")
        return self


class SafeAnswerPayload(StrictContentContract):
    kind: Literal["answer"] = "answer"
    message: PublishedAnswerMessage


class SafeTerminalPayload(StrictContentContract):
    kind: Literal["terminal"] = "terminal"
    outcome: Literal["failed", "cancelled"]
    message: str = Field(min_length=1, max_length=500)
    guidance: str | None = Field(default=None, min_length=1, max_length=1000)


SafeAgentEventPayload = Annotated[
    SafeTaskStartedPayload
    | SafePlanProjectionPayload
    | SafeProgressPayload
    | SafeInputRequestPayload
    | SafeInputValidationPayload
    | SafeAnswerPayload
    | SafeTerminalPayload,
    Field(discriminator="kind"),
]


class SafeAgentEvent(StrictContentContract):
    schema_name: Literal["bid.pure_agent.public_event.v1"] = (
        "bid.pure_agent.public_event.v1"
    )
    event_id: Reference
    event_type: SafeAgentEventType
    conversation_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    status: AgentTaskStatus
    terminal: bool
    occurred_at: datetime
    payload: SafeAgentEventPayload

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> "SafeAgentEvent":
        expected_terminal = self.status in TERMINAL_STATUSES
        if self.terminal != expected_terminal:
            raise ValueError("terminal flag must match the task status")
        terminal_types = {
            SafeAgentEventType.ANSWER_COMPLETED,
            SafeAgentEventType.TASK_FAILED,
            SafeAgentEventType.TASK_CANCELLED,
        }
        if self.terminal != (self.event_type in terminal_types):
            raise ValueError("terminal event type does not match terminal flag")
        expected_payload_kinds = {
            SafeAgentEventType.TASK_STARTED: {"task_started"},
            SafeAgentEventType.PLAN_UPDATED: {"plan_projection"},
            SafeAgentEventType.PROGRESS_UPDATED: {"progress"},
            SafeAgentEventType.INPUT_REQUIRED: {"input_request"},
            SafeAgentEventType.INPUT_VALIDATING: {"input_validation"},
            SafeAgentEventType.INPUT_REJECTED: {"input_validation"},
            SafeAgentEventType.INPUT_ACCEPTED: {"input_validation"},
            SafeAgentEventType.ANSWER_PREPARING: {"progress"},
            SafeAgentEventType.ANSWER_COMPLETED: {"answer"},
            SafeAgentEventType.TASK_FAILED: {"terminal"},
            SafeAgentEventType.TASK_CANCELLED: {"terminal"},
        }
        if self.payload.kind not in expected_payload_kinds[self.event_type]:
            raise ValueError("event payload kind does not match event type")
        return self


class SafeAgentEventPage(StrictContract):
    task_ref: Reference
    events: tuple[SafeAgentEvent, ...] = Field(max_length=100)
    after_version: int = Field(ge=0)
    next_after_version: int = Field(ge=0)
    current_state_version: int = Field(ge=1)
    current_status: AgentTaskStatus
    has_more: bool
