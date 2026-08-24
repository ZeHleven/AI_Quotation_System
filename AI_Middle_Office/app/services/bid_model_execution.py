"""Phase 4A-2 controlled Model Gateway and provider-attempt executor.

This module persists every model intent before I/O.  It does not register a
provider or call a model by itself; callers must explicitly inject an adapter.
All mutating primitives flush and leave commit/rollback to their caller.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from pydantic import ValidationError
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.agents.bid_assessment_local.contracts import (
    TASK_ACTION_SCHEMA,
    normalize_task_action,
)
from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_config import BidModelProfileVersion, BidPromptBundle
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidAsyncOperation,
    BidCheckpoint,
    BidTask,
    BidTaskAttempt,
)
from app.models.bid_assessment_tooling import BidContextManifest
from app.models.bid_model_execution import (
    BidModelCall,
    BidModelCallAttempt,
    BidModelResult,
)
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    canonical_json,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_task_runtime import (
    TaskLeaseClaim,
    build_task_contract,
    lock_task_claim,
)


MODEL_GATEWAY_PRODUCER = "bid-model-gateway-v1"
MODEL_RESULT_REF_PREFIX = "model-result:"
ACTIVE_MODEL_CALL_STATES = frozenset({"accepted", "leased", "sending", "retry_wait"})
LEASED_MODEL_CALL_STATES = frozenset({"leased", "sending"})
ACTIVE_MODEL_ATTEMPT_STATES = frozenset({"leased", "sending"})
ACTIVE_ASYNC_STATES = frozenset({"created", "submitted", "running"})
MAX_MODEL_ACTION_BYTES = 64 * 1024
MODEL_PROVIDER_ACCOUNTING_SCHEMA = "bid.model.provider-accounting.v1"


class BidModelExecutionError(RuntimeError):
    code = "BID_MODEL_EXECUTION_ERROR"


class BidModelConfigurationInvalid(BidModelExecutionError):
    code = "BID_MODEL_CONFIGURATION_INVALID"


class BidModelCallConflict(BidModelExecutionError):
    code = "BID_MODEL_CALL_CONFLICT"


class BidModelBudgetExhausted(BidModelExecutionError):
    code = "BID_MODEL_BUDGET_EXHAUSTED"


class BidModelFenceLost(BidModelExecutionError):
    code = "BID_MODEL_FENCE_LOST"


class BidModelActionInvalid(BidModelExecutionError):
    code = "BID_MODEL_ACTION_INVALID"

    def __init__(
        self,
        message: str | None = None,
        *,
        validation_issues: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message or self.code)
        self.validation_issues = list(validation_issues or [])


@dataclass(frozen=True)
class ModelProviderResult:
    action: dict[str, Any]
    usage: dict[str, int]
    finish_reason: str
    provider_receipt_id: str | None = None
    actual_cost_microunits: int = 0


@dataclass(frozen=True)
class ModelCallReceipt:
    model_call_id: str
    operation_id: str
    status: str
    request_hash: str
    duplicate: bool


@dataclass(frozen=True)
class ModelCallClaim:
    model_call_id: str
    model_call_attempt_id: str
    worker_id: str
    fencing_token: int
    lease_until: datetime
    provider_ref: str
    model_ref: str
    replay_policy: str
    provider_request_id: str
    request_envelope: dict[str, Any]


@dataclass(frozen=True)
class ModelResultReceipt:
    model_call_id: str
    model_result_id: str
    operation_id: str
    result_hash: str
    action: dict[str, Any]
    duplicate: bool


@dataclass(frozen=True)
class ModelCallMaintenanceResult:
    scanned: int
    recovered: int
    uncertain: int
    failed: int


class ModelProvider(Protocol):
    def execute(
        self,
        *,
        request_envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ModelProviderResult: ...


def _provider_response_accounting(
    provider_result: ModelProviderResult,
) -> dict[str, Any] | None:
    """Return a content-free ledger entry for a response whose spend is known."""

    try:
        raw_usage = dict(provider_result.usage or {})
        if set(raw_usage) != {"input_tokens", "output_tokens"}:
            return None
        input_tokens = int(raw_usage["input_tokens"])
        output_tokens = int(raw_usage["output_tokens"])
        actual_cost_microunits = int(provider_result.actual_cost_microunits)
    except (TypeError, ValueError):
        return None
    if min(input_tokens, output_tokens, actual_cost_microunits) < 0:
        return None
    action = dict(provider_result.action or {})
    receipt = str(provider_result.provider_receipt_id or "")
    return {
        "schema": MODEL_PROVIDER_ACCOUNTING_SCHEMA,
        "provider_response_received": True,
        "action_type": str(action.get("action_type") or "unknown")[:64],
        "action_keys": sorted(str(key)[:128] for key in action)[:32],
        "action_hash": canonical_hash(action),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "actual_cost_microunits": actual_cost_microunits,
        "finish_reason": str(provider_result.finish_reason or "unspecified")[:64],
        "provider_receipt_hash": canonical_hash(receipt) if receipt else None,
    }


def _record_provider_response_accounting(
    call: BidModelCall,
    call_attempt: BidModelCallAttempt,
    provider_result: ModelProviderResult,
) -> dict[str, Any] | None:
    accounting = _provider_response_accounting(provider_result)
    if accounting is None:
        return None
    usage = dict(accounting["usage"])
    call.actual_input_tokens = int(call.actual_input_tokens) + int(
        usage["input_tokens"]
    )
    call.actual_output_tokens = int(call.actual_output_tokens) + int(
        usage["output_tokens"]
    )
    call.actual_cost_microunits = int(call.actual_cost_microunits) + int(
        accounting["actual_cost_microunits"]
    )
    call_attempt.detail_json = accounting
    if provider_result.provider_receipt_id:
        call_attempt.provider_receipt_id = str(
            provider_result.provider_receipt_id
        )[:191]
    return accounting


def resolve_model_route(
    profile: BidModelProfileVersion,
    *,
    role: str,
) -> dict[str, Any]:
    stored_profile_hash = str(profile.artifact_hash or "").lower()
    profile_payload = {
        "role_routing": dict(profile.role_routing_json or {}),
        "provider_identifiers": dict(profile.provider_identifiers_json or {}),
        "model_identifiers": dict(profile.model_identifiers_json or {}),
    }
    if (
        len(stored_profile_hash) != 64
        or any(value not in "0123456789abcdef" for value in stored_profile_hash)
        or canonical_hash(profile_payload) != stored_profile_hash
    ):
        raise BidModelConfigurationInvalid("BID_MODEL_PROFILE_HASH_MISMATCH")
    routes = dict(profile.role_routing_json or {})
    route = routes.get(str(role))
    required = {
        "provider_ref",
        "model_ref",
        "prompt_role",
        "action_schema",
        "replay_policy",
        "max_attempts",
        "timeout_seconds",
        "reserved_cost_microunits",
    }
    if not isinstance(route, dict) or set(route) != required:
        raise BidModelConfigurationInvalid("BID_MODEL_ROLE_ROUTE_INVALID")
    provider_ref = str(route.get("provider_ref") or "")
    model_ref = str(route.get("model_ref") or "")
    providers = dict(profile.provider_identifiers_json or {})
    models = dict(profile.model_identifiers_json or {})
    provider = providers.get(provider_ref)
    model = models.get(model_ref)
    if not isinstance(provider, dict) or not isinstance(model, dict):
        raise BidModelConfigurationInvalid("BID_MODEL_PROVIDER_BINDING_MISSING")
    if str(model.get("provider_ref") or "") != provider_ref:
        raise BidModelConfigurationInvalid("BID_MODEL_PROVIDER_MODEL_MISMATCH")
    replay_policy = str(route.get("replay_policy") or "")
    try:
        max_attempts = int(route.get("max_attempts") or 0)
        timeout_seconds = int(route.get("timeout_seconds") or 0)
        reserved_cost_microunits = int(route.get("reserved_cost_microunits") or 0)
    except (TypeError, ValueError) as exc:
        raise BidModelConfigurationInvalid("BID_MODEL_ROLE_ROUTE_POLICY_INVALID") from exc
    if (
        str(route.get("action_schema") or "") != TASK_ACTION_SCHEMA
        or replay_policy not in {"safe_idempotent", "reconcile_required", "no_replay"}
        or not 1 <= max_attempts <= 5
        or not 30 <= timeout_seconds <= 900
        or not 0 <= reserved_cost_microunits <= 10_000_000_000
        or not str(route.get("prompt_role") or "")
    ):
        raise BidModelConfigurationInvalid("BID_MODEL_ROLE_ROUTE_POLICY_INVALID")
    normalized = {
        "provider_ref": provider_ref,
        "model_ref": model_ref,
        "prompt_role": str(route["prompt_role"]),
        "action_schema": TASK_ACTION_SCHEMA,
        "replay_policy": replay_policy,
        "max_attempts": max_attempts,
        "timeout_seconds": timeout_seconds,
        "reserved_cost_microunits": reserved_cost_microunits,
    }
    normalized["route_hash"] = canonical_hash(
        {
            "logical_role": str(role),
            "route": normalized,
            "provider_binding": provider,
            "model_binding": model,
            "model_profile_hash": stored_profile_hash,
        }
    )
    return normalized


def schedule_model_call(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    context_manifest_id: str,
    checkpoint_id: str,
    action_seq: int,
    idempotency_key: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ModelCallReceipt:
    """Persist a logical model call and yield its Task to an async continuation."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    early_existing = (
        db.query(BidModelCall)
        .filter(BidModelCall.task_id == claim.task_id, BidModelCall.action_seq == int(action_seq))
        .with_for_update()
        .one_or_none()
    )
    if early_existing is not None:
        frozen = dict(early_existing.request_envelope_json or {})
        if (
            str(early_existing.task_attempt_id) != str(claim.attempt_id)
            or int(early_existing.fencing_token) != int(claim.fencing_token)
            or str(early_existing.context_manifest_id) != str(context_manifest_id)
            or str(early_existing.checkpoint_id) != str(checkpoint_id)
            or str(early_existing.idempotency_key) != str(idempotency_key or "")
            or str(frozen.get("task_contract_hash") or "") != str(claim.task_contract_hash)
        ):
            raise BidModelCallConflict("BID_MODEL_ACTION_SEQUENCE_REUSED")
        return ModelCallReceipt(
            model_call_id=str(early_existing.id),
            operation_id=str(early_existing.async_operation_id),
            status=str(early_existing.status),
            request_hash=str(early_existing.request_hash),
            duplicate=True,
        )
    attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    contract = build_task_contract(db, task)
    skill_binding = dict(contract.get("skill_binding") or {})
    if (
        skill_binding.get("executor_kind") != "langgraph"
        or skill_binding.get("action_contract") != TASK_ACTION_SCHEMA
    ):
        raise BidModelConfigurationInvalid("BID_MODEL_TASK_SKILL_BINDING_INVALID")
    normalized_key = str(idempotency_key or "")
    if not 16 <= len(normalized_key) <= 128:
        raise BidModelCallConflict("BID_MODEL_IDEMPOTENCY_KEY_INVALID")
    if int(action_seq) < 1:
        raise BidModelCallConflict("BID_MODEL_ACTION_SEQUENCE_INVALID")

    context = (
        db.query(BidContextManifest)
        .filter(
            BidContextManifest.id == str(context_manifest_id),
            BidContextManifest.task_attempt_id == attempt.id,
            BidContextManifest.task_id == task.id,
            BidContextManifest.run_id == run.id,
        )
        .with_for_update()
        .one_or_none()
    )
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(
            BidCheckpoint.id == str(checkpoint_id),
            BidCheckpoint.task_attempt_id == attempt.id,
            BidCheckpoint.context_manifest_id == str(context_manifest_id),
        )
        .with_for_update()
        .one_or_none()
    )
    state = dict(checkpoint.state_json or {}) if checkpoint is not None else {}
    if (
        context is None
        or checkpoint is None
        or int(context.fencing_token) != int(claim.fencing_token)
        or int(checkpoint.fencing_token) != int(claim.fencing_token)
        or str(checkpoint.next_state or "") != "await_model"
        or int(state.get("action_seq", -1)) != int(action_seq)
        or str(state.get("phase") or "") != "await_model"
        or str(state.get("outstanding_operation_ref") or "")
        != f"model-call:{uuid.uuid5(uuid.NAMESPACE_URL, f'bid-model-call-v1:{task.id}:{int(action_seq)}')}"
    ):
        raise BidModelCallConflict("BID_MODEL_CONTEXT_CHECKPOINT_INVALID")

    profile = (
        db.query(BidModelProfileVersion)
        .filter(BidModelProfileVersion.id == run.model_profile_version_id)
        .one_or_none()
    )
    prompt = (
        db.query(BidPromptBundle)
        .filter(BidPromptBundle.id == run.prompt_bundle_id)
        .one_or_none()
    )
    if profile is None or prompt is None:
        raise BidModelConfigurationInvalid("BID_MODEL_FROZEN_ARTIFACT_MISSING")
    if (
        str(profile.status) != "active"
        or str(profile.active_slot_key or "") != "active"
        or str(prompt.status) != "active"
        or str(prompt.active_slot_key or "") != "active"
    ):
        raise BidModelConfigurationInvalid("BID_MODEL_FROZEN_ARTIFACT_INVALID")
    role = str(context.role)
    route = resolve_model_route(profile, role=role)

    model_call_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"bid-model-call-v1:{task.id}:{int(action_seq)}")
    )
    input_payload = {
        "context_manifest_id": str(context.id),
        "context_manifest_hash": str(context.manifest_hash),
        "checkpoint_id": str(checkpoint.id),
        "checkpoint_state_hash": str(checkpoint.state_hash),
        "task_contract_hash": str(claim.task_contract_hash),
        "model_profile_hash": str(profile.artifact_hash),
        "model_route_hash": str(route["route_hash"]),
        "prompt_bundle_hash": str(prompt.artifact_hash),
    }
    envelope = {
        "schema": "bid.model.request.v1",
        "model_call_id": model_call_id,
        "assessment_id": str(run.assessment_id),
        "run_id": str(run.id),
        "task_id": str(task.id),
        "task_attempt_id": str(attempt.id),
        "fencing_token": int(claim.fencing_token),
        "action_seq": int(action_seq),
        "logical_role": role,
        "provider_ref": route["provider_ref"],
        "model_ref": route["model_ref"],
        "prompt_role": route["prompt_role"],
        "action_schema": route["action_schema"],
        **input_payload,
        "input_token_limit": int(contract["budget"]["max_input_tokens"]),
        "output_token_limit": int(contract["budget"]["max_output_tokens"]),
        "cost_microunits_limit": int(route["reserved_cost_microunits"]),
        "timeout_seconds": int(route["timeout_seconds"]),
    }
    request_hash = canonical_hash(envelope)
    existing = (
        db.query(BidModelCall)
        .filter(BidModelCall.task_id == task.id, BidModelCall.action_seq == int(action_seq))
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        if (
            str(existing.idempotency_key) != normalized_key
            or str(existing.request_hash) != request_hash
        ):
            raise BidModelCallConflict("BID_MODEL_ACTION_SEQUENCE_REUSED")
        return ModelCallReceipt(
            model_call_id=str(existing.id),
            operation_id=str(existing.async_operation_id),
            status=str(existing.status),
            request_hash=str(existing.request_hash),
            duplicate=True,
        )

    used_iterations = int(
        db.query(func.count(BidModelCall.id)).filter(BidModelCall.task_id == task.id).scalar()
        or 0
    )
    max_iterations = int(contract["budget"]["max_iterations"])
    if used_iterations >= max_iterations or int(action_seq) > max_iterations:
        raise BidModelBudgetExhausted(BidModelBudgetExhausted.code)

    operation = BidAsyncOperation(
        id=str(uuid.uuid4()),
        task_id=str(task.id),
        task_attempt_id=str(attempt.id),
        operation_type=f"model:{role}"[:64],
        provider_ref=str(route["provider_ref"]),
        status="created",
        input_hash=request_hash,
        retry_count=0,
        timeout_at=current_time + timedelta(seconds=int(route["timeout_seconds"])),
        row_version=1,
    )
    call = BidModelCall(
        id=model_call_id,
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        task_id=str(task.id),
        task_attempt_id=str(attempt.id),
        checkpoint_id=str(checkpoint.id),
        context_manifest_id=str(context.id),
        async_operation_id=str(operation.id),
        model_profile_version_id=str(profile.id),
        prompt_bundle_id=str(prompt.id),
        action_seq=int(action_seq),
        fencing_token=int(claim.fencing_token),
        logical_role=role,
        provider_ref=str(route["provider_ref"]),
        model_ref=str(route["model_ref"]),
        prompt_role=str(route["prompt_role"]),
        action_schema=TASK_ACTION_SCHEMA,
        replay_policy=str(route["replay_policy"]),
        idempotency_key=normalized_key,
        request_envelope_json=envelope,
        request_hash=request_hash,
        input_hash=canonical_hash(input_payload),
        status="accepted",
        attempt_count=0,
        max_attempts=int(route["max_attempts"]),
        available_at=current_time,
        timeout_at=operation.timeout_at,
        reserved_input_tokens=int(contract["budget"]["max_input_tokens"]),
        reserved_output_tokens=int(contract["budget"]["max_output_tokens"]),
        actual_input_tokens=0,
        actual_output_tokens=0,
        reserved_cost_microunits=int(route["reserved_cost_microunits"]),
        actual_cost_microunits=0,
        accepted_at=current_time,
        row_version=1,
    )
    db.add(operation)
    db.add(call)
    db.flush()

    attempt.status = "waiting_operation"
    attempt.lease_owner = None
    attempt.lease_until = None
    attempt.heartbeat_at = None
    attempt.row_version = int(attempt.row_version) + 1
    task.status = "waiting_operation"
    task.row_version = int(task.row_version) + 1
    run.status = "waiting_operation"
    run.waiting_reason = "model_operation_pending"
    run.current_stage = "waiting_operation"
    run.row_version = int(run.row_version) + 1
    db.flush()
    event = append_outbox_event(
        db,
        event_type="bid.task.waiting_operation.v1",
        producer=MODEL_GATEWAY_PRODUCER,
        aggregate_type="task",
        aggregate_id=str(task.id),
        aggregate_version=int(task.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(request_id or f"model-pending:{call.id}"),
        payload_schema="bid.task.waiting_operation.v1.payload",
        payload={
            "task_id": str(task.id),
            "task_key": str(task.task_key),
            "task_type": str(task.task_type),
            "run_id": str(run.id),
            "plan_revision_id": str(task.plan_revision_id),
            "attempt_id": str(attempt.id),
            "operation_id": str(operation.id),
            "checkpoint_id": str(checkpoint.id),
            "stage_code": "waiting_operation",
            "status": "waiting_operation",
            "message": "Waiting for governed model operation",
            "completed_units": 0,
            "total_units": 0,
            "resource_version": int(run.row_version),
        },
        dedupe_key=f"model-operation-pending:{call.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="model.call.schedule",
        entity_type="model_call",
        entity_id=str(call.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"model-pending:{call.id}"),
        correlation_id=str(event.event_id),
        after={
            "task_id": str(task.id),
            "attempt_id": str(attempt.id),
            "action_seq": int(action_seq),
            "request_hash": request_hash,
            "operation_id": str(operation.id),
        },
        occurred_at=current_time,
    )
    db.flush()
    return ModelCallReceipt(
        model_call_id=str(call.id),
        operation_id=str(operation.id),
        status="accepted",
        request_hash=request_hash,
        duplicate=False,
    )


def claim_model_call(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> ModelCallClaim | None:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    normalized_worker = str(worker_id or "")[:128]
    if not normalized_worker:
        raise BidModelCallConflict("BID_MODEL_WORKER_ID_REQUIRED")
    call = (
        db.query(BidModelCall)
        .join(BidAsyncOperation, BidAsyncOperation.id == BidModelCall.async_operation_id)
        .join(BidTask, BidTask.id == BidModelCall.task_id)
        .join(BidTaskAttempt, BidTaskAttempt.id == BidModelCall.task_attempt_id)
        .join(BidAnalysisRun, BidAnalysisRun.id == BidModelCall.run_id)
        .join(BidAssessment, BidAssessment.id == BidModelCall.assessment_id)
        .filter(
            BidModelCall.status.in_(("accepted", "retry_wait")),
            BidModelCall.available_at <= current_time,
            BidModelCall.timeout_at > current_time,
            BidModelCall.attempt_count < BidModelCall.max_attempts,
            BidAsyncOperation.status.in_(tuple(ACTIVE_ASYNC_STATES)),
            BidTask.status == "waiting_operation",
            BidTask.current_attempt_id == BidTaskAttempt.id,
            BidTaskAttempt.status == "waiting_operation",
            BidAnalysisRun.status == "waiting_operation",
            BidAnalysisRun.cancel_requested_at.is_(None),
            BidAssessment.lifecycle_status == "active",
            BidAssessment.active_run_id == BidAnalysisRun.id,
        )
        .order_by(BidModelCall.available_at.asc(), BidModelCall.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if call is None:
        return None
    attempt_no = int(
        db.query(func.max(BidModelCallAttempt.attempt_no))
        .filter(BidModelCallAttempt.model_call_id == call.id)
        .scalar()
        or 0
    ) + 1
    fencing_token = int(
        db.query(func.max(BidModelCallAttempt.fencing_token))
        .filter(BidModelCallAttempt.model_call_id == call.id)
        .scalar()
        or 0
    ) + 1
    lease_until = min(
        current_time + timedelta(seconds=max(15, min(int(lease_seconds), 300))),
        as_utc(call.timeout_at),
    )
    provider_request_id = f"bid-model:{call.id}:attempt:{attempt_no}"
    execution_key = canonical_hash(
        {
            "model_call_id": str(call.id),
            "attempt_no": attempt_no,
            "fencing_token": fencing_token,
            "provider_request_id": provider_request_id,
        }
    )
    call_attempt = BidModelCallAttempt(
        id=str(uuid.uuid4()),
        model_call_id=str(call.id),
        attempt_no=attempt_no,
        fencing_token=fencing_token,
        worker_id=normalized_worker,
        status="leased",
        execution_key=execution_key,
        provider_request_id=provider_request_id,
        lease_until=lease_until,
        started_at=current_time,
        heartbeat_at=current_time,
    )
    db.add(call_attempt)
    call.status = "leased"
    call.attempt_count = attempt_no
    call.lease_owner = normalized_worker
    call.lease_until = lease_until
    call.started_at = call.started_at or current_time
    call.row_version = int(call.row_version) + 1
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == call.async_operation_id)
        .with_for_update()
        .one()
    )
    operation.status = "submitted"
    operation.submitted_at = operation.submitted_at or current_time
    operation.row_version = int(operation.row_version) + 1
    db.flush()
    return ModelCallClaim(
        model_call_id=str(call.id),
        model_call_attempt_id=str(call_attempt.id),
        worker_id=normalized_worker,
        fencing_token=fencing_token,
        lease_until=lease_until,
        provider_ref=str(call.provider_ref),
        model_ref=str(call.model_ref),
        replay_policy=str(call.replay_policy),
        provider_request_id=provider_request_id,
        request_envelope=dict(call.request_envelope_json or {}),
    )


def _lock_model_claim(
    db: Session,
    claim: ModelCallClaim,
    *,
    now: datetime,
) -> tuple[BidModelCall, BidModelCallAttempt]:
    call = (
        db.query(BidModelCall)
        .filter(BidModelCall.id == claim.model_call_id)
        .with_for_update()
        .one_or_none()
    )
    attempt = (
        db.query(BidModelCallAttempt)
        .filter(BidModelCallAttempt.id == claim.model_call_attempt_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        call is None
        or attempt is None
        or str(attempt.model_call_id) != str(call.id)
        or str(call.status) not in LEASED_MODEL_CALL_STATES
        or str(attempt.status) not in ACTIVE_MODEL_ATTEMPT_STATES
        or str(call.lease_owner or "") != str(claim.worker_id)
        or str(attempt.worker_id) != str(claim.worker_id)
        or int(attempt.fencing_token) != int(claim.fencing_token)
        or str(attempt.provider_request_id) != str(claim.provider_request_id)
        or str(call.provider_ref) != str(claim.provider_ref)
        or str(call.model_ref) != str(claim.model_ref)
        or str(call.replay_policy) != str(claim.replay_policy)
        or canonical_hash(dict(claim.request_envelope or {})) != str(call.request_hash)
        or as_utc(call.timeout_at) <= now
        or as_utc(attempt.lease_until) <= now
        or call.lease_until is None
        or as_utc(call.lease_until) <= now
    ):
        raise BidModelFenceLost(BidModelFenceLost.code)
    return call, attempt


def heartbeat_model_call(
    db: Session,
    claim: ModelCallClaim,
    *,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> datetime:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    call, attempt = _lock_model_claim(db, claim, now=current_time)
    lease_until = min(
        current_time + timedelta(seconds=max(15, min(int(lease_seconds), 300))),
        as_utc(call.timeout_at),
    )
    call.lease_until = lease_until
    call.row_version = int(call.row_version) + 1
    attempt.lease_until = lease_until
    attempt.heartbeat_at = current_time
    db.flush()
    return lease_until


def mark_model_call_sending(
    db: Session,
    claim: ModelCallClaim,
    *,
    now: datetime | None = None,
) -> None:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    call, attempt = _lock_model_claim(db, claim, now=current_time)
    call.status = "sending"
    call.row_version = int(call.row_version) + 1
    attempt.status = "sending"
    attempt.send_started_at = current_time
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == call.async_operation_id)
        .with_for_update()
        .one()
    )
    operation.status = "running"
    operation.started_at = operation.started_at or current_time
    operation.row_version = int(operation.row_version) + 1
    db.flush()


def _resume_task_after_model_operation(
    db: Session,
    *,
    call: BidModelCall,
    operation: BidAsyncOperation,
    task: BidTask,
    task_attempt: BidTaskAttempt,
    run: BidAnalysisRun,
    current_time: datetime,
    message: str,
    dedupe_suffix: str,
    request_id: str,
) -> Any:
    task_attempt.status = "cancelled"
    task_attempt.error_code = "BID_MODEL_OPERATION_CONTINUATION_TRANSFERRED"
    task_attempt.finished_at = current_time
    task_attempt.row_version = int(task_attempt.row_version) + 1
    task.status = "ready"
    task.current_attempt_id = None
    task.row_version = int(task.row_version) + 1
    run.status = "queued"
    run.waiting_reason = None
    run.current_stage = "task_execution"
    run.row_version = int(run.row_version) + 1
    db.flush()
    return append_outbox_event(
        db,
        event_type="bid.task.ready.v1",
        producer=MODEL_GATEWAY_PRODUCER,
        aggregate_type="task",
        aggregate_id=str(task.id),
        aggregate_version=int(task.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=request_id,
        payload_schema="bid.task.ready.v1.payload",
        payload={
            "task_id": str(task.id),
            "task_key": str(task.task_key),
            "task_type": str(task.task_type),
            "run_id": str(run.id),
            "plan_revision_id": str(task.plan_revision_id),
            "stage_code": "task_execution",
            "status": "ready",
            "message": message,
            "completed_units": 0,
            "total_units": 0,
            "resource_version": int(run.row_version),
        },
        dedupe_key=f"model-operation-ready:{call.id}:{dedupe_suffix}",
        occurred_at=current_time,
    )


def settle_model_call(
    db: Session,
    claim: ModelCallClaim,
    *,
    provider_result: ModelProviderResult,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ModelResultReceipt:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    terminal_call = (
        db.query(BidModelCall)
        .filter(BidModelCall.id == claim.model_call_id)
        .with_for_update()
        .one_or_none()
    )
    if terminal_call is not None and str(terminal_call.status) == "succeeded":
        terminal_attempt = (
            db.query(BidModelCallAttempt)
            .filter(BidModelCallAttempt.id == claim.model_call_attempt_id)
            .one_or_none()
        )
        terminal_result = (
            db.query(BidModelResult)
            .filter(BidModelResult.model_call_id == terminal_call.id)
            .one_or_none()
        )
        if (
            terminal_attempt is None
            or terminal_result is None
            or str(terminal_attempt.model_call_id) != str(terminal_call.id)
            or str(terminal_result.model_call_attempt_id) != str(terminal_attempt.id)
            or str(terminal_attempt.worker_id) != str(claim.worker_id)
            or int(terminal_attempt.fencing_token) != int(claim.fencing_token)
            or str(terminal_attempt.provider_request_id) != str(claim.provider_request_id)
        ):
            raise BidModelFenceLost(BidModelFenceLost.code)
        try:
            replay_action = normalize_task_action(dict(provider_result.action or {}))
            replay_usage_raw = dict(provider_result.usage or {})
            if set(replay_usage_raw) != {"input_tokens", "output_tokens"}:
                raise ValueError("usage shape")
            replay_usage = {
                str(key): int(value) for key, value in replay_usage_raw.items()
            }
        except (ValidationError, TypeError, ValueError) as exc:
            raise BidModelCallConflict("BID_MODEL_RESULT_REPLAY_MISMATCH") from exc
        try:
            replay_cost = int(provider_result.actual_cost_microunits)
        except (TypeError, ValueError) as exc:
            raise BidModelCallConflict("BID_MODEL_RESULT_REPLAY_MISMATCH") from exc
        replay_response = {
            "action_hash": canonical_hash(replay_action),
            "usage": replay_usage,
            "finish_reason": str(provider_result.finish_reason or "unspecified")[:64],
            "provider_receipt_id": (
                str(provider_result.provider_receipt_id)[:191]
                if provider_result.provider_receipt_id
                else None
            ),
            "actual_cost_microunits": replay_cost,
        }
        if (
            canonical_hash(replay_action) != str(terminal_result.action_hash)
            or canonical_hash(replay_response) != str(terminal_result.response_hash)
        ):
            raise BidModelCallConflict("BID_MODEL_RESULT_REPLAY_MISMATCH")
        return ModelResultReceipt(
            model_call_id=str(terminal_call.id),
            model_result_id=str(terminal_result.id),
            operation_id=str(terminal_call.async_operation_id),
            result_hash=str(terminal_result.result_hash),
            action=dict(terminal_result.action_json or {}),
            duplicate=True,
        )
    call, call_attempt = _lock_model_claim(db, claim, now=current_time)
    task = db.query(BidTask).filter(BidTask.id == call.task_id).with_for_update().one()
    task_attempt = (
        db.query(BidTaskAttempt)
        .filter(BidTaskAttempt.id == call.task_attempt_id)
        .with_for_update()
        .one()
    )
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == call.run_id)
        .with_for_update()
        .one()
    )
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == call.assessment_id)
        .with_for_update()
        .one()
    )
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == call.async_operation_id)
        .with_for_update()
        .one()
    )
    try:
        actual_cost_microunits = int(provider_result.actual_cost_microunits)
    except (TypeError, ValueError) as exc:
        raise BidModelBudgetExhausted("BID_MODEL_COST_BUDGET_INVALID") from exc
    if (
        str(task.status) != "waiting_operation"
        or str(task.current_attempt_id or "") != str(task_attempt.id)
        or str(task_attempt.status) != "waiting_operation"
        or int(task_attempt.fencing_token) != int(call.fencing_token)
        or str(run.status) != "waiting_operation"
        or run.cancel_requested_at is not None
        or str(assessment.lifecycle_status) != "active"
        or str(assessment.active_run_id or "") != str(run.id)
        or str(operation.status) not in ACTIVE_ASYNC_STATES
    ):
        raise BidModelFenceLost("BID_MODEL_RESULT_TASK_FENCE_LOST")
    try:
        action = normalize_task_action(
            dict(provider_result.action or {}),
            allowed_tools=set(build_task_contract(db, task)["allowed_tools"]),
        )
        action_text = canonical_json(action)
    except ValidationError as exc:
        issues = [
            {
                "loc": [str(value)[:128] for value in error.get("loc", ())],
                "type": str(error.get("type") or "validation_error")[:128],
                "message": str(error.get("msg") or "validation error")[:200],
            }
            for error in exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )[:20]
        ]
        raise BidModelActionInvalid(
            BidModelActionInvalid.code,
            validation_issues=issues,
        ) from exc
    except (TypeError, ValueError) as exc:
        raise BidModelActionInvalid(
            BidModelActionInvalid.code,
            validation_issues=[{"loc": [], "type": type(exc).__name__[:128]}],
        ) from exc
    if len(action_text.encode("utf-8")) > MAX_MODEL_ACTION_BYTES:
        raise BidModelActionInvalid("BID_MODEL_ACTION_TOO_LARGE")
    raw_usage = dict(provider_result.usage or {})
    if set(raw_usage) != {"input_tokens", "output_tokens"}:
        raise BidModelBudgetExhausted("BID_MODEL_USAGE_INVALID")
    try:
        usage = {str(key): int(value) for key, value in raw_usage.items()}
    except (TypeError, ValueError) as exc:
        raise BidModelBudgetExhausted("BID_MODEL_USAGE_INVALID") from exc
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    total_input_tokens = int(call.actual_input_tokens) + input_tokens
    total_output_tokens = int(call.actual_output_tokens) + output_tokens
    total_cost_microunits = (
        int(call.actual_cost_microunits) + actual_cost_microunits
    )
    if (
        input_tokens < 0
        or output_tokens < 0
        or total_input_tokens > int(call.reserved_input_tokens)
        or total_output_tokens > int(call.reserved_output_tokens)
        or actual_cost_microunits < 0
        or total_cost_microunits > int(call.reserved_cost_microunits)
    ):
        raise BidModelBudgetExhausted("BID_MODEL_TOKEN_BUDGET_EXCEEDED")
    action_hash = canonical_hash(action)
    normalized_finish_reason = str(provider_result.finish_reason or "unspecified")[:64]
    response_payload = {
        "action_hash": action_hash,
        "usage": usage,
        "finish_reason": normalized_finish_reason,
        "provider_receipt_id": (
            str(provider_result.provider_receipt_id)[:191]
            if provider_result.provider_receipt_id
            else None
        ),
        "actual_cost_microunits": actual_cost_microunits,
    }
    response_hash = canonical_hash(response_payload)
    result_hash = canonical_hash(
        {
            "model_call_id": str(call.id),
            "model_call_attempt_id": str(call_attempt.id),
            "action_hash": action_hash,
            "response_hash": response_hash,
        }
    )
    result = BidModelResult(
        id=str(uuid.uuid4()),
        model_call_id=str(call.id),
        model_call_attempt_id=str(call_attempt.id),
        task_id=str(task.id),
        source_task_attempt_id=str(task_attempt.id),
        action_type=str(action["action_type"]),
        storage_kind="inline",
        action_json=json.loads(action_text),
        action_hash=action_hash,
        response_hash=response_hash,
        usage_json=usage,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        actual_cost_microunits=actual_cost_microunits,
        finish_reason=normalized_finish_reason,
        provider_receipt_id=response_payload["provider_receipt_id"],
        result_hash=result_hash,
        created_at=current_time,
    )
    db.add(result)
    accounting = _record_provider_response_accounting(
        call,
        call_attempt,
        provider_result,
    )
    if accounting is None:
        raise BidModelBudgetExhausted("BID_MODEL_USAGE_INVALID")
    accounting = {
        **accounting,
        "action_type": str(action["action_type"]),
        # Successful results are authoritative only after Pydantic has applied
        # defaults and produced the canonical closed action.
        "action_hash": action_hash,
    }
    call_attempt.detail_json = accounting
    call.status = "succeeded"
    call.lease_owner = None
    call.lease_until = None
    call.completed_at = current_time
    call.row_version = int(call.row_version) + 1
    call_attempt.status = "succeeded"
    call_attempt.provider_receipt_id = response_payload["provider_receipt_id"]
    call_attempt.finished_at = current_time
    call_attempt.outcome_hash = result_hash
    operation.status = "succeeded"
    operation.result_ref = f"{MODEL_RESULT_REF_PREFIX}{result.id}"
    operation.finished_at = current_time
    operation.row_version = int(operation.row_version) + 1
    event = _resume_task_after_model_operation(
        db,
        call=call,
        operation=operation,
        task=task,
        task_attempt=task_attempt,
        run=run,
        current_time=current_time,
        message="Governed model operation completed; task may resume",
        dedupe_suffix=result_hash,
        request_id=str(request_id or f"model-result:{result.id}"),
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="model.call.settle",
        entity_type="model_call",
        entity_id=str(call.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"model-result:{result.id}"),
        correlation_id=str(event.event_id),
        after={
            "result_id": str(result.id),
            "result_hash": result_hash,
            "action_type": str(action["action_type"]),
            "previous_task_attempt_id": str(task_attempt.id),
        },
        occurred_at=current_time,
    )
    db.flush()
    return ModelResultReceipt(
        model_call_id=str(call.id),
        model_result_id=str(result.id),
        operation_id=str(operation.id),
        result_hash=result_hash,
        action=action,
        duplicate=False,
    )


