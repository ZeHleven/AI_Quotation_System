from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.agent import AgentSchedulerRun
from app.services.agent_daily_review import build_daily_quote_review_summary, coerce_review_date, run_daily_quote_review
from app.services.agent_daily_review import build_daily_quote_review_closure_stats
from app.services.agent_quote_review import json_dumps, json_loads


logger = logging.getLogger(__name__)

SCHEDULER_KEY_QUOTE_REVIEW_DAILY = "quote_review_daily"
SYSTEM_TRIGGER = "system_scheduler"
_scheduler_lock = asyncio.Lock()


def scheduler_enabled() -> bool:
    return bool(settings.feature_agent_assistants and settings.feature_agent_daily_review)


def serialize_agent_scheduler_run(row: AgentSchedulerRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "scheduler_key": row.scheduler_key,
        "run_date": row.run_date.isoformat() if row.run_date else None,
        "status": row.status,
        "scheduled_at": _format_dt(row.scheduled_at),
        "started_at": _format_dt(row.started_at),
        "finished_at": _format_dt(row.finished_at),
        "triggered_by": row.triggered_by,
        "candidate_count": row.candidate_count,
        "created_run_count": row.created_run_count,
        "skipped_duplicate_count": row.skipped_duplicate_count,
        "skipped_invalid_count": row.skipped_invalid_count,
        "failed_count": row.failed_count,
        "result": json_loads(row.result_json, None),
        "error_message": row.error_message,
        "created_at": _format_dt(row.created_at),
        "updated_at": _format_dt(row.updated_at),
    }


