"""Deferred, reference-aware cleanup for abandoned API-16 upload batches."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidDocumentVersion,
    BidFileObject,
    BidUploadBatch,
    BidUploadBatchFile,
)
from app.services.bid_assessment_eventing import append_audit_log
from app.services.bid_upload_batch_snapshots import build_upload_batch_snapshot
from app.services.bid_upload_file_storage import (
    BidUploadObjectStorage,
    normalized_upload_object_prefix,
)


@dataclass(frozen=True)
class ReleasedAbandonedBatch:
    batch_id: str
    cleanup_object_keys: tuple[str, ...]
    detached_file_count: int
    removed_file_object_count: int
    preserved_reference_count: int


@dataclass(frozen=True)
class AbandonedBatchCleanupResult:
    scanned_batches: int
    released_batches: int
    detached_files: int
    removed_file_objects: int
    preserved_references: int
    deleted_objects: int
    delete_failed: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_managed_key(object_key: str | None) -> bool:
    if not object_key:
        return False
    prefix = normalized_upload_object_prefix().rstrip("/") + "/"
    return str(object_key).startswith(prefix)


def _object_reference_count(
    db: Session,
    *,
    file_object_id: str,
    object_key: str,
) -> int:
    return sum(
        (
            int(
                db.query(func.count(BidUploadBatchFile.id))
                .filter(BidUploadBatchFile.file_object_id == file_object_id)
                .scalar()
                or 0
            ),
            int(
                db.query(func.count(BidDocumentVersion.id))
                .filter(BidDocumentVersion.file_object_id == file_object_id)
                .scalar()
                or 0
            ),
            int(
                db.query(func.count(BidUploadBatchFile.id))
                .filter(BidUploadBatchFile.temporary_object_ref == object_key)
                .scalar()
                or 0
            ),
        )
    )


def _unlinked_key_is_safe(db: Session, *, object_key: str) -> bool:
    if not _is_managed_key(object_key):
        return False
    file_objects = int(
        db.query(func.count(BidFileObject.id))
        .filter(BidFileObject.object_key == object_key)
        .scalar()
        or 0
    )
    temporary_refs = int(
        db.query(func.count(BidUploadBatchFile.id))
        .filter(BidUploadBatchFile.temporary_object_ref == object_key)
        .scalar()
        or 0
    )
    return file_objects == 0 and temporary_refs == 0


def release_abandoned_batch_references(
    db: Session,
    *,
    batch_id: str,
    now: datetime | None = None,
) -> ReleasedAbandonedBatch | None:
    """Detach one due batch in a DB transaction; caller deletes keys after commit."""

    current_time = _as_utc(now or _utc_now())
    batch = (
        db.query(BidUploadBatch)
        .filter(BidUploadBatch.id == batch_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        batch is None
        or str(batch.status) != "abandoned"
        or batch.cleanup_after is None
        or _as_utc(batch.cleanup_after) > current_time
        or batch.cleanup_completed_at is not None
    ):
        return None

    before = build_upload_batch_snapshot(db, batch)
    files = (
        db.query(BidUploadBatchFile)
        .filter(BidUploadBatchFile.batch_id == batch.id)
        .order_by(BidUploadBatchFile.id.asc())
        .with_for_update()
        .all()
    )
    file_object_ids = {
        str(row.file_object_id) for row in files if row.file_object_id is not None
    }
    file_objects = {
        str(row.id): row
        for row in (
            db.query(BidFileObject)
            .filter(BidFileObject.id.in_(file_object_ids))
            .with_for_update()
            .all()
            if file_object_ids
            else []
        )
    }

    detached_file_ids: set[str] = set()
    unlinked_temporary_keys: set[str] = set()
    preserved_reference_count = 0
    for row in files:
        changed = False
        file_object = (
            file_objects.get(str(row.file_object_id)) if row.file_object_id else None
        )
        if file_object is not None:
            if _is_managed_key(str(file_object.object_key)):
                row.file_object_id = None
                changed = True
            else:
                preserved_reference_count += 1
        if row.temporary_object_ref:
            temporary_key = str(row.temporary_object_ref)
            if _is_managed_key(temporary_key):
                row.temporary_object_ref = None
                unlinked_temporary_keys.add(temporary_key)
                changed = True
            else:
                preserved_reference_count += 1
        if changed:
            row.row_version = int(row.row_version) + 1
            detached_file_ids.add(str(row.id))
    db.flush()

    cleanup_keys: set[str] = set()
    removed_file_object_count = 0
    for file_object in file_objects.values():
        object_key = str(file_object.object_key)
        if not _is_managed_key(object_key):
            continue
        remaining = _object_reference_count(
            db,
            file_object_id=str(file_object.id),
            object_key=object_key,
        )
        if remaining:
            preserved_reference_count += remaining
            continue
        db.delete(file_object)
        db.flush()
        cleanup_keys.add(object_key)
        removed_file_object_count += 1

    for object_key in unlinked_temporary_keys:
        if _unlinked_key_is_safe(db, object_key=object_key):
            cleanup_keys.add(object_key)
        elif object_key not in cleanup_keys:
            preserved_reference_count += 1

    batch.cleanup_completed_at = current_time
    batch.row_version = int(batch.row_version) + 1
    db.flush()
    after = build_upload_batch_snapshot(db, batch)
    append_audit_log(
        db,
        actor_type="system",
        actor_ref="bid.cleanup_abandoned_upload_batches",
        action="upload_batch.cleanup_references",
        entity_type="upload_batch",
        entity_id=str(batch.id),
        assessment_id=str(batch.assessment_id),
        outcome="succeeded",
        request_id=f"cleanup_{uuid.uuid4().hex}",
        before=before,
        after=after,
        metadata={
            "detached_file_count": len(detached_file_ids),
            "removed_file_object_count": removed_file_object_count,
            "physical_delete_candidate_count": len(cleanup_keys),
            "preserved_reference_count": preserved_reference_count,
            "physical_delete_phase": "after_database_commit",
        },
        occurred_at=current_time,
    )
    db.flush()
    return ReleasedAbandonedBatch(
        batch_id=str(batch.id),
        cleanup_object_keys=tuple(sorted(cleanup_keys)),
        detached_file_count=len(detached_file_ids),
        removed_file_object_count=removed_file_object_count,
        preserved_reference_count=preserved_reference_count,
    )


def cleanup_due_abandoned_upload_batches(
    *,
    session_factory: Callable[[], Session],
    storage: BidUploadObjectStorage,
    now: datetime | None = None,
    limit: int = 100,
) -> AbandonedBatchCleanupResult:
    """Release due batches, commit DB first, then delete exact managed keys."""

    current_time = _as_utc(now or _utc_now())
    inventory = session_factory()
    try:
        batch_ids = [
            str(value[0])
            for value in (
                inventory.query(BidUploadBatch.id)
                .filter(
                    BidUploadBatch.status == "abandoned",
                    BidUploadBatch.cleanup_completed_at.is_(None),
                    BidUploadBatch.cleanup_after <= current_time,
                )
                .order_by(BidUploadBatch.cleanup_after.asc(), BidUploadBatch.id.asc())
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
        ]
    finally:
        inventory.close()

    released: list[ReleasedAbandonedBatch] = []
    for batch_id in batch_ids:
        db = session_factory()
        try:
            with db.begin():
                result = release_abandoned_batch_references(
                    db,
                    batch_id=batch_id,
                    now=current_time,
                )
            if result is not None:
                released.append(result)
        finally:
            db.close()

    deleted_objects = 0
    delete_failed = 0
    for result in released:
        for object_key in result.cleanup_object_keys:
            try:
                storage.delete(object_key=object_key)
                deleted_objects += 1
            except Exception:
                # Metadata and authoritative references are already committed.
                # The generic orphan reconciler will retry this exact managed key.
                delete_failed += 1

    return AbandonedBatchCleanupResult(
        scanned_batches=len(batch_ids),
        released_batches=len(released),
        detached_files=sum(row.detached_file_count for row in released),
        removed_file_objects=sum(row.removed_file_object_count for row in released),
        preserved_references=sum(row.preserved_reference_count for row in released),
        deleted_objects=deleted_objects,
        delete_failed=delete_failed,
    )
