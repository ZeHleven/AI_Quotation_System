from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.execution_task import ExecutionTask
from app.models.user import User
from app.services.execution_tasks import CN_TZ, _to_local_naive, is_overdue
from app.services.quote_dashboard import LOW_SAMPLE_THRESHOLD, VALID_RANGES, _db_time, _range_bounds


def _avg(values: list[int]) -> int | None:
    values = [value for value in values if value is not None and value >= 0]
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    start_local = _to_local_naive(start)
    end_local = _to_local_naive(end)
    if not start_local or not end_local:
        return None
    delta = (end_local - start_local).total_seconds() * 1000
    if delta < 0:
        return None
    return int(round(delta))


def _tasks_by_day(tasks: list[ExecutionTask]) -> dict[str, list[ExecutionTask]]:
    groups: dict[str, list[ExecutionTask]] = defaultdict(list)
    for task in tasks:
        created_at = _to_local_naive(task.created_at)
        if created_at:
            groups[created_at.date().isoformat()].append(task)
    return groups


def build_execution_speed_dashboard(db: Session, *, range_name: str = "last_30_days") -> dict:
    range_name = range_name if range_name in VALID_RANGES else "last_30_days"
    start, end = _range_bounds(range_name)
    tasks = (
        db.query(ExecutionTask)
        .filter(ExecutionTask.created_at >= _db_time(start), ExecutionTask.created_at <= _db_time(end))
        .order_by(ExecutionTask.created_at.asc(), ExecutionTask.id.asc())
        .all()
    )
    assignee_ids = {task.assignee_id for task in tasks if task.assignee_id}
    users = db.query(User).filter(User.id.in_(assignee_ids)).all() if assignee_ids else []
    users_by_id = {user.id: user for user in users}

    completion_durations = [
        duration
        for duration in (_duration_ms(task.created_at, task.completed_at) for task in tasks if task.status == "done")
        if duration is not None
    ]
    overdue_count = sum(1 for task in tasks if is_overdue(task))
    status_counts = Counter(task.status for task in tasks)

    by_assignee: dict[int, list[ExecutionTask]] = defaultdict(list)
    for task in tasks:
        by_assignee[task.assignee_id].append(task)
    assignee_rows = []
    for assignee_id, assignee_tasks in by_assignee.items():
        done_tasks = [task for task in assignee_tasks if task.status == "done"]
        assignee_rows.append(
            {
                "assignee_id": assignee_id,
                "username": users_by_id.get(assignee_id).username if users_by_id.get(assignee_id) else str(assignee_id),
                "task_count": len(assignee_tasks),
                "done_count": len(done_tasks),
                "overdue_count": sum(1 for task in assignee_tasks if is_overdue(task)),
                "avg_completion_duration_ms": _avg(
                    [
                        duration
                        for duration in (
                            _duration_ms(task.created_at, task.completed_at)
                            for task in done_tasks
                        )
                        if duration is not None
                    ]
                ),
            }
        )

    by_day = _tasks_by_day(tasks)
    daily_trends = []
    current_day = start.date()
    while current_day <= end.date():
        day_key = current_day.isoformat()
        day_tasks = by_day.get(day_key, [])
        done_tasks = [task for task in day_tasks if task.status == "done"]
        daily_trends.append(
            {
                "date": day_key,
                "task_count": len(day_tasks),
                "done_count": len(done_tasks),
                "cancelled_count": sum(1 for task in day_tasks if task.status == "cancelled"),
                "overdue_count": sum(1 for task in day_tasks if is_overdue(task)),
                "avg_completion_duration_ms": _avg(
                    [
                        duration
                        for duration in (
                            _duration_ms(task.created_at, task.completed_at)
                            for task in done_tasks
                        )
                        if duration is not None
                    ]
                ),
            }
        )
        current_day += timedelta(days=1)

    return {
        "timezone": "Asia/Shanghai",
        "range": range_name,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "task_count": len(tasks),
        "open_count": sum(1 for task in tasks if task.status not in {"done", "cancelled"}),
        "done_count": status_counts.get("done", 0),
        "cancelled_count": status_counts.get("cancelled", 0),
        "overdue_count": overdue_count,
        "avg_completion_duration_ms": _avg(completion_durations),
        "daily_trends": daily_trends,
        "by_assignee": sorted(assignee_rows, key=lambda item: (-item["task_count"], item["username"])),
        "status_distribution": [
            {"status": task_status, "count": count}
            for task_status, count in sorted(status_counts.items())
        ],
        "empty_state": len(tasks) == 0,
        "low_sample_warning": 0 < len(tasks) < LOW_SAMPLE_THRESHOLD,
    }
