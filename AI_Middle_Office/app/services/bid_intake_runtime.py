from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.bid_intake_runtime import (
    BidIntakeAgentRun,
    BidIntakeAssessment,
    BidIntakeHumanDecision,
    BidIntakeRunEvent,
    BidIntakeWorkerHeartbeat,
)
from app.models.bidding import BidProject
from app.models.tender_evidence import BidEvidenceManifest
from app.models.tender_evidence import BidEvidenceDocument
from app.models.tender_evidence_index import BidEvidenceIndexJob
from app.models.user import User
from app.agents.bid_intake.runtime_config import (
    model_configuration_summary,
)
from app.services.bid_policy_catalog import (
    BidPolicyCatalogError,
    active_bid_policy_version,
)


DEFAULT_ANALYSIS_GOAL = "判断该招标项目是否值得进入报价立项。"
HUMAN_ACTIONS = frozenset(
    {
        "approved",
        "approved_with_conditions",
        "rejected",
        "supplement_requested",
        "research_requested",
    }
)
APPROVAL_ACTIONS = frozenset(
    {"approved", "approved_with_conditions"}
)
CANCELLABLE_RUN_STATUSES = frozenset(
    {"queued", "running", "resume_queued"}
)
TERMINAL_RUN_STATUSES = frozenset(
    {
        "completed",
        "cancelled",
        "blocked_stale_manifest",
    }
)
RUNTIME_VERSION = "bid_intake_runtime_phase5a"


class BidIntakeRuntimeError(RuntimeError):
    code = "BID_INTAKE_RUNTIME_ERROR"


class BidIntakeRuntimeNotFound(BidIntakeRuntimeError):
    code = "BID_INTAKE_RUNTIME_NOT_FOUND"


class BidIntakeRuntimeConflict(BidIntakeRuntimeError):
    code = "BID_INTAKE_RUNTIME_CONFLICT"


@dataclass(frozen=True)
class CreatedAssessmentRun:
    assessment: BidIntakeAssessment
    run: BidIntakeAgentRun


@dataclass(frozen=True)
class ClaimedRun:
    run_uuid: str
    lease_token: str
    recovered: bool


