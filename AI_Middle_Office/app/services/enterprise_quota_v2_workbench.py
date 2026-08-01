from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.enterprise_quota import (
    IMPORT_BATCH_STATUS_ACTIVATED,
    IMPORT_BATCH_STATUS_IMPORTED,
    QUOTA_VERSION_STATUS_ACTIVE,
    QUOTA_VERSION_STATUS_ARCHIVED,
    QUOTA_VERSION_STATUS_DRAFT,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaSheetRow,
    EnterpriseQuotaVersion,
    EnterpriseQuotaVersionEvent,
)
from app.models.quote_job import QuoteJob
from app.services.enterprise_quota_units import normalize_enterprise_quota_unit
from app.services.enterprise_quota_v2_parser import (
    ENTERPRISE_HEADERS,
    ENTERPRISE_QUOTA_V2_SCHEMA,
    ENTERPRISE_SHEET,
    LABOR_HEADERS,
    LABOR_SHEET,
    MATERIAL_HEADERS,
    MATERIAL_SHEET,
    VALIDATION_HEADERS,
    VALIDATION_SHEET,
    compact_json,
    parse_enterprise_quota_v2_bytes,
)


ACTIVE_QUOTE_JOB_STATUSES = {"queued", "running"}
WORKBENCH_SHEET_KEYS = {
    "enterprise": ENTERPRISE_SHEET,
    "labor": LABOR_SHEET,
    "material": MATERIAL_SHEET,
    "validation": VALIDATION_SHEET,
}
HEADERS_BY_SHEET = {
    ENTERPRISE_SHEET: ENTERPRISE_HEADERS,
    LABOR_SHEET: LABOR_HEADERS,
    MATERIAL_SHEET: MATERIAL_HEADERS,
    VALIDATION_SHEET: VALIDATION_HEADERS,
}
RESOURCE_UPDATE_FIELDS = {
    "category",
    "resource_code",
    "resource_type",
    "resource_name",
    "work_content",
    "calculation_rule",
    "specification",
    "brand",
    "unit",
    "default_quantity",
    "price",
}
RESOURCE_TYPE_FROM_LABEL = {
    "人工": "labor",
    "主材": "main_material",
    "辅材": "auxiliary_material",
    "机械": "machinery",
    "labor": "labor",
    "main_material": "main_material",
    "auxiliary_material": "auxiliary_material",
    "machinery": "machinery",
}
RESOURCE_TYPE_LABEL = {
    "labor": "人工",
    "main_material": "主材",
    "auxiliary_material": "辅材",
    "machinery": "机械",
    "unknown": "未知",
}
FEE_FIELD_BY_BUCKET = {
    "labor": "labor_fee",
    "main_material": "main_material_fee",
    "auxiliary_material": "auxiliary_material_fee",
    "machinery": "machinery_fee",
}
_VERSION_CODE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class EnterpriseQuotaV2WorkbenchError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def preview_v2_workbook(content: bytes, *, filename: str) -> dict[str, Any]:
    parsed = parse_enterprise_quota_v2_bytes(content, filename=filename)
    return {
        "source": parsed["source"],
        "workbook_title": parsed["workbook_title"],
        "summary": parsed["summary"],
        "quality": parsed["quality"],
        "sheet_contracts": {
            "enterprise": list(ENTERPRISE_HEADERS),
            "labor": list(LABOR_HEADERS),
            "material": list(MATERIAL_HEADERS),
            "validation": list(VALIDATION_HEADERS),
        },
    }


