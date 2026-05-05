from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.services.ops_monitor import build_ops_dashboard, collect_error_logs, collect_job_status, collect_service_statuses, send_dingtalk_alerts


router = APIRouter()


@router.get("/admin/ops/dashboard", summary="运维监控总览")
async def get_ops_dashboard(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"code": 200, "data": build_ops_dashboard(db)}


@router.get("/admin/ops/services", summary="基础服务探活")
async def get_ops_services(current_user: User = Depends(require_admin)):
    return {"code": 200, "data": collect_service_statuses()}


@router.get("/admin/ops/logs", summary="异常日志聚合")
async def get_ops_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
):
    return {"code": 200, "data": collect_error_logs(limit=limit)}


@router.get("/admin/ops/jobs", summary="报价任务卡住提醒")
async def get_ops_jobs(
    stuck_minutes: int = Query(30, ge=1, le=1440),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"code": 200, "data": collect_job_status(db, stuck_minutes=stuck_minutes)}


@router.post("/admin/ops/test_alert", summary="发送测试告警到钉钉")
async def test_alert(current_user: User = Depends(require_admin)):
    test_alerts = [{"level": "warning", "title": "测试告警", "message": "这是一条来自 AI 中台的测试告警，说明钉钉推送配置正常。"}]
    send_dingtalk_alerts(test_alerts)
    from app.core.config import settings
    if not settings.alert_dingtalk_webhook:
        return {"code": 200, "message": "ALERT_DINGTALK_WEBHOOK 未配置，未发送"}
    return {"code": 200, "message": "测试告警已发送，请检查钉钉群"}
