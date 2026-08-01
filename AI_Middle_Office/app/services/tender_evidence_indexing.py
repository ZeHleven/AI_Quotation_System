from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import registry as model_registry  # noqa: F401
from app.models.bidding import BidProject
from app.models.tender_evidence import (
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
)
from app.models.tender_evidence_index import BidEvidenceIndexJob
from app.services.tender_evidence_body_storage import (
    TenderEvidenceBodyError,
    TenderEvidenceBodyReader,
)
from mcp_servers.tender_evidence.hybrid_client import (
    HybridIndexBlock,
    TenderHybridIndexStale,
    TenderHybridSearch,
    TenderHybridSearchError,
    TenderHybridSearchUnavailable,
    configured_hybrid_client,
)


logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
INDEX_SCHEMA_VERSION = "tender-hybrid-v1"


class TenderEvidenceIndexError(RuntimeError):
    pass


class TenderEvidenceIndexConflict(TenderEvidenceIndexError):
    pass


@dataclass(frozen=True)
class TenderEvidenceIndexRunResult:
    job_uuid: str
    status: str
    stage: str
    attempt_count: int
    indexed_block_count: int
    error_code: str | None


def ensure_evidence_index_job(
    db: Session,
    *,
    project_id: int,
    manifest: BidEvidenceManifest,
    requested_block_count: int,
    created_by: int,
) -> BidEvidenceIndexJob:
    """Create the transactional outbox record for a manifest, idempotently."""

    existing = (
        db.query(BidEvidenceIndexJob)
        .filter(
            BidEvidenceIndexJob.manifest_id == manifest.id,
            BidEvidenceIndexJob.index_schema_version == INDEX_SCHEMA_VERSION,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    job = BidEvidenceIndexJob(
        job_uuid=str(uuid.uuid4()),
        project_id=project_id,
        manifest_id=manifest.id,
        manifest_version=manifest.version_no,
        manifest_hash=manifest.manifest_hash,
        index_schema_version=INDEX_SCHEMA_VERSION,
        status="queued",
        stage="queued",
        attempt_count=0,
        max_attempts=_max_attempts(),
        requested_block_count=max(0, int(requested_block_count)),
        indexed_block_count=0,
        created_by=created_by,
    )
    db.add(job)
    db.flush()
    return job


def run_tender_evidence_index_job(
    job_uuid: str,
    *,
    session_factory: SessionFactory = SessionLocal,
    client: TenderHybridSearch | None = None,
    body_reader: TenderEvidenceBodyReader | None = None,
) -> TenderEvidenceIndexRunResult:
    result, should_execute = _claim_job(
        job_uuid,
        session_factory=session_factory,
    )
    if not should_execute:
        return result

    try:
        client = client or configured_hybrid_client()
    except Exception:
        logger.exception(
            "tender hybrid client configuration failed job_uuid=%s",
            job_uuid,
        )
        return _record_failure(
            job_uuid,
            code="HYBRID_CLIENT_NOT_CONFIGURED",
            message="Tender hybrid search client is not configured.",
            permanent=False,
            session_factory=session_factory,
        )

    db = session_factory()
    try:
        job, project, manifest = _load_job_context(db, job_uuid, lock=True)
        if not manifest.active:
            job.status = "cancelled"
            job.stage = "superseded"
            job.error_code = "MANIFEST_SUPERSEDED"
            job.error_message = (
                "Manifest was superseded before hybrid indexing started."
            )
            job.finished_at = _utcnow()
            db.commit()
            return _run_result(job)
        if (
            manifest.version_no != job.manifest_version
            or manifest.manifest_hash != job.manifest_hash
        ):
            raise _PermanentIndexFailure(
                "MANIFEST_IDENTITY_MISMATCH",
                "Index job does not match its evidence manifest.",
            )
        blocks = _build_active_snapshot(
            db,
            project=project,
            manifest=manifest,
            body_reader=body_reader,
        )
        if not blocks:
            raise _PermanentIndexFailure(
                "NO_ACTIVE_EVIDENCE_BLOCKS",
                "Active manifest contains no evidence blocks to index.",
            )
        if len(blocks) != job.requested_block_count:
            raise _PermanentIndexFailure(
                "MANIFEST_BLOCK_COUNT_MISMATCH",
                "Active evidence block count changed after the job was queued.",
            )
        job.stage = "uploading_snapshot"
        job.search_service_url = str(
            getattr(client, "service_url", "")
            or getattr(client, "base_url", "")
        )[:500] or None
        db.commit()

        response = client.reindex(
            case_id=project.project_uuid,
            manifest_version=manifest.version_no,
            manifest_hash=manifest.manifest_hash,
            index_schema_version=job.index_schema_version,
            blocks=blocks,
        )

        job, _, current_manifest = _load_job_context(
            db,
            job_uuid,
            lock=True,
        )
        if job.status == "completed":
            return _run_result(job)
        if not current_manifest.active:
            job.status = "cancelled"
            job.stage = "superseded"
            job.error_code = "MANIFEST_SUPERSEDED"
            job.error_message = (
                "Manifest was superseded while hybrid indexing was running."
            )
            job.finished_at = _utcnow()
            db.commit()
            return _run_result(job)
        job.status = "completed"
        job.stage = "completed"
        job.indexed_block_count = response.indexed_block_count
        job.http_status = 200
        job.error_code = None
        job.error_message = None
        job.finished_at = _utcnow()
        db.commit()
        return _run_result(job)
    except _PermanentIndexFailure as exc:
        db.rollback()
        return _record_failure(
            job_uuid,
            code=exc.code,
            message=exc.safe_message,
            permanent=True,
            session_factory=session_factory,
        )
    except TenderHybridIndexStale:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="HYBRID_INDEX_STALE",
            message="Hybrid service rejected the manifest identity.",
            permanent=False,
            session_factory=session_factory,
        )
    except TenderHybridSearchUnavailable:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="HYBRID_SERVICE_UNAVAILABLE",
            message="Tender hybrid search service is temporarily unavailable.",
            permanent=False,
            session_factory=session_factory,
        )
    except TenderHybridSearchError:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="HYBRID_INDEX_REJECTED",
            message="Tender hybrid search service rejected the index snapshot.",
            permanent=False,
            session_factory=session_factory,
        )
    except TenderEvidenceBodyError:
        db.rollback()
        return _record_failure(
            job_uuid,
            code="EVIDENCE_BODY_UNAVAILABLE",
            message=(
                "Authoritative tender evidence body could not be loaded "
                "or failed integrity verification."
            ),
            permanent=False,
            session_factory=session_factory,
        )
    except Exception:
        logger.exception(
            "unexpected tender evidence index failure job_uuid=%s",
            job_uuid,
        )
        db.rollback()
        return _record_failure(
            job_uuid,
            code="HYBRID_INDEX_ERROR",
            message="Tender evidence indexing failed unexpectedly.",
            permanent=False,
            session_factory=session_factory,
        )
    finally:
        db.close()


