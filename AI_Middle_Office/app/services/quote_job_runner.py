import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import reset_trace_id, set_trace_id
from app.models.client_inquiry import ClientInquiry  # noqa: F401 - load FK metadata for Celery workers
from app.models.file_object import FileObject
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.file_storage import get_object_bytes
from app.services.model_gateway import call_glm_vision_extract, post_json_via_gateway
from app.services.quote_cost_context import (
    build_cost_context_fallback_quote,
    cost_context_references_as_source_rows,
    safe_append_quote_cost_context,
)
from app.services.quote_cost_matching import safe_enrich_quote_payload_with_cost_refs
from app.services.quote_excel_parser import (
    QuoteExcelParseError,
    is_legacy_excel_file,
    is_quote_excel_file,
    parse_quote_excel_bytes,
)
from app.services.quote_feedback import safe_record_ai_preview
from app.services.quote_job_readability import (
    apply_job_duration,
    apply_job_failure,
    apply_job_result_summary,
    create_job_event_from_payload,
)
from app.services.quote_history import project_details
from app.services.quote_helpers import normalize_quote_request_text, sign_payload
from app.services.quote_review import (
    attach_requirement_integrity_summary,
    build_requirement_rows_quote_message,
    merge_requirement_preview_rows,
    missing_requirement_rows_for_preview,
    quote_requirement_rows,
    requirement_rows_as_source_rows,
)


logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"queued", "running"}
RETRYABLE_STATUSES = {"failed", "canceled", "timed_out"}
TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "timed_out"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started_at: float) -> int:
    return max(1, int(round((time.perf_counter() - started_at) * 1000)))


def _quote_n8n_timeout_seconds() -> int:
    return max(1, int(getattr(settings, "quote_n8n_timeout_seconds", 180) or 180))


def _quote_job_heartbeat_interval_seconds() -> float:
    try:
        interval = float(getattr(settings, "quote_job_heartbeat_interval_seconds", 15.0) or 0)
    except (TypeError, ValueError):
        interval = 15.0
    return max(0.0, interval)


def _quote_requirement_batch_size() -> int:
    try:
        value = int(getattr(settings, "quote_requirement_batch_size", 20) or 20)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, 50))


def _quote_requirement_batch_threshold() -> int:
    try:
        value = int(getattr(settings, "quote_requirement_batch_threshold", 30) or 30)
    except (TypeError, ValueError):
        value = 30
    return max(1, value)


def _quote_requirement_batch_retry_count() -> int:
    try:
        value = int(getattr(settings, "quote_requirement_batch_retry_count", 1) or 0)
    except (TypeError, ValueError):
        value = 1
    return max(0, min(value, 2))


def _bind_requirement_rows_to_payload(payload: Any, requirement_rows: list[Any]) -> Any:
    if not requirement_rows or not isinstance(payload, dict):
        return payload
    preview_rows = project_details(payload)
    merged_rows, merge_summary = merge_requirement_preview_rows(requirement_rows, preview_rows)
    return {
        **payload,
        "project_details": merged_rows,
        "requirement_merge_summary": merge_summary,
    }


