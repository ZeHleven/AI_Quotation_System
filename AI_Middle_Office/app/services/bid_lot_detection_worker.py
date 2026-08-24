"""Durable, evidence-only LotDetection Worker for Phase 2."""
from __future__ import annotations

import socket
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidLotCandidate,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidEvidenceFragment,
)
from app.models.bid_assessment_eventing import BidOutboxEvent
from app.models.bid_assessment_lots import (
    BidLotCandidateEvidence,
    BidLotDetectionAttempt,
    BidLotDetectionEvent,
    BidLotDetectionRun,
)
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    process_outbox_event_once,
)
from app.services.bid_lot_detection_runs import build_manifest_parse_set
from app.services.bid_lot_detector import (
    DetectedLotCandidate,
    LotDetectionEvidenceInput,
    build_whole_manifest_scope_candidate,
    detect_lot_candidates,
)


LOT_DETECTION_REQUEST_EVENT = "bid.lot_detection.requested.v1"
LOT_DETECTION_CONSUMER = "bid-lot-detection-worker-v1"
LOT_SELECTABLE_ASSESSMENT_STATES = frozenset({"preparing", "awaiting_lot_selection"})


class BidLotDetectionWorkerError(RuntimeError):
    code = "BID_LOT_DETECTION_WORKER_ERROR"


class BidLotDetectionLeaseConflict(BidLotDetectionWorkerError):
    code = "BID_LOT_DETECTION_LEASE_CONFLICT"


class BidLotDetectionFencingRejected(BidLotDetectionWorkerError):
    code = "BID_LOT_DETECTION_FENCING_REJECTED"


class BidLotDetectionInputStale(BidLotDetectionWorkerError):
    code = "BID_LOT_DETECTION_INPUT_STALE"


@dataclass(frozen=True)
class LotDetectionClaim:
    run_id: str
    attempt_id: str
    attempt_no: int
    worker_id: str
    fencing_token: int


@dataclass(frozen=True)
class LotDetectionExecutionResult:
    event_id: str
    detection_run_id: str | None
    status: str
    candidate_count: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_sequence(db: Session, run_id: str) -> int:
    return int(
        db.query(func.max(BidLotDetectionEvent.sequence_no))
        .filter(BidLotDetectionEvent.run_id == run_id)
        .scalar()
        or 0
    ) + 1


def _append_event(
    db: Session,
    *,
    run_id: str,
    attempt_id: str | None,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any],
) -> None:
    db.add(
        BidLotDetectionEvent(
            id=f"lde_{uuid.uuid4().hex}",
            run_id=run_id,
            attempt_id=attempt_id,
            sequence_no=_next_sequence(db, run_id),
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            payload_json=payload,
            payload_hash=canonical_hash(payload),
        )
    )
    db.flush()


