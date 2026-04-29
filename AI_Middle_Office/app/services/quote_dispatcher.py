import logging
from threading import Thread
from typing import Optional

from app.core.config import settings
from app.services.quote_job_runner import run_quote_job


logger = logging.getLogger(__name__)


def dispatch_quote_job(job_id: str) -> Optional[str]:
    mode = (settings.task_queue_mode or "local").lower()

    if mode == "disabled":
        logger.info("quote_job_dispatch_disabled", extra={"job_id": job_id, "event": "quote_job_dispatch_disabled"})
        return None

    if mode == "inline":
        run_quote_job(job_id)
        return None

    if mode == "celery":
        from app.tasks.quote_tasks import run_quote_job_task

        if run_quote_job_task is None:
            raise RuntimeError("Celery 未安装，请先安装 requirements.txt 中的 celery[redis]")
        result = run_quote_job_task.delay(job_id)
        return result.id

    if mode == "local":
        worker = Thread(target=run_quote_job, args=(job_id,), daemon=True)
        worker.start()
        return None

    raise RuntimeError(f"未知 TASK_QUEUE_MODE: {settings.task_queue_mode}")
