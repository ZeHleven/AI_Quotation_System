"""Document Worker orchestration around durable claims and a pure parser."""
from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.bid_assessment import BidDocumentVersion, BidFileObject
from app.models.bid_assessment_documents import BidDocumentParseRun
from app.models.bid_assessment_eventing import BidOutboxEvent
from app.services.bid_document_parse_consumer import DOCUMENT_PARSE_REQUEST_EVENT
from app.services.bid_document_parse_worker import (
    BidDocumentParseFencingRejected,
    BidDocumentParseLeaseConflict,
    claim_document_parse_run,
    complete_document_parse_run,
    fail_document_parse_run,
    heartbeat_document_parse_run,
)
from app.services.bid_document_parser_adapter import (
    BidDocumentParserAdapterError,
    parse_bid_document_bytes,
)
from app.services.bid_upload_file_storage import (
    BidUploadObjectStorage,
    BidUploadStorageError,
    get_bid_upload_object_storage,
)


@dataclass(frozen=True)
class DocumentParseExecutionResult:
    event_id: str
    parse_run_id: str | None
    status: str
    requeued: bool = False


class BidDocumentParseExecutionError(RuntimeError):
    code = "BID_DOCUMENT_PARSE_EXECUTION_FAILED"


def _close_reader(reader: object | None) -> None:
    if reader is None:
        return
    try:
        close = getattr(reader, "close", None)
        if callable(close):
            close()
    finally:
        release_conn = getattr(reader, "release_conn", None)
        if callable(release_conn):
            release_conn()


