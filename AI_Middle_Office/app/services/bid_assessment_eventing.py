"""Transactional primitives for the bid-assessment v1 event protocol.

All write helpers deliberately flush but never commit. Callers must put the
business mutation, Outbox event, processed-event marker, and audit rows in the
same SQLAlchemy transaction.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_eventing import (
    OUTBOX_EVENT_TYPES,
    PUBLIC_EVENT_TYPES,
    PUBLIC_RESOURCE_TYPES,
    BidAuditLog,
    BidOutboxEvent,
    BidProcessedEvent,
    BidPublicEvent,
)
from app.services.bid_assessment_snapshots import build_assessment_snapshot


PUBLIC_PROJECTOR_CONSUMER = "bid-public-event-projector-v1"


class BidEventingError(RuntimeError):
    """Base class for deterministic eventing failures."""


class BidEventNotFound(BidEventingError):
    pass


class BidPublicProjectionError(BidEventingError):
    pass


@dataclass(frozen=True)
class ProcessedEventResult:
    duplicate: bool
    event_id: str
    result_hash: str
    result_ref: str | None
    value: Any = None


@dataclass(frozen=True)
class PublicProjection:
    event_type: str
    resource_type: str
    resource_id: str
    resource_version: int
    payload: dict[str, Any]
    projection_key: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize database datetimes across MySQL and SQLite test dialects."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Unsupported canonical JSON type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def append_outbox_event(
    db: Session,
    *,
    event_type: str,
    producer: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    request_id: str,
    payload_schema: str,
    payload: dict[str, Any],
    dedupe_key: str,
    assessment_id: str | None = None,
    run_id: str | None = None,
    causation_event_id: str | None = None,
    event_id: str | None = None,
    available_at: datetime | None = None,
    occurred_at: datetime | None = None,
) -> BidOutboxEvent:
    if event_type not in OUTBOX_EVENT_TYPES:
        raise BidEventingError(f"BID_OUTBOX_EVENT_TYPE_NOT_ALLOWED:{event_type}")
    if int(aggregate_version) < 1:
        raise BidEventingError("BID_OUTBOX_AGGREGATE_VERSION_INVALID")
    if run_id and not assessment_id:
        raise BidEventingError("BID_OUTBOX_RUN_SCOPE_INVALID")

    now = occurred_at or utc_now()
    normalized_payload = json.loads(canonical_json(payload))
    event = BidOutboxEvent(
        id=str(uuid.uuid4()),
        event_id=event_id or f"evt_{uuid.uuid4().hex}",
        event_type=event_type,
        producer=str(producer)[:128],
        aggregate_type=str(aggregate_type)[:64],
        aggregate_id=str(aggregate_id)[:80],
        aggregate_version=int(aggregate_version),
        assessment_id=assessment_id,
        run_id=run_id,
        request_id=str(request_id)[:80],
        causation_event_id=(str(causation_event_id)[:80] if causation_event_id else None),
        payload_schema=str(payload_schema)[:160],
        payload_json=normalized_payload,
        payload_hash=canonical_hash(normalized_payload),
        dedupe_key=str(dedupe_key)[:191],
        status="pending",
        available_at=available_at or now,
        attempts=0,
        occurred_at=now,
        row_version=1,
    )
    db.add(event)
    db.flush()
    return event


def append_audit_log(
    db: Session,
    *,
    actor_type: str,
    actor_ref: str,
    action: str,
    entity_type: str,
    entity_id: str,
    outcome: str,
    request_id: str,
    actor_id: int | None = None,
    assessment_id: str | None = None,
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    occurred_at: datetime | None = None,
) -> BidAuditLog:
    if actor_type not in {"user", "system", "service"}:
        raise BidEventingError("BID_AUDIT_ACTOR_TYPE_INVALID")
    if (actor_type == "user") != (actor_id is not None):
        raise BidEventingError("BID_AUDIT_ACTOR_INVALID")
    if outcome not in {"succeeded", "denied", "failed"}:
        raise BidEventingError("BID_AUDIT_OUTCOME_INVALID")

    audit_id = str(uuid.uuid4())
    now = occurred_at or utc_now()
    normalized_metadata = json.loads(canonical_json(metadata or {}))
    stored_actor_ref = str(actor_ref)[:128]
    stored_action = str(action)[:128]
    stored_entity_type = str(entity_type)[:64]
    stored_entity_id = str(entity_id)[:80]
    stored_request_id = str(request_id)[:80]
    stored_correlation_id = str(correlation_id)[:80] if correlation_id else None
    before_hash = canonical_hash(before) if before is not None else None
    after_hash = canonical_hash(after) if after is not None else None
    record_hash = canonical_hash(
        {
            "id": audit_id,
            "assessment_id": assessment_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "actor_ref": stored_actor_ref,
            "action": stored_action,
            "entity_type": stored_entity_type,
            "entity_id": stored_entity_id,
            "outcome": outcome,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "request_id": stored_request_id,
            "correlation_id": stored_correlation_id,
            "metadata": normalized_metadata,
            "occurred_at": now,
        }
    )
    row = BidAuditLog(
        id=audit_id,
        assessment_id=assessment_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_ref=stored_actor_ref,
        action=stored_action,
        entity_type=stored_entity_type,
        entity_id=stored_entity_id,
        outcome=outcome,
        before_hash=before_hash,
        after_hash=after_hash,
        request_id=stored_request_id,
        correlation_id=stored_correlation_id,
        metadata_json=normalized_metadata,
        metadata_hash=canonical_hash(normalized_metadata),
        record_hash=record_hash,
        occurred_at=now,
    )
    db.add(row)
    db.flush()
    return row


def process_outbox_event_once(
    db: Session,
    *,
    consumer_name: str,
    event_id: str,
    handler: Callable[[Session, BidOutboxEvent], Any],
    processed_at: datetime | None = None,
) -> ProcessedEventResult:
    existing = (
        db.query(BidProcessedEvent)
        .filter(
            BidProcessedEvent.consumer_name == consumer_name,
            BidProcessedEvent.event_id == event_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        return ProcessedEventResult(
            duplicate=True,
            event_id=event_id,
            result_hash=existing.result_hash,
            result_ref=existing.result_ref,
        )

    event = (
        db.query(BidOutboxEvent)
        .filter(BidOutboxEvent.event_id == event_id)
        .one_or_none()
    )
    if event is None:
        raise BidEventNotFound(f"BID_OUTBOX_EVENT_NOT_FOUND:{event_id}")

    result = handler(db, event)
    result_ref = None
    result_value = result
    if isinstance(result, dict) and "result_ref" in result:
        result_ref = str(result["result_ref"])[:512] if result["result_ref"] else None
    result_hash = canonical_hash(result)
    db.add(
        BidProcessedEvent(
            consumer_name=str(consumer_name)[:128],
            event_id=event_id,
            result_hash=result_hash,
            result_ref=result_ref,
            processed_at=processed_at or utc_now(),
        )
    )
    db.flush()
    return ProcessedEventResult(
        duplicate=False,
        event_id=event_id,
        result_hash=result_hash,
        result_ref=result_ref,
        value=result_value,
    )


PUBLIC_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "assessment.snapshot": ("snapshot",),
    "assessment.status.changed": ("from", "to", "recommended_view", "allowed_actions"),
    "upload_batch.changed": ("batch_id", "status", "ready_count", "failed_count"),
    "document.parse.changed": ("document_version_id", "status", "quality", "warnings"),
    "lot.selection.required": ("candidate_count", "lots_url"),
    "lot.selected": ("scope_id", "lot_id"),
    "run.status.changed": ("run_id", "from", "to", "retryable"),
    "run.stage.changed": (
        "run_id",
        "stage_code",
        "status",
        "message",
        "completed_units",
        "total_units",
    ),
    "question.round.published": ("round_id", "question_count", "critical_count"),
    "question.round.answered": ("round_id", "run_id"),
    "report.published": ("report_id", "report_type", "version", "decision_class"),
    "report.delta.published": ("delta_id", "severity", "decision_changed"),
    "operation.failed": ("operation_type", "resource_id", "error_code", "retryable"),
    "stream.reset": ("reason", "snapshot_url"),
    "stream.closed": ("reason", "terminal"),
}


OUTBOX_PUBLIC_MAPPING: dict[str, tuple[str, str, str | None]] = {
    "bid.assessment.created.v1": ("assessment.snapshot", "assessment", None),
    "bid.upload_batch.created.v1": ("upload_batch.changed", "upload_batch", "batch_id"),
    "bid.upload_file.received.v1": ("upload_batch.changed", "upload_batch", "batch_id"),
    "bid.upload_file.removed.v1": ("upload_batch.changed", "upload_batch", "batch_id"),
    "bid.upload_batch.deactivation_added.v1": (
        "upload_batch.changed",
        "upload_batch",
        "batch_id",
    ),
    "bid.upload_batch.abandoned.v1": (
        "upload_batch.changed",
        "upload_batch",
        "batch_id",
    ),
    "bid.manifest.committed.v1": ("assessment.snapshot", "assessment", None),
    "bid.assessment.input_stale.v1": ("assessment.status.changed", "assessment", None),
    "bid.document.parsed.v1": ("document.parse.changed", "document_version", "document_version_id"),
    "bid.document.parse_failed.v1": ("document.parse.changed", "document_version", "document_version_id"),
    "bid.lots.detected.v1": ("lot.selection.required", "assessment", None),
    "bid.lot.selected.v1": ("lot.selected", "assessment", None),
    "bid.run.created.v1": ("run.status.changed", "run", "run_id"),
    "bid.run.cancelled.v1": ("run.status.changed", "run", "run_id"),
    "bid.run.succeeded.v1": ("run.status.changed", "run", "run_id"),
    "bid.run.failed.v1": ("run.status.changed", "run", "run_id"),
    "bid.task.ready.v1": ("run.stage.changed", "run", "run_id"),
    "bid.task.waiting_operation.v1": ("run.stage.changed", "run", "run_id"),
    "bid.task.waiting_input.v1": ("run.stage.changed", "run", "run_id"),
    "bid.task.succeeded.v1": ("run.stage.changed", "run", "run_id"),
    "bid.task.failed.v1": ("run.stage.changed", "run", "run_id"),
    "bid.task.stale.v1": ("run.stage.changed", "run", "run_id"),
    "bid.question.published.v1": ("question.round.published", "question_round", "round_id"),
    "bid.question.answered.v1": ("question.round.answered", "question_round", "round_id"),
    "bid.report.published.v1": ("report.published", "report", "report_id"),
    "bid.report.failed.v1": ("operation.failed", "report", "report_id"),
    "bid.report.superseded.v1": ("report.delta.published", "delta", "delta_id"),
}


def _projection_for_event(event: BidOutboxEvent) -> PublicProjection | None:
    mapping = OUTBOX_PUBLIC_MAPPING.get(event.event_type)
    if mapping is None:
        return None
    event_type, resource_type, resource_id_field = mapping
    payload = dict(event.payload_json or {})
    required_fields = PUBLIC_REQUIRED_FIELDS[event_type]
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise BidPublicProjectionError(
            f"BID_PUBLIC_EVENT_PAYLOAD_MISSING:{event.event_type}:{','.join(missing)}"
        )
    public_payload = {field: payload[field] for field in required_fields}
    resource_id = (
        str(payload[resource_id_field])
        if resource_id_field is not None
        else str(event.assessment_id or event.aggregate_id)
    )
    resource_version = int(payload.get("resource_version") or event.aggregate_version)
    projection_key = f"{event_type}:{resource_type}:{resource_id}:{resource_version}"
    return PublicProjection(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version=resource_version,
        payload=public_payload,
        projection_key=projection_key,
    )


def project_outbox_event_to_public(
    db: Session,
    *,
    event_id: str,
    retention_days: int = 7,
    now: datetime | None = None,
) -> ProcessedEventResult:
    current_time = now or utc_now()

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        projection = _projection_for_event(event)
        if projection is None or event.assessment_id is None:
            return {"public_event_ids": []}
        if projection.event_type not in PUBLIC_EVENT_TYPES:
            raise BidPublicProjectionError("BID_PUBLIC_EVENT_TYPE_NOT_ALLOWED")
        if projection.resource_type not in PUBLIC_RESOURCE_TYPES:
            raise BidPublicProjectionError("BID_PUBLIC_RESOURCE_TYPE_NOT_ALLOWED")

        assessment = (
            session.query(BidAssessment)
            .filter(BidAssessment.id == event.assessment_id)
            .with_for_update()
            .one_or_none()
        )
        if assessment is None:
            raise BidPublicProjectionError("BID_PUBLIC_ASSESSMENT_NOT_FOUND")
        sequence_no = int(
            session.query(func.max(BidPublicEvent.sequence_no))
            .filter(BidPublicEvent.assessment_id == event.assessment_id)
            .scalar()
            or 0
        ) + 1
        public_event = BidPublicEvent(
            id=str(uuid.uuid4()),
            assessment_id=event.assessment_id,
            sequence_no=sequence_no,
            event_id=f"aevt_{uuid.uuid4().hex}",
            origin_type="outbox",
            source_event_id=event.event_id,
            projection_key=projection.projection_key,
            event_type=projection.event_type,
            resource_type=projection.resource_type,
            resource_id=projection.resource_id,
            resource_version=projection.resource_version,
            request_id=event.request_id,
            payload_json=projection.payload,
            payload_hash=canonical_hash(projection.payload),
            occurred_at=event.occurred_at,
            expires_at=current_time + timedelta(days=max(1, int(retention_days))),
        )
        session.add(public_event)
        session.flush()
        return {"public_event_ids": [public_event.event_id]}

    return process_outbox_event_once(
        db,
        consumer_name=PUBLIC_PROJECTOR_CONSUMER,
        event_id=event_id,
        handler=_handler,
        processed_at=current_time,
    )


def append_stream_control_events(
    db: Session,
    *,
    assessment_id: str,
    request_id: str,
    reset_reason: str | None = None,
    retention_days: int = 7,
    now: datetime | None = None,
) -> tuple[int, list[BidPublicEvent]]:
    current_time = now or utc_now()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise BidPublicProjectionError("BID_PUBLIC_ASSESSMENT_NOT_FOUND")
    previous_sequence = int(
        db.query(func.max(BidPublicEvent.sequence_no))
        .filter(BidPublicEvent.assessment_id == assessment_id)
        .scalar()
        or 0
    )
    rows: list[BidPublicEvent] = []

    def _append(event_type: str, payload: dict[str, Any]) -> None:
        sequence_no = previous_sequence + len(rows) + 1
        row = BidPublicEvent(
            id=str(uuid.uuid4()),
            assessment_id=assessment_id,
            sequence_no=sequence_no,
            event_id=f"aevt_{uuid.uuid4().hex}",
            origin_type="stream_control",
            source_event_id=None,
            projection_key=f"stream:{request_id}:{event_type}:{sequence_no}",
            event_type=event_type,
            resource_type="assessment",
            resource_id=assessment_id,
            resource_version=int(assessment.row_version),
            request_id=str(request_id)[:80],
            payload_json=payload,
            payload_hash=canonical_hash(payload),
            occurred_at=current_time,
            expires_at=current_time + timedelta(days=max(1, int(retention_days))),
        )
        db.add(row)
        rows.append(row)

    if reset_reason:
        _append(
            "stream.reset",
            {
                "reason": str(reset_reason)[:160],
                "snapshot_url": f"/api/v1/bid-assessments/{assessment_id}",
            },
        )
    _append("assessment.snapshot", {"snapshot": build_assessment_snapshot(db, assessment)})
    db.flush()
    return previous_sequence, rows


def resolve_sse_start_sequence(
    db: Session,
    *,
    assessment_id: str,
    last_event_id: str | None,
    request_id: str,
    retention_days: int = 7,
    now: datetime | None = None,
) -> int:
    current_time = now or utc_now()
    if last_event_id:
        cursor = (
            db.query(BidPublicEvent)
            .filter(
                BidPublicEvent.assessment_id == assessment_id,
                BidPublicEvent.event_id == last_event_id,
            )
            .one_or_none()
        )
        if cursor is not None and as_utc(cursor.expires_at) > as_utc(current_time):
            return int(cursor.sequence_no)
        previous_sequence, _ = append_stream_control_events(
            db,
            assessment_id=assessment_id,
            request_id=request_id,
            reset_reason="last_event_id_outside_retention",
            retention_days=retention_days,
            now=current_time,
        )
        return previous_sequence

    latest_snapshot = (
        db.query(BidPublicEvent)
        .filter(
            BidPublicEvent.assessment_id == assessment_id,
            BidPublicEvent.event_type == "assessment.snapshot",
            BidPublicEvent.expires_at > current_time,
        )
        .order_by(BidPublicEvent.sequence_no.desc())
        .first()
    )
    if latest_snapshot is not None:
        return max(0, int(latest_snapshot.sequence_no) - 1)
    previous_sequence, _ = append_stream_control_events(
        db,
        assessment_id=assessment_id,
        request_id=request_id,
        retention_days=retention_days,
        now=current_time,
    )
    return previous_sequence


def list_public_events_after(
    db: Session,
    *,
    assessment_id: str,
    sequence_no: int,
    limit: int = 100,
    now: datetime | None = None,
) -> list[BidPublicEvent]:
    return (
        db.query(BidPublicEvent)
        .filter(
            BidPublicEvent.assessment_id == assessment_id,
            BidPublicEvent.sequence_no > int(sequence_no),
            BidPublicEvent.expires_at > (now or utc_now()),
        )
        .order_by(BidPublicEvent.sequence_no.asc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )


def public_event_payload(event: BidPublicEvent) -> dict[str, Any]:
    occurred_at = as_utc(event.occurred_at)
    return {
        "event_id": event.event_id,
        "occurred_at": occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "assessment_id": event.assessment_id,
        "resource": {
            "type": event.resource_type,
            "id": event.resource_id,
            "version": int(event.resource_version),
        },
        "payload": event.payload_json,
        "request_id": event.request_id,
    }


def format_public_event_sse(event: BidPublicEvent, *, retry_ms: int = 5000) -> str:
    data = json.dumps(public_event_payload(event), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.event_type}\nretry: {int(retry_ms)}\ndata: {data}\n\n"
