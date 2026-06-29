from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.cost_item import CostRagSyncRun
from app.models.enterprise_quota import (
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.services.enterprise_quota_cost_reference import ENTERPRISE_QUOTA_REFERENCE_SOURCE, active_enterprise_quota_version
from app.services.enterprise_quota_units import normalize_enterprise_quota_unit


def serialize_enterprise_quota_version(version: EnterpriseQuotaVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "id": version.id,
        "version_code": version.version_code,
        "version_name": version.version_name,
        "source_filename": version.source_filename,
        "source_file_sha256": version.source_file_sha256,
        "status": version.status,
        "is_active": bool(version.is_active),
        "created_by": version.created_by,
        "activated_by": version.activated_by,
        "activated_at": _format_dt(version.activated_at),
        "created_at": _format_dt(version.created_at),
        "updated_at": _format_dt(version.updated_at),
    }


def enterprise_quota_master_summary(db: Session) -> dict[str, Any]:
    version = active_enterprise_quota_version(db)
    by_status = {
        status: int(count or 0)
        for status, count in db.query(EnterpriseQuotaVersion.status, func.count(EnterpriseQuotaVersion.id))
        .group_by(EnterpriseQuotaVersion.status)
        .all()
    }
    if version is None:
        return {
            "source": ENTERPRISE_QUOTA_REFERENCE_SOURCE,
            "active_version": None,
            "by_status": by_status,
            "section_count": 0,
            "quota_item_count": 0,
            "component_count": 0,
            "resource_count": 0,
            "latest_successful_rag_sync": _latest_enterprise_quota_rag_sync(db),
        }

    version_id = int(version.id)
    return {
        "source": ENTERPRISE_QUOTA_REFERENCE_SOURCE,
        "active_version": serialize_enterprise_quota_version(version),
        "by_status": by_status,
        "section_count": _count_for_version(db, EnterpriseQuotaSection, version_id),
        "quota_item_count": _count_for_version(db, EnterpriseQuotaItem, version_id),
        "component_count": _count_for_version(db, EnterpriseQuotaComponent, version_id),
        "resource_count": _count_for_version(db, EnterpriseCostResource, version_id),
        "latest_successful_rag_sync": _latest_enterprise_quota_rag_sync(db),
    }


def list_enterprise_quota_master_items(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    section_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    version = active_enterprise_quota_version(db)
    if version is None:
        return [], 0
    query = (
        db.query(EnterpriseQuotaItem, EnterpriseQuotaSection)
        .outerjoin(EnterpriseQuotaSection, EnterpriseQuotaItem.section_id == EnterpriseQuotaSection.id)
        .filter(EnterpriseQuotaItem.version_id == version.id)
    )
    if section_id:
        query = query.filter(EnterpriseQuotaItem.section_id == section_id)
    keyword_text = _clean_keyword(keyword)
    if keyword_text:
        pattern = f"%{keyword_text}%"
        query = query.filter(
            or_(
                EnterpriseQuotaItem.quota_code.like(pattern),
                EnterpriseQuotaItem.item_name.like(pattern),
                EnterpriseQuotaItem.work_content.like(pattern),
                EnterpriseQuotaItem.worker_or_subtype.like(pattern),
                EnterpriseQuotaSection.section_code.like(pattern),
                EnterpriseQuotaSection.section_name.like(pattern),
            )
        )
    total = query.count()
    rows = (
        query.order_by(EnterpriseQuotaItem.sort_order.asc(), EnterpriseQuotaItem.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    component_counts = _component_counts_for_items(db, [int(item.id) for item, _section in rows if item.id])
    return [
        serialize_enterprise_quota_item(item, section, component_count=component_counts.get(int(item.id), 0))
        for item, section in rows
    ], total


def get_enterprise_quota_master_item_detail(db: Session, item_id: int) -> dict[str, Any] | None:
    version = active_enterprise_quota_version(db)
    if version is None:
        return None
    row = (
        db.query(EnterpriseQuotaItem, EnterpriseQuotaSection)
        .outerjoin(EnterpriseQuotaSection, EnterpriseQuotaItem.section_id == EnterpriseQuotaSection.id)
        .filter(EnterpriseQuotaItem.version_id == version.id, EnterpriseQuotaItem.id == item_id)
        .first()
    )
    if row is None:
        return None
    item, section = row
    components = (
        db.query(EnterpriseQuotaComponent)
        .filter(EnterpriseQuotaComponent.version_id == version.id, EnterpriseQuotaComponent.quota_item_id == item.id)
        .order_by(EnterpriseQuotaComponent.sort_order.asc(), EnterpriseQuotaComponent.id.asc())
        .all()
    )
    data = serialize_enterprise_quota_item(item, section, component_count=len(components))
    data["source"] = ENTERPRISE_QUOTA_REFERENCE_SOURCE
    data["active_version"] = serialize_enterprise_quota_version(version)
    data["components"] = [serialize_enterprise_quota_component(component, item) for component in components]
    return data


def list_enterprise_quota_master_components(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    quota_item_id: int | None = None,
    fee_bucket: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    version = active_enterprise_quota_version(db)
    if version is None:
        return [], 0
    query = (
        db.query(EnterpriseQuotaComponent, EnterpriseQuotaItem)
        .outerjoin(EnterpriseQuotaItem, EnterpriseQuotaComponent.quota_item_id == EnterpriseQuotaItem.id)
        .filter(EnterpriseQuotaComponent.version_id == version.id)
    )
    if quota_item_id:
        query = query.filter(EnterpriseQuotaComponent.quota_item_id == quota_item_id)
    if fee_bucket:
        query = query.filter(EnterpriseQuotaComponent.fee_bucket == fee_bucket)
    keyword_text = _clean_keyword(keyword)
    if keyword_text:
        pattern = f"%{keyword_text}%"
        query = query.filter(
            or_(
                EnterpriseQuotaComponent.parent_quota_code.like(pattern),
                EnterpriseQuotaComponent.component_type.like(pattern),
                EnterpriseQuotaComponent.resource_code.like(pattern),
                EnterpriseQuotaComponent.resource_name.like(pattern),
                EnterpriseQuotaComponent.worker_or_subtype.like(pattern),
                EnterpriseQuotaItem.item_name.like(pattern),
                EnterpriseQuotaItem.quota_code.like(pattern),
            )
        )
    total = query.count()
    rows = (
        query.order_by(EnterpriseQuotaComponent.sort_order.asc(), EnterpriseQuotaComponent.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [serialize_enterprise_quota_component(component, item) for component, item in rows], total


def list_enterprise_quota_master_resources(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    resource_type: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    version = active_enterprise_quota_version(db)
    if version is None:
        return [], 0
    query = db.query(EnterpriseCostResource).filter(EnterpriseCostResource.version_id == version.id)
    if resource_type:
        query = query.filter(EnterpriseCostResource.resource_type == resource_type)
    keyword_text = _clean_keyword(keyword)
    if keyword_text:
        pattern = f"%{keyword_text}%"
        query = query.filter(
            or_(
                EnterpriseCostResource.resource_code.like(pattern),
                EnterpriseCostResource.resource_name.like(pattern),
                EnterpriseCostResource.resource_type.like(pattern),
                EnterpriseCostResource.price_block_label.like(pattern),
            )
        )
    total = query.count()
    rows = (
        query.order_by(EnterpriseCostResource.sort_order.asc(), EnterpriseCostResource.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [serialize_enterprise_cost_resource(resource) for resource in rows], total


def serialize_enterprise_quota_item(
    item: EnterpriseQuotaItem,
    section: EnterpriseQuotaSection | None,
    *,
    component_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": item.id,
        "version_id": item.version_id,
        "section_id": item.section_id,
        "section_code": section.section_code if section else None,
        "section_name": section.section_name if section else None,
        "quota_code": item.quota_code,
        "item_name": item.item_name,
        "work_content": item.work_content,
        "worker_or_subtype": item.worker_or_subtype,
        "unit": normalize_enterprise_quota_unit(item.unit),
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "labor_fee": item.labor_fee,
        "main_material_fee": item.main_material_fee,
        "auxiliary_material_fee": item.auxiliary_material_fee,
        "machinery_fee": item.machinery_fee,
        "component_count": component_count,
        "source_sheet": item.source_sheet,
        "source_row_index": item.source_row_index,
        "sort_order": item.sort_order,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }


def serialize_enterprise_quota_component(
    component: EnterpriseQuotaComponent,
    item: EnterpriseQuotaItem | None,
) -> dict[str, Any]:
    return {
        "id": component.id,
        "version_id": component.version_id,
        "quota_item_id": component.quota_item_id,
        "quota_code": item.quota_code if item else component.parent_quota_code,
        "quota_item_name": item.item_name if item else None,
        "parent_quota_code": component.parent_quota_code,
        "component_type": component.component_type,
        "resource_code": component.resource_code,
        "resource_name": component.resource_name,
        "worker_or_subtype": component.worker_or_subtype,
        "unit": normalize_enterprise_quota_unit(component.unit),
        "quantity": component.quantity,
        "unit_price": component.unit_price,
        "amount": component.amount,
        "fee_bucket": component.fee_bucket,
        "source_sheet": component.source_sheet,
        "source_row_index": component.source_row_index,
        "sort_order": component.sort_order,
        "created_at": _format_dt(component.created_at),
        "updated_at": _format_dt(component.updated_at),
    }


def serialize_enterprise_cost_resource(resource: EnterpriseCostResource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "version_id": resource.version_id,
        "resource_code": resource.resource_code,
        "resource_name": resource.resource_name,
        "resource_type": resource.resource_type,
        "unit": normalize_enterprise_quota_unit(resource.unit),
        "price": resource.price,
        "tax_rate": resource.tax_rate,
        "computed_price": resource.computed_price,
        "price_block_label": resource.price_block_label,
        "source_sheet": resource.source_sheet,
        "source_row_index": resource.source_row_index,
        "sort_order": resource.sort_order,
        "created_at": _format_dt(resource.created_at),
        "updated_at": _format_dt(resource.updated_at),
    }


def _latest_enterprise_quota_rag_sync(db: Session) -> dict[str, Any] | None:
    run = (
        db.query(CostRagSyncRun)
        .filter(CostRagSyncRun.source == ENTERPRISE_QUOTA_REFERENCE_SOURCE, CostRagSyncRun.status == "success")
        .order_by(CostRagSyncRun.finished_at.desc(), CostRagSyncRun.started_at.desc(), CostRagSyncRun.id.desc())
        .first()
    )
    if run is None:
        return None
    return {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "requested_count": run.requested_count,
        "synced_count": run.synced_count,
        "message": run.message,
        "http_status": run.http_status,
        "duration_ms": run.duration_ms,
        "started_at": _format_dt(run.started_at),
        "finished_at": _format_dt(run.finished_at),
    }


def _count_for_version(db: Session, model, version_id: int) -> int:
    return int(db.query(func.count(model.id)).filter(model.version_id == version_id).scalar() or 0)


def _component_counts_for_items(db: Session, item_ids: list[int]) -> dict[int, int]:
    if not item_ids:
        return {}
    rows = (
        db.query(EnterpriseQuotaComponent.quota_item_id, func.count(EnterpriseQuotaComponent.id))
        .filter(EnterpriseQuotaComponent.quota_item_id.in_(item_ids))
        .group_by(EnterpriseQuotaComponent.quota_item_id)
        .all()
    )
    return {int(item_id): int(count or 0) for item_id, count in rows if item_id is not None}


def _clean_keyword(keyword: str | None) -> str:
    return (keyword or "").strip()[:128]


def _format_dt(value) -> str | None:
    return value.isoformat() if value else None
