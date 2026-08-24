from __future__ import annotations

from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models.registry  # noqa: F401  # Register FK targets without FastAPI startup.
from app.agents.bid_assessment_pure.persistence_models import (
    BidPureAgentCheckpoint,
    BidPureAgentEffectFence,
    BidPureAgentSlot,
)
from app.agents.bid_assessment_pure.repository import (
    PureAgentConflict,
    PureAgentFenceRejected,
    PureAgentRepository,
)
from app.agents.bid_assessment_pure.state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
)
from app.core.database import Base


@pytest.fixture(scope="module")
def sqlite_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def repository(sqlite_engine) -> Iterator[tuple[PureAgentRepository, Session]]:
    connection = sqlite_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield PureAgentRepository(session), session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


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


def _new_task(repo: PureAgentRepository, *, suffix: str) -> tuple[str, str]:
    conversation = repo.create_conversation(
        owner_id=1,
        tenant_ref="tenant:v601",
        conversation_id=f"conversation-{suffix}",
    )
    message = repo.append_message(
        conversation_id=conversation.id,
        role="user",
        message_type="user.task_trigger",
        content={"text": "请判断投标风险"},
        created_by_ref="user:1",
        idempotency_key=f"message-key:{suffix}",
    )
    task = repo.create_task(
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        owner_id=1,
        goal_ref=f"goal:{suffix}",
    )
    return conversation.id, task.task_id


def _settled_action(
    repo: PureAgentRepository,
    *,
    task_id: str,
    suffix: str,
) -> tuple[str, str, AgentTaskState]:
    reservation = repo.reserve_action(
        task_id=task_id,
        event_id=f"event-action:{suffix}",
        action_type="request_information_basis",
        execution_kind="direct",
        arguments={"missing": "deadline"},
        effect_key=f"effect-key:{suffix}",
        effect_type="agent_action",
        replay_policy="safe_idempotent",
        fencing_token=1,
    )
    repo.mark_effect_running(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=1,
        expected_state_version=reservation.state.state_version,
    )
    settlement = repo.settle_effect(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=1,
        expected_state_version=reservation.state.state_version,
        status="succeeded",
        result_ref=f"observation:{suffix}",
        result={"missing": "deadline"},
    )
    assert settlement.accepted_for_context
    observed = repo.commit_transition(
        _event(
            reservation.state,
            TaskEventType.OBSERVATION_ACCEPTED,
            event_id=f"event-observation:{suffix}",
            action_ref=reservation.action_id,
            observation_ref=f"observation:{suffix}",
        )
    ).state
    return reservation.action_id, reservation.effect_fence_id, observed


def _suspend(
    repo: PureAgentRepository,
    *,
    task_id: str,
    suffix: str,
    resume_token: str,
):
    action_id, effect_fence_id, _ = _settled_action(
        repo,
        task_id=task_id,
        suffix=suffix,
    )
    return repo.suspend_for_slot(
        task_id=task_id,
        event_id=f"event-pending:{suffix}",
        name="bid.deadline",
        request_message="请补充允许的工期天数。",
        input_model_ref="slot-model:deadline-v1",
        business_validator_refs=("validator:min-deadline",),
        context_snapshot_ref=f"context:{suffix}",
        suspended_action_id=action_id,
        effect_fence_id=effect_fence_id,
        resume_token=resume_token,
    )


