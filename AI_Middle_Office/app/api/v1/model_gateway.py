from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import require_admin
from app.models.model_call_log import ModelCallLog
from app.models.user import User
from app.services.model_gateway import get_circuit_status, reset_circuit_breakers


router = APIRouter()


@router.get("/admin/model_gateway/stats", summary="模型网关调用统计")
async def get_model_gateway_stats(
    hours: int = Query(24, ge=1, le=168),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(
            ModelCallLog.provider,
            ModelCallLog.model,
            ModelCallLog.endpoint_type,
            ModelCallLog.status,
            func.count(ModelCallLog.id),
            func.avg(ModelCallLog.latency_ms),
            func.sum(ModelCallLog.estimated_cost),
            func.sum(ModelCallLog.input_chars),
            func.sum(ModelCallLog.output_chars),
        )
        .filter(ModelCallLog.created_at >= since)
        .group_by(
            ModelCallLog.provider,
            ModelCallLog.model,
            ModelCallLog.endpoint_type,
            ModelCallLog.status,
        )
        .all()
    )

    return {
        "code": 200,
        "hours": hours,
        "data": [
            {
                "provider": row[0],
                "model": row[1],
                "endpoint_type": row[2],
                "status": row[3],
                "count": row[4],
                "avg_latency_ms": round(float(row[5] or 0), 2),
                "estimated_cost": round(float(row[6] or 0), 6),
                "input_chars": int(row[7] or 0),
                "output_chars": int(row[8] or 0),
            }
            for row in rows
        ],
        "circuit_breakers": get_circuit_status(),
    }


@router.get("/admin/model_gateway/circuits", summary="模型网关熔断状态")
async def get_model_gateway_circuits(current_user: User = Depends(require_admin)):
    return {"code": 200, "data": get_circuit_status()}


@router.post("/admin/model_gateway/circuits/reset", summary="手动重置所有熔断器")
async def reset_all_circuits(current_user: User = Depends(require_admin)):
    reset_circuit_breakers()
    return {"code": 200, "message": "所有熔断器已重置"}
