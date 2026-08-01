import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.logging import get_trace_id
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.client_inquiry import ClientInquiry
from app.models.file_object import FileObject
from app.models.quote_cost_evidence import QuoteCostEvidence
from app.models.quote_feedback import QuoteFeedback
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User
from app.services.file_storage import StorageDisabledError, store_file_bytes
from app.services.client_inquiries import _parse_local_datetime, create_or_reuse_client_inquiry, serialize_client_inquiry
from app.services.quote_dispatcher import dispatch_quote_job
from app.services.rbac import has_admin_role, has_any_role
from app.services.quote_job_readability import (
    apply_job_duration,
    apply_job_failure,
    apply_job_request_summary,
    event_rows_from_json,
    serialize_event_row,
)
from app.services.quote_job_numbers import (
    find_quote_job_by_identifier,
    quote_job_number,
    quote_job_number_database_id,
)
from app.services.quote_job_runner import (
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    _load_job_file_content,
    append_job_event,
    mark_stale_quote_jobs,
)
from app.services.quote_budget_workspace import (
    QuoteBudgetWorkspaceError,
    materialize_quote_budget_workspace,
)
from app.services.quote_review import (
    build_quote_review_detail,
    parse_requirement_rows_payload,
    save_quote_requirement_rows,
)
from app.services.quote_preview_drafts import (
    delete_preview_drafts,
    discard_preview_draft,
    get_preview_draft,
    mark_preview_draft_pushed,
    save_preview_draft,
)


router = APIRouter()
logger = logging.getLogger(__name__)


class PreviewDraftSaveIn(BaseModel):
    draft: dict[str, Any] = Field(default_factory=dict)
    quote_id: Optional[str] = None
    trace_id: Optional[str] = None


class PreviewDraftBatchDeleteIn(BaseModel):
    draft_ids: list[int] = Field(default_factory=list)


def _decode_json(raw_value: Optional[str], fallback):
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except Exception:
        return fallback


