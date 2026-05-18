from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.client_inquiry import ClientInquiry
from app.models.quote_history import QuoteHistory, QuoteHistoryItem
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.rbac import has_admin_role
from app.services.quote_history import json_loads, serialize_history_item


router = APIRouter()


def _format_dt(value) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


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


def _client_inquiry_map(db: Session, records: list[QuoteHistory]) -> dict[str, ClientInquiry]:
    job_ids = [record.quote_job_id for record in records if record.quote_job_id]
    if not job_ids:
        return {}
    rows = (
        db.query(QuoteJob.job_id, ClientInquiry)
        .join(ClientInquiry, QuoteJob.client_inquiry_id == ClientInquiry.inquiry_id)
        .filter(QuoteJob.job_id.in_(job_ids))
        .all()
    )
    return {job_id: inquiry for job_id, inquiry in rows}


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
    }
    if include_payload_json:
        data["payload_json"] = record.payload_json
    return data


def _get_accessible_history(history_id: int, current_user: User, db: Session) -> QuoteHistory:
    record = db.query(QuoteHistory).filter(QuoteHistory.id == history_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="quote history not found")
    if not has_admin_role(current_user) and record.username != current_user.username:
        raise HTTPException(status_code=404, detail="quote history not found")
    return record


@router.get("/history", summary="查询报价历史（本人；admin 可查全部）")
async def get_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    username: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(QuoteHistory)
    if not has_admin_role(current_user):
        query = query.filter(QuoteHistory.username == current_user.username)
    elif username:
        query = query.filter(QuoteHistory.username == username)

    total = query.count()
    records = (
        query.order_by(QuoteHistory.created_at.desc(), QuoteHistory.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    inquiries_by_job_id = _client_inquiry_map(db, records)

    return api_page(
        [_history_row(record, client_inquiry=inquiries_by_job_id.get(record.quote_job_id)) for record in records],
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
