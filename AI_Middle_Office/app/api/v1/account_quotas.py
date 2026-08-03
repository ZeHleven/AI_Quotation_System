from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import require_admin
from app.models.user import User
from app.schemas.account_quota import (
    AccountQuotaBatchStatusIn,
    AccountQuotaCreateIn,
    AccountQuotaStatusIn,
    AccountQuotaUpdateIn,
)
from app.services.account_quotas import (
    AccountQuotaError,
    batch_change_account_quota_status,
    change_account_quota_status,
    create_account_quota_item,
    get_account_quota_item,
    list_account_quota_history,
    list_account_quota_items,
    serialize_account_quota_history,
    serialize_account_quota_item,
    update_account_quota_item,
)
from app.services.account_tenancy import AccountTenancyError


router = APIRouter()


def _ensure_feature_enabled() -> None:
    if not settings.feature_account_quotas:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _http_error(exc: AccountQuotaError | AccountTenancyError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/admin/account-quotas", summary="查询当前账号定额")
async def list_account_quotas_endpoint(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    source: Optional[str] = Query(default=None, max_length=32),
    detail_type: Optional[str] = Query(default=None, max_length=32),
    keyword: Optional[str] = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        rows, total = list_account_quota_items(
            db,
            current_user,
            status_filter=status_filter,
            source=source,
            detail_type=detail_type,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    except (AccountQuotaError, AccountTenancyError) as exc:
        raise _http_error(exc) from exc
    return api_page(
        [serialize_account_quota_item(item) for item in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/admin/account-quotas", summary="新建当前账号定额")
async def create_account_quota_endpoint(
    payload: AccountQuotaCreateIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        item = create_account_quota_item(db, current_user, payload)
        db.commit()
        db.refresh(item)
    except (AccountQuotaError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_account_quota_item(item), message="账号定额已创建")


@router.post("/admin/account-quotas/status/batch", summary="批量流转当前账号定额状态")
async def batch_change_account_quota_status_endpoint(
    payload: AccountQuotaBatchStatusIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        items = batch_change_account_quota_status(db, current_user, payload)
        db.commit()
        for item in items:
            db.refresh(item)
    except (AccountQuotaError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        {
            "target_status": payload.target_status,
            "updated_count": len(items),
            "items": [serialize_account_quota_item(item) for item in items],
        },
        message="账户定额批量状态已更新",
    )


@router.get("/admin/account-quotas/{item_identifier}", summary="查看当前账号定额详情")
async def get_account_quota_endpoint(
    item_identifier: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        item = get_account_quota_item(db, current_user, item_identifier)
    except (AccountQuotaError, AccountTenancyError) as exc:
        raise _http_error(exc) from exc
    return api_ok(serialize_account_quota_item(item))


@router.patch("/admin/account-quotas/{item_identifier}", summary="修改当前账号定额")
async def update_account_quota_endpoint(
    item_identifier: str,
    payload: AccountQuotaUpdateIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        item = update_account_quota_item(db, current_user, item_identifier, payload)
        db.commit()
        db.refresh(item)
    except (AccountQuotaError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_account_quota_item(item), message="账号定额已更新")


@router.post("/admin/account-quotas/{item_identifier}/status", summary="流转当前账号定额状态")
async def change_account_quota_status_endpoint(
    item_identifier: str,
    payload: AccountQuotaStatusIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        item = change_account_quota_status(db, current_user, item_identifier, payload)
        db.commit()
        db.refresh(item)
    except (AccountQuotaError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_account_quota_item(item), message="账号定额状态已更新")


@router.get("/admin/account-quotas/{item_identifier}/history", summary="查询当前账号定额历史")
async def list_account_quota_history_endpoint(
    item_identifier: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    try:
        rows, total = list_account_quota_history(
            db,
            current_user,
            item_identifier,
            page=page,
            page_size=page_size,
        )
    except (AccountQuotaError, AccountTenancyError) as exc:
        raise _http_error(exc) from exc
    return api_page(
        [serialize_account_quota_history(history) for history in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
