from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.cost_item import (
    CostItemActivateIn,
    CostItemArchiveIn,
    CostItemBulkStatusIn,
    CostItemCreateIn,
    CostItemImportConfirmIn,
    CostItemUpdateIn,
    CostItemWithdrawIn,
)
from app.services.cost_rag_sync import (
    cost_rag_sync_status_summary,
    list_cost_rag_sync_runs,
    preview_active_cost_items_rag_sync,
    serialize_cost_rag_sync_run,
    sync_active_cost_items_to_rag,
)
from app.services.cost_audit import (
    list_cost_audit_logs,
    record_cost_audit,
    serialize_cost_audit_log,
)
from app.services.cost_items import (
    activate_cost_item,
    archive_cost_item,
    build_import_preview,
    bulk_update_cost_item_status,
    can_access_cost_db,
    confirm_import_batch,
    cost_item_lineage_summary,
    create_cost_item,
    export_cost_items,
    get_cost_item_lineage,
    get_cost_item,
    import_preview_response,
    list_quote_cost_candidates,
    list_cost_item_lineage,
    list_cost_items,
    require_cost_db_access,
    require_cost_db_approver,
    serialize_cost_item,
    serialize_quote_cost_candidate,
    update_cost_item,
    withdraw_cost_item_activation,
)
from app.services.enterprise_quota_master import (
    enterprise_quota_master_summary,
    get_enterprise_quota_master_item_detail,
    list_enterprise_quota_master_components,
    list_enterprise_quota_master_items,
    list_enterprise_quota_master_resources,
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


def _cost_filter_context(
    *,
    category: str | None = None,
    subcategory: str | None = None,
    status_filter: str | None = None,
    price_type: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
) -> dict:
    return {
        "category": category,
        "subcategory": subcategory,
        "status": status_filter,
        "price_type": price_type,
        "source": source,
        "keyword": keyword,
    }


def _cost_items_csv(items) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "类别",
            "子类",
            "施工项目",
            "规格/项目特征",
            "单位",
            "主参考价",
            "对甲税前综合单价",
            "对甲人工费",
            "对甲主材费",
            "对甲辅材费",
            "对甲直接费小计",
            "对甲管理费利润",
            "劳务发包综合单价",
            "劳务人工费",
            "劳务主材费",
            "劳务辅材费",
            "班组标底税前价",
            "价格类型",
            "状态",
            "来源",
            "生效日期",
            "备注",
            "更新时间",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item.id,
                item.category,
                item.subcategory,
                item.item_name,
                item.spec,
                item.unit,
                item.price,
                item.client_tax_excluded_price,
                item.client_labor_price,
                item.client_main_material_price,
                item.client_auxiliary_material_price,
                item.client_direct_fee,
                item.client_management_profit,
                item.subcontract_composite_price,
                item.subcontract_labor_price,
                item.subcontract_main_material_price,
                item.subcontract_auxiliary_material_price,
                item.crew_benchmark_price,
                item.price_type,
                item.status,
                item.source,
                item.effective_date.isoformat() if item.effective_date else "",
                item.notes,
                item.updated_at.isoformat() if item.updated_at else "",
            ]
        )
    return "\ufeff" + output.getvalue()


@router.post("/admin/cost-items", summary="新建成本条目")
async def create_admin_cost_item(
    payload: CostItemCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = create_cost_item(db, current_user, _payload_dict(payload))
    db.commit()
    db.refresh(item)
    data = serialize_cost_item(item)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.create",
        resource_id=item.id,
        status_value="success",
        message="created draft cost item",
        request=request,
    )
    return api_ok(data)


@router.get("/admin/cost-items", summary="查询成本条目")
async def list_admin_cost_items(
    request: Request,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    price_type: Optional[str] = None,
    source: Optional[str] = None,
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
        source=source,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    rows = [serialize_cost_item(item) for item in items]
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.list",
        resource_type="cost_item",
        filters=_cost_filter_context(
            category=category,
            subcategory=subcategory,
            status_filter=status_filter,
            price_type=price_type,
            source=source,
            keyword=keyword,
        ),
        result_count=total,
        status_value="success",
        request=request,
    )
    return api_page(rows, total=total, page=page, page_size=page_size)


