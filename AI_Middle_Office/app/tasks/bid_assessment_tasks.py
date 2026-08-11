"""Celery entries for bid-assessment Outbox dispatch and public projection."""
from __future__ import annotations

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.bid_assessment_eventing import project_outbox_event_to_public
from app.services.bid_assessment_outbox import dispatch_outbox_batch
from app.services.bid_upload_batch_cleanup import cleanup_due_abandoned_upload_batches
from app.services.bid_upload_file_storage import get_bid_upload_object_storage
from app.services.bid_upload_files import cleanup_orphaned_bid_upload_objects
from app.tasks.celery_app import celery_app


if celery_app is None:
    dispatch_bid_outbox_task = None
    project_bid_public_event_task = None
    cleanup_bid_upload_orphans_task = None
    cleanup_abandoned_bid_upload_batches_task = None
else:

    @celery_app.task(bind=True, name="bid.dispatch_outbox")
    def dispatch_bid_outbox_task(self) -> dict[str, int]:
        result = dispatch_outbox_batch(
            worker_id=(
                f"celery:{self.request.hostname or 'worker'}:"
                f"{self.request.id or 'manual'}"
            )[:128],
            batch_size=settings.bid_outbox_batch_size,
            lease_seconds=settings.bid_outbox_lease_seconds,
            max_attempts=settings.bid_outbox_max_attempts,
        )
        return {
            "claimed": result.claimed,
            "published": result.published,
            "retry_wait": result.retry_wait,
            "dead_lettered": result.dead_lettered,
            "lease_lost": result.lease_lost,
        }

    @celery_app.task(
        bind=True,
        name="bid.project_public_event",
        max_retries=10,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_backoff_max=300,
        retry_jitter=True,
    )
    def project_bid_public_event_task(self, event_id: str) -> dict[str, object]:
        db = SessionLocal()
        try:
            with db.begin():
                result = project_outbox_event_to_public(
                    db,
                    event_id=event_id,
                    retention_days=settings.bid_public_event_retention_days,
                )
            return {
                "event_id": result.event_id,
                "duplicate": result.duplicate,
                "result_hash": result.result_hash,
                "result_ref": result.result_ref,
            }
        finally:
            db.close()

    @celery_app.task(
        name="bid.cleanup_upload_orphans",
        max_retries=5,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_backoff_max=300,
        retry_jitter=True,
    )
    def cleanup_bid_upload_orphans_task(limit: int = 1000) -> dict[str, int]:
        """Reconcile old temporary objects; never delete a referenced object."""

        db = SessionLocal()
        try:
            result = cleanup_orphaned_bid_upload_objects(
                db,
                storage=get_bid_upload_object_storage(),
                limit=max(1, min(int(limit), 10000)),
            )
            return {
                "scanned": result.scanned,
                "referenced": result.referenced,
                "deleted": result.deleted,
                "delete_failed": result.delete_failed,
            }
        finally:
            db.close()

    @celery_app.task(
        name="bid.cleanup_abandoned_upload_batches",
        max_retries=5,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_backoff_max=300,
        retry_jitter=True,
    )
    def cleanup_abandoned_bid_upload_batches_task(
        limit: int = 100,
    ) -> dict[str, int]:
        """Release API-16 references only after each frozen cleanup_after."""

        if not settings.feature_bid_assessment_v1_runtime:
            return {
                "scanned_batches": 0,
                "released_batches": 0,
                "detached_files": 0,
                "removed_file_objects": 0,
                "preserved_references": 0,
                "deleted_objects": 0,
                "delete_failed": 0,
            }
        result = cleanup_due_abandoned_upload_batches(
            session_factory=SessionLocal,
            storage=get_bid_upload_object_storage(),
            limit=max(1, min(int(limit), 1000)),
        )
        return {
            "scanned_batches": result.scanned_batches,
            "released_batches": result.released_batches,
            "detached_files": result.detached_files,
            "removed_file_objects": result.removed_file_objects,
            "preserved_references": result.preserved_references,
            "deleted_objects": result.deleted_objects,
            "delete_failed": result.delete_failed,
        }
