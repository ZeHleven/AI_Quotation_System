from __future__ import annotations

import asyncio
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.budget_pricing import (
    BudgetProjectPricingEvent,
    BudgetProjectPricingMatchCandidate,
    BudgetProjectPricingRun,
    BudgetProjectPricingRunLine,
)
from app.models.budget_pricing_draft import (
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftLine,
)
from app.models.user import User
from app.schemas.budget_pricing import (
    BudgetProjectQuotaEnterpriseSyncIn,
    BudgetProjectQuotaMaterializeIn,
    BudgetProjectQuotaResourceCreateIn,
    BudgetProjectQuotaResourceDeleteIn,
    BudgetProjectQuotaResourceUpdateIn,
    BudgetPricingDraftCreate,
    BudgetPricingDraftAccountQuotaSyncConfirmIn,
    BudgetPricingDraftAccountQuotaSyncPreviewIn,
    BudgetPricingDraftQuoteJobCreate,
    BudgetPricingDraftLineAiEstimateIn,
    BudgetPricingDraftLineConstructionNotePatch,
    BudgetPricingDraftLinePatch,
    BudgetPricingDraftTotalsConfigPatch,
    BudgetPricingRunCreate,
)
from app.services.budget_pricing import (
    BudgetPricingError,
    activate_budget_pricing_run,
    archive_budget_pricing_run,
    build_budget_pricing_readiness,
    create_budget_pricing_run,
    get_budget_pricing_line,
    get_budget_pricing_run,
    serialize_budget_pricing_candidate,
    serialize_budget_pricing_event,
    serialize_budget_pricing_line,
    serialize_budget_pricing_run,
)
from app.services.budget_pricing_drafts import (
    capture_budget_pricing_run_draft_snapshot,
    create_or_rebuild_budget_pricing_draft,
    get_current_budget_pricing_draft,
    patch_budget_pricing_draft_line,
    patch_budget_pricing_draft_line_construction_note,
    refresh_budget_pricing_draft_summary,
    restore_budget_pricing_draft_from_run_snapshot,
    serialize_budget_pricing_draft,
    serialize_budget_pricing_draft_line,
    update_budget_pricing_draft_totals_config,
)
from app.services.budget_pricing_ai_estimates import estimate_budget_pricing_draft_line
from app.services.budget_pricing_original_export import (
    prepare_budget_pricing_original_export,
    render_budget_pricing_original_export,
)
from app.services.budget_pricing_resource_details import (
    build_budget_procurement_statistics,
    build_budget_pricing_resource_details,
)
from app.services.budget_pricing_statistics_export import (
    render_budget_pricing_statistics_export,
)
from app.services.budget_project_quotas import (
    create_project_quota_resource,
    delete_project_quota_resource,
    materialize_project_quota,
    serialize_project_quota,
    sync_project_quota_to_enterprise,
    update_project_quota_resource,
)
from app.services.budget_pricing_draft_quote_jobs import (
    cancel_budget_pricing_draft_quote_job,
    create_budget_pricing_draft_quote_job,
    get_budget_pricing_draft_quote_job,
    get_current_budget_pricing_draft_quote_job,
    run_budget_pricing_draft_quote_job_sync,
    serialize_budget_pricing_draft_quote_job,
)
from app.services.account_quota_draft_sync import (
    confirm_account_quota_sync,
    preview_account_quota_sync,
)
from app.services.budget_projects import get_budget_profile
from app.services.file_storage import get_object_bytes
from app.services.rbac import (
    can_create_budget_pricing,
    has_any_role,
    require_budget_pricing_create,
    require_budget_pricing_view,
)


router = APIRouter()


PricingMatchStatusFilter = Literal[
    "auto_matched",
    "manual_matched",
    "ambiguous",
    "unmatched",
    "unit_conflict",
]
PricingLineStatusFilter = Literal[
    "priced",
    "quantity_unresolved",
    "missing_unit_price",
    "pending_match",
    "unit_conflict",
    "numeric_overflow",
]


def _ensure_feature_enabled() -> None:
    if not settings.feature_budget_projects or not settings.feature_budget_pricing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BUDGET_PRICING_DISABLED")


def _ensure_draft_feature_enabled() -> None:
    _ensure_feature_enabled()
    if not settings.feature_budget_pricing_drafts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BUDGET_PRICING_DRAFTS_DISABLED",
        )


