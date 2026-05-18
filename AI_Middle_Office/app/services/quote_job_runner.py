import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import reset_trace_id, set_trace_id
from app.models.client_inquiry import ClientInquiry  # noqa: F401 - load FK metadata for Celery workers
from app.models.file_object import FileObject
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.file_storage import get_object_bytes
from app.services.model_gateway import call_glm_vision_extract, post_json_via_gateway
from app.services.quote_feedback import safe_record_ai_preview
from app.services.quote_job_readability import (
    apply_job_duration,
    apply_job_failure,
    apply_job_result_summary,
    create_job_event_from_payload,
)
from app.services.quote_helpers import sign_payload


logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"queued", "running"}
RETRYABLE_STATUSES = {"failed", "canceled", "timed_out"}
TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "timed_out"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started_at: float) -> int:
    return max(1, int(round((time.perf_counter() - started_at) * 1000)))


def _apply_runtime_duration(job: QuoteJob, started_at: float) -> None:
    job.duration_ms = _elapsed_ms(started_at)


def _event_timestamp() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_events(raw_events: Optional[str]) -> list[dict]:
    if not raw_events:
        return []
    try:
        events = json.loads(raw_events)
        return events if isinstance(events, list) else []
    except Exception:
        return []


def append_job_event(
    job: QuoteJob,
    status_name: str,
    message: str,
    trace_id: Optional[str] = None,
    **extra: Any,
) -> None:
    events = _decode_events(job.events_json)
    event = {
        "status": status_name,
        "message": message,
        "trace_id": trace_id or job.trace_id,
        "created_at": _event_timestamp(),
    }
    event.update(extra)
    events.append(event)
    job.events_json = json.dumps(events, ensure_ascii=False)
    job.events.append(create_job_event_from_payload(job, len(events), event))
    job.updated_at = _utcnow()


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def mark_stale_quote_jobs(db, timeout_minutes: int) -> list[QuoteJob]:
    cutoff = _utcnow() - timedelta(minutes=timeout_minutes)
    stale_jobs = []
    candidates = db.query(QuoteJob).filter(QuoteJob.status.in_(ACTIVE_STATUSES)).all()

    for job in candidates:
        last_seen = _as_utc(job.updated_at) or _as_utc(job.created_at)
        if not last_seen or last_seen > cutoff:
            continue

        job.status = "timed_out"
        job.stage = "timeout"
        job.error_message = f"任务超过 {timeout_minutes} 分钟未完成，已标记超时"
        job.finished_at = _utcnow()
        apply_job_failure(job, "timeout")
        apply_job_duration(job)
        append_job_event(job, "error", f"❌ {job.error_message}", trace_id=job.trace_id, stage="timeout")
        stale_jobs.append(job)

    if stale_jobs:
        db.commit()
    return stale_jobs


async def _load_job_file_content(job: QuoteJob, db) -> Optional[bytes]:
    if job.file_object_id:
        file_obj = db.query(FileObject).filter(FileObject.file_id == job.file_object_id).first()
        if not file_obj:
            raise RuntimeError(f"报价附件不存在: {job.file_object_id}")
        return await asyncio.to_thread(get_object_bytes, file_obj.object_name, file_obj.bucket)
    if job.file_base64:
        return base64.b64decode(job.file_base64)
    return None