def test_checkpoint_resume_requires_token_and_latest_recovery_fence(repository) -> None:
    repo, session = repository
    conversation_id, task_id = _new_task(repo, suffix="resume")
    resume_token = "resume-token-v601-valid"
    suspension = _suspend(
        repo,
        task_id=task_id,
        suffix="resume",
        resume_token=resume_token,
    )
    assert suspension.state.status is AgentTaskStatus.PENDING
    assert suspension.checkpoint.status.value == "open"

    candidate_message = repo.append_message(
        conversation_id=conversation_id,
        role="user",
        message_type="user.slot_candidate",
        content={"deadline_days": 30},
        created_by_ref="user:1",
        idempotency_key="slot-message:resume",
    )
    format_attempt = repo.begin_slot_validation(
        task_id=task_id,
        event_id="event-format-start:resume",
        candidate_message_id=candidate_message.id,
        candidate={"deadline_days": 30},
        idempotency_key="format-attempt:resume",
    )
    business_attempt = repo.accept_slot_format(
        task_id=task_id,
        event_id="event-format-pass:resume",
        format_attempt_id=format_attempt.attempt_id,
        typed_value={"deadline_days": 30},
        business_idempotency_key="business-attempt:resume",
    )

    with pytest.raises(PureAgentFenceRejected, match="resume proof"):
        repo.resolve_slot_and_resume(
            task_id=task_id,
            event_id="event-resume:wrong-token",
            business_attempt_id=business_attempt.attempt_id,
            resolved_value={"deadline_days": 30},
            resume_token="resume-token-v601-wrong",
        )

    claim = repo.claim_pending_recovery(
        task_id=task_id,
        lease_owner="worker:v601",
        lease_seconds=30,
    )
    assert claim.fencing_token == 1
    with pytest.raises(PureAgentFenceRejected, match="required or stale"):
        repo.resolve_slot_and_resume(
            task_id=task_id,
            event_id="event-resume:missing-fence",
            business_attempt_id=business_attempt.attempt_id,
            resolved_value={"deadline_days": 30},
            resume_token=resume_token,
        )

    resumed = repo.resolve_slot_and_resume(
        task_id=task_id,
        event_id="event-resume:accepted",
        business_attempt_id=business_attempt.attempt_id,
        resolved_value={"deadline_days": 30},
        resume_token=resume_token,
        recovery_fencing_token=claim.fencing_token,
    )
    assert resumed.state.status is AgentTaskStatus.RUNNING
    assert resumed.state.pending_context is None
    checkpoint = session.get(BidPureAgentCheckpoint, suspension.checkpoint.checkpoint_id)
    slot = session.get(BidPureAgentSlot, suspension.slot.slot_id)
    assert checkpoint.status == "consumed"
    assert checkpoint.consumed_at is not None
    assert checkpoint.recovery_lease_owner is None
    assert slot.status == "resolved"
    assert slot.resolved_value_ref is not None

    replay = repo.resolve_slot_and_resume(
        task_id=task_id,
        event_id="event-resume:accepted",
        business_attempt_id=business_attempt.attempt_id,
        resolved_value={"deadline_days": 30},
        resume_token=resume_token,
        recovery_fencing_token=claim.fencing_token,
    )
    assert replay.replayed
    assert replay.state == resumed.state


def test_pending_cancellation_invalidates_checkpoint_and_recovery(repository) -> None:
    repo, session = repository
    conversation_id, task_id = _new_task(repo, suffix="cancel-pending")
    suspension = _suspend(
        repo,
        task_id=task_id,
        suffix="cancel-pending",
        resume_token="resume-token-v601-cancel",
    )
    claim = repo.claim_pending_recovery(
        task_id=task_id,
        lease_owner="worker:v601",
        lease_seconds=30,
    )
    cancelled = repo.cancel_task(
        task_id=task_id,
        event_id="event-cancel:pending",
        requested_by_ref="user:1",
        reason="用户取消",
        expected_state_version=suspension.state.state_version,
        expected_owner_id=1,
        expected_conversation_id=conversation_id,
    )
    assert claim.checkpoint.status.value == "open"
    assert cancelled.state.status is AgentTaskStatus.CANCELLED
    checkpoint = session.get(BidPureAgentCheckpoint, suspension.checkpoint.checkpoint_id)
    assert checkpoint.status == "invalidated"
    assert checkpoint.recovery_lease_owner is None
    assert checkpoint.recovery_lease_until is None
    with pytest.raises(PureAgentConflict, match="only pending task"):
        repo.assess_pending_recovery(task_id=task_id)


def test_cancelled_effect_result_is_recorded_but_never_accepted(repository) -> None:
    repo, session = repository
    _, task_id = _new_task(repo, suffix="late-result")
    reservation = repo.reserve_action(
        task_id=task_id,
        event_id="event-action:late-result",
        action_type="evidence_read",
        execution_kind="direct",
        arguments={"evidence_ref": "evidence:1"},
        effect_key="effect-key:late-result",
        effect_type="tool_call",
        replay_policy="safe_idempotent",
        fencing_token=9,
    )
    repo.mark_effect_running(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=9,
        expected_state_version=reservation.state.state_version,
    )
    repo.cancel_task(
        task_id=task_id,
        event_id="event-cancel:late-result",
        requested_by_ref="user:1",
        reason="用户取消",
        expected_state_version=reservation.state.state_version,
    )
    late = repo.settle_effect(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=9,
        expected_state_version=reservation.state.state_version,
        status="succeeded",
        result_ref="result:late",
        result={"secret": "late"},
    )
    assert late.status == "ignored_late"
    assert not late.accepted_for_context
    fence = session.get(BidPureAgentEffectFence, reservation.effect_fence_id)
    assert fence.status == "ignored_late"
    assert fence.error_code == "PURE_AGENT_LATE_RESULT_IGNORED"
