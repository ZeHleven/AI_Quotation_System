"""Phase 3G deterministic Run validation and terminal convergence.

The validator reads only governed database authority.  It performs no model,
OCR, visual, Tool Adapter, network, or object-storage operation.  Every
primitive flushes and leaves commit/rollback to its caller.
"""
from __future__ import annotations

import logging
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
)
from app.models.bid_assessment_config import BidModelProfileVersion, BidPromptBundle
from app.models.bid_assessment_eventing import BidOutboxEvent, BidProcessedEvent
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidAsyncOperation,
    BidCheckpoint,
    BidPlanRevision,
    BidTask,
    BidTaskAttempt,
    BidTaskDependency,
)
from app.models.bid_assessment_tooling import (
    BidContextManifest,
    BidToolInvocation,
    BidToolResult,
)
from app.models.bid_run_validation import BidRunValidation, BidRunValidationAttempt
from app.models.bid_tool_execution import BidToolDispatch, BidToolDispatchAttempt
from app.models.bid_model_execution import (
    BidModelCall,
    BidModelCallAttempt,
    BidModelResult,
)
from app.agents.bid_assessment_local.contracts import (
    normalize_local_agent_state,
    normalize_task_action,
)
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    canonical_json,
    process_outbox_event_once,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_model_execution import (
    MODEL_PROVIDER_ACCOUNTING_SCHEMA,
    resolve_model_route,
)
from app.services.bid_task_registry import load_standard_task_registry
from app.services.bid_task_runtime import build_task_contract


RUN_VALIDATION_CONSUMER = "bid-run-validation-v1"
RUN_VALIDATION_PRODUCER = "bid-run-validation-v1"
RUN_VALIDATION_EVENT = "bid.run.validation_requested.v1"
RUN_VALIDATOR_VERSION = "bid-run-integrity-validator-v2"
PHASE4_RUN_VALIDATOR_VERSION = "bid-run-integrity-validator-v4"
MVP1_RUN_VALIDATOR_VERSION = "bid-run-integrity-validator-v5"
MODEL_LINEAGE_VALIDATORS = frozenset(
    {PHASE4_RUN_VALIDATOR_VERSION, MVP1_RUN_VALIDATOR_VERSION}
)
SUCCESS_TASK_STATES = frozenset({"succeeded", "skipped"})
TERMINAL_TASK_STATES = frozenset({"succeeded", "failed", "skipped", "stale", "cancelled"})
ACTIVE_TASK_STATES = frozenset(
    {"blocked", "ready", "leased", "running", "waiting_operation", "waiting_input", "validating"}
)
ACTIVE_ATTEMPT_STATES = frozenset({"created", "leased", "running", "waiting_operation", "waiting_input", "validating"})
ACTIVE_OPERATION_STATES = frozenset({"created", "submitted", "running"})
ACTIVE_INVOCATION_STATES = frozenset({"accepted", "pending"})
ACTIVE_DISPATCH_STATES = frozenset({"queued", "leased", "sending", "awaiting_receipt", "retry_wait"})
ACTIVE_MODEL_CALL_STATES = frozenset({"accepted", "leased", "sending", "retry_wait"})
TERMINAL_RUN_STATES = frozenset({"succeeded", "stale", "cancelled"})
logger = logging.getLogger(__name__)


class BidRunValidationError(RuntimeError):
    code = "BID_RUN_VALIDATION_ERROR"


class BidRunValidationEventInvalid(BidRunValidationError):
    code = "BID_RUN_VALIDATION_EVENT_INVALID"


class BidRunValidationNotLeaseable(BidRunValidationError):
    code = "BID_RUN_VALIDATION_NOT_LEASEABLE"


class BidRunValidationFenceLost(BidRunValidationError):
    code = "BID_RUN_VALIDATION_FENCE_LOST"


@dataclass(frozen=True)
class RunValidationClaim:
    validation_id: str
    run_id: str
    attempt_id: str
    attempt_no: int
    fencing_token: int
    worker_id: str
    lease_until: datetime


@dataclass(frozen=True)
class RunValidationExecutionResult:
    validation_id: str
    run_id: str
    outcome: str
    run_status: str
    retryable: bool
    result_hash: str


@dataclass(frozen=True)
class RunValidationBatchResult:
    scanned: int
    claimed: int
    passed: int
    failed: int
    stale: int
    ignored: int
    errors: int


@dataclass(frozen=True)
class RunValidationMaintenanceResult:
    scanned_events: int
    materialized: int
    duplicate: int
    recovered: int
    cancelled: int
    failed: int


def _utc_text(value: datetime) -> str:
    return as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_validation_event(event: BidOutboxEvent) -> None:
    if str(event.event_type) != RUN_VALIDATION_EVENT or str(event.aggregate_type) != "run":
        raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_EVENT_TYPE_INVALID")
    payload = dict(event.payload_json or {})
    required = {
        "run_id",
        "from",
        "to",
        "retryable",
        "completed_units",
        "total_units",
        "resource_version",
    }
    if not required.issubset(payload) or str(payload.get("to")) != "validating":
        raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_EVENT_PAYLOAD_INVALID")
    if str(payload.get("run_id")) != str(event.run_id or event.aggregate_id):
        raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_EVENT_RUN_MISMATCH")


def _committed_plan(db: Session, run_id: str) -> BidPlanRevision | None:
    rows = (
        db.query(BidPlanRevision)
        .filter(
            BidPlanRevision.run_id == run_id,
            BidPlanRevision.status == "committed",
            BidPlanRevision.committed_slot_key == "committed",
        )
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def _run_validator_version(plan: BidPlanRevision | None) -> str:
    envelope = dict(plan.proposal_json or {}) if plan is not None else {}
    if str(envelope.get("schema") or "") != "bid.plan.commit.envelope.v2":
        return RUN_VALIDATOR_VERSION
    return (
        MVP1_RUN_VALIDATOR_VERSION
        if settings.feature_bid_assessment_phase4_preliminary_report
        else PHASE4_RUN_VALIDATOR_VERSION
    )


def _all_plan_revisions(db: Session, run_id: str) -> list[BidPlanRevision]:
    return (
        db.query(BidPlanRevision)
        .filter(
            BidPlanRevision.run_id == run_id,
            BidPlanRevision.status.in_(("committed", "superseded")),
        )
        .order_by(BidPlanRevision.revision_no.asc(), BidPlanRevision.id.asc())
        .all()
    )


def _runtime_lineage_snapshot(
    db: Session,
    *,
    run_id: str,
    include_model_lineage: bool = True,
) -> dict[str, Any]:
    """Return the deterministic A-G runtime lineage bound into validation input."""

    attempts = (
        db.query(BidTaskAttempt)
        .join(BidTask, BidTask.id == BidTaskAttempt.task_id)
        .filter(BidTask.run_id == run_id)
        .order_by(
            BidTaskAttempt.task_id.asc(),
            BidTaskAttempt.attempt_no.asc(),
            BidTaskAttempt.id.asc(),
        )
        .all()
    )
    attempt_ids = tuple(str(row.id) for row in attempts)
    checkpoints = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.task_attempt_id.in_(attempt_ids))
        .order_by(
            BidCheckpoint.task_attempt_id.asc(),
            BidCheckpoint.action_seq.asc(),
            BidCheckpoint.id.asc(),
        )
        .all()
        if attempt_ids
        else []
    )
    contexts = (
        db.query(BidContextManifest)
        .filter(BidContextManifest.run_id == run_id)
        .order_by(
            BidContextManifest.task_id.asc(),
            BidContextManifest.task_attempt_id.asc(),
            BidContextManifest.manifest_seq.asc(),
            BidContextManifest.id.asc(),
        )
        .all()
    )
    invocations = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.run_id == run_id)
        .order_by(
            BidToolInvocation.task_id.asc(),
            BidToolInvocation.task_attempt_id.asc(),
            BidToolInvocation.invocation_seq.asc(),
            BidToolInvocation.id.asc(),
        )
        .all()
    )
    task_ids = tuple(
        str(row[0])
        for row in db.query(BidTask.id)
        .filter(BidTask.run_id == run_id)
        .order_by(BidTask.id.asc())
        .all()
    )
    operations = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.task_id.in_(task_ids))
        .order_by(
            BidAsyncOperation.task_id.asc(),
            BidAsyncOperation.task_attempt_id.asc(),
            BidAsyncOperation.id.asc(),
        )
        .all()
        if task_ids
        else []
    )
    dispatches = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.task_id.in_(task_ids))
        .order_by(BidToolDispatch.task_id.asc(), BidToolDispatch.id.asc())
        .all()
        if task_ids
        else []
    )
    dispatch_ids = tuple(str(row.id) for row in dispatches)
    dispatch_attempts = (
        db.query(BidToolDispatchAttempt)
        .filter(BidToolDispatchAttempt.dispatch_id.in_(dispatch_ids))
        .order_by(
            BidToolDispatchAttempt.dispatch_id.asc(),
            BidToolDispatchAttempt.attempt_no.asc(),
            BidToolDispatchAttempt.id.asc(),
        )
        .all()
        if dispatch_ids
        else []
    )
    invocation_ids = tuple(str(row.id) for row in invocations)
    results = (
        db.query(BidToolResult)
        .filter(BidToolResult.invocation_id.in_(invocation_ids))
        .order_by(BidToolResult.invocation_id.asc(), BidToolResult.id.asc())
        .all()
        if invocation_ids
        else []
    )
    model_calls = (
        db.query(BidModelCall)
        .filter(BidModelCall.run_id == run_id)
        .order_by(BidModelCall.task_id.asc(), BidModelCall.action_seq.asc())
        .all()
        if include_model_lineage
        else []
    )
    model_call_ids = tuple(str(row.id) for row in model_calls)
    model_attempts = (
        db.query(BidModelCallAttempt)
        .filter(BidModelCallAttempt.model_call_id.in_(model_call_ids))
        .order_by(
            BidModelCallAttempt.model_call_id.asc(),
            BidModelCallAttempt.attempt_no.asc(),
        )
        .all()
        if model_call_ids
        else []
    )
    model_results = (
        db.query(BidModelResult)
        .filter(BidModelResult.model_call_id.in_(model_call_ids))
        .order_by(BidModelResult.model_call_id.asc(), BidModelResult.id.asc())
        .all()
        if model_call_ids
        else []
    )
    snapshot = {
        "attempts": [
            {
                "attempt_id": str(row.id),
                "task_id": str(row.task_id),
                "attempt_no": int(row.attempt_no),
                "status": str(row.status),
                "fencing_token": int(row.fencing_token),
                "error_code": str(row.error_code) if row.error_code else None,
            }
            for row in attempts
        ],
        "checkpoints": [
            {
                "checkpoint_id": str(row.id),
                "task_attempt_id": str(row.task_attempt_id),
                "fencing_token": int(row.fencing_token),
                "action_seq": int(row.action_seq),
                "context_manifest_id": (
                    str(row.context_manifest_id) if row.context_manifest_id else None
                ),
                "state_hash": str(row.state_hash),
                "tool_refs_hash": canonical_hash(row.tool_refs_json),
                "budget_usage_hash": canonical_hash(row.budget_usage_json),
                "candidate_output_ref": (
                    str(row.candidate_output_ref) if row.candidate_output_ref else None
                ),
                "next_state": str(row.next_state) if row.next_state else None,
            }
            for row in checkpoints
        ],
        "context_manifests": [
            {
                "context_manifest_id": str(row.id),
                "assessment_id": str(row.assessment_id),
                "task_id": str(row.task_id),
                "task_attempt_id": str(row.task_attempt_id),
                "manifest_seq": int(row.manifest_seq),
                "fencing_token": int(row.fencing_token),
                "manifest_hash": str(row.manifest_hash),
            }
            for row in contexts
        ],
        "tool_invocations": [
            {
                "invocation_id": str(row.id),
                "assessment_id": str(row.assessment_id),
                "task_id": str(row.task_id),
                "task_attempt_id": str(row.task_attempt_id),
                "context_manifest_id": str(row.context_manifest_id),
                "async_operation_id": (
                    str(row.async_operation_id) if row.async_operation_id else None
                ),
                "checkpoint_id": str(row.checkpoint_id) if row.checkpoint_id else None,
                "invocation_seq": int(row.invocation_seq),
                "fencing_token": int(row.fencing_token),
                "tool_name": str(row.tool_name),
                "arguments_hash": str(row.arguments_hash),
                "request_hash": str(row.request_hash),
                "scope_token_hash": str(row.scope_token_hash),
                "status": str(row.status),
                "error_code": str(row.error_code) if row.error_code else None,
            }
            for row in invocations
        ],
        "async_operations": [
            {
                "operation_id": str(row.id),
                "task_id": str(row.task_id),
                "task_attempt_id": str(row.task_attempt_id),
                "operation_type": str(row.operation_type),
                "status": str(row.status),
                "input_hash": str(row.input_hash),
                "result_ref": str(row.result_ref) if row.result_ref else None,
                "error_code": str(row.error_code) if row.error_code else None,
                "retry_count": int(row.retry_count),
            }
            for row in operations
        ],
        "tool_dispatches": [
            {
                "dispatch_id": str(row.id),
                "invocation_id": str(row.invocation_id),
                "async_operation_id": str(row.async_operation_id),
                "task_id": str(row.task_id),
                "task_attempt_id": str(row.task_attempt_id),
                "adapter_name": str(row.adapter_name),
                "adapter_version": str(row.adapter_version),
                "adapter_mode": str(row.adapter_mode),
                "replay_policy": str(row.replay_policy),
                "envelope_hash": str(row.envelope_hash),
                "scope_token_hash": str(row.scope_token_hash),
                "status": str(row.status),
                "attempt_count": int(row.attempt_count),
                "reserved_cost_microunits": int(row.reserved_cost_microunits),
                "actual_cost_microunits": int(row.actual_cost_microunits),
                "fencing_token": int(row.fencing_token),
                "provider_request_id": str(row.provider_request_id),
                "provider_receipt_id": (
                    str(row.provider_receipt_id) if row.provider_receipt_id else None
                ),
                "actual_cost_microunits": int(row.actual_cost_microunits),
                "last_error_code": (
                    str(row.last_error_code) if row.last_error_code else None
                ),
            }
            for row in dispatches
        ],
        "tool_dispatch_attempts": [
            {
                "dispatch_attempt_id": str(row.id),
                "dispatch_id": str(row.dispatch_id),
                "attempt_no": int(row.attempt_no),
                "fencing_token": int(row.fencing_token),
                "status": str(row.status),
                "execution_key": str(row.execution_key),
                "outcome_hash": str(row.outcome_hash) if row.outcome_hash else None,
                "error_code": str(row.error_code) if row.error_code else None,
            }
            for row in dispatch_attempts
        ],
        "tool_results": [
            {
                "result_id": str(row.id),
                "invocation_id": str(row.invocation_id),
                "task_attempt_id": str(row.task_attempt_id),
                "async_operation_id": (
                    str(row.async_operation_id) if row.async_operation_id else None
                ),
                "status": str(row.status),
                "storage_kind": str(row.storage_kind),
                "data_hash": str(row.data_hash),
                "result_hash": str(row.result_hash),
            }
            for row in results
        ],
    }
    if include_model_lineage:
        snapshot["model_calls"] = [
            {
                "model_call_id": str(row.id),
                "task_id": str(row.task_id),
                "task_attempt_id": str(row.task_attempt_id),
                "checkpoint_id": str(row.checkpoint_id),
                "context_manifest_id": str(row.context_manifest_id),
                "async_operation_id": str(row.async_operation_id),
                "action_seq": int(row.action_seq),
                "fencing_token": int(row.fencing_token),
                "logical_role": str(row.logical_role),
                "provider_ref": str(row.provider_ref),
                "model_ref": str(row.model_ref),
                "prompt_role": str(row.prompt_role),
                "action_schema": str(row.action_schema),
                "replay_policy": str(row.replay_policy),
                "request_hash": str(row.request_hash),
                "input_hash": str(row.input_hash),
                "status": str(row.status),
                "attempt_count": int(row.attempt_count),
                "max_attempts": int(row.max_attempts),
                "reserved_input_tokens": int(row.reserved_input_tokens),
                "reserved_output_tokens": int(row.reserved_output_tokens),
                "actual_input_tokens": int(row.actual_input_tokens),
                "actual_output_tokens": int(row.actual_output_tokens),
                "reserved_cost_microunits": int(row.reserved_cost_microunits),
                "actual_cost_microunits": int(row.actual_cost_microunits),
                "last_error_code": (
                    str(row.last_error_code) if row.last_error_code else None
                ),
            }
            for row in model_calls
        ]
        snapshot["model_call_attempts"] = [
            {
                "model_call_attempt_id": str(row.id),
                "model_call_id": str(row.model_call_id),
                "attempt_no": int(row.attempt_no),
                "fencing_token": int(row.fencing_token),
                "status": str(row.status),
                "execution_key": str(row.execution_key),
                "provider_request_id": str(row.provider_request_id),
                "outcome_hash": str(row.outcome_hash) if row.outcome_hash else None,
                "error_code": str(row.error_code) if row.error_code else None,
            }
            for row in model_attempts
        ]
        snapshot["model_results"] = [
            {
                "model_result_id": str(row.id),
                "model_call_id": str(row.model_call_id),
                "model_call_attempt_id": str(row.model_call_attempt_id),
                "task_id": str(row.task_id),
                "source_task_attempt_id": str(row.source_task_attempt_id),
                "action_type": str(row.action_type),
                "action_hash": str(row.action_hash),
                "response_hash": str(row.response_hash),
                "actual_cost_microunits": int(row.actual_cost_microunits),
                "result_hash": str(row.result_hash),
            }
            for row in model_results
        ]
    return snapshot


