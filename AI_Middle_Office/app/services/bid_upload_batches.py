"""Upload-batch commands for the isolated bid-assessment v1 runtime."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import BidAssessment, BidUploadBatch
from app.services.bid_assessment_eventing import append_audit_log, append_outbox_event
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_assessment_snapshots import assessment_etag
from app.services.bid_upload_batch_snapshots import build_upload_batch_snapshot


OPEN_BATCH_STATUSES = ("draft", "uploading", "ready", "committing")


class BidUploadBatchCommandError(RuntimeError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"


class BidUploadBatchAssessmentNotFound(BidUploadBatchCommandError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidUploadBatchVersionMismatch(BidUploadBatchCommandError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(
        self,
        *,
        provided_etag: str,
        current_etag: str,
        assessment_id: str,
        current_row_version: int,
    ):
        super().__init__(self.code)
        self.provided_etag = provided_etag
        self.current_etag = current_etag
        self.assessment_id = assessment_id
        self.current_row_version = int(current_row_version)


class BidUploadBatchAlreadyOpen(BidUploadBatchCommandError):
    code = "BID_UPLOAD_BATCH_ALREADY_OPEN"

    def __init__(self, batch: BidUploadBatch):
        super().__init__(self.code)
        self.batch_id = str(batch.id)
        self.status = str(batch.status)


class BidUploadBatchBaselineStale(BidUploadBatchCommandError):
    code = "BID_BASE_MANIFEST_STALE"

    def __init__(self, *, provided_manifest_id: str | None, current_manifest_id: str | None):
        super().__init__(self.code)
        self.provided_manifest_id = provided_manifest_id
        self.current_manifest_id = current_manifest_id


class BidUploadBatchStateConflict(BidUploadBatchCommandError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"

    def __init__(self, *, lifecycle_status: str, business_status: str):
        super().__init__(self.code)
        self.lifecycle_status = lifecycle_status
        self.business_status = business_status


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _verify_assessment_writable(assessment: BidAssessment) -> None:
    lifecycle_status = str(assessment.lifecycle_status)
    business_status = str(assessment.business_status)
    if lifecycle_status != "active" or business_status in {"draft", "superseded"}:
        raise BidUploadBatchStateConflict(
            lifecycle_status=lifecycle_status,
            business_status=business_status,
        )


def _verify_batch_creation_state(assessment: BidAssessment, *, purpose: str) -> None:
    lifecycle_status = str(assessment.lifecycle_status)
    business_status = str(assessment.business_status)
    if purpose == "initial" and business_status not in {"awaiting_files", "cancelled"}:
        raise BidUploadBatchStateConflict(
            lifecycle_status=lifecycle_status,
            business_status=business_status,
        )


def _verify_manifest_baseline(
    assessment: BidAssessment,
    *,
    purpose: str,
    base_manifest_id: str | None,
) -> None:
    current_manifest_id = (
        str(assessment.current_manifest_id) if assessment.current_manifest_id else None
    )
    if purpose == "initial":
        if current_manifest_id is not None:
            raise BidUploadBatchBaselineStale(
                provided_manifest_id=base_manifest_id,
                current_manifest_id=current_manifest_id,
            )
        return
    if current_manifest_id is None or str(base_manifest_id) != current_manifest_id:
        raise BidUploadBatchBaselineStale(
            provided_manifest_id=base_manifest_id,
            current_manifest_id=current_manifest_id,
        )


def create_bid_upload_batch(
    db: Session,
    *,
    assessment_id: str,
    expected_assessment_etag: str,
    actor_id: int,
    actor_ref: str,
    request_id: str,
    purpose: str,
    base_manifest_id: str | None,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Create API-10 state, Outbox, audit, and response in one caller transaction."""

    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise BidUploadBatchAssessmentNotFound("BID_RESOURCE_NOT_FOUND")

    current_etag = assessment_etag(str(assessment.id), int(assessment.row_version))
    if expected_assessment_etag != current_etag:
        raise BidUploadBatchVersionMismatch(
            provided_etag=expected_assessment_etag,
            current_etag=current_etag,
            assessment_id=str(assessment.id),
            current_row_version=int(assessment.row_version),
        )

    _verify_assessment_writable(assessment)
    _verify_manifest_baseline(
        assessment,
        purpose=purpose,
        base_manifest_id=base_manifest_id,
    )
    _verify_batch_creation_state(assessment, purpose=purpose)

    existing = (
        db.query(BidUploadBatch)
        .filter(
            BidUploadBatch.assessment_id == assessment.id,
            BidUploadBatch.status.in_(OPEN_BATCH_STATUSES),
            BidUploadBatch.open_slot_key.is_not(None),
        )
        .order_by(BidUploadBatch.created_at.desc(), BidUploadBatch.id.desc())
        .with_for_update()
        .first()
    )
    if existing is not None:
        raise BidUploadBatchAlreadyOpen(existing)

    current_time = now or _utc_now()
    batch = BidUploadBatch(
        id=str(uuid.uuid4()),
        assessment_id=str(assessment.id),
        base_manifest_id=base_manifest_id,
        purpose=purpose,
        status="draft",
        open_slot_key=purpose,
        expires_at=current_time
        + timedelta(days=max(1, int(settings.bid_upload_batch_ttl_days))),
        created_by=int(actor_id),
        updated_by=int(actor_id),
        row_version=1,
    )
    db.add(batch)
    db.flush()
    db.refresh(batch)

    snapshot = build_upload_batch_snapshot(db, batch)
    event_payload = {
        "batch_id": str(batch.id),
        "status": str(batch.status),
        "ready_count": 0,
        "failed_count": 0,
        "resource_version": int(batch.row_version),
    }
    event = append_outbox_event(
        db,
        event_type="bid.upload_batch.created.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="upload_batch",
        aggregate_id=str(batch.id),
        aggregate_version=int(batch.row_version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        payload_schema="bid.upload_batch.created.v1.payload",
        payload=event_payload,
        dedupe_key=f"upload-batch-created:{batch.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="upload_batch.create",
        entity_type="upload_batch",
        entity_id=str(batch.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before=None,
        after=snapshot,
        metadata={
            "http_method": "POST",
            "route_template": (
                "/api/v1/bid-assessments/{assessment_id}/upload-batches"
            ),
            "assessment_etag": current_etag,
            "purpose": purpose,
            "base_manifest_id": base_manifest_id,
            "outbox_event_type": event.event_type,
        },
        correlation_id=event.event_id,
        occurred_at=current_time,
    )

    response_body: dict[str, Any] = {
        "code": 200,
        "message": "ok",
        "data": snapshot,
        "error": None,
        "request_id": request_id,
    }
    return IdempotentCommandResult(
        status_code=201,
        body=response_body,
        resource_type="upload_batch",
        resource_id=str(batch.id),
        response_ref=f"/api/v1/bid-upload-batches/{batch.id}",
    )