def consume_lot_detection_requested_event(
    db: Session,
    *,
    event_id: str,
) -> ProcessedEventResult:
    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) != LOT_DETECTION_REQUEST_EVENT:
            return {"ignored": True, "event_type": str(event.event_type)}
        payload = dict(event.payload_json or {})
        required = ("detection_run_id", "manifest_id", "parse_set_hash", "input_hash")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise BidLotDetectionWorkerError(
                f"BID_LOT_DETECTION_EVENT_PAYLOAD_MISSING:{','.join(missing)}"
            )
        run = (
            session.query(BidLotDetectionRun)
            .filter(BidLotDetectionRun.id == str(payload["detection_run_id"]))
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            raise BidLotDetectionWorkerError("BID_LOT_DETECTION_RUN_NOT_FOUND")
        expected = {
            "aggregate_type": "lot_detection_run",
            "aggregate_id": str(run.id),
            "manifest_id": str(run.manifest_id),
            "parse_set_hash": str(run.parse_set_hash),
            "input_hash": str(run.input_hash),
        }
        actual = {
            "aggregate_type": str(event.aggregate_type),
            "aggregate_id": str(event.aggregate_id),
            "manifest_id": str(payload["manifest_id"]),
            "parse_set_hash": str(payload["parse_set_hash"]),
            "input_hash": str(payload["input_hash"]),
        }
        if actual != expected:
            raise BidLotDetectionWorkerError("BID_LOT_DETECTION_EVENT_MISMATCH")
        return {"ignored": False, "detection_run_id": str(run.id)}

    return process_outbox_event_once(
        db,
        consumer_name=LOT_DETECTION_CONSUMER,
        event_id=event_id,
        handler=_handler,
    )


def _claim_run(
    db: Session,
    *,
    run_id: str,
    worker_id: str,
    now: datetime,
) -> LotDetectionClaim | None:
    run = (
        db.query(BidLotDetectionRun)
        .filter(BidLotDetectionRun.id == run_id)
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise BidLotDetectionWorkerError("BID_LOT_DETECTION_RUN_NOT_FOUND")
    if str(run.status) in {"succeeded", "failed", "stale"}:
        return None
    attempts = (
        db.query(BidLotDetectionAttempt)
        .filter(BidLotDetectionAttempt.run_id == run.id)
        .order_by(BidLotDetectionAttempt.attempt_no.desc())
        .with_for_update()
        .all()
    )
    latest = attempts[0] if attempts else None
    if latest is not None and str(latest.status) in {"leased", "running"}:
        if as_utc(latest.lease_until) > now:
            raise BidLotDetectionLeaseConflict()
        latest.status = "expired"
        latest.finished_at = now
        latest.retryable = True
        run.status = "queued"
        run.started_at = None
        run.row_version = int(run.row_version) + 1
        _append_event(
            db,
            run_id=str(run.id),
            attempt_id=str(latest.id),
            event_type="lot_detection.attempt_expired",
            from_status="running",
            to_status="queued",
            payload={"attempt_no": int(latest.attempt_no)},
        )
    attempt_no = int(latest.attempt_no if latest is not None else 0) + 1
    if attempt_no > max(1, int(settings.bid_lot_detection_max_attempts)):
        run.status = "failed"
        run.retryable = False
        run.finished_at = now
        run.error_code = "BID_LOT_DETECTION_ATTEMPTS_EXHAUSTED"
        run.row_version = int(run.row_version) + 1
        _append_event(
            db,
            run_id=str(run.id),
            attempt_id=str(latest.id) if latest is not None else None,
            event_type="lot_detection.failed",
            from_status="queued",
            to_status="failed",
            payload={"error_code": run.error_code, "retryable": False},
        )
        return None
    fencing_token = int(
        db.query(func.max(BidLotDetectionAttempt.fencing_token))
        .filter(BidLotDetectionAttempt.run_id == run.id)
        .scalar()
        or 0
    ) + 1
    attempt = BidLotDetectionAttempt(
        id=f"lda_{uuid.uuid4().hex}",
        run_id=str(run.id),
        attempt_no=attempt_no,
        status="running",
        lease_owner=worker_id[:128],
        lease_until=now
        + timedelta(seconds=max(5, int(settings.bid_lot_detection_lease_seconds))),
        heartbeat_at=now,
        fencing_token=fencing_token,
        retryable=False,
        started_at=now,
    )
    db.add(attempt)
    previous_status = str(run.status)
    run.status = "running"
    run.started_at = now
    run.finished_at = None
    run.error_code = None
    run.row_version = int(run.row_version) + 1
    db.flush()
    _append_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="lot_detection.attempt_started",
        from_status=previous_status,
        to_status="running",
        payload={"attempt_no": attempt_no, "fencing_token": fencing_token},
    )
    return LotDetectionClaim(
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        attempt_no=attempt_no,
        worker_id=str(attempt.lease_owner),
        fencing_token=fencing_token,
    )


def _locked_claim(
    db: Session,
    *,
    claim: LotDetectionClaim,
    now: datetime,
) -> tuple[BidLotDetectionRun, BidLotDetectionAttempt]:
    run = (
        db.query(BidLotDetectionRun)
        .filter(BidLotDetectionRun.id == claim.run_id)
        .with_for_update()
        .one_or_none()
    )
    attempt = (
        db.query(BidLotDetectionAttempt)
        .filter(BidLotDetectionAttempt.id == claim.attempt_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        run is None
        or attempt is None
        or str(run.status) != "running"
        or str(attempt.status) != "running"
        or str(attempt.run_id) != str(run.id)
        or str(attempt.lease_owner) != claim.worker_id
        or int(attempt.fencing_token) != claim.fencing_token
        or as_utc(attempt.lease_until) <= now
    ):
        raise BidLotDetectionFencingRejected()
    return run, attempt


def _load_evidence_input(
    db: Session,
    *,
    run: BidLotDetectionRun,
) -> tuple[LotDetectionEvidenceInput, ...]:
    parse_set = build_manifest_parse_set(db, manifest_id=str(run.manifest_id))
    if parse_set.status != "ready" or parse_set.parse_set_hash != str(run.parse_set_hash):
        raise BidLotDetectionInputStale()
    rows = (
        db.query(BidManifestDocument, BidEvidenceFragment)
        .join(
            BidDocumentParseHead,
            BidDocumentParseHead.document_version_id
            == BidManifestDocument.document_version_id,
        )
        .join(
            BidEvidenceFragment,
            and_(
                BidEvidenceFragment.parse_run_id
                == BidDocumentParseHead.current_run_id,
                BidEvidenceFragment.document_version_id
                == BidManifestDocument.document_version_id,
            ),
        )
        .filter(BidManifestDocument.manifest_id == run.manifest_id)
        .order_by(
            BidManifestDocument.order_no.asc(),
            BidEvidenceFragment.ordinal.asc(),
            BidEvidenceFragment.id.asc(),
        )
        .all()
    )
    result: list[LotDetectionEvidenceInput] = []
    for member, fragment in rows:
        locator = dict(fragment.locator_json or {})
        if not _is_citable_detection_fragment(locator):
            continue
        result.append(
            LotDetectionEvidenceInput(
                evidence_id=str(fragment.id),
                document_version_id=str(fragment.document_version_id),
                role=str(member.role),
                text=str(fragment.normalized_text),
                locator=locator,
            )
        )
    return tuple(result)


def _is_citable_detection_fragment(locator: dict[str, Any]) -> bool:
    """Gate v2 hierarchy while preserving role-less Phase 2 v1 evidence."""

    fragment_role = locator.get("fragment_role")
    if fragment_role is None:
        return True
    return fragment_role == "evidence_atom" and locator.get("is_citable") is True


def _complete_run(
    db: Session,
    *,
    claim: LotDetectionClaim,
    candidates: tuple[DetectedLotCandidate, ...],
    request_id: str,
    causation_event_id: str,
    now: datetime,
) -> LotDetectionExecutionResult:
    run, attempt = _locked_claim(db, claim=claim, now=now)
    current_parse_set = build_manifest_parse_set(
        db,
        manifest_id=str(run.manifest_id),
    )
    if (
        current_parse_set.status != "ready"
        or current_parse_set.parse_set_hash != str(run.parse_set_hash)
    ):
        raise BidLotDetectionInputStale()
    if (
        db.query(BidLotCandidate.id)
        .filter(BidLotCandidate.detection_run_id == run.id)
        .first()
        is not None
    ):
        raise BidLotDetectionWorkerError("BID_LOT_DETECTION_RESULT_ALREADY_WRITTEN")
    evidence_rows = {
        str(row.id): row
        for row in (
            db.query(BidEvidenceFragment)
            .filter(
                BidEvidenceFragment.id.in_(
                    [
                        link.evidence_id
                        for candidate in candidates
                        for link in candidate.evidence
                    ]
                )
            )
            .all()
        )
    } if candidates else {}
    for candidate in candidates:
        if not candidate.evidence:
            raise BidLotDetectionWorkerError("BID_LOT_CANDIDATE_EVIDENCE_REQUIRED")
        lot_id = f"lot_{uuid.uuid4().hex}"
        db.add(
            BidLotCandidate(
                id=lot_id,
                manifest_id=str(run.manifest_id),
                detection_run_id=str(run.id),
                lot_code=candidate.lot_code,
                lot_name=candidate.lot_name,
                scope_summary=candidate.scope_summary,
                normalized_lot_key=candidate.normalized_lot_key,
                source_status=candidate.source_status,
                confidence=Decimal(candidate.confidence_score),
                confidence_level=candidate.confidence_level,
                candidate_hash=candidate.candidate_hash,
                warnings_json=list(candidate.warnings),
            )
        )
        for display_order, link in enumerate(candidate.evidence):
            fragment = evidence_rows.get(link.evidence_id)
            if fragment is None or str(fragment.document_version_id) != str(
                link.document_version_id
            ):
                raise BidLotDetectionWorkerError("BID_LOT_CANDIDATE_EVIDENCE_INVALID")
            db.add(
                BidLotCandidateEvidence(
                    lot_candidate_id=lot_id,
                    evidence_id=link.evidence_id,
                    manifest_id=str(run.manifest_id),
                    document_version_id=link.document_version_id,
                    support_role=link.support_role,
                    display_order=display_order,
                    display_label=link.display_label,
                )
            )
    db.flush()

    result_hash = canonical_hash([asdict(candidate) for candidate in candidates])
    run.status = "succeeded"
    run.retryable = False
    run.finished_at = now
    run.result_hash = result_hash
    run.candidate_count = len(candidates)
    run.warnings_json = []
    run.error_code = None
    run.row_version = int(run.row_version) + 1
    attempt.status = "succeeded"
    attempt.retryable = False
    attempt.finished_at = now
    db.flush()
    _append_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="lot_detection.completed",
        from_status="running",
        to_status="succeeded",
        payload={"candidate_count": len(candidates), "result_hash": result_hash},
    )

    manifest = (
        db.query(BidDocumentManifest)
        .filter(BidDocumentManifest.id == run.manifest_id)
        .one()
    )
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == manifest.assessment_id)
        .with_for_update()
        .one()
    )
    scope_exists = (
        db.query(BidAssessmentScope.id)
        .filter(BidAssessmentScope.assessment_id == assessment.id)
        .first()
        is not None
    )
    is_current = str(assessment.current_manifest_id or "") == str(manifest.id)
    selection_required = bool(
        candidates
        and is_current
        and not scope_exists
        and str(assessment.business_status) in LOT_SELECTABLE_ASSESSMENT_STATES
    )
    previous_status = str(assessment.business_status)
    if selection_required and previous_status == "preparing":
        assessment.business_status = "awaiting_lot_selection"
        assessment.row_version = int(assessment.row_version) + 1
    db.flush()
    outbox = append_outbox_event(
        db,
        event_type="bid.lots.detected.v1",
        producer="bid-lot-detection-worker-v1",
        aggregate_type="lot_detection_run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        causation_event_id=causation_event_id,
        payload_schema="bid.lots.detected.v1.payload",
        payload={
            "detection_run_id": str(run.id),
            "manifest_id": str(run.manifest_id),
            "parse_set_hash": str(run.parse_set_hash),
            "candidate_count": len(candidates),
            "selection_required": selection_required,
            "lots_url": f"/api/v1/bid-assessments/{assessment.id}/lots?manifest_id={manifest.id}",
            "resource_version": int(assessment.row_version),
        },
        dedupe_key=f"lots-detected:{run.id}",
        occurred_at=now,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref="service:bid-lot-detection-worker-v1",
        action="lot_detection.complete",
        entity_type="lot_detection_run",
        entity_id=str(run.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before={"assessment_status": previous_status},
        after={
            "assessment_status": str(assessment.business_status),
            "candidate_count": len(candidates),
            "result_hash": result_hash,
        },
        metadata={"manifest_id": str(manifest.id)},
        correlation_id=str(outbox.event_id),
        occurred_at=now,
    )
    return LotDetectionExecutionResult(
        event_id=causation_event_id,
        detection_run_id=str(run.id),
        status="succeeded",
        candidate_count=len(candidates),
    )


def _mark_stale(
    db: Session,
    *,
    claim: LotDetectionClaim,
    now: datetime,
) -> None:
    run, attempt = _locked_claim(db, claim=claim, now=now)
    run.status = "stale"
    run.retryable = False
    run.finished_at = now
    run.row_version = int(run.row_version) + 1
    attempt.status = "cancelled"
    attempt.retryable = False
    attempt.finished_at = now
    _append_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="lot_detection.input_stale",
        from_status="running",
        to_status="stale",
        payload={"reason": "parse_set_changed"},
    )