def get_quote_review_scheduler_status(
    db: Session,
    *,
    review_date: date | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _coerce_now(now)
    run_day = coerce_review_date(review_date) if review_date else current.date()
    scheduled_at = scheduled_datetime(run_day)
    window_end_at = scheduled_at + timedelta(minutes=_catchup_minutes())
    row = find_scheduler_run(db, run_day)
    status = row.status if row else _virtual_status(current, scheduled_at, window_end_at)
    return {
        "scheduler_key": SCHEDULER_KEY_QUOTE_REVIEW_DAILY,
        "review_date": run_day.isoformat(),
        "timezone": settings.agent_daily_review_timezone,
        "run_time": settings.agent_daily_review_run_time,
        "poll_seconds": _poll_seconds(),
        "catchup_minutes": _catchup_minutes(),
        "enabled": scheduler_enabled(),
        "status": status,
        "scheduled_at": _format_dt(_drop_tz(scheduled_at)),
        "window_end_at": _format_dt(_drop_tz(window_end_at)),
        "current_time": _format_dt(_drop_tz(current)),
        "run": serialize_agent_scheduler_run(row),
        "next_action": _next_action(status, row),
    }


def list_quote_review_scheduler_history(
    db: Session,
    *,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    current_day = _coerce_now().date()
    start_day = coerce_review_date(date_from) if date_from else current_day - timedelta(days=29)
    end_day = coerce_review_date(date_to) if date_to else current_day
    if start_day > end_day:
        raise ValueError("date_from must be earlier than or equal to date_to")

    query = db.query(AgentSchedulerRun).filter(
        AgentSchedulerRun.scheduler_key == SCHEDULER_KEY_QUOTE_REVIEW_DAILY,
        AgentSchedulerRun.triggered_by == SYSTEM_TRIGGER,
        AgentSchedulerRun.run_date >= start_day,
        AgentSchedulerRun.run_date <= end_day,
    )
    if status and status != "all":
        query = query.filter(AgentSchedulerRun.status == status)

    total = query.count()
    rows = (
        query.order_by(AgentSchedulerRun.run_date.desc(), AgentSchedulerRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = serialize_agent_scheduler_run(row)
        summary = build_daily_quote_review_summary(db, review_date=row.run_date)
        closure = build_daily_quote_review_closure_stats(db, review_date=row.run_date)
        item["daily_summary"] = {
            "candidate_count": summary["candidate_count"],
            "run_count": summary["run_count"],
            "completed_run_count": summary["completed_run_count"],
            "failed_run_count": summary["failed_run_count"],
            "high_risk_run_count": summary["high_risk_run_count"],
            "audit_record_count": summary.get("audit_record_count", 0),
            "audit_high_risk_record_count": summary.get("audit_high_risk_record_count", 0),
            "audit_manual_modified_count": summary.get("audit_manual_modified_count", 0),
            "audit_market_search_result_count": summary.get("audit_market_search_result_count", 0),
            "audit_market_search_covered_line_count": summary.get("audit_market_search_covered_line_count", 0),
            "audit_risk_type_counts": summary.get("audit_risk_type_counts", {}),
            "open_suggestion_count": summary["open_suggestion_count"],
            "open_estimated_saving_amount": summary["open_estimated_saving_amount"],
            "handled_count": closure["handled_count"],
            "overdue_count": closure["overdue_count"],
            "closure_rate": closure["closure_rate"],
            "confirmed_saving_amount": closure["confirmed_saving_amount"],
        }
        item["next_action"] = _next_action(row.status, row)
        item["manual_rescan_available"] = False
        items.append(item)

    meta = {
        "scheduler_key": SCHEDULER_KEY_QUOTE_REVIEW_DAILY,
        "date_from": start_day.isoformat(),
        "date_to": end_day.isoformat(),
        "status": status or "all",
        "timezone": settings.agent_daily_review_timezone,
        "run_time": settings.agent_daily_review_run_time,
    }
    return items, total, meta


def build_quote_review_todo_summary(
    db: Session,
    *,
    review_date: date | str | None = None,
) -> dict[str, Any]:
    day = coerce_review_date(review_date)
    daily_summary = build_daily_quote_review_summary(db, review_date=day)
    closure = build_daily_quote_review_closure_stats(db, review_date=day)
    scheduler_status = get_quote_review_scheduler_status(db, review_date=day)
    return {
        "review_date": day.isoformat(),
        "timezone": settings.agent_daily_review_timezone,
        "run_time": settings.agent_daily_review_run_time,
        "status": "clear",
        "message": "每日报价审核仅保留已下发报价审计记录，不再生成待办或二次确认动作。",
        "primary_action": "none",
        "todo_count": 0,
        "urgent_count": 0,
        "todos": [],
        "metrics": {
            "candidate_count": daily_summary.get("candidate_count", 0),
            "run_count": daily_summary.get("run_count", 0),
            "high_risk_run_count": daily_summary.get("high_risk_run_count", 0),
            "audit_record_count": daily_summary.get("audit_record_count", 0),
            "audit_high_risk_record_count": daily_summary.get("audit_high_risk_record_count", 0),
            "audit_manual_modified_count": daily_summary.get("audit_manual_modified_count", 0),
            "audit_market_search_result_count": daily_summary.get("audit_market_search_result_count", 0),
            "audit_market_search_covered_line_count": daily_summary.get("audit_market_search_covered_line_count", 0),
            "open_suggestion_count": 0,
            "pending_review_count": 0,
            "approved_count": 0,
            "draft_generated_count": 0,
            "open_estimated_saving_amount": 0,
            "handled_count": closure["handled_count"],
            "overdue_count": closure["overdue_count"],
            "closure_rate": closure["closure_rate"],
            "confirmed_saving_amount": closure["confirmed_saving_amount"],
            "scheduler_status": scheduler_status.get("status"),
        },
        "scheduler": {
            "status": scheduler_status.get("status"),
            "next_action": scheduler_status.get("next_action"),
            "scheduled_at": scheduler_status.get("scheduled_at"),
            "run": scheduler_status.get("run"),
        },
    }


def run_due_quote_review_scheduler_once(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _coerce_now(now)
    if not scheduler_enabled():
        return get_quote_review_scheduler_status(db, now=current) | {
            "executed": False,
            "skip_reason": "feature_disabled",
        }

    run_day = current.date()
    scheduled_at = scheduled_datetime(run_day)
    window_end_at = scheduled_at + timedelta(minutes=_catchup_minutes())
    row = find_scheduler_run(db, run_day)
    if row:
        return get_quote_review_scheduler_status(db, review_date=run_day, now=current) | {
            "executed": False,
            "skip_reason": f"already_{row.status}",
        }
    if current < scheduled_at:
        return get_quote_review_scheduler_status(db, review_date=run_day, now=current) | {
            "executed": False,
            "skip_reason": "not_due",
        }
    if current > window_end_at:
        missed = _create_scheduler_row(
            db,
            run_day=run_day,
            scheduled_at=scheduled_at,
            status="missed",
            started_at=None,
            finished_at=current,
            error_message="MISSED_CATCHUP_WINDOW",
        )
        return get_quote_review_scheduler_status(db, review_date=run_day, now=current) | {
            "executed": False,
            "skip_reason": "missed_catchup_window",
            "run": serialize_agent_scheduler_run(missed),
        }

    run = _create_scheduler_row(
        db,
        run_day=run_day,
        scheduled_at=scheduled_at,
        status="running",
        started_at=current,
    )
    if run.status != "running":
        return get_quote_review_scheduler_status(db, review_date=run_day, now=current) | {
            "executed": False,
            "skip_reason": f"already_{run.status}",
        }

    try:
        result = run_daily_quote_review(
            db,
            review_date=run_day,
            actor=SYSTEM_TRIGGER,
            dry_run=False,
            limit=settings.agent_daily_review_max_jobs,
        )
        run.status = "success"
        run.candidate_count = int(result.get("candidate_count") or 0)
        run.created_run_count = int(result.get("created_run_count") or 0)
        run.skipped_duplicate_count = int(result.get("skipped_duplicate_count") or 0)
        run.skipped_invalid_count = int(result.get("skipped_invalid_count") or 0)
        run.failed_count = int(result.get("failed_count") or 0)
        run.result_json = json_dumps(result)
        run.error_message = None
        run.finished_at = _drop_tz(_coerce_now())
        db.commit()
        db.refresh(run)
        logger.info(
            "agent_daily_scheduler_success",
            extra={
                "run_date": run_day.isoformat(),
                "candidate_count": run.candidate_count,
                "created_run_count": run.created_run_count,
                "failed_count": run.failed_count,
            },
        )
    except Exception as exc:
        db.rollback()
        run = find_scheduler_run(db, run_day) or run
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = _drop_tz(_coerce_now())
        db.commit()
        db.refresh(run)
        logger.exception("agent_daily_scheduler_failed", extra={"run_date": run_day.isoformat()})

    return get_quote_review_scheduler_status(db, review_date=run_day) | {
        "executed": run.status == "success",
        "run": serialize_agent_scheduler_run(run),
    }


async def quote_review_daily_scheduler_loop() -> None:
    logger.info(
        "agent_daily_scheduler_loop_started",
        extra={
            "enabled": scheduler_enabled(),
            "run_time": settings.agent_daily_review_run_time,
            "timezone": settings.agent_daily_review_timezone,
            "poll_seconds": _poll_seconds(),
        },
    )
    while True:
        try:
            if scheduler_enabled():
                await _run_scheduler_tick()
            await asyncio.sleep(_poll_seconds())
        except asyncio.CancelledError:
            logger.info("agent_daily_scheduler_loop_cancelled")
            break
        except Exception:
            logger.exception("agent_daily_scheduler_loop_error")
            await asyncio.sleep(_poll_seconds())


async def _run_scheduler_tick() -> None:
    if _scheduler_lock.locked():
        return
    async with _scheduler_lock:
        await asyncio.to_thread(_run_scheduler_tick_sync)


def _run_scheduler_tick_sync() -> None:
    db = SessionLocal()
    try:
        run_due_quote_review_scheduler_once(db)
    finally:
        db.close()


def find_scheduler_run(db: Session, run_day: date) -> AgentSchedulerRun | None:
    return (
        db.query(AgentSchedulerRun)
        .filter(
            AgentSchedulerRun.scheduler_key == SCHEDULER_KEY_QUOTE_REVIEW_DAILY,
            AgentSchedulerRun.run_date == run_day,
            AgentSchedulerRun.triggered_by == SYSTEM_TRIGGER,
        )
        .order_by(AgentSchedulerRun.id.desc())
        .first()
    )


def scheduled_datetime(run_day: date) -> datetime:
    return datetime.combine(run_day, _parse_run_time(), tzinfo=_scheduler_timezone())


def _create_scheduler_row(
    db: Session,
    *,
    run_day: date,
    scheduled_at: datetime,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error_message: str | None = None,
) -> AgentSchedulerRun:
    row = AgentSchedulerRun(
        scheduler_key=SCHEDULER_KEY_QUOTE_REVIEW_DAILY,
        run_date=run_day,
        status=status,
        scheduled_at=_drop_tz(scheduled_at),
        started_at=_drop_tz(started_at),
        finished_at=_drop_tz(finished_at),
        triggered_by=SYSTEM_TRIGGER,
        error_message=error_message,
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        existing = find_scheduler_run(db, run_day)
        if existing:
            return existing
        raise


def _virtual_status(current: datetime, scheduled_at: datetime, window_end_at: datetime) -> str:
    if not scheduler_enabled():
        return "disabled"
    if current < scheduled_at:
        return "not_due"
    if current > window_end_at:
        return "missed"
    return "pending"


def _next_action(status: str, row: AgentSchedulerRun | None) -> str:
    if status == "disabled":
        return "enable_feature_flags"
    if status == "not_due":
        return "wait_for_run_time"
    if status == "pending":
        return "scheduler_will_run"
    if status == "running":
        return "wait_for_finish"
    if status == "success":
        return "check_result"
    if status == "failed":
        return "check_result"
    if status == "missed":
        return "check_result"
    if status == "skipped":
        return "check_result"
    if row:
        return "check_result"
    return "unknown"


def _parse_run_time() -> time:
    raw = (settings.agent_daily_review_run_time or "18:30").strip()
    try:
        parts = raw.split(":")
        if len(parts) != 2:
            raise ValueError
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour=hour, minute=minute)
    except Exception:
        return time(hour=18, minute=30)


def _scheduler_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.agent_daily_review_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _coerce_now(value: datetime | None = None) -> datetime:
    tz = _scheduler_timezone()
    if value is None:
        return datetime.now(tz)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _drop_tz(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(_scheduler_timezone()).replace(tzinfo=None)


def _poll_seconds() -> int:
    return max(5, int(settings.agent_daily_review_poll_seconds or 60))


def _catchup_minutes() -> int:
    return max(0, int(settings.agent_daily_review_catchup_minutes or 0))


def _format_dt(value: Any) -> str | None:
    if not value:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