def _ensure_account_quota_sync_feature_enabled() -> None:
    _ensure_draft_feature_enabled()
    if not settings.feature_account_quotas or not settings.feature_account_quota_draft_sync:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _ensure_ai_estimate_feature_enabled() -> None:
    _ensure_draft_feature_enabled()
    if not settings.feature_budget_pricing_ai_estimate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _http_error(exc: BudgetPricingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _accessible_profile(db: Session, project_id: int, current_user: User):
    require_budget_pricing_view(current_user)
    return get_budget_profile(db, project_id, current_user)


def _accessible_run(db: Session, run_identifier: str, current_user: User) -> BudgetProjectPricingRun:
    require_budget_pricing_view(current_user)
    try:
        run = get_budget_pricing_run(db, run_identifier)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    get_budget_profile(db, run.project_id, current_user)
    return run


def _line_keyword_predicate(pattern: str):
    """Search immutable quota evidence without joining duplicate run lines."""

    candidate_snapshot_match = exists().where(
        and_(
            BudgetProjectPricingMatchCandidate.run_line_id == BudgetProjectPricingRunLine.id,
            BudgetProjectPricingMatchCandidate.quota_item_snapshot_json.like(pattern),
        )
    )
    return or_(
        BudgetProjectPricingRunLine.item_name.like(pattern),
        BudgetProjectPricingRunLine.spec.like(pattern),
        BudgetProjectPricingRunLine.source_sheet.like(pattern),
        BudgetProjectPricingRunLine.source_row_key.like(pattern),
        BudgetProjectPricingRunLine.selected_quota_item_snapshot_json.like(pattern),
        candidate_snapshot_match,
    )


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/current",
    summary="Get the account-scoped mutable pricing draft",
)
async def get_current_pricing_draft_endpoint(
    project_id: int,
    pricing_mode: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        draft = get_current_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
        if draft is not None:
            refresh_budget_pricing_draft_summary(db, draft)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    return api_ok(serialize_budget_pricing_draft(draft) if draft else None)


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/original-format-export",
    summary="Export the current pricing draft into the original workbook format",
)
async def export_original_format_pricing_draft_endpoint(
    project_id: int,
    pricing_mode: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        source = prepare_budget_pricing_original_export(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
        source_content = await asyncio.to_thread(
            get_object_bytes,
            source.source_file.object_name,
            source.source_file.bucket,
        )
        result = render_budget_pricing_original_export(
            source_content,
            source,
            project_name=getattr(profile.project, "name", None) or f"项目{project_id}",
        )
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"BUDGET_PRICING_ORIGINAL_EXPORT_FAILED: {exc}",
        ) from exc
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename)}",
        },
    )


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft",
    summary="Create or rebuild the account-scoped dual-mode pricing draft",
)
async def create_pricing_draft_endpoint(
    project_id: int,
    payload: BudgetPricingDraftCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        draft = create_or_rebuild_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=payload.pricing_mode,
            source_import_batch_id=payload.source_import_batch_id,
            source_import_revision_id=payload.source_import_revision_id,
            expected_active_quota_version_id=payload.expected_active_quota_version_id,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
        )
        draft_id = draft.id
        db.commit()
        db.expire_all()
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == draft_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_budget_pricing_draft(draft), message="计价草稿已保存")


