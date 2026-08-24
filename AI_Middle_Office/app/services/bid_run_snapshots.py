"""Private, actor-authorized Run progress projections for API-40/API-41."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessmentScope, BidDocumentManifest
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_eventing import BidPublicEvent
from app.models.bid_assessment_release import BidHardGateComparisonBaseline
from app.models.bid_assessment_runtime import BidAnalysisRun, BidTask
from app.services.bid_assessment_eventing import as_utc, canonical_hash


_STAGE_LABELS = {
    "planning": "运行规划",
    "fact_baseline": "事实覆盖基线",
    "preliminary_analysis": "初筛研判",
    "deep_analysis": "深入研判",
    "validation": "一致性校验",
    "reporting": "报告生成",
    "cancelling": "正在取消",
    "cancelled": "已取消",
    "completed": "运行完成",
    "stale": "输入已失效",
}


def _utc_rfc3339(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _row(db: Session, model: type, row_id: str) -> Any:
    value = db.query(model).filter(model.id == row_id).one_or_none()
    if value is None:
        raise RuntimeError(f"BID_RUN_FROZEN_INPUT_MISSING:{model.__name__}:{row_id}")
    return value


def _input_versions(db: Session, run: BidAnalysisRun) -> dict[str, Any]:
    manifest = _row(db, BidDocumentManifest, str(run.manifest_id))
    scope = _row(db, BidAssessmentScope, str(run.scope_id))
    enterprise = _row(db, BidEnterpriseSnapshot, str(run.enterprise_snapshot_id))
    rule_set = _row(db, BidRuleSet, str(run.rule_set_id))
    fact_catalog = _row(db, BidFactCatalogVersion, str(run.fact_catalog_version_id))
    prompt_bundle = _row(db, BidPromptBundle, str(run.prompt_bundle_id))
    tool_registry = _row(db, BidToolRegistryVersion, str(run.tool_registry_version_id))
    model_profile = _row(db, BidModelProfileVersion, str(run.model_profile_version_id))
    formula_catalog = _row(db, BidFormulaCatalogVersion, str(run.formula_catalog_version_id))
    versions = {
        "manifest_id": str(manifest.id),
        "manifest_version": int(manifest.version),
        "scope_id": str(scope.id),
        "scope_version": int(scope.version),
        "enterprise_snapshot_version": str(enterprise.version),
        "rule_set_version": str(rule_set.version),
        "fact_catalog_version": str(fact_catalog.version),
        "prompt_bundle_version": str(prompt_bundle.version),
        "tool_registry_version": str(tool_registry.version),
        "model_profile_version": str(model_profile.version),
        "formula_catalog_version": str(formula_catalog.version),
        "evaluation_time": _utc_rfc3339(run.evaluation_time),
    }
    if run.hard_gate_comparison_baseline_id or run.hard_gate_comparison_baseline_hash:
        comparison = _row(
            db,
            BidHardGateComparisonBaseline,
            str(run.hard_gate_comparison_baseline_id or ""),
        )
        if str(comparison.baseline_hash) != str(
            run.hard_gate_comparison_baseline_hash or ""
        ):
            raise RuntimeError("BID_RUN_HARD_GATE_COMPARISON_BINDING_STALE")
        versions.update(
            {
                "hard_gate_comparison_baseline_id": str(comparison.id),
                "hard_gate_comparison_baseline_version": str(comparison.version),
                "hard_gate_comparison_baseline_hash": str(comparison.baseline_hash),
            }
        )
    return versions


def _stage_snapshot(db: Session, run: BidAnalysisRun) -> list[dict[str, Any]]:
    stage_code = str(run.current_stage or "planning")
    run_status = str(run.status)
    if run.cancel_requested_at is not None and run_status not in {
        "succeeded",
        "stale",
        "cancelled",
    }:
        # API-42 deliberately keeps the durable business status unchanged
        # until maintenance completes the fence.  The actor-facing stage must
        # nevertheless describe the in-flight cancellation, not the previous
        # failed/waiting state.
        stage_status = "running"
    elif run_status == "created":
        stage_status = "not_started"
    elif run_status in {"planning", "queued", "running", "waiting_operation", "validating"}:
        stage_status = "running"
    elif run_status == "waiting_input":
        stage_status = "waiting_user"
    elif run_status == "succeeded":
        stage_status = "succeeded"
    elif run_status == "failed":
        stage_status = "failed"
    elif run_status == "stale":
        stage_status = "stale"
    else:
        stage_status = "skipped"
    task_statuses = [
        str(row[0])
        for row in db.query(BidTask.status)
        .filter(BidTask.run_id == run.id)
        .all()
    ]
    total_units = len(task_statuses) or 1
    completed_units = sum(
        1 for status in task_statuses if status in {"succeeded", "skipped"}
    )
    if not task_statuses and stage_status == "succeeded":
        completed_units = 1
    return [
        {
            "code": stage_code[:80],
            "label": _STAGE_LABELS.get(stage_code, stage_code.replace("_", " ")[:100]),
            "status": stage_status,
            "completed_units": completed_units,
            "total_units": total_units,
            "message": str(run.waiting_reason or "")[:500],
            "started_at": _utc_rfc3339(run.started_at),
            "finished_at": _utc_rfc3339(run.finished_at),
        }
    ]


def _latest_public_event(db: Session, run: BidAnalysisRun) -> dict[str, Any] | None:
    event = (
        db.query(BidPublicEvent)
        .filter(
            BidPublicEvent.assessment_id == run.assessment_id,
            BidPublicEvent.resource_type == "run",
            BidPublicEvent.resource_id == run.id,
        )
        .order_by(BidPublicEvent.sequence_no.desc())
        .first()
    )
    if event is None:
        return None
    return {
        "event_id": str(event.event_id),
        "event_type": str(event.event_type),
        "resource_version": int(event.resource_version),
        "occurred_at": _utc_rfc3339(event.occurred_at),
    }


def _allowed_actions(run: BidAnalysisRun) -> list[dict[str, Any]]:
    actions = [
        {
            "code": "run.view_progress",
            "enabled": True,
            "requires_confirmation": False,
            "reason_code": None,
            "target": {
                "assessment_id": str(run.assessment_id),
                "run_id": str(run.id),
            },
        }
    ]
    if run.cancel_requested_at is not None:
        return actions
    status = str(run.status)
    if status in {
        "created",
        "planning",
        "queued",
        "running",
        "waiting_input",
        "waiting_operation",
        "validating",
    } or (status == "failed" and bool(run.retryable)):
        actions.append(
            {
                "code": "run.cancel",
                "enabled": True,
                "requires_confirmation": True,
                "reason_code": None,
                "target": {
                    "assessment_id": str(run.assessment_id),
                    "run_id": str(run.id),
                },
            }
        )
    if status == "failed" and bool(run.retryable):
        actions.append(
            {
                "code": "run.retry_from_checkpoint",
                "enabled": True,
                "requires_confirmation": True,
                "reason_code": None,
                "target": {
                    "assessment_id": str(run.assessment_id),
                    "run_id": str(run.id),
                },
            }
        )
    return actions


def build_run_progress_snapshot(db: Session, run: BidAnalysisRun) -> dict[str, Any]:
    waiting = None
    if str(run.status) in {"waiting_input", "waiting_operation"}:
        waiting = {
            "type": "owner_input" if str(run.status) == "waiting_input" else "async_operation",
            "resource_id": str(run.id),
            "reason": str(run.waiting_reason or "")[:500],
        }
    return {
        "run_id": str(run.id),
        "assessment_id": str(run.assessment_id),
        "run_kind": str(run.run_kind),
        "status": str(run.status),
        "row_version": int(run.row_version),
        "input_fingerprint": str(run.input_fingerprint),
        "input_hash": str(run.input_hash),
        "input_versions": _input_versions(db, run),
        "stages": _stage_snapshot(db, run),
        "current_stage": str(run.current_stage) if run.current_stage else None,
        "waiting_reason": str(run.waiting_reason) if run.waiting_reason else None,
        "waiting": waiting,
        "latest_event": _latest_public_event(db, run),
        "allowed_actions": _allowed_actions(run),
        "retryable": bool(run.retryable),
        "cancel_requested_at": _utc_rfc3339(run.cancel_requested_at),
        "last_checkpoint_at": _utc_rfc3339(run.last_checkpoint_at),
        "started_at": _utc_rfc3339(run.started_at),
        "finished_at": _utc_rfc3339(run.finished_at),
    }


def run_etag(run_id: str, row_version: int, snapshot: dict[str, Any]) -> str:
    """Hash the complete actor-visible projection, including latest event."""

    return (
        f'"bid-run:{run_id}:{int(row_version)}:'
        f'{canonical_hash(snapshot)[:12]}"'
    )


def run_snapshot_headers(run: BidAnalysisRun, snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        "ETag": run_etag(str(run.id), int(run.row_version), snapshot),
        "X-Resource-Version": str(int(run.row_version)),
        "Cache-Control": "private, no-cache, max-age=0, must-revalidate",
        "Vary": "Authorization",
    }
