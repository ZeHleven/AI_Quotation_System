from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.enterprise_profile import (
    EnterpriseProfileAttachmentIn,
    EnterpriseProfileItemCreateIn,
    EnterpriseProfileItemUpdateIn,
    EnterpriseProfileStatusIn,
)
from app.services.enterprise_profile import (
    activate_profile_item,
    add_profile_attachment,
    archive_profile_item,
    create_profile_item,
    enterprise_profile_summary,
    get_item_by_uuid,
    list_active_profile_candidates,
    list_profile_items,
    require_enterprise_profile_view,
    serialize_attachment,
    serialize_item,
    update_profile_item,
)


router = APIRouter()


def _ensure_enterprise_profile_enabled() -> None:
    if not settings.feature_enterprise_profile:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


@router.get("/admin/enterprise-profile/summary", summary="企业资料库概览")
async def get_enterprise_profile_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    return api_ok(enterprise_profile_summary(db, current_user))


@router.get("/admin/enterprise-profile/items", summary="查询企业资料")
async def list_enterprise_profile_items(
    category: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    missing_attachment: Optional[bool] = None,
    expiring_days: Optional[int] = Query(None, ge=0, le=3650),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    items, total = list_profile_items(
        db,
        current_user,
        category=category,
        status_filter=status_filter,
        keyword=keyword,
        missing_attachment=missing_attachment,
        expiring_days=expiring_days,
        page=page,
        page_size=page_size,
    )
    return api_page([serialize_item(item) for item in items], total=total, page=page, page_size=page_size)


@router.post("/admin/enterprise-profile/items", summary="新建企业资料")
async def create_enterprise_profile_item(
    payload: EnterpriseProfileItemCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    item = create_profile_item(db, current_user, payload)
    db.commit()
    db.refresh(item)
    item = get_item_by_uuid(db, item.item_uuid)
    return api_ok(serialize_item(item, detail=True))


@router.get("/admin/enterprise-profile/items/{item_uuid}", summary="企业资料详情")
async def get_enterprise_profile_item(
    item_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    require_enterprise_profile_view(current_user)
    item = get_item_by_uuid(db, item_uuid)
    return api_ok(serialize_item(item, detail=True))


@router.patch("/admin/enterprise-profile/items/{item_uuid}", summary="更新企业资料")
async def update_enterprise_profile_item(
    item_uuid: str,
    payload: EnterpriseProfileItemUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    item = get_item_by_uuid(db, item_uuid)
    update_profile_item(db, current_user, item, payload)
    db.commit()
    item = get_item_by_uuid(db, item_uuid)
    return api_ok(serialize_item(item, detail=True))


@router.post("/admin/enterprise-profile/items/{item_uuid}/attachments", summary="绑定企业资料附件")
async def add_enterprise_profile_attachment(
    item_uuid: str,
    payload: EnterpriseProfileAttachmentIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    item = get_item_by_uuid(db, item_uuid)
    attachment = add_profile_attachment(db, current_user, item, payload)
    db.commit()
    db.refresh(attachment)
    return api_ok(serialize_attachment(attachment))


@router.post("/admin/enterprise-profile/items/{item_uuid}/activate", summary="启用企业资料")
async def activate_enterprise_profile_item(
    item_uuid: str,
    payload: EnterpriseProfileStatusIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    item = get_item_by_uuid(db, item_uuid)
    activate_profile_item(db, current_user, item, payload.reason)
    db.commit()
    item = get_item_by_uuid(db, item_uuid)
    return api_ok(serialize_item(item, detail=True))


@router.post("/admin/enterprise-profile/items/{item_uuid}/archive", summary="归档企业资料")
async def archive_enterprise_profile_item(
    item_uuid: str,
    payload: EnterpriseProfileStatusIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    item = get_item_by_uuid(db, item_uuid)
    archive_profile_item(db, current_user, item, payload.reason)
    db.commit()
    item = get_item_by_uuid(db, item_uuid)
    return api_ok(serialize_item(item, detail=True))


@router.get("/enterprise-profile/candidates", summary="按需查询可用于投标的企业资料候选")
async def list_enterprise_profile_candidates(
    category: Optional[str] = None,
    keyword: Optional[str] = Query(None, max_length=255),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enterprise_profile_enabled()
    items = list_active_profile_candidates(
        db,
        current_user,
        category=category,
        keyword=keyword,
        limit=limit,
    )
    return api_ok([serialize_item(item) for item in items])
