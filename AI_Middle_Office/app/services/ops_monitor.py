import logging
import json
import re
import socket
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.database import engine
from app.models.quote_job import QuoteJob
from app.services.file_storage import check_storage_health
from app.services.queue_health import check_task_queue


LOG_DIR = BASE_DIR / "logs"
ACKNOWLEDGED_LOG_EVENTS_FILE = "ops_acknowledged_events.json"
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
JSON_TS_RE = re.compile(r'"ts":\s*"([^"]+)"')
CELERY_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+:")
RESOLVED_LOG_CATEGORIES = {"quote_timeout"}


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


def _probe_attempts() -> int:
    try:
        return max(1, int(getattr(settings, "ops_probe_attempts", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _probe_retry_delay_seconds() -> float:
    try:
        return max(0.0, float(getattr(settings, "ops_probe_retry_delay_seconds", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _measure_probe(fn):
    started = time.perf_counter()
    last_exc = None
    attempts = _probe_attempts()
    delay = _probe_retry_delay_seconds()
    for attempt in range(attempts):
        try:
            result = fn()
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return result, latency_ms
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1 and delay > 0:
                time.sleep(delay)
    raise last_exc


def _parse_url_host_port(url: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if not parsed.hostname:
        raise ValueError(f"invalid endpoint: {url}")
    return parsed.hostname, parsed.port or default_port


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> None:
    with socket.create_connection((host, port), timeout=timeout_seconds):
        return


def _http_probe(url: str, timeout_seconds: float) -> dict:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        response = client.get(url, headers={"User-Agent": "ai-middle-office-ops-probe"})
    if response.status_code < 500:
        return {"http_status": response.status_code}
    response.raise_for_status()
    return {"http_status": response.status_code}


def check_database_service() -> dict:
    try:
        def _select_one():
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        _, latency_ms = _measure_probe(_select_one)
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

        _, latency_ms = _measure_probe(_ping)
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
    queue_status = {}
    started = time.perf_counter()
    attempts = _probe_attempts()
    delay = _probe_retry_delay_seconds()
    for attempt in range(attempts):
        queue_status = check_task_queue()
        if queue_status.get("ok"):
            break
        if attempt < attempts - 1 and delay > 0:
            time.sleep(delay)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    ok = bool(queue_status.get("ok"))
    return _service_status(
        key="celery",
        name="Celery Worker",
        ok=ok,
        status="ok" if ok else "degraded",
        latency_ms=latency_ms,
        detail=queue_status.get("detail", ""),
        meta=queue_status,
    )


def check_rag_service() -> dict:
    try:
        probe_url = settings.rag_service_url.rstrip("/") + "/docs"
        meta, latency_ms = _measure_probe(lambda: _http_probe(probe_url, settings.ops_probe_timeout_seconds))
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
        _, latency_ms = _measure_probe(lambda: _tcp_probe(host, port, settings.ops_probe_timeout_seconds))
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
    try:
        health, latency_ms = _measure_probe(check_storage_health)
        ok = bool(health.get("ok"))
        if health.get("status") == "disabled":
            ok = True
        return _service_status(
            key="minio",
            name="MinIO",
            ok=ok,
            status=health.get("status", "unknown"),
            latency_ms=latency_ms,
            detail=health.get("detail", ""),
            meta=health,
        )
    except Exception as exc:
        return _service_status(key="minio", name="MinIO", ok=False, status="error", detail=str(exc))


def collect_service_statuses() -> List[dict]:
    return [
        check_database_service(),
        check_redis_service(),
        check_celery_service(),
        check_rag_service(),
        check_minio_service(),
        check_n8n_service(),
    ]


def collect_external_dependency_statuses() -> List[dict]:
    return [
        check_redis_service(),
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


def _to_local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _parse_log_timestamp(line: str) -> Optional[datetime]:
    json_match = JSON_TS_RE.search(line)
    if json_match:
        raw = json_match.group(1).replace("Z", "+00:00")
        if len(raw) >= 5 and raw[-5] in {"+", "-"} and raw[-3] != ":":
            raw = f"{raw[:-2]}:{raw[-2:]}"
        try:
            return _to_local_naive(datetime.fromisoformat(raw))
        except ValueError:
            return None

    celery_match = CELERY_TS_RE.search(line)
    if celery_match:
        try:
            return datetime.strptime(celery_match.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    return None


def _ops_log_cutoff() -> Optional[datetime]:
    lookback_minutes = max(settings.ops_log_lookback_minutes, 0)
    if not lookback_minutes:
        return None
    return datetime.now() - timedelta(minutes=lookback_minutes)


def _ops_log_current_cutoff() -> Optional[datetime]:
    current_minutes = max(int(getattr(settings, "ops_log_current_minutes", 30) or 0), 0)
    if not current_minutes:
        return None
    return datetime.now() - timedelta(minutes=current_minutes)


def _is_traceback_context(message: str) -> bool:
    stripped = message.strip()
    return (
        stripped.startswith("Traceback")
        or stripped.startswith("The above exception")
        or stripped.startswith("During handling")
        or stripped.startswith("File ")
    )


def _classify_log_message(message: str) -> str:
    lowered = message.lower()
    if "quote_request_timeout" in lowered or ("readtimeout" in lowered and "budget-calc" in lowered):
        return "quote_timeout"
    if "quote_job_crashed" in lowered:
        return "quote_job_crashed"
    if "cannot connect to redis" in lowered or "task_queue_worker_unavailable" in lowered:
        return "queue_connectivity"
    if "task_queue_broker_unavailable" in lowered or "connection refused" in lowered:
        return "queue_connectivity"
    if "operationalerror" in lowered or "lost connection" in lowered:
        return "database"
    if "n8n_workflow_failed" in lowered or "request_failed" in lowered:
        return "upstream"
    if "critical" in lowered:
        return "critical"
    return "unknown"


def _log_category_title(category: str) -> str:
    return {
        "quote_timeout": "报价链路超时",
        "quote_job_crashed": "报价任务崩溃",
        "queue_connectivity": "任务队列连接异常",
        "database": "数据库连接异常",
        "upstream": "上游服务异常",
        "critical": "关键异常",
    }.get(category, "未分类异常")


def _event_anchor(message: str) -> str:
    tail = message.split("] ", 1)[1] if "] " in message else message
    known_tokens = (
        "quote_request_timeout",
        "quote_job_crashed",
        "task_queue_worker_unavailable",
        "task_queue_broker_unavailable",
        "n8n_workflow_failed",
        "request_failed",
        "OperationalError",
        "Lost connection",
    )
    for token in known_tokens:
        if token.lower() in tail.lower():
            return token
    return re.sub(r"\s+", " ", tail.strip())[:120] or "log_event"


def _parse_matched_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _acknowledged_events_path() -> Path:
    return LOG_DIR / ACKNOWLEDGED_LOG_EVENTS_FILE


def _load_acknowledged_log_events() -> dict[str, dict]:
    path = _acknowledged_events_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    acknowledged = payload.get("acknowledged", {})
    if not isinstance(acknowledged, dict):
        return {}
    return {str(key): value for key, value in acknowledged.items() if isinstance(value, dict)}


def _write_acknowledged_log_events(acknowledged: dict[str, dict]) -> None:
    path = _acknowledged_events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": _utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "acknowledged": acknowledged,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _is_current_log_event(category: str, matched_at: Optional[datetime], current_cutoff: Optional[datetime]) -> bool:
    if category in RESOLVED_LOG_CATEGORIES:
        return False
    if current_cutoff is None:
        return category not in RESOLVED_LOG_CATEGORIES
    return bool(matched_at and matched_at >= current_cutoff)


def _build_log_events(
    matches: list[dict],
    limit: int,
    acknowledged_events: Optional[dict[str, dict]] = None,
) -> tuple[list[dict], list[dict]]:
    current_cutoff = _ops_log_current_cutoff()
    acknowledged_events = acknowledged_events or {}
    events: list[dict] = []
    last_event_by_file: dict[str, dict] = {}

    for match in matches:
        file_name = match["file"]
        message = match["message"]
        matched_at = match.get("matched_at")
        category = _classify_log_message(message)
        is_context = _is_traceback_context(message)
        previous = last_event_by_file.get(file_name)

        if is_context and previous and previous.get("matched_at") == matched_at:
            event = previous
        else:
            event = {
                "file": file_name,
                "line": match["line"],
                "first_line": match["line"],
                "last_line": match["line"],
                "message": message,
                "matched_at": matched_at,
                "modified_at": match.get("modified_at"),
                "match_count": 0,
                "category": category,
                "category_title": _log_category_title(category),
                "fingerprint": f"{file_name}|{matched_at or match['line']}|{_event_anchor(message)}",
                "raw_messages": [],
                "_sort_at": _parse_matched_at(matched_at),
            }
            events.append(event)
            last_event_by_file[file_name] = event

        event["match_count"] += 1
        event["last_line"] = max(event["last_line"], match["line"])
        event["raw_messages"].append(message)
        if event["category"] == "unknown" and category != "unknown":
            event["category"] = category
            event["category_title"] = _log_category_title(category)
        if not _is_traceback_context(message) and _is_traceback_context(event["message"]):
            event["message"] = message

    for event in events:
        matched_dt = event.get("_sort_at")
        is_current = _is_current_log_event(event["category"], matched_dt, current_cutoff)
        acknowledged = acknowledged_events.get(event["fingerprint"])
        event["acknowledged"] = bool(acknowledged)
        if acknowledged:
            event["acknowledged_at"] = acknowledged.get("acknowledged_at")
            event["acknowledged_by"] = acknowledged.get("acknowledged_by")
        event["status"] = "current" if is_current else "historical"
        event["is_current"] = is_current
        if acknowledged and is_current:
            event["status"] = "acknowledged"
            event["is_current"] = False
        if event["category"] == "quote_timeout":
            event["resolution"] = "报价任务已按超时失败归档；当前是否可用以服务探活和任务状态为准"
        elif acknowledged and is_current:
            event["resolution"] = "已标记已读，保留在历史记录中，当前不触发钉钉告警"
        elif is_current:
            event["resolution"] = "当前窗口内新出现，建议关注"
        else:
            event["resolution"] = "历史窗口内保留，当前不直接触发告警"

    sorted_events = sorted(
        events,
        key=lambda item: (item.get("_sort_at") or datetime.min, item.get("last_line", 0)),
        reverse=True,
    )
    for event in sorted_events:
        event.pop("_sort_at", None)

    return sorted_events[:limit], sorted_events


def collect_error_logs(limit: int = 50) -> dict:
    if not LOG_DIR.exists():
        return {
            "log_dir": str(LOG_DIR),
            "total_matches": 0,
            "total_events": 0,
            "current_event_count": 0,
            "historical_event_count": 0,
            "acknowledged_event_count": 0,
            "items": [],
            "raw_items": [],
        }

    log_files = sorted(
        [path for path in LOG_DIR.glob("*.log") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: settings.ops_log_max_files]

    matches = []
    cutoff = _ops_log_cutoff()
    for path in log_files:
        base_line_no, lines = _tail_lines(path, settings.ops_log_scan_lines)
        last_seen_at = None
        for offset, line in enumerate(lines):
            parsed_at = _parse_log_timestamp(line)
            if parsed_at:
                last_seen_at = parsed_at
            if not any(keyword in line for keyword in ERROR_LOG_KEYWORDS):
                continue
            if cutoff and last_seen_at and last_seen_at < cutoff:
                continue
            matches.append(
                {
                    "file": path.name,
                    "line": base_line_no + offset,
                    "message": line.strip()[:800],
                    "matched_at": last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if last_seen_at else None,
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

    acknowledged_events = _load_acknowledged_log_events()
    event_items, all_events = _build_log_events(matches, limit, acknowledged_events)
    current_event_count = sum(1 for item in all_events if item.get("is_current"))
    acknowledged_event_count = sum(1 for item in all_events if item.get("acknowledged"))

    return {
        "log_dir": str(LOG_DIR),
        "total_matches": len(matches),
        "total_events": len(all_events),
        "current_event_count": current_event_count,
        "historical_event_count": len(all_events) - current_event_count,
        "acknowledged_event_count": acknowledged_event_count,
        "items": event_items,
        "raw_items": matches[-limit:][::-1],
    }


def acknowledge_error_logs(
    *,
    fingerprints: Optional[List[str]] = None,
    username: str = "system",
    limit: int = 200,
) -> dict:
    logs = collect_error_logs(limit=limit)
    target_fingerprints = set(fingerprints or [])
    items = []
    for item in logs.get("items", []):
        if item.get("status") != "current":
            continue
        if target_fingerprints and item.get("fingerprint") not in target_fingerprints:
            continue
        items.append(item)

    acknowledged = _load_acknowledged_log_events()
    acknowledged_at = _utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for item in items:
        fingerprint = item.get("fingerprint")
        if not fingerprint:
            continue
        acknowledged[fingerprint] = {
            "acknowledged_at": acknowledged_at,
            "acknowledged_by": username or "system",
            "file": item.get("file"),
            "line": item.get("line"),
            "matched_at": item.get("matched_at"),
            "category": item.get("category"),
            "message": item.get("message"),
        }

    if items:
        _write_acknowledged_log_events(acknowledged)

    updated_logs = collect_error_logs(limit=limit)
    return {
        "acknowledged_count": len(items),
        "current_event_count": updated_logs.get("current_event_count", 0),
        "acknowledged_event_count": updated_logs.get("acknowledged_event_count", 0),
        "logs": updated_logs,
    }


def build_alerts(services: list[dict], jobs: dict, logs: dict) -> list[dict]:
    alerts = []
    down_services = [service["name"] for service in services if not service.get("ok")]
    if down_services:
        alerts.append(
            {
                "kind": "service_down",
                "level": "critical",
                "title": "基础服务异常",
                "message": "、".join(down_services) + " 探活失败，请检查网络、容器或启动脚本。",
            }
        )
    if jobs.get("stuck_count", 0) > 0:
        alerts.append(
            {
                "kind": "stuck_jobs",
                "level": "warning",
                "title": "报价任务可能卡住",
                "message": f"{jobs['stuck_count']} 个任务超过 {jobs['stuck_minutes']} 分钟未更新。",
            }
        )
    current_log_events = logs.get("current_event_count", 0)
    if current_log_events > 0:
        alerts.append(
            {
                "kind": "current_log_events",
                "level": "warning",
                "title": "发现当前异常日志",
                "message": f"最近日志中聚合到 {current_log_events} 起当前异常事件，请查看日志详情。",
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


_logger = logging.getLogger(__name__)

# 内存状态：去重缓存（title → 上次发送时间）+ 限流滑动窗口
_dedup_cache: Dict[str, datetime] = {}
_rate_window: deque = deque()


def _should_send_alert(alert: dict) -> bool:
    now = _utcnow()
    title = alert.get("title", "")

    last_sent = _dedup_cache.get(title)
    if last_sent and (now - last_sent).total_seconds() < settings.alert_dedup_minutes * 60:
        return False

    window_seconds = settings.alert_rate_limit_window_minutes * 60
    while _rate_window and (now - _rate_window[0]).total_seconds() > window_seconds:
        _rate_window.popleft()
    if len(_rate_window) >= settings.alert_rate_limit_count:
        return False

    return True


def _mark_sent(alert: dict) -> None:
    title = alert.get("title", "")
    now = _utcnow()
    _dedup_cache[title] = now
    _rate_window.append(now)


def send_dingtalk_alerts(alerts: List[dict]) -> None:
    """过滤并推送告警到钉钉。webhook 未配置或无需发送时直接返回。"""
    if not settings.alert_dingtalk_webhook:
        return

    to_send = [a for a in alerts if _should_send_alert(a)]
    if not to_send:
        return

    has_critical = any(a.get("level") == "critical" for a in to_send)
    lines = ["## AI 中台运维告警\n"]
    for alert in to_send:
        icon = "🔴" if alert.get("level") == "critical" else "🟡"
        lines.append(f"{icon} **{alert['title']}**\n\n> {alert['message']}\n")

    payload: dict = {
        "msgtype": "markdown",
        "markdown": {
            "title": "AI 中台运维告警",
            "text": "\n".join(lines),
        },
    }
    if has_critical:
        payload["at"] = {"isAtAll": True}

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(settings.alert_dingtalk_webhook, json=payload)
        response.raise_for_status()
        result = response.json()
        if result.get("errcode", 0) != 0:
            _logger.warning("dingtalk_alert_rejected", extra={"response": result})
            return
        for alert in to_send:
            _mark_sent(alert)
        _logger.info("dingtalk_alerts_sent", extra={"count": len(to_send)})
    except Exception:
        _logger.warning("dingtalk_alert_send_failed", exc_info=True)