def fail_model_call_attempt(
    db: Session,
    claim: ModelCallClaim,
    *,
    error_code: str,
    retryable: bool,
    send_started: bool,
    retry_delay_seconds: int = 5,
    request_id: str | None = None,
    now: datetime | None = None,
    provider_result: ModelProviderResult | None = None,
    validation_issues: list[dict[str, Any]] | None = None,
) -> str:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    call, call_attempt = _lock_model_claim(db, claim, now=current_time)
    normalized_error = str(error_code or BidModelExecutionError.code)[:100]
    was_uncertain = bool(send_started)
    accounting = (
        _record_provider_response_accounting(call, call_attempt, provider_result)
        if provider_result is not None
        else None
    )
    if accounting is not None and validation_issues:
        accounting = {
            **accounting,
            "validation_issues": list(validation_issues)[:20],
        }
        call_attempt.detail_json = accounting
    call_attempt.status = "uncertain" if was_uncertain else "failed"
    call_attempt.error_code = normalized_error
    call_attempt.finished_at = current_time
    call_attempt.outcome_hash = canonical_hash(
        {
            "model_call_id": str(call.id),
            "attempt_id": str(call_attempt.id),
            "error_code": normalized_error,
            "send_started": was_uncertain,
            "provider_accounting": accounting,
        }
    )
    can_retry = (
        bool(retryable)
        and int(call.attempt_count) < int(call.max_attempts)
        and as_utc(call.timeout_at) > current_time
        and (not was_uncertain or str(call.replay_policy) == "safe_idempotent")
        and str(call.replay_policy) != "no_replay"
        and int(call.actual_input_tokens) < int(call.reserved_input_tokens)
        and int(call.actual_output_tokens) < int(call.reserved_output_tokens)
        and int(call.actual_cost_microunits) <= int(call.reserved_cost_microunits)
    )
    call.lease_owner = None
    call.lease_until = None
    call.last_error_code = normalized_error
    call.row_version = int(call.row_version) + 1
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == call.async_operation_id)
        .with_for_update()
        .one()
    )
    if can_retry:
        call.status = "retry_wait"
        call.available_at = current_time + timedelta(
            seconds=max(1, min(int(retry_delay_seconds), 300))
        )
        operation.status = "submitted"
        operation.retry_count = int(operation.retry_count) + 1
        operation.error_code = normalized_error
        operation.row_version = int(operation.row_version) + 1
        db.flush()
        return "retry_wait"

    call.status = "uncertain" if was_uncertain else "failed"
    call.completed_at = current_time
    operation.status = "failed"
    operation.error_code = normalized_error
    operation.finished_at = current_time
    operation.row_version = int(operation.row_version) + 1
    task = db.query(BidTask).filter(BidTask.id == call.task_id).with_for_update().one()
    task_attempt = (
        db.query(BidTaskAttempt)
        .filter(BidTaskAttempt.id == call.task_attempt_id)
        .with_for_update()
        .one()
    )
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == call.run_id)
        .with_for_update()
        .one()
    )
    event = _resume_task_after_model_operation(
        db,
        call=call,
        operation=operation,
        task=task,
        task_attempt=task_attempt,
        run=run,
        current_time=current_time,
        message="Governed model operation stopped; task resumed for deterministic recovery",
        dedupe_suffix=call_attempt.outcome_hash,
        request_id=str(request_id or f"model-failure:{call_attempt.id}"),
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="model.call.fail",
        entity_type="model_call",
        entity_id=str(call.id),
        assessment_id=str(run.assessment_id),
        outcome="failed",
        request_id=str(request_id or f"model-failure:{call_attempt.id}"),
        correlation_id=str(event.event_id),
        after={"status": str(call.status), "error_code": normalized_error},
        occurred_at=current_time,
    )
    db.flush()
    return str(call.status)


