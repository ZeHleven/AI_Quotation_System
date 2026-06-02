from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.file_object import FileObject
from app.models.project_progress import Project, ProjectStage, ProjectTask, ProjectTaskEvent, ProjectTaskEvidence
from app.models.user import User
from app.services.file_storage import StorageDisabledError, generate_presigned_get_url
from app.services.rbac import get_effective_roles
from app.services.project_progress import (
    QISHENG_EPC_COMPACT_TEMPLATE_KEY,
    QISHENG_EPC_FULL_TEMPLATE_KEY,
    QISHENG_EPC_TEMPLATE_NAME,
    SINGLE_USER_TRIAL_TEMPLATE_KEY,
    SINGLE_USER_TRIAL_TEMPLATE_NAME,
    TASK_PROGRESS_BY_STATUS,
    TASK_ROLLBACK_TARGETS,
    TASK_STATUS_LABELS,
    TASK_TERMINAL_STATUSES,
    TASK_TRANSITIONS,
    accessible_project_query,
    can_manage_project,
    can_manage_project_module,
    can_view_all_projects,
    clean_text,
    create_default_stages,
    create_qisheng_epc_stages,
    create_qisheng_epc_tasks,
    create_single_user_trial_tasks,
    derive_task_evidence_fields,
    get_accessible_project,
    get_accessible_task,
    get_project_stage,
    next_project_code,
    normalize_priority,
    normalize_evidence_type,
    normalize_project_status,
    normalize_source_type,
    normalize_stage_status,
    normalize_task_status,
    now_local_naive,
    parse_local_datetime,
    project_snapshot,
    recalculate_project_progress,
    require_project_access,
    require_project_manager,
    require_task_mutation,
    serialize_event,
    serialize_evidence,
    serialize_project,
    serialize_stage,
    serialize_task,
    stage_snapshot,
    task_snapshot,
    task_evidence_requirement,
    task_evidence_summary,
    write_project_event,
)


router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    project_code: Optional[str] = None
    client_name: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    project_manager_id: int
    owner_department: Optional[str] = None
    planned_start_at: Optional[str] = None
    planned_finish_at: Optional[str] = None


class ProjectTrialTemplateCreate(BaseModel):
    name: Optional[str] = None
    client_name: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    owner_department: Optional[str] = None
    planned_start_at: Optional[str] = None
    planned_finish_at: Optional[str] = None
    template_key: str = SINGLE_USER_TRIAL_TEMPLATE_KEY


class ProjectEpcTemplateCreate(BaseModel):
    name: Optional[str] = None
    client_name: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    owner_department: Optional[str] = None
    planned_start_at: Optional[str] = None
    planned_finish_at: Optional[str] = None
    mode: str = "compact"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    client_name: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    project_manager_id: Optional[int] = None
    owner_department: Optional[str] = None
    planned_start_at: Optional[str] = None
    planned_finish_at: Optional[str] = None
    actual_start_at: Optional[str] = None
    actual_finish_at: Optional[str] = None


class StageUpdate(BaseModel):
    owner_role: Optional[str] = None
    owner_user_id: Optional[int] = None
    status: Optional[str] = None
    planned_start_at: Optional[str] = None
    planned_finish_at: Optional[str] = None
    actual_start_at: Optional[str] = None
    actual_finish_at: Optional[str] = None


class ProjectTaskCreate(BaseModel):
    stage_id: int
    title: str
    description: Optional[str] = None
    owner_user_id: int
    owner_role: Optional[str] = None
    collaborators: list[int] = Field(default_factory=list)
    priority: str = "normal"
    planned_start_at: Optional[str] = None
    due_at: Optional[str] = None
    next_action: Optional[str] = None
    source_type: Optional[str] = "manual"
    source_id: Optional[str] = None


class ProjectTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    owner_user_id: Optional[int] = None
    owner_role: Optional[str] = None
    collaborators: Optional[list[int]] = None
    priority: Optional[str] = None
    planned_start_at: Optional[str] = None
    due_at: Optional[str] = None
    next_action: Optional[str] = None


class TaskBlockIn(BaseModel):
    reason: str
    next_action: Optional[str] = None


class TaskUnblockIn(BaseModel):
    resolution: str
    next_action: Optional[str] = None


class TaskRollbackIn(BaseModel):
    target_status: str
    reason: str


class ProjectTaskEvidenceCreate(BaseModel):
    evidence_type: str
    title: str
    description: Optional[str] = None
    file_object_id: Optional[str] = None
    external_url: Optional[str] = None
    external_provider: Optional[str] = None


class TaskEvidenceSoftConfirmIn(BaseModel):
    confirm_without_evidence_reason: Optional[str] = None
    bypass_reason: Optional[str] = None


class ReasonIn(BaseModel):
    reason: Optional[str] = None