@router.patch(
    "/admin/budget-projects/{project_id}/pricing-draft/totals-config",
    summary="Update editable draft totals/rate settings",
)
async def patch_pricing_draft_totals_config_endpoint(
    project_id: int,
    payload: BudgetPricingDraftTotalsConfigPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        draft = update_budget_pricing_draft_totals_config(
            db,
            profile,
            current_user,
            pricing_mode=payload.pricing_mode,
            expected_revision=payload.expected_revision,
            config_patch=payload.model_dump(mode="json", exclude={"pricing_mode", "expected_revision", "reason"}, exclude_none=True),
            reason=payload.reason,
        )
        draft_id = draft.id
        db.commit()
        db.expire_all()
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == draft_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_budget_pricing_draft(draft), message="统计费率已更新")


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/account-quota-sync/preview",
    summary="Preview syncing manually adjusted pricing-draft lines to account quotas",
)
async def preview_account_quota_sync_endpoint(
    project_id: int,
    payload: BudgetPricingDraftAccountQuotaSyncPreviewIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_account_quota_sync_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        result = preview_account_quota_sync(db, profile, current_user, payload)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    return api_ok(result)


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/account-quota-sync/confirm",
    summary="Confirm syncing pricing-draft lines to current-account quota drafts",
)
async def confirm_account_quota_sync_endpoint(
    project_id: int,
    payload: BudgetPricingDraftAccountQuotaSyncConfirmIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ensure_account_quota_sync_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        result = confirm_account_quota_sync(db, profile, current_user, payload)
        db.commit()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(result, message="已同步到账户定额草稿")


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/lines",
    summary="List account-scoped mutable pricing draft lines",
)
async def list_pricing_draft_lines_endpoint(
    project_id: int,
    pricing_mode: Optional[str] = Query(default=None),
    keyword: Optional[str] = None,
    match_status: Optional[str] = None,
    pricing_status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        draft = get_current_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BUDGET_PRICING_DRAFT_NOT_FOUND"},
        )
    query = db.query(BudgetProjectPricingDraftLine).filter(
        BudgetProjectPricingDraftLine.draft_id == draft.id
    )
    keyword_text = (keyword or "").strip()[:128]
    if keyword_text:
        pattern = f"%{keyword_text}%"
        query = query.filter(
            or_(
                BudgetProjectPricingDraftLine.item_name.like(pattern),
                BudgetProjectPricingDraftLine.spec.like(pattern),
                BudgetProjectPricingDraftLine.source_sheet.like(pattern),
                BudgetProjectPricingDraftLine.source_row_key.like(pattern),
            )
        )
    if match_status:
        query = query.filter(BudgetProjectPricingDraftLine.match_status == match_status.strip()[:32])
    if pricing_status:
        query = query.filter(BudgetProjectPricingDraftLine.pricing_status == pricing_status.strip()[:32])
    total = query.count()
    lines = (
        query.order_by(
            BudgetProjectPricingDraftLine.source_sort_order.asc(),
            BudgetProjectPricingDraftLine.id.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_budget_pricing_draft_line(line) for line in lines],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/resource-details",
    summary="List resource details behind one pricing-draft total",
)
async def get_pricing_draft_resource_details_endpoint(
    project_id: int,
    bucket: Literal["labor", "main_material", "auxiliary_material"] = Query(...),
    pricing_mode: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        draft = get_current_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
        if draft is None:
            raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
        result = build_budget_pricing_resource_details(db, draft, bucket=bucket)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    return api_ok(result)


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/statistics-export",
    summary="Export pricing-draft statistics and resource details to Excel",
)
async def export_pricing_draft_statistics_endpoint(
    project_id: int,
    pricing_mode: Optional[str] = Query(default=None),
    sections: Optional[str] = Query(
        default=None,
        description="Comma-separated export sections: summary, main_material, auxiliary_material, labor",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        draft = get_current_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
        if draft is None:
            raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
        result = render_budget_pricing_statistics_export(
            db,
            draft,
            project_name=getattr(profile.project, "name", None) or f"项目{project_id}",
            sections=None if sections is None else sections.split(","),
        )
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename)}",
        },
    )


