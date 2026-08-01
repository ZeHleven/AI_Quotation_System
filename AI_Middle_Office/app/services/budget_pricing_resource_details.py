"""Resource-level detail rows behind pricing-draft labor/material totals."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.budget_pricing_draft import (
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftLine,
)
from app.models.enterprise_quota import (
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
)
from app.services.budget_pricing import BudgetPricingError, _decimal, _decimal_text, _q6
from app.services.budget_pricing_drafts import (
    _line_summary_multiplier,
    _source_component_unit,
)


RESOURCE_DETAIL_BUCKETS = {"labor", "main_material", "auxiliary_material"}
_BUCKET_CONFIG = {
    "labor": {
        "library_kind": "labor",
        "resource_type": "人工",
        "breakdown_key": "labor_unit_cost",
        "headers": [
            "编码",
            "类型",
            "项目名称",
            "工作内容",
            "计算规则",
            "单位",
            "数量",
            "不含税人工单价",
            "人工总价",
        ],
    },
    "main_material": {
        "library_kind": "material",
        "resource_type": "主材",
        "breakdown_key": "main_material_unit_cost",
        "headers": [
            "分类",
            "材料编码",
            "类型",
            "材料名称",
            "规格",
            "品牌",
            "单位",
            "数量",
            "除税单价",
            "总价",
        ],
    },
    "auxiliary_material": {
        "library_kind": "material",
        "resource_type": "辅材",
        "breakdown_key": "auxiliary_material_unit_cost",
        "headers": [
            "分类",
            "材料编码",
            "类型",
            "材料名称",
            "规格",
            "品牌",
            "单位",
            "数量",
            "除税单价",
            "总价",
        ],
    },
}
_COMPONENT_RECONCILIATION_TOLERANCE = Decimal("0.010000")
_PROCUREMENT_BUCKETS = {"labor", "main_material", "auxiliary_material"}


def _text(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _effective_line_quantity(line: BudgetProjectPricingDraftLine) -> Decimal:
    quantity = _decimal(line.calculation_quantity) or Decimal("0")
    if quantity <= 0 or not line.amount_included:
        return Decimal("0")
    return _q6(quantity * _line_summary_multiplier(line)) or Decimal("0")


def _component_price(
    component: EnterpriseQuotaComponent,
    resource: EnterpriseCostResource | None,
) -> Decimal:
    return (
        _decimal(component.unit_price)
        or (_decimal(resource.price) if resource is not None else None)
        or Decimal("0")
    )


def _component_content(component: EnterpriseQuotaComponent) -> Decimal:
    return _decimal(component.quantity) or Decimal("0")


def _component_detail(
    *,
    bucket: str,
    component: EnterpriseQuotaComponent,
    resource: EnterpriseCostResource | None,
    quota_item: EnterpriseQuotaItem | None,
    line_quantity: Decimal,
) -> dict[str, Any]:
    config = _BUCKET_CONFIG[bucket]
    content = _component_content(component)
    price = _component_price(component, resource)
    quantity = _q6(line_quantity * content) or Decimal("0")
    amount = _q6(quantity * price) or Decimal("0")
    return {
        "resource_code": _text(component.resource_code) or _text(resource.resource_code if resource else None),
        "resource_type": _text(component.component_type)
        or _text(resource.resource_type if resource else None)
        or config["resource_type"],
        "resource_name": _text(component.resource_name) or _text(resource.resource_name if resource else None),
        "work_content": _text(component.work_content) or _text(resource.work_content if resource else None),
        "calculation_rule": _text(resource.calculation_rule if resource else None),
        "category": _text(resource.category if resource else None) or config["resource_type"],
        "specification": _text(component.specification)
        or _text(resource.specification if resource else None),
        "brand": _text(component.brand) or _text(resource.brand if resource else None),
        "resource_specification": _text(component.specification)
        or _text(resource.specification if resource else None),
        "resource_brand": _text(component.brand) or _text(resource.brand if resource else None),
        "unit": _text(component.unit) or _text(resource.unit if resource else None),
        "default_quantity": content,
        "price": price,
        "quantity": quantity,
        "amount": amount,
        "detail_source": "enterprise_resource",
    }


def _derived_detail(
    *,
    bucket: str,
    line: BudgetProjectPricingDraftLine,
    line_quantity: Decimal,
    unit_cost: Decimal,
) -> dict[str, Any]:
    config = _BUCKET_CONFIG[bucket]
    selected = line.selected_source_snapshot_json or ""
    selected_section = None
    if selected:
        try:
            snapshot = json.loads(selected)
            selected_section = (
                snapshot.get("section", {}).get("section_name")
                if isinstance(snapshot, dict) and isinstance(snapshot.get("section"), dict)
                else None
            )
        except Exception:
            selected_section = None
    return {
        "resource_code": None,
        "resource_type": config["resource_type"],
        "resource_name": _text(line.item_name) or "未命名报价项目",
        "work_content": _text(line.spec),
        "calculation_rule": "报价清单工程量 × 单位费用",
        "category": (
            _text(selected_section) or "报价费用拆分"
            if bucket == "labor"
            else config["resource_type"]
        ),
        "specification": None,
        "brand": None,
        "resource_specification": None,
        "resource_brand": None,
        "unit": _text(line.unit),
        "default_quantity": Decimal("1"),
        "price": unit_cost,
        "quantity": line_quantity,
        "amount": _q6(line_quantity * unit_cost) or Decimal("0"),
        "detail_source": "pricing_line_breakdown",
    }


def _detail_key(bucket: str, detail: dict[str, Any]) -> tuple[Any, ...]:
    common = (
        detail.get("resource_code"),
        detail.get("resource_type"),
        detail.get("resource_name"),
        detail.get("unit"),
        _decimal_text(_q6(_decimal(detail.get("price")))),
        detail.get("detail_source"),
    )
    if bucket == "labor":
        return common + (
            detail.get("work_content"),
            detail.get("calculation_rule"),
            _decimal_text(_q6(_decimal(detail.get("default_quantity")))),
        )
    return common + (
        detail.get("category"),
        detail.get("specification"),
        detail.get("brand"),
    )


def _serialize_aggregated_details(bucket: str, details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregated: dict[tuple[Any, ...], dict[str, Any]] = {}
    for detail in details:
        key = _detail_key(bucket, detail)
        current = aggregated.get(key)
        if current is None:
            current = dict(detail)
            current["quantity"] = Decimal("0")
            current["amount"] = Decimal("0")
            current["quote_line_count"] = 0
            aggregated[key] = current
        current["quantity"] += _decimal(detail.get("quantity")) or Decimal("0")
        current["amount"] += _decimal(detail.get("amount")) or Decimal("0")
        current["quote_line_count"] += 1

    rows: list[dict[str, Any]] = []
    for index, detail in enumerate(
        sorted(
            aggregated.values(),
            key=lambda item: (
                item.get("resource_code") or "\uffff",
                item.get("resource_name") or "",
                item.get("unit") or "",
            ),
        ),
        start=1,
    ):
        quantity = _q6(_decimal(detail.get("quantity"))) or Decimal("0")
        price = _q6(_decimal(detail.get("price"))) or Decimal("0")
        rows.append(
            {
                **detail,
                "id": f"{bucket}:{index}",
                "default_quantity": _decimal_text(
                    _q6(_decimal(detail.get("default_quantity")))
                ),
                "price": _decimal_text(price),
                "quantity": _decimal_text(quantity),
                "amount": _decimal_text(_q6(quantity * price)),
            }
        )
    return rows


def _procurement_detail_key(detail: dict[str, Any], *, kind: str) -> tuple[Any, ...]:
    if kind == "labor":
        return (
            detail.get("resource_code"),
            detail.get("resource_type"),
            detail.get("resource_name"),
            detail.get("work_content"),
            detail.get("calculation_rule"),
            detail.get("unit"),
            _decimal_text(_q6(_decimal(detail.get("price")))),
        )
    return (
        detail.get("category"),
        detail.get("resource_code"),
        detail.get("resource_type"),
        detail.get("resource_name"),
        detail.get("specification"),
        detail.get("brand"),
        detail.get("unit"),
        _decimal_text(_q6(_decimal(detail.get("price")))),
    )


def _aggregate_procurement_details(
    details: list[dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    aggregated: dict[tuple[Any, ...], dict[str, Any]] = {}
    contents_by_key: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for detail in details:
        key = _procurement_detail_key(detail, kind=kind)
        current = aggregated.get(key)
        if current is None:
            current = dict(detail)
            current["quantity"] = Decimal("0")
            current["amount"] = Decimal("0")
            current["quote_line_count"] = 0
            aggregated[key] = current
        current["quantity"] += _decimal(detail.get("quantity")) or Decimal("0")
        current["amount"] += _decimal(detail.get("amount")) or Decimal("0")
        current["quote_line_count"] += 1
        content_text = _decimal_text(_q6(_decimal(detail.get("default_quantity"))))
        if content_text:
            contents_by_key[key].add(content_text)

    rows: list[dict[str, Any]] = []
    for index, (key, detail) in enumerate(
        sorted(
            aggregated.items(),
            key=lambda item: (
                item[1].get("resource_code") or "\uffff",
                item[1].get("resource_name") or "",
                item[1].get("unit") or "",
            ),
        ),
        start=1,
    ):
        contents = contents_by_key.get(key, set())
        quantity = _q6(_decimal(detail.get("quantity"))) or Decimal("0")
        price = _q6(_decimal(detail.get("price"))) or Decimal("0")
        rows.append(
            {
                **detail,
                "id": f"procurement:{kind}:{index}",
                "default_quantity": next(iter(contents)) if len(contents) == 1 else None,
                "price": _decimal_text(price),
                "quantity": _decimal_text(quantity),
                "amount": _decimal_text(_q6(quantity * price)),
            }
        )
    return rows


def _unit_quantity_totals(rows: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    totals: dict[str | None, Decimal] = defaultdict(Decimal)
    for row in rows:
        totals[_text(row.get("unit"))] += _decimal(row.get("quantity")) or Decimal("0")
    return [
        {
            "unit": unit,
            "quantity": _decimal_text(_q6(quantity)),
        }
        for unit, quantity in sorted(totals.items(), key=lambda item: item[0] or "\uffff")
    ]


def build_budget_pricing_resource_details(
    db: Session,
    draft: BudgetProjectPricingDraft,
    *,
    bucket: str,
) -> dict[str, Any]:
    """Build reconciled resource details for one visible pricing total."""

    normalized_bucket = str(bucket or "").strip()
    if normalized_bucket not in RESOURCE_DETAIL_BUCKETS:
        raise BudgetPricingError(
            "BUDGET_PRICING_RESOURCE_BUCKET_INVALID",
            status_code=422,
            context={"bucket": normalized_bucket},
        )
    config = _BUCKET_CONFIG[normalized_bucket]
    lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(
            BudgetProjectPricingDraftLine.source_sort_order.asc(),
            BudgetProjectPricingDraftLine.id.asc(),
        )
        .all()
    )
    item_ids = sorted(
        {
            int(line.selected_enterprise_quota_item_id)
            for line in lines
            if line.selected_enterprise_quota_item_id is not None
        }
    )
    components_by_item: dict[
        int,
        list[
            tuple[
                EnterpriseQuotaComponent,
                EnterpriseCostResource | None,
                EnterpriseQuotaItem,
            ]
        ],
    ] = defaultdict(list)
    if item_ids and draft.enterprise_quota_version_id:
        component_rows = (
            db.query(
                EnterpriseQuotaComponent,
                EnterpriseCostResource,
                EnterpriseQuotaItem,
            )
            .join(
                EnterpriseQuotaItem,
                EnterpriseQuotaItem.id == EnterpriseQuotaComponent.quota_item_id,
            )
            .outerjoin(
                EnterpriseCostResource,
                EnterpriseCostResource.id == EnterpriseQuotaComponent.resource_id,
            )
            .filter(
                EnterpriseQuotaComponent.version_id == draft.enterprise_quota_version_id,
                EnterpriseQuotaComponent.quota_item_id.in_(item_ids),
                EnterpriseQuotaComponent.fee_bucket == normalized_bucket,
            )
            .order_by(
                EnterpriseQuotaComponent.quota_item_id.asc(),
                EnterpriseQuotaComponent.sort_order.asc(),
                EnterpriseQuotaComponent.id.asc(),
            )
            .all()
        )
        for component, resource, quota_item in component_rows:
            components_by_item[int(component.quota_item_id)].append(
                (component, resource, quota_item)
            )

    details: list[dict[str, Any]] = []
    for line in lines:
        line_quantity = _effective_line_quantity(line)
        if line_quantity <= 0:
            continue
        target_unit_cost = _source_component_unit(line, config["breakdown_key"])
        if target_unit_cost <= 0:
            continue
        component_rows = components_by_item.get(
            int(line.selected_enterprise_quota_item_id)
            if line.selected_enterprise_quota_item_id is not None
            else -1,
            [],
        )
        eligible_components = [
            (component, resource, quota_item)
            for component, resource, quota_item in component_rows
            if _component_content(component) > 0 and _component_price(component, resource) > 0
        ]
        component_unit_total = sum(
            (
                _component_content(component) * _component_price(component, resource)
                for component, resource, _quota_item in eligible_components
            ),
            Decimal("0"),
        )
        if eligible_components:
            details.extend(
                _component_detail(
                    bucket=normalized_bucket,
                    component=component,
                    resource=resource,
                    quota_item=quota_item,
                    line_quantity=line_quantity,
                )
                for component, resource, quota_item in eligible_components
            )
            adjustment_unit_cost = _q6(target_unit_cost - component_unit_total) or Decimal("0")
            if abs(adjustment_unit_cost) > _COMPONENT_RECONCILIATION_TOLERANCE:
                adjustment = _derived_detail(
                    bucket=normalized_bucket,
                    line=line,
                    line_quantity=line_quantity,
                    unit_cost=adjustment_unit_cost,
                )
                adjustment["resource_type"] = "费用调整"
                adjustment["resource_name"] = f"{_text(line.item_name) or '未命名报价项目'}（费用差额）"
                adjustment["category"] = "定额资源与当前报价差额"
                details.append(adjustment)
        else:
            details.append(
                _derived_detail(
                    bucket=normalized_bucket,
                    line=line,
                    line_quantity=line_quantity,
                    unit_cost=target_unit_cost,
                )
            )

    rows = _serialize_aggregated_details(normalized_bucket, details)
    return {
        "bucket": normalized_bucket,
        "library_kind": config["library_kind"],
        "resource_type": config["resource_type"],
        "headers": list(config["headers"]),
        "rows": rows,
        "row_count": len(rows),
        "enterprise_resource_row_count": sum(
            1 for row in rows if row.get("detail_source") == "enterprise_resource"
        ),
        "derived_row_count": sum(
            1 for row in rows if row.get("detail_source") == "pricing_line_breakdown"
        ),
        "total_amount": _decimal_text(
            _q6(
                sum(
                    (_decimal(row.get("amount")) or Decimal("0") for row in rows),
                    Decimal("0"),
                )
            )
        ),
    }


def build_budget_procurement_statistics(
    db: Session,
    draft: BudgetProjectPricingDraft,
) -> dict[str, Any]:
    """Aggregate purchasable materials and labor trades from quota components."""

    lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(
            BudgetProjectPricingDraftLine.source_sort_order.asc(),
            BudgetProjectPricingDraftLine.id.asc(),
        )
        .all()
    )
    item_ids = sorted(
        {
            int(line.selected_enterprise_quota_item_id)
            for line in lines
            if line.selected_enterprise_quota_item_id is not None
        }
    )
    components_by_item: dict[
        int,
        list[
            tuple[
                EnterpriseQuotaComponent,
                EnterpriseCostResource | None,
                EnterpriseQuotaItem,
            ]
        ],
    ] = defaultdict(list)
    if item_ids and draft.enterprise_quota_version_id:
        component_rows = (
            db.query(
                EnterpriseQuotaComponent,
                EnterpriseCostResource,
                EnterpriseQuotaItem,
            )
            .join(
                EnterpriseQuotaItem,
                EnterpriseQuotaItem.id == EnterpriseQuotaComponent.quota_item_id,
            )
            .outerjoin(
                EnterpriseCostResource,
                EnterpriseCostResource.id == EnterpriseQuotaComponent.resource_id,
            )
            .filter(
                EnterpriseQuotaComponent.version_id == draft.enterprise_quota_version_id,
                EnterpriseQuotaComponent.quota_item_id.in_(item_ids),
                EnterpriseQuotaComponent.fee_bucket.in_(_PROCUREMENT_BUCKETS),
            )
            .order_by(
                EnterpriseQuotaComponent.quota_item_id.asc(),
                EnterpriseQuotaComponent.sort_order.asc(),
                EnterpriseQuotaComponent.id.asc(),
            )
            .all()
        )
        for component, resource, quota_item in component_rows:
            components_by_item[int(component.quota_item_id)].append(
                (component, resource, quota_item)
            )

    material_details: list[dict[str, Any]] = []
    labor_details: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    material_requirement_line_count = 0
    material_resolved_line_count = 0
    labor_requirement_line_count = 0
    labor_resolved_line_count = 0

    for line in lines:
        line_quantity = _effective_line_quantity(line)
        if line_quantity <= 0:
            continue
        component_rows = components_by_item.get(
            int(line.selected_enterprise_quota_item_id)
            if line.selected_enterprise_quota_item_id is not None
            else -1,
            [],
        )
        material_components = [
            (component, resource, quota_item)
            for component, resource, quota_item in component_rows
            if component.fee_bucket in {"main_material", "auxiliary_material"}
            and _component_content(component) > 0
            and (
                _text(component.resource_name)
                or _text(resource.resource_name if resource else None)
                or _text(component.resource_code)
            )
        ]
        labor_components = [
            (component, resource, quota_item)
            for component, resource, quota_item in component_rows
            if component.fee_bucket == "labor"
            and _component_content(component) > 0
            and (
                _text(component.resource_name)
                or _text(resource.resource_name if resource else None)
                or _text(component.resource_code)
            )
        ]
        expects_material = (
            _source_component_unit(line, "main_material_unit_cost") > 0
            or _source_component_unit(line, "auxiliary_material_unit_cost") > 0
        )
        expects_labor = _source_component_unit(line, "labor_unit_cost") > 0
        if material_components:
            expects_material = True
        if labor_components:
            expects_labor = True

        if expects_material:
            material_requirement_line_count += 1
            if material_components:
                material_resolved_line_count += 1
        if expects_labor:
            labor_requirement_line_count += 1
            if labor_components:
                labor_resolved_line_count += 1

        material_details.extend(
            _component_detail(
                bucket=str(component.fee_bucket),
                component=component,
                resource=resource,
                quota_item=quota_item,
                line_quantity=line_quantity,
            )
            for component, resource, quota_item in material_components
        )
        labor_details.extend(
            _component_detail(
                bucket="labor",
                component=component,
                resource=resource,
                quota_item=quota_item,
                line_quantity=line_quantity,
            )
            for component, resource, quota_item in labor_components
        )

        missing_kinds: list[str] = []
        if expects_material and not material_components:
            missing_kinds.append("材料")
        if expects_labor and not labor_components:
            missing_kinds.append("人工")
        if missing_kinds:
            unresolved_rows.append(
                {
                    "id": f"unresolved:{line.id}",
                    "line_id": line.id,
                    "item_name": _text(line.item_name) or "未命名报价项目",
                    "specification": _text(line.spec),
                    "unit": _text(line.unit),
                    "quantity": _decimal_text(_q6(line_quantity)),
                    "price_source": _text(line.price_source) or "none",
                    "missing_kinds": missing_kinds,
                    "missing_kinds_text": "、".join(missing_kinds),
                }
            )

    material_rows = _aggregate_procurement_details(material_details, kind="material")
    labor_rows = _aggregate_procurement_details(labor_details, kind="labor")
    return {
        "material_rows": material_rows,
        "labor_rows": labor_rows,
        "unresolved_rows": unresolved_rows,
        "material_kind_count": len(material_rows),
        "labor_trade_count": len(labor_rows),
        "unresolved_line_count": len(unresolved_rows),
        "material_requirement_line_count": material_requirement_line_count,
        "material_resolved_line_count": material_resolved_line_count,
        "material_unresolved_line_count": (
            material_requirement_line_count - material_resolved_line_count
        ),
        "labor_requirement_line_count": labor_requirement_line_count,
        "labor_resolved_line_count": labor_resolved_line_count,
        "labor_unresolved_line_count": (
            labor_requirement_line_count - labor_resolved_line_count
        ),
        "material_unit_totals": _unit_quantity_totals(material_rows),
        "labor_unit_totals": _unit_quantity_totals(labor_rows),
        "material_amount": _decimal_text(
            _q6(
                sum(
                    (_decimal(row.get("amount")) or Decimal("0") for row in material_rows),
                    Decimal("0"),
                )
            )
        ),
        "labor_amount": _decimal_text(
            _q6(
                sum(
                    (_decimal(row.get("amount")) or Decimal("0") for row in labor_rows),
                    Decimal("0"),
                )
            )
        ),
    }
