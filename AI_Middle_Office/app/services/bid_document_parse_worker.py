"""Lease, fencing, completion, and recovery primitives for Document Worker."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import BidDocumentManifest, BidManifestDocument
from app.models.bid_assessment_documents import (
    CONTENT_SOURCES,
    EVIDENCE_LOCATOR_TYPES,
    OCR_STATES,
    PARSE_UNIT_STATES,
    PARSE_UNIT_TYPES,
    QUALITY_GRADES,
    BidDocumentParseAttempt,
    BidDocumentParseEvent,
    BidDocumentParseRun,
    BidDocumentParseUnit,
    BidEvidenceFragment,
)
from app.services.bid_assessment_eventing import (
    append_outbox_event,
    as_utc,
    canonical_hash,
)
from app.services.bid_parse_quality_gate import (
    BidParseQualityGateError,
    validate_quality_report,
)


class BidDocumentParseWorkerError(RuntimeError):
    code = "BID_DOCUMENT_PARSE_WORKER_ERROR"


class BidDocumentParseLeaseConflict(BidDocumentParseWorkerError):
    code = "BID_DOCUMENT_PARSE_LEASE_CONFLICT"


class BidDocumentParseFencingRejected(BidDocumentParseWorkerError):
    code = "BID_DOCUMENT_PARSE_FENCING_REJECTED"


class BidDocumentParseResultInvalid(BidDocumentParseWorkerError):
    code = "BID_DOCUMENT_PARSE_RESULT_INVALID"


@dataclass(frozen=True)
class BidDocumentParseClaim:
    run_id: str
    document_version_id: str
    attempt_id: str
    attempt_no: int
    worker_id: str
    fencing_token: int
    lease_until: datetime


@dataclass(frozen=True)
class ParseUnitResult:
    unit_key: str
    unit_type: str
    ordinal: int
    content_source: str
    status: str
    ocr_status: str
    page_no: int | None = None
    sheet_index: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    image_index: int | None = None
    section_path: tuple[str, ...] = ()
    text_hash: str | None = None
    text_length: int | None = None
    result_ref: str | None = None
    ocr_engine_version: str | None = None
    ocr_confidence: str | None = None
    warnings: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceFragmentResult:
    evidence_key: str
    unit_key: str
    locator_type: str
    locator: dict[str, Any]
    normalized_text: str
    ordinal: int = 0
    parent_key: str | None = None
    object_ref: str | None = None


@dataclass(frozen=True)
class DocumentParseResult:
    status: str
    quality_grade: str
    quality_score: int
    ocr_status: str
    units: tuple[ParseUnitResult, ...]
    evidence: tuple[EvidenceFragmentResult, ...]
    warnings: tuple[dict[str, Any], ...] = ()
    result_ref: str | None = None


@dataclass(frozen=True)
class DocumentParseCompletion:
    run_id: str
    status: str
    result_hash: str | None
    emitted_event_ids: tuple[str, ...]
    requeued: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_event_sequence(db: Session, run_id: str) -> int:
    return int(
        db.query(func.max(BidDocumentParseEvent.sequence_no))
        .filter(BidDocumentParseEvent.run_id == run_id)
        .scalar()
        or 0
    ) + 1


def _append_parse_event(
    db: Session,
    *,
    run_id: str,
    attempt_id: str | None,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any],
) -> BidDocumentParseEvent:
    row = BidDocumentParseEvent(
        id=f"dpe_{uuid.uuid4().hex}",
        run_id=run_id,
        attempt_id=attempt_id,
        sequence_no=_next_event_sequence(db, run_id),
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        payload_json=payload,
        payload_hash=canonical_hash(payload),
    )
    db.add(row)
    db.flush()
    return row


def claim_document_parse_run(
    db: Session,
    *,
    run_id: str,
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
    request_id: str,
    causation_event_id: str | None,
    now: datetime | None = None,
) -> BidDocumentParseClaim | None:
    """Claim one queued run, expiring a stale attempt under the same lock."""

    current_time = as_utc(now or _utc_now())
    run = (
        db.query(BidDocumentParseRun)
        .filter(BidDocumentParseRun.id == run_id)
        .with_for_update()
        .one_or_none()
    )
    if run is None:
        raise BidDocumentParseWorkerError("BID_DOCUMENT_PARSE_RUN_NOT_FOUND")
    if str(run.status) in {"succeeded", "partial", "failed"}:
        return None

    attempts = (
        db.query(BidDocumentParseAttempt)
        .filter(BidDocumentParseAttempt.run_id == run.id)
        .order_by(BidDocumentParseAttempt.attempt_no.desc())
        .with_for_update()
        .all()
    )
    latest = attempts[0] if attempts else None
    if latest is not None and str(latest.status) in {"leased", "running"}:
        if as_utc(latest.lease_until) > current_time:
            raise BidDocumentParseLeaseConflict()
        latest.status = "expired"
        latest.finished_at = current_time
        latest.retryable = True
        _append_parse_event(
            db,
            run_id=str(run.id),
            attempt_id=str(latest.id),
            event_type="parse.attempt_expired",
            from_status=str(run.status),
            to_status="queued",
            payload={"attempt_no": int(latest.attempt_no)},
        )
        run.status = "queued"
        run.started_at = None
        run.row_version = int(run.row_version) + 1

    attempt_no = int(latest.attempt_no if latest is not None else 0) + 1
    if attempt_no > max(1, int(max_attempts)):
        run.status = "failed"
        run.retryable = False
        run.finished_at = current_time
        run.ocr_status = "not_requested"
        run.warnings_json = []
        run.error_code = "BID_DOCUMENT_PARSE_ATTEMPTS_EXHAUSTED"
        run.row_version = int(run.row_version) + 1
        db.flush()
        _append_parse_event(
            db,
            run_id=str(run.id),
            attempt_id=(str(latest.id) if latest is not None else None),
            event_type="parse.failed",
            from_status="queued",
            to_status="failed",
            payload={
                "attempt_no": int(latest.attempt_no if latest is not None else 0),
                "error_code": "BID_DOCUMENT_PARSE_ATTEMPTS_EXHAUSTED",
                "retryable": False,
            },
        )
        payload = {
            "parse_run_id": str(run.id),
            "document_version_id": str(run.document_version_id),
            "status": "failed",
            "quality": None,
            "warnings": [],
            "error_code": "BID_DOCUMENT_PARSE_ATTEMPTS_EXHAUSTED",
            "retryable": False,
            "attempt_count": int(latest.attempt_no if latest is not None else 0),
            "resource_version": int(run.row_version),
        }
        for assessment_id in _assessment_ids_for_version(
            db,
            str(run.document_version_id),
        ):
            append_outbox_event(
                db,
                event_type="bid.document.parse_failed.v1",
                producer="bid-document-worker-v1",
                aggregate_type="document_parse_run",
                aggregate_id=str(run.id),
                aggregate_version=int(run.row_version),
                assessment_id=assessment_id,
                request_id=request_id,
                causation_event_id=causation_event_id,
                payload_schema="bid.document.parse_failed.v1.payload",
                payload=payload,
                dedupe_key=f"document-parse-exhausted:{run.id}:{assessment_id}",
                occurred_at=current_time,
            )
        return None
    fencing_token = int(
        db.query(func.max(BidDocumentParseAttempt.fencing_token))
        .filter(BidDocumentParseAttempt.run_id == run.id)
        .scalar()
        or 0
    ) + 1
    lease_until = current_time + timedelta(seconds=max(5, int(lease_seconds)))
    attempt = BidDocumentParseAttempt(
        id=f"dpa_{uuid.uuid4().hex}",
        run_id=str(run.id),
        attempt_no=attempt_no,
        status="running",
        lease_owner=str(worker_id)[:128],
        lease_until=lease_until,
        heartbeat_at=current_time,
        fencing_token=fencing_token,
        retryable=False,
        started_at=current_time,
    )
    db.add(attempt)
    previous_status = str(run.status)
    run.status = "running"
    run.started_at = current_time
    run.finished_at = None
    run.error_code = None
    run.ocr_status = "not_requested"
    run.row_version = int(run.row_version) + 1
    db.flush()
    _append_parse_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="parse.attempt_started",
        from_status=previous_status,
        to_status="running",
        payload={
            "attempt_no": attempt_no,
            "fencing_token": fencing_token,
        },
    )
    return BidDocumentParseClaim(
        run_id=str(run.id),
        document_version_id=str(run.document_version_id),
        attempt_id=str(attempt.id),
        attempt_no=attempt_no,
        worker_id=str(attempt.lease_owner),
        fencing_token=fencing_token,
        lease_until=lease_until,
    )


def _locked_claim_rows(
    db: Session,
    *,
    claim: BidDocumentParseClaim,
    now: datetime,
) -> tuple[BidDocumentParseRun, BidDocumentParseAttempt]:
    run = (
        db.query(BidDocumentParseRun)
        .filter(BidDocumentParseRun.id == claim.run_id)
        .with_for_update()
        .one_or_none()
    )
    attempt = (
        db.query(BidDocumentParseAttempt)
        .filter(BidDocumentParseAttempt.id == claim.attempt_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        run is None
        or attempt is None
        or str(run.status) != "running"
        or str(attempt.run_id) != str(run.id)
        or str(attempt.status) != "running"
        or str(attempt.lease_owner) != claim.worker_id
        or int(attempt.fencing_token) != int(claim.fencing_token)
        or as_utc(attempt.lease_until) <= as_utc(now)
    ):
        raise BidDocumentParseFencingRejected()
    return run, attempt


def heartbeat_document_parse_run(
    db: Session,
    *,
    claim: BidDocumentParseClaim,
    lease_seconds: int,
    now: datetime | None = None,
) -> datetime:
    current_time = as_utc(now or _utc_now())
    _run, attempt = _locked_claim_rows(db, claim=claim, now=current_time)
    lease_until = current_time + timedelta(seconds=max(5, int(lease_seconds)))
    attempt.heartbeat_at = current_time
    attempt.lease_until = lease_until
    db.flush()
    return lease_until


def _validate_result(result: DocumentParseResult) -> None:
    if result.status not in {"succeeded", "partial"}:
        raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_RESULT_STATUS_INVALID")
    if result.quality_grade not in QUALITY_GRADES:
        raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_QUALITY_INVALID")
    if not 0 <= int(result.quality_score) <= 100:
        raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_QUALITY_INVALID")
    if result.ocr_status not in OCR_STATES:
        raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_OCR_STATUS_INVALID")

    unit_keys: set[str] = set()
    for unit in result.units:
        if not unit.unit_key or unit.unit_key in unit_keys:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_UNIT_KEY_INVALID")
        unit_keys.add(unit.unit_key)
        if unit.unit_type not in PARSE_UNIT_TYPES:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_UNIT_TYPE_INVALID")
        if unit.status not in PARSE_UNIT_STATES:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_UNIT_STATUS_INVALID")
        if unit.content_source not in CONTENT_SOURCES or unit.ocr_status not in OCR_STATES:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_UNIT_SOURCE_INVALID")

    evidence_keys: set[str] = set()
    for fragment in result.evidence:
        if not fragment.evidence_key or fragment.evidence_key in evidence_keys:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_EVIDENCE_KEY_INVALID")
        evidence_keys.add(fragment.evidence_key)
        if fragment.unit_key not in unit_keys:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_EVIDENCE_UNIT_INVALID")
        if fragment.locator_type not in EVIDENCE_LOCATOR_TYPES:
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_EVIDENCE_LOCATOR_INVALID")
        if not fragment.normalized_text.strip():
            raise BidDocumentParseResultInvalid("BID_DOCUMENT_EVIDENCE_TEXT_EMPTY")
    if any(
        fragment.parent_key is not None and fragment.parent_key not in evidence_keys
        for fragment in result.evidence
    ):
        raise BidDocumentParseResultInvalid("BID_DOCUMENT_EVIDENCE_PARENT_INVALID")


def _assessment_ids_for_version(db: Session, document_version_id: str) -> list[str]:
    return sorted(
        {
            str(row[0])
            for row in (
                db.query(BidDocumentManifest.assessment_id)
                .join(
                    BidManifestDocument,
                    BidManifestDocument.manifest_id == BidDocumentManifest.id,
                )
                .filter(BidManifestDocument.document_version_id == document_version_id)
                .distinct()
                .all()
            )
        }
    )


def complete_document_parse_run(
    db: Session,
    *,
    claim: BidDocumentParseClaim,
    result: DocumentParseResult,
    request_id: str,
    causation_event_id: str | None,
    now: datetime | None = None,
) -> DocumentParseCompletion:
    """Commit immutable units/evidence, terminal state, and notification events."""

    _validate_result(result)
    current_time = as_utc(now or _utc_now())
    run, attempt = _locked_claim_rows(db, claim=claim, now=current_time)
    try:
        validate_quality_report(
            warnings=result.warnings,
            parser_profile_version=str(run.parser_profile_version),
            quality_score=int(result.quality_score),
            quality_grade=str(result.quality_grade),
        )
    except BidParseQualityGateError as exc:
        raise BidDocumentParseResultInvalid(exc.code) from exc
    if (
        db.query(BidDocumentParseUnit.id)
        .filter(BidDocumentParseUnit.run_id == run.id)
        .first()
        is not None
    ):
        raise BidDocumentParseResultInvalid("BID_DOCUMENT_PARSE_RESULT_ALREADY_WRITTEN")

    unit_ids = {unit.unit_key: f"dpu_{uuid.uuid4().hex}" for unit in result.units}
    for unit in result.units:
        db.add(
            BidDocumentParseUnit(
                id=unit_ids[unit.unit_key],
                run_id=str(run.id),
                unit_type=unit.unit_type,
                unit_key=unit.unit_key,
                ordinal=int(unit.ordinal),
                page_no=unit.page_no,
                sheet_index=unit.sheet_index,
                sheet_name=unit.sheet_name,
                cell_range=unit.cell_range,
                image_index=unit.image_index,
                section_path_json=list(unit.section_path) or None,
                content_source=unit.content_source,
                status=unit.status,
                text_hash=unit.text_hash,
                text_length=unit.text_length,
                result_ref=unit.result_ref,
                ocr_status=unit.ocr_status,
                ocr_engine_version=unit.ocr_engine_version,
                ocr_confidence=unit.ocr_confidence,
                warnings_json=list(unit.warnings) or None,
                metrics_json=dict(unit.metrics) or None,
            )
        )
    db.flush()

    evidence_ids = {
        fragment.evidence_key: f"bef_{uuid.uuid4().hex}"
        for fragment in result.evidence
    }
    for fragment in result.evidence:
        normalized_text = fragment.normalized_text.strip()
        db.add(
            BidEvidenceFragment(
                id=evidence_ids[fragment.evidence_key],
                parse_run_id=str(run.id),
                document_version_id=str(run.document_version_id),
                parse_unit_id=unit_ids[fragment.unit_key],
                locator_type=fragment.locator_type,
                locator_json=dict(fragment.locator),
                locator_hash=canonical_hash(fragment.locator),
                normalized_text=normalized_text,
                text_hash=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
                parent_id=(
                    evidence_ids[fragment.parent_key]
                    if fragment.parent_key is not None
                    else None
                ),
                ordinal=int(fragment.ordinal),
                object_ref=fragment.object_ref,
            )
        )
    db.flush()

    result_hash = canonical_hash(
        {
            "status": result.status,
            "quality_grade": result.quality_grade,
            "quality_score": int(result.quality_score),
            "ocr_status": result.ocr_status,
            "units": [asdict(unit) for unit in result.units],
            "evidence": [asdict(fragment) for fragment in result.evidence],
            "warnings": list(result.warnings),
        }
    )
    run.status = result.status
    run.retryable = False
    run.finished_at = current_time
    run.result_ref = result.result_ref
    run.result_hash = result_hash
    run.quality_grade = result.quality_grade
    run.quality_score = int(result.quality_score)
    run.page_count = sum(1 for unit in result.units if unit.unit_type == "page")
    run.sheet_count = sum(1 for unit in result.units if unit.unit_type == "sheet")
    run.ocr_status = result.ocr_status
    run.warning_count = len(result.warnings)
    run.warnings_json = list(result.warnings)
    run.error_code = None
    run.row_version = int(run.row_version) + 1
    attempt.status = "succeeded"
    attempt.retryable = False
    attempt.finished_at = current_time
    db.flush()
    _append_parse_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="parse.completed",
        from_status="running",
        to_status=result.status,
        payload={
            "attempt_no": int(attempt.attempt_no),
            "result_hash": result_hash,
            "unit_count": len(result.units),
            "evidence_count": len(result.evidence),
        },
    )

    quality = {
        "grade": result.quality_grade,
        "score": int(result.quality_score),
        "page_count": int(run.page_count),
        "sheet_count": int(run.sheet_count),
        "ocr_status": result.ocr_status,
        "low_quality_locations": [],
    }
    payload = {
        "parse_run_id": str(run.id),
        "document_version_id": str(run.document_version_id),
        "status": result.status,
        "quality": quality,
        "warnings": list(result.warnings),
        "unit_counts": {
            "total": len(result.units),
            "pages": int(run.page_count),
            "sheets": int(run.sheet_count),
            "evidence_fragments": len(result.evidence),
        },
        "result_hash": result_hash,
        "resource_version": int(run.row_version),
    }
    emitted = []
    for assessment_id in _assessment_ids_for_version(db, str(run.document_version_id)):
        outbox = append_outbox_event(
            db,
            event_type="bid.document.parsed.v1",
            producer="bid-document-worker-v1",
            aggregate_type="document_parse_run",
            aggregate_id=str(run.id),
            aggregate_version=int(run.row_version),
            assessment_id=assessment_id,
            request_id=request_id,
            causation_event_id=causation_event_id,
            payload_schema="bid.document.parsed.v1.payload",
            payload=payload,
            dedupe_key=f"document-parsed:{run.id}:{assessment_id}",
            occurred_at=current_time,
        )
        emitted.append(str(outbox.event_id))
    return DocumentParseCompletion(
        run_id=str(run.id),
        status=str(run.status),
        result_hash=result_hash,
        emitted_event_ids=tuple(emitted),
    )


def fail_document_parse_run(
    db: Session,
    *,
    claim: BidDocumentParseClaim,
    error_code: str,
    retryable: bool,
    max_attempts: int,
    request_id: str,
    causation_event_id: str | None,
    now: datetime | None = None,
) -> DocumentParseCompletion:
    """Record a transient requeue or a final, sanitized failure."""

    current_time = as_utc(now or _utc_now())
    run, attempt = _locked_claim_rows(db, claim=claim, now=current_time)
    stable_error = str(error_code or "BID_DOCUMENT_PARSE_FAILED")[:100]
    can_requeue = bool(retryable) and int(attempt.attempt_no) < max(1, int(max_attempts))
    attempt.status = "failed"
    attempt.error_code = stable_error
    attempt.retryable = can_requeue
    attempt.finished_at = current_time
    if can_requeue:
        run.status = "queued"
        run.started_at = None
        run.finished_at = None
        run.retryable = True
        run.ocr_status = "not_requested"
        run.error_code = None
        run.row_version = int(run.row_version) + 1
        _append_parse_event(
            db,
            run_id=str(run.id),
            attempt_id=str(attempt.id),
            event_type="parse.attempt_failed",
            from_status="running",
            to_status="queued",
            payload={
                "attempt_no": int(attempt.attempt_no),
                "error_code": stable_error,
                "retryable": True,
            },
        )
        return DocumentParseCompletion(
            run_id=str(run.id),
            status="queued",
            result_hash=None,
            emitted_event_ids=(),
            requeued=True,
        )

    run.status = "failed"
    run.retryable = False
    run.finished_at = current_time
    run.ocr_status = "not_requested"
    run.warnings_json = []
    run.error_code = stable_error
    run.row_version = int(run.row_version) + 1
    db.flush()
    _append_parse_event(
        db,
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        event_type="parse.failed",
        from_status="running",
        to_status="failed",
        payload={
            "attempt_no": int(attempt.attempt_no),
            "error_code": stable_error,
            "retryable": bool(run.retryable),
        },
    )
    payload = {
        "parse_run_id": str(run.id),
        "document_version_id": str(run.document_version_id),
        "status": "failed",
        "quality": None,
        "warnings": [],
        "error_code": stable_error,
        "retryable": bool(run.retryable),
        "attempt_count": int(attempt.attempt_no),
        "resource_version": int(run.row_version),
    }
    emitted = []
    for assessment_id in _assessment_ids_for_version(db, str(run.document_version_id)):
        outbox = append_outbox_event(
            db,
            event_type="bid.document.parse_failed.v1",
            producer="bid-document-worker-v1",
            aggregate_type="document_parse_run",
            aggregate_id=str(run.id),
            aggregate_version=int(run.row_version),
            assessment_id=assessment_id,
            request_id=request_id,
            causation_event_id=causation_event_id,
            payload_schema="bid.document.parse_failed.v1.payload",
            payload=payload,
            dedupe_key=f"document-parse-failed:{run.id}:{assessment_id}:{attempt.attempt_no}",
            occurred_at=current_time,
        )
        emitted.append(str(outbox.event_id))
    return DocumentParseCompletion(
        run_id=str(run.id),
        status="failed",
        result_hash=None,
        emitted_event_ids=tuple(emitted),
    )