def _project_quota_payload(snapshot, current_user: User):
    return serialize_project_quota(
        snapshot,
        can_edit=can_create_budget_pricing(current_user),
        can_sync_enterprise=has_any_role(
            current_user,
            {"system_admin", "admin", "cost_approver"},
        ),
    )


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/project-quota",
    summary="Materialize or read the mutable project quota snapshot for one draft line",
)
async def materialize_project_quota_endpoint(
    project_id: int,
    line_identifier: str,
    payload: BudgetProjectQuotaMaterializeIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        snapshot = materialize_project_quota(
            db,
            profile,
            current_user,
            pricing_mode=payload.pricing_mode,
            line_identifier=line_identifier,
        )
        snapshot_id = snapshot.id
        db.commit()
        db.expire_all()
        from app.models.budget_project_quota import BudgetProjectQuotaSnapshot

        snapshot = (
            db.query(BudgetProjectQuotaSnapshot)
            .filter(BudgetProjectQuotaSnapshot.id == snapshot_id)
            .one()
        )
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(_project_quota_payload(snapshot, current_user))


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/project-quota/resources",
    summary="Add a labor/material/machinery row to the project quota",
)
async def create_project_quota_resource_endpoint(
    project_id: int,
    line_identifier: str,
    payload: BudgetProjectQuotaResourceCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    data = payload.model_dump(
        mode="json",
        exclude={"expected_snapshot_revision", "reason"},
        exclude_none=True,
    )
    try:
        snapshot = create_project_quota_resource(
            db,
            profile,
            current_user,
            line_identifier=line_identifier,
            expected_snapshot_revision=payload.expected_snapshot_revision,
            payload=data,
            reason=payload.reason,
        )
        snapshot_id = snapshot.id
        db.commit()
        db.expire_all()
        from app.models.budget_project_quota import BudgetProjectQuotaSnapshot

        snapshot = db.query(BudgetProjectQuotaSnapshot).filter_by(id=snapshot_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(_project_quota_payload(snapshot, current_user), message="工料机明细已新增")


@router.patch(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/project-quota/resources/{resource_identifier}",
    summary="Update every editable field of one project quota resource row",
)
async def update_project_quota_resource_endpoint(
    project_id: int,
    line_identifier: str,
    resource_identifier: str,
    payload: BudgetProjectQuotaResourceUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    data = payload.model_dump(
        mode="json",
        exclude={"expected_snapshot_revision", "expected_resource_revision", "reason"},
        exclude_unset=True,
    )
    try:
        snapshot = update_project_quota_resource(
            db,
            profile,
            current_user,
            line_identifier=line_identifier,
            resource_identifier=resource_identifier,
            expected_snapshot_revision=payload.expected_snapshot_revision,
            expected_resource_revision=payload.expected_resource_revision,
            payload=data,
            reason=payload.reason,
        )
        snapshot_id = snapshot.id
        db.commit()
        db.expire_all()
        from app.models.budget_project_quota import BudgetProjectQuotaSnapshot

        snapshot = db.query(BudgetProjectQuotaSnapshot).filter_by(id=snapshot_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(_project_quota_payload(snapshot, current_user), message="工料机明细已更新，项目定额已重算")


@router.delete(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/project-quota/resources/{resource_identifier}",
    summary="Delete one project quota resource row",
)
async def delete_project_quota_resource_endpoint(
    project_id: int,
    line_identifier: str,
    resource_identifier: str,
    payload: BudgetProjectQuotaResourceDeleteIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        snapshot = delete_project_quota_resource(
            db,
            profile,
            current_user,
            line_identifier=line_identifier,
            resource_identifier=resource_identifier,
            expected_snapshot_revision=payload.expected_snapshot_revision,
            expected_resource_revision=payload.expected_resource_revision,
            reason=payload.reason,
        )
        snapshot_id = snapshot.id
        db.commit()
        db.expire_all()
        from app.models.budget_project_quota import BudgetProjectQuotaSnapshot

        snapshot = db.query(BudgetProjectQuotaSnapshot).filter_by(id=snapshot_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(_project_quota_payload(snapshot, current_user), message="工料机明细已删除，项目定额已重算")


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/project-quota/sync-enterprise",
    summary="Synchronize the project quota to a versioned enterprise-quota draft",
)
async def sync_project_quota_to_enterprise_endpoint(
    project_id: int,
    line_identifier: str,
    payload: BudgetProjectQuotaEnterpriseSyncIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    if not settings.feature_cost_db:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    if not has_any_role(current_user, {"system_admin", "admin", "cost_approver"}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ENTERPRISE_QUOTA_SYNC_PERMISSION_DENIED",
                "message": "无权限同步企业定额，请联系管理员授予成本核定权限",
            },
        )
    try:
        result = sync_project_quota_to_enterprise(
            db,
            profile,
            current_user,
            line_identifier=line_identifier,
            expected_snapshot_revision=payload.expected_snapshot_revision,
            reason=payload.reason,
        )
        snapshot_id = result["snapshot"].id
        db.commit()
        db.expire_all()
        from app.models.budget_project_quota import BudgetProjectQuotaSnapshot

        snapshot = db.query(BudgetProjectQuotaSnapshot).filter_by(id=snapshot_id).one()
        result["snapshot"] = _project_quota_payload(snapshot, current_user)
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(result, message=result["message"])


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/procurement-statistics",
    summary="Aggregate material procurement quantities and labor trades",
)
async def get_pricing_draft_procurement_statistics_endpoint(
    project_id: int,
    pricing_mode: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        draft = get_current_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
        if draft is None:
            raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
        result = build_budget_procurement_statistics(db, draft)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    return api_ok(result)


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/ai-estimate",
    summary="Manually trigger AI unit-price estimation for one unpriced draft line",
)
async def estimate_pricing_draft_line_endpoint(
    project_id: int,
    line_identifier: str,
    payload: BudgetPricingDraftLineAiEstimateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_ai_estimate_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        draft, line = await estimate_budget_pricing_draft_line(
            db,
            profile,
            current_user,
            pricing_mode=payload.pricing_mode,
            line_identifier=line_identifier,
            expected_revision=payload.expected_revision,
            expected_line_revision=payload.expected_line_revision,
            reason=payload.reason,
        )
        draft_id = draft.id
        line_id = line.id
        db.commit()
        db.expire_all()
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == draft_id).one()
        line = db.query(BudgetProjectPricingDraftLine).filter(BudgetProjectPricingDraftLine.id == line_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        {
            "draft": serialize_budget_pricing_draft(draft),
            "line": serialize_budget_pricing_draft_line(line),
        },
        message="AI 估价已写入计价草稿",
    )


@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/quote-job",
    summary="Start one-click enterprise-quota plus AI-fallback quote draft generation",
)
async def create_pricing_draft_quote_job_endpoint(
    project_id: int,
    payload: BudgetPricingDraftQuoteJobCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_ai_estimate_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        job = create_budget_pricing_draft_quote_job(db, profile, current_user, payload)
        job_id = job.id
        should_start = bool(getattr(job, "_quote_job_was_created", False)) and job.status == "queued"
        db.commit()
        if should_start:
            background_tasks.add_task(run_budget_pricing_draft_quote_job_sync, job_id)
        db.expire_all()
        job = get_budget_pricing_draft_quote_job(db, job_id)
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_budget_pricing_draft_quote_job(job), message="一键生成报价任务已创建")


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/quote-job/current",
    summary="Get the latest one-click quote draft generation job for this project",
)
async def get_current_pricing_draft_quote_job_endpoint(
    project_id: int,
    pricing_mode: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_ai_estimate_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        draft = get_current_budget_pricing_draft(
            db,
            profile,
            current_user,
            pricing_mode=pricing_mode,
        )
        if draft is None:
            return api_ok(None)
        job = get_current_budget_pricing_draft_quote_job(
            db,
            account_id=draft.account_id,
            project_id=profile.project_id,
            draft_id=draft.id,
        )
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    return api_ok(serialize_budget_pricing_draft_quote_job(job) if job else None)


@router.get(
    "/admin/budget-projects/{project_id}/pricing-draft/quote-jobs/{job_identifier}",
    summary="Get one one-click quote draft generation job",
)
async def get_pricing_draft_quote_job_endpoint(
    project_id: int,
    job_identifier: str,
    include_lines: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_ai_estimate_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    try:
        job = get_budget_pricing_draft_quote_job(db, job_identifier)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    if int(job.project_id) != int(profile.project_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BUDGET_PRICING_DRAFT_QUOTE_JOB_NOT_FOUND")
    return api_ok(serialize_budget_pricing_draft_quote_job(job, include_lines=include_lines))

@router.post(
    "/admin/budget-projects/{project_id}/pricing-draft/quote-jobs/{job_identifier}/cancel",
    summary="Cancel one-click quote draft generation and stop future AI estimates",
)
async def cancel_pricing_draft_quote_job_endpoint(
    project_id: int,
    job_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_ai_estimate_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        job = get_budget_pricing_draft_quote_job(db, job_identifier)
        if int(job.project_id) != int(profile.project_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BUDGET_PRICING_DRAFT_QUOTE_JOB_NOT_FOUND")
        job = cancel_budget_pricing_draft_quote_job(db, job, current_user)
        job_id = job.id
        db.commit()
        db.expire_all()
        job = get_budget_pricing_draft_quote_job(db, job_id)
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_budget_pricing_draft_quote_job(job), message="Quote generation cancelled")


@router.patch(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}",
    summary="Edit or clear one manual draft unit price with optimistic locks",
)
async def patch_pricing_draft_line_endpoint(
    project_id: int,
    line_identifier: str,
    payload: BudgetPricingDraftLinePatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        draft, line = patch_budget_pricing_draft_line(
            db,
            profile,
            current_user,
            pricing_mode=payload.pricing_mode,
            line_identifier=line_identifier,
            expected_revision=payload.expected_revision,
            expected_line_revision=payload.expected_line_revision,
            manual_unit_price=payload.manual_unit_price,
            pricing_breakdown=payload.pricing_breakdown,
            reason=payload.reason,
        )
        draft_id = draft.id
        line_id = line.id
        db.commit()
        db.expire_all()
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == draft_id).one()
        line = db.query(BudgetProjectPricingDraftLine).filter(BudgetProjectPricingDraftLine.id == line_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        {
            "draft": serialize_budget_pricing_draft(draft),
            "line": serialize_budget_pricing_draft_line(line),
        },
        message="草稿单价已更新",
    )


@router.patch(
    "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}/construction-note",
    summary="Edit one construction note without changing price fields",
)
async def patch_pricing_draft_line_construction_note_endpoint(
    project_id: int,
    line_identifier: str,
    payload: BudgetPricingDraftLineConstructionNotePatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_draft_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        draft, line = patch_budget_pricing_draft_line_construction_note(
            db,
            profile,
            current_user,
            pricing_mode=payload.pricing_mode,
            line_identifier=line_identifier,
            expected_revision=payload.expected_revision,
            expected_line_revision=payload.expected_line_revision,
            remark=payload.remark,
            reason=payload.reason,
        )
        draft_id = draft.id
        line_id = line.id
        db.commit()
        db.expire_all()
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == draft_id).one()
        line = db.query(BudgetProjectPricingDraftLine).filter(BudgetProjectPricingDraftLine.id == line_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        {
            "draft": serialize_budget_pricing_draft(draft),
            "line": serialize_budget_pricing_draft_line(line),
        },
        message="施工提示已更新",
    )


# These static routes must be registered before the project-id routes and the
# existing budget project /{project_id} route in app.main.
@router.get(
    "/admin/budget-projects/pricing-runs/{run_identifier}/lines/{line_identifier}/candidates",
    summary="List immutable enterprise quota candidates for a pricing line",
)
async def list_budget_pricing_line_candidates(
    run_identifier: str,
    line_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    run = _accessible_run(db, run_identifier, current_user)
    try:
        line = get_budget_pricing_line(db, run, line_identifier)
    except BudgetPricingError as exc:
        raise _http_error(exc) from exc
    candidates = (
        db.query(BudgetProjectPricingMatchCandidate)
        .filter(BudgetProjectPricingMatchCandidate.run_line_id == line.id)
        .order_by(BudgetProjectPricingMatchCandidate.rank.asc())
        .all()
    )
    line_data = serialize_budget_pricing_line(line)
    return api_ok(
        {
            "line": line_data,
            "candidates": [serialize_budget_pricing_candidate(candidate) for candidate in candidates],
            "evidence": line_data.get("match_evidence") or {},
        }
    )


@router.get(
    "/admin/budget-projects/pricing-runs/{run_identifier}/lines",
    summary="List immutable pricing run lines",
)
async def list_budget_pricing_run_lines(
    run_identifier: str,
    keyword: Optional[str] = None,
    match_status: Optional[PricingMatchStatusFilter] = None,
    pricing_status: Optional[PricingLineStatusFilter] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    run = _accessible_run(db, run_identifier, current_user)
    query = db.query(BudgetProjectPricingRunLine).filter(BudgetProjectPricingRunLine.run_id == run.id)
    keyword_text = (keyword or "").strip()[:128]
    if keyword_text:
        pattern = f"%{keyword_text}%"
        query = query.filter(_line_keyword_predicate(pattern))
    if match_status:
        query = query.filter(BudgetProjectPricingRunLine.match_status == match_status)
    if pricing_status:
        query = query.filter(BudgetProjectPricingRunLine.pricing_status == pricing_status)
    total = query.count()
    rows = (
        query.order_by(BudgetProjectPricingRunLine.source_sort_order.asc(), BudgetProjectPricingRunLine.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_budget_pricing_line(line) for line in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/admin/budget-projects/pricing-runs/{run_identifier}/events",
    summary="List immutable pricing lifecycle events",
)
async def list_budget_pricing_run_events(
    run_identifier: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    run = _accessible_run(db, run_identifier, current_user)
    query = db.query(BudgetProjectPricingEvent).filter(BudgetProjectPricingEvent.run_id == run.id)
    total = query.count()
    rows = (
        query.order_by(BudgetProjectPricingEvent.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_budget_pricing_event(event) for event in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/admin/budget-projects/pricing-runs/{run_identifier}",
    summary="Get one immutable pricing run",
)
async def get_budget_pricing_run_detail(
    run_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    run = _accessible_run(db, run_identifier, current_user)
    return api_ok(serialize_budget_pricing_run(run))


@router.get(
    "/admin/budget-projects/{project_id}/pricing-readiness",
    summary="Check formal source and strict active quota readiness",
)
async def get_budget_pricing_readiness(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    return api_ok(build_budget_pricing_readiness(db, profile, current_user))


@router.get(
    "/admin/budget-projects/{project_id}/pricing-runs",
    summary="List immutable pricing versions for a budget project",
)
async def list_budget_pricing_runs(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    query = db.query(BudgetProjectPricingRun).filter(BudgetProjectPricingRun.project_id == profile.project_id)
    total = query.count()
    runs = (
        query.order_by(BudgetProjectPricingRun.run_number.desc(), BudgetProjectPricingRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for run in runs:
        item = serialize_budget_pricing_run(run)
        item["is_active"] = int(profile.active_pricing_run_id or 0) == int(run.id)
        items.append(item)
    return api_page(
        items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/admin/budget-projects/{project_id}/pricing-runs",
    summary="Create an immutable enterprise-quota pricing run",
)
async def create_budget_pricing_run_endpoint(
    project_id: int,
    payload: BudgetPricingRunCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    if payload.has_conflicting_version_ids():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "BUDGET_PRICING_EXPECTED_QUOTA_VERSION_CONFLICT"},
        )
    expected_quota_version_id = payload.expected_quota_version_id()
    if expected_quota_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "BUDGET_PRICING_EXPECTED_QUOTA_VERSION_REQUIRED"},
        )
    try:
        run = create_budget_pricing_run(
            db,
            profile,
            current_user,
            source_import_batch_id=payload.source_import_batch_id,
            source_import_revision_id=payload.source_import_revision_id,
            expected_quota_version_id=expected_quota_version_id,
            reason=payload.reason,
        )
        capture_budget_pricing_run_draft_snapshot(
            db,
            profile,
            current_user,
            run,
        )
        run_id = run.id
        db.commit()
        db.expire_all()
        run = db.query(BudgetProjectPricingRun).filter(BudgetProjectPricingRun.id == run_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_budget_pricing_run(run), message="项目成本计价已生成")


@router.post(
    "/admin/budget-projects/{project_id}/pricing-runs/{run_identifier}/activate",
    summary="Activate one immutable pricing version and refresh the current quote draft",
)
async def activate_budget_pricing_run_endpoint(
    project_id: int,
    run_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        run = activate_budget_pricing_run(
            db,
            profile,
            current_user,
            run_identifier,
        )
        draft = restore_budget_pricing_draft_from_run_snapshot(
            db,
            profile,
            current_user,
            run,
        )
        run_id = run.id
        draft_id = draft.id
        db.commit()
        db.expire_all()
        run = db.query(BudgetProjectPricingRun).filter(BudgetProjectPricingRun.id == run_id).one()
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == draft_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    run_payload = serialize_budget_pricing_run(run)
    run_payload["is_active"] = True
    return api_ok(
        {
            "run": run_payload,
            "draft": serialize_budget_pricing_draft(draft),
        },
        message="报价版本已启用，当前报价草稿已更新",
    )


@router.post(
    "/admin/budget-projects/{project_id}/pricing-runs/{run_identifier}/archive",
    summary="Archive one immutable pricing version",
)
async def archive_budget_pricing_run_endpoint(
    project_id: int,
    run_identifier: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    profile = _accessible_profile(db, project_id, current_user)
    require_budget_pricing_create(current_user)
    try:
        run = archive_budget_pricing_run(
            db,
            profile,
            current_user,
            run_identifier,
        )
        run_id = run.id
        db.commit()
        db.expire_all()
        run = db.query(BudgetProjectPricingRun).filter(BudgetProjectPricingRun.id == run_id).one()
    except BudgetPricingError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    run_payload = serialize_budget_pricing_run(run)
    run_payload["is_active"] = False
    return api_ok(run_payload, message="报价版本已归档")