def _format_dt(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _serialize_history_summary(record: Optional[QuoteHistory]) -> Optional[dict]:
    if not record:
        return None
    return {
        "id": record.id,
        "quote_id": record.quote_id,
        "confirmed_by": record.confirmed_by,
        "pushed_to_dingtalk": record.pushed_to_dingtalk,
        "created_at": _format_dt(record.created_at),
        "total_amount": record.total_amount,
        "item_count": record.item_count,
        "display_title": record.display_title,
        "project_summary": record.project_summary,
    }


def _serialize_feedback_summary(record: Optional[QuoteFeedback]) -> Optional[dict]:
    if not record:
        return None
    return {
        "id": record.id,
        "quote_id": record.quote_id,
        "quote_job_id": record.quote_job_id,
        "status": record.status,
        "rejected": record.rejected,
        "rejection_reason": record.rejection_reason,
        "change_summary": record.change_summary,
        "reviewed_by": record.reviewed_by,
        "created_at": _format_dt(record.created_at),
        "confirmed_at": _format_dt(record.confirmed_at),
        "rejected_at": _format_dt(record.rejected_at),
    }


def _quote_job_context_maps(
    db: Session,
    jobs: list[QuoteJob],
) -> tuple[dict[str, ClientInquiry], dict[str, QuoteHistory], dict[str, int], dict[str, QuoteFeedback]]:
    job_ids = [job.job_id for job in jobs]
    inquiry_ids = [job.client_inquiry_id for job in jobs if job.client_inquiry_id]
    inquiries_by_id: dict[str, ClientInquiry] = {}
    histories_by_job_id: dict[str, QuoteHistory] = {}
    evidence_counts_by_job_id: dict[str, int] = {}
    feedback_by_job_id: dict[str, QuoteFeedback] = {}

    if inquiry_ids:
        inquiries = (
            db.query(ClientInquiry)
            .filter(ClientInquiry.inquiry_id.in_(inquiry_ids))
            .all()
        )
        inquiries_by_id = {inquiry.inquiry_id: inquiry for inquiry in inquiries}

    if job_ids:
        histories = (
            db.query(QuoteHistory)
            .filter(QuoteHistory.quote_job_id.in_(job_ids))
            .order_by(QuoteHistory.created_at.desc(), QuoteHistory.id.desc())
            .all()
        )
        for history in histories:
            if history.quote_job_id and history.quote_job_id not in histories_by_job_id:
                histories_by_job_id[history.quote_job_id] = history

        feedback_rows = (
            db.query(QuoteFeedback)
            .filter(QuoteFeedback.quote_job_id.in_(job_ids))
            .order_by(QuoteFeedback.updated_at.desc(), QuoteFeedback.id.desc())
            .all()
        )
        for feedback in feedback_rows:
            if feedback.quote_job_id and feedback.quote_job_id not in feedback_by_job_id:
                feedback_by_job_id[feedback.quote_job_id] = feedback

        evidence_counts_by_job_id = {
            quote_job_id: count
            for quote_job_id, count in (
                db.query(QuoteCostEvidence.quote_job_id, func.count(QuoteCostEvidence.id))
                .filter(QuoteCostEvidence.quote_job_id.in_(job_ids))
                .group_by(QuoteCostEvidence.quote_job_id)
                .all()
            )
            if quote_job_id
        }

    return inquiries_by_id, histories_by_job_id, evidence_counts_by_job_id, feedback_by_job_id


def _serialize_job(
    job: QuoteJob,
    include_events: bool = True,
    include_result: bool = True,
    client_inquiry: Optional[ClientInquiry] = None,
    history: Optional[QuoteHistory] = None,
    feedback: Optional[QuoteFeedback] = None,
    cost_evidence_count: int = 0,
) -> dict:
    data = {
        "job_id": job.job_id,
        "job_number": quote_job_number(job),
        "username": job.username,
        "status": job.status,
        "stage": job.stage,
        "trace_id": job.trace_id,
        "celery_task_id": job.celery_task_id,
        "message_preview": (job.message or "")[:120],
        "request_summary": job.request_summary or (job.message or "")[:180],
        "file_name": job.file_name,
        "source_file_name": job.source_file_name or job.file_name,
        "file_object_id": job.file_object_id,
        "result_total_amount": job.result_total_amount,
        "result_item_count": job.result_item_count,
        "preview_project_names": [
            item.strip() for item in (job.preview_project_names or "").split(",") if item.strip()
        ],
        "duration_ms": job.duration_ms,
        "failure_stage": job.failure_stage,
        "client_inquiry_id": job.client_inquiry_id,
        "budget_project_id": job.budget_project_id,
        "budget_pricing_draft_id": job.budget_pricing_draft_id,
        "budget_workspace_source_sha256": job.budget_workspace_source_sha256,
        "budget_workspace_synced_at": _format_dt(job.budget_workspace_synced_at),
        "created_at": _format_dt(job.created_at),
        "updated_at": _format_dt(job.updated_at),
        "finished_at": _format_dt(job.finished_at),
        "error_message": job.error_message,
        "client_inquiry": serialize_client_inquiry(client_inquiry) if client_inquiry else None,
        "history": _serialize_history_summary(history),
        "feedback": _serialize_feedback_summary(feedback),
        "cost_evidence_count": cost_evidence_count,
    }
    if include_events:
        data["events"] = [serialize_event_row(item) for item in job.events] or event_rows_from_json(job.events_json)
    if include_result:
        data["result"] = _decode_json(job.result_json, None)
    return data


def _can_view_quote_operations(user: User) -> bool:
    return has_admin_role(user) or has_any_role(user, {"quote_operator"})


def _get_accessible_job(
    job_id: str,
    current_user: User,
    db: Session,
    *,
    allow_quote_operations: bool = False,
) -> QuoteJob:
    job = find_quote_job_by_identifier(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="报价任务不存在")
    can_view_all = allow_quote_operations and _can_view_quote_operations(current_user)
    if not has_admin_role(current_user) and not can_view_all and job.username != current_user.username:
        raise HTTPException(status_code=404, detail="报价任务不存在")
    return job


def _serialize_job_with_context(
    db: Session,
    job: QuoteJob,
    *,
    include_events: bool = True,
    include_result: bool = True,
) -> dict:
    inquiries_by_id, histories_by_job_id, evidence_counts_by_job_id, feedback_by_job_id = _quote_job_context_maps(db, [job])
    return _serialize_job(
        job,
        include_events=include_events,
        include_result=include_result,
        client_inquiry=inquiries_by_id.get(job.client_inquiry_id),
        history=histories_by_job_id.get(job.job_id),
        feedback=feedback_by_job_id.get(job.job_id),
        cost_evidence_count=evidence_counts_by_job_id.get(job.job_id, 0),
    )


def _dispatch_and_store(job: QuoteJob, db: Session) -> None:
    try:
        celery_task_id = dispatch_quote_job(job.job_id)
        if celery_task_id:
            job.celery_task_id = celery_task_id
            db.commit()
            db.refresh(job)
    except Exception as exc:
        logger.exception("quote_job_dispatch_failed", extra={"job_id": job.job_id, "event": "quote_job_dispatch_failed"})
        job.status = "failed"
        job.stage = "dispatch"
        job.error_message = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        apply_job_failure(job, "dispatch")
        apply_job_duration(job)
        append_job_event(job, "error", f"❌ 异步报价任务派发失败: {str(exc)}", trace_id=job.trace_id, stage="dispatch")
        db.commit()
        db.refresh(job)


def _revoke_celery_task(job: QuoteJob) -> None:
    if not job.celery_task_id:
        return
    try:
        from app.tasks.celery_app import celery_app

        if celery_app is not None:
            celery_app.control.revoke(job.celery_task_id, terminate=False)
    except Exception:
        logger.exception("quote_job_revoke_failed", extra={"job_id": job.job_id, "event": "quote_job_revoke_failed"})


async def _persist_quote_attachment(
    *,
    file: UploadFile,
    file_content: bytes,
    current_user: User,
    db: Session,
) -> tuple[Optional[str], Optional[str]]:
    max_bytes = settings.minio_max_upload_mb * 1024 * 1024
    if len(file_content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.minio_max_upload_mb}MB 上限")

    if not settings.minio_enabled:
        return None, base64.b64encode(file_content).decode("utf-8")

    try:
        stored = await asyncio.to_thread(
            store_file_bytes,
            content=file_content,
            original_filename=file.filename or "quote_attachment",
            content_type=file.content_type,
            username=current_user.username,
            purpose="quote_job_attachment",
        )
    except StorageDisabledError:
        return None, base64.b64encode(file_content).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"报价附件上传 MinIO 失败: {str(exc)}") from exc

    file_obj = FileObject(
        file_id=str(uuid.uuid4()),
        username=current_user.username,
        purpose="quote_job_attachment",
        bucket=stored["bucket"],
        object_name=stored["object_name"],
        original_filename=file.filename or "quote_attachment",
        content_type=stored["content_type"],
        size_bytes=stored["size_bytes"],
    )
    db.add(file_obj)
    db.flush()
    return file_obj.file_id, None


