from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent import AgentRun
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.services.agent_quote_review import (
    QUOTE_REVIEW_AGENT_TYPE,
    create_quote_review_agent_run,
    json_loads,
    serialize_agent_run,
)


DAILY_REVIEW_TRIGGER_SOURCE = "scheduled_daily"
DAILY_REVIEW_TRIGGER_REF_TYPE = "quote_history"
def run_daily_quote_review(
    db: Session,
    *,
    review_date: date | str | None = None,
    actor: str = "system_scheduler",
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    day = coerce_review_date(review_date)
    max_jobs = limit or settings.agent_daily_review_max_jobs
    candidates = _daily_quote_histories(db, day, max_jobs)
    result: dict[str, Any] = {
        "review_date": day.isoformat(),
        "trigger_source": DAILY_REVIEW_TRIGGER_SOURCE,
        "trigger_ref_type": DAILY_REVIEW_TRIGGER_REF_TYPE,
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "created_run_count": 0,
        "skipped_duplicate_count": 0,
        "skipped_invalid_count": 0,
        "failed_count": 0,
        "runs": [],
        "skipped": [],
        "failures": [],
    }

    for history in candidates:
        history_ref = str(history.id)
        existing = find_daily_review_run(db, history.id)
        if existing:
            result["skipped_duplicate_count"] += 1
            result["skipped"].append(
                {
                    "quote_history_id": history.id,
                    "quote_job_id": history.quote_job_id,
                    "reason": "duplicate",
                    "existing_run_id": existing.run_id,
                }
            )
            continue

        job = db.query(QuoteJob).filter(QuoteJob.job_id == history.quote_job_id).first()
        if not job or job.status not in {"succeeded", "completed"}:
            result["skipped_invalid_count"] += 1
            result["skipped"].append(
                {
                    "quote_history_id": history.id,
                    "quote_job_id": history.quote_job_id,
                    "reason": "quote_job_not_ready",
                    "status": job.status if job else None,
                }
            )
            continue

        if dry_run:
            result["runs"].append(
                {
                    "quote_history_id": history.id,
                    "quote_job_id": history.quote_job_id,
                    "status": "candidate",
                }
            )
            continue

        try:
            run = create_quote_review_agent_run(
                db,
                job=job,
                created_by=actor,
                trace_id=history.trace_id or job.trace_id,
                trigger_source=DAILY_REVIEW_TRIGGER_SOURCE,
                trigger_ref_type=DAILY_REVIEW_TRIGGER_REF_TYPE,
                trigger_ref_id=history_ref,
                audit_only=True,
                audit_date=history.created_at.date() if history.created_at else day,
            )
        except Exception as exc:
            db.rollback()
            result["failed_count"] += 1
            result["failures"].append(
                {
                    "quote_history_id": history.id,
                    "quote_job_id": history.quote_job_id,
                    "reason": str(exc),
                }
            )
            continue

        result["created_run_count"] += 1
        run_data = serialize_agent_run(run)
        run_data["quote_history_id"] = history.id
        result["runs"].append(run_data)

    return result


def build_daily_quote_review_summary(db: Session, *, review_date: date | str | None = None) -> dict[str, Any]:
    day = coerce_review_date(review_date)
    start_at, end_at = day_bounds(day)
    candidate_refs = _daily_quote_history_refs(db, start_at, end_at)
    candidate_count = len(candidate_refs)
    run_query = db.query(AgentRun).filter(
        AgentRun.agent_type == QUOTE_REVIEW_AGENT_TYPE,
        AgentRun.trigger_source == DAILY_REVIEW_TRIGGER_SOURCE,
        AgentRun.trigger_ref_type == DAILY_REVIEW_TRIGGER_REF_TYPE,
    )
    if candidate_refs:
        run_query = run_query.filter(AgentRun.trigger_ref_id.in_(candidate_refs))
    else:
        run_query = run_query.filter(AgentRun.id < 0)
    runs = run_query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).all()
    run_ids = [row.run_id for row in runs]
    risk_counts: dict[str, int] = {}
    audit_record_count = 0
    audit_high_risk_record_count = 0
    audit_manual_modified_count = 0
    audit_market_search_result_count = 0
    audit_market_search_covered_line_count = 0
    audit_risk_type_counts: dict[str, int] = {}
    for run in runs:
        key = run.risk_level or "unknown"
        risk_counts[key] = risk_counts.get(key, 0) + 1
        output = run.output_json
        payload = {}
        if output:
            try:
                payload = json_loads(output, {})
            except Exception:
                payload = {}
        audit_summary = payload.get("audit_summary") if isinstance(payload, dict) else {}
        audit_summary = audit_summary if isinstance(audit_summary, dict) else {}
        audit_record_count += int(audit_summary.get("audit_record_count") or 0)
        audit_high_risk_record_count += int(audit_summary.get("high_risk_count") or 0)
        audit_manual_modified_count += int(audit_summary.get("manual_modified_count") or 0)
        audit_market_search_result_count += int(audit_summary.get("market_search_result_count") or 0)
        audit_market_search_covered_line_count += int(audit_summary.get("market_search_covered_line_count") or 0)
        for risk_type, count in (audit_summary.get("risk_type_counts") or {}).items():
            audit_risk_type_counts[str(risk_type)] = audit_risk_type_counts.get(str(risk_type), 0) + int(count or 0)

    return {
        "review_date": day.isoformat(),
        "timezone": settings.agent_daily_review_timezone,
        "run_time": settings.agent_daily_review_run_time,
        "trigger_source": DAILY_REVIEW_TRIGGER_SOURCE,
        "candidate_count": candidate_count,
        "run_count": len(runs),
        "completed_run_count": sum(1 for row in runs if row.status == "completed"),
        "failed_run_count": sum(1 for row in runs if row.status == "failed"),
        "high_risk_run_count": risk_counts.get("high", 0),
        "risk_counts": risk_counts,
        "audit_record_count": audit_record_count,
        "audit_high_risk_record_count": audit_high_risk_record_count,
        "audit_manual_modified_count": audit_manual_modified_count,
        "audit_market_search_result_count": audit_market_search_result_count,
        "audit_market_search_covered_line_count": audit_market_search_covered_line_count,
        "audit_risk_type_counts": audit_risk_type_counts,
        "suggestion_count": 0,
        "open_suggestion_count": 0,
        "pending_review_count": 0,
        "approved_count": 0,
        "draft_generated_count": 0,
        "final_confirmed_count": 0,
        "human_modified_count": 0,
        "status_counts": {},
        "priority_counts": {},
        "type_counts": {},
        "estimated_total_saving_amount": 0,
        "open_estimated_saving_amount": 0,
        "latest_runs": [serialize_agent_run(row, include_output=False) for row in runs[:10]],
    }