def recover_expired_model_calls(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> ModelCallMaintenanceResult:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    rows = (
        db.query(BidModelCallAttempt)
        .join(BidModelCall, BidModelCall.id == BidModelCallAttempt.model_call_id)
        .filter(
            BidModelCallAttempt.status.in_(("leased", "sending")),
            or_(
                BidModelCallAttempt.lease_until <= current_time,
                BidModelCall.timeout_at <= current_time,
            ),
        )
        .order_by(BidModelCallAttempt.lease_until.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    recovered = uncertain = failed = 0
    for attempt in rows:
        call = (
            db.query(BidModelCall)
            .filter(BidModelCall.id == attempt.model_call_id)
            .with_for_update()
            .one()
        )
        if str(call.status) not in LEASED_MODEL_CALL_STATES:
            continue
        sent = str(attempt.status) == "sending" or attempt.send_started_at is not None
        attempt.status = "uncertain" if sent else "lease_expired"
        attempt.error_code = "BID_MODEL_SEND_OUTCOME_UNKNOWN" if sent else "BID_MODEL_LEASE_EXPIRED"
        attempt.finished_at = current_time
        attempt.outcome_hash = canonical_hash(
            {"attempt_id": str(attempt.id), "status": str(attempt.status)}
        )
        can_retry = (
            int(call.attempt_count) < int(call.max_attempts)
            and as_utc(call.timeout_at) > current_time
            and (not sent or str(call.replay_policy) == "safe_idempotent")
            and str(call.replay_policy) != "no_replay"
        )
        call.lease_owner = None
        call.lease_until = None
        call.last_error_code = str(attempt.error_code)
        call.row_version = int(call.row_version) + 1
        if can_retry:
            call.status = "retry_wait"
            call.available_at = current_time
            recovered += 1
            if sent:
                uncertain += 1
        else:
            call.status = "uncertain" if sent else "failed"
            call.completed_at = current_time
            failed += 1
            if sent:
                uncertain += 1
            operation = (
                db.query(BidAsyncOperation)
                .filter(BidAsyncOperation.id == call.async_operation_id)
                .with_for_update()
                .one()
            )
            operation.status = "failed"
            operation.error_code = str(attempt.error_code)
            operation.finished_at = current_time
            operation.row_version = int(operation.row_version) + 1
            task = (
                db.query(BidTask)
                .filter(BidTask.id == call.task_id)
                .with_for_update()
                .one()
            )
            task_attempt = (
                db.query(BidTaskAttempt)
                .filter(BidTaskAttempt.id == call.task_attempt_id)
                .with_for_update()
                .one()
            )
            run = (
                db.query(BidAnalysisRun)
                .filter(BidAnalysisRun.id == call.run_id)
                .with_for_update()
                .one()
            )
            if (
                str(task.status) == "waiting_operation"
                and str(task.current_attempt_id or "") == str(task_attempt.id)
                and str(task_attempt.status) == "waiting_operation"
                and str(run.status) == "waiting_operation"
                and run.cancel_requested_at is None
            ):
                _resume_task_after_model_operation(
                    db,
                    call=call,
                    operation=operation,
                    task=task,
                    task_attempt=task_attempt,
                    run=run,
                    current_time=current_time,
                    message=(
                        "Governed model operation expired; task resumed for "
                        "deterministic recovery"
                    ),
                    dedupe_suffix=str(attempt.outcome_hash),
                    request_id=f"model-expired:{attempt.id}",
                )
    unclaimed_calls = (
        db.query(BidModelCall)
        .filter(
            BidModelCall.status.in_(("accepted", "retry_wait")),
            BidModelCall.timeout_at <= current_time,
        )
        .order_by(BidModelCall.timeout_at.asc(), BidModelCall.id.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    for call in unclaimed_calls:
        call.status = "failed"
        call.last_error_code = "BID_MODEL_OPERATION_TIMEOUT"
        call.completed_at = current_time
        call.row_version = int(call.row_version) + 1
        operation = (
            db.query(BidAsyncOperation)
            .filter(BidAsyncOperation.id == call.async_operation_id)
            .with_for_update()
            .one()
        )
        operation.status = "failed"
        operation.error_code = "BID_MODEL_OPERATION_TIMEOUT"
        operation.finished_at = current_time
        operation.row_version = int(operation.row_version) + 1
        task = db.query(BidTask).filter(BidTask.id == call.task_id).with_for_update().one()
        task_attempt = (
            db.query(BidTaskAttempt)
            .filter(BidTaskAttempt.id == call.task_attempt_id)
            .with_for_update()
            .one()
        )
        run = (
            db.query(BidAnalysisRun)
            .filter(BidAnalysisRun.id == call.run_id)
            .with_for_update()
            .one()
        )
        event = None
        if (
            str(task.status) == "waiting_operation"
            and str(task.current_attempt_id or "") == str(task_attempt.id)
            and str(task_attempt.status) == "waiting_operation"
            and str(run.status) == "waiting_operation"
            and run.cancel_requested_at is None
        ):
            event = _resume_task_after_model_operation(
                db,
                call=call,
                operation=operation,
                task=task,
                task_attempt=task_attempt,
                run=run,
                current_time=current_time,
                message="Governed model operation timed out before Provider lease",
                dedupe_suffix="unclaimed-timeout",
                request_id=f"model-timeout:{call.id}",
            )
        append_audit_log(
            db,
            actor_type="service",
            actor_ref=f"service:{MODEL_GATEWAY_PRODUCER}",
            action="model.call.timeout",
            entity_type="model_call",
            entity_id=str(call.id),
            assessment_id=str(call.assessment_id),
            outcome="failed",
            request_id=f"model-timeout:{call.id}",
            correlation_id=str(event.event_id) if event is not None else None,
            after={
                "status": "failed",
                "error_code": "BID_MODEL_OPERATION_TIMEOUT",
                "attempt_count": int(call.attempt_count),
            },
            occurred_at=current_time,
        )
        failed += 1
    db.flush()
    return ModelCallMaintenanceResult(
        scanned=len(rows) + len(unclaimed_calls),
        recovered=recovered,
        uncertain=uncertain,
        failed=failed,
    )


def execute_model_call_claim(
    session_factory: Callable[[], Session],
    *,
    claim: ModelCallClaim,
    provider: ModelProvider,
) -> ModelResultReceipt | str:
    """Execute one already-persisted claim; Provider I/O is outside transactions."""

    sending_started = False
    with session_factory() as db:
        try:
            mark_model_call_sending(db, claim)
            db.commit()
            sending_started = True
        except Exception:
            db.rollback()
            raise
    try:
        provider_result = provider.execute(
            request_envelope=dict(claim.request_envelope),
            provider_request_id=str(claim.provider_request_id),
        )
    except Exception as exc:
        raw_code = str(getattr(exc, "code", "") or "")
        code = (
            raw_code[:100]
            if raw_code.startswith("BID_") and raw_code.replace("_", "").isalnum()
            else "BID_MODEL_PROVIDER_ERROR"
        )
        with session_factory() as db:
            try:
                status = fail_model_call_attempt(
                    db,
                    claim,
                    error_code=code,
                    retryable=bool(getattr(exc, "retryable", True)),
                    send_started=sending_started,
                )
                db.commit()
                return status
            except Exception:
                db.rollback()
                raise
    with session_factory() as db:
        try:
            receipt = settle_model_call(db, claim, provider_result=provider_result)
            db.commit()
            return receipt
        except (BidModelActionInvalid, BidModelBudgetExhausted) as exc:
            db.rollback()
            with session_factory() as recovery_db:
                status = fail_model_call_attempt(
                    recovery_db,
                    claim,
                    error_code=str(getattr(exc, "code", BidModelActionInvalid.code)),
                    retryable=True,
                    # A provider response was received and rejected locally;
                    # the outcome is known, so this is not a send-unknown case.
                    send_started=False,
                    provider_result=provider_result,
                    validation_issues=list(
                        getattr(exc, "validation_issues", []) or []
                    ),
                )
                recovery_db.commit()
                return status
        except Exception:
            db.rollback()
            raise
