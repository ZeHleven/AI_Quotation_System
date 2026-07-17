from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.project_cost_import import ProjectCostImportBatch, ProjectCostPriceCandidate
from app.models.user import User
from app.services.enterprise_quota_master import serialize_enterprise_quota_version
from app.services.project_cost_import import (
    ProjectCostImportError,
    create_draft_quota_version_from_batch,
    create_project_cost_import_batch,
    list_price_observations,
    list_project_cost_candidates,
    list_project_cost_import_batches,
    review_project_cost_candidates,
    serialize_price_observation,
    serialize_project_cost_candidate,
    serialize_project_cost_import_batch,
    update_project_cost_candidate,
)
from app.services.rbac import has_any_role


router = APIRouter()


class CandidateReviewIn(BaseModel):
    candidate_ids: list[int] = Field(..., min_length=1, max_length=500)
    action: str
    note: Optional[str] = Field(None, max_length=2000)


class CandidateUpdateIn(BaseModel):
    normalized_item_name: Optional[str] = Field(None, max_length=255)
    brand: Optional[str] = Field(None, max_length=255)
    spec: Optional[str] = Field(None, max_length=500)
    unit: Optional[str] = Field(None, max_length=64)
    resource_type: Optional[str] = Field(None, max_length=32)
    recommended_price: Optional[float] = Field(None, gt=0)
    matched_resource_id: Optional[int] = Field(None, ge=0)
    review_note: Optional[str] = Field(None, max_length=2000)


class DraftVersionIn(BaseModel):
    version_name: Optional[str] = Field(None, max_length=255)


def _ensure_feature() -> None:
    if not settings.feature_cost_db or not settings.feature_project_cost_import:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _require_view(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_viewer", "cost_editor", "cost_approver", "cost_exporter"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _require_edit(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_editor", "cost_approver"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _require_approve(user: User) -> None:
    if not has_any_role(user, {"system_admin", "admin", "cost_approver"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _batch(db: Session, batch_id: int) -> ProjectCostImportBatch:
    row = db.query(ProjectCostImportBatch).filter(ProjectCostImportBatch.id == batch_id).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_COST_IMPORT_BATCH_NOT_FOUND")
    return row


def _candidate(db: Session, candidate_id: int) -> ProjectCostPriceCandidate:
    row = db.query(ProjectCostPriceCandidate).filter(ProjectCostPriceCandidate.id == candidate_id).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_COST_CANDIDATE_NOT_FOUND")
    return row


def _payload(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _raise_import_error(exc: ProjectCostImportError) -> None:
    conflict_codes = {"IMPORT_BATCH_ALREADY_CREATED_DRAFT", "NO_APPROVED_CANDIDATES"}
    code = str(exc)
    http_status = status.HTTP_409_CONFLICT if code in conflict_codes else status.HTTP_422_UNPROCESSABLE_ENTITY
    raise HTTPException(status_code=http_status, detail=code) from exc


@router.post("/admin/project-cost-imports", summary="导入项目采购资料并生成价格候选")
async def create_project_cost_import(
    files: list[UploadFile] = File(...),
    project_name: str = Form(...),
    source_name: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    payloads = [(file.filename or "purchase.xlsx", await file.read()) for file in files]
    try:
        batch = create_project_cost_import_batch(
            db,
            project_name=project_name,
            source_name=source_name,
            files=payloads,
            actor_user_id=current_user.id,
        )
        db.commit()
        db.refresh(batch)
    except ProjectCostImportError as exc:
        db.rollback()
        _raise_import_error(exc)
    return api_ok(serialize_project_cost_import_batch(batch), message="项目采购资料已解析，请审核价格候选")


@router.get("/admin/project-cost-imports", summary="查询项目采购入库批次")
async def get_project_cost_imports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_view(current_user)
    rows, total = list_project_cost_import_batches(db, page=page, page_size=page_size, status=status_filter, keyword=keyword)
    return api_page([serialize_project_cost_import_batch(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/admin/project-cost-imports/{batch_id}", summary="查询项目采购入库批次详情")
async def get_project_cost_import(
    batch_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_view(current_user)
    return api_ok(serialize_project_cost_import_batch(_batch(db, batch_id)))


@router.get("/admin/project-cost-imports/{batch_id}/candidates", summary="查询项目采购价格候选")
async def get_project_cost_candidates(
    batch_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    risk_level: Optional[str] = None,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_view(current_user)
    _batch(db, batch_id)
    rows, total = list_project_cost_candidates(
        db,
        batch_id=batch_id,
        page=page,
        page_size=page_size,
        status=status_filter,
        risk_level=risk_level,
        keyword=keyword,
    )
    return api_page([serialize_project_cost_candidate(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/admin/project-cost-imports/{batch_id}/observations", summary="查询项目采购价格原始观察")
async def get_project_cost_observations(
    batch_id: int,
    candidate_key: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_view(current_user)
    _batch(db, batch_id)
    rows, total = list_price_observations(db, batch_id=batch_id, candidate_key=candidate_key, page=page, page_size=page_size)
    return api_page([serialize_price_observation(row) for row in rows], total=total, page=page, page_size=page_size)


@router.patch("/admin/project-cost-imports/candidates/{candidate_id}", summary="修正项目采购价格候选")
async def patch_project_cost_candidate(
    candidate_id: int,
    payload: CandidateUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_edit(current_user)
    candidate = _candidate(db, candidate_id)
    try:
        update_project_cost_candidate(db, candidate, _payload(payload))
        db.commit()
        db.refresh(candidate)
    except ProjectCostImportError as exc:
        db.rollback()
        _raise_import_error(exc)
    return api_ok(serialize_project_cost_candidate(candidate))


@router.post("/admin/project-cost-imports/{batch_id}/review", summary="批量审核项目采购价格候选")
async def review_project_cost_import_candidates(
    batch_id: int,
    payload: CandidateReviewIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_approve(current_user)
    batch = _batch(db, batch_id)
    try:
        rows = review_project_cost_candidates(
            db,
            batch=batch,
            candidate_ids=payload.candidate_ids,
            action=payload.action,
            note=payload.note,
            actor_user_id=current_user.id,
        )
        db.commit()
        db.refresh(batch)
    except ProjectCostImportError as exc:
        db.rollback()
        _raise_import_error(exc)
    return api_ok({"batch": serialize_project_cost_import_batch(batch), "candidates": [serialize_project_cost_candidate(row) for row in rows]})


@router.post("/admin/project-cost-imports/{batch_id}/draft-version", summary="由已审核采购价生成企业定额草稿版本")
async def create_project_cost_import_draft_version(
    batch_id: int,
    payload: DraftVersionIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature()
    _require_approve(current_user)
    batch = _batch(db, batch_id)
    try:
        version = create_draft_quota_version_from_batch(
            db,
            batch=batch,
            actor_user_id=current_user.id,
            version_name=payload.version_name,
        )
        db.commit()
        db.refresh(version)
        db.refresh(batch)
    except ProjectCostImportError as exc:
        db.rollback()
        _raise_import_error(exc)
    return api_ok({"batch": serialize_project_cost_import_batch(batch), "draft_version": serialize_enterprise_quota_version(version)}, message="企业定额草稿版本已生成，active 主库未变更")
