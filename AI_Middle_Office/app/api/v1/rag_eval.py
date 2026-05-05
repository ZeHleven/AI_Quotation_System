import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.chat import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.rag_eval_report import RagEvalReport
from app.models.user import User

router = APIRouter()


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可操作")
    return current_user


def _format_report(report: RagEvalReport) -> dict:
    quality_ok = None
    if report.status == "completed" and report.hit_rate is not None and report.mrr is not None:
        quality_ok = (
            report.hit_rate >= settings.rag_eval_warn_hit_rate
            and report.mrr >= settings.rag_eval_warn_mrr
        )
    by_level = None
    if report.by_level_json:
        try:
            by_level = json.loads(report.by_level_json)
        except Exception:
            pass
    return {
        "id": report.id,
        "triggered_by": report.triggered_by,
        "status": report.status,
        "started_at": report.started_at.isoformat() if report.started_at else None,
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
        "top_k": report.top_k,
        "case_count": report.case_count,
        "hit_rate": report.hit_rate,
        "mrr": report.mrr,
        "by_level": by_level,
        "quality_ok": quality_ok,
        "error": report.error,
    }


@router.get("/admin/rag_eval/latest", summary="最新 RAG 评测结果")
async def get_latest_rag_eval(
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    report = (
        db.query(RagEvalReport)
        .order_by(RagEvalReport.started_at.desc())
        .first()
    )
    return {"code": 200, "data": _format_report(report) if report else None}


@router.get("/admin/rag_eval/history", summary="RAG 评测历史记录")
async def get_rag_eval_history(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    reports = (
        db.query(RagEvalReport)
        .order_by(RagEvalReport.started_at.desc())
        .limit(limit)
        .all()
    )
    return {"code": 200, "data": [_format_report(r) for r in reports]}
