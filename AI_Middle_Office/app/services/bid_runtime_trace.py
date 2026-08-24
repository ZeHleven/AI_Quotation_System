"""Read-only, redacted runtime trace projections for the Phase 4 MVP-0 lab.

The projection intentionally exposes control-plane lineage, not business payloads:
prompt/context bodies, model actions, tool arguments and tool result bodies never
leave this service.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_eventing import BidPublicEvent
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
from app.models.bid_model_execution import (
    BidModelCall,
    BidModelCallAttempt,
    BidModelResult,
)
from app.models.bid_run_validation import BidRunValidation, BidRunValidationAttempt
from app.models.bid_tool_execution import BidToolDispatch, BidToolDispatchAttempt
from app.services.bid_assessment_eventing import as_utc, canonical_hash


TRACE_SCHEMA = "bid.runtime.trace.v1"
TRACE_REDACTION = {
    "policy": "control_plane_metadata_only",
    "omitted": [
        "prompt_body",
        "context_body",
        "model_action_body",
        "tool_arguments",
        "tool_result_body",
        "chain_of_thought",
    ],
}


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _safe(value: Any, *, limit: int = 160) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:limit]


def _hashes(**values: Any) -> dict[str, str]:
    return {name: str(value) for name, value in values.items() if value}


def _node(
    *,
    node_id: str,
    kind: str,
    label: str,
    status: str | None,
    created_at: datetime | None,
    updated_at: datetime | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
    details: dict[str, Any] | None = None,
    hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "label": label[:180],
        "status": status or "recorded",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "created_at": _utc(created_at),
        "updated_at": _utc(updated_at),
        "details": {key: _safe(value) for key, value in (details or {}).items()},
        "hashes": hashes or {},
    }


def _edge(source: str, target: str, kind: str, label: str = "") -> dict[str, str]:
    return {"source": source, "target": target, "kind": kind, "label": label}


def _rows_by_ids(db: Session, model: type, column: Any, values: Iterable[str]) -> list[Any]:
    ids = tuple(dict.fromkeys(str(value) for value in values if value))
    if not ids:
        return []
    return db.query(model).filter(column.in_(ids)).all()


def list_visible_runs(
    db: Session,
    *,
    actor_id: int,
    actor_is_admin: bool,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = (
        db.query(BidAnalysisRun, BidAssessment)
        .join(BidAssessment, BidAssessment.id == BidAnalysisRun.assessment_id)
    )
    if not actor_is_admin:
        query = query.filter(BidAssessment.created_by == int(actor_id))
    rows = (
        query.order_by(BidAnalysisRun.updated_at.desc(), BidAnalysisRun.created_at.desc())
        .limit(max(1, min(int(limit), 50)))
        .all()
    )
    return [
        {
            "run_id": str(run.id),
            "assessment_id": str(run.assessment_id),
            "assessment_title": str(assessment.title),
            "client_name": str(assessment.client_name),
            "run_kind": str(run.run_kind),
            "run_sequence": int(run.run_sequence),
            "status": str(run.status),
            "current_stage": str(run.current_stage) if run.current_stage else None,
            "retryable": bool(run.retryable),
            "cancel_requested_at": _utc(run.cancel_requested_at),
            "row_version": int(run.row_version),
            "last_checkpoint_at": _utc(run.last_checkpoint_at),
            "created_at": _utc(run.created_at),
            "updated_at": _utc(run.updated_at),
        }
        for run, assessment in rows
    ]


def build_runtime_trace(db: Session, run: BidAnalysisRun) -> dict[str, Any]:
    plans = (
        db.query(BidPlanRevision)
        .filter(BidPlanRevision.run_id == run.id)
        .order_by(BidPlanRevision.revision_no.asc())
        .all()
    )
    tasks = (
        db.query(BidTask)
        .filter(BidTask.run_id == run.id)
        .order_by(BidTask.created_at.asc(), BidTask.task_key.asc())
        .all()
    )
    task_ids = [str(row.id) for row in tasks]
    dependencies = (
        db.query(BidTaskDependency)
        .filter(BidTaskDependency.run_id == run.id)
        .all()
    )
    dependencies.sort(key=lambda row: (str(row.task_id), str(row.depends_on_task_id)))
    attempts = _rows_by_ids(db, BidTaskAttempt, BidTaskAttempt.task_id, task_ids)
    attempts.sort(key=lambda row: (str(row.task_id), int(row.attempt_no)))
    attempt_ids = [str(row.id) for row in attempts]
    checkpoints = _rows_by_ids(
        db, BidCheckpoint, BidCheckpoint.task_attempt_id, attempt_ids
    )
    checkpoints.sort(key=lambda row: (str(row.task_attempt_id), int(row.action_seq)))
    async_ops = _rows_by_ids(
        db, BidAsyncOperation, BidAsyncOperation.task_attempt_id, attempt_ids
    )
    async_ops.sort(key=lambda row: (str(row.created_at), str(row.id)))
    contexts = (
        db.query(BidContextManifest)
        .filter(BidContextManifest.run_id == run.id)
        .order_by(BidContextManifest.created_at.asc(), BidContextManifest.id.asc())
        .all()
    )
    invocations = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.run_id == run.id)
        .order_by(BidToolInvocation.created_at.asc(), BidToolInvocation.id.asc())
        .all()
    )
    invocation_ids = [str(row.id) for row in invocations]
    tool_results = _rows_by_ids(
        db, BidToolResult, BidToolResult.invocation_id, invocation_ids
    )
    tool_results.sort(key=lambda row: (str(row.created_at), str(row.id)))
    dispatches = _rows_by_ids(
        db, BidToolDispatch, BidToolDispatch.invocation_id, invocation_ids
    )
    dispatches.sort(key=lambda row: (str(row.created_at), str(row.id)))
    dispatch_ids = [str(row.id) for row in dispatches]
    dispatch_attempts = _rows_by_ids(
        db, BidToolDispatchAttempt, BidToolDispatchAttempt.dispatch_id, dispatch_ids
    )
    dispatch_attempts.sort(
        key=lambda row: (str(row.dispatch_id), int(row.attempt_no), str(row.id))
    )
    model_calls = (
        db.query(BidModelCall)
        .filter(BidModelCall.run_id == run.id)
        .order_by(BidModelCall.created_at.asc(), BidModelCall.id.asc())
        .all()
    )
    model_call_ids = [str(row.id) for row in model_calls]
    model_attempts = _rows_by_ids(
        db, BidModelCallAttempt, BidModelCallAttempt.model_call_id, model_call_ids
    )
    model_attempts.sort(
        key=lambda row: (str(row.model_call_id), int(row.attempt_no), str(row.id))
    )
    model_results = _rows_by_ids(
        db, BidModelResult, BidModelResult.model_call_id, model_call_ids
    )
    model_results.sort(key=lambda row: (str(row.created_at), str(row.id)))
    validation = (
        db.query(BidRunValidation).filter(BidRunValidation.run_id == run.id).one_or_none()
    )
    validation_attempts = (
        []
        if validation is None
        else db.query(BidRunValidationAttempt)
        .filter(BidRunValidationAttempt.validation_id == validation.id)
        .order_by(BidRunValidationAttempt.attempt_no.asc())
        .all()
    )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    run_node = f"run:{run.id}"
    run_details = {
        "stage": run.current_stage,
        "row_version": run.row_version,
        "retryable": run.retryable,
    }
    if run.hard_gate_comparison_baseline_id:
        run_details["hard_gate_comparison_baseline_id"] = str(
            run.hard_gate_comparison_baseline_id
        )
    nodes.append(
        _node(
            node_id=run_node,
            kind="run",
            label=f"Run #{run.run_sequence} · {run.run_kind}",
            status=str(run.status),
            created_at=run.created_at,
            updated_at=run.updated_at,
            details=run_details,
            hashes=_hashes(
                input_hash=run.input_hash,
                input_fingerprint=run.input_fingerprint,
                hard_gate_comparison_baseline_hash=(
                    run.hard_gate_comparison_baseline_hash
                ),
            ),
        )
    )

    plan_task_meta: dict[tuple[str, str], dict[str, Any]] = {}
    for plan in plans:
        plan_node = f"plan:{plan.id}"
        nodes.append(
            _node(
                node_id=plan_node,
                kind="plan",
                label=f"Plan Revision {plan.revision_no}",
                status=str(plan.status),
                created_at=plan.created_at,
                updated_at=plan.updated_at,
                details={"revision_no": plan.revision_no, "row_version": plan.row_version},
                hashes=_hashes(validated_hash=plan.validated_hash),
            )
        )
        edges.append(_edge(run_node, plan_node, "contains", "plan"))
        envelope = plan.proposal_json if isinstance(plan.proposal_json, dict) else {}
        proposal = envelope.get("proposal") if isinstance(envelope.get("proposal"), dict) else {}
        definitions = proposal.get("add_tasks") if isinstance(proposal.get("add_tasks"), list) else []
        for definition in definitions:
            if isinstance(definition, dict) and definition.get("task_key"):
                plan_task_meta[(str(plan.id), str(definition["task_key"]))] = definition

    for task in tasks:
        task_node = f"task:{task.id}"
        meta = plan_task_meta.get((str(task.plan_revision_id), str(task.task_key)), {})
        binding = meta.get("skill_binding") if isinstance(meta.get("skill_binding"), dict) else {}
        nodes.append(
            _node(
                node_id=task_node,
                kind="task",
                label=str(task.task_key),
                status=str(task.status),
                created_at=task.created_at,
                updated_at=task.updated_at,
                task_id=str(task.id),
                details={
                    "task_type": task.task_type,
                    "stage_sequence": meta.get("stage_sequence"),
                    "skill_id": binding.get("skill_id"),
                    "skill_version": binding.get("skill_version"),
                    "executor_kind": binding.get("executor_kind"),
                    "tool_profile": task.tool_profile,
                    "context_profile": task.context_profile,
                },
                hashes=_hashes(input_hash=task.input_hash, skill_hash=binding.get("skill_hash")),
            )
        )
        edges.append(_edge(f"plan:{task.plan_revision_id}", task_node, "contains", "task"))

    for dependency in dependencies:
        edges.append(
            _edge(
                f"task:{dependency.depends_on_task_id}",
                f"task:{dependency.task_id}",
                "depends_on",
                "DAG",
            )
        )

    for attempt in attempts:
        attempt_node = f"task_attempt:{attempt.id}"
        nodes.append(
            _node(
                node_id=attempt_node,
                kind="task_attempt",
                label=f"Attempt {attempt.attempt_no}",
                status=str(attempt.status),
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
                task_id=str(attempt.task_id),
                attempt_id=str(attempt.id),
                details={
                    "attempt_no": attempt.attempt_no,
                    "fencing_token": attempt.fencing_token,
                    "lease_owner": attempt.lease_owner,
                    "lease_until": _utc(attempt.lease_until),
                    "error_code": attempt.error_code,
                },
            )
        )
        edges.append(_edge(f"task:{attempt.task_id}", attempt_node, "attempt", "lease"))

    for context in contexts:
        node_id = f"context:{context.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="context",
                label=f"Context Manifest {context.manifest_seq}",
                status="frozen",
                created_at=context.created_at,
                task_id=str(context.task_id),
                attempt_id=str(context.task_attempt_id),
                details={
                    "role": context.role,
                    "token_estimate": context.token_estimate,
                    "compression_level": context.compression_level,
                    "assembler_version": context.assembler_version,
                    "fencing_token": context.fencing_token,
                },
                hashes=_hashes(manifest_hash=context.manifest_hash),
            )
        )
        edges.append(_edge(f"task_attempt:{context.task_attempt_id}", node_id, "context", "freeze"))

    for checkpoint in checkpoints:
        node_id = f"checkpoint:{checkpoint.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="checkpoint",
                label=f"Checkpoint #{checkpoint.action_seq}",
                status=str(checkpoint.next_state or "saved"),
                created_at=checkpoint.created_at,
                attempt_id=str(checkpoint.task_attempt_id),
                details={
                    "action_seq": checkpoint.action_seq,
                    "next_state": checkpoint.next_state,
                    "fencing_token": checkpoint.fencing_token,
                    "has_candidate_output": bool(checkpoint.candidate_output_ref),
                },
                hashes=_hashes(state_hash=checkpoint.state_hash),
            )
        )
        edges.append(_edge(f"task_attempt:{checkpoint.task_attempt_id}", node_id, "checkpoint", "append"))
        if checkpoint.context_manifest_id:
            edges.append(_edge(f"context:{checkpoint.context_manifest_id}", node_id, "lineage", "context"))

    for operation in async_ops:
        node_id = f"async:{operation.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="async_operation",
                label=str(operation.operation_type),
                status=str(operation.status),
                created_at=operation.created_at,
                updated_at=operation.updated_at,
                task_id=str(operation.task_id),
                attempt_id=str(operation.task_attempt_id),
                details={
                    "provider_ref": operation.provider_ref,
                    "retry_count": operation.retry_count,
                    "timeout_at": _utc(operation.timeout_at),
                    "error_code": operation.error_code,
                },
                hashes=_hashes(input_hash=operation.input_hash),
            )
        )
        edges.append(_edge(f"task_attempt:{operation.task_attempt_id}", node_id, "operation", "async"))

    for call in model_calls:
        node_id = f"model_call:{call.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="model_call",
                label=f"{call.logical_role} · {call.model_ref}",
                status=str(call.status),
                created_at=call.created_at,
                updated_at=call.updated_at,
                task_id=str(call.task_id),
                attempt_id=str(call.task_attempt_id),
                details={
                    "action_seq": call.action_seq,
                    "provider": call.provider_ref,
                    "action_schema": call.action_schema,
                    "replay_policy": call.replay_policy,
                    "attempts": f"{call.attempt_count}/{call.max_attempts}",
                    "tokens": f"{call.actual_input_tokens}+{call.actual_output_tokens}",
                    "cost_microunits": call.actual_cost_microunits,
                    "fencing_token": call.fencing_token,
                },
                hashes=_hashes(request_hash=call.request_hash, input_hash=call.input_hash),
            )
        )
        edges.append(_edge(f"checkpoint:{call.checkpoint_id}", node_id, "model_call", "gateway"))
        edges.append(_edge(f"context:{call.context_manifest_id}", node_id, "lineage", "context"))
        edges.append(_edge(node_id, f"async:{call.async_operation_id}", "operation", "await"))

    for attempt in model_attempts:
        node_id = f"model_attempt:{attempt.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="model_attempt",
                label=f"Provider Attempt {attempt.attempt_no}",
                status=str(attempt.status),
                created_at=attempt.created_at,
                attempt_id=str(attempt.id),
                details={
                    "attempt_no": attempt.attempt_no,
                    "fencing_token": attempt.fencing_token,
                    "worker_id": attempt.worker_id,
                    "provider_request_id": attempt.provider_request_id,
                    "error_code": attempt.error_code,
                },
                hashes=_hashes(outcome_hash=attempt.outcome_hash),
            )
        )
        edges.append(_edge(f"model_call:{attempt.model_call_id}", node_id, "attempt", "provider"))

    for result in model_results:
        node_id = f"model_result:{result.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="model_result",
                label=f"Model Action · {result.action_type}",
                status="immutable",
                created_at=result.created_at,
                task_id=str(result.task_id),
                attempt_id=str(result.source_task_attempt_id),
                details={
                    "action_type": result.action_type,
                    "storage_kind": result.storage_kind,
                    "tokens": f"{result.input_tokens}+{result.output_tokens}",
                    "cost_microunits": result.actual_cost_microunits,
                    "finish_reason": result.finish_reason,
                },
                hashes=_hashes(
                    action_hash=result.action_hash,
                    response_hash=result.response_hash,
                    result_hash=result.result_hash,
                ),
            )
        )
        edges.append(_edge(f"model_call:{result.model_call_id}", node_id, "result", "immutable"))

    for invocation in invocations:
        node_id = f"tool:{invocation.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="tool_invocation",
                label=str(invocation.tool_name),
                status=str(invocation.status),
                created_at=invocation.created_at,
                updated_at=invocation.updated_at,
                task_id=str(invocation.task_id),
                attempt_id=str(invocation.task_attempt_id),
                details={
                    "invocation_seq": invocation.invocation_seq,
                    "tool_profile": invocation.tool_profile,
                    "fencing_token": invocation.fencing_token,
                    "error_code": invocation.error_code,
                },
                hashes=_hashes(
                    arguments_hash=invocation.arguments_hash,
                    request_hash=invocation.request_hash,
                    scope_token_hash=invocation.scope_token_hash,
                ),
            )
        )
        edges.append(_edge(f"context:{invocation.context_manifest_id}", node_id, "tool_call", "gateway"))

    for dispatch in dispatches:
        node_id = f"dispatch:{dispatch.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="tool_dispatch",
                label=f"{dispatch.adapter_name}@{dispatch.adapter_version}",
                status=str(dispatch.status),
                created_at=dispatch.created_at,
                updated_at=dispatch.updated_at,
                task_id=str(dispatch.task_id),
                attempt_id=str(dispatch.task_attempt_id),
                details={
                    "mode": dispatch.adapter_mode,
                    "replay_policy": dispatch.replay_policy,
                    "attempts": f"{dispatch.attempt_count}/{dispatch.max_attempts}",
                    "fencing_token": dispatch.fencing_token,
                    "error_code": dispatch.last_error_code,
                },
                hashes=_hashes(envelope_hash=dispatch.envelope_hash),
            )
        )
        edges.append(_edge(f"tool:{dispatch.invocation_id}", node_id, "dispatch", "router"))

    for attempt in dispatch_attempts:
        node_id = f"dispatch_attempt:{attempt.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="dispatch_attempt",
                label=f"Adapter Attempt {attempt.attempt_no}",
                status=str(attempt.status),
                created_at=attempt.created_at,
                attempt_id=str(attempt.id),
                details={
                    "attempt_no": attempt.attempt_no,
                    "fencing_token": attempt.fencing_token,
                    "worker_id": attempt.worker_id,
                    "error_code": attempt.error_code,
                },
                hashes=_hashes(outcome_hash=attempt.outcome_hash),
            )
        )
        edges.append(_edge(f"dispatch:{attempt.dispatch_id}", node_id, "attempt", "adapter"))

    for result in tool_results:
        node_id = f"tool_result:{result.id}"
        nodes.append(
            _node(
                node_id=node_id,
                kind="tool_result",
                label=f"Tool Result · {result.status}",
                status=str(result.status),
                created_at=result.created_at,
                attempt_id=str(result.task_attempt_id),
                details={
                    "storage_kind": result.storage_kind,
                    "byte_count": result.byte_count,
                    "returned_items": result.returned_items,
                    "truncated": result.truncated,
                },
                hashes=_hashes(data_hash=result.data_hash, result_hash=result.result_hash),
            )
        )
        edges.append(_edge(f"tool:{result.invocation_id}", node_id, "result", "immutable"))

    if validation is not None:
        validation_node = f"validation:{validation.id}"
        nodes.append(
            _node(
                node_id=validation_node,
                kind="validation",
                label=f"Run Validator · {validation.validator_version}",
                status=str(validation.status),
                created_at=validation.created_at,
                updated_at=validation.updated_at,
                details={
                    "outcome": validation.outcome,
                    "attempt_count": validation.attempt_count,
                    "fencing_token": validation.fencing_token,
                    "failure_code": validation.failure_code,
                },
                hashes=_hashes(input_hash=validation.input_hash, result_hash=validation.result_hash),
            )
        )
        edges.append(_edge(run_node, validation_node, "validation", "converge"))
        for attempt in validation_attempts:
            node_id = f"validation_attempt:{attempt.id}"
            nodes.append(
                _node(
                    node_id=node_id,
                    kind="validation_attempt",
                    label=f"Validation Attempt {attempt.attempt_no}",
                    status=str(attempt.status),
                    created_at=attempt.created_at,
                    attempt_id=str(attempt.id),
                    details={
                        "fencing_token": attempt.fencing_token,
                        "worker_id": attempt.worker_id,
                        "error_code": attempt.error_code,
                    },
                    hashes=_hashes(result_hash=attempt.result_hash),
                )
            )
            edges.append(_edge(validation_node, node_id, "attempt", "validator"))

    public_events = (
        db.query(BidPublicEvent)
        .filter(
            BidPublicEvent.assessment_id == run.assessment_id,
            or_(
                and_(
                    BidPublicEvent.resource_type == "run",
                    BidPublicEvent.resource_id == run.id,
                ),
                and_(
                    BidPublicEvent.resource_type == "assessment",
                    BidPublicEvent.resource_id == run.assessment_id,
                ),
            ),
        )
        .order_by(BidPublicEvent.sequence_no.desc())
        .limit(100)
        .all()
    )
    timeline = [
        {
            "id": str(event.event_id),
            "type": str(event.event_type),
            "source": "public_event",
            "resource_type": str(event.resource_type),
            "resource_id": str(event.resource_id),
            "status": "published",
            "occurred_at": _utc(event.occurred_at),
            "sequence_no": int(event.sequence_no),
        }
        for event in reversed(public_events)
    ]
    for node in nodes:
        timeline.append(
            {
                "id": node["id"],
                "type": node["kind"],
                "source": "authority_row",
                "resource_type": node["kind"],
                "resource_id": node["id"].split(":", 1)[-1],
                "status": node["status"],
                "occurred_at": node["created_at"],
                "sequence_no": None,
            }
        )
    timeline.sort(key=lambda item: (item.get("occurred_at") or "", item["id"]))
    counts = Counter(node["kind"] for node in nodes)
    run_projection = {
        "run_id": str(run.id),
        "assessment_id": str(run.assessment_id),
        "run_kind": str(run.run_kind),
        "run_sequence": int(run.run_sequence),
        "status": str(run.status),
        "current_stage": str(run.current_stage) if run.current_stage else None,
        "row_version": int(run.row_version),
        "retryable": bool(run.retryable),
        "started_at": _utc(run.started_at),
        "finished_at": _utc(run.finished_at),
        "last_checkpoint_at": _utc(run.last_checkpoint_at),
    }
    if run.hard_gate_comparison_baseline_id or run.hard_gate_comparison_baseline_hash:
        run_projection.update(
            {
                "hard_gate_comparison_baseline_id": (
                    str(run.hard_gate_comparison_baseline_id)
                    if run.hard_gate_comparison_baseline_id
                    else None
                ),
                "hard_gate_comparison_baseline_hash": (
                    str(run.hard_gate_comparison_baseline_hash)
                    if run.hard_gate_comparison_baseline_hash
                    else None
                ),
            }
        )
    trace_core = {
        "schema": TRACE_SCHEMA,
        "run": run_projection,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "task_count": counts["task"],
            "attempt_count": counts["task_attempt"],
            "checkpoint_count": counts["checkpoint"],
            "model_call_count": counts["model_call"],
            "tool_call_count": counts["tool_invocation"],
            "validation_count": counts["validation"],
            "counts_by_kind": dict(sorted(counts.items())),
        },
        "redaction": TRACE_REDACTION,
        "nodes": nodes,
        "edges": edges,
        "timeline": timeline[-500:],
    }
    return {
        **trace_core,
        "trace_hash": canonical_hash(trace_core),
        "generated_at": _utc(datetime.now(timezone.utc)),
    }


def runtime_trace_headers(trace: dict[str, Any]) -> dict[str, str]:
    run = trace["run"]
    return {
        "ETag": (
            f'"bid-runtime-trace:{run["run_id"]}:{run["row_version"]}:'
            f'{trace["trace_hash"][:12]}"'
        ),
        "X-Resource-Version": str(run["row_version"]),
        "X-Trace-Schema": TRACE_SCHEMA,
        "Cache-Control": "private, no-cache, max-age=0, must-revalidate",
        "Vary": "Authorization",
    }