def requeue_tender_evidence_index_job(
    db: Session,
    *,
    job_uuid: str,
    project_id: int,
) -> BidEvidenceIndexJob:
    job = (
        db.query(BidEvidenceIndexJob)
        .filter(
            BidEvidenceIndexJob.job_uuid == job_uuid,
            BidEvidenceIndexJob.project_id == project_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if job is None:
        raise TenderEvidenceIndexError("evidence index job does not exist")
    if job.status == "queued":
        return job
    if job.status != "retryable":
        raise TenderEvidenceIndexConflict(
            "only queued or retryable index jobs can be dispatched: "
            f"{job.status}"
        )
    if job.attempt_count >= job.max_attempts:
        raise TenderEvidenceIndexConflict(
            "evidence index job has exhausted its attempts"
        )
    job.status = "queued"
    job.stage = "queued"
    job.error_code = None
    job.error_message = None
    return job


def record_index_dispatch(
    db: Session,
    *,
    job_uuid: str,
    celery_task_id: str | None,
) -> BidEvidenceIndexJob:
    job = (
        db.query(BidEvidenceIndexJob)
        .filter(BidEvidenceIndexJob.job_uuid == job_uuid)
        .with_for_update()
        .one()
    )
    if celery_task_id:
        job.celery_task_id = celery_task_id[:160]
    return job


def record_index_dispatch_failure(
    db: Session,
    *,
    job_uuid: str,
) -> BidEvidenceIndexJob:
    job = (
        db.query(BidEvidenceIndexJob)
        .filter(BidEvidenceIndexJob.job_uuid == job_uuid)
        .with_for_update()
        .one()
    )
    if job.status in {"completed", "cancelled"}:
        return job
    job.status = "retryable"
    job.stage = "dispatch_failed"
    job.error_code = "DISPATCH_FAILED"
    job.error_message = (
        "Evidence index job could not be dispatched; it can be retried."
    )
    return job


def serialize_evidence_index_job(job: BidEvidenceIndexJob) -> dict:
    return {
        "job_uuid": job.job_uuid,
        "project_id": job.project_id,
        "manifest_version": job.manifest_version,
        "manifest_hash": job.manifest_hash,
        "index_schema_version": job.index_schema_version,
        "status": job.status,
        "stage": job.stage,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "requested_block_count": job.requested_block_count,
        "indexed_block_count": job.indexed_block_count,
        "search_service_url": job.search_service_url,
        "http_status": job.http_status,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def latest_project_index_job(
    db: Session,
    *,
    project_id: int,
) -> BidEvidenceIndexJob | None:
    return (
        db.query(BidEvidenceIndexJob)
        .filter(BidEvidenceIndexJob.project_id == project_id)
        .order_by(
            BidEvidenceIndexJob.manifest_version.desc(),
            BidEvidenceIndexJob.id.desc(),
        )
        .first()
    )


class _PermanentIndexFailure(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def _claim_job(
    job_uuid: str,
    *,
    session_factory: SessionFactory,
) -> tuple[TenderEvidenceIndexRunResult, bool]:
    db = session_factory()
    try:
        job = (
            db.query(BidEvidenceIndexJob)
            .filter(BidEvidenceIndexJob.job_uuid == job_uuid)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise TenderEvidenceIndexError("evidence index job does not exist")
        if job.status in {"running", "completed", "failed", "cancelled"}:
            return _run_result(job), False
        if job.status not in {"queued", "retryable"}:
            return _run_result(job), False
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.stage = "failed"
            job.error_code = "ATTEMPTS_EXHAUSTED"
            job.error_message = "Evidence index job exhausted its attempts."
            job.finished_at = _utcnow()
            db.commit()
            return _run_result(job), False
        job.attempt_count += 1
        job.status = "running"
        job.stage = "building_snapshot"
        job.started_at = _utcnow()
        job.finished_at = None
        job.http_status = None
        job.error_code = None
        job.error_message = None
        db.commit()
        return _run_result(job), True
    finally:
        db.close()


def _load_job_context(
    db: Session,
    job_uuid: str,
    *,
    lock: bool = False,
) -> tuple[BidEvidenceIndexJob, BidProject, BidEvidenceManifest]:
    query = db.query(BidEvidenceIndexJob).filter(
        BidEvidenceIndexJob.job_uuid == job_uuid
    )
    if lock:
        query = query.with_for_update()
    job = query.one()
    project = db.query(BidProject).filter(BidProject.id == job.project_id).one()
    manifest = (
        db.query(BidEvidenceManifest)
        .filter(BidEvidenceManifest.id == job.manifest_id)
        .one()
    )
    return job, project, manifest


def _build_active_snapshot(
    db: Session,
    *,
    project: BidProject,
    manifest: BidEvidenceManifest,
    body_reader: TenderEvidenceBodyReader | None = None,
) -> list[HybridIndexBlock]:
    body_reader = body_reader or TenderEvidenceBodyReader()
    rows = (
        db.query(BidEvidenceBlock, BidEvidenceDocument)
        .join(
            BidEvidenceDocument,
            BidEvidenceDocument.id == BidEvidenceBlock.document_id,
        )
        .filter(
            BidEvidenceBlock.project_id == project.id,
            BidEvidenceDocument.project_id == project.id,
            BidEvidenceDocument.active.is_(True),
            BidEvidenceDocument.parse_status != "failed",
        )
        .order_by(
            BidEvidenceDocument.document_key.asc(),
            BidEvidenceDocument.version_no.asc(),
            BidEvidenceBlock.block_order.asc(),
        )
        .all()
    )
    return [
        HybridIndexBlock(
            evidence_id=block.evidence_id,
            block_id=block.block_id,
            document_id=document.evidence_document_uuid,
            document_key=document.document_key,
            document_version=document.version_no,
            block_order=block.block_order,
            content_hash=block.content_hash,
            content=body_reader.read(document=document, block=block),
            keywords=tuple(_load_json_list(block.keywords_json)),
            locator=_load_locator(block),
        )
        for block, document in rows
    ]


def _record_failure(
    job_uuid: str,
    *,
    code: str,
    message: str,
    permanent: bool,
    session_factory: SessionFactory,
) -> TenderEvidenceIndexRunResult:
    db = session_factory()
    try:
        job = (
            db.query(BidEvidenceIndexJob)
            .filter(BidEvidenceIndexJob.job_uuid == job_uuid)
            .with_for_update()
            .one()
        )
        if job.status == "completed":
            return _run_result(job)
        terminal = permanent or job.attempt_count >= job.max_attempts
        job.status = "failed" if terminal else "retryable"
        job.stage = "failed"
        job.error_code = code[:64]
        job.error_message = " ".join(message.split())[:1000]
        job.finished_at = _utcnow() if terminal else None
        db.commit()
        return _run_result(job)
    finally:
        db.close()


def _load_json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item)[:80] for item in parsed if str(item).strip()][:50]


def _load_locator(block: BidEvidenceBlock) -> dict:
    try:
        parsed = json.loads(block.locator_json or "{}")
    except (TypeError, ValueError):
        parsed = {}
    if isinstance(parsed, dict) and parsed:
        return parsed
    return {
        key: value
        for key, value in {
            "page": block.page,
            "sheet": block.sheet,
            "cell_range": block.cell_range,
            "section": block.section,
        }.items()
        if value is not None
    }


def _run_result(job: BidEvidenceIndexJob) -> TenderEvidenceIndexRunResult:
    return TenderEvidenceIndexRunResult(
        job_uuid=job.job_uuid,
        status=job.status,
        stage=job.stage,
        attempt_count=job.attempt_count,
        indexed_block_count=job.indexed_block_count,
        error_code=job.error_code,
    )


def _max_attempts() -> int:
    try:
        value = int(
            os.environ.get("TENDER_EVIDENCE_INDEX_MAX_ATTEMPTS", "3")
        )
    except ValueError:
        value = 3
    return max(1, min(value, 10))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
