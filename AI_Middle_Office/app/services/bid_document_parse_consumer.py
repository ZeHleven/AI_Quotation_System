"""Idempotent Outbox admission for the Phase 2 Document Worker."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.bid_assessment_documents import BidDocumentParseRun
from app.models.bid_assessment_eventing import BidOutboxEvent
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    process_outbox_event_once,
)


DOCUMENT_PARSE_REQUEST_CONSUMER = "bid-document-worker-v1"
DOCUMENT_PARSE_REQUEST_EVENT = "bid.document.parse_requested.v1"
DOCUMENT_PARSE_REQUEST_FIELDS = (
    "parse_run_id",
    "document_version_id",
    "input_hash",
    "parser_profile_version",
)


class BidDocumentParseConsumerError(RuntimeError):
    code = "BID_DOCUMENT_PARSE_EVENT_INVALID"


def consume_document_parse_requested_event(
    db: Session,
    *,
    event_id: str,
) -> ProcessedEventResult:
    """Validate and durably acknowledge a parse request exactly once.

    The queued ParseRun is the recovery source of truth. A separate runner may
    claim it immediately or later; acknowledging this event never marks the
    document parsed and never calls a parser itself.
    """

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, object]:
        if str(event.event_type) != DOCUMENT_PARSE_REQUEST_EVENT:
            return {"ignored": True, "event_type": str(event.event_type)}

        payload = dict(event.payload_json or {})
        missing = [field for field in DOCUMENT_PARSE_REQUEST_FIELDS if not payload.get(field)]
        if missing:
            raise BidDocumentParseConsumerError(
                f"BID_DOCUMENT_PARSE_EVENT_PAYLOAD_MISSING:{','.join(missing)}"
            )
        run = (
            session.query(BidDocumentParseRun)
            .filter(BidDocumentParseRun.id == str(payload["parse_run_id"]))
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            raise BidDocumentParseConsumerError("BID_DOCUMENT_PARSE_RUN_NOT_FOUND")
        expected = {
            "aggregate_type": "document_parse_run",
            "aggregate_id": str(run.id),
            "document_version_id": str(run.document_version_id),
            "input_hash": str(run.input_hash),
            "parser_profile_version": str(run.parser_profile_version),
        }
        actual = {
            "aggregate_type": str(event.aggregate_type),
            "aggregate_id": str(event.aggregate_id),
            "document_version_id": str(payload["document_version_id"]),
            "input_hash": str(payload["input_hash"]),
            "parser_profile_version": str(payload["parser_profile_version"]),
        }
        if actual != expected:
            raise BidDocumentParseConsumerError("BID_DOCUMENT_PARSE_EVENT_MISMATCH")
        return {
            "ignored": False,
            "parse_run_id": str(run.id),
            "status": str(run.status),
        }

    return process_outbox_event_once(
        db,
        consumer_name=DOCUMENT_PARSE_REQUEST_CONSUMER,
        event_id=event_id,
        handler=_handler,
    )

