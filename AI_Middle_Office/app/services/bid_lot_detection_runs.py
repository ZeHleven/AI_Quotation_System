"""Manifest ParseSet readiness and durable LotDetectionRun scheduling."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidDocumentManifest,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
)
from app.models.bid_assessment_lots import (
    BidLotDetectionAttempt,
    BidLotDetectionEvent,
    BidLotDetectionHead,
    BidLotDetectionRun,
)
from app.models.bid_assessment_eventing import BidOutboxEvent
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    append_outbox_event,
    canonical_hash,
    process_outbox_event_once,
)
from app.services.bid_parse_quality_gate import (
    BidParseQualityGateBlocked,
    BidParseQualityGateError,
    assert_parse_run_consumer_allowed,
)


MANIFEST_PARSE_COORDINATOR_CONSUMER = "bid-manifest-parse-coordinator-v1"
PARSE_TERMINAL_EVENTS = {
    "bid.document.parsed.v1",
    "bid.document.parse_failed.v1",
}


@dataclass(frozen=True)
class ManifestParseSet:
    manifest_id: str
    manifest_version: int
    manifest_hash: str
    status: str
    parse_set_hash: str
    document_count: int
    partial_count: int
    documents: tuple[dict[str, Any], ...]
    blocking_reasons: tuple[str, ...]


@dataclass(frozen=True)
class LotDetectionSchedule:
    run: BidLotDetectionRun
    created: bool
    head_changed: bool
    ready_event_id: str | None
    request_event_id: str | None


class BidLotDetectionSchedulingError(RuntimeError):
    code = "BID_LOT_DETECTION_SCHEDULING_FAILED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_manifest_parse_set(
    db: Session,
    *,
    manifest_id: str,
) -> ManifestParseSet:
    manifest = (
        db.query(BidDocumentManifest)
        .filter(BidDocumentManifest.id == manifest_id)
        .one_or_none()
    )
    if manifest is None:
        raise BidLotDetectionSchedulingError("BID_MANIFEST_NOT_FOUND")
    rows = (
        db.query(
            BidManifestDocument,
            BidDocumentParseHead,
            BidDocumentParseRun,
        )
        .outerjoin(
            BidDocumentParseHead,
            BidDocumentParseHead.document_version_id
            == BidManifestDocument.document_version_id,
        )
        .outerjoin(
            BidDocumentParseRun,
            BidDocumentParseRun.id == BidDocumentParseHead.current_run_id,
        )
        .filter(BidManifestDocument.manifest_id == manifest.id)
        .order_by(
            BidManifestDocument.order_no.asc(),
            BidManifestDocument.document_version_id.asc(),
        )
        .all()
    )
    documents: list[dict[str, Any]] = []
    blocking: list[str] = []
    partial_count = 0
    for member, head, run in rows:
        if head is None or run is None:
            status = "not_requested"
            blocking.append(f"parse_missing:{member.document_version_id}")
            run_projection = None
        else:
            status = str(run.status)
            quality_gate = None
            quality_gate_invalid = False
            quality_gate_blocked = False
            if status in {"succeeded", "partial"}:
                try:
                    quality_gate = assert_parse_run_consumer_allowed(
                        run,
                        consumer="lot_detection",
                    )
                except BidParseQualityGateBlocked:
                    quality_gate_blocked = True
                except BidParseQualityGateError:
                    quality_gate_invalid = True
            run_projection = {
                "parse_run_id": str(run.id),
                "status": status,
                "result_hash": str(run.result_hash) if run.result_hash else None,
                "parser_profile_version": str(run.parser_profile_version),
                "quality_score": (
                    int(run.quality_score) if run.quality_score is not None else None
                ),
            }
            if quality_gate is not None:
                run_projection["quality_gate"] = {
                    "status": str(quality_gate["status"]),
                    "result_hash": str(quality_gate["result_hash"]),
                    "lot_detection_allowed": bool(
                        quality_gate["consumer_gates"]["lot_detection"]
                    ),
                }
            if quality_gate_invalid:
                blocking.append(
                    f"parse_quality_gate_invalid:{member.document_version_id}"
                )
            elif quality_gate_blocked:
                blocking.append(
                    f"parse_quality_gate_blocked_lot_detection:{member.document_version_id}"
                )
            if status in {"queued", "running"}:
                blocking.append(f"parse_pending:{member.document_version_id}")
            elif status == "failed":
                blocking.append(f"parse_failed:{member.document_version_id}")
            elif status == "partial":
                partial_count += 1
                if int(run.quality_score or 0) < int(
                    settings.bid_document_parse_min_lot_quality_score
                ):
                    blocking.append(
                        f"parse_quality_below_lot_gate:{member.document_version_id}"
                    )
        documents.append(
            {
                "document_version_id": str(member.document_version_id),
                "role": str(member.role),
                "order_no": int(member.order_no),
                "parse": run_projection,
            }
        )
    if not rows:
        blocking.append("manifest_has_no_documents")
    has_failure = any(
        reason.startswith("parse_failed:")
        or reason.startswith("parse_quality_below_lot_gate:")
        or reason.startswith("parse_quality_gate_invalid:")
        or reason.startswith("parse_quality_gate_blocked_lot_detection:")
        for reason in blocking
    )
    status = "failed" if has_failure else ("pending" if blocking else "ready")
    parse_set_hash = canonical_hash(
        {
            "manifest_id": str(manifest.id),
            "manifest_hash": str(manifest.manifest_hash),
            "documents": documents,
        }
    )
    return ManifestParseSet(
        manifest_id=str(manifest.id),
        manifest_version=int(manifest.version),
        manifest_hash=str(manifest.manifest_hash),
        status=status,
        parse_set_hash=parse_set_hash,
        document_count=len(rows),
        partial_count=partial_count,
        documents=tuple(documents),
        blocking_reasons=tuple(blocking),
    )


def _lot_detection_input_hash(parse_set: ManifestParseSet) -> str:
    return canonical_hash(
        {
            "manifest_id": parse_set.manifest_id,
            "parse_set_hash": parse_set.parse_set_hash,
            "detector_version": settings.bid_lot_detector_version,
            "rule_set_version": settings.bid_lot_rule_set_version,
            "normalizer_version": settings.bid_lot_normalizer_version,
        }
    )


def ensure_lot_detection_run(
    db: Session,
    *,
    parse_set: ManifestParseSet,
    assessment_id: str,
    request_id: str,
    causation_event_id: str | None,
    requested_at: datetime | None = None,
) -> LotDetectionSchedule:
    if parse_set.status != "ready":
        raise BidLotDetectionSchedulingError("BID_MANIFEST_PARSE_SET_NOT_READY")
    current_time = requested_at or _utc_now()
    input_hash = _lot_detection_input_hash(parse_set)
    run = (
        db.query(BidLotDetectionRun)
        .filter(
            BidLotDetectionRun.manifest_id == parse_set.manifest_id,
            BidLotDetectionRun.input_hash == input_hash,
        )
        .with_for_update()
        .one_or_none()
    )
    created = run is None
    if run is None:
        run = BidLotDetectionRun(
            id=f"ldr_{uuid.uuid4().hex}",
            manifest_id=parse_set.manifest_id,
            parse_set_hash=parse_set.parse_set_hash,
            detector_version=settings.bid_lot_detector_version,
            rule_set_version=settings.bid_lot_rule_set_version,
            normalizer_version=settings.bid_lot_normalizer_version,
            input_hash=input_hash,
            status="queued",
            retryable=True,
            requested_at=current_time,
            candidate_count=0,
            row_version=1,
        )
        db.add(run)
        db.flush()
        payload = {
            "manifest_id": parse_set.manifest_id,
            "parse_set_hash": parse_set.parse_set_hash,
            "input_hash": input_hash,
        }
        db.add(
            BidLotDetectionEvent(
                id=f"lde_{uuid.uuid4().hex}",
                run_id=str(run.id),
                attempt_id=None,
                sequence_no=1,
                event_type="lot_detection.requested",
                from_status=None,
                to_status="queued",
                payload_json=payload,
                payload_hash=canonical_hash(payload),
            )
        )
        db.flush()

    head = (
        db.query(BidLotDetectionHead)
        .filter(BidLotDetectionHead.manifest_id == parse_set.manifest_id)
        .with_for_update()
        .one_or_none()
    )
    head_changed = head is None or str(head.current_run_id) != str(run.id)
    if head is None:
        db.add(
            BidLotDetectionHead(
                manifest_id=parse_set.manifest_id,
                current_run_id=str(run.id),
                row_version=1,
            )
        )
    elif head_changed:
        previous = (
            db.query(BidLotDetectionRun)
            .filter(BidLotDetectionRun.id == head.current_run_id)
            .with_for_update()
            .one_or_none()
        )
        if previous is not None and str(previous.status) in {"queued", "running"}:
            previous_status = str(previous.status)
            previous.status = "stale"
            previous.retryable = False
            previous.finished_at = current_time
            previous.row_version = int(previous.row_version) + 1
            active_attempts = (
                db.query(BidLotDetectionAttempt)
                .filter(
                    BidLotDetectionAttempt.run_id == previous.id,
                    BidLotDetectionAttempt.status.in_(("leased", "running")),
                )
                .with_for_update()
                .all()
            )
            for attempt in active_attempts:
                attempt.status = "cancelled"
                attempt.retryable = False
                attempt.finished_at = current_time
            sequence_no = int(
                db.query(func.max(BidLotDetectionEvent.sequence_no))
                .filter(BidLotDetectionEvent.run_id == previous.id)
                .scalar()
                or 0
            ) + 1
            stale_payload = {
                "reason": "parse_set_superseded",
                "replacement_run_id": str(run.id),
                "cancelled_attempt_ids": sorted(
                    str(attempt.id) for attempt in active_attempts
                ),
            }
            db.add(
                BidLotDetectionEvent(
                    id=f"lde_{uuid.uuid4().hex}",
                    run_id=str(previous.id),
                    attempt_id=None,
                    sequence_no=sequence_no,
                    event_type="lot_detection.input_stale",
                    from_status=previous_status,
                    to_status="stale",
                    payload_json=stale_payload,
                    payload_hash=canonical_hash(stale_payload),
                )
            )
        head.current_run_id = str(run.id)
        head.row_version = int(head.row_version) + 1
    db.flush()

    ready_event_id = request_event_id = None
    if created:
        ready_event = append_outbox_event(
            db,
            event_type="bid.manifest.parse_set_ready.v1",
            producer="bid-manifest-parse-coordinator-v1",
            aggregate_type="manifest",
            aggregate_id=parse_set.manifest_id,
            aggregate_version=parse_set.manifest_version,
            assessment_id=assessment_id,
            request_id=request_id,
            causation_event_id=causation_event_id,
            payload_schema="bid.manifest.parse_set_ready.v1.payload",
            payload={
                "manifest_id": parse_set.manifest_id,
                "parse_set_hash": parse_set.parse_set_hash,
                "document_count": parse_set.document_count,
                "partial_count": parse_set.partial_count,
            },
            dedupe_key=f"manifest-parse-set-ready:{parse_set.manifest_id}:{parse_set.parse_set_hash}",
            occurred_at=current_time,
        )
        ready_event_id = str(ready_event.event_id)
        request_event = append_outbox_event(
            db,
            event_type="bid.lot_detection.requested.v1",
            producer="bid-manifest-parse-coordinator-v1",
            aggregate_type="lot_detection_run",
            aggregate_id=str(run.id),
            aggregate_version=int(run.row_version),
            assessment_id=assessment_id,
            request_id=request_id,
            causation_event_id=ready_event_id,
            payload_schema="bid.lot_detection.requested.v1.payload",
            payload={
                "detection_run_id": str(run.id),
                "manifest_id": parse_set.manifest_id,
                "parse_set_hash": parse_set.parse_set_hash,
                "input_hash": input_hash,
            },
            dedupe_key=f"lot-detection-requested:{run.id}",
            occurred_at=current_time,
        )
        request_event_id = str(request_event.event_id)
    return LotDetectionSchedule(
        run=run,
        created=created,
        head_changed=head_changed,
        ready_event_id=ready_event_id,
        request_event_id=request_event_id,
    )


def consume_document_parse_terminal_for_lots(
    db: Session,
    *,
    event_id: str,
) -> ProcessedEventResult:
    """Re-evaluate every Manifest that references the parsed DocumentVersion."""

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) not in PARSE_TERMINAL_EVENTS:
            return {"ignored": True, "event_type": str(event.event_type)}
        payload = dict(event.payload_json or {})
        document_version_id = str(payload.get("document_version_id") or "")
        if not document_version_id:
            raise BidLotDetectionSchedulingError(
                "BID_PARSE_TERMINAL_EVENT_DOCUMENT_MISSING"
            )
        manifests = (
            session.query(BidDocumentManifest)
            .join(
                BidManifestDocument,
                BidManifestDocument.manifest_id == BidDocumentManifest.id,
            )
            .filter(BidManifestDocument.document_version_id == document_version_id)
            .order_by(BidDocumentManifest.id.asc())
            .with_for_update()
            .all()
        )
        scheduled: list[str] = []
        states: dict[str, str] = {}
        for manifest in manifests:
            parse_set = build_manifest_parse_set(
                session,
                manifest_id=str(manifest.id),
            )
            states[str(manifest.id)] = parse_set.status
            if parse_set.status != "ready":
                continue
            schedule = ensure_lot_detection_run(
                session,
                parse_set=parse_set,
                assessment_id=str(manifest.assessment_id),
                request_id=str(event.request_id),
                causation_event_id=str(event.event_id),
                requested_at=event.occurred_at,
            )
            if schedule.created:
                scheduled.append(str(schedule.run.id))
        return {
            "ignored": False,
            "document_version_id": document_version_id,
            "manifest_states": states,
            "scheduled_detection_run_ids": scheduled,
        }

    return process_outbox_event_once(
        db,
        consumer_name=MANIFEST_PARSE_COORDINATOR_CONSUMER,
        event_id=event_id,
        handler=_handler,
    )