def _validation_input(
    db: Session,
    *,
    run: BidAnalysisRun,
    plan: BidPlanRevision,
) -> dict[str, Any]:
    tasks = (
        db.query(BidTask)
        .filter(BidTask.run_id == run.id)
        .order_by(BidTask.task_key.asc(), BidTask.id.asc())
        .all()
    )
    dependencies = (
        db.query(BidTaskDependency)
        .filter(BidTaskDependency.run_id == run.id)
        .order_by(
            BidTaskDependency.task_id.asc(),
            BidTaskDependency.depends_on_task_id.asc(),
        )
        .all()
    )
    task_rows: list[dict[str, Any]] = []
    for task in tasks:
        attempt = None
        checkpoint = None
        if task.current_attempt_id:
            attempt = (
                db.query(BidTaskAttempt)
                .filter(
                    BidTaskAttempt.id == task.current_attempt_id,
                    BidTaskAttempt.task_id == task.id,
                )
                .one_or_none()
            )
        if attempt is not None:
            checkpoint = (
                db.query(BidCheckpoint)
                .filter(BidCheckpoint.task_attempt_id == attempt.id)
                .order_by(BidCheckpoint.action_seq.desc(), BidCheckpoint.id.desc())
                .first()
            )
        task_rows.append(
            {
                "task_id": str(task.id),
                "task_key": str(task.task_key),
                "task_type": str(task.task_type),
                "status": str(task.status),
                "input_hash": str(task.input_hash),
                "attempt_id": str(attempt.id) if attempt is not None else None,
                "attempt_no": int(attempt.attempt_no) if attempt is not None else None,
                "attempt_status": str(attempt.status) if attempt is not None else None,
                "fencing_token": int(attempt.fencing_token) if attempt is not None else None,
                "checkpoint_id": str(checkpoint.id) if checkpoint is not None else None,
                "checkpoint_state_hash": (
                    str(checkpoint.state_hash) if checkpoint is not None else None
                ),
            }
        )
    plans = _all_plan_revisions(db, str(run.id))
    validator_version = _run_validator_version(plan)
    runtime_lineage = _runtime_lineage_snapshot(
        db,
        run_id=str(run.id),
        include_model_lineage=validator_version in MODEL_LINEAGE_VALIDATORS,
    )
    payload = {
        "schema": "bid.run.validation.input.v1",
        "validator_version": validator_version,
        "run_id": str(run.id),
        "assessment_id": str(run.assessment_id),
        "run_input_hash": str(run.input_hash),
        "run_input_fingerprint": str(run.input_fingerprint),
        "plan_revision_id": str(plan.id),
        "plan_validated_hash": str(plan.validated_hash or ""),
        "tasks": task_rows,
        "dependencies": [
            {
                "task_id": str(row.task_id),
                "depends_on_task_id": str(row.depends_on_task_id),
            }
            for row in dependencies
        ],
        "runtime_lineage": runtime_lineage,
    }
    comparison_binding_required = bool(
        getattr(settings, "feature_bid_assessment_phase4_fact_verification", False)
        or run.hard_gate_comparison_baseline_id
        or run.hard_gate_comparison_baseline_hash
    )
    if comparison_binding_required:
        payload["hard_gate_comparison_baseline_id"] = (
            str(run.hard_gate_comparison_baseline_id)
            if run.hard_gate_comparison_baseline_id
            else None
        )
        payload["hard_gate_comparison_baseline_hash"] = (
            str(run.hard_gate_comparison_baseline_hash)
            if run.hard_gate_comparison_baseline_hash
            else None
        )
    if validator_version in MODEL_LINEAGE_VALIDATORS:
        payload["plan_revisions"] = [
            {
                "plan_revision_id": str(row.id),
                "revision_no": int(row.revision_no),
                "status": str(row.status),
                "validated_hash": str(row.validated_hash or ""),
                "schema": str((dict(row.proposal_json or {})).get("schema") or ""),
                "stage": (dict(row.proposal_json or {})).get("stage"),
            }
            for row in plans
        ]
        if validator_version == MVP1_RUN_VALIDATOR_VERSION:
            payload["mvp1_result_authority"] = (
                _mvp1_result_authority_snapshot(db, str(run.id))
                or {
                    "schema": "bid.mvp1.result-authority.v1",
                    "run_id": str(run.id),
                    "status": "missing",
                }
            )
    return payload


