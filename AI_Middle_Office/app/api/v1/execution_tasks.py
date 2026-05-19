from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.execution_task import ExecutionTask
from app.models.user import User
from app.services.execution_tasks import (
    PROGRESS_TRANSITIONS,
    TERMINAL_EXECUTION_STATUSES,
    can_view_all_execution_tasks,
    clean_text,
    get_accessible_execution_task,
    normalize_source,
    normalize_status,
    parse_local_datetime,
    require_execution_task_access,
    serialize_execution_event,
    serialize_execution_task,
    task_snapshot,
    write_execution_event,
    _now_local_naive,
)


router = APIRouter()


class ExecutionTaskCreate(BaseModel):
    title: str
    source: Optional[str] = "manual"
    source_ref_id: Optional[str] = None
    assignee_id: int
    due_at: str
    notes: Optional[str] = None


class ExecutionTaskUpdate(BaseModel):
    title: Optional[str] = None
    source: Optional[str] = None
    source_ref_id: Optional[str] = None
    assignee_id: Optional[int] = None
    due_at: Optional[str] = None
    completed_at: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class ExecutionTaskCancel(BaseModel):
    reason: str


def _payload_dict(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _ensure_execution_enabled() -> None:
    if not settings.feature_execution:
        raise HTTPException(status_code=403, detail="FEATURE_DISABLED")


def _get_user_or_422(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_ASSIGNEE")
    return user


def _assignee_map(db: Session, tasks: list[ExecutionTask]) -> dict[int, User]:
    assignee_ids = {task.assignee_id for task in tasks if task.assignee_id}
    if not assignee_ids:
        return {}
    users = db.query(User).filter(User.id.in_(assignee_ids)).all()
    return {user.id: user for user in users}


def _serialize_task_with_assignee(db: Session, task: ExecutionTask) -> dict:
    return serialize_execution_task(task, assignee=_assignee_map(db, [task]).get(task.assignee_id))


@router.post("/execution-tasks", summary="创建执行任务")
async def create_execution_task(
    payload: ExecutionTaskCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_execution_enabled()
    title = clean_text(payload.title, 255)
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="TITLE_REQUIRED")
    due_at = parse_local_datetime(payload.due_at, field_name="due_at")
    if due_at is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="DUE_AT_REQUIRED")
    assignee = _get_user_or_422(db, payload.assignee_id)
    task = ExecutionTask(
        title=title,
        source=normalize_source(payload.source),
        source_ref_id=clean_text(payload.source_ref_id, 64),
        assignee_id=assignee.id,
        due_at=due_at,
        status="pending",
        notes=clean_text(payload.notes, 2000),
    )
    db.add(task)
    db.flush()
    after = task_snapshot(task)
    write_execution_event(
        db,
        task=task,
        event_type="created",
        operator=current_user,
        before=None,
        after=after,
        request=request,
    )
    db.commit()
    db.refresh(task)
    return api_ok(serialize_execution_task(task, assignee=assignee))


@router.get("/execution-tasks", summary="查询执行任务")
async def list_execution_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    source: Optional[str] = None,
    assignee_id: Optional[int] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_execution_enabled()
    require_execution_task_access(current_user)
    query = db.query(ExecutionTask)
    if not can_view_all_execution_tasks(current_user):
        query = query.filter(ExecutionTask.assignee_id == current_user.id)
    elif assignee_id is not None:
        query = query.filter(ExecutionTask.assignee_id == assignee_id)
    if status_filter:
        statuses = [normalize_status(item) for item in status_filter.split(",") if item.strip()]
        statuses = [item for item in statuses if item]
        if statuses:
            query = query.filter(ExecutionTask.status.in_(statuses))
    if source:
        query = query.filter(ExecutionTask.source == normalize_source(source))
    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            pattern = f"%{keyword_value}%"
            query = query.filter(
                or_(
                    ExecutionTask.title.like(pattern),
                    ExecutionTask.source_ref_id.like(pattern),
                    ExecutionTask.notes.like(pattern),
                )
            )
    total = query.count()
    tasks = (
        query.order_by(ExecutionTask.due_at.asc(), ExecutionTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    assignees = _assignee_map(db, tasks)
    return api_page(
        [serialize_execution_task(task, assignee=assignees.get(task.assignee_id)) for task in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/execution-tasks/{task_id}", summary="查询执行任务详情")
async def get_execution_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_execution_enabled()
    task = get_accessible_execution_task(db, task_id, current_user)
    data = _serialize_task_with_assignee(db, task)
    data["events"] = [serialize_execution_event(event) for event in task.events]
    return api_ok(data)


@router.patch("/execution-tasks/{task_id}", summary="更新执行任务")
async def update_execution_task(
    task_id: int,
    payload: ExecutionTaskUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_execution_enabled()
    task = get_accessible_execution_task(db, task_id, current_user)
    updates = _payload_dict(payload)
    if not updates:
        return api_ok(_serialize_task_with_assignee(db, task))

    is_admin = can_view_all_execution_tasks(current_user)
    if not is_admin:
        disallowed = set(updates) - {"status", "completed_at", "notes"}
        if disallowed:
            raise HTTPException(status_code=403, detail="PERMISSION_DENIED")

    requested_status = normalize_status(updates.get("status")) if "status" in updates else None
    if task.status in TERMINAL_EXECUTION_STATUSES:
        if task.status == "done" and requested_status == "done" and set(updates) <= {"status", "completed_at"}:
            return api_ok(_serialize_task_with_assignee(db, task))
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="EXECUTION_TASK_TERMINAL")

    before = task_snapshot(task)
    if is_admin:
        if "title" in updates:
            title = clean_text(updates["title"], 255)
            if not title:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="TITLE_REQUIRED")
            task.title = title
        if "source" in updates:
            task.source = normalize_source(updates["source"])
        if "source_ref_id" in updates:
            task.source_ref_id = clean_text(updates["source_ref_id"], 64)
        if "assignee_id" in updates:
            task.assignee_id = _get_user_or_422(db, int(updates["assignee_id"])).id
        if "due_at" in updates:
            due_at = parse_local_datetime(updates["due_at"], field_name="due_at")
            if due_at is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="DUE_AT_REQUIRED")
            task.due_at = due_at
    if "notes" in updates:
        task.notes = clean_text(updates["notes"], 2000)

    status_changed = False
    if requested_status:
        if requested_status != "done" and "completed_at" in updates:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="STATUS_DONE_REQUIRED")
        if requested_status == "cancelled":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="USE_CANCEL_ENDPOINT")
        if requested_status != task.status:
            if (task.status, requested_status) not in PROGRESS_TRANSITIONS:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="INVALID_EXECUTION_TRANSITION")
            status_changed = True
            task.status = requested_status
        if requested_status == "done":
            completed_at = parse_local_datetime(updates.get("completed_at"), field_name="completed_at")
            task.completed_at = completed_at or _now_local_naive()
    elif "completed_at" in updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="STATUS_DONE_REQUIRED")

    after = task_snapshot(task)
    event_type = "completed" if status_changed and task.status == "done" else "status_changed" if status_changed else "updated"
    write_execution_event(
        db,
        task=task,
        event_type=event_type,
        operator=current_user,
        before=before,
        after=after,
        request=request,
    )
    db.commit()
    db.refresh(task)
    return api_ok(_serialize_task_with_assignee(db, task))


@router.post("/execution-tasks/{task_id}/cancel", summary="取消执行任务")
async def cancel_execution_task(
    task_id: int,
    payload: ExecutionTaskCancel,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_execution_enabled()
    task = get_accessible_execution_task(db, task_id, current_user)
    reason = clean_text(payload.reason, 2000)
    if not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    if task.status == "cancelled":
        return api_ok(_serialize_task_with_assignee(db, task))
    if task.status == "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="EXECUTION_TASK_DONE")
    before = task_snapshot(task)
    task.status = "cancelled"
    task.completed_at = None
    after = task_snapshot(task)
    write_execution_event(
        db,
        task=task,
        event_type="cancelled",
        operator=current_user,
        before=before,
        after=after,
        reason=reason,
        request=request,
    )
    db.commit()
    db.refresh(task)
    return api_ok(_serialize_task_with_assignee(db, task))
