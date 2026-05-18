import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.logging import get_trace_id
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.file_object import FileObject
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User
from app.services.file_storage import StorageDisabledError, store_file_bytes
from app.services.quote_dispatcher import dispatch_quote_job
from app.services.rbac import has_admin_role
from app.services.quote_job_readability import (
    apply_job_duration,
    apply_job_failure,
    apply_job_request_summary,
    event_rows_from_json,
    serialize_event_row,
)
from app.services.quote_job_runner import (
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    append_job_event,
    mark_stale_quote_jobs,
)


router = APIRouter()
logger = logging.getLogger(__name__)


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


def _serialize_job(job: QuoteJob, include_events: bool = True, include_result: bool = True) -> dict:
    data = {
        "job_id": job.job_id,
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
        "created_at": _format_dt(job.created_at),
        "updated_at": _format_dt(job.updated_at),
        "finished_at": _format_dt(job.finished_at),
        "error_message": job.error_message,
    }
    if include_events:
        data["events"] = [serialize_event_row(item) for item in job.events] or event_rows_from_json(job.events_json)
    if include_result:
        data["result"] = _decode_json(job.result_json, None)
    return data


def _get_accessible_job(job_id: str, current_user: User, db: Session) -> QuoteJob:
    job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="报价任务不存在")
    if not has_admin_role(current_user) and job.username != current_user.username:
        raise HTTPException(status_code=404, detail="报价任务不存在")
    return job


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
        trace_id=get_trace_id() or uuid.uuid4().hex,
    )
    apply_job_request_summary(job)
    append_job_event(job, "queued", "报价任务已进入队列", trace_id=job.trace_id, stage="queued")
    db.add(job)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(QuoteJob)
    if not has_admin_role(current_user):
        query = query.filter(QuoteJob.username == current_user.username)
    elif username:
        query = query.filter(QuoteJob.username == username)

    if status_filter:
        statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(QuoteJob.status.in_(statuses))

    total = query.count()
    jobs = (
        query.order_by(QuoteJob.created_at.desc(), QuoteJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [_serialize_job(job, include_events=False, include_result=False) for job in jobs],
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
    data = _serialize_job(_get_accessible_job(job_id, current_user, db))
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
        trace_id=get_trace_id() or uuid.uuid4().hex,
    )
    apply_job_request_summary(retry_job)
    append_job_event(
        retry_job,
        "queued",
        f"报价任务已由 {source_job.job_id} 重试创建",
        trace_id=retry_job.trace_id,
        stage="queued",
        source_job_id=source_job.job_id,
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
                job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).first()
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