def touch_worker_heartbeat(
    db: Session,
    *,
    worker_id: str,
    status: str,
    process_id: int,
    hostname: str | None = None,
    current_run_uuid: str | None = None,
    capabilities: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> BidIntakeWorkerHeartbeat:
    now = datetime.now(timezone.utc)
    normalized_worker_id = str(worker_id or "").strip()[:160]
    if not normalized_worker_id:
        raise BidIntakeRuntimeConflict("WORKER_ID_REQUIRED")
    heartbeat = (
        db.query(BidIntakeWorkerHeartbeat)
        .filter(
            BidIntakeWorkerHeartbeat.worker_id == normalized_worker_id
        )
        .one_or_none()
    )
    if heartbeat is None:
        heartbeat = BidIntakeWorkerHeartbeat(
            worker_id=normalized_worker_id,
            hostname=str(hostname or socket.gethostname())[:160],
            process_id=int(process_id),
            runtime_version=RUNTIME_VERSION,
            started_at=now,
            last_seen_at=now,
        )
        db.add(heartbeat)
    heartbeat.hostname = str(hostname or heartbeat.hostname)[:160]
    heartbeat.process_id = int(process_id)
    heartbeat.runtime_version = RUNTIME_VERSION
    heartbeat.status = str(status or "online")[:24]
    heartbeat.current_run_uuid = current_run_uuid
    heartbeat.capabilities_json = _dump_json(capabilities or {})
    heartbeat.error_message = (
        str(error_message)[:2000] if error_message else None
    )
    heartbeat.last_seen_at = now
    heartbeat.stopped_at = now if heartbeat.status == "stopped" else None
    db.flush()
    return heartbeat


def worker_capabilities_from_environment() -> dict[str, Any]:
    try:
        policy_version = active_bid_policy_version()
    except BidPolicyCatalogError:
        policy_version = None
    model_summary = model_configuration_summary()
    return {
        "mcp_configured": all(
            _env_present(name)
            for name in (
                "BID_INTAKE_MCP_URL",
                "TENDER_MCP_JWT_SECRET",
            )
        ),
        **model_summary,
        "checkpoint_backend": "sqlalchemy",
        "mcp_session_mode": "persistent",
        "model_protocol": "openai_compatible",
        "policy_configured": policy_version is not None,
        "active_policy_version": policy_version,
        "fact_coverage_mode": _fact_coverage_mode_from_environment(),
    }


def build_project_runtime_readiness(
    db: Session,
    *,
    project_id: int,
    online_window_seconds: int | None = None,
) -> dict[str, Any]:
    window_seconds = online_window_seconds or _env_int(
        "BID_INTAKE_WORKER_ONLINE_SECONDS",
        30,
    )
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=max(10, min(window_seconds, 300))
    )
    workers = (
        db.query(BidIntakeWorkerHeartbeat)
        .filter(
            BidIntakeWorkerHeartbeat.last_seen_at >= threshold,
            BidIntakeWorkerHeartbeat.status.in_(
                ("online", "busy", "error")
            ),
        )
        .order_by(BidIntakeWorkerHeartbeat.last_seen_at.desc())
        .all()
    )
    worker_payloads = [
        {
            "worker_id": item.worker_id,
            "status": item.status,
            "runtime_version": item.runtime_version,
            "current_run_uuid": item.current_run_uuid,
            "capabilities": _load_json(item.capabilities_json, {}),
            "last_seen_at": _iso(item.last_seen_at),
            "error_message": item.error_message,
        }
        for item in workers
    ]
    healthy_workers = [
        item for item in worker_payloads if item["status"] != "error"
    ]
    try:
        active_policy_version = active_bid_policy_version()
    except BidPolicyCatalogError:
        active_policy_version = None
    capable_workers = [
        item
        for item in healthy_workers
        if item["capabilities"].get("mcp_configured")
        and item["capabilities"].get("model_configured")
        and item["capabilities"].get("policy_configured")
        and item["capabilities"].get("active_policy_version")
        == active_policy_version
    ]
    capabilities = (
        capable_workers[0]["capabilities"]
        if capable_workers
        else healthy_workers[0]["capabilities"]
        if healthy_workers
        else {}
    )

    manifest = current_evidence_manifest(db, project_id=project_id)
    ready_document_count = (
        db.query(BidEvidenceDocument)
        .filter(
            BidEvidenceDocument.project_id == project_id,
            BidEvidenceDocument.active.is_(True),
            BidEvidenceDocument.parse_status == "ready",
        )
        .count()
    )
    index_job = None
    if manifest is not None:
        index_job = (
            db.query(BidEvidenceIndexJob)
            .filter(
                BidEvidenceIndexJob.project_id == project_id,
                BidEvidenceIndexJob.manifest_id == manifest.id,
            )
            .order_by(BidEvidenceIndexJob.id.desc())
            .first()
        )
    hybrid_ready = bool(
        index_job
        and index_job.status == "completed"
        and index_job.indexed_block_count == index_job.requested_block_count
    )
    runtime_enabled = _env_bool(
        "BID_INTAKE_AGENT_RUNTIME_ENABLED",
        False,
    )
    blockers: list[str] = []
    if not runtime_enabled:
        blockers.append("RUNTIME_DISABLED")
    if manifest is None:
        blockers.append("ACTIVE_MANIFEST_REQUIRED")
    if ready_document_count < 1:
        blockers.append("READY_EVIDENCE_REQUIRED")
    if active_policy_version is None:
        blockers.append("POLICY_NOT_CONFIGURED")
    if not healthy_workers:
        blockers.append("WORKER_OFFLINE")
    if healthy_workers and not any(
        item["capabilities"].get("mcp_configured")
        for item in healthy_workers
    ):
        blockers.append("MCP_NOT_CONFIGURED")
    if healthy_workers and not any(
        item["capabilities"].get("model_configured")
        for item in healthy_workers
    ):
        blockers.append("MODEL_NOT_CONFIGURED")
    if (
        healthy_workers
        and active_policy_version is not None
        and not any(
            item["capabilities"].get("policy_configured")
            for item in healthy_workers
        )
    ):
        blockers.append("WORKER_POLICY_NOT_CONFIGURED")
    elif (
        healthy_workers
        and active_policy_version is not None
        and not any(
            item["capabilities"].get("active_policy_version")
            == active_policy_version
            for item in healthy_workers
        )
    ):
        blockers.append("POLICY_VERSION_MISMATCH")
    if (
        healthy_workers
        and not capable_workers
        and "MCP_NOT_CONFIGURED" not in blockers
        and "MODEL_NOT_CONFIGURED" not in blockers
        and "POLICY_NOT_CONFIGURED" not in blockers
        and "WORKER_POLICY_NOT_CONFIGURED" not in blockers
        and "POLICY_VERSION_MISMATCH" not in blockers
    ):
        blockers.append("WORKER_CAPABILITY_MISMATCH")
    return {
        "runtime_enabled": runtime_enabled,
        "ready_to_start": not blockers,
        "blockers": blockers,
        "worker": {
            "online": bool(healthy_workers),
            "online_count": len(healthy_workers),
            "error_count": len(worker_payloads) - len(healthy_workers),
            "latest": worker_payloads[0] if worker_payloads else None,
        },
        "evidence": {
            "manifest_version": manifest.version_no if manifest else None,
            "manifest_hash": manifest.manifest_hash if manifest else None,
            "ready_document_count": ready_document_count,
            "search_backend": (
                "hybrid_rrf" if hybrid_ready else "database_lexical"
            ),
            "hybrid_ready": hybrid_ready,
            "index_status": index_job.status if index_job else None,
        },
        "capabilities": capabilities,
        "policy": {
            "active_version": active_policy_version,
            "configured": active_policy_version is not None,
        },
    }


