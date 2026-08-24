from __future__ import annotations

from typing import Any

import pytest
from pydantic import Field, ValidationError

from app.agents.bid_assessment_pure.common import StrictContract
from app.agents.bid_assessment_pure.planning import ExecutionMode
from app.agents.bid_assessment_pure.slot_validation import (
    BusinessRuleDecision,
    BusinessValidationContext,
    SlotValidatorRegistry,
)
from app.agents.bid_assessment_pure.slots import (
    CheckpointStatus,
    ContinuationCheckpoint,
    PendingContext,
    PendingPhase,
    ResumeProof,
    Slot,
    SlotStatus,
)
from app.agents.bid_assessment_pure.state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
)
from app.agents.bid_assessment_pure.state_machine import (
    TransitionRejected,
    create_running_task,
    decide_transition,
)


def _event(
    state: AgentTaskState,
    event_type: TaskEventType,
    *,
    event_id: str,
    **overrides: Any,
) -> TaskTransitionEvent:
    values: dict[str, Any] = {
        "event_id": event_id,
        "task_id": state.task_id,
        "expected_state_version": state.state_version,
        "event_type": event_type,
        "effect_idempotency_key": None,
        "action_ref": None,
        "pending_context": None,
        "resume_proof": None,
        "execution_mode": None,
        "plan_ref": None,
        "observation_ref": None,
        "result_committed": False,
        "error_ref": None,
        "cancellation_fence_ref": None,
    }
    values.update(overrides)
    return TaskTransitionEvent.model_validate(values)


def _pending(
    *,
    phase: PendingPhase = PendingPhase.WAITING_INPUT,
    attempt_ref: str | None = None,
    error_ref: str | None = None,
) -> PendingContext:
    return PendingContext(
        slot_ref="slot:deadline",
        checkpoint_ref="checkpoint:deadline",
        phase=phase,
        validation_attempt_ref=attempt_ref,
        last_error_ref=error_ref,
    )


def _advance(
    state: AgentTaskState,
    event_type: TaskEventType,
    *,
    event_id: str,
    **overrides: Any,
) -> AgentTaskState:
    return decide_transition(
        state,
        _event(state, event_type, event_id=event_id, **overrides),
    ).next_state


