from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_intake_runtime import (
    BidIntakeAgentRun,
    BidIntakeAssessment,
    BidIntakeHumanDecision,
    BidIntakeRunEvent,
)
from app.services.bid_intake_runtime import (
    append_run_event,
    current_evidence_manifest,
    queued_human_decision,
)

from .contracts import DocumentManifest, HumanDecision
from .execution_trace import (
    BidIntakeExecutionTrace,
    restore_trace_position,
)
from .graph import BidIntakeAgent, build_bid_intake_agent, build_initial_state
from .ports import AgentRuntime
from .sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver


class BidIntakeExecutionError(RuntimeError):
    pass


class PersistentBidIntakeExecutor:
    """Executes one leased run and mirrors durable graph state to control tables."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
    ) -> None:
        self._session_factory = session_factory

    def execute(
        self,
        *,
        run_uuid: str,
        lease_token: str,
        runtime: AgentRuntime,
        manifest: DocumentManifest,
    ) -> dict[str, Any]:
        context = self._load_context(
            run_uuid=run_uuid,
            lease_token=lease_token,
        )
        if runtime.policy.version != context["policy_version"]:
            error = BidIntakeExecutionError(
                "BOUND_POLICY_VERSION_MISMATCH"
            )
            self._mark_failed(
                run_uuid=run_uuid,
                lease_token=lease_token,
                checkpoint_id=context["checkpoint_id"],
                exc=error,
            )
            raise error
        if (
            manifest.case_id != context["project_uuid"]
            or manifest.manifest_version != context["manifest_version"]
            or manifest.manifest_hash != context["manifest_hash"]
            or context["current_manifest_id"] != context["manifest_id"]
            or context["current_manifest_version"]
            != context["manifest_version"]
            or context["current_manifest_hash"] != context["manifest_hash"]
        ):
            self._mark_stale_manifest(
                run_uuid=run_uuid,
                lease_token=lease_token,
                observed_manifest=manifest,
            )
            raise BidIntakeExecutionError("STALE_MANIFEST")

        saver = SqlAlchemyCheckpointSaver(self._session_factory)
        agent = build_bid_intake_agent(runtime, checkpointer=saver)
        trace = self._build_execution_trace(
            run_uuid=run_uuid,
            lease_token=lease_token,
            run_id=context["run_id"],
            assessment_id=context["assessment_id"],
        )
        try:
            decision = self._load_pending_decision(
                run_id=context["run_id"]
            )
            if decision is not None:
                stream = agent.stream_resume(
                    _decision_contract(decision),
                    thread_id=context["thread_id"],
                )
            elif context["checkpoint_id"]:
                stream = agent.stream_continue(
                    thread_id=context["thread_id"]
                )
            else:
                stream = agent.stream_start(
                    build_initial_state(
                        manifest=manifest,
                        assessment_id=context["assessment_uuid"],
                        agent_run_id=run_uuid,
                        analysis_goal=context["analysis_goal"],
                    ),
                    thread_id=context["thread_id"],
                )
            for event in stream:
                trace.consume(event)
            snapshot = agent.graph_snapshot(thread_id=context["thread_id"])
            return self._persist_snapshot(
                run_uuid=run_uuid,
                lease_token=lease_token,
                snapshot=snapshot,
            )
        except Exception as exc:
            checkpoint_id = None
            try:
                checkpoint_id = _snapshot_checkpoint_id(
                    agent.graph_snapshot(thread_id=context["thread_id"])
                )
            except Exception:
                pass
            self._mark_failed(
                run_uuid=run_uuid,
                lease_token=lease_token,
                checkpoint_id=checkpoint_id,
                exc=exc,
            )
            raise

    def _load_context(
        self,
        *,
        run_uuid: str,
        lease_token: str,
    ) -> dict[str, Any]:
        db = self._session_factory()
        try:
            run = (
                db.query(BidIntakeAgentRun)
                .filter(BidIntakeAgentRun.run_uuid == run_uuid)
                .one_or_none()
            )
            if (
                run is None
                or run.status != "running"
                or run.lease_token != lease_token
            ):
                raise BidIntakeExecutionError("RUN_LEASE_NOT_OWNED")
            assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == run.assessment_id)
                .one()
            )
            from app.models.bidding import BidProject

            project = (
                db.query(BidProject)
                .filter(BidProject.id == run.project_id)
                .one()
            )
            current = current_evidence_manifest(
                db,
                project_id=run.project_id,
            )
            return {
                "run_id": run.id,
                "assessment_id": assessment.id,
                "assessment_uuid": assessment.assessment_uuid,
                "analysis_goal": assessment.analysis_goal,
                "project_uuid": project.project_uuid,
                "manifest_id": assessment.manifest_id,
                "manifest_version": assessment.manifest_version,
                "manifest_hash": assessment.manifest_hash,
                "policy_version": assessment.policy_version,
                "current_manifest_id": current.id if current else None,
                "current_manifest_version": (
                    current.version_no if current else None
                ),
                "current_manifest_hash": (
                    current.manifest_hash if current else None
                ),
                "thread_id": run.thread_id,
                "checkpoint_id": run.checkpoint_id,
            }
        finally:
            db.close()

    def _build_execution_trace(
        self,
        *,
        run_uuid: str,
        lease_token: str,
        run_id: int,
        assessment_id: int,
    ) -> BidIntakeExecutionTrace:
        db = self._session_factory()
        try:
            rows = (
                db.query(BidIntakeRunEvent)
                .filter(
                    BidIntakeRunEvent.run_id == run_id,
                    BidIntakeRunEvent.event_type.like("trace_step_%"),
                )
                .order_by(
                    BidIntakeRunEvent.created_at.asc(),
                    BidIntakeRunEvent.id.asc(),
                )
                .all()
            )
            payloads = [
                _load_trace_payload(row.payload_json)
                for row in rows
            ]
        finally:
            db.close()
        sequence, frontier, react_iteration = restore_trace_position(
            payloads
        )

        def persist(
            event_type: str,
            message: str,
            payload: dict[str, Any],
        ) -> None:
            self._persist_trace_event(
                run_uuid=run_uuid,
                lease_token=lease_token,
                assessment_id=assessment_id,
                event_type=event_type,
                message=message,
                payload=payload,
            )

        return BidIntakeExecutionTrace(
            emit=persist,
            sequence=sequence,
            frontier=frontier,
            react_iteration=react_iteration,
        )

    def _persist_trace_event(
        self,
        *,
        run_uuid: str,
        lease_token: str,
        assessment_id: int,
        event_type: str,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        db = self._session_factory()
        try:
            run = _owned_run(
                db,
                run_uuid=run_uuid,
                lease_token=lease_token,
            )
            node_name = str(payload.get("node_name") or "running")
            run.phase = node_name[:64]
            append_run_event(
                db,
                assessment_id=assessment_id,
                run_id=run.id,
                event_type=event_type,
                status=run.status,
                phase=run.phase,
                message=message,
                payload=payload,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _load_pending_decision(
        self,
        *,
        run_id: int,
    ) -> BidIntakeHumanDecision | None:
        db = self._session_factory()
        try:
            decision = queued_human_decision(db, run_id=run_id)
            if decision is not None:
                db.expunge(decision)
            return decision
        finally:
            db.close()

    def _persist_snapshot(
        self,
        *,
        run_uuid: str,
        lease_token: str,
        snapshot,
    ) -> dict[str, Any]:
        state = dict(snapshot.values or {})
        waiting_human = bool(snapshot.interrupts)
        now = datetime.now(timezone.utc)
        db = self._session_factory()
        try:
            run = _owned_run(
                db,
                run_uuid=run_uuid,
                lease_token=lease_token,
            )
            assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == run.assessment_id)
                .one()
            )
            phase = str(state.get("phase") or "unknown")[:64]
            checkpoint_id = _snapshot_checkpoint_id(snapshot)
            run.checkpoint_id = checkpoint_id
            run.versions_json = _dump_json(state.get("versions") or {})
            run.state_summary_json = _dump_json(
                _state_summary(state, snapshot=snapshot)
            )
            run.worker_id = None
            run.lease_token = None
            run.lease_expires_at = None
            run.error_code = None
            run.error_message = None

            draft = state.get("assessment_draft")
            policy = state.get("policy_evaluation")
            gate = state.get("gate_result")
            assessment.assessment_json = _dump_json(draft) if draft else None
            assessment.policy_evaluation_json = (
                _dump_json(policy) if policy else None
            )
            assessment.gate_result_json = _dump_json(gate) if gate else None
            assessment.recommendation = (
                str(policy.get("decision"))[:40]
                if isinstance(policy, dict) and policy.get("decision")
                else str(draft.get("recommendation"))[:40]
                if isinstance(draft, dict)
                and draft.get("recommendation")
                else None
            )
            assessment.gate_status = (
                str(gate.get("status"))[:40]
                if isinstance(gate, dict) and gate.get("status")
                else None
            )

            if waiting_human:
                run.status = "waiting_human"
                run.phase = "human_review"
                run.paused_at = now
                assessment.status = "waiting_human"
                event_type = "human_review_paused"
                message = "LangGraph 已持久化暂停，等待人工决策。"
            elif snapshot.next:
                run.status = "queued"
                run.phase = phase
                assessment.status = "queued"
                event_type = "run_yielded"
                message = "LangGraph 尚有后续节点，任务已重新入队。"
            else:
                run.status = "completed"
                run.phase = phase
                run.finished_at = now
                assessment.status = phase
                event_type = "run_completed"
                message = "研判运行已完成。"
                _apply_consumed_decision(
                    db,
                    run_id=run.id,
                    state=state,
                    applied_at=now,
                )

            append_run_event(
                db,
                assessment_id=assessment.id,
                run_id=run.id,
                event_type=event_type,
                status=run.status,
                phase=run.phase,
                message=message,
                payload={
                    "checkpoint_id": checkpoint_id,
                    "next": list(snapshot.next),
                    "interrupt_count": len(snapshot.interrupts),
                },
            )
            db.commit()
            return {
                "run_uuid": run.run_uuid,
                "assessment_uuid": assessment.assessment_uuid,
                "status": run.status,
                "phase": run.phase,
                "checkpoint_id": checkpoint_id,
                "waiting_human": waiting_human,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_stale_manifest(
        self,
        *,
        run_uuid: str,
        lease_token: str,
        observed_manifest: DocumentManifest,
    ) -> None:
        db = self._session_factory()
        try:
            run = _owned_run(
                db,
                run_uuid=run_uuid,
                lease_token=lease_token,
            )
            assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == run.assessment_id)
                .one()
            )
            run.status = "blocked_stale_manifest"
            run.phase = "blocked_stale_manifest"
            run.error_code = "STALE_MANIFEST"
            run.error_message = "当前资料清单与本次研判绑定版本不一致。"
            run.finished_at = datetime.now(timezone.utc)
            run.worker_id = None
            run.lease_token = None
            run.lease_expires_at = None
            assessment.status = "blocked_stale_manifest"
            append_run_event(
                db,
                assessment_id=assessment.id,
                run_id=run.id,
                event_type="run_blocked_stale_manifest",
                status=run.status,
                phase=run.phase,
                message=run.error_message,
                payload={
                    "bound_manifest_version": assessment.manifest_version,
                    "observed_manifest_version": (
                        observed_manifest.manifest_version
                    ),
                    "bound_manifest_hash": assessment.manifest_hash,
                    "observed_manifest_hash": observed_manifest.manifest_hash,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_failed(
        self,
        *,
        run_uuid: str,
        lease_token: str,
        checkpoint_id: str | None,
        exc: Exception,
    ) -> None:
        db = self._session_factory()
        try:
            run = (
                db.query(BidIntakeAgentRun)
                .filter(BidIntakeAgentRun.run_uuid == run_uuid)
                .one_or_none()
            )
            if (
                run is None
                or run.status != "running"
                or run.lease_token != lease_token
            ):
                return
            assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == run.assessment_id)
                .one()
            )
            run.status = "failed"
            run.phase = "failed"
            run.checkpoint_id = checkpoint_id or run.checkpoint_id
            run.error_code = _error_code(exc)
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            run.worker_id = None
            run.lease_token = None
            run.lease_expires_at = None
            assessment.status = "failed"
            decision = queued_human_decision(db, run_id=run.id)
            if decision is not None:
                decision.status = "failed"
                decision.error_message = run.error_message
            append_run_event(
                db,
                assessment_id=assessment.id,
                run_id=run.id,
                event_type="run_failed",
                status=run.status,
                phase=run.phase,
                message="Agent 运行失败，可在重试预算内从 Checkpoint 恢复。",
                payload={
                    "error_code": run.error_code,
                    "checkpoint_id": run.checkpoint_id,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _owned_run(
    db: Session,
    *,
    run_uuid: str,
    lease_token: str,
) -> BidIntakeAgentRun:
    run = (
        db.query(BidIntakeAgentRun)
        .filter(BidIntakeAgentRun.run_uuid == run_uuid)
        .one_or_none()
    )
    if (
        run is None
        or run.status != "running"
        or run.lease_token != lease_token
    ):
        raise BidIntakeExecutionError("RUN_LEASE_NOT_OWNED")
    return run


def _decision_contract(
    decision: BidIntakeHumanDecision,
) -> HumanDecision:
    try:
        conditions = json.loads(decision.conditions_json or "[]")
    except (TypeError, ValueError):
        conditions = []
    return HumanDecision(
        decision_id=decision.decision_uuid,
        action=decision.action,
        report_version=decision.report_version,
        manifest_version=decision.manifest_version,
        decided_by=decision.decided_by_name,
        note=decision.note,
        conditions=conditions,
    )


def _apply_consumed_decision(
    db: Session,
    *,
    run_id: int,
    state: dict[str, Any],
    applied_at: datetime,
) -> None:
    human_decision = state.get("human_decision")
    if not isinstance(human_decision, dict):
        return
    decision_id = str(human_decision.get("decision_id") or "")
    if not decision_id:
        return
    decision = (
        db.query(BidIntakeHumanDecision)
        .filter(
            BidIntakeHumanDecision.run_id == run_id,
            BidIntakeHumanDecision.decision_uuid == decision_id,
        )
        .one_or_none()
    )
    if decision is not None:
        decision.status = "applied"
        decision.applied_at = applied_at
        decision.error_message = None


def _state_summary(state: dict[str, Any], *, snapshot) -> dict[str, Any]:
    draft = state.get("assessment_draft")
    gate = state.get("gate_result")
    interrupt_payloads = [
        getattr(item, "value", None) for item in snapshot.interrupts
    ]
    return {
        "phase": state.get("phase"),
        "report_version": state.get("report_version"),
        "recommendation": (
            draft.get("recommendation") if isinstance(draft, dict) else None
        ),
        "confidence": (
            draft.get("confidence") if isinstance(draft, dict) else None
        ),
        "gate_status": (
            gate.get("status") if isinstance(gate, dict) else None
        ),
        "termination_reason": state.get("termination_reason"),
        "reasoning_loop_count": state.get("reasoning_loop_count"),
        "tool_call_count": state.get("tool_call_count"),
        "output_repair_count": state.get("output_repair_count"),
        "repair_count": state.get("repair_count"),
        "next": list(snapshot.next),
        "interrupts": interrupt_payloads,
        "errors": state.get("errors") or [],
    }


def _snapshot_checkpoint_id(snapshot) -> str | None:
    configurable = (snapshot.config or {}).get("configurable", {})
    checkpoint_id = configurable.get("checkpoint_id")
    return str(checkpoint_id) if checkpoint_id else None


def _error_code(exc: Exception) -> str:
    text = str(exc)
    known = (
        "STALE_REPORT",
        "STALE_MANIFEST",
        "APPROVAL_BLOCKED_BY_EVIDENCE_GATE",
        "APPROVAL_BLOCKED_PENDING_SUPPLEMENT",
        "RUN_LEASE_NOT_OWNED",
    )
    for code in known:
        if text.startswith(code):
            return code
    return "AGENT_EXECUTION_FAILED"


def _dump_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _load_trace_payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}
