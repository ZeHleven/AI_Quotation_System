from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok
from app.dependencies import require_dashboard_viewer
from app.models.user import User
from app.services.business_lite_dashboard import build_business_lite_dashboard
from app.services.quote_dashboard import VALID_RANGES, build_quote_speed_dashboard
from app.services.response_dashboard import build_response_speed_dashboard


router = APIRouter()


@router.get("/admin/dashboard/quote-speed", summary="报价速度看板聚合")
async def get_quote_speed_dashboard(
    range_name: str = Query("last_30_days", alias="range"),
    current_user: User = Depends(require_dashboard_viewer),
    db: Session = Depends(get_db),
):
    if not settings.feature_dashboard_quote:
        raise HTTPException(status_code=403, detail="FEATURE_DISABLED")
    if range_name not in VALID_RANGES:
        raise HTTPException(status_code=422, detail="VALIDATION_ERROR")
    return api_ok(build_quote_speed_dashboard(db, range_name=range_name))


@router.get("/admin/dashboard/response-speed", summary="响应速度看板聚合")
async def get_response_speed_dashboard(
    range_name: str = Query("last_30_days", alias="range"),
    current_user: User = Depends(require_dashboard_viewer),
    db: Session = Depends(get_db),
):
    if not settings.feature_dashboard_response:
        raise HTTPException(status_code=403, detail="FEATURE_DISABLED")
    if range_name not in VALID_RANGES:
        raise HTTPException(status_code=422, detail="VALIDATION_ERROR")
    return api_ok(build_response_speed_dashboard(db, range_name=range_name))


@router.get("/admin/dashboard/business-lite", summary="经营驾驶舱轻量 MVP 聚合")
async def get_business_lite_dashboard(
    range_name: str = Query("last_30_days", alias="range"),
    current_user: User = Depends(require_dashboard_viewer),
    db: Session = Depends(get_db),
):
    if not settings.feature_dashboard_business_lite:
        raise HTTPException(status_code=403, detail="FEATURE_DISABLED")
    if range_name not in VALID_RANGES:
        raise HTTPException(status_code=422, detail="VALIDATION_ERROR")
    return api_ok(build_business_lite_dashboard(db, range_name=range_name))