def test_contracts_are_closed_and_enforce_state_invariants() -> None:
    with pytest.raises(ValidationError, match="pending task requires pending_context"):
        AgentTaskState(
            task_id="task:1",
            session_id="conversation:1",
            state_version=1,
            status=AgentTaskStatus.PENDING,
            execution_mode=ExecutionMode.DIRECT,
            goal_ref="goal:1",
            plan_ref=None,
            pending_context=None,
            in_flight_action_ref=None,
            observation_refs=(),
            last_error_ref=None,
        )

    with pytest.raises(ValidationError, match="terminal task forbids"):
        AgentTaskState(
            task_id="task:1",
            session_id="conversation:1",
            state_version=2,
            status=AgentTaskStatus.COMPLETED,
            execution_mode=ExecutionMode.DIRECT,
            goal_ref="goal:1",
            plan_ref=None,
            pending_context=None,
            in_flight_action_ref="action:1",
            observation_refs=(),
            last_error_ref=None,
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentTaskState.model_validate(
            {
                **create_running_task(
                    task_id="task:1",
                    session_id="conversation:1",
                    goal_ref="goal:1",
                ).model_dump(mode="python"),
                "unexpected": "forbidden",
            }
        )


def test_resume_and_checkpoint_contracts_require_complete_fence_proof() -> None:
    with pytest.raises(ValidationError, match="all recovery guards"):
        ResumeProof(
            slot_ref="slot:1",
            checkpoint_ref="checkpoint:1",
            resolved_value_ref="slot-value:1",
            resume_token_verified=True,
            effect_fence_verified=False,
            checkpoint_consumed=True,
        )

    with pytest.raises(ValidationError, match="resume_token_hash"):
        ContinuationCheckpoint(
            checkpoint_id="checkpoint:1",
            task_id="task:1",
            slot_ref="slot:1",
            suspended_state_version=2,
            execution_mode=ExecutionMode.DIRECT,
            context_snapshot_ref="context:1",
            suspended_action_ref="action:1",
            effect_fence_ref="effect:1",
            resume_token_hash="plain-text-token",
            status=CheckpointStatus.OPEN,
        )


def test_five_state_machine_supports_pending_validation_resume_and_completion() -> None:
    state = create_running_task(
        task_id="task:1",
        session_id="conversation:1",
        goal_ref="goal:1",
    )
    state = _advance(
        state,
        TaskEventType.ACTION_ACCEPTED,
        event_id="event:action",
        action_ref="action:1",
        effect_idempotency_key="effect-key:1",
    )
    assert state.state_version == 2
    assert state.in_flight_action_ref == "action:1"

    state = _advance(
        state,
        TaskEventType.OBSERVATION_ACCEPTED,
        event_id="event:observation",
        action_ref="action:1",
        observation_ref="observation:1",
    )
    assert state.state_version == 3
    assert state.in_flight_action_ref is None
    assert state.observation_refs == ("observation:1",)

    state = _advance(
        state,
        TaskEventType.INFORMATION_REQUIRED,
        event_id="event:pending",
        pending_context=_pending(),
    )
    assert state.status is AgentTaskStatus.PENDING
    assert state.pending_context is not None

    state = _advance(
        state,
        TaskEventType.SLOT_VALIDATION_STARTED,
        event_id="event:format-start",
        pending_context=_pending(
            phase=PendingPhase.VALIDATING_FORMAT,
            attempt_ref="validation:format",
        ),
    )
    state = _advance(
        state,
        TaskEventType.SLOT_FORMAT_ACCEPTED,
        event_id="event:format-accepted",
        pending_context=_pending(
            phase=PendingPhase.VALIDATING_BUSINESS,
            attempt_ref="validation:business",
        ),
    )
    state = _advance(
        state,
        TaskEventType.SLOT_RESOLVED,
        event_id="event:slot-resolved",
        resume_proof=ResumeProof(
            slot_ref="slot:deadline",
            checkpoint_ref="checkpoint:deadline",
            resolved_value_ref="slot-value:deadline",
            resume_token_verified=True,
            effect_fence_verified=True,
            checkpoint_consumed=True,
        ),
    )
    assert state.status is AgentTaskStatus.RUNNING
    assert state.pending_context is None

    state = _advance(
        state,
        TaskEventType.COMPLETION_ACCEPTED,
        event_id="event:completed",
        result_committed=True,
    )
    assert state.status is AgentTaskStatus.COMPLETED

    with pytest.raises(TransitionRejected) as error:
        decide_transition(
            state,
            _event(
                state,
                TaskEventType.CANCEL_REQUESTED,
                event_id="event:too-late",
                cancellation_fence_ref="cancel-fence:1",
            ),
        )
    assert error.value.code == "TERMINAL_STATE"


def test_state_machine_rejects_stale_unsafe_and_unfenced_transitions() -> None:
    running = create_running_task(
        task_id="task:1",
        session_id="conversation:1",
        goal_ref="goal:1",
    )
    stale = _event(
        running,
        TaskEventType.FATAL_ERROR,
        event_id="event:stale",
        expected_state_version=2,
        error_ref="error:1",
    )
    with pytest.raises(TransitionRejected) as stale_error:
        decide_transition(running, stale)
    assert stale_error.value.code == "STATE_VERSION_CONFLICT"

    in_flight = _advance(
        running,
        TaskEventType.ACTION_ACCEPTED,
        event_id="event:action",
        action_ref="action:1",
        effect_idempotency_key="effect-key:1",
    )
    with pytest.raises(TransitionRejected) as suspend_error:
        _advance(
            in_flight,
            TaskEventType.INFORMATION_REQUIRED,
            event_id="event:unsafe-pending",
            pending_context=_pending(),
        )
    assert suspend_error.value.code == "UNSAFE_SUSPEND"

    with pytest.raises(TransitionRejected) as cancel_error:
        _advance(
            running,
            TaskEventType.CANCEL_REQUESTED,
            event_id="event:unfenced-cancel",
        )
    assert cancel_error.value.code == "CANCELLATION_FENCE_REQUIRED"


def test_state_machine_allows_only_one_way_planning_upgrade_and_guarded_terminals() -> None:
    running = create_running_task(
        task_id="task:1",
        session_id="conversation:1",
        goal_ref="goal:1",
    )
    planned = _advance(
        running,
        TaskEventType.EXECUTION_MODE_CHANGED,
        event_id="event:planned",
        execution_mode=ExecutionMode.PLANNED,
        plan_ref="plan:1",
    )
    assert planned.execution_mode is ExecutionMode.PLANNED
    assert planned.plan_ref == "plan:1"
    with pytest.raises(TransitionRejected) as downgrade_error:
        _advance(
            planned,
            TaskEventType.EXECUTION_MODE_CHANGED,
            event_id="event:downgrade",
            execution_mode=ExecutionMode.DIRECT,
            plan_ref="plan:2",
        )
    assert downgrade_error.value.code == "EXECUTION_MODE_GUARD_FAILED"

    failed = _advance(
        running,
        TaskEventType.FATAL_ERROR,
        event_id="event:failed",
        error_ref="error:fatal",
    )
    assert failed.status is AgentTaskStatus.FAILED
    assert failed.last_error_ref == "error:fatal"

    pending = _advance(
        running,
        TaskEventType.INFORMATION_REQUIRED,
        event_id="event:pending-for-cancel",
        pending_context=_pending(),
    )
    cancelled = _advance(
        pending,
        TaskEventType.CANCEL_REQUESTED,
        event_id="event:cancelled",
        cancellation_fence_ref="cancel-fence:1",
    )
    assert cancelled.status is AgentTaskStatus.CANCELLED
    assert cancelled.pending_context is None


def test_state_machine_rejects_consumed_event_and_effect_identities() -> None:
    running = create_running_task(
        task_id="task:1",
        session_id="conversation:1",
        goal_ref="goal:1",
    )
    event = _event(
        running,
        TaskEventType.ACTION_ACCEPTED,
        event_id="event:deduplicated",
        action_ref="action:1",
        effect_idempotency_key="effect-key:deduplicated",
    )
    with pytest.raises(TransitionRejected) as event_error:
        decide_transition(
            running,
            event,
            consumed_event_ids={event.event_id},
        )
    assert event_error.value.code == "EVENT_ALREADY_CONSUMED"

    with pytest.raises(TransitionRejected) as effect_error:
        decide_transition(
            running,
            event,
            consumed_effect_keys={event.effect_idempotency_key},
        )
    assert effect_error.value.code == "EFFECT_ALREADY_CONSUMED"


def test_rejected_slot_validation_returns_to_waiting_input() -> None:
    running = create_running_task(
        task_id="task:1",
        session_id="conversation:1",
        goal_ref="goal:1",
    )
    pending = _advance(
        running,
        TaskEventType.INFORMATION_REQUIRED,
        event_id="event:pending",
        pending_context=_pending(),
    )
    validating = _advance(
        pending,
        TaskEventType.SLOT_VALIDATION_STARTED,
        event_id="event:validation-start",
        pending_context=_pending(
            phase=PendingPhase.VALIDATING_FORMAT,
            attempt_ref="validation:1",
        ),
    )
    rejected = _advance(
        validating,
        TaskEventType.SLOT_VALIDATION_REJECTED,
        event_id="event:validation-rejected",
        pending_context=_pending(error_ref="validation-error:1"),
    )
    assert rejected.status is AgentTaskStatus.PENDING
    assert rejected.pending_context is not None
    assert rejected.pending_context.phase is PendingPhase.WAITING_INPUT
    assert rejected.pending_context.validation_attempt_ref is None
    assert rejected.last_error_ref == "validation-error:1"


class DeadlineInput(StrictContract):
    deadline_days: int = Field(ge=1, le=365)
    project_code: str = Field(pattern=r"^[A-Z]{2}-[0-9]{4}$")


class MinimumDeadlineValidator:
    def validate(
        self,
        value: DeadlineInput,
        *,
        context: BusinessValidationContext,
    ) -> BusinessRuleDecision:
        assert context.slot_ref == "slot:deadline"
        if value.deadline_days < 7:
            return BusinessRuleDecision(
                accepted=False,
                code="DEADLINE_TOO_SHORT",
                field="deadline_days",
                message="工期不能短于七天。",
                guidance="请输入 7 到 365 之间的天数。",
                retryable=True,
            )
        return BusinessRuleDecision(accepted=True)


class ExplodingValidator:
    def validate(self, value: DeadlineInput, *, context: BusinessValidationContext):
        del value, context
        raise RuntimeError("internal-secret-value")


def _slot(*, validator_refs: tuple[str, ...] = ()) -> Slot:
    return Slot(
        slot_id="slot:deadline",
        task_id="task:1",
        name="bid.deadline",
        request_message="请补充允许的工期天数和项目编码。",
        input_model_ref="slot-model:deadline-v1",
        business_validator_refs=validator_refs,
        status=SlotStatus.UNRESOLVED,
        candidate_input_ref=None,
        resolved_value_ref=None,
    )


def _business_context() -> BusinessValidationContext:
    return BusinessValidationContext(
        user_ref="user:1",
        tenant_ref="tenant:1",
        conversation_ref="conversation:1",
        task_ref="task:1",
        slot_ref="slot:deadline",
        authorization_snapshot_ref="authorization:1",
    )


def test_slot_validator_applies_pydantic_then_business_validation() -> None:
    registry = SlotValidatorRegistry()
    registry.register_input_model(
        "slot-model:deadline-v1",
        DeadlineInput,
        format_guidance="请按项目编码 AB-1234、工期 1 到 365 天的格式填写。",
    )
    registry.register_business_validator("validator:min-deadline", MinimumDeadlineValidator())
    slot = _slot(validator_refs=("validator:min-deadline",))

    invalid = registry.validate_format(
        slot,
        {"deadline_days": 0, "project_code": "secret-invalid-project"},
    )
    assert not invalid.accepted
    assert invalid.value is None
    assert {issue.code for issue in invalid.issues} == {"SLOT_FORMAT_INVALID"}
    assert all("secret-invalid-project" not in issue.message for issue in invalid.issues)
    assert all("secret-invalid-project" not in issue.guidance for issue in invalid.issues)

    formatted = registry.validate_format(
        slot,
        {"deadline_days": 5, "project_code": "AB-1234"},
    )
    assert formatted.accepted
    assert isinstance(formatted.value, DeadlineInput)

    rejected = registry.validate_business(
        slot,
        formatted.value,
        context=_business_context(),
    )
    assert not rejected.accepted
    assert rejected.issues[0].code == "DEADLINE_TOO_SHORT"
    assert rejected.issues[0].guidance == "请输入 7 到 365 之间的天数。"

    accepted_format = registry.validate_format(
        slot,
        {"deadline_days": 30, "project_code": "AB-1234"},
    )
    accepted = registry.validate_business(
        slot,
        accepted_format.value,
        context=_business_context(),
    )
    assert accepted.accepted
    assert accepted.value == accepted_format.value
    assert accepted.issues == ()


def test_slot_business_validator_exception_is_safely_redacted() -> None:
    registry = SlotValidatorRegistry()
    registry.register_input_model(
        "slot-model:deadline-v1",
        DeadlineInput,
        format_guidance="请按要求填写。",
    )
    registry.register_business_validator("validator:exploding", ExplodingValidator())
    slot = _slot(validator_refs=("validator:exploding",))
    formatted = registry.validate_format(
        slot,
        {"deadline_days": 30, "project_code": "AB-1234"},
    )
    result = registry.validate_business(
        slot,
        formatted.value,
        context=_business_context(),
    )
    assert not result.accepted
    assert result.issues[0].code == "SLOT_BUSINESS_VALIDATION_UNAVAILABLE"
    assert "internal-secret-value" not in result.issues[0].message
    assert "internal-secret-value" not in result.issues[0].guidance
