"""Conversation-facing projections and Slot continuation bridge.

This module is an application boundary, not an Agent workflow. It records open
messages, projects safe state, and invokes the already-authorized Slot
validation capability. It never calls a model, Tool, RAG adapter, or Worker.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .conversation_contracts import (
    AgentTaskView,
    ConversationMessagePage,
    ConversationMessageView,
    ConversationView,
    PendingSlotView,
    PublicSlotInput,
    PublicUserInput,
    SlotSubmissionView,
)
from .repository import (
    PureAgentConflict,
    PureAgentRepository,
    canonical_json,
    hash_resume_token,
)
from .response_contracts import PublishedAnswerMessage
from .runtime_controller import (
    ContinuationTokenService,
)
from .slot_validation import BusinessValidationContext, SlotValidatorRegistry
from .slots import SlotValidationIssue
from .state import AgentTaskState, AgentTaskStatus


MAX_SLOT_CANDIDATE_BYTES = 65_536


@dataclass(frozen=True)
class SlotSubmissionCommand:
    conversation_ref: str
    task_ref: str
    slot_ref: str
    owner_id: int
    created_by_ref: str
    tenant_ref: str
    authorization_snapshot_ref: str
    expected_state_version: int
    resume_token: str | None
    candidate: Any
    idempotency_key: str


class ConversationApiRuntime:
    """Small synchronous API capability container with no execution loop."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        slot_validators: SlotValidatorRegistry,
        runtime_available: bool = False,
        continuation_tokens: ContinuationTokenService | None = None,
    ) -> None:
        self.repository = repository
        self.slot_validators = slot_validators
        self.runtime_available = bool(runtime_available)
        self.continuation_tokens = continuation_tokens or ContinuationTokenService()

    def conversation_view(self, row: Any) -> ConversationView:
        active = self.repository.load_active_task_state(row.id, lock=False)
        latest = active or self.repository.load_latest_task_state(row.id)
        return ConversationView(
            conversation_ref=row.id,
            assessment_ref=row.assessment_id,
            title=row.title,
            status=row.status,
            last_message_sequence=max(0, int(row.next_message_sequence) - 1),
            active_task=self.task_view(active) if active is not None else None,
            latest_task=self.task_view(latest) if latest is not None else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def task_view(self, state: AgentTaskState) -> AgentTaskView:
        pending = None
        if state.status is AgentTaskStatus.PENDING:
            if state.pending_context is None:
                raise PureAgentConflict("pending task has no pending context")
            slot = self.repository.load_task_slot(
                task_id=state.task_id,
                slot_id=state.pending_context.slot_ref,
            )
            issues = tuple(
                SlotValidationIssue.model_validate(issue)
                for issue in self.repository.load_validation_issues(
                    task_id=state.task_id,
                    validation_ref=state.pending_context.last_error_ref,
                )
            )
            pending = PendingSlotView(
                slot_ref=slot.slot_id,
                phase=state.pending_context.phase,
                request_message=slot.request_message,
                issues=issues,
            )
        return AgentTaskView(
            task_ref=state.task_id,
            status=state.status,
            state_version=state.state_version,
            execution_mode=state.execution_mode,
            pending=pending,
            dispatch_status=self._dispatch_status(state),
        )

    def _dispatch_status(self, state: AgentTaskState) -> str:
        if not self.runtime_available:
            return "disabled"
        if state.status is AgentTaskStatus.PENDING:
            return "waiting_input"
        if state.status in {
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
        }:
            return "finished"
        if state.in_flight_action_ref is not None:
            return "active"
        return "ready"

    def message_view(self, row: Any) -> ConversationMessageView:
        content = row.content_json
        if row.role == "assistant" and row.message_type == "answer.committed":
            public_content = PublishedAnswerMessage.model_validate(content)
            public_type = "answer"
        elif row.role == "user" and row.message_type in {
            "user.task_trigger",
            "user.steering_candidate",
        }:
            if (
                not isinstance(content, dict)
                or content.get("schema_name") != "bid.user-message.internal.v1"
                or not isinstance(content.get("input"), dict)
            ):
                raise PureAgentConflict("stored user message is invalid")
            public_content = PublicUserInput.model_validate(
                {
                    "schema_name": "bid.user-input.message.v1",
                    **content["input"],
                }
            )
            public_type = (
                "user_input"
                if row.message_type == "user.task_trigger"
                else "steering_candidate"
            )
        elif row.role == "user" and row.message_type == "user.slot_candidate":
            if (
                not isinstance(content, dict)
                or content.get("schema_name") != "bid.slot-input.internal.v1"
            ):
                raise PureAgentConflict("stored Slot input message is invalid")
            public_content = PublicSlotInput(
                slot_ref=content.get("slot_ref"),
                candidate=content.get("candidate"),
            )
            public_type = "slot_input"
        else:
            raise PureAgentConflict("message cannot be projected to the public API")
        return ConversationMessageView(
            message_ref=row.id,
            sequence=int(row.sequence_no),
            role=row.role,
            message_type=public_type,
            reply_to_message_ref=row.reply_to_message_id,
            content=public_content,
            created_at=row.created_at,
        )

    def message_page(
        self,
        *,
        conversation_ref: str,
        after_sequence: int,
        limit: int,
    ) -> ConversationMessagePage:
        requested_limit = max(1, min(int(limit), 100))
        rows = self.repository.list_conversation_messages(
            conversation_ref,
            after_sequence=after_sequence,
            limit=requested_limit + 1,
        )
        has_more = len(rows) > requested_limit
        page_rows = rows[:requested_limit]
        items = tuple(self.message_view(row) for row in page_rows)
        next_after = items[-1].sequence if items else max(0, int(after_sequence))
        return ConversationMessagePage(
            items=items,
            after_sequence=max(0, int(after_sequence)),
            next_after_sequence=next_after,
            has_more=has_more,
        )

    def submit_slot_input(self, command: SlotSubmissionCommand) -> SlotSubmissionView:
        try:
            candidate_size = len(canonical_json(command.candidate).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise PureAgentConflict("Slot candidate must be valid JSON") from exc
        if candidate_size > MAX_SLOT_CANDIDATE_BYTES:
            raise PureAgentConflict("Slot candidate exceeds the size limit")

        conversation = self.repository.load_owned_conversation(
            command.conversation_ref,
            owner_id=command.owner_id,
            lock=True,
        )
        if conversation.tenant_ref != command.tenant_ref:
            raise PureAgentConflict("conversation scope changed")
        state = self.repository.load_owned_task_state(
            command.task_ref,
            conversation_id=command.conversation_ref,
            owner_id=command.owner_id,
            lock=True,
        )
        slot = self.repository.load_task_slot(
            task_id=command.task_ref,
            slot_id=command.slot_ref,
        )
        checkpoint = self.repository.load_task_checkpoint_for_slot(
            task_id=command.task_ref,
            slot_id=command.slot_ref,
        )
        resume_token = command.resume_token
        if self.continuation_tokens.available:
            expected_token = self.continuation_tokens.issue(checkpoint)
            if resume_token is not None and not self.continuation_tokens.matches(
                checkpoint,
                resume_token,
            ):
                raise PureAgentConflict("checkpoint resume proof is invalid")
            resume_token = expected_token
        elif resume_token is None:
            raise PureAgentConflict("checkpoint resume proof is unavailable")
        if resume_token is None:
            raise PureAgentConflict("checkpoint resume proof is unavailable")
        message = self.repository.append_message(
            conversation_id=command.conversation_ref,
            role="user",
            message_type="user.slot_candidate",
            content={
                "schema_name": "bid.slot-input.internal.v1",
                "task_ref": command.task_ref,
                "slot_ref": command.slot_ref,
                "candidate": command.candidate,
                "expected_state_version": command.expected_state_version,
                "resume_token_hash": hash_resume_token(resume_token),
            },
            created_by_ref=command.created_by_ref,
            idempotency_key=command.idempotency_key,
        )

        format_key = self._derived_key("slot-format", command.idempotency_key)
        business_key = self._derived_key("slot-business", command.idempotency_key)
        replay = self._slot_replay(
            state=state,
            slot_ref=command.slot_ref,
            format_key=format_key,
            business_key=business_key,
        )
        if replay is not None:
            accepted, issues = replay
            return SlotSubmissionView(
                conversation_ref=command.conversation_ref,
                accepted=accepted,
                message=self.message_view(message),
                task=self.task_view(state),
                issues=issues,
                replayed=True,
            )

        if (
            state.status is not AgentTaskStatus.PENDING
            or state.pending_context is None
            or state.pending_context.slot_ref != command.slot_ref
        ):
            raise PureAgentConflict("task is not waiting for this Slot")
        if state.state_version != int(command.expected_state_version):
            raise PureAgentConflict("expected task state version is stale")

        format_attempt = self.repository.begin_slot_validation(
            task_id=command.task_ref,
            event_id=self._derived_key("slot-format-start", command.idempotency_key),
            candidate_message_id=message.id,
            candidate=command.candidate,
            idempotency_key=format_key,
        )
        format_result = self.slot_validators.validate_format(slot, command.candidate)
        if not format_result.accepted or format_result.value is None:
            issues = format_result.issues
            rejected = self.repository.reject_slot_validation(
                task_id=command.task_ref,
                event_id=self._derived_key("slot-format-reject", command.idempotency_key),
                attempt_id=format_attempt.attempt_id,
                issues=[issue.model_dump(mode="json") for issue in issues],
            )
            return SlotSubmissionView(
                conversation_ref=command.conversation_ref,
                accepted=False,
                message=self.message_view(message),
                task=self.task_view(rejected.state),
                issues=issues,
                replayed=False,
            )

        typed_value = format_result.value.model_dump(mode="json")
        business_attempt = self.repository.accept_slot_format(
            task_id=command.task_ref,
            event_id=self._derived_key("slot-format-accept", command.idempotency_key),
            format_attempt_id=format_attempt.attempt_id,
            typed_value=typed_value,
            business_idempotency_key=business_key,
        )
        business_result = self.slot_validators.validate_business(
            slot,
            format_result.value,
            context=BusinessValidationContext(
                user_ref=command.created_by_ref,
                tenant_ref=command.tenant_ref,
                conversation_ref=command.conversation_ref,
                task_ref=command.task_ref,
                slot_ref=command.slot_ref,
                authorization_snapshot_ref=command.authorization_snapshot_ref,
            ),
        )
        if not business_result.accepted or business_result.value is None:
            issues = business_result.issues
            rejected = self.repository.reject_slot_validation(
                task_id=command.task_ref,
                event_id=self._derived_key("slot-business-reject", command.idempotency_key),
                attempt_id=business_attempt.attempt_id,
                issues=[issue.model_dump(mode="json") for issue in issues],
            )
            return SlotSubmissionView(
                conversation_ref=command.conversation_ref,
                accepted=False,
                message=self.message_view(message),
                task=self.task_view(rejected.state),
                issues=issues,
                replayed=False,
            )

        resumed = self.repository.resolve_slot_and_resume(
            task_id=command.task_ref,
            event_id=self._derived_key("slot-resolve", command.idempotency_key),
            business_attempt_id=business_attempt.attempt_id,
            resolved_value=business_result.value.model_dump(mode="json"),
            resume_token=resume_token,
        )
        return SlotSubmissionView(
            conversation_ref=command.conversation_ref,
            accepted=True,
            message=self.message_view(message),
            task=self.task_view(resumed.state),
            issues=(),
            replayed=False,
        )

    def _slot_replay(
        self,
        *,
        state: AgentTaskState,
        slot_ref: str,
        format_key: str,
        business_key: str,
    ) -> tuple[bool, tuple[SlotValidationIssue, ...]] | None:
        format_row = self.repository.load_validation_by_key(
            slot_id=slot_ref,
            idempotency_key=format_key,
        )
        if format_row is None:
            return None
        if format_row.status == "failed":
            return False, self._issues(format_row.issues_json)
        business_row = self.repository.load_validation_by_key(
            slot_id=slot_ref,
            idempotency_key=business_key,
        )
        if business_row is None or business_row.status == "running":
            raise PureAgentConflict("Slot validation receipt is incomplete")
        if business_row.status == "failed":
            return False, self._issues(business_row.issues_json)
        slot = self.repository.load_task_slot(task_id=state.task_id, slot_id=slot_ref)
        if slot.status.value != "resolved":
            raise PureAgentConflict("Slot validation receipt is inconsistent")
        return True, ()

    @staticmethod
    def _issues(raw: Any) -> tuple[SlotValidationIssue, ...]:
        if not isinstance(raw, list):
            raise PureAgentConflict("Slot validation issues are invalid")
        return tuple(SlotValidationIssue.model_validate(item) for item in raw)

    @staticmethod
    def _derived_key(namespace: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"{namespace}:{digest}"
