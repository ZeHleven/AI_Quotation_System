from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.schemas.quote_feedback import QuoteFeedbackRejectRequest
from app.services.quote_feedback import record_rejected_quote


router = APIRouter()


@router.post("/quote/feedback/reject", summary="记录人工预审打回")
async def reject_quote_feedback(
    payload: QuoteFeedbackRejectRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.quote_job_id and not payload.trace_id:
        raise HTTPException(status_code=400, detail="quote_job_id or trace_id is required")
    if payload.quote_job_id:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == payload.quote_job_id).first()
        if not job or (current_user.role != "admin" and job.username != current_user.username):
            raise HTTPException(status_code=404, detail="quote job not found")
    feedback = record_rejected_quote(
        db,
        username=current_user.username,
        quote_job_id=payload.quote_job_id,
        trace_id=payload.trace_id,
        reason=payload.reason,
        allow_cross_user=current_user.role == "admin",
    )
    db.commit()
    return api_ok(
        {
            "feedback_id": feedback.id,
            "quote_id": feedback.quote_id,
            "quote_job_id": feedback.quote_job_id,
            "status": feedback.status,
        }
    )
