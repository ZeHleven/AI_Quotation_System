from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.cost_measurement import (
    MEASUREMENT_STATUS_DRAFT,
    MEASUREMENT_STATUS_LOCKED,
    PRICING_MODE_BREAKDOWN,
    PRICING_MODE_COMPOSITE,
    CostMeasurement,
    CostMeasurementLine,
)
from app.models.enterprise_quota import EnterpriseQuotaItem
from app.models.user import User
from app.services.cost_audit import record_cost_audit
from app.services.cost_measurement import (
    CostMeasurementImportError,
    apply_quota_item,
    build_measurement_export,
    clean_text,
    create_measurement_from_import,
    parse_cost_measurement_workbook,
    recalculate_measurement,
    serialize_measurement,
    serialize_measurement_line,
    write_measurement_event,
)
from app.services.cost_measurement_drafts import (
    CostMeasurementDraftError,
    build_measurement_cost_draft_preview,
    create_measurement_cost_drafts,
)
from app.services.rbac import has_any_role


router = APIRouter()


class MeasurementUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    project_name: Optional[str] = Field(None, max_length=255)
    management_rate: Optional[float] = Field(None, ge=0, le=1)
    profit_rate: Optional[float] = Field(None, ge=0, le=1)
    tax_rate: Optional[float] = Field(None, ge=0, le=1)
    notes: Optional[str] = None


class MeasurementLineUpdate(BaseModel):
    item_name: Optional[str] = Field(None, max_length=255)
    feature: Optional[str] = None
    unit: Optional[str] = Field(None, max_length=64)
    quantity: Optional[float] = Field(None, ge=0)
    pricing_mode: Optional[str] = None
    source_unit_price: Optional[float] = Field(None, ge=0)
    labor_unit_price: Optional[float] = Field(None, ge=0)
    main_material_unit_price: Optional[float] = Field(None, ge=0)
    material_loss_rate: Optional[float] = Field(None, ge=0, le=10)
    auxiliary_machinery_unit_price: Optional[float] = Field(None, ge=0)
    subcontract_unit_price: Optional[float] = Field(None, ge=0)
    review_status: Optional[str] = None


class LockMeasurementIn(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)


class CostDraftPreviewIn(BaseModel):
    line_ids: Optional[list[int]] = Field(None, max_length=5000)


class CostDraftCreateIn(BaseModel):
    line_ids: list[int] = Field(..., min_length=1, max_length=5000)
    note: Optional[str] = Field(None, max_length=2000)


def _payload_dict(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _ensure_feature() -> None:
    if not settings.feature_cost_measurement:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _ensure_cost_db_feature() -> None:
    if not settings.feature_cost_db:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="COST_DB_FEATURE_DISABLED")


def _draft_error(exc: CostMeasurementDraftError) -> HTTPException:
    detail = str(exc)
    status_code = status.HTTP_409_CONFLICT if detail == "MEASUREMENT_MUST_BE_LOCKED" else status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=status_code, detail=detail)


def _require_view(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_viewer", "cost_editor", "cost_approver", "cost_exporter"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _require_edit(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_editor", "cost_approver"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _require_approve(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_approver"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _require_export(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_exporter"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _get_measurement(db: Session, measurement_id: int) -> CostMeasurement:
    measurement = db.query(CostMeasurement).filter(CostMeasurement.id == measurement_id).first()
    if not measurement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="COST_MEASUREMENT_NOT_FOUND")
    return measurement


def _require_draft(measurement: CostMeasurement) -> None:
    if measurement.status != MEASUREMENT_STATUS_DRAFT:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="COST_MEASUREMENT_LOCKED")


def _get_line(db: Session, measurement: CostMeasurement, line_id: int) -> CostMeasurementLine:
    line = db.query(CostMeasurementLine).filter(
        CostMeasurementLine.id == line_id,
        CostMeasurementLine.measurement_id == measurement.id,
    ).first()
    if not line:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="COST_MEASUREMENT_LINE_NOT_FOUND")
    return line


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = clean_text(file.filename, 255) or "cost-measurement.xlsx"
    return filename, await file.read()


