from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.chat import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.ops_monitor import build_ops_dashboard, collect_error_logs, collect_job_status, collect_service_statuses


router = APIRouter()


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可操作")
    return current_user


@router.get("/admin/ops/dashboard", summary="运维监控总览")
async def get_ops_dashboard(
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    return {"code": 200, "data": build_ops_dashboard(db)}


@router.get("/admin/ops/services", summary="基础服务探活")
async def get_ops_services(current_user: User = Depends(_require_admin)):
    return {"code": 200, "data": collect_service_statuses()}


@router.get("/admin/ops/logs", summary="异常日志聚合")
async def get_ops_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(_require_admin),
):
    return {"code": 200, "data": collect_error_logs(limit=limit)}


@router.get("/admin/ops/jobs", summary="报价任务卡住提醒")
async def get_ops_jobs(
    stuck_minutes: int = Query(30, ge=1, le=1440),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    return {"code": 200, "data": collect_job_status(db, stuck_minutes=stuck_minutes)}