def create_assessment_run(
    db: Session,
    *,
    project: BidProject,
    current_user: User,
    analysis_goal: str = DEFAULT_ANALYSIS_GOAL,
    trigger_source: str = "manual",
    max_attempts: int = 3,
) -> CreatedAssessmentRun:
    manifest = current_evidence_manifest(db, project_id=project.id)
    if manifest is None:
        raise BidIntakeRuntimeConflict("ACTIVE_EVIDENCE_MANIFEST_REQUIRED")
    try:
        policy_version = active_bid_policy_version()
    except BidPolicyCatalogError as exc:
        raise BidIntakeRuntimeConflict(
            "ACTIVE_BID_POLICY_REQUIRED"
        ) from exc
    goal = str(analysis_goal or "").strip()
    if not goal:
        raise BidIntakeRuntimeConflict("ANALYSIS_GOAL_REQUIRED")

    assessment_uuid = str(uuid.uuid4())
    run_uuid = str(uuid.uuid4())
    assessment = BidIntakeAssessment(
        assessment_uuid=assessment_uuid,
        project_id=project.id,
        manifest_id=manifest.id,
        manifest_version=manifest.version_no,
        manifest_hash=manifest.manifest_hash,
        policy_version=policy_version,
        analysis_goal=goal,
        status="queued",
        report_version=1,
        latest_run_uuid=run_uuid,
        created_by=current_user.id,
    )
    db.add(assessment)
    db.flush()

    run = BidIntakeAgentRun(
        run_uuid=run_uuid,
        assessment_id=assessment.id,
        project_id=project.id,
        thread_id=f"bid-intake:{run_uuid}",
        status="queued",
        phase="queued",
        trigger_source=str(trigger_source or "manual")[:32],
        max_attempts=max(1, min(int(max_attempts), 10)),
        created_by=current_user.id,
    )
    db.add(run)
    db.flush()
    append_run_event(
        db,
        assessment_id=assessment.id,
        run_id=run.id,
        event_type="run_queued",
        status=run.status,
        phase=run.phase,
        message="研判任务已进入持久化 Agent 队列。",
        payload={
            "manifest_version": manifest.version_no,
            "manifest_hash": manifest.manifest_hash,
            "trigger_source": run.trigger_source,
        },
    )
    return CreatedAssessmentRun(assessment=assessment, run=run)