@router.post("/admin/cost-measurements/import-preview", summary="\u9884\u89c8\u5bfc\u5165\u6210\u672c\u6d4b\u7b97 Excel")
async def preview_cost_measurement_import(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    _ensure_feature()
    _require_view(current_user)
    filename, content = await _read_upload(file)
    try:
        parsed = parse_cost_measurement_workbook(filename, content)
    except CostMeasurementImportError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return api_ok(parsed)


@router.post("/admin/cost-measurements/import", summary="\u5bfc\u5165\u5e76\u521b\u5efa\u6210\u672c\u6d4b\u7b97\u8349\u7a3f")
async def import_cost_measurement(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    project_name: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    filename, content = await _read_upload(file)
    try:
        measurement = create_measurement_from_import(
            db, filename=filename, content=content, name=name, project_name=project_name,
            notes=notes, actor_user_id=current_user.id,
        )
    except CostMeasurementImportError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return api_ok(serialize_measurement(measurement, include_lines=True, include_events=True), message="\u6210\u672c\u6d4b\u7b97\u8349\u7a3f\u5df2\u521b\u5efa")


@router.get("/admin/cost-measurements", summary="\u67e5\u8be2\u6210\u672c\u6d4b\u7b97\u9879\u76ee")
async def list_cost_measurements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_view(current_user)
    query = db.query(CostMeasurement)
    if status_filter:
        statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
        if statuses:
            query = query.filter(CostMeasurement.status.in_(statuses))
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.filter(or_(
            CostMeasurement.measurement_code.like(pattern),
            CostMeasurement.name.like(pattern),
            CostMeasurement.project_name.like(pattern),
            CostMeasurement.source_filename.like(pattern),
        ))
    total = query.count()
    rows = query.order_by(CostMeasurement.updated_at.desc(), CostMeasurement.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return api_page([serialize_measurement(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/admin/cost-measurements/{measurement_id}", summary="\u67e5\u8be2\u6210\u672c\u6d4b\u7b97\u8be6\u60c5")
async def get_cost_measurement(measurement_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_feature()
    _require_view(current_user)
    return api_ok(serialize_measurement(_get_measurement(db, measurement_id), include_lines=True, include_events=True))


@router.post("/admin/cost-measurements/{measurement_id}/cost-drafts/preview", summary="\u9884\u89c8\u6d4b\u7b97\u660e\u7ec6\u6c89\u6dc0\u4e3a\u6210\u672c\u5e93 draft")
async def preview_measurement_cost_drafts(
    measurement_id: int,
    payload: CostDraftPreviewIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _ensure_cost_db_feature()
    _require_view(current_user)
    measurement = _get_measurement(db, measurement_id)
    try:
        result = build_measurement_cost_draft_preview(db, measurement, line_ids=payload.line_ids)
    except CostMeasurementDraftError as exc:
        raise _draft_error(exc) from exc
    record_cost_audit(
        db,
        user=current_user,
        action="cost_measurement.cost_draft_preview",
        resource_type="cost_measurement",
        resource_id=measurement.id,
        result_count=result["summary"]["selected_line_count"],
        status_value="success",
        message=measurement.measurement_code,
        request=request,
    )
    return api_ok(result)


@router.post("/admin/cost-measurements/{measurement_id}/cost-drafts", summary="\u5c06\u5df2\u590d\u6838\u6d4b\u7b97\u660e\u7ec6\u751f\u6210\u6210\u672c\u5e93 draft")
async def create_measurement_cost_draft_items(
    measurement_id: int,
    payload: CostDraftCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _ensure_cost_db_feature()
    _require_edit(current_user)
    measurement = _get_measurement(db, measurement_id)
    try:
        result = create_measurement_cost_drafts(
            db,
            measurement,
            current_user,
            line_ids=payload.line_ids,
            note=payload.note,
        )
    except CostMeasurementDraftError as exc:
        raise _draft_error(exc) from exc
    db.commit()
    record_cost_audit(
        db,
        user=current_user,
        action="cost_measurement.cost_draft_create",
        resource_type="cost_measurement",
        resource_id=measurement.id,
        result_count=result["created_count"],
        status_value="success",
        message=f"created={result['created_count']}; skipped={result['skipped_count']}",
        request=request,
    )
    return api_ok(result, message="\u6210\u672c\u5e93 draft \u5df2\u751f\u6210")


@router.patch("/admin/cost-measurements/{measurement_id}", summary="\u66f4\u65b0\u6210\u672c\u6d4b\u7b97\u53c2\u6570")
async def update_cost_measurement(
    measurement_id: int, payload: MeasurementUpdate,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    measurement = _get_measurement(db, measurement_id)
    _require_draft(measurement)
    changes = _payload_dict(payload)
    for field in ("name", "project_name", "notes"):
        if field in changes:
            setattr(measurement, field, clean_text(changes[field], 255 if field != "notes" else None))
    for field in ("management_rate", "profit_rate", "tax_rate"):
        if field in changes and changes[field] is not None:
            setattr(measurement, field, float(changes[field]))
    measurement.updated_by = current_user.id
    recalculate_measurement(db, measurement)
    write_measurement_event(db, measurement, event_type="parameters_updated", actor_user_id=current_user.id, message="\u66f4\u65b0\u6d4b\u7b97\u53c2\u6570\u5e76\u7edf\u4e00\u91cd\u7b97", payload=changes)
    db.commit()
    db.refresh(measurement)
    return api_ok(serialize_measurement(measurement, include_lines=True))


@router.patch("/admin/cost-measurements/{measurement_id}/lines/{line_id}", summary="\u66f4\u65b0\u6210\u672c\u6d4b\u7b97\u884c")
async def update_cost_measurement_line(
    measurement_id: int, line_id: int, payload: MeasurementLineUpdate,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    measurement = _get_measurement(db, measurement_id)
    _require_draft(measurement)
    line = _get_line(db, measurement, line_id)
    changes = _payload_dict(payload)
    if "pricing_mode" in changes and changes["pricing_mode"] not in {PRICING_MODE_BREAKDOWN, PRICING_MODE_COMPOSITE}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PRICING_MODE")
    if "review_status" in changes and changes["review_status"] not in {"required", "ready", "reviewed", "accepted"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_REVIEW_STATUS")
    for field, value in changes.items():
        if field in {"item_name", "feature", "unit"}:
            value = clean_text(value, 255 if field == "item_name" else 64 if field == "unit" else None)
        setattr(line, field, value)
    measurement.updated_by = current_user.id
    recalculate_measurement(db, measurement)
    write_measurement_event(db, measurement, line_id=line.id, event_type="line_updated", actor_user_id=current_user.id, message=f"\u66f4\u65b0\u6d4b\u7b97\u884c\uff1a{line.item_name}", payload=changes)
    db.commit()
    db.refresh(line)
    return api_ok({"line": serialize_measurement_line(line), "summary": serialize_measurement(measurement)})


@router.post("/admin/cost-measurements/{measurement_id}/lines/{line_id}/apply-quota/{quota_item_id}", summary="\u5957\u7528\u4f01\u4e1a\u5b9a\u989d\u4e3b\u9879")
async def apply_measurement_quota(
    measurement_id: int, line_id: int, quota_item_id: int,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    measurement = _get_measurement(db, measurement_id)
    _require_draft(measurement)
    line = _get_line(db, measurement, line_id)
    quota_item = db.query(EnterpriseQuotaItem).filter(EnterpriseQuotaItem.id == quota_item_id).first()
    if not quota_item or not quota_item.version or not quota_item.version.is_active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ACTIVE_QUOTA_ITEM_NOT_FOUND")
    apply_quota_item(line, quota_item, measurement)
    measurement.quota_version_id = quota_item.version_id
    measurement.updated_by = current_user.id
    recalculate_measurement(db, measurement)
    write_measurement_event(db, measurement, line_id=line.id, event_type="quota_applied", actor_user_id=current_user.id, message=f"\u5957\u7528\u4f01\u4e1a\u5b9a\u989d\uff1a{quota_item.quota_code or ''} {quota_item.item_name or ''}", payload={"quota_item_id": quota_item.id})
    db.commit()
    db.refresh(line)
    return api_ok({"line": serialize_measurement_line(line), "summary": serialize_measurement(measurement)})


@router.post("/admin/cost-measurements/{measurement_id}/recalculate", summary="\u7edf\u4e00\u91cd\u7b97\u6210\u672c\u6d4b\u7b97")
async def recalculate_cost_measurement(
    measurement_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    measurement = _get_measurement(db, measurement_id)
    _require_draft(measurement)
    measurement.updated_by = current_user.id
    recalculate_measurement(db, measurement)
    write_measurement_event(db, measurement, event_type="recalculated", actor_user_id=current_user.id, message="\u6267\u884c\u7edf\u4e00\u91cd\u7b97")
    db.commit()
    db.refresh(measurement)
    return api_ok(serialize_measurement(measurement, include_lines=True))


@router.post("/admin/cost-measurements/{measurement_id}/lock", summary="\u9501\u5b9a\u6210\u672c\u6d4b\u7b97\u7248\u672c")
async def lock_cost_measurement(
    measurement_id: int, payload: LockMeasurementIn,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_approve(current_user)
    measurement = _get_measurement(db, measurement_id)
    _require_draft(measurement)
    recalculate_measurement(db, measurement)
    note = clean_text(payload.note, 2000)
    if measurement.review_line_count and (not note or len(note) < 6):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MEASUREMENT_REVIEW_NOTE_REQUIRED")
    measurement.status = MEASUREMENT_STATUS_LOCKED
    measurement.locked_by = current_user.id
    measurement.locked_at = datetime.now()
    measurement.updated_by = current_user.id
    write_measurement_event(db, measurement, event_type="locked", actor_user_id=current_user.id, message=note or "\u6d4b\u7b97\u590d\u6838\u5b8c\u6210\u5e76\u9501\u5b9a", payload={"review_line_count": measurement.review_line_count})
    db.commit()
    db.refresh(measurement)
    return api_ok(serialize_measurement(measurement, include_lines=True, include_events=True), message="\u6210\u672c\u6d4b\u7b97\u5df2\u9501\u5b9a")


@router.get("/admin/cost-measurements/{measurement_id}/export", summary="\u5bfc\u51fa\u6210\u672c\u6d4b\u7b97 Excel")
async def export_cost_measurement(
    measurement_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_export(current_user)
    measurement = _get_measurement(db, measurement_id)
    payload = build_measurement_export(measurement)
    filename = f"{measurement.measurement_code}-{measurement.name}.xlsx"
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
