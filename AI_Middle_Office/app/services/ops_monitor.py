import socket
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.database import engine
from app.models.quote_job import QuoteJob
from app.services.file_storage import check_storage_health
from app.services.queue_health import check_task_queue


LOG_DIR = BASE_DIR / "logs"
ACTIVE_JOB_STATUSES = {"queued", "running"}
ERROR_LOG_KEYWORDS = (
    "ERROR",
    "CRITICAL",
    "Traceback",
    "OperationalError",
    "Lost connection",
    "request_failed",
    "quote_job_crashed",
    "failed",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _service_status(
    *,
    key: str,
    name: str,
    ok: bool,
    status: str,
    latency_ms: Optional[float] = None,
    detail: str = "",
    meta: Optional[dict] = None,
) -> dict:
    return {
        "key": key,
        "name": name,
        "ok": ok,
        "status": status,
        "latency_ms": latency_ms,
        "detail": detail,
        "meta": meta or {},
    }


def _measure(fn):
    started = time.perf_counter()
    result = fn()
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return result, latency_ms


def _parse_url_host_port(url: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if not parsed.hostname:
        raise ValueError(f"invalid endpoint: {url}")
    return parsed.hostname, parsed.port or default_port


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> None:
    with socket.create_connection((host, port), timeout=timeout_seconds):
        return


def _http_probe(url: str, timeout_seconds: float) -> dict:
    request = Request(url, headers={"User-Agent": "ai-middle-office-ops-probe"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return {"http_status": response.status}
    except HTTPError as exc:
        if exc.code < 500:
            return {"http_status": exc.code}
        raise


def check_database_service() -> dict:
    try:
        def _select_one():
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        _, latency_ms = _measure(_select_one)
        return _service_status(key="mysql", name="MySQL", ok=True, status="ok", latency_ms=latency_ms)
    except Exception as exc:
        return _service_status(key="mysql", name="MySQL", ok=False, status="error", detail=str(exc))


def check_redis_service() -> dict:
    try:
        import redis

        def _ping():
            client = redis.Redis.from_url(
                settings.celery_broker_url,
                socket_connect_timeout=settings.ops_probe_timeout_seconds,
                socket_timeout=settings.ops_probe_timeout_seconds,
            )
            client.ping()

        _, latency_ms = _measure(_ping)
        return _service_status(
            key="redis",
            name="Redis",
            ok=True,
            status="ok",
            latency_ms=latency_ms,
            meta={"broker_url": settings.celery_broker_url},
        )
    except Exception as exc:
        return _service_status(key="redis", name="Redis", ok=False, status="error", detail=str(exc))


def check_celery_service() -> dict:
    queue_status = check_task_queue()
    ok = bool(queue_status.get("ok"))
    return _service_status(
        key="celery",
        name="Celery Worker",
        ok=ok,
        status="ok" if ok else "degraded",
        detail=queue_status.get("detail", ""),
        meta=queue_status,
    )


def check_rag_service() -> dict:
    try:
        probe_url = settings.rag_service_url.rstrip("/") + "/docs"
        meta, latency_ms = _measure(lambda: _http_probe(probe_url, settings.ops_probe_timeout_seconds))
        return _service_status(
            key="rag",
            name="RAG Service",
            ok=True,
            status="ok",
            latency_ms=latency_ms,
            meta={"url": settings.rag_service_url, **meta},
        )
    except Exception as exc:
        return _service_status(
            key="rag",
            name="RAG Service",
            ok=False,
            status="error",
            detail=str(exc),
            meta={"url": settings.rag_service_url},
        )


def check_n8n_service() -> dict:
    try:
        host, port = _parse_url_host_port(settings.n8n_webhook_url_calc, 80)
        _, latency_ms = _measure(lambda: _tcp_probe(host, port, settings.ops_probe_timeout_seconds))
        return _service_status(
            key="n8n",
            name="n8n",
            ok=True,
            status="ok",
            latency_ms=latency_ms,
            meta={"host": host, "port": port},
        )
    except Exception as exc:
        return _service_status(key="n8n", name="n8n", ok=False, status="error", detail=str(exc))


def check_minio_service() -> dict:
    health = check_storage_health()
    ok = bool(health.get("ok"))
    if health.get("status") == "disabled":
        ok = True
    return _service_status(
        key="minio",
        name="MinIO",
        ok=ok,
        status=health.get("status", "unknown"),
        detail=health.get("detail", ""),
        meta=health,
    )


def collect_service_statuses() -> List[dict]:
    return [
        check_database_service(),
        check_redis_service(),
        check_celery_service(),
        check_rag_service(),
        check_minio_service(),
        check_n8n_service(),
    ]


def _format_dt(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return _as_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def collect_job_status(db: Session, stuck_minutes: Optional[int] = None) -> dict:
    stuck_minutes = stuck_minutes or settings.ops_stuck_job_minutes
    counts = {
        status: count
        for status, count in db.query(QuoteJob.status, func.count(QuoteJob.id)).group_by(QuoteJob.status).all()
    }
    cutoff = _utcnow() - timedelta(minutes=stuck_minutes)
    stuck_jobs = []
    candidates = db.query(QuoteJob).filter(QuoteJob.status.in_(ACTIVE_JOB_STATUSES)).all()
    for job in candidates:
        last_seen = _as_utc(job.updated_at) or _as_utc(job.created_at)
        if not last_seen or last_seen > cutoff:
            continue
        stuck_jobs.append(
            {
                "job_id": job.job_id,
                "username": job.username,
                "status": job.status,
                "stage": job.stage,
                "created_at": _format_dt(job.created_at),
                "updated_at": _format_dt(job.updated_at),
                "age_minutes": round((_utcnow() - (_as_utc(job.created_at) or last_seen)).total_seconds() / 60, 1),
                "idle_minutes": round((_utcnow() - last_seen).total_seconds() / 60, 1),
                "message_preview": (job.message or "")[:120],
                "error_message": job.error_message,
            }
        )

    return {
        "counts": counts,
        "active_count": sum(counts.get(status, 0) for status in ACTIVE_JOB_STATUSES),
        "stuck_minutes": stuck_minutes,
        "stuck_count": len(stuck_jobs),
        "stuck_jobs": sorted(stuck_jobs, key=lambda item: item["idle_minutes"], reverse=True)[:20],
    }


def _tail_lines(path: Path, max_lines: int) -> tuple[int, list[str]]:
    try:
        tail = deque(maxlen=max_lines)
        total_lines = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                total_lines += 1
                tail.append(line.rstrip("\n"))
        base_line_no = max(total_lines - len(tail) + 1, 1)
        return base_line_no, list(tail)
    except Exception:
        return 1, []


def collect_error_logs(limit: int = 50) -> dict:
    if not LOG_DIR.exists():
        return {"log_dir": str(LOG_DIR), "total_matches": 0, "items": []}

    log_files = sorted(
        [path for path in LOG_DIR.glob("*.log") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: settings.ops_log_max_files]

    matches = []
    for path in log_files:
        base_line_no, lines = _tail_lines(path, settings.ops_log_scan_lines)
        for offset, line in enumerate(lines):
            if not any(keyword in line for keyword in ERROR_LOG_KEYWORDS):
                continue
            matches.append(
                {
                    "file": path.name,
                    "line": base_line_no + offset,
                    "message": line.strip()[:800],
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

    return {
        "log_dir": str(LOG_DIR),
        "total_matches": len(matches),
        "items": matches[-limit:][::-1],
    }


def build_alerts(services: list[dict], jobs: dict, logs: dict) -> list[dict]:
    alerts = []
    down_services = [service["name"] for service in services if not service.get("ok")]
    if down_services:
        alerts.append(
            {
                "level": "critical",
                "title": "基础服务异常",
                "message": "、".join(down_services) + " 探活失败，请检查网络、容器或启动脚本。",
            }
        )
    if jobs.get("stuck_count", 0) > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "报价任务可能卡住",
                "message": f"{jobs['stuck_count']} 个任务超过 {jobs['stuck_minutes']} 分钟未更新。",
            }
        )
    if logs.get("total_matches", 0) > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "发现异常日志",
                "message": f"最近日志中匹配到 {logs['total_matches']} 条异常线索。",
            }
        )
    return alerts


def build_ops_dashboard(db: Session) -> Dict[str, Any]:
    services = collect_service_statuses()
    jobs = collect_job_status(db)
    logs = collect_error_logs(limit=30)
    alerts = build_alerts(services, jobs, logs)
    return {
        "generated_at": _utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "overall_status": "ready" if not alerts and all(service["ok"] for service in services) else "degraded",
        "services": services,
        "jobs": jobs,
        "logs": logs,
        "alerts": alerts,
    }
