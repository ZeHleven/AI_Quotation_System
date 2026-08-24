"""Phase 3C task-runtime control plane for bid-assessment Runs.

This module owns deterministic TaskContract materialization, TaskAttempt
leases, heartbeat/fencing checks, immutable checkpoints, dependency release,
and lease recovery.  It deliberately does not execute a model, OCR parser,
visual parser, tool, or object-storage operation.  Every mutating primitive
flushes but leaves commit/rollback to its caller.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
)
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidCheckpoint,
    BidPlanRevision,
    BidTask,
    BidTaskAttempt,
    BidTaskDependency,
)
from app.models.bid_assessment_tooling import BidContextManifest
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    canonical_json,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_task_registry import load_frozen_task_registry, load_standard_task_registry


TASK_RUNTIME_PRODUCER = "bid-task-runtime-v1"
TASK_OUTPUT_VALIDATOR_VERSION = "bid-task-output-validator-v1"
PLAN_ENVELOPE_SCHEMA = "bid.plan.commit.envelope.v1"
PLANNER_GENERATOR_VERSION = "bid-deterministic-bootstrap-planner-v1"
PLAN_VALIDATOR_VERSION = "bid-plan-validator-v1"
PHASE4_PLAN_ENVELOPE_SCHEMA = "bid.plan.commit.envelope.v2"
PHASE4_PLANNER_GENERATOR_VERSION = "bid-deterministic-stage-planner-v2"
PHASE4_PLAN_VALIDATOR_VERSION = "bid-plan-validator-v2"
LEASEABLE_RUN_STATES = frozenset({"queued", "running"})
ACTIVE_ATTEMPT_STATES = frozenset({"leased", "running", "validating"})
SUCCESS_DEPENDENCY_STATES = frozenset({"succeeded", "skipped"})
TERMINAL_TASK_STATES = frozenset(
    {"succeeded", "failed", "skipped", "stale", "cancelled"}
)
TERMINAL_RUN_STATES = frozenset({"succeeded", "stale", "cancelled"})
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ERROR_CODE_PATTERN = re.compile(r"^BID_[A-Z0-9_]{2,96}$")
MAX_CHECKPOINT_JSON_BYTES = 64 * 1024
logger = logging.getLogger(__name__)

BUDGET_PROFILES: dict[str, dict[str, int]] = {
    "LOW_V1": {
        "max_iterations": 3,
        "max_tool_calls": 4,
        "max_input_tokens": 8000,
        "max_output_tokens": 2000,
    },
    "STANDARD_V1": {
        "max_iterations": 6,
        "max_tool_calls": 8,
        "max_input_tokens": 16000,
        "max_output_tokens": 4000,
    },
    "HIGH_V1": {
        "max_iterations": 8,
        "max_tool_calls": 12,
        "max_input_tokens": 24000,
        "max_output_tokens": 6000,
    },
}
STOP_CONDITIONS = (
    "completion_contract_satisfied",
    "task_budget_exhausted",
    "two_consecutive_actions_without_new_governed_information",
    "run_input_or_fencing_token_became_stale",
)


class BidTaskRuntimeError(RuntimeError):
    code = "BID_TASK_RUNTIME_ERROR"


class BidTaskRuntimeNotFound(BidTaskRuntimeError):
    code = "BID_TASK_RUNTIME_NOT_FOUND"


class BidTaskContractInvalid(BidTaskRuntimeError):
    code = "BID_TASK_CONTRACT_INVALID"


class BidTaskNotLeaseable(BidTaskRuntimeError):
    code = "BID_TASK_NOT_LEASEABLE"


class BidTaskFenceLost(BidTaskRuntimeError):
    code = "BID_TASK_FENCE_LOST"


class BidTaskLeaseExpired(BidTaskFenceLost):
    code = "BID_TASK_LEASE_EXPIRED"


class BidCheckpointConflict(BidTaskRuntimeError):
    code = "BID_CHECKPOINT_CONFLICT"


@dataclass(frozen=True)
class TaskLeaseClaim:
    task_id: str
    attempt_id: str
    attempt_no: int
    worker_id: str
    fencing_token: int
    lease_until: datetime
    task_contract: dict[str, Any]
    task_contract_hash: str
    resume_checkpoint: dict[str, Any] | None


@dataclass(frozen=True)
class TaskCheckpointReceipt:
    checkpoint_id: str
    task_id: str
    attempt_id: str
    action_seq: int
    state_hash: str
    duplicate: bool


@dataclass(frozen=True)
class TaskCompletionReceipt:
    checkpoint_id: str
    state_hash: str
    output_hash: str
    completion_contract: str
    validator_version: str
    output_ref: str | None = None


@dataclass(frozen=True)
class TaskCompletionResult:
    task_id: str
    attempt_id: str
    status: str
    released_task_ids: tuple[str, ...]
    validation_requested: bool
    outbox_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskFailureResult:
    task_id: str
    attempt_id: str
    task_status: str
    run_status: str
    retry_scheduled: bool
    outbox_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskRuntimeMaintenanceResult:
    scanned: int
    recovered: int
    retry_scheduled: int
    run_failed: int
    terminal_fenced: int
    failed: int


def _utc_text(value: datetime) -> str:
    return as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _binding_utc_text(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _normalized_json(value: Any, *, field: str) -> Any:
    try:
        text = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise BidCheckpointConflict(f"BID_CHECKPOINT_{field.upper()}_INVALID") from exc
    if len(text.encode("utf-8")) > MAX_CHECKPOINT_JSON_BYTES:
        raise BidCheckpointConflict(f"BID_CHECKPOINT_{field.upper()}_TOO_LARGE")
    return json.loads(text)


def _require_hash(value: str, *, field: str) -> str:
    normalized = str(value or "").lower()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise BidTaskRuntimeError(f"BID_TASK_{field.upper()}_INVALID")
    return normalized


def _plan_task_definition(plan: BidPlanRevision, task: BidTask) -> dict[str, Any]:
    envelope = dict(plan.proposal_json or {})
    if (
        str(plan.status) not in {"committed", "superseded"}
        or str(envelope.get("schema") or "")
        not in {PLAN_ENVELOPE_SCHEMA, PHASE4_PLAN_ENVELOPE_SCHEMA}
        or str((envelope.get("validation") or {}).get("validated_hash") or "")
        != str(plan.validated_hash or "")
    ):
        raise BidTaskContractInvalid("BID_TASK_PLAN_NOT_COMMITTED")
    matches = [
        item
        for item in (envelope.get("proposal") or {}).get("add_tasks", [])
        if isinstance(item, dict) and str(item.get("task_key") or "") == str(task.task_key)
    ]
    if len(matches) != 1:
        raise BidTaskContractInvalid("BID_TASK_DEFINITION_NOT_UNIQUE")
    return matches[0]


def _run_bound_versions(
    db: Session,
    *,
    run: BidAnalysisRun,
    scope: BidAssessmentScope,
) -> dict[str, Any]:
    bindings = (
        ("manifest_version", BidDocumentManifest, run.manifest_id),
        ("enterprise_snapshot_version", BidEnterpriseSnapshot, run.enterprise_snapshot_id),
        ("rule_set_version", BidRuleSet, run.rule_set_id),
        ("fact_catalog_version", BidFactCatalogVersion, run.fact_catalog_version_id),
        ("prompt_bundle_version", BidPromptBundle, run.prompt_bundle_id),
        ("tool_registry_version", BidToolRegistryVersion, run.tool_registry_version_id),
        ("model_profile_version", BidModelProfileVersion, run.model_profile_version_id),
        ("formula_catalog_version", BidFormulaCatalogVersion, run.formula_catalog_version_id),
    )
    values: dict[str, Any] = {}
    for field, model, row_id in bindings:
        row = db.query(model).filter(model.id == row_id).one_or_none()
        if row is None:
            raise BidTaskContractInvalid(f"BID_TASK_BOUND_VERSION_MISSING:{field}")
        values[field] = int(row.version) if field == "manifest_version" else str(row.version)
    return {
        "manifest_id": str(run.manifest_id),
        "manifest_version": values["manifest_version"],
        "scope_id": str(scope.id),
        "scope_version": int(scope.version),
        "enterprise_snapshot_version": values["enterprise_snapshot_version"],
        "rule_set_version": values["rule_set_version"],
        "fact_catalog_version": values["fact_catalog_version"],
        "prompt_bundle_version": values["prompt_bundle_version"],
        "tool_registry_version": values["tool_registry_version"],
        "model_profile_version": values["model_profile_version"],
        "formula_catalog_version": values["formula_catalog_version"],
        "evaluation_time": _binding_utc_text(run.evaluation_time),
    }


def build_task_contract(db: Session, task: BidTask) -> dict[str, Any]:
    """Rebuild one immutable TaskContract only from committed governed data."""

    run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == task.run_id).one_or_none()
    plan = (
        db.query(BidPlanRevision)
        .filter(
            BidPlanRevision.id == task.plan_revision_id,
            BidPlanRevision.run_id == task.run_id,
        )
        .one_or_none()
    )
    if run is None or plan is None:
        raise BidTaskContractInvalid("BID_TASK_FROZEN_BINDING_MISSING")
    scope = (
        db.query(BidAssessmentScope)
        .filter(
            BidAssessmentScope.id == run.scope_id,
            BidAssessmentScope.assessment_id == run.assessment_id,
        )
        .one_or_none()
    )
    if scope is None:
        raise BidTaskContractInvalid("BID_TASK_FROZEN_BINDING_MISSING")

    definition = _plan_task_definition(plan, task)
    envelope = dict(plan.proposal_json or {})
    planner_input = dict(envelope.get("planner_input") or {})
    proposal = dict(envelope.get("proposal") or {})
    bound_versions = dict(planner_input.get("bound_versions") or {})
    envelope_schema = str(envelope.get("schema") or "")
    phase4_envelope = envelope_schema == PHASE4_PLAN_ENVELOPE_SCHEMA
    registry = (
        load_frozen_task_registry(
            catalog_ref=str(envelope.get("task_catalog_ref") or ""),
            registry_version=str(envelope.get("task_registry_version") or ""),
            registry_hash=str(envelope.get("task_registry_hash") or ""),
        )
        if phase4_envelope
        else load_standard_task_registry()
    )
    generator_version = (
        PHASE4_PLANNER_GENERATOR_VERSION if phase4_envelope else PLANNER_GENERATOR_VERSION
    )
    validator_version = (
        PHASE4_PLAN_VALIDATOR_VERSION if phase4_envelope else PLAN_VALIDATOR_VERSION
    )
    skill_catalog = None
    if phase4_envelope:
        from app.services.bid_skill_registry import (
            load_skill_catalog,
            verify_frozen_skill_binding,
        )

        skill_catalog = load_skill_catalog(str(envelope.get("skill_catalog_ref") or ""))
        verify_frozen_skill_binding(
            catalog_ref=skill_catalog.catalog_ref,
            catalog_version=str(envelope.get("skill_catalog_version") or ""),
            catalog_hash=str(envelope.get("skill_catalog_hash") or ""),
            task_type=str(task.task_type),
            binding=dict(definition.get("skill_binding") or {}),
        )
    validated_payload = {
        "schema": envelope_schema,
        "generator_version": generator_version,
        "validator_version": validator_version,
        "task_registry_version": registry.version,
        "task_registry_hash": registry.registry_hash,
        "run_input_hash": str(run.input_hash),
        "planner_input_hash": canonical_hash(planner_input),
        "proposal_hash": canonical_hash(proposal),
    }
    if phase4_envelope:
        validated_payload.update(
            {
                "skill_catalog_ref": skill_catalog.catalog_ref,
                "skill_catalog_version": skill_catalog.version,
                "skill_catalog_hash": skill_catalog.catalog_hash,
                "stage": str(envelope.get("stage") or ""),
                "final_stage": bool(envelope.get("final_stage")),
                "task_catalog_ref": registry.catalog_ref,
            }
        )
    if (
        str(envelope.get("generator_version") or "") != generator_version
        or str(envelope.get("validator_version") or "") != validator_version
        or str(envelope.get("task_registry_version") or "") != registry.version
        or str(envelope.get("task_registry_hash") or "") != registry.registry_hash
        or str(envelope.get("planner_input_hash") or "")
        != validated_payload["planner_input_hash"]
        or str(envelope.get("proposal_hash") or "")
        != validated_payload["proposal_hash"]
        or canonical_hash(validated_payload) != str(plan.validated_hash or "")
    ):
        raise BidTaskContractInvalid("BID_TASK_PLAN_ENVELOPE_HASH_MISMATCH")
    authoritative_versions = _run_bound_versions(db, run=run, scope=scope)
    if (
        str(envelope.get("run_input_hash") or "") != str(run.input_hash)
        or bound_versions != authoritative_versions
    ):
        raise BidTaskContractInvalid("BID_TASK_FROZEN_BINDING_MISMATCH")

    policy = registry.policies.get(str(task.task_type))
    if policy is None:
        raise BidTaskContractInvalid("BID_TASK_TYPE_NOT_REGISTERED")
    expected_fields = {
        "task_type": policy.task_type,
        "objective": str(definition.get("objective") or ""),
        "tool_profile": policy.tool_profile,
        "context_profile": policy.context_profile,
        "budget_profile": policy.budget_profile,
        "completion_contract": policy.completion_contract,
    }
    actual_fields = {
        "task_type": str(task.task_type),
        "objective": str(task.objective),
        "tool_profile": str(task.tool_profile),
        "context_profile": str(task.context_profile),
        "budget_profile": str(task.budget_profile),
        "completion_contract": str(task.completion_contract),
    }
    definition_fields = {
        "task_type": str(definition.get("task_type") or ""),
        "objective": str(definition.get("objective") or ""),
        "tool_profile": str(definition.get("tool_profile") or ""),
        "context_profile": str(definition.get("context_profile") or ""),
        "budget_profile": str(definition.get("budget_profile") or ""),
        "completion_contract": str(definition.get("completion_contract") or ""),
    }
    if actual_fields != expected_fields or definition_fields != expected_fields:
        raise BidTaskContractInvalid("BID_TASK_ROW_POLICY_MISMATCH")
    expected_input_hash = canonical_hash(
        {"run_input_hash": str(run.input_hash), "task_definition": definition}
    )
    if str(task.input_hash) != expected_input_hash:
        raise BidTaskContractInvalid("BID_TASK_INPUT_HASH_MISMATCH")
    budget = BUDGET_PROFILES.get(str(task.budget_profile))
    if budget is None:
        raise BidTaskContractInvalid("BID_TASK_BUDGET_PROFILE_INVALID")

    dependencies = (
        db.query(BidTask.task_key)
        .join(
            BidTaskDependency,
            BidTaskDependency.depends_on_task_id == BidTask.id,
        )
        .filter(
            BidTaskDependency.run_id == task.run_id,
            BidTaskDependency.task_id == task.id,
        )
        .order_by(BidTask.task_key.asc())
        .all()
    )
    actual_dependencies = tuple(str(row[0]) for row in dependencies)
    expected_dependencies = tuple(
        sorted(str(value) for value in definition.get("depends_on") or [])
    )
    if actual_dependencies != expected_dependencies:
        raise BidTaskContractInvalid("BID_TASK_DEPENDENCY_MISMATCH")

    scope_snapshot = dict(scope.selected_lot_snapshot_json or {})
    lot_id = scope_snapshot.get("lot_id") or scope.source_lot_candidate_id
    contract = {
        "task_id": str(task.id),
        "task_key": str(task.task_key),
        "task_type": str(task.task_type),
        "objective": str(task.objective),
        "scope": {
            "assessment_id": str(run.assessment_id),
            "scope_id": str(scope.id),
            "scope_version": int(scope.version),
            "lot_id": str(lot_id) if lot_id else None,
        },
        "depends_on": list(actual_dependencies),
        "bound_versions": bound_versions,
        "required_fact_slots": sorted(
            str(value) for value in definition.get("required_fact_slots") or []
        ),
        "allowed_tools": list(policy.allowed_tools),
        "context_profile": str(policy.context_profile),
        "budget": dict(budget),
        "completion_contract": str(policy.completion_contract),
        "stop_conditions": list(STOP_CONDITIONS),
        "failure_policy": "retry_then_fail",
        "output_version": "bid-task-output-v1",
    }
    if phase4_envelope:
        if list(definition.get("allowed_tools") or []) != list(policy.allowed_tools):
            raise BidTaskContractInvalid("BID_TASK_ALLOWED_TOOLS_MISMATCH")
        # Keep the v1 contract shape stable for legacy Plans; v2 Tasks add the
        # binding as a closed optional property.
        contract["skill_binding"] = dict(definition["skill_binding"])
    # Canonical serialization is also the final fail-closed shape check.
    return json.loads(canonical_json(contract))


def _locked_claim_rows(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime,
    allowed_attempt_states: Iterable[str] = ACTIVE_ATTEMPT_STATES,
) -> tuple[BidTaskAttempt, BidTask, BidAnalysisRun]:
    attempt = (
        db.query(BidTaskAttempt)
        .filter(BidTaskAttempt.id == claim.attempt_id)
        .with_for_update()
        .one_or_none()
    )
    if attempt is None:
        raise BidTaskRuntimeNotFound("BID_TASK_ATTEMPT_NOT_FOUND")
    task = (
        db.query(BidTask)
        .filter(BidTask.id == claim.task_id)
        .with_for_update()
        .one_or_none()
    )
    if task is None or str(attempt.task_id) != str(task.id):
        raise BidTaskRuntimeNotFound("BID_TASK_NOT_FOUND")
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == task.run_id)
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise BidTaskRuntimeNotFound("BID_TASK_RUN_NOT_FOUND")
    if (
        str(task.current_attempt_id or "") != str(attempt.id)
        or str(attempt.lease_owner or "") != str(claim.worker_id)
        or int(attempt.fencing_token) != int(claim.fencing_token)
        or str(attempt.status) not in set(allowed_attempt_states)
        or str(run.status) not in LEASEABLE_RUN_STATES
        or run.cancel_requested_at is not None
        or str(task.status) not in {"leased", "running", "validating"}
    ):
        raise BidTaskFenceLost(BidTaskFenceLost.code)
    if attempt.lease_until is None or as_utc(attempt.lease_until) <= as_utc(now):
        raise BidTaskLeaseExpired(BidTaskLeaseExpired.code)
    current_contract = build_task_contract(db, task)
    if (
        canonical_hash(current_contract) != str(claim.task_contract_hash)
        or canonical_hash(claim.task_contract) != str(claim.task_contract_hash)
    ):
        raise BidTaskContractInvalid("BID_TASK_CONTRACT_HASH_MISMATCH")
    return attempt, task, run


def lock_task_claim(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    now: datetime,
    allowed_attempt_states: Iterable[str] = ACTIVE_ATTEMPT_STATES,
) -> tuple[BidTaskAttempt, BidTask, BidAnalysisRun]:
    """Public fencing gate shared by Phase 3E context/tool control services."""

    return _locked_claim_rows(
        db,
        claim,
        now=now,
        allowed_attempt_states=allowed_attempt_states,
    )


def lease_next_ready_task(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 180,
    allowed_task_types: Iterable[str] | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> TaskLeaseClaim | None:
    """Atomically lease one ready task; no external work is performed here."""

    normalized_worker = str(worker_id or "")[:128]
    if not normalized_worker:
        raise BidTaskNotLeaseable("BID_TASK_WORKER_ID_REQUIRED")
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    query = (
        db.query(BidTask)
        .join(BidAnalysisRun, BidAnalysisRun.id == BidTask.run_id)
        .join(
            BidAssessment,
            BidAssessment.id == BidAnalysisRun.assessment_id,
        )
        .filter(
            BidTask.status == "ready",
            BidAnalysisRun.status.in_(tuple(LEASEABLE_RUN_STATES)),
            BidAnalysisRun.cancel_requested_at.is_(None),
            BidAssessment.lifecycle_status == "active",
            BidAssessment.active_run_id == BidAnalysisRun.id,
        )
    )
    normalized_types = tuple(sorted(set(str(value) for value in allowed_task_types or ())))
    if allowed_task_types is not None and not normalized_types:
        return None
    if normalized_types:
        query = query.filter(BidTask.task_type.in_(normalized_types))
    task = (
        query.order_by(BidTask.priority.asc(), BidTask.created_at.asc(), BidTask.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if task is None:
        return None
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == task.run_id)
        .with_for_update()
        .one()
    )
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == run.assessment_id)
        .with_for_update()
        .one()
    )
    if (
        str(task.status) != "ready"
        or str(run.status) not in LEASEABLE_RUN_STATES
        or run.cancel_requested_at is not None
        or str(assessment.active_run_id or "") != str(run.id)
    ):
        return None

    task_contract = build_task_contract(db, task)
    task_contract_hash = canonical_hash(task_contract)
    checkpoint = (
        db.query(BidCheckpoint)
        .join(BidTaskAttempt, BidTaskAttempt.id == BidCheckpoint.task_attempt_id)
        .filter(BidTaskAttempt.task_id == task.id)
        .order_by(
            BidTaskAttempt.attempt_no.desc(),
            BidCheckpoint.action_seq.desc(),
            BidCheckpoint.created_at.desc(),
        )
        .first()
    )
    resume_checkpoint = None
    if checkpoint is not None:
        resume_checkpoint = {
            "checkpoint_id": str(checkpoint.id),
            "source_attempt_id": str(checkpoint.task_attempt_id),
            "action_seq": int(checkpoint.action_seq),
            "state_hash": str(checkpoint.state_hash),
            "candidate_output_ref": (
                str(checkpoint.candidate_output_ref)
                if checkpoint.candidate_output_ref
                else None
            ),
            "next_state": str(checkpoint.next_state) if checkpoint.next_state else None,
        }
    lease_until = current_time + timedelta(seconds=max(15, min(int(lease_seconds), 900)))
    attempt = None
    if task.current_attempt_id:
        candidate = (
            db.query(BidTaskAttempt)
            .filter(
                BidTaskAttempt.id == str(task.current_attempt_id),
                BidTaskAttempt.task_id == task.id,
            )
            .with_for_update()
            .one_or_none()
        )
        if candidate is not None and str(candidate.status) == "created":
            attempt = candidate
    if attempt is None:
        attempt_no = int(
            db.query(func.max(BidTaskAttempt.attempt_no))
            .filter(BidTaskAttempt.task_id == task.id)
            .scalar()
            or 0
        ) + 1
        fencing_token = int(
            db.query(func.max(BidTaskAttempt.fencing_token))
            .filter(BidTaskAttempt.task_id == task.id)
            .scalar()
            or 0
        ) + 1
        attempt = BidTaskAttempt(
            id=str(uuid.uuid4()),
            task_id=str(task.id),
            attempt_no=attempt_no,
            status="leased",
            lease_owner=normalized_worker,
            lease_until=lease_until,
            heartbeat_at=current_time,
            fencing_token=fencing_token,
            row_version=1,
        )
        db.add(attempt)
        db.flush()
    else:
        attempt_no = int(attempt.attempt_no)
        fencing_token = int(attempt.fencing_token)
        attempt.status = "leased"
        attempt.lease_owner = normalized_worker
        attempt.lease_until = lease_until
        attempt.heartbeat_at = current_time
        attempt.row_version = int(attempt.row_version) + 1
        db.flush()
    before = {"task_status": str(task.status), "task_row_version": int(task.row_version)}
    task.status = "leased"
    task.current_attempt_id = str(attempt.id)
    task.row_version = int(task.row_version) + 1
    if str(run.status) == "queued":
        run.status = "running"
        run.started_at = run.started_at or current_time
        run.row_version = int(run.row_version) + 1
    db.flush()

    event = append_outbox_event(
        db,
        event_type="bid.task.leased.v1",
        producer=TASK_RUNTIME_PRODUCER,
        aggregate_type="task",
        aggregate_id=str(task.id),
        aggregate_version=int(task.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(request_id or f"lease:{attempt.id}"),
        payload_schema="bid.task.leased.v1.payload",
        payload={
            "task_id": str(task.id),
            "task_key": str(task.task_key),
            "task_type": str(task.task_type),
            "run_id": str(run.id),
            "plan_revision_id": str(task.plan_revision_id),
            "attempt_id": str(attempt.id),
            "attempt_no": attempt_no,
            "lease_owner": normalized_worker,
            "lease_until": _utc_text(lease_until),
            "fencing_token": fencing_token,
            "task_contract_hash": task_contract_hash,
            "resume_checkpoint_id": (
                resume_checkpoint["checkpoint_id"] if resume_checkpoint else None
            ),
            "resource_version": int(task.row_version),
        },
        dedupe_key=f"task-leased:{attempt.id}:{fencing_token}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{normalized_worker}",
        action="task.attempt.lease",
        entity_type="task",
        entity_id=str(task.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"lease:{attempt.id}"),
        correlation_id=str(event.event_id),
        before=before,
        after={
            "task_status": str(task.status),
            "task_row_version": int(task.row_version),
            "attempt_id": str(attempt.id),
            "attempt_no": attempt_no,
            "fencing_token": fencing_token,
            "task_contract_hash": task_contract_hash,
            "resume_checkpoint_id": (
                resume_checkpoint["checkpoint_id"] if resume_checkpoint else None
            ),
        },
        occurred_at=current_time,
    )
    db.flush()
    return TaskLeaseClaim(
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        attempt_no=attempt_no,
        worker_id=normalized_worker,
        fencing_token=fencing_token,
        lease_until=lease_until,
        task_contract=task_contract,
        task_contract_hash=task_contract_hash,
        resume_checkpoint=resume_checkpoint,
    )


def start_task_attempt(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    request_id: str | None = None,
    now: datetime | None = None,
) -> datetime:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = _locked_claim_rows(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"leased", "running"},
    )
    if str(attempt.status) == "running" and str(task.status) == "running":
        return as_utc(attempt.started_at or current_time)
    if str(attempt.status) != "leased" or str(task.status) != "leased":
        raise BidTaskFenceLost(BidTaskFenceLost.code)
    before = {"task_status": str(task.status), "attempt_status": str(attempt.status)}
    attempt.status = "running"
    attempt.started_at = attempt.started_at or current_time
    attempt.heartbeat_at = current_time
    attempt.row_version = int(attempt.row_version) + 1
    task.status = "running"
    task.row_version = int(task.row_version) + 1
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="task.attempt.start",
        entity_type="task_attempt",
        entity_id=str(attempt.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"start:{attempt.id}"),
        before=before,
        after={"task_status": "running", "attempt_status": "running"},
        metadata={"fencing_token": int(claim.fencing_token)},
        occurred_at=current_time,
    )
    db.flush()
    return as_utc(attempt.started_at)


def heartbeat_task_attempt(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    lease_seconds: int = 180,
    now: datetime | None = None,
) -> datetime:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, _task, _run = _locked_claim_rows(db, claim, now=current_time)
    lease_until = current_time + timedelta(seconds=max(15, min(int(lease_seconds), 900)))
    attempt.heartbeat_at = current_time
    attempt.lease_until = lease_until
    attempt.row_version = int(attempt.row_version) + 1
    db.flush()
    return lease_until


def write_task_checkpoint(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    action_seq: int,
    state: dict[str, Any],
    tool_refs: list[dict[str, Any]] | None = None,
    budget_usage: dict[str, Any] | None = None,
    candidate_output_ref: str | None = None,
    next_state: str | None = None,
    context_manifest_id: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> TaskCheckpointReceipt:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = _locked_claim_rows(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    if int(action_seq) < 0:
        raise BidCheckpointConflict("BID_CHECKPOINT_ACTION_SEQUENCE_INVALID")
    manifest_id = str(context_manifest_id) if context_manifest_id else None
    if manifest_id is not None:
        context_manifest = (
            db.query(BidContextManifest)
            .filter(
                BidContextManifest.id == manifest_id,
                BidContextManifest.task_attempt_id == attempt.id,
                BidContextManifest.task_id == task.id,
                BidContextManifest.run_id == run.id,
            )
            .one_or_none()
        )
        if (
            context_manifest is None
            or int(context_manifest.fencing_token) != int(claim.fencing_token)
        ):
            raise BidCheckpointConflict("BID_CHECKPOINT_CONTEXT_MANIFEST_MISMATCH")
    normalized_state = _normalized_json(state, field="state")
    normalized_tools = (
        _normalized_json(tool_refs, field="tool_refs") if tool_refs is not None else None
    )
    normalized_budget = (
        _normalized_json(budget_usage, field="budget_usage")
        if budget_usage is not None
        else None
    )
    normalized_output_ref = (
        str(candidate_output_ref)[:512] if candidate_output_ref else None
    )
    normalized_next_state = str(next_state)[:64] if next_state else None
    state_hash = canonical_hash(normalized_state)
    existing = (
        db.query(BidCheckpoint)
        .filter(
            BidCheckpoint.task_attempt_id == attempt.id,
            BidCheckpoint.action_seq == int(action_seq),
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        same = (
            int(existing.fencing_token) == int(claim.fencing_token)
            and (str(existing.context_manifest_id) if existing.context_manifest_id else None)
            == manifest_id
            and str(existing.state_hash) == state_hash
            and json.loads(canonical_json(existing.tool_refs_json))
            == json.loads(canonical_json(normalized_tools))
            and json.loads(canonical_json(existing.budget_usage_json))
            == json.loads(canonical_json(normalized_budget))
            and (existing.candidate_output_ref or None) == normalized_output_ref
            and (existing.next_state or None) == normalized_next_state
        )
        if not same:
            raise BidCheckpointConflict("BID_CHECKPOINT_ACTION_SEQUENCE_REUSED")
        return TaskCheckpointReceipt(
            checkpoint_id=str(existing.id),
            task_id=str(task.id),
            attempt_id=str(attempt.id),
            action_seq=int(existing.action_seq),
            state_hash=str(existing.state_hash),
            duplicate=True,
        )
    latest_value = (
        db.query(func.max(BidCheckpoint.action_seq))
        .filter(BidCheckpoint.task_attempt_id == attempt.id)
        .scalar()
    )
    latest_seq = int(latest_value) if latest_value is not None else -1
    if int(action_seq) != latest_seq + 1:
        raise BidCheckpointConflict(
            f"BID_CHECKPOINT_ACTION_SEQUENCE_GAP:{latest_seq + 1}:{action_seq}"
        )
    checkpoint = BidCheckpoint(
        id=str(uuid.uuid4()),
        task_attempt_id=str(attempt.id),
        fencing_token=int(claim.fencing_token),
        action_seq=int(action_seq),
        context_manifest_id=manifest_id,
        state_json=normalized_state,
        state_hash=state_hash,
        tool_refs_json=normalized_tools,
        budget_usage_json=normalized_budget,
        candidate_output_ref=normalized_output_ref,
        next_state=normalized_next_state,
        created_at=current_time,
    )
    db.add(checkpoint)
    run.last_checkpoint_at = current_time
    run.row_version = int(run.row_version) + 1
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="task.checkpoint.write",
        entity_type="checkpoint",
        entity_id=str(checkpoint.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"checkpoint:{checkpoint.id}"),
        after={
            "task_id": str(task.id),
            "attempt_id": str(attempt.id),
            "action_seq": int(action_seq),
            "fencing_token": int(claim.fencing_token),
            "state_hash": state_hash,
        },
        occurred_at=current_time,
    )
    db.flush()
    return TaskCheckpointReceipt(
        checkpoint_id=str(checkpoint.id),
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        action_seq=int(action_seq),
        state_hash=state_hash,
        duplicate=False,
    )


def _task_counts(db: Session, *, run_id: str) -> tuple[int, int]:
    total = int(db.query(func.count(BidTask.id)).filter(BidTask.run_id == run_id).scalar() or 0)
    completed = int(
        db.query(func.count(BidTask.id))
        .filter(BidTask.run_id == run_id, BidTask.status.in_(tuple(SUCCESS_DEPENDENCY_STATES)))
        .scalar()
        or 0
    )
    return completed, total


def _append_task_stage_event(
    db: Session,
    *,
    task: BidTask,
    run: BidAnalysisRun,
    event_type: str,
    status: str,
    message: str,
    request_id: str,
    causation_event_id: str | None,
    occurred_at: datetime,
    extra: dict[str, Any] | None = None,
) -> Any:
    completed, total = _task_counts(db, run_id=str(run.id))
    run.row_version = int(run.row_version) + 1
    db.flush()
    payload = {
        "task_id": str(task.id),
        "task_key": str(task.task_key),
        "task_type": str(task.task_type),
        "run_id": str(run.id),
        "plan_revision_id": str(task.plan_revision_id),
        "stage_code": str(run.current_stage or "task_execution"),
        "status": str(status),
        "message": str(message)[:500],
        "completed_units": completed,
        "total_units": total,
        "resource_version": int(run.row_version),
    }
    payload.update(extra or {})
    return append_outbox_event(
        db,
        event_type=event_type,
        producer=TASK_RUNTIME_PRODUCER,
        aggregate_type="task",
        aggregate_id=str(task.id),
        aggregate_version=int(task.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=request_id,
        causation_event_id=causation_event_id,
        payload_schema=f"{event_type}.payload",
        payload=payload,
        dedupe_key=f"{event_type}:{task.id}:rv{task.row_version}",
        occurred_at=occurred_at,
    )


def _release_ready_dependents(
    db: Session,
    *,
    task: BidTask,
    run: BidAnalysisRun,
    request_id: str,
    causation_event_id: str,
    occurred_at: datetime,
) -> tuple[list[str], list[str]]:
    child_ids = [
        str(row[0])
        for row in db.query(BidTaskDependency.task_id)
        .filter(BidTaskDependency.depends_on_task_id == task.id)
        .order_by(BidTaskDependency.task_id.asc())
        .all()
    ]
    released: list[str] = []
    event_ids: list[str] = []
    for child_id in child_ids:
        child = (
            db.query(BidTask)
            .filter(BidTask.id == child_id, BidTask.run_id == run.id)
            .with_for_update()
            .one()
        )
        if str(child.status) != "blocked":
            continue
        parent_statuses = [
            str(row[0])
            for row in db.query(BidTask.status)
            .join(
                BidTaskDependency,
                BidTaskDependency.depends_on_task_id == BidTask.id,
            )
            .filter(BidTaskDependency.task_id == child.id)
            .all()
        ]
        if not parent_statuses or not all(
            value in SUCCESS_DEPENDENCY_STATES for value in parent_statuses
        ):
            continue
        child.status = "ready"
        child.row_version = int(child.row_version) + 1
        db.flush()
        ready_event = _append_task_stage_event(
            db,
            task=child,
            run=run,
            event_type="bid.task.ready.v1",
            status="ready",
            message="上游任务已完成，任务进入就绪队列",
            request_id=request_id,
            causation_event_id=causation_event_id,
            occurred_at=occurred_at,
        )
        released.append(str(child.id))
        event_ids.append(str(ready_event.event_id))
    return released, event_ids


def complete_task_attempt(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    completion: TaskCompletionReceipt,
    request_id: str | None = None,
    now: datetime | None = None,
    plan_continuation_enabled: bool | None = None,
) -> TaskCompletionResult:
    if plan_continuation_enabled is None:
        from app.core.config import settings

        plan_continuation_enabled = bool(
            settings.feature_bid_assessment_phase4_plan_continuation
        )
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = _locked_claim_rows(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(
            BidCheckpoint.id == completion.checkpoint_id,
            BidCheckpoint.task_attempt_id == attempt.id,
        )
        .with_for_update()
        .one_or_none()
    )
    latest = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.task_attempt_id == attempt.id)
        .order_by(BidCheckpoint.action_seq.desc())
        .first()
    )
    if checkpoint is None or latest is None or str(latest.id) != str(checkpoint.id):
        raise BidCheckpointConflict("BID_TASK_FINAL_CHECKPOINT_REQUIRED")
    if (
        int(checkpoint.fencing_token) != int(claim.fencing_token)
        or str(checkpoint.state_hash) != _require_hash(completion.state_hash, field="state_hash")
    ):
        raise BidTaskFenceLost(BidTaskFenceLost.code)
    if str(checkpoint.next_state or "") not in {"succeeded", "validating"}:
        raise BidCheckpointConflict("BID_TASK_FINAL_CHECKPOINT_STATE_INVALID")
    output_hash = _require_hash(completion.output_hash, field="output_hash")
    if str(completion.completion_contract) != str(task.completion_contract):
        raise BidTaskContractInvalid("BID_TASK_COMPLETION_CONTRACT_MISMATCH")
    if str(completion.validator_version) != TASK_OUTPUT_VALIDATOR_VERSION:
        raise BidTaskContractInvalid("BID_TASK_OUTPUT_VALIDATOR_VERSION_INVALID")
    if (checkpoint.candidate_output_ref or None) != (completion.output_ref or None):
        raise BidCheckpointConflict("BID_TASK_OUTPUT_REF_MISMATCH")

    before = {
        "task_status": str(task.status),
        "attempt_status": str(attempt.status),
        "task_row_version": int(task.row_version),
    }
    attempt.status = "succeeded"
    attempt.finished_at = current_time
    attempt.heartbeat_at = current_time
    attempt.row_version = int(attempt.row_version) + 1
    task.status = "succeeded"
    task.row_version = int(task.row_version) + 1
    run.last_checkpoint_at = current_time
    db.flush()
    normalized_request_id = str(request_id or f"complete:{attempt.id}")
    success_event = _append_task_stage_event(
        db,
        task=task,
        run=run,
        event_type="bid.task.succeeded.v1",
        status="succeeded",
        message="任务已通过完成契约并持久化结果",
        request_id=normalized_request_id,
        causation_event_id=None,
        occurred_at=current_time,
        extra={
            "attempt_id": str(attempt.id),
            "checkpoint_id": str(checkpoint.id),
            "result_hash": output_hash,
        },
    )
    released, ready_event_ids = _release_ready_dependents(
        db,
        task=task,
        run=run,
        request_id=normalized_request_id,
        causation_event_id=str(success_event.event_id),
        occurred_at=current_time,
    )

    validation_requested = False
    validation_event_id: str | None = None
    nonterminal = int(
        db.query(func.count(BidTask.id))
        .filter(
            BidTask.run_id == run.id,
            ~BidTask.status.in_(tuple(TERMINAL_TASK_STATES)),
        )
        .scalar()
        or 0
    )
    failed_tasks = int(
        db.query(func.count(BidTask.id))
        .filter(BidTask.run_id == run.id, BidTask.status == "failed")
        .scalar()
        or 0
    )
    if nonterminal == 0 and failed_tasks == 0 and str(run.status) == "running":
        continuation_stage: str | None = None
        continuation_next_stage: str | None = None
        continuation_plan: BidPlanRevision | None = None
        if plan_continuation_enabled:
            from app.services.bid_plan_continuation import (
                NEXT_STAGE,
                PHASE4_FINAL_STAGE,
                PHASE4_PLAN_ENVELOPE_SCHEMA,
                _plan_stage,
            )

            continuation_plan = (
                db.query(BidPlanRevision)
                .filter(
                    BidPlanRevision.run_id == run.id,
                    BidPlanRevision.status == "committed",
                    BidPlanRevision.committed_slot_key == "committed",
                )
                .with_for_update()
                .one_or_none()
            )
            if continuation_plan is not None:
                continuation_schema = str(
                    (continuation_plan.proposal_json or {}).get("schema") or ""
                )
                continuation_stage = _plan_stage(continuation_plan)
                if (
                    continuation_schema == PHASE4_PLAN_ENVELOPE_SCHEMA
                    and continuation_stage is None
                ):
                    raise BidTaskContractInvalid("BID_PLAN_CONTINUATION_STAGE_INVALID")
                continuation_final = bool(
                    (continuation_plan.proposal_json or {}).get("final_stage")
                )
                if continuation_schema == PHASE4_PLAN_ENVELOPE_SCHEMA and (
                    (continuation_stage == PHASE4_FINAL_STAGE) != continuation_final
                ):
                    raise BidTaskContractInvalid(
                        "BID_PLAN_CONTINUATION_FINAL_STAGE_INVALID"
                    )
                continuation_next_stage = NEXT_STAGE.get(str(continuation_stage))
                if (
                    continuation_schema == PHASE4_PLAN_ENVELOPE_SCHEMA
                    and continuation_stage != PHASE4_FINAL_STAGE
                    and continuation_next_stage is None
                ):
                    raise BidTaskContractInvalid("BID_PLAN_CONTINUATION_SEQUENCE_INVALID")
        if continuation_next_stage and continuation_plan is not None:
            from_state = str(run.status)
            run.status = "planning"
            run.current_stage = "planning"
            run.waiting_reason = f"awaiting_plan_continuation:{continuation_next_stage}"
            run.row_version = int(run.row_version) + 1
            db.flush()
            completed, total = _task_counts(db, run_id=str(run.id))
            continuation_event = append_outbox_event(
                db,
                event_type="bid.plan.continuation_requested.v1",
                producer=TASK_RUNTIME_PRODUCER,
                aggregate_type="run",
                aggregate_id=str(run.id),
                aggregate_version=int(run.row_version),
                assessment_id=str(run.assessment_id),
                run_id=str(run.id),
                request_id=normalized_request_id,
                causation_event_id=str(success_event.event_id),
                payload_schema="bid.plan.continuation_requested.v1.payload",
                payload={
                    "run_id": str(run.id),
                    "completed_plan_revision_id": str(continuation_plan.id),
                    "completed_stage": str(continuation_stage),
                    "next_stage": str(continuation_next_stage),
                    "from": from_state,
                    "to": "planning",
                    "stage_code": "planning",
                    "status": "planning",
                    "message": f"正在生成受控计划阶段 {continuation_next_stage}",
                    "completed_units": completed,
                    "total_units": total,
                    "resource_version": int(run.row_version),
                },
                dedupe_key=(
                    f"plan-continuation:{run.id}:{continuation_plan.id}:"
                    f"{continuation_next_stage}"
                ),
                occurred_at=current_time,
            )
            validation_event_id = str(continuation_event.event_id)
        else:
            from_state = str(run.status)
            run.status = "validating"
            run.current_stage = "validation"
            run.waiting_reason = None
            run.row_version = int(run.row_version) + 1
            db.flush()
            completed, total = _task_counts(db, run_id=str(run.id))
            validation_event = append_outbox_event(
                db,
                event_type="bid.run.validation_requested.v1",
                producer=TASK_RUNTIME_PRODUCER,
                aggregate_type="run",
                aggregate_id=str(run.id),
                aggregate_version=int(run.row_version),
                assessment_id=str(run.assessment_id),
                run_id=str(run.id),
                request_id=normalized_request_id,
                causation_event_id=str(success_event.event_id),
                payload_schema="bid.run.validation_requested.v1.payload",
                payload={
                    "run_id": str(run.id),
                    "from": from_state,
                    "to": "validating",
                    "retryable": False,
                    "completed_units": completed,
                    "total_units": total,
                    "resource_version": int(run.row_version),
                },
                dedupe_key=f"run-validation-requested:{run.id}:rv{run.row_version}",
                occurred_at=current_time,
            )
            validation_requested = True
            validation_event_id = str(validation_event.event_id)

    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="task.attempt.complete",
        entity_type="task_attempt",
        entity_id=str(attempt.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=normalized_request_id,
        correlation_id=str(success_event.event_id),
        before=before,
        after={
            "task_status": str(task.status),
            "attempt_status": str(attempt.status),
            "task_row_version": int(task.row_version),
            "checkpoint_id": str(checkpoint.id),
            "result_hash": output_hash,
            "released_task_ids": released,
            "validation_requested": validation_requested,
        },
        metadata={
            "fencing_token": int(claim.fencing_token),
            "task_contract_hash": str(claim.task_contract_hash),
        },
        occurred_at=current_time,
    )
    db.flush()
    event_ids = [str(success_event.event_id), *ready_event_ids]
    if validation_event_id:
        event_ids.append(validation_event_id)
    return TaskCompletionResult(
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        status="succeeded",
        released_task_ids=tuple(released),
        validation_requested=validation_requested,
        outbox_event_ids=tuple(event_ids),
    )


def fail_task_attempt(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    error_code: str,
    retryable: bool,
    error_detail_ref: str | None = None,
    max_attempts: int = 3,
    request_id: str | None = None,
    now: datetime | None = None,
) -> TaskFailureResult:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = _locked_claim_rows(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    normalized_error = str(error_code or "")
    if ERROR_CODE_PATTERN.fullmatch(normalized_error) is None:
        raise BidTaskRuntimeError("BID_TASK_ERROR_CODE_INVALID")
    normalized_request_id = str(request_id or f"fail:{attempt.id}")
    before = {
        "task_status": str(task.status),
        "attempt_status": str(attempt.status),
        "task_row_version": int(task.row_version),
    }
    attempt.status = "failed"
    attempt.error_code = normalized_error[:100]
    attempt.error_detail_ref = str(error_detail_ref)[:512] if error_detail_ref else None
    attempt.finished_at = current_time
    attempt.heartbeat_at = current_time
    attempt.row_version = int(attempt.row_version) + 1
    task.status = "failed"
    task.row_version = int(task.row_version) + 1
    db.flush()
    retry_scheduled = bool(retryable) and int(attempt.attempt_no) < max(1, int(max_attempts))
    failure_event = _append_task_stage_event(
        db,
        task=task,
        run=run,
        event_type="bid.task.failed.v1",
        status="retrying" if retry_scheduled else "failed",
        message=(
            "任务执行失败，已进入受控重试队列"
            if retry_scheduled
            else "任务执行失败，运行等待人工重试或终止"
        ),
        request_id=normalized_request_id,
        causation_event_id=None,
        occurred_at=current_time,
        extra={
            "attempt_id": str(attempt.id),
            "error_code": normalized_error,
            "retryable": bool(retryable),
            "retry_scheduled": retry_scheduled,
        },
    )
    event_ids = [str(failure_event.event_id)]
    if retry_scheduled:
        task.status = "ready"
        task.row_version = int(task.row_version) + 1
        db.flush()
        ready_event = _append_task_stage_event(
            db,
            task=task,
            run=run,
            event_type="bid.task.ready.v1",
            status="ready",
            message="失败任务已生成新 Attempt 的重试资格",
            request_id=normalized_request_id,
            causation_event_id=str(failure_event.event_id),
            occurred_at=current_time,
        )
        event_ids.append(str(ready_event.event_id))
    else:
        from_state = str(run.status)
        run.status = "failed"
        run.retryable = bool(retryable)
        run.waiting_reason = normalized_error
        run.finished_at = current_time
        run.row_version = int(run.row_version) + 1
        db.flush()
        run_event = append_outbox_event(
            db,
            event_type="bid.run.failed.v1",
            producer=TASK_RUNTIME_PRODUCER,
            aggregate_type="run",
            aggregate_id=str(run.id),
            aggregate_version=int(run.row_version),
            assessment_id=str(run.assessment_id),
            run_id=str(run.id),
            request_id=normalized_request_id,
            causation_event_id=str(failure_event.event_id),
            payload_schema="bid.run.failed.v1.payload",
            payload={
                "run_id": str(run.id),
                "from": from_state,
                "to": "failed",
                "retryable": bool(run.retryable),
                "error_code": normalized_error,
                "resource_version": int(run.row_version),
            },
            dedupe_key=f"run-failed:{run.id}:rv{run.row_version}",
            occurred_at=current_time,
        )
        event_ids.append(str(run_event.event_id))

    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="task.attempt.fail",
        entity_type="task_attempt",
        entity_id=str(attempt.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=normalized_request_id,
        correlation_id=str(failure_event.event_id),
        before=before,
        after={
            "task_status": str(task.status),
            "attempt_status": str(attempt.status),
            "task_row_version": int(task.row_version),
            "error_code": normalized_error,
            "retryable": bool(retryable),
            "retry_scheduled": retry_scheduled,
            "run_status": str(run.status),
        },
        metadata={"fencing_token": int(claim.fencing_token)},
        occurred_at=current_time,
    )
    db.flush()
    return TaskFailureResult(
        task_id=str(task.id),
        attempt_id=str(attempt.id),
        task_status=str(task.status),
        run_status=str(run.status),
        retry_scheduled=retry_scheduled,
        outbox_event_ids=tuple(event_ids),
    )


def _recover_one_attempt(
    db: Session,
    *,
    attempt_id: str,
    max_attempts: int,
    now: datetime,
) -> tuple[bool, bool, bool]:
    attempt = (
        db.query(BidTaskAttempt)
        .filter(BidTaskAttempt.id == attempt_id)
        .with_for_update()
        .one_or_none()
    )
    if attempt is None or str(attempt.status) not in ACTIVE_ATTEMPT_STATES:
        return False, False, False
    task = (
        db.query(BidTask)
        .filter(BidTask.id == attempt.task_id)
        .with_for_update()
        .one()
    )
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == task.run_id)
        .with_for_update()
        .one()
    )
    is_terminal_fence = str(run.status) in TERMINAL_RUN_STATES
    is_expired = attempt.lease_until is None or as_utc(attempt.lease_until) <= as_utc(now)
    if not is_terminal_fence and not is_expired:
        return False, False, False
    if str(task.current_attempt_id or "") != str(attempt.id):
        attempt.status = "stale" if is_terminal_fence else "lease_expired"
        attempt.finished_at = now
        attempt.lease_reclaimed_at = now
        attempt.row_version = int(attempt.row_version) + 1
        db.flush()
        return True, False, is_terminal_fence

    before = {
        "task_status": str(task.status),
        "attempt_status": str(attempt.status),
        "task_row_version": int(task.row_version),
        "run_status": str(run.status),
    }
    if is_terminal_fence:
        terminal_task_state = "stale" if str(run.status) == "stale" else "cancelled"
        attempt.status = terminal_task_state
        attempt.error_code = "BID_RUN_INPUT_STALE" if terminal_task_state == "stale" else None
        attempt.finished_at = now
        attempt.lease_reclaimed_at = now
        attempt.row_version = int(attempt.row_version) + 1
        task.status = terminal_task_state
        task.row_version = int(task.row_version) + 1
        db.flush()
        event_type = "bid.task.stale.v1" if terminal_task_state == "stale" else None
        event = None
        if event_type is not None:
            event = _append_task_stage_event(
                db,
                task=task,
                run=run,
                event_type=event_type,
                status="stale",
                message="运行输入已失效，旧 fencing token 不再允许写入",
                request_id=f"terminal-fence:{attempt.id}",
                causation_event_id=None,
                occurred_at=now,
                extra={
                    "attempt_id": str(attempt.id),
                    "error_code": "BID_RUN_INPUT_STALE",
                },
            )
        append_audit_log(
            db,
            actor_type="service",
            actor_ref=f"service:{TASK_RUNTIME_PRODUCER}",
            action="task.attempt.terminal_fence",
            entity_type="task_attempt",
            entity_id=str(attempt.id),
            assessment_id=str(run.assessment_id),
            outcome="succeeded",
            request_id=f"terminal-fence:{attempt.id}",
            correlation_id=str(event.event_id) if event is not None else None,
            before=before,
            after={
                "task_status": str(task.status),
                "attempt_status": str(attempt.status),
                "task_row_version": int(task.row_version),
                "run_status": str(run.status),
            },
            occurred_at=now,
        )
        db.flush()
        return True, False, True

    attempt.status = "lease_expired"
    attempt.error_code = "BID_TASK_LEASE_EXPIRED"
    attempt.finished_at = now
    attempt.lease_reclaimed_at = now
    attempt.row_version = int(attempt.row_version) + 1
    automatic_retry = int(attempt.attempt_no) < max(1, int(max_attempts))
    if str(task.status) == "running":
        task.status = "failed"
        task.row_version = int(task.row_version) + 1
    if automatic_retry:
        task.status = "ready"
        task.row_version = int(task.row_version) + 1
    elif str(task.status) in {"leased", "validating"}:
        # These states have a contract-valid path back to ready.  The failed
        # Run gates further leasing until API-43 explicitly resumes it.
        task.status = "ready"
        task.row_version = int(task.row_version) + 1
    db.flush()
    request_id = f"lease-recovery:{attempt.id}"
    failure_event = _append_task_stage_event(
        db,
        task=task,
        run=run,
        event_type="bid.task.failed.v1",
        status="retrying" if automatic_retry else "retry_required",
        message=(
            "任务租约已过期，已创建受控重试资格"
            if automatic_retry
            else "任务租约连续过期，运行等待显式重试"
        ),
        request_id=request_id,
        causation_event_id=None,
        occurred_at=now,
        extra={
            "attempt_id": str(attempt.id),
            "error_code": "BID_TASK_LEASE_EXPIRED",
            "retryable": True,
            "retry_scheduled": automatic_retry,
        },
    )
    correlation_id = str(failure_event.event_id)
    if automatic_retry:
        ready_event = _append_task_stage_event(
            db,
            task=task,
            run=run,
            event_type="bid.task.ready.v1",
            status="ready",
            message="租约恢复已完成，后续必须创建递增 Attempt 与 fencing token",
            request_id=request_id,
            causation_event_id=correlation_id,
            occurred_at=now,
        )
        correlation_id = str(ready_event.event_id)
    else:
        from_state = str(run.status)
        run.status = "failed"
        run.retryable = True
        run.waiting_reason = "BID_TASK_LEASE_EXPIRED"
        run.finished_at = now
        run.row_version = int(run.row_version) + 1
        db.flush()
        run_event = append_outbox_event(
            db,
            event_type="bid.run.failed.v1",
            producer=TASK_RUNTIME_PRODUCER,
            aggregate_type="run",
            aggregate_id=str(run.id),
            aggregate_version=int(run.row_version),
            assessment_id=str(run.assessment_id),
            run_id=str(run.id),
            request_id=request_id,
            causation_event_id=correlation_id,
            payload_schema="bid.run.failed.v1.payload",
            payload={
                "run_id": str(run.id),
                "from": from_state,
                "to": "failed",
                "retryable": True,
                "error_code": "BID_TASK_LEASE_EXPIRED",
                "resource_version": int(run.row_version),
            },
            dedupe_key=f"run-lease-expired:{run.id}:rv{run.row_version}",
            occurred_at=now,
        )
        correlation_id = str(run_event.event_id)
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{TASK_RUNTIME_PRODUCER}",
        action="task.attempt.lease_recover",
        entity_type="task_attempt",
        entity_id=str(attempt.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=request_id,
        correlation_id=correlation_id,
        before=before,
        after={
            "task_status": str(task.status),
            "attempt_status": str(attempt.status),
            "task_row_version": int(task.row_version),
            "run_status": str(run.status),
            "retry_scheduled": automatic_retry,
        },
        occurred_at=now,
    )
    db.flush()
    return True, automatic_retry, False


def maintain_task_runtime(
    *,
    session_factory: Callable[[], Session],
    limit: int = 100,
    max_attempts: int = 3,
    now: datetime | None = None,
) -> TaskRuntimeMaintenanceResult:
    """Recover expired leases and fence Attempts belonging to terminal Runs."""

    scan_db = session_factory()
    try:
        current_time = as_utc(now) if now is not None else database_utc_now(scan_db)
        rows = (
            scan_db.query(BidTaskAttempt.id)
            .join(BidTask, BidTask.id == BidTaskAttempt.task_id)
            .join(BidAnalysisRun, BidAnalysisRun.id == BidTask.run_id)
            .filter(
                BidTaskAttempt.status.in_(tuple(ACTIVE_ATTEMPT_STATES)),
                (
                    (BidTaskAttempt.lease_until.is_(None))
                    | (BidTaskAttempt.lease_until <= current_time)
                    | (BidAnalysisRun.status.in_(tuple(TERMINAL_RUN_STATES)))
                ),
            )
            .order_by(BidTaskAttempt.lease_until.asc(), BidTaskAttempt.id.asc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
        attempt_ids = [str(row[0]) for row in rows]
    finally:
        scan_db.close()

    recovered = retry_scheduled = run_failed = terminal_fenced = failed = 0
    for attempt_id in attempt_ids:
        db = session_factory()
        try:
            with db.begin():
                changed, retry, fenced = _recover_one_attempt(
                    db,
                    attempt_id=attempt_id,
                    max_attempts=max_attempts,
                    now=current_time,
                )
                if changed:
                    recovered += 1
                if retry:
                    retry_scheduled += 1
                if fenced:
                    terminal_fenced += 1
                if changed and not retry and not fenced:
                    run_failed += 1
        except Exception:
            logger.exception(
                "bid_task_runtime_maintenance_attempt_failed",
                extra={"attempt_id": attempt_id},
            )
            failed += 1
        finally:
            db.close()
    return TaskRuntimeMaintenanceResult(
        scanned=len(attempt_ids),
        recovered=recovered,
        retry_scheduled=retry_scheduled,
        run_failed=run_failed,
        terminal_fenced=terminal_fenced,
        failed=failed,
    )
