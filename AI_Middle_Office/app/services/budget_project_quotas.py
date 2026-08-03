"""Project-local quota composition workbench.

The service materializes a mutable project snapshot from the selected pricing
source.  Resource CRUD recalculates the project quota and its pricing-draft
line.  Enterprise master data is only touched by ``sync_project_quota_to_enterprise``,
which writes to a versioned enterprise draft after the API permission gate.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.budget_pricing_draft import (
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftLine,
)
from app.models.budget_project import BudgetProjectProfile
from app.models.budget_project_quota import (
    BudgetProjectQuotaEvent,
    BudgetProjectQuotaResource,
    BudgetProjectQuotaSnapshot,
)
from app.models.enterprise_quota import (
    QUOTA_VERSION_STATUS_DRAFT,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaSheetRow,
    EnterpriseQuotaVersion,
    EnterpriseQuotaVersionEvent,
)
from app.models.user import User
from app.services.budget_pricing import BudgetPricingError
from app.services.budget_pricing_drafts import (
    get_budget_pricing_draft_line,
    get_current_budget_pricing_draft,
    patch_budget_pricing_draft_line,
)
from app.services.enterprise_quota_v2_parser import (
    ENTERPRISE_SHEET,
    LABOR_SHEET,
    MATERIAL_SHEET,
)
from app.services.enterprise_quota_v2_workbench import (
    clone_version_to_draft,
    compact_json,
    recalculate_version,
)


PROJECT_QUOTA_FEE_BUCKETS = {
    "labor",
    "main_material",
    "auxiliary_material",
    "machinery",
}
_BUCKET_LABELS = {
    "labor": "人工",
    "main_material": "主材",
    "auxiliary_material": "辅材",
    "machinery": "机械",
}
_BREAKDOWN_KEYS = {
    "labor": "labor_unit_cost",
    "main_material": "main_material_unit_cost",
    "auxiliary_material": "auxiliary_material_unit_cost",
    "machinery": "machinery_unit_cost",
}
_SIX = Decimal("0.000001")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _text(value: Any, limit: int | None = None) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return cleaned[:limit] if limit else cleaned


def _decimal(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    if not parsed.is_finite():
        return default
    return parsed


def _q6(value: Any) -> Decimal:
    parsed = _decimal(value, default=Decimal("0")) or Decimal("0")
    return parsed.quantize(_SIX, rounding=ROUND_HALF_UP)


def _decimal_text(value: Any) -> str:
    return format(_q6(value), "f")


def _format_dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _resource_type_for_bucket(bucket: str) -> str:
    return {
        "labor": "labor",
        "main_material": "main_material",
        "auxiliary_material": "auxiliary_material",
        "machinery": "machinery",
    }.get(bucket, "unknown")


def _component_type_for_bucket(bucket: str) -> str:
    return _BUCKET_LABELS.get(bucket, "其他")


def _library_kind_for_bucket(bucket: str) -> str:
    return "labor" if bucket == "labor" else "material"


def _section_path(db: Session, item: EnterpriseQuotaItem | None, line: BudgetProjectPricingDraftLine) -> list[dict[str, Any]]:
    if item is not None and item.section_id:
        sections: list[EnterpriseQuotaSection] = []
        current = db.query(EnterpriseQuotaSection).filter(EnterpriseQuotaSection.id == item.section_id).first()
        visited: set[int] = set()
        while current is not None and int(current.id) not in visited:
            visited.add(int(current.id))
            sections.append(current)
            current = (
                db.query(EnterpriseQuotaSection)
                .filter(EnterpriseQuotaSection.id == current.parent_section_id)
                .first()
                if current.parent_section_id
                else None
            )
        sections.reverse()
        if sections:
            return [
                {
                    "id": section.id,
                    "code": section.section_code,
                    "name": section.section_name or f"第 {index} 级",
                    "level": section.level or index,
                }
                for index, section in enumerate(sections, start=1)
            ]
    return [
        {
            "id": None,
            "code": None,
            "name": _text(line.source_sheet) or "报价清单",
            "level": 1,
        }
    ]


def _serialize_resource(resource: BudgetProjectQuotaResource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "resource_uuid": resource.resource_uuid,
        "snapshot_id": resource.snapshot_id,
        "source_enterprise_component_id": resource.source_enterprise_component_id,
        "source_enterprise_resource_id": resource.source_enterprise_resource_id,
        "origin": resource.origin,
        "component_type": resource.component_type,
        "resource_code": resource.resource_code,
        "resource_name": resource.resource_name,
        "worker_or_subtype": resource.worker_or_subtype,
        "work_content": resource.work_content,
        "specification": resource.specification,
        "brand": resource.brand,
        "unit": resource.unit,
        "quantity": _decimal_text(resource.quantity),
        "unit_price": _decimal_text(resource.unit_price),
        "amount": _decimal_text(resource.amount),
        "fee_bucket": resource.fee_bucket,
        "fee_bucket_label": _BUCKET_LABELS.get(resource.fee_bucket, resource.fee_bucket),
        "library_kind": resource.library_kind,
        "category": resource.category,
        "calculation_rule": resource.calculation_rule,
        "tax_rate": _decimal_text(resource.tax_rate) if resource.tax_rate is not None else None,
        "sort_order": resource.sort_order,
        "revision": resource.revision,
        "created_by": resource.created_by,
        "updated_by": resource.updated_by,
        "created_at": _format_dt(resource.created_at),
        "updated_at": _format_dt(resource.updated_at),
    }


def serialize_project_quota(
    snapshot: BudgetProjectQuotaSnapshot,
    *,
    can_edit: bool,
    can_sync_enterprise: bool,
) -> dict[str, Any]:
    levels = _json_load(snapshot.section_path_json, [])
    resources = [_serialize_resource(resource) for resource in snapshot.resources]
    return {
        "id": snapshot.id,
        "snapshot_uuid": snapshot.snapshot_uuid,
        "account_id": snapshot.account_id,
        "project_id": snapshot.project_id,
        "draft_id": snapshot.draft_id,
        "draft_line_id": snapshot.draft_line_id,
        "source_enterprise_version_id": snapshot.source_enterprise_version_id,
        "source_enterprise_quota_item_id": snapshot.source_enterprise_quota_item_id,
        "classification_levels": levels,
        "quota": {
            "quota_code": snapshot.quota_code,
            "item_name": snapshot.item_name,
            "work_content": snapshot.work_content,
            "specification": snapshot.specification,
            "brand": snapshot.brand,
            "unit": snapshot.unit,
            "labor_fee": _decimal_text(snapshot.labor_fee),
            "main_material_fee": _decimal_text(snapshot.main_material_fee),
            "auxiliary_material_fee": _decimal_text(snapshot.auxiliary_material_fee),
            "machinery_fee": _decimal_text(snapshot.machinery_fee),
            "unit_price": _decimal_text(snapshot.unit_price),
        },
        "resources": resources,
        "resource_count": len(resources),
        "revision": snapshot.revision,
        "enterprise_sync": {
            "eligible": snapshot.source_enterprise_quota_item_id is not None,
            "can_sync": bool(can_sync_enterprise and snapshot.source_enterprise_quota_item_id),
            "target_version_id": snapshot.enterprise_sync_version_id,
            "synced_by": snapshot.enterprise_synced_by,
            "synced_at": _format_dt(snapshot.enterprise_synced_at),
            "mode": "enterprise_draft_version",
        },
        "capabilities": {
            "can_edit_resources": can_edit,
            "can_add_resource": can_edit,
            "can_delete_resource": can_edit,
            "can_sync_enterprise": bool(can_sync_enterprise and snapshot.source_enterprise_quota_item_id),
        },
        "created_by": snapshot.created_by,
        "updated_by": snapshot.updated_by,
        "created_at": _format_dt(snapshot.created_at),
        "updated_at": _format_dt(snapshot.updated_at),
    }


def _append_event(
    db: Session,
    snapshot: BudgetProjectQuotaSnapshot,
    current_user: User,
    event_type: str,
    *,
    resource_uuid: str | None = None,
    before: Any = None,
    after: Any = None,
    details: Any = None,
) -> None:
    db.add(
        BudgetProjectQuotaEvent(
            event_uuid=str(uuid4()),
            snapshot_id=snapshot.id,
            account_id=snapshot.account_id,
            project_id=snapshot.project_id,
            event_type=event_type,
            resource_uuid=resource_uuid,
            actor_id=current_user.id,
            before_json=_json_dump(before) if before is not None else None,
            after_json=_json_dump(after) if after is not None else None,
            details_json=_json_dump(details) if details is not None else None,
        )
    )


def _selected_enterprise_item(
    db: Session,
    draft: BudgetProjectPricingDraft,
    line: BudgetProjectPricingDraftLine,
) -> EnterpriseQuotaItem | None:
    if line.selected_enterprise_quota_item_id is None:
        return None
    return (
        db.query(EnterpriseQuotaItem)
        .filter(
            EnterpriseQuotaItem.id == line.selected_enterprise_quota_item_id,
            EnterpriseQuotaItem.version_id == draft.enterprise_quota_version_id,
        )
        .first()
    )


def _component_rows(
    db: Session,
    item: EnterpriseQuotaItem,
) -> list[tuple[EnterpriseQuotaComponent, EnterpriseCostResource | None]]:
    return (
        db.query(EnterpriseQuotaComponent, EnterpriseCostResource)
        .outerjoin(
            EnterpriseCostResource,
            EnterpriseCostResource.id == EnterpriseQuotaComponent.resource_id,
        )
        .filter(
            EnterpriseQuotaComponent.version_id == item.version_id,
            EnterpriseQuotaComponent.quota_item_id == item.id,
        )
        .order_by(EnterpriseQuotaComponent.sort_order, EnterpriseQuotaComponent.id)
        .all()
    )


def _materialize_enterprise_resources(
    db: Session,
    snapshot: BudgetProjectQuotaSnapshot,
    item: EnterpriseQuotaItem,
    current_user: User,
) -> None:
    for index, (component, resource) in enumerate(_component_rows(db, item), start=1):
        bucket = component.fee_bucket if component.fee_bucket in PROJECT_QUOTA_FEE_BUCKETS else "auxiliary_material"
        quantity = _q6(component.quantity)
        unit_price = _q6(component.unit_price if component.unit_price is not None else getattr(resource, "price", None))
        amount = _q6(component.amount) if component.amount is not None else _q6(quantity * unit_price)
        db.add(
            BudgetProjectQuotaResource(
                resource_uuid=str(uuid4()),
                snapshot_id=snapshot.id,
                source_enterprise_component_id=component.id,
                source_enterprise_resource_id=component.resource_id,
                origin="enterprise_snapshot",
                component_type=_text(component.component_type, 64) or _component_type_for_bucket(bucket),
                resource_code=_text(component.resource_code, 64) or _text(getattr(resource, "resource_code", None), 64),
                resource_name=_text(component.resource_name, 255)
                or _text(getattr(resource, "resource_name", None), 255)
                or f"{_BUCKET_LABELS[bucket]}明细",
                worker_or_subtype=_text(component.worker_or_subtype, 128),
                work_content=_text(component.work_content) or _text(getattr(resource, "work_content", None)),
                specification=_text(component.specification, 255) or _text(getattr(resource, "specification", None), 255),
                brand=_text(component.brand, 255) or _text(getattr(resource, "brand", None), 255),
                unit=_text(component.unit, 64) or _text(getattr(resource, "unit", None), 64),
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
                fee_bucket=bucket,
                library_kind=_text(getattr(resource, "library_kind", None), 24) or _library_kind_for_bucket(bucket),
                category=_text(getattr(resource, "category", None), 128),
                calculation_rule=_text(getattr(resource, "calculation_rule", None)),
                tax_rate=_q6(getattr(resource, "tax_rate", None)) if getattr(resource, "tax_rate", None) is not None else None,
                sort_order=index,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
        )


def _synthetic_breakdown(line: BudgetProjectPricingDraftLine) -> dict[str, Any]:
    stored = _json_load(line.pricing_breakdown_json, {})
    selected = _json_load(line.selected_source_snapshot_json, {})
    result: dict[str, Any] = {}
    for bucket, key in _BREAKDOWN_KEYS.items():
        value = stored.get(key) if isinstance(stored, dict) else None
        if value is None and isinstance(selected, dict):
            value = selected.get(
                {
                    "labor": "labor_fee",
                    "main_material": "main_material_fee",
                    "auxiliary_material": "auxiliary_material_fee",
                    "machinery": "machinery_fee",
                }[bucket]
            )
        result[bucket] = _q6(value)
    if not any(value > 0 for value in result.values()):
        result["auxiliary_material"] = _q6(line.effective_unit_price)
    return result


def _materialize_synthetic_resources(
    db: Session,
    snapshot: BudgetProjectQuotaSnapshot,
    line: BudgetProjectPricingDraftLine,
    current_user: User,
) -> None:
    sort_order = 0
    for bucket, amount in _synthetic_breakdown(line).items():
        if amount <= 0:
            continue
        sort_order += 1
        db.add(
            BudgetProjectQuotaResource(
                resource_uuid=str(uuid4()),
                snapshot_id=snapshot.id,
                origin="pricing_breakdown",
                component_type=_component_type_for_bucket(bucket),
                resource_name=f"{snapshot.item_name}（{_BUCKET_LABELS[bucket]}）",
                work_content=snapshot.work_content,
                unit=snapshot.unit,
                quantity=Decimal("1.000000"),
                unit_price=amount,
                amount=amount,
                fee_bucket=bucket,
                library_kind=_library_kind_for_bucket(bucket),
                category="项目报价拆分",
                calculation_rule="项目定额含量 × 工料机单价",
                sort_order=sort_order,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
        )


def _recalculate_snapshot(db: Session, snapshot: BudgetProjectQuotaSnapshot) -> None:
    sums: defaultdict[str, Decimal] = defaultdict(Decimal)
    for resource in snapshot.resources:
        sums[resource.fee_bucket] += _q6(resource.amount)
    snapshot.labor_fee = _q6(sums["labor"])
    snapshot.main_material_fee = _q6(sums["main_material"])
    snapshot.auxiliary_material_fee = _q6(sums["auxiliary_material"])
    snapshot.machinery_fee = _q6(sums["machinery"])
    snapshot.unit_price = _q6(sum(sums.values(), Decimal("0")))
    db.flush()


def materialize_project_quota(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    pricing_mode: str | None,
    line_identifier: str | int,
) -> BudgetProjectQuotaSnapshot:
    draft = get_current_budget_pricing_draft(
        db,
        profile,
        current_user,
        pricing_mode=pricing_mode,
        for_update=True,
    )
    if draft is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
    line = get_budget_pricing_draft_line(db, draft, line_identifier, for_update=True)
    existing = (
        db.query(BudgetProjectQuotaSnapshot)
        .filter(BudgetProjectQuotaSnapshot.draft_line_id == line.id)
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        return existing

    item = _selected_enterprise_item(db, draft, line)
    selected = _json_load(line.selected_source_snapshot_json, {})
    snapshot = BudgetProjectQuotaSnapshot(
        snapshot_uuid=str(uuid4()),
        account_id=draft.account_id,
        project_id=draft.project_id,
        draft_id=draft.id,
        draft_line_id=line.id,
        source_enterprise_version_id=item.version_id if item else None,
        source_enterprise_quota_item_id=item.id if item else None,
        section_path_json=_json_dump(_section_path(db, item, line)),
        quota_code=_text(item.quota_code, 64) if item else _text(selected.get("quota_code"), 64),
        item_name=_text(item.item_name, 255) if item else (_text(line.item_name, 255) or "未命名项目定额"),
        work_content=_text(item.work_content) if item else _text(line.spec),
        specification=_text(item.specification, 255) if item else _text(line.spec, 255),
        brand=_text(item.brand, 255) if item else None,
        unit=_text(item.unit, 64) if item else _text(line.unit, 64),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(snapshot)
    db.flush()
    if item is not None:
        _materialize_enterprise_resources(db, snapshot, item, current_user)
    else:
        _materialize_synthetic_resources(db, snapshot, line, current_user)
    db.flush()
    db.refresh(snapshot)
    _recalculate_snapshot(db, snapshot)
    _append_event(
        db,
        snapshot,
        current_user,
        "snapshot_materialized",
        after={
            "source_enterprise_quota_item_id": snapshot.source_enterprise_quota_item_id,
            "resource_count": len(snapshot.resources),
            "unit_price": _decimal_text(snapshot.unit_price),
        },
    )
    db.flush()
    return snapshot


def _locked_snapshot(
    db: Session,
    *,
    project_id: int,
    line_identifier: str | int,
) -> BudgetProjectQuotaSnapshot:
    text = str(line_identifier).strip()
    query = (
        db.query(BudgetProjectQuotaSnapshot)
        .join(
            BudgetProjectPricingDraftLine,
            BudgetProjectPricingDraftLine.id == BudgetProjectQuotaSnapshot.draft_line_id,
        )
        .filter(BudgetProjectQuotaSnapshot.project_id == project_id)
    )
    if text.isdigit():
        query = query.filter(BudgetProjectPricingDraftLine.id == int(text))
    else:
        query = query.filter(BudgetProjectPricingDraftLine.line_uuid == text)
    snapshot = query.with_for_update().one_or_none()
    if snapshot is None:
        raise BudgetPricingError("PROJECT_QUOTA_SNAPSHOT_NOT_FOUND", status_code=404)
    return snapshot


def _check_snapshot_revision(snapshot: BudgetProjectQuotaSnapshot, expected_revision: int) -> None:
    if int(snapshot.revision) != int(expected_revision):
        raise BudgetPricingError(
            "PROJECT_QUOTA_SNAPSHOT_REVISION_CONFLICT",
            status_code=409,
            context={
                "expected_revision": expected_revision,
                "current_revision": snapshot.revision,
            },
        )


def _resource_payload(resource: BudgetProjectQuotaResource, payload: dict[str, Any], *, is_create: bool) -> None:
    text_fields = {
        "component_type": 64,
        "resource_code": 64,
        "resource_name": 255,
        "worker_or_subtype": 128,
        "work_content": None,
        "specification": 255,
        "brand": 255,
        "unit": 64,
        "library_kind": 24,
        "category": 128,
        "calculation_rule": None,
    }
    for field, limit in text_fields.items():
        if field in payload:
            setattr(resource, field, _text(payload.get(field), limit))
    if "fee_bucket" in payload:
        bucket = str(payload.get("fee_bucket") or "").strip()
        if bucket not in PROJECT_QUOTA_FEE_BUCKETS:
            raise BudgetPricingError("PROJECT_QUOTA_FEE_BUCKET_INVALID", status_code=422)
        resource.fee_bucket = bucket
        if "component_type" not in payload:
            resource.component_type = _component_type_for_bucket(bucket)
        if "library_kind" not in payload:
            resource.library_kind = _library_kind_for_bucket(bucket)
    for field in ("quantity", "unit_price", "amount", "tax_rate"):
        if field not in payload:
            continue
        value = _decimal(payload.get(field))
        if value is None and field == "tax_rate":
            setattr(resource, field, None)
            continue
        if value is None or value < 0:
            raise BudgetPricingError("PROJECT_QUOTA_RESOURCE_NUMBER_INVALID", status_code=422)
        setattr(resource, field, _q6(value))
    if ("quantity" in payload or "unit_price" in payload or is_create) and "amount" not in payload:
        resource.amount = _q6(_q6(resource.quantity) * _q6(resource.unit_price))
    if not _text(resource.resource_name):
        raise BudgetPricingError("PROJECT_QUOTA_RESOURCE_NAME_REQUIRED", status_code=422)


def _sync_draft_line_from_snapshot(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    snapshot: BudgetProjectQuotaSnapshot,
    *,
    reason: str | None,
) -> None:
    if _q6(snapshot.unit_price) <= 0:
        raise BudgetPricingError(
            "PROJECT_QUOTA_TOTAL_MUST_BE_POSITIVE",
            status_code=409,
            context={"message": "项目定额工料机合计必须大于 0"},
        )
    line = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.id == snapshot.draft_line_id)
        .with_for_update()
        .one()
    )
    draft = (
        db.query(BudgetProjectPricingDraft)
        .filter(BudgetProjectPricingDraft.id == snapshot.draft_id)
        .with_for_update()
        .one()
    )
    stored = _json_load(line.pricing_breakdown_json, {})
    breakdown = dict(stored) if isinstance(stored, dict) else {}
    breakdown.update(
        {
            "labor_unit_cost": _decimal_text(snapshot.labor_fee),
            "main_material_unit_cost": _decimal_text(snapshot.main_material_fee),
            "auxiliary_material_unit_cost": _decimal_text(snapshot.auxiliary_material_fee),
            "machinery_unit_cost": _decimal_text(snapshot.machinery_fee),
            "comprehensive_unit_cost": "0.000000",
            "management_unit_cost": "0.000000",
            "profit_unit_cost": "0.000000",
            "measure_unit_cost": "0.000000",
            "tax_amount": "0.000000",
            "source": "project_quota_snapshot",
            "project_quota_snapshot_uuid": snapshot.snapshot_uuid,
        }
    )
    patch_budget_pricing_draft_line(
        db,
        profile,
        current_user,
        pricing_mode=draft.pricing_mode,
        line_identifier=line.id,
        expected_revision=int(draft.revision),
        expected_line_revision=int(line.line_revision),
        manual_unit_price=_q6(snapshot.unit_price),
        pricing_breakdown=breakdown,
        reason=reason or "项目定额工料机明细变更联动",
    )


def create_project_quota_resource(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    line_identifier: str | int,
    expected_snapshot_revision: int,
    payload: dict[str, Any],
    reason: str | None,
) -> BudgetProjectQuotaSnapshot:
    snapshot = _locked_snapshot(db, project_id=profile.project_id, line_identifier=line_identifier)
    _check_snapshot_revision(snapshot, expected_snapshot_revision)
    max_order = (
        db.query(func.max(BudgetProjectQuotaResource.sort_order))
        .filter(BudgetProjectQuotaResource.snapshot_id == snapshot.id)
        .scalar()
        or 0
    )
    resource = BudgetProjectQuotaResource(
        resource_uuid=str(uuid4()),
        snapshot_id=snapshot.id,
        origin="manual_added",
        fee_bucket="auxiliary_material",
        quantity=Decimal("0"),
        unit_price=Decimal("0"),
        amount=Decimal("0"),
        sort_order=int(max_order) + 1,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    _resource_payload(resource, payload, is_create=True)
    db.add(resource)
    db.flush()
    db.expire(snapshot, ["resources"])
    snapshot.revision = int(snapshot.revision) + 1
    snapshot.updated_by = current_user.id
    _recalculate_snapshot(db, snapshot)
    _sync_draft_line_from_snapshot(db, profile, current_user, snapshot, reason=reason)
    after = _serialize_resource(resource)
    _append_event(
        db,
        snapshot,
        current_user,
        "resource_created",
        resource_uuid=resource.resource_uuid,
        after=after,
        details={"reason": _text(reason, 2000)},
    )
    db.flush()
    return snapshot


def update_project_quota_resource(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    line_identifier: str | int,
    resource_identifier: str | int,
    expected_snapshot_revision: int,
    expected_resource_revision: int,
    payload: dict[str, Any],
    reason: str | None,
) -> BudgetProjectQuotaSnapshot:
    snapshot = _locked_snapshot(db, project_id=profile.project_id, line_identifier=line_identifier)
    _check_snapshot_revision(snapshot, expected_snapshot_revision)
    text = str(resource_identifier).strip()
    query = db.query(BudgetProjectQuotaResource).filter(
        BudgetProjectQuotaResource.snapshot_id == snapshot.id
    )
    resource = (
        query.filter(BudgetProjectQuotaResource.id == int(text)).with_for_update().one_or_none()
        if text.isdigit()
        else query.filter(BudgetProjectQuotaResource.resource_uuid == text).with_for_update().one_or_none()
    )
    if resource is None:
        raise BudgetPricingError("PROJECT_QUOTA_RESOURCE_NOT_FOUND", status_code=404)
    if int(resource.revision) != int(expected_resource_revision):
        raise BudgetPricingError(
            "PROJECT_QUOTA_RESOURCE_REVISION_CONFLICT",
            status_code=409,
            context={"current_revision": resource.revision},
        )
    before = _serialize_resource(resource)
    _resource_payload(resource, payload, is_create=False)
    resource.revision = int(resource.revision) + 1
    resource.updated_by = current_user.id
    snapshot.revision = int(snapshot.revision) + 1
    snapshot.updated_by = current_user.id
    _recalculate_snapshot(db, snapshot)
    _sync_draft_line_from_snapshot(db, profile, current_user, snapshot, reason=reason)
    after = _serialize_resource(resource)
    _append_event(
        db,
        snapshot,
        current_user,
        "resource_updated",
        resource_uuid=resource.resource_uuid,
        before=before,
        after=after,
        details={"reason": _text(reason, 2000)},
    )
    db.flush()
    return snapshot


def delete_project_quota_resource(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    line_identifier: str | int,
    resource_identifier: str | int,
    expected_snapshot_revision: int,
    expected_resource_revision: int,
    reason: str | None,
) -> BudgetProjectQuotaSnapshot:
    snapshot = _locked_snapshot(db, project_id=profile.project_id, line_identifier=line_identifier)
    _check_snapshot_revision(snapshot, expected_snapshot_revision)
    resources = (
        db.query(BudgetProjectQuotaResource)
        .filter(BudgetProjectQuotaResource.snapshot_id == snapshot.id)
        .order_by(BudgetProjectQuotaResource.sort_order, BudgetProjectQuotaResource.id)
        .with_for_update()
        .all()
    )
    text = str(resource_identifier).strip()
    resource = next(
        (
            row
            for row in resources
            if (text.isdigit() and int(row.id) == int(text))
            or (not text.isdigit() and row.resource_uuid == text)
        ),
        None,
    )
    if resource is None:
        raise BudgetPricingError("PROJECT_QUOTA_RESOURCE_NOT_FOUND", status_code=404)
    if int(resource.revision) != int(expected_resource_revision):
        raise BudgetPricingError(
            "PROJECT_QUOTA_RESOURCE_REVISION_CONFLICT",
            status_code=409,
            context={"current_revision": resource.revision},
        )
    if len(resources) <= 1:
        raise BudgetPricingError(
            "PROJECT_QUOTA_LAST_RESOURCE_DELETE_BLOCKED",
            status_code=409,
            context={"message": "至少保留一条工料机明细；可将其改为新的明细"},
        )
    before = _serialize_resource(resource)
    resource_uuid = resource.resource_uuid
    db.delete(resource)
    db.flush()
    snapshot.revision = int(snapshot.revision) + 1
    snapshot.updated_by = current_user.id
    db.expire(snapshot, ["resources"])
    _recalculate_snapshot(db, snapshot)
    _sync_draft_line_from_snapshot(db, profile, current_user, snapshot, reason=reason)
    _append_event(
        db,
        snapshot,
        current_user,
        "resource_deleted",
        resource_uuid=resource_uuid,
        before=before,
        details={"reason": _text(reason, 2000)},
    )
    db.flush()
    return snapshot


def _reuse_enterprise_sync_draft(
    db: Session,
    snapshot: BudgetProjectQuotaSnapshot,
) -> EnterpriseQuotaVersion | None:
    target_ids = [
        row[0]
        for row in (
            db.query(BudgetProjectQuotaSnapshot.enterprise_sync_version_id)
            .filter(
                BudgetProjectQuotaSnapshot.project_id == snapshot.project_id,
                BudgetProjectQuotaSnapshot.source_enterprise_version_id == snapshot.source_enterprise_version_id,
                BudgetProjectQuotaSnapshot.enterprise_sync_version_id.is_not(None),
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    ]
    if not target_ids:
        return None
    return (
        db.query(EnterpriseQuotaVersion)
        .filter(
            EnterpriseQuotaVersion.id.in_(target_ids),
            EnterpriseQuotaVersion.status == QUOTA_VERSION_STATUS_DRAFT,
            EnterpriseQuotaVersion.is_active.is_(False),
        )
        .order_by(EnterpriseQuotaVersion.id.desc())
        .with_for_update()
        .first()
    )


def _target_quota_item(
    db: Session,
    target_version: EnterpriseQuotaVersion,
    source_item: EnterpriseQuotaItem,
) -> EnterpriseQuotaItem:
    query = db.query(EnterpriseQuotaItem).filter(
        EnterpriseQuotaItem.version_id == target_version.id
    )
    target = (
        query.filter(EnterpriseQuotaItem.quota_code == source_item.quota_code).one_or_none()
        if source_item.quota_code
        else None
    )
    if target is None:
        target = (
            query.filter(
                EnterpriseQuotaItem.source_sheet == source_item.source_sheet,
                EnterpriseQuotaItem.source_row_index == source_item.source_row_index,
                EnterpriseQuotaItem.item_name == source_item.item_name,
            )
            .first()
        )
    if target is None:
        raise BudgetPricingError("ENTERPRISE_SYNC_TARGET_QUOTA_NOT_FOUND", status_code=409)
    return target


def _next_sheet_row(db: Session, version_id: int, sheet_name: str) -> int:
    current = (
        db.query(func.max(EnterpriseQuotaSheetRow.row_number))
        .filter(
            EnterpriseQuotaSheetRow.version_id == version_id,
            EnterpriseQuotaSheetRow.sheet_name == sheet_name,
        )
        .scalar()
        or 2
    )
    return int(current) + 1


def _enterprise_resource_values(resource: EnterpriseCostResource) -> dict[str, Any]:
    if resource.library_kind == "labor":
        return {
            "A": resource.resource_code,
            "B": _component_type_for_bucket("labor"),
            "C": resource.resource_name,
            "D": resource.work_content,
            "E": resource.calculation_rule,
            "F": resource.unit,
            "G": resource.default_quantity,
            "H": resource.price,
        }
    return {
        "A": resource.category,
        "B": resource.resource_code,
        "C": _component_type_for_bucket(resource.resource_type),
        "D": resource.resource_name,
        "E": resource.specification,
        "F": resource.brand,
        "G": resource.unit,
        "H": resource.price,
    }


def _enterprise_component_values(component: EnterpriseQuotaComponent) -> dict[str, Any]:
    values = {
        "A": component.resource_code,
        "B": component.component_type,
        "C": component.resource_name,
        "D": component.work_content,
        "E": component.specification,
        "F": component.brand,
        "G": component.unit,
        "H": component.quantity,
        "I": component.unit_price,
        "J": None,
        "K": None,
        "L": None,
        "M": None,
    }
    amount_column = {
        "labor": "J",
        "main_material": "K",
        "auxiliary_material": "L",
        "machinery": "M",
    }.get(component.fee_bucket)
    if amount_column:
        values[amount_column] = component.amount
    return values


def _replace_target_components(
    db: Session,
    *,
    target_version: EnterpriseQuotaVersion,
    target_item: EnterpriseQuotaItem,
    snapshot: BudgetProjectQuotaSnapshot,
) -> None:
    old_components = (
        db.query(EnterpriseQuotaComponent)
        .filter(
            EnterpriseQuotaComponent.version_id == target_version.id,
            EnterpriseQuotaComponent.quota_item_id == target_item.id,
        )
        .with_for_update()
        .all()
    )
    old_ids = [component.id for component in old_components]
    old_project_resource_ids = [
        int(component.resource_id)
        for component in old_components
        if component.resource_id is not None
        and snapshot.snapshot_uuid in str(component.raw_row_json or "")
    ]
    if old_ids:
        db.query(EnterpriseQuotaSheetRow).filter(
            EnterpriseQuotaSheetRow.version_id == target_version.id,
            EnterpriseQuotaSheetRow.entity_type == "component",
            EnterpriseQuotaSheetRow.entity_id.in_(old_ids),
        ).delete(synchronize_session=False)
        for component in old_components:
            db.delete(component)
        db.flush()
    if old_project_resource_ids:
        db.query(EnterpriseQuotaSheetRow).filter(
            EnterpriseQuotaSheetRow.version_id == target_version.id,
            EnterpriseQuotaSheetRow.entity_type == "resource",
            EnterpriseQuotaSheetRow.entity_id.in_(old_project_resource_ids),
        ).delete(synchronize_session=False)
        db.query(EnterpriseCostResource).filter(
            EnterpriseCostResource.version_id == target_version.id,
            EnterpriseCostResource.id.in_(old_project_resource_ids),
        ).delete(synchronize_session=False)
        db.flush()

    max_resource_order = (
        db.query(func.max(EnterpriseCostResource.sort_order))
        .filter(EnterpriseCostResource.version_id == target_version.id)
        .scalar()
        or 0
    )
    max_component_order = (
        db.query(func.max(EnterpriseQuotaComponent.sort_order))
        .filter(EnterpriseQuotaComponent.version_id == target_version.id)
        .scalar()
        or 0
    )
    resource_order = int(max_resource_order)
    component_order = int(max_component_order)
    next_enterprise_row = _next_sheet_row(db, target_version.id, ENTERPRISE_SHEET)
    next_labor_row = _next_sheet_row(db, target_version.id, LABOR_SHEET)
    next_material_row = _next_sheet_row(db, target_version.id, MATERIAL_SHEET)

    for project_resource in snapshot.resources:
        bucket = project_resource.fee_bucket
        library_kind = project_resource.library_kind or _library_kind_for_bucket(bucket)
        sheet_name = LABOR_SHEET if library_kind == "labor" else MATERIAL_SHEET
        source_row_index = next_labor_row if library_kind == "labor" else next_material_row
        if library_kind == "labor":
            next_labor_row += 1
        else:
            next_material_row += 1
        resource_order += 1
        enterprise_resource = EnterpriseCostResource(
            version_id=target_version.id,
            resource_code=project_resource.resource_code,
            resource_name=project_resource.resource_name,
            resource_type=_resource_type_for_bucket(bucket),
            library_kind=library_kind,
            category=project_resource.category,
            specification=project_resource.specification,
            brand=project_resource.brand,
            work_content=project_resource.work_content,
            calculation_rule=project_resource.calculation_rule,
            unit=project_resource.unit,
            default_quantity=float(project_resource.quantity or 0),
            price=float(project_resource.unit_price or 0),
            computed_price=float(project_resource.unit_price or 0),
            tax_rate=float(project_resource.tax_rate) if project_resource.tax_rate is not None else None,
            source_sheet=sheet_name,
            source_row_index=source_row_index,
            sort_order=resource_order,
            formulas_json="{}",
            raw_row_json=_json_dump(
                {
                    "source": "project_quota_sync",
                    "project_quota_snapshot_uuid": snapshot.snapshot_uuid,
                    "project_resource_uuid": project_resource.resource_uuid,
                }
            ),
        )
        db.add(enterprise_resource)
        db.flush()
        db.add(
            EnterpriseQuotaSheetRow(
                version_id=target_version.id,
                sheet_name=sheet_name,
                sheet_order=1 if library_kind == "labor" else 2,
                row_number=source_row_index,
                row_kind="data",
                outline_level=0,
                entity_type="resource",
                entity_id=enterprise_resource.id,
                values_json=compact_json(_enterprise_resource_values(enterprise_resource)),
                formulas_json="{}",
                styles_json="{}",
                merge_ranges_json="[]",
                hidden=False,
                collapsed=False,
            )
        )

        component_order += 1
        component = EnterpriseQuotaComponent(
            version_id=target_version.id,
            quota_item_id=target_item.id,
            resource_id=enterprise_resource.id,
            parent_quota_code=target_item.quota_code,
            component_type=project_resource.component_type or _component_type_for_bucket(bucket),
            resource_code=project_resource.resource_code,
            resource_name=project_resource.resource_name,
            worker_or_subtype=project_resource.worker_or_subtype,
            work_content=project_resource.work_content,
            specification=project_resource.specification,
            brand=project_resource.brand,
            unit=project_resource.unit,
            quantity=float(project_resource.quantity or 0),
            unit_price=float(project_resource.unit_price or 0),
            amount=float(project_resource.amount or 0),
            fee_bucket=bucket,
            source_sheet=ENTERPRISE_SHEET,
            source_row_index=next_enterprise_row,
            outline_level=1,
            formulas_json="{}",
            formula_library_kind=None,
            formula_link_status="linked",
            sort_order=component_order,
            raw_row_json=_json_dump(
                {
                    "source": "project_quota_sync",
                    "project_quota_snapshot_uuid": snapshot.snapshot_uuid,
                    "project_resource_uuid": project_resource.resource_uuid,
                }
            ),
        )
        db.add(component)
        db.flush()
        db.add(
            EnterpriseQuotaSheetRow(
                version_id=target_version.id,
                sheet_name=ENTERPRISE_SHEET,
                sheet_order=0,
                row_number=next_enterprise_row,
                row_kind="data",
                outline_level=1,
                parent_row_number=target_item.source_row_index,
                entity_type="component",
                entity_id=component.id,
                values_json=compact_json(_enterprise_component_values(component)),
                formulas_json="{}",
                styles_json="{}",
                merge_ranges_json="[]",
                hidden=False,
                collapsed=False,
            )
        )
        next_enterprise_row += 1
    db.flush()


def sync_project_quota_to_enterprise(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    line_identifier: str | int,
    expected_snapshot_revision: int,
    reason: str,
) -> dict[str, Any]:
    snapshot = _locked_snapshot(db, project_id=profile.project_id, line_identifier=line_identifier)
    _check_snapshot_revision(snapshot, expected_snapshot_revision)
    if snapshot.source_enterprise_version_id is None or snapshot.source_enterprise_quota_item_id is None:
        raise BudgetPricingError(
            "PROJECT_QUOTA_ENTERPRISE_SYNC_NOT_ELIGIBLE",
            status_code=409,
            context={"message": "当前项目定额不是从企业定额匹配形成，不能直接回写企业主库"},
        )
    active_version_ids = [
        int(row[0])
        for row in (
            db.query(EnterpriseQuotaVersion.id)
            .filter(
                EnterpriseQuotaVersion.status == "active",
                EnterpriseQuotaVersion.is_active.is_(True),
            )
            .with_for_update()
            .all()
        )
    ]
    if active_version_ids != [int(snapshot.source_enterprise_version_id)]:
        raise BudgetPricingError(
            "PROJECT_QUOTA_ENTERPRISE_SOURCE_VERSION_CHANGED",
            status_code=409,
            context={
                "message": "企业定额 active 版本已变化，请重新生成项目计价草稿后再同步",
                "source_version_id": snapshot.source_enterprise_version_id,
                "active_version_ids": active_version_ids,
            },
        )
    source_item = (
        db.query(EnterpriseQuotaItem)
        .filter(
            EnterpriseQuotaItem.id == snapshot.source_enterprise_quota_item_id,
            EnterpriseQuotaItem.version_id == snapshot.source_enterprise_version_id,
        )
        .one_or_none()
    )
    if source_item is None:
        raise BudgetPricingError("PROJECT_QUOTA_ENTERPRISE_SOURCE_NOT_FOUND", status_code=409)
    target_version = _reuse_enterprise_sync_draft(db, snapshot)
    if target_version is None:
        target_version = clone_version_to_draft(
            db,
            snapshot.source_enterprise_version_id,
            actor_id=current_user.id,
            version_code=None,
            version_name=f"项目 {profile.project_id} 工料机同步草稿",
            reason=reason,
        )
    target_item = _target_quota_item(db, target_version, source_item)
    before = {
        "version_id": target_version.id,
        "quota_item_id": target_item.id,
        "unit_price": target_item.unit_price,
        "component_count": (
            db.query(func.count(EnterpriseQuotaComponent.id))
            .filter(EnterpriseQuotaComponent.quota_item_id == target_item.id)
            .scalar()
            or 0
        ),
    }
    _replace_target_components(
        db,
        target_version=target_version,
        target_item=target_item,
        snapshot=snapshot,
    )
    target_version.revision = int(target_version.revision or 1) + 1
    recalculation = recalculate_version(
        db,
        target_version.id,
        actor_id=current_user.id,
        reason=reason,
        record_event=False,
    )
    db.refresh(target_item)
    after = {
        "version_id": target_version.id,
        "quota_item_id": target_item.id,
        "unit_price": target_item.unit_price,
        "component_count": len(snapshot.resources),
        "recalculation": recalculation,
    }
    db.add(
        EnterpriseQuotaVersionEvent(
            version_id=target_version.id,
            event_type="project_quota_synced",
            actor_id=current_user.id,
            reason=_text(reason, 500),
            details_json=_json_dump(
                {
                    "project_id": snapshot.project_id,
                    "project_quota_snapshot_uuid": snapshot.snapshot_uuid,
                    "source_enterprise_version_id": snapshot.source_enterprise_version_id,
                    "source_enterprise_quota_item_id": snapshot.source_enterprise_quota_item_id,
                    "before": before,
                    "after": after,
                }
            ),
        )
    )
    snapshot.enterprise_sync_version_id = target_version.id
    snapshot.enterprise_synced_by = current_user.id
    snapshot.enterprise_synced_at = datetime.now(timezone.utc)
    snapshot.revision = int(snapshot.revision) + 1
    snapshot.updated_by = current_user.id
    _append_event(
        db,
        snapshot,
        current_user,
        "enterprise_draft_synced",
        before=before,
        after=after,
        details={
            "reason": reason,
            "enterprise_version_status": target_version.status,
            "requires_activation": True,
        },
    )
    db.flush()
    return {
        "snapshot": snapshot,
        "enterprise_version": {
            "id": target_version.id,
            "version_code": target_version.version_code,
            "version_name": target_version.version_name,
            "status": target_version.status,
            "is_active": bool(target_version.is_active),
            "revision": target_version.revision,
        },
        "enterprise_quota": after,
        "requires_activation": True,
        "message": "已同步到企业定额草稿版本，审核启用前不会影响当前企业 active 定额",
    }
