"""External Assessment snapshot and resource-version helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidManifestDocument,
)
from app.models.bid_assessment_runtime import BidAnalysisRun


def _utc_rfc3339(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def assessment_etag(assessment_id: str, row_version: int) -> str:
    """Return the strong ETag format used by Assessment read/write APIs."""

    return f'"bid-assessment:{assessment_id}:{int(row_version)}"'


def _allowed_action(
    code: str,
    *,
    assessment_id: str,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "enabled": True,
        "requires_confirmation": requires_confirmation,
        "reason_code": None,
        "target": {"assessment_id": assessment_id},
    }


def _navigation_for(assessment: BidAssessment) -> tuple[str, str | None, list[dict[str, Any]], dict[str, str] | None]:
    assessment_id = str(assessment.id)
    status = str(assessment.business_status)
    if status == "draft":
        return (
            "overview",
            "assessment.edit_metadata",
            [
                _allowed_action("assessment.edit_metadata", assessment_id=assessment_id),
                _allowed_action(
                    "assessment.abandon_draft",
                    assessment_id=assessment_id,
                    requires_confirmation=True,
                ),
            ],
            None,
        )
    if status == "awaiting_files":
        return (
            "documents",
            "upload_batch.create",
            [
                _allowed_action("upload_batch.create", assessment_id=assessment_id),
                _allowed_action("assessment.edit_metadata", assessment_id=assessment_id),
                _allowed_action(
                    "assessment.abandon_draft",
                    assessment_id=assessment_id,
                    requires_confirmation=True,
                ),
            ],
            {
                "code": "FILES_REQUIRED",
                "message": "请上传并提交有效的招标资料",
            },
        )

    recommended_views = {
        "preparing": "progress",
        "awaiting_lot_selection": "documents",
        "preliminary_analyzing": "progress",
        "preliminary_ready": "reports",
        "awaiting_owner_input": "questions",
        "deep_analyzing": "progress",
        "validating": "progress",
        "deep_ready": "reports",
        "stale_input": "overview",
        "failed": "progress",
        "cancelled": "overview",
        "superseded": "overview",
    }
    actions: list[dict[str, Any]] = []
    if assessment.active_run_id:
        actions.append(
            {
                **_allowed_action("run.view_progress", assessment_id=assessment_id),
                "target": {
                    "assessment_id": assessment_id,
                    "run_id": str(assessment.active_run_id),
                },
            }
        )
    return recommended_views.get(status, "overview"), None, actions, None


def _scope_snapshot(db: Session, assessment_id: str) -> dict[str, Any] | None:
    scope = (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == assessment_id)
        .order_by(BidAssessmentScope.version.desc())
        .first()
    )
    if scope is None:
        return None
    selected = dict(scope.selected_lot_snapshot_json or {})
    lot_id = selected.get("lot_id") or scope.source_lot_candidate_id or scope.id
    lot_name = selected.get("lot_name") or selected.get("name") or "未命名标段"
    return {
        "scope_id": str(scope.id),
        "lot_id": str(lot_id),
        "lot_code": selected.get("lot_code"),
        "lot_name": str(lot_name)[:300],
        "scope_version": int(scope.version),
    }


def _manifest_summary(db: Session, manifest_id: str | None) -> dict[str, Any] | None:
    if not manifest_id:
        return None
    manifest = db.query(BidDocumentManifest).filter(BidDocumentManifest.id == manifest_id).one_or_none()
    if manifest is None:
        return None
    document_count = int(
        db.query(func.count(BidManifestDocument.document_version_id))
        .filter(BidManifestDocument.manifest_id == manifest.id)
        .scalar()
        or 0
    )
    return {
        "manifest_id": str(manifest.id),
        "version": int(manifest.version),
        "document_count": document_count,
        "committed_at": _utc_rfc3339(manifest.created_at),
    }


def _active_run_summary(db: Session, assessment: BidAssessment) -> dict[str, Any] | None:
    if not assessment.active_run_id:
        return None
    run = (
        db.query(BidAnalysisRun)
        .filter(
            BidAnalysisRun.id == assessment.active_run_id,
            BidAnalysisRun.assessment_id == assessment.id,
        )
        .one_or_none()
    )
    if run is None:
        return None
    return {
        "run_id": str(run.id),
        "status": str(run.status),
        "run_kind": str(run.run_kind),
        "input_manifest_id": str(run.manifest_id),
        "progress_url": f"/api/v1/bid-assessments/{assessment.id}/runs/{run.id}",
    }


def build_assessment_snapshot(db: Session, assessment: BidAssessment) -> dict[str, Any]:
    """Build the frozen external AssessmentSnapshot from persisted state."""

    recommended_view, primary_action, allowed_actions, blocking_reason = _navigation_for(assessment)
    return {
        "assessment_id": str(assessment.id),
        "title": str(assessment.title),
        "client_name": str(assessment.client_name),
        "internal_note": assessment.internal_note,
        "lifecycle_status": str(assessment.lifecycle_status),
        "business_status": str(assessment.business_status),
        "row_version": int(assessment.row_version),
        "scope": _scope_snapshot(db, str(assessment.id)),
        "current_manifest": _manifest_summary(db, assessment.current_manifest_id),
        "active_run": _active_run_summary(db, assessment),
        "latest_reports": {"preliminary": None, "deep": None},
        "blocking_reason": blocking_reason,
        "recommended_view": recommended_view,
        "primary_action": primary_action,
        "allowed_actions": allowed_actions,
        "created_at": _utc_rfc3339(assessment.created_at),
        "updated_at": _utc_rfc3339(assessment.updated_at),
    }
