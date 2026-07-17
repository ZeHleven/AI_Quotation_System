from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.budget_project import (
    BUDGET_IMPORT_STATUS_ACTIVE,
    BudgetProjectImportBatch,
    BudgetProjectImportRevision,
    BudgetProjectProfile,
    BudgetProjectStandardRow,
)
from app.models.project_progress import Project
from app.models.user import User
from app.schemas.budget_project import (
    BudgetImportRemap,
    BudgetProjectArchive,
    BudgetProjectCreate,
    BudgetProjectUpdate,
    model_payload,
)
from app.services.budget_projects import (
    BUDGET_WORKSPACE_ACTIVE,
    MAX_IMPORT_BYTES,
    accessible_budget_profile_query,
    activate_import_batch,
    apply_import_sheet_mappings,
    archive_budget_project,
    confirm_import_batch,
    create_budget_project,
    create_import_batch,
    get_accessible_import_batch,
    get_budget_profile,
    get_import_batch,
    get_import_revision,
    require_budget_project_access,
    serialize_budget_project,
    serialize_import_batch,
    serialize_import_revision,
    serialize_standard_row,
    update_budget_project,
)
from app.services.requirement_standardizer import RequirementStandardizationError


router = APIRouter()


def _ensure_feature_enabled() -> None:
    if not settings.feature_budget_projects:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NOT_FOUND")


def _commit_and_reload_batch(db: Session, batch: BudgetProjectImportBatch) -> BudgetProjectImportBatch:
    identifier = batch.id
    db.commit()
    db.expire_all()
    return db.query(BudgetProjectImportBatch).filter(BudgetProjectImportBatch.id == identifier).one()


