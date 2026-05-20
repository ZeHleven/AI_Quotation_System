from __future__ import annotations

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.meeting import MeetingNote, MeetingNoteRevision, TaskDraft
from app.models.user import User
from app.services.execution_tasks import _now_local_naive, clean_text, format_dt, parse_local_datetime, serialize_execution_task
from app.services.meetings import (
    accept_task_draft,
    can_view_all_meetings,
    content_sha256,
    create_manual_draft,
    ensure_assignee,
    extract_and_store_drafts,
    get_accessible_meeting_note,
    require_meeting_access,
    require_meeting_owner_or_admin,
    reject_task_draft,
    serialize_meeting_detail,
    serialize_meeting_note,
    serialize_task_draft,
    user_map,
)


router = APIRouter()


class MeetingCreate(BaseModel):
    content: str


class MeetingUpdate(BaseModel):
    content: str


class MeetingCancel(BaseModel):
    reason: str


class MeetingRevisionCreate(BaseModel):
    content: str
    reason: str


class ManualTaskDraftCreate(BaseModel):
    title: str
    source_sentence: Optional[str] = None
    assignee_id: Optional[int] = None
    due_at: Optional[str] = None
    notes: Optional[str] = None


class ConfirmTaskDraftItem(BaseModel):
    draft_id: int
    action: Literal["accept", "reject"] = "accept"
    title: Optional[str] = None
    assignee_id: Optional[int] = None
    due_at: Optional[str] = None
    notes: Optional[str] = None
    rejection_reason: Optional[str] = None


class ConfirmTaskDrafts(BaseModel):
    drafts: list[ConfirmTaskDraftItem]


def _ensure_meeting_enabled() -> None:
    if not settings.feature_meeting_ai:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None) or request.headers.get("x-trace-id")


