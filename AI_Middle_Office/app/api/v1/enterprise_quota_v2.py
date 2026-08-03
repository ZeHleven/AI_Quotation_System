from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.enterprise_quota_v2 import (
    EnterpriseQuotaActivateIn,
    EnterpriseQuotaRecalculateIn,
    EnterpriseQuotaResourceCreateIn,
    EnterpriseQuotaResourceUpdateIn,
    EnterpriseQuotaVersionCloneIn,
)
from app.services.cost_audit import record_cost_audit
from app.services.cost_items import (
    require_cost_db_access,
    require_cost_db_approver,
    require_cost_db_editor,
)
from app.services.enterprise_quota_v2_parser import EnterpriseQuotaV2ParseError
from app.services.enterprise_quota_v2_workbench import (
    EnterpriseQuotaV2WorkbenchError,
    activate_version,
    clone_version_to_draft,
    create_resource,
    get_version,
    import_v2_workbook_as_draft,
    list_sheet_rows,
    list_version_events,
    list_versions,
    preview_v2_workbook,
    recalculate_draft_version,
    serialize_version,
    update_resource,
)


router = APIRouter()
MAX_WORKBOOK_BYTES = 20 * 1024 * 1024


def _ensure_enabled() -> None:
    if not settings.feature_cost_db:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _payload(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _raise_workbench_error(exc: EnterpriseQuotaV2WorkbenchError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message, "details": exc.details},
    ) from exc