def _fail_run(
    db: Session,
    *,
    claim: LotDetectionClaim,
    error_code: str,
    request_id: str,
    causation_event_id: str,
    now: datetime,
) -> str:
    run, attempt = _locked_claim(db, claim=claim, now=now)
    stable_error = str(error_code or "BID_LOT_DETECTION_FAILED")[:100]
    can_requeue = int(attempt.attempt_no) < max(
        1,
        int(settings.bid_lot_detection_max_attempts),
    )
    attempt.status = "failed"
    attempt.error_code = stable_error
    attempt.retryable = can_requeue
    attempt.finished_at = now
    if can_requeue:
        run.status = "queued"
        run.retryable = True
        run.started_at = None
        run.finished_at = None
        run.error_code = None
        run.row_version = int(run.row_version) + 1
        _append_event(
            db,
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            event_type="lot_detection.attempt_failed",
            from_status="running",
            to_status="queued",
            payload={
                "error_code": stable_error,
                "retryable": True,
                "attempt_no": int(attempt.attempt_no),
            },
        )
        return "queued"

    run.status = "failed"
    run.retryable = False
    run.finished_at = now
    run.error_code = stable_error
    run.row_version = int(run.row_version) + 1
    attempt.retryable = False
    _append_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="lot_detection.failed",
        from_status="running",
        to_status="failed",
        payload={"error_code": stable_error, "retryable": False},
    )
    _append_failure_outbox(
        db,
        run=run,
        attempt_count=int(attempt.attempt_no),
        request_id=request_id,
        causation_event_id=causation_event_id,
        now=now,
    )
    return "failed"