def _payload_dict(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _ensure_feature_enabled() -> None:
    if not settings.feature_project_progress:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NOT_FOUND")


def _ensure_dashboard_feature_enabled() -> None:
    if not settings.feature_dashboard_project:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _get_user_or_422(db: Session, user_id: int | None, *, detail: str = "INVALID_USER") -> User | None:
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    return user


def _user_map(db: Session, user_ids: set[int | None]) -> dict[int, User]:
    ids = {int(user_id) for user_id in user_ids if user_id}
    if not ids:
        return {}
    users = db.query(User).filter(User.id.in_(ids)).all()
    return {user.id: user for user in users}


def _project_user_map(db: Session, projects: list[Project]) -> dict[int, User]:
    user_ids = {project.project_manager_id for project in projects}
    for project in projects:
        for stage in project.stages:
            user_ids.add(stage.owner_user_id)
        for task in project.tasks:
            user_ids.add(task.owner_user_id)
    return _user_map(db, user_ids)


def _task_user_map(db: Session, tasks: list[ProjectTask]) -> dict[int, User]:
    user_ids = {task.owner_user_id for task in tasks}
    user_ids.update(task.created_by for task in tasks)
    return _user_map(db, user_ids)


def _event_user_map(db: Session, events: list[ProjectTaskEvent]) -> dict[int, User]:
    return _user_map(db, {event.actor_user_id for event in events})


def _evidence_user_map(db: Session, evidences: list[ProjectTaskEvidence]) -> dict[int, User]:
    user_ids = set()
    for evidence in evidences:
        user_ids.add(evidence.created_by)
        user_ids.add(evidence.removed_by)
    return _user_map(db, user_ids)


def _evidence_file_map(db: Session, evidences: list[ProjectTaskEvidence]) -> dict[str, FileObject]:
    file_ids = {evidence.file_object_id for evidence in evidences if evidence.file_object_id}
    if not file_ids:
        return {}
    files = db.query(FileObject).filter(FileObject.file_id.in_(file_ids)).all()
    return {file_obj.file_id: file_obj for file_obj in files}


def _get_accessible_evidence(db: Session, evidence_id: int, current_user: User) -> ProjectTaskEvidence:
    evidence = db.query(ProjectTaskEvidence).filter(ProjectTaskEvidence.id == evidence_id).first()
    if not evidence:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_TASK_EVIDENCE_NOT_FOUND")
    get_accessible_task(db, evidence.task_id, current_user)
    return evidence


def _validate_stage_belongs_to_project(stage: ProjectStage, project_id: int) -> None:
    if stage.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="STAGE_PROJECT_MISMATCH")


def _set_project_datetime(project: Project, field_name: str, value: str | None) -> None:
    setattr(project, field_name, parse_local_datetime(value, field_name=field_name))


