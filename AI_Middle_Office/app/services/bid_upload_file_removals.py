"""API-13 draft-file removal with shared-object reference protection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidDocumentVersion,
    BidFileObject,
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
    upload_batch_file_etag,
)
from app.services.bid_upload_file_storage import normalized_upload_object_prefix


REMOVABLE_BATCH_STATUSES = ("draft", "uploading", "ready")


class BidUploadFileRemovalError(RuntimeError):
    code = "BID_UPLOAD_BATCH_NOT_READY"


class BidUploadFileRemovalNotFound(BidUploadFileRemovalError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidUploadFileRemovalVersionMismatch(BidUploadFileRemovalError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(
        self,
        *,
        batch_id: str,
        file_id: str,
        provided_etag: str,
        current_etag: str,
        current_row_version: int,
    ):
        super().__init__(self.code)
        self.batch_id = batch_id
        self.file_id = file_id
        self.provided_etag = provided_etag
        self.current_etag = current_etag
        self.current_row_version = int(current_row_version)


class BidUploadFileRemovalBatchCommitted(BidUploadFileRemovalError):
    code = "BID_UPLOAD_BATCH_ALREADY_COMMITTED"

    def __init__(self, *, status: str):
        super().__init__(self.code)
        self.status = status


class BidUploadFileRemovalBatchStateConflict(BidUploadFileRemovalError):
    code = "BID_UPLOAD_BATCH_NOT_READY"

    def __init__(self, *, status: str):
        super().__init__(self.code)
        self.status = status


@dataclass(frozen=True)
class BidUploadFileRemoval:
    command: IdempotentCommandResult
    cleanup_object_key: str | None
    preserved_reference_count: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_managed_upload_key(object_key: str | None) -> bool:
    if not object_key:
        return False
    prefix = normalized_upload_object_prefix().rstrip("/") + "/"
    return str(object_key).startswith(prefix)


def _file_snapshot(row: BidUploadBatchFile) -> dict[str, Any]:
    return {
        "batch_file_id": str(row.id),
        "batch_id": str(row.batch_id),
        "file_object_id": str(row.file_object_id) if row.file_object_id else None,
        "client_file_id": str(row.client_file_id),
        "filename": str(row.filename),
        "relative_path": row.relative_path,
        "operation": str(row.operation),
        "replace_document_id": (
            str(row.replace_document_id) if row.replace_document_id else None
        ),
        "size_bytes": int(row.size_bytes),
        "sha256": str(row.sha256),
        "mime_type": str(row.mime_type),
        "status": str(row.status),
        "error_code": row.error_code,
        "row_version": int(row.row_version),
        "etag": upload_batch_file_etag(str(row.id), int(row.row_version)),
    }


def _remaining_object_references(
    db: Session,
    *,
    file_object_id: str,
    object_key: str,
) -> tuple[int, int, int]:
    batch_references = int(
        db.query(func.count(BidUploadBatchFile.id))
        .filter(BidUploadBatchFile.file_object_id == file_object_id)
        .scalar()
        or 0
    )
    document_references = int(
        db.query(func.count(BidDocumentVersion.id))
        .filter(BidDocumentVersion.file_object_id == file_object_id)
        .scalar()
        or 0
    )
    temporary_references = int(
        db.query(func.count(BidUploadBatchFile.id))
        .filter(BidUploadBatchFile.temporary_object_ref == object_key)
        .scalar()
        or 0
    )
    return batch_references, document_references, temporary_references


def _unlinked_temporary_key_is_safe(
    db: Session,
    *,
    object_key: str | None,
) -> bool:
    if not _is_managed_upload_key(object_key):
        return False
    file_object_references = int(
        db.query(func.count(BidFileObject.id))
        .filter(BidFileObject.object_key == object_key)
        .scalar()
        or 0
    )
    temporary_references = int(
        db.query(func.count(BidUploadBatchFile.id))
        .filter(BidUploadBatchFile.temporary_object_ref == object_key)
        .scalar()
        or 0
    )
    return file_object_references == 0 and temporary_references == 0


def remove_bid_upload_batch_file(
    db: Session,
    *,
    batch_id: str,
    file_id: str,
    expected_file_etag: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> BidUploadFileRemoval:
    """Logically remove one draft BatchFile; caller commits before object delete."""

    current_time = now or _utc_now()
    batch = (
        db.query(BidUploadBatch)
        .filter(BidUploadBatch.id == batch_id)
        .with_for_update()
        .one_or_none()
    )
    if batch is None:
        raise BidUploadFileRemovalNotFound()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == batch.assessment_id)
        .with_for_update()
        .one()
    )
    if int(assessment.created_by) != int(actor_id) and not actor_is_admin:
        raise BidUploadFileRemovalNotFound()

    batch_file = (
        db.query(BidUploadBatchFile)
        .filter(
            BidUploadBatchFile.id == file_id,
            BidUploadBatchFile.batch_id == batch.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if batch_file is None:
        raise BidUploadFileRemovalNotFound()

    current_file_etag = upload_batch_file_etag(
        str(batch_file.id),
        int(batch_file.row_version),
    )
    if expected_file_etag != current_file_etag:
        raise BidUploadFileRemovalVersionMismatch(
            batch_id=str(batch.id),
            file_id=str(batch_file.id),
            provided_etag=expected_file_etag,
            current_etag=current_file_etag,
            current_row_version=int(batch_file.row_version),
        )

    batch_status = str(batch.status)
    if batch_status in {"committing", "committed"}:
        raise BidUploadFileRemovalBatchCommitted(status=batch_status)
    if (
        batch_status not in REMOVABLE_BATCH_STATUSES
        or _as_utc(batch.expires_at) <= _as_utc(current_time)
    ):
        raise BidUploadFileRemovalBatchStateConflict(status=batch_status)

    before = _file_snapshot(batch_file)
    file_object = None
    if batch_file.file_object_id:
        file_object = (
            db.query(BidFileObject)
            .filter(BidFileObject.id == batch_file.file_object_id)
            .with_for_update()
            .one_or_none()
        )
    temporary_object_ref = (
        str(batch_file.temporary_object_ref)
        if batch_file.temporary_object_ref
        else None
    )
    db.delete(batch_file)
    db.flush()

    cleanup_object_key: str | None = None
    preserved_reference_count = 0
    if file_object is not None:
        object_key = str(file_object.object_key)
        reference_counts = _remaining_object_references(
            db,
            file_object_id=str(file_object.id),
            object_key=object_key,
        )
        preserved_reference_count = sum(reference_counts)
        if preserved_reference_count == 0 and _is_managed_upload_key(object_key):
            db.delete(file_object)
            db.flush()
            cleanup_object_key = object_key
    elif _unlinked_temporary_key_is_safe(
        db,
        object_key=temporary_object_ref,
    ):
        cleanup_object_key = temporary_object_ref

    remaining_statuses = [
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
        remaining_statuses,
        deactivation_count=deactivation_count,
    )
    batch.row_version = int(batch.row_version) + 1
    batch.updated_by = int(actor_id)
    db.flush()

    batch_snapshot = build_upload_batch_snapshot(db, batch)
    ready_count = sum(
        1 for row in batch_snapshot["files"] if row["status"] == "ready"
    )
    failed_count = sum(
        1
        for row in batch_snapshot["files"]
        if row["status"] in {"rejected", "failed"}
    )
    event = append_outbox_event(
        db,
        event_type="bid.upload_file.removed.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="upload_batch",
        aggregate_id=str(batch.id),
        aggregate_version=int(batch.row_version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        payload_schema="bid.upload_file.removed.v1.payload",
        payload={
            "batch_id": str(batch.id),
            "batch_file_id": str(file_id),
            "status": str(batch.status),
            "ready_count": ready_count,
            "failed_count": failed_count,
            "resource_version": int(batch.row_version),
        },
        dedupe_key=f"upload-file-removed:{file_id}",
        occurred_at=current_time,
    )
    receipt = {
        "batch_id": str(batch.id),
        "batch_row_version": int(batch.row_version),
        "batch_etag": upload_batch_etag(
            str(batch.id),
            int(batch.row_version),
            limits=dict(batch_snapshot["limits"]),
        ),
        "removed_batch_file_id": str(file_id),
        "removed_file_etag": current_file_etag,
    }
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="upload_file.remove_draft",
        entity_type="upload_file",
        entity_id=str(file_id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before=before,
        after={
            "removed": True,
            "batch": batch_snapshot,
            "physical_delete_eligible": cleanup_object_key is not None,
            "preserved_reference_count": preserved_reference_count,
        },
        metadata={
            "http_method": "DELETE",
            "route_template": (
                "/api/v1/bid-upload-batches/{batch_id}/files/{file_id}"
            ),
            "batch_id": str(batch.id),
            "file_etag": current_file_etag,
            "outbox_event_type": event.event_type,
        },
        correlation_id=event.event_id,
        occurred_at=current_time,
    )
    return BidUploadFileRemoval(
        command=IdempotentCommandResult(
            status_code=204,
            body=receipt,
            resource_type="upload_batch",
            resource_id=str(batch.id),
            response_ref=f"/api/v1/bid-upload-batches/{batch.id}",
        ),
        cleanup_object_key=cleanup_object_key,
        preserved_reference_count=preserved_reference_count,
    )
