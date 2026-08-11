"""API-16 atomic upload-batch abandonment without inline object deletion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import BidAssessment, BidUploadBatch
from app.services.bid_assessment_eventing import append_audit_log, append_outbox_event
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_upload_batch_snapshots import (
    build_upload_batch_snapshot,
    upload_batch_etag,
)


ABANDONABLE_BATCH_STATUSES = {"draft", "uploading", "ready"}


class BidUploadBatchAbandonmentError(RuntimeError):
    code = "BID_UPLOAD_BATCH_NOT_READY"


class BidUploadBatchAbandonmentNotFound(BidUploadBatchAbandonmentError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidUploadBatchAbandonmentVersionMismatch(BidUploadBatchAbandonmentError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(
        self,
        *,
        batch_id: str,
        provided_etag: str,
        current_etag: str,
        current_row_version: int,
    ) -> None:
        super().__init__(self.code)
        self.batch_id = batch_id
        self.provided_etag = provided_etag
        self.current_etag = current_etag
        self.current_row_version = int(current_row_version)


class BidUploadBatchAbandonmentAlreadyCommitted(BidUploadBatchAbandonmentError):
    code = "BID_UPLOAD_BATCH_ALREADY_COMMITTED"

    def __init__(self, *, status: str, committed_manifest_id: str | None) -> None:
        super().__init__(self.code)
        self.status = status
        self.committed_manifest_id = committed_manifest_id


class BidUploadBatchAlreadyAbandoned(BidUploadBatchAbandonmentError):
    code = "BID_UPLOAD_BATCH_ALREADY_ABANDONED"

    def __init__(self, *, abandoned_at: datetime | None) -> None:
        super().__init__(self.code)
        self.abandoned_at = abandoned_at


class BidUploadBatchAbandonmentStateConflict(BidUploadBatchAbandonmentError):
    code = "BID_UPLOAD_BATCH_NOT_READY"

    def __init__(self, *, status: str, expired: bool) -> None:
        super().__init__(self.code)
        self.status = status
        self.expired = bool(expired)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_rfc3339(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def abandon_bid_upload_batch(
    db: Session,
    *,
    batch_id: str,
    expected_batch_etag: str,
    reason: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Terminalize an open batch; object references remain until cleanup_after."""

    current_time = _as_utc(now or _utc_now())
    batch = (
        db.query(BidUploadBatch)
        .filter(BidUploadBatch.id == batch_id)
        .with_for_update()
        .one_or_none()
    )
    if batch is None:
        raise BidUploadBatchAbandonmentNotFound()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == batch.assessment_id)
        .with_for_update()
        .one()
    )
    if int(assessment.created_by) != int(actor_id) and not actor_is_admin:
        raise BidUploadBatchAbandonmentNotFound()

    current_etag = upload_batch_etag(str(batch.id), int(batch.row_version))
    if expected_batch_etag != current_etag:
        raise BidUploadBatchAbandonmentVersionMismatch(
            batch_id=str(batch.id),
            provided_etag=expected_batch_etag,
            current_etag=current_etag,
            current_row_version=int(batch.row_version),
        )

    status = str(batch.status)
    if status in {"committing", "committed"}:
        raise BidUploadBatchAbandonmentAlreadyCommitted(
            status=status,
            committed_manifest_id=(
                str(batch.committed_manifest_id)
                if batch.committed_manifest_id
                else None
            ),
        )
    if status == "abandoned":
        raise BidUploadBatchAlreadyAbandoned(abandoned_at=batch.abandoned_at)
    expired = _as_utc(batch.expires_at) <= current_time
    if status not in ABANDONABLE_BATCH_STATUSES or expired:
        raise BidUploadBatchAbandonmentStateConflict(
            status=status,
            expired=expired,
        )

    before = build_upload_batch_snapshot(db, batch)
    grace_seconds = max(3600, int(settings.bid_upload_orphan_grace_seconds))
    cleanup_after = current_time + timedelta(seconds=grace_seconds)
    batch.status = "abandoned"
    batch.open_slot_key = None
    batch.abandon_reason = reason
    batch.abandoned_at = current_time
    batch.cleanup_after = cleanup_after
    batch.cleanup_completed_at = None
    batch.updated_by = int(actor_id)
    batch.row_version = int(batch.row_version) + 1
    db.flush()

    snapshot = build_upload_batch_snapshot(db, batch)
    ready_count = sum(1 for row in snapshot["files"] if row["status"] == "ready")
    failed_count = sum(
        1
        for row in snapshot["files"]
        if row["status"] in {"rejected", "failed"}
    )
    event = append_outbox_event(
        db,
        event_type="bid.upload_batch.abandoned.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="upload_batch",
        aggregate_id=str(batch.id),
        aggregate_version=int(batch.row_version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        payload_schema="bid.upload_batch.abandoned.v1.payload",
        payload={
            "batch_id": str(batch.id),
            "status": "abandoned",
            "ready_count": ready_count,
            "failed_count": failed_count,
            "resource_version": int(batch.row_version),
            "cleanup_after": _utc_rfc3339(cleanup_after),
        },
        dedupe_key=f"upload-batch-abandoned:{batch.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="upload_batch.abandon",
        entity_type="upload_batch",
        entity_id=str(batch.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before=before,
        after=snapshot,
        metadata={
            "http_method": "POST",
            "route_template": "/api/v1/bid-upload-batches/{batch_id}/abandon",
            "batch_etag": current_etag,
            "reason": reason,
            "cleanup_policy": "deferred_reference_aware",
            "cleanup_grace_seconds": grace_seconds,
            "file_count": len(snapshot["files"]),
            "deactivation_count": len(snapshot["deactivations"]),
            "outbox_event_type": event.event_type,
        },
        correlation_id=event.event_id,
        occurred_at=current_time,
    )
    body = {
        "code": 200,
        "message": "上传批次已放弃",
        "data": snapshot,
        "error": None,
        "request_id": request_id,
    }
    db.flush()
    return IdempotentCommandResult(
        status_code=200,
        body=body,
        resource_type="upload_batch",
        resource_id=str(batch.id),
        response_ref=f"/api/v1/bid-upload-batches/{batch.id}",
    )
