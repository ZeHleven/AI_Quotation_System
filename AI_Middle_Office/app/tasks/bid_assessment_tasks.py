"""Celery entries for bid-assessment intake and shared runtime infrastructure.

The deterministic Plan Commit, P0-P4 continuation, Task-DAG maintenance, and
fixed MVP1 task queue are no longer registered.  Input bootstrap, model/tool
ledgers, cancellation, validation, and historical projections remain shared
infrastructure for the replacement goal-driven Agent runtime.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.bid_assessment_eventing import project_outbox_event_to_public
from app.services.bid_assessment_outbox import dispatch_outbox_batch
from app.services.bid_document_parse_consumer import (
    consume_document_parse_requested_event,
)
from app.services.bid_document_parse_execution import (
    execute_document_parse_request,
    process_queued_document_parse_runs,
)
from app.services.bid_evidence_retrieval_index import (
    consume_document_parsed_for_retrieval_index,
    process_pending_retrieval_indexes,
)
from app.services.bid_evidence_semantic_index import (
    process_pending_semantic_indexes,
)
from app.services.bid_lot_detection_runs import (
    consume_document_parse_terminal_for_lots,
)
from app.services.bid_lot_detection_worker import (
    consume_lot_detection_requested_event,
    execute_lot_detection_request,
    process_queued_lot_detection_runs,
)
from app.services.bid_model_execution import recover_expired_model_calls
from app.services.bid_mvp1_executor import process_mvp1_model_queue
from app.services.bid_mvp1_model_provider import ControlledChatCompletionsProvider
from app.services.bid_run_bootstrap import (
    BidRunInputNotReady,
    consume_plan_requested_event,
    process_pending_run_bootstraps,
)
from app.services.bid_run_lifecycle import maintain_run_lifecycle
from app.services.bid_run_validation import (
    consume_run_validation_requested_event,
    maintain_run_validations,
    process_run_validation_queue,
)
from app.services.bid_semantic_vector_provider import (
    BidSemanticProviderError,
    configured_bid_semantic_provider,
)
from app.services.bid_tool_context import maintain_tool_operations
from app.services.bid_tool_execution import (
    maintain_tool_dispatches,
    process_tool_dispatch_queue,
)
from app.services.bid_upload_batch_cleanup import cleanup_due_abandoned_upload_batches
from app.services.bid_upload_file_storage import get_bid_upload_object_storage
from app.services.bid_upload_files import cleanup_orphaned_bid_upload_objects
from app.tasks.celery_app import celery_app


# Import compatibility only.  These names deliberately have no implementation
# and are never registered with Celery after removal of the fixed Workflow.
process_bid_plan_commit_queue_task = None
process_bid_plan_continuation_queue_task = None
maintain_bid_task_runtime_task = None
process_bid_mvp1_task_queue_task = None


if celery_app is None:
    dispatch_bid_outbox_task = None
    consume_bid_outbox_event_task = None
    process_bid_document_parse_queue_task = None
    process_bid_evidence_retrieval_index_queue_task = None
    process_bid_evidence_semantic_index_queue_task = None
    process_bid_lot_detection_queue_task = None
    process_bid_run_bootstrap_queue_task = None
    maintain_bid_run_lifecycle_task = None
    maintain_bid_model_calls_task = None
    process_bid_mvp1_model_queue_task = None
    maintain_bid_tool_operations_task = None
    process_bid_tool_dispatch_queue_task = None
    maintain_bid_tool_dispatches_task = None
    process_bid_run_validation_queue_task = None
    maintain_bid_run_validations_task = None
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
        name="bid.consume_outbox_event",
        max_retries=10,
        autoretry_for=(Exception,),
        retry_backoff=True,
        retry_backoff_max=300,
        retry_jitter=True,
    )
    def consume_bid_outbox_event_task(self, event_id: str) -> dict[str, object]:
        """Fan out one durable event to separately idempotent consumers."""

        public_db = SessionLocal()
        try:
            with public_db.begin():
                public_result = project_outbox_event_to_public(
                    public_db,
                    event_id=event_id,
                    retention_days=settings.bid_public_event_retention_days,
                )
        finally:
            public_db.close()

        document_db = SessionLocal()
        try:
            with document_db.begin():
                document_result = consume_document_parse_requested_event(
                    document_db,
                    event_id=event_id,
                )
        finally:
            document_db.close()

        manifest_db = SessionLocal()
        try:
            with manifest_db.begin():
                manifest_result = consume_document_parse_terminal_for_lots(
                    manifest_db,
                    event_id=event_id,
                )
        finally:
            manifest_db.close()

        retrieval_result = None
        if settings.feature_bid_assessment_pdf_c3_role_aware_retrieval:
            retrieval_db = SessionLocal()
            try:
                with retrieval_db.begin():
                    retrieval_result = consume_document_parsed_for_retrieval_index(
                        retrieval_db,
                        event_id=event_id,
                    )
            finally:
                retrieval_db.close()

        lot_db = SessionLocal()
        try:
            with lot_db.begin():
                lot_result = consume_lot_detection_requested_event(
                    lot_db,
                    event_id=event_id,
                )
        finally:
            lot_db.close()

        execution = None
        if settings.feature_bid_assessment_phase2_document_worker:
            execution = execute_document_parse_request(event_id=event_id)

        lot_execution = None
        if settings.feature_bid_assessment_phase2_lot_worker:
            lot_execution = execute_lot_detection_request(event_id=event_id)

        run_bootstrap_result = None
        run_bootstrap_status = "worker_disabled"
        if settings.feature_bid_assessment_phase3_run_bootstrap:
            run_db = SessionLocal()
            try:
                try:
                    with run_db.begin():
                        run_bootstrap_result = consume_plan_requested_event(
                            run_db,
                            event_id=event_id,
                        )
                    run_bootstrap_status = "processed"
                except BidRunInputNotReady:
                    run_bootstrap_status = "input_not_ready"
            finally:
                run_db.close()

        run_validation_result = None
        run_validation_status = "worker_disabled"
        if settings.feature_bid_assessment_phase3_run_validation:
            validation_db = SessionLocal()
            try:
                with validation_db.begin():
                    run_validation_result = consume_run_validation_requested_event(
                        validation_db,
                        event_id=event_id,
                    )
                run_validation_status = "processed"
            finally:
                validation_db.close()

        return {
            "event_id": event_id,
            "public_duplicate": public_result.duplicate,
            "document_duplicate": document_result.duplicate,
            "public_result_hash": public_result.result_hash,
            "document_result_hash": document_result.result_hash,
            "manifest_duplicate": manifest_result.duplicate,
            "manifest_result_hash": manifest_result.result_hash,
            "retrieval_index_duplicate": (
                retrieval_result.duplicate
                if retrieval_result is not None
                else False
            ),
            "retrieval_index_result_hash": (
                retrieval_result.result_hash
                if retrieval_result is not None
                else None
            ),
            "lot_duplicate": lot_result.duplicate,
            "lot_result_hash": lot_result.result_hash,
            "document_execution_status": (
                execution.status if execution is not None else "worker_disabled"
            ),
            "document_parse_run_id": (
                execution.parse_run_id if execution is not None else None
            ),
            "lot_execution_status": (
                lot_execution.status if lot_execution is not None else "worker_disabled"
            ),
            "lot_detection_run_id": (
                lot_execution.detection_run_id
                if lot_execution is not None
                else None
            ),
            "run_bootstrap_status": run_bootstrap_status,
            "run_bootstrap_duplicate": (
                run_bootstrap_result.duplicate
                if run_bootstrap_result is not None
                else False
            ),
            "run_validation_status": run_validation_status,
            "run_validation_duplicate": (
                run_validation_result.duplicate
                if run_validation_result is not None
                else False
            ),
        }

    @celery_app.task(name="bid.process_document_parse_queue")
    def process_bid_document_parse_queue_task(limit: int = 20) -> dict[str, object]:
        if not settings.feature_bid_assessment_phase2_document_worker:
            return {"processed": 0, "statuses": {}}
        results = process_queued_document_parse_runs(
            limit=max(1, min(int(limit), 100)),
        )
        statuses: dict[str, int] = {}
        for result in results:
            statuses[result.status] = statuses.get(result.status, 0) + 1
        return {"processed": len(results), "statuses": statuses}

    @celery_app.task(name="bid.process_evidence_retrieval_index_queue")
    def process_bid_evidence_retrieval_index_queue_task(
        limit: int = 20,
    ) -> dict[str, int]:
        if not settings.feature_bid_assessment_pdf_c3_role_aware_retrieval:
            return {"scanned": 0, "ready": 0, "stale": 0, "failed": 0}
        result = process_pending_retrieval_indexes(
            session_factory=SessionLocal,
            limit=max(1, min(int(limit), 100)),
        )
        return {
            "scanned": result.scanned,
            "ready": result.ready,
            "stale": result.stale,
            "failed": result.failed,
        }

    @celery_app.task(name="bid.process_evidence_semantic_index_queue")
    def process_bid_evidence_semantic_index_queue_task(
        limit: int = 20,
    ) -> dict[str, int | str]:
        if not settings.feature_bid_assessment_rq2a_semantic_recall:
            return {
                "scanned": 0,
                "ready": 0,
                "retry_wait": 0,
                "stale": 0,
                "failed": 0,
            }
        try:
            provider = configured_bid_semantic_provider(settings)
        except BidSemanticProviderError as exc:
            return {
                "scanned": 0,
                "ready": 0,
                "retry_wait": 0,
                "stale": 0,
                "failed": 1,
                "error_code": str(exc) or exc.code,
            }
        result = process_pending_semantic_indexes(
            provider=provider,
            session_factory=SessionLocal,
            worker_id="celery:bid-semantic-index",
            limit=max(1, min(int(limit), 100)),
            lease_seconds=settings.bid_evidence_semantic_lease_seconds,
            max_attempts=settings.bid_evidence_semantic_max_attempts,
        )
        return {
            "scanned": result.scanned,
            "ready": result.ready,
            "retry_wait": result.retry_wait,
            "stale": result.stale,
            "failed": result.failed,
        }

    @celery_app.task(name="bid.process_lot_detection_queue")
    def process_bid_lot_detection_queue_task(limit: int = 20) -> dict[str, object]:
        if not settings.feature_bid_assessment_phase2_lot_worker:
            return {"processed": 0, "statuses": {}}
        results = process_queued_lot_detection_runs(
            limit=max(1, min(int(limit), 100)),
        )
        statuses: dict[str, int] = {}
        for result in results:
            statuses[result.status] = statuses.get(result.status, 0) + 1
        return {"processed": len(results), "statuses": statuses}

    @celery_app.task(name="bid.process_run_bootstrap_queue")
    def process_bid_run_bootstrap_queue_task(limit: int = 20) -> dict[str, int]:
        if not settings.feature_bid_assessment_phase3_run_bootstrap:
            return {
                "scanned": 0,
                "created": 0,
                "duplicate": 0,
                "pending_input": 0,
                "ignored": 0,
                "failed": 0,
            }
        result = process_pending_run_bootstraps(
            session_factory=SessionLocal,
            limit=max(1, min(int(limit), 200)),
        )
        return {
            "scanned": result.scanned,
            "created": result.created,
            "duplicate": result.duplicate,
            "pending_input": result.pending_input,
            "ignored": result.ignored,
            "failed": result.failed,
        }

    @celery_app.task(name="bid.maintain_run_lifecycle")
    def maintain_bid_run_lifecycle_task(limit: int = 100) -> dict[str, int]:
        if not settings.feature_bid_assessment_phase3_run_lifecycle:
            return {
                "scanned": 0,
                "cancelled": 0,
                "tasks_cancelled": 0,
                "attempts_cancelled": 0,
                "operations_cancelled": 0,
                "failed": 0,
            }
        result = maintain_run_lifecycle(
            session_factory=SessionLocal,
            limit=max(1, min(int(limit), 500)),
        )
        return {
            "scanned": result.scanned,
            "cancelled": result.cancelled,
            "tasks_cancelled": result.tasks_cancelled,
            "attempts_cancelled": result.attempts_cancelled,
            "operations_cancelled": result.operations_cancelled,
            "failed": result.failed,
        }

    @celery_app.task(name="bid.maintain_model_calls")
    def maintain_bid_model_calls_task(limit: int = 100) -> dict[str, int]:
        if not settings.feature_bid_assessment_phase4_model_executor:
            return {"scanned": 0, "recovered": 0, "uncertain": 0, "failed": 0}
        db = SessionLocal()
        try:
            with db.begin():
                result = recover_expired_model_calls(
                    db,
                    limit=max(1, min(int(limit), 500)),
                )
        finally:
            db.close()
        return {
            "scanned": result.scanned,
            "recovered": result.recovered,
            "uncertain": result.uncertain,
            "failed": result.failed,
        }

    @celery_app.task(bind=True, name="bid.process_mvp1_model_queue")
    def process_bid_mvp1_model_queue_task(self, limit: int = 10) -> dict[str, int]:
        if not (
            settings.feature_bid_assessment_phase4_mvp
            and settings.feature_bid_assessment_phase4_model_executor
            and settings.feature_bid_assessment_phase4_deepseek_adapter
        ):
            return {"claimed": 0, "succeeded": 0, "deferred": 0, "failed": 0}
        provider = ControlledChatCompletionsProvider(
            session_factory=SessionLocal,
            provider_ref=settings.bid_assessment_model_provider_ref,
            model_ref=settings.bid_assessment_model_id,
            api_key=settings.bid_assessment_model_api_key,
            chat_url=settings.bid_assessment_model_chat_url,
            thinking_mode=settings.bid_assessment_model_thinking_mode,
            timeout_seconds=settings.bid_assessment_model_timeout_seconds,
        )
        result = process_mvp1_model_queue(
            session_factory=SessionLocal,
            worker_id=(
                f"celery:{self.request.hostname or 'worker'}:mvp1-model:"
                f"{self.request.id or 'manual'}"
            )[:128],
            provider=provider,
            limit=max(1, min(int(limit), 50)),
            lease_seconds=settings.bid_model_call_lease_seconds,
        )
        return {
            "claimed": result.claimed,
            "succeeded": result.succeeded,
            "deferred": result.deferred,
            "failed": result.failed,
        }

    @celery_app.task(name="bid.maintain_tool_operations")
    def maintain_bid_tool_operations_task(limit: int = 100) -> dict[str, int]:
        if not settings.feature_bid_assessment_phase3_tool_context:
            return {
                "scanned": 0,
                "timed_out": 0,
                "recovered": 0,
                "failed": 0,
            }
        result = maintain_tool_operations(
            session_factory=SessionLocal,
            limit=max(1, min(int(limit), 500)),
        )
        return {
            "scanned": result.scanned,
            "timed_out": result.timed_out,
            "recovered": result.recovered,
            "failed": result.failed,
        }

    @celery_app.task(bind=True, name="bid.process_tool_dispatch_queue")
    def process_bid_tool_dispatch_queue_task(
        self,
        limit: int = 20,
    ) -> dict[str, int]:
        if not (
            settings.feature_bid_assessment_phase3_task_runtime
            and settings.feature_bid_assessment_phase3_tool_context
            and settings.feature_bid_assessment_phase3_tool_executor
        ):
            return {
                "claimed": 0,
                "succeeded": 0,
                "retry_wait": 0,
                "failed": 0,
                "uncertain": 0,
            }
        result = process_tool_dispatch_queue(
            session_factory=SessionLocal,
            worker_id=(
                f"celery:{self.request.hostname or 'worker'}:"
                f"{self.request.id or 'manual'}"
            )[:128],
            scope_signing_key=settings.bid_tool_scope_signing_key,
            limit=max(1, min(int(limit), 100)),
            lease_seconds=max(15, int(settings.bid_tool_dispatch_lease_seconds)),
        )
        return {
            "claimed": result.claimed,
            "succeeded": result.succeeded,
            "retry_wait": result.retry_wait,
            "failed": result.failed,
            "uncertain": result.uncertain,
        }

    @celery_app.task(name="bid.maintain_tool_dispatches")
    def maintain_bid_tool_dispatches_task(limit: int = 100) -> dict[str, int]:
        if not (
            settings.feature_bid_assessment_phase3_task_runtime
            and settings.feature_bid_assessment_phase3_tool_context
            and settings.feature_bid_assessment_phase3_tool_executor
        ):
            return {
                "scanned": 0,
                "recovered": 0,
                "uncertain": 0,
                "failed": 0,
            }
        result = maintain_tool_dispatches(
            session_factory=SessionLocal,
            limit=max(1, min(int(limit), 500)),
        )
        return {
            "scanned": result.scanned,
            "recovered": result.recovered,
            "uncertain": result.uncertain,
            "failed": result.failed,
        }

    @celery_app.task(bind=True, name="bid.process_run_validation_queue")
    def process_bid_run_validation_queue_task(
        self,
        limit: int = 20,
    ) -> dict[str, int]:
        if not settings.feature_bid_assessment_phase3_run_validation:
            return {
                "scanned": 0,
                "claimed": 0,
                "passed": 0,
                "failed": 0,
                "stale": 0,
                "ignored": 0,
                "errors": 0,
            }
        result = process_run_validation_queue(
            session_factory=SessionLocal,
            worker_id=(
                f"celery:{self.request.hostname or 'worker'}:"
                f"{self.request.id or 'manual'}"
            )[:128],
            limit=max(1, min(int(limit), 200)),
            lease_seconds=settings.bid_run_validation_lease_seconds,
        )
        return {
            "scanned": result.scanned,
            "claimed": result.claimed,
            "passed": result.passed,
            "failed": result.failed,
            "stale": result.stale,
            "ignored": result.ignored,
            "errors": result.errors,
        }

    @celery_app.task(name="bid.maintain_run_validations")
    def maintain_bid_run_validations_task(limit: int = 100) -> dict[str, int]:
        if not settings.feature_bid_assessment_phase3_run_validation:
            return {
                "scanned_events": 0,
                "materialized": 0,
                "duplicate": 0,
                "recovered": 0,
                "cancelled": 0,
                "failed": 0,
            }
        result = maintain_run_validations(
            session_factory=SessionLocal,
            limit=max(1, min(int(limit), 500)),
        )
        return {
            "scanned_events": result.scanned_events,
            "materialized": result.materialized,
            "duplicate": result.duplicate,
            "recovered": result.recovered,
            "cancelled": result.cancelled,
            "failed": result.failed,
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