def _append_failure_outbox(
    db: Session,
    *,
    run: BidLotDetectionRun,
    attempt_count: int,
    request_id: str,
    causation_event_id: str,
    now: datetime,
) -> None:
    dedupe_key = f"lot-detection-failed:{run.id}:{attempt_count}"
    if (
        db.query(BidOutboxEvent.event_id)
        .filter(BidOutboxEvent.dedupe_key == dedupe_key)
        .first()
        is not None
    ):
        return
    manifest = (
        db.query(BidDocumentManifest)
        .filter(BidDocumentManifest.id == run.manifest_id)
        .one()
    )
    append_outbox_event(
        db,
        event_type="bid.lot_detection.failed.v1",
        producer="bid-lot-detection-worker-v1",
        aggregate_type="lot_detection_run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=str(manifest.assessment_id),
        request_id=request_id,
        causation_event_id=causation_event_id,
        payload_schema="bid.lot_detection.failed.v1.payload",
        payload={
            "detection_run_id": str(run.id),
            "manifest_id": str(run.manifest_id),
            "error_code": str(run.error_code or "BID_LOT_DETECTION_FAILED"),
            "retryable": bool(run.retryable),
            "attempt_count": int(attempt_count),
            "operation_type": "lot_detection",
            "resource_id": str(run.id),
        },
        dedupe_key=dedupe_key,
        occurred_at=now,
    )


