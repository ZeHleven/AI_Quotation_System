from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.enterprise_quota import (
    QUOTA_VERSION_STATUS_ACTIVE,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.services.enterprise_quota_units import normalize_enterprise_quota_unit


ENTERPRISE_QUOTA_REFERENCE_SOURCE = "enterprise_quota.active"
ENTERPRISE_QUOTA_PRICE_SOURCE = "enterprise_quota_unit_price"


@dataclass(frozen=True)
class EnterpriseQuotaCostReference:
    id: int
    category: str | None
    subcategory: str | None
    item_name: str | None
    spec: str | None
    unit: str | None
    price: float | None
    client_tax_excluded_price: float | None
    client_labor_price: float | None
    client_main_material_price: float | None
    client_auxiliary_material_price: float | None
    client_direct_fee: float | None
    client_management_profit: float | None
    subcontract_composite_price: float | None
    subcontract_labor_price: float | None
    subcontract_main_material_price: float | None
    subcontract_auxiliary_material_price: float | None
    crew_benchmark_price: float | None
    price_type: str
    status: str
    source: str
    effective_date: Any | None
    notes: str | None
    created_by: int | None
    created_at: datetime | None
    updated_at: datetime | None
    reference_source: str
    reference_price_source: str
    source_type: str
    enterprise_quota_version_id: int
    enterprise_quota_version_code: str
    enterprise_quota_version_name: str
    enterprise_quota_item_id: int
    quota_code: str | None
    section_id: int | None
    section_code: str | None
    section_name: str | None
    work_content: str | None


def active_enterprise_quota_version(db: Session) -> EnterpriseQuotaVersion | None:
    return (
        db.query(EnterpriseQuotaVersion)
        .filter(
            EnterpriseQuotaVersion.status == QUOTA_VERSION_STATUS_ACTIVE,
            EnterpriseQuotaVersion.is_active.is_(True),
        )
        .order_by(EnterpriseQuotaVersion.activated_at.desc(), EnterpriseQuotaVersion.id.desc())
        .first()
    )


def load_active_enterprise_quota_cost_references(db: Session) -> list[EnterpriseQuotaCostReference]:
    version = active_enterprise_quota_version(db)
    if version is None:
        return []

    rows = (
        db.query(EnterpriseQuotaItem, EnterpriseQuotaSection)
        .outerjoin(EnterpriseQuotaSection, EnterpriseQuotaItem.section_id == EnterpriseQuotaSection.id)
        .filter(EnterpriseQuotaItem.version_id == version.id)
        .order_by(EnterpriseQuotaItem.updated_at.desc(), EnterpriseQuotaItem.id.desc())
        .all()
    )
    return [_reference_from_item(version, item, section) for item, section in rows]


def search_active_enterprise_quota_cost_references(
    db: Session,
    keyword: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[EnterpriseQuotaCostReference], int]:
    keyword_text = (keyword or "").strip().lower()
    if not keyword_text:
        return [], 0

    matched = [
        item
        for item in load_active_enterprise_quota_cost_references(db)
        if _matches_keyword(item, keyword_text)
    ]
    offset = max(0, page - 1) * page_size
    return matched[offset : offset + page_size], len(matched)


def _reference_from_item(
    version: EnterpriseQuotaVersion,
    item: EnterpriseQuotaItem,
    section: EnterpriseQuotaSection | None,
) -> EnterpriseQuotaCostReference:
    direct_fee = _sum_optional(item.labor_fee, item.main_material_fee, item.auxiliary_material_fee, item.machinery_fee)
    notes_parts = [
        f"source={ENTERPRISE_QUOTA_REFERENCE_SOURCE}",
        f"version_code={version.version_code}",
    ]
    if item.quota_code:
        notes_parts.append(f"quota_code={item.quota_code}")
    if item.work_content:
        notes_parts.append(f"work_content={item.work_content}")

    return EnterpriseQuotaCostReference(
        id=int(item.id),
        category=section.section_name if section else None,
        subcategory=item.worker_or_subtype,
        item_name=item.item_name,
        spec=item.work_content,
        unit=normalize_enterprise_quota_unit(item.unit),
        price=item.unit_price,
        client_tax_excluded_price=None,
        client_labor_price=item.labor_fee,
        client_main_material_price=item.main_material_fee,
        client_auxiliary_material_price=item.auxiliary_material_fee,
        client_direct_fee=direct_fee,
        client_management_profit=None,
        subcontract_composite_price=None,
        subcontract_labor_price=item.labor_fee,
        subcontract_main_material_price=item.main_material_fee,
        subcontract_auxiliary_material_price=item.auxiliary_material_fee,
        crew_benchmark_price=None,
        price_type="combined",
        status="active",
        source=ENTERPRISE_QUOTA_REFERENCE_SOURCE,
        effective_date=None,
        notes="\n".join(notes_parts),
        created_by=version.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
        reference_source=ENTERPRISE_QUOTA_REFERENCE_SOURCE,
        reference_price_source=ENTERPRISE_QUOTA_PRICE_SOURCE,
        source_type="enterprise_quota_item",
        enterprise_quota_version_id=int(version.id),
        enterprise_quota_version_code=version.version_code,
        enterprise_quota_version_name=version.version_name,
        enterprise_quota_item_id=int(item.id),
        quota_code=item.quota_code,
        section_id=int(section.id) if section and section.id else None,
        section_code=section.section_code if section else None,
        section_name=section.section_name if section else None,
        work_content=item.work_content,
    )


def _matches_keyword(item: EnterpriseQuotaCostReference, keyword: str) -> bool:
    values = (
        item.item_name,
        item.spec,
        item.category,
        item.subcategory,
        item.quota_code,
        item.section_code,
        item.section_name,
        item.work_content,
    )
    return any(keyword in str(value).lower() for value in values if value not in (None, ""))


def _sum_optional(*values: float | None) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(present), 6)