def _mvp1_result_authority_snapshot(
    db: Session,
    run_id: str,
) -> dict[str, Any] | None:
    """Bind the published MVP-1 artifact set into the Run validation input.

    The import is deliberately local so Phase 3/4A profiles that have not
    enabled or migrated MVP-1 keep their historical validation payloads.
    """

    from app.models.bid_assessment_results import (
        BidClaimCitation,
        BidHardGateResult,
        BidPreliminaryDecision,
        BidPreliminaryReport,
        BidReportClaim,
        BidReportValidation,
        BidResolvedFact,
        BidResolvedFactHead,
    )

    report = (
        db.query(BidPreliminaryReport)
        .filter(BidPreliminaryReport.run_id == run_id)
        .one_or_none()
    )
    if report is None:
        return None
    decision = (
        db.query(BidPreliminaryDecision)
        .filter(BidPreliminaryDecision.run_id == run_id)
        .one_or_none()
    )
    report_validation = (
        db.query(BidReportValidation)
        .filter(BidReportValidation.run_id == run_id)
        .one_or_none()
    )
    gates = (
        db.query(BidHardGateResult)
        .filter(BidHardGateResult.run_id == run_id)
        .order_by(BidHardGateResult.gate_code.asc())
        .all()
    )
    facts = (
        db.query(BidResolvedFact)
        .join(
            BidResolvedFactHead,
            BidResolvedFactHead.resolved_fact_id == BidResolvedFact.id,
        )
        .filter(BidResolvedFactHead.run_id == run_id)
        .order_by(BidResolvedFact.fact_slot.asc(), BidResolvedFact.id.asc())
        .all()
    )
    claims = (
        db.query(BidReportClaim)
        .filter(BidReportClaim.run_id == run_id)
        .order_by(BidReportClaim.claim_order.asc(), BidReportClaim.id.asc())
        .all()
    )
    claim_ids = tuple(str(row.id) for row in claims)
    citations = (
        db.query(BidClaimCitation)
        .filter(BidClaimCitation.claim_id.in_(claim_ids))
        .order_by(BidClaimCitation.claim_id.asc(), BidClaimCitation.id.asc())
        .all()
        if claim_ids
        else []
    )
    published_event = (
        db.query(BidOutboxEvent)
        .filter(
            BidOutboxEvent.event_type == "bid.report.published.v1",
            BidOutboxEvent.run_id == run_id,
            BidOutboxEvent.aggregate_id == report.id,
        )
        .order_by(BidOutboxEvent.occurred_at.desc())
        .first()
    )
    payload = {
        "schema": "bid.mvp1.result-authority.v1",
        "run_id": run_id,
        "report": {
            "report_id": str(report.id),
            "assessment_id": str(report.assessment_id),
            "decision_id": str(report.decision_id),
            "validation_id": str(report.validation_id),
            "status": str(report.status),
            "report_version": int(report.report_version),
            "report_hash": str(report.report_hash),
            "body_hash": canonical_hash(report.report_json),
        },
        "decision": (
            {
                "decision_id": str(decision.id),
                "run_id": str(decision.run_id),
                "decision": str(decision.decision),
                "decision_hash": str(decision.decision_hash),
            }
            if decision is not None
            else None
        ),
        "validation": (
            {
                "validation_id": str(report_validation.id),
                "run_id": str(report_validation.run_id),
                "status": str(report_validation.status),
                "input_hash": str(report_validation.input_hash),
                "result_hash": str(report_validation.result_hash),
                "checks_hash": canonical_hash(report_validation.checks_json),
            }
            if report_validation is not None
            else None
        ),
        "gates": [
            {
                "gate_id": str(row.id),
                "gate_code": str(row.gate_code),
                "status": str(row.status),
                "result_hash": str(row.result_hash),
                "details_hash": canonical_hash(row.details_json),
            }
            for row in gates
        ],
        "facts": [
            {
                "fact_id": str(row.id),
                "fact_slot": str(row.fact_slot),
                "scope_type": str(row.scope_type),
                "scope_id": str(row.scope_id),
                "status": str(row.status),
                "resolution_hash": str(row.resolution_hash),
            }
            for row in facts
        ],
        "claims": [
            {
                "claim_id": str(row.id),
                "claim_order": int(row.claim_order),
                "status": str(row.status),
                "claim_hash": str(row.claim_hash),
            }
            for row in claims
        ],
        "citations": [
            {
                "citation_id": str(row.id),
                "claim_id": str(row.claim_id),
                "evidence_fragment_id": str(row.evidence_fragment_id),
                "citation_hash": str(row.citation_hash),
            }
            for row in citations
        ],
        "published_event": (
            {
                "event_id": str(published_event.event_id),
                "payload_hash": str(published_event.payload_hash),
                "report_hash": str((published_event.payload_json or {}).get("report_hash") or ""),
            }
            if published_event is not None
            else None
        ),
    }
    payload["authority_hash"] = canonical_hash(payload)
    return payload


def _materialize_validation(
    db: Session,
    *,
    event: BidOutboxEvent,
    requested_at: datetime,
) -> dict[str, Any]:
    _require_validation_event(event)
    run_id = str(event.run_id or event.aggregate_id)
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == run_id)
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_RUN_NOT_FOUND")
    payload = dict(event.payload_json or {})
    if int(payload["resource_version"]) > int(run.row_version):
        raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_EVENT_VERSION_AHEAD")
    existing = (
        db.query(BidRunValidation)
        .filter(BidRunValidation.run_id == run_id)
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        if str(existing.source_event_id) != str(event.event_id):
            raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_DUPLICATE_REQUEST")
        return {
            "materialized": False,
            "ignored": False,
            "validation_id": str(existing.id),
            "result_ref": f"bid-run-validation:{existing.id}",
        }
    if str(run.status) in TERMINAL_RUN_STATES or str(run.status) == "failed":
        return {"materialized": False, "ignored": True, "validation_id": None}
    if str(run.status) != "validating":
        raise BidRunValidationEventInvalid("BID_RUN_VALIDATION_RUN_STATE_INVALID")
    plan = _committed_plan(db, run_id)
    input_payload = (
        _validation_input(db, run=run, plan=plan)
        if plan is not None
        else {
            "schema": "bid.run.validation.input.v1",
            "validator_version": RUN_VALIDATOR_VERSION,
            "run_id": run_id,
            "assessment_id": str(run.assessment_id),
            "run_input_hash": str(run.input_hash),
            "plan_revision_id": None,
            "plan_validated_hash": None,
            "tasks": [],
            "dependencies": [],
        }
    )
    validator_version = _run_validator_version(plan)
    validation = BidRunValidation(
        id=f"validation_{uuid.uuid4().hex}",
        assessment_id=str(run.assessment_id),
        run_id=run_id,
        plan_revision_id=str(plan.id) if plan is not None else None,
        source_event_id=str(event.event_id),
        validation_key=f"run-validation:{run_id}",
        validator_version=validator_version,
        input_hash=canonical_hash(input_payload),
        status="requested",
        retryable=False,
        attempt_count=0,
        fencing_token=0,
        requested_at=requested_at,
        row_version=1,
    )
    db.add(validation)
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{RUN_VALIDATION_CONSUMER}",
        action="run.validation.materialize",
        entity_type="run_validation",
        entity_id=str(validation.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(event.request_id),
        correlation_id=str(event.event_id),
        after={
            "run_id": run_id,
            "status": "requested",
            "input_hash": str(validation.input_hash),
            "validator_version": validator_version,
        },
        occurred_at=requested_at,
    )
    return {
        "materialized": True,
        "ignored": False,
        "validation_id": str(validation.id),
        "result_ref": f"bid-run-validation:{validation.id}",
    }


def consume_run_validation_requested_event(
    db: Session,
    *,
    event_id: str,
    now: datetime | None = None,
) -> ProcessedEventResult:
    current_time = as_utc(now) if now is not None else database_utc_now(db)

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) != RUN_VALIDATION_EVENT:
            return {"materialized": False, "ignored": True, "validation_id": None}
        return _materialize_validation(session, event=event, requested_at=current_time)

    return process_outbox_event_once(
        db,
        consumer_name=RUN_VALIDATION_CONSUMER,
        event_id=str(event_id),
        handler=_handler,
        processed_at=current_time,
    )


def pending_validation_event_ids(db: Session, *, limit: int = 100) -> list[str]:
    rows = (
        db.query(BidOutboxEvent.event_id)
        .outerjoin(
            BidProcessedEvent,
            and_(
                BidProcessedEvent.event_id == BidOutboxEvent.event_id,
                BidProcessedEvent.consumer_name == RUN_VALIDATION_CONSUMER,
            ),
        )
        .filter(
            BidOutboxEvent.event_type == RUN_VALIDATION_EVENT,
            BidProcessedEvent.event_id.is_(None),
        )
        .order_by(BidOutboxEvent.occurred_at.asc(), BidOutboxEvent.event_id.asc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    return [str(row[0]) for row in rows]


def claim_next_run_validation(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 90,
    now: datetime | None = None,
) -> RunValidationClaim | None:
    normalized_worker = str(worker_id or "")[:128]
    if not normalized_worker:
        raise BidRunValidationNotLeaseable("BID_RUN_VALIDATION_WORKER_REQUIRED")
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    validation = (
        db.query(BidRunValidation)
        .join(BidAnalysisRun, BidAnalysisRun.id == BidRunValidation.run_id)
        .join(BidAssessment, BidAssessment.id == BidRunValidation.assessment_id)
        .filter(
            BidRunValidation.status == "requested",
            BidAnalysisRun.status == "validating",
            BidAnalysisRun.cancel_requested_at.is_(None),
            BidAssessment.lifecycle_status == "active",
            BidAssessment.active_run_id == BidRunValidation.run_id,
        )
        .order_by(BidRunValidation.requested_at.asc(), BidRunValidation.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if validation is None:
        return None
    lease_until = current_time + timedelta(seconds=max(15, min(int(lease_seconds), 900)))
    attempt_no = int(validation.attempt_count) + 1
    fencing_token = int(validation.fencing_token) + 1
    attempt = BidRunValidationAttempt(
        id=f"validation_attempt_{uuid.uuid4().hex}",
        validation_id=str(validation.id),
        run_id=str(validation.run_id),
        attempt_no=attempt_no,
        fencing_token=fencing_token,
        worker_id=normalized_worker,
        status="leased",
        execution_key=f"run-validation:{validation.id}:{fencing_token}",
        lease_until=lease_until,
        started_at=current_time,
        heartbeat_at=current_time,
    )
    db.add(attempt)
    validation.status = "leased"
    validation.attempt_count = attempt_no
    validation.fencing_token = fencing_token
    validation.lease_owner = normalized_worker
    validation.lease_until = lease_until
    validation.started_at = validation.started_at or current_time
    validation.row_version = int(validation.row_version) + 1
    db.flush()
    return RunValidationClaim(
        validation_id=str(validation.id),
        run_id=str(validation.run_id),
        attempt_id=str(attempt.id),
        attempt_no=attempt_no,
        fencing_token=fencing_token,
        worker_id=normalized_worker,
        lease_until=lease_until,
    )


def _locked_claim(
    db: Session,
    claim: RunValidationClaim,
    *,
    now: datetime,
) -> tuple[BidRunValidation, BidRunValidationAttempt, BidAnalysisRun, BidAssessment]:
    validation = (
        db.query(BidRunValidation)
        .filter(BidRunValidation.id == claim.validation_id)
        .with_for_update()
        .one_or_none()
    )
    attempt = (
        db.query(BidRunValidationAttempt)
        .filter(BidRunValidationAttempt.id == claim.attempt_id)
        .with_for_update()
        .one_or_none()
    )
    if validation is None or attempt is None:
        raise BidRunValidationFenceLost(BidRunValidationFenceLost.code)
    if (
        str(validation.run_id) != str(claim.run_id)
        or str(attempt.validation_id) != str(validation.id)
        or int(validation.fencing_token) != int(claim.fencing_token)
        or int(attempt.fencing_token) != int(claim.fencing_token)
        or str(validation.lease_owner or "") != str(claim.worker_id)
        or str(attempt.worker_id) != str(claim.worker_id)
        or validation.lease_until is None
        or as_utc(validation.lease_until) <= now
        or as_utc(attempt.lease_until) <= now
        or str(validation.status) not in {"leased", "running"}
        or str(attempt.status) not in {"leased", "running"}
    ):
        raise BidRunValidationFenceLost(BidRunValidationFenceLost.code)
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == validation.run_id)
        .with_for_update()
        .one()
    )
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == validation.assessment_id)
        .with_for_update()
        .one()
    )
    return validation, attempt, run, assessment


def heartbeat_run_validation(
    db: Session,
    claim: RunValidationClaim,
    *,
    lease_seconds: int = 90,
    now: datetime | None = None,
) -> datetime:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    validation, attempt, _run, _assessment = _locked_claim(db, claim, now=current_time)
    lease_until = current_time + timedelta(seconds=max(15, min(int(lease_seconds), 900)))
    validation.status = "running"
    validation.lease_until = lease_until
    validation.row_version = int(validation.row_version) + 1
    attempt.status = "running"
    attempt.lease_until = lease_until
    attempt.heartbeat_at = current_time
    db.flush()
    return lease_until


def _current_scope(db: Session, assessment_id: str) -> BidAssessmentScope | None:
    return (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == assessment_id)
        .order_by(BidAssessmentScope.version.desc(), BidAssessmentScope.id.desc())
        .first()
    )


