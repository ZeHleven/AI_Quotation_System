"""Authenticated, resumable SSE for the isolated bid-assessment v1 domain."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import BidAssessment
from app.models.user import User
from app.services.bid_assessment_eventing import (
    format_public_event_sse,
    list_public_events_after,
    resolve_sse_start_sequence,
)
from app.services.rbac import has_admin_role


logger = logging.getLogger(__name__)
router = APIRouter()


def _require_visible_assessment(
    db: Session,
    *,
    assessment_id: str,
    current_user: User,
) -> BidAssessment:
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .one_or_none()
    )
    if assessment is None or (
        int(assessment.created_by) != int(current_user.id)
        and not has_admin_role(current_user)
    ):
        raise HTTPException(status_code=404, detail="BID_RESOURCE_NOT_FOUND")
    return assessment


@router.get(
    "/bid-assessments/{assessment_id}/events",
    summary="订阅研判 Assessment 公共事件",
)
async def stream_bid_assessment_events(
    assessment_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.feature_bid_assessment_v1_runtime:
        raise HTTPException(status_code=404, detail="BID_RESOURCE_NOT_FOUND")

    _require_visible_assessment(
        db,
        assessment_id=assessment_id,
        current_user=current_user,
    )
    request_id = str(getattr(request.state, "trace_id", "") or f"req_{uuid.uuid4().hex}")[:80]
    try:
        start_sequence = resolve_sse_start_sequence(
            db,
            assessment_id=assessment_id,
            last_event_id=last_event_id,
            request_id=request_id,
            retention_days=settings.bid_public_event_retention_days,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    async def event_generator():
        sequence_no = int(start_sequence)
        last_output_at = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            stream_db = SessionLocal()
            try:
                events = list_public_events_after(
                    stream_db,
                    assessment_id=assessment_id,
                    sequence_no=sequence_no,
                    limit=100,
                )
            except Exception:
                logger.exception(
                    "bid_sse_read_failed",
                    extra={"assessment_id": assessment_id, "sequence_no": sequence_no},
                )
                return
            finally:
                stream_db.close()

            for event in events:
                sequence_no = int(event.sequence_no)
                last_output_at = time.monotonic()
                yield format_public_event_sse(event)
                if event.event_type == "stream.closed" and bool(
                    (event.payload_json or {}).get("terminal")
                ):
                    return

            if time.monotonic() - last_output_at >= max(
                1,
                settings.bid_sse_keepalive_seconds,
            ):
                yield ": keepalive\n\n"
                last_output_at = time.monotonic()
            await asyncio.sleep(max(0.1, settings.bid_sse_poll_seconds))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
