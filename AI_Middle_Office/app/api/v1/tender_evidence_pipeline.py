from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.bidding import BidProject
from app.models.tender_evidence_index import BidEvidenceIndexJob
from app.models.tender_parse_pipeline import BidTenderParseJob
from app.models.user import User
from app.services.rbac import has_any_role
from app.services.tender_parse_dispatcher import dispatch_tender_parse_job
from app.services.tender_evidence_index_dispatcher import (
    dispatch_tender_evidence_index_job,
)
from app.services.tender_evidence_indexing import (
    TenderEvidenceIndexConflict,
    latest_project_index_job,
    record_index_dispatch,
    record_index_dispatch_failure,
    requeue_tender_evidence_index_job,
    serialize_evidence_index_job,
)
from app.services.tender_parse_pipeline import (
    TenderParsePipelineConflict,
    TenderParsePipelineError,
    create_tender_parse_job,
    record_tender_parse_dispatch,
    record_tender_parse_dispatch_failure,
    requeue_tender_parse_job,
    serialize_tender_parse_job,
)
from app.services.tender_source_storage import (
    MinioTenderSourceStorage,
    TenderSourceStorageError,
)
from mcp_servers.tender_evidence.hybrid_client import hybrid_search_enabled


router = APIRouter()
logger = logging.getLogger(__name__)
BIDDING_ROLES = {
    "admin",
    "system_admin",
    "staff",
    "manager",
    "quote_user",
    "quote_operator",
}
VIEW_ALL_ROLES = {"admin", "system_admin", "manager", "quote_operator"}
FILE_TYPES = {
    "auto",
    "tender_document",
    "clarification",
    "addendum",
    "contract",
    "drawing",
    "bill_of_quantities",
    "other",
}


def _storage() -> MinioTenderSourceStorage:
    return MinioTenderSourceStorage()


def _ensure_feature_and_role(current_user: User) -> None:
    if not settings.feature_bidding_mvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NOT_FOUND",
        )
    if not has_any_role(current_user, BIDDING_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PERMISSION_DENIED",
        )


def _get_project(
    db: Session,
    project_uuid: str,
    current_user: User,
) -> BidProject:
    _ensure_feature_and_role(current_user)
    project = (
        db.query(BidProject)
        .filter(BidProject.project_uuid == project_uuid)
        .one_or_none()
    )
    can_view_all = has_any_role(current_user, VIEW_ALL_ROLES)
    can_manage = (
        can_view_all
        or project is not None
        and (
            project.created_by == current_user.id
            or project.owner_user_id == current_user.id
        )
    )
    if project is None or not can_manage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BID_PROJECT_NOT_FOUND",
        )
    return project


def _get_job(
    db: Session,
    *,
    project_id: int,
    job_uuid: str,
) -> BidTenderParseJob:
    job = (
        db.query(BidTenderParseJob)
        .filter(
            BidTenderParseJob.project_id == project_id,
            BidTenderParseJob.job_uuid == job_uuid,
        )
        .one_or_none()
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TENDER_PARSE_JOB_NOT_FOUND",
        )
    return job


def _get_index_job(
    db: Session,
    *,
    project_id: int,
    job_uuid: str,
) -> BidEvidenceIndexJob:
    job = (
        db.query(BidEvidenceIndexJob)
        .filter(
            BidEvidenceIndexJob.project_id == project_id,
            BidEvidenceIndexJob.job_uuid == job_uuid,
        )
        .one_or_none()
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TENDER_INDEX_JOB_NOT_FOUND",
        )
    return job


def _dispatch_and_record(db: Session, job_uuid: str) -> None:
    try:
        task_id = dispatch_tender_parse_job(job_uuid)
        record_tender_parse_dispatch(
            db,
            job_uuid=job_uuid,
            celery_task_id=task_id,
        )
        db.commit()
    except Exception as exc:
        logger.exception(
            "failed to dispatch tender parse job job_uuid=%s",
            job_uuid,
        )
        db.rollback()
        record_tender_parse_dispatch_failure(db, job_uuid=job_uuid)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TENDER_PARSE_DISPATCH_FAILED",
        ) from exc


def _dispatch_index_and_record(db: Session, job_uuid: str) -> None:
    try:
        task_id = dispatch_tender_evidence_index_job(job_uuid)
        record_index_dispatch(
            db,
            job_uuid=job_uuid,
            celery_task_id=task_id,
        )
        db.commit()
    except Exception as exc:
        logger.exception(
            "failed to dispatch evidence index job job_uuid=%s",
            job_uuid,
        )
        db.rollback()
        record_index_dispatch_failure(db, job_uuid=job_uuid)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TENDER_INDEX_DISPATCH_FAILED",
        ) from exc


