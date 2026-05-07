from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import require_admin
from app.models.prompt_regression import PromptRegressionCase, PromptRegressionRun
from app.models.user import User
from app.schemas.prompt_regression import PromptRegressionBuildRequest, PromptRegressionRunRequest
from app.services.prompt_regression import (
    build_golden_cases_from_feedback,
    case_to_dict,
    create_prompt_regression_run,
    run_to_dict,
)


router = APIRouter()


@router.post("/admin/prompt_regression/cases/build", summary="Build prompt regression golden cases")
def build_prompt_regression_cases(
    payload: Optional[PromptRegressionBuildRequest] = Body(default=None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = payload or PromptRegressionBuildRequest()
    return api_ok(build_golden_cases_from_feedback(db, payload))


@router.get("/admin/prompt_regression/cases", summary="List prompt regression golden cases")
def list_prompt_regression_cases(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    prompt_version: Optional[str] = None,
    active: Optional[bool] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(PromptRegressionCase)
    if prompt_version:
        query = query.filter(PromptRegressionCase.source_prompt_version == prompt_version)
    if active is not None:
        query = query.filter(PromptRegressionCase.active.is_(active))

    total = query.count()
    rows = (
        query.order_by(PromptRegressionCase.created_at.desc(), PromptRegressionCase.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page([case_to_dict(item) for item in rows], total=total, page=page, page_size=page_size)


@router.post("/admin/prompt_regression/runs", summary="Create prompt regression report")
def create_prompt_regression_report(
    payload: Optional[PromptRegressionRunRequest] = Body(default=None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = payload or PromptRegressionRunRequest()
    try:
        run = create_prompt_regression_run(db, triggered_by=current_user.username, request=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return api_ok(run_to_dict(run))


@router.get("/admin/prompt_regression/runs/latest", summary="Latest prompt regression report")
def get_latest_prompt_regression_report(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = db.query(PromptRegressionRun).order_by(PromptRegressionRun.started_at.desc()).first()
    return api_ok(run_to_dict(run) if run else None)


@router.get("/admin/prompt_regression/runs", summary="List prompt regression reports")
def list_prompt_regression_reports(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    runs = (
        db.query(PromptRegressionRun)
        .order_by(PromptRegressionRun.started_at.desc(), PromptRegressionRun.id.desc())
        .limit(limit)
        .all()
    )
    return api_ok([run_to_dict(item) for item in runs])