@router.post("/meetings", summary="保存会议纪要并提取任务草稿")
async def create_meeting_note(
    payload: MeetingCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    require_meeting_access(current_user)
    content = clean_text(payload.content, 20000)
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CONTENT_REQUIRED")

    note = MeetingNote(
        content=content,
        status="draft",
        created_by=current_user.id,
        ai_status="pending",
        trace_id=_trace_id(request),
    )
    db.add(note)
    db.flush()
    extract_and_store_drafts(
        db,
        note=note,
        content=content,
        username=current_user.username,
        trace_id=note.trace_id,
    )
    db.commit()
    db.refresh(note)
    return api_ok(serialize_meeting_detail(db, note))


@router.get("/meetings", summary="查询会议纪要")
async def list_meeting_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    require_meeting_access(current_user)
    query = db.query(MeetingNote)
    if not can_view_all_meetings(current_user):
        query = query.filter(MeetingNote.created_by == current_user.id)
    if status_filter:
        statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
        query = query.filter(MeetingNote.status.in_(statuses))
    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            pattern = f"%{keyword_value}%"
            query = query.filter(or_(MeetingNote.content.like(pattern), MeetingNote.extraction_error.like(pattern)))
    total = query.count()
    notes = (
        query.order_by(MeetingNote.created_at.desc(), MeetingNote.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    users = user_map(db, [note.created_by for note in notes] + [note.confirmed_by for note in notes])
    return api_page(
        [serialize_meeting_note(note, users=users, include_content=False) for note in notes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/meetings/{meeting_id}", summary="查询会议纪要详情")
async def get_meeting_note(
    meeting_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    note = get_accessible_meeting_note(db, meeting_id, current_user)
    return api_ok(serialize_meeting_detail(db, note))


@router.patch("/meetings/{meeting_id}", summary="草稿阶段更正纪要并重新提取")
async def update_meeting_note(
    meeting_id: int,
    payload: MeetingUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    note = get_accessible_meeting_note(db, meeting_id, current_user)
    require_meeting_owner_or_admin(note, current_user)
    if note.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MEETING_NOTE_NOT_DRAFT")
    content = clean_text(payload.content, 20000)
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CONTENT_REQUIRED")
    for draft in note.drafts:
        if draft.status == "pending_review":
            draft.status = "rejected"
            draft.rejection_reason = "meeting_reextracted"
    note.content = content
    note.ai_status = "pending"
    note.trace_id = _trace_id(request)
    extract_and_store_drafts(
        db,
        note=note,
        content=content,
        username=current_user.username,
        trace_id=note.trace_id,
    )
    db.commit()
    db.refresh(note)
    return api_ok(serialize_meeting_detail(db, note))


@router.post("/meetings/{meeting_id}/revisions", summary="已确认纪要创建更正版本")
async def create_meeting_revision(
    meeting_id: int,
    payload: MeetingRevisionCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    note = get_accessible_meeting_note(db, meeting_id, current_user)
    if note.status not in {"confirmed", "revised"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MEETING_NOTE_NOT_CONFIRMED")
    content = clean_text(payload.content, 20000)
    reason = clean_text(payload.reason, 1000)
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CONTENT_REQUIRED")
    if not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    latest_content = note.revisions[-1].content if note.revisions else note.content
    revision = MeetingNoteRevision(
        meeting_note_id=note.id,
        content=content,
        reason=reason,
        created_by=current_user.id,
        previous_content_sha256=content_sha256(latest_content),
        trace_id=_trace_id(request),
    )
    db.add(revision)
    db.flush()
    note.status = "revised"
    note.ai_status = "pending"
    extract_and_store_drafts(
        db,
        note=note,
        content=content,
        username=current_user.username,
        revision=revision,
        trace_id=revision.trace_id,
    )
    db.commit()
    db.refresh(note)
    return api_ok(serialize_meeting_detail(db, note))


@router.post("/meetings/{meeting_id}/drafts", summary="人工补充任务草稿")
async def add_manual_task_draft(
    meeting_id: int,
    payload: ManualTaskDraftCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    note = get_accessible_meeting_note(db, meeting_id, current_user)
    require_meeting_owner_or_admin(note, current_user)
    if note.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MEETING_NOTE_CANCELLED")
    if payload.assignee_id is not None:
        ensure_assignee(db, payload.assignee_id)
    draft = create_manual_draft(
        db,
        note=note,
        title=payload.title,
        source_sentence=payload.source_sentence,
        assignee_id=payload.assignee_id,
        due_at=payload.due_at,
        notes=payload.notes,
        trace_id=_trace_id(request),
    )
    db.commit()
    db.refresh(draft)
    users = user_map(db, [draft.suggested_assignee_id, draft.confirmed_assignee_id])
    return api_ok(serialize_task_draft(draft, users=users))


@router.post("/meetings/{meeting_id}/cancel", summary="作废草稿会议纪要")
async def cancel_meeting_note(
    meeting_id: int,
    payload: MeetingCancel,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    note = get_accessible_meeting_note(db, meeting_id, current_user)
    require_meeting_owner_or_admin(note, current_user)
    reason = clean_text(payload.reason, 1000)
    if not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    if note.status == "cancelled":
        return api_ok(serialize_meeting_detail(db, note))
    if note.status in {"confirmed", "revised"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MEETING_NOTE_CONFIRMED")
    note.status = "cancelled"
    note.cancelled_at = _now_local_naive()
    note.cancelled_by = current_user.id
    note.cancel_reason = reason
    for draft in note.drafts:
        if draft.status == "pending_review":
            draft.status = "rejected"
            draft.rejection_reason = "meeting_cancelled"
    if not note.trace_id:
        note.trace_id = _trace_id(request)
    db.commit()
    db.refresh(note)
    return api_ok(serialize_meeting_detail(db, note))


@router.post("/meetings/{meeting_id}/confirm-tasks", summary="确认草稿并写入执行任务")
async def confirm_meeting_tasks(
    meeting_id: int,
    payload: ConfirmTaskDrafts,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_meeting_enabled()
    note = get_accessible_meeting_note(db, meeting_id, current_user)
    require_meeting_owner_or_admin(note, current_user)
    if note.status == "cancelled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MEETING_NOTE_CANCELLED")
    if not payload.drafts:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="DRAFTS_REQUIRED")

    task_results = []
    accepted_task_ids = []
    for item in payload.drafts:
        draft = (
            db.query(TaskDraft)
            .filter(TaskDraft.id == item.draft_id, TaskDraft.meeting_note_id == note.id)
            .first()
        )
        if not draft:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TASK_DRAFT_NOT_FOUND")
        if item.action == "reject":
            reject_task_draft(draft, item.rejection_reason)
            continue
        if item.assignee_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ASSIGNEE_REQUIRED")
        due_at_value = item.due_at or (format_dt(draft.suggested_due_at) if draft.suggested_due_at else None)
        due_at = parse_local_datetime(due_at_value, field_name="due_at") if due_at_value else None
        if due_at is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="DUE_AT_REQUIRED")
        assignee = ensure_assignee(db, item.assignee_id)
        task = accept_task_draft(
            db,
            draft=draft,
            title=item.title or draft.title,
            assignee=assignee,
            due_at=due_at,
            notes=item.notes,
            operator=current_user,
            request=request,
        )
        accepted_task_ids.append(task.id)
        task_results.append(serialize_execution_task(task, assignee=assignee))

    if accepted_task_ids:
        if note.status == "draft":
            note.confirmed_at = _now_local_naive()
            note.confirmed_by = current_user.id
        note.status = "confirmed"
        for revision in note.revisions:
            if revision.task_ids_json is None and any(draft.revision_id == revision.id for draft in note.drafts):
                revision.task_ids_json = json.dumps(accepted_task_ids, ensure_ascii=False)
    db.commit()
    db.refresh(note)
    return api_ok({"meeting": serialize_meeting_detail(db, note), "tasks": task_results})
