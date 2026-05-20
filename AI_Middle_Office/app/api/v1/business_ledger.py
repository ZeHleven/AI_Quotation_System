from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.client_inquiry import ClientInquiry
from app.models.user import User
from app.schemas.business_ledger import BusinessLedgerCancelIn, BusinessLedgerCreateIn, BusinessLedgerUpdateIn
from app.services.business_ledger import (
    EventContext,
    cancel_ledger,
    create_outbound,
    get_ledger,
    ledger_snapshot,
    list_ledger,
    update_ledger,
)


router = APIRouter()


def _require_feature_enabled() -> None:
    if not settings.feature_business_ledger:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NOT_FOUND")


def _build_event_context(request: Request) -> EventContext:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    ip_address = None
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip() or None
    if not ip_address and request.client:
        ip_address = request.client.host
    user_agent = request.headers.get("User-Agent")
    trace_id = request.headers.get("X-Trace-Id") or getattr(request.state, "trace_id", None)
    return EventContext(
        ip_address=ip_address[:64] if ip_address else None,
        user_agent=user_agent[:512] if user_agent else None,
        trace_id=trace_id[:64] if trace_id else None,
    )


def _payload_dict(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _user_map(db: Session, inquiries: list[ClientInquiry]) -> dict[int, str]:
    user_ids = {item.responder_id for item in inquiries if item.responder_id}
    user_ids.update(item.cancelled_by_id for item in inquiries if item.cancelled_by_id)
    if not user_ids:
        return {}
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    return {user.id: user.username for user in users}


def _serialize_ledger(inquiry: ClientInquiry, *, users: dict[int, str] | None = None) -> dict:
    users = users or {}
    data = ledger_snapshot(inquiry)
    data["responder_username"] = users.get(inquiry.responder_id) if inquiry.responder_id else None
    data["cancelled_by_username"] = users.get(inquiry.cancelled_by_id) if inquiry.cancelled_by_id else None
    return data


@router.post("/business-ledger", summary="新建商务台账记录")
async def create_business_ledger(
    payload: BusinessLedgerCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature_enabled()
    inquiry = create_outbound(
        db,
        current_user,
        _payload_dict(payload),
        context=_build_event_context(request),
    )
    db.commit()
    db.refresh(inquiry)
    return api_ok(_serialize_ledger(inquiry, users=_user_map(db, [inquiry])))


@router.get("/business-ledger", summary="查询商务台账记录")
async def list_business_ledgers(
    stage: Optional[str] = None,
    source: Optional[str] = None,
    responder_id: Optional[int] = None,
    overdue_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature_enabled()
    items, total = list_ledger(
        db,
        current_user,
        stage=stage,
        source=source,
        responder_id=responder_id,
        overdue_only=overdue_only,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    users = _user_map(db, items)
    return api_page(
        [_serialize_ledger(item, users=users) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/business-ledger/{inquiry_id}", summary="查询商务台账详情")
async def get_business_ledger(
    inquiry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature_enabled()
    inquiry = get_ledger(db, current_user, inquiry_id)
    return api_ok(_serialize_ledger(inquiry, users=_user_map(db, [inquiry])))


@router.patch("/business-ledger/{inquiry_id}", summary="更新商务台账记录")
async def update_business_ledger(
    inquiry_id: str,
    payload: BusinessLedgerUpdateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature_enabled()
    inquiry = update_ledger(
        db,
        current_user,
        inquiry_id,
        _payload_dict(payload),
        context=_build_event_context(request),
    )
    db.commit()
    db.refresh(inquiry)
    return api_ok(_serialize_ledger(inquiry, users=_user_map(db, [inquiry])))


@router.post("/business-ledger/{inquiry_id}/cancel", summary="作废商务台账记录")
async def cancel_business_ledger(
    inquiry_id: str,
    payload: BusinessLedgerCancelIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature_enabled()
    inquiry = cancel_ledger(
        db,
        current_user,
        inquiry_id,
        payload.reason,
        context=_build_event_context(request),
    )
    db.commit()
    db.refresh(inquiry)
    return api_ok(_serialize_ledger(inquiry, users=_user_map(db, [inquiry])))
