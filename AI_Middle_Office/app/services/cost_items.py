from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Mapping
from uuid import uuid4

from fastapi import HTTPException, status
from openpyxl import load_workbook
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.cost_item import (
    CHANGE_TYPE_PRICE,
    CHANGE_TYPE_STATUS,
    COST_SOURCE_IMPORTED,
    COST_SOURCE_VALUES,
    COST_STATUS_ACTIVE,
    COST_STATUS_ARCHIVED,
    COST_STATUS_DRAFT,
    COST_STATUS_VALUES,
    CostItem,
    CostItemHistory,
    PRICE_TYPE_COMBINED,
    PRICE_TYPE_VALUES,
)
from app.models.user import User
from app.services.rbac import has_admin_role, has_any_role


IMPORT_BATCH_TTL = timedelta(minutes=30)
IMPORT_BATCHES: dict[str, "ImportBatch"] = {}

PRICE_FIELDS = (
    "price",
    "client_tax_excluded_price",
    "client_labor_price",
    "client_main_material_price",
    "client_auxiliary_material_price",
    "client_direct_fee",
    "client_management_profit",
    "subcontract_composite_price",
    "subcontract_labor_price",
    "subcontract_main_material_price",
    "subcontract_auxiliary_material_price",
    "crew_benchmark_price",
)
UPDATE_FIELDS = {
    "category",
    "subcategory",
    "item_name",
    "spec",
    "unit",
    "price",
    "client_tax_excluded_price",
    "client_labor_price",
    "client_main_material_price",
    "client_auxiliary_material_price",
    "client_direct_fee",
    "client_management_profit",
    "subcontract_composite_price",
    "subcontract_labor_price",
    "subcontract_main_material_price",
    "subcontract_auxiliary_material_price",
    "crew_benchmark_price",
    "price_type",
    "source",
    "effective_date",
    "notes",
}


@dataclass
class ImportBatch:
    batch_id: str
    created_at: datetime
    items: list[dict[str, Any]]
    duplicate_warnings: list[dict[str, Any]]
    skipped_rows: list[dict[str, Any]]
    confirmed: bool = False
    result: dict[str, Any] | None = None


def can_access_cost_db(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "staff"})


def can_manage_cost_db(user: User) -> bool:
    return has_admin_role(user)