@router.get("/cost-items/quote-candidates", summary="报价预审按需查询 active 成本候选")
async def list_quote_cost_item_candidates(
    keyword: str = Query(..., min_length=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    items, total = list_quote_cost_candidates(
        db,
        current_user,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    include_full_cost = can_access_cost_db(current_user)
    return api_page(
        [serialize_quote_cost_candidate(item, include_full_cost=include_full_cost) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/cost-master/summary", summary="查询企业定额成本主库汇总")
async def get_admin_cost_master_summary(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    data = enterprise_quota_master_summary(db)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_master.summary",
        resource_type="enterprise_quota",
        result_count=data.get("quota_item_count"),
        status_value="success",
        message=data.get("source"),
        request=request,
    )
    return api_ok(data)


@router.get("/admin/cost-master/quota-items", summary="查询企业定额主项")
async def list_admin_cost_master_quota_items(
    request: Request,
    keyword: Optional[str] = None,
    section_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    rows, total = list_enterprise_quota_master_items(
        db,
        keyword=keyword,
        section_id=section_id,
        page=page,
        page_size=page_size,
    )
    record_cost_audit(
        db,
        user=current_user,
        action="cost_master.quota_items",
        resource_type="enterprise_quota_item",
        filters={"keyword": keyword, "section_id": section_id},
        result_count=total,
        status_value="success",
        request=request,
    )
    return api_page(rows, total=total, page=page, page_size=page_size)


@router.get("/admin/cost-master/quota-items/{item_id}", summary="查询企业定额主项详情")
async def get_admin_cost_master_quota_item(
    item_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    data = get_enterprise_quota_master_item_detail(db, item_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RESOURCE_NOT_FOUND")
    record_cost_audit(
        db,
        user=current_user,
        action="cost_master.quota_item_detail",
        resource_type="enterprise_quota_item",
        resource_id=item_id,
        result_count=1,
        status_value="success",
        request=request,
    )
    return api_ok(data)


@router.get("/admin/cost-master/components", summary="查询企业定额组成明细")
async def list_admin_cost_master_components(
    request: Request,
    keyword: Optional[str] = None,
    quota_item_id: Optional[int] = None,
    fee_bucket: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    rows, total = list_enterprise_quota_master_components(
        db,
        keyword=keyword,
        quota_item_id=quota_item_id,
        fee_bucket=fee_bucket,
        page=page,
        page_size=page_size,
    )
    record_cost_audit(
        db,
        user=current_user,
        action="cost_master.components",
        resource_type="enterprise_quota_component",
        filters={"keyword": keyword, "quota_item_id": quota_item_id, "fee_bucket": fee_bucket},
        result_count=total,
        status_value="success",
        request=request,
    )
    return api_page(rows, total=total, page=page, page_size=page_size)


@router.get("/admin/cost-master/resources", summary="查询企业定额资源价格")
async def list_admin_cost_master_resources(
    request: Request,
    keyword: Optional[str] = None,
    resource_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    rows, total = list_enterprise_quota_master_resources(
        db,
        keyword=keyword,
        resource_type=resource_type,
        page=page,
        page_size=page_size,
    )
    record_cost_audit(
        db,
        user=current_user,
        action="cost_master.resources",
        resource_type="enterprise_cost_resource",
        filters={"keyword": keyword, "resource_type": resource_type},
        result_count=total,
        status_value="success",
        request=request,
    )
    return api_page(rows, total=total, page=page, page_size=page_size)


@router.get("/admin/cost-items/export", summary="导出成本条目")
async def export_admin_cost_items(
    request: Request,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    price_type: Optional[str] = None,
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    filter_context = _cost_filter_context(
        category=category,
        subcategory=subcategory,
        status_filter=status_filter,
        price_type=price_type,
        source=source,
        keyword=keyword,
    )
    items, total = export_cost_items(
        db,
        current_user,
        category=category,
        subcategory=subcategory,
        statuses=_parse_statuses(status_filter),
        price_type=price_type,
        source=source,
        keyword=keyword,
    )
    content = _cost_items_csv(items)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.export",
        resource_type="cost_item",
        filters={**filter_context, "total_count": total, "exported_count": len(items)},
        result_count=len(items),
        status_value="success",
        message="exported cost items csv",
        request=request,
    )
    filename = f"cost_items_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/cost-items/audit-logs", summary="查询成本库敏感操作审计")
async def list_admin_cost_audit_logs(
    action: Optional[str] = None,
    username: Optional[str] = None,
    resource_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    rows, total = list_cost_audit_logs(
        db,
        current_user,
        action=action,
        username=username,
        resource_id=resource_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    return api_page(
        [serialize_cost_audit_log(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/cost-items/lineage/summary", summary="成本库状态与流向汇总")
async def cost_item_lineage_summary_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    data = cost_item_lineage_summary(db, current_user)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.lineage_summary",
        resource_type="cost_lineage",
        result_count=data.get("total_count"),
        status_value="success",
        request=request,
    )
    return api_ok(data)


@router.get("/admin/cost-items/lineage", summary="查询成本库状态与流向")
async def list_cost_item_lineage_admin(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    has_quote_usage: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    rows, total = list_cost_item_lineage(
        db,
        current_user,
        statuses=_parse_statuses(status_filter),
        source=source,
        keyword=keyword,
        has_quote_usage=has_quote_usage,
        page=page,
        page_size=page_size,
    )
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.lineage_list",
        resource_type="cost_lineage",
        filters={
            "status": status_filter,
            "source": source,
            "keyword": keyword,
            "has_quote_usage": has_quote_usage,
        },
        result_count=total,
        status_value="success",
        request=request,
    )
    return api_page(rows, total=total, page=page, page_size=page_size)


@router.post("/admin/cost-items/sync-rag", summary="同步 active 成本条目至 RAG")
async def sync_active_cost_items_to_rag_admin(
    request: Request,
    dry_run: bool = Query(False),
    sample_limit: int = Query(5, ge=0, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_approver(current_user)
    if dry_run:
        result = preview_active_cost_items_rag_sync(db, sample_limit=sample_limit)
        record_cost_audit(
            db,
            user=current_user,
            action="cost_rag.dry_run",
            resource_type="cost_rag",
            result_count=result.get("requested_count"),
            status_value="success",
            message=result.get("source"),
            request=request,
        )
        return api_ok(result, message=result["message"])

    result = await sync_active_cost_items_to_rag(db, current_user.username, user_id=current_user.id)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_rag.sync",
        resource_type="cost_rag",
        result_count=result.get("synced_count"),
        status_value="success" if result["success"] else "failed",
        message=result.get("message") or result.get("error"),
        request=request,
    )
    if result["success"]:
        return api_ok(
            {
                "synced_count": result["synced_count"],
                "source": result["source"],
                "source_detail": result.get("source_detail"),
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
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    runs, total = list_cost_rag_sync_runs(db, page=page, page_size=page_size)
    rows = [serialize_cost_rag_sync_run(run) for run in runs]
    record_cost_audit(
        db,
        user=current_user,
        action="cost_rag.runs",
        resource_type="cost_rag",
        result_count=total,
        status_value="success",
        request=request,
    )
    return api_page(
        rows,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/cost-items/sync-rag/status", summary="查询成本库 RAG 同步状态")
async def get_admin_cost_rag_sync_status(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    require_cost_db_access(current_user)
    data = cost_rag_sync_status_summary(db)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_rag.status",
        resource_type="cost_rag",
        result_count=data.get("active_count"),
        status_value="success",
        message=data.get("status"),
        request=request,
    )
    return api_ok(data)


@router.get("/admin/cost-items/{item_id}/lineage", summary="查询成本条目来源与流向详情")
async def get_cost_item_lineage_admin(
    item_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    data = get_cost_item_lineage(db, current_user, item_id)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.lineage_detail",
        resource_type="cost_item",
        resource_id=item_id,
        status_value="success",
        request=request,
    )
    return api_ok(data)


@router.post("/admin/cost-items/bulk-status", summary="批量更新成本条目状态")
async def bulk_update_admin_cost_item_status(
    payload: CostItemBulkStatusIn,
    request: Request,
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
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.bulk_status",
        resource_type="cost_item",
        filters={"item_ids": payload.item_ids, "target_status": payload.target_status},
        result_count=result.get("changed_count"),
        status_value="success",
        message=f"bulk status to {payload.target_status}",
        request=request,
    )
    return api_ok(result, message=f"已更新 {result['changed_count']} 条成本条目")


@router.get("/admin/cost-items/{item_id}", summary="查询成本条目详情")
async def get_admin_cost_item(
    item_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = get_cost_item(db, current_user, item_id)
    data = serialize_cost_item(item, include_history=True)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.detail",
        resource_type="cost_item",
        resource_id=item_id,
        status_value="success",
        request=request,
    )
    return api_ok(data)


@router.patch("/admin/cost-items/{item_id}", summary="更新成本条目")
async def update_admin_cost_item(
    item_id: int,
    payload: CostItemUpdateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = update_cost_item(db, current_user, item_id, _payload_dict(payload))
    db.commit()
    db.refresh(item)
    data = serialize_cost_item(item, include_history=True)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.update",
        resource_type="cost_item",
        resource_id=item_id,
        status_value="success",
        message="updated cost item",
        request=request,
    )
    return api_ok(data)


@router.post("/admin/cost-items/{item_id}/activate", summary="核定成本条目")
async def activate_admin_cost_item(
    item_id: int,
    request: Request,
    payload: CostItemActivateIn | None = Body(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = activate_cost_item(db, current_user, item_id, reason=payload.reason if payload else None)
    db.commit()
    db.refresh(item)
    data = serialize_cost_item(item, include_history=True)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.activate",
        resource_type="cost_item",
        resource_id=item_id,
        status_value="success",
        message="activated cost item",
        request=request,
    )
    return api_ok(data)


@router.post("/admin/cost-items/{item_id}/archive", summary="停用成本条目")
async def archive_admin_cost_item(
    item_id: int,
    payload: CostItemArchiveIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = archive_cost_item(db, current_user, item_id, reason=payload.reason)
    db.commit()
    db.refresh(item)
    data = serialize_cost_item(item, include_history=True)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.archive",
        resource_type="cost_item",
        resource_id=item_id,
        status_value="success",
        message="archived cost item",
        request=request,
    )
    return api_ok(data)


@router.post("/admin/cost-items/{item_id}/withdraw", summary="撤回启用成本条目")
async def withdraw_admin_cost_item_activation(
    item_id: int,
    payload: CostItemWithdrawIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    item = withdraw_cost_item_activation(db, current_user, item_id, reason=payload.reason)
    db.commit()
    db.refresh(item)
    data = serialize_cost_item(item, include_history=True)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.withdraw",
        resource_type="cost_item",
        resource_id=item_id,
        status_value="success",
        message="withdrew active cost item",
        request=request,
    )
    return api_ok(data)


@router.post("/admin/cost-items/import/preview", summary="成本条目 Excel 导入预览")
async def preview_cost_items_import(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    content = await file.read()
    batch = build_import_preview(db, current_user, content)
    data = import_preview_response(batch)
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.import_preview",
        resource_type="cost_import",
        resource_id=batch.batch_id,
        result_count=data.get("item_count"),
        status_value="success",
        message=file.filename,
        request=request,
    )
    return api_ok(data)


@router.post("/admin/cost-items/import/confirm", summary="确认成本条目导入")
async def confirm_cost_items_import(
    payload: CostItemImportConfirmIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_cost_db_enabled()
    result = confirm_import_batch(db, current_user, payload.batch_id)
    db.commit()
    record_cost_audit(
        db,
        user=current_user,
        action="cost_item.import_confirm",
        resource_type="cost_import",
        resource_id=payload.batch_id,
        result_count=(result.get("created_count") or 0) + (result.get("updated_count") or 0),
        status_value="success",
        message="confirmed cost import",
        request=request,
    )
    return api_ok(result)
