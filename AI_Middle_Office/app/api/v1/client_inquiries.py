from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.client_inquiry import ClientInquiry
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.client_inquiries import (
    _clean_text,
    _parse_local_datetime,
    count_quote_jobs_by_inquiry,
    get_accessible_client_inquiry,
    normalize_time_source,
    require_client_inquiry_access,
    serialize_client_inquiry,
    can_view_all_client_inquiries,
)


router = APIRouter()


class ClientInquiryUpdate(BaseModel):
    source: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    inquiry_time: Optional[str] = None
    first_response_time: Optional[str] = None
    time_source: Optional[str] = None
    notes: Optional[str] = None


def _payload_dict(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _ensure_feature_enabled() -> None:
    if not settings.feature_client_inquiry:
        raise HTTPException(status_code=403, detail="FEATURE_DISABLED")


@router.get("/client-inquiries", summary="查询客户咨询记录")
async def list_client_inquiries(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,
    responder_id: Optional[int] = None,
    time_source: Optional[str] = None,
    has_quote_job: Optional[bool] = None,
    has_client_info: Optional[bool] = None,
    sort: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    require_client_inquiry_access(current_user)

    query = db.query(ClientInquiry)
    if not can_view_all_client_inquiries(current_user):
        query = query.filter(ClientInquiry.responder_id == current_user.id)
    elif responder_id is not None:
        query = query.filter(ClientInquiry.responder_id == responder_id)

    start = _parse_local_datetime(date_from)
    end = _parse_local_datetime(date_to)
    if start:
        query = query.filter(ClientInquiry.inquiry_time >= start)
    if end:
        query = query.filter(ClientInquiry.inquiry_time <= end)
    if source:
        query = query.filter(ClientInquiry.source == source.strip())
    if time_source:
        normalized_source = normalize_time_source(time_source, has_manual_time=False)
        query = query.filter(ClientInquiry.time_source == normalized_source)
    if has_quote_job is not None:
        job_inquiries = db.query(QuoteJob.client_inquiry_id).filter(QuoteJob.client_inquiry_id.isnot(None))
        query = query.filter(ClientInquiry.inquiry_id.in_(job_inquiries) if has_quote_job else ~ClientInquiry.inquiry_id.in_(job_inquiries))
    if has_client_info is True:
        query = query.filter(
            or_(
                ClientInquiry.source.isnot(None),
                ClientInquiry.client_name.isnot(None),
                ClientInquiry.client_phone.isnot(None),
            )
        )
    elif has_client_info is False:
        query = query.filter(
            ClientInquiry.source.is_(None),
            ClientInquiry.client_name.is_(None),
            ClientInquiry.client_phone.is_(None),
        )

    total = query.count()
    if sort == "created_at_desc":
        query = query.order_by(ClientInquiry.created_at.desc(), ClientInquiry.id.desc())
    else:
        query = query.order_by(ClientInquiry.inquiry_time.desc(), ClientInquiry.id.desc())
    inquiries = query.offset((page - 1) * page_size).limit(page_size).all()
    counts = count_quote_jobs_by_inquiry(db, [item.inquiry_id for item in inquiries])
    return api_page(
        [serialize_client_inquiry(item, counts.get(item.inquiry_id, 0)) for item in inquiries],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/client-inquiries/{inquiry_id}", summary="修正客户咨询记录")
async def update_client_inquiry(
    inquiry_id: str,
    payload: ClientInquiryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    inquiry = get_accessible_client_inquiry(db, inquiry_id, current_user)
    updates = _payload_dict(payload)
    if "source" in updates:
        inquiry.source = _clean_text(updates["source"], 64)
    if "client_name" in updates:
        inquiry.client_name = _clean_text(updates["client_name"], 128)
    if "client_phone" in updates:
        inquiry.client_phone = _clean_text(updates["client_phone"], 64)
    if "inquiry_time" in updates:
        parsed = _parse_local_datetime(updates["inquiry_time"])
        if parsed is not None:
            inquiry.inquiry_time = parsed
        if "time_source" not in updates and inquiry.time_source == "default":
            inquiry.time_source = "manual"
    if "first_response_time" in updates:
        parsed = _parse_local_datetime(updates["first_response_time"])
        if parsed is not None:
            inquiry.first_response_time = parsed
    if "time_source" in updates:
        inquiry.time_source = normalize_time_source(updates["time_source"], has_manual_time=inquiry.inquiry_time is not None)
    if "notes" in updates:
        inquiry.notes = _clean_text(updates["notes"], 2000)

    db.commit()
    db.refresh(inquiry)
    counts = count_quote_jobs_by_inquiry(db, [inquiry.inquiry_id])
    return api_ok(serialize_client_inquiry(inquiry, counts.get(inquiry.inquiry_id, 0)))