def execute_lot_detection_request(
    *,
    event_id: str,
    session_factory: Callable[[], Session] = SessionLocal,
    allow_whole_manifest_scope: bool = False,
) -> LotDetectionExecutionResult:
    metadata_db = session_factory()
    try:
        event = metadata_db.query(BidOutboxEvent).filter(BidOutboxEvent.event_id == event_id).one_or_none()
        if event is None:
            raise BidLotDetectionWorkerError("BID_OUTBOX_EVENT_NOT_FOUND")
        if str(event.event_type) != LOT_DETECTION_REQUEST_EVENT:
            return LotDetectionExecutionResult(event_id, None, "ignored")
        payload = dict(event.payload_json or {})
        run_id = str(payload.get("detection_run_id") or "")
        request_id = str(event.request_id)
    finally:
        metadata_db.close()
    now = _utc_now()
    claim_db = session_factory()
    try:
        try:
            with claim_db.begin():
                claim = _claim_run(
                    claim_db,
                    run_id=run_id,
                    worker_id=f"lot-worker:{socket.gethostname()}:{uuid.uuid4().hex[:8]}",
                    now=now,
                )
        except BidLotDetectionLeaseConflict:
            return LotDetectionExecutionResult(event_id, run_id, "already_running")
    finally:
        claim_db.close()
    if claim is None:
        status_db = session_factory()
        try:
            with status_db.begin():
                run = (
                    status_db.query(BidLotDetectionRun)
                    .filter(BidLotDetectionRun.id == run_id)
                    .with_for_update()
                    .one_or_none()
                )
                status = str(run.status) if run is not None else "not_found"
                if (
                    run is not None
                    and status == "failed"
                    and str(run.error_code or "")
                    == "BID_LOT_DETECTION_ATTEMPTS_EXHAUSTED"
                ):
                    attempt_count = int(
                        status_db.query(func.max(BidLotDetectionAttempt.attempt_no))
                        .filter(BidLotDetectionAttempt.run_id == run.id)
                        .scalar()
                        or settings.bid_lot_detection_max_attempts
                    )
                    _append_failure_outbox(
                        status_db,
                        run=run,
                        attempt_count=attempt_count,
                        request_id=request_id,
                        causation_event_id=event_id,
                        now=_utc_now(),
                    )
        finally:
            status_db.close()
        return LotDetectionExecutionResult(event_id, run_id, status)

    input_db = session_factory()
    try:
        run = input_db.query(BidLotDetectionRun).filter(BidLotDetectionRun.id == run_id).one()
        evidence = _load_evidence_input(input_db, run=run)
    except BidLotDetectionInputStale:
        input_db.close()
        stale_db = session_factory()
        try:
            with stale_db.begin():
                _mark_stale(stale_db, claim=claim, now=_utc_now())
        finally:
            stale_db.close()
        return LotDetectionExecutionResult(event_id, run_id, "stale")
    finally:
        if input_db.is_active:
            input_db.close()

    try:
        candidates = detect_lot_candidates(evidence)
        if not candidates and allow_whole_manifest_scope:
            fallback = build_whole_manifest_scope_candidate(evidence)
            candidates = (fallback,) if fallback is not None else ()
        complete_db = session_factory()
        try:
            with complete_db.begin():
                return _complete_run(
                    complete_db,
                    claim=claim,
                    candidates=candidates,
                    request_id=request_id,
                    causation_event_id=event_id,
                    now=_utc_now(),
                )
        finally:
            complete_db.close()
    except BidLotDetectionInputStale:
        stale_db = session_factory()
        try:
            with stale_db.begin():
                _mark_stale(stale_db, claim=claim, now=_utc_now())
        finally:
            stale_db.close()
        return LotDetectionExecutionResult(event_id, run_id, "stale")
    except BidLotDetectionFencingRejected:
        raise
    except Exception:
        failure_db = session_factory()
        try:
            with failure_db.begin():
                failure_status = _fail_run(
                    failure_db,
                    claim=claim,
                    error_code="BID_LOT_DETECTION_FAILED",
                    request_id=request_id,
                    causation_event_id=event_id,
                    now=_utc_now(),
                )
        finally:
            failure_db.close()
        return LotDetectionExecutionResult(event_id, run_id, failure_status)


