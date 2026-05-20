from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Iterable

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.execution_task import ExecutionTask
from app.models.meeting import MeetingNote, MeetingNoteRevision, TaskDraft
from app.models.user import User
from app.services.execution_tasks import (
    _now_local_naive,
    clean_text,
    format_dt,
    parse_local_datetime,
    serialize_execution_task,
    task_snapshot,
    write_execution_event,
)
from app.services.model_gateway import record_model_call
from app.services.rbac import has_admin_role, has_any_role


MEETING_PROMPT_VERSION = "meeting_extract@1.0.0+20260519"
VALID_MEETING_STATUSES = {"draft", "confirmed", "cancelled", "revised"}
VALID_DRAFT_STATUSES = {"pending_review", "accepted", "rejected"}
TASK_KEYWORDS = (
    "负责",
    "跟进",
    "确认",
    "完成",
    "安排",
    "处理",
    "复核",
    "联系",
    "提交",
    "整理",
    "检查",
    "推进",
    "落实",
)


class ExtractedTaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_sentence: str = Field(min_length=1)
    suggested_assignee_id: int | None = None
    suggested_due_at: str | None = None
    notes: str | None = None


class MeetingExtractionResult(BaseModel):
    tasks: list[ExtractedTaskDraft]


def can_access_meetings(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "staff", "manager"})


def can_view_all_meetings(user: User) -> bool:
    return has_admin_role(user)


