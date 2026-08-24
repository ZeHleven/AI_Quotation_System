from app.core.config import settings

try:
    from celery import Celery
except Exception:
    Celery = None


if Celery is None:
    celery_app = None
else:
    celery_app = Celery(
        "ai_middle_office",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=[
            "app.tasks.quote_tasks",
            "app.tasks.tender_evidence_tasks",
            "app.tasks.bid_assessment_tasks",
        ],
    )
    celery_app.conf.update(
        task_track_started=True,
        task_time_limit=settings.quote_task_time_limit_seconds,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_acks_on_failure_or_timeout=True,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "visibility_timeout": max(300, settings.quote_task_time_limit_seconds + 60),
        },
        result_expires=24 * 60 * 60,
        beat_schedule={
            "bid-process-document-parse-queue": {
                "task": "bid.process_document_parse_queue",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-lot-detection-queue": {
                "task": "bid.process_lot_detection_queue",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-evidence-retrieval-index-queue": {
                "task": "bid.process_evidence_retrieval_index_queue",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-evidence-semantic-index-queue": {
                "task": "bid.process_evidence_semantic_index_queue",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-run-bootstrap-queue": {
                "task": "bid.process_run_bootstrap_queue",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-maintain-run-lifecycle": {
                "task": "bid.maintain_run_lifecycle",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-maintain-model-calls": {
                "task": "bid.maintain_model_calls",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-mvp1-model-queue": {
                "task": "bid.process_mvp1_model_queue",
                "schedule": 2.0,
                "options": {"expires": 2},
            },
            "bid-maintain-tool-operations": {
                "task": "bid.maintain_tool_operations",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-tool-dispatch-queue": {
                "task": "bid.process_tool_dispatch_queue",
                "schedule": 5.0,
                "options": {"expires": 4},
            },
            "bid-maintain-tool-dispatches": {
                "task": "bid.maintain_tool_dispatches",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-process-run-validation-queue": {
                "task": "bid.process_run_validation_queue",
                "schedule": 5.0,
                "options": {"expires": 4},
            },
            "bid-maintain-run-validations": {
                "task": "bid.maintain_run_validations",
                "schedule": 30.0,
                "options": {"expires": 25},
            },
            "bid-cleanup-abandoned-upload-batches": {
                "task": "bid.cleanup_abandoned_upload_batches",
                "schedule": 300.0,
                "options": {"expires": 240},
            }
        },
    )