def import_v2_workbook_as_draft(
    db: Session,
    content: bytes,
    *,
    filename: str,
    actor_id: int | None,
    version_code: str | None = None,
    version_name: str | None = None,
) -> EnterpriseQuotaVersion:
    parsed = parse_enterprise_quota_v2_bytes(content, filename=filename)
    source = parsed["source"]
    resolved_code = _unique_version_code(
        db,
        version_code or _default_version_code(filename, source["sha256"]),
    )
    resolved_name = (version_name or parsed.get("workbook_title") or f"企业定额 2.0 - {filename}").strip()[:255]

    batch = CostImportBatch(
        batch_uuid=_batch_uuid(source["sha256"]),
        source_filename=filename[:255],
        source_file_sha256=source["sha256"],
        source_file_size=source["file_size"],
        parser_version=ENTERPRISE_QUOTA_V2_SCHEMA,
        status=IMPORT_BATCH_STATUS_IMPORTED,
        summary_json=compact_json(parsed["summary"]),
        issues_json=compact_json(parsed["quality"]["issues"]),
        error_count=int(parsed["summary"]["error_count"]),
        warning_count=int(parsed["summary"]["warning_count"]),
        created_by=actor_id,
    )
    version = EnterpriseQuotaVersion(
        version_code=resolved_code,
        version_name=resolved_name,
        source_filename=filename[:255],
        source_file_sha256=source["sha256"],
        schema_version=ENTERPRISE_QUOTA_V2_SCHEMA,
        workbook_title=(parsed.get("workbook_title") or "")[:255] or None,
        workbook_metadata_json=compact_json(parsed["workbook_metadata"]),
        quality_status=parsed["quality"]["status"],
        quality_summary_json=compact_json(parsed["quality"]),
        formula_count=int(parsed["summary"]["formula_count"]),
        revision=1,
        status=QUOTA_VERSION_STATUS_DRAFT,
        is_active=False,
        summary_json=compact_json(parsed["summary"]),
        notes="由企业定额 2.0 工作簿导入；所有公式、层级和四张工作表行数据均已保留。",
        created_by=actor_id,
    )
    batch.versions.append(version)
    db.add(batch)
    db.flush()

    section_by_key: dict[str, EnterpriseQuotaSection] = {}
    for source_row in parsed["sections"]:
        parent = section_by_key.get(source_row.get("parent_key"))
        section = EnterpriseQuotaSection(
            version_id=version.id,
            parent=parent,
            section_code=source_row["section_code"],
            section_name=source_row["section_name"],
            level=int(source_row["level"]),
            outline_level=int(source_row.get("outline_level") or 0),
            source_sheet=source_row["source_sheet"],
            source_row_index=source_row["source_row_index"],
            sort_order=source_row["sort_order"],
            raw_row_json=compact_json(
                {"values": source_row["raw_values"], "source_key": source_row["key"]}
            ),
        )
        db.add(section)
        section_by_key[source_row["key"]] = section
    db.flush()

    resource_by_key: dict[str, EnterpriseCostResource] = {}
    for sort_order, source_row in enumerate(parsed["resources"], start=1):
        resource = EnterpriseCostResource(
            version_id=version.id,
            library_kind=source_row["library_kind"],
            category=source_row.get("category"),
            resource_code=source_row.get("resource_code"),
            resource_type=source_row["resource_type"],
            resource_name=source_row.get("resource_name"),
            work_content=source_row.get("work_content"),
            calculation_rule=source_row.get("calculation_rule"),
            specification=source_row.get("specification"),
            brand=source_row.get("brand"),
            unit=source_row.get("unit"),
            default_quantity=source_row.get("default_quantity"),
            price=source_row.get("price"),
            computed_price=source_row.get("price"),
            source_sheet=source_row["source_sheet"],
            source_row_index=source_row["source_row_index"],
            sort_order=sort_order,
            formulas_json=compact_json(source_row.get("formulas") or {}),
            raw_row_json=compact_json(
                {
                    "values": source_row["raw_values"],
                    "source_key": source_row["key"],
                }
            ),
        )
        db.add(resource)
        resource_by_key[source_row["key"]] = resource
    db.flush()

    item_by_key: dict[str, EnterpriseQuotaItem] = {}
    for source_row in parsed["items"]:
        item = EnterpriseQuotaItem(
            version_id=version.id,
            section=section_by_key.get(source_row.get("section_key")),
            quota_code=source_row.get("quota_code"),
            row_type=source_row.get("row_type"),
            item_name=source_row.get("item_name"),
            work_content=source_row.get("work_content"),
            specification=source_row.get("specification"),
            brand=source_row.get("brand"),
            unit=source_row.get("unit"),
            quantity=source_row.get("quantity"),
            unit_price=source_row.get("unit_price"),
            labor_fee=source_row.get("labor_fee"),
            main_material_fee=source_row.get("main_material_fee"),
            auxiliary_material_fee=source_row.get("auxiliary_material_fee"),
            machinery_fee=source_row.get("machinery_fee"),
            outline_level=int(source_row.get("outline_level") or 0),
            source_sheet=source_row["source_sheet"],
            source_row_index=source_row["source_row_index"],
            sort_order=source_row["sort_order"],
            formulas_json=compact_json(source_row.get("formulas") or {}),
            raw_row_json=compact_json(
                {
                    "values": source_row["raw_values"],
                    "source_key": source_row["key"],
                }
            ),
        )
        db.add(item)
        item_by_key[source_row["key"]] = item
    db.flush()

    component_by_key: dict[str, EnterpriseQuotaComponent] = {}
    for source_row in parsed["components"]:
        component = EnterpriseQuotaComponent(
            version_id=version.id,
            quota_item=item_by_key.get(source_row.get("item_key")),
            resource=resource_by_key.get(source_row.get("resource_key")),
            parent_quota_code=source_row.get("parent_quota_code"),
            component_type=source_row.get("component_type"),
            resource_code=source_row.get("resource_code"),
            resource_name=source_row.get("resource_name"),
            work_content=source_row.get("work_content"),
            specification=source_row.get("specification"),
            brand=source_row.get("brand"),
            unit=source_row.get("unit"),
            quantity=source_row.get("quantity"),
            unit_price=source_row.get("unit_price"),
            amount=source_row.get("amount"),
            fee_bucket=source_row.get("fee_bucket"),
            outline_level=int(source_row.get("outline_level") or 1),
            formulas_json=compact_json(source_row.get("formulas") or {}),
            formula_library_kind=source_row.get("formula_library_kind"),
            formula_link_status=source_row.get("formula_link_status"),
            source_sheet=source_row["source_sheet"],
            source_row_index=source_row["source_row_index"],
            sort_order=source_row["sort_order"],
            raw_row_json=compact_json(
                {
                    "values": source_row["raw_values"],
                    "source_key": source_row["key"],
                }
            ),
        )
        db.add(component)
        component_by_key[source_row["key"]] = component
    db.flush()

    entity_maps: dict[str, dict[str, Any]] = {
        "section": section_by_key,
        "quota_item": item_by_key,
        "component": component_by_key,
        "resource": resource_by_key,
    }
    for source_row in parsed["workbook_rows"]:
        entity = entity_maps.get(source_row.get("entity_type") or "", {}).get(
            source_row.get("entity_key")
        )
        db.add(
            EnterpriseQuotaSheetRow(
                version_id=version.id,
                sheet_name=source_row["sheet_name"],
                sheet_order=source_row["sheet_order"],
                row_number=source_row["row_number"],
                row_kind=source_row["row_kind"],
                outline_level=source_row["outline_level"],
                parent_row_number=source_row.get("parent_row_number"),
                entity_type=source_row.get("entity_type"),
                entity_id=getattr(entity, "id", None),
                values_json=compact_json(source_row["values"]),
                formulas_json=compact_json(source_row.get("formulas") or {}),
                styles_json=compact_json(source_row.get("styles") or {}),
                merge_ranges_json=compact_json(source_row.get("merge_ranges") or []),
                row_height=source_row.get("row_height"),
                hidden=bool(source_row.get("hidden")),
                collapsed=bool(source_row.get("collapsed")),
            )
        )

    _add_event(
        db,
        version,
        "imported",
        actor_id=actor_id,
        reason="导入企业定额 2.0 工作簿",
        details={
            "source": source,
            "summary": parsed["summary"],
            "quality": {
                "status": parsed["quality"]["status"],
                "blocker_count": parsed["quality"]["blocker_count"],
                "warning_count": parsed["quality"]["warning_count"],
            },
        },
    )
    db.flush()
    recalculate_version(db, version.id, actor_id=actor_id, reason="导入后首次公式重算", record_event=False)
    return version


def list_versions(db: Session) -> list[dict[str, Any]]:
    versions = (
        db.query(EnterpriseQuotaVersion)
        .order_by(
            EnterpriseQuotaVersion.is_active.desc(),
            EnterpriseQuotaVersion.created_at.desc(),
            EnterpriseQuotaVersion.id.desc(),
        )
        .all()
    )
    return [serialize_version(db, version) for version in versions]


def serialize_version(db: Session, version: EnterpriseQuotaVersion) -> dict[str, Any]:
    counts = {
        "sections": _count(db, EnterpriseQuotaSection, version.id),
        "items": _count(db, EnterpriseQuotaItem, version.id),
        "components": _count(db, EnterpriseQuotaComponent, version.id),
        "resources": _count(db, EnterpriseCostResource, version.id),
        "workbook_rows": _count(db, EnterpriseQuotaSheetRow, version.id),
    }
    quality = _json_load(version.quality_summary_json)
    return {
        "id": version.id,
        "version_code": version.version_code,
        "version_name": version.version_name,
        "schema_version": version.schema_version,
        "workbook_title": version.workbook_title,
        "source_filename": version.source_filename,
        "source_file_sha256": version.source_file_sha256,
        "status": version.status,
        "is_active": bool(version.is_active),
        "revision": int(version.revision or 1),
        "formula_count": int(version.formula_count or 0),
        "quality_status": version.quality_status or quality.get("status"),
        "quality": quality,
        "counts": counts,
        "created_by": version.created_by,
        "activated_by": version.activated_by,
        "activated_at": _iso(version.activated_at),
        "last_recalculated_at": _iso(version.last_recalculated_at),
        "created_at": _iso(version.created_at),
        "updated_at": _iso(version.updated_at),
    }


def get_version(db: Session, version_id: int, *, lock: bool = False) -> EnterpriseQuotaVersion:
    query = db.query(EnterpriseQuotaVersion).filter(EnterpriseQuotaVersion.id == version_id)
    if lock:
        query = query.with_for_update()
    version = query.first()
    if version is None:
        raise EnterpriseQuotaV2WorkbenchError(
            "VERSION_NOT_FOUND",
            "企业定额版本不存在",
            status_code=404,
        )
    return version