async def _iter_quote_events(
    *,
    username: str,
    message: str,
    file_content: Optional[bytes],
    mime_type: Optional[str],
    filename: Optional[str],
) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    final_query = message or ""

    yield (
        "processing",
        "[API Gateway] 📡 安全握手成功，异步任务已接管本次报价请求...",
        {"stage": "api_gateway"},
    )
    await asyncio.sleep(0.2)

    if file_content:
        if "pdf" in (mime_type or "").lower():
            yield (
                "error",
                "❌ [格式校验] 拦截：国内引擎暂只支持图片输入，请截图重试",
                {"stage": "validation"},
            )
            return

        yield (
            "processing",
            f"[Vision Module] 📸 正在驱动 GLM-4V 多模态大模型扫描附件: {filename}...",
            {"stage": "vision"},
        )

        base64_data = base64.b64encode(file_content).decode("utf-8")
        try:
            extracted_text = await call_glm_vision_extract(
                base64_data,
                mime_type or "",
                username=username,
            )
        except Exception as glm_err:
            logger.exception(
                "vision_model_failed",
                extra={"username": username, "event": "vision_model_failed"},
            )
            yield (
                "error",
                f"❌ [Vision Module] GLM-4V 调用失败: {str(glm_err)}",
                {"stage": "vision"},
            )
            return

        if not extracted_text:
            yield (
                "error",
                "❌ [Vision Module] GLM-4V 返回空内容，请重试",
                {"stage": "vision"},
            )
            return

        final_query = f"{final_query} [从文件识别到的内容]: {extracted_text}".replace("\n", "；").replace("\r", "")
        yield (
            "processing",
            "[Vision Module] ✅ 提取完毕，已成功结构化二维图纸特征！",
            {"stage": "vision"},
        )
        await asyncio.sleep(0.2)
    elif final_query.strip():
        yield (
            "processing",
            "[Text Module] 📝 识别纯文本指令，正在执行语义清洗...",
            {"stage": "text"},
        )
        await asyncio.sleep(0.2)

    if not final_query.strip():
        yield (
            "error",
            "❌ 请输入业务指令或上传清单图片",
            {"stage": "validation"},
        )
        return

    yield (
        "processing",
        "[RAG & Agent] 🔍 正在穿透企业知识库寻找刚性底价并驱动专家大脑...",
        {"stage": "n8n"},
    )

    payload = {"text": {"content": final_query}, "conversationId": str(uuid.uuid4())}
    try:
        response = await post_json_via_gateway(
            provider="n8n",
            model="dify-deepseek",
            endpoint_type="quote_calc",
            url=settings.n8n_webhook_url_calc,
            json_payload=payload,
            headers=sign_payload(payload),
            timeout=180,
            username=username,
        )
    except httpx.TimeoutException:
        logger.exception("quote_request_timeout", extra={"username": username, "event": "quote_request_timeout"})
        yield (
            "error",
            "❌ [n8n Workflow] 严重超时：请检查 Dify 模型是否拥堵挂起",
            {"stage": "n8n"},
        )
        return

    if response.status_code != 200:
        try:
            error_detail = response.json().get("message", "未知错误")
        except Exception:
            error_detail = f"状态码 {response.status_code}，响应体为空"
        logger.error(
            "n8n_workflow_failed",
            extra={"username": username, "event": "n8n_workflow_failed", "status_code": response.status_code},
        )
        yield (
            "error",
            f"❌ [n8n Workflow] 中断: 底层算价引擎抛出异常 -> {error_detail}",
            {"stage": "n8n"},
        )
        return

    try:
        calc_result = response.json()
    except Exception:
        body_preview = response.text[:500].strip() if response.text else "<empty>"
        logger.exception(
            "n8n_response_parse_failed",
            extra={"username": username, "event": "n8n_response_parse_failed"},
        )
        yield (
            "error",
            "❌ [n8n Workflow] 响应体解析失败（HTTP 200）\n"
            f"实际返回内容：{body_preview}\n"
            "→ 请确认 N8N budget-calc 工作流末尾有 Respond to Webhook 节点且 Response Body 设为 JSON",
            {"stage": "n8n"},
        )
        return

    logger.info("quote_request_finished", extra={"username": username, "event": "quote_request_finished"})
    yield (
        "preview",
        "[n8n Workflow] ✅ AI 预审数据已就绪，请人工复核！",
        {"stage": "completed", "data": calc_result},
    )