@router.post("/quote/jobs", status_code=status.HTTP_202_ACCEPTED, summary="创建异步报价任务")
async def create_quote_job(
    message: str = Form(None),
    file: UploadFile = File(None),
    client_inquiry_id: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    client_name: Optional[str] = Form(None),
    client_phone: Optional[str] = Form(None),
    inquiry_time: Optional[str] = Form(None),
    time_source: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    requirement_rows_json: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.quota <= 0:
        raise HTTPException(status_code=403, detail="您的 AI 调用额度已耗尽，请联系管理员充值")

    file_content = await file.read() if file else None
    file_object_id = None
    file_base64 = None
    if file and file_content:
        file_object_id, file_base64 = await _persist_quote_attachment(
            file=file,
            file_content=file_content,
            current_user=current_user,
            db=db,
        )

    inquiry = None
    if settings.feature_client_inquiry:
        inquiry = create_or_reuse_client_inquiry(
            db,
            current_user=current_user,
            client_inquiry_id=client_inquiry_id,
            source=source,
            client_name=client_name,
            client_phone=client_phone,
            inquiry_time=inquiry_time,
            time_source=time_source,
            notes=notes,
        )

    try:
        requirement_rows = parse_requirement_rows_payload(requirement_rows_json)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = QuoteJob(
        job_id=str(uuid.uuid4()),
        username=current_user.username,
        status="queued",
        stage="queued",
        message=message or "",
        file_name=file.filename if file else None,
        file_mime_type=file.content_type if file else None,
        file_object_id=file_object_id,
        file_base64=file_base64,
        client_inquiry_id=inquiry.inquiry_id if inquiry else None,
        trace_id=get_trace_id() or uuid.uuid4().hex,
    )
    if inquiry and not inquiry.first_quote_job_id:
        inquiry.first_quote_job_id = job.job_id
    apply_job_request_summary(job)
    append_job_event(job, "queued", "报价任务已进入队列", trace_id=job.trace_id, stage="queued")
    db.add(job)
    db.flush()
    save_quote_requirement_rows(db, quote_job_id=job.job_id, rows=requirement_rows)
    db.commit()
    db.refresh(job)
    _dispatch_and_store(job, db)

    data = _serialize_job(job)
    return api_ok(data)


@router.get("/quote/jobs", summary="查询报价任务列表（本人；admin 可查全队列）")
async def list_quote_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    username: Optional[str] = None,
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(QuoteJob)
    joined_inquiry = False
    if not _can_view_quote_operations(current_user):
        query = query.filter(QuoteJob.username == current_user.username)
    elif username:
        query = query.filter(QuoteJob.username == username)

    if status_filter:
        statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(QuoteJob.status.in_(statuses))

    start = _parse_local_datetime(date_from)
    end = _parse_local_datetime(date_to)
    if start:
        query = query.filter(QuoteJob.created_at >= start)
    if end:
        query = query.filter(QuoteJob.created_at <= end)

    if source:
        query = query.outerjoin(
            ClientInquiry,
            QuoteJob.client_inquiry_id == ClientInquiry.inquiry_id,
        )
        joined_inquiry = True
        query = query.filter(ClientInquiry.source == source.strip())

    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            if not joined_inquiry:
                query = query.outerjoin(
                    ClientInquiry,
                    QuoteJob.client_inquiry_id == ClientInquiry.inquiry_id,
                )
                joined_inquiry = True
            pattern = f"%{keyword_value}%"
            job_number_id = quote_job_number_database_id(keyword_value)
            keyword_conditions = [
                QuoteJob.job_id.like(pattern),
                QuoteJob.username.like(pattern),
                QuoteJob.message.like(pattern),
                QuoteJob.request_summary.like(pattern),
                QuoteJob.source_file_name.like(pattern),
                QuoteJob.file_name.like(pattern),
                ClientInquiry.source.like(pattern),
                ClientInquiry.client_name.like(pattern),
                ClientInquiry.client_phone.like(pattern),
            ]
            if job_number_id is not None:
                keyword_conditions.append(QuoteJob.id == job_number_id)
            query = query.filter(
                or_(
                    *keyword_conditions,
                )
            )

    total = query.count()
    jobs = (
        query.order_by(QuoteJob.created_at.desc(), QuoteJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    inquiries_by_id, histories_by_job_id, evidence_counts_by_job_id, feedback_by_job_id = _quote_job_context_maps(db, jobs)
    return api_page(
        [
            _serialize_job(
                job,
                include_events=False,
                include_result=False,
                client_inquiry=inquiries_by_id.get(job.client_inquiry_id),
                history=histories_by_job_id.get(job.job_id),
                feedback=feedback_by_job_id.get(job.job_id),
                cost_evidence_count=evidence_counts_by_job_id.get(job.job_id, 0),
            )
            for job in jobs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/quote/jobs/{job_id}", summary="查询异步报价任务状态")
async def get_quote_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = _serialize_job_with_context(
        db,
        _get_accessible_job(job_id, current_user, db, allow_quote_operations=True),
    )
    return api_ok(data)


@router.get("/quote/jobs/{job_id}/review-detail", summary="查询报价预审条目与确认清单对账")
async def get_quote_job_review_detail(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db, allow_quote_operations=True)
    return api_ok(build_quote_review_detail(db, job))


@router.post("/quote/jobs/{job_id}/budget-workspace", summary="将对话报价同步到详细报价工作台")
async def materialize_quote_job_budget_workspace(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db, allow_quote_operations=True)
    try:
        try:
            file_content = await _load_job_file_content(job, db)
        except Exception:
            file_content = None
            logger.warning(
                "quote_budget_workspace_source_file_unavailable",
                extra={"job_id": job.job_id, "event": "quote_budget_workspace_source_file_unavailable"},
            )
        data = materialize_quote_budget_workspace(
            db,
            job=job,
            current_user=current_user,
            file_content=file_content,
        )
        append_job_event(
            job,
            "budget_workspace_ready",
            "详细报价工作台已准备完成",
            trace_id=job.trace_id,
            stage="budget_workspace_ready",
            budget_project_id=data["budget_project_id"],
            budget_pricing_draft_id=data["budget_pricing_draft_id"],
            synced=data["synced"],
        )
        db.commit()
        db.refresh(job)
        return api_ok(data)
    except QuoteBudgetWorkspaceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception(
            "quote_budget_workspace_materialize_failed",
            extra={"job_id": job.job_id, "event": "quote_budget_workspace_materialize_failed"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "QUOTE_BUDGET_WORKSPACE_FAILED"},
        ) from exc


@router.get("/quote/jobs/{job_id}/preview-draft", summary="查询报价预审草稿")
async def get_quote_preview_draft(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db)
    return api_ok(get_preview_draft(db, job.job_id))


@router.put("/quote/jobs/{job_id}/preview-draft", summary="保存报价预审草稿")
async def save_quote_preview_draft(
    job_id: str,
    payload: PreviewDraftSaveIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db)
    data = save_preview_draft(
        db,
        job=job,
        user=current_user,
        draft=payload.draft,
        quote_id=payload.quote_id,
        trace_id=payload.trace_id,
    )
    db.commit()
    return api_ok(data)


@router.post("/quote/jobs/{job_id}/preview-draft/discard", summary="放弃报价预审草稿")
async def discard_quote_preview_draft(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db)
    data = discard_preview_draft(db, quote_job_id=job.job_id)
    db.commit()
    return api_ok(data)


@router.post("/quote/jobs/{job_id}/preview-draft/mark-pushed", summary="标记报价预审草稿已推送")
async def mark_quote_preview_draft_pushed(
    job_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db)
    data = mark_preview_draft_pushed(db, quote_job_id=job.job_id) or get_preview_draft(db, job.job_id)
    db.commit()
    return api_ok(data)


@router.post("/quote/preview-drafts/batch-delete", summary="批量删除报价预审草稿")
async def batch_delete_quote_preview_drafts(
    payload: PreviewDraftBatchDeleteIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = delete_preview_drafts(db, draft_ids=payload.draft_ids, user=current_user)
    db.commit()
    return api_ok(data)


@router.post("/quote/jobs/{job_id}/cancel", summary="取消报价任务")
async def cancel_quote_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_accessible_job(job_id, current_user, db)
    if job.status == "canceled":
        data = _serialize_job(job)
        return api_ok(data)
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"任务已结束，当前状态为 {job.status}，无法取消")

    _revoke_celery_task(job)
    job.status = "canceled"
    job.stage = "canceled"
    job.error_message = "任务已取消"
    job.finished_at = datetime.now(timezone.utc)
    apply_job_failure(job, "canceled")
    apply_job_duration(job)
    append_job_event(job, "error", "⏹️ 报价任务已取消", trace_id=job.trace_id, stage="canceled")
    db.commit()
    db.refresh(job)
    data = _serialize_job(job)
    return api_ok(data)


@router.post("/quote/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED, summary="重试失败/取消/超时的报价任务")
async def retry_quote_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_job = _get_accessible_job(job_id, current_user, db)
    if source_job.status not in RETRYABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"当前状态为 {source_job.status}，仅失败/取消/超时任务可重试")

    target_user = db.query(User).filter(User.username == source_job.username).first()
    if not target_user or not target_user.is_active:
        raise HTTPException(status_code=400, detail="原任务用户不存在或已禁用，无法重试")
    if target_user.quota <= 0:
        raise HTTPException(status_code=403, detail="原任务用户 AI 调用额度已耗尽，无法重试")

    retry_job = QuoteJob(
        job_id=str(uuid.uuid4()),
        username=source_job.username,
        status="queued",
        stage="queued",
        message=source_job.message or "",
        file_name=source_job.file_name,
        file_mime_type=source_job.file_mime_type,
        file_object_id=source_job.file_object_id,
        file_base64=source_job.file_base64,
        client_inquiry_id=source_job.client_inquiry_id,
        budget_project_id=source_job.budget_project_id,
        trace_id=get_trace_id() or uuid.uuid4().hex,
    )
    apply_job_request_summary(retry_job)
    append_job_event(
        retry_job,
        "queued",
        f"报价任务已由 {quote_job_number(source_job) or source_job.job_id} 重试创建",
        trace_id=retry_job.trace_id,
        stage="queued",
        source_job_id=source_job.job_id,
        source_job_number=quote_job_number(source_job),
    )
    db.add(retry_job)
    db.commit()
    db.refresh(retry_job)
    _dispatch_and_store(retry_job, db)
    data = _serialize_job(retry_job)
    return api_ok(data)


@router.post("/admin/quote/jobs/mark_timeouts", summary="管理员标记超时任务")
async def mark_quote_job_timeouts(
    timeout_minutes: int = Query(30, ge=1, le=1440),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stale_jobs = mark_stale_quote_jobs(db, timeout_minutes)
    data = [_serialize_job(job, include_events=False, include_result=False) for job in stale_jobs]
    return api_ok(data, timeout_minutes=timeout_minutes, marked_count=len(stale_jobs))


@router.get("/quote/jobs/{job_id}/events", summary="订阅异步报价任务事件")
async def stream_quote_job_events(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    username = current_user.username
    is_admin = has_admin_role(current_user)

    async def event_generator():
        sent_count = 0
        while True:
            db = SessionLocal()
            try:
                job = find_quote_job_by_identifier(db, job_id)
                if not job or (not is_admin and job.username != username):
                    payload = {"status": "error", "message": "报价任务不存在", "trace_id": get_trace_id()}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    return

                events = _decode_json(job.events_json, [])
                for event in events[sent_count:]:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                sent_count = len(events)

                if job.status in TERMINAL_STATUSES:
                    return
            finally:
                db.close()

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