def _check(
    checks: list[dict[str, Any]],
    code: str,
    passed: bool,
    *,
    severity: str = "fatal",
    detail: Any = None,
) -> None:
    checks.append(
        {
            "code": code,
            "passed": bool(passed),
            "severity": severity,
            "detail": detail,
        }
    )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _runtime_lineage_checks(
    db: Session,
    *,
    run: BidAnalysisRun,
    tasks: list[BidTask],
    checks: list[dict[str, Any]],
    include_model_lineage: bool = True,
) -> None:
    """Validate the complete persisted Task/Context/Tool/Dispatch lineage."""

    task_by_id = {str(row.id): row for row in tasks}
    task_ids = tuple(sorted(task_by_id))
    attempts = (
        db.query(BidTaskAttempt)
        .filter(BidTaskAttempt.task_id.in_(task_ids))
        .order_by(
            BidTaskAttempt.task_id.asc(),
            BidTaskAttempt.attempt_no.asc(),
            BidTaskAttempt.id.asc(),
        )
        .all()
        if task_ids
        else []
    )
    attempt_by_id = {str(row.id): row for row in attempts}
    attempt_chains_valid = True
    for task_id, task in task_by_id.items():
        rows = [row for row in attempts if str(row.task_id) == task_id]
        attempt_numbers = [int(row.attempt_no) for row in rows]
        fencing_tokens = [int(row.fencing_token) for row in rows]
        if (
            attempt_numbers != list(range(1, len(rows) + 1))
            or fencing_tokens != sorted(fencing_tokens)
            or len(fencing_tokens) != len(set(fencing_tokens))
            or (
                task.current_attempt_id is not None
                and str(task.current_attempt_id) not in attempt_by_id
            )
        ):
            attempt_chains_valid = False
            break
    _check(
        checks,
        "TASK_ATTEMPT_CHAINS_MONOTONIC",
        attempt_chains_valid,
        detail=len(attempts),
    )

    contexts = (
        db.query(BidContextManifest)
        .filter(BidContextManifest.run_id == run.id)
        .order_by(BidContextManifest.id.asc())
        .all()
    )
    context_by_id = {str(row.id): row for row in contexts}
    context_lineage_valid = True
    for context in contexts:
        attempt = attempt_by_id.get(str(context.task_attempt_id))
        manifest_payload = dict(context.manifest_json or {})
        payload_id = manifest_payload.pop("context_manifest_id", None)
        payload_hash = manifest_payload.pop("hash", None)
        if (
            attempt is None
            or str(context.assessment_id) != str(run.assessment_id)
            or str(context.task_id) not in task_by_id
            or str(attempt.task_id) != str(context.task_id)
            or int(context.fencing_token) != int(attempt.fencing_token)
            or str(payload_id or "") != str(context.id)
            or str(payload_hash or "") != str(context.manifest_hash)
            or canonical_hash(manifest_payload) != str(context.manifest_hash)
        ):
            context_lineage_valid = False
            break
    _check(
        checks,
        "CONTEXT_MANIFEST_LINEAGE_VALID",
        context_lineage_valid,
        detail=len(contexts),
    )

    invocations = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.run_id == run.id)
        .order_by(BidToolInvocation.id.asc())
        .all()
    )
    invocation_by_id = {str(row.id): row for row in invocations}
    invocation_lineage_valid = True
    for invocation in invocations:
        attempt = attempt_by_id.get(str(invocation.task_attempt_id))
        context = context_by_id.get(str(invocation.context_manifest_id))
        expected_request_hash = canonical_hash(
            {
                "attempt_id": str(invocation.task_attempt_id),
                "fencing_token": int(invocation.fencing_token),
                "context_manifest_id": str(invocation.context_manifest_id),
                "tool_name": str(invocation.tool_name),
                "arguments": dict(invocation.arguments_json or {}),
            }
        )
        if (
            attempt is None
            or context is None
            or str(invocation.assessment_id) != str(run.assessment_id)
            or str(invocation.task_id) not in task_by_id
            or str(attempt.task_id) != str(invocation.task_id)
            or str(context.task_id) != str(invocation.task_id)
            or str(context.task_attempt_id) != str(invocation.task_attempt_id)
            or int(invocation.fencing_token) != int(attempt.fencing_token)
            or int(invocation.fencing_token) != int(context.fencing_token)
            or canonical_hash(invocation.arguments_json or {})
            != str(invocation.arguments_hash)
            or expected_request_hash != str(invocation.request_hash)
            or str(invocation.tool_registry_version_id)
            != str(run.tool_registry_version_id)
        ):
            invocation_lineage_valid = False
            break
    _check(
        checks,
        "TOOL_INVOCATION_LINEAGE_VALID",
        invocation_lineage_valid,
        detail=len(invocations),
    )

    operations = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.task_id.in_(task_ids))
        .order_by(BidAsyncOperation.id.asc())
        .all()
        if task_ids
        else []
    )
    operation_by_id = {str(row.id): row for row in operations}
    async_lineage_valid = True
    for operation in operations:
        attempt = attempt_by_id.get(str(operation.task_attempt_id))
        linked_invocations = [
            row
            for row in invocations
            if str(row.async_operation_id or "") == str(operation.id)
        ]
        if (
            attempt is None
            or str(operation.task_id) not in task_by_id
            or str(attempt.task_id) != str(operation.task_id)
            or (
                str(operation.operation_type).startswith("tool:")
                and (
                    len(linked_invocations) != 1
                    or str(linked_invocations[0].task_id) != str(operation.task_id)
                    or str(linked_invocations[0].task_attempt_id)
                    != str(operation.task_attempt_id)
                    or str(linked_invocations[0].request_hash)
                    != str(operation.input_hash)
                )
            )
        ):
            async_lineage_valid = False
            break
    _check(
        checks,
        "ASYNC_OPERATION_LINEAGE_VALID",
        async_lineage_valid,
        detail=len(operations),
    )

    dispatches = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.task_id.in_(task_ids))
        .order_by(BidToolDispatch.id.asc())
        .all()
        if task_ids
        else []
    )
    dispatch_by_id = {str(row.id): row for row in dispatches}
    dispatch_lineage_valid = True
    for dispatch in dispatches:
        invocation = invocation_by_id.get(str(dispatch.invocation_id))
        operation = operation_by_id.get(str(dispatch.async_operation_id))
        envelope = dict(dispatch.envelope_json or {})
        if (
            invocation is None
            or operation is None
            or str(dispatch.task_id) != str(invocation.task_id)
            or str(dispatch.task_attempt_id) != str(invocation.task_attempt_id)
            or str(dispatch.task_id) != str(operation.task_id)
            or str(dispatch.task_attempt_id) != str(operation.task_attempt_id)
            or canonical_hash(envelope) != str(dispatch.envelope_hash)
            or str(envelope.get("schema_version") or "")
            != "bid-tool-dispatch-envelope-v1"
            or str(envelope.get("invocation_id") or "") != str(invocation.id)
            or str(envelope.get("operation_id") or "") != str(operation.id)
            or str(envelope.get("assessment_id") or "") != str(run.assessment_id)
            or str(envelope.get("run_id") or "") != str(run.id)
            or str(envelope.get("task_id") or "") != str(dispatch.task_id)
            or str(envelope.get("task_attempt_id") or "")
            != str(dispatch.task_attempt_id)
            or str(envelope.get("context_manifest_id") or "")
            != str(invocation.context_manifest_id)
            or str(envelope.get("manifest_id") or "") != str(run.manifest_id)
            or str(envelope.get("tool_registry_version_id") or "")
            != str(run.tool_registry_version_id)
            or str(envelope.get("tool_name") or "") != str(invocation.tool_name)
            or canonical_hash(envelope.get("arguments") or {})
            != str(invocation.arguments_hash)
            or str(envelope.get("request_hash") or "") != str(invocation.request_hash)
            or str(envelope.get("provider_request_id") or "")
            != str(dispatch.provider_request_id)
            or str(dispatch.scope_token_hash) != str(invocation.scope_token_hash)
        ):
            dispatch_lineage_valid = False
            break
    _check(
        checks,
        "TOOL_DISPATCH_LINEAGE_VALID",
        dispatch_lineage_valid,
        detail=len(dispatches),
    )

    dispatch_attempts = (
        db.query(BidToolDispatchAttempt)
        .filter(BidToolDispatchAttempt.dispatch_id.in_(tuple(dispatch_by_id)))
        .order_by(
            BidToolDispatchAttempt.dispatch_id.asc(),
            BidToolDispatchAttempt.attempt_no.asc(),
            BidToolDispatchAttempt.id.asc(),
        )
        .all()
        if dispatch_by_id
        else []
    )
    dispatch_attempt_chains_valid = True
    for dispatch_id, dispatch in dispatch_by_id.items():
        rows = [row for row in dispatch_attempts if str(row.dispatch_id) == dispatch_id]
        attempt_numbers = [int(row.attempt_no) for row in rows]
        fencing_tokens = [int(row.fencing_token) for row in rows]
        if (
            attempt_numbers != list(range(1, int(dispatch.attempt_count) + 1))
            or fencing_tokens != sorted(fencing_tokens)
            or len(fencing_tokens) != len(set(fencing_tokens))
            or (rows and int(rows[-1].fencing_token) != int(dispatch.fencing_token))
            or (not rows and int(dispatch.fencing_token) != 0)
        ):
            dispatch_attempt_chains_valid = False
            break
    _check(
        checks,
        "TOOL_DISPATCH_ATTEMPT_CHAINS_MONOTONIC",
        dispatch_attempt_chains_valid,
        detail=len(dispatch_attempts),
    )

    results = (
        db.query(BidToolResult)
        .filter(BidToolResult.invocation_id.in_(tuple(invocation_by_id)))
        .order_by(BidToolResult.id.asc())
        .all()
        if invocation_by_id
        else []
    )
    result_by_id = {str(row.id): row for row in results}
    result_lineage_valid = True
    for result in results:
        invocation = invocation_by_id.get(str(result.invocation_id))
        operation = (
            operation_by_id.get(str(result.async_operation_id))
            if result.async_operation_id
            else None
        )
        expected_hash = canonical_hash(
            {
                "status": str(result.status),
                "summary": str(result.summary),
                "data_hash": str(result.data_hash),
                "evidence_refs": list(result.evidence_refs_json or []),
                "warnings": list(result.warnings_json or []),
                "metrics": dict(result.metrics_json or {}),
                "truncated": bool(result.truncated),
                "external_object_ref": (
                    str(result.object_ref) if result.object_ref else None
                ),
            }
        )
        if (
            invocation is None
            or str(result.task_attempt_id) != str(invocation.task_attempt_id)
            or (
                result.async_operation_id is not None
                and (
                    operation is None
                    or str(operation.task_attempt_id) != str(result.task_attempt_id)
                    or str(operation.result_ref or "") != f"tool-result:{result.id}"
                    or str(invocation.async_operation_id or "") != str(operation.id)
                )
            )
            or (
                str(result.storage_kind) == "inline"
                and (
                    result.inline_data_json is None
                    or result.object_ref is not None
                    or hashlib.sha256(
                        canonical_json(result.inline_data_json).encode("utf-8")
                    ).hexdigest()
                    != str(result.data_hash)
                )
            )
            or (
                str(result.storage_kind) == "external"
                and not str(result.object_ref or "")
            )
            or expected_hash != str(result.result_hash)
        ):
            result_lineage_valid = False
            break
    for dispatch in dispatches:
        if str(dispatch.status) != "succeeded":
            continue
        result = next(
            (
                row
                for row in results
                if str(row.invocation_id) == str(dispatch.invocation_id)
            ),
            None,
        )
        settled_attempt = next(
            (
                row
                for row in reversed(dispatch_attempts)
                if str(row.dispatch_id) == str(dispatch.id)
            ),
            None,
        )
        if (
            result is None
            or settled_attempt is None
            or str(settled_attempt.status) != "succeeded"
            or str(settled_attempt.outcome_hash or "") != str(result.result_hash)
        ):
            result_lineage_valid = False
            break
    _check(
        checks,
        "TOOL_RESULTS_IMMUTABLE_AND_SCOPED",
        result_lineage_valid,
        detail=len(results),
    )

    checkpoints = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.task_attempt_id.in_(tuple(attempt_by_id)))
        .order_by(BidCheckpoint.id.asc())
        .all()
        if attempt_by_id
        else []
    )
    checkpoint_tool_refs_valid = True
    for checkpoint in checkpoints:
        for reference in list(checkpoint.tool_refs_json or []):
            if not isinstance(reference, dict):
                checkpoint_tool_refs_valid = False
                break
            invocation_id = str(reference.get("invocation_id") or "")
            result_id = str(
                reference.get("result_id")
                or reference.get("result_ref_id")
                or ""
            )
            if invocation_id and not result_id:
                invocation = invocation_by_id.get(invocation_id)
                if (
                    invocation is None
                    or str(invocation.task_attempt_id)
                    != str(checkpoint.task_attempt_id)
                    or (
                        reference.get("tool_call_id") is not None
                        and str(reference.get("tool_call_id"))
                        != str(invocation.tool_call_id)
                    )
                    or (
                        reference.get("tool_name") is not None
                        and str(reference.get("tool_name"))
                        != str(invocation.tool_name)
                    )
                ):
                    checkpoint_tool_refs_valid = False
                    break
                continue
            result = result_by_id.get(result_id)
            expected_result_hash = reference.get("result_hash")
            if (
                result is None
                or (
                    expected_result_hash is not None
                    and str(expected_result_hash) != str(result.result_hash)
                )
            ):
                checkpoint_tool_refs_valid = False
                break
        if not checkpoint_tool_refs_valid:
            break
    _check(
        checks,
        "CHECKPOINT_TOOL_REFS_VALID",
        checkpoint_tool_refs_valid,
        detail=sum(len(list(row.tool_refs_json or [])) for row in checkpoints),
    )

    # Model authority was introduced by the Phase 4 validator.  Preserve the
    # exact Phase 3 v2 check set and result hash for historical Plan envelopes.
    if not include_model_lineage:
        return

    checkpoint_lineage_valid = True
    for attempt_id, attempt in attempt_by_id.items():
        rows = sorted(
            (
                row
                for row in checkpoints
                if str(row.task_attempt_id) == attempt_id
            ),
            key=lambda row: (int(row.action_seq), str(row.id)),
        )
        if [int(row.action_seq) for row in rows] != list(range(len(rows))):
            checkpoint_lineage_valid = False
            break
        for checkpoint in rows:
            context = (
                context_by_id.get(str(checkpoint.context_manifest_id))
                if checkpoint.context_manifest_id
                else None
            )
            state = dict(checkpoint.state_json or {})
            state_fence = _safe_int(state.get("fencing_token"))
            if (
                int(checkpoint.fencing_token) != int(attempt.fencing_token)
                or canonical_hash(state) != str(checkpoint.state_hash)
                or (
                    checkpoint.context_manifest_id is not None
                    and (
                        context is None
                        or str(context.task_attempt_id) != attempt_id
                        or int(context.fencing_token) != int(attempt.fencing_token)
                    )
                )
            ):
                checkpoint_lineage_valid = False
                break
            if state.get("schema") == "bid.local_agent.state.v1":
                try:
                    normalized_state = normalize_local_agent_state(state)
                except (TypeError, ValueError):
                    checkpoint_lineage_valid = False
                    break
                if (
                    normalized_state != state
                    or str(state.get("run_id") or "") != str(run.id)
                    or str(state.get("task_id") or "") != str(attempt.task_id)
                    or str(state.get("task_attempt_id") or "") != attempt_id
                    or state_fence != int(attempt.fencing_token)
                ):
                    checkpoint_lineage_valid = False
                    break
        if not checkpoint_lineage_valid:
            break
    _check(
        checks,
        "PHASE4_CHECKPOINT_LINEAGE_VALID",
        checkpoint_lineage_valid,
        detail=len(checkpoints),
    )

    model_calls = (
        db.query(BidModelCall)
        .filter(BidModelCall.run_id == run.id)
        .order_by(BidModelCall.task_id.asc(), BidModelCall.action_seq.asc())
        .all()
    )
    model_call_by_id = {str(row.id): row for row in model_calls}
    model_profiles = {
        str(row.id): row
        for row in db.query(BidModelProfileVersion)
        .filter(
            BidModelProfileVersion.id.in_(
                tuple({str(row.model_profile_version_id) for row in model_calls})
            )
            if model_calls
            else False
        )
        .all()
    }
    prompt_bundles = {
        str(row.id): row
        for row in db.query(BidPromptBundle)
        .filter(
            BidPromptBundle.id.in_(
                tuple({str(row.prompt_bundle_id) for row in model_calls})
            )
            if model_calls
            else False
        )
        .all()
    }
    model_call_lineage_valid = True
    per_task_sequences: dict[str, list[int]] = {}
    task_contracts: dict[str, dict[str, Any]] = {}
    for call in model_calls:
        task = task_by_id.get(str(call.task_id))
        attempt = attempt_by_id.get(str(call.task_attempt_id))
        context = context_by_id.get(str(call.context_manifest_id))
        operation = operation_by_id.get(str(call.async_operation_id))
        profile = model_profiles.get(str(call.model_profile_version_id))
        prompt = prompt_bundles.get(str(call.prompt_bundle_id))
        checkpoint = next(
            (row for row in checkpoints if str(row.id) == str(call.checkpoint_id)),
            None,
        )
        checkpoint_state = dict(checkpoint.state_json or {}) if checkpoint is not None else {}
        envelope = dict(call.request_envelope_json or {})
        try:
            frozen_route = (
                resolve_model_route(profile, role=str(call.logical_role))
                if profile is not None
                else None
            )
        except (TypeError, ValueError, RuntimeError):
            frozen_route = None
        try:
            task_contract = build_task_contract(db, task) if task is not None else None
        except (TypeError, ValueError, RuntimeError):
            task_contract = None
        if task_contract is not None:
            task_contracts[str(call.task_id)] = task_contract
        envelope_fencing_token = _safe_int(envelope.get("fencing_token"))
        envelope_action_seq = _safe_int(envelope.get("action_seq"))
        envelope_input_limit = _safe_int(envelope.get("input_token_limit"))
        envelope_output_limit = _safe_int(envelope.get("output_token_limit"))
        envelope_cost_limit = _safe_int(envelope.get("cost_microunits_limit"))
        envelope_timeout_seconds = _safe_int(envelope.get("timeout_seconds"))
        time_bounds_valid = False
        if envelope_timeout_seconds is not None and operation is not None:
            try:
                expected_timeout = as_utc(call.accepted_at) + timedelta(
                    seconds=envelope_timeout_seconds
                )
                time_bounds_valid = (
                    as_utc(call.timeout_at) == expected_timeout
                    and operation.timeout_at is not None
                    and as_utc(operation.timeout_at) == expected_timeout
                )
            except (TypeError, ValueError):
                time_bounds_valid = False
        if (
            task is None
            or attempt is None
            or context is None
            or operation is None
            or profile is None
            or prompt is None
            or frozen_route is None
            or task_contract is None
            or checkpoint is None
            or str(profile.status) != "active"
            or str(profile.active_slot_key or "") != "active"
            or str(prompt.status) != "active"
            or str(prompt.active_slot_key or "") != "active"
            or str(call.assessment_id) != str(run.assessment_id)
            or str(attempt.task_id) != str(call.task_id)
            or str(context.task_id) != str(call.task_id)
            or str(context.task_attempt_id) != str(call.task_attempt_id)
            or str(operation.task_id) != str(call.task_id)
            or str(operation.task_attempt_id) != str(call.task_attempt_id)
            or str(checkpoint.task_attempt_id) != str(call.task_attempt_id)
            or str(checkpoint.context_manifest_id or "") != str(call.context_manifest_id)
            or str(checkpoint_state.get("phase") or "") != "await_model"
            or _safe_int(checkpoint_state.get("action_seq")) != int(call.action_seq)
            or str(checkpoint_state.get("outstanding_operation_ref") or "")
            != f"model-call:{call.id}"
            or int(call.fencing_token) != int(attempt.fencing_token)
            or int(call.fencing_token) != int(context.fencing_token)
            or int(call.fencing_token) != int(checkpoint.fencing_token)
            or str(call.model_profile_version_id) != str(run.model_profile_version_id)
            or str(call.prompt_bundle_id) != str(run.prompt_bundle_id)
            or str(call.action_schema) != "bid.task.action.v1"
            or canonical_hash(envelope) != str(call.request_hash)
            or str(envelope.get("model_call_id") or "") != str(call.id)
            or str(envelope.get("assessment_id") or "") != str(run.assessment_id)
            or str(envelope.get("run_id") or "") != str(run.id)
            or str(envelope.get("task_id") or "") != str(call.task_id)
            or str(envelope.get("task_attempt_id") or "") != str(call.task_attempt_id)
            or envelope_fencing_token != int(call.fencing_token)
            or envelope_action_seq != int(call.action_seq)
            or str(envelope.get("context_manifest_id") or "") != str(context.id)
            or str(envelope.get("context_manifest_hash") or "")
            != str(context.manifest_hash)
            or str(envelope.get("checkpoint_id") or "") != str(checkpoint.id)
            or str(envelope.get("checkpoint_state_hash") or "")
            != str(checkpoint.state_hash)
            or str(envelope.get("task_contract_hash") or "")
            != canonical_hash(task_contract)
            or str(envelope.get("model_profile_hash") or "")
            != str(profile.artifact_hash)
            or str(envelope.get("model_route_hash") or "")
            != str(frozen_route["route_hash"])
            or str(envelope.get("provider_ref") or "")
            != str(frozen_route["provider_ref"])
            or str(envelope.get("model_ref") or "")
            != str(frozen_route["model_ref"])
            or str(envelope.get("prompt_role") or "")
            != str(frozen_route["prompt_role"])
            or str(call.provider_ref) != str(frozen_route["provider_ref"])
            or str(call.model_ref) != str(frozen_route["model_ref"])
            or str(call.prompt_role) != str(frozen_route["prompt_role"])
            or str(envelope.get("action_schema") or "") != "bid.task.action.v1"
            or str(call.replay_policy) != str(frozen_route["replay_policy"])
            or int(call.max_attempts) != int(frozen_route["max_attempts"])
            or str(envelope.get("prompt_bundle_hash") or "")
            != str(prompt.artifact_hash)
            or envelope_input_limit != int(call.reserved_input_tokens)
            or envelope_input_limit != int(task_contract["budget"]["max_input_tokens"])
            or envelope_output_limit != int(call.reserved_output_tokens)
            or envelope_output_limit != int(task_contract["budget"]["max_output_tokens"])
            or envelope_cost_limit != int(call.reserved_cost_microunits)
            or envelope_cost_limit != int(frozen_route["reserved_cost_microunits"])
            or int(call.actual_input_tokens) < 0
            or int(call.actual_output_tokens) < 0
            or int(call.actual_cost_microunits) < 0
            or int(call.actual_input_tokens) > int(call.reserved_input_tokens)
            or int(call.actual_output_tokens) > int(call.reserved_output_tokens)
            or int(call.actual_cost_microunits) > int(call.reserved_cost_microunits)
            or envelope_timeout_seconds is None
            or not 30 <= envelope_timeout_seconds <= 900
            or envelope_timeout_seconds != int(frozen_route["timeout_seconds"])
            or not time_bounds_valid
            or str(operation.input_hash) != str(call.request_hash)
        ):
            model_call_lineage_valid = False
            break
        input_payload = {
            "context_manifest_id": str(context.id),
            "context_manifest_hash": str(context.manifest_hash),
            "checkpoint_id": str(checkpoint.id),
            "checkpoint_state_hash": str(checkpoint.state_hash),
            "task_contract_hash": str(envelope.get("task_contract_hash") or ""),
            "model_profile_hash": str(envelope.get("model_profile_hash") or ""),
            "model_route_hash": str(envelope.get("model_route_hash") or ""),
            "prompt_bundle_hash": str(envelope.get("prompt_bundle_hash") or ""),
        }
        if canonical_hash(input_payload) != str(call.input_hash):
            model_call_lineage_valid = False
            break
        per_task_sequences.setdefault(str(call.task_id), []).append(int(call.action_seq))
    if model_call_lineage_valid:
        model_call_lineage_valid = all(
            seqs == list(range(1, len(seqs) + 1))
            and len(seqs)
            <= int(task_contracts[task_id]["budget"]["max_iterations"])
            for task_id, seqs in per_task_sequences.items()
        )
    _check(
        checks,
        "MODEL_CALL_LINEAGE_VALID",
        model_call_lineage_valid,
        detail=len(model_calls),
    )

    model_attempts = (
        db.query(BidModelCallAttempt)
        .filter(BidModelCallAttempt.model_call_id.in_(tuple(model_call_by_id)))
        .order_by(
            BidModelCallAttempt.model_call_id.asc(),
            BidModelCallAttempt.attempt_no.asc(),
            BidModelCallAttempt.id.asc(),
        )
        .all()
        if model_call_by_id
        else []
    )
    model_attempt_by_id = {str(row.id): row for row in model_attempts}
    model_attempt_accounting: dict[str, dict[str, Any]] = {}
    model_accounting_call_ids: set[str] = set()
    model_attempt_chains_valid = True
    for call_id, call in model_call_by_id.items():
        rows = [row for row in model_attempts if str(row.model_call_id) == call_id]
        numbers = [int(row.attempt_no) for row in rows]
        fences = [int(row.fencing_token) for row in rows]
        expected_provider_ids = [
            f"bid-model:{call_id}:attempt:{number}" for number in numbers
        ]
        accounted_input = accounted_output = accounted_cost = 0
        accounting_valid = True
        for row in rows:
            detail = dict(row.detail_json or {})
            if detail.get("schema") != MODEL_PROVIDER_ACCOUNTING_SCHEMA:
                continue
            usage = dict(detail.get("usage") or {})
            input_tokens = _safe_int(usage.get("input_tokens"))
            output_tokens = _safe_int(usage.get("output_tokens"))
            cost_microunits = _safe_int(detail.get("actual_cost_microunits"))
            action_keys = detail.get("action_keys")
            if (
                detail.get("provider_response_received") is not True
                or set(usage) != {"input_tokens", "output_tokens"}
                or input_tokens is None
                or output_tokens is None
                or cost_microunits is None
                or min(input_tokens, output_tokens, cost_microunits) < 0
                or not isinstance(action_keys, list)
                or any(not isinstance(key, str) for key in action_keys)
                or action_keys != sorted(set(action_keys))
                or not str(detail.get("action_type") or "")
                or len(str(detail.get("action_hash") or "")) != 64
            ):
                accounting_valid = False
                break
            accounted_input += input_tokens
            accounted_output += output_tokens
            accounted_cost += cost_microunits
            model_attempt_accounting[str(row.id)] = detail
            model_accounting_call_ids.add(call_id)
        if model_accounting_call_ids and call_id in model_accounting_call_ids:
            accounting_valid = accounting_valid and (
                accounted_input == int(call.actual_input_tokens)
                and accounted_output == int(call.actual_output_tokens)
                and accounted_cost == int(call.actual_cost_microunits)
            )
        if (
            numbers != list(range(1, int(call.attempt_count) + 1))
            or fences != numbers
            or [str(row.provider_request_id) for row in rows] != expected_provider_ids
            or any(
                str(row.status) not in {"leased", "sending"} and not row.outcome_hash
                for row in rows
            )
            or (
                str(call.status)
                in {"succeeded", "failed", "cancelled", "uncertain", "dead_letter"}
                and any(str(row.status) in {"leased", "sending"} for row in rows)
            )
            or (
                str(call.status) == "succeeded"
                and (
                    not rows
                    or str(rows[-1].status) != "succeeded"
                    or sum(str(row.status) == "succeeded" for row in rows) != 1
                )
            )
            or (
                str(call.status) != "succeeded"
                and any(str(row.status) == "succeeded" for row in rows)
            )
            or not accounting_valid
        ):
            model_attempt_chains_valid = False
            break
    _check(
        checks,
        "MODEL_CALL_ATTEMPT_CHAINS_MONOTONIC",
        model_attempt_chains_valid,
        detail=len(model_attempts),
    )

    model_results = (
        db.query(BidModelResult)
        .filter(BidModelResult.model_call_id.in_(tuple(model_call_by_id)))
        .order_by(BidModelResult.model_call_id.asc())
        .all()
        if model_call_by_id
        else []
    )
    model_results_valid = True
    result_count_by_call = {
        call_id: sum(str(row.model_call_id) == call_id for row in model_results)
        for call_id in model_call_by_id
    }
    if any(
        (str(call.status) == "succeeded") != (result_count_by_call[call_id] == 1)
        for call_id, call in model_call_by_id.items()
    ):
        model_results_valid = False
    for result in model_results:
        call = model_call_by_id.get(str(result.model_call_id))
        model_attempt = model_attempt_by_id.get(str(result.model_call_attempt_id))
        operation = (
            operation_by_id.get(str(call.async_operation_id)) if call is not None else None
        )
        action = dict(result.action_json or {})
        task_contract = task_contracts.get(str(result.task_id))
        try:
            normalized_action = (
                normalize_task_action(
                    action,
                    allowed_tools=set(task_contract["allowed_tools"]),
                )
                if task_contract is not None
                else None
            )
        except (TypeError, ValueError):
            normalized_action = None
        response_payload = {
            "action_hash": str(result.action_hash),
            "usage": dict(result.usage_json or {}),
            "finish_reason": str(result.finish_reason),
            "provider_receipt_id": (
                str(result.provider_receipt_id) if result.provider_receipt_id else None
            ),
            "actual_cost_microunits": int(result.actual_cost_microunits),
        }
        expected_result_hash = canonical_hash(
            {
                "model_call_id": str(result.model_call_id),
                "model_call_attempt_id": str(result.model_call_attempt_id),
                "action_hash": str(result.action_hash),
                "response_hash": str(result.response_hash),
            }
        )
        usage = dict(result.usage_json or {})
        attempt_accounting = model_attempt_accounting.get(
            str(result.model_call_attempt_id)
        )
        legacy_accounting = str(result.model_call_id) not in model_accounting_call_ids
        usage_valid = set(usage) == {"input_tokens", "output_tokens"}
        if usage_valid:
            try:
                usage_valid = (
                    int(usage["input_tokens"]) == int(result.input_tokens)
                    and int(usage["output_tokens"]) == int(result.output_tokens)
                    and int(result.input_tokens) >= 0
                    and int(result.output_tokens) >= 0
                    and int(result.input_tokens) <= int(call.reserved_input_tokens)
                    and int(result.output_tokens) <= int(call.reserved_output_tokens)
                ) if call is not None else False
            except (TypeError, ValueError):
                usage_valid = False
        if (
            call is None
            or model_attempt is None
            or operation is None
            or str(model_attempt.model_call_id) != str(call.id)
            or str(result.task_id) != str(call.task_id)
            or str(result.source_task_attempt_id) != str(call.task_attempt_id)
            or str(result.storage_kind) != "inline"
            or int(result.actual_cost_microunits) < 0
            or int(result.actual_cost_microunits) > int(call.reserved_cost_microunits)
            or (
                legacy_accounting
                and int(call.actual_cost_microunits)
                != int(result.actual_cost_microunits)
            )
            or (
                legacy_accounting
                and int(call.actual_input_tokens) != int(result.input_tokens)
            )
            or (
                legacy_accounting
                and int(call.actual_output_tokens) != int(result.output_tokens)
            )
            or (
                not legacy_accounting
                and (
                    attempt_accounting is None
                    or dict(attempt_accounting.get("usage") or {}) != usage
                    or int(attempt_accounting.get("actual_cost_microunits") or 0)
                    != int(result.actual_cost_microunits)
                    or str(attempt_accounting.get("action_hash") or "")
                    != str(result.action_hash)
                )
            )
            or not usage_valid
            or normalized_action != action
            or canonical_hash(action) != str(result.action_hash)
            or canonical_hash(response_payload) != str(result.response_hash)
            or expected_result_hash != str(result.result_hash)
            or str(result.action_type) != str(action.get("action_type") or "")
            or str(call.status) != "succeeded"
            or str(model_attempt.status) != "succeeded"
            or str(model_attempt.outcome_hash or "") != str(result.result_hash)
            or str(model_attempt.provider_receipt_id or "")
            != str(result.provider_receipt_id or "")
            or str(operation.status) != "succeeded"
            or str(operation.result_ref or "") != f"model-result:{result.id}"
        ):
            model_results_valid = False
            break
    _check(
        checks,
        "MODEL_RESULTS_IMMUTABLE_AND_SCOPED",
        model_results_valid,
        detail=len(model_results),
    )


