"""Public Conversation API contracts for the isolated Pure Agent surface."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import Field, model_validator

from .common import Reference, StrictContentContract, StrictContract
from .planning import ExecutionMode
from .response_contracts import PublishedAnswerMessage
from .slots import PendingPhase, SlotValidationIssue
from .state import AgentTaskStatus


class ConversationResourceReference(StrictContract):
    kind: Literal["assessment", "bid_document_version"]
    ref: Reference


class CreateConversationRequest(StrictContract):
    assessment_ref: Reference | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)


class SubmitUserMessageRequest(StrictContentContract):
    text: str | None = Field(default=None, max_length=131_072)
    resources: tuple[ConversationResourceReference, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    reply_to_message_ref: Reference | None = None

    @model_validator(mode="after")
    def validate_open_input(self) -> "SubmitUserMessageRequest":
        if (self.text is None or not self.text.strip()) and not self.resources:
            raise ValueError("text or at least one resource reference is required")
        identities = tuple((item.kind, item.ref) for item in self.resources)
        if len(identities) != len(set(identities)):
            raise ValueError("resource references must be unique")
        return self


class SubmitSlotInputRequest(StrictContentContract):
    expected_state_version: int = Field(ge=1)
    # C01 normally reconstructs the opaque proof on the server.  The optional
    # field remains for isolated compatibility fixtures and never appears in a
    # response or safe event.
    resume_token: str | None = Field(default=None, min_length=16, max_length=512)
    candidate: Any


class CancelTaskRequest(StrictContract):
    expected_state_version: int = Field(ge=1)


class PublicUserInput(StrictContentContract):
    schema_name: Literal["bid.user-input.message.v1"] = "bid.user-input.message.v1"
    text: str | None = Field(default=None, max_length=131_072)
    resources: tuple[ConversationResourceReference, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )


class PublicSlotInput(StrictContentContract):
    schema_name: Literal["bid.slot-input.message.v1"] = "bid.slot-input.message.v1"
    slot_ref: Reference
    candidate: Any


PublicMessageContent = Annotated[
    PublicUserInput | PublicSlotInput | PublishedAnswerMessage,
    Field(discriminator="schema_name"),
]


class ConversationMessageView(StrictContentContract):
    message_ref: Reference
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    message_type: Literal[
        "user_input",
        "steering_candidate",
        "slot_input",
        "answer",
    ]
    reply_to_message_ref: Reference | None
    content: PublicMessageContent
    created_at: datetime


class PendingSlotView(StrictContentContract):
    slot_ref: Reference
    phase: PendingPhase
    request_message: str = Field(min_length=1, max_length=2000)
    issues: tuple[SlotValidationIssue, ...] = Field(default_factory=tuple, max_length=32)


class AgentTaskView(StrictContract):
    task_ref: Reference
    status: AgentTaskStatus
    state_version: int = Field(ge=1)
    execution_mode: ExecutionMode
    pending: PendingSlotView | None
    dispatch_status: Literal[
        "disabled",
        "ready",
        "active",
        "waiting_input",
        "finished",
    ] = "disabled"


class ConversationView(StrictContract):
    conversation_ref: Reference
    assessment_ref: Reference | None
    title: str | None
    status: Literal["active", "archived"]
    last_message_sequence: int = Field(ge=0)
    active_task: AgentTaskView | None
    latest_task: AgentTaskView | None
    created_at: datetime
    updated_at: datetime


class MessageAdmissionView(StrictContract):
    conversation_ref: Reference
    admission: Literal["task_trigger", "steering_candidate"]
    message: ConversationMessageView
    task: AgentTaskView
    replayed: bool


class SlotSubmissionView(StrictContract):
    conversation_ref: Reference
    accepted: bool
    message: ConversationMessageView
    task: AgentTaskView
    issues: tuple[SlotValidationIssue, ...] = Field(default_factory=tuple, max_length=32)
    replayed: bool


class TaskCancellationView(StrictContract):
    conversation_ref: Reference
    task: AgentTaskView
    replayed: bool


class ConversationMessagePage(StrictContract):
    items: tuple[ConversationMessageView, ...] = Field(max_length=100)
    after_sequence: int = Field(ge=0)
    next_after_sequence: int = Field(ge=0)
    has_more: bool


DataT = TypeVar("DataT")


class PureAgentApiSuccess(StrictContract, Generic[DataT]):
    code: Literal[200] = 200
    message: Literal["ok"] = "ok"
    data: DataT
    error: None = None
    request_id: str = Field(max_length=80)


class PureAgentApiErrorDetail(StrictContract):
    code: str = Field(min_length=1, max_length=100)
    retryable: bool
    guidance: str | None = Field(default=None, max_length=1000)


class PureAgentApiError(StrictContract):
    code: int = Field(ge=400, le=599)
    message: str = Field(min_length=1, max_length=500)
    data: None = None
    error: PureAgentApiErrorDetail
    request_id: str = Field(max_length=80)
