"""API-32: create an independent Assessment for another authoritative lot."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidDocumentVersion,
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


CLONE_FOR_LOT_ROUTE_TEMPLATE = (
    "/api/v1/bid-assessments/{assessment_id}/clone-for-lot"
)
CLONED_LOT_SCOPE_SCHEMA_VERSION = "bid-assessment-cloned-lot-scope-v1"


class BidAssessmentCloneError(RuntimeError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"


class BidAssessmentCloneNotFound(BidAssessmentCloneError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidAssessmentCloneVersionMismatch(BidAssessmentCloneError):
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


class BidAssessmentCloneManifestMismatch(BidAssessmentCloneError):
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


class BidAssessmentCloneCandidatesNotReady(BidAssessmentCloneError):
    code = "BID_LOT_CANDIDATES_NOT_READY"

    def __init__(self, *, status: str, reason: str):
        super().__init__(self.code)
        self.status = str(status)
        self.reason = str(reason)


class BidAssessmentCloneLotNotInManifest(BidAssessmentCloneError):
    code = "BID_LOT_NOT_IN_MANIFEST"

    def __init__(self, *, lot_id: str, manifest_id: str):
        super().__init__(self.code)
        self.lot_id = str(lot_id)
        self.manifest_id = str(manifest_id)


class BidAssessmentCloneStateConflict(BidAssessmentCloneError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"

    def __init__(self, assessment: BidAssessment, *, reason: str):
        super().__init__(self.code)
        self.lifecycle_status = str(assessment.lifecycle_status)
        self.business_status = str(assessment.business_status)
        self.reason = str(reason)


class BidAssessmentCloneSameLot(BidAssessmentCloneError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"

    def __init__(self, *, source_lot_id: str, requested_lot_id: str):
        super().__init__(self.code)
        self.source_lot_id = str(source_lot_id)
        self.requested_lot_id = str(requested_lot_id)


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


def _operation_id(scope_id: str) -> str:
    return f"op_{canonical_hash({'scope_id': scope_id, 'type': 'clone-for-lot'})[:32]}"


def _latest_scope(
    db: Session,
    assessment_id: str,
) -> BidAssessmentScope | None:
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


def clone_bid_assessment_for_lot(
    db: Session,
    *,
    assessment_id: str,
    source_manifest_id: str,
    lot_id: str,
    title: str,
    expected_assessment_etag: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Create API-32 state without copying object-storage bytes or source ACLs."""

    current_time = now or _utc_now()
    source = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if source is None or (
        int(source.created_by) != int(actor_id) and not actor_is_admin
    ):
        raise BidAssessmentCloneNotFound()

    current_etag = assessment_etag(str(source.id), int(source.row_version))
    if expected_assessment_etag != current_etag:
        raise BidAssessmentCloneVersionMismatch(
            source,
            provided_etag=expected_assessment_etag,
        )
    if str(source.lifecycle_status) != "active":
        raise BidAssessmentCloneStateConflict(
            source,
            reason="source_assessment_not_active",
        )

    current_manifest_id = (
        str(source.current_manifest_id)
        if source.current_manifest_id is not None
        else None
    )
    if current_manifest_id is None or str(source_manifest_id) != current_manifest_id:
        raise BidAssessmentCloneManifestMismatch(
            provided_manifest_id=source_manifest_id,
            current_manifest_id=current_manifest_id,
        )
    source_manifest = (
        db.query(BidDocumentManifest)
        .filter(
            BidDocumentManifest.id == source_manifest_id,
            BidDocumentManifest.assessment_id == source.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if source_manifest is None:
        raise BidAssessmentCloneManifestMismatch(
            provided_manifest_id=source_manifest_id,
            current_manifest_id=current_manifest_id,
        )

    source_scope = _latest_scope(db, str(source.id))
    if source_scope is None:
        raise BidAssessmentCloneStateConflict(
            source,
            reason="source_scope_required",
        )
    source_scope_snapshot = dict(source_scope.selected_lot_snapshot_json or {})

    detection_head = (
        db.query(BidLotDetectionHead)
        .filter(BidLotDetectionHead.manifest_id == source_manifest.id)
        .with_for_update()
        .one_or_none()
    )
    if detection_head is None:
        raise BidAssessmentCloneCandidatesNotReady(
            status="not_started",
            reason="lot_detection_not_started",
        )

    _lock_manifest_parse_heads(db, str(source_manifest.id))
    parse_set = build_manifest_parse_set(
        db,
        manifest_id=str(source_manifest.id),
    )
    if parse_set.status != "ready":
        raise BidAssessmentCloneCandidatesNotReady(
            status=str(parse_set.status),
            reason="manifest_parse_set_not_ready",
        )
    detection_run = (
        db.query(BidLotDetectionRun)
        .filter(
            BidLotDetectionRun.id == detection_head.current_run_id,
            BidLotDetectionRun.manifest_id == source_manifest.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if detection_run is None:
        raise BidAssessmentCloneCandidatesNotReady(
            status="not_started",
            reason="lot_detection_head_invalid",
        )
    if str(detection_run.parse_set_hash) != str(parse_set.parse_set_hash):
        raise BidAssessmentCloneCandidatesNotReady(
            status="stale",
            reason="lot_detection_parse_set_stale",
        )
    if str(detection_run.status) != "succeeded":
        raise BidAssessmentCloneCandidatesNotReady(
            status=str(detection_run.status),
            reason="lot_detection_not_succeeded",
        )

    candidate = (
        db.query(BidLotCandidate)
        .filter(
            BidLotCandidate.id == lot_id,
            BidLotCandidate.manifest_id == source_manifest.id,
            BidLotCandidate.detection_run_id == detection_run.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if candidate is None:
        raise BidAssessmentCloneLotNotInManifest(
            lot_id=lot_id,
            manifest_id=source_manifest_id,
        )
    evidence_links = (
        db.query(BidLotCandidateEvidence)
        .filter(
            BidLotCandidateEvidence.lot_candidate_id == candidate.id,
            BidLotCandidateEvidence.manifest_id == source_manifest.id,
        )
        .order_by(
            BidLotCandidateEvidence.display_order.asc(),
            BidLotCandidateEvidence.evidence_id.asc(),
        )
        .with_for_update()
        .all()
    )
    if not evidence_links:
        raise BidAssessmentCloneLotNotInManifest(
            lot_id=lot_id,
            manifest_id=source_manifest_id,
        )

    source_lot_id = str(
        source_scope.source_lot_candidate_id
        or source_scope_snapshot.get("lot_id")
        or source_scope.id
    )
    source_normalized_key = str(
        source_scope_snapshot.get("normalized_lot_key") or ""
    )
    if (
        str(candidate.id) == source_lot_id
        or (
            source_normalized_key
            and str(candidate.normalized_lot_key) == source_normalized_key
        )
    ):
        raise BidAssessmentCloneSameLot(
            source_lot_id=source_lot_id,
            requested_lot_id=str(candidate.id),
        )

    source_members = (
        db.query(BidManifestDocument, BidDocumentVersion)
        .join(
            BidDocumentVersion,
            BidDocumentVersion.id == BidManifestDocument.document_version_id,
        )
        .filter(BidManifestDocument.manifest_id == source_manifest.id)
        .order_by(
            BidManifestDocument.order_no.asc(),
            BidManifestDocument.document_version_id.asc(),
        )
        .with_for_update()
        .all()
    )
    if not source_members:
        raise BidAssessmentCloneStateConflict(
            source,
            reason="source_manifest_empty",
        )

    cloned = BidAssessment(
        id=str(uuid.uuid4()),
        title=title,
        client_name=str(source.client_name),
        internal_note=source.internal_note,
        external_ref=None,
        lifecycle_status="active",
        business_status="preliminary_analyzing",
        current_manifest_id=None,
        active_run_id=None,
        created_by=int(actor_id),
        updated_by=int(actor_id),
        row_version=1,
    )
    db.add(cloned)
    db.flush()

    manifest_members = [
        {
            "document_id": str(version.document_id),
            "document_version_id": str(member.document_version_id),
            "file_object_id": str(version.file_object_id),
            "role": str(member.role),
            "order_no": int(member.order_no),
        }
        for member, version in source_members
    ]
    cloned_manifest = BidDocumentManifest(
        id=str(uuid.uuid4()),
        assessment_id=str(cloned.id),
        version=1,
        manifest_hash=canonical_hash(
            {
                "assessment_id": str(cloned.id),
                "members": manifest_members,
            }
        ),
        change_note="API-32 为其他标段建立独立资料清单",
        committed_by=int(actor_id),
    )
    db.add(cloned_manifest)
    db.flush()
    for member, _version in source_members:
        db.add(
            BidManifestDocument(
                manifest_id=str(cloned_manifest.id),
                document_version_id=str(member.document_version_id),
                role=str(member.role),
                order_no=int(member.order_no),
            )
        )
    db.flush()

    scope_id = f"scope_{uuid.uuid4().hex[:30]}"
    operation_id = _operation_id(scope_id)
    selected_lot_snapshot = {
        "schema_version": CLONED_LOT_SCOPE_SCHEMA_VERSION,
        "assessment_id": str(cloned.id),
        "manifest_id": str(cloned_manifest.id),
        "manifest_version": int(cloned_manifest.version),
        "manifest_hash": str(cloned_manifest.manifest_hash),
        "source_assessment_id": str(source.id),
        "source_manifest_id": str(source_manifest.id),
        "source_manifest_version": int(source_manifest.version),
        "source_detection_run_id": str(detection_run.id),
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
        "cloned_by": int(actor_id),
        "operation_id": operation_id,
    }
    scope = BidAssessmentScope(
        id=scope_id,
        assessment_id=str(cloned.id),
        version=1,
        scope_type="lot",
        # The immutable snapshot is authoritative. Keeping this nullable avoids
        # making the cloned aggregate's lifetime depend on the source candidate.
        source_lot_candidate_id=None,
        selected_lot_snapshot_json=selected_lot_snapshot,
        scope_hash=canonical_hash(selected_lot_snapshot),
        created_by=int(actor_id),
    )
    db.add(scope)
    cloned.current_manifest_id = str(cloned_manifest.id)
    db.flush()
    db.refresh(cloned)

    snapshot = build_assessment_snapshot(db, cloned)
    created_event = append_outbox_event(
        db,
        event_type="bid.assessment.created.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="assessment",
        aggregate_id=str(cloned.id),
        aggregate_version=int(cloned.row_version),
        assessment_id=str(cloned.id),
        request_id=request_id,
        payload_schema="bid.assessment.created.v1.payload",
        payload={"snapshot": snapshot},
        dedupe_key=f"assessment-created:{cloned.id}",
        occurred_at=current_time,
    )
    plan_event = append_outbox_event(
        db,
        event_type="bid.plan.requested.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="scope",
        aggregate_id=str(scope.id),
        aggregate_version=int(scope.version),
        assessment_id=str(cloned.id),
        request_id=request_id,
        causation_event_id=str(created_event.event_id),
        payload_schema="bid.plan.requested.v1.payload",
        payload={
            "operation_id": operation_id,
            "assessment_id": str(cloned.id),
            "scope_id": str(scope.id),
            "manifest_id": str(cloned_manifest.id),
            "lot_id": str(candidate.id),
            "requested_run_kind": "preliminary",
            "resource_version": int(cloned.row_version),
            "source_assessment_id": str(source.id),
            "source_manifest_id": str(source_manifest.id),
        },
        dedupe_key=f"plan-requested:{scope.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="assessment.clone_for_lot",
        entity_type="assessment",
        entity_id=str(cloned.id),
        assessment_id=str(cloned.id),
        outcome="succeeded",
        request_id=request_id,
        before={
            "source_assessment_id": str(source.id),
            "source_manifest_id": str(source_manifest.id),
            "source_scope_id": str(source_scope.id),
        },
        after=snapshot,
        metadata={
            "http_method": "POST",
            "route_template": CLONE_FOR_LOT_ROUTE_TEMPLATE,
            "document_reuse": "document_version_reference_only",
            "source_assessment_mutated": False,
            "outbox_event_types": [
                created_event.event_type,
                plan_event.event_type,
            ],
        },
        correlation_id=str(plan_event.event_id),
        occurred_at=current_time,
    )

    return IdempotentCommandResult(
        status_code=201,
        body={
            "code": 201,
            "message": "已为其他标段创建独立研判",
            "data": snapshot,
            "error": None,
            "request_id": request_id,
        },
        resource_type="assessment",
        resource_id=str(cloned.id),
        response_ref=f"/api/v1/bid-assessments/{cloned.id}",
    )