# Keep these static routes before /{project_id}; otherwise "imports" may be
# interpreted as a project id by Starlette's first matching route.
@router.get("/admin/budget-projects/imports/{batch_identifier}", summary="Get budget import batch")
async def get_budget_import_batch(
    batch_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    return api_ok(
        serialize_import_batch(
            batch,
            include_preview=True,
            profile=profile,
            current_user=current_user,
        )
    )


@router.get("/admin/budget-projects/imports/{batch_identifier}/revisions", summary="List budget import revisions")
async def list_budget_import_revisions(
    batch_identifier: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    query = db.query(BudgetProjectImportRevision).filter(
        BudgetProjectImportRevision.batch_id == batch.id
    )
    total = query.count()
    revisions = (
        query.order_by(BudgetProjectImportRevision.revision_number.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_import_revision(revision) for revision in revisions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/admin/budget-projects/imports/{batch_identifier}/revisions/{revision_identifier}",
    summary="Get immutable budget import revision",
)
async def get_budget_import_revision(
    batch_identifier: str,
    revision_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    revision = get_import_revision(db, batch, revision_identifier)
    return api_ok(serialize_import_revision(revision, include_snapshot=True))


@router.get("/admin/budget-projects/imports/{batch_identifier}/rows", summary="List budget import rows")
async def list_budget_import_rows(
    batch_identifier: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    source_sheet: Optional[str] = None,
    row_type: Optional[str] = None,
    quantity_status: Optional[str] = None,
    standard_items_only: bool = False,
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    query = db.query(BudgetProjectStandardRow).filter(BudgetProjectStandardRow.batch_id == batch.id)
    if source_sheet:
        query = query.filter(BudgetProjectStandardRow.source_sheet == source_sheet.strip())
    if row_type:
        query = query.filter(BudgetProjectStandardRow.row_type == row_type.strip())
    if quantity_status:
        query = query.filter(BudgetProjectStandardRow.quantity_status == quantity_status.strip())
    if standard_items_only:
        query = query.filter(BudgetProjectStandardRow.is_standard_item.is_(True))
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                BudgetProjectStandardRow.item_name.like(pattern),
                BudgetProjectStandardRow.spec.like(pattern),
                BudgetProjectStandardRow.raw_text.like(pattern),
            )
        )
    total = query.count()
    rows = (
        query.order_by(BudgetProjectStandardRow.sort_order.asc(), BudgetProjectStandardRow.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_standard_row(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        batch=serialize_import_batch(batch, profile=profile, current_user=current_user),
    )


@router.post("/admin/budget-projects/imports/{batch_identifier}/remap", summary="Remap budget import columns")
async def remap_budget_import(
    batch_identifier: str,
    payload: BudgetImportRemap,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    batch = apply_import_sheet_mappings(
        db,
        profile,
        batch,
        [model_payload(item) for item in payload.sheet_mappings],
        current_user,
        expected_remap_revision=(
            payload.expected_remap_revision
            if payload.expected_remap_revision is not None
            else int(batch.remap_revision or 0)
        ),
    )
    batch = _commit_and_reload_batch(db, batch)
    return api_ok(
        serialize_import_batch(
            batch,
            include_preview=True,
            profile=profile,
            current_user=current_user,
        )
    )


@router.post("/admin/budget-projects/imports/{batch_identifier}/confirm", summary="Confirm budget import revision")
async def confirm_budget_import(
    batch_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    batch = confirm_import_batch(db, profile, batch, current_user)
    batch = _commit_and_reload_batch(db, batch)
    return api_ok(
        serialize_import_batch(batch, profile=profile, current_user=current_user)
    )


@router.post("/admin/budget-projects/imports/{batch_identifier}/activate", summary="Activate confirmed budget import")
async def activate_budget_import(
    batch_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile, batch = get_accessible_import_batch(db, batch_identifier, current_user)
    batch = activate_import_batch(db, profile, batch, current_user)
    batch = _commit_and_reload_batch(db, batch)
    return api_ok(
        serialize_import_batch(batch, profile=profile, current_user=current_user)
    )


@router.get("/admin/budget-projects", summary="List budget projects")
async def list_budget_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workspace_status: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    query = accessible_budget_profile_query(db, current_user)
    if workspace_status:
        statuses = [value.strip() for value in workspace_status.split(",") if value.strip()]
        invalid = [value for value in statuses if value not in {"active", "archived"}]
        if invalid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_BUDGET_PROJECT_STATUS")
        if statuses:
            query = query.filter(BudgetProjectProfile.workspace_status.in_(statuses))
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                Project.project_code.like(pattern),
                Project.name.like(pattern),
                Project.client_name.like(pattern),
                Project.address.like(pattern),
            )
        )
    total = query.count()
    profiles = (
        query.order_by(BudgetProjectProfile.updated_at.desc(), BudgetProjectProfile.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_budget_project(db, profile, current_user) for profile in profiles],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/admin/budget-projects", summary="Create budget project")
async def create_budget_project_endpoint(
    payload: BudgetProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = create_budget_project(db, model_payload(payload), current_user)
    db.commit()
    db.expire_all()
    profile = db.query(BudgetProjectProfile).filter(BudgetProjectProfile.id == profile.id).one()
    return api_ok(serialize_budget_project(db, profile, current_user))


@router.get("/admin/budget-projects/{project_id}/imports", summary="List project budget imports")
async def list_project_budget_imports(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = get_budget_profile(db, project_id, current_user)
    query = db.query(BudgetProjectImportBatch).filter(BudgetProjectImportBatch.project_id == profile.project_id)
    total = query.count()
    batches = (
        query.order_by(BudgetProjectImportBatch.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [
            serialize_import_batch(
                batch,
                profile=profile,
                current_user=current_user,
            )
            for batch in batches
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/admin/budget-projects/{project_id}/imports", summary="Upload project budget workbook")
async def upload_project_budget_import(
    project_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = get_budget_profile(db, project_id, current_user)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        batch = create_import_batch(
            db,
            profile,
            filename=file.filename or "requirements.xlsx",
            content=content,
            current_user=current_user,
        )
    except RequirementStandardizationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    batch = _commit_and_reload_batch(db, batch)
    return api_ok(
        serialize_import_batch(
            batch,
            include_preview=True,
            profile=profile,
            current_user=current_user,
        )
    )


@router.get("/admin/budget-projects/{project_id}", summary="Get budget project")
async def get_budget_project_endpoint(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = get_budget_profile(db, project_id, current_user)
    data = serialize_budget_project(db, profile, current_user)
    latest = (
        db.query(BudgetProjectImportBatch)
        .filter(BudgetProjectImportBatch.project_id == project_id)
        .order_by(BudgetProjectImportBatch.id.desc())
        .first()
    )
    data["latest_import"] = (
        serialize_import_batch(
            latest,
            include_preview=True,
            profile=profile,
            current_user=current_user,
        )
        if latest
        else None
    )
    return api_ok(data)


@router.patch("/admin/budget-projects/{project_id}", summary="Update budget project")
async def update_budget_project_endpoint(
    project_id: int,
    payload: BudgetProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = get_budget_profile(db, project_id, current_user)
    profile = update_budget_project(db, profile, model_payload(payload, exclude_unset=True), current_user)
    db.commit()
    db.expire_all()
    profile = db.query(BudgetProjectProfile).filter(BudgetProjectProfile.id == profile.id).one()
    return api_ok(serialize_budget_project(db, profile, current_user))


@router.patch("/admin/budget-projects/{project_id}/archive", summary="Archive budget project")
async def archive_budget_project_endpoint(
    project_id: int,
    payload: BudgetProjectArchive | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = get_budget_profile(db, project_id, current_user)
    profile = archive_budget_project(db, profile, payload.reason if payload else None, current_user)
    db.commit()
    db.expire_all()
    profile = db.query(BudgetProjectProfile).filter(BudgetProjectProfile.id == profile.id).one()
    return api_ok(serialize_budget_project(db, profile, current_user))


@router.get("/admin/budget-projects/{project_id}/active-import", summary="Get active confirmed budget import")
async def get_active_project_budget_import(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = get_budget_profile(db, project_id, current_user)
    if not profile.active_import_batch_id and not profile.active_import_revision_id:
        return api_ok(None)
    batch = (
        db.query(BudgetProjectImportBatch)
        .filter(BudgetProjectImportBatch.id == profile.active_import_batch_id)
        .first()
    )
    revision = (
        db.query(BudgetProjectImportRevision)
        .filter(BudgetProjectImportRevision.id == profile.active_import_revision_id)
        .first()
    )
    if (
        not batch
        or not revision
        or batch.project_id != profile.project_id
        or revision.batch_id != batch.id
        or batch.status != BUDGET_IMPORT_STATUS_ACTIVE
        or batch.confirmed_revision_id != revision.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="BUDGET_ACTIVE_IMPORT_POINTER_INVALID",
        )
    data = serialize_import_batch(
        batch,
        include_preview=False,
        profile=profile,
        current_user=current_user,
    )
    data["active_revision"] = serialize_import_revision(revision, include_snapshot=True)
    return api_ok(data)
