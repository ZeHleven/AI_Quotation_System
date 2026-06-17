"""BIZ-1a business ledger service.

This service only handles `client_inquiries.direction == "outbound"` records.
`client_phone` is the API and DB field for the plan's "contact" concept.
Staff can edit only: client_phone, notes, next_followup_at, stage.
Admin/system_admin can additionally edit: client_name, source, responder_id.
`company_name` is intentionally not implemented; adding it later requires an
Alembic column first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.time_utils import app_local_naive, parse_iso_datetime
from app.models.client_inquiry import (
    DIRECTION_OUTBOUND,
    STAGE_FOLLOWUP_NEGOTIATION,
    STAGE_INITIAL_CONTACT,
    STAGE_LOST,
    STAGE_QUOTING,
    STAGE_REQUIREMENT_CONFIRMATION,
    STAGE_TERMINAL,
    STAGE_VALUES,
    STAGE_WON,
    ClientInquiry,
)
from app.models.client_inquiry_event import (
    EVENT_TYPE_CANCEL,
    EVENT_TYPE_CREATE,
    EVENT_TYPE_EDIT,
    EVENT_TYPE_STAGE_CHANGE,
    EVENT_TYPE_TRANSFER,
    ClientInquiryEvent,
)
from app.models.user import User
from app.services.rbac import has_admin_role, has_any_role


STAFF_UPDATE_FIELDS = {"stage", "next_followup_at", "client_phone", "notes"}
ADMIN_UPDATE_FIELDS = STAFF_UPDATE_FIELDS | {"client_name", "source", "responder_id"}
FORBIDDEN_PATCH_FIELDS = {
    "id",
    "inquiry_id",
    "direction",
    "inquiry_time",
    "first_response_time",
    "time_source",
    "created_at",
    "updated_at",
    "cancelled_at",
    "cancelled_by_id",
    "cancel_reason",
    "company_name",
}
CREATE_FIELDS = {"source", "client_name", "client_phone", "stage", "next_followup_at", "responder_id", "notes"}
VALID_STAGE_TRANSITIONS = {
    STAGE_INITIAL_CONTACT: {STAGE_REQUIREMENT_CONFIRMATION, STAGE_WON, STAGE_LOST},
    STAGE_REQUIREMENT_CONFIRMATION: {STAGE_QUOTING, STAGE_WON, STAGE_LOST},
    STAGE_QUOTING: {STAGE_FOLLOWUP_NEGOTIATION, STAGE_WON, STAGE_LOST},
    STAGE_FOLLOWUP_NEGOTIATION: {STAGE_WON, STAGE_LOST},
}


@dataclass(frozen=True)
class EventContext:
    ip_address: str | None = None
    user_agent: str | None = None
    trace_id: str | None = None


def _now_local_naive() -> datetime:
    return app_local_naive()


def _to_local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return app_local_naive(value)


def parse_local_datetime(value: str | datetime | None, *, field_name: str = "datetime") -> datetime | None:
    if value is None or isinstance(value, datetime):
        return _to_local_naive(value)
    raw_value = str(value).strip()
    if not raw_value:
        return None
    try:
        parsed = parse_iso_datetime(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"INVALID_{field_name.upper()}") from exc
    return _to_local_naive(parsed)


def clean_text(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def can_access_business_ledger(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "staff"})


def can_view_all_business_ledger(user: User) -> bool:
    return has_admin_role(user)


def require_business_ledger_access(user: User) -> None:
    if not can_access_business_ledger(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def normalize_stage(value: str | None, *, default: str | None = None) -> str | None:
    stage = clean_text(value, 32) if value is not None else default
    if stage is None:
        return None
    if stage not in STAGE_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_STAGE")
    return stage


def _require_valid_transition(old_stage: str | None, new_stage: str | None) -> None:
    if new_stage is None or old_stage == new_stage:
        return
    if old_stage is None:
        return
    if old_stage in STAGE_TERMINAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STATE_CONFLICT")
    if new_stage in STAGE_TERMINAL:
        return
    if new_stage not in VALID_STAGE_TRANSITIONS.get(old_stage, set()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STATE_CONFLICT")


def _ensure_active_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_RESPONDER")
    return user


def _payload_dict(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    if hasattr(payload, "dict"):
        return payload.dict(exclude_unset=True)
    return dict(payload or {})


def _format_dt(value: datetime | None) -> str | None:
    value = _to_local_naive(value)
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def ledger_snapshot(inquiry: ClientInquiry) -> dict[str, Any]:
    return {
        "id": inquiry.id,
        "inquiry_id": inquiry.inquiry_id,
        "direction": inquiry.direction,
        "source": inquiry.source,
        "client_name": inquiry.client_name,
        "client_phone": inquiry.client_phone,
        "stage": inquiry.stage,
        "next_followup_at": _format_dt(inquiry.next_followup_at),
        "responder_id": inquiry.responder_id,
        "notes": inquiry.notes,
        "cancelled_at": _format_dt(inquiry.cancelled_at),
        "cancelled_by_id": inquiry.cancelled_by_id,
        "cancel_reason": inquiry.cancel_reason,
        "created_at": _format_dt(inquiry.created_at),
        "updated_at": _format_dt(inquiry.updated_at),
    }


def _log_event(
    db: Session,
    *,
    inquiry_id: str,
    event_type: str,
    operator_id: int | None,
    old_value: str | None = None,
    new_value: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    context: EventContext | None = None,
) -> ClientInquiryEvent:
    context = context or EventContext()
    event = ClientInquiryEvent(
        inquiry_id=inquiry_id,
        event_type=event_type,
        old_value=clean_text(old_value, 64),
        new_value=clean_text(new_value, 64),
        operator_id=operator_id,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        trace_id=context.trace_id,
        before_json=before,
        after_json=after,
    )
    db.add(event)
    return event


def create_outbound(
    db: Session,
    user: User,
    payload: Mapping[str, Any] | Any,
    *,
    context: EventContext | None = None,
) -> ClientInquiry:
    require_business_ledger_access(user)
    data = _payload_dict(payload)
    unexpected = set(data) - (CREATE_FIELDS | {"direction"})
    if unexpected:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNSUPPORTED_FIELDS")
    if data.get("direction") not in (None, DIRECTION_OUTBOUND):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_DIRECTION")

    is_admin = can_view_all_business_ledger(user)
    responder_id = data.get("responder_id") or user.id
    try:
        responder_id = int(responder_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_RESPONDER") from exc
    if not is_admin and responder_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    _ensure_active_user(db, responder_id)

    stage = normalize_stage(data.get("stage"), default=STAGE_INITIAL_CONTACT)
    now = _now_local_naive()
    inquiry = ClientInquiry(
        inquiry_id=str(uuid4()),
        direction=DIRECTION_OUTBOUND,
        source=clean_text(data.get("source"), 64),
        client_name=clean_text(data.get("client_name"), 128),
        client_phone=clean_text(data.get("client_phone"), 64),
        inquiry_time=now,
        first_response_time=None,
        time_source="manual",
        responder_id=responder_id,
        stage=stage,
        next_followup_at=parse_local_datetime(data.get("next_followup_at"), field_name="next_followup_at"),
        notes=clean_text(data.get("notes"), 2000),
    )
    db.add(inquiry)
    db.flush()
    _log_event(
        db,
        inquiry_id=inquiry.inquiry_id,
        event_type=EVENT_TYPE_CREATE,
        operator_id=user.id,
        new_value=stage,
        after=ledger_snapshot(inquiry),
        context=context,
    )
    return inquiry


def _base_ledger_query(db: Session):
    return db.query(ClientInquiry).filter(ClientInquiry.direction == DIRECTION_OUTBOUND)


def _apply_access_filter(query, user: User):
    if can_view_all_business_ledger(user):
        return query
    return query.filter(ClientInquiry.responder_id == user.id)


def list_ledger(
    db: Session,
    user: User,
    *,
    stage: str | None = None,
    source: str | None = None,
    responder_id: int | None = None,
    overdue_only: bool = False,
    date_from: datetime | str | None = None,
    date_to: datetime | str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ClientInquiry], int]:
    require_business_ledger_access(user)
    query = _base_ledger_query(db).filter(ClientInquiry.cancelled_at.is_(None))
    query = _apply_access_filter(query, user)

    if can_view_all_business_ledger(user) and responder_id is not None:
        query = query.filter(ClientInquiry.responder_id == responder_id)
    if stage:
        stages = [normalize_stage(item.strip()) for item in stage.split(",") if item.strip()]
        if stages:
            query = query.filter(ClientInquiry.stage.in_(stages))
    if source:
        query = query.filter(ClientInquiry.source == source.strip())
    start = parse_local_datetime(date_from, field_name="date_from")
    end = parse_local_datetime(date_to, field_name="date_to")
    if start:
        query = query.filter(ClientInquiry.created_at >= start)
    if end:
        query = query.filter(ClientInquiry.created_at <= end)
    if overdue_only:
        query = query.filter(
            ClientInquiry.next_followup_at.isnot(None),
            ClientInquiry.next_followup_at < _now_local_naive(),
            ~ClientInquiry.stage.in_(STAGE_TERMINAL),
        )
    if keyword:
        keyword_value = keyword.strip()
        if keyword_value:
            pattern = f"%{keyword_value}%"
            query = query.filter(
                or_(
                    ClientInquiry.client_name.like(pattern),
                    ClientInquiry.client_phone.like(pattern),
                    ClientInquiry.notes.like(pattern),
                )
            )

    total = query.count()
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    items = (
        query.order_by(ClientInquiry.next_followup_at.asc(), ClientInquiry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def _get_ledger_record(
    db: Session,
    user: User,
    inquiry_id: str,
    *,
    include_cancelled: bool = False,
) -> ClientInquiry:
    require_business_ledger_access(user)
    query = _base_ledger_query(db).filter(ClientInquiry.inquiry_id == inquiry_id)
    if not include_cancelled:
        query = query.filter(ClientInquiry.cancelled_at.is_(None))
    query = _apply_access_filter(query, user)
    inquiry = query.first()
    if not inquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BUSINESS_LEDGER_NOT_FOUND")
    return inquiry


def get_ledger(db: Session, user: User, inquiry_id: str) -> ClientInquiry:
    return _get_ledger_record(db, user, inquiry_id)


def _get_ledger_record_for_update(db: Session, user: User, inquiry_id: str) -> ClientInquiry:
    require_business_ledger_access(user)
    inquiry = _base_ledger_query(db).filter(ClientInquiry.inquiry_id == inquiry_id).first()
    if not inquiry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BUSINESS_LEDGER_NOT_FOUND")
    if not can_view_all_business_ledger(user) and inquiry.responder_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    return inquiry


def _ensure_mutable(inquiry: ClientInquiry) -> None:
    if inquiry.cancelled_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STATE_CONFLICT")
    if inquiry.stage in STAGE_TERMINAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STATE_CONFLICT")


def _validate_update_fields(data: dict[str, Any], *, is_admin: bool) -> None:
    forbidden = set(data) & FORBIDDEN_PATCH_FIELDS
    if forbidden:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNSUPPORTED_FIELDS")
    allowed = ADMIN_UPDATE_FIELDS if is_admin else STAFF_UPDATE_FIELDS
    unsupported = set(data) - allowed
    if not unsupported:
        return
    if not is_admin and unsupported <= (ADMIN_UPDATE_FIELDS - STAFF_UPDATE_FIELDS):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNSUPPORTED_FIELDS")


def update_ledger(
    db: Session,
    user: User,
    inquiry_id: str,
    payload: Mapping[str, Any] | Any,
    *,
    context: EventContext | None = None,
) -> ClientInquiry:
    inquiry = _get_ledger_record_for_update(db, user, inquiry_id)
    _ensure_mutable(inquiry)
    data = _payload_dict(payload)
    if not data:
        return inquiry

    is_admin = can_view_all_business_ledger(user)
    _validate_update_fields(data, is_admin=is_admin)
    before = ledger_snapshot(inquiry)
    changed_fields: set[str] = set()

    if "stage" in data:
        new_stage = normalize_stage(data["stage"])
        _require_valid_transition(inquiry.stage, new_stage)
        if new_stage != inquiry.stage:
            old_stage = inquiry.stage
            inquiry.stage = new_stage
            changed_fields.add("stage")
            _log_event(
                db,
                inquiry_id=inquiry.inquiry_id,
                event_type=EVENT_TYPE_STAGE_CHANGE,
                operator_id=user.id,
                old_value=old_stage,
                new_value=new_stage,
                before=before,
                after=None,
                context=context,
            )
    if "responder_id" in data:
        responder_id = int(data["responder_id"])
        _ensure_active_user(db, responder_id)
        if responder_id != inquiry.responder_id:
            old_responder_id = str(inquiry.responder_id)
            inquiry.responder_id = responder_id
            changed_fields.add("responder_id")
            _log_event(
                db,
                inquiry_id=inquiry.inquiry_id,
                event_type=EVENT_TYPE_TRANSFER,
                operator_id=user.id,
                old_value=old_responder_id,
                new_value=str(responder_id),
                before=before,
                after=None,
                context=context,
            )
    text_fields = {
        "source": ("source", 64),
        "client_name": ("client_name", 128),
        "client_phone": ("client_phone", 64),
        "notes": ("notes", 2000),
    }
    for field, (attr, max_length) in text_fields.items():
        if field in data:
            new_value = clean_text(data[field], max_length)
            if getattr(inquiry, attr) != new_value:
                setattr(inquiry, attr, new_value)
                changed_fields.add(field)
    if "next_followup_at" in data:
        new_followup = parse_local_datetime(data["next_followup_at"], field_name="next_followup_at")
        if _to_local_naive(inquiry.next_followup_at) != new_followup:
            inquiry.next_followup_at = new_followup
            changed_fields.add("next_followup_at")

    if changed_fields:
        after = ledger_snapshot(inquiry)
        for event in db.new:
            if isinstance(event, ClientInquiryEvent) and event.inquiry_id == inquiry.inquiry_id and event.after_json is None:
                event.after_json = after
        edit_fields = sorted(changed_fields - {"stage", "responder_id"})
        if edit_fields:
            _log_event(
                db,
                inquiry_id=inquiry.inquiry_id,
                event_type=EVENT_TYPE_EDIT,
                operator_id=user.id,
                old_value=",".join(edit_fields)[:64],
                new_value="updated",
                before=before,
                after=after,
                context=context,
            )
    return inquiry


def cancel_ledger(
    db: Session,
    user: User,
    inquiry_id: str,
    reason: str,
    *,
    context: EventContext | None = None,
) -> ClientInquiry:
    require_business_ledger_access(user)
    if not can_view_all_business_ledger(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    inquiry = _get_ledger_record(db, user, inquiry_id, include_cancelled=True)
    reason = clean_text(reason, 2000)
    if not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    _ensure_mutable(inquiry)
    before = ledger_snapshot(inquiry)
    inquiry.cancelled_at = _now_local_naive()
    inquiry.cancelled_by_id = user.id
    inquiry.cancel_reason = reason
    after = ledger_snapshot(inquiry)
    _log_event(
        db,
        inquiry_id=inquiry.inquiry_id,
        event_type=EVENT_TYPE_CANCEL,
        operator_id=user.id,
        old_value=inquiry.stage,
        new_value=reason,
        before=before,
        after=after,
        context=context,
    )
    return inquiry
