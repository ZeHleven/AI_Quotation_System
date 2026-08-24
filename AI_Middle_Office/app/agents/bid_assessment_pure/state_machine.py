"""Pure transition decisions for the five-state Agent task lifecycle."""

from __future__ import annotations

from typing import AbstractSet

from .planning import ExecutionMode
from .slots import PendingPhase
from .state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionDecision,
    TaskTransitionEvent,
    TERMINAL_STATUSES,
)


class TransitionRejected(ValueError):
    """A deterministic state transition guard rejected an event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_ALLOWED_EVENTS = {
    AgentTaskStatus.RUNNING: frozenset(
        {
            TaskEventType.ACTION_ACCEPTED,
            TaskEventType.OBSERVATION_ACCEPTED,
            TaskEventType.EXECUTION_MODE_CHANGED,
            TaskEventType.INFORMATION_REQUIRED,
            TaskEventType.COMPLETION_ACCEPTED,
            TaskEventType.FATAL_ERROR,
            TaskEventType.CANCEL_REQUESTED,
        }
    ),
    AgentTaskStatus.PENDING: frozenset(
        {
            TaskEventType.SLOT_VALIDATION_STARTED,
            TaskEventType.SLOT_FORMAT_ACCEPTED,
            TaskEventType.SLOT_VALIDATION_REJECTED,
            TaskEventType.SLOT_RESOLVED,
            TaskEventType.FATAL_ERROR,
            TaskEventType.CANCEL_REQUESTED,
        }
    ),
}


def create_running_task(
    *,
    task_id: str,
    session_id: str,
    goal_ref: str,
) -> AgentTaskState:
    """Create the only legal initial task state."""

    return AgentTaskState(
        task_id=task_id,
        session_id=session_id,
        state_version=1,
        status=AgentTaskStatus.RUNNING,
        execution_mode=ExecutionMode.DIRECT,
        goal_ref=goal_ref,
        plan_ref=None,
        pending_context=None,
        in_flight_action_ref=None,
        observation_refs=(),
        last_error_ref=None,
    )


def decide_transition(
    current: AgentTaskState,
    event: TaskTransitionEvent,
    *,
    consumed_event_ids: AbstractSet[str] = frozenset(),
    consumed_effect_keys: AbstractSet[str] = frozenset(),
) -> TaskTransitionDecision:
    """Apply common and event-specific guards without performing any effect."""

    _guard_common(
        current,
        event,
        consumed_event_ids=consumed_event_ids,
        consumed_effect_keys=consumed_effect_keys,
    )
    updates = _event_updates(current, event)
    payload = current.model_dump(mode="python")
    payload.update(updates)
    payload["state_version"] = current.state_version + 1
    next_state = AgentTaskState.model_validate(payload)
    return TaskTransitionDecision(
        event_id=event.event_id,
        task_id=current.task_id,
        previous_status=current.status,
        next_state=next_state,
    )


def _guard_common(
    current: AgentTaskState,
    event: TaskTransitionEvent,
    *,
    consumed_event_ids: AbstractSet[str],
    consumed_effect_keys: AbstractSet[str],
) -> None:
    if event.task_id != current.task_id:
        _reject("TASK_MISMATCH", "event does not belong to the current task")
    if current.status in TERMINAL_STATUSES:
        _reject("TERMINAL_STATE", "terminal tasks reject ordinary events")
    if event.event_id in consumed_event_ids:
        _reject("EVENT_ALREADY_CONSUMED", "event id has already been consumed")
    if event.expected_state_version != current.state_version:
        _reject("STATE_VERSION_CONFLICT", "expected state version is stale")
    if (
        event.effect_idempotency_key is not None
        and event.effect_idempotency_key in consumed_effect_keys
    ):
        _reject("EFFECT_ALREADY_CONSUMED", "effect idempotency key is already used")
    if event.event_type not in _ALLOWED_EVENTS[current.status]:
        _reject(
            "ILLEGAL_TRANSITION",
            f"{event.event_type.value} is not legal from {current.status.value}",
        )


def _event_updates(
    current: AgentTaskState,
    event: TaskTransitionEvent,
) -> dict[str, object]:
    if event.event_type is TaskEventType.ACTION_ACCEPTED:
        if event.action_ref is None or event.effect_idempotency_key is None:
            _reject("ACTION_GUARD_FAILED", "action and effect references are required")
        if current.in_flight_action_ref is not None:
            _reject("ACTION_IN_FLIGHT", "an action is already in flight")
        return {"in_flight_action_ref": event.action_ref}

    if event.event_type is TaskEventType.OBSERVATION_ACCEPTED:
        if event.observation_ref is None or event.action_ref is None:
            _reject("OBSERVATION_GUARD_FAILED", "observation and action are required")
        if current.in_flight_action_ref != event.action_ref:
            _reject("ACTION_REFERENCE_MISMATCH", "observation does not match in-flight action")
        if event.observation_ref in current.observation_refs:
            _reject("OBSERVATION_ALREADY_ACCEPTED", "observation was already accepted")
        return {
            "in_flight_action_ref": None,
            "observation_refs": (*current.observation_refs, event.observation_ref),
        }

    if event.event_type is TaskEventType.EXECUTION_MODE_CHANGED:
        initial_upgrade = (
            current.execution_mode is ExecutionMode.DIRECT
            and event.execution_mode is ExecutionMode.PLANNED
            and event.plan_ref is not None
        )
        rolling_plan_update = (
            current.execution_mode is ExecutionMode.PLANNED
            and event.execution_mode is ExecutionMode.PLANNED
            and current.plan_ref is not None
            and event.plan_ref is not None
            and event.plan_ref != current.plan_ref
        )
        if not (initial_upgrade or rolling_plan_update):
            _reject(
                "EXECUTION_MODE_GUARD_FAILED",
                "only direct-to-planned upgrade or a new rolling Plan head is allowed",
            )
        return {
            "execution_mode": ExecutionMode.PLANNED,
            "plan_ref": event.plan_ref,
        }

    if event.event_type is TaskEventType.INFORMATION_REQUIRED:
        if event.pending_context is None:
            _reject("PENDING_CONTEXT_REQUIRED", "pending requires slot and checkpoint")
        if event.pending_context.phase is not PendingPhase.WAITING_INPUT:
            _reject("PENDING_PHASE_INVALID", "new pending context must wait for input")
        if current.in_flight_action_ref is not None:
            _reject("UNSAFE_SUSPEND", "cannot suspend with an in-flight action")
        return {
            "status": AgentTaskStatus.PENDING,
            "pending_context": event.pending_context,
        }

    if event.event_type is TaskEventType.SLOT_VALIDATION_REJECTED:
        pending = event.pending_context
        if (
            pending is None
            or current.pending_context is None
            or current.pending_context.phase
            not in {
                PendingPhase.VALIDATING_FORMAT,
                PendingPhase.VALIDATING_BUSINESS,
            }
            or pending.slot_ref != current.pending_context.slot_ref
            or pending.checkpoint_ref != current.pending_context.checkpoint_ref
            or pending.phase is not PendingPhase.WAITING_INPUT
            or pending.last_error_ref is None
        ):
            _reject(
                "SLOT_REJECTION_GUARD_FAILED",
                "slot rejection must preserve suspension and provide safe guidance",
            )
        return {"pending_context": pending, "last_error_ref": pending.last_error_ref}

    if event.event_type in {
        TaskEventType.SLOT_VALIDATION_STARTED,
        TaskEventType.SLOT_FORMAT_ACCEPTED,
    }:
        pending = event.pending_context
        expected_phase = (
            PendingPhase.VALIDATING_FORMAT
            if event.event_type is TaskEventType.SLOT_VALIDATION_STARTED
            else PendingPhase.VALIDATING_BUSINESS
        )
        if (
            pending is None
            or current.pending_context is None
            or (
                event.event_type is TaskEventType.SLOT_VALIDATION_STARTED
                and current.pending_context.phase is not PendingPhase.WAITING_INPUT
            )
            or (
                event.event_type is TaskEventType.SLOT_FORMAT_ACCEPTED
                and current.pending_context.phase
                is not PendingPhase.VALIDATING_FORMAT
            )
            or pending.slot_ref != current.pending_context.slot_ref
            or pending.checkpoint_ref != current.pending_context.checkpoint_ref
            or pending.phase is not expected_phase
            or pending.validation_attempt_ref is None
        ):
            _reject(
                "SLOT_VALIDATION_GUARD_FAILED",
                "validation event must preserve suspension and identify its attempt",
            )
        return {"pending_context": pending, "last_error_ref": None}

    if event.event_type is TaskEventType.SLOT_RESOLVED:
        proof = event.resume_proof
        pending = current.pending_context
        if (
            proof is None
            or pending is None
            or pending.phase is not PendingPhase.VALIDATING_BUSINESS
            or proof.slot_ref != pending.slot_ref
            or proof.checkpoint_ref != pending.checkpoint_ref
        ):
            _reject(
                "RESUME_GUARD_FAILED",
                "resume proof does not match the active slot and checkpoint",
            )
        return {
            "status": AgentTaskStatus.RUNNING,
            "pending_context": None,
            "last_error_ref": None,
        }

    if event.event_type is TaskEventType.COMPLETION_ACCEPTED:
        if not event.result_committed or current.in_flight_action_ref is not None:
            _reject(
                "COMPLETION_GUARD_FAILED",
                "completion requires committed output and no in-flight action",
            )
        return {"status": AgentTaskStatus.COMPLETED}

    if event.event_type is TaskEventType.FATAL_ERROR:
        if event.error_ref is None:
            _reject("ERROR_REFERENCE_REQUIRED", "fatal error requires a structured error")
        return {
            "status": AgentTaskStatus.FAILED,
            "pending_context": None,
            "in_flight_action_ref": None,
            "last_error_ref": event.error_ref,
        }

    if event.event_type is TaskEventType.CANCEL_REQUESTED:
        if event.cancellation_fence_ref is None:
            _reject(
                "CANCELLATION_FENCE_REQUIRED",
                "cancellation requires a persisted fence",
            )
        return {
            "status": AgentTaskStatus.CANCELLED,
            "pending_context": None,
            "in_flight_action_ref": None,
        }

    _reject("UNSUPPORTED_EVENT", "event is not implemented")


def _reject(code: str, message: str) -> None:
    raise TransitionRejected(code, message)