def require_cost_db_access(user: User) -> None:
    if not can_access_cost_db(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_cost_db_manager(user: User) -> None:
    if not can_manage_cost_db(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _payload_dict(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    if hasattr(payload, "dict"):
        return payload.dict(exclude_unset=True)
    return dict(payload or {})


def clean_text(value: Any, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned[:max_length] if max_length else cleaned


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    for parser in (
        date.fromisoformat,
        lambda text: datetime.strptime(text, "%Y-%m-%d").date(),
        lambda text: datetime.strptime(text, "%Y/%m/%d").date(),
    ):
        try:
            return parser(raw)
        except ValueError:
            continue
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_EFFECTIVE_DATE")


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", "")
    if not raw or raw in {"-", "/", "—"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _positive_price(value: float | None, *, field_name: str) -> float | None:
    if value is None:
        return None
    if value <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"INVALID_{field_name.upper()}")
    return round(float(value), 6)


def _non_negative_price(value: float | None, *, field_name: str) -> float | None:
    if value is None:
        return None
    if value < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"INVALID_{field_name.upper()}")
    return round(float(value), 6)


def derive_main_price(data: Mapping[str, Any]) -> float:
    explicit = parse_float(data.get("price"))
    if explicit is not None:
        return _positive_price(explicit, field_name="price") or 0.0
    for field_name in ("subcontract_composite_price", "crew_benchmark_price", "client_tax_excluded_price"):
        candidate = parse_float(data.get(field_name))
        if candidate is not None:
            return _positive_price(candidate, field_name=field_name) or 0.0
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PRICE_REQUIRED")


def normalize_price_type(value: Any) -> str:
    price_type = clean_text(value, 24) or PRICE_TYPE_COMBINED
    if price_type not in PRICE_TYPE_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PRICE_TYPE")
    return price_type


def normalize_source(value: Any) -> str:
    source = clean_text(value, 32) or "manual"
    if source not in COST_SOURCE_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_SOURCE")
    return source


def normalize_status(value: Any) -> str:
    status_value = clean_text(value, 24) or COST_STATUS_DRAFT
    if status_value not in COST_STATUS_VALUES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_STATUS")
    return status_value


def _normalize_item_payload(data: Mapping[str, Any], *, for_import: bool = False) -> dict[str, Any]:
    category = clean_text(data.get("category"), 128)
    item_name = clean_text(data.get("item_name"), 255)
    unit = clean_text(data.get("unit"), 64)
    if not category:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CATEGORY_REQUIRED")
    if not item_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ITEM_NAME_REQUIRED")
    if not unit:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNIT_REQUIRED")

    normalized = {
        "category": category,
        "subcategory": clean_text(data.get("subcategory"), 128),
        "item_name": item_name,
        "spec": clean_text(data.get("spec")),
        "unit": unit,
        "price": derive_main_price(data),
        "client_tax_excluded_price": _positive_price(parse_float(data.get("client_tax_excluded_price")), field_name="client_tax_excluded_price"),
        "client_labor_price": _non_negative_price(parse_float(data.get("client_labor_price")), field_name="client_labor_price"),
        "client_main_material_price": _non_negative_price(parse_float(data.get("client_main_material_price")), field_name="client_main_material_price"),
        "client_auxiliary_material_price": _non_negative_price(parse_float(data.get("client_auxiliary_material_price")), field_name="client_auxiliary_material_price"),
        "client_direct_fee": _non_negative_price(parse_float(data.get("client_direct_fee")), field_name="client_direct_fee"),
        "client_management_profit": _non_negative_price(parse_float(data.get("client_management_profit")), field_name="client_management_profit"),
        "subcontract_composite_price": _positive_price(parse_float(data.get("subcontract_composite_price")), field_name="subcontract_composite_price"),
        "subcontract_labor_price": _non_negative_price(parse_float(data.get("subcontract_labor_price")), field_name="subcontract_labor_price"),
        "subcontract_main_material_price": _non_negative_price(parse_float(data.get("subcontract_main_material_price")), field_name="subcontract_main_material_price"),
        "subcontract_auxiliary_material_price": _non_negative_price(parse_float(data.get("subcontract_auxiliary_material_price")), field_name="subcontract_auxiliary_material_price"),
        "crew_benchmark_price": _positive_price(parse_float(data.get("crew_benchmark_price")), field_name="crew_benchmark_price"),
        "price_type": normalize_price_type(data.get("price_type")),
        "source": COST_SOURCE_IMPORTED if for_import else normalize_source(data.get("source")),
        "effective_date": parse_date(data.get("effective_date")),
        "notes": clean_text(data.get("notes")),
    }
    return normalized


def _format_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat(timespec="seconds")


def _format_date(value: date | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


def _history_snapshot(history: CostItemHistory) -> dict[str, Any]:
    return {
        "id": history.id,
        "cost_item_id": history.cost_item_id,
        "old_price": history.old_price,
        "new_price": history.new_price,
        "old_client_tax_excluded_price": history.old_client_tax_excluded_price,
        "new_client_tax_excluded_price": history.new_client_tax_excluded_price,
        "old_client_labor_price": history.old_client_labor_price,
        "new_client_labor_price": history.new_client_labor_price,
        "old_client_main_material_price": history.old_client_main_material_price,
        "new_client_main_material_price": history.new_client_main_material_price,
        "old_client_auxiliary_material_price": history.old_client_auxiliary_material_price,
        "new_client_auxiliary_material_price": history.new_client_auxiliary_material_price,
        "old_client_direct_fee": history.old_client_direct_fee,
        "new_client_direct_fee": history.new_client_direct_fee,
        "old_client_management_profit": history.old_client_management_profit,
        "new_client_management_profit": history.new_client_management_profit,
        "old_subcontract_composite_price": history.old_subcontract_composite_price,
        "new_subcontract_composite_price": history.new_subcontract_composite_price,
        "old_subcontract_labor_price": history.old_subcontract_labor_price,
        "new_subcontract_labor_price": history.new_subcontract_labor_price,
        "old_subcontract_main_material_price": history.old_subcontract_main_material_price,
        "new_subcontract_main_material_price": history.new_subcontract_main_material_price,
        "old_subcontract_auxiliary_material_price": history.old_subcontract_auxiliary_material_price,
        "new_subcontract_auxiliary_material_price": history.new_subcontract_auxiliary_material_price,
        "old_crew_benchmark_price": history.old_crew_benchmark_price,
        "new_crew_benchmark_price": history.new_crew_benchmark_price,
        "old_status": history.old_status,
        "new_status": history.new_status,
        "change_type": history.change_type,
        "changed_by": history.changed_by,
        "change_reason": history.change_reason,
        "changed_at": _format_dt(history.changed_at),
    }


def serialize_cost_item(item: CostItem, *, include_history: bool = False) -> dict[str, Any]:
    data = {
        "id": item.id,
        "category": item.category,
        "subcategory": item.subcategory,
        "item_name": item.item_name,
        "spec": item.spec,
        "unit": item.unit,
        "price": item.price,
        "client_tax_excluded_price": item.client_tax_excluded_price,
        "client_labor_price": item.client_labor_price,
        "client_main_material_price": item.client_main_material_price,
        "client_auxiliary_material_price": item.client_auxiliary_material_price,
        "client_direct_fee": item.client_direct_fee,
        "client_management_profit": item.client_management_profit,
        "subcontract_composite_price": item.subcontract_composite_price,
        "subcontract_labor_price": item.subcontract_labor_price,
        "subcontract_main_material_price": item.subcontract_main_material_price,
        "subcontract_auxiliary_material_price": item.subcontract_auxiliary_material_price,
        "crew_benchmark_price": item.crew_benchmark_price,
        "price_type": item.price_type,
        "status": item.status,
        "source": item.source,
        "effective_date": _format_date(item.effective_date),
        "notes": item.notes,
        "created_by": item.created_by,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }
    if include_history:
        data["history"] = [_history_snapshot(history) for history in sorted(item.history, key=lambda row: row.id)]
    return data


def _write_price_history(db: Session, item: CostItem, user: User, old_values: dict[str, Any], reason: str | None) -> None:
    changed = any(old_values[field] != getattr(item, field) for field in PRICE_FIELDS)
    if not changed:
        return
    db.add(
        CostItemHistory(
            cost_item_id=item.id,
            old_price=old_values["price"],
            new_price=item.price,
            old_client_tax_excluded_price=old_values["client_tax_excluded_price"],
            new_client_tax_excluded_price=item.client_tax_excluded_price,
            old_client_labor_price=old_values["client_labor_price"],
            new_client_labor_price=item.client_labor_price,
            old_client_main_material_price=old_values["client_main_material_price"],
            new_client_main_material_price=item.client_main_material_price,
            old_client_auxiliary_material_price=old_values["client_auxiliary_material_price"],
            new_client_auxiliary_material_price=item.client_auxiliary_material_price,
            old_client_direct_fee=old_values["client_direct_fee"],
            new_client_direct_fee=item.client_direct_fee,
            old_client_management_profit=old_values["client_management_profit"],
            new_client_management_profit=item.client_management_profit,
            old_subcontract_composite_price=old_values["subcontract_composite_price"],
            new_subcontract_composite_price=item.subcontract_composite_price,
            old_subcontract_labor_price=old_values["subcontract_labor_price"],
            new_subcontract_labor_price=item.subcontract_labor_price,
            old_subcontract_main_material_price=old_values["subcontract_main_material_price"],
            new_subcontract_main_material_price=item.subcontract_main_material_price,
            old_subcontract_auxiliary_material_price=old_values["subcontract_auxiliary_material_price"],
            new_subcontract_auxiliary_material_price=item.subcontract_auxiliary_material_price,
            old_crew_benchmark_price=old_values["crew_benchmark_price"],
            new_crew_benchmark_price=item.crew_benchmark_price,
            change_type=CHANGE_TYPE_PRICE,
            changed_by=user.id,
            change_reason=clean_text(reason, 2000) or "price updated",
        )
    )


def _write_status_history(db: Session, item: CostItem, user: User, old_status: str | None, reason: str | None) -> None:
    if old_status == item.status:
        return
    db.add(
        CostItemHistory(
            cost_item_id=item.id,
            old_status=old_status,
            new_status=item.status,
            change_type=CHANGE_TYPE_STATUS,
            changed_by=user.id,
            change_reason=clean_text(reason, 2000),
        )
    )


def _get_item(db: Session, item_id: int) -> CostItem:
    item = db.query(CostItem).filter(CostItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RESOURCE_NOT_FOUND")
    return item


def create_cost_item(db: Session, user: User, payload: Mapping[str, Any] | Any) -> CostItem:
    require_cost_db_manager(user)
    data = _normalize_item_payload(_payload_dict(payload))
    item = CostItem(**data, status=COST_STATUS_DRAFT, created_by=user.id)
    db.add(item)
    db.flush()
    return item


def list_cost_items(
    db: Session,
    user: User,
    *,
    category: str | None = None,
    subcategory: str | None = None,
    statuses: list[str] | None = None,
    price_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CostItem], int]:
    require_cost_db_access(user)
    query = db.query(CostItem)
    category_text = clean_text(category, 128)
    if category_text:
        pattern = f"%{category_text}%"
        query = query.filter(or_(CostItem.category.like(pattern), CostItem.subcategory.like(pattern)))
    subcategory_text = clean_text(subcategory, 128)
    if subcategory_text:
        query = query.filter(CostItem.subcategory.like(f"%{subcategory_text}%"))
    if statuses:
        invalid = [status_value for status_value in statuses if status_value not in COST_STATUS_VALUES]
        if invalid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_STATUS")
        query = query.filter(CostItem.status.in_(statuses))
    if price_type:
        query = query.filter(CostItem.price_type == normalize_price_type(price_type))
    keyword_text = clean_text(keyword, 128)
    if keyword_text:
        pattern = f"%{keyword_text}%"
        query = query.filter(
            or_(
                CostItem.item_name.like(pattern),
                CostItem.spec.like(pattern),
                CostItem.category.like(pattern),
                CostItem.subcategory.like(pattern),
                CostItem.notes.like(pattern),
            )
        )
    total = query.count()
    items = query.order_by(CostItem.updated_at.desc(), CostItem.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def get_cost_item(db: Session, user: User, item_id: int) -> CostItem:
    require_cost_db_access(user)
    return _get_item(db, item_id)


def update_cost_item(db: Session, user: User, item_id: int, payload: Mapping[str, Any] | Any) -> CostItem:
    require_cost_db_manager(user)
    item = _get_item(db, item_id)
    if item.status == COST_STATUS_ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STATE_CONFLICT")

    data = _payload_dict(payload)
    reason = data.pop("change_reason", None)
    unexpected = set(data) - UPDATE_FIELDS
    if unexpected:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNSUPPORTED_FIELDS")

    old_prices = {field: getattr(item, field) for field in PRICE_FIELDS}
    candidate = serialize_cost_item(item)
    candidate.update(data)
    if "price" not in data and any(field in data for field in PRICE_FIELDS if field != "price"):
        candidate["price"] = None
    normalized = _normalize_item_payload(candidate)
    for field, value in normalized.items():
        setattr(item, field, value)
    db.flush()
    _write_price_history(db, item, user, old_prices, reason)
    return item


def activate_cost_item(db: Session, user: User, item_id: int) -> CostItem:
    require_cost_db_manager(user)
    item = _get_item(db, item_id)
    if item.status == COST_STATUS_ACTIVE:
        return item
    if item.status == COST_STATUS_ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="STATE_CONFLICT")
    old_status = item.status
    item.status = COST_STATUS_ACTIVE
    db.flush()
    _write_status_history(db, item, user, old_status, "activated")
    return item


def archive_cost_item(db: Session, user: User, item_id: int, reason: str | None = None) -> CostItem:
    require_cost_db_manager(user)
    item = _get_item(db, item_id)
    if item.status == COST_STATUS_ARCHIVED:
        return item
    cleaned_reason = clean_text(reason, 2000)
    if item.status == COST_STATUS_ACTIVE and not cleaned_reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REASON_REQUIRED")
    old_status = item.status
    item.status = COST_STATUS_ARCHIVED
    db.flush()
    _write_status_history(db, item, user, old_status, cleaned_reason or "archived draft")
    return item


def _duplicate_key(data: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(data.get("category") or ""),
        str(data.get("subcategory") or ""),
        str(data.get("item_name") or ""),
        str(data.get("spec") or ""),
        str(data.get("unit") or ""),
    )


def _existing_by_key(db: Session, data: Mapping[str, Any]) -> CostItem | None:
    return (
        db.query(CostItem)
        .filter(
            CostItem.category == data.get("category"),
            CostItem.subcategory == data.get("subcategory"),
            CostItem.item_name == data.get("item_name"),
            CostItem.spec == data.get("spec"),
            CostItem.unit == data.get("unit"),
        )
        .order_by(CostItem.id.desc())
        .first()
    )


def _make_notes(code: str | None, calc_rule: Any, work_content: Any, client_note: Any, labor_note: Any) -> str | None:
    parts = []
    if code:
        parts.append(f"编号: {code}")
    for label, value in (
        ("计算规则", calc_rule),
        ("工作内容", work_content),
        ("对甲备注", client_note),
        ("劳务备注", labor_note),
    ):
        text = clean_text(value)
        if text:
            parts.append(f"{label}: {text}")
    return "\n".join(parts) if parts else None


def parse_cost_workbook(content: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_EXCEL") from exc

    sheet = workbook.worksheets[0]
    current_category: str | None = None
    items: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []

    for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        values = list(row) + [None] * 19
        code = clean_text(values[0], 64)
        item_name = clean_text(values[1], 255)
        if code and not item_name and "章" in code:
            current_category = code
            continue
        if not code or code in {"编号", "序号"} or not item_name:
            continue

        unit = clean_text(values[5], 64)
        client_labor_price = parse_float(values[6])
        client_main_material_price = parse_float(values[7])
        client_auxiliary_material_price = parse_float(values[8])
        client_direct_fee = parse_float(values[9])
        client_management_profit = parse_float(values[10])
        client_tax_excluded_price = parse_float(values[11])
        subcontract_labor_price = parse_float(values[13])
        subcontract_main_material_price = parse_float(values[14])
        subcontract_auxiliary_material_price = parse_float(values[15])
        subcontract_composite_price = parse_float(values[16])
        crew_benchmark_price = parse_float(values[17])
        if not unit or not any(value is not None for value in (client_tax_excluded_price, subcontract_composite_price, crew_benchmark_price)):
            skipped_rows.append({"row": row_number, "reason": "missing_unit_or_price", "code": code, "item_name": item_name})
            continue

        raw_item = {
            "category": current_category or sheet.title,
            "subcategory": None,
            "item_name": item_name,
            "spec": clean_text(values[2]),
            "unit": unit,
            "client_tax_excluded_price": client_tax_excluded_price,
            "client_labor_price": client_labor_price,
            "client_main_material_price": client_main_material_price,
            "client_auxiliary_material_price": client_auxiliary_material_price,
            "client_direct_fee": client_direct_fee,
            "client_management_profit": client_management_profit,
            "subcontract_composite_price": subcontract_composite_price,
            "subcontract_labor_price": subcontract_labor_price,
            "subcontract_main_material_price": subcontract_main_material_price,
            "subcontract_auxiliary_material_price": subcontract_auxiliary_material_price,
            "crew_benchmark_price": crew_benchmark_price,
            "price_type": PRICE_TYPE_COMBINED,
            "source": COST_SOURCE_IMPORTED,
            "effective_date": None,
            "notes": _make_notes(code, values[3], values[4], values[12], values[18]),
            "source_row": row_number,
            "source_sheet": sheet.title,
        }
        try:
            normalized = _normalize_item_payload(raw_item, for_import=True)
        except HTTPException as exc:
            skipped_rows.append({"row": row_number, "reason": str(exc.detail), "code": code, "item_name": item_name})
            continue
        normalized["source_row"] = row_number
        normalized["source_sheet"] = sheet.title
        items.append(normalized)

    return items, skipped_rows


def build_import_preview(db: Session, user: User, content: bytes) -> ImportBatch:
    require_cost_db_manager(user)
    _cleanup_import_batches()
    items, skipped_rows = parse_cost_workbook(content)
    duplicate_warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for index, item in enumerate(items):
        key = _duplicate_key(item)
        if key in seen:
            duplicate_warnings.append({"index": index, "type": "within_file", "item_name": item["item_name"], "spec": item.get("spec")})
            continue
        seen.add(key)
        existing = _existing_by_key(db, item)
        if existing:
            duplicate_warnings.append(
                {
                    "index": index,
                    "type": f"existing_{existing.status}",
                    "cost_item_id": existing.id,
                    "item_name": item["item_name"],
                    "spec": item.get("spec"),
                }
            )
    batch_id = str(uuid4())
    batch = ImportBatch(
        batch_id=batch_id,
        created_at=datetime.now(timezone.utc),
        items=items,
        duplicate_warnings=duplicate_warnings,
        skipped_rows=skipped_rows,
    )
    IMPORT_BATCHES[batch_id] = batch
    return batch


def _cleanup_import_batches() -> None:
    now = datetime.now(timezone.utc)
    expired = [batch_id for batch_id, batch in IMPORT_BATCHES.items() if now - batch.created_at > IMPORT_BATCH_TTL]
    for batch_id in expired:
        IMPORT_BATCHES.pop(batch_id, None)


def _get_import_batch(batch_id: str) -> ImportBatch:
    _cleanup_import_batches()
    batch = IMPORT_BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="BATCH_EXPIRED")
    return batch


def import_preview_response(batch: ImportBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.batch_id,
        "expires_in_seconds": int(IMPORT_BATCH_TTL.total_seconds()),
        "items": batch.items,
        "duplicate_warnings": batch.duplicate_warnings,
        "skipped_rows": batch.skipped_rows,
        "item_count": len(batch.items),
    }


def confirm_import_batch(db: Session, user: User, batch_id: str) -> dict[str, Any]:
    require_cost_db_manager(user)
    batch = _get_import_batch(batch_id)
    if batch.confirmed and batch.result is not None:
        return batch.result

    created: list[int] = []
    updated: list[int] = []
    skipped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item_data in batch.items:
        key = _duplicate_key(item_data)
        if key in seen:
            skipped.append({"item_name": item_data["item_name"], "reason": "duplicate_within_file"})
            continue
        seen.add(key)
        existing = _existing_by_key(db, item_data)
        clean_data = {key_name: value for key_name, value in item_data.items() if key_name in UPDATE_FIELDS}
        if existing and existing.status == COST_STATUS_ACTIVE:
            skipped.append({"cost_item_id": existing.id, "item_name": item_data["item_name"], "reason": "active_duplicate"})
            continue
        if existing and existing.status == COST_STATUS_DRAFT:
            old_prices = {field: getattr(existing, field) for field in PRICE_FIELDS}
            for field, value in clean_data.items():
                setattr(existing, field, value)
            existing.source = COST_SOURCE_IMPORTED
            db.flush()
            _write_price_history(db, existing, user, old_prices, "import draft overwrite")
            updated.append(existing.id)
            continue
        new_item = CostItem(**clean_data, status=COST_STATUS_DRAFT, created_by=user.id)
        db.add(new_item)
        db.flush()
        created.append(new_item.id)

    batch.confirmed = True
    batch.result = {
        "batch_id": batch.batch_id,
        "created_count": len(created),
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "created_ids": created,
        "updated_ids": updated,
        "skipped": skipped,
    }
    return batch.result
