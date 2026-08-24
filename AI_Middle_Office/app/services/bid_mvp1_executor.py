"""Durable task/model workers completing the Phase 4 MVP-1 vertical slice."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import BidManifestDocument
from app.models.bid_assessment_documents import BidDocumentParseHead, BidDocumentParseRun
from app.models.bid_assessment_runtime import BidCheckpoint, BidTask
from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_enterprise_capability import materialize_enterprise_snapshot_facts
from app.services.bid_local_agent_executor import advance_local_agent_one_action
from app.services.bid_model_execution import (
    ModelProvider,
    claim_model_call,
    execute_model_call_claim,
)
from app.services.bid_mvp1_authority import (
    AuthorityOutput,
    BidMvp1AuthorityError,
    build_fact_coverage_baseline,
    evaluate_hard_gate,
    evaluate_preliminary_decision,
    generate_preliminary_report,
    persist_claim_candidates,
    persist_fact_candidates,
    resolve_facts,
    validate_claims,
)
from app.services.bid_task_runtime import (
    TASK_OUTPUT_VALIDATOR_VERSION,
    TaskCompletionReceipt,
    TaskLeaseClaim,
    complete_task_attempt,
    fail_task_attempt,
    lease_next_ready_task,
    start_task_attempt,
    write_task_checkpoint,
)


MVP1_TASK_EXECUTOR_VERSION = "bid-mvp1-task-executor-v1"
DETERMINISTIC_TASK_TYPES = frozenset(
    {
        "bind_assessment_snapshot",
        "inventory_documents",
        "build_coverage_baseline",
        "build_enterprise_snapshot",
        "resolve_fact_conflicts",
        "evaluate_deadline_gate",
        "evaluate_qualification_gate",
        "evaluate_personnel_performance_gate",
        "evaluate_legal_compliance_gate",
        "evaluate_guarantee_cash_gate",
        "evaluate_minimum_bid_capacity_gate",
        "evaluate_enterprise_prohibited_risk_gate",
        "evaluate_final_decision",
        "validate_claim_evidence",
        "validate_report_consistency",
        "generate_preliminary_report",
    }
)


@dataclass(frozen=True)
class Mvp1TaskBatchResult:
    claimed: int
    completed: int
    waiting: int
    failed: int
    error_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mvp1ModelBatchResult:
    claimed: int
    succeeded: int
    deferred: int
    failed: int


def _task_failure_code(exc: Exception) -> str:
    code = str(getattr(exc, "code", ""))
    if code.startswith("BID_") and code.replace("_", "").isalnum():
        return code[:100]
    return "BID_MVP1_TASK_EXECUTION_FAILED"


def _checkpoint_seq(db: Session, claim: TaskLeaseClaim) -> int:
    value = (
        db.query(func.max(BidCheckpoint.action_seq))
        .filter(BidCheckpoint.task_attempt_id == claim.attempt_id)
        .scalar()
    )
    return int(value) + 1 if value is not None else 0


def _complete(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    output: AuthorityOutput,
    request_id: str,
) -> None:
    state = {
        "schema": "bid.task.deterministic.state.v1",
        "executor_version": MVP1_TASK_EXECUTOR_VERSION,
        "task_id": str(claim.task_id),
        "attempt_id": str(claim.attempt_id),
        "fencing_token": int(claim.fencing_token),
        "output_ref": output.output_ref,
        "output_hash": output.output_hash,
        "status": "succeeded",
    }
    checkpoint = write_task_checkpoint(
        db,
        claim,
        action_seq=_checkpoint_seq(db, claim),
        state=state,
        candidate_output_ref=output.output_ref,
        next_state="succeeded",
        request_id=request_id,
    )
    complete_task_attempt(
        db,
        claim,
        completion=TaskCompletionReceipt(
            checkpoint_id=checkpoint.checkpoint_id,
            state_hash=checkpoint.state_hash,
            output_hash=output.output_hash,
            completion_contract=str(claim.task_contract["completion_contract"]),
            validator_version=TASK_OUTPUT_VALIDATOR_VERSION,
            output_ref=output.output_ref,
        ),
        request_id=request_id,
        plan_continuation_enabled=True,
    )


def _generic_deterministic_output(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    task: BidTask,
) -> AuthorityOutput:
    run_id = str(task.run_id)
    if str(task.task_type) == "bind_assessment_snapshot":
        payload = {
            "authority_version": MVP1_TASK_EXECUTOR_VERSION,
            "run_id": run_id,
            "task_id": str(task.id),
            "bound_versions": dict(claim.task_contract["bound_versions"]),
            "task_contract_hash": str(claim.task_contract_hash),
        }
        return AuthorityOutput(
            output_ref=f"assessment-snapshot:{run_id}",
            output_hash=canonical_hash(payload),
            payload=payload,
        )
    if str(task.task_type) == "inventory_documents":
        rows = (
            db.query(BidManifestDocument, BidDocumentParseHead, BidDocumentParseRun)
            .join(
                BidDocumentParseHead,
                BidDocumentParseHead.document_version_id
                == BidManifestDocument.document_version_id,
            )
            .join(BidDocumentParseRun, BidDocumentParseRun.id == BidDocumentParseHead.current_run_id)
            .filter(
                BidManifestDocument.manifest_id
                == claim.task_contract["bound_versions"]["manifest_id"],
                BidDocumentParseRun.status.in_(("succeeded", "partial")),
            )
            .order_by(BidManifestDocument.order_no.asc())
            .all()
        )
        documents = [
            {
                "document_version_id": str(member.document_version_id),
                "role": str(member.role),
                "order_no": int(member.order_no),
                "parse_run_id": str(parse_run.id),
                "parse_status": str(parse_run.status),
                "quality_grade": parse_run.quality_grade,
                "warning_count": int(parse_run.warning_count),
            }
            for member, _head, parse_run in rows
        ]
        payload = {
            "authority_version": MVP1_TASK_EXECUTOR_VERSION,
            "run_id": run_id,
            "manifest_id": claim.task_contract["bound_versions"]["manifest_id"],
            "documents": documents,
        }
        return AuthorityOutput(
            output_ref=f"document-inventory:{run_id}",
            output_hash=canonical_hash(payload),
            payload=payload,
        )
    raise BidMvp1AuthorityError("BID_MVP1_DETERMINISTIC_TASK_UNSUPPORTED")


def _enterprise_materialization_output_ref(payload: dict[str, object]) -> str:
    comparison_baseline_id = str(payload.get("comparison_baseline_id") or "").strip()
    if comparison_baseline_id:
        return f"hard-gate-comparison-facts:{comparison_baseline_id}"
    enterprise_snapshot_id = str(payload.get("enterprise_snapshot_id") or "").strip()
    if enterprise_snapshot_id:
        return f"enterprise-snapshot-facts:{enterprise_snapshot_id}"
    raise BidMvp1AuthorityError(
        "BID_MVP1_ENTERPRISE_MATERIALIZATION_REF_MISSING"
    )


def _execute_claim(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    tool_scope_signing_key: str,
    request_id: str,
) -> str:
    task = db.query(BidTask).filter(BidTask.id == claim.task_id).one()
    binding = dict(claim.task_contract.get("skill_binding") or {})
    if binding.get("executor_kind") == "langgraph":
        step = advance_local_agent_one_action(
            db,
            claim,
            tool_scope_signing_key=tool_scope_signing_key,
            request_id=request_id,
        )
        if step.operation_type == "submit_fact_candidates":
            model_result_id = str(step.candidate_ref or "").split(":", 1)[-1]
            output = persist_fact_candidates(
                db, claim, model_result_id=model_result_id
            )
            _complete(db, claim, output=output, request_id=request_id)
            return "completed"
        if step.operation_type == "submit_claim_candidates":
            model_result_id = str(step.candidate_ref or "").split(":", 1)[-1]
            output = persist_claim_candidates(
                db, claim, model_result_id=model_result_id
            )
            _complete(db, claim, output=output, request_id=request_id)
            return "completed"
        if step.operation_type == "finish" and step.candidate_ref:
            payload = {
                "task_id": str(task.id),
                "model_result_ref": str(step.candidate_ref),
                "status": "succeeded",
            }
            output = AuthorityOutput(
                output_ref=str(step.candidate_ref),
                output_hash=canonical_hash(payload),
                payload=payload,
            )
            _complete(db, claim, output=output, request_id=request_id)
            return "completed"
        if step.requires_failure:
            fail_task_attempt(
                db,
                claim,
                error_code="BID_MVP1_AGENT_STOPPED",
                retryable=False,
                request_id=request_id,
            )
            return "failed"
        return "waiting"

    task_type = str(task.task_type)
    if task_type not in DETERMINISTIC_TASK_TYPES:
        raise BidMvp1AuthorityError("BID_MVP1_DETERMINISTIC_TASK_UNSUPPORTED")
    if task_type in {"bind_assessment_snapshot", "inventory_documents"}:
        output = _generic_deterministic_output(db, claim, task=task)
    elif task_type == "build_coverage_baseline":
        output = build_fact_coverage_baseline(db, claim)
    elif task_type == "build_enterprise_snapshot":
        payload = materialize_enterprise_snapshot_facts(db, claim)
        output = AuthorityOutput(
            output_ref=_enterprise_materialization_output_ref(payload),
            output_hash=canonical_hash(payload),
            payload=payload,
        )
    elif task_type == "resolve_fact_conflicts":
        output = resolve_facts(db, claim)
    elif task_type.startswith("evaluate_") and task_type.endswith("_gate"):
        output = evaluate_hard_gate(db, claim)
    elif task_type == "evaluate_final_decision":
        output = evaluate_preliminary_decision(db, claim)
    elif task_type == "validate_claim_evidence":
        output = validate_claims(db, claim, final_consistency=False)
    elif task_type == "validate_report_consistency":
        output = validate_claims(db, claim, final_consistency=True)
    elif task_type == "generate_preliminary_report":
        output = generate_preliminary_report(db, claim)
    else:
        raise BidMvp1AuthorityError("BID_MVP1_DETERMINISTIC_TASK_UNSUPPORTED")
    _complete(db, claim, output=output, request_id=request_id)
    return "completed"


def process_mvp1_task_queue(
    *,
    session_factory: Callable[[], Session],
    worker_id: str,
    tool_scope_signing_key: str,
    limit: int = 20,
    lease_seconds: int = 180,
) -> Mvp1TaskBatchResult:
    claimed_count = completed = waiting = failed = 0
    error_codes: list[str] = []
    allowed = set(DETERMINISTIC_TASK_TYPES) | {
        "extract_tender_overview",
        "extract_critical_dates",
        "extract_qualification_requirements",
        "extract_rejection_clauses",
        "extract_guarantees_and_fees",
        "extract_evaluation_method",
        "extract_scope_and_quantities",
        "extract_deliverables_and_samples",
        "extract_contract_terms",
        "extract_schedule_and_site_constraints",
        "synthesize_assessment",
    }
    for index in range(max(1, min(int(limit), 100))):
        claim = None
        lease_db = session_factory()
        try:
            with lease_db.begin():
                claim = lease_next_ready_task(
                    lease_db,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                    allowed_task_types=allowed,
                    request_id=f"mvp1-lease:{worker_id}:{index}",
                )
        finally:
            lease_db.close()
        if claim is None:
            break
        claimed_count += 1
        start_db = session_factory()
        started = False
        try:
            with start_db.begin():
                start_task_attempt(
                    start_db, claim, request_id=f"mvp1-start:{claim.attempt_id}"
                )
            started = True
        except Exception as exc:
            start_db.rollback()
            error_codes.append(_task_failure_code(exc))
            failed += 1
        finally:
            start_db.close()
        if not started:
            continue
        execution_db = session_factory()
        try:
            with execution_db.begin():
                status = _execute_claim(
                    execution_db,
                    claim,
                    tool_scope_signing_key=tool_scope_signing_key,
                    request_id=f"mvp1-execute:{claim.attempt_id}",
                )
            if status == "completed":
                completed += 1
            elif status == "waiting":
                waiting += 1
            else:
                failed += 1
        except Exception as exc:
            execution_db.rollback()
            error_codes.append(_task_failure_code(exc))
            if claim is not None:
                recovery = session_factory()
                try:
                    with recovery.begin():
                        fail_task_attempt(
                            recovery,
                            claim,
                            error_code=_task_failure_code(exc),
                            retryable=not isinstance(exc, BidMvp1AuthorityError),
                            request_id=f"mvp1-fail:{claim.attempt_id}",
                        )
                except Exception as recovery_exc:
                    recovery.rollback()
                    error_codes.append(
                        f"recovery:{getattr(recovery_exc, 'code', type(recovery_exc).__name__)}"[
                            :109
                        ]
                    )
                finally:
                    recovery.close()
            failed += 1
        finally:
            execution_db.close()
    return Mvp1TaskBatchResult(
        claimed=claimed_count,
        completed=completed,
        waiting=waiting,
        failed=failed,
        error_codes=tuple(error_codes),
    )


def process_mvp1_model_queue(
    *,
    session_factory: Callable[[], Session],
    worker_id: str,
    provider: ModelProvider,
    limit: int = 10,
    lease_seconds: int = 120,
) -> Mvp1ModelBatchResult:
    claimed_count = succeeded = deferred = failed = 0
    for _ in range(max(1, min(int(limit), 50))):
        db = session_factory()
        try:
            with db.begin():
                claim = claim_model_call(
                    db, worker_id=worker_id, lease_seconds=lease_seconds
                )
        finally:
            db.close()
        if claim is None:
            break
        claimed_count += 1
        result = execute_model_call_claim(
            session_factory, claim=claim, provider=provider
        )
        if not isinstance(result, str):
            succeeded += 1
        elif result in {"retry_wait", "uncertain"}:
            deferred += 1
        else:
            failed += 1
    return Mvp1ModelBatchResult(
        claimed=claimed_count, succeeded=succeeded, deferred=deferred, failed=failed
    )
