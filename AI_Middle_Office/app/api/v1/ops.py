import asyncio

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.responses import api_ok
from app.dependencies import require_admin
from app.models.user import User
from app.services.ops_monitor import (
    acknowledge_error_logs,
    build_ops_dashboard,
    collect_error_logs,
    collect_job_status,
    collect_service_statuses,
    send_dingtalk_alerts,
)


router = APIRouter()


class AcknowledgeOpsLogsRequest(BaseModel):
    fingerprints: list[str] | None = None


def _build_ops_dashboard_with_session():
    db = SessionLocal()
    try:
        return build_ops_dashboard(db)
    finally:
        db.close()


def _collect_job_status_with_session(stuck_minutes: int):
    db = SessionLocal()
    try:
        return collect_job_status(db, stuck_minutes=stuck_minutes)
    finally:
        db.close()


@router.get("/admin/ops/dashboard", summary="Ops dashboard")
async def get_ops_dashboard(
    current_user: User = Depends(require_admin),
):
    return api_ok(await asyncio.to_thread(_build_ops_dashboard_with_session))


@router.get("/admin/ops/services", summary="Ops service probes")
async def get_ops_services(current_user: User = Depends(require_admin)):
    return api_ok(await asyncio.to_thread(collect_service_statuses))


@router.get("/admin/ops/logs", summary="Ops error logs")
async def get_ops_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
):
    return api_ok(await asyncio.to_thread(collect_error_logs, limit=limit))


@router.post("/admin/ops/logs/acknowledge", summary="Acknowledge current ops log events")
async def acknowledge_ops_logs(
    req: AcknowledgeOpsLogsRequest,
    current_user: User = Depends(require_admin),
):
    result = await asyncio.to_thread(
        acknowledge_error_logs,
        fingerprints=req.fingerprints,
        username=current_user.username,
    )
    return api_ok(result)


@router.get("/admin/ops/jobs", summary="Ops stuck quote jobs")
async def get_ops_jobs(
    stuck_minutes: int = Query(30, ge=1, le=1440),
    current_user: User = Depends(require_admin),
):
    return api_ok(await asyncio.to_thread(_collect_job_status_with_session, stuck_minutes))


@router.post("/admin/ops/test_alert", summary="Send test alert")
async def test_alert(current_user: User = Depends(require_admin)):
    test_alerts = [
        {
            "level": "warning",
            "title": "测试告警",
            "message": "这是一条来自 AI 中台的测试告警，说明钉钉推送配置正常。",
        }
    ]
    if not settings.alert_dingtalk_webhook:
        return api_ok(message="ALERT_DINGTALK_WEBHOOK 未配置，未发送")
    await asyncio.to_thread(send_dingtalk_alerts, test_alerts)
    return api_ok(message="测试告警已发送，请检查钉钉群")
