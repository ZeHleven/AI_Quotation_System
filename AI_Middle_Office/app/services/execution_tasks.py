from __future__ import annotations

import json
from datetime import datetime

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.time_utils import APP_TZ, app_local_naive, parse_iso_datetime
from app.models.execution_task import ExecutionTask, ExecutionTaskEvent
from app.models.user import User
from app.services.rbac import has_admin_role, has_any_role


VALID_EXECUTION_SOURCES = {"manual", "meeting", "quote"}
CN_TZ = APP_TZ
VALID_EXECUTION_STATUSES = {"pending", "in_progress", "done", "cancelled"}
TERMINAL_EXECUTION_STATUSES = {"done", "cancelled"}
PROGRESS_TRANSITIONS = {
    ("pending", "in_progress"),
    ("in_progress", "done"),
    ("pending", "done"),
}


def _now_local_naive() -> datetime:
    return app_local_naive()


def _to_local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return app_local_naive(value)


def parse_local_datetime(value: str | None, *, field_name: str = "datetime") -> datetime | None:
    if value is None:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    try:
        parsed = parse_iso_datetime(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"INVALID_{field_name.upper()}") from exc
    return _to_local_naive(parsed)


def clean_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def normalize_source(value: str | None) -> str:
    normalized = (value or "manual").strip()
    if normalized not in VALID_EXECUTION_SOURCES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_EXECUTION_SOURCE")
    return normalized


def normalize_status(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized not in VALID_EXECUTION_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_EXECUTION_STATUS")
    return normalized


def can_access_execution_tasks(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "staff", "manager"})


def can_view_all_execution_tasks(user: User) -> bool:
    return has_admin_role(user)


def require_execution_task_access(user: User) -> None:
    if not can_access_execution_tasks(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def get_accessible_execution_task(db: Session, task_id: int, user: User) -> ExecutionTask:
    require_execution_task_access(user)
    query = db.query(ExecutionTask).filter(ExecutionTask.id == task_id)
    if not can_view_all_execution_tasks(user):
        query = query.filter(ExecutionTask.assignee_id == user.id)
    task = query.first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EXECUTION_TASK_NOT_FOUND")
    return task


def is_overdue(task: ExecutionTask, *, now: datetime | None = None) -> bool:
    if task.status in TERMINAL_EXECUTION_STATUSES or task.completed_at is not None:
        return False
    due_at = _to_local_naive(task.due_at)
    if due_at is None:
        return False
    return due_at < (now or _now_local_naive())


def task_snapshot(task: ExecutionTask) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "source": task.source,
        "source_ref_id": task.source_ref_id,
        "assignee_id": task.assignee_id,
        "due_at": format_dt(task.due_at),
        "completed_at": format_dt(task.completed_at),
        "status": task.status,
        "notes": task.notes,
    }


def format_dt(value: datetime | None) -> str | None:
    value = _to_local_naive(value)
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def serialize_execution_task(task: ExecutionTask, *, assignee: User | None = None) -> dict:
    return {
        **task_snapshot(task),
        "created_at": format_dt(task.created_at),
        "updated_at": format_dt(task.updated_at),
        "assignee_username": assignee.username if assignee else None,
        "is_overdue": is_overdue(task),
    }


def serialize_execution_event(event: ExecutionTaskEvent) -> dict:
    return {
        "id": event.id,
        "created_at": format_dt(event.created_at),
        "execution_task_id": event.execution_task_id,
        "event_type": event.event_type,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "operator_id": event.operator_id,
        "reason": event.reason,
        "before_json": event.before_json,
        "after_json": event.after_json,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "trace_id": event.trace_id,
    }


def request_context(request: Request | None) -> tuple[str | None, str | None, str | None]:
    if request is None:
        return None, None, None
    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else None
    if not ip_address and request.client:
        ip_address = request.client.host
    user_agent = request.headers.get("user-agent")
    trace_id = getattr(request.state, "trace_id", None) or request.headers.get("x-trace-id")
    return ip_address, user_agent, trace_id


def write_execution_event(
    db: Session,
    *,
    task: ExecutionTask,
    event_type: str,
    operator: User | None,
    before: dict | None,
    after: dict | None,
    reason: str | None = None,
    request: Request | None = None,
) -> ExecutionTaskEvent:
    ip_address, user_agent, trace_id = request_context(request)
    event = ExecutionTaskEvent(
        execution_task_id=task.id,
        event_type=event_type,
        from_status=before.get("status") if before else None,
        to_status=after.get("status") if after else None,
        operator_id=operator.id if operator else None,
        reason=clean_text(reason, 2000),
        before_json=json.dumps(before, ensure_ascii=False, default=str) if before else None,
        after_json=json.dumps(after, ensure_ascii=False, default=str) if after else None,
        ip_address=ip_address,
        user_agent=user_agent,
        trace_id=trace_id,
    )
    db.add(event)
    return event