def _chunk_requirement_rows(rows: list[Any], size: int) -> list[list[Any]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


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


async def _iter_batched_requirement_quote_events(
    *,
    username: str,
    base_query: str,
    db: Optional[Session],
    requirement_rows: list[Any],
) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    batch_size = _quote_requirement_batch_size()
    retry_count = _quote_requirement_batch_retry_count()
    batches = _chunk_requirement_rows(requirement_rows, batch_size)
    full_source_rows = requirement_rows_as_source_rows(requirement_rows)
    all_preview_rows: list[dict[str, Any]] = []
    batch_reports: list[dict[str, Any]] = []

    yield (
        "processing",
        f"[Requirement Batch] 确认清单共 {len(requirement_rows)} 行，已切分为 {len(batches)} 批逐批报价。",
        {
            "stage": "requirement_batch",
            "requirement_row_count": len(requirement_rows),
            "batch_size": batch_size,
            "batch_count": len(batches),
        },
    )

    for batch_index, batch_rows in enumerate(batches, start=1):
        pending_rows = list(batch_rows)
        attempt = 0
        while pending_rows and attempt <= retry_count:
            is_retry = attempt > 0
            attempt_label = "补报" if is_retry else "报价"
            batch_query = build_requirement_rows_quote_message(base_query, pending_rows)
            batch_source_rows = requirement_rows_as_source_rows(pending_rows)
            batch_query, cost_context = safe_append_quote_cost_context(db, batch_query, source_rows=batch_source_rows)
            if cost_context.matched_count:
                logger.info(
                    "quote_requirement_batch_cost_context_attached",
                    extra={
                        "username": username,
                        "event": "quote_requirement_batch_cost_context_attached",
                        "batch_index": batch_index,
                        "attempt": attempt + 1,
                        "matched_count": cost_context.matched_count,
                    },
                )

            yield (
                "processing",
                f"[Requirement Batch] 第 {batch_index}/{len(batches)} 批{attempt_label}中，本次 {len(pending_rows)} 行。",
                {
                    "stage": "requirement_batch",
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "attempt": attempt + 1,
                    "row_count": len(pending_rows),
                    "retry": is_retry,
                },
            )

            payload = {"text": {"content": batch_query}, "conversationId": str(uuid.uuid4())}
            request_started_at = time.perf_counter()
            heartbeat_interval = _quote_job_heartbeat_interval_seconds()
            request_task = asyncio.create_task(
                post_json_via_gateway(
                    provider="n8n",
                    model="dify-deepseek",
                    endpoint_type="quote_calc",
                    url=settings.n8n_webhook_url_calc,
                    json_payload=payload,
                    headers=sign_payload(payload),
                    timeout=_quote_n8n_timeout_seconds(),
                    username=username,
                )
            )

            response: httpx.Response | None = None
            error_detail = ""
            try:
                if heartbeat_interval <= 0:
                    response = await request_task
                else:
                    while True:
                        done, _ = await asyncio.wait({request_task}, timeout=heartbeat_interval)
                        if request_task in done:
                            response = request_task.result()
                            break
                        elapsed_seconds = max(1, int(round(time.perf_counter() - request_started_at)))
                        yield (
                            "processing",
                            f"[Requirement Batch] 第 {batch_index}/{len(batches)} 批仍在处理，已等待约 {elapsed_seconds} 秒。",
                            {
                                "stage": "requirement_batch",
                                "heartbeat": True,
                                "elapsed_seconds": elapsed_seconds,
                                "batch_index": batch_index,
                                "batch_count": len(batches),
                            },
                        )
            except httpx.TimeoutException as exc:
                logger.exception(
                    "quote_requirement_batch_timeout",
                    extra={"username": username, "event": "quote_requirement_batch_timeout", "batch_index": batch_index},
                )
                error_detail = str(exc) or "timeout"
            except Exception as exc:
                logger.exception(
                    "quote_requirement_batch_failed",
                    extra={"username": username, "event": "quote_requirement_batch_failed", "batch_index": batch_index},
                )
                error_detail = str(exc) or "request_failed"

            batch_preview_rows: list[dict[str, Any]] = []
            parse_status = "ok"
            if response is None:
                parse_status = "request_failed"
            elif response.status_code != 200:
                parse_status = "http_failed"
                try:
                    error_detail = response.json().get("message", f"HTTP {response.status_code}")
                except Exception:
                    error_detail = f"HTTP {response.status_code}"
            else:
                try:
                    calc_result = response.json()
                    batch_preview_rows = project_details(calc_result)
                except Exception:
                    parse_status = "parse_failed"
                    error_detail = (response.text or "").strip()[:300] or "empty_response"

            all_preview_rows.extend(batch_preview_rows)
            missing_rows = missing_requirement_rows_for_preview(pending_rows, batch_preview_rows)
            batch_reports.append(
                {
                    "batch_index": batch_index,
                    "attempt": attempt + 1,
                    "status": parse_status,
                    "requested_count": len(pending_rows),
                    "returned_count": len(batch_preview_rows),
                    "missing_count": len(missing_rows),
                    "error": error_detail,
                }
            )
            pending_rows = missing_rows
            attempt += 1

        if pending_rows:
            yield (
                "processing",
                f"[Requirement Batch] 第 {batch_index}/{len(batches)} 批仍有 {len(pending_rows)} 行未返回，稍后生成需人工补价的占位行。",
                {
                    "stage": "requirement_batch",
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "missing_count": len(pending_rows),
                },
            )

    merged_rows, merge_summary = merge_requirement_preview_rows(requirement_rows, all_preview_rows)
    calc_result = {
        "project_details": merged_rows,
        "requirement_batch_summary": {
            **merge_summary,
            "batch_size": batch_size,
            "batch_count": len(batches),
            "retry_count": retry_count,
            "batch_reports": batch_reports,
        },
    }
    calc_result = attach_requirement_integrity_summary(calc_result, requirement_rows)
    yield (
        "preview",
        "[Requirement Batch] 确认清单分批报价已完成，已按原确认行顺序合并预审单。",
        {"stage": "completed", "data": calc_result, "source_rows": full_source_rows},
    )


async def _iter_quote_events(
    *,
    username: str,
    message: str,
    file_content: Optional[bytes],
    mime_type: Optional[str],
    filename: Optional[str],
    db: Optional[Session] = None,
    requirement_rows: Optional[list[Any]] = None,
) -> Iterable[Tuple[str, str, Dict[str, Any]]]:
    final_query = message or ""
    excel_source_rows: list[dict[str, Any]] = []
    requirement_rows = requirement_rows or []

    yield (
        "processing",
        "[API Gateway] 📡 安全握手成功，异步任务已接管本次报价请求...",
        {"stage": "api_gateway"},
    )
    await asyncio.sleep(0.2)

    if file_content:
        if is_quote_excel_file(filename, mime_type):
            yield (
                "processing",
                f"[Excel Module] 📊 正在解析报价需求单: {filename}...",
                {"stage": "excel"},
            )
            try:
                parsed_excel = parse_quote_excel_bytes(file_content, filename=filename)
            except QuoteExcelParseError as exc:
                yield (
                    "error",
                    f"❌ [Excel Module] {str(exc)}",
                    {"stage": "excel"},
                )
                return

            excel_source_rows = list(parsed_excel.items)
            final_query = f"{final_query} [从Excel需求单解析到的内容]: {parsed_excel.text}".replace("\n", "；").replace("\r", "")
            yield (
                "processing",
                f"[Excel Module] ✅ 已解析 {parsed_excel.item_count} 行报价需求，准备进入算价流程",
                {"stage": "excel"},
            )
            await asyncio.sleep(0.2)
        elif is_legacy_excel_file(filename, mime_type):
            yield (
                "error",
                "❌ [格式校验] 暂不支持旧版 .xls，请另存为 .xlsx 后重新上传",
                {"stage": "validation"},
            )
            return
        elif "pdf" in (mime_type or "").lower():
            yield (
                "error",
                "❌ [格式校验] 拦截：国内引擎暂只支持图片输入，请截图重试",
                {"stage": "validation"},
            )
            return
        else:
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

    final_query = normalize_quote_request_text(final_query)
    if requirement_rows and len(requirement_rows) > _quote_requirement_batch_threshold():
        async for event in _iter_batched_requirement_quote_events(
            username=username,
            base_query=final_query,
            db=db,
            requirement_rows=requirement_rows,
        ):
            yield event
        return

    if requirement_rows:
        final_query = build_requirement_rows_quote_message(final_query, requirement_rows)
        if not excel_source_rows:
            excel_source_rows = requirement_rows_as_source_rows(requirement_rows)
        yield (
            "processing",
            f"[Requirement Guard] 已锁定 {len(requirement_rows)} 行确认清单，要求 AI 按行逐条返回预审报价。",
            {"stage": "requirement_guard", "requirement_row_count": len(requirement_rows)},
        )
    final_query, cost_context = safe_append_quote_cost_context(db, final_query, source_rows=excel_source_rows)
    cost_priority_source_rows = excel_source_rows or cost_context_references_as_source_rows(cost_context)
    if cost_context.matched_count:
        logger.info(
            "quote_job_cost_context_attached",
            extra={
                "username": username,
                "event": "quote_job_cost_context_attached",
                "matched_count": cost_context.matched_count,
                "active_cost_item_count": cost_context.active_cost_item_count,
            },
        )
    yield (
        "processing",
        "[RAG & Agent] 🔍 正在穿透企业知识库寻找刚性底价并驱动专家大脑...",
        {"stage": "n8n"},
    )

    payload = {"text": {"content": final_query}, "conversationId": str(uuid.uuid4())}
    request_started_at = time.perf_counter()
    heartbeat_interval = _quote_job_heartbeat_interval_seconds()
    request_task = asyncio.create_task(
        post_json_via_gateway(
            provider="n8n",
            model="dify-deepseek",
            endpoint_type="quote_calc",
            url=settings.n8n_webhook_url_calc,
            json_payload=payload,
            headers=sign_payload(payload),
            timeout=_quote_n8n_timeout_seconds(),
            username=username,
        )
    )
    try:
        if heartbeat_interval <= 0:
            response = await request_task
        else:
            while True:
                done, _ = await asyncio.wait({request_task}, timeout=heartbeat_interval)
                if request_task in done:
                    response = request_task.result()
                    break
                elapsed_seconds = max(1, int(round(time.perf_counter() - request_started_at)))
                yield (
                    "processing",
                    f"[n8n Workflow] AI 报价仍在处理中，已等待约 {elapsed_seconds} 秒，请保持页面打开。",
                    {"stage": "n8n", "heartbeat": True, "elapsed_seconds": elapsed_seconds},
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
        fallback_result = None
        if not (response.text or "").strip():
            fallback_result = build_cost_context_fallback_quote(cost_context, reason="n8n_empty_response")
            if fallback_result:
                logger.warning(
                    "n8n_empty_response_cost_context_fallback",
                    extra={
                        "username": username,
                    "event": "n8n_empty_response_cost_context_fallback",
                        "matched_count": cost_context.matched_count,
                    },
                )
                fallback_result = _bind_requirement_rows_to_payload(fallback_result, requirement_rows)
                fallback_result = attach_requirement_integrity_summary(fallback_result, requirement_rows)
                yield (
                    "preview",
                    "[Cost DB] N8N 返回空响应，已用成本库 active 底价生成预审报价，请人工复核。",
                {"stage": "completed", "data": fallback_result, "source_rows": cost_priority_source_rows},
            )
            return
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

    calc_result = _bind_requirement_rows_to_payload(calc_result, requirement_rows)
    calc_result = attach_requirement_integrity_summary(calc_result, requirement_rows)
    logger.info("quote_request_finished", extra={"username": username, "event": "quote_request_finished"})
    yield (
        "preview",
        "[n8n Workflow] ✅ AI 预审数据已就绪，请人工复核！",
        {"stage": "completed", "data": calc_result, "source_rows": cost_priority_source_rows},
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

        stored_requirement_rows = quote_requirement_rows(db, job.job_id)

        async for status_name, message, extra in _iter_quote_events(
            username=job.username,
            message=job.message or "",
            file_content=file_content,
            mime_type=job.file_mime_type,
            filename=job.file_name,
            db=db,
            requirement_rows=stored_requirement_rows,
        ):
            db.refresh(job)
            if job.status in TERMINAL_STATUSES:
                return

            job.stage = extra.get("stage", job.stage)
            if status_name == "preview":
                extra = dict(extra)
                source_rows = extra.pop("source_rows", None)
                extra["data"] = safe_enrich_quote_payload_with_cost_refs(db, extra.get("data"), source_rows=source_rows)
                extra["data"] = attach_requirement_integrity_summary(extra.get("data"), stored_requirement_rows)
            append_job_event(job, status_name, message, trace_id=job.trace_id, **extra)

            if status_name == "preview":
                job.status = "succeeded"
                result_payload = extra.get("data")
                job.result_json = json.dumps(result_payload, ensure_ascii=False)
                job.finished_at = _utcnow()
                apply_job_result_summary(job, result_payload)
                _apply_runtime_duration(job, run_started_at)
                user.quota -= 1
                db.commit()
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
            db.rollback()
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