def process_queued_lot_detection_runs(
    *,
    limit: int = 20,
    session_factory: Callable[[], Session] = SessionLocal,
    allow_whole_manifest_scope: bool = False,
) -> list[LotDetectionExecutionResult]:
    db = session_factory()
    try:
        run_ids = [
            str(row[0])
            for row in (
                db.query(BidLotDetectionRun.id)
                .filter(BidLotDetectionRun.status == "queued")
                .order_by(BidLotDetectionRun.requested_at.asc())
                .limit(max(1, min(int(limit), 100)))
                .all()
            )
        ]
        event_ids = {
            str(row.aggregate_id): str(row.event_id)
            for row in (
                db.query(BidOutboxEvent)
                .filter(
                    BidOutboxEvent.event_type == LOT_DETECTION_REQUEST_EVENT,
                    BidOutboxEvent.aggregate_id.in_(run_ids),
                )
                .order_by(BidOutboxEvent.occurred_at.asc())
                .all()
            )
        } if run_ids else {}
    finally:
        db.close()
    return [
        execute_lot_detection_request(
            event_id=event_ids[run_id],
            session_factory=session_factory,
            allow_whole_manifest_scope=allow_whole_manifest_scope,
        )
        for run_id in run_ids
        if run_id in event_ids
    ]