def _deterministic_validation(
    db: Session,
    *,
    validation: BidRunValidation,
    run: BidAnalysisRun,
    assessment: BidAssessment,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    scope = _current_scope(db, str(assessment.id))
    _check(checks, "ASSESSMENT_ACTIVE", str(assessment.lifecycle_status) == "active")
    _check(checks, "RUN_ACTIVE_POINTER", str(assessment.active_run_id or "") == str(run.id))
    _check(checks, "MANIFEST_CURRENT", str(assessment.current_manifest_id or "") == str(run.manifest_id))
    _check(checks, "SCOPE_CURRENT", scope is not None and str(scope.id) == str(run.scope_id))
    _check(checks, "RUN_NOT_CANCEL_REQUESTED", run.cancel_requested_at is None)

    manifest = (
        db.query(BidDocumentManifest)
        .filter(
            BidDocumentManifest.id == run.manifest_id,
            BidDocumentManifest.assessment_id == run.assessment_id,
        )
        .one_or_none()
    )
    _check(checks, "FROZEN_MANIFEST_PRESENT", manifest is not None)
    comparison_binding_required = bool(
        getattr(settings, "feature_bid_assessment_phase4_fact_verification", False)
        or run.hard_gate_comparison_baseline_id
        or run.hard_gate_comparison_baseline_hash
    )
    if comparison_binding_required:
        comparison_binding_valid = False
        try:
            from app.models.bid_assessment_release import BidHardGateComparisonBaseline
            from app.services.bid_hard_gate_fact_verification import (
                validate_hard_gate_comparison_baseline_at,
            )

            comparison_baseline = db.query(BidHardGateComparisonBaseline).filter(
                BidHardGateComparisonBaseline.id
                == run.hard_gate_comparison_baseline_id,
                BidHardGateComparisonBaseline.status == "frozen",
            ).one_or_none()
            comparison_binding_valid = bool(
                comparison_baseline is not None
                and str(comparison_baseline.baseline_hash)
                == str(run.hard_gate_comparison_baseline_hash or "")
            )
            if comparison_binding_valid:
                validate_hard_gate_comparison_baseline_at(
                    db,
                    baseline=comparison_baseline,
                    effective_at=as_utc(run.evaluation_time),
                )
        except Exception:
            comparison_binding_valid = False
        _check(
            checks,
            "HARD_GATE_COMPARISON_BASELINE_CURRENT",
            comparison_binding_valid,
        )
    plan = _committed_plan(db, str(run.id))
    _check(checks, "COMMITTED_PLAN_UNIQUE", plan is not None)
    if plan is not None:
        _check(
            checks,
            "VALIDATION_PLAN_BOUND",
            str(validation.plan_revision_id or "") == str(plan.id),
        )
        _check(checks, "PLAN_VALIDATED_HASH_PRESENT", bool(plan.validated_hash))

    tasks = db.query(BidTask).filter(BidTask.run_id == run.id).all()
    task_ids = {str(task.id) for task in tasks}
    task_keys = [str(task.task_key) for task in tasks]
    _check(checks, "TASK_SET_NONEMPTY", bool(tasks))
    _check(checks, "TASK_KEYS_UNIQUE", len(task_keys) == len(set(task_keys)))
    _check(
        checks,
        "TASKS_TERMINAL_SUCCESS",
        bool(tasks) and all(str(task.status) in SUCCESS_TASK_STATES for task in tasks),
        detail={
            "total": len(tasks),
            "states": {
                state: sum(1 for task in tasks if str(task.status) == state)
                for state in sorted({str(task.status) for task in tasks})
            },
        },
    )
    registry = load_standard_task_registry()
    if plan is not None:
        current_envelope_for_registry = dict(plan.proposal_json or {})
        if str(current_envelope_for_registry.get("schema") or "") == "bid.plan.commit.envelope.v2":
            try:
                from app.services.bid_task_registry import load_frozen_task_registry

                registry = load_frozen_task_registry(
                    catalog_ref=str(current_envelope_for_registry.get("task_catalog_ref") or ""),
                    registry_version=str(current_envelope_for_registry.get("task_registry_version") or ""),
                    registry_hash=str(current_envelope_for_registry.get("task_registry_hash") or ""),
                )
            except RuntimeError:
                _check(checks, "TASK_CATALOG_FROZEN", False)
            else:
                _check(checks, "TASK_CATALOG_FROZEN", True)
    _check(
        checks,
        "TASK_TYPES_REGISTERED",
        all(str(task.task_type) in registry.policies for task in tasks),
    )
    if plan is not None:
        plan_revisions = _all_plan_revisions(db, str(run.id))
        proposal_tasks = [
            item
            for revision in plan_revisions
            for item in (
                (dict(revision.proposal_json or {}).get("proposal") or {}).get("add_tasks")
                or []
            )
        ]
        proposal_keys = [
            str(item.get("task_key") or "")
            for item in proposal_tasks
            if isinstance(item, dict)
        ]
        _check(checks, "PLAN_TASK_COVERAGE", sorted(proposal_keys) == sorted(task_keys))
        current_envelope = dict(plan.proposal_json or {})
        if str(current_envelope.get("schema") or "") == "bid.plan.commit.envelope.v2":
            stage_codes = [
                str((dict(row.proposal_json or {})).get("stage") or "")
                for row in plan_revisions
            ]
            _check(checks, "PLAN_STAGE_SEQUENCE_COMPLETE", stage_codes == ["P0", "P1", "P2", "P3", "P4"])
            _check(
                checks,
                "PLAN_REVISION_SEQUENCE_CONTIGUOUS",
                [int(row.revision_no) for row in plan_revisions] == [1, 2, 3, 4, 5],
            )
            _check(
                checks,
                "PLAN_REVISION_STATUS_SEQUENCE",
                [str(row.status) for row in plan_revisions]
                == ["superseded", "superseded", "superseded", "superseded", "committed"],
            )
            _check(
                checks,
                "PLAN_STAGE_FINAL_FLAGS_VALID",
                [bool((dict(row.proposal_json or {})).get("final_stage")) for row in plan_revisions]
                == [False, False, False, False, True],
            )
            _check(checks, "PLAN_FINAL_STAGE_BOUND", bool(current_envelope.get("final_stage")) and current_envelope.get("stage") == "P4")
            skill_bindings_valid = True
            try:
                from app.services.bid_skill_registry import verify_frozen_skill_binding

                for revision in plan_revisions:
                    envelope = dict(revision.proposal_json or {})
                    for definition in (envelope.get("proposal") or {}).get("add_tasks") or []:
                        verify_frozen_skill_binding(
                            catalog_ref=str(envelope.get("skill_catalog_ref") or ""),
                            catalog_version=str(envelope.get("skill_catalog_version") or ""),
                            catalog_hash=str(envelope.get("skill_catalog_hash") or ""),
                            task_type=str(definition.get("task_type") or ""),
                            binding=dict(definition.get("skill_binding") or {}),
                        )
            except (RuntimeError, TypeError, ValueError):
                skill_bindings_valid = False
            _check(checks, "PLAN_SKILL_BINDINGS_VALID", skill_bindings_valid)
            task_contracts_rebuildable = True
            try:
                from app.services.bid_task_runtime import build_task_contract

                for task in tasks:
                    build_task_contract(db, task)
            except (RuntimeError, TypeError, ValueError):
                task_contracts_rebuildable = False
            _check(checks, "TASK_CONTRACTS_REBUILDABLE", task_contracts_rebuildable)

    dependencies = db.query(BidTaskDependency).filter(BidTaskDependency.run_id == run.id).all()
    _check(
        checks,
        "DEPENDENCY_ENDPOINTS_PRESENT",
        all(
            str(row.task_id) in task_ids and str(row.depends_on_task_id) in task_ids
            for row in dependencies
        ),
    )
    task_status = {str(task.id): str(task.status) for task in tasks}
    _check(
        checks,
        "DEPENDENCIES_SATISFIED",
        all(task_status.get(str(row.depends_on_task_id)) in SUCCESS_TASK_STATES for row in dependencies),
    )

    successful_attempts = True
    final_checkpoints = True
    for task in tasks:
        if str(task.status) == "skipped":
            continue
        attempt = (
            db.query(BidTaskAttempt)
            .filter(
                BidTaskAttempt.id == task.current_attempt_id,
                BidTaskAttempt.task_id == task.id,
            )
            .one_or_none()
            if task.current_attempt_id
            else None
        )
        if attempt is None or str(attempt.status) != "succeeded":
            successful_attempts = False
            final_checkpoints = False
            continue
        checkpoint = (
            db.query(BidCheckpoint)
            .filter(BidCheckpoint.task_attempt_id == attempt.id)
            .order_by(BidCheckpoint.action_seq.desc(), BidCheckpoint.id.desc())
            .first()
        )
        if (
            checkpoint is None
            or int(checkpoint.fencing_token) != int(attempt.fencing_token)
            or str(checkpoint.next_state or "") not in {"succeeded", "validating"}
        ):
            final_checkpoints = False
    _check(checks, "CURRENT_ATTEMPTS_SUCCEEDED", successful_attempts)
    _check(checks, "FINAL_CHECKPOINTS_VALID", final_checkpoints)
    validator_version = _run_validator_version(plan)
    include_model_lineage = validator_version in MODEL_LINEAGE_VALIDATORS
    _runtime_lineage_checks(
        db,
        run=run,
        tasks=tasks,
        checks=checks,
        include_model_lineage=include_model_lineage,
    )
    result_authority = None
    if validator_version == MVP1_RUN_VALIDATOR_VERSION:
        result_authority = _mvp1_result_authority_snapshot(db, str(run.id))
        _check(
            checks,
            "MVP1_RESULT_AUTHORITY_PRESENT",
            result_authority is not None,
        )
    if result_authority is not None:
        from app.models.bid_assessment_results import BidPreliminaryReport

        report = dict(result_authority["report"])
        decision = dict(result_authority.get("decision") or {})
        report_validation = dict(result_authority.get("validation") or {})
        gate_rows = list(result_authority.get("gates") or [])
        claim_rows = list(result_authority.get("claims") or [])
        citation_rows = list(result_authority.get("citations") or [])
        published_event = dict(result_authority.get("published_event") or {})
        report_json = dict(
            db.query(BidPreliminaryReport.report_json)
            .filter(BidPreliminaryReport.id == report["report_id"])
            .scalar()
            or {}
        )
        report_claim_ids = {
            str(item.get("claim_id") or "")
            for item in (report_json.get("claims") or [])
        }
        report_citations = {
            (
                str(item.get("claim_id") or ""),
                str(citation.get("evidence_id") or ""),
            )
            for item in (report_json.get("claims") or [])
            for citation in (item.get("citations") or [])
        }
        stored_citations = {
            (
                str(item.get("claim_id") or ""),
                str(item.get("evidence_fragment_id") or ""),
            )
            for item in citation_rows
        }
        _check(
            checks,
            "MVP1_REPORT_IMMUTABLE_AND_SCOPED",
            report.get("assessment_id") == str(run.assessment_id)
            and report.get("status") == "ready"
            and report.get("report_hash") == report.get("body_hash")
            and report_json.get("run_id") == str(run.id)
            and report_json.get("assessment_id") == str(run.assessment_id),
        )
        _check(
            checks,
            "MVP1_DECISION_BOUND",
            bool(decision)
            and decision.get("run_id") == str(run.id)
            and decision.get("decision_id") == report.get("decision_id")
            and (report_json.get("decision") or {}).get("code")
            == decision.get("decision"),
        )
        _check(
            checks,
            "MVP1_REPORT_VALIDATION_PASSED",
            bool(report_validation)
            and report_validation.get("run_id") == str(run.id)
            and report_validation.get("validation_id") == report.get("validation_id")
            and report_validation.get("status") == "passed"
            and report_validation.get("result_hash")
            == report_validation.get("checks_hash"),
        )
        _check(
            checks,
            "MVP1_HARD_GATE_SET_COMPLETE",
            [item.get("gate_code") for item in gate_rows]
            == ["HG01", "HG02", "HG03", "HG04", "HG05", "HG06", "HG07"],
            detail=len(gate_rows),
        )
        _check(
            checks,
            "MVP1_CLAIMS_VALID_AND_RENDERED",
            bool(claim_rows)
            and all(item.get("status") == "valid" for item in claim_rows)
            and {str(item.get("claim_id") or "") for item in claim_rows}
            == report_claim_ids,
            detail=len(claim_rows),
        )
        _check(
            checks,
            "MVP1_CITATIONS_RENDERED_FROM_AUTHORITY",
            report_citations == stored_citations,
            detail=len(citation_rows),
        )
        _check(
            checks,
            "MVP1_REPORT_PUBLICATION_EVENT_PRESENT",
            bool(published_event)
            and published_event.get("report_hash") == report.get("report_hash"),
        )

    task_filter = BidAsyncOperation.task_id.in_(tuple(task_ids)) if task_ids else False
    active_operations = int(
        db.query(func.count(BidAsyncOperation.id))
        .filter(task_filter, BidAsyncOperation.status.in_(tuple(ACTIVE_OPERATION_STATES)))
        .scalar()
        or 0
    )
    active_invocations = int(
        db.query(func.count(BidToolInvocation.id))
        .filter(
            BidToolInvocation.run_id == run.id,
            BidToolInvocation.status.in_(tuple(ACTIVE_INVOCATION_STATES)),
        )
        .scalar()
        or 0
    )
    active_dispatches = int(
        db.query(func.count(BidToolDispatch.id))
        .filter(
            BidToolDispatch.task_id.in_(tuple(task_ids)) if task_ids else False,
            BidToolDispatch.status.in_(tuple(ACTIVE_DISPATCH_STATES)),
        )
        .scalar()
        or 0
    )
    _check(checks, "NO_ACTIVE_ASYNC_OPERATIONS", active_operations == 0, detail=active_operations)
    _check(checks, "NO_ACTIVE_TOOL_INVOCATIONS", active_invocations == 0, detail=active_invocations)
    _check(checks, "NO_ACTIVE_TOOL_DISPATCHES", active_dispatches == 0, detail=active_dispatches)
    if include_model_lineage:
        active_model_calls = int(
            db.query(func.count(BidModelCall.id))
            .filter(
                BidModelCall.run_id == run.id,
                BidModelCall.status.in_(tuple(ACTIVE_MODEL_CALL_STATES)),
            )
            .scalar()
            or 0
        )
        _check(
            checks,
            "NO_ACTIVE_MODEL_CALLS",
            active_model_calls == 0,
            detail=active_model_calls,
        )
    orphan_results = int(
        db.query(func.count(BidToolResult.id))
        .outerjoin(BidToolInvocation, BidToolInvocation.id == BidToolResult.invocation_id)
        .filter(
            BidToolResult.task_attempt_id.in_(
                db.query(BidTaskAttempt.id).filter(BidTaskAttempt.task_id.in_(tuple(task_ids)))
            ) if task_ids else False,
            BidToolInvocation.id.is_(None),
        )
        .scalar()
        or 0
    )
    _check(checks, "TOOL_RESULTS_HAVE_INVOCATIONS", orphan_results == 0, detail=orphan_results)

    stale_codes = {
        "ASSESSMENT_ACTIVE",
        "RUN_ACTIVE_POINTER",
        "MANIFEST_CURRENT",
        "SCOPE_CURRENT",
        "RUN_NOT_CANCEL_REQUESTED",
        "HARD_GATE_COMPARISON_BASELINE_CURRENT",
    }
    failed_codes = [item["code"] for item in checks if not item["passed"]]
    if any(code in stale_codes for code in failed_codes):
        outcome = "stale"
        failure_code = "BID_RUN_INPUT_STALE"
        retryable = False
    elif failed_codes:
        outcome = "failed"
        retryable = False
        failure_code = "BID_RUN_VALIDATION_INTEGRITY_FAILED"
    else:
        outcome = "passed"
        failure_code = None
        retryable = False
    result = {
        "schema": "bid.run.validation.result.v1",
        "validator_version": str(validation.validator_version),
        "validation_id": str(validation.id),
        "run_id": str(run.id),
        "outcome": outcome,
        "retryable": retryable,
        "failure_code": failure_code,
        "run_input_hash": str(run.input_hash),
        "validation_input_hash": str(validation.input_hash),
        "checks": checks,
        "summary": {
            "check_count": len(checks),
            "passed_count": sum(1 for item in checks if item["passed"]),
            "failed_count": len(failed_codes),
            "failed_codes": sorted(failed_codes),
        },
    }
    result["result_hash"] = canonical_hash(result)
    return result


def _assessment_success_status(run_kind: str) -> str:
    return "preliminary_ready" if str(run_kind) == "preliminary" else "deep_ready"


def _settle_validation(
    db: Session,
    claim: RunValidationClaim,
    *,
    result: dict[str, Any],
    now: datetime,
) -> RunValidationExecutionResult:
    validation, attempt, run, assessment = _locked_claim(db, claim, now=now)
    outcome = str(result["outcome"])
    if outcome not in {"passed", "failed", "stale"}:
        raise BidRunValidationError("BID_RUN_VALIDATION_OUTCOME_INVALID")
    result_hash = str(result["result_hash"])
    from_state = str(run.status)
    assessment_from = str(assessment.business_status)
    validation.status = outcome
    validation.outcome = outcome
    validation.retryable = bool(result["retryable"])
    validation.result_json = result
    validation.result_hash = result_hash
    validation.failure_code = result.get("failure_code")
    validation.lease_owner = None
    validation.lease_until = None
    validation.finished_at = now
    validation.row_version = int(validation.row_version) + 1
    attempt.status = outcome
    attempt.finished_at = now
    attempt.heartbeat_at = now
    attempt.result_hash = result_hash
    attempt.error_code = result.get("failure_code")

    is_current_active_run = str(assessment.active_run_id or "") == str(run.id)
    assessment_changed = False
    if outcome == "passed":
        run.status = "succeeded"
        run.retryable = False
        run.current_stage = "completed"
        run.waiting_reason = None
        if is_current_active_run:
            assessment.business_status = _assessment_success_status(str(run.run_kind))
            assessment_changed = True
        event_type = "bid.run.succeeded.v1"
    elif outcome == "stale":
        run.status = "stale"
        run.retryable = False
        run.current_stage = "stale"
        run.waiting_reason = str(result["failure_code"])
        if is_current_active_run:
            assessment.business_status = "stale_input"
            assessment_changed = True
        event_type = "bid.run.stale.v1"
    else:
        run.status = "failed"
        run.retryable = bool(result["retryable"])
        run.current_stage = "validation"
        run.waiting_reason = str(result["failure_code"])
        if is_current_active_run:
            assessment.business_status = "failed"
            assessment_changed = True
        event_type = "bid.run.failed.v1"
    run.finished_at = now
    run.row_version = int(run.row_version) + 1
    if assessment_changed:
        assessment.row_version = int(assessment.row_version) + 1
    db.flush()
    completed = int(
        db.query(func.count(BidTask.id))
        .filter(BidTask.run_id == run.id, BidTask.status.in_(tuple(SUCCESS_TASK_STATES)))
        .scalar()
        or 0
    )
    total = int(db.query(func.count(BidTask.id)).filter(BidTask.run_id == run.id).scalar() or 0)
    event = append_outbox_event(
        db,
        event_type=event_type,
        producer=RUN_VALIDATION_PRODUCER,
        aggregate_type="run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=f"run-validation:{validation.id}",
        causation_event_id=str(validation.source_event_id),
        payload_schema=f"{event_type}.payload",
        payload={
            "run_id": str(run.id),
            "from": from_state,
            "to": str(run.status),
            "retryable": bool(run.retryable),
            "validation_id": str(validation.id),
            "validator_version": str(validation.validator_version),
            "validation_result_hash": result_hash,
            "failure_code": result.get("failure_code"),
            "completed_units": completed,
            "total_units": total,
            "resource_version": int(run.row_version),
            "assessment_resource_version": int(assessment.row_version),
        },
        dedupe_key=f"run-validation-converged:{run.id}",
        occurred_at=now,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="run.validation.converge",
        entity_type="run_validation",
        entity_id=str(validation.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=f"run-validation:{validation.id}",
        correlation_id=str(event.event_id),
        before={"run_status": from_state, "business_status": assessment_from},
        after={
            "validation_status": outcome,
            "run_status": str(run.status),
            "business_status": str(assessment.business_status),
            "retryable": bool(run.retryable),
            "result_hash": result_hash,
        },
        metadata={
            "attempt_id": str(attempt.id),
            "attempt_no": int(attempt.attempt_no),
            "fencing_token": int(attempt.fencing_token),
            "failed_codes": result["summary"]["failed_codes"],
        },
        occurred_at=now,
    )
    db.flush()
    return RunValidationExecutionResult(
        validation_id=str(validation.id),
        run_id=str(run.id),
        outcome=outcome,
        run_status=str(run.status),
        retryable=bool(run.retryable),
        result_hash=result_hash,
    )


def execute_run_validation_claim(
    db: Session,
    claim: RunValidationClaim,
    *,
    now: datetime | None = None,
) -> RunValidationExecutionResult:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    validation, attempt, run, assessment = _locked_claim(db, claim, now=current_time)
    validation.status = "running"
    validation.row_version = int(validation.row_version) + 1
    attempt.status = "running"
    attempt.heartbeat_at = current_time
    db.flush()
    result = _deterministic_validation(
        db,
        validation=validation,
        run=run,
        assessment=assessment,
    )
    current_plan = _committed_plan(db, str(run.id))
    rebuilt_input_hash = (
        canonical_hash(_validation_input(db, run=run, plan=current_plan))
        if current_plan is not None
        else None
    )
    if rebuilt_input_hash != str(validation.input_hash):
        result["outcome"] = "failed"
        result["retryable"] = False
        result["failure_code"] = "BID_RUN_VALIDATION_INPUT_DRIFT"
        result["checks"].append(
            {
                "code": "VALIDATION_INPUT_IMMUTABLE",
                "passed": False,
                "severity": "fatal",
                "detail": {"expected": str(validation.input_hash), "actual": rebuilt_input_hash},
            }
        )
        result["summary"]["check_count"] += 1
        result["summary"]["failed_count"] += 1
        result["summary"]["failed_codes"] = sorted(
            [*result["summary"]["failed_codes"], "VALIDATION_INPUT_IMMUTABLE"]
        )
        result.pop("result_hash", None)
        result["result_hash"] = canonical_hash(result)
    return _settle_validation(db, claim, result=result, now=current_time)


def process_run_validation_queue(
    *,
    session_factory: Callable[[], Session],
    worker_id: str,
    limit: int = 20,
    lease_seconds: int = 90,
) -> RunValidationBatchResult:
    scanned = claimed = passed = failed = stale = ignored = errors = 0
    for _index in range(max(1, min(int(limit), 200))):
        db = session_factory()
        try:
            with db.begin():
                claim = claim_next_run_validation(
                    db,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
            if claim is None:
                break
            scanned += 1
            claimed += 1
        except Exception:
            logger.exception("bid_run_validation_claim_failed")
            errors += 1
            db.close()
            break
        finally:
            if db.is_active:
                db.close()
        execution_db = session_factory()
        try:
            with execution_db.begin():
                result = execute_run_validation_claim(execution_db, claim)
            if result.outcome == "passed":
                passed += 1
            elif result.outcome == "stale":
                stale += 1
            else:
                failed += 1
        except BidRunValidationFenceLost:
            ignored += 1
        except Exception:
            logger.exception(
                "bid_run_validation_execute_failed",
                extra={"validation_id": claim.validation_id},
            )
            errors += 1
        finally:
            execution_db.close()
    return RunValidationBatchResult(scanned, claimed, passed, failed, stale, ignored, errors)


def _cancel_validation(
    db: Session,
    validation: BidRunValidation,
    *,
    now: datetime,
    code: str,
) -> None:
    attempts = (
        db.query(BidRunValidationAttempt)
        .filter(
            BidRunValidationAttempt.validation_id == validation.id,
            BidRunValidationAttempt.status.in_(("leased", "running")),
        )
        .with_for_update()
        .all()
    )
    for attempt in attempts:
        attempt.status = "cancelled"
        attempt.finished_at = now
        attempt.heartbeat_at = now
        attempt.error_code = code
    validation.status = "cancelled"
    validation.lease_owner = None
    validation.lease_until = None
    validation.failure_code = code
    validation.finished_at = now
    validation.row_version = int(validation.row_version) + 1
    db.flush()


def recover_expired_run_validation(
    db: Session,
    *,
    validation_id: str,
    now: datetime | None = None,
) -> str:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    validation = (
        db.query(BidRunValidation)
        .filter(BidRunValidation.id == validation_id)
        .with_for_update()
        .one_or_none()
    )
    if validation is None or str(validation.status) not in {"leased", "running"}:
        return "ignored"
    if validation.lease_until is None or as_utc(validation.lease_until) > current_time:
        return "ignored"
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == validation.run_id)
        .with_for_update()
        .one()
    )
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == validation.assessment_id)
        .with_for_update()
        .one()
    )
    active_attempt = (
        db.query(BidRunValidationAttempt)
        .filter(
            BidRunValidationAttempt.validation_id == validation.id,
            BidRunValidationAttempt.fencing_token == validation.fencing_token,
            BidRunValidationAttempt.status.in_(("leased", "running")),
        )
        .with_for_update()
        .one_or_none()
    )
    if active_attempt is not None:
        active_attempt.status = "lease_expired"
        active_attempt.finished_at = current_time
        active_attempt.heartbeat_at = current_time
        active_attempt.error_code = "BID_RUN_VALIDATION_LEASE_EXPIRED"
    if (
        str(run.status) == "validating"
        and run.cancel_requested_at is None
        and str(assessment.active_run_id or "") == str(run.id)
        and str(assessment.lifecycle_status) == "active"
    ):
        validation.status = "requested"
        validation.lease_owner = None
        validation.lease_until = None
        validation.row_version = int(validation.row_version) + 1
        db.flush()
        return "recovered"
    _cancel_validation(
        db,
        validation,
        now=current_time,
        code="BID_RUN_VALIDATION_TERMINAL_FENCE",
    )
    return "cancelled"