def current_evidence_manifest(
    db: Session,
    *,
    project_id: int,
) -> BidEvidenceManifest | None:
    return (
        db.query(BidEvidenceManifest)
        .filter(
            BidEvidenceManifest.project_id == project_id,
            BidEvidenceManifest.active.is_(True),
        )
        .order_by(
            BidEvidenceManifest.version_no.desc(),
            BidEvidenceManifest.id.desc(),
        )
        .first()
    )


def queue_human_decision(
    db: Session,
    *,
    assessment: BidIntakeAssessment,
    run: BidIntakeAgentRun,
    current_user: User,
    decision_uuid: str,
    action: str,
    report_version: int,
    manifest_version: int,
    note: str | None = None,
    conditions: list[str] | None = None,
) -> tuple[BidIntakeHumanDecision, bool]:
    normalized_action = str(action or "").strip()
    if normalized_action not in HUMAN_ACTIONS:
        raise BidIntakeRuntimeConflict("INVALID_HUMAN_ACTION")
    normalized_decision_uuid = str(decision_uuid or "").strip()
    try:
        uuid.UUID(normalized_decision_uuid)
    except (ValueError, TypeError) as exc:
        raise BidIntakeRuntimeConflict("INVALID_DECISION_UUID") from exc

    existing = (
        db.query(BidIntakeHumanDecision)
        .filter(
            BidIntakeHumanDecision.decision_uuid
            == normalized_decision_uuid
        )
        .one_or_none()
    )
    normalized_conditions = [
        str(item).strip()
        for item in (conditions or [])
        if str(item).strip()
    ]
    if existing is not None:
        same_command = (
            existing.assessment_id == assessment.id
            and existing.run_id == run.id
            and existing.action == normalized_action
            and existing.report_version == int(report_version)
            and existing.manifest_version == int(manifest_version)
            and existing.decided_by == current_user.id
            and _load_json(existing.conditions_json, []) == normalized_conditions
            and (existing.note or None) == (str(note).strip() if note else None)
        )
        if not same_command:
            raise BidIntakeRuntimeConflict("DECISION_IDEMPOTENCY_CONFLICT")
        return existing, True

    if run.status != "waiting_human":
        raise BidIntakeRuntimeConflict("RUN_NOT_WAITING_FOR_HUMAN")
    if int(report_version) != assessment.report_version:
        raise BidIntakeRuntimeConflict("STALE_REPORT")
    if int(manifest_version) != assessment.manifest_version:
        raise BidIntakeRuntimeConflict("STALE_MANIFEST")
    if normalized_action in APPROVAL_ACTIONS:
        gate = _load_json(assessment.gate_result_json, {})
        blocking_codes = {
            str(item.get("code") or "")
            for item in gate.get("issues", [])
            if isinstance(item, dict)
        }
        if "POLICY_REQUIRES_MANUAL_REVIEW" in blocking_codes:
            raise BidIntakeRuntimeConflict(
                "APPROVAL_BLOCKED_BY_POLICY"
            )
        if blocking_codes & {
            "REQUIRED_DIMENSION_MISSING",
            "EVIDENCE_VALIDATION_UNAVAILABLE",
            "EVIDENCE_REF_INVALID",
            "HIGH_RISK_EVIDENCE_MISSING",
            "HIGH_RISK_CONTEXT_NOT_READ",
            "POLICY_FACTOR_EVIDENCE_MISSING",
            "POLICY_FACTOR_CONTEXT_NOT_READ",
            "AGENT_TERMINATED_EARLY",
        }:
            raise BidIntakeRuntimeConflict(
                "APPROVAL_BLOCKED_BY_EVIDENCE_GATE"
            )
    current_manifest = current_evidence_manifest(db, project_id=assessment.project_id)
    if (
        current_manifest is None
        or current_manifest.id != assessment.manifest_id
        or current_manifest.version_no != assessment.manifest_version
        or current_manifest.manifest_hash != assessment.manifest_hash
    ):
        raise BidIntakeRuntimeConflict("STALE_MANIFEST")
    pending = (
        db.query(BidIntakeHumanDecision)
        .filter(
            BidIntakeHumanDecision.run_id == run.id,
            BidIntakeHumanDecision.status.in_(("queued", "applied")),
        )
        .first()
    )
    if pending is not None:
        raise BidIntakeRuntimeConflict("RUN_DECISION_ALREADY_EXISTS")

    decision = BidIntakeHumanDecision(
        decision_uuid=normalized_decision_uuid,
        assessment_id=assessment.id,
        run_id=run.id,
        action=normalized_action,
        report_version=int(report_version),
        manifest_version=int(manifest_version),
        decided_by=current_user.id,
        decided_by_name=str(current_user.username or current_user.id),
        note=str(note).strip() if note else None,
        conditions_json=_dump_json(normalized_conditions),
        status="queued",
    )
    db.add(decision)
    run.status = "resume_queued"
    run.phase = "human_decision_queued"
    run.worker_id = None
    run.lease_token = None
    run.lease_expires_at = None
    assessment.status = "resume_queued"
    db.flush()
    append_run_event(
        db,
        assessment_id=assessment.id,
        run_id=run.id,
        event_type="human_decision_queued",
        status=run.status,
        phase=run.phase,
        message="人工决策已持久化，等待从 LangGraph 暂停点恢复。",
        payload={
            "decision_uuid": decision.decision_uuid,
            "action": decision.action,
            "report_version": decision.report_version,
            "manifest_version": decision.manifest_version,
        },
    )
    return decision, False


