from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.quote_feedback import QuoteFeedback
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob


CN_TZ = ZoneInfo("Asia/Shanghai")
VALID_RANGES = {"today", "week", "month", "last_30_days"}
LOW_SAMPLE_THRESHOLD = 5


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(CN_TZ).replace(tzinfo=None)


def _range_bounds(range_name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    range_name = range_name if range_name in VALID_RANGES else "last_30_days"
    now = now or _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_name == "today":
        start = today_start
    elif range_name == "week":
        start = today_start - timedelta(days=today_start.weekday())
    elif range_name == "month":
        start = today_start.replace(day=1)
    else:
        start = today_start - timedelta(days=29)
    return start, now


def _avg(values: list[float | int]) -> int | None:
    values = [value for value in values if value is not None and value >= 0]
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    start_local = _to_local(start)
    end_local = _to_local(end)
    if not start_local or not end_local:
        return None
    delta = (end_local - start_local).total_seconds() * 1000
    if delta < 0:
        return None
    return int(round(delta))


def _effective_finished_at(job: QuoteJob) -> datetime | None:
    """Handle legacy rows where finished_at was stored as UTC while created_at was local time."""
    finished_at = job.finished_at
    if not job.created_at or not finished_at:
        return finished_at
    created_local = _to_local(job.created_at)
    finished_local = _to_local(finished_at)
    if not created_local or not finished_local or finished_local >= created_local:
        return finished_at
    shifted_finished = finished_at + timedelta(hours=8)
    shifted_local = _to_local(shifted_finished)
    if shifted_local and shifted_local >= created_local:
        return shifted_finished
    return finished_at


def _first_history_by_job(histories: list[QuoteHistory]) -> dict[str, QuoteHistory]:
    result: dict[str, QuoteHistory] = {}
    sort_floor = datetime.min.replace(tzinfo=CN_TZ)
    for history in sorted(histories, key=lambda item: _to_local(item.created_at) or sort_floor):
        if history.quote_job_id and history.quote_job_id not in result:
            result[history.quote_job_id] = history
    return result


def _jobs_by_day(jobs: list[QuoteJob]) -> dict[str, list[QuoteJob]]:
    groups: dict[str, list[QuoteJob]] = defaultdict(list)
    for job in jobs:
        created_at = _to_local(job.created_at)
        if created_at:
            groups[created_at.date().isoformat()].append(job)
    return groups


def build_quote_speed_dashboard(db: Session, *, range_name: str = "last_30_days") -> dict:
    range_name = range_name if range_name in VALID_RANGES else "last_30_days"
    start, end = _range_bounds(range_name)
    jobs = (
        db.query(QuoteJob)
        .filter(QuoteJob.created_at >= _db_time(start), QuoteJob.created_at <= _db_time(end))
        .order_by(QuoteJob.created_at.asc(), QuoteJob.id.asc())
        .all()
    )
    job_ids = [job.job_id for job in jobs]
    job_map = {job.job_id: job for job in jobs}

    histories: list[QuoteHistory] = []
    feedback_rows: list[QuoteFeedback] = []
    if job_ids:
        histories = db.query(QuoteHistory).filter(QuoteHistory.quote_job_id.in_(job_ids)).all()
        feedback_rows = (
            db.query(QuoteFeedback)
            .filter(QuoteFeedback.quote_job_id.in_(job_ids), QuoteFeedback.status == "confirmed")
            .all()
        )

    first_history = _first_history_by_job(histories)
    ai_durations = [int(job.duration_ms) for job in jobs if job.status == "succeeded" and job.duration_ms is not None]
    manual_durations = []
    total_durations = []
    for job_id, history in first_history.items():
        job = job_map.get(job_id)
        if not job:
            continue
        manual_duration = _duration_ms(_effective_finished_at(job), history.created_at)
        total_duration = _duration_ms(job.created_at, history.created_at)
        if manual_duration is not None:
            manual_durations.append(manual_duration)
        if total_duration is not None:
            total_durations.append(total_duration)

    feedback_by_job = {feedback.quote_job_id: feedback for feedback in feedback_rows if feedback.quote_job_id}
    modified_count = sum(1 for feedback in feedback_by_job.values() if feedback.was_modified)
    status_counts = Counter(job.status for job in jobs)

    by_day = _jobs_by_day(jobs)
    daily_trends = []
    current_day = start.date()
    while current_day <= end.date():
        day_key = current_day.isoformat()
        day_jobs = by_day.get(day_key, [])
        day_ids = {job.job_id for job in day_jobs}
        day_histories = {job_id: first_history[job_id] for job_id in day_ids if job_id in first_history}
        day_feedback = [feedback_by_job[job_id] for job_id in day_ids if job_id in feedback_by_job]
        day_ai_durations = [
            int(job.duration_ms)
            for job in day_jobs
            if job.status == "succeeded" and job.duration_ms is not None
        ]
        day_total_durations = []
        for job_id, history in day_histories.items():
            total_duration = _duration_ms(job_map[job_id].created_at, history.created_at)
            if total_duration is not None:
                day_total_durations.append(total_duration)
        daily_trends.append(
            {
                "date": day_key,
                "sample_count": len(day_jobs),
                "confirmed_count": len(day_histories),
                "ai_duration_avg_ms": _avg(day_ai_durations),
                "total_delivery_duration_avg_ms": _avg(day_total_durations),
                "modified_rate": _ratio(sum(1 for feedback in day_feedback if feedback.was_modified), len(day_feedback)),
            }
        )
        current_day += timedelta(days=1)

    return {
        "timezone": "Asia/Shanghai",
        "range": range_name,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "sample_count": len(jobs),
        "completed_count": status_counts.get("succeeded", 0),
        "confirmed_count": len(first_history),
        "feedback_sample_count": len(feedback_by_job),
        "modified_count": modified_count,
        "ai_duration_avg_ms": _avg(ai_durations),
        "manual_confirm_duration_avg_ms": _avg(manual_durations),
        "total_delivery_duration_avg_ms": _avg(total_durations),
        "modified_rate": _ratio(modified_count, len(feedback_by_job)),
        "daily_trends": daily_trends,
        "status_distribution": [
            {"status": status, "count": count}
            for status, count in sorted(status_counts.items())
        ],
        "empty_state": len(jobs) == 0,
        "low_sample_warning": 0 < len(jobs) < LOW_SAMPLE_THRESHOLD,
    }