def build_daily_quote_review_closure_stats(
    db: Session,
    *,
    review_date: date | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    day = coerce_review_date(review_date)
    runs = _daily_review_runs(db, day)
    due_at = datetime.combine(day + timedelta(days=1), time.min)
    current = _coerce_local_now(now)

    high_risk_open_count = sum(1 for row in runs if row.risk_level == "high")
    return {
        "review_date": day.isoformat(),
        "timezone": settings.agent_daily_review_timezone,
        "due_at": _format_local_dt(due_at),
        "is_overdue": current >= due_at,
        "run_count": len(runs),
        "high_risk_run_count": high_risk_open_count,
        "suggestion_count": 0,
        "handled_count": 0,
        "open_count": 0,
        "overdue_count": 0,
        "rejected_count": 0,
        "final_confirmed_count": 0,
        "human_modified_count": 0,
        "closure_rate": 1.0,
        "estimated_open_saving_amount": 0,
        "confirmed_saving_amount": 0,
        "handled_estimated_saving_amount": 0,
    }


def build_quote_review_closure_summary(
    db: Session,
    *,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_day = coerce_review_date(now) if now else datetime.now(_daily_timezone()).date()
    start_day = coerce_review_date(date_from) if date_from else current_day - timedelta(days=6)
    end_day = coerce_review_date(date_to) if date_to else current_day
    if start_day > end_day:
        raise ValueError("date_from must be earlier than or equal to date_to")
    if (end_day - start_day).days > 60:
        raise ValueError("date range must be 60 days or less")

    daily: list[dict[str, Any]] = []
    day = start_day
    while day <= end_day:
        daily.append(build_daily_quote_review_closure_stats(db, review_date=day, now=now))
        day += timedelta(days=1)

    total_suggestion_count = sum(item["suggestion_count"] for item in daily)
    handled_count = sum(item["handled_count"] for item in daily)
    open_count = sum(item["open_count"] for item in daily)
    overdue_count = sum(item["overdue_count"] for item in daily)
    confirmed_saving = sum(item["confirmed_saving_amount"] for item in daily)
    estimated_open_saving = sum(item["estimated_open_saving_amount"] for item in daily)
    rejected_count = sum(item["rejected_count"] for item in daily)
    human_modified_count = sum(item["human_modified_count"] for item in daily)
    final_confirmed_count = sum(item["final_confirmed_count"] for item in daily)
    high_risk_run_count = sum(item["high_risk_run_count"] for item in daily)
    closure_rate = round(handled_count / total_suggestion_count, 4) if total_suggestion_count else 1.0
    status = "overdue" if overdue_count else ("action_required" if open_count else "clear")
    return {
        "date_from": start_day.isoformat(),
        "date_to": end_day.isoformat(),
        "timezone": settings.agent_daily_review_timezone,
        "status": status,
        "metrics": {
            "suggestion_count": total_suggestion_count,
            "handled_count": handled_count,
            "open_count": open_count,
            "overdue_count": overdue_count,
            "high_risk_run_count": high_risk_run_count,
            "closure_rate": closure_rate,
            "estimated_open_saving_amount": round(estimated_open_saving, 2),
            "confirmed_saving_amount": round(confirmed_saving, 2),
            "rejected_count": rejected_count,
            "human_modified_count": human_modified_count,
            "final_confirmed_count": final_confirmed_count,
        },
        "daily": list(reversed(daily)),
    }


def list_quote_review_suggestions(
    db: Session,
    *,
    review_date: date | str | None = None,
    status_filter: str = "open",
    trigger_source: str | None = DAILY_REVIEW_TRIGGER_SOURCE,
    created_by: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    coerce_review_date(review_date) if review_date else None
    return [], 0


def find_daily_review_run(db: Session, quote_history_id: int) -> AgentRun | None:
    return (
        db.query(AgentRun)
        .filter(
            AgentRun.agent_type == QUOTE_REVIEW_AGENT_TYPE,
            AgentRun.trigger_source == DAILY_REVIEW_TRIGGER_SOURCE,
            AgentRun.trigger_ref_type == DAILY_REVIEW_TRIGGER_REF_TYPE,
            AgentRun.trigger_ref_id == str(quote_history_id),
        )
        .order_by(AgentRun.id.desc())
        .first()
    )


def _daily_review_runs(db: Session, day: date) -> list[AgentRun]:
    start_at, end_at = day_bounds(day)
    candidate_refs = _daily_quote_history_refs(db, start_at, end_at)
    query = db.query(AgentRun).filter(
        AgentRun.agent_type == QUOTE_REVIEW_AGENT_TYPE,
        AgentRun.trigger_source == DAILY_REVIEW_TRIGGER_SOURCE,
        AgentRun.trigger_ref_type == DAILY_REVIEW_TRIGGER_REF_TYPE,
    )
    if candidate_refs:
        query = query.filter(AgentRun.trigger_ref_id.in_(candidate_refs))
    else:
        query = query.filter(AgentRun.id < 0)
    return query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).all()


def coerce_review_date(value: date | str | None) -> date:
    if value is None:
        return datetime.now(_daily_timezone()).date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value).strip())


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    return start, end


def _daily_quote_histories(db: Session, day: date, limit: int) -> list[QuoteHistory]:
    start_at, end_at = day_bounds(day)
    return (
        _daily_quote_histories_query(db, start_at, end_at)
        .order_by(QuoteHistory.created_at.asc(), QuoteHistory.id.asc())
        .limit(limit)
        .all()
    )


def _daily_quote_histories_query(db: Session, start_at: datetime, end_at: datetime):
    return db.query(QuoteHistory).filter(
        QuoteHistory.pushed_to_dingtalk.is_(True),
        QuoteHistory.quote_job_id.isnot(None),
        QuoteHistory.created_at >= start_at,
        QuoteHistory.created_at < end_at,
    )


def _daily_quote_history_refs(db: Session, start_at: datetime, end_at: datetime) -> list[str]:
    rows = _daily_quote_histories_query(db, start_at, end_at).with_entities(QuoteHistory.id).all()
    return [str(row[0]) for row in rows]


def _daily_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.agent_daily_review_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _coerce_local_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(_daily_timezone()).replace(tzinfo=None)
    if value.tzinfo is None:
        return value
    return value.astimezone(_daily_timezone()).replace(tzinfo=None)


def _format_local_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")
