import logging
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_trace_id
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.client_inquiry import DIRECTION_INBOUND, ClientInquiry
from app.models.quote_feedback import QuoteFeedback
from app.models.quote_history import QuoteHistory, QuoteHistoryItem
from app.models.quote_job import QuoteJob
from app.models.quote_preview_draft import PREVIEW_DRAFT_STATUS_EDITING, QuotePreviewDraft
from app.models.user import User
from app.services.excel_service import build_excel_base64
from app.services.model_gateway import post_json_via_gateway
from app.services.rbac import has_admin_role
from app.services.quote_history import (
    build_display_title,
    build_project_summary,
    json_loads,
    project_details,
    project_names,
    serialize_history_item,
    total_amount,
)
from app.services.quote_helpers import attach_quote_filename, sign_payload


router = APIRouter()
CN_TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


def _format_dt(value) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _parse_filter_datetime(value: Optional[str], *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="INVALID_DATE_FILTER") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(CN_TZ).replace(tzinfo=None)
    if len(raw_value) <= 10:
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed


def _parse_row_datetime(value: Optional[str]) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(CN_TZ).replace(tzinfo=None)
        return parsed


def _amount_value(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _history_keyword_text(row: dict) -> str:
    parts = [
        row.get("display_title"),
        row.get("project_summary"),
        row.get("request_text"),
        row.get("source_file_name"),
        row.get("quote_job_id"),
        row.get("quote_id"),
        row.get("trace_id"),
        row.get("username"),
        row.get("rejection_reason"),
    ]
    parts.extend(row.get("first_project_names") or [])
    inquiry = row.get("client_inquiry") or {}
    parts.extend(
        [
            inquiry.get("source"),
            inquiry.get("client_name"),
            inquiry.get("client_phone"),
            inquiry.get("notes"),
        ]
    )
    return " ".join(str(part) for part in parts if part)


def _filter_history_rows(
    rows: list[dict],
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    keyword: Optional[str],
    min_item_count: Optional[int],
    max_item_count: Optional[int],
    min_total_amount: Optional[float],
    max_total_amount: Optional[float],
    push_status: Optional[str],
) -> list[dict]:
    start_dt = _parse_filter_datetime(start_date)
    end_dt = _parse_filter_datetime(end_date, end_of_day=True)
    status_filter = (push_status or "").strip()
    if status_filter and status_filter not in {"draft", "pushed", "not_pushed", "rejected"}:
        raise HTTPException(status_code=422, detail="INVALID_PUSH_STATUS_FILTER")
    keyword_value = (keyword or "").strip().lower()

    filtered: list[dict] = []
    for row in rows:
        row_dt = _parse_row_datetime(row.get("created_at"))
        if start_dt and (not row_dt or row_dt < start_dt):
            continue
        if end_dt and (not row_dt or row_dt > end_dt):
            continue

        row_item_count = int(row.get("item_count") or 0)
        if min_item_count is not None and row_item_count < min_item_count:
            continue
        if max_item_count is not None and row_item_count > max_item_count:
            continue

        row_total_amount = _amount_value(row.get("total_amount")) or 0.0
        if min_total_amount is not None and row_total_amount < min_total_amount:
            continue
        if max_total_amount is not None and row_total_amount > max_total_amount:
            continue

        if status_filter and row.get("push_status") != status_filter:
            continue

        if keyword_value and keyword_value not in _history_keyword_text(row).lower():
            continue

        filtered.append(row)
    return filtered


def _display_local_time(value: datetime | None, *, baseline: datetime | None = None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is not None:
        return value.astimezone(CN_TZ).replace(tzinfo=None)
    if baseline and baseline.tzinfo is None and value < baseline:
        delta = baseline - value
        if timedelta(hours=6) <= delta <= timedelta(hours=10):
            return value + timedelta(hours=8)
    return value


def _first_project_name_list(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _client_inquiry_row(inquiry: Optional[ClientInquiry]) -> Optional[dict]:
    if not inquiry:
        return None
    return {
        "inquiry_id": inquiry.inquiry_id,
        "source": inquiry.source,
        "client_name": inquiry.client_name,
        "client_phone": inquiry.client_phone,
        "inquiry_time": _format_dt(inquiry.inquiry_time),
        "first_response_time": _format_dt(inquiry.first_response_time),
        "time_source": inquiry.time_source,
        "notes": inquiry.notes,
    }


def _client_inquiry_map_for_job_ids(db: Session, job_ids: list[str]) -> dict[str, ClientInquiry]:
    job_ids = [job_id for job_id in job_ids if job_id]
    if not job_ids:
        return {}
    rows = (
        db.query(QuoteJob.job_id, ClientInquiry)
        .join(
            ClientInquiry,
            and_(
                QuoteJob.client_inquiry_id == ClientInquiry.inquiry_id,
                ClientInquiry.direction == DIRECTION_INBOUND,
            ),
        )
        .filter(QuoteJob.job_id.in_(job_ids))
        .all()
    )
    return {job_id: inquiry for job_id, inquiry in rows}


def _client_inquiry_map(db: Session, records: list[QuoteHistory]) -> dict[str, ClientInquiry]:
    return _client_inquiry_map_for_job_ids(db, [record.quote_job_id for record in records if record.quote_job_id])


def _history_row(
    record: QuoteHistory,
    include_payload_json: bool = True,
    client_inquiry: Optional[ClientInquiry] = None,
) -> dict:
    data = {
        "id": record.id,
        "username": record.username,
        "quote_id": record.quote_id,
        "quote_job_id": record.quote_job_id,
        "trace_id": record.trace_id,
        "request_text": record.request_text,
        "source_file_name": record.source_file_name,
        "display_title": record.display_title,
        "project_summary": record.project_summary,
        "first_project_names": _first_project_name_list(record.first_project_names),
        "confirmed_by": record.confirmed_by,
        "pushed_to_dingtalk": record.pushed_to_dingtalk,
        "created_at": _format_dt(record.created_at),
        "total_amount": record.total_amount,
        "item_count": record.item_count,
        "client_inquiry": _client_inquiry_row(client_inquiry),
        "record_type": "history",
        "push_status": "pushed" if record.pushed_to_dingtalk else "not_pushed",
        "can_edit_preview_draft": False,
    }
    if include_payload_json:
        data["payload_json"] = record.payload_json
    return data


def _preview_draft_history_row(
    draft: QuotePreviewDraft,
    job: QuoteJob,
    client_inquiry: Optional[ClientInquiry] = None,
) -> dict:
    payload = json_loads(draft.draft_json) or {}
    details = project_details(payload)
    source_file_name = job.source_file_name or job.file_name
    title = build_display_title(payload if isinstance(payload, dict) else {}, details, source_file_name)
    summary = build_project_summary(details) or job.request_summary or job.message or ""
    display_updated_at = _display_local_time(draft.updated_at, baseline=draft.created_at)
    display_created_at = display_updated_at or _display_local_time(draft.created_at)
    return {
        "id": f"draft-{draft.id}",
        "draft_id": draft.id,
        "username": draft.username,
        "quote_id": draft.quote_id,
        "quote_job_id": draft.quote_job_id,
        "trace_id": draft.trace_id or job.trace_id,
        "request_text": job.message,
        "source_file_name": source_file_name,
        "display_title": f"预审草稿：{title}",
        "project_summary": summary,
        "first_project_names": project_names(details),
        "confirmed_by": None,
        "pushed_to_dingtalk": False,
        "created_at": _format_dt(display_created_at),
        "updated_at": _format_dt(display_updated_at),
        "total_amount": total_amount(payload),
        "item_count": draft.row_count or len(details),
        "client_inquiry": _client_inquiry_row(client_inquiry),
        "record_type": "preview_draft",
        "push_status": "draft",
        "draft_status": draft.status,
        "can_edit_preview_draft": True,
        "priced_row_count": draft.priced_row_count,
        "unpriced_row_count": draft.unpriced_row_count,
    }


def _rejected_feedback_history_row(
    feedback: QuoteFeedback,
    job: Optional[QuoteJob] = None,
    client_inquiry: Optional[ClientInquiry] = None,
) -> dict:
    payload = json_loads(feedback.ai_payload_json) or {}
    details = project_details(payload)
    source_file_name = feedback.source_file_name or (job.source_file_name if job else None) or (job.file_name if job else None)
    title = build_display_title(payload if isinstance(payload, dict) else {}, details, source_file_name)
    summary = feedback.project_summary or build_project_summary(details) or (job.request_summary if job else None) or (job.message if job else "") or ""
    rejected_at = _display_local_time(feedback.rejected_at, baseline=feedback.created_at)
    created_at = rejected_at or _display_local_time(feedback.updated_at, baseline=feedback.created_at) or _display_local_time(feedback.created_at)
    return {
        "id": f"rejected-{feedback.id}",
        "feedback_id": feedback.id,
        "username": feedback.username,
        "quote_id": feedback.quote_id,
        "quote_job_id": feedback.quote_job_id,
        "trace_id": feedback.trace_id or (job.trace_id if job else None),
        "request_text": feedback.request_text or (job.message if job else None),
        "source_file_name": source_file_name,
        "display_title": f"打回重填：{title}",
        "project_summary": summary,
        "first_project_names": project_names(details),
        "confirmed_by": feedback.reviewed_by,
        "pushed_to_dingtalk": False,
        "created_at": _format_dt(created_at),
        "updated_at": _format_dt(_display_local_time(feedback.updated_at, baseline=feedback.created_at)),
        "total_amount": feedback.ai_total_amount if feedback.ai_total_amount is not None else total_amount(payload),
        "item_count": feedback.ai_item_count or len(details),
        "client_inquiry": _client_inquiry_row(client_inquiry),
        "record_type": "rejected_quote",
        "push_status": "rejected",
        "can_edit_preview_draft": False,
        "rejection_reason": feedback.rejection_reason,
        "rejected_at": _format_dt(rejected_at),
        "payload_json": feedback.ai_payload_json,
    }


def _get_accessible_history(history_id: int, current_user: User, db: Session) -> QuoteHistory:
    record = db.query(QuoteHistory).filter(QuoteHistory.id == history_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="quote history not found")
    if not has_admin_role(current_user) and record.username != current_user.username:
        raise HTTPException(status_code=404, detail="quote history not found")
    return record


def _project_details_from_history(record: QuoteHistory, items: list[QuoteHistoryItem]) -> tuple[dict, list[dict]]:
    raw_payload = json_loads(record.payload_json) or {}
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    details = project_details(payload)
    if details:
        return dict(payload), details

    fallback_details: list[dict] = []
    for item in items:
        serialized = serialize_history_item(item)
        raw = serialized.get("raw")
        fallback_details.append(raw if isinstance(raw, dict) and raw else serialized)
    return dict(payload), fallback_details


@router.get("/history", summary="查询报价历史（本人；admin 可查全部）")
async def get_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    username: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    min_item_count: Optional[int] = Query(None, ge=0),
    max_item_count: Optional[int] = Query(None, ge=0),
    min_total_amount: Optional[float] = Query(None, ge=0),
    max_total_amount: Optional[float] = Query(None, ge=0),
    push_status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(QuoteHistory)
    if not has_admin_role(current_user):
        query = query.filter(QuoteHistory.username == current_user.username)
    elif username:
        query = query.filter(QuoteHistory.username == username)

    history_records = query.all()
    history_job_ids = {record.quote_job_id for record in history_records if record.quote_job_id}

    feedback_query = db.query(QuoteFeedback).filter(QuoteFeedback.status == "rejected", QuoteFeedback.rejected.is_(True))
    if not has_admin_role(current_user):
        feedback_query = feedback_query.filter(QuoteFeedback.username == current_user.username)
    elif username:
        feedback_query = feedback_query.filter(QuoteFeedback.username == username)
    if history_job_ids:
        feedback_query = feedback_query.filter(
            or_(QuoteFeedback.quote_job_id.is_(None), ~QuoteFeedback.quote_job_id.in_(history_job_ids))
        )
    rejected_feedback_rows = feedback_query.all()

    draft_query = (
        db.query(QuotePreviewDraft, QuoteJob)
        .join(QuoteJob, QuotePreviewDraft.quote_job_id == QuoteJob.job_id)
        .filter(QuotePreviewDraft.status == PREVIEW_DRAFT_STATUS_EDITING)
    )
    if not has_admin_role(current_user):
        draft_query = draft_query.filter(QuotePreviewDraft.username == current_user.username)
    elif username:
        draft_query = draft_query.filter(QuotePreviewDraft.username == username)
    if history_job_ids:
        draft_query = draft_query.filter(~QuotePreviewDraft.quote_job_id.in_(history_job_ids))
    draft_records = draft_query.all()

    job_ids = [record.quote_job_id for record in history_records if record.quote_job_id]
    job_ids.extend([draft.quote_job_id for draft, _job in draft_records if draft.quote_job_id])
    job_ids.extend([feedback.quote_job_id for feedback in rejected_feedback_rows if feedback.quote_job_id])
    feedback_job_ids = [feedback.quote_job_id for feedback in rejected_feedback_rows if feedback.quote_job_id]
    jobs_by_id = {
        job.job_id: job
        for job in db.query(QuoteJob).filter(QuoteJob.job_id.in_(feedback_job_ids)).all()
    } if feedback_job_ids else {}
    inquiries_by_job_id = _client_inquiry_map_for_job_ids(db, job_ids)

    rows = [
        _history_row(record, client_inquiry=inquiries_by_job_id.get(record.quote_job_id))
        for record in history_records
    ]
    rows.extend(
        _preview_draft_history_row(draft, job, client_inquiry=inquiries_by_job_id.get(draft.quote_job_id))
        for draft, job in draft_records
    )
    rows.extend(
        _rejected_feedback_history_row(
            feedback,
            jobs_by_id.get(feedback.quote_job_id),
            client_inquiry=inquiries_by_job_id.get(feedback.quote_job_id),
        )
        for feedback in rejected_feedback_rows
    )
    rows = _filter_history_rows(
        rows,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        min_item_count=min_item_count,
        max_item_count=max_item_count,
        min_total_amount=min_total_amount,
        max_total_amount=max_total_amount,
        push_status=push_status,
    )
    rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    total = len(rows)
    offset = (page - 1) * page_size

    return api_page(
        rows[offset : offset + page_size],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/history/{history_id}", summary="查询报价历史详情")
async def get_history_detail(
    history_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_accessible_history(history_id, current_user, db)
    items = (
        db.query(QuoteHistoryItem)
        .filter(QuoteHistoryItem.quote_history_id == record.id)
        .order_by(QuoteHistoryItem.line_no.asc(), QuoteHistoryItem.id.asc())
        .all()
    )
    inquiries_by_job_id = _client_inquiry_map(db, [record])
    data = _history_row(record, client_inquiry=inquiries_by_job_id.get(record.quote_job_id))
    data["payload"] = json_loads(record.payload_json)
    data["items"] = [serialize_history_item(item) for item in items]
    return api_ok(data)


@router.post("/history/{history_id}/resend", summary="再次发送已推送报价到钉钉")
async def resend_history_quote(
    history_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_accessible_history(history_id, current_user, db)
    if not record.pushed_to_dingtalk:
        raise HTTPException(status_code=409, detail="仅已推送的报价历史支持再次发送")

    items = (
        db.query(QuoteHistoryItem)
        .filter(QuoteHistoryItem.quote_history_id == record.id)
        .order_by(QuoteHistoryItem.line_no.asc(), QuoteHistoryItem.id.asc())
        .all()
    )
    payload, details = _project_details_from_history(record, items)
    if not details:
        raise HTTPException(status_code=400, detail="该历史报价缺少可重发的报价明细")

    payload.pop("excel_base64", None)
    payload.update(
        {
            "project_details": details,
            "quote_history_id": record.id,
            "quote_id": record.quote_id,
            "quote_job_id": record.quote_job_id,
            "trace_id": record.trace_id,
            "request_text": record.request_text,
            "source_file_name": record.source_file_name,
            "resend_from_history_id": record.id,
            "resend": True,
        }
    )
    payload = attach_quote_filename(payload, current_user.username)
    excel_b64 = build_excel_base64(details)
    if not excel_b64:
        raise HTTPException(status_code=500, detail="Excel生成失败，无法再次发送")
    payload["excel_base64"] = excel_b64

    response = await post_json_via_gateway(
        provider="n8n",
        model="dingtalk-export",
        endpoint_type="quote_push",
        url=settings.n8n_webhook_url_push,
        json_payload=payload,
        headers=sign_payload(payload),
        timeout=60,
        username=current_user.username,
        trace_id=get_trace_id() or record.trace_id,
    )
    if response.status_code != 200:
        logger.error(
            "history_quote_resend_failed",
            extra={"history_id": history_id, "status_code": response.status_code},
        )
        raise HTTPException(status_code=502, detail=f"钉钉再次发送失败：HTTP {response.status_code}")

    return api_ok(
        {
            "history_id": record.id,
            "quote_id": record.quote_id,
            "quote_job_id": record.quote_job_id,
            "item_count": len(details),
            "total_amount": total_amount({"project_details": details}),
        },
        message="已再次发送报价到钉钉",
    )