async def _read_workbook(file: UploadFile) -> bytes:
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="企业定额 2.0 仅支持 .xlsx/.xlsm")
    content = await file.read(MAX_WORKBOOK_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_WORKBOOK_BYTES:
        raise HTTPException(status_code=413, detail="企业定额文件不能超过 20MB")
    return content


@router.post("/admin/enterprise-quota-v2/import/preview", summary="预览企业定额 2.0 工作簿")
async def preview_enterprise_quota_v2(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    _ensure_enabled()
    require_cost_db_access(current_user)
    content = await _read_workbook(file)
    try:
        return api_ok(preview_v2_workbook(content, filename=file.filename or "enterprise-quota-v2.xlsx"))
    except EnterpriseQuotaV2ParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/enterprise-quota-v2/import", summary="导入企业定额 2.0 为草稿版本")
async def import_enterprise_quota_v2(
    request: Request,
    file: UploadFile = File(...),
    version_code: str | None = Form(default=None),
    version_name: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_editor(current_user)
    content = await _read_workbook(file)
    try:
        version = import_v2_workbook_as_draft(
            db,
            content,
            filename=file.filename or "enterprise-quota-v2.xlsx",
            actor_id=current_user.id,
            version_code=version_code,
            version_name=version_name,
        )
        record_cost_audit(
            db,
            user=current_user,
            action="enterprise_quota_v2.import",
            resource_type="enterprise_quota_version",
            resource_id=version.id,
            result_count=1,
            status_value="success",
            filters={"version_code": version.version_code, "source_filename": version.source_filename},
            request=request,
        )
        db.commit()
        db.refresh(version)
        return api_ok(serialize_version(db, version))
    except EnterpriseQuotaV2WorkbenchError as exc:
        db.rollback()
        _raise_workbench_error(exc)
    except EnterpriseQuotaV2ParseError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/admin/enterprise-quota-v2/versions", summary="查询企业定额版本中心")
async def list_enterprise_quota_v2_versions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_access(current_user)
    return api_ok(list_versions(db))


@router.get("/admin/enterprise-quota-v2/versions/{version_id}", summary="查询企业定额版本详情")
async def get_enterprise_quota_v2_version(
    version_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_access(current_user)
    try:
        return api_ok(serialize_version(db, get_version(db, version_id)))
    except EnterpriseQuotaV2WorkbenchError as exc:
        _raise_workbench_error(exc)


@router.get("/admin/enterprise-quota-v2/versions/{version_id}/rows", summary="查询企业定额 Excel 工作表行")
async def list_enterprise_quota_v2_rows(
    version_id: int,
    sheet: str = Query("enterprise"),
    keyword: str | None = Query(default=None, max_length=128),
    major_section_id: int | None = Query(default=None, ge=1),
    chapter_id: int | None = Query(default=None, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(120, ge=20, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_access(current_user)
    try:
        return api_ok(
            list_sheet_rows(
                db,
                version_id,
                sheet_key=sheet,
                keyword=keyword,
                major_section_id=major_section_id,
                chapter_id=chapter_id,
                page=page,
                page_size=page_size,
            )
        )
    except EnterpriseQuotaV2WorkbenchError as exc:
        _raise_workbench_error(exc)


@router.patch(
    "/admin/enterprise-quota-v2/versions/{version_id}/resources/{resource_id}",
    summary="更新人工或材料价格并重算企业定额",
)
async def update_enterprise_quota_v2_resource(
    version_id: int,
    resource_id: int,
    payload: EnterpriseQuotaResourceUpdateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_editor(current_user)
    data = _payload(payload)
    expected_revision = data.pop("expected_revision", None)
    reason = data.pop("reason", None)
    try:
        result = update_resource(
            db,
            version_id,
            resource_id,
            data,
            actor_id=current_user.id,
            expected_revision=expected_revision,
            reason=reason,
        )
        record_cost_audit(
            db,
            user=current_user,
            action="enterprise_quota_v2.resource_update",
            resource_type="enterprise_cost_resource",
            resource_id=resource_id,
            status_value="success",
            filters={"version_id": version_id},
            request=request,
        )
        db.commit()
        return api_ok(result)
    except EnterpriseQuotaV2WorkbenchError as exc:
        db.rollback()
        _raise_workbench_error(exc)
    except Exception:
        db.rollback()
        raise


@router.post(
    "/admin/enterprise-quota-v2/versions/{version_id}/resources",
    summary="向人工或材料价格库新增记录并重算",
)
async def create_enterprise_quota_v2_resource(
    version_id: int,
    payload: EnterpriseQuotaResourceCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_editor(current_user)
    data = _payload(payload)
    expected_revision = data.pop("expected_revision", None)
    reason = data.pop("reason", None)
    try:
        result = create_resource(
            db,
            version_id,
            data,
            actor_id=current_user.id,
            expected_revision=expected_revision,
            reason=reason,
        )
        record_cost_audit(
            db,
            user=current_user,
            action="enterprise_quota_v2.resource_create",
            resource_type="enterprise_cost_resource",
            resource_id=result["resource"]["id"],
            status_value="success",
            filters={"version_id": version_id, "library_kind": data.get("library_kind")},
            request=request,
        )
        db.commit()
        return api_ok(result)
    except EnterpriseQuotaV2WorkbenchError as exc:
        db.rollback()
        _raise_workbench_error(exc)
    except Exception:
        db.rollback()
        raise


@router.post("/admin/enterprise-quota-v2/versions/{version_id}/clone", summary="克隆企业定额版本为可编辑草稿")
async def clone_enterprise_quota_v2_version(
    version_id: int,
    payload: EnterpriseQuotaVersionCloneIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_editor(current_user)
    data = _payload(payload)
    try:
        version = clone_version_to_draft(
            db,
            version_id,
            actor_id=current_user.id,
            version_code=data.get("version_code"),
            version_name=data.get("version_name"),
            reason=data.get("reason"),
        )
        record_cost_audit(
            db,
            user=current_user,
            action="enterprise_quota_v2.clone",
            resource_type="enterprise_quota_version",
            resource_id=version.id,
            status_value="success",
            filters={"source_version_id": version_id},
            request=request,
        )
        db.commit()
        return api_ok(serialize_version(db, version))
    except EnterpriseQuotaV2WorkbenchError as exc:
        db.rollback()
        _raise_workbench_error(exc)
    except Exception:
        db.rollback()
        raise


@router.post("/admin/enterprise-quota-v2/versions/{version_id}/recalculate", summary="全量重算企业定额公式")
async def recalculate_enterprise_quota_v2_version(
    version_id: int,
    payload: EnterpriseQuotaRecalculateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_editor(current_user)
    data = _payload(payload)
    try:
        result = recalculate_draft_version(
            db,
            version_id,
            actor_id=current_user.id,
            expected_revision=data.get("expected_revision"),
            reason=data.get("reason"),
        )
        record_cost_audit(
            db,
            user=current_user,
            action="enterprise_quota_v2.recalculate",
            resource_type="enterprise_quota_version",
            resource_id=version_id,
            status_value="success",
            request=request,
        )
        db.commit()
        return api_ok(result)
    except EnterpriseQuotaV2WorkbenchError as exc:
        db.rollback()
        _raise_workbench_error(exc)
    except Exception:
        db.rollback()
        raise


@router.post("/admin/enterprise-quota-v2/versions/{version_id}/activate", summary="受控启用企业定额版本")
async def activate_enterprise_quota_v2_version(
    version_id: int,
    payload: EnterpriseQuotaActivateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_approver(current_user)
    data = _payload(payload)
    try:
        version = activate_version(
            db,
            version_id,
            actor_id=current_user.id,
            expected_revision=data.get("expected_revision"),
            reason=data["reason"],
            acknowledge_warnings=bool(data.get("acknowledge_warnings")),
        )
        record_cost_audit(
            db,
            user=current_user,
            action="enterprise_quota_v2.activate",
            resource_type="enterprise_quota_version",
            resource_id=version.id,
            status_value="success",
            filters={"version_code": version.version_code},
            request=request,
        )
        db.commit()
        return api_ok(serialize_version(db, version))
    except EnterpriseQuotaV2WorkbenchError as exc:
        db.rollback()
        _raise_workbench_error(exc)
    except Exception:
        db.rollback()
        raise


@router.get("/admin/enterprise-quota-v2/versions/{version_id}/events", summary="查询企业定额版本操作记录")
async def list_enterprise_quota_v2_events(
    version_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    require_cost_db_access(current_user)
    try:
        rows, total = list_version_events(
            db,
            version_id,
            page=page,
            page_size=page_size,
        )
        return api_page(rows, total=total, page=page, page_size=page_size)
    except EnterpriseQuotaV2WorkbenchError as exc:
        _raise_workbench_error(exc)
