import logging
from threading import Thread

from app.core.config import settings
from app.services.tender_parse_pipeline import run_tender_parse_job


logger = logging.getLogger(__name__)


def dispatch_tender_parse_job(job_uuid: str) -> str | None:
    mode = (settings.task_queue_mode or "local").lower()

    if mode == "disabled":
        logger.info(
            "tender_parse_dispatch_disabled",
            extra={
                "job_uuid": job_uuid,
                "event": "tender_parse_dispatch_disabled",
            },
        )
        return None

    if mode == "inline":
        run_tender_parse_job(job_uuid)
        return None

    if mode == "celery":
        from app.tasks.tender_evidence_tasks import run_tender_parse_job_task

        if run_tender_parse_job_task is None:
            raise RuntimeError(
                "Celery is unavailable; install and configure the task worker."
            )
        result = run_tender_parse_job_task.delay(job_uuid)
        return result.id

    if mode == "local":
        worker = Thread(
            target=run_tender_parse_job,
            args=(job_uuid,),
            daemon=True,
        )
        worker.start()
        return None

    raise RuntimeError(f"unknown TASK_QUEUE_MODE: {settings.task_queue_mode}")
