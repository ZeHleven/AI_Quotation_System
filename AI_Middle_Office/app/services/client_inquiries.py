from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.client_inquiry import DIRECTION_INBOUND, ClientInquiry
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.rbac import has_admin_role, has_any_role


CN_TZ = ZoneInfo("Asia/Shanghai")
VALID_TIME_SOURCES = {"manual", "default", "integration"}


def _now_local_naive() -> datetime:
    return datetime.now(CN_TZ).replace(tzinfo=None)


def _parse_local_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_DATETIME") from exc
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(CN_TZ).replace(tzinfo=None)


def _clean_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def normalize_time_source(value: str | None, *, has_manual_time: bool) -> str:
    if value:
        normalized = value.strip()
        if normalized not in VALID_TIME_SOURCES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_TIME_SOURCE")
        return normalized
    return "manual" if has_manual_time else "default"


def can_access_client_inquiries(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "staff", "manager"})


def can_view_all_client_inquiries(user: User) -> bool:
    return has_admin_role(user)


def require_client_inquiry_access(user: User) -> None:
    if not can_access_client_inquiries(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def get_accessible_client_inquiry(db: Session, inquiry_id: str, user: User) -> ClientInquiry:
    require_client_inquiry_access(user)
    query = db.query(ClientInquiry).filter(
        ClientInquiry.inquiry_id == inquiry_id,
        ClientInquiry.direction == DIRECTION_INBOUND,
    )
    if not can_view_all_client_inquiries(user):
        query = query.filter(ClientInquiry.responder_id == user.id)
    inquiry = query.first()
    if not inquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CLIENT_INQUIRY_NOT_FOUND")
    return inquiry


def create_or_reuse_client_inquiry(
    db: Session,
    *,
    current_user: User,
    client_inquiry_id: str | None = None,
    source: str | None = None,
    client_name: str | None = None,
    client_phone: str | None = None,
    inquiry_time: str | None = None,
    time_source: str | None = None,
    notes: str | None = None,
) -> ClientInquiry:
    require_client_inquiry_access(current_user)
    if client_inquiry_id:
        inquiry = get_accessible_client_inquiry(db, client_inquiry_id.strip(), current_user)
        if inquiry.first_response_time is None:
            inquiry.first_response_time = _now_local_naive()
        return inquiry

    parsed_inquiry_time = _parse_local_datetime(inquiry_time)
    now = _now_local_naive()
    normalized_source = normalize_time_source(time_source, has_manual_time=parsed_inquiry_time is not None)
    inquiry = ClientInquiry(
        inquiry_id=str(uuid.uuid4()),
        source=_clean_text(source, 64),
        client_name=_clean_text(client_name, 128),
        client_phone=_clean_text(client_phone, 64),
        inquiry_time=parsed_inquiry_time or now,
        first_response_time=now,
        time_source=normalized_source,
        responder_id=current_user.id,
        notes=_clean_text(notes, 2000),
        direction=DIRECTION_INBOUND,
    )
    db.add(inquiry)
    db.flush()
    return inquiry


def serialize_client_inquiry(inquiry: ClientInquiry, quote_job_count: int = 0) -> dict:
    return {
        "id": inquiry.id,
        "inquiry_id": inquiry.inquiry_id,
        "source": inquiry.source,
        "client_name": inquiry.client_name,
        "client_phone": inquiry.client_phone,
        "inquiry_time": _format_dt(inquiry.inquiry_time),
        "first_response_time": _format_dt(inquiry.first_response_time),
        "time_source": inquiry.time_source,
        "responder_id": inquiry.responder_id,
        "notes": inquiry.notes,
        "first_quote_job_id": inquiry.first_quote_job_id,
        "quote_job_count": quote_job_count,
        "created_at": _format_dt(inquiry.created_at),
        "updated_at": _format_dt(inquiry.updated_at),
    }


def count_quote_jobs_by_inquiry(db: Session, inquiry_ids: list[str]) -> dict[str, int]:
    if not inquiry_ids:
        return {}
    rows = (
        db.query(QuoteJob.client_inquiry_id, QuoteJob.id)
        .filter(QuoteJob.client_inquiry_id.in_(inquiry_ids))
        .all()
    )
    counts: dict[str, int] = {}
    for inquiry_id, _job_id in rows:
        if inquiry_id:
            counts[inquiry_id] = counts.get(inquiry_id, 0) + 1
    return counts


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")
