from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import registry as model_registry  # noqa: F401
from app.models.bidding import BidProject, BidProjectFile
from app.models.file_object import FileObject
from app.models.tender_parse_pipeline import (
    BidTenderParseJob,
    BidTenderParseJobEvent,
    BidTenderSourceObject,
)
from app.models.user import User
from app.services.bidding_parser import (
    BIDDING_PARSER_VERSION,
    TenderParseError,
    extract_tender_text,
)
from app.services.tender_evidence_ingestion import (
    TenderEvidenceIngestError,
    ingest_bid_project_file,
    normalize_document_key,
)
from app.services.tender_file_type_classifier import (
    AUTO_FILE_TYPE,
    classify_tender_file_type,
)
from app.services.tender_source_storage import (
    MinioTenderSourceStorage,
    TenderSourceStorage,
    TenderSourceStorageError,
)


logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
ACTIVE_JOB_STATUSES = {"queued", "running", "retryable"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


class TenderParsePipelineError(RuntimeError):
    pass


class TenderParsePipelineConflict(TenderParsePipelineError):
    pass


@dataclass(frozen=True)
class TenderParseJobCreation:
    job_uuid: str
    source_uuid: str
    status: str
    idempotent: bool


@dataclass(frozen=True)
class TenderParseJobRunResult:
    job_uuid: str
    status: str
    stage: str
    attempt_count: int
    evidence_document_uuid: str | None
    error_code: str | None


def create_tender_parse_job(
    db: Session,
    *,
    project_uuid: str,
    content: bytes,
    original_filename: str,
    content_type: str | None,
    file_type: str,
    document_key: str,
    current_user: User,
    storage: TenderSourceStorage | None = None,
    max_attempts: int = 3,
) -> TenderParseJobCreation:
    """Persist an original upload and its parse job as one recoverable unit.

    This function owns the database commit. If that commit fails after object
    upload, it deletes only the exact newly-created object as compensation.
    """

    storage = storage or MinioTenderSourceStorage()
    try:
        normalized_key = normalize_document_key(document_key)
    except TenderEvidenceIngestError as exc:
        raise TenderParsePipelineError(str(exc)) from exc
    filename = (original_filename or "tender-file").strip()[:255]
    normalized_file_type = (file_type or "tender_document").strip()[:64]
    if not content:
        raise TenderParsePipelineError("original tender file is empty")
    if not filename:
        raise TenderParsePipelineError("original_filename is required")
    max_attempts = max(1, min(int(max_attempts), 10))
    sha256 = hashlib.sha256(content).hexdigest()

    project = (
        db.query(BidProject)
        .filter(BidProject.project_uuid == project_uuid.strip())
        .one_or_none()
    )
    if project is None:
        raise TenderParsePipelineError("bid project does not exist")

    existing = _find_existing_job(
        db,
        project_id=project.id,
        document_key=normalized_key,
        sha256=sha256,
        parser_version=BIDDING_PARSER_VERSION,
    )
    if existing is not None:
        source, job = existing
        return TenderParseJobCreation(
            job_uuid=job.job_uuid,
            source_uuid=source.source_uuid,
            status=job.status,
            idempotent=True,
        )

    existing_source = _find_existing_source(
        db,
        project_id=project.id,
        document_key=normalized_key,
        sha256=sha256,
    )
    if existing_source is not None:
        if (
            normalized_file_type != AUTO_FILE_TYPE
            and existing_source.file_type != normalized_file_type
        ):
            raise TenderParsePipelineConflict(
                "stored source already has another file_type"
            )
        if normalized_file_type == AUTO_FILE_TYPE:
            existing_source.file_type = AUTO_FILE_TYPE
        job = BidTenderParseJob(
            job_uuid=str(uuid.uuid4()),
            project_id=project.id,
            source_object_id=existing_source.id,
            status="queued",
            stage="queued",
            attempt_count=0,
            max_attempts=max_attempts,
            parser_version=BIDDING_PARSER_VERSION,
            created_by=current_user.id,
        )
        try:
            db.add(job)
            db.flush()
            _append_event(
                db,
                job=job,
                event_type="job_created",
                message=(
                    "Existing original tender file reused; parse job queued "
                    "for the current parser version."
                ),
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            concurrent = _find_existing_job(
                db,
                project_id=project.id,
                document_key=normalized_key,
                sha256=sha256,
                parser_version=BIDDING_PARSER_VERSION,
            )
            if concurrent is None:
                raise
            concurrent_source, concurrent_job = concurrent
            return TenderParseJobCreation(
                job_uuid=concurrent_job.job_uuid,
                source_uuid=concurrent_source.source_uuid,
                status=concurrent_job.status,
                idempotent=True,
            )
        return TenderParseJobCreation(
            job_uuid=job.job_uuid,
            source_uuid=existing_source.source_uuid,
            status=job.status,
            idempotent=False,
        )

    username = (current_user.username or f"user-{current_user.id}")[:64]
    stored = storage.store(
        content=content,
        original_filename=filename,
        content_type=content_type,
        username=username,
    )
    file_id = str(uuid.uuid4())
    source_uuid = str(uuid.uuid4())
    job_uuid = str(uuid.uuid4())
    try:
        db.add(
            FileObject(
                file_id=file_id,
                username=username,
                purpose="bid_tender_source",
                bucket=stored.bucket,
                object_name=stored.object_name,
                original_filename=filename,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
            )
        )
        source = BidTenderSourceObject(
            source_uuid=source_uuid,
            project_id=project.id,
            file_object_id=file_id,
            document_key=normalized_key,
            file_type=normalized_file_type,
            original_filename=filename,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=sha256,
            status="stored",
            created_by=current_user.id,
        )
        db.add(source)
        db.flush()
        job = BidTenderParseJob(
            job_uuid=job_uuid,
            project_id=project.id,
            source_object_id=source.id,
            status="queued",
            stage="queued",
            attempt_count=0,
            max_attempts=max_attempts,
            parser_version=BIDDING_PARSER_VERSION,
            created_by=current_user.id,
        )
        db.add(job)
        db.flush()
        _append_event(
            db,
            job=job,
            event_type="job_created",
            message="Original tender file stored; parse job queued.",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        _compensate_new_object(storage, stored.bucket, stored.object_name)
        concurrent = _find_existing_job(
            db,
            project_id=project.id,
            document_key=normalized_key,
            sha256=sha256,
            parser_version=BIDDING_PARSER_VERSION,
        )
        if concurrent is None:
            raise
        concurrent_source, concurrent_job = concurrent
        return TenderParseJobCreation(
            job_uuid=concurrent_job.job_uuid,
            source_uuid=concurrent_source.source_uuid,
            status=concurrent_job.status,
            idempotent=True,
        )
    except Exception:
        db.rollback()
        _compensate_new_object(storage, stored.bucket, stored.object_name)
        raise

    return TenderParseJobCreation(
        job_uuid=job_uuid,
        source_uuid=source_uuid,
        status="queued",
        idempotent=False,
    )


def run_tender_parse_job(
    job_uuid: str,
    *,
    session_factory: SessionFactory = SessionLocal,
    storage: TenderSourceStorage | None = None,
) -> TenderParseJobRunResult:
    """Claim, parse and promote one original file into the evidence store."""

    storage = storage or MinioTenderSourceStorage()
    claimed, should_execute = _claim_job(
        job_uuid,
        session_factory=session_factory,
    )
    if not should_execute:
        return claimed

    db = session_factory()
    try:
        job, source, file_object, project = _load_job_context(db, job_uuid)
        expected_parser_version = job.parser_version
        source_bytes = storage.get(
            bucket=file_object.bucket,
            object_name=file_object.object_name,
        )
        actual_sha = hashlib.sha256(source_bytes).hexdigest()
        if actual_sha != source.sha256:
            raise _PermanentPipelineFailure(
                "SOURCE_HASH_MISMATCH",
                "Stored original file failed SHA-256 verification.",
            )
        job.stage = "parsing"
        _append_event(
            db,
            job=job,
            event_type="source_verified",
            message="Original file loaded and SHA-256 verified.",
        )
        db.commit()

        parsed = extract_tender_text(
            source_bytes,
            source.original_filename,
            source.content_type,
        )
        if str(parsed.get("parser_version") or "") != expected_parser_version:
            raise _PermanentPipelineFailure(
                "PARSER_VERSION_MISMATCH",
                "Worker parser version does not match the queued job version.",
            )

        job, source, _, project = _load_job_context(
            db,
            job_uuid,
            lock=True,
        )
        if job.status == "completed":
            return _run_result(job)
        if job.status != "running":
            raise TenderParsePipelineConflict(
                f"parse job is no longer running: {job.status}"
            )
        parse_diagnostics = parsed.get("parse_diagnostics")
        if isinstance(parse_diagnostics, dict):
            sheet_diagnostics = (
                parse_diagnostics.get("sheets")
                if isinstance(
                    parse_diagnostics.get("sheets"),
                    list,
                )
                else []
            )
            quarantined_sheets = [
                str(item.get("sheet_name") or "")
                for item in sheet_diagnostics
                if isinstance(item, dict)
                and item.get("status") == "quarantined"
            ]
            diagnostic_summary = {
                "schema_version": parse_diagnostics.get(
                    "schema_version"
                ),
                "sheet_count": parse_diagnostics.get(
                    "sheet_count"
                ),
                "parsed_sheet_count": parse_diagnostics.get(
                    "parsed_sheet_count"
                ),
                "quarantined_sheet_count": (
                    parse_diagnostics.get(
                        "quarantined_sheet_count"
                    )
                ),
                "skipped_sheet_count": parse_diagnostics.get(
                    "skipped_sheet_count"
                ),
                "extracted_segment_count": (
                    parse_diagnostics.get(
                        "extracted_segment_count"
                    )
                ),
                "warning_codes": parse_diagnostics.get(
                    "warning_codes"
                ),
                "quarantined_sheets": quarantined_sheets[:20],
            }
            _append_event(
                db,
                job=job,
                event_type="workbook_scan_completed",
                message=json.dumps(
                    diagnostic_summary,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        if source.file_type == AUTO_FILE_TYPE:
            classification = classify_tender_file_type(
                original_filename=source.original_filename,
                extracted_text=str(parsed.get("text") or ""),
            )
            source.file_type = classification.file_type
            _append_event(
                db,
                job=job,
                event_type="file_type_classified",
                message=(
                    "Automatically classified tender source as "
                    f"{classification.file_type} "
                    f"(confidence={classification.confidence:.3f})."
                ),
            )
        job.stage = "evidence_ingestion"
        parsed_file = _get_or_create_parsed_file(
            db,
            project=project,
            source=source,
            job=job,
            parsed=parsed,
        )
        evidence = ingest_bid_project_file(
            db,
            project_uuid=project.project_uuid,
            file_uuid=parsed_file.file_uuid,
            document_key=source.document_key,
            document_type=source.file_type,
            created_by=job.created_by,
        )
        now = _utcnow()
        source.status = "ingested"
        job.status = "completed"
        job.stage = "completed"
        job.bid_project_file_id = parsed_file.id
        job.evidence_document_uuid = evidence.evidence_document_uuid
        job.error_code = None
        job.error_message = None
        job.finished_at = now
        _append_event(
            db,
            job=job,
            event_type="evidence_ingested",
            message=(
                f"Evidence document {evidence.evidence_document_uuid} "
                f"created with {evidence.block_count} blocks."
            ),
        )
        db.commit()
        parse_result = _run_result(job)
        _dispatch_index_job_after_commit(
            db,
            evidence.index_job_uuid,
        )
        return parse_result
    except TenderParseError as exc:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="UNSUPPORTED_OR_UNREADABLE_FILE",
            message=str(exc),
            permanent=True,
            session_factory=session_factory,
        )
    except TenderEvidenceIngestError as exc:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="EVIDENCE_INGEST_REJECTED",
            message=str(exc),
            permanent=True,
            session_factory=session_factory,
        )
    except _PermanentPipelineFailure as exc:
        db.rollback()
        return _record_failure(
            job_uuid,
            code=exc.code,
            message=exc.safe_message,
            permanent=True,
            session_factory=session_factory,
        )
    except TenderSourceStorageError:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="SOURCE_STORAGE_UNAVAILABLE",
            message="Original tender file is temporarily unavailable.",
            permanent=False,
            session_factory=session_factory,
        )
    except TenderParsePipelineConflict:
        db.rollback()
        raise
    except Exception:
        logger.exception("unexpected tender parse pipeline failure job_uuid=%s", job_uuid)
        db.rollback()
        return _record_failure(
            job_uuid,
            code="PARSE_PIPELINE_ERROR",
            message="Tender parse pipeline failed unexpectedly.",
            permanent=False,
            session_factory=session_factory,
        )
    finally:
        db.close()


def requeue_tender_parse_job(
    db: Session,
    *,
    job_uuid: str,
    project_id: int,
) -> BidTenderParseJob:
    job = (
        db.query(BidTenderParseJob)
        .filter(
            BidTenderParseJob.job_uuid == job_uuid,
            BidTenderParseJob.project_id == project_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if job is None:
        raise TenderParsePipelineError("parse job does not exist")
    if job.status != "retryable":
        raise TenderParsePipelineConflict(
            f"only retryable jobs can be queued again: {job.status}"
        )
    if job.attempt_count >= job.max_attempts:
        raise TenderParsePipelineConflict("parse job has exhausted its attempts")
    job.status = "queued"
    job.stage = "queued"
    job.error_code = None
    job.error_message = None
    _append_event(
        db,
        job=job,
        event_type="job_requeued",
        message="Parse job queued for another attempt.",
    )
    return job


def record_tender_parse_dispatch(
    db: Session,
    *,
    job_uuid: str,
    celery_task_id: str | None,
) -> BidTenderParseJob:
    job = (
        db.query(BidTenderParseJob)
        .filter(BidTenderParseJob.job_uuid == job_uuid)
        .with_for_update()
        .one()
    )
    if celery_task_id:
        job.celery_task_id = celery_task_id[:160]
    _append_event(
        db,
        job=job,
        event_type="job_dispatched",
        message=(
            f"Parse job dispatched as task {celery_task_id}."
            if celery_task_id
            else "Parse job dispatched by the configured local mode."
        ),
    )
    return job


def record_tender_parse_dispatch_failure(
    db: Session,
    *,
    job_uuid: str,
) -> BidTenderParseJob:
    job = (
        db.query(BidTenderParseJob)
        .filter(BidTenderParseJob.job_uuid == job_uuid)
        .with_for_update()
        .one()
    )
    if job.status == "completed":
        return job
    job.status = "retryable"
    job.stage = "dispatch_failed"
    job.error_code = "DISPATCH_FAILED"
    job.error_message = "Parse job could not be dispatched; it can be retried."
    _append_event(
        db,
        job=job,
        event_type="dispatch_failed",
        message=job.error_message,
    )
    return job


def serialize_tender_parse_job(
    db: Session,
    job: BidTenderParseJob,
    *,
    include_events: bool = False,
) -> dict:
    source = (
        db.query(BidTenderSourceObject)
        .filter(BidTenderSourceObject.id == job.source_object_id)
        .one()
    )
    result = {
        "job_uuid": job.job_uuid,
        "project_id": job.project_id,
        "source_uuid": source.source_uuid,
        "document_key": source.document_key,
        "file_type": source.file_type,
        "original_filename": source.original_filename,
        "content_type": source.content_type,
        "size_bytes": source.size_bytes,
        "sha256": source.sha256,
        "status": job.status,
        "stage": job.stage,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "parser_version": job.parser_version,
        "bid_project_file_id": job.bid_project_file_id,
        "evidence_document_uuid": job.evidence_document_uuid,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }
    if include_events:
        events = (
            db.query(BidTenderParseJobEvent)
            .filter(BidTenderParseJobEvent.parse_job_id == job.id)
            .order_by(
                BidTenderParseJobEvent.created_at.asc(),
                BidTenderParseJobEvent.id.asc(),
            )
            .all()
        )
        result["events"] = [
            {
                "event_uuid": item.event_uuid,
                "event_type": item.event_type,
                "status": item.status,
                "stage": item.stage,
                "attempt_no": item.attempt_no,
                "message": item.message,
                "created_at": _iso(item.created_at),
            }
            for item in events
        ]
    return result


class _PermanentPipelineFailure(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def _find_existing_job(
    db: Session,
    *,
    project_id: int,
    document_key: str,
    sha256: str,
    parser_version: str,
) -> tuple[BidTenderSourceObject, BidTenderParseJob] | None:
    row = (
        db.query(BidTenderSourceObject, BidTenderParseJob)
        .join(
            BidTenderParseJob,
            BidTenderParseJob.source_object_id == BidTenderSourceObject.id,
        )
        .filter(
            BidTenderSourceObject.project_id == project_id,
            BidTenderSourceObject.document_key == document_key,
            BidTenderSourceObject.sha256 == sha256,
            BidTenderParseJob.parser_version == parser_version,
        )
        .first()
    )
    return row if row else None


def _find_existing_source(
    db: Session,
    *,
    project_id: int,
    document_key: str,
    sha256: str,
) -> BidTenderSourceObject | None:
    return (
        db.query(BidTenderSourceObject)
        .filter(
            BidTenderSourceObject.project_id == project_id,
            BidTenderSourceObject.document_key == document_key,
            BidTenderSourceObject.sha256 == sha256,
        )
        .one_or_none()
    )


def _claim_job(
    job_uuid: str,
    *,
    session_factory: SessionFactory,
) -> tuple[TenderParseJobRunResult, bool]:
    db = session_factory()
    try:
        job = (
            db.query(BidTenderParseJob)
            .filter(BidTenderParseJob.job_uuid == job_uuid)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise TenderParsePipelineError("parse job does not exist")
        if job.status == "completed":
            return _run_result(job), False
        if job.status == "running":
            return _run_result(job), False
        if job.status not in {"queued", "retryable"}:
            return _run_result(job), False
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.stage = "failed"
            job.error_code = "ATTEMPTS_EXHAUSTED"
            job.error_message = "Parse job exhausted its configured attempts."
            job.finished_at = _utcnow()
            _append_event(
                db,
                job=job,
                event_type="job_failed",
                message=job.error_message,
            )
            db.commit()
            return _run_result(job), False
        job.attempt_count += 1
        job.status = "running"
        job.stage = "fetching_source"
        job.started_at = _utcnow()
        job.finished_at = None
        job.error_code = None
        job.error_message = None
        _append_event(
            db,
            job=job,
            event_type="attempt_started",
            message=f"Parse attempt {job.attempt_count} started.",
        )
        db.commit()
        return _run_result(job), True
    finally:
        db.close()


def _load_job_context(
    db: Session,
    job_uuid: str,
    *,
    lock: bool = False,
) -> tuple[
    BidTenderParseJob,
    BidTenderSourceObject,
    FileObject,
    BidProject,
]:
    query = db.query(BidTenderParseJob).filter(
        BidTenderParseJob.job_uuid == job_uuid
    )
    if lock:
        query = query.with_for_update()
    job = query.one_or_none()
    if job is None:
        raise TenderParsePipelineError("parse job does not exist")
    source = (
        db.query(BidTenderSourceObject)
        .filter(BidTenderSourceObject.id == job.source_object_id)
        .one()
    )
    file_object = (
        db.query(FileObject)
        .filter(FileObject.file_id == source.file_object_id)
        .one()
    )
    project = db.query(BidProject).filter(BidProject.id == job.project_id).one()
    return job, source, file_object, project


def _get_or_create_parsed_file(
    db: Session,
    *,
    project: BidProject,
    source: BidTenderSourceObject,
    job: BidTenderParseJob,
    parsed: dict,
) -> BidProjectFile:
    deterministic_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bid-project-file:{source.source_uuid}:{job.parser_version}",
        )
    )
    existing = (
        db.query(BidProjectFile)
        .filter(
            BidProjectFile.project_id == project.id,
            BidProjectFile.file_uuid == deterministic_uuid,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    parsed_file = BidProjectFile(
        file_uuid=deterministic_uuid,
        project_id=project.id,
        file_type=source.file_type,
        original_filename=source.original_filename,
        content_type=source.content_type,
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        parser_status="parsed",
        parser_version=str(parsed["parser_version"]),
        extracted_text=str(parsed["text"]),
        segments_json=json.dumps(
            parsed["segments"],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        page_count=int(parsed["page_count"]),
        section_count=int(parsed["section_count"]),
        uploaded_by=job.created_by,
    )
    db.add(parsed_file)
    db.flush()
    return parsed_file


def _record_failure(
    job_uuid: str,
    *,
    code: str,
    message: str,
    permanent: bool,
    session_factory: SessionFactory,
) -> TenderParseJobRunResult:
    db = session_factory()
    try:
        job = (
            db.query(BidTenderParseJob)
            .filter(BidTenderParseJob.job_uuid == job_uuid)
            .with_for_update()
            .one()
        )
        if job.status == "completed":
            return _run_result(job)
        terminal = permanent or job.attempt_count >= job.max_attempts
        job.status = "failed" if terminal else "retryable"
        job.stage = "failed"
        job.error_code = code[:64]
        job.error_message = _safe_message(message)
        job.finished_at = _utcnow() if terminal else None
        source = (
            db.query(BidTenderSourceObject)
            .filter(BidTenderSourceObject.id == job.source_object_id)
            .one()
        )
        completed_sibling_exists = (
            db.query(BidTenderParseJob.id)
            .filter(
                BidTenderParseJob.source_object_id == source.id,
                BidTenderParseJob.id != job.id,
                BidTenderParseJob.status == "completed",
            )
            .first()
            is not None
        )
        if terminal and not completed_sibling_exists:
            source.status = "parse_failed"
        _append_event(
            db,
            job=job,
            event_type="job_failed" if terminal else "attempt_failed",
            message=job.error_message,
        )
        db.commit()
        return _run_result(job)
    finally:
        db.close()


def _append_event(
    db: Session,
    *,
    job: BidTenderParseJob,
    event_type: str,
    message: str | None,
) -> None:
    db.add(
        BidTenderParseJobEvent(
            event_uuid=str(uuid.uuid4()),
            parse_job_id=job.id,
            event_type=event_type[:64],
            status=job.status,
            stage=job.stage,
            attempt_no=job.attempt_count,
            message=_safe_message(message),
        )
    )


def _compensate_new_object(
    storage: TenderSourceStorage,
    bucket: str,
    object_name: str,
) -> None:
    try:
        storage.delete(bucket=bucket, object_name=object_name)
    except Exception:
        logger.exception(
            "failed to compensate tender source object bucket=%s object=%s",
            bucket,
            object_name,
        )


def _dispatch_index_job_after_commit(db: Session, job_uuid: str) -> None:
    from app.services.tender_evidence_index_dispatcher import (
        dispatch_tender_evidence_index_job,
    )
    from app.services.tender_evidence_indexing import (
        record_index_dispatch,
        record_index_dispatch_failure,
    )
    from mcp_servers.tender_evidence.hybrid_client import (
        hybrid_search_enabled,
    )

    if not hybrid_search_enabled():
        return
    try:
        task_id = dispatch_tender_evidence_index_job(job_uuid)
        record_index_dispatch(
            db,
            job_uuid=job_uuid,
            celery_task_id=task_id,
        )
        db.commit()
    except Exception:
        logger.exception(
            "failed to dispatch tender evidence index job job_uuid=%s",
            job_uuid,
        )
        db.rollback()
        try:
            record_index_dispatch_failure(db, job_uuid=job_uuid)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "failed to record evidence index dispatch failure "
                "job_uuid=%s",
                job_uuid,
            )


def _safe_message(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())[:1000]


def _run_result(job: BidTenderParseJob) -> TenderParseJobRunResult:
    return TenderParseJobRunResult(
        job_uuid=job.job_uuid,
        status=job.status,
        stage=job.stage,
        attempt_count=job.attempt_count,
        evidence_document_uuid=job.evidence_document_uuid,
        error_code=job.error_code,
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
