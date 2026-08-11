"""API-14 baseline-document deactivation commands."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidDocumentVersion,
    BidManifestDocument,
    BidUploadBatch,
    BidUploadBatchDeactivation,
    BidUploadBatchFile,
)
from app.services.bid_assessment_eventing import append_audit_log, append_outbox_event
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_upload_batch_snapshots import (
    build_upload_batch_snapshot,
    derive_upload_batch_status,
    upload_batch_etag,
)


WRITABLE_BATCH_STATUSES = ("draft", "uploading", "ready")


class BidUploadBatchDeactivationError(RuntimeError):
    code = "BID_UPLOAD_DEACTIVATION_NOT_ALLOWED"


class BidUploadBatchDeactivationNotFound(BidUploadBatchDeactivationError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidUploadBatchDeactivationVersionMismatch(BidUploadBatchDeactivationError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(
        self,
        *,
        batch_id: str,
        provided_etag: str,
        current_etag: str,
        current_row_version: int,
    ):
        super().__init__(self.code)
        self.batch_id = batch_id
        self.provided_etag = provided_etag
        self.current_etag = current_etag
        self.current_row_version = int(current_row_version)


class BidUploadBatchDeactivationBatchCommitted(BidUploadBatchDeactivationError):
    code = "BID_UPLOAD_BATCH_ALREADY_COMMITTED"

    def __init__(self, *, status: str):
        super().__init__(self.code)
        self.status = status


class BidUploadBatchDeactivationStateConflict(BidUploadBatchDeactivationError):
    code = "BID_UPLOAD_BATCH_NOT_READY"

    def __init__(self, *, status: str):
        super().__init__(self.code)
        self.status = status


class BidUploadBatchDeactivationNotAllowed(BidUploadBatchDeactivationError):
    code = "BID_UPLOAD_DEACTIVATION_NOT_ALLOWED"

    def __init__(self, *, purpose: str):
        super().__init__(self.code)
        self.purpose = purpose


class BidUploadBatchDeactivationBaselineStale(BidUploadBatchDeactivationError):
    code = "BID_BASE_MANIFEST_STALE"

    def __init__(
        self,
        *,
        base_manifest_id: str | None,
        current_manifest_id: str | None,
    ):
        super().__init__(self.code)
        self.base_manifest_id = base_manifest_id
        self.current_manifest_id = current_manifest_id


class BidUploadBatchDeactivationTargetInvalid(BidUploadBatchDeactivationError):
    code = "BID_UPLOAD_DEACTIVATION_TARGET_INVALID"

    def __init__(self, *, invalid_document_ids: list[str]):
        super().__init__(self.code)
        self.invalid_document_ids = list(invalid_document_ids)


class BidUploadBatchDeactivationConflict(BidUploadBatchDeactivationError):
    code = "BID_UPLOAD_DEACTIVATION_CONFLICT"

    def __init__(
        self,
        *,
        document_id: str,
        existing_reason: str,
    ):
        super().__init__(self.code)
        self.document_id = document_id
        self.existing_reason = existing_reason


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_bid_upload_batch_deactivations(
    db: Session,
    *,
    batch_id: str,
    expected_batch_etag: str,
    document_ids: list[str],
    reason: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Register baseline-document removals without mutating historical data."""

    current_time = now or _utc_now()
    normalized_document_ids = sorted(str(value) for value in document_ids)
    batch = (
        db.query(BidUploadBatch)
        .filter(BidUploadBatch.id == batch_id)
        .with_for_update()
        .one_or_none()
    )
    if batch is None:
        raise BidUploadBatchDeactivationNotFound()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == batch.assessment_id)
        .with_for_update()
        .one()
    )
    if int(assessment.created_by) != int(actor_id) and not actor_is_admin:
        raise BidUploadBatchDeactivationNotFound()

    current_etag = upload_batch_etag(str(batch.id), int(batch.row_version))
    if expected_batch_etag != current_etag:
        raise BidUploadBatchDeactivationVersionMismatch(
            batch_id=str(batch.id),
            provided_etag=expected_batch_etag,
            current_etag=current_etag,
            current_row_version=int(batch.row_version),
        )

    batch_status = str(batch.status)
    if batch_status in {"committing", "committed"}:
        raise BidUploadBatchDeactivationBatchCommitted(status=batch_status)
    if (
        batch_status not in WRITABLE_BATCH_STATUSES
        or _as_utc(batch.expires_at) <= _as_utc(current_time)
    ):
        raise BidUploadBatchDeactivationStateConflict(status=batch_status)
    if str(batch.purpose) != "change":
        raise BidUploadBatchDeactivationNotAllowed(purpose=str(batch.purpose))

    base_manifest_id = str(batch.base_manifest_id) if batch.base_manifest_id else None
    current_manifest_id = (
        str(assessment.current_manifest_id)
        if assessment.current_manifest_id
        else None
    )
    if base_manifest_id is None or base_manifest_id != current_manifest_id:
        raise BidUploadBatchDeactivationBaselineStale(
            base_manifest_id=base_manifest_id,
            current_manifest_id=current_manifest_id,
        )

    baseline_document_ids = {
        str(value[0])
        for value in db.query(BidDocumentVersion.document_id)
        .join(
            BidManifestDocument,
            BidManifestDocument.document_version_id == BidDocumentVersion.id,
        )
        .filter(
            BidManifestDocument.manifest_id == base_manifest_id,
            BidDocumentVersion.document_id.in_(normalized_document_ids),
        )
        .distinct()
        .all()
    }
    invalid_document_ids = sorted(
        set(normalized_document_ids) - baseline_document_ids
    )
    if invalid_document_ids:
        raise BidUploadBatchDeactivationTargetInvalid(
            invalid_document_ids=invalid_document_ids,
        )

    existing_rows = (
        db.query(BidUploadBatchDeactivation)
        .filter(
            BidUploadBatchDeactivation.batch_id == batch.id,
            BidUploadBatchDeactivation.document_id.in_(normalized_document_ids),
        )
        .with_for_update()
        .all()
    )
    existing_by_document = {str(row.document_id): row for row in existing_rows}
    for document_id, existing in existing_by_document.items():
        if str(existing.reason) != reason:
            raise BidUploadBatchDeactivationConflict(
                document_id=document_id,
                existing_reason=str(existing.reason),
            )

    before = build_upload_batch_snapshot(db, batch)
    added_document_ids = [
        document_id
        for document_id in normalized_document_ids
        if document_id not in existing_by_document
    ]
    for document_id in added_document_ids:
        db.add(
            BidUploadBatchDeactivation(
                id=str(uuid.uuid4()),
                batch_id=str(batch.id),
                document_id=document_id,
                reason=reason,
            )
        )
    if added_document_ids:
        db.flush()

    event = None
    if added_document_ids:
        file_statuses = [
            str(value[0])
            for value in db.query(BidUploadBatchFile.status)
            .filter(BidUploadBatchFile.batch_id == batch.id)
            .all()
        ]
        deactivation_count = int(
            db.query(func.count(BidUploadBatchDeactivation.id))
            .filter(BidUploadBatchDeactivation.batch_id == batch.id)
            .scalar()
            or 0
        )
        batch.status = derive_upload_batch_status(
            file_statuses,
            deactivation_count=deactivation_count,
        )
        batch.row_version = int(batch.row_version) + 1
        batch.updated_by = int(actor_id)
        db.flush()

        ready_count = sum(1 for status in file_statuses if status == "ready")
        failed_count = sum(
            1 for status in file_statuses if status in {"rejected", "failed"}
        )
        event = append_outbox_event(
            db,
            event_type="bid.upload_batch.deactivation_added.v1",
            producer="bid-assessment-api-v1",
            aggregate_type="upload_batch",
            aggregate_id=str(batch.id),
            aggregate_version=int(batch.row_version),
            assessment_id=str(assessment.id),
            request_id=request_id,
            payload_schema="bid.upload_batch.deactivation_added.v1.payload",
            payload={
                "batch_id": str(batch.id),
                "document_ids": added_document_ids,
                "status": str(batch.status),
                "ready_count": ready_count,
                "failed_count": failed_count,
                "deactivation_count": deactivation_count,
                "resource_version": int(batch.row_version),
            },
            dedupe_key=(
                f"upload-batch-deactivation:{batch.id}:{batch.row_version}"
            ),
            occurred_at=current_time,
        )

    snapshot = build_upload_batch_snapshot(db, batch)
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="upload_batch.add_deactivations",
        entity_type="upload_batch",
        entity_id=str(batch.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before=before,
        after=snapshot,
        metadata={
            "http_method": "POST",
            "route_template": (
                "/api/v1/bid-upload-batches/{batch_id}/deactivations"
            ),
            "batch_etag": current_etag,
            "base_manifest_id": base_manifest_id,
            "requested_document_ids": normalized_document_ids,
            "added_document_ids": added_document_ids,
            "duplicate_document_ids": sorted(existing_by_document),
            "reason": reason,
            "operation_noop": not added_document_ids,
            "outbox_event_type": event.event_type if event is not None else None,
        },
        correlation_id=event.event_id if event is not None else None,
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
