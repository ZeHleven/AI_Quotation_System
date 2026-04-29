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
        include=["app.tasks.quote_tasks"],
    )
    celery_app.conf.update(
        task_track_started=True,
        task_time_limit=settings.quote_task_time_limit_seconds,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
    )