async def run_quote_job_async(job_id: str) -> None:
    db = SessionLocal()
    trace_token = None
    run_started_at = time.perf_counter()
    try:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).first()
        if not job or job.status in TERMINAL_STATUSES:
            return

        trace_token = set_trace_id(job.trace_id or uuid.uuid4().hex)
        user = db.query(User).filter(User.username == job.username).first()
        if not user or not user.is_active:
            job.status = "failed"
            job.stage = "auth"
            job.error_message = "用户不存在或已禁用"
            job.finished_at = _utcnow()
            apply_job_failure(job, "auth")
            _apply_runtime_duration(job, run_started_at)
            append_job_event(job, "error", "❌ 登录状态已失效，请重新登录", trace_id=job.trace_id, stage="auth")
            db.commit()
            return

        if user.quota <= 0:
            job.status = "failed"
            job.stage = "quota"
            job.error_message = "AI 调用额度已耗尽"
            job.finished_at = _utcnow()
            apply_job_failure(job, "quota")
            _apply_runtime_duration(job, run_started_at)
            append_job_event(job, "error", "❌ 您的 AI 调用额度已耗尽，请联系管理员充值", trace_id=job.trace_id, stage="quota")
            db.commit()
            return

        job.status = "running"
        job.stage = "started"
        append_job_event(job, "processing", "异步报价任务已开始执行", trace_id=job.trace_id, stage="started")
        db.commit()

        try:
            file_content = await _load_job_file_content(job, db)
        except Exception as exc:
            logger.exception("quote_job_file_load_failed", extra={"job_id": job.job_id, "event": "quote_job_file_load_failed"})
            job.status = "failed"
            job.stage = "file_load"
            job.error_message = f"报价附件读取失败: {str(exc)}"
            job.finished_at = _utcnow()
            apply_job_failure(job, "file_load")
            _apply_runtime_duration(job, run_started_at)
            append_job_event(job, "error", f"❌ {job.error_message}", trace_id=job.trace_id, stage="file_load")
            db.commit()
            return

        async for status_name, message, extra in _iter_quote_events(
            username=job.username,
            message=job.message or "",
            file_content=file_content,
            mime_type=job.file_mime_type,
            filename=job.file_name,
        ):
            db.refresh(job)
            if job.status in TERMINAL_STATUSES:
                return

            job.stage = extra.get("stage", job.stage)
            append_job_event(job, status_name, message, trace_id=job.trace_id, **extra)

            if status_name == "preview":
                job.status = "succeeded"
                result_payload = extra.get("data")
                job.result_json = json.dumps(result_payload, ensure_ascii=False)
                job.finished_at = _utcnow()
                apply_job_result_summary(job, result_payload)
                _apply_runtime_duration(job, run_started_at)
                user.quota -= 1
                safe_record_ai_preview(
                    db,
                    username=job.username,
                    ai_payload=extra.get("data"),
                    quote_job=job,
                    source="async_job",
                    query_text=job.message,
                )
            elif status_name == "error":
                job.status = "failed"
                job.error_message = message
                job.finished_at = _utcnow()
                apply_job_failure(job, extra.get("stage"))
                _apply_runtime_duration(job, run_started_at)

            db.commit()
            if job.status in TERMINAL_STATUSES:
                break
    except Exception as exc:
        logger.exception("quote_job_crashed", extra={"job_id": job_id, "event": "quote_job_crashed"})
        try:
            job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).first()
            if job:
                job.status = "failed"
                job.stage = "crashed"
                job.error_message = str(exc)
                job.finished_at = _utcnow()
                apply_job_failure(job, "crashed")
                _apply_runtime_duration(job, run_started_at)
                append_job_event(job, "error", f"❌ [API Gateway] 异步任务崩溃: {str(exc)}", trace_id=job.trace_id, stage="crashed")
                db.commit()
        except Exception:
            db.rollback()
    finally:
        if trace_token is not None:
            reset_trace_id(trace_token)
        db.close()


def run_quote_job(job_id: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_quote_job_async(job_id))
        return
    raise RuntimeError("run_quote_job cannot be called synchronously inside an active event loop")
