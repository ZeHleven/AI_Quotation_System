"""Phase 3D Run cancellation, checkpoint retry, and lifecycle convergence.

This service is a control-plane boundary only.  It never executes model, OCR,
tool, document, or object-storage work.  API commands, their idempotency
record, Outbox events, and audit rows are committed by the caller in one
database transaction.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessment, BidAssessmentScope
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidAsyncOperation,
    BidCheckpoint,
    BidTask,
    BidTaskAttempt,
    BidTaskDependency,
)
from app.models.bid_assessment_tooling import BidToolInvocation
from app.models.bid_tool_execution import BidToolDispatch, BidToolDispatchAttempt
from app.models.bid_model_execution import BidModelCall, BidModelCallAttempt
from app.models.bid_run_validation import BidRunValidation, BidRunValidationAttempt
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
)
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_run_snapshots import build_run_progress_snapshot, run_etag


logger = logging.getLogger(__name__)


RUN_CANCEL_ROUTE_TEMPLATE = (
    "/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/cancel"
)
RUN_RETRY_ROUTE_TEMPLATE = (
    "/api/v1/bid-assessments/{assessment_id}/runs/{run_id}/retry"
)
RUN_LIFECYCLE_PRODUCER = "bid-run-lifecycle-v1"

CANCELLABLE_RUN_STATES = frozenset(
    {
        "created",
        "planning",
        "queued",
        "running",
        "waiting_input",
        "waiting_operation",
        "validating",
    }
)
RUN_TERMINAL_STATES = frozenset({"succeeded", "stale", "cancelled"})
TASK_TERMINAL_STATES = frozenset({"succeeded", "skipped", "stale", "cancelled"})
ATTEMPT_ACTIVE_STATES = frozenset(
    {"created", "leased", "running", "waiting_operation", "waiting_input", "validating"}
)
ASYNC_ACTIVE_STATES = frozenset({"created", "submitted", "running"})
TOOL_INVOCATION_ACTIVE_STATES = frozenset({"accepted", "pending"})
TOOL_DISPATCH_ACTIVE_STATES = frozenset(
    {"queued", "leased", "sending", "awaiting_receipt", "retry_wait"}
)
MODEL_CALL_ACTIVE_STATES = frozenset({"accepted", "leased", "sending", "retry_wait"})
RETRY_TASK_STATES = frozenset(
    {"failed", "leased", "running", "waiting_operation", "waiting_input", "validating"}
)


class BidRunLifecycleError(RuntimeError):
    code = "BID_RUN_LIFECYCLE_ERROR"


class BidRunLifecycleNotFound(BidRunLifecycleError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidRunLifecycleVersionMismatch(BidRunLifecycleError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(
        self,
        run: BidAnalysisRun,
        *,
        provided_etag: str,
        current_etag: str,
    ):
        super().__init__(self.code)
        self.assessment_id = str(run.assessment_id)
        self.run_id = str(run.id)
        self.provided_etag = str(provided_etag)
        self.current_etag = str(current_etag)
        self.current_row_version = int(run.row_version)


class BidRunNotCancellable(BidRunLifecycleError):
    code = "BID_RUN_NOT_CANCELLABLE"

    def __init__(self, run: BidAnalysisRun):
        super().__init__(self.code)
        self.run_id = str(run.id)
        self.status = str(run.status)
        self.retryable = bool(run.retryable)


class BidRunNotRetryable(BidRunLifecycleError):
    code = "BID_RUN_NOT_RETRYABLE"

    def __init__(self, run: BidAnalysisRun, *, reason: str):
        super().__init__(self.code)
        self.run_id = str(run.id)
        self.status = str(run.status)
        self.retryable = bool(run.retryable)
        self.reason = str(reason)


class BidRunInputStale(BidRunLifecycleError):
    code = "BID_RUN_INPUT_STALE"

    def __init__(self, run: BidAnalysisRun, *, reasons: tuple[str, ...]):
        super().__init__(self.code)
        self.run_id = str(run.id)
        self.reasons = tuple(sorted(set(str(reason) for reason in reasons)))


@dataclass(frozen=True)
class RunCancellationMaintenanceResult:
    scanned: int
    cancelled: int
    tasks_cancelled: int
    attempts_cancelled: int
    operations_cancelled: int
    failed: int


def _locked_assessment_run(
    db: Session,
    *,
    assessment_id: str,
    run_id: str,
) -> tuple[BidAssessment, BidAnalysisRun]:
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == str(assessment_id))
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise BidRunLifecycleNotFound()
    run = (
        db.query(BidAnalysisRun)
        .filter(
            BidAnalysisRun.id == str(run_id),
            BidAnalysisRun.assessment_id == str(assessment_id),
        )
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise BidRunLifecycleNotFound()
    return assessment, run


def _locked_visible_assessment_run(
    db: Session,
    *,
    assessment_id: str,
    run_id: str,
    actor_id: int,
    actor_is_admin: bool,
) -> tuple[BidAssessment, BidAnalysisRun]:
    assessment, run = _locked_assessment_run(
        db,
        assessment_id=assessment_id,
        run_id=run_id,
    )
    if int(assessment.created_by) != int(actor_id) and not actor_is_admin:
        raise BidRunLifecycleNotFound()
    return assessment, run


def _assert_run_etag(
    db: Session,
    run: BidAnalysisRun,
    *,
    expected_run_etag: str,
) -> None:
    snapshot = build_run_progress_snapshot(db, run)
    current_etag = run_etag(str(run.id), int(run.row_version), snapshot)
    if str(expected_run_etag) != current_etag:
        raise BidRunLifecycleVersionMismatch(
            run,
            provided_etag=expected_run_etag,
            current_etag=current_etag,
        )


def _response(db: Session, run: BidAnalysisRun) -> IdempotentCommandResult:
    return IdempotentCommandResult(
        status_code=202,
        body={
            "code": 202,
            "message": "accepted",
            "data": build_run_progress_snapshot(db, run),
            "error": None,
            "request_id": "",
        },
        resource_type="run",
        resource_id=str(run.id),
        response_ref=(
            f"/api/v1/bid-assessments/{run.assessment_id}/runs/{run.id}"
        ),
    )


def _task_counts(db: Session, *, run_id: str) -> tuple[int, int]:
    statuses = [
        str(row[0])
        for row in db.query(BidTask.status).filter(BidTask.run_id == str(run_id)).all()
    ]
    return (
        sum(1 for status in statuses if status in {"succeeded", "skipped"}),
        len(statuses),
    )


def request_run_cancellation(
    db: Session,
    *,
    assessment_id: str,
    run_id: str,
    reason: str,
    expected_run_etag: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Persist a cancellation request; maintenance performs the terminal fence."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    normalized_reason = str(reason or "").strip()
    if not normalized_reason or len(normalized_reason) > 1000:
        raise BidRunLifecycleError("BID_RUN_CANCEL_REASON_INVALID")
    assessment, run = _locked_visible_assessment_run(
        db,
        assessment_id=assessment_id,
        run_id=run_id,
        actor_id=actor_id,
        actor_is_admin=actor_is_admin,
    )
    _assert_run_etag(db, run, expected_run_etag=expected_run_etag)

    if str(run.status) == "cancelled":
        result = _response(db, run)
        result.body["request_id"] = str(request_id)
        return result
    if run.cancel_requested_at is not None and str(run.status) not in RUN_TERMINAL_STATES:
        result = _response(db, run)
        result.body["request_id"] = str(request_id)
        return result
    cancellable = str(run.status) in CANCELLABLE_RUN_STATES or (
        str(run.status) == "failed" and bool(run.retryable)
    )
    if not cancellable or str(assessment.active_run_id or "") != str(run.id):
        raise BidRunNotCancellable(run)

    before = {
        "status": str(run.status),
        "current_stage": str(run.current_stage or ""),
        "cancel_requested_at": None,
        "row_version": int(run.row_version),
    }
    previous_stage = str(run.current_stage or "planning")
    run.cancel_requested_at = current_time
    run.current_stage = "cancelling"
    run.waiting_reason = normalized_reason[:500]
    run.row_version = int(run.row_version) + 1
    db.flush()
    completed, total = _task_counts(db, run_id=str(run.id))
    event = append_outbox_event(
        db,
        event_type="bid.run.cancel_requested.v1",
        producer=RUN_LIFECYCLE_PRODUCER,
        aggregate_type="run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(request_id),
        payload_schema="bid.run.cancel_requested.v1.payload",
        payload={
            "run_id": str(run.id),
            "from": str(run.status),
            "to": str(run.status),
            "retryable": bool(run.retryable),
            "reason": normalized_reason,
            "cancel_requested_at": current_time.isoformat(),
            "previous_stage": previous_stage,
            "stage_code": "cancelling",
            "status": "running",
            "message": "取消请求已持久化，正在围栏运行任务",
            "completed_units": completed,
            "total_units": total,
            "resource_version": int(run.row_version),
        },
        dedupe_key=f"run-cancel-requested:{run.id}:rv{run.row_version}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=str(actor_ref),
        action="run.cancel.request",
        entity_type="run",
        entity_id=str(run.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id),
        correlation_id=str(event.event_id),
        before=before,
        after={
            "status": str(run.status),
            "current_stage": str(run.current_stage),
            "cancel_requested_at": current_time.isoformat(),
            "row_version": int(run.row_version),
        },
        metadata={"reason": normalized_reason},
        occurred_at=current_time,
    )
    db.flush()
    result = _response(db, run)
    result.body["request_id"] = str(request_id)
    return result


def _latest_checkpoint(db: Session, *, task_id: str) -> BidCheckpoint | None:
    return (
        db.query(BidCheckpoint)
        .join(BidTaskAttempt, BidTaskAttempt.id == BidCheckpoint.task_attempt_id)
        .filter(BidTaskAttempt.task_id == str(task_id))
        .order_by(
            BidTaskAttempt.attempt_no.desc(),
            BidCheckpoint.action_seq.desc(),
            BidCheckpoint.created_at.desc(),
        )
        .first()
    )


def _retry_staleness_reasons(
    db: Session,
    *,
    assessment: BidAssessment,
    run: BidAnalysisRun,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if str(assessment.lifecycle_status) != "active":
        reasons.append("assessment_not_active")
    if str(assessment.active_run_id or "") != str(run.id):
        reasons.append("run_not_current_active")
    if str(assessment.current_manifest_id or "") != str(run.manifest_id):
        reasons.append("manifest_is_not_current")
    if str(assessment.business_status) in {"stale_input", "cancelled", "superseded"}:
        reasons.append(f"assessment_{assessment.business_status}")
    latest_scope = (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == assessment.id)
        .order_by(BidAssessmentScope.version.desc(), BidAssessmentScope.created_at.desc())
        .first()
    )
    if latest_scope is None or str(latest_scope.id) != str(run.scope_id):
        reasons.append("scope_is_not_current")
    return tuple(sorted(set(reasons)))


def _dependencies_satisfied(
    task_id: str,
    *,
    dependencies: dict[str, set[str]],
    task_statuses: dict[str, str],
) -> bool:
    return all(
        task_statuses.get(parent_id) in {"succeeded", "skipped"}
        for parent_id in dependencies.get(str(task_id), set())
    )


def retry_run_from_latest_checkpoint(
    db: Session,
    *,
    assessment_id: str,
    run_id: str,
    retry_mode: str,
    note: str | None,
    expected_run_etag: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Fence old claims and create new, unleased Attempts under the same Run."""

    if str(retry_mode) != "from_latest_checkpoint":
        raise BidRunLifecycleError("BID_RUN_RETRY_MODE_INVALID")
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    assessment, run = _locked_visible_assessment_run(
        db,
        assessment_id=assessment_id,
        run_id=run_id,
        actor_id=actor_id,
        actor_is_admin=actor_is_admin,
    )
    _assert_run_etag(db, run, expected_run_etag=expected_run_etag)
    if str(run.status) != "failed" or not bool(run.retryable):
        raise BidRunNotRetryable(run, reason="run_not_failed_retryable")
    if run.cancel_requested_at is not None:
        raise BidRunNotRetryable(run, reason="cancel_already_requested")
    stale_reasons = _retry_staleness_reasons(db, assessment=assessment, run=run)
    if stale_reasons:
        raise BidRunInputStale(run, reasons=stale_reasons)

    tasks = (
        db.query(BidTask)
        .filter(BidTask.run_id == run.id)
        .order_by(BidTask.priority.asc(), BidTask.created_at.asc(), BidTask.id.asc())
        .with_for_update()
        .all()
    )
    if not tasks:
        raise BidRunNotRetryable(run, reason="run_has_no_tasks")
    task_by_id = {str(task.id): task for task in tasks}
    task_statuses = {task_id: str(task.status) for task_id, task in task_by_id.items()}
    dependencies: dict[str, set[str]] = {}
    for row in (
        db.query(BidTaskDependency)
        .filter(BidTaskDependency.run_id == run.id)
        .all()
    ):
        dependencies.setdefault(str(row.task_id), set()).add(str(row.depends_on_task_id))

    restart_tasks = [task for task in tasks if str(task.status) in RETRY_TASK_STATES]
    if not restart_tasks:
        raise BidRunNotRetryable(run, reason="no_interrupted_task")

    active_attempts = (
        db.query(BidTaskAttempt)
        .join(BidTask, BidTask.id == BidTaskAttempt.task_id)
        .filter(
            BidTask.run_id == run.id,
            BidTaskAttempt.status.in_(tuple(ATTEMPT_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for attempt in active_attempts:
        attempt.status = "cancelled"
        attempt.finished_at = current_time
        attempt.heartbeat_at = current_time
        attempt.row_version = int(attempt.row_version) + 1

    operations = (
        db.query(BidAsyncOperation)
        .filter(
            BidAsyncOperation.task_id.in_(tuple(task_by_id)),
            BidAsyncOperation.status.in_(tuple(ASYNC_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for operation in operations:
        operation.status = "cancelled"
        operation.finished_at = current_time
        operation.row_version = int(operation.row_version) + 1

    tool_invocations = (
        db.query(BidToolInvocation)
        .filter(
            BidToolInvocation.run_id == run.id,
            BidToolInvocation.status.in_(tuple(TOOL_INVOCATION_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for invocation in tool_invocations:
        invocation.status = "cancelled"
        invocation.error_code = "BID_RUN_RETRY_FENCE"
        invocation.completed_at = current_time
        invocation.row_version = int(invocation.row_version) + 1
    tool_dispatches = (
        db.query(BidToolDispatch)
        .filter(
            BidToolDispatch.task_id.in_(tuple(task_by_id)),
            BidToolDispatch.status.in_(tuple(TOOL_DISPATCH_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for dispatch in tool_dispatches:
        active_dispatch_attempts = (
            db.query(BidToolDispatchAttempt)
            .filter(
                BidToolDispatchAttempt.dispatch_id == dispatch.id,
                BidToolDispatchAttempt.status.in_(("leased", "sending")),
            )
            .with_for_update()
            .all()
        )
        for dispatch_attempt in active_dispatch_attempts:
            dispatch_attempt.status = "cancelled"
            dispatch_attempt.finished_at = current_time
            dispatch_attempt.error_code = "BID_RUN_RETRY_FENCE"
        dispatch.status = "cancelled"
        dispatch.lease_owner = None
        dispatch.lease_until = None
        dispatch.last_error_code = "BID_RUN_RETRY_FENCE"
        dispatch.completed_at = current_time
        dispatch.row_version = int(dispatch.row_version) + 1
    model_calls = (
        db.query(BidModelCall)
        .filter(
            BidModelCall.run_id == run.id,
            BidModelCall.status.in_(tuple(MODEL_CALL_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for model_call in model_calls:
        model_attempts = (
            db.query(BidModelCallAttempt)
            .filter(
                BidModelCallAttempt.model_call_id == model_call.id,
                BidModelCallAttempt.status.in_(("leased", "sending")),
            )
            .with_for_update()
            .all()
        )
        for model_attempt in model_attempts:
            model_attempt.status = "cancelled"
            model_attempt.finished_at = current_time
            model_attempt.error_code = "BID_RUN_RETRY_FENCE"
            model_attempt.outcome_hash = canonical_hash(
                {
                    "attempt_id": str(model_attempt.id),
                    "status": "cancelled",
                    "error_code": "BID_RUN_RETRY_FENCE",
                }
            )
        model_call.status = "cancelled"
        model_call.lease_owner = None
        model_call.lease_until = None
        model_call.last_error_code = "BID_RUN_RETRY_FENCE"
        model_call.completed_at = current_time
        model_call.row_version = int(model_call.row_version) + 1

    created_attempts: list[dict[str, Any]] = []
    for task in restart_tasks:
        task_id = str(task.id)
        if not _dependencies_satisfied(
            task_id,
            dependencies=dependencies,
            task_statuses=task_statuses,
        ):
            task.status = "blocked"
            task.current_attempt_id = None
            task.row_version = int(task.row_version) + 1
            continue
        checkpoint = _latest_checkpoint(db, task_id=task_id)
        attempt_no = int(
            db.query(func.max(BidTaskAttempt.attempt_no))
            .filter(BidTaskAttempt.task_id == task_id)
            .scalar()
            or 0
        ) + 1
        fencing_token = int(
            db.query(func.max(BidTaskAttempt.fencing_token))
            .filter(BidTaskAttempt.task_id == task_id)
            .scalar()
            or 0
        ) + 1
        new_attempt = BidTaskAttempt(
            id=str(uuid.uuid4()),
            task_id=task_id,
            attempt_no=attempt_no,
            status="created",
            fencing_token=fencing_token,
            row_version=1,
            created_at=current_time,
            updated_at=current_time,
        )
        db.add(new_attempt)
        db.flush()
        task.status = "ready"
        task.current_attempt_id = str(new_attempt.id)
        task.row_version = int(task.row_version) + 1
        created_attempts.append(
            {
                "task_id": task_id,
                "attempt_id": str(new_attempt.id),
                "attempt_no": attempt_no,
                "fencing_token": fencing_token,
                "resume_checkpoint_id": str(checkpoint.id) if checkpoint is not None else None,
            }
        )

    if not created_attempts:
        raise BidRunNotRetryable(run, reason="no_task_ready_for_retry")
    from_state = str(run.status)
    before = {
        "status": from_state,
        "retryable": bool(run.retryable),
        "waiting_reason": str(run.waiting_reason or ""),
        "row_version": int(run.row_version),
    }
    run.status = "queued"
    run.retryable = False
    run.waiting_reason = None
    run.cancel_requested_at = None
    run.finished_at = None
    run.row_version = int(run.row_version) + 1
    db.flush()
    completed, total = _task_counts(db, run_id=str(run.id))
    event = append_outbox_event(
        db,
        event_type="bid.run.retry_requested.v1",
        producer=RUN_LIFECYCLE_PRODUCER,
        aggregate_type="run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(request_id),
        payload_schema="bid.run.retry_requested.v1.payload",
        payload={
            "run_id": str(run.id),
            "from": from_state,
            "to": "queued",
            "retryable": False,
            "retry_mode": "from_latest_checkpoint",
            "attempts": created_attempts,
            "completed_units": completed,
            "total_units": total,
            "resource_version": int(run.row_version),
        },
        dedupe_key=f"run-retry-requested:{run.id}:rv{run.row_version}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=str(actor_ref),
        action="run.retry.request",
        entity_type="run",
        entity_id=str(run.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id),
        correlation_id=str(event.event_id),
        before=before,
        after={
            "status": str(run.status),
            "retryable": bool(run.retryable),
            "waiting_reason": None,
            "row_version": int(run.row_version),
            "created_attempt_count": len(created_attempts),
        },
        metadata={
            "retry_mode": "from_latest_checkpoint",
            "note": str(note).strip()[:1000] if note else None,
            "attempt_ids": [row["attempt_id"] for row in created_attempts],
            "resume_checkpoint_ids": [
                row["resume_checkpoint_id"]
                for row in created_attempts
                if row["resume_checkpoint_id"] is not None
            ],
        },
        occurred_at=current_time,
    )
    db.flush()
    result = _response(db, run)
    result.body["request_id"] = str(request_id)
    return result


def finalize_cancel_requested_run(
    db: Session,
    *,
    run_id: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, int, int, int]:
    """Fence one requested Run and atomically converge all control-plane rows."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    run_probe = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == str(run_id)).one_or_none()
    if run_probe is None:
        return False, 0, 0, 0
    assessment, run = _locked_assessment_run(
        db,
        assessment_id=str(run_probe.assessment_id),
        run_id=str(run_id),
    )
    if run.cancel_requested_at is None or str(run.status) in RUN_TERMINAL_STATES:
        return False, 0, 0, 0
    current_request_id = str(request_id or f"cancel-settle:{run.id}")
    from_state = str(run.status)
    assessment_from_state = str(assessment.business_status)
    tasks = (
        db.query(BidTask)
        .filter(BidTask.run_id == run.id)
        .with_for_update()
        .all()
    )
    task_ids = tuple(str(task.id) for task in tasks)
    attempts = []
    operations = []
    if task_ids:
        attempts = (
            db.query(BidTaskAttempt)
            .filter(
                BidTaskAttempt.task_id.in_(task_ids),
                BidTaskAttempt.status.in_(tuple(ATTEMPT_ACTIVE_STATES)),
            )
            .with_for_update()
            .all()
        )
        operations = (
            db.query(BidAsyncOperation)
            .filter(
                BidAsyncOperation.task_id.in_(task_ids),
                BidAsyncOperation.status.in_(tuple(ASYNC_ACTIVE_STATES)),
            )
            .with_for_update()
            .all()
        )
    tool_invocations = (
        db.query(BidToolInvocation)
        .filter(
            BidToolInvocation.run_id == run.id,
            BidToolInvocation.status.in_(tuple(TOOL_INVOCATION_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for attempt in attempts:
        attempt.status = "cancelled"
        attempt.finished_at = current_time
        attempt.heartbeat_at = current_time
        attempt.row_version = int(attempt.row_version) + 1
    tasks_cancelled = 0
    for task in tasks:
        if str(task.status) not in TASK_TERMINAL_STATES:
            task.status = "cancelled"
            task.row_version = int(task.row_version) + 1
            tasks_cancelled += 1
    for operation in operations:
        operation.status = "cancelled"
        operation.finished_at = current_time
        operation.row_version = int(operation.row_version) + 1
    for invocation in tool_invocations:
        invocation.status = "cancelled"
        invocation.error_code = "BID_RUN_CANCELLED"
        invocation.completed_at = current_time
        invocation.row_version = int(invocation.row_version) + 1
    tool_dispatches = (
        db.query(BidToolDispatch)
        .filter(
            BidToolDispatch.task_id.in_(task_ids) if task_ids else False,
            BidToolDispatch.status.in_(tuple(TOOL_DISPATCH_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for dispatch in tool_dispatches:
        active_dispatch_attempts = (
            db.query(BidToolDispatchAttempt)
            .filter(
                BidToolDispatchAttempt.dispatch_id == dispatch.id,
                BidToolDispatchAttempt.status.in_(("leased", "sending")),
            )
            .with_for_update()
            .all()
        )
        for dispatch_attempt in active_dispatch_attempts:
            dispatch_attempt.status = "cancelled"
            dispatch_attempt.finished_at = current_time
            dispatch_attempt.error_code = "BID_RUN_CANCELLED"
        dispatch.status = "cancelled"
        dispatch.lease_owner = None
        dispatch.lease_until = None
        dispatch.last_error_code = "BID_RUN_CANCELLED"
        dispatch.completed_at = current_time
        dispatch.row_version = int(dispatch.row_version) + 1

    model_calls = (
        db.query(BidModelCall)
        .filter(
            BidModelCall.run_id == run.id,
            BidModelCall.status.in_(tuple(MODEL_CALL_ACTIVE_STATES)),
        )
        .with_for_update()
        .all()
    )
    for model_call in model_calls:
        model_attempts = (
            db.query(BidModelCallAttempt)
            .filter(
                BidModelCallAttempt.model_call_id == model_call.id,
                BidModelCallAttempt.status.in_(("leased", "sending")),
            )
            .with_for_update()
            .all()
        )
        for model_attempt in model_attempts:
            model_attempt.status = "cancelled"
            model_attempt.finished_at = current_time
            model_attempt.error_code = "BID_RUN_CANCELLED"
            model_attempt.outcome_hash = canonical_hash(
                {
                    "attempt_id": str(model_attempt.id),
                    "status": "cancelled",
                    "error_code": "BID_RUN_CANCELLED",
                }
            )
        model_call.status = "cancelled"
        model_call.lease_owner = None
        model_call.lease_until = None
        model_call.last_error_code = "BID_RUN_CANCELLED"
        model_call.completed_at = current_time
        model_call.row_version = int(model_call.row_version) + 1

    run_validations = (
        db.query(BidRunValidation)
        .filter(
            BidRunValidation.run_id == run.id,
            BidRunValidation.status.in_(("requested", "leased", "running")),
        )
        .with_for_update()
        .all()
    )
    for validation in run_validations:
        validation_attempts = (
            db.query(BidRunValidationAttempt)
            .filter(
                BidRunValidationAttempt.validation_id == validation.id,
                BidRunValidationAttempt.status.in_(("leased", "running")),
            )
            .with_for_update()
            .all()
        )
        for validation_attempt in validation_attempts:
            validation_attempt.status = "cancelled"
            validation_attempt.finished_at = current_time
            validation_attempt.heartbeat_at = current_time
            validation_attempt.error_code = "BID_RUN_CANCELLED"
        validation.status = "cancelled"
        validation.lease_owner = None
        validation.lease_until = None
        validation.failure_code = "BID_RUN_CANCELLED"
        validation.finished_at = current_time
        validation.row_version = int(validation.row_version) + 1

    run.status = "cancelled"
    run.retryable = False
    run.current_stage = "cancelled"
    run.finished_at = current_time
    run.row_version = int(run.row_version) + 1
    assessment.business_status = "cancelled"
    assessment.row_version = int(assessment.row_version) + 1
    db.flush()
    event = append_outbox_event(
        db,
        event_type="bid.run.cancelled.v1",
        producer=RUN_LIFECYCLE_PRODUCER,
        aggregate_type="run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=current_request_id,
        payload_schema="bid.run.cancelled.v1.payload",
        payload={
            "run_id": str(run.id),
            "from": from_state,
            "to": "cancelled",
            "retryable": False,
            "resource_version": int(run.row_version),
            "assessment_resource_version": int(assessment.row_version),
            "tasks_cancelled": tasks_cancelled,
            "attempts_cancelled": len(attempts),
            "operations_cancelled": len(operations),
        },
        dedupe_key=f"run-cancelled:{run.id}:rv{run.row_version}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{RUN_LIFECYCLE_PRODUCER}",
        action="run.cancel.settle",
        entity_type="run",
        entity_id=str(run.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=current_request_id,
        correlation_id=str(event.event_id),
        before={"status": from_state, "business_status": assessment_from_state},
        after={
            "status": "cancelled",
            "business_status": "cancelled",
            "tasks_cancelled": tasks_cancelled,
            "attempts_cancelled": len(attempts),
            "operations_cancelled": len(operations),
            "tool_invocations_cancelled": len(tool_invocations),
            "tool_dispatches_cancelled": len(tool_dispatches),
            "model_calls_cancelled": len(model_calls),
            "run_validations_cancelled": len(run_validations),
        },
        occurred_at=current_time,
    )
    db.flush()
    return True, tasks_cancelled, len(attempts), len(operations)


def maintain_run_lifecycle(
    *,
    session_factory: Callable[[], Session],
    limit: int = 100,
    now: datetime | None = None,
) -> RunCancellationMaintenanceResult:
    scan_db = session_factory()
    try:
        run_ids = [
            str(row[0])
            for row in scan_db.query(BidAnalysisRun.id)
            .filter(
                BidAnalysisRun.cancel_requested_at.is_not(None),
                ~BidAnalysisRun.status.in_(tuple(RUN_TERMINAL_STATES)),
            )
            .order_by(BidAnalysisRun.cancel_requested_at.asc(), BidAnalysisRun.id.asc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        ]
    finally:
        scan_db.close()

    cancelled = tasks_cancelled = attempts_cancelled = operations_cancelled = failed = 0
    for run_id in run_ids:
        db = session_factory()
        try:
            with db.begin():
                changed, task_count, attempt_count, operation_count = (
                    finalize_cancel_requested_run(
                        db,
                        run_id=run_id,
                        now=now,
                    )
                )
            if changed:
                cancelled += 1
                tasks_cancelled += task_count
                attempts_cancelled += attempt_count
                operations_cancelled += operation_count
        except Exception:
            logger.exception(
                "bid_run_lifecycle_maintenance_failed",
                extra={"run_id": run_id},
            )
            failed += 1
        finally:
            db.close()
    return RunCancellationMaintenanceResult(
        scanned=len(run_ids),
        cancelled=cancelled,
        tasks_cancelled=tasks_cancelled,
        attempts_cancelled=attempts_cancelled,
        operations_cancelled=operations_cancelled,
        failed=failed,
    )