def maintain_run_validations(
    *,
    session_factory: Callable[[], Session],
    limit: int = 100,
    now: datetime | None = None,
) -> RunValidationMaintenanceResult:
    current_limit = max(1, min(int(limit), 500))
    index_db = session_factory()
    try:
        event_ids = pending_validation_event_ids(index_db, limit=current_limit)
    finally:
        index_db.close()
    materialized = duplicate = failed = 0
    for event_id in event_ids:
        db = session_factory()
        try:
            with db.begin():
                result = consume_run_validation_requested_event(db, event_id=event_id, now=now)
            if result.duplicate:
                duplicate += 1
            elif isinstance(result.value, dict) and result.value.get("materialized"):
                materialized += 1
        except Exception:
            logger.exception(
                "bid_run_validation_materialize_failed",
                extra={"event_id": event_id},
            )
            failed += 1
        finally:
            db.close()

    scan_db = session_factory()
    try:
        current_time = as_utc(now) if now is not None else database_utc_now(scan_db)
        expired_ids = [
            str(row[0])
            for row in scan_db.query(BidRunValidation.id)
            .filter(
                BidRunValidation.status.in_(("leased", "running")),
                BidRunValidation.lease_until <= current_time,
            )
            .order_by(BidRunValidation.lease_until.asc(), BidRunValidation.id.asc())
            .limit(current_limit)
            .all()
        ]
    finally:
        scan_db.close()
    recovered = cancelled = 0
    for validation_id in expired_ids:
        db = session_factory()
        try:
            with db.begin():
                outcome = recover_expired_run_validation(
                    db,
                    validation_id=validation_id,
                    now=now,
                )
            if outcome == "recovered":
                recovered += 1
            elif outcome == "cancelled":
                cancelled += 1
        except Exception:
            logger.exception(
                "bid_run_validation_recovery_failed",
                extra={"validation_id": validation_id},
            )
            failed += 1
        finally:
            db.close()
    return RunValidationMaintenanceResult(
        scanned_events=len(event_ids),
        materialized=materialized,
        duplicate=duplicate,
        recovered=recovered,
        cancelled=cancelled,
        failed=failed,
    )
