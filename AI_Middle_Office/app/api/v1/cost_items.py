from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.cost_item import (
    CostItemArchiveIn,
    CostItemBulkStatusIn,
    CostItemCreateIn,
    CostItemImportConfirmIn,
    CostItemUpdateIn,
    CostItemWithdrawIn,
)
from app.services.cost_rag_sync import (
    list_cost_rag_sync_runs,
    serialize_cost_rag_sync_run,
    sync_active_cost_items_to_rag,
)
from app.services.cost_items import (
    activate_cost_item,
    archive_cost_item,
    build_import_preview,
    bulk_update_cost_item_status,
    confirm_import_batch,
    create_cost_item,
    get_cost_item,
    import_preview_response,
    list_cost_items,
    require_cost_db_manager,
    serialize_cost_item,
    update_cost_item,
    withdraw_cost_item_activation,
)


router = APIRouter()


def _ensure_cost_db_enabled() -> None:
    if not settings.feature_cost_db:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _payload_dict(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _parse_statuses(status_value: str | None) -> list[str] | None:
    if not status_value:
        return None
    statuses = [item.strip() for item in status_value.split(",") if item.strip()]
    return statuses or None


@router.post("/admin/cost-items", summary="新建成本条目")
async def create_admin_cost_item(
    payload: CostItemCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = create_cost_item(db, current_user, _payload_dict(payload))
    db.commit()
    db.refresh(item)
    return api_ok(serialize_cost_item(item))


@router.get("/admin/cost-items", summary="查询成本条目")
async def list_admin_cost_items(
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    price_type: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    items, total = list_cost_items(
        db,
        current_user,
        category=category,
        subcategory=subcategory,
        statuses=_parse_statuses(status_filter),
        price_type=price_type,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return api_page([serialize_cost_item(item) for item in items], total=total, page=page, page_size=page_size)


@router.post("/admin/cost-items/sync-rag", summary="同步 active 成本条目至 RAG")
async def sync_active_cost_items_to_rag_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_manager(current_user)
    result = await sync_active_cost_items_to_rag(db, current_user.username, user_id=current_user.id)
    if result["success"]:
        return api_ok(
            {
                "synced_count": result["synced_count"],
                "source": result["source"],
                "run": result.get("run"),
            },
            message=result["message"],
        )
    if result["error"] == "NO_ACTIVE_COST_ITEMS":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
    if "超时" in (result["error"] or ""):
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=result["error"])
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["error"])


@router.get("/admin/cost-items/sync-rag/runs", summary="查询成本库 RAG 同步记录")
async def list_admin_cost_rag_sync_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_manager(current_user)
    runs, total = list_cost_rag_sync_runs(db, page=page, page_size=page_size)
    return api_page(
        [serialize_cost_rag_sync_run(run) for run in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/admin/cost-items/bulk-status", summary="批量更新成本条目状态")
async def bulk_update_admin_cost_item_status(
    payload: CostItemBulkStatusIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    result = bulk_update_cost_item_status(
        db,
        current_user,
        payload.item_ids,
        payload.target_status,
        reason=payload.reason,
    )
    db.commit()
    return api_ok(result, message=f"已更新 {result['changed_count']} 条成本条目")


@router.get("/admin/cost-items/{item_id}", summary="查询成本条目详情")
async def get_admin_cost_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = get_cost_item(db, current_user, item_id)
    return api_ok(serialize_cost_item(item, include_history=True))


@router.patch("/admin/cost-items/{item_id}", summary="更新成本条目")
async def update_admin_cost_item(
    item_id: int,
    payload: CostItemUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = update_cost_item(db, current_user, item_id, _payload_dict(payload))
    db.commit()
    db.refresh(item)
    return api_ok(serialize_cost_item(item, include_history=True))


@router.post("/admin/cost-items/{item_id}/activate", summary="核定成本条目")
async def activate_admin_cost_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = activate_cost_item(db, current_user, item_id)
    db.commit()
    db.refresh(item)
    return api_ok(serialize_cost_item(item, include_history=True))


@router.post("/admin/cost-items/{item_id}/archive", summary="停用成本条目")
async def archive_admin_cost_item(
    item_id: int,
    payload: CostItemArchiveIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = archive_cost_item(db, current_user, item_id, reason=payload.reason)
    db.commit()
    db.refresh(item)
    return api_ok(serialize_cost_item(item, include_history=True))


@router.post("/admin/cost-items/{item_id}/withdraw", summary="撤回启用成本条目")
async def withdraw_admin_cost_item_activation(
    item_id: int,
    payload: CostItemWithdrawIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = withdraw_cost_item_activation(db, current_user, item_id, reason=payload.reason)
    db.commit()
    db.refresh(item)
    return api_ok(serialize_cost_item(item, include_history=True))


@router.post("/admin/cost-items/import/preview", summary="成本条目 Excel 导入预览")
async def preview_cost_items_import(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    content = await file.read()
    batch = build_import_preview(db, current_user, content)
    return api_ok(import_preview_response(batch))


@router.post("/admin/cost-items/import/confirm", summary="确认成本条目导入")
async def confirm_cost_items_import(
    payload: CostItemImportConfirmIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    result = confirm_import_batch(db, current_user, payload.batch_id)
    db.commit()
    return api_ok(result)
