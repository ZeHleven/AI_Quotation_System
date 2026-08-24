"""Single-Task, single-action LangGraph orchestration for Phase 4A-2.

LangGraph evaluates a pure bounded transition.  This service owns the
authoritative Context/Checkpoint/Model/Tool transactions around that action.
It never calls a model or a tool adapter directly.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.bid_assessment_local.contracts import (
    LOCAL_AGENT_STATE_SCHEMA,
    normalize_local_agent_state,
)
from app.agents.bid_assessment_local.graph import run_bounded_transition
from app.models.bid_assessment_runtime import BidCheckpoint, BidTask
from app.models.bid_assessment_tooling import BidToolInvocation, BidToolResult
from app.models.bid_model_execution import BidModelCall, BidModelResult
from app.services.bid_assessment_eventing import as_utc, canonical_hash
from app.services.bid_model_execution import (
    MODEL_RESULT_REF_PREFIX,
    schedule_model_call,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_task_runtime import (
    TaskLeaseClaim,
    lock_task_claim,
    write_task_checkpoint,
)
from app.services.bid_tool_context import (
    assemble_context_manifest,
    authorize_tool_invocation,
)
from app.services.bid_tool_execution import enqueue_tool_dispatch


LOCAL_AGENT_EXECUTOR_VERSION = "bid-bounded-langgraph-executor-v1"


class BidLocalAgentError(RuntimeError):
    code = "BID_LOCAL_AGENT_ERROR"


class BidLocalAgentStateInvalid(BidLocalAgentError):
    code = "BID_LOCAL_AGENT_STATE_INVALID"


@dataclass(frozen=True)
class LocalAgentStepReceipt:
    task_id: str
    attempt_id: str
    action_seq: int
    operation_type: str
    checkpoint_id: str | None
    operation_ref: str | None
    candidate_ref: str | None
    completion_ready: bool
    requires_failure: bool


def _initial_state(claim: TaskLeaseClaim, *, run_id: str) -> dict[str, Any]:
    binding = dict(claim.task_contract.get("skill_binding") or {})
    if (
        binding.get("executor_kind") != "langgraph"
        or binding.get("action_contract") != "bid.task.action.v1"
    ):
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_SKILL_BINDING_INVALID")
    return normalize_local_agent_state(
        {
            "schema": LOCAL_AGENT_STATE_SCHEMA,
            "run_id": str(run_id),
            "task_id": str(claim.task_id),
            "task_attempt_id": str(claim.attempt_id),
            "fencing_token": int(claim.fencing_token),
            "task_contract_hash": str(claim.task_contract_hash),
            "skill_binding_hash": canonical_hash(binding),
            "phase": "hydrate",
            "action_seq": 0,
            "observed_model_result_refs": [],
            "observed_tool_result_refs": [],
            "candidate_refs": [],
            "missing_slots": [],
            "outstanding_operation_ref": None,
            "stop_reason": None,
        }
    )


def _load_state(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    run_id: str,
) -> dict[str, Any]:
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.task_attempt_id == str(claim.attempt_id))
        .order_by(BidCheckpoint.action_seq.desc(), BidCheckpoint.created_at.desc())
        .first()
    )
    if checkpoint is None and claim.resume_checkpoint:
        checkpoint = (
            db.query(BidCheckpoint)
            .filter(BidCheckpoint.id == str(claim.resume_checkpoint["checkpoint_id"]))
            .one_or_none()
        )
    if checkpoint is None:
        return _initial_state(claim, run_id=run_id)
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.id == str(checkpoint.id))
        .one_or_none()
    )
    if (
        checkpoint is None
        or canonical_hash(checkpoint.state_json) != str(checkpoint.state_hash)
    ):
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_RESUME_CHECKPOINT_INVALID")
    value = dict(checkpoint.state_json or {})
    if value.get("schema") != LOCAL_AGENT_STATE_SCHEMA:
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_RESUME_SCHEMA_INVALID")
    state = normalize_local_agent_state(value)
    binding_hash = canonical_hash(dict(claim.task_contract.get("skill_binding") or {}))
    if (
        str(state["run_id"]) != str(run_id)
        or str(state["task_id"]) != str(claim.task_id)
        or str(state["task_contract_hash"]) != str(claim.task_contract_hash)
        or str(state["skill_binding_hash"]) != binding_hash
    ):
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_RESUME_LINEAGE_INVALID")
    # Continuation always transfers to the current Attempt/Fence.  Historical
    # attempt identity remains immutable in the source Checkpoint itself.  Keep
    # the wait phase until the authority row is explicitly resolved below; a
    # ready/stopped state remains stable so duplicates cannot schedule I/O.
    source_phase = str(state["phase"])
    state.update(
        {
            "task_attempt_id": str(claim.attempt_id),
            "fencing_token": int(claim.fencing_token),
            "phase": source_phase,
            "stop_reason": (
                str(state["stop_reason"])
                if source_phase == "stopped" and state.get("stop_reason")
                else None
            ),
        }
    )
    return normalize_local_agent_state(state)


def _next_checkpoint_seq(db: Session, *, attempt_id: str) -> int:
    row = (
        db.query(BidCheckpoint.action_seq)
        .filter(BidCheckpoint.task_attempt_id == str(attempt_id))
        .order_by(BidCheckpoint.action_seq.desc())
        .first()
    )
    return int(row[0]) + 1 if row is not None else 0


def _ready_receipt(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    task: BidTask,
    state: dict[str, Any],
    request_id: str | None,
    now: datetime,
) -> LocalAgentStepReceipt:
    """Project an already-ready action without advancing the graph again."""

    candidate_ref = (
        str(state["candidate_refs"][-1]) if state.get("candidate_refs") else None
    )
    operation_type = str(state["phase"])
    if (
        str(state["phase"]) != "stopped"
        and candidate_ref
        and candidate_ref.startswith(MODEL_RESULT_REF_PREFIX)
    ):
        result = (
            db.query(BidModelResult)
            .filter(BidModelResult.id == candidate_ref.split(":", 1)[1])
            .one_or_none()
        )
        if result is None:
            raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_READY_RESULT_MISSING")
        operation_type = str(result.action_type)
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(BidCheckpoint.task_attempt_id == str(claim.attempt_id))
        .order_by(BidCheckpoint.action_seq.desc(), BidCheckpoint.created_at.desc())
        .first()
    )
    if checkpoint is None or str(checkpoint.next_state or "") != str(state["phase"]):
        checkpoint = write_task_checkpoint(
            db,
            claim,
            action_seq=_next_checkpoint_seq(db, attempt_id=str(claim.attempt_id)),
            state=state,
            candidate_output_ref=candidate_ref,
            next_state=str(state["phase"]),
            request_id=request_id,
            now=now,
        )
        checkpoint_id = checkpoint.checkpoint_id
    else:
        checkpoint_id = str(checkpoint.id)
    return LocalAgentStepReceipt(
        task_id=str(task.id),
        attempt_id=str(claim.attempt_id),
        action_seq=int(state["action_seq"]),
        operation_type=operation_type,
        checkpoint_id=checkpoint_id,
        operation_ref=None,
        candidate_ref=candidate_ref,
        completion_ready=operation_type == "finish",
        requires_failure=str(state["phase"]) == "stopped",
    )


def _resolve_outstanding(
    db: Session,
    state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    ref = str(state.get("outstanding_operation_ref") or "")
    if not ref:
        return state, None, None
    if ref.startswith("model-call:"):
        call_id = ref.split(":", 1)[1]
        call = db.query(BidModelCall).filter(BidModelCall.id == call_id).one_or_none()
        if call is None or str(call.task_id) != str(state["task_id"]):
            raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_MODEL_CALL_OUT_OF_SCOPE")
        result = (
            db.query(BidModelResult)
            .filter(BidModelResult.model_call_id == call.id)
            .one_or_none()
        )
        if result is not None:
            result_ref = f"{MODEL_RESULT_REF_PREFIX}{result.id}"
            observed = list(state["observed_model_result_refs"])
            if result_ref not in observed:
                observed.append(result_ref)
            state.update(
                {
                    "phase": "hydrate",
                    "observed_model_result_refs": observed,
                    "outstanding_operation_ref": None,
                }
            )
            return normalize_local_agent_state(state), dict(result.action_json or {}), result_ref
        if str(call.status) in {"failed", "uncertain", "dead_letter", "cancelled"}:
            state.update(
                {
                    "phase": "stopped",
                    "outstanding_operation_ref": None,
                    "stop_reason": f"model_call_{call.status}",
                }
            )
            return normalize_local_agent_state(state), None, None
        return state, None, None
    if ref.startswith("tool-invocation:"):
        invocation_id = ref.split(":", 1)[1]
        invocation = (
            db.query(BidToolInvocation)
            .filter(BidToolInvocation.id == invocation_id)
            .one_or_none()
        )
        if invocation is None or str(invocation.task_id) != str(state["task_id"]):
            raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_TOOL_INVOCATION_OUT_OF_SCOPE")
        result = (
            db.query(BidToolResult)
            .filter(BidToolResult.invocation_id == invocation.id)
            .one_or_none()
        )
        if result is not None:
            result_ref = f"tool-result:{result.id}"
            observed = list(state["observed_tool_result_refs"])
            if result_ref not in observed:
                observed.append(result_ref)
            state.update(
                {
                    "phase": "hydrate",
                    "observed_tool_result_refs": observed,
                    "outstanding_operation_ref": None,
                }
            )
            return normalize_local_agent_state(state), None, result_ref
        if str(invocation.status) in {"failed", "rejected", "cancelled"}:
            state.update(
                {
                    "phase": "stopped",
                    "outstanding_operation_ref": None,
                    "stop_reason": f"tool_invocation_{invocation.status}",
                }
            )
            return normalize_local_agent_state(state), None, None
        return state, None, None
    raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_OPERATION_REF_INVALID")


def advance_local_agent_one_action(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    tool_scope_signing_key: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> LocalAgentStepReceipt:
    """Advance one Task by at most one persistable model-derived action."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    binding = dict(claim.task_contract.get("skill_binding") or {})
    if binding.get("executor_kind") != "langgraph":
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_EXECUTOR_KIND_INVALID")
    state = _load_state(db, claim, run_id=str(run.id))
    if state["phase"] in {
        "candidate_ready",
        "input_candidate_ready",
        "finish_ready",
        "stopped",
    }:
        return _ready_receipt(
            db,
            claim,
            task=task,
            state=state,
            request_id=request_id,
            now=current_time,
        )
    checkpoint_seq = _next_checkpoint_seq(db, attempt_id=str(claim.attempt_id))
    state, proposed_action, resolved_ref = _resolve_outstanding(db, state)
    if state["phase"] == "stopped":
        return _ready_receipt(
            db,
            claim,
            task=task,
            state=state,
            request_id=request_id,
            now=current_time,
        )
    outstanding = str(state.get("outstanding_operation_ref") or "")
    if outstanding and proposed_action is None:
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_OPERATION_NOT_SETTLED")

    operation = run_bounded_transition(
        local_state=state,
        proposed_action=proposed_action,
        allowed_tools=list(claim.task_contract["allowed_tools"]),
    )
    operation_type = str(operation["operation_type"])
    if operation_type == "request_model":
        next_action_seq = int(state["action_seq"]) + 1
        if next_action_seq > int(claim.task_contract["budget"]["max_iterations"]):
            state.update({"phase": "stopped", "stop_reason": "task_budget_exhausted"})
            checkpoint = write_task_checkpoint(
                db,
                claim,
                action_seq=checkpoint_seq,
                state=normalize_local_agent_state(state),
                next_state="stopped",
                request_id=request_id,
                now=current_time,
            )
            return LocalAgentStepReceipt(
                task_id=str(task.id),
                attempt_id=str(claim.attempt_id),
                action_seq=int(state["action_seq"]),
                operation_type="stopped",
                checkpoint_id=checkpoint.checkpoint_id,
                operation_ref=None,
                candidate_ref=None,
                completion_ready=False,
                requires_failure=True,
            )
        model_call_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bid-model-call-v1:{task.id}:{next_action_seq}")
        )
        state.update(
            {
                "phase": "await_model",
                "action_seq": next_action_seq,
                "outstanding_operation_ref": f"model-call:{model_call_id}",
            }
        )
        state = normalize_local_agent_state(state)
        context = assemble_context_manifest(
            db,
            claim,
            working_state=state,
            included_tool_result_ids=[
                ref.split(":", 1)[1]
                for ref in state["observed_tool_result_refs"]
                if ref.startswith("tool-result:")
            ],
            included_model_result_ids=[
                ref.split(":", 1)[1]
                for ref in state["observed_model_result_refs"]
                if ref.startswith(MODEL_RESULT_REF_PREFIX)
            ],
            request_id=request_id,
            now=current_time,
        )
        checkpoint = write_task_checkpoint(
            db,
            claim,
            action_seq=checkpoint_seq,
            state=state,
            budget_usage={
                "model_iterations_used": next_action_seq,
                "model_iterations_limit": int(
                    claim.task_contract["budget"]["max_iterations"]
                ),
            },
            next_state="await_model",
            context_manifest_id=context.context_manifest_id,
            request_id=request_id,
            now=current_time,
        )
        model_call = schedule_model_call(
            db,
            claim,
            context_manifest_id=context.context_manifest_id,
            checkpoint_id=checkpoint.checkpoint_id,
            action_seq=next_action_seq,
            idempotency_key=canonical_hash(
                {"task_id": str(task.id), "action_seq": next_action_seq}
            ),
            request_id=request_id,
            now=current_time,
        )
        return LocalAgentStepReceipt(
            task_id=str(task.id),
            attempt_id=str(claim.attempt_id),
            action_seq=next_action_seq,
            operation_type="request_model",
            checkpoint_id=checkpoint.checkpoint_id,
            operation_ref=f"model-call:{model_call.model_call_id}",
            candidate_ref=None,
            completion_ready=False,
            requires_failure=False,
        )

    action = dict(operation.get("action") or {})
    if proposed_action is None or not resolved_ref:
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_MODEL_RESULT_REQUIRED")
    if operation_type == "request_tool":
        context = assemble_context_manifest(
            db,
            claim,
            working_state=state,
            included_tool_result_ids=[
                ref.split(":", 1)[1]
                for ref in state["observed_tool_result_refs"]
                if ref.startswith("tool-result:")
            ],
            included_model_result_ids=[
                ref.split(":", 1)[1]
                for ref in state["observed_model_result_refs"]
                if ref.startswith(MODEL_RESULT_REF_PREFIX)
            ],
            request_id=request_id,
            now=current_time,
        )
        decision = authorize_tool_invocation(
            db,
            claim,
            context_manifest_id=context.context_manifest_id,
            tool_name=str(action["tool_name"]),
            arguments=dict(action["arguments"]),
            idempotency_key=canonical_hash(
                {
                    "task_id": str(task.id),
                    "model_action_seq": int(state["action_seq"]),
                    "tool_call_id": str(action["tool_call_id"]),
                }
            ),
            scope_signing_key=tool_scope_signing_key,
            request_id=request_id,
            now=current_time,
        )
        state.update(
            {
                "phase": "await_tool",
                "outstanding_operation_ref": f"tool-invocation:{decision.invocation_id}",
            }
        )
        state = normalize_local_agent_state(state)
        checkpoint = write_task_checkpoint(
            db,
            claim,
            action_seq=checkpoint_seq,
            state=state,
            tool_refs=[
                {
                    "invocation_id": decision.invocation_id,
                    "tool_call_id": decision.tool_call_id,
                    "tool_name": str(action["tool_name"]),
                }
            ],
            candidate_output_ref=resolved_ref,
            next_state="await_tool",
            context_manifest_id=context.context_manifest_id,
            request_id=request_id,
            now=current_time,
        )
        scope_token = str((decision.call_envelope or {}).get("scope_token") or "")
        dispatch = enqueue_tool_dispatch(
            db,
            claim,
            invocation_id=decision.invocation_id,
            checkpoint_id=checkpoint.checkpoint_id,
            scope_token=scope_token,
            scope_signing_key=tool_scope_signing_key,
            request_id=request_id,
            now=current_time,
        )
        return LocalAgentStepReceipt(
            task_id=str(task.id),
            attempt_id=str(claim.attempt_id),
            action_seq=int(state["action_seq"]),
            operation_type=operation_type,
            checkpoint_id=checkpoint.checkpoint_id,
            operation_ref=f"tool-dispatch:{dispatch.dispatch_id}",
            candidate_ref=resolved_ref,
            completion_ready=False,
            requires_failure=False,
        )

    phase_by_action = {
        "submit_fact_candidates": "candidate_ready",
        "submit_claim_candidates": "candidate_ready",
        "request_task_input": "input_candidate_ready",
        "finish": "finish_ready",
    }
    if operation_type not in phase_by_action:
        raise BidLocalAgentStateInvalid("BID_LOCAL_AGENT_ACTION_ROUTE_INVALID")
    candidates = list(state["candidate_refs"])
    if resolved_ref not in candidates:
        candidates.append(resolved_ref)
    state.update(
        {
            "phase": phase_by_action[operation_type],
            "candidate_refs": candidates,
            "outstanding_operation_ref": None,
        }
    )
    state = normalize_local_agent_state(state)
    checkpoint = write_task_checkpoint(
        db,
        claim,
        action_seq=checkpoint_seq,
        state=state,
        candidate_output_ref=resolved_ref,
        next_state=phase_by_action[operation_type],
        request_id=request_id,
        now=current_time,
    )
    return LocalAgentStepReceipt(
        task_id=str(task.id),
        attempt_id=str(claim.attempt_id),
        action_seq=int(state["action_seq"]),
        operation_type=operation_type,
        checkpoint_id=checkpoint.checkpoint_id,
        operation_ref=None,
        candidate_ref=resolved_ref,
        completion_ready=operation_type == "finish",
        requires_failure=False,
    )
