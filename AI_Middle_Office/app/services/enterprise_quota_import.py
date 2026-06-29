from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import user as user_model  # noqa: F401
from app.models.enterprise_quota import (
    IMPORT_BATCH_STATUS_IMPORTED,
    QUOTA_VERSION_STATUS_DRAFT,
    RESOURCE_TYPE_AUXILIARY_MATERIAL,
    RESOURCE_TYPE_LABOR,
    RESOURCE_TYPE_MACHINERY,
    RESOURCE_TYPE_MAIN_MATERIAL,
    RESOURCE_TYPE_UNKNOWN,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.services.enterprise_quota_phase0 import PHASE0_VERSION, preview_enterprise_quota_file
from app.services.enterprise_quota_units import normalize_enterprise_quota_unit


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class EnterpriseQuotaImportError(ValueError):
    pass


def save_enterprise_quota_draft_from_file(
    db: Session,
    file_path: str | Path,
    *,
    version_code: str | None = None,
    version_name: str | None = None,
    created_by: int | None = None,
    batch_uuid: str | None = None,
) -> dict[str, Any]:
    """Parse a quota workbook and write it as a draft master version.

    The caller owns transaction boundaries. This function flushes rows so IDs
    are available in the returned summary, but it does not commit.
    """

    preview = preview_enterprise_quota_file(file_path)
    return save_enterprise_quota_draft_from_preview(
        db,
        preview,
        version_code=version_code,
        version_name=version_name,
        created_by=created_by,
        batch_uuid=batch_uuid,
    )


def save_enterprise_quota_draft_from_preview(
    db: Session,
    preview_result: dict[str, Any],
    *,
    version_code: str | None = None,
    version_name: str | None = None,
    created_by: int | None = None,
    batch_uuid: str | None = None,
) -> dict[str, Any]:
    if not isinstance(preview_result, dict):
        raise EnterpriseQuotaImportError("Phase 0 preview result must be a dict.")
    if not preview_result.get("ok"):
        summary = preview_result.get("summary") if isinstance(preview_result.get("summary"), dict) else {}
        raise EnterpriseQuotaImportError(
            f"Phase 0 preview is not importable: error_count={summary.get('error_count', 0)}"
        )

    source = preview_result.get("source") if isinstance(preview_result.get("source"), dict) else {}
    source_filename = _bounded_text(source.get("file_name"), 255) or "enterprise_quota_workbook"
    source_sha256 = _clean(source.get("sha256")) or _preview_fingerprint(preview_result)
    source_file_size = _to_int(source.get("file_size"))
    parser_version = _clean(preview_result.get("version")) or PHASE0_VERSION

    resolved_version_code = _normalize_version_code(
        version_code or _default_version_code(source_filename, source_sha256)
    )
    resolved_version_name = _bounded_text(version_name, 255) or _bounded_text(_default_version_name(source_filename), 255)
    resolved_batch_uuid = _clean(batch_uuid) or str(uuid4())

    _ensure_version_code_available(db, resolved_version_code)
    _ensure_batch_uuid_available(db, resolved_batch_uuid)

    batch = CostImportBatch(
        batch_uuid=resolved_batch_uuid,
        source_filename=source_filename,
        source_file_sha256=source_sha256[:64],
        source_file_size=source_file_size,
        parser_version=parser_version[:64],
        status=IMPORT_BATCH_STATUS_IMPORTED,
        summary_json=_json_dumps(preview_result.get("summary") or {}),
        issues_json=_json_dumps(preview_result.get("issues") or []),
        error_count=_to_int((preview_result.get("summary") or {}).get("error_count")) or 0,
        warning_count=_to_int((preview_result.get("summary") or {}).get("warning_count")) or 0,
        created_by=created_by,
    )
    version = EnterpriseQuotaVersion(
        version_code=resolved_version_code,
        version_name=resolved_version_name,
        source_filename=source_filename,
        source_file_sha256=source_sha256[:64],
        status=QUOTA_VERSION_STATUS_DRAFT,
        is_active=False,
        summary_json=_json_dumps(preview_result.get("summary") or {}),
        notes="Imported from Phase 0 preview as draft. Activation is a later phase.",
        created_by=created_by,
    )
    batch.versions.append(version)
    db.add(batch)
    db.flush()

    enterprise = preview_result.get("enterprise_quota") if isinstance(preview_result.get("enterprise_quota"), dict) else {}
    section_by_code: dict[str, EnterpriseQuotaSection] = {}
    section_by_name: dict[str, EnterpriseQuotaSection] = {}
    item_by_code: dict[str, EnterpriseQuotaItem] = {}
    resource_by_key: dict[tuple[Any, ...], EnterpriseCostResource] = {}

    sections = _list(enterprise.get("sections"))
    for sort_order, row in enumerate(sections, start=1):
        section = EnterpriseQuotaSection(
            section_code=_none_if_blank(row.get("section_code"), 64),
            section_name=_none_if_blank(row.get("section_name"), 255),
            source_sheet=_none_if_blank(row.get("source_sheet"), 128),
            source_row_index=_to_int(row.get("row_index")),
            sort_order=sort_order,
            raw_row_json=_json_dumps(row.get("raw_row") or row),
        )
        version.sections.append(section)
        code = _clean(row.get("section_code"))
        name = _clean(row.get("section_name"))
        if code and code not in section_by_code:
            section_by_code[code] = section
        if name and name not in section_by_name:
            section_by_name[name] = section

    items = _list(enterprise.get("items"))
    for sort_order, row in enumerate(items, start=1):
        section = section_by_code.get(_clean(row.get("section_code"))) or section_by_name.get(
            _clean(row.get("section_name"))
        )
        item = EnterpriseQuotaItem(
            section=section,
            quota_code=_none_if_blank(row.get("quota_code"), 64),
            item_name=_none_if_blank(row.get("item_name"), 255),
            work_content=_none_if_blank(row.get("work_content")),
            worker_or_subtype=_none_if_blank(row.get("worker_or_subtype"), 128),
            unit=_normalized_unit_or_none(row.get("unit"), 64),
            quantity=_to_float(row.get("quantity")),
            unit_price=_to_float(row.get("unit_price")),
            labor_fee=_to_float(row.get("labor_fee")),
            main_material_fee=_to_float(row.get("main_material_fee")),
            auxiliary_material_fee=_to_float(row.get("auxiliary_material_fee")),
            machinery_fee=_to_float(row.get("machinery_fee")),
            source_sheet=_none_if_blank(row.get("source_sheet"), 128),
            source_row_index=_to_int(row.get("row_index")),
            sort_order=sort_order,
            raw_row_json=_json_dumps(row.get("raw_row") or row),
        )
        version.items.append(item)
        code = _clean(row.get("quota_code"))
        if code and code not in item_by_code:
            item_by_code[code] = item

    resource_sort_order = 0
    component_resource_rows: list[tuple[dict[str, Any], EnterpriseCostResource | None]] = []
    components = _list(enterprise.get("components"))
    for row in components:
        resource_type = _resource_type_from_component(row.get("component_type"))
        resource = None
        if _clean(row.get("resource_code")) or _clean(row.get("resource_name")):
            resource_sort_order += 1
            resource = _get_or_create_resource(
                version,
                resource_by_key,
                resource_type=resource_type,
                resource_code=row.get("resource_code"),
                resource_name=row.get("resource_name"),
                unit=row.get("unit"),
                price=row.get("unit_price"),
                tax_rate=None,
                computed_price=None,
                price_block_label="component",
                source_sheet=row.get("source_sheet"),
                source_row_index=row.get("row_index"),
                sort_order=resource_sort_order,
                raw_row=row.get("raw_row") or row,
            )
        component_resource_rows.append((row, resource))

    labor = preview_result.get("labor_guide") if isinstance(preview_result.get("labor_guide"), dict) else {}
    for row in _list(labor.get("candidates")):
        if not (_clean(row.get("quota_code")) or _clean(row.get("item_name")) or _clean(row.get("work_content"))):
            continue
        resource_sort_order += 1
        _get_or_create_resource(
            version,
            resource_by_key,
            resource_type=RESOURCE_TYPE_LABOR,
            resource_code=row.get("quota_code"),
            resource_name=row.get("item_name") or row.get("work_content"),
            unit=row.get("unit"),
            price=row.get("guide_price"),
            tax_rate=None,
            computed_price=None,
            price_block_label="labor_guide",
            source_sheet=row.get("source_sheet"),
            source_row_index=row.get("row_index"),
            sort_order=resource_sort_order,
            raw_row=row.get("raw_row") or row,
        )

    material = (
        preview_result.get("material_price_library")
        if isinstance(preview_result.get("material_price_library"), dict)
        else {}
    )
    for row in _list(material.get("candidates")):
        price_blocks = _list(row.get("price_blocks"))
        if not price_blocks:
            resource_sort_order += 1
            _get_or_create_resource(
                version,
                resource_by_key,
                resource_type=RESOURCE_TYPE_MAIN_MATERIAL,
                resource_code=row.get("resource_code"),
                resource_name=row.get("resource_name"),
                unit=None,
                price=None,
                tax_rate=None,
                computed_price=None,
                price_block_label="material_candidate",
                source_sheet=row.get("source_sheet"),
                source_row_index=row.get("row_index"),
                sort_order=resource_sort_order,
                raw_row=row.get("raw_row") or row,
            )
            continue
        for block_index, block in enumerate(price_blocks, start=1):
            resource_sort_order += 1
            block_label = _clean(block.get("block")) or "material_price_block"
            _get_or_create_resource(
                version,
                resource_by_key,
                resource_type=RESOURCE_TYPE_MAIN_MATERIAL,
                resource_code=row.get("resource_code"),
                resource_name=row.get("resource_name"),
                unit=block.get("unit"),
                price=block.get("price"),
                tax_rate=block.get("tax_rate"),
                computed_price=block.get("computed_price"),
                price_block_label=f"{block_label}#{block_index}",
                source_sheet=row.get("source_sheet"),
                source_row_index=row.get("row_index"),
                sort_order=resource_sort_order,
                raw_row={"candidate": row.get("raw_row") or row, "price_block": block},
            )

    for sort_order, (row, resource) in enumerate(component_resource_rows, start=1):
        component_type = _none_if_blank(row.get("component_type"), 64)
        component = EnterpriseQuotaComponent(
            quota_item=item_by_code.get(_clean(row.get("parent_quota_code"))),
            resource=resource,
            parent_quota_code=_none_if_blank(row.get("parent_quota_code"), 64),
            component_type=component_type,
            resource_code=_none_if_blank(row.get("resource_code"), 64),
            resource_name=_none_if_blank(row.get("resource_name"), 255),
            worker_or_subtype=_none_if_blank(row.get("worker_or_subtype"), 128),
            unit=_normalized_unit_or_none(row.get("unit"), 64),
            quantity=_to_float(row.get("quantity")),
            unit_price=_to_float(row.get("unit_price")),
            amount=_to_float(row.get("amount")),
            fee_bucket=_resource_type_from_component(component_type),
            source_sheet=_none_if_blank(row.get("source_sheet"), 128),
            source_row_index=_to_int(row.get("row_index")),
            sort_order=sort_order,
            raw_row_json=_json_dumps(row.get("raw_row") or row),
        )
        version.components.append(component)

    db.flush()

    counts = {
        "section_count": len(version.sections),
        "item_count": len(version.items),
        "component_count": len(version.components),
        "resource_count": len(version.resources),
    }
    version.summary_json = _json_dumps(
        {
            **(preview_result.get("summary") or {}),
            "phase2_import": counts,
            "phase2_status": "draft_saved",
        }
    )
    batch.summary_json = version.summary_json
    db.flush()

    return {
        "ok": True,
        "status": QUOTA_VERSION_STATUS_DRAFT,
        "import_batch_id": batch.id,
        "batch_uuid": batch.batch_uuid,
        "quota_version_id": version.id,
        "version_code": version.version_code,
        "version_name": version.version_name,
        "is_active": bool(version.is_active),
        **counts,
        "source": {
            "file_name": source_filename,
            "sha256": source_sha256,
            "file_size": source_file_size,
            "parser_version": parser_version,
        },
    }


def _ensure_version_code_available(db: Session, version_code: str) -> None:
    existing = db.query(EnterpriseQuotaVersion.id).filter(EnterpriseQuotaVersion.version_code == version_code).first()
    if existing:
        raise EnterpriseQuotaImportError(f"Version code already exists: {version_code}")


def _ensure_batch_uuid_available(db: Session, batch_uuid: str) -> None:
    existing = db.query(CostImportBatch.id).filter(CostImportBatch.batch_uuid == batch_uuid).first()
    if existing:
        raise EnterpriseQuotaImportError(f"Import batch uuid already exists: {batch_uuid}")


def _get_or_create_resource(
    version: EnterpriseQuotaVersion,
    resource_by_key: dict[tuple[Any, ...], EnterpriseCostResource],
    *,
    resource_type: str,
    resource_code: Any,
    resource_name: Any,
    unit: Any,
    price: Any,
    tax_rate: Any,
    computed_price: Any,
    price_block_label: Any,
    source_sheet: Any,
    source_row_index: Any,
    sort_order: int,
    raw_row: Any,
) -> EnterpriseCostResource:
    key = (
        resource_type,
        _clean(resource_code),
        _clean(resource_name),
        _clean(normalize_enterprise_quota_unit(unit)),
        _round_key(price),
        _round_key(tax_rate),
        _round_key(computed_price),
        _clean(price_block_label),
    )
    existing = resource_by_key.get(key)
    if existing is not None:
        return existing
    resource = EnterpriseCostResource(
        resource_type=resource_type,
        resource_code=_none_if_blank(resource_code, 64),
        resource_name=_none_if_blank(resource_name, 255),
        unit=_normalized_unit_or_none(unit, 64),
        price=_to_float(price),
        tax_rate=_to_float(tax_rate),
        computed_price=_to_float(computed_price),
        price_block_label=_none_if_blank(price_block_label, 64),
        source_sheet=_none_if_blank(source_sheet, 128),
        source_row_index=_to_int(source_row_index),
        sort_order=sort_order,
        raw_row_json=_json_dumps(raw_row),
    )
    version.resources.append(resource)
    resource_by_key[key] = resource
    return resource


def _default_version_code(source_filename: str, source_sha256: str) -> str:
    stem = Path(source_filename).stem
    date_match = re.search(r"(20\d{6})", stem)
    date_part = date_match.group(1) if date_match else "undated"
    hash_part = (source_sha256 or _preview_fingerprint({"file_name": source_filename}))[:8]
    slug = _slug(stem)[:28] or "enterprise-quota"
    return f"{slug}-{date_part}-{hash_part}"[:64]


def _default_version_name(source_filename: str) -> str:
    stem = Path(source_filename).stem
    return stem or "Enterprise quota draft"


def _normalize_version_code(value: str) -> str:
    code = _slug(value)
    if not code:
        raise EnterpriseQuotaImportError("Version code is required.")
    return code[:64]


def _slug(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def _resource_type_from_component(component_type: Any) -> str:
    text = _clean(component_type).lower()
    if "rg" in text or "labor" in text or "人工" in text:
        return RESOURCE_TYPE_LABOR
    if "ca" in text or "main" in text or "主材" in text:
        return RESOURCE_TYPE_MAIN_MATERIAL
    if "cb" in text or "aux" in text or "辅材" in text:
        return RESOURCE_TYPE_AUXILIARY_MATERIAL
    if "mach" in text or "jx" in text or "机械" in text:
        return RESOURCE_TYPE_MACHINERY
    return RESOURCE_TYPE_UNKNOWN


def _preview_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return _CONTROL_CHARS_RE.sub("", text)


def _bounded_text(value: Any, max_length: int | None = None) -> str:
    text = _clean(value)
    if max_length is not None and len(text) > max_length:
        return text[:max_length]
    return text


def _none_if_blank(value: Any, max_length: int | None = None) -> str | None:
    text = _bounded_text(value, max_length)
    return text or None


def _normalized_unit_or_none(value: Any, max_length: int | None = None) -> str | None:
    return _none_if_blank(normalize_enterprise_quota_unit(value), max_length)


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _round_key(value: Any) -> float | None:
    number = _to_float(value)
    return round(number, 8) if number is not None else None
