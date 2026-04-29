import logging
from typing import Any, Dict

from app.core.config import settings


logger = logging.getLogger(__name__)


def _base_status(mode: str) -> Dict[str, Any]:
    return {
        "mode": mode,
        "broker_url": settings.celery_broker_url,
        "ok": True,
        "broker": "not_required",
        "worker": "not_required",
    }


def check_task_queue() -> Dict[str, Any]:
    mode = (settings.task_queue_mode or "local").lower()
    status = _base_status(mode)

    if mode in {"local", "inline"}:
        status["broker"] = "skipped"
        status["worker"] = "in_process"
        return status

    if mode == "disabled":
        status["broker"] = "skipped"
        status["worker"] = "disabled"
        return status

    if mode != "celery":
        status.update({"ok": False, "broker": "unknown_mode", "worker": "unknown_mode"})
        return status

    try:
        import redis

        client = redis.Redis.from_url(
            settings.celery_broker_url,
            socket_connect_timeout=settings.queue_health_timeout_seconds,
            socket_timeout=settings.queue_health_timeout_seconds,
        )
        client.ping()
        status["broker"] = "ok"
    except Exception as exc:
        logger.warning("task_queue_broker_unavailable", extra={"event": "task_queue_broker_unavailable", "detail": str(exc)})
        status.update({"ok": False, "broker": "error", "worker": "unknown", "detail": str(exc)})
        return status

    try:
        from app.tasks.celery_app import celery_app

        if celery_app is None:
            status.update({"ok": False, "worker": "celery_not_installed"})
            return status

        replies = celery_app.control.ping(timeout=settings.queue_health_timeout_seconds)
        status["worker"] = "ok" if replies else "no_reply"
        status["worker_count"] = len(replies)
        if not replies:
            status["ok"] = False
    except Exception as exc:
        logger.warning("task_queue_worker_unavailable", extra={"event": "task_queue_worker_unavailable", "detail": str(exc)})
        status.update({"ok": False, "worker": "error", "detail": str(exc)})

    return status