def _read_bounded(
    storage: BidUploadObjectStorage,
    *,
    object_key: str,
    max_bytes: int,
) -> bytes:
    reader = None
    chunks: list[bytes] = []
    size = 0
    try:
        reader = storage.open_read(object_key=object_key)
        while True:
            chunk = reader.read(min(1024 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise BidDocumentParseExecutionError("BID_DOCUMENT_PARSE_FILE_TOO_LARGE")
            chunks.append(bytes(chunk))
    finally:
        _close_reader(reader)
    return b"".join(chunks)


def _failure_contract(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, BidDocumentParserAdapterError):
        return str(exc.code), bool(exc.retryable)
    if isinstance(exc, BidUploadStorageError):
        return "BID_STORAGE_UNAVAILABLE", True
    if isinstance(exc, BidDocumentParseExecutionError):
        return str(exc), False
    return "BID_DOCUMENT_PARSE_FAILED", True


def execute_document_parse_request(
    *,
    event_id: str,
    worker_id: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    storage: BidUploadObjectStorage | None = None,
) -> DocumentParseExecutionResult:
    """Execute one parse request without holding a DB transaction during I/O."""

    metadata_db = session_factory()
    try:
        event = (
            metadata_db.query(BidOutboxEvent)
            .filter(BidOutboxEvent.event_id == event_id)
            .one_or_none()
        )
        if event is None:
            raise BidDocumentParseExecutionError("BID_OUTBOX_EVENT_NOT_FOUND")
        if str(event.event_type) != DOCUMENT_PARSE_REQUEST_EVENT:
            return DocumentParseExecutionResult(
                event_id=event_id,
                parse_run_id=None,
                status="ignored",
            )
        payload = dict(event.payload_json or {})
        run_id = str(payload.get("parse_run_id") or "")
        row = (
            metadata_db.query(BidDocumentParseRun, BidDocumentVersion, BidFileObject)
            .join(
                BidDocumentVersion,
                BidDocumentVersion.id == BidDocumentParseRun.document_version_id,
            )
            .join(BidFileObject, BidFileObject.id == BidDocumentVersion.file_object_id)
            .filter(BidDocumentParseRun.id == run_id)
            .one_or_none()
        )
        if row is None:
            raise BidDocumentParseExecutionError("BID_DOCUMENT_PARSE_RUN_NOT_FOUND")
        run, _version, file_object = row
        request_id = str(event.request_id)
        parser_profile_version = str(run.parser_profile_version)
        expected_sha256 = str(file_object.sha256)
        expected_size = int(file_object.size_bytes)
        mime_type = str(file_object.mime_type)
        object_key = str(file_object.object_key)
    finally:
        metadata_db.close()

    effective_worker_id = (
        worker_id
        or f"document-worker:{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
    )[:128]
    claim_db = session_factory()
    try:
        try:
            with claim_db.begin():
                claim = claim_document_parse_run(
                    claim_db,
                    run_id=run_id,
                    worker_id=effective_worker_id,
                    lease_seconds=settings.bid_document_parse_lease_seconds,
                    max_attempts=settings.bid_document_parse_max_attempts,
                    request_id=request_id,
                    causation_event_id=event_id,
                )
        except BidDocumentParseLeaseConflict:
            return DocumentParseExecutionResult(
                event_id=event_id,
                parse_run_id=run_id,
                status="already_running",
            )
    finally:
        claim_db.close()
    if claim is None:
        status_db = session_factory()
        try:
            current_status = (
                status_db.query(BidDocumentParseRun.status)
                .filter(BidDocumentParseRun.id == run_id)
                .scalar()
            )
        finally:
            status_db.close()
        return DocumentParseExecutionResult(
            event_id=event_id,
            parse_run_id=run_id,
            status=str(current_status or "not_found"),
        )

    try:
        active_storage = storage or get_bid_upload_object_storage()
        content = _read_bounded(
            active_storage,
            object_key=object_key,
            max_bytes=max(1, int(settings.bid_document_parse_max_bytes)),
        )
        if len(content) != expected_size:
            raise BidDocumentParseExecutionError("BID_DOCUMENT_PARSE_SIZE_MISMATCH")
        heartbeat_db = session_factory()
        try:
            with heartbeat_db.begin():
                heartbeat_document_parse_run(
                    heartbeat_db,
                    claim=claim,
                    lease_seconds=settings.bid_document_parse_lease_seconds,
                )
        finally:
            heartbeat_db.close()
        parsed = parse_bid_document_bytes(
            content=content,
            expected_sha256=expected_sha256,
            mime_type=mime_type,
            parser_profile_version=parser_profile_version,
            pdf_native_layout_enabled=(
                settings.feature_bid_assessment_pdf_c2_native_layout
            ),
            rq1a_structure_enabled=(
                settings.feature_bid_assessment_rq1a_structure_aggregation
            ),
            rq1b_quality_gate_enabled=(
                settings.feature_bid_assessment_rq1b_parse_quality_gate
            ),
        )
        complete_db = session_factory()
        try:
            with complete_db.begin():
                completion = complete_document_parse_run(
                    complete_db,
                    claim=claim,
                    result=parsed,
                    request_id=request_id,
                    causation_event_id=event_id,
                )
        finally:
            complete_db.close()
        return DocumentParseExecutionResult(
            event_id=event_id,
            parse_run_id=run_id,
            status=completion.status,
        )
    except BidDocumentParseFencingRejected:
        raise
    except Exception as exc:
        error_code, retryable = _failure_contract(exc)
        failure_db = session_factory()
        try:
            with failure_db.begin():
                completion = fail_document_parse_run(
                    failure_db,
                    claim=claim,
                    error_code=error_code,
                    retryable=retryable,
                    max_attempts=settings.bid_document_parse_max_attempts,
                    request_id=request_id,
                    causation_event_id=event_id,
                )
        finally:
            failure_db.close()
        return DocumentParseExecutionResult(
            event_id=event_id,
            parse_run_id=run_id,
            status=completion.status,
            requeued=completion.requeued,
        )


def process_queued_document_parse_runs(
    *,
    limit: int = 20,
    session_factory: Callable[[], Session] = SessionLocal,
) -> list[DocumentParseExecutionResult]:
    """Recover durable queued jobs even if the original Celery wake-up was lost."""

    db = session_factory()
    try:
        run_ids = [
            str(row[0])
            for row in (
                db.query(BidDocumentParseRun.id)
                .filter(BidDocumentParseRun.status == "queued")
                .order_by(BidDocumentParseRun.requested_at.asc())
                .limit(max(1, min(int(limit), 100)))
                .all()
            )
        ]
        event_ids = {
            str(row.aggregate_id): str(row.event_id)
            for row in (
                db.query(BidOutboxEvent)
                .filter(
                    BidOutboxEvent.event_type == DOCUMENT_PARSE_REQUEST_EVENT,
                    BidOutboxEvent.aggregate_id.in_(run_ids),
                )
                .order_by(BidOutboxEvent.occurred_at.asc())
                .all()
            )
        } if run_ids else {}
    finally:
        db.close()

    results = []
    for run_id in run_ids:
        event_id = event_ids.get(run_id)
        if not event_id:
            continue
        results.append(
            execute_document_parse_request(
                event_id=event_id,
                session_factory=session_factory,
            )
        )
    return results