def list_sheet_rows(
    db: Session,
    version_id: int,
    *,
    sheet_key: str,
    keyword: str | None = None,
    major_section_id: int | None = None,
    chapter_id: int | None = None,
    page: int = 1,
    page_size: int = 120,
) -> dict[str, Any]:
    version = get_version(db, version_id)
    sheet_name = WORKBENCH_SHEET_KEYS.get(sheet_key)
    if not sheet_name:
        raise EnterpriseQuotaV2WorkbenchError("SHEET_NOT_FOUND", "未知工作表", status_code=404)
    rows = (
        db.query(EnterpriseQuotaSheetRow)
        .filter(
            EnterpriseQuotaSheetRow.version_id == version.id,
            EnterpriseQuotaSheetRow.sheet_name == sheet_name,
        )
        .order_by(EnterpriseQuotaSheetRow.row_number.asc())
        .all()
    )
    classification = {
        "major_sections": [],
        "chapters": [],
        "selected_major_section_id": None,
        "selected_chapter_id": None,
    }
    if sheet_key == "enterprise":
        sections = (
            db.query(EnterpriseQuotaSection)
            .filter(EnterpriseQuotaSection.version_id == version.id)
            .order_by(EnterpriseQuotaSection.sort_order.asc(), EnterpriseQuotaSection.id.asc())
            .all()
        )
        classification = _serialize_section_filters(
            sections,
            major_section_id=major_section_id,
            chapter_id=chapter_id,
        )
        rows = _filter_sheet_rows_by_section(
            rows,
            sections,
            major_section_id=classification["selected_major_section_id"],
            chapter_id=classification["selected_chapter_id"],
        )
    keyword_text = (keyword or "").strip().lower()
    if keyword_text:
        rows = [
            row
            for row in rows
            if keyword_text in " ".join(str(value or "") for value in _json_load(row.values_json).values()).lower()
        ]
    total = len(rows)
    start = (page - 1) * page_size
    selected = rows[start : start + page_size]
    return {
        "version": serialize_version(db, version),
        "sheet_key": sheet_key,
        "sheet_name": sheet_name,
        "headers": list(HEADERS_BY_SHEET[sheet_name]),
        "rows": [serialize_sheet_row(row) for row in selected],
        "total": total,
        "page": page,
        "page_size": page_size,
        "editable": version.status == QUOTA_VERSION_STATUS_DRAFT and sheet_key in {"labor", "material"},
        "editable_columns": list("ABCDEFGH") if sheet_key in {"labor", "material"} else [],
        "classification": classification,
    }


def _serialize_section_filters(
    sections: list[EnterpriseQuotaSection],
    *,
    major_section_id: int | None,
    chapter_id: int | None,
) -> dict[str, Any]:
    section_by_id = {int(section.id): section for section in sections}
    major = section_by_id.get(int(major_section_id)) if major_section_id else None
    chapter = section_by_id.get(int(chapter_id)) if chapter_id else None

    if major_section_id and (major is None or int(major.level) != 1):
        raise EnterpriseQuotaV2WorkbenchError(
            "MAJOR_SECTION_NOT_FOUND",
            "所选企业定额大类不存在于当前版本",
            status_code=404,
        )
    if chapter_id and (chapter is None or int(chapter.level) != 2):
        raise EnterpriseQuotaV2WorkbenchError(
            "CHAPTER_NOT_FOUND",
            "所选企业定额小类不存在于当前版本",
            status_code=404,
        )
    if chapter is not None:
        chapter_major = section_by_id.get(int(chapter.parent_section_id)) if chapter.parent_section_id else None
        if chapter_major is None:
            raise EnterpriseQuotaV2WorkbenchError(
                "CHAPTER_MAJOR_SECTION_MISSING",
                "所选企业定额小类缺少所属大类",
                status_code=409,
            )
        if major is not None and int(chapter_major.id) != int(major.id):
            raise EnterpriseQuotaV2WorkbenchError(
                "SECTION_FILTER_MISMATCH",
                "所选小类不属于当前大类",
                status_code=400,
            )
        major = chapter_major

    def serialize(section: EnterpriseQuotaSection) -> dict[str, Any]:
        return {
            "id": int(section.id),
            "section_code": section.section_code,
            "section_name": section.section_name,
            "label": section.section_name or section.section_code or f"分类 {section.id}",
            "parent_section_id": int(section.parent_section_id) if section.parent_section_id else None,
            "source_row_index": section.source_row_index,
            "sort_order": section.sort_order,
        }

    return {
        "major_sections": [serialize(section) for section in sections if int(section.level) == 1],
        "chapters": [serialize(section) for section in sections if int(section.level) == 2],
        "selected_major_section_id": int(major.id) if major is not None else None,
        "selected_chapter_id": int(chapter.id) if chapter is not None else None,
    }


def _filter_sheet_rows_by_section(
    rows: list[EnterpriseQuotaSheetRow],
    sections: list[EnterpriseQuotaSection],
    *,
    major_section_id: int | None,
    chapter_id: int | None,
) -> list[EnterpriseQuotaSheetRow]:
    if not major_section_id and not chapter_id:
        return rows

    section_by_id = {int(section.id): section for section in sections}
    target = section_by_id.get(int(chapter_id or major_section_id))
    if target is None or target.source_row_index is None:
        return []

    start_row = int(target.source_row_index)
    next_boundaries = [
        int(section.source_row_index)
        for section in sections
        if section.source_row_index is not None
        and int(section.source_row_index) > start_row
        and (chapter_id is not None or int(section.level) == 1)
    ]
    end_row = min(next_boundaries) if next_boundaries else None
    return [
        row
        for row in rows
        if int(row.row_number) >= start_row
        and (end_row is None or int(row.row_number) < end_row)
    ]


def serialize_sheet_row(row: EnterpriseQuotaSheetRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "row_number": row.row_number,
        "row_kind": row.row_kind,
        "outline_level": row.outline_level,
        "parent_row_number": row.parent_row_number,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "values": _json_load(row.values_json),
        "formulas": _json_load(row.formulas_json),
        "styles": _json_load(row.styles_json),
        "merge_ranges": _json_list(row.merge_ranges_json),
        "row_height": row.row_height,
        "hidden": bool(row.hidden),
        "collapsed": bool(row.collapsed),
    }


def update_resource(
    db: Session,
    version_id: int,
    resource_id: int,
    payload: dict[str, Any],
    *,
    actor_id: int | None,
    expected_revision: int | None,
    reason: str | None,
) -> dict[str, Any]:
    version = get_version(db, version_id, lock=True)
    _require_draft(version)
    _check_revision(version, expected_revision)
    resource = (
        db.query(EnterpriseCostResource)
        .filter(
            EnterpriseCostResource.id == resource_id,
            EnterpriseCostResource.version_id == version.id,
        )
        .with_for_update()
        .first()
    )
    if resource is None:
        raise EnterpriseQuotaV2WorkbenchError(
            "RESOURCE_NOT_FOUND",
            "人工/材料价格记录不存在",
            status_code=404,
        )
    before = serialize_resource(resource)
    _apply_resource_payload(resource, payload)
    version.revision = int(version.revision or 1) + 1
    db.flush()
    recalculation = recalculate_version(
        db,
        version.id,
        actor_id=actor_id,
        reason=reason or f"更新{_library_label(resource.library_kind)}第 {resource.source_row_index} 行",
        record_event=False,
    )
    after = serialize_resource(resource)
    _add_event(
        db,
        version,
        "resource_updated",
        actor_id=actor_id,
        reason=reason,
        details={"before": before, "after": after, "recalculation": recalculation},
    )
    return {
        "version": serialize_version(db, version),
        "resource": after,
        "recalculation": recalculation,
    }