@router.post(
    "/admin/bidding/projects/{project_uuid}/evidence/parse-jobs",
    status_code=status.HTTP_201_CREATED,
    summary="保存原始招标文件并创建证据解析任务",
)
async def create_parse_job(
    project_uuid: str,
    file: UploadFile = File(...),
    file_type: str = Form("auto"),
    document_key: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    normalized_file_type = (file_type or "").strip()
    if normalized_file_type not in FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="INVALID_BID_FILE_TYPE",
        )
    max_bytes = max(1, int(settings.minio_max_upload_mb)) * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="TENDER_FILE_TOO_LARGE",
        )
    normalized_document_key = (
        document_key
        or f"{normalized_file_type}-{file.filename or 'tender-file'}"
    )
    try:
        created = create_tender_parse_job(
            db,
            project_uuid=project.project_uuid,
            content=content,
            original_filename=file.filename or "tender-file",
            content_type=file.content_type,
            file_type=normalized_file_type,
            document_key=normalized_document_key,
            current_user=current_user,
            storage=_storage(),
        )
    except TenderSourceStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TENDER_SOURCE_STORAGE_UNAVAILABLE",
        ) from exc
    except TenderParsePipelineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if created.status == "queued":
        _dispatch_and_record(db, created.job_uuid)
    job = _get_job(
        db,
        project_id=project.id,
        job_uuid=created.job_uuid,
    )
    return api_ok(
        {
            **serialize_tender_parse_job(db, job, include_events=True),
            "idempotent": created.idempotent,
        },
        message="招标原始文件已保存，解析任务已创建",
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/evidence/parse-jobs",
    summary="查询项目的证据解析任务",
)
def list_parse_jobs(
    project_uuid: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    jobs = (
        db.query(BidTenderParseJob)
        .filter(BidTenderParseJob.project_id == project.id)
        .order_by(BidTenderParseJob.created_at.desc(), BidTenderParseJob.id.desc())
        .limit(limit)
        .all()
    )
    return api_ok(
        [serialize_tender_parse_job(db, item) for item in jobs],
        total=len(jobs),
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/evidence/parse-jobs/{job_uuid}",
    summary="查询证据解析任务详情",
)
def get_parse_job(
    project_uuid: str,
    job_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    job = _get_job(db, project_id=project.id, job_uuid=job_uuid)
    return api_ok(serialize_tender_parse_job(db, job, include_events=True))


@router.post(
    "/admin/bidding/projects/{project_uuid}/evidence/parse-jobs/{job_uuid}/retry",
    summary="重试可恢复的证据解析任务",
)
def retry_parse_job(
    project_uuid: str,
    job_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    _get_job(db, project_id=project.id, job_uuid=job_uuid)
    try:
        requeue_tender_parse_job(
            db,
            job_uuid=job_uuid,
            project_id=project.id,
        )
        db.commit()
    except TenderParsePipelineConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    _dispatch_and_record(db, job_uuid)
    job = _get_job(db, project_id=project.id, job_uuid=job_uuid)
    return api_ok(
        serialize_tender_parse_job(db, job, include_events=True),
        message="证据解析任务已重新入队",
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/evidence/index-jobs",
    summary="查询项目的混合检索索引任务",
)
def list_index_jobs(
    project_uuid: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    jobs = (
        db.query(BidEvidenceIndexJob)
        .filter(BidEvidenceIndexJob.project_id == project.id)
        .order_by(
            BidEvidenceIndexJob.manifest_version.desc(),
            BidEvidenceIndexJob.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return api_ok(
        [serialize_evidence_index_job(item) for item in jobs],
        total=len(jobs),
        hybrid_enabled=hybrid_search_enabled(),
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/evidence/index-status",
    summary="查询当前招标证据混合索引状态",
)
def get_index_status(
    project_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    latest = latest_project_index_job(db, project_id=project.id)
    enabled = hybrid_search_enabled()
    ready = bool(
        enabled
        and latest
        and latest.status == "completed"
        and latest.indexed_block_count == latest.requested_block_count
    )
    return api_ok(
        {
            "hybrid_enabled": enabled,
            "hybrid_ready": ready,
            "effective_search_backend": (
                "hybrid_rrf" if ready else "database_lexical"
            ),
            "latest_job": (
                serialize_evidence_index_job(latest) if latest else None
            ),
            "fallback": "database_lexical",
        }
    )


@router.post(
    "/admin/bidding/projects/{project_uuid}/evidence/index-jobs/"
    "{job_uuid}/retry",
    summary="重试可恢复的招标证据索引任务",
)
def retry_index_job(
    project_uuid: str,
    job_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    if not hybrid_search_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TENDER_EVIDENCE_HYBRID_DISABLED",
        )
    _get_index_job(db, project_id=project.id, job_uuid=job_uuid)
    try:
        job = requeue_tender_evidence_index_job(
            db,
            job_uuid=job_uuid,
            project_id=project.id,
        )
        db.commit()
    except TenderEvidenceIndexConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    _dispatch_index_and_record(db, job.job_uuid)
    db.refresh(job)
    return api_ok(
        serialize_evidence_index_job(job),
        message="招标证据索引任务已重新入队",
    )
