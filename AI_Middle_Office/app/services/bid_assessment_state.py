"""Deterministic state transitions with same-transaction Outbox and audit."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidAsyncOperation,
    BidPlanRevision,
    BidQuestion,
    BidQuestionRound,
    BidTask,
    BidTaskAttempt,
)
from app.services.bid_assessment_eventing import append_audit_log, append_outbox_event


class BidStateError(RuntimeError):
    code = "BID_STATE_ERROR"


class BidStateNotFound(BidStateError):
    code = "BID_STATE_RESOURCE_NOT_FOUND"


class BidStateConflict(BidStateError):
    code = "BID_STATE_CONFLICT"


class BidVersionConflict(BidStateConflict):
    code = "BID_RESOURCE_VERSION_MISMATCH"


class BidFencingConflict(BidStateConflict):
    code = "BID_FENCING_TOKEN_MISMATCH"


@dataclass(frozen=True)
class BidActor:
    actor_type: str
    actor_ref: str
    actor_id: int | None = None

    @classmethod
    def user(cls, user_id: int, username: str) -> "BidActor":
        return cls(actor_type="user", actor_id=int(user_id), actor_ref=f"user:{username}")

    @classmethod
    def service(cls, service_name: str) -> "BidActor":
        return cls(actor_type="service", actor_ref=f"service:{service_name}")


@dataclass(frozen=True)
class BidStateTransitionResult:
    entity_type: str
    entity_id: str
    from_state: str
    to_state: str
    row_version: int
    outbox_event_id: str
    audit_id: str


_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "bid_assessment"
    / "v1"
    / "state-transitions.json"
)
_MACHINE_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))["state_machines"]

ATTEMPT_TRANSITIONS = {
    "created": {"leased", "cancelled"},
    "leased": {"running", "lease_expired", "cancelled"},
    "running": {
        "waiting_operation",
        "waiting_input",
        "validating",
        "succeeded",
        "failed",
        "stale",
        "cancelled",
        "lease_expired",
    },
    "waiting_operation": {"lease_expired", "cancelled"},
    "waiting_input": {"lease_expired", "cancelled"},
    "validating": {"succeeded", "failed", "stale", "lease_expired"},
    "succeeded": set(),
    "failed": set(),
    "stale": set(),
    "cancelled": set(),
    "lease_expired": set(),
}

ENTITY_CONFIG: dict[str, tuple[type, str, str]] = {
    "assessment": (BidAssessment, "business_status", "assessment_business"),
    "run": (BidAnalysisRun, "status", "analysis_run"),
    "plan_revision": (BidPlanRevision, "status", "plan_revision"),
    "task": (BidTask, "status", "task"),
    "task_attempt": (BidTaskAttempt, "status", "task_attempt"),
    "question_round": (BidQuestionRound, "status", "question"),
    "async_operation": (BidAsyncOperation, "status", "async_operation"),
}


def _allowed_transitions(machine_name: str, from_state: str) -> set[str]:
    if machine_name == "task_attempt":
        return ATTEMPT_TRANSITIONS.get(from_state, set())
    transitions = _MACHINE_CONTRACT[machine_name]["transitions"]
    return set(transitions.get(from_state, []))


def _scope_for_entity(
    db: Session,
    *,
    entity_type: str,
    entity: Any,
) -> tuple[str | None, str | None]:
    if entity_type == "assessment":
        return entity.id, None
    if entity_type == "run":
        return entity.assessment_id, entity.id
    if entity_type == "question_round":
        return entity.assessment_id, entity.run_id
    if entity_type == "plan_revision":
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == entity.run_id).one()
        return run.assessment_id, run.id
    if entity_type == "task":
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == entity.run_id).one()
        return run.assessment_id, run.id
    if entity_type == "task_attempt":
        task = db.query(BidTask).filter(BidTask.id == entity.task_id).one()
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == task.run_id).one()
        return run.assessment_id, run.id
    if entity_type == "async_operation":
        task = db.query(BidTask).filter(BidTask.id == entity.task_id).one()
        run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == task.run_id).one()
        return run.assessment_id, run.id
    raise BidStateConflict("BID_STATE_ENTITY_TYPE_INVALID")


def _verify_fencing(
    db: Session,
    *,
    entity_type: str,
    entity: Any,
    expected_fencing_token: int | None,
) -> None:
    attempt: BidTaskAttempt | None = None
    if entity_type == "task_attempt":
        attempt = entity
    elif entity_type == "task" and entity.current_attempt_id:
        attempt = (
            db.query(BidTaskAttempt)
            .filter(
                BidTaskAttempt.id == entity.current_attempt_id,
                BidTaskAttempt.task_id == entity.id,
            )
            .with_for_update()
            .one_or_none()
        )
    if attempt is None:
        return
    if expected_fencing_token is None:
        raise BidFencingConflict("BID_FENCING_TOKEN_REQUIRED")
    if int(attempt.fencing_token) != int(expected_fencing_token):
        raise BidFencingConflict("BID_FENCING_TOKEN_MISMATCH")


def _apply_transition_fields(
    db: Session,
    *,
    entity_type: str,
    entity: Any,
    from_state: str,
    to_state: str,
    now: datetime,
) -> None:
    if entity_type == "run":
        if to_state in {"succeeded", "failed", "stale", "cancelled"}:
            entity.finished_at = now
        elif from_state == "failed" and to_state == "queued":
            entity.finished_at = None
    elif entity_type == "plan_revision":
        if to_state in {"committed", "rejected", "superseded"} and not entity.validated_hash:
            raise BidStateConflict("BID_PLAN_VALIDATED_HASH_REQUIRED")
        if to_state == "committed":
            entity.committed_slot_key = "committed"
            entity.committed_at = now
        elif to_state == "superseded":
            entity.committed_slot_key = None
            entity.superseded_at = now
    elif entity_type == "question_round":
        questions = (
            db.query(BidQuestion)
            .filter(BidQuestion.question_round_id == entity.id)
            .with_for_update()
            .all()
        )
        if to_state == "published":
            question_count = len(questions)
            if not 1 <= question_count <= 3:
                raise BidStateConflict("BID_QUESTION_ROUND_SIZE_INVALID")
            entity.open_slot_key = "published"
            entity.published_at = now
        elif to_state in {"answered", "expired", "withdrawn", "superseded"}:
            entity.open_slot_key = None
            if to_state == "answered":
                entity.answered_at = now
            elif to_state == "withdrawn":
                entity.withdrawn_at = now
        for question in questions:
            if question.status != from_state:
                raise BidStateConflict("BID_QUESTION_ROUND_MEMBER_STATE_MISMATCH")
            question.status = to_state
            question.row_version = int(question.row_version) + 1
    elif entity_type == "async_operation":
        if to_state == "submitted":
            entity.submitted_at = now
        elif to_state == "running":
            entity.started_at = entity.started_at or now
        elif to_state in {"succeeded", "failed", "cancelled", "timed_out"}:
            entity.finished_at = now
    elif entity_type == "task_attempt":
        if to_state == "running":
            entity.started_at = entity.started_at or now
        elif to_state in {"succeeded", "failed", "stale", "cancelled", "lease_expired"}:
            entity.finished_at = now
            if to_state == "lease_expired":
                entity.lease_reclaimed_at = now


def _public_payload_defaults(
    *,
    entity_type: str,
    entity: Any,
    from_state: str,
    to_state: str,
    assessment_id: str | None,
    run_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    question_counts: tuple[int, int] | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("from", from_state)
    result.setdefault("to", to_state)
    result.setdefault("resource_version", int(entity.row_version))
    if entity_type == "run":
        result.setdefault("run_id", entity.id)
        result.setdefault("retryable", bool(entity.retryable))
    elif entity_type in {"task", "task_attempt"}:
        result.setdefault("run_id", run_id)
        result.setdefault("stage_code", getattr(entity, "task_type", "task_execution"))
        result.setdefault("status", to_state)
        result.setdefault("message", "")
        result.setdefault("completed_units", 1 if to_state == "succeeded" else 0)
        result.setdefault("total_units", 1)
    elif entity_type == "question_round":
        result.setdefault("round_id", entity.id)
        result.setdefault("run_id", entity.run_id)
        if event_type == "bid.question.published.v1" and question_counts is not None:
            result.setdefault("question_count", question_counts[0])
            result.setdefault("critical_count", question_counts[1])
    elif entity_type == "assessment":
        result.setdefault("recommended_view", "assessment")
        result.setdefault("allowed_actions", [])
    result.setdefault("assessment_id", assessment_id)
    return result


def transition_bid_state(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    to_state: str,
    expected_row_version: int,
    event_type: str,
    actor: BidActor,
    request_id: str,
    payload: dict[str, Any] | None = None,
    causation_event_id: str | None = None,
    expected_fencing_token: int | None = None,
    producer: str = "bid-workflow-service",
    now: datetime | None = None,
) -> BidStateTransitionResult:
    config = ENTITY_CONFIG.get(entity_type)
    if config is None:
        raise BidStateConflict("BID_STATE_ENTITY_TYPE_INVALID")
    model, state_attribute, machine_name = config
    entity = (
        db.query(model)
        .filter(model.id == entity_id)
        .with_for_update()
        .one_or_none()
    )
    if entity is None:
        raise BidStateNotFound(f"BID_STATE_RESOURCE_NOT_FOUND:{entity_type}:{entity_id}")
    if int(entity.row_version) != int(expected_row_version):
        raise BidVersionConflict(
            f"BID_RESOURCE_VERSION_MISMATCH:{expected_row_version}:{entity.row_version}"
        )

    from_state = str(getattr(entity, state_attribute))
    if to_state not in _allowed_transitions(machine_name, from_state):
        raise BidStateConflict(f"BID_STATE_TRANSITION_INVALID:{from_state}:{to_state}")
    _verify_fencing(
        db,
        entity_type=entity_type,
        entity=entity,
        expected_fencing_token=expected_fencing_token,
    )

    transition_time = now or datetime.now(timezone.utc)
    question_counts: tuple[int, int] | None = None
    if entity_type == "question_round" and to_state == "published":
        questions = (
            db.query(BidQuestion)
            .filter(BidQuestion.question_round_id == entity.id)
            .with_for_update()
            .all()
        )
        question_counts = (
            len(questions),
            sum(1 for question in questions if question.priority == "critical"),
        )
    before = {"state": from_state, "row_version": int(entity.row_version)}
    setattr(entity, state_attribute, to_state)
    entity.row_version = int(entity.row_version) + 1
    _apply_transition_fields(
        db,
        entity_type=entity_type,
        entity=entity,
        from_state=from_state,
        to_state=to_state,
        now=transition_time,
    )
    assessment_id, run_id = _scope_for_entity(
        db,
        entity_type=entity_type,
        entity=entity,
    )
    event_payload = _public_payload_defaults(
        entity_type=entity_type,
        entity=entity,
        from_state=from_state,
        to_state=to_state,
        assessment_id=assessment_id,
        run_id=run_id,
        event_type=event_type,
        payload=payload or {},
        question_counts=question_counts,
    )
    outbox = append_outbox_event(
        db,
        event_type=event_type,
        producer=producer,
        aggregate_type=entity_type,
        aggregate_id=entity_id,
        aggregate_version=int(entity.row_version),
        assessment_id=assessment_id,
        run_id=run_id,
        request_id=request_id,
        causation_event_id=causation_event_id,
        payload_schema=f"{event_type}.payload",
        payload=event_payload,
        dedupe_key=f"state:{entity_type}:{entity_id}:rv{entity.row_version}:{event_type}",
        occurred_at=transition_time,
    )
    after = {"state": to_state, "row_version": int(entity.row_version)}
    audit = append_audit_log(
        db,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        actor_ref=actor.actor_ref,
        action=f"{entity_type}.state.transition",
        entity_type=entity_type,
        entity_id=entity_id,
        assessment_id=assessment_id,
        request_id=request_id,
        correlation_id=outbox.event_id,
        before=before,
        after=after,
        metadata={
            "event_type": event_type,
            "causation_event_id": causation_event_id,
        },
        outcome="succeeded",
        occurred_at=transition_time,
    )
    db.flush()
    return BidStateTransitionResult(
        entity_type=entity_type,
        entity_id=entity_id,
        from_state=from_state,
        to_state=to_state,
        row_version=int(entity.row_version),
        outbox_event_id=outbox.event_id,
        audit_id=audit.id,
    )