def retry_failed_run(
    db: Session,
    *,
    assessment: BidIntakeAssessment,
    run: BidIntakeAgentRun,
) -> BidIntakeAgentRun:
    if run.status != "failed":
        raise BidIntakeRuntimeConflict("RUN_NOT_RETRYABLE")
    if run.attempt_count >= run.max_attempts:
        raise BidIntakeRuntimeConflict("RUN_ATTEMPT_BUDGET_EXHAUSTED")
    current_manifest = current_evidence_manifest(db, project_id=assessment.project_id)
    if (
        current_manifest is None
        or current_manifest.id != assessment.manifest_id
        or current_manifest.manifest_hash != assessment.manifest_hash
    ):
        raise BidIntakeRuntimeConflict("STALE_MANIFEST")
    run.status = "queued"
    run.phase = "recovery_queued" if run.checkpoint_id else "queued"
    run.worker_id = None
    run.lease_token = None
    run.lease_expires_at = None
    run.error_code = None
    run.error_message = None
    run.finished_at = None
    assessment.status = "queued"
    append_run_event(
        db,
        assessment_id=assessment.id,
        run_id=run.id,
        event_type="run_retry_queued",
        status=run.status,
        phase=run.phase,
        message="失败任务已重新入队；有 Checkpoint 时将从最近状态恢复。",
        payload={"checkpoint_id": run.checkpoint_id},
    )
    return run