def create_resource(
    db: Session,
    version_id: int,
    payload: dict[str, Any],
    *,
    actor_id: int | None,
    expected_revision: int | None,
    reason: str | None,
) -> dict[str, Any]:
    version = get_version(db, version_id, lock=True)
    _require_draft(version)
    _check_revision(version, expected_revision)
    library_kind = str(payload.get("library_kind") or "").strip()
    if library_kind not in {"labor", "material"}:
        raise EnterpriseQuotaV2WorkbenchError(
            "LIBRARY_KIND_INVALID",
            "价格库类型必须是 labor 或 material",
        )
    sheet_name = LABOR_SHEET if library_kind == "labor" else MATERIAL_SHEET
    max_row = (
        db.query(func.max(EnterpriseCostResource.source_row_index))
        .filter(
            EnterpriseCostResource.version_id == version.id,
            EnterpriseCostResource.library_kind == library_kind,
        )
        .scalar()
        or 2
    )
    row_number = int(max_row) + 1
    max_order = (
        db.query(func.max(EnterpriseCostResource.sort_order))
        .filter(EnterpriseCostResource.version_id == version.id)
        .scalar()
        or 0
    )
    resource = EnterpriseCostResource(
        version_id=version.id,
        library_kind=library_kind,
        resource_type="labor" if library_kind == "labor" else "auxiliary_material",
        source_sheet=sheet_name,
        source_row_index=row_number,
        sort_order=int(max_order) + 1,
    )
    _apply_resource_payload(resource, payload)
    db.add(resource)
    db.flush()
    values = _resource_sheet_values(resource)
    previous_row = (
        db.query(EnterpriseQuotaSheetRow)
        .filter(
            EnterpriseQuotaSheetRow.version_id == version.id,
            EnterpriseQuotaSheetRow.sheet_name == sheet_name,
            EnterpriseQuotaSheetRow.row_kind == "data",
        )
        .order_by(EnterpriseQuotaSheetRow.row_number.desc())
        .first()
    )
    db.add(
        EnterpriseQuotaSheetRow(
            version_id=version.id,
            sheet_name=sheet_name,
            sheet_order=1 if library_kind == "labor" else 2,
            row_number=row_number,
            row_kind="data",
            outline_level=0,
            entity_type="resource",
            entity_id=resource.id,
            values_json=compact_json(values),
            formulas_json=compact_json({}),
            styles_json=previous_row.styles_json if previous_row else compact_json({}),
            merge_ranges_json=compact_json([]),
            row_height=previous_row.row_height if previous_row else None,
        )
    )
    version.revision = int(version.revision or 1) + 1
    recalculation = recalculate_version(
        db,
        version.id,
        actor_id=actor_id,
        reason=reason or f"新增{_library_label(library_kind)}记录",
        record_event=False,
    )
    _add_event(
        db,
        version,
        "resource_created",
        actor_id=actor_id,
        reason=reason,
        details={"resource": serialize_resource(resource), "recalculation": recalculation},
    )
    return {
        "version": serialize_version(db, version),
        "resource": serialize_resource(resource),
        "recalculation": recalculation,
    }


def recalculate_version(
    db: Session,
    version_id: int,
    *,
    actor_id: int | None,
    reason: str | None,
    record_event: bool = True,
) -> dict[str, Any]:
    version = get_version(db, version_id)
    resources = (
        db.query(EnterpriseCostResource)
        .filter(EnterpriseCostResource.version_id == version.id)
        .order_by(EnterpriseCostResource.sort_order, EnterpriseCostResource.id)
        .all()
    )
    by_library_name: defaultdict[tuple[str, str], list[EnterpriseCostResource]] = defaultdict(list)
    for resource in resources:
        name = _clean(resource.resource_name)
        if resource.library_kind and name:
            by_library_name[(resource.library_kind, name)].append(resource)

    components = (
        db.query(EnterpriseQuotaComponent)
        .filter(EnterpriseQuotaComponent.version_id == version.id)
        .order_by(EnterpriseQuotaComponent.sort_order, EnterpriseQuotaComponent.id)
        .all()
    )
    linked_count = 0
    unresolved_count = 0
    ambiguous_count = 0
    affected_item_ids: set[int] = set()
    for component in components:
        matches = by_library_name.get(
            (component.formula_library_kind or "", _clean(component.resource_name) or ""),
            [],
        )
        if component.resource_id is not None:
            linked_resource = next(
                (resource for resource in resources if resource.id == component.resource_id),
                None,
            )
            if linked_resource is not None and linked_resource not in matches:
                matches = [linked_resource, *matches]
        resource = matches[0] if matches else None
        if resource is None:
            component.resource = None
            component.formula_link_status = "unresolved"
            unresolved_count += 1
        else:
            component.resource = resource
            component.formula_link_status = "ambiguous" if len(matches) > 1 else "linked"
            if component.formula_link_status == "ambiguous":
                ambiguous_count += 1
            linked_count += 1
            component.resource_code = resource.resource_code
            component.resource_name = resource.resource_name
            component.component_type = RESOURCE_TYPE_LABEL.get(resource.resource_type, "未知")
            component.work_content = resource.work_content
            component.specification = resource.specification
            component.brand = resource.brand
            component.unit = resource.unit
            component.unit_price = resource.computed_price if resource.computed_price is not None else resource.price
            component.fee_bucket = _fee_bucket_for_resource_type(resource.resource_type)
        component.amount = _multiply(component.quantity, component.unit_price)
        if component.quota_item_id:
            affected_item_ids.add(int(component.quota_item_id))
        _sync_component_sheet_row(db, component)

    items = (
        db.query(EnterpriseQuotaItem)
        .filter(EnterpriseQuotaItem.version_id == version.id)
        .order_by(EnterpriseQuotaItem.sort_order, EnterpriseQuotaItem.id)
        .all()
    )
    component_sums: defaultdict[int, Counter] = defaultdict(Counter)
    for component in components:
        if component.quota_item_id and component.fee_bucket:
            amount = _decimal(component.amount) or Decimal("0")
            component_sums[int(component.quota_item_id)][component.fee_bucket] += amount
    for item in items:
        sums = component_sums[int(item.id)]
        item.labor_fee = _float(sums["labor"])
        item.main_material_fee = _float(sums["main_material"])
        item.auxiliary_material_fee = _float(sums["auxiliary_material"])
        item.machinery_fee = _float(sums["machinery"])
        item.unit_price = _float(sum(sums.values(), Decimal("0")))
        _sync_item_sheet_row(db, item)

    for resource in resources:
        resource.computed_price = resource.price
        _sync_resource_sheet_row(db, resource)

    version.last_recalculated_at = _utcnow()
    quality = evaluate_version_quality(db, version)
    version.quality_status = quality["status"]
    version.quality_summary_json = compact_json(quality)
    db.flush()
    result = {
        "linked_component_count": linked_count,
        "unresolved_component_count": unresolved_count,
        "ambiguous_component_count": ambiguous_count,
        "recalculated_item_count": len(items),
        "quality_status": quality["status"],
        "blocker_count": quality["blocker_count"],
        "warning_count": quality["warning_count"],
    }
    if record_event:
        _add_event(
            db,
            version,
            "recalculated",
            actor_id=actor_id,
            reason=reason,
            details=result,
        )
    return result


