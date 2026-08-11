from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.quote_consistency import (
    QuoteConsistencyError,
    claim_quote_push_for_n8n,
    mark_quote_push_delivery_unknown,
    mark_quote_push_external_delivered,
    mark_quote_push_failed_before_dispatch,
    mark_quote_push_n8n_dispatching,
)


router = APIRouter()


class N8NQuotePushCallback(BaseModel):
    idempotency_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    quote_job_id: str | None = Field(default=None, max_length=36)
    execution_id: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=4000)


def require_n8n_callback_secret(
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> None:
    expected = settings.webhook_secret or ""
    provided = x_webhook_secret or ""
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="INVALID_N8N_CALLBACK_SECRET")


def _commit_or_conflict(db: Session, operation):
    try:
        result = operation()
        db.commit()
        return result
    except QuoteConsistencyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/internal/n8n/quote-push/claim", dependencies=[Depends(require_n8n_callback_secret)])
def claim_quote_push(payload: N8NQuotePushCallback, db: Session = Depends(get_db)):
    start = _commit_or_conflict(
        db,
        lambda: claim_quote_push_for_n8n(
            db,
            idempotency_key=payload.idempotency_key,
            quote_job_id=payload.quote_job_id,
        ),
    )
    return {"action": start.action, "attempt_status": start.attempt.status}


@router.post("/internal/n8n/quote-push/dispatch-start", dependencies=[Depends(require_n8n_callback_secret)])
def mark_dispatch_started(payload: N8NQuotePushCallback, db: Session = Depends(get_db)):
    attempt = _commit_or_conflict(
        db,
        lambda: mark_quote_push_n8n_dispatching(
            db,
            idempotency_key=payload.idempotency_key,
            quote_job_id=payload.quote_job_id,
        ),
    )
    return {"action": "dispatching", "attempt_status": attempt.status}


@router.post("/internal/n8n/quote-push/delivered", dependencies=[Depends(require_n8n_callback_secret)])
def mark_delivered(payload: N8NQuotePushCallback, db: Session = Depends(get_db)):
    def operation():
        attempt = claim_quote_push_for_n8n(
            db,
            idempotency_key=payload.idempotency_key,
            quote_job_id=payload.quote_job_id,
        ).attempt
        return mark_quote_push_external_delivered(
            db,
            attempt_id=attempt.id,
            status_code=200,
            response_text=f"n8n_execution:{payload.execution_id or 'unknown'}",
        )

    attempt = _commit_or_conflict(db, operation)
    return {"action": "delivered", "attempt_status": attempt.status}


@router.post("/internal/n8n/quote-push/fail-before-dispatch", dependencies=[Depends(require_n8n_callback_secret)])
def mark_failed_before_dispatch(payload: N8NQuotePushCallback, db: Session = Depends(get_db)):
    attempt = _commit_or_conflict(
        db,
        lambda: mark_quote_push_failed_before_dispatch(
            db,
            idempotency_key=payload.idempotency_key,
            quote_job_id=payload.quote_job_id,
            error_message=payload.error_message or "N8N_FAILED_BEFORE_DISPATCH",
        ),
    )
    return {"action": "failed", "attempt_status": attempt.status}


@router.post("/internal/n8n/quote-push/delivery-unknown", dependencies=[Depends(require_n8n_callback_secret)])
def mark_delivery_unknown(payload: N8NQuotePushCallback, db: Session = Depends(get_db)):
    attempt = _commit_or_conflict(
        db,
        lambda: mark_quote_push_delivery_unknown(
            db,
            idempotency_key=payload.idempotency_key,
            quote_job_id=payload.quote_job_id,
            error_message=payload.error_message or "N8N_DELIVERY_UNKNOWN",
        ),
    )
    return {"action": "delivery_unknown", "attempt_status": attempt.status}
