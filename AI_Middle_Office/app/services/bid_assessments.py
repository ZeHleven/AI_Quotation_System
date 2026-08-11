"""Assessment aggregate commands for the isolated v1 runtime."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessment
from app.services.bid_assessment_eventing import append_audit_log, append_outbox_event
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_assessment_snapshots import build_assessment_snapshot


class BidAssessmentCommandError(RuntimeError):
    code = "BID_ASSESSMENT_STATE_CONFLICT"


class BidAssessmentExternalRefConflict(BidAssessmentCommandError):
    pass


def create_bid_assessment(
    db: Session,
    *,
    actor_id: int,
    actor_ref: str,
    request_id: str,
    title: str,
    client_name: str,
    internal_note: str | None,
    external_ref: str | None,
) -> IdempotentCommandResult:
    """Create API-01 state, Outbox, audit, and response in the caller transaction.

    The helper flushes but never commits. API-01 completes the minimal metadata
    step, so the externally visible initial state is ``awaiting_files``. It
    intentionally does not create a Manifest, Scope, or Run.
    """

    if external_ref:
        existing = (
            db.query(BidAssessment.id)
            .filter(BidAssessment.external_ref == external_ref)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            raise BidAssessmentExternalRefConflict("BID_ASSESSMENT_EXTERNAL_REF_CONFLICT")

    assessment = BidAssessment(
        id=str(uuid.uuid4()),
        title=title,
        client_name=client_name,
        internal_note=internal_note,
        external_ref=external_ref,
        lifecycle_status="active",
        business_status="awaiting_files",
        current_manifest_id=None,
        active_run_id=None,
        created_by=int(actor_id),
        updated_by=int(actor_id),
        row_version=1,
    )
    db.add(assessment)
    db.flush()
    db.refresh(assessment)

    snapshot = build_assessment_snapshot(db, assessment)
    event = append_outbox_event(
        db,
        event_type="bid.assessment.created.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="assessment",
        aggregate_id=assessment.id,
        aggregate_version=int(assessment.row_version),
        assessment_id=assessment.id,
        request_id=request_id,
        payload_schema="bid.assessment.created.v1.payload",
        payload={"snapshot": snapshot},
        dedupe_key=f"assessment-created:{assessment.id}",
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="assessment.create",
        entity_type="assessment",
        entity_id=assessment.id,
        assessment_id=assessment.id,
        outcome="succeeded",
        request_id=request_id,
        before=None,
        after=snapshot,
        metadata={
            "http_method": "POST",
            "route_template": "/api/v1/bid-assessments",
            "outbox_event_type": event.event_type,
        },
        correlation_id=event.event_id,
    )

    response_body: dict[str, Any] = {
        "code": 200,
        "message": "ok",
        "data": snapshot,
        "error": None,
        "request_id": request_id,
    }
    return IdempotentCommandResult(
        status_code=201,
        body=response_body,
        resource_type="assessment",
        resource_id=assessment.id,
        response_ref=f"/api/v1/bid-assessments/{assessment.id}",
    )