def cancel_agent_run(
    db: Session,
    *,
    assessment: BidIntakeAssessment,
    run: BidIntakeAgentRun,
    current_user: User,
) -> tuple[BidIntakeAgentRun, bool]:
    """Stop one active run while preserving its audit trail.

    Clearing the lease is also the cancellation fence: a Worker that returns
    later with the old lease can no longer append trace events or persist a
    graph snapshot over the cancelled state.
    """

    if run.status == "cancelled":
        return run, True
    if run.status not in CANCELLABLE_RUN_STATUSES:
        raise BidIntakeRuntimeConflict("RUN_NOT_CANCELLABLE")

    previous_status = run.status
    now = datetime.now(timezone.utc)
    run.status = "cancelled"
    run.phase = "cancelled"
    run.finished_at = now
    run.worker_id = None
    run.lease_token = None
    run.lease_expires_at = None
    run.error_code = None
    run.error_message = None
    assessment.status = "cancelled"

    decision = queued_human_decision(db, run_id=run.id)
    if decision is not None:
        decision.status = "cancelled"
        decision.error_message = "研判已由用户终止。"

    append_run_event(
        db,
        assessment_id=assessment.id,
        run_id=run.id,
        event_type="run_cancelled",
        status=run.status,
        phase=run.phase,
        message="本次研判已由用户终止；现有运行轨迹继续保留。",
        payload={
            "previous_status": previous_status,
            "cancelled_by": current_user.id,
            "cancelled_by_name": str(
                current_user.username or current_user.id
            ),
        },
    )
    db.flush()
    return run, False


def fail_claimed_agent_run(
    db: Session,
    *,
    run_uuid: str,
    lease_token: str,
    error_code: str,
    error_message: str,
) -> bool:
    """Persist a failure that happens before the LangGraph executor starts.

    MCP session initialization and model/runtime construction happen after a
    Worker claims the run but before ``PersistentBidIntakeExecutor.execute``.
    Those failures must still release the lease and become visible to the UI.
    """

    run = (
        db.query(BidIntakeAgentRun)
        .filter(BidIntakeAgentRun.run_uuid == str(run_uuid))
        .one_or_none()
    )
    if (
        run is None
        or run.status != "running"
        or run.lease_token != str(lease_token)
    ):
        return False
    assessment = (
        db.query(BidIntakeAssessment)
        .filter(BidIntakeAssessment.id == run.assessment_id)
        .one()
    )
    normalized_code = (
        str(error_code or "AGENT_PRE_EXECUTION_FAILED")
        .strip()
        .upper()[:80]
    )
    normalized_message = str(error_message or normalized_code).strip()[:2000]
    run.status = "failed"
    run.phase = "failed"
    run.error_code = normalized_code
    run.error_message = normalized_message
    run.finished_at = datetime.now(timezone.utc)
    run.worker_id = None
    run.lease_token = None
    run.lease_expires_at = None
    assessment.status = "failed"
    decision = queued_human_decision(db, run_id=run.id)
    if decision is not None:
        decision.status = "failed"
        decision.error_message = normalized_message
    append_run_event(
        db,
        assessment_id=assessment.id,
        run_id=run.id,
        event_type="run_failed",
        status=run.status,
        phase=run.phase,
        message="Agent 在进入 LangGraph 前失败，可在重试预算内重新入队。",
        payload={
            "error_code": normalized_code,
            "failure_stage": "pre_execution",
        },
    )
    db.flush()
    return True


