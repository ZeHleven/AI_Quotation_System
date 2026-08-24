"""API-31: atomically bind one authoritative lot candidate as Assessment Scope."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidLotCandidate,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import BidDocumentParseHead
from app.models.bid_assessment_lots import (
    BidLotCandidateEvidence,
    BidLotDetectionHead,
    BidLotDetectionRun,
)
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    canonical_hash,
    canonical_json,
)
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_assessment_snapshots import (
    assessment_etag,
    build_assessment_snapshot,
)
from app.services.bid_lot_detection_runs import build_manifest_parse_set


LOT_SELECTION_ROUTE_TEMPLATE = (
    "/api/v1/bid-assessments/{assessment_id}/lot-selection"
)
LOT_SCOPE_SCHEMA_VERSION = "bid-assessment-lot-scope-v1"


class BidLotSelectionError(RuntimeError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"


class BidLotSelectionNotFound(BidLotSelectionError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidLotSelectionVersionMismatch(BidLotSelectionError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(self, assessment: BidAssessment, *, provided_etag: str):
        super().__init__(self.code)
        self.assessment_id = str(assessment.id)
        self.provided_etag = str(provided_etag)
        self.current_row_version = int(assessment.row_version)
        self.current_etag = assessment_etag(
            self.assessment_id,
            self.current_row_version,
        )


class BidLotSelectionStateConflict(BidLotSelectionError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"

    def __init__(self, assessment: BidAssessment):
        super().__init__(self.code)
        self.lifecycle_status = str(assessment.lifecycle_status)
        self.business_status = str(assessment.business_status)


class BidLotSelectionManifestMismatch(BidLotSelectionError):
    code = "BID_LOT_NOT_IN_MANIFEST"

    def __init__(
        self,
        *,
        provided_manifest_id: str,
        current_manifest_id: str | None,
    ):
        super().__init__(self.code)
        self.provided_manifest_id = str(provided_manifest_id)
        self.current_manifest_id = current_manifest_id


class BidLotCandidatesNotReady(BidLotSelectionError):
    code = "BID_LOT_CANDIDATES_NOT_READY"

    def __init__(self, *, status: str, reason: str):
        super().__init__(self.code)
        self.status = str(status)
        self.reason = str(reason)


class BidLotNotInManifest(BidLotSelectionError):
    code = "BID_LOT_NOT_IN_MANIFEST"

    def __init__(self, *, lot_id: str, manifest_id: str):
        super().__init__(self.code)
        self.lot_id = str(lot_id)
        self.manifest_id = str(manifest_id)


class BidLotScopeAlreadyBound(BidLotSelectionError):
    code = "BID_LOT_SCOPE_ALREADY_BOUND"

    def __init__(self, scope: BidAssessmentScope):
        super().__init__(self.code)
        snapshot = dict(scope.selected_lot_snapshot_json or {})
        self.scope_id = str(scope.id)
        self.selected_lot_id = str(
            scope.source_lot_candidate_id or snapshot.get("lot_id") or ""
        )
        self.manifest_id = str(snapshot.get("manifest_id") or "")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.quantize(Decimal("0.000001")), "f")
    return rendered.rstrip("0").rstrip(".") or "0"


def _safe_json_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return json.loads(canonical_json(value[:100]))


def _scope_snapshot(scope: BidAssessmentScope) -> dict[str, Any]:
    selected = dict(scope.selected_lot_snapshot_json or {})
    return {
        "scope_id": str(scope.id),
        "lot_id": str(
            scope.source_lot_candidate_id or selected.get("lot_id") or scope.id
        ),
        "lot_code": (
            str(selected["lot_code"])[:100]
            if selected.get("lot_code") is not None
            else None
        ),
        "lot_name": str(selected.get("lot_name") or "未命名标段")[:300],
        "scope_version": int(scope.version),
    }


def _operation_id(scope_id: str) -> str:
    return f"op_{canonical_hash({'scope_id': scope_id, 'type': 'lot-selection'})[:32]}"


def _response_body(
    db: Session,
    *,
    assessment: BidAssessment,
    scope: BidAssessmentScope,
    request_id: str,
) -> dict[str, Any]:
    selected = dict(scope.selected_lot_snapshot_json or {})
    operation_id = str(selected.get("operation_id") or _operation_id(str(scope.id)))
    return {
        "code": 202,
        "message": "标段已选择，研判规划已受理",
        "data": {
            "scope": _scope_snapshot(scope),
            "accepted_operation": {
                "operation_id": operation_id,
                "status": "accepted",
                "status_url": f"/api/v1/bid-assessments/{assessment.id}",
            },
            "run": None,
            "assessment": build_assessment_snapshot(db, assessment),
        },
        "error": None,
        "request_id": request_id,
    }


def _latest_scope(db: Session, assessment_id: str) -> BidAssessmentScope | None:
    return (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == assessment_id)
        .order_by(BidAssessmentScope.version.desc(), BidAssessmentScope.id.desc())
        .with_for_update()
        .first()
    )


def _lock_manifest_parse_heads(db: Session, manifest_id: str) -> None:
    version_ids = [
        str(row[0])
        for row in (
            db.query(BidManifestDocument.document_version_id)
            .filter(BidManifestDocument.manifest_id == manifest_id)
            .order_by(BidManifestDocument.document_version_id.asc())
            .all()
        )
    ]
    if not version_ids:
        return
    (
        db.query(BidDocumentParseHead)
        .filter(BidDocumentParseHead.document_version_id.in_(version_ids))
        .order_by(BidDocumentParseHead.document_version_id.asc())
        .with_for_update()
        .all()
    )


def select_bid_lot(
    db: Session,
    *,
    assessment_id: str,
    manifest_id: str,
    lot_id: str,
    selection_note: str | None,
    expected_assessment_etag: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Bind the first lot Scope and persist the Phase 3 planning trigger."""

    current_time = now or _utc_now()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None or (
        int(assessment.created_by) != int(actor_id) and not actor_is_admin
    ):
        raise BidLotSelectionNotFound()

    current_etag = assessment_etag(
        str(assessment.id),
        int(assessment.row_version),
    )
    if expected_assessment_etag != current_etag:
        raise BidLotSelectionVersionMismatch(
            assessment,
            provided_etag=expected_assessment_etag,
        )

    current_manifest_id = (
        str(assessment.current_manifest_id)
        if assessment.current_manifest_id is not None
        else None
    )
    if current_manifest_id is None or str(manifest_id) != current_manifest_id:
        raise BidLotSelectionManifestMismatch(
            provided_manifest_id=manifest_id,
            current_manifest_id=current_manifest_id,
        )
    manifest = (
        db.query(BidDocumentManifest)
        .filter(
            BidDocumentManifest.id == manifest_id,
            BidDocumentManifest.assessment_id == assessment.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if manifest is None:
        raise BidLotSelectionManifestMismatch(
            provided_manifest_id=manifest_id,
            current_manifest_id=current_manifest_id,
        )

    existing_scope = _latest_scope(db, str(assessment.id))
    if existing_scope is not None:
        selected = dict(existing_scope.selected_lot_snapshot_json or {})
        same_binding = (
            str(existing_scope.source_lot_candidate_id or selected.get("lot_id") or "")
            == str(lot_id)
            and str(selected.get("manifest_id") or "") == str(manifest_id)
        )
        if same_binding:
            body = _response_body(
                db,
                assessment=assessment,
                scope=existing_scope,
                request_id=request_id,
            )
            return IdempotentCommandResult(
                status_code=202,
                body=body,
                resource_type="scope",
                resource_id=str(existing_scope.id),
                response_ref=f"/api/v1/bid-assessments/{assessment.id}",
            )
        raise BidLotScopeAlreadyBound(existing_scope)

    if (
        str(assessment.lifecycle_status) != "active"
        or str(assessment.business_status) != "awaiting_lot_selection"
    ):
        raise BidLotSelectionStateConflict(assessment)

    detection_head = (
        db.query(BidLotDetectionHead)
        .filter(BidLotDetectionHead.manifest_id == manifest.id)
        .with_for_update()
        .one_or_none()
    )
    if detection_head is None:
        raise BidLotCandidatesNotReady(
            status="not_started",
            reason="lot_detection_not_started",
        )

    _lock_manifest_parse_heads(db, str(manifest.id))
    parse_set = build_manifest_parse_set(db, manifest_id=str(manifest.id))
    if parse_set.status != "ready":
        raise BidLotCandidatesNotReady(
            status=str(parse_set.status),
            reason="manifest_parse_set_not_ready",
        )

    detection_run = (
        db.query(BidLotDetectionRun)
        .filter(
            BidLotDetectionRun.id == detection_head.current_run_id,
            BidLotDetectionRun.manifest_id == manifest.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if detection_run is None:
        raise BidLotCandidatesNotReady(
            status="not_started",
            reason="lot_detection_head_invalid",
        )
    if str(detection_run.parse_set_hash) != str(parse_set.parse_set_hash):
        raise BidLotCandidatesNotReady(
            status="stale",
            reason="lot_detection_parse_set_stale",
        )
    if str(detection_run.status) != "succeeded":
        raise BidLotCandidatesNotReady(
            status=str(detection_run.status),
            reason="lot_detection_not_succeeded",
        )

    candidate = (
        db.query(BidLotCandidate)
        .filter(
            BidLotCandidate.id == lot_id,
            BidLotCandidate.manifest_id == manifest.id,
            BidLotCandidate.detection_run_id == detection_run.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if candidate is None:
        raise BidLotNotInManifest(lot_id=lot_id, manifest_id=manifest_id)
    evidence_links = (
        db.query(BidLotCandidateEvidence)
        .filter(
            BidLotCandidateEvidence.lot_candidate_id == candidate.id,
            BidLotCandidateEvidence.manifest_id == manifest.id,
        )
        .order_by(
            BidLotCandidateEvidence.display_order.asc(),
            BidLotCandidateEvidence.evidence_id.asc(),
        )
        .with_for_update()
        .all()
    )
    if not evidence_links:
        raise BidLotNotInManifest(lot_id=lot_id, manifest_id=manifest_id)

    scope_id = f"scope_{uuid.uuid4().hex[:30]}"
    operation_id = _operation_id(scope_id)
    normalized_note = selection_note.strip() if selection_note else None
    selected_lot_snapshot = {
        "schema_version": LOT_SCOPE_SCHEMA_VERSION,
        "assessment_id": str(assessment.id),
        "manifest_id": str(manifest.id),
        "manifest_version": int(manifest.version),
        "manifest_hash": str(manifest.manifest_hash),
        "detection_run_id": str(detection_run.id),
        "parse_set_hash": str(detection_run.parse_set_hash),
        "lot_id": str(candidate.id),
        "candidate_hash": str(candidate.candidate_hash),
        "lot_code": str(candidate.lot_code) if candidate.lot_code else None,
        "lot_name": str(candidate.lot_name),
        "scope_summary": (
            str(candidate.scope_summary) if candidate.scope_summary else None
        ),
        "normalized_lot_key": str(candidate.normalized_lot_key),
        "source_status": str(candidate.source_status),
        "confidence_level": str(candidate.confidence_level),
        "confidence_score": _decimal_string(candidate.confidence),
        "warnings": _safe_json_list(candidate.warnings_json),
        "evidence_ids": [str(row.evidence_id) for row in evidence_links],
        "selection_note": normalized_note,
        "selected_by": int(actor_id),
        "operation_id": operation_id,
    }
    scope_version = int(
        db.query(func.max(BidAssessmentScope.version))
        .filter(BidAssessmentScope.assessment_id == assessment.id)
        .scalar()
        or 0
    ) + 1
    scope = BidAssessmentScope(
        id=scope_id,
        assessment_id=str(assessment.id),
        version=scope_version,
        scope_type="lot",
        source_lot_candidate_id=str(candidate.id),
        selected_lot_snapshot_json=selected_lot_snapshot,
        scope_hash=canonical_hash(selected_lot_snapshot),
        created_by=int(actor_id),
    )
    db.add(scope)
    previous_status = str(assessment.business_status)
    assessment.business_status = "preliminary_analyzing"
    assessment.updated_by = int(actor_id)
    assessment.row_version = int(assessment.row_version) + 1
    db.flush()
    db.refresh(scope)

    response_body = _response_body(
        db,
        assessment=assessment,
        scope=scope,
        request_id=request_id,
    )
    lot_event = append_outbox_event(
        db,
        event_type="bid.lot.selected.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="scope",
        aggregate_id=str(scope.id),
        aggregate_version=int(scope.version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        payload_schema="bid.lot.selected.v1.payload",
        payload={
            "scope_id": str(scope.id),
            "lot_id": str(candidate.id),
            "manifest_id": str(manifest.id),
            "detection_run_id": str(detection_run.id),
            "from": previous_status,
            "to": str(assessment.business_status),
            "resource_version": int(assessment.row_version),
        },
        dedupe_key=f"lot-selected:{scope.id}",
        occurred_at=current_time,
    )
    plan_event = append_outbox_event(
        db,
        event_type="bid.plan.requested.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="scope",
        aggregate_id=str(scope.id),
        aggregate_version=int(scope.version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        causation_event_id=str(lot_event.event_id),
        payload_schema="bid.plan.requested.v1.payload",
        payload={
            "operation_id": operation_id,
            "assessment_id": str(assessment.id),
            "scope_id": str(scope.id),
            "manifest_id": str(manifest.id),
            "lot_id": str(candidate.id),
            "requested_run_kind": "preliminary",
            "resource_version": int(assessment.row_version),
        },
        dedupe_key=f"plan-requested:{scope.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="lot.select",
        entity_type="scope",
        entity_id=str(scope.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before={
            "assessment_status": previous_status,
            "assessment_row_version": int(assessment.row_version) - 1,
            "scope_id": None,
        },
        after={
            "assessment_status": str(assessment.business_status),
            "assessment_row_version": int(assessment.row_version),
            "scope_id": str(scope.id),
            "scope_hash": str(scope.scope_hash),
            "lot_id": str(candidate.id),
        },
        metadata={
            "http_method": "POST",
            "route_template": LOT_SELECTION_ROUTE_TEMPLATE,
            "manifest_id": str(manifest.id),
            "detection_run_id": str(detection_run.id),
            "evidence_count": len(evidence_links),
            "selection_note_present": normalized_note is not None,
            "lot_selected_event_id": str(lot_event.event_id),
            "plan_requested_event_id": str(plan_event.event_id),
        },
        correlation_id=str(plan_event.event_id),
        occurred_at=current_time,
    )
    db.flush()
    return IdempotentCommandResult(
        status_code=202,
        body=response_body,
        resource_type="scope",
        resource_id=str(scope.id),
        response_ref=f"/api/v1/bid-assessments/{assessment.id}",
    )