def require_meeting_access(user: User) -> None:
    if not can_access_meetings(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_meeting_owner_or_admin(note: MeetingNote, user: User) -> None:
    require_meeting_access(user)
    if can_view_all_meetings(user):
        return
    if note.created_by != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def get_accessible_meeting_note(db: Session, meeting_id: int, user: User) -> MeetingNote:
    require_meeting_access(user)
    query = db.query(MeetingNote).filter(MeetingNote.id == meeting_id)
    if not can_view_all_meetings(user):
        query = query.filter(MeetingNote.created_by == user.id)
    note = query.first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MEETING_NOTE_NOT_FOUND")
    return note


def normalize_title(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip()).lower()
    return normalized[:255]


def content_sha256(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _trace_id_from_request(request: Request | None) -> str | None:
    if request is None:
        return None
    return getattr(request.state, "trace_id", None) or request.headers.get("x-trace-id")


def _split_sentences(content: str) -> list[str]:
    raw_parts = re.split(r"[\n。；;]+", content or "")
    sentences = []
    for raw_part in raw_parts:
        sentence = re.sub(r"\s+", " ", raw_part).strip(" -\t\r，,、")
        if len(sentence) >= 4:
            sentences.append(sentence)
    return sentences


def _title_from_sentence(sentence: str) -> str:
    title = re.sub(r"^(会议决定|待办|任务|安排|请|需要|[-•*\d.、\s]+)", "", sentence).strip()
    return (title or sentence)[:120]


def _suggest_due_at(sentence: str) -> datetime | None:
    base = _now_local_naive().replace(hour=18, minute=0, second=0, microsecond=0)
    if "后天" in sentence:
        return base + timedelta(days=2)
    if "明天" in sentence:
        return base + timedelta(days=1)
    if "今天" in sentence or "今晚" in sentence:
        return base
    return None


def _suggest_assignee_id(sentence: str, users: Iterable[User]) -> int | None:
    for user in users:
        if user.username and user.username in sentence:
            return user.id
    return None


def extract_task_drafts(db: Session, content: str) -> MeetingExtractionResult:
    active_users = db.query(User).filter(User.is_active.is_(True)).all()
    tasks: list[ExtractedTaskDraft] = []
    for sentence in _split_sentences(content):
        if not any(keyword in sentence for keyword in TASK_KEYWORDS):
            continue
        due_at = _suggest_due_at(sentence)
        tasks.append(
            ExtractedTaskDraft(
                title=_title_from_sentence(sentence),
                source_sentence=sentence,
                suggested_assignee_id=_suggest_assignee_id(sentence, active_users),
                suggested_due_at=format_dt(due_at),
                notes="由会议纪要结构化提取，确认前请人工复核。",
            )
        )
    return MeetingExtractionResult(tasks=tasks[:12])


def _reject_pending_drafts(note: MeetingNote, reason: str) -> None:
    for draft in note.drafts:
        if draft.status == "pending_review":
            draft.status = "rejected"
            draft.rejection_reason = reason


def create_drafts_from_extraction(
    db: Session,
    *,
    note: MeetingNote,
    extraction: MeetingExtractionResult,
    revision: MeetingNoteRevision | None = None,
    trace_id: str | None = None,
) -> list[TaskDraft]:
    drafts: list[TaskDraft] = []
    for item in extraction.tasks:
        title = clean_text(item.title, 255)
        source_sentence = clean_text(item.source_sentence, 2000)
        if not title or not source_sentence:
            continue
        suggested_due_at = parse_local_datetime(item.suggested_due_at, field_name="suggested_due_at")
        draft = TaskDraft(
            meeting_note_id=note.id,
            revision_id=revision.id if revision else None,
            title=title,
            normalized_title=normalize_title(title),
            status="pending_review",
            source_sentence=source_sentence,
            suggested_assignee_id=item.suggested_assignee_id,
            suggested_due_at=suggested_due_at,
            notes=clean_text(item.notes, 1000),
            prompt_version=MEETING_PROMPT_VERSION,
            trace_id=trace_id,
        )
        db.add(draft)
        drafts.append(draft)
    return drafts


def extract_and_store_drafts(
    db: Session,
    *,
    note: MeetingNote,
    content: str,
    username: str,
    revision: MeetingNoteRevision | None = None,
    trace_id: str | None = None,
) -> list[TaskDraft]:
    started = datetime.now()
    try:
        extraction = extract_task_drafts(db, content)
        payload = extraction.model_dump() if hasattr(extraction, "model_dump") else extraction.dict()
        output_chars = len(json.dumps(payload, ensure_ascii=False))
        drafts = create_drafts_from_extraction(db, note=note, extraction=extraction, revision=revision, trace_id=trace_id)
        note.ai_status = "extracted" if drafts else "no_tasks"
        note.prompt_version = MEETING_PROMPT_VERSION
        note.extraction_error = None
        record_model_call(
            provider="local",
            model="meeting_extract_rules",
            endpoint_type="meeting_extract",
            status="success",
            username=username,
            trace_id=trace_id,
            latency_ms=(datetime.now() - started).total_seconds() * 1000,
            input_chars=len(content or ""),
            output_chars=output_chars,
        )
        return drafts
    except Exception as exc:
        note.ai_status = "failed"
        note.extraction_error = str(exc)[:1000]
        record_model_call(
            provider="local",
            model="meeting_extract_rules",
            endpoint_type="meeting_extract",
            status="error",
            username=username,
            trace_id=trace_id,
            latency_ms=(datetime.now() - started).total_seconds() * 1000,
            input_chars=len(content or ""),
            error_message=str(exc),
        )
        return []


def user_map(db: Session, ids: Iterable[int | None]) -> dict[int, User]:
    user_ids = {int(user_id) for user_id in ids if user_id}
    if not user_ids:
        return {}
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    return {user.id: user for user in users}


def serialize_task_draft(draft: TaskDraft, *, users: dict[int, User] | None = None) -> dict:
    users = users or {}
    suggested_user = users.get(draft.suggested_assignee_id)
    confirmed_user = users.get(draft.confirmed_assignee_id)
    return {
        "id": draft.id,
        "created_at": format_dt(draft.created_at),
        "updated_at": format_dt(draft.updated_at),
        "meeting_note_id": draft.meeting_note_id,
        "revision_id": draft.revision_id,
        "title": draft.title,
        "status": draft.status,
        "source_sentence": draft.source_sentence,
        "suggested_assignee_id": draft.suggested_assignee_id,
        "suggested_assignee_username": suggested_user.username if suggested_user else None,
        "suggested_due_at": format_dt(draft.suggested_due_at),
        "confirmed_assignee_id": draft.confirmed_assignee_id,
        "confirmed_assignee_username": confirmed_user.username if confirmed_user else None,
        "confirmed_due_at": format_dt(draft.confirmed_due_at),
        "accepted_task_id": draft.accepted_task_id,
        "notes": draft.notes,
        "rejection_reason": draft.rejection_reason,
        "prompt_version": draft.prompt_version,
        "trace_id": draft.trace_id,
    }


def serialize_revision(revision: MeetingNoteRevision) -> dict:
    return {
        "id": revision.id,
        "created_at": format_dt(revision.created_at),
        "meeting_note_id": revision.meeting_note_id,
        "reason": revision.reason,
        "created_by": revision.created_by,
        "previous_content_sha256": revision.previous_content_sha256,
        "trace_id": revision.trace_id,
        "task_ids_json": revision.task_ids_json,
    }


def serialize_meeting_note(note: MeetingNote, *, users: dict[int, User] | None = None, include_content: bool = True) -> dict:
    users = users or {}
    created_by_user = users.get(note.created_by)
    confirmed_by_user = users.get(note.confirmed_by)
    data = {
        "id": note.id,
        "created_at": format_dt(note.created_at),
        "updated_at": format_dt(note.updated_at),
        "status": note.status,
        "created_by": note.created_by,
        "created_by_username": created_by_user.username if created_by_user else None,
        "confirmed_at": format_dt(note.confirmed_at),
        "confirmed_by": note.confirmed_by,
        "confirmed_by_username": confirmed_by_user.username if confirmed_by_user else None,
        "cancelled_at": format_dt(note.cancelled_at),
        "cancel_reason": note.cancel_reason,
        "ai_status": note.ai_status,
        "prompt_version": note.prompt_version,
        "extraction_error": note.extraction_error,
        "trace_id": note.trace_id,
        "draft_count": len(note.drafts or []),
        "pending_draft_count": len([draft for draft in note.drafts or [] if draft.status == "pending_review"]),
        "accepted_draft_count": len([draft for draft in note.drafts or [] if draft.status == "accepted"]),
        "revision_count": len(note.revisions or []),
    }
    if include_content:
        data["content"] = note.content
    else:
        data["content"] = (note.content or "")[:160]
    return data


def serialize_meeting_detail(db: Session, note: MeetingNote) -> dict:
    ids = [note.created_by, note.confirmed_by, note.cancelled_by]
    ids.extend(user_id for draft in note.drafts for user_id in (draft.suggested_assignee_id, draft.confirmed_assignee_id))
    users = user_map(db, ids)
    data = serialize_meeting_note(note, users=users, include_content=True)
    data["drafts"] = [serialize_task_draft(draft, users=users) for draft in note.drafts]
    data["revisions"] = [serialize_revision(revision) for revision in note.revisions]
    return data


def create_manual_draft(
    db: Session,
    *,
    note: MeetingNote,
    title: str,
    source_sentence: str | None,
    assignee_id: int | None,
    due_at: str | None,
    notes: str | None,
    trace_id: str | None,
) -> TaskDraft:
    cleaned_title = clean_text(title, 255)
    if not cleaned_title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="TITLE_REQUIRED")
    parsed_due_at = parse_local_datetime(due_at, field_name="due_at") if due_at else None
    draft = TaskDraft(
        meeting_note_id=note.id,
        title=cleaned_title,
        normalized_title=normalize_title(cleaned_title),
        status="pending_review",
        source_sentence=clean_text(source_sentence, 2000) or "人工补充",
        suggested_assignee_id=assignee_id,
        suggested_due_at=parsed_due_at,
        notes=clean_text(notes, 1000),
        prompt_version=MEETING_PROMPT_VERSION,
        trace_id=trace_id,
    )
    db.add(draft)
    return draft


def find_duplicate_accepted_draft(
    db: Session,
    *,
    draft: TaskDraft,
    assignee_id: int,
    due_at: datetime,
) -> TaskDraft | None:
    return (
        db.query(TaskDraft)
        .filter(
            TaskDraft.id != draft.id,
            TaskDraft.meeting_note_id == draft.meeting_note_id,
            TaskDraft.status == "accepted",
            TaskDraft.normalized_title == normalize_title(draft.title),
            TaskDraft.confirmed_assignee_id == assignee_id,
            TaskDraft.confirmed_due_at == due_at,
        )
        .first()
    )


def accept_task_draft(
    db: Session,
    *,
    draft: TaskDraft,
    title: str,
    assignee: User,
    due_at: datetime,
    notes: str | None,
    operator: User,
    request: Request,
) -> ExecutionTask:
    if draft.status == "accepted":
        if draft.accepted_task_id:
            existing = db.query(ExecutionTask).filter(ExecutionTask.id == draft.accepted_task_id).first()
            if existing:
                return existing
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="TASK_DRAFT_ACCEPTED_WITHOUT_TASK")
    if draft.status == "rejected":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="TASK_DRAFT_REJECTED")

    cleaned_title = clean_text(title, 255)
    if not cleaned_title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="TITLE_REQUIRED")
    draft.title = cleaned_title
    draft.normalized_title = normalize_title(cleaned_title)
    duplicate = find_duplicate_accepted_draft(db, draft=draft, assignee_id=assignee.id, due_at=due_at)
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="DUPLICATE_MEETING_TASK_DRAFT")

    task_notes = "\n".join(
        item
        for item in [
            clean_text(draft.notes, 1000),
            clean_text(notes, 1000),
            f"来源句子：{draft.source_sentence}",
        ]
        if item
    )
    task = ExecutionTask(
        title=cleaned_title,
        source="meeting",
        source_ref_id=str(draft.meeting_note_id),
        assignee_id=assignee.id,
        due_at=due_at,
        status="pending",
        notes=task_notes,
    )
    db.add(task)
    db.flush()
    draft.status = "accepted"
    draft.confirmed_assignee_id = assignee.id
    draft.confirmed_due_at = due_at
    draft.accepted_task_id = task.id
    write_execution_event(
        db,
        task=task,
        event_type="created_from_meeting",
        operator=operator,
        before=None,
        after=task_snapshot(task),
        request=request,
    )
    return task


def reject_task_draft(draft: TaskDraft, reason: str | None) -> None:
    if draft.status == "accepted":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="TASK_DRAFT_ALREADY_ACCEPTED")
    draft.status = "rejected"
    draft.rejection_reason = clean_text(reason, 1000) or "人工驳回"


def ensure_assignee(db: Session, assignee_id: int) -> User:
    user = db.query(User).filter(User.id == assignee_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_ASSIGNEE")
    return user