def claim_agent_run(
    db: Session,
    *,
    worker_id: str,
    run_uuid: str | None = None,
    lease_seconds: int = 3600,
) -> ClaimedRun | None:
    now = datetime.now(timezone.utc)
    statuses = ("queued", "resume_queued")
    query = db.query(BidIntakeAgentRun).filter(
        BidIntakeAgentRun.attempt_count < BidIntakeAgentRun.max_attempts,
        or_(
            BidIntakeAgentRun.status.in_(statuses),
            (
                (BidIntakeAgentRun.status == "running")
                & (BidIntakeAgentRun.lease_expires_at.is_not(None))
                & (BidIntakeAgentRun.lease_expires_at < now)
            ),
        ),
    )
    if run_uuid:
        query = query.filter(BidIntakeAgentRun.run_uuid == run_uuid)
    run = (
        query.order_by(
            BidIntakeAgentRun.created_at.asc(),
            BidIntakeAgentRun.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if run is None:
        return None

    recovered = run.status == "running"
    lease_token = uuid.uuid4().hex
    run.status = "running"
    run.phase = "recovering" if recovered else run.phase
    run.worker_id = str(worker_id or "bid-intake-worker")[:160]
    run.lease_token = lease_token
    run.lease_expires_at = now + timedelta(
        seconds=max(60, min(int(lease_seconds), 14_400))
    )
    run.claimed_at = now
    run.started_at = run.started_at or now
    run.paused_at = None
    run.finished_at = None
    run.attempt_count += 1
    run.error_code = None
    run.error_message = None
    assessment = (
        db.query(BidIntakeAssessment)
        .filter(BidIntakeAssessment.id == run.assessment_id)
        .one()
    )
    assessment.status = "running"
    append_run_event(
        db,
        assessment_id=run.assessment_id,
        run_id=run.id,
        event_type="run_recovered" if recovered else "run_claimed",
        status=run.status,
        phase=run.phase,
        message=(
            "租约过期任务已由新 Worker 从 Checkpoint 接管。"
            if recovered
            else "Agent Worker 已领取任务。"
        ),
        payload={
            "worker_id": run.worker_id,
            "attempt_count": run.attempt_count,
            "lease_seconds": lease_seconds,
        },
    )
    db.flush()
    return ClaimedRun(
        run_uuid=run.run_uuid,
        lease_token=lease_token,
        recovered=recovered,
    )


def get_assessment(
    db: Session,
    *,
    project_id: int,
    assessment_uuid: str,
) -> BidIntakeAssessment:
    assessment = (
        db.query(BidIntakeAssessment)
        .filter(
            BidIntakeAssessment.project_id == project_id,
            BidIntakeAssessment.assessment_uuid == assessment_uuid,
        )
        .one_or_none()
    )
    if assessment is None:
        raise BidIntakeRuntimeNotFound("BID_INTAKE_ASSESSMENT_NOT_FOUND")
    return assessment


def get_run(
    db: Session,
    *,
    assessment_id: int,
    run_uuid: str,
) -> BidIntakeAgentRun:
    run = (
        db.query(BidIntakeAgentRun)
        .filter(
            BidIntakeAgentRun.assessment_id == assessment_id,
            BidIntakeAgentRun.run_uuid == run_uuid,
        )
        .one_or_none()
    )
    if run is None:
        raise BidIntakeRuntimeNotFound("BID_INTAKE_RUN_NOT_FOUND")
    return run


def queued_human_decision(
    db: Session,
    *,
    run_id: int,
) -> BidIntakeHumanDecision | None:
    return (
        db.query(BidIntakeHumanDecision)
        .filter(
            BidIntakeHumanDecision.run_id == run_id,
            BidIntakeHumanDecision.status == "queued",
        )
        .order_by(
            BidIntakeHumanDecision.created_at.asc(),
            BidIntakeHumanDecision.id.asc(),
        )
        .first()
    )


def append_run_event(
    db: Session,
    *,
    assessment_id: int,
    run_id: int,
    event_type: str,
    status: str,
    phase: str | None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> BidIntakeRunEvent:
    event = BidIntakeRunEvent(
        event_uuid=str(uuid.uuid4()),
        assessment_id=assessment_id,
        run_id=run_id,
        event_type=event_type[:64],
        status=status[:32],
        phase=phase[:64] if phase else None,
        message=message,
        payload_json=_dump_json(payload) if payload is not None else None,
    )
    db.add(event)
    return event


def serialize_assessment(
    db: Session,
    assessment: BidIntakeAssessment,
    *,
    include_runs: bool = False,
    include_events: bool = False,
) -> dict[str, Any]:
    result = {
        "assessment_uuid": assessment.assessment_uuid,
        "project_id": assessment.project_id,
        "manifest_version": assessment.manifest_version,
        "manifest_hash": assessment.manifest_hash,
        "policy_version": assessment.policy_version,
        "analysis_goal": assessment.analysis_goal,
        "status": assessment.status,
        "report_version": assessment.report_version,
        "latest_run_uuid": assessment.latest_run_uuid,
        "recommendation": assessment.recommendation,
        "gate_status": assessment.gate_status,
        "assessment": _load_json(assessment.assessment_json, None),
        "policy_evaluation": _load_json(
            assessment.policy_evaluation_json,
            None,
        ),
        "gate_result": _load_json(assessment.gate_result_json, None),
        "created_by": assessment.created_by,
        "created_at": _iso(assessment.created_at),
        "updated_at": _iso(assessment.updated_at),
    }
    if include_runs:
        runs = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.assessment_id == assessment.id)
            .order_by(
                BidIntakeAgentRun.created_at.desc(),
                BidIntakeAgentRun.id.desc(),
            )
            .all()
        )
        result["runs"] = [
            serialize_run(
                db,
                item,
                include_events=include_events,
                include_decisions=include_events,
            )
            for item in runs
        ]
    return result


def serialize_run(
    db: Session,
    run: BidIntakeAgentRun,
    *,
    include_events: bool = False,
    include_decisions: bool = False,
) -> dict[str, Any]:
    result = {
        "run_uuid": run.run_uuid,
        "assessment_id": run.assessment_id,
        "thread_id": run.thread_id,
        "status": run.status,
        "phase": run.phase,
        "trigger_source": run.trigger_source,
        "attempt_count": run.attempt_count,
        "max_attempts": run.max_attempts,
        "checkpoint_id": run.checkpoint_id,
        "state_summary": _load_json(run.state_summary_json, None),
        "versions": _load_json(run.versions_json, None),
        "worker_id": run.worker_id,
        "lease_expires_at": _iso(run.lease_expires_at),
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_by": run.created_by,
        "claimed_at": _iso(run.claimed_at),
        "started_at": _iso(run.started_at),
        "paused_at": _iso(run.paused_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
    }
    if include_events:
        events = (
            db.query(BidIntakeRunEvent)
            .filter(BidIntakeRunEvent.run_id == run.id)
            .order_by(
                BidIntakeRunEvent.created_at.asc(),
                BidIntakeRunEvent.id.asc(),
            )
            .all()
        )
        result["events"] = [
            {
                "event_uuid": item.event_uuid,
                "event_type": item.event_type,
                "status": item.status,
                "phase": item.phase,
                "message": item.message,
                "payload": _load_json(item.payload_json, None),
                "created_at": _iso(item.created_at),
            }
            for item in events
        ]
    if include_decisions:
        decisions = (
            db.query(BidIntakeHumanDecision)
            .filter(BidIntakeHumanDecision.run_id == run.id)
            .order_by(
                BidIntakeHumanDecision.created_at.asc(),
                BidIntakeHumanDecision.id.asc(),
            )
            .all()
        )
        result["decisions"] = [
            serialize_human_decision(item) for item in decisions
        ]
    return result


def serialize_human_decision(
    decision: BidIntakeHumanDecision,
) -> dict[str, Any]:
    return {
        "decision_uuid": decision.decision_uuid,
        "action": decision.action,
        "report_version": decision.report_version,
        "manifest_version": decision.manifest_version,
        "decided_by": decision.decided_by,
        "decided_by_name": decision.decided_by_name,
        "note": decision.note,
        "conditions": _load_json(decision.conditions_json, []),
        "status": decision.status,
        "error_message": decision.error_message,
        "created_at": _iso(decision.created_at),
        "applied_at": _iso(decision.applied_at),
    }


def _dump_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _load_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _fact_coverage_mode_from_environment() -> str:
    value = os.environ.get(
        "BID_INTAKE_FACT_COVERAGE_MODE",
        "shadow",
    ).strip().lower()
    return value if value in {"off", "shadow", "enforced"} else "shadow"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
