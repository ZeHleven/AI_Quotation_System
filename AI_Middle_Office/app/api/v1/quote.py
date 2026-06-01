import asyncio
import base64
import json
import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_trace_id, reset_trace_id, set_trace_id
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.schemas.quote import ConfirmPushRequest
from app.services.excel_service import build_excel_base64
from app.services.model_gateway import call_glm_vision_extract, post_json_via_gateway
from app.services.no_cost_draft_capture import create_no_cost_draft_items
from app.services.quote_preview_drafts import mark_preview_draft_pushed
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
from app.services.quote_feedback import record_ai_preview, record_confirmed_quote
from app.services.quote_history import create_quote_history_record, parse_amount
from app.services.quote_helpers import attach_quote_filename, normalize_quote_request_text, sign_payload
from app.services.quote_review import build_quote_review_detail
from app.services.rbac import has_admin_role


router = APIRouter()
logger = logging.getLogger(__name__)

N8N_WEBHOOK_URL_CALC = settings.n8n_webhook_url_calc
N8N_WEBHOOK_URL_PUSH = settings.n8n_webhook_url_push


def _unpriced_requirement_placeholders(details: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for index, row in enumerate(details):
        if not isinstance(row, dict):
            continue
        if not (row.get("requirement_placeholder") or row.get("quote_source") == "requirement_placeholder"):
            continue
        unit_price = parse_amount(row.get("unit_price"))
        total_price = parse_amount(row.get("total_price"))
        if unit_price is None or unit_price <= 0 or total_price is None or total_price <= 0:
            rows.append({"index": index, "project_name": row.get("project_name") or row.get("item_name")})
    return rows


def _unconfirmed_cost_candidate_rows(details: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for index, row in enumerate(details):
        if not isinstance(row, dict):
            continue
        reference = row.get("cost_reference") or row.get("costReference") or {}
        if not isinstance(reference, dict):
            continue
        if not reference.get("requires_manual_cost_candidate_confirmation"):
            continue
        if reference.get("manual_cost_candidate_confirmed") or reference.get("match_type") == "manual_selected":
            continue
        rows.append({"index": index, "project_name": row.get("project_name") or row.get("item_name")})
    return rows


def _unconfirmed_ai_rewrite_rows(details: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for index, row in enumerate(details):
        if not isinstance(row, dict):
            continue
        reference = row.get("cost_reference") or row.get("costReference") or {}
        if not isinstance(reference, dict):
            continue
        if not reference.get("requires_manual_ai_rewrite_confirmation"):
            continue
        if reference.get("manual_ai_rewrite_confirmed") or reference.get("match_type") == "manual_selected":
            continue
        rows.append({"index": index, "project_name": row.get("project_name") or row.get("item_name")})
    return rows


def _unconfirmed_ai_note_conflict_rows(details: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for index, row in enumerate(details):
        if not isinstance(row, dict):
            continue
        reference = row.get("cost_reference") or row.get("costReference") or {}
        if not isinstance(reference, dict):
            continue
        if not reference.get("requires_manual_ai_note_confirmation"):
            continue
        if reference.get("manual_ai_note_confirmed") or reference.get("match_type") == "manual_selected":
            continue
        rows.append({"index": index, "project_name": row.get("project_name") or row.get("item_name")})
    return rows


def _get_accessible_quote_job_for_push(db: Session, current_user: User, quote_job_id: str | None) -> QuoteJob | None:
    if not quote_job_id:
        return None
    job = db.query(QuoteJob).filter(QuoteJob.job_id == quote_job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="报价任务不存在")
    if not has_admin_role(current_user) and job.username != current_user.username:
        raise HTTPException(status_code=404, detail="报价任务不存在")
    return job


def _no_cost_draft_capture_enabled() -> bool:
    return bool(settings.feature_cost_db and settings.feature_no_cost_draft_capture)


def _sse_event(status_name: str, message: str, trace_id: Optional[str] = None, **extra):
    payload = {"status": status_name, "message": message, "trace_id": trace_id or get_trace_id()}
    payload.update(extra)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def analyze_image_with_domestic_ai(base64_image: str, mime_type: str):
    """利用国内智谱大模型 (GLM-4V) 提取业务信息"""
    return await call_glm_vision_extract(base64_image, mime_type, trace_id=get_trace_id())


@router.post("/chat", summary="AI 业务对话 (支持文本/图片/文档)")
async def process_chat(
        message: str = Form(None),
        file: UploadFile = File(None),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    if current_user.quota <= 0:
        raise HTTPException(status_code=403, detail="您的 AI 调用额度已耗尽，请联系管理员充值")

    logger.info("quote_request_started", extra={"username": current_user.username, "event": "quote_request_started"})
    file_content = await file.read() if file else None
    mime_type = file.content_type if file else None
    filename = file.filename if file else None
    request_trace_id = get_trace_id()

    async def event_generator():
        trace_token = set_trace_id(request_trace_id)
        yield f": {' ' * 1024}\n\n"
        final_query = message or ""
        excel_source_rows: list[dict] = []
        try:
            yield _sse_event("processing", "[API Gateway] 📡 安全握手成功，已剥离 JWT 并唤醒底层引擎...", trace_id=request_trace_id)
            await asyncio.sleep(0.5)

            if file_content:
                if is_quote_excel_file(filename, mime_type):
                    yield _sse_event("processing", f"[Excel Module] 📊 正在解析报价需求单: {filename}...", trace_id=request_trace_id)
                    try:
                        parsed_excel = parse_quote_excel_bytes(file_content, filename=filename)
                    except QuoteExcelParseError as exc:
                        yield _sse_event("error", f"❌ [Excel Module] {str(exc)}", trace_id=request_trace_id)
                        return

                    excel_source_rows = list(parsed_excel.items)
                    final_query = f"{final_query} [从Excel需求单解析到的内容]: {parsed_excel.text}".replace("\n", "；").replace("\r", "")
                    yield _sse_event(
                        "processing",
                        f"[Excel Module] ✅ 已解析 {parsed_excel.item_count} 行报价需求，准备进入算价流程",
                        trace_id=request_trace_id,
                    )
                    await asyncio.sleep(0.5)
                elif is_legacy_excel_file(filename, mime_type):
                    yield _sse_event("error", "❌ [格式校验] 暂不支持旧版 .xls，请另存为 .xlsx 后重新上传", trace_id=request_trace_id)
                    return
                elif "pdf" in (mime_type or "").lower():
                    yield _sse_event("error", "❌ [格式校验] 拦截：国内引擎暂只支持图片输入，请截图重试", trace_id=request_trace_id)
                    return
                else:
                    yield _sse_event("processing", f"[Vision Module] 📸 正在驱动 GLM-4V 多模态大模型扫描附件: {filename}...", trace_id=request_trace_id)

                    base64_data = base64.b64encode(file_content).decode("utf-8")
                    try:
                        extracted_text = await call_glm_vision_extract(
                            base64_data,
                            mime_type,
                            username=current_user.username,
                            trace_id=request_trace_id,
                        )
                    except Exception as glm_err:
                        logger.exception("vision_model_failed", extra={"username": current_user.username, "event": "vision_model_failed"})
                        yield _sse_event("error", f"❌ [Vision Module] GLM-4V 调用失败: {str(glm_err)}", trace_id=request_trace_id)
                        return

                    if extracted_text:
                        final_query = f"{final_query} [从文件识别到的内容]: {extracted_text}".replace("\n", "；").replace(
                            "\r", "")
                        yield _sse_event("processing", "[Vision Module] ✅ 提取完毕，已成功结构化二维图纸特征！", trace_id=request_trace_id)
                        await asyncio.sleep(0.5)
                    else:
                        yield _sse_event("error", "❌ [Vision Module] GLM-4V 返回空内容，请重试", trace_id=request_trace_id)
                        return
            else:
                if final_query.strip():
                    yield _sse_event("processing", "[Text Module] 📝 识别纯文本指令，正在执行语义清洗...", trace_id=request_trace_id)
                    await asyncio.sleep(0.5)

            if not final_query.strip():
                yield _sse_event("error", "❌ 请输入业务指令或上传清单图片", trace_id=request_trace_id)
                return

            final_query = normalize_quote_request_text(final_query)
            final_query, cost_context = safe_append_quote_cost_context(db, final_query, source_rows=excel_source_rows)
            cost_priority_source_rows = excel_source_rows or cost_context_references_as_source_rows(cost_context)
            if cost_context.matched_count:
                logger.info(
                    "quote_cost_context_attached",
                    extra={
                        "username": current_user.username,
                        "event": "quote_cost_context_attached",
                        "matched_count": cost_context.matched_count,
                        "active_cost_item_count": cost_context.active_cost_item_count,
                    },
                )
            yield _sse_event("processing", "[RAG & Agent] 🔍 正在穿透企业知识库寻找刚性底价并驱动专家大脑...\n(后台算力执行中，预计静候 15~30 秒，完成后将弹出核对面板)", trace_id=request_trace_id)

            payload = {"text": {"content": final_query}, "conversationId": str(uuid.uuid4())}
            response = await post_json_via_gateway(
                provider="n8n",
                model="dify-deepseek",
                endpoint_type="quote_calc",
                url=N8N_WEBHOOK_URL_CALC,
                json_payload=payload,
                headers=sign_payload(payload),
                timeout=180,
                username=current_user.username,
                trace_id=request_trace_id,
            )

            if response.status_code == 200:
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
                                "username": current_user.username,
                                "event": "n8n_empty_response_cost_context_fallback",
                                "matched_count": cost_context.matched_count,
                            },
                        )
                        calc_result = safe_enrich_quote_payload_with_cost_refs(
                            db,
                            fallback_result,
                            source_rows=cost_priority_source_rows,
                        )
                        current_user.quota -= 1
                        db.commit()
                        try:
                            record_ai_preview(
                                db,
                                username=current_user.username,
                                ai_payload=calc_result,
                                quote_id=request_trace_id,
                                trace_id=request_trace_id,
                                source="sync_chat",
                                query_text=final_query,
                            )
                            db.commit()
                        except Exception:
                            db.rollback()
                            logger.exception(
                                "quote_feedback_preview_record_failed",
                                extra={"event": "quote_feedback_preview_record_failed"},
                            )
                        yield _sse_event(
                            "preview",
                            "[Cost DB] N8N 返回空响应，已用成本库 active 底价生成预审报价，请人工复核。",
                            trace_id=request_trace_id,
                            data=calc_result,
                        )
                        return
                    logger.exception("n8n_response_parse_failed", extra={"username": current_user.username, "event": "n8n_response_parse_failed"})
                    yield _sse_event("error", f"❌ [n8n Workflow] 响应体解析失败（HTTP 200）\n实际返回内容：{body_preview}\n→ 请确认 N8N budget-calc 工作流末尾有 Respond to Webhook 节点且 Response Body 设为 JSON", trace_id=request_trace_id)
                    return
                calc_result = safe_enrich_quote_payload_with_cost_refs(db, calc_result, source_rows=cost_priority_source_rows)
                current_user.quota -= 1
                db.commit()
                try:
                    record_ai_preview(
                        db,
                        username=current_user.username,
                        ai_payload=calc_result,
                        quote_id=request_trace_id,
                        trace_id=request_trace_id,
                        source="sync_chat",
                        query_text=final_query,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("quote_feedback_preview_record_failed", extra={"event": "quote_feedback_preview_record_failed"})
                logger.info("quote_request_finished", extra={"username": current_user.username, "event": "quote_request_finished"})
                yield _sse_event("preview", "[n8n Workflow] ✅ AI 预审数据已就绪，请人工复核！", trace_id=request_trace_id, data=calc_result)
            else:
                try:
                    error_detail = response.json().get("message", "未知错误")
                except Exception:
                    error_detail = f"状态码 {response.status_code}，响应体为空"
                logger.error("n8n_workflow_failed", extra={"username": current_user.username, "event": "n8n_workflow_failed", "status_code": response.status_code})
                yield _sse_event("error", f"❌ [n8n Workflow] 中断: 底层算价引擎抛出异常 -> {error_detail}", trace_id=request_trace_id)

        except httpx.TimeoutException:
            logger.exception("quote_request_timeout", extra={"username": current_user.username, "event": "quote_request_timeout"})
            yield _sse_event("error", "❌ [n8n Workflow] 严重超时：请检查 Dify 模型是否拥堵挂起", trace_id=request_trace_id)
        except Exception as e:
            logger.exception("quote_request_crashed", extra={"username": current_user.username, "event": "quote_request_crashed"})
            yield _sse_event("error", f"❌ [API Gateway] 流转崩溃: {str(e)}", trace_id=request_trace_id)
        finally:
            reset_trace_id(trace_token)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


@router.post("/confirm_push", summary="人工审核通过后推送钉钉")
async def confirm_and_push(
        payload: ConfirmPushRequest = Body(...),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    try:
        payload = payload.model_dump(mode="json")
        quote_job_id = payload.get("quote_job_id")
        job = _get_accessible_quote_job_for_push(db, current_user, quote_job_id)
        if job:
            review_detail = build_quote_review_detail(db, job)
            summary = review_detail.get("summary") or {}
            if summary.get("requirement_row_count") and summary.get("missing_count"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"AI 预审不完整：确认清单 {summary.get('requirement_row_count')} 行，"
                        f"AI 预审返回 {summary.get('preview_row_count') or 0} 行，"
                        f"仍有 {summary.get('missing_count')} 行未匹配到预审报价。"
                        "请打回重填或重新发起报价。"
                    ),
                )
        payload = attach_quote_filename(payload, current_user.username)
        details = payload.get("project_details", [])
        unpriced_placeholders = _unpriced_requirement_placeholders(details)
        if unpriced_placeholders:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"预审单中还有 {len(unpriced_placeholders)} 行是 AI 未返回的占位行，"
                    "请先人工填写单价和系统合计后再下发。"
                ),
            )
        unconfirmed_candidates = _unconfirmed_cost_candidate_rows(details)
        if unconfirmed_candidates:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"预审单中还有 {len(unconfirmed_candidates)} 行存在多条 active 成本候选，"
                    "请先人工确认采用哪条成本依据后再下发。"
                ),
            )
        unconfirmed_ai_rewrites = _unconfirmed_ai_rewrite_rows(details)
        if unconfirmed_ai_rewrites:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"预审单中还有 {len(unconfirmed_ai_rewrites)} 行存在 AI 改写项目与原始成本依据不一致，"
                    "请先人工确认原始成本依据或切换成本条目后再下发。"
                ),
            )
        unconfirmed_ai_notes = _unconfirmed_ai_note_conflict_rows(details)
        if unconfirmed_ai_notes:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"预审单中还有 {len(unconfirmed_ai_notes)} 行存在 AI 备注与成本库依据不一致，"
                    "请先人工确认备注处理或修改备注后再下发。"
                ),
            )
        excel_b64 = build_excel_base64(details)
        logger.info("confirm_push_excel_check", extra={"excel_b64_len": len(excel_b64), "details_count": len(details)})
        if not excel_b64:
            raise HTTPException(status_code=500, detail="❌ Excel生成失败，请查看FastAPI日志中的 build_excel_base64_failed 错误")
        payload = dict(payload)
        payload["excel_base64"] = excel_b64
        logger.info("confirm_push_payload_keys", extra={"keys": list(payload.keys())})
        response = await post_json_via_gateway(
            provider="n8n",
            model="dingtalk-export",
            endpoint_type="quote_push",
            url=N8N_WEBHOOK_URL_PUSH,
            json_payload=payload,
            headers=sign_payload(payload),
            timeout=60,
            username=current_user.username,
            trace_id=get_trace_id(),
        )
        if response.status_code == 200:
            quote_history_id = None
            no_cost_draft_summary = None
            try:
                record = create_quote_history_record(
                    db,
                    username=current_user.username,
                    payload=payload,
                    confirmed_by=current_user.username,
                )
                db.commit()
                db.refresh(record)
                quote_history_id = record.id
            except Exception:
                db.rollback()
                logger.exception("quote_history_record_failed", extra={"event": "quote_history_record_failed"})
            try:
                record_confirmed_quote(
                    db,
                    username=current_user.username,
                    final_payload=payload,
                    quote_history_id=quote_history_id,
                    allow_cross_user=has_admin_role(current_user),
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("quote_feedback_confirm_record_failed", extra={"event": "quote_feedback_confirm_record_failed"})
            if _no_cost_draft_capture_enabled():
                try:
                    no_cost_draft_summary = create_no_cost_draft_items(
                        db,
                        current_user,
                        payload,
                        quote_job_id=quote_job_id,
                        quote_history_id=quote_history_id,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("no_cost_draft_capture_failed", extra={"event": "no_cost_draft_capture_failed"})
                    no_cost_draft_summary = {
                        "enabled": True,
                        "success": False,
                        "error": "NO_COST_DRAFT_CAPTURE_FAILED",
                    }
            if quote_job_id:
                try:
                    mark_preview_draft_pushed(db, quote_job_id=quote_job_id)
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("quote_preview_draft_mark_pushed_failed", extra={"event": "quote_preview_draft_mark_pushed_failed"})
            message = "✅ 最终报价单已成功投递至钉钉群！"
            if no_cost_draft_summary and no_cost_draft_summary.get("created_count"):
                message += f" 已生成 {no_cost_draft_summary['created_count']} 条成本库待审核草稿。"
            return api_ok({"no_cost_draft_summary": no_cost_draft_summary}, message=message)
        raise HTTPException(status_code=500, detail="底层推送流水线异常")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