@router.post("/admin/projects", summary="创建工程项目")
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    require_project_manager(current_user)
    name = clean_text(payload.name, 255)
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_NAME_REQUIRED")
    manager = _get_user_or_422(db, payload.project_manager_id, detail="INVALID_PROJECT_MANAGER")
    project_code = clean_text(payload.project_code, 64) or next_project_code(db)
    if db.query(Project).filter(Project.project_code == project_code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_CODE_EXISTS")
    planned_start = parse_local_datetime(payload.planned_start_at, field_name="planned_start_at")
    planned_finish = parse_local_datetime(payload.planned_finish_at, field_name="planned_finish_at")
    if planned_start and planned_finish and planned_finish < planned_start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_DATE_RANGE")
    project = Project(
        project_code=project_code,
        name=name,
        client_name=clean_text(payload.client_name, 128),
        address=clean_text(payload.address, 255),
        description=clean_text(payload.description, 4000),
        project_manager_id=manager.id,
        owner_department=clean_text(payload.owner_department, 128),
        planned_start_at=planned_start,
        planned_finish_at=planned_finish,
        created_by=current_user.id,
        status="planning",
        risk_level="normal",
        progress_percent=0,
    )
    db.add(project)
    db.flush()
    create_default_stages(db, project)
    db.flush()
    recalculate_project_progress(db, project)
    write_project_event(
        db,
        project=project,
        event_type="project_created",
        actor=current_user,
        after=project_snapshot(project),
        message="创建工程项目",
    )
    db.commit()
    db.refresh(project)
    return api_ok(serialize_project(project, users=_project_user_map(db, [project]), include_stages=True))


@router.get("/admin/projects", summary="查询工程项目")
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    risk_level: Optional[str] = None,
    project_manager_id: Optional[int] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    query = accessible_project_query(db, current_user)
    if status_filter:
        statuses = [normalize_project_status(item) for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(Project.status.in_(statuses))
    if risk_level:
        risks = [item.strip() for item in risk_level.split(",") if item.strip()]
        invalid = [item for item in risks if item not in {"normal", "warning", "delayed", "blocked"}]
        if invalid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_RISK")
        if risks:
            query = query.filter(Project.risk_level.in_(risks))
    if project_manager_id is not None:
        query = query.filter(Project.project_manager_id == project_manager_id)
    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            pattern = f"%{keyword_value}%"
            query = query.filter(
                or_(
                    Project.project_code.like(pattern),
                    Project.name.like(pattern),
                    Project.client_name.like(pattern),
                    Project.address.like(pattern),
                    Project.owner_department.like(pattern),
                )
            )
    total = query.count()
    projects = (
        query.order_by(Project.updated_at.desc(), Project.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    users = _project_user_map(db, projects)
    return api_page(
        [serialize_project(project, users=users) for project in projects],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/projects/users", summary="查询项目可分配用户")
async def list_project_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    if not can_manage_project_module(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).all()
    return api_ok(
        [
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "roles": get_effective_roles(user),
                "is_active": user.is_active,
            }
            for user in users
        ]
    )


@router.post("/admin/projects/trial-template", summary="创建单人试运行工程项目")
async def create_single_user_trial_project(
    payload: ProjectTrialTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    require_project_manager(current_user)
    if payload.template_key != SINGLE_USER_TRIAL_TEMPLATE_KEY:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TRIAL_TEMPLATE")
    planned_start = parse_local_datetime(payload.planned_start_at, field_name="planned_start_at") or now_local_naive()
    planned_finish = parse_local_datetime(payload.planned_finish_at, field_name="planned_finish_at")
    if planned_finish and planned_finish < planned_start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_DATE_RANGE")
    name = clean_text(payload.name, 255) or f"单人试运行项目-{planned_start.strftime('%Y%m%d')}"
    project = Project(
        project_code=next_project_code(db),
        name=name,
        client_name=clean_text(payload.client_name, 128),
        address=clean_text(payload.address, 255),
        description=clean_text(payload.description, 4000) or f"{SINGLE_USER_TRIAL_TEMPLATE_NAME}，任务默认分配给当前账号。",
        project_manager_id=current_user.id,
        owner_department=clean_text(payload.owner_department, 128) or "工程部",
        planned_start_at=planned_start,
        planned_finish_at=planned_finish,
        created_by=current_user.id,
        status="planning",
        risk_level="normal",
        progress_percent=0,
    )
    db.add(project)
    db.flush()
    create_default_stages(db, project)
    db.flush()
    create_single_user_trial_tasks(db, project, owner=current_user, actor=current_user)
    write_project_event(
        db,
        project=project,
        event_type="project_created",
        actor=current_user,
        after=project_snapshot(project),
        message="创建单人试运行项目",
    )
    db.commit()
    db.refresh(project)
    data = serialize_project(project, users=_project_user_map(db, [project]), include_stages=True)
    data["tasks"] = [
        serialize_task(task, users=_task_user_map(db, project.tasks))
        for task in sorted(project.tasks, key=lambda item: (item.stage.sort_order if item.stage else 0, item.id))
    ]
    data["template"] = {
        "template_key": SINGLE_USER_TRIAL_TEMPLATE_KEY,
        "template_name": SINGLE_USER_TRIAL_TEMPLATE_NAME,
    }
    return api_ok(data)


@router.post("/admin/projects/epc-template", summary="创建旗胜 EPC 流程项目")
async def create_qisheng_epc_project(
    payload: ProjectEpcTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    require_project_manager(current_user)
    mode = (payload.mode or "compact").strip()
    if mode not in {"compact", "full"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_EPC_TEMPLATE_MODE")
    planned_start = parse_local_datetime(payload.planned_start_at, field_name="planned_start_at") or now_local_naive()
    planned_finish = parse_local_datetime(payload.planned_finish_at, field_name="planned_finish_at")
    if planned_finish and planned_finish < planned_start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_DATE_RANGE")
    name = clean_text(payload.name, 255) or f"旗胜EPC项目-{planned_start.strftime('%Y%m%d')}"
    project = Project(
        project_code=next_project_code(db),
        name=name,
        client_name=clean_text(payload.client_name, 128),
        address=clean_text(payload.address, 255),
        description=clean_text(payload.description, 4000) or f"{QISHENG_EPC_TEMPLATE_NAME}，{mode} 模式，任务默认分配给当前账号。",
        project_manager_id=current_user.id,
        owner_department=clean_text(payload.owner_department, 128) or "项目管理部",
        planned_start_at=planned_start,
        planned_finish_at=planned_finish,
        created_by=current_user.id,
        status="planning",
        risk_level="normal",
        progress_percent=0,
    )
    db.add(project)
    db.flush()
    create_qisheng_epc_stages(db, project, owner=current_user)
    db.flush()
    create_qisheng_epc_tasks(db, project, owner=current_user, actor=current_user, mode=mode)
    write_project_event(
        db,
        project=project,
        event_type="project_created",
        actor=current_user,
        after=project_snapshot(project),
        message=f"创建旗胜 EPC 流程项目（{'完整' if mode == 'full' else '精简'}模式）",
    )
    db.commit()
    db.refresh(project)
    data = serialize_project(project, users=_project_user_map(db, [project]), include_stages=True)
    data["tasks"] = [
        serialize_task(task, users=_task_user_map(db, project.tasks))
        for task in sorted(project.tasks, key=lambda item: (item.stage.sort_order if item.stage else 0, item.id))
    ]
    data["template"] = {
        "template_key": QISHENG_EPC_FULL_TEMPLATE_KEY if mode == "full" else QISHENG_EPC_COMPACT_TEMPLATE_KEY,
        "template_name": QISHENG_EPC_TEMPLATE_NAME,
        "mode": mode,
        "task_count": len(data["tasks"]),
    }
    return api_ok(data)


@router.get("/admin/projects/{project_id}", summary="查询工程项目详情")
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    recalculate_project_progress(db, project)
    db.commit()
    db.refresh(project)
    data = serialize_project(project, users=_project_user_map(db, [project]), include_stages=True)
    data["tasks"] = [
        serialize_task(task, users=_task_user_map(db, project.tasks))
        for task in sorted(project.tasks, key=lambda item: (item.stage.sort_order if item.stage else 0, item.id))
    ]
    return api_ok(data)


@router.patch("/admin/projects/{project_id}", summary="更新工程项目")
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    require_project_manager(current_user, project)
    updates = _payload_dict(payload)
    if not updates:
        return api_ok(serialize_project(project, users=_project_user_map(db, [project]), include_stages=True))
    before = project_snapshot(project)
    if "name" in updates:
        name = clean_text(updates["name"], 255)
        if not name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_NAME_REQUIRED")
        project.name = name
    if "client_name" in updates:
        project.client_name = clean_text(updates["client_name"], 128)
    if "address" in updates:
        project.address = clean_text(updates["address"], 255)
    if "description" in updates:
        project.description = clean_text(updates["description"], 4000)
    if "status" in updates:
        project.status = normalize_project_status(updates["status"])
    if "project_manager_id" in updates:
        project.project_manager_id = _get_user_or_422(db, int(updates["project_manager_id"]), detail="INVALID_PROJECT_MANAGER").id
    if "owner_department" in updates:
        project.owner_department = clean_text(updates["owner_department"], 128)
    for field in ("planned_start_at", "planned_finish_at", "actual_start_at", "actual_finish_at"):
        if field in updates:
            _set_project_datetime(project, field, updates[field])
    if project.planned_start_at and project.planned_finish_at and project.planned_finish_at < project.planned_start_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_DATE_RANGE")
    recalculate_project_progress(db, project)
    write_project_event(
        db,
        project=project,
        event_type="project_updated",
        actor=current_user,
        before=before,
        after=project_snapshot(project),
        message="更新工程项目",
    )
    db.commit()
    db.refresh(project)
    return api_ok(serialize_project(project, users=_project_user_map(db, [project]), include_stages=True))


def _change_project_status(db: Session, project: Project, current_user: User, *, status_value: str, event_type: str, message: str) -> dict:
    require_project_manager(current_user, project)
    before = project_snapshot(project)
    project.status = status_value
    now = now_local_naive()
    if status_value == "active" and project.actual_start_at is None:
        project.actual_start_at = now
    if status_value == "completed" and project.actual_finish_at is None:
        project.actual_finish_at = now
    if status_value in {"planning", "active", "paused"}:
        project.actual_finish_at = None
    recalculate_project_progress(db, project)
    write_project_event(
        db,
        project=project,
        event_type=event_type,
        actor=current_user,
        before=before,
        after=project_snapshot(project),
        message=message,
    )
    db.commit()
    db.refresh(project)
    return serialize_project(project, users=_project_user_map(db, [project]), include_stages=True)


@router.post("/admin/projects/{project_id}/start", summary="启动工程项目")
async def start_project(project_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    return api_ok(_change_project_status(db, project, current_user, status_value="active", event_type="project_started", message="启动工程项目"))


@router.post("/admin/projects/{project_id}/pause", summary="暂停工程项目")
async def pause_project(project_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    return api_ok(_change_project_status(db, project, current_user, status_value="paused", event_type="project_paused", message="暂停工程项目"))


@router.post("/admin/projects/{project_id}/complete", summary="完成工程项目")
async def complete_project(project_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    open_tasks = [task for task in project.tasks if task.status not in {"done", "cancelled"}]
    if open_tasks:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_HAS_OPEN_TASKS")
    return api_ok(_change_project_status(db, project, current_user, status_value="completed", event_type="project_completed", message="完成工程项目"))


@router.post("/admin/projects/{project_id}/cancel", summary="取消工程项目")
async def cancel_project(
    project_id: int,
    payload: ReasonIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    data = _change_project_status(
        db,
        project,
        current_user,
        status_value="cancelled",
        event_type="project_cancelled",
        message=clean_text(payload.reason, 2000) or "取消工程项目",
    )
    return api_ok(data)


@router.get("/admin/projects/{project_id}/stages", summary="查询项目阶段")
async def list_project_stages(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    recalculate_project_progress(db, project)
    db.commit()
    return api_ok([serialize_stage(stage, users=_project_user_map(db, [project])) for stage in sorted(project.stages, key=lambda item: item.sort_order)])


@router.patch("/admin/project-stages/{stage_id}", summary="更新项目阶段")
async def update_project_stage(
    stage_id: int,
    payload: StageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    stage = get_project_stage(db, stage_id, current_user)
    project = stage.project
    require_project_manager(current_user, project)
    updates = _payload_dict(payload)
    before = stage_snapshot(stage)
    if "owner_role" in updates:
        stage.owner_role = clean_text(updates["owner_role"], 64)
    if "owner_user_id" in updates:
        stage.owner_user_id = _get_user_or_422(db, updates["owner_user_id"]).id if updates["owner_user_id"] else None
    if "status" in updates:
        if any(task.status != "cancelled" for task in stage.tasks):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STAGE_HAS_TASKS")
        stage.status = normalize_stage_status(updates["status"])
    for field in ("planned_start_at", "planned_finish_at", "actual_start_at", "actual_finish_at"):
        if field in updates:
            setattr(stage, field, parse_local_datetime(updates[field], field_name=field))
    recalculate_project_progress(db, project)
    write_project_event(
        db,
        project=project,
        stage=stage,
        event_type="stage_updated",
        actor=current_user,
        before=before,
        after=stage_snapshot(stage),
        message="更新项目阶段",
    )
    db.commit()
    db.refresh(stage)
    return api_ok(serialize_stage(stage, users=_project_user_map(db, [project])))


@router.get("/admin/projects/{project_id}/tasks", summary="查询项目任务")
async def list_project_tasks(
    project_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    owner_user_id: Optional[int] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    query = db.query(ProjectTask).filter(ProjectTask.project_id == project.id)
    if status_filter:
        statuses = [normalize_task_status(item) for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(ProjectTask.status.in_(statuses))
    if owner_user_id is not None:
        query = query.filter(ProjectTask.owner_user_id == owner_user_id)
    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            pattern = f"%{keyword_value}%"
            query = query.filter(or_(ProjectTask.title.like(pattern), ProjectTask.description.like(pattern), ProjectTask.next_action.like(pattern)))
    tasks = query.order_by(ProjectTask.due_at.asc(), ProjectTask.id.asc()).all()
    return api_ok([serialize_task(task, users=_task_user_map(db, tasks)) for task in tasks])


@router.post("/admin/projects/{project_id}/tasks", summary="创建项目任务")
async def create_project_task(
    project_id: int,
    payload: ProjectTaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    require_project_manager(current_user, project)
    stage = get_project_stage(db, payload.stage_id, current_user)
    _validate_stage_belongs_to_project(stage, project.id)
    title = clean_text(payload.title, 255)
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_TITLE_REQUIRED")
    owner = _get_user_or_422(db, payload.owner_user_id, detail="INVALID_TASK_OWNER")
    planned_start = parse_local_datetime(payload.planned_start_at, field_name="planned_start_at")
    due_at = parse_local_datetime(payload.due_at, field_name="due_at")
    if planned_start and due_at and due_at < planned_start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_TASK_DATE_RANGE")
    task = ProjectTask(
        project_id=project.id,
        stage_id=stage.id,
        title=title,
        description=clean_text(payload.description, 4000),
        **derive_task_evidence_fields(source_id=payload.source_id),
        owner_user_id=owner.id,
        owner_role=clean_text(payload.owner_role, 64),
        collaborators_json=None,
        status="todo",
        progress_percent=0,
        priority=normalize_priority(payload.priority),
        planned_start_at=planned_start,
        due_at=due_at,
        next_action=clean_text(payload.next_action, 2000),
        source_type=normalize_source_type(payload.source_type),
        source_id=clean_text(payload.source_id, 64),
        created_by=current_user.id,
    )
    if payload.collaborators:
        valid_collaborators = []
        for user_id in payload.collaborators:
            valid_collaborators.append(_get_user_or_422(db, user_id).id)
        task.collaborators_json = "[" + ",".join(str(user_id) for user_id in sorted(set(valid_collaborators))) + "]"
    db.add(task)
    db.flush()
    recalculate_project_progress(db, project)
    write_project_event(
        db,
        project=project,
        stage=stage,
        task=task,
        event_type="task_created",
        actor=current_user,
        after=task_snapshot(task),
        message="创建项目任务",
    )
    db.commit()
    db.refresh(task)
    return api_ok(serialize_task(task, users=_task_user_map(db, [task])))


@router.patch("/admin/project-tasks/{task_id}", summary="更新项目任务")
async def update_project_task(
    task_id: int,
    payload: ProjectTaskUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    if not can_manage_project(current_user, task.project):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    if task.status in TASK_TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_TASK_TERMINAL")
    updates = _payload_dict(payload)
    before = task_snapshot(task)
    if "title" in updates:
        title = clean_text(updates["title"], 255)
        if not title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_TITLE_REQUIRED")
        task.title = title
    if "description" in updates:
        task.description = clean_text(updates["description"], 4000)
    if "owner_user_id" in updates:
        task.owner_user_id = _get_user_or_422(db, int(updates["owner_user_id"]), detail="INVALID_TASK_OWNER").id
    if "owner_role" in updates:
        task.owner_role = clean_text(updates["owner_role"], 64)
    if "collaborators" in updates:
        collaborators = []
        for user_id in updates["collaborators"] or []:
            collaborators.append(_get_user_or_422(db, int(user_id)).id)
        task.collaborators_json = "[" + ",".join(str(user_id) for user_id in sorted(set(collaborators))) + "]" if collaborators else None
    if "priority" in updates:
        task.priority = normalize_priority(updates["priority"])
    if "planned_start_at" in updates:
        task.planned_start_at = parse_local_datetime(updates["planned_start_at"], field_name="planned_start_at")
    if "due_at" in updates:
        task.due_at = parse_local_datetime(updates["due_at"], field_name="due_at")
    if "next_action" in updates:
        task.next_action = clean_text(updates["next_action"], 2000)
    if task.planned_start_at and task.due_at and task.due_at < task.planned_start_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_TASK_DATE_RANGE")
    recalculate_project_progress(db, task.project)
    write_project_event(
        db,
        project=task.project,
        stage=task.stage,
        task=task,
        event_type="task_updated",
        actor=current_user,
        before=before,
        after=task_snapshot(task),
        message="更新项目任务",
    )
    db.commit()
    db.refresh(task)
    return api_ok(serialize_task(task, users=_task_user_map(db, [task])))


def _change_task_status(
    db: Session,
    task: ProjectTask,
    current_user: User,
    *,
    next_status: str,
    event_type: str,
    message: str,
    reason: str | None = None,
    next_action: str | None = None,
    extra_event_type: str | None = None,
    extra_message: str | None = None,
    extra_event_payload: dict | None = None,
):
    require_task_mutation(current_user, task)
    if next_status == "done" and not can_manage_project(current_user, task.project):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PROJECT_MANAGER_CONFIRM_REQUIRED")
    if next_status == "cancelled" and not can_manage_project(current_user, task.project):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    if task.status in TASK_TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_TASK_TERMINAL")
    next_status = normalize_task_status(next_status)
    if task.status == next_status:
        return serialize_task(task, users=_task_user_map(db, [task]))
    if (task.status, next_status) not in TASK_TRANSITIONS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="INVALID_PROJECT_TASK_TRANSITION")
    before = task_snapshot(task)
    if next_status == "blocked":
        cleaned_reason = clean_text(reason, 2000)
        if not cleaned_reason:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="BLOCK_REASON_REQUIRED")
        task.last_status_before_blocked = task.status if task.status != "blocked" else task.last_status_before_blocked
        task.blocked_reason = cleaned_reason
        task.next_action = clean_text(next_action, 2000)
    else:
        task.blocked_reason = None
        task.next_action = clean_text(next_action, 2000) if next_action is not None else task.next_action
        if next_status != "blocked":
            task.last_status_before_blocked = None
    task.status = next_status
    if next_status in TASK_PROGRESS_BY_STATUS:
        task.progress_percent = TASK_PROGRESS_BY_STATUS[next_status]
    if next_status == "done":
        task.completed_at = now_local_naive()
    elif next_status == "cancelled":
        task.completed_at = None
    if next_status in {"started", "progressing"} and task.planned_start_at is None:
        task.planned_start_at = now_local_naive()
    recalculate_project_progress(db, task.project)
    after = task_snapshot(task)
    write_project_event(
        db,
        project=task.project,
        stage=task.stage,
        task=task,
        event_type=event_type,
        actor=current_user,
        before=before,
        after=after,
        message=message,
    )
    if extra_event_type:
        write_project_event(
            db,
            project=task.project,
            stage=task.stage,
            task=task,
            event_type=extra_event_type,
            actor=current_user,
            after=after,
            message=extra_message,
            payload=extra_event_payload,
        )
    db.commit()
    db.refresh(task)
    return serialize_task(task, users=_task_user_map(db, [task]))


@router.post("/admin/project-tasks/{task_id}/start", summary="开始项目任务")
async def start_project_task(task_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    return api_ok(_change_task_status(db, task, current_user, next_status="started", event_type="task_started", message="开始项目任务"))


@router.post("/admin/project-tasks/{task_id}/progress", summary="推进项目任务")
async def progress_project_task(task_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    return api_ok(_change_task_status(db, task, current_user, next_status="progressing", event_type="task_progressing", message="推进项目任务"))


@router.post("/admin/project-tasks/{task_id}/submit", summary="提交项目任务确认")
async def submit_project_task(
    task_id: int,
    payload: TaskEvidenceSoftConfirmIn | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    missing_required_evidence = bool(task_evidence_requirement(task)) and not task_evidence_summary(task)["has_evidence"]
    reason = clean_text(payload.confirm_without_evidence_reason if payload else None, 2000) or "用户确认继续提交"
    return api_ok(
        _change_task_status(
            db,
            task,
            current_user,
            next_status="submitted",
            event_type="task_submitted",
            message="提交项目任务确认",
            extra_event_type="task_submitted_without_evidence" if missing_required_evidence else None,
            extra_message=f"无成果证据提交确认：{reason}" if missing_required_evidence else None,
        )
    )


@router.post("/admin/project-tasks/{task_id}/complete", summary="确认项目任务完成")
async def complete_project_task(
    task_id: int,
    payload: TaskEvidenceSoftConfirmIn | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    evidence_summary = task_evidence_summary(task)
    missing_required_evidence = bool(evidence_summary.get("requirement")) and not evidence_summary.get("has_evidence")
    is_hard_gate = missing_required_evidence and (task.evidence_policy or "none") == "complete_required"
    reason = clean_text(payload.confirm_without_evidence_reason if payload else None, 2000)
    bypass_reason = clean_text(payload.bypass_reason if payload else None, 2000)
    extra_event_type = "task_completed_without_evidence" if missing_required_evidence else None
    extra_message = f"无成果证据仍确认完成：{reason}" if missing_required_evidence else None
    extra_event_payload = None
    if is_hard_gate:
        if not can_manage_project(current_user, task.project):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="EVIDENCE_HARD_GATE_BLOCKED")
        if not bypass_reason or len(bypass_reason) < 6:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="EVIDENCE_BYPASS_REASON_REQUIRED")
        roles = get_effective_roles(current_user)
        extra_event_type = "task_completed_bypass_gate"
        extra_message = f"关键节点缺成果证据放行完成：{bypass_reason}"
        extra_event_payload = {
            "is_key_node": bool(task.is_key_node),
            "evidence_policy": task.evidence_policy,
            "evidence_requirement": evidence_summary.get("requirement"),
            "evidence_count_at_decision": int(evidence_summary.get("evidence_count") or 0),
            "bypass_reason": bypass_reason,
            "decided_by_user_id": current_user.id,
            "decided_by_username": current_user.username,
            "decided_by_role": roles[0] if roles else current_user.role,
            "decided_by_roles": roles,
            "task_status_before": task.status,
            "task_status_after": "done",
            "decided_at": now_local_naive().strftime("%Y-%m-%d %H:%M:%S"),
        }
    elif missing_required_evidence and not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="EVIDENCE_CONFIRM_REASON_REQUIRED")
    return api_ok(
        _change_task_status(
            db,
            task,
            current_user,
            next_status="done",
            event_type="task_completed",
            message="确认项目任务完成",
            extra_event_type=extra_event_type,
            extra_message=extra_message,
            extra_event_payload=extra_event_payload,
        )
    )


@router.post("/admin/project-tasks/{task_id}/rollback", summary="回退项目任务进度")
async def rollback_project_task(
    task_id: int,
    payload: TaskRollbackIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    require_task_mutation(current_user, task)
    target_status = normalize_task_status(payload.target_status)
    reason = clean_text(payload.reason, 2000)
    if not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ROLLBACK_REASON_REQUIRED")
    if task.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_TASK_TERMINAL")
    if task.status == "blocked":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_TASK_BLOCKED_UNBLOCK_FIRST")
    if task.status == "done" and not can_manage_project(current_user, task.project):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PROJECT_MANAGER_CONFIRM_REQUIRED")
    allowed_targets = TASK_ROLLBACK_TARGETS.get(task.status, set())
    if target_status not in allowed_targets:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="INVALID_PROJECT_TASK_ROLLBACK")
    before = task_snapshot(task)
    task.status = target_status
    task.progress_percent = TASK_PROGRESS_BY_STATUS[target_status]
    task.completed_at = None
    task.blocked_reason = None
    task.last_status_before_blocked = None
    recalculate_project_progress(db, task.project)
    target_label = TASK_STATUS_LABELS.get(target_status, target_status)
    write_project_event(
        db,
        project=task.project,
        stage=task.stage,
        task=task,
        event_type="task_rolled_back",
        actor=current_user,
        before=before,
        after=task_snapshot(task),
        message=f"回退到{target_label}：{reason}",
    )
    db.commit()
    db.refresh(task)
    return api_ok(serialize_task(task, users=_task_user_map(db, [task])))


@router.post("/admin/project-tasks/{task_id}/block", summary="标记项目任务阻塞")
async def block_project_task(
    task_id: int,
    payload: TaskBlockIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    return api_ok(
        _change_task_status(
            db,
            task,
            current_user,
            next_status="blocked",
            event_type="task_blocked",
            message=payload.reason,
            reason=payload.reason,
            next_action=payload.next_action,
        )
    )


@router.post("/admin/project-tasks/{task_id}/unblock", summary="解除项目任务阻塞")
async def unblock_project_task(
    task_id: int,
    payload: TaskUnblockIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    if task.status != "blocked":
        return api_ok(serialize_task(task, users=_task_user_map(db, [task])))
    resolution = clean_text(payload.resolution, 2000)
    if not resolution:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNBLOCK_RESOLUTION_REQUIRED")
    next_status = task.last_status_before_blocked or "progressing"
    return api_ok(
        _change_task_status(
            db,
            task,
            current_user,
            next_status=next_status,
            event_type="task_unblocked",
            message=f"解除阻塞：{resolution}",
            next_action=payload.next_action,
        )
    )


@router.post("/admin/project-tasks/{task_id}/cancel", summary="取消项目任务")
async def cancel_project_task(
    task_id: int,
    payload: ReasonIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    return api_ok(
        _change_task_status(
            db,
            task,
            current_user,
            next_status="cancelled",
            event_type="task_cancelled",
            message=clean_text(payload.reason, 2000) or "取消项目任务",
        )
    )


@router.get("/admin/project-tasks/{task_id}/evidences", summary="查询项目任务成果证据")
async def list_project_task_evidences(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    evidences = (
        db.query(ProjectTaskEvidence)
        .filter(ProjectTaskEvidence.task_id == task.id, ProjectTaskEvidence.status == "active")
        .order_by(ProjectTaskEvidence.created_at.desc(), ProjectTaskEvidence.id.desc())
        .all()
    )
    summary = task_evidence_summary(task)
    users = _evidence_user_map(db, evidences)
    files = _evidence_file_map(db, evidences)
    items = [serialize_evidence(evidence, users=users, files=files) for evidence in evidences]
    return api_ok(
        {
            **summary,
            "task_id": task.id,
            "requirement": task_evidence_requirement(task),
            "items": items,
            "evidence_count": len(items),
        }
    )


@router.post("/admin/project-tasks/{task_id}/evidences", summary="新增项目任务成果证据")
async def create_project_task_evidence(
    task_id: int,
    payload: ProjectTaskEvidenceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    task = get_accessible_task(db, task_id, current_user)
    require_task_mutation(current_user, task)
    evidence_type = normalize_evidence_type(payload.evidence_type)
    title = clean_text(payload.title, 255)
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_EVIDENCE_TITLE_REQUIRED")
    description = clean_text(payload.description, 4000)
    file_object_id = clean_text(payload.file_object_id, 36)
    external_url = clean_text(payload.external_url, 1024)
    external_provider = clean_text(payload.external_provider, 64)
    if evidence_type == "file":
        if not file_object_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_EVIDENCE_FILE_REQUIRED")
        file_obj = db.query(FileObject).filter(FileObject.file_id == file_object_id).first()
        if not file_obj:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_EVIDENCE_FILE_NOT_FOUND")
        if file_obj.username != current_user.username and not can_manage_project(current_user, task.project):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FILE_ACCESS_DENIED")
    elif evidence_type == "link":
        if not external_url:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_EVIDENCE_LINK_REQUIRED")
        if not external_url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TASK_EVIDENCE_LINK")
        file_object_id = None
    else:
        if not description:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_TASK_EVIDENCE_TEXT_REQUIRED")
        file_object_id = None
        external_url = None
        external_provider = None
    evidence = ProjectTaskEvidence(
        project_id=task.project_id,
        stage_id=task.stage_id,
        task_id=task.id,
        evidence_type=evidence_type,
        title=title,
        description=description,
        file_object_id=file_object_id,
        external_url=external_url,
        external_provider=external_provider,
        requirement_snapshot=task_evidence_requirement(task),
        status="active",
        created_by=current_user.id,
    )
    db.add(evidence)
    db.flush()
    serialized = serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files=_evidence_file_map(db, [evidence]))
    write_project_event(
        db,
        project=task.project,
        stage=task.stage,
        task=task,
        event_type="task_evidence_added",
        actor=current_user,
        after=serialized,
        message=f"新增成果证据：{title}",
    )
    db.commit()
    db.refresh(evidence)
    return api_ok(serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files=_evidence_file_map(db, [evidence])))


@router.delete("/admin/project-task-evidences/{evidence_id}", summary="删除项目任务成果证据")
async def remove_project_task_evidence(
    evidence_id: int,
    payload: ReasonIn | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    evidence = _get_accessible_evidence(db, evidence_id, current_user)
    task = evidence.task
    if not can_manage_project(current_user, task.project) and evidence.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    if evidence.status == "removed":
        return api_ok(serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files=_evidence_file_map(db, [evidence])))
    before = serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files=_evidence_file_map(db, [evidence]))
    reason = clean_text(payload.reason if payload else None, 2000) or "删除成果证据"
    evidence.status = "removed"
    evidence.removed_by = current_user.id
    evidence.removed_at = now_local_naive()
    evidence.remove_reason = reason
    db.flush()
    after = serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files=_evidence_file_map(db, [evidence]))
    write_project_event(
        db,
        project=task.project,
        stage=task.stage,
        task=task,
        event_type="task_evidence_removed",
        actor=current_user,
        before=before,
        after=after,
        message=f"删除成果证据：{reason}",
    )
    db.commit()
    db.refresh(evidence)
    return api_ok(serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files=_evidence_file_map(db, [evidence])))


@router.get("/admin/project-task-evidences/{evidence_id}/download_url", summary="生成项目任务成果文件下载链接")
async def get_project_task_evidence_download_url(
    evidence_id: int,
    expires_seconds: Optional[int] = Query(None, ge=60, le=604800),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    evidence = _get_accessible_evidence(db, evidence_id, current_user)
    if evidence.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_TASK_EVIDENCE_NOT_FOUND")
    if evidence.evidence_type != "file" or not evidence.file_object_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROJECT_TASK_EVIDENCE_NOT_FILE")
    file_obj = db.query(FileObject).filter(FileObject.file_id == evidence.file_object_id).first()
    if not file_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_TASK_EVIDENCE_FILE_NOT_FOUND")
    try:
        expires_seconds = expires_seconds or settings.minio_presigned_expire_seconds
        download_url = await asyncio.to_thread(generate_presigned_get_url, file_obj.object_name, expires_seconds, file_obj.bucket)
    except StorageDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"生成临时下载链接失败: {str(exc)}") from exc
    return api_ok(
        {
            **serialize_evidence(evidence, users=_evidence_user_map(db, [evidence]), files={file_obj.file_id: file_obj}),
            "download_url": download_url,
            "expires_in_seconds": expires_seconds,
        }
    )


@router.get("/admin/project-tasks/my", summary="我的项目任务")
async def list_my_project_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    require_project_access(current_user)
    query = db.query(ProjectTask).filter(ProjectTask.owner_user_id == current_user.id)
    if status_filter:
        statuses = [normalize_task_status(item) for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(ProjectTask.status.in_(statuses))
    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            pattern = f"%{keyword_value}%"
            query = query.filter(or_(ProjectTask.title.like(pattern), ProjectTask.description.like(pattern), ProjectTask.next_action.like(pattern)))
    total = query.count()
    tasks = query.order_by(ProjectTask.due_at.asc(), ProjectTask.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return api_page([serialize_task(task, users=_task_user_map(db, tasks)) for task in tasks], total=total, page=page, page_size=page_size)


@router.get("/admin/projects/{project_id}/events", summary="查询项目动态")
async def list_project_events(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    project = get_accessible_project(db, project_id, current_user)
    events = (
        db.query(ProjectTaskEvent)
        .filter(ProjectTaskEvent.project_id == project.id)
        .order_by(ProjectTaskEvent.id.desc())
        .limit(200)
        .all()
    )
    return api_ok([serialize_event(event, users=_event_user_map(db, events)) for event in events])


@router.get("/admin/dashboard/projects", summary="项目进度看板聚合")
async def get_project_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _ensure_dashboard_feature_enabled()
    if not (can_view_all_projects(current_user) or can_manage_project_module(current_user)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    projects = accessible_project_query(db, current_user).all()
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id.in_([project.id for project in projects])).all() if projects else []
    for project in projects:
        recalculate_project_progress(db, project)
    db.commit()
    open_tasks = [task for task in tasks if task.status not in {"done", "cancelled"}]
    active_projects = [project for project in projects if project.status in {"planning", "active", "paused"}]
    stage_counts: dict[str, int] = {}
    for project in active_projects:
        current = next((stage for stage in sorted(project.stages, key=lambda item: item.sort_order) if int(stage.progress_percent or 0) < 100), None)
        if current:
            stage_counts[current.stage_name] = stage_counts.get(current.stage_name, 0) + 1
    owner_counts: dict[int, dict] = {}
    users = _project_user_map(db, projects)
    for project in projects:
        row = owner_counts.setdefault(
            project.project_manager_id,
            {"project_manager_id": project.project_manager_id, "username": users.get(project.project_manager_id).username if users.get(project.project_manager_id) else str(project.project_manager_id), "project_count": 0, "blocked_count": 0, "delayed_count": 0},
        )
        row["project_count"] += 1
        if project.risk_level == "blocked":
            row["blocked_count"] += 1
        if project.risk_level == "delayed":
            row["delayed_count"] += 1
    avg_progress = int(round(sum(int(project.progress_percent or 0) for project in projects) / len(projects))) if projects else 0
    return api_ok(
        {
            "project_count": len(projects),
            "active_count": sum(1 for project in projects if project.status in {"planning", "active", "paused"}),
            "completed_count": sum(1 for project in projects if project.status == "completed"),
            "blocked_count": sum(1 for project in projects if project.risk_level == "blocked"),
            "delayed_count": sum(1 for project in projects if project.risk_level == "delayed"),
            "warning_count": sum(1 for project in projects if project.risk_level == "warning"),
            "avg_progress_percent": avg_progress,
            "task_count": len([task for task in tasks if task.status != "cancelled"]),
            "open_task_count": len(open_tasks),
            "done_task_count": sum(1 for task in tasks if task.status == "done"),
            "blocked_task_count": sum(1 for task in tasks if task.status == "blocked"),
            "overdue_task_count": sum(1 for task in open_tasks if task.due_at and task.due_at < now_local_naive()),
            "stage_distribution": [{"stage_name": key, "count": value} for key, value in sorted(stage_counts.items())],
            "by_project_manager": sorted(owner_counts.values(), key=lambda item: (-item["project_count"], item["username"])),
            "empty_state": len(projects) == 0,
        }
    )