def recalculate_draft_version(
    db: Session,
    version_id: int,
    *,
    actor_id: int | None,
    expected_revision: int | None,
    reason: str | None,
) -> dict[str, Any]:
    version = get_version(db, version_id, lock=True)
    _require_draft(version)
    _check_revision(version, expected_revision)
    version.revision = int(version.revision or 1) + 1
    result = recalculate_version(
        db,
        version.id,
        actor_id=actor_id,
        reason=reason or "人工触发全量公式重算",
        record_event=True,
    )
    return {"version": serialize_version(db, version), "recalculation": result}


def evaluate_version_quality(db: Session, version: EnterpriseQuotaVersion) -> dict[str, Any]:
    items = (
        db.query(EnterpriseQuotaItem)
        .filter(EnterpriseQuotaItem.version_id == version.id)
        .order_by(EnterpriseQuotaItem.sort_order)
        .all()
    )
    components = (
        db.query(EnterpriseQuotaComponent)
        .filter(EnterpriseQuotaComponent.version_id == version.id)
        .order_by(EnterpriseQuotaComponent.sort_order)
        .all()
    )
    resources = (
        db.query(EnterpriseCostResource)
        .filter(EnterpriseCostResource.version_id == version.id)
        .order_by(EnterpriseCostResource.sort_order)
        .all()
    )
    issues: list[dict[str, Any]] = []

    unresolved = [component for component in components if component.formula_link_status == "unresolved"]
    for component in unresolved[:100]:
        issues.append(
            _quality_issue(
                "error",
                "FORMULA_RESOURCE_UNRESOLVED",
                f"组成行无法链接价格库：{component.resource_name or '-'}",
                sheet=component.source_sheet,
                row=component.source_row_index,
                evidence={
                    "quota_code": component.parent_quota_code,
                    "resource_name": component.resource_name,
                    "library_kind": component.formula_library_kind,
                    "formulas": _json_load(component.formulas_json),
                },
            )
        )

    required_missing = []
    for item in items:
        fields = [
            label
            for value, label in (
                (item.quota_code, "定额编码"),
                (item.item_name, "项目名称"),
                (item.unit, "单位"),
            )
            if not _clean(value)
        ]
        if fields:
            required_missing.append(
                {
                    "row": item.source_row_index,
                    "quota_code": item.quota_code,
                    "missing": fields,
                }
            )
    if required_missing:
        issues.append(
            _quality_issue(
                "error",
                "ITEM_REQUIRED_FIELD_MISSING",
                f"{len(required_missing)} 条定额主项缺少必填字段",
                sheet=ENTERPRISE_SHEET,
                evidence={"examples": required_missing[:50]},
            )
        )

    ambiguous_names = _duplicate_resource_groups(resources, key="name")
    if ambiguous_names:
        issues.append(
            _quality_issue(
                "warning",
                "FORMULA_LOOKUP_NAME_AMBIGUOUS",
                f"存在 {len(ambiguous_names)} 组同名资源；按 Excel MATCH 规则使用首条记录",
                evidence={"groups": ambiguous_names[:50]},
            )
        )
    duplicate_codes = _duplicate_resource_groups(resources, key="code")
    if duplicate_codes:
        issues.append(
            _quality_issue(
                "warning",
                "RESOURCE_CODE_DUPLICATED",
                f"人工/材料价格库存在 {len(duplicate_codes)} 组重复编码",
                evidence={"groups": duplicate_codes[:50]},
            )
        )

    report_mismatches = _validation_report_mismatches(db, version.id, items)
    if report_mismatches:
        issues.append(
            _quality_issue(
                "warning",
                "VALIDATION_REPORT_MISMATCH",
                f"当前联动结果与静态校验报告有 {len(report_mismatches)} 条差异",
                sheet=VALIDATION_SHEET,
                evidence={"count": len(report_mismatches), "examples": report_mismatches[:50]},
            )
        )
    imported_summary = _json_load(version.summary_json)
    formula_error_count = int(imported_summary.get("formula_error_cell_count") or 0)
    if formula_error_count:
        issues.append(
            _quality_issue(
                "warning",
                "SOURCE_FORMULA_CACHED_ERROR",
                f"源工作簿缓存结果包含 {formula_error_count} 个公式错误；系统已按结构化公式重新计算",
                sheet=ENTERPRISE_SHEET,
            )
        )

    blockers = [issue for issue in issues if issue["severity"] == "error"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    status = "blocked" if blockers else ("warning" if warnings else "ready")
    return {
        "status": status,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "validated_at": _utcnow().isoformat(),
        "counts": {
            "sections": _count(db, EnterpriseQuotaSection, version.id),
            "items": len(items),
            "components": len(components),
            "resources": len(resources),
            "linked_components": sum(component.formula_link_status in {"linked", "ambiguous"} for component in components),
            "unresolved_components": len(unresolved),
            "ambiguous_components": sum(component.formula_link_status == "ambiguous" for component in components),
            "formula_count": int(version.formula_count or 0),
            "validation_report_mismatches": len(report_mismatches),
        },
        "issues": issues,
    }


def clone_version_to_draft(
    db: Session,
    source_version_id: int,
    *,
    actor_id: int | None,
    version_code: str | None,
    version_name: str | None,
    reason: str | None,
) -> EnterpriseQuotaVersion:
    source = get_version(db, source_version_id)
    resolved_code = _unique_version_code(
        db,
        version_code or f"{source.version_code}-draft-{_utcnow().strftime('%Y%m%d%H%M%S')}",
    )
    clone = EnterpriseQuotaVersion(
        version_code=resolved_code,
        version_name=(version_name or f"{source.version_name} - 编辑草稿")[:255],
        source_filename=source.source_filename,
        source_file_sha256=source.source_file_sha256,
        schema_version=source.schema_version,
        workbook_title=source.workbook_title,
        workbook_metadata_json=source.workbook_metadata_json,
        quality_status=source.quality_status,
        quality_summary_json=source.quality_summary_json,
        formula_count=source.formula_count,
        revision=1,
        last_recalculated_at=source.last_recalculated_at,
        status=QUOTA_VERSION_STATUS_DRAFT,
        is_active=False,
        summary_json=source.summary_json,
        notes=f"从版本 {source.version_code} 克隆为可编辑草稿。",
        created_by=actor_id,
    )
    db.add(clone)
    db.flush()

    section_map: dict[int, EnterpriseQuotaSection] = {}
    source_sections = (
        db.query(EnterpriseQuotaSection)
        .filter(EnterpriseQuotaSection.version_id == source.id)
        .order_by(EnterpriseQuotaSection.sort_order, EnterpriseQuotaSection.id)
        .all()
    )
    for old in source_sections:
        new = EnterpriseQuotaSection(
            version_id=clone.id,
            section_code=old.section_code,
            section_name=old.section_name,
            level=old.level,
            outline_level=old.outline_level,
            source_sheet=old.source_sheet,
            source_row_index=old.source_row_index,
            sort_order=old.sort_order,
            raw_row_json=old.raw_row_json,
        )
        db.add(new)
        db.flush()
        section_map[int(old.id)] = new
    for old in source_sections:
        if old.parent_section_id:
            section_map[int(old.id)].parent_section_id = section_map[int(old.parent_section_id)].id

    resource_map: dict[int, EnterpriseCostResource] = {}
    for old in (
        db.query(EnterpriseCostResource)
        .filter(EnterpriseCostResource.version_id == source.id)
        .order_by(EnterpriseCostResource.sort_order, EnterpriseCostResource.id)
        .all()
    ):
        new = EnterpriseCostResource(
            version_id=clone.id,
            resource_code=old.resource_code,
            resource_name=old.resource_name,
            resource_type=old.resource_type,
            library_kind=old.library_kind,
            category=old.category,
            specification=old.specification,
            brand=old.brand,
            work_content=old.work_content,
            calculation_rule=old.calculation_rule,
            unit=old.unit,
            default_quantity=old.default_quantity,
            price=old.price,
            tax_rate=old.tax_rate,
            computed_price=old.computed_price,
            price_block_label=old.price_block_label,
            source_sheet=old.source_sheet,
            source_row_index=old.source_row_index,
            sort_order=old.sort_order,
            formulas_json=old.formulas_json,
            raw_row_json=old.raw_row_json,
        )
        db.add(new)
        db.flush()
        resource_map[int(old.id)] = new

    item_map: dict[int, EnterpriseQuotaItem] = {}
    for old in (
        db.query(EnterpriseQuotaItem)
        .filter(EnterpriseQuotaItem.version_id == source.id)
        .order_by(EnterpriseQuotaItem.sort_order, EnterpriseQuotaItem.id)
        .all()
    ):
        new = EnterpriseQuotaItem(
            version_id=clone.id,
            section_id=section_map.get(int(old.section_id)).id if old.section_id else None,
            quota_code=old.quota_code,
            row_type=old.row_type,
            item_name=old.item_name,
            work_content=old.work_content,
            worker_or_subtype=old.worker_or_subtype,
            specification=old.specification,
            brand=old.brand,
            unit=old.unit,
            quantity=old.quantity,
            unit_price=old.unit_price,
            labor_fee=old.labor_fee,
            main_material_fee=old.main_material_fee,
            auxiliary_material_fee=old.auxiliary_material_fee,
            machinery_fee=old.machinery_fee,
            outline_level=old.outline_level,
            source_sheet=old.source_sheet,
            source_row_index=old.source_row_index,
            sort_order=old.sort_order,
            formulas_json=old.formulas_json,
            raw_row_json=old.raw_row_json,
        )
        db.add(new)
        db.flush()
        item_map[int(old.id)] = new

    component_map: dict[int, EnterpriseQuotaComponent] = {}
    for old in (
        db.query(EnterpriseQuotaComponent)
        .filter(EnterpriseQuotaComponent.version_id == source.id)
        .order_by(EnterpriseQuotaComponent.sort_order, EnterpriseQuotaComponent.id)
        .all()
    ):
        new = EnterpriseQuotaComponent(
            version_id=clone.id,
            quota_item_id=item_map.get(int(old.quota_item_id)).id if old.quota_item_id else None,
            resource_id=resource_map.get(int(old.resource_id)).id if old.resource_id else None,
            parent_quota_code=old.parent_quota_code,
            component_type=old.component_type,
            resource_code=old.resource_code,
            resource_name=old.resource_name,
            worker_or_subtype=old.worker_or_subtype,
            work_content=old.work_content,
            specification=old.specification,
            brand=old.brand,
            unit=old.unit,
            quantity=old.quantity,
            unit_price=old.unit_price,
            amount=old.amount,
            fee_bucket=old.fee_bucket,
            outline_level=old.outline_level,
            formulas_json=old.formulas_json,
            formula_library_kind=old.formula_library_kind,
            formula_link_status=old.formula_link_status,
            source_sheet=old.source_sheet,
            source_row_index=old.source_row_index,
            sort_order=old.sort_order,
            raw_row_json=old.raw_row_json,
        )
        db.add(new)
        db.flush()
        component_map[int(old.id)] = new

    entity_maps = {
        "section": section_map,
        "quota_item": item_map,
        "component": component_map,
        "resource": resource_map,
    }
    for old in (
        db.query(EnterpriseQuotaSheetRow)
        .filter(EnterpriseQuotaSheetRow.version_id == source.id)
        .order_by(EnterpriseQuotaSheetRow.sheet_order, EnterpriseQuotaSheetRow.row_number)
        .all()
    ):
        mapped_entity = entity_maps.get(old.entity_type or "", {}).get(int(old.entity_id)) if old.entity_id else None
        db.add(
            EnterpriseQuotaSheetRow(
                version_id=clone.id,
                sheet_name=old.sheet_name,
                sheet_order=old.sheet_order,
                row_number=old.row_number,
                row_kind=old.row_kind,
                outline_level=old.outline_level,
                parent_row_number=old.parent_row_number,
                entity_type=old.entity_type,
                entity_id=getattr(mapped_entity, "id", None),
                values_json=old.values_json,
                formulas_json=old.formulas_json,
                styles_json=old.styles_json,
                merge_ranges_json=old.merge_ranges_json,
                row_height=old.row_height,
                hidden=old.hidden,
                collapsed=old.collapsed,
            )
        )
    _add_event(
        db,
        clone,
        "cloned",
        actor_id=actor_id,
        reason=reason,
        details={"source_version_id": source.id, "source_version_code": source.version_code},
    )
    db.flush()
    return clone


def activate_version(
    db: Session,
    version_id: int,
    *,
    actor_id: int | None,
    expected_revision: int | None,
    reason: str,
    acknowledge_warnings: bool,
) -> EnterpriseQuotaVersion:
    version = get_version(db, version_id, lock=True)
    _require_draft(version)
    _check_revision(version, expected_revision)
    quality = evaluate_version_quality(db, version)
    version.quality_status = quality["status"]
    version.quality_summary_json = compact_json(quality)
    if quality["blocker_count"]:
        raise EnterpriseQuotaV2WorkbenchError(
            "ACTIVATION_BLOCKED",
            "当前版本仍有数据或公式阻断项，不能启用",
            status_code=409,
            details=quality,
        )
    if quality["warning_count"] and not acknowledge_warnings:
        raise EnterpriseQuotaV2WorkbenchError(
            "WARNINGS_NOT_ACKNOWLEDGED",
            "当前版本仍有警告，需明确确认后才能启用",
            status_code=409,
            details=quality,
        )
    active_job_count = (
        db.query(func.count(QuoteJob.id))
        .filter(QuoteJob.status.in_(sorted(ACTIVE_QUOTE_JOB_STATUSES)))
        .scalar()
        or 0
    )
    if active_job_count:
        raise EnterpriseQuotaV2WorkbenchError(
            "ACTIVE_QUOTE_JOBS",
            f"当前仍有 {active_job_count} 个报价任务处理中，暂不能切换企业定额版本",
            status_code=409,
        )

    now = _utcnow()
    current_active = (
        db.query(EnterpriseQuotaVersion)
        .filter(
            EnterpriseQuotaVersion.is_active.is_(True),
            EnterpriseQuotaVersion.id != version.id,
        )
        .with_for_update()
        .all()
    )
    archived = []
    for old in current_active:
        old.is_active = False
        old.status = QUOTA_VERSION_STATUS_ARCHIVED
        old.archived_by = actor_id
        old.archived_at = now
        archived.append({"id": old.id, "version_code": old.version_code})
        _add_event(
            db,
            old,
            "archived_on_activation",
            actor_id=actor_id,
            reason=reason,
            details={"replacement_version_id": version.id, "replacement_version_code": version.version_code},
        )
    version.is_active = True
    version.status = QUOTA_VERSION_STATUS_ACTIVE
    version.revision = int(version.revision or 1) + 1
    version.activated_by = actor_id
    version.activated_at = now
    version.archived_by = None
    version.archived_at = None
    if version.import_batch:
        version.import_batch.status = IMPORT_BATCH_STATUS_ACTIVATED
    _add_event(
        db,
        version,
        "activated",
        actor_id=actor_id,
        reason=reason,
        details={"archived_versions": archived, "quality": quality},
    )
    db.flush()
    return version


def list_version_events(
    db: Session,
    version_id: int,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    get_version(db, version_id)
    query = db.query(EnterpriseQuotaVersionEvent).filter(
        EnterpriseQuotaVersionEvent.version_id == version_id
    )
    total = query.count()
    rows = (
        query.order_by(
            EnterpriseQuotaVersionEvent.created_at.desc(),
            EnterpriseQuotaVersionEvent.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "actor_id": row.actor_id,
            "reason": row.reason,
            "details": _json_load(row.details_json),
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ], total


def serialize_resource(resource: EnterpriseCostResource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "version_id": resource.version_id,
        "library_kind": resource.library_kind,
        "category": resource.category,
        "resource_code": resource.resource_code,
        "resource_type": resource.resource_type,
        "resource_type_label": RESOURCE_TYPE_LABEL.get(resource.resource_type, "未知"),
        "resource_name": resource.resource_name,
        "work_content": resource.work_content,
        "calculation_rule": resource.calculation_rule,
        "specification": resource.specification,
        "brand": resource.brand,
        "unit": normalize_enterprise_quota_unit(resource.unit),
        "default_quantity": resource.default_quantity,
        "price": resource.price,
        "computed_price": resource.computed_price,
        "source_sheet": resource.source_sheet,
        "source_row_index": resource.source_row_index,
        "updated_at": _iso(resource.updated_at),
    }


def _apply_resource_payload(resource: EnterpriseCostResource, payload: dict[str, Any]) -> None:
    data = {key: value for key, value in payload.items() if key in RESOURCE_UPDATE_FIELDS}
    if "resource_type" in data:
        normalized_type = RESOURCE_TYPE_FROM_LABEL.get(str(data["resource_type"] or "").strip())
        if normalized_type is None:
            raise EnterpriseQuotaV2WorkbenchError(
                "RESOURCE_TYPE_INVALID",
                "类型必须是人工、主材、辅材或机械",
            )
        if resource.library_kind == "material" and normalized_type not in {"main_material", "auxiliary_material"}:
            raise EnterpriseQuotaV2WorkbenchError(
                "RESOURCE_TYPE_INVALID",
                "材料价格库类型必须是主材或辅材",
            )
        resource.resource_type = normalized_type
    for field in (
        "category",
        "resource_code",
        "resource_name",
        "work_content",
        "calculation_rule",
        "specification",
        "brand",
    ):
        if field in data:
            setattr(resource, field, _clean(data[field]))
    if "unit" in data:
        resource.unit = normalize_enterprise_quota_unit(data["unit"])
    if "default_quantity" in data:
        resource.default_quantity = _nonnegative_number(data["default_quantity"], "含量")
    if "price" in data:
        resource.price = _nonnegative_number(data["price"], "单价")
        resource.computed_price = resource.price
    if not _clean(resource.resource_name):
        raise EnterpriseQuotaV2WorkbenchError("RESOURCE_NAME_REQUIRED", "项目/材料名称不能为空")
    if not _clean(resource.unit):
        raise EnterpriseQuotaV2WorkbenchError("RESOURCE_UNIT_REQUIRED", "单位不能为空")
    if resource.price is None:
        raise EnterpriseQuotaV2WorkbenchError("RESOURCE_PRICE_REQUIRED", "价格不能为空")


def _sync_resource_sheet_row(db: Session, resource: EnterpriseCostResource) -> None:
    row = _entity_sheet_row(db, resource.version_id, "resource", resource.id)
    if row is None:
        return
    row.values_json = compact_json(_resource_sheet_values(resource))


def _resource_sheet_values(resource: EnterpriseCostResource) -> dict[str, Any]:
    if resource.library_kind == "labor":
        return {
            "A": resource.resource_code,
            "B": RESOURCE_TYPE_LABEL.get(resource.resource_type, "未知"),
            "C": resource.resource_name,
            "D": resource.work_content,
            "E": resource.calculation_rule,
            "F": normalize_enterprise_quota_unit(resource.unit),
            "G": resource.default_quantity,
            "H": resource.price,
        }
    return {
        "A": resource.category,
        "B": resource.resource_code,
        "C": RESOURCE_TYPE_LABEL.get(resource.resource_type, "未知"),
        "D": resource.resource_name,
        "E": resource.specification,
        "F": resource.brand,
        "G": normalize_enterprise_quota_unit(resource.unit),
        "H": resource.price,
    }


def _sync_component_sheet_row(db: Session, component: EnterpriseQuotaComponent) -> None:
    row = _entity_sheet_row(db, component.version_id, "component", component.id)
    if row is None:
        return
    values = _json_load(row.values_json)
    values.update(
        {
            "A": component.resource_code,
            "B": component.component_type,
            "C": component.resource_name,
            "D": component.work_content,
            "E": component.specification,
            "F": component.brand,
            "G": normalize_enterprise_quota_unit(component.unit),
            "H": component.quantity,
            "I": component.unit_price,
            "J": None,
            "K": None,
            "L": None,
            "M": None,
        }
    )
    amount_column = {
        "labor": "J",
        "main_material": "K",
        "auxiliary_material": "L",
        "machinery": "M",
    }.get(component.fee_bucket or "")
    if amount_column:
        values[amount_column] = component.amount
    row.values_json = compact_json(values)


def _sync_item_sheet_row(db: Session, item: EnterpriseQuotaItem) -> None:
    row = _entity_sheet_row(db, item.version_id, "quota_item", item.id)
    if row is None:
        return
    values = _json_load(row.values_json)
    values.update(
        {
            "A": item.quota_code,
            "B": item.row_type or "定额",
            "C": item.item_name,
            "D": item.work_content,
            "E": item.specification,
            "F": item.brand,
            "G": normalize_enterprise_quota_unit(item.unit),
            "H": item.quantity,
            "I": item.unit_price,
            "J": item.labor_fee,
            "K": item.main_material_fee,
            "L": item.auxiliary_material_fee,
            "M": item.machinery_fee,
        }
    )
    row.values_json = compact_json(values)


def _entity_sheet_row(
    db: Session,
    version_id: int,
    entity_type: str,
    entity_id: int,
) -> EnterpriseQuotaSheetRow | None:
    return (
        db.query(EnterpriseQuotaSheetRow)
        .filter(
            EnterpriseQuotaSheetRow.version_id == version_id,
            EnterpriseQuotaSheetRow.entity_type == entity_type,
            EnterpriseQuotaSheetRow.entity_id == entity_id,
        )
        .first()
    )


def _validation_report_mismatches(
    db: Session,
    version_id: int,
    items: list[EnterpriseQuotaItem],
) -> list[dict[str, Any]]:
    rows = (
        db.query(EnterpriseQuotaSheetRow)
        .filter(
            EnterpriseQuotaSheetRow.version_id == version_id,
            EnterpriseQuotaSheetRow.sheet_name == VALIDATION_SHEET,
            EnterpriseQuotaSheetRow.row_kind == "data",
        )
        .order_by(EnterpriseQuotaSheetRow.row_number)
        .all()
    )
    item_by_code = {_clean(item.quota_code): item for item in items if _clean(item.quota_code)}
    mismatches = []
    for row in rows:
        values = _json_load(row.values_json)
        code = _clean(values.get("A"))
        if not code:
            continue
        item = item_by_code.get(code)
        if item is None:
            mismatches.append({"quota_code": code, "reason": "quota_item_missing"})
            continue
        differences = {}
        for item_value, report_value, label in (
            (item.labor_fee, values.get("F"), "labor_fee"),
            (item.main_material_fee, values.get("G"), "main_material_fee"),
            (item.auxiliary_material_fee, values.get("H"), "auxiliary_material_fee"),
            (item.machinery_fee, values.get("I"), "machinery_fee"),
            (item.unit_price, values.get("J"), "unit_price"),
        ):
            if not _numbers_equal(item_value, report_value, Decimal("0.01")):
                differences[label] = {"calculated": item_value, "report": report_value}
        if differences:
            mismatches.append({"quota_code": code, "differences": differences})
    return mismatches


def _duplicate_resource_groups(
    resources: list[EnterpriseCostResource],
    *,
    key: str,
) -> list[dict[str, Any]]:
    groups: defaultdict[tuple[str, str], list[EnterpriseCostResource]] = defaultdict(list)
    for resource in resources:
        value = _clean(resource.resource_name if key == "name" else resource.resource_code)
        if resource.library_kind and value:
            groups[(resource.library_kind, value)].append(resource)
    return [
        {
            "library_kind": library_kind,
            "value": value,
            "rows": [resource.source_row_index for resource in matches],
            "prices": [resource.price for resource in matches],
        }
        for (library_kind, value), matches in groups.items()
        if len(matches) > 1
    ]


def _quality_issue(
    severity: str,
    code: str,
    message: str,
    *,
    sheet: str | None = None,
    row: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "sheet": sheet,
        "row": row,
        "evidence": evidence or {},
    }


def _add_event(
    db: Session,
    version: EnterpriseQuotaVersion,
    event_type: str,
    *,
    actor_id: int | None,
    reason: str | None,
    details: dict[str, Any] | None,
) -> EnterpriseQuotaVersionEvent:
    event = EnterpriseQuotaVersionEvent(
        version_id=version.id,
        event_type=event_type,
        actor_id=actor_id,
        reason=_clean(reason),
        details_json=compact_json(details or {}),
    )
    db.add(event)
    return event


def _require_draft(version: EnterpriseQuotaVersion) -> None:
    if version.status != QUOTA_VERSION_STATUS_DRAFT or version.is_active:
        raise EnterpriseQuotaV2WorkbenchError(
            "DRAFT_REQUIRED",
            "正式启用版本不能直接修改，请先克隆为草稿",
            status_code=409,
        )


def _check_revision(version: EnterpriseQuotaVersion, expected_revision: int | None) -> None:
    if expected_revision is not None and int(version.revision or 1) != int(expected_revision):
        raise EnterpriseQuotaV2WorkbenchError(
            "VERSION_CONFLICT",
            "企业定额草稿已被其他操作更新，请刷新后重试",
            status_code=409,
            details={"current_revision": int(version.revision or 1)},
        )


def _default_version_code(filename: str, sha256: str) -> str:
    stem = PathLikeStem(filename)
    normalized = _VERSION_CODE_RE.sub("-", stem).strip("-_").lower()
    normalized = normalized[:42] or "enterprise-quota-v2"
    return f"{normalized}-{sha256[:10]}"[:64]


def _unique_version_code(db: Session, requested: str) -> str:
    base = _VERSION_CODE_RE.sub("-", requested).strip("-_")[:64] or "enterprise-quota-v2"
    candidate = base
    suffix = 2
    while db.query(EnterpriseQuotaVersion.id).filter(EnterpriseQuotaVersion.version_code == candidate).first():
        tail = f"-{suffix}"
        candidate = f"{base[:64 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _batch_uuid(sha256: str) -> str:
    return str(uuid4())


def PathLikeStem(filename: str) -> str:
    value = str(filename or "").replace("\\", "/").split("/")[-1]
    return value.rsplit(".", 1)[0] if "." in value else value


def _count(db: Session, model, version_id: int) -> int:
    return int(db.query(func.count(model.id)).filter(model.version_id == version_id).scalar() or 0)


def _fee_bucket_for_resource_type(resource_type: str | None) -> str | None:
    return {
        "labor": "labor",
        "main_material": "main_material",
        "auxiliary_material": "auxiliary_material",
        "machinery": "machinery",
    }.get(resource_type or "")


def _library_label(library_kind: str | None) -> str:
    return "人工价格库" if library_kind == "labor" else "材料价格库"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _nonnegative_number(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    decimal_value = _decimal(value)
    if decimal_value is None or decimal_value < 0:
        raise EnterpriseQuotaV2WorkbenchError(
            "NUMBER_INVALID",
            f"{label}必须是大于或等于 0 的数字",
        )
    return float(decimal_value)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _float(value: Decimal | Any) -> float:
    decimal_value = _decimal(value) or Decimal("0")
    return float(decimal_value)


def _multiply(left: Any, right: Any) -> float | None:
    left_value = _decimal(left)
    right_value = _decimal(right)
    if left_value is None or right_value is None:
        return None
    return float(left_value * right_value)


def _numbers_equal(left: Any, right: Any, tolerance: Decimal) -> bool:
    left_value = _decimal(left)
    right_value = _decimal(right)
    if left_value is None or right_value is None:
        return left_value is right_value
    return abs(left_value - right_value) <= tolerance


def _json_load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
