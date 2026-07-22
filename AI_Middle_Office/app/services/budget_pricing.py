"""Version-bound project budget pricing using only enterprise quota data."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.budget_project import (
    BUDGET_IMPORT_STATUS_ACTIVE,
    BudgetProjectImportBatch,
    BudgetProjectImportRevision,
    BudgetProjectProfile,
)
from app.models.budget_pricing import (
    PRICING_COMPLETENESS_COMPLETE,
    PRICING_COMPLETENESS_PARTIAL,
    PRICING_LINE_STATUS_MISSING_UNIT_PRICE,
    PRICING_LINE_STATUS_PENDING_MATCH,
    PRICING_LINE_STATUS_PRICED,
    PRICING_LINE_STATUS_QUANTITY_UNRESOLVED,
    PRICING_LINE_STATUS_UNIT_CONFLICT,
    PRICING_MATCH_AMBIGUOUS,
    PRICING_MATCH_AUTO,
    PRICING_MATCH_UNIT_CONFLICT,
    PRICING_MATCH_UNMATCHED,
    PRICING_RUN_STATUS_PROCESSING,
    PRICING_RUN_STATUS_READY,
    BudgetProjectPricingEvent,
    BudgetProjectPricingMatchCandidate,
    BudgetProjectPricingRun,
    BudgetProjectPricingRunLine,
)
from app.models.enterprise_quota import (
    QUOTA_VERSION_STATUS_ACTIVE,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.user import User
from app.services.rbac import can_create_budget_pricing, can_view_budget_pricing


MATCHING_ENGINE_VERSION = "budget-pricing-match-v1"
PRICING_ENGINE_VERSION = "budget-pricing-decimal-v1"
PRICE_BASIS = "enterprise_quota_items.unit_price"
TAX_BASIS = "source_as_is"
_Q6 = Decimal("0.000001")
_Q2 = Decimal("0.01")
_FUZZY_MIN = Decimal("0.420000")
_MAX_CANDIDATES = 5
_NUMERIC_20_6_MAX = Decimal("99999999999999.999999")
_NUMERIC_24_6_MAX = Decimal("999999999999999999.999999")


class BudgetPricingError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


@dataclass(frozen=True)
class _QuotaEntry:
    item_id: int
    version_id: int
    quota_code: str | None
    item_name: str
    work_content: str | None
    worker_or_subtype: str | None
    unit: str
    normalized_unit: str
    unit_price: Decimal | None
    labor_fee: Decimal | None
    main_material_fee: Decimal | None
    auxiliary_material_fee: Decimal | None
    machinery_fee: Decimal | None
    name_norm: str
    spec_norm: str
    code_norm: str
    snapshot: dict[str, Any]
    full_snapshot: dict[str, Any]
    unit_price_issue: str | None = None


@dataclass(frozen=True)
class _QuotaCatalogIndex:
    by_code: dict[str, tuple[_QuotaEntry, ...]]
    by_name: dict[str, tuple[_QuotaEntry, ...]]


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _q6(value: Decimal | None) -> Decimal | None:
    if value is None or not value.is_finite():
        return None
    try:
        return value.quantize(_Q6, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _fits_numeric(value: Decimal | None, maximum: Decimal) -> bool:
    return bool(value is not None and value.is_finite() and abs(value) <= maximum and _q6(value) is not None)


def _decimal_text(value: Any) -> str | None:
    parsed = _decimal(value)
    quantized = _q6(parsed)
    return format(quantized, "f") if quantized is not None else None


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, str) else _json_dump(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean(value: Any, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _format_dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def normalize_pricing_unit(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    compact = unicodedata.normalize("NFKC", text).replace(" ", "").lower()
    aliases = {
        "m2": "area_m2",
        "平方米": "area_m2",
        "平米": "area_m2",
        "m3": "volume_m3",
        "立方米": "volume_m3",
        "立米": "volume_m3",
        "m": "length_m",
        "米": "length_m",
        "延米": "length_m",
    }
    return aliases.get(compact, compact)


def _normalize_text(value: Any) -> str:
    text = _clean(value) or ""
    text = unicodedata.normalize("NFKC", text).lower().replace("×", "x").replace("＊", "x")
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _contains_unhealthy_text(value: Any) -> bool:
    text = _clean(value) or ""
    if "\ufffd" in text:
        return True
    return any(unicodedata.category(char) in {"Cc", "Cs"} for char in text)


def quota_item_health_reason(item: EnterpriseQuotaItem) -> str | None:
    name = _clean(item.item_name)
    unit = normalize_pricing_unit(item.unit)
    if not name:
        return "missing_item_name"
    if not unit:
        return "missing_unit"
    if any(_contains_unhealthy_text(value) for value in (item.quota_code, name, item.unit)):
        return "unhealthy_text"
    if len(name) >= 255 and len(_clean(item.quota_code) or "") >= 64:
        return "suspected_truncated_text"
    if item.unit_price is not None and _decimal(item.unit_price) is None:
        return "invalid_unit_price"
    return None


def quota_item_is_healthy(item: EnterpriseQuotaItem) -> bool:
    return quota_item_health_reason(item) is None


def strict_active_quota_version(db: Session, *, for_update: bool = False) -> EnterpriseQuotaVersion:
    query = (
        db.query(EnterpriseQuotaVersion)
        .filter(
            or_(
                EnterpriseQuotaVersion.status == QUOTA_VERSION_STATUS_ACTIVE,
                EnterpriseQuotaVersion.is_active.is_(True),
            )
        )
        .order_by(EnterpriseQuotaVersion.id.asc())
    )
    if for_update:
        query = query.with_for_update()
    rows = query.all()
    inconsistent = [
        row
        for row in rows
        if (row.status == QUOTA_VERSION_STATUS_ACTIVE) != bool(row.is_active)
    ]
    if inconsistent:
        raise BudgetPricingError(
            "BUDGET_PRICING_ACTIVE_QUOTA_INCONSISTENT",
            context={"version_ids": [int(row.id) for row in inconsistent]},
        )
    active = [row for row in rows if row.status == QUOTA_VERSION_STATUS_ACTIVE and bool(row.is_active)]
    if not active:
        raise BudgetPricingError("BUDGET_PRICING_ACTIVE_QUOTA_REQUIRED")
    if len(active) != 1:
        raise BudgetPricingError(
            "BUDGET_PRICING_ACTIVE_QUOTA_AMBIGUOUS",
            context={"version_ids": [int(row.id) for row in active]},
        )
    return active[0]


def _component_snapshot(component: EnterpriseQuotaComponent) -> dict[str, Any]:
    return {
        "id": component.id,
        "component_type": component.component_type,
        "resource_id": component.resource_id,
        "resource_code": component.resource_code,
        "resource_name": component.resource_name,
        "worker_or_subtype": component.worker_or_subtype,
        "unit": component.unit,
        "quantity": _decimal_text(component.quantity),
        "unit_price": _decimal_text(component.unit_price),
        "amount": _decimal_text(component.amount),
        "fee_bucket": component.fee_bucket,
        "source_sheet": component.source_sheet,
        "source_row_index": component.source_row_index,
        "sort_order": component.sort_order,
    }


def _load_quota_catalog(db: Session, version: EnterpriseQuotaVersion) -> tuple[list[_QuotaEntry], dict[str, Any]]:
    rows = (
        db.query(EnterpriseQuotaItem, EnterpriseQuotaSection)
        .outerjoin(EnterpriseQuotaSection, EnterpriseQuotaItem.section_id == EnterpriseQuotaSection.id)
        .filter(EnterpriseQuotaItem.version_id == version.id)
        .order_by(EnterpriseQuotaItem.id.asc())
        .all()
    )
    healthy_rows: list[tuple[EnterpriseQuotaItem, EnterpriseQuotaSection | None]] = []
    rejected: dict[str, int] = {}
    for item, section in rows:
        reason = quota_item_health_reason(item)
        if section is not None and int(section.version_id) != int(version.id):
            reason = "cross_version_section"
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        healthy_rows.append((item, section))

    item_ids = [int(item.id) for item, _ in healthy_rows]
    component_rows = []
    if item_ids:
        component_rows = (
            db.query(EnterpriseQuotaComponent)
            .filter(
                EnterpriseQuotaComponent.version_id == version.id,
                EnterpriseQuotaComponent.quota_item_id.in_(item_ids),
            )
            .order_by(EnterpriseQuotaComponent.quota_item_id.asc(), EnterpriseQuotaComponent.sort_order.asc(), EnterpriseQuotaComponent.id.asc())
            .all()
        )
    components_by_item: dict[int, list[dict[str, Any]]] = {}
    for component in component_rows:
        components_by_item.setdefault(int(component.quota_item_id), []).append(_component_snapshot(component))

    entries: list[_QuotaEntry] = []
    for item, section in healthy_rows:
        raw_unit_price = _decimal(item.unit_price)
        unit_price_issue = None
        if raw_unit_price is not None and raw_unit_price <= 0:
            raw_unit_price = None
        elif raw_unit_price is not None and not _fits_numeric(raw_unit_price, _NUMERIC_20_6_MAX):
            unit_price_issue = "BUDGET_PRICING_UNIT_PRICE_OVERFLOW"
            raw_unit_price = None
        unit_price = _q6(raw_unit_price)
        base_snapshot = {
            "id": item.id,
            "version_id": item.version_id,
            "quota_code": item.quota_code,
            "item_name": item.item_name,
            "work_content": item.work_content,
            "worker_or_subtype": item.worker_or_subtype,
            "unit": item.unit,
            "unit_price": _decimal_text(unit_price),
            "labor_fee": _decimal_text(item.labor_fee),
            "main_material_fee": _decimal_text(item.main_material_fee),
            "auxiliary_material_fee": _decimal_text(item.auxiliary_material_fee),
            "machinery_fee": _decimal_text(item.machinery_fee),
            "section": (
                {
                    "id": section.id,
                    "section_code": section.section_code,
                    "section_name": section.section_name,
                }
                if section
                else None
            ),
            "source_sheet": item.source_sheet,
            "source_row_index": item.source_row_index,
        }
        full_snapshot = {**base_snapshot, "components": components_by_item.get(int(item.id), [])}
        entries.append(
            _QuotaEntry(
                item_id=int(item.id),
                version_id=int(item.version_id),
                quota_code=_clean(item.quota_code, 64),
                item_name=_clean(item.item_name, 255) or "",
                work_content=_clean(item.work_content),
                worker_or_subtype=_clean(item.worker_or_subtype, 128),
                unit=_clean(item.unit, 64) or "",
                normalized_unit=normalize_pricing_unit(item.unit) or "",
                unit_price=unit_price,
                labor_fee=_q6(_decimal(item.labor_fee)),
                main_material_fee=_q6(_decimal(item.main_material_fee)),
                auxiliary_material_fee=_q6(_decimal(item.auxiliary_material_fee)),
                machinery_fee=_q6(_decimal(item.machinery_fee)),
                name_norm=_normalize_text(item.item_name),
                spec_norm=_normalize_text(item.work_content),
                code_norm=_normalize_text(item.quota_code),
                snapshot=base_snapshot,
                full_snapshot=full_snapshot,
                unit_price_issue=unit_price_issue,
            )
        )

    catalog_payload = [entry.full_snapshot for entry in entries]
    return entries, {
        "total_item_count": len(rows),
        "eligible_item_count": len(entries),
        "priced_item_count": sum(entry.unit_price is not None for entry in entries),
        "rejected_item_count": len(rows) - len(entries),
        "rejected_by_reason": rejected,
        "catalog_sha256": _sha256(catalog_payload),
    }


def _raise_source(code: str, **context: Any) -> None:
    raise BudgetPricingError(code, context=context)


def _resolve_formal_source(
    db: Session,
    profile: BudgetProjectProfile,
    *,
    expected_batch_id: int,
    expected_revision_id: int,
) -> tuple[BudgetProjectImportBatch, BudgetProjectImportRevision, list[dict[str, Any]]]:
    if not profile.active_import_batch_id or not profile.active_import_revision_id:
        _raise_source("BUDGET_PRICING_FORMAL_IMPORT_REQUIRED")
    if int(profile.active_import_batch_id) != int(expected_batch_id) or int(profile.active_import_revision_id) != int(expected_revision_id):
        _raise_source(
            "BUDGET_PRICING_FORMAL_IMPORT_CHANGED",
            active_import_batch_id=profile.active_import_batch_id,
            active_import_revision_id=profile.active_import_revision_id,
        )
    batch = db.get(BudgetProjectImportBatch, expected_batch_id)
    revision = db.get(BudgetProjectImportRevision, expected_revision_id)
    if (
        batch is None
        or revision is None
        or int(batch.project_id) != int(profile.project_id)
        or int(revision.batch_id) != int(batch.id)
        or batch.status != BUDGET_IMPORT_STATUS_ACTIVE
        or int(batch.confirmed_revision_id or 0) != int(revision.id)
    ):
        _raise_source("BUDGET_PRICING_FORMAL_IMPORT_INVALID")
    rows = _json_load(revision.standard_rows_json, None)
    if not isinstance(rows, list):
        _raise_source("BUDGET_PRICING_SOURCE_SNAPSHOT_INVALID", field="standard_rows_json")
    formal_rows: list[dict[str, Any]] = []
    row_keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            _raise_source("BUDGET_PRICING_SOURCE_SNAPSHOT_INVALID", field="standard_rows_json.row")
        if row.get("sheet_role") != "bill" or row.get("is_standard_item") is not True:
            continue
        standard_row = row.get("standard_row")
        row_key = _clean(row.get("row_key"), 255)
        if not isinstance(standard_row, dict) or not row_key or row_key in row_keys:
            _raise_source("BUDGET_PRICING_SOURCE_SNAPSHOT_INVALID", field="standard_rows_json.formal_row")
        row_keys.add(row_key)
        formal_rows.append(row)
    if not formal_rows:
        _raise_source("BUDGET_PRICING_FORMAL_ROWS_REQUIRED")
    expected_count = int(batch.standard_item_count or 0)
    if expected_count and len(formal_rows) != expected_count:
        _raise_source(
            "BUDGET_PRICING_SOURCE_COUNT_MISMATCH",
            snapshot_count=len(formal_rows),
            expected_count=expected_count,
        )
    return batch, revision, formal_rows


def _source_quota_code(standard_row: dict[str, Any]) -> str | None:
    for key in ("quota_code", "enterprise_quota_code", "item_code"):
        value = _clean(standard_row.get(key), 64)
        if value:
            return value
    raw_fields = standard_row.get("raw_fields")
    if isinstance(raw_fields, dict):
        for key, value in raw_fields.items():
            if "定额编码" in str(key) or "项目编码" in str(key):
                cleaned = _clean(value, 64)
                if cleaned:
                    return cleaned
    return None


def _source_values(row: dict[str, Any]) -> dict[str, Any]:
    standard = row["standard_row"]
    quantity = _decimal(standard.get("calculation_quantity"))
    summary_multiplier = _decimal(standard.get("budget_summary_multiplier"))
    if summary_multiplier is None or summary_multiplier <= 0:
        summary_multiplier = Decimal("1.000000")
    summary_multiplier = _q6(summary_multiplier) or Decimal("1.000000")
    quantity_status = _clean(row.get("quantity_status") or standard.get("quantity_status"), 32) or "missing"
    quantity_resolved = (
        quantity_status == "valid"
        and quantity is not None
        and quantity > 0
        and _fits_numeric(quantity, _NUMERIC_20_6_MAX)
    )
    safe_quantity = _q6(quantity) if quantity_resolved else Decimal("0.000000")
    if quantity_status == "valid" and not quantity_resolved:
        quantity_status = "out_of_range"
    return {
        "row_key": _clean(row.get("row_key"), 255) or "",
        "source_sheet": _clean(row.get("source_sheet"), 255) or "",
        "raw_row_index": int(row.get("raw_row_index") or 0),
        "sort_order": int(row.get("sort_order") or 0),
        "item_name": _clean(standard.get("item_name"), 255),
        "spec": _clean(standard.get("spec")),
        "unit": _clean(standard.get("unit"), 64),
        "normalized_unit": normalize_pricing_unit(standard.get("unit")),
        "quota_code": _source_quota_code(standard),
        "quantity": safe_quantity,
        "summary_multiplier": summary_multiplier,
        "effective_quantity": _q6(safe_quantity * summary_multiplier) if quantity_resolved else Decimal("0.000000"),
        "quantity_status": quantity_status,
        "quantity_resolved": quantity_resolved,
        "snapshot": row,
    }


def _similarity(left: str, right: str) -> Decimal:
    if not left or not right:
        return Decimal("0")
    if left == right:
        return Decimal("1")
    ratio = Decimal(str(SequenceMatcher(None, left, right).ratio()))
    if len(left) >= 4 and (left in right or right in left):
        ratio = max(ratio, Decimal("0.850000"))
    return _q6(ratio) or Decimal("0")


def _unit_compatibility(source_unit: str | None, quota_unit: str | None) -> str:
    if not source_unit or not quota_unit:
        return "missing"
    return "compatible" if source_unit == quota_unit else "conflict"


def _spec_exact(source_spec: str, entry: _QuotaEntry) -> bool:
    if not source_spec:
        return False
    if source_spec == entry.spec_norm:
        return True
    combined = f"{entry.name_norm}{entry.spec_norm}"
    return len(source_spec) >= 4 and source_spec in combined


def _build_catalog_index(catalog: list[_QuotaEntry]) -> _QuotaCatalogIndex:
    codes: dict[str, list[_QuotaEntry]] = {}
    names: dict[str, list[_QuotaEntry]] = {}
    for entry in catalog:
        if entry.code_norm:
            codes.setdefault(entry.code_norm, []).append(entry)
        if entry.name_norm:
            names.setdefault(entry.name_norm, []).append(entry)
    return _QuotaCatalogIndex(
        by_code={key: tuple(value) for key, value in codes.items()},
        by_name={key: tuple(value) for key, value in names.items()},
    )


def _match_source(source: dict[str, Any], catalog: list[_QuotaEntry], catalog_index: _QuotaCatalogIndex | None = None) -> dict[str, Any]:
    name_norm = _normalize_text(source.get("item_name"))
    spec_norm = _normalize_text(source.get("spec"))
    code_norm = _normalize_text(source.get("quota_code"))
    records: list[dict[str, Any]] = []
    exact_name_ids: set[int] = set()
    if catalog_index is not None:
        exact_name_ids = {entry.item_id for entry in catalog_index.by_name.get(name_norm, ())}
    if name_norm or code_norm:
        for entry in catalog:
            code_exact = bool(code_norm and entry.code_norm and code_norm == entry.code_norm)
            name_score = Decimal("1") if entry.item_id in exact_name_ids else _similarity(name_norm, entry.name_norm)
            if not code_exact and name_score < _FUZZY_MIN:
                continue
            spec_score = _similarity(spec_norm, f"{entry.name_norm}{entry.spec_norm}") if spec_norm else None
            compatibility = _unit_compatibility(source.get("normalized_unit"), entry.normalized_unit)
            unit_score = Decimal("1") if compatibility == "compatible" else Decimal("0")
            if code_exact:
                score = Decimal("1")
                match_type = "code_exact"
            elif name_score == 1 and _spec_exact(spec_norm, entry):
                score = Decimal("0.980000") if compatibility == "compatible" else Decimal("0.880000")
                match_type = "name_spec_exact"
            elif name_score == 1:
                score = Decimal("0.930000") if compatibility == "compatible" else Decimal("0.830000")
                match_type = "name_exact"
            else:
                score = name_score * Decimal("0.80")
                if spec_score is not None:
                    score += spec_score * Decimal("0.10")
                score += unit_score * Decimal("0.10")
                score = _q6(score) or Decimal("0")
                match_type = "name_fuzzy"
            records.append(
                {
                    "entry": entry,
                    "score": _q6(score),
                    "name_score": name_score,
                    "spec_score": spec_score,
                    "unit_score": unit_score,
                    "match_type": match_type,
                    "unit_compatibility": compatibility,
                    "code_exact": code_exact,
                    "name_exact": name_score == 1,
                    "spec_exact": _spec_exact(spec_norm, entry),
                }
            )
    records.sort(
        key=lambda record: (
            -(record["score"] or Decimal("0")),
            record["entry"].quota_code or "\uffff",
            record["entry"].item_id,
        )
    )

    selected: dict[str, Any] | None = None
    rule = "no_candidate"
    compatible_code = [r for r in records if r["code_exact"] and r["unit_compatibility"] == "compatible"]
    compatible_name = [r for r in records if r["name_exact"] and r["unit_compatibility"] == "compatible"]
    if len(compatible_code) == 1:
        selected, rule = compatible_code[0], "unique_code_and_unit"
    elif len(compatible_name) == 1:
        selected, rule = compatible_name[0], "unique_name_and_unit"
    elif len(compatible_name) > 1:
        with_spec = [record for record in compatible_name if record["spec_exact"]]
        if len(with_spec) == 1:
            selected, rule = with_spec[0], "unique_name_spec_and_unit"

    if selected is not None:
        match_status = PRICING_MATCH_AUTO
    elif records:
        exact_records = [r for r in records if r["code_exact"] or r["name_exact"]]
        if exact_records and all(r["unit_compatibility"] == "conflict" for r in exact_records):
            match_status, rule = PRICING_MATCH_UNIT_CONFLICT, "exact_name_or_code_unit_conflict"
        elif any(r["unit_compatibility"] in {"compatible", "missing"} for r in records):
            match_status, rule = PRICING_MATCH_AMBIGUOUS, "candidate_requires_review"
        else:
            match_status, rule = PRICING_MATCH_UNIT_CONFLICT, "candidate_unit_conflict"
    else:
        match_status = PRICING_MATCH_UNMATCHED

    top_records = records[:_MAX_CANDIDATES]
    for record in top_records:
        record["is_selected"] = selected is record
        record["selection_eligibility"] = (
            "auto_select"
            if selected is record
            else ("blocked_unit" if record["unit_compatibility"] == "conflict" else "review_only")
        )
    return {
        "match_status": match_status,
        "selected": selected,
        "candidates": top_records,
        "reason": {
            "rule": rule,
            "source_name_normalized": name_norm,
            "source_spec_normalized": spec_norm,
            "source_unit_normalized": source.get("normalized_unit"),
            "source_quota_code_normalized": code_norm,
        },
    }


def _pricing_values(source: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    selected = match.get("selected")
    if selected is None:
        return {
            "pricing_status": (
                PRICING_LINE_STATUS_UNIT_CONFLICT
                if match["match_status"] == PRICING_MATCH_UNIT_CONFLICT
                else PRICING_LINE_STATUS_PENDING_MATCH
            ),
            "unit_price": None,
            "line_total": None,
            "amount_included": False,
            "unit_costs": (None, None, None, None),
            "totals": (None, None, None, None),
            "breakdown_covered": False,
        }
    entry: _QuotaEntry = selected["entry"]
    price = entry.unit_price
    raw_unit_costs = (entry.labor_fee, entry.main_material_fee, entry.auxiliary_material_fee, entry.machinery_fee)
    unit_costs = tuple(value if _fits_numeric(value, _NUMERIC_20_6_MAX) else None for value in raw_unit_costs)
    component_unit_overflow = any(raw is not None and safe is None for raw, safe in zip(raw_unit_costs, unit_costs))
    breakdown_covered = all(value is not None for value in unit_costs) and not component_unit_overflow
    if entry.unit_price_issue:
        return {
            "pricing_status": "numeric_overflow", "unit_price": None, "line_total": None,
            "amount_included": False, "unit_costs": unit_costs, "totals": (None, None, None, None),
            "breakdown_covered": False, "warning_codes": [entry.unit_price_issue],
        }
    if price is None or price <= 0:
        return {
            "pricing_status": PRICING_LINE_STATUS_MISSING_UNIT_PRICE,
            "unit_price": None,
            "line_total": None,
            "amount_included": False,
            "unit_costs": unit_costs,
            "totals": (None, None, None, None),
            "breakdown_covered": breakdown_covered,
        }
    if not _fits_numeric(price, _NUMERIC_20_6_MAX):
        return {
            "pricing_status": "numeric_overflow",
            "unit_price": None,
            "line_total": None,
            "amount_included": False,
            "unit_costs": unit_costs,
            "totals": (None, None, None, None),
            "breakdown_covered": False,
            "warning_codes": ["BUDGET_PRICING_UNIT_PRICE_OVERFLOW"],
        }
    quantity: Decimal = source["quantity"]
    if not source["quantity_resolved"]:
        totals = tuple(Decimal("0.000000") if value is not None else None for value in unit_costs)
        return {
            "pricing_status": PRICING_LINE_STATUS_QUANTITY_UNRESOLVED,
            "unit_price": price,
            "line_total": Decimal("0.000000"),
            "amount_included": False,
            "unit_costs": unit_costs,
            "totals": totals,
            "breakdown_covered": breakdown_covered,
        }
    summary_multiplier: Decimal = source.get("summary_multiplier") or Decimal("1")
    line_total = quantity * summary_multiplier * price
    if not _fits_numeric(line_total, _NUMERIC_24_6_MAX):
        return {
            "pricing_status": "numeric_overflow", "unit_price": price, "line_total": None,
            "amount_included": False, "unit_costs": unit_costs, "totals": (None, None, None, None),
            "breakdown_covered": False, "warning_codes": ["BUDGET_PRICING_LINE_TOTAL_OVERFLOW"],
        }
    raw_totals = tuple(quantity * summary_multiplier * value if value is not None else None for value in unit_costs)
    totals = tuple(_q6(value) if _fits_numeric(value, _NUMERIC_24_6_MAX) else None for value in raw_totals)
    component_total_overflow = any(raw is not None and safe is None for raw, safe in zip(raw_totals, totals))
    warning_codes = []
    if component_unit_overflow or component_total_overflow:
        warning_codes.append("BUDGET_PRICING_COMPONENT_TOTAL_OVERFLOW")
    return {
        "pricing_status": PRICING_LINE_STATUS_PRICED,
        "unit_price": price,
        "line_total": _q6(line_total),
        "amount_included": True,
        "unit_costs": unit_costs,
        "totals": totals,
        "breakdown_covered": breakdown_covered and not component_total_overflow,
        "warning_codes": warning_codes,
    }


def create_budget_pricing_run(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    source_import_batch_id: int,
    source_import_revision_id: int,
    expected_quota_version_id: int,
    reason: str | None = None,
) -> BudgetProjectPricingRun:
    locked_profile = (
        db.query(BudgetProjectProfile)
        .filter(BudgetProjectProfile.id == profile.id)
        .with_for_update()
        .one_or_none()
    )
    if locked_profile is None or int(locked_profile.project_id) != int(profile.project_id):
        raise BudgetPricingError("BUDGET_PROJECT_NOT_FOUND", status_code=404)
    if locked_profile.workspace_status != "active":
        raise BudgetPricingError("BUDGET_PROJECT_ARCHIVED")
    batch, revision, formal_rows = _resolve_formal_source(
        db,
        locked_profile,
        expected_batch_id=source_import_batch_id,
        expected_revision_id=source_import_revision_id,
    )
    quota_version = strict_active_quota_version(db, for_update=True)
    if int(quota_version.id) != int(expected_quota_version_id):
        raise BudgetPricingError(
            "BUDGET_PRICING_ACTIVE_QUOTA_CHANGED",
            context={"active_quota_version_id": quota_version.id},
        )
    catalog, catalog_stats = _load_quota_catalog(db, quota_version)
    if not catalog:
        raise BudgetPricingError("BUDGET_PRICING_QUOTA_CATALOG_EMPTY")

    previous_run = (
        db.query(BudgetProjectPricingRun)
        .filter(BudgetProjectPricingRun.project_id == locked_profile.project_id)
        .order_by(BudgetProjectPricingRun.run_number.desc(), BudgetProjectPricingRun.id.desc())
        .first()
    )
    run_number = int(previous_run.run_number if previous_run else 0) + 1
    source_rows_sha256 = _sha256(formal_rows)
    source_snapshot = {
        "project_id": locked_profile.project_id,
        "batch_id": batch.id,
        "batch_uuid": batch.batch_uuid,
        "revision_id": revision.id,
        "revision_uuid": revision.revision_uuid,
        "revision_number": revision.revision_number,
        "revision_snapshot_sha256": revision.snapshot_sha256,
        "standard_rows": formal_rows,
    }
    run = BudgetProjectPricingRun(
        run_uuid=str(uuid4()),
        project_id=locked_profile.project_id,
        run_number=run_number,
        parent_run_id=previous_run.id if previous_run else None,
        run_kind="auto_match",
        reason=_clean(reason, 2000),
        source_import_batch_id=batch.id,
        source_import_revision_id=revision.id,
        source_import_snapshot_sha256=revision.snapshot_sha256,
        source_rows_sha256=source_rows_sha256,
        source_snapshot_json=_json_dump(source_snapshot),
        quota_version_id=quota_version.id,
        quota_version_code=quota_version.version_code,
        quota_version_name=quota_version.version_name,
        quota_source_file_sha256=quota_version.source_file_sha256,
        quota_catalog_sha256=catalog_stats["catalog_sha256"],
        matching_engine_version=MATCHING_ENGINE_VERSION,
        pricing_engine_version=PRICING_ENGINE_VERSION,
        price_basis=PRICE_BASIS,
        tax_basis=TAX_BASIS,
        status=PRICING_RUN_STATUS_PROCESSING,
        summary_json="{}",
        created_by=current_user.id,
    )
    db.add(run)
    # Persist the run header before calculating lines so the database-generated
    # created_at is guaranteed to precede ready_at. The transaction is still
    # atomic: any later pricing failure rolls this insert back with its lines.
    db.flush()

    counters = {
        "standard_item_count": len(formal_rows),
        "matched_count": 0,
        "unit_priced_count": 0,
        "amount_priced_count": 0,
        "review_required_count": 0,
        "unmatched_count": 0,
        "unit_conflict_count": 0,
        "quantity_unresolved_count": 0,
        "missing_price_count": 0,
        "numeric_overflow_count": 0,
        "breakdown_covered_line_count": 0,
    }
    priced_subtotal = Decimal("0")
    split_subtotals: list[Decimal | None] = [None, None, None, None]
    split_subtotal_overflow = [False, False, False, False]
    result_lines: list[dict[str, Any]] = []

    catalog_index = _build_catalog_index(catalog)
    for row in formal_rows:
        source = _source_values(row)
        match = _match_source(source, catalog, catalog_index)
        pricing = _pricing_values(source, match)
        selected_record = match.get("selected")
        selected_entry: _QuotaEntry | None = selected_record["entry"] if selected_record else None
        unit_costs = pricing["unit_costs"]
        totals = pricing["totals"]
        warnings = list((row.get("standard_row") or {}).get("warnings") or [])
        warnings.extend(pricing.get("warning_codes") or [])
        if pricing.get("amount_included") and not _fits_numeric(priced_subtotal + pricing["line_total"], _NUMERIC_24_6_MAX):
            pricing.update(pricing_status="numeric_overflow", line_total=None, amount_included=False, totals=(None, None, None, None))
            warnings.append("BUDGET_PRICING_SUBTOTAL_OVERFLOW")
            totals = pricing["totals"]
        if pricing["pricing_status"] == "numeric_overflow":
            counters["numeric_overflow_count"] += 1
        if not source["quantity_resolved"]:
            warnings.append("BUDGET_PRICING_QUANTITY_UNRESOLVED")
            counters["quantity_unresolved_count"] += 1
        if match["match_status"] == PRICING_MATCH_UNMATCHED:
            warnings.append("BUDGET_PRICING_UNMATCHED")
            counters["unmatched_count"] += 1
        if match["match_status"] == PRICING_MATCH_UNIT_CONFLICT:
            warnings.append("BUDGET_PRICING_UNIT_CONFLICT")
            counters["unit_conflict_count"] += 1
        if selected_entry:
            counters["matched_count"] += 1
            if selected_entry.unit_price is not None:
                counters["unit_priced_count"] += 1
            else:
                counters["missing_price_count"] += 1
        if pricing["amount_included"]:
            counters["amount_priced_count"] += 1
            priced_subtotal += pricing["line_total"] or Decimal("0")
            for index, total in enumerate(totals):
                if total is None or split_subtotal_overflow[index]:
                    continue
                next_split = (split_subtotals[index] or Decimal("0")) + total
                if not _fits_numeric(next_split, _NUMERIC_24_6_MAX):
                    split_subtotals[index] = None
                    split_subtotal_overflow[index] = True
                    warnings.append("BUDGET_PRICING_COMPONENT_SUBTOTAL_OVERFLOW")
                else:
                    split_subtotals[index] = next_split
        if pricing["breakdown_covered"]:
            counters["breakdown_covered_line_count"] += 1
        if (
            match["match_status"] != PRICING_MATCH_AUTO
            or pricing["pricing_status"] != PRICING_LINE_STATUS_PRICED
            or any("OVERFLOW" in warning for warning in warnings)
        ):
            counters["review_required_count"] += 1

        selected_snapshot = selected_entry.full_snapshot if selected_entry else None
        line = BudgetProjectPricingRunLine(
            line_uuid=str(uuid4()),
            source_row_key=source["row_key"],
            source_sheet=source["source_sheet"],
            source_raw_row_index=source["raw_row_index"],
            source_sort_order=source["sort_order"],
            source_row_sha256=_sha256(row),
            source_row_snapshot_json=_json_dump(row),
            item_name=source["item_name"],
            spec=source["spec"],
            unit=source["unit"],
            calculation_quantity=source["quantity"],
            quantity_status=source["quantity_status"],
            match_status=match["match_status"],
            unit_compatibility=(selected_record or (match["candidates"][0] if match["candidates"] else {})).get("unit_compatibility"),
            selected_quota_item_id=selected_entry.item_id if selected_entry else None,
            selected_quota_item_snapshot_json=_json_dump(selected_snapshot) if selected_snapshot else None,
            selected_quota_item_snapshot_sha256=_sha256(selected_snapshot) if selected_snapshot else None,
            selection_source="automatic" if selected_entry else None,
            match_score=(selected_record or (match["candidates"][0] if match["candidates"] else {})).get("score"),
            match_reason_json=_json_dump(match["reason"]),
            candidate_count=len(match["candidates"]),
            pricing_status=pricing["pricing_status"],
            quota_unit_price=pricing["unit_price"],
            effective_unit_cost=pricing["unit_price"],
            line_total=pricing["line_total"],
            amount_included=pricing["amount_included"],
            labor_unit_cost=unit_costs[0],
            main_material_unit_cost=unit_costs[1],
            auxiliary_material_unit_cost=unit_costs[2],
            machinery_unit_cost=unit_costs[3],
            labor_total=totals[0],
            main_material_total=totals[1],
            auxiliary_material_total=totals[2],
            machinery_total=totals[3],
            cost_breakdown_json=_json_dump(
                {
                    "price_basis": PRICE_BASIS,
                    "tax_basis": TAX_BASIS,
                    "breakdown_covered": pricing["breakdown_covered"],
                    "component_evidence_only": True,
                }
            ),
            warnings_json=_json_dump(list(dict.fromkeys(warnings))),
        )
        run.lines.append(line)
        result_candidates = []
        for rank, candidate in enumerate(match["candidates"], start=1):
            entry: _QuotaEntry = candidate["entry"]
            evidence = {**match["reason"], "quota_item_id": entry.item_id, "component_count": len(entry.full_snapshot.get("components") or [])}
            line.candidates.append(
                BudgetProjectPricingMatchCandidate(
                    rank=rank,
                    quota_item_id=entry.item_id,
                    quota_item_snapshot_json=_json_dump(entry.snapshot),
                    candidate_score=candidate["score"],
                    name_score=candidate["name_score"],
                    spec_score=candidate["spec_score"],
                    unit_score=candidate["unit_score"],
                    match_type=candidate["match_type"],
                    unit_compatibility=candidate["unit_compatibility"],
                    selection_eligibility=candidate["selection_eligibility"],
                    is_selected=candidate["is_selected"],
                    evidence_json=_json_dump(evidence),
                )
            )
            result_candidates.append({"rank": rank, "quota_item_snapshot": entry.snapshot, "candidate_score": _decimal_text(candidate["score"]), "match_type": candidate["match_type"], "unit_compatibility": candidate["unit_compatibility"], "selection_eligibility": candidate["selection_eligibility"], "is_selected": candidate["is_selected"], "evidence": evidence})
        result_lines.append(
            {
                "source_row_sha256": line.source_row_sha256,
                "match_status": line.match_status,
                "pricing_status": line.pricing_status,
                "selected_quota_item_snapshot_sha256": line.selected_quota_item_snapshot_sha256,
                "line_total": _decimal_text(line.line_total),
                "amount_included": bool(line.amount_included),
                "candidates": result_candidates,
            }
        )

    counters["ambiguous_count"] = max(
        0,
        counters["standard_item_count"]
        - counters["matched_count"]
        - counters["unmatched_count"]
        - counters["unit_conflict_count"],
    )
    complete = counters["amount_priced_count"] == counters["standard_item_count"] and counters["review_required_count"] == 0
    completeness = PRICING_COMPLETENESS_COMPLETE if complete else PRICING_COMPLETENESS_PARTIAL
    priced_subtotal = _q6(priced_subtotal) or Decimal("0.000000")
    coverage_percent = (
        Decimal(counters["amount_priced_count"]) * Decimal("100") / Decimal(counters["standard_item_count"])
    ).quantize(_Q2, rounding=ROUND_HALF_UP)
    summary = {
        **counters,
        "completeness_status": completeness,
        "coverage_status": completeness,
        "coverage_percent": format(coverage_percent, "f"),
        "priced_subtotal": _decimal_text(priced_subtotal),
        "total_cost": _decimal_text(priced_subtotal) if complete else None,
        "price_basis": PRICE_BASIS,
        "tax_basis": TAX_BASIS,
        "quota_catalog": catalog_stats,
        "partial_reason": None if complete else "存在未匹配、待复核、缺价或工程量未解决的正式清单行",
    }
    run.standard_item_count = counters["standard_item_count"]
    run.matched_count = counters["matched_count"]
    run.unit_priced_count = counters["unit_priced_count"]
    run.amount_priced_count = counters["amount_priced_count"]
    run.review_required_count = counters["review_required_count"]
    run.unmatched_count = counters["unmatched_count"]
    run.unit_conflict_count = counters["unit_conflict_count"]
    run.quantity_unresolved_count = counters["quantity_unresolved_count"]
    run.missing_price_count = counters["missing_price_count"]
    run.breakdown_covered_line_count = counters["breakdown_covered_line_count"]
    run.priced_subtotal = priced_subtotal
    run.total_cost = priced_subtotal if complete else None
    run.labor_subtotal = _q6(split_subtotals[0])
    run.main_material_subtotal = _q6(split_subtotals[1])
    run.auxiliary_material_subtotal = _q6(split_subtotals[2])
    run.machinery_subtotal = _q6(split_subtotals[3])
    run.completeness_status = completeness
    run.summary_json = _json_dump(summary)
    run.status = PRICING_RUN_STATUS_READY
    # Use the same database clock that populated ``created_at``.  The
    # application host and MySQL host can differ by a few seconds, which would
    # otherwise make a completed run appear ready before it was created.
    run.ready_at = db.execute(select(func.now())).scalar_one()
    run.result_sha256 = _sha256(
        {
            "source_rows_sha256": source_rows_sha256,
            "quota_catalog_sha256": catalog_stats["catalog_sha256"],
            "summary": summary,
            "lines": result_lines,
        }
    )
    run.events.append(
        BudgetProjectPricingEvent(
            event_uuid=str(uuid4()),
            project_id=locked_profile.project_id,
            event_type="run_created",
            from_status=PRICING_RUN_STATUS_PROCESSING,
            to_status=PRICING_RUN_STATUS_READY,
            actor_id=current_user.id,
            event_json=_json_dump(
                {
                    "source_import_batch_id": batch.id,
                    "source_import_revision_id": revision.id,
                    "quota_version_id": quota_version.id,
                    "result_sha256": run.result_sha256,
                    "completeness_status": completeness,
                }
            ),
        )
    )
    db.flush()
    return run


_READINESS_MESSAGES = {
    "BUDGET_PRICING_FORMAL_IMPORT_REQUIRED": "请先确认并启用正式工程量清单",
    "BUDGET_PRICING_FORMAL_IMPORT_INVALID": "正式清单双指针状态不一致",
    "BUDGET_PRICING_SOURCE_SNAPSHOT_INVALID": "正式清单不可变快照损坏",
    "BUDGET_PRICING_FORMAL_ROWS_REQUIRED": "正式清单没有可计价的 bill 行",
    "BUDGET_PRICING_ACTIVE_QUOTA_REQUIRED": "当前没有 active 企业定额版本",
    "BUDGET_PRICING_ACTIVE_QUOTA_AMBIGUOUS": "当前存在多个 active 企业定额版本",
    "BUDGET_PRICING_ACTIVE_QUOTA_INCONSISTENT": "企业定额状态与 active 标记不一致",
    "BUDGET_PRICING_QUOTA_CATALOG_EMPTY": "active 企业定额没有健康候选项",
    "BUDGET_PROJECT_ARCHIVED": "归档项目只能回看历史计价",
}


def build_budget_pricing_readiness(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    source_data: dict[str, Any] | None = None
    quota_data: dict[str, Any] | None = None
    catalog_stats: dict[str, Any] | None = None
    if profile.workspace_status != "active":
        blockers.append({"code": "BUDGET_PROJECT_ARCHIVED"})
    try:
        batch, revision, formal_rows = _resolve_formal_source(
            db,
            profile,
            expected_batch_id=int(profile.active_import_batch_id or 0),
            expected_revision_id=int(profile.active_import_revision_id or 0),
        )
        source_data = {
            "batch_id": batch.id,
            "batch_uuid": batch.batch_uuid,
            "revision_id": revision.id,
            "revision_uuid": revision.revision_uuid,
            "snapshot_sha256": revision.snapshot_sha256,
            "standard_item_count": len(formal_rows),
        }
    except BudgetPricingError as exc:
        blockers.append(exc.detail)
    try:
        version = strict_active_quota_version(db)
        catalog, catalog_stats = _load_quota_catalog(db, version)
        quota_data = {
            "id": version.id,
            "version_code": version.version_code,
            "version_name": version.version_name,
            "source_file_sha256": version.source_file_sha256,
            "catalog_sha256": catalog_stats["catalog_sha256"],
            "eligible_item_count": len(catalog),
        }
        if not catalog:
            blockers.append({"code": "BUDGET_PRICING_QUOTA_CATALOG_EMPTY"})
    except BudgetPricingError as exc:
        blockers.append(exc.detail)
    eligible = not blockers
    first_code = blockers[0]["code"] if blockers else None
    return {
        "project_id": profile.project_id,
        "eligible": eligible,
        "ready": eligible,
        "message": "可以基于当前正式清单创建计价" if eligible else _READINESS_MESSAGES.get(first_code, first_code),
        "active_import_batch_id": profile.active_import_batch_id,
        "active_import_revision_id": profile.active_import_revision_id,
        "active_import": source_data,
        "active_quota_version": quota_data,
        "quota_catalog": catalog_stats,
        "current_pricing_run_id": profile.active_pricing_run_id,
        "blockers": blockers,
        "capabilities": {
            "can_view_pricing": can_view_budget_pricing(current_user),
            "can_create_pricing_run": eligible and can_create_budget_pricing(current_user),
        },
    }


def get_budget_pricing_run(db: Session, identifier: str | int) -> BudgetProjectPricingRun:
    text = str(identifier).strip()
    query = db.query(BudgetProjectPricingRun)
    run = query.filter(BudgetProjectPricingRun.id == int(text)).first() if text.isdigit() else query.filter(BudgetProjectPricingRun.run_uuid == text).first()
    if run is None:
        raise BudgetPricingError("BUDGET_PRICING_RUN_NOT_FOUND", status_code=404)
    return run


def get_budget_pricing_line(
    db: Session,
    run: BudgetProjectPricingRun,
    identifier: str | int,
) -> BudgetProjectPricingRunLine:
    text = str(identifier).strip()
    query = db.query(BudgetProjectPricingRunLine).filter(BudgetProjectPricingRunLine.run_id == run.id)
    line = query.filter(BudgetProjectPricingRunLine.id == int(text)).first() if text.isdigit() else query.filter(BudgetProjectPricingRunLine.line_uuid == text).first()
    if line is None:
        raise BudgetPricingError("BUDGET_PRICING_LINE_NOT_FOUND", status_code=404)
    return line


def _status_message(line: BudgetProjectPricingRunLine) -> str:
    if line.pricing_status == PRICING_LINE_STATUS_PRICED:
        return "已匹配企业定额并完成金额计算"
    if line.pricing_status == PRICING_LINE_STATUS_QUANTITY_UNRESOLVED:
        return "已匹配并展示单位成本，工程量待解决，金额未计入"
    if line.pricing_status == PRICING_LINE_STATUS_MISSING_UNIT_PRICE:
        return "已识别定额，但企业定额综合单价缺失"
    if line.pricing_status == PRICING_LINE_STATUS_UNIT_CONFLICT:
        return "存在名称候选，但单位不兼容"
    if line.pricing_status == "numeric_overflow":
        return "计价数值超出当前精度范围，已排除出完整成本并需人工复核"
    return "尚未形成唯一匹配"


def serialize_budget_pricing_run(run: BudgetProjectPricingRun) -> dict[str, Any]:
    summary = _json_load(run.summary_json, {})
    return {
        "id": run.id,
        "run_uuid": run.run_uuid,
        "project_id": run.project_id,
        "run_number": run.run_number,
        "parent_run_id": run.parent_run_id,
        "run_kind": run.run_kind,
        "reason": run.reason,
        "source_import_batch_id": run.source_import_batch_id,
        "source_import_revision_id": run.source_import_revision_id,
        "source_import_snapshot_sha256": run.source_import_snapshot_sha256,
        "source_rows_sha256": run.source_rows_sha256,
        "quota_version_id": run.quota_version_id,
        "quota_version_code": run.quota_version_code,
        "quota_version_name": run.quota_version_name,
        "quota_catalog_sha256": run.quota_catalog_sha256,
        "quota_version": {
            "id": run.quota_version_id,
            "version_code": run.quota_version_code,
            "version_name": run.quota_version_name,
            "source_file_sha256": run.quota_source_file_sha256,
        },
        "matching_engine_version": run.matching_engine_version,
        "pricing_engine_version": run.pricing_engine_version,
        "price_basis": run.price_basis,
        "tax_basis": run.tax_basis,
        "status": run.status,
        "completeness_status": run.completeness_status,
        "coverage_status": run.completeness_status,
        "standard_item_count": run.standard_item_count,
        "matched_count": run.matched_count,
        "ambiguous_count": int(summary.get("ambiguous_count") or 0),
        "unit_priced_count": run.unit_priced_count,
        "amount_priced_count": run.amount_priced_count,
        "review_required_count": run.review_required_count,
        "unmatched_count": run.unmatched_count,
        "unit_conflict_count": run.unit_conflict_count,
        "quantity_unresolved_count": run.quantity_unresolved_count,
        "missing_price_count": run.missing_price_count,
        "numeric_overflow_count": int(summary.get("numeric_overflow_count") or 0),
        "breakdown_covered_line_count": run.breakdown_covered_line_count,
        "priced_subtotal": _decimal_text(run.priced_subtotal),
        "total_cost": _decimal_text(run.total_cost),
        "labor_subtotal": _decimal_text(run.labor_subtotal),
        "main_material_subtotal": _decimal_text(run.main_material_subtotal),
        "auxiliary_material_subtotal": _decimal_text(run.auxiliary_material_subtotal),
        "machinery_subtotal": _decimal_text(run.machinery_subtotal),
        "summary": summary,
        "coverage_percent": summary.get("coverage_percent"),
        "result_sha256": run.result_sha256,
        "created_by": run.created_by,
        "created_at": _format_dt(run.created_at),
        "ready_at": _format_dt(run.ready_at),
    }


def _source_row_context(snapshot_json: str | None) -> dict[str, Any]:
    snapshot = _json_load(snapshot_json, {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    standard_row = snapshot.get("standard_row") if isinstance(snapshot.get("standard_row"), dict) else {}
    raw_fields = standard_row.get("raw_fields") if isinstance(standard_row.get("raw_fields"), dict) else {}
    location = standard_row.get("location") or standard_row.get("work_area")
    return {
        "region": standard_row.get("region") or standard_row.get("area"),
        "work_area": standard_row.get("work_area") or location,
        "location": location,
        "remark": standard_row.get("remark"),
        "raw_fields": raw_fields,
    }


def serialize_budget_pricing_line(line: BudgetProjectPricingRunLine) -> dict[str, Any]:
    selected = _json_load(line.selected_quota_item_snapshot_json, None)
    source_context = _source_row_context(line.source_row_snapshot_json)
    return {
        "id": line.id,
        "line_uuid": line.line_uuid,
        "run_id": line.run_id,
        "source_row_key": line.source_row_key,
        "source_sheet": line.source_sheet,
        "source_raw_row_index": line.source_raw_row_index,
        "source_sort_order": line.source_sort_order,
        "source_context": source_context,
        "region": source_context.get("region"),
        "work_area": source_context.get("work_area"),
        "remark": source_context.get("remark"),
        "item_name": line.item_name,
        "spec": line.spec,
        "unit": line.unit,
        "quantity": _decimal_text(line.calculation_quantity),
        "calculation_quantity": _decimal_text(line.calculation_quantity),
        "quantity_status": line.quantity_status,
        "match_status": line.match_status,
        "pricing_status": line.pricing_status,
        "unit_compatibility": line.unit_compatibility,
        "selected_quota_item_id": line.selected_quota_item_id,
        "selected_quota": selected,
        "match_score": _decimal_text(line.match_score),
        "match_evidence": _json_load(line.match_reason_json, {}),
        "candidate_count": line.candidate_count,
        "quota_unit_price": _decimal_text(line.quota_unit_price),
        "effective_unit_cost": _decimal_text(line.effective_unit_cost),
        "line_total": _decimal_text(line.line_total),
        "amount_included": bool(line.amount_included),
        "labor_unit_cost": _decimal_text(line.labor_unit_cost),
        "main_material_unit_cost": _decimal_text(line.main_material_unit_cost),
        "auxiliary_material_unit_cost": _decimal_text(line.auxiliary_material_unit_cost),
        "machinery_unit_cost": _decimal_text(line.machinery_unit_cost),
        "labor_total": _decimal_text(line.labor_total),
        "main_material_total": _decimal_text(line.main_material_total),
        "auxiliary_material_total": _decimal_text(line.auxiliary_material_total),
        "machinery_total": _decimal_text(line.machinery_total),
        "cost_breakdown": _json_load(line.cost_breakdown_json, {}),
        "warnings": _json_load(line.warnings_json, []),
        "status_message": _status_message(line),
        "created_at": _format_dt(line.created_at),
    }


def serialize_budget_pricing_candidate(candidate: BudgetProjectPricingMatchCandidate) -> dict[str, Any]:
    snapshot = _json_load(candidate.quota_item_snapshot_json, {})
    evidence = _json_load(candidate.evidence_json, {})
    reasons = [evidence.get("rule")] if evidence.get("rule") else []
    return {
        "id": candidate.id,
        "run_line_id": candidate.run_line_id,
        "rank": candidate.rank,
        "quota_item_id": candidate.quota_item_id,
        "quota_item": snapshot,
        "quota_item_snapshot": snapshot,
        "unit_price": snapshot.get("unit_price"),
        "candidate_score": _decimal_text(candidate.candidate_score),
        "name_score": _decimal_text(candidate.name_score),
        "spec_score": _decimal_text(candidate.spec_score),
        "unit_score": _decimal_text(candidate.unit_score),
        "match_type": candidate.match_type,
        "unit_compatibility": candidate.unit_compatibility,
        "selection_eligibility": candidate.selection_eligibility,
        "is_selected": bool(candidate.is_selected),
        "evidence": evidence,
        "reasons": reasons,
        "created_at": _format_dt(candidate.created_at),
    }


def serialize_budget_pricing_event(event: BudgetProjectPricingEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_uuid": event.event_uuid,
        "project_id": event.project_id,
        "run_id": event.run_id,
        "event_type": event.event_type,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "actor_id": event.actor_id,
        "event": _json_load(event.event_json, {}),
        "created_at": _format_dt(event.created_at),
    }
