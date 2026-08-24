"""External bid-assessment v1 API facade."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import settings
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import (
    BidAssessment,
    BidFileObject,
    BidUploadBatch,
    BidUploadBatchFile,
)
from app.models.user import User
from app.schemas.bid_assessment import (
    BidAssessmentCloneForLotIn,
    BidAssessmentCreateIn,
    BidLotSelectionIn,
    BidRunCancelIn,
    BidRunCreateIn,
    BidRunRetryIn,
    BidUploadBatchAbandonIn,
    BidUploadBatchCreateIn,
    BidUploadBatchCommitIn,
    BidUploadBatchDeactivationCreateIn,
    BidUploadFileCreateIn,
)
from app.services.bid_assessment_idempotency import (
    BidIdempotencyError,
    BidIdempotencyInProgress,
    BidIdempotencyKeyReused,
    begin_idempotent_request,
    complete_idempotent_request,
    execute_idempotent_request,
    fail_idempotent_request,
    validate_idempotency_key,
)
from app.services.bid_assessment_snapshots import assessment_etag, build_assessment_snapshot
from app.services.bid_document_pages import (
    PARSE_STATUSES,
    BidDocumentManifestNotFound,
    bid_document_page_headers,
    build_bid_document_page,
)
from app.services.bid_document_versions import (
    BidDocumentVersionNotFound,
    bid_document_version_headers,
    build_bid_document_version_detail,
    iter_bid_download,
    load_visible_bid_document_version,
    safe_content_disposition,
    safe_download_mime_type,
)
from app.services.bid_assessment_clones import (
    CLONE_FOR_LOT_ROUTE_TEMPLATE,
    BidAssessmentCloneCandidatesNotReady,
    BidAssessmentCloneLotNotInManifest,
    BidAssessmentCloneManifestMismatch,
    BidAssessmentCloneNotFound,
    BidAssessmentCloneSameLot,
    BidAssessmentCloneStateConflict,
    BidAssessmentCloneVersionMismatch,
    clone_bid_assessment_for_lot,
)
from app.services.bid_lot_candidates import (
    BidLotManifestNotFound,
    bid_lot_candidate_headers,
    build_bid_lot_candidate_page,
)
from app.services.bid_lot_selections import (
    LOT_SELECTION_ROUTE_TEMPLATE,
    BidLotCandidatesNotReady,
    BidLotNotInManifest,
    BidLotScopeAlreadyBound,
    BidLotSelectionManifestMismatch,
    BidLotSelectionNotFound,
    BidLotSelectionStateConflict,
    BidLotSelectionVersionMismatch,
    select_bid_lot,
)
from app.services.bid_run_bootstrap import (
    RUN_CREATE_ROUTE_TEMPLATE,
    BidActiveRunExists,
    BidRunAlreadyExistsForInput,
    BidRunInputNotReady,
    BidRunNotFound,
    BidRunVersionMismatch,
    create_manual_run,
)
from app.services.bid_run_snapshots import (
    build_run_progress_snapshot,
    run_etag,
    run_snapshot_headers,
)
from app.services.bid_run_lifecycle import (
    RUN_CANCEL_ROUTE_TEMPLATE,
    RUN_RETRY_ROUTE_TEMPLATE,
    BidRunInputStale as BidLifecycleRunInputStale,
    BidRunLifecycleNotFound,
    BidRunLifecycleVersionMismatch,
    BidRunNotCancellable,
    BidRunNotRetryable,
    request_run_cancellation,
    retry_run_from_latest_checkpoint,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.services.bid_assessments import (
    BidAssessmentExternalRefConflict,
    create_bid_assessment,
)
from app.services.bid_upload_batch_snapshots import (
    build_upload_batch_snapshot,
    current_upload_limits,
    upload_batch_file_etag,
    upload_batch_etag,
)
from app.services.bid_upload_batches import (
    BidUploadBatchAlreadyOpen,
    BidUploadBatchAssessmentNotFound,
    BidUploadBatchBaselineStale,
    BidUploadBatchStateConflict,
    BidUploadBatchVersionMismatch,
    OPEN_BATCH_STATUSES,
    create_bid_upload_batch,
)
from app.services.bid_upload_batch_deactivations import (
    BidUploadBatchDeactivationBaselineStale,
    BidUploadBatchDeactivationBatchCommitted,
    BidUploadBatchDeactivationConflict,
    BidUploadBatchDeactivationNotAllowed,
    BidUploadBatchDeactivationNotFound,
    BidUploadBatchDeactivationStateConflict,
    BidUploadBatchDeactivationTargetInvalid,
    BidUploadBatchDeactivationVersionMismatch,
    add_bid_upload_batch_deactivations,
)
from app.services.bid_upload_batch_commits import (
    BidUploadBatchAlreadyCommitted,
    BidUploadBatchCommitBaselineStale,
    BidUploadBatchCommitNotFound,
    BidUploadBatchCommitVersionMismatch,
    BidUploadBatchExpectedCountMismatch,
    BidUploadBatchExpectedDeactivationCountMismatch,
    BidUploadBatchMergeConflict,
    BidUploadBatchNotReady,
    commit_bid_upload_batch,
)
from app.services.bid_upload_batch_abandonments import (
    BidUploadBatchAbandonmentAlreadyCommitted,
    BidUploadBatchAbandonmentNotFound,
    BidUploadBatchAbandonmentStateConflict,
    BidUploadBatchAbandonmentVersionMismatch,
    BidUploadBatchAlreadyAbandoned,
    abandon_bid_upload_batch,
)
from app.services.bid_upload_file_storage import (
    build_temporary_object_key,
    get_bid_upload_object_storage,
)
from app.services.bid_upload_files import (
    BidUploadBatchStateConflict as BidUploadFileBatchStateConflict,
    BidUploadBatchTooLarge,
    BidUploadClientFileConflict,
    BidUploadFileContentInvalid,
    BidUploadFileError,
    BidUploadFileResourceNotFound,
    BidUploadFileStorageUnavailable,
    BidUploadFileTooLarge,
    BidUploadFileTypeUnsupported,
    BidUploadReplacementTargetInvalid,
    inspect_bid_upload,
    register_bid_upload_file,
)
from app.services.bid_upload_file_removals import (
    BidUploadFileRemoval,
    BidUploadFileRemovalBatchCommitted,
    BidUploadFileRemovalBatchStateConflict,
    BidUploadFileRemovalNotFound,
    BidUploadFileRemovalVersionMismatch,
    remove_bid_upload_batch_file,
)
from app.services.rbac import has_admin_role


logger = logging.getLogger(__name__)
router = APIRouter()

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_ROUTE_TEMPLATE = "/api/v1/bid-assessments"
_UPLOAD_BATCH_ROUTE_TEMPLATE = (
    "/api/v1/bid-assessments/{assessment_id}/upload-batches"
)
_UPLOAD_FILE_ROUTE_TEMPLATE = "/api/v1/bid-upload-batches/{batch_id}/files"
_UPLOAD_FILE_ITEM_ROUTE_TEMPLATE = (
    "/api/v1/bid-upload-batches/{batch_id}/files/{file_id}"
)
_UPLOAD_DEACTIVATION_ROUTE_TEMPLATE = (
    "/api/v1/bid-upload-batches/{batch_id}/deactivations"
)
_UPLOAD_COMMIT_ROUTE_TEMPLATE = "/api/v1/bid-upload-batches/{batch_id}/commit"
_UPLOAD_ABANDON_ROUTE_TEMPLATE = "/api/v1/bid-upload-batches/{batch_id}/abandon"
_STRONG_ETAG_PATTERN = re.compile(r'^"[\x21\x23-\x7e]{1,200}"$')
_DOCUMENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _request_id(request: Request) -> str:
    candidate = str(getattr(request.state, "trace_id", "") or "")
    if 1 <= len(candidate) <= 80 and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return f"req_{uuid.uuid4().hex}"


def _field_errors(exc: ValidationError) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        rows.append(
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "type": str(error.get("type") or "value_error"),
                "message": str(error.get("msg") or "invalid value"),
            }
        )
    return rows


def _error_response(
    *,
    status_code: int,
    request_id: str,
    error_code: str,
    message: str,
    retryable: bool,
    field_errors: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": message,
            "data": None,
            "error": {
                "error_code": error_code,
                "retryable": retryable,
                "field_errors": field_errors or [],
                "details": details or {},
                "recovery": recovery,
            },
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


def _resource_headers(body: dict[str, Any], *, replayed: bool) -> dict[str, str]:
    snapshot = dict(body.get("data") or {})
    assessment_id = str(snapshot["assessment_id"])
    row_version = int(snapshot["row_version"])
    headers = {
        "Location": f"/api/v1/bid-assessments/{assessment_id}",
        "ETag": assessment_etag(assessment_id, row_version),
        "X-Resource-Version": str(row_version),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _snapshot_headers(assessment: BidAssessment) -> dict[str, str]:
    return {
        "ETag": assessment_etag(str(assessment.id), int(assessment.row_version)),
        "X-Resource-Version": str(int(assessment.row_version)),
        "Cache-Control": "private, no-store",
    }


def _upload_batch_headers(body: dict[str, Any], *, replayed: bool) -> dict[str, str]:
    snapshot = dict(body.get("data") or {})
    batch_id = str(snapshot["batch_id"])
    row_version = int(snapshot["row_version"])
    headers = {
        "Location": f"/api/v1/bid-upload-batches/{batch_id}",
        "ETag": upload_batch_etag(
            batch_id,
            row_version,
            limits=dict(snapshot["limits"]),
        ),
        "X-Resource-Version": str(row_version),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _upload_batch_snapshot_headers(batch: BidUploadBatch) -> dict[str, str]:
    return {
        "ETag": upload_batch_etag(str(batch.id), int(batch.row_version)),
        "X-Resource-Version": str(int(batch.row_version)),
        "Cache-Control": "private, no-store",
    }


def _upload_commit_headers(body: dict[str, Any], *, replayed: bool) -> dict[str, str]:
    data = dict(body.get("data") or {})
    assessment = dict(data.get("assessment") or {})
    batch = dict(data.get("batch") or {})
    assessment_id = str(assessment["assessment_id"])
    headers = {
        "Location": f"/api/v1/bid-assessments/{assessment_id}",
        "ETag": assessment_etag(assessment_id, int(assessment["row_version"])),
        "X-Resource-Version": str(int(assessment["row_version"])),
        "X-Batch-ETag": str(batch["etag"]),
        "X-Batch-Resource-Version": str(int(batch["row_version"])),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _lot_selection_headers(body: dict[str, Any], *, replayed: bool) -> dict[str, str]:
    data = dict(body.get("data") or {})
    assessment = dict(data.get("assessment") or {})
    assessment_id = str(assessment["assessment_id"])
    row_version = int(assessment["row_version"])
    headers = {
        "Location": f"/api/v1/bid-assessments/{assessment_id}",
        "ETag": assessment_etag(assessment_id, row_version),
        "X-Resource-Version": str(row_version),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _run_command_headers(body: dict[str, Any], *, replayed: bool) -> dict[str, str]:
    snapshot = dict(body.get("data") or {})
    run_id = str(snapshot["run_id"])
    row_version = int(snapshot["row_version"])
    headers = {
        "Location": (
            f"/api/v1/bid-assessments/{snapshot['assessment_id']}/runs/{run_id}"
        ),
        "ETag": run_etag(run_id, row_version, snapshot),
        "X-Resource-Version": str(row_version),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _upload_file_headers(body: dict[str, Any], *, replayed: bool) -> dict[str, str]:
    data = dict(body.get("data") or {})
    file_snapshot = dict(data.get("file") or {})
    batch_snapshot = dict(data.get("batch") or {})
    batch_id = str(batch_snapshot["batch_id"])
    batch_file_id = str(file_snapshot["batch_file_id"])
    file_row_version = int(file_snapshot["row_version"])
    batch_row_version = int(batch_snapshot["row_version"])
    headers = {
        "Location": (
            f"/api/v1/bid-upload-batches/{batch_id}/files/{batch_file_id}"
        ),
        "ETag": upload_batch_file_etag(batch_file_id, file_row_version),
        "X-Resource-Version": str(file_row_version),
        "X-Batch-ETag": upload_batch_etag(batch_id, batch_row_version),
        "X-Batch-Resource-Version": str(batch_row_version),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _removed_upload_file_headers(
    receipt: dict[str, Any],
    *,
    replayed: bool,
) -> dict[str, str]:
    headers = {
        "X-Batch-ETag": str(receipt["batch_etag"]),
        "X-Batch-Resource-Version": str(int(receipt["batch_row_version"])),
        "Cache-Control": "private, no-store",
    }
    if replayed:
        headers["Idempotent-Replay"] = "true"
    return headers


def _json_response_body(response: JSONResponse) -> dict[str, Any]:
    return json.loads(bytes(response.body).decode("utf-8"))


def _upload_file_error_response(
    exc: BidUploadFileError,
    *,
    request_id: str,
) -> JSONResponse:
    if isinstance(exc, BidUploadFileTooLarge):
        return _error_response(
            status_code=413,
            request_id=request_id,
            error_code=exc.code,
            message="单文件超过当前上传上限",
            retryable=False,
            details={
                "max_file_bytes": exc.max_file_bytes,
                "observed_bytes": exc.observed_bytes,
            },
            recovery={"action": "choose_smaller_file"},
        )
    if isinstance(exc, BidUploadBatchTooLarge):
        return _error_response(
            status_code=413,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次超过当前文件数或总字节上限",
            retryable=False,
            details={
                "reason": exc.reason,
                "limit": exc.limit,
                "observed": exc.observed,
            },
            recovery={"action": "get_latest_upload_batch"},
        )
    if isinstance(exc, BidUploadFileTypeUnsupported):
        return _error_response(
            status_code=415,
            request_id=request_id,
            error_code=exc.code,
            message="文件扩展名或 MIME 类型不受支持",
            retryable=False,
            details={
                "filename": exc.filename,
                "extension": exc.extension,
                "declared_mime_type": exc.declared_mime,
            },
            recovery={"action": "choose_supported_file_type"},
        )
    if isinstance(exc, BidUploadFileContentInvalid):
        details: dict[str, Any] = {"reason": exc.reason}
        if exc.expected_sha256 is not None:
            details["expected_sha256"] = exc.expected_sha256
        if exc.actual_sha256 is not None:
            details["actual_sha256"] = exc.actual_sha256
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code=exc.code,
            message="文件内容、哈希或类型校验失败",
            retryable=False,
            details=details,
            recovery={"action": "replace_invalid_file"},
        )
    if isinstance(exc, BidUploadReplacementTargetInvalid):
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="替换目标不属于当前资料基线",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    if isinstance(exc, BidUploadClientFileConflict):
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="同一 client_file_id 已绑定不同文件内容或元数据",
            retryable=False,
            details={"existing_batch_file_id": exc.existing_file_id},
            recovery={"action": "use_new_client_file_id"},
        )
    if isinstance(exc, BidUploadFileBatchStateConflict):
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次当前状态不允许接收文件",
            retryable=False,
            details={"batch_status": exc.status},
            recovery={"action": "get_latest_upload_batch"},
        )
    if isinstance(exc, BidUploadFileResourceNotFound):
        return _upload_batch_not_found_response(request_id)
    return _error_response(
        status_code=503,
        request_id=request_id,
        error_code="BID_STORAGE_UNAVAILABLE",
        message="文件对象或上传记录暂时无法持久化",
        retryable=True,
        recovery={"action": "retry_same_request"},
    )


def _complete_upload_error_idempotency(
    db: Session,
    *,
    record_id: str,
    response: JSONResponse,
) -> None:
    complete_idempotent_request(
        db,
        record_id=record_id,
        response_status_code=int(response.status_code),
        response_body=_json_response_body(response),
    )
    db.commit()


def _fail_upload_idempotency(
    db: Session,
    *,
    record_id: str,
    failure_code: str,
) -> None:
    try:
        fail_idempotent_request(
            db,
            record_id=record_id,
            failure_code=failure_code,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_file_idempotency_failure_mark_failed",
            extra={"record_id": record_id, "failure_code": failure_code},
        )


async def _compensate_upload_object(
    *,
    storage,
    object_key: str | None,
    request_id: str,
) -> None:
    if storage is None or not object_key:
        return
    try:
        await asyncio.to_thread(storage.delete, object_key=object_key)
    except Exception:
        logger.exception(
            "bid_upload_file_compensation_delete_failed",
            extra={"request_id": request_id, "object_key": object_key},
        )


async def _delete_unreferenced_upload_object_after_commit(
    *,
    object_key: str | None,
    request_id: str,
) -> None:
    if not object_key:
        return
    try:
        storage = get_bid_upload_object_storage()
        await asyncio.to_thread(storage.delete, object_key=object_key)
    except Exception:
        # The database no longer references this exact key. The reference-aware
        # orphan task is the durable fallback and must never broaden the target.
        logger.exception(
            "bid_upload_file_post_commit_delete_failed",
            extra={"request_id": request_id, "object_key": object_key},
        )


def _if_none_match_matches(value: str | None, current_etag: str) -> bool:
    """Apply weak comparison for an If-None-Match value on a GET request."""

    if not value:
        return False
    for raw_token in value.split(","):
        token = raw_token.strip()
        if token == "*":
            return True
        if token[:2].lower() == "w/":
            token = token[2:].strip()
        if token == current_etag:
            return True
    return False


def _not_found_response(request_id: str) -> JSONResponse:
    return _error_response(
        status_code=404,
        request_id=request_id,
        error_code="BID_RESOURCE_NOT_FOUND",
        message="Assessment 不存在或不可见",
        retryable=False,
        recovery={"action": "return_to_assessment_list"},
    )


def _upload_batch_not_found_response(request_id: str) -> JSONResponse:
    return _error_response(
        status_code=404,
        request_id=request_id,
        error_code="BID_RESOURCE_NOT_FOUND",
        message="上传批次不存在或不可见",
        retryable=False,
        recovery={"action": "return_to_assessment"},
    )


def _document_version_not_found_response(request_id: str) -> JSONResponse:
    return _error_response(
        status_code=404,
        request_id=request_id,
        error_code="BID_RESOURCE_NOT_FOUND",
        message="文件版本不存在或不可见",
        retryable=False,
        recovery={"action": "return_to_document_list"},
    )


def _assessment_is_visible(
    db: Session,
    *,
    assessment_id: str,
    current_user: User,
) -> bool:
    owner_row = (
        db.query(BidAssessment.created_by)
        .filter(BidAssessment.id == assessment_id)
        .one_or_none()
    )
    if owner_row is None:
        return False
    return int(owner_row[0]) == int(current_user.id) or has_admin_role(current_user)


def _visible_upload_batch(
    db: Session,
    *,
    batch_id: str,
    current_user: User,
) -> BidUploadBatch | None:
    query = (
        db.query(BidUploadBatch)
        .join(BidAssessment, BidAssessment.id == BidUploadBatch.assessment_id)
        .filter(BidUploadBatch.id == batch_id)
    )
    if not has_admin_role(current_user):
        query = query.filter(BidAssessment.created_by == int(current_user.id))
    return query.one_or_none()


def _document_list_field_errors(
    *,
    manifest_id: str | None,
    document_type: str | None,
    parse_status: str | None,
    include_versions: str,
    page: str,
    page_size: str,
) -> tuple[list[dict[str, Any]], bool, int, int]:
    errors: list[dict[str, Any]] = []
    if manifest_id is not None and not (
        1 <= len(manifest_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(manifest_id)
    ):
        errors.append(
            {
                "field": "manifest_id",
                "type": "string_pattern_mismatch",
                "message": "manifest_id 格式无效",
            }
        )
    if document_type is not None and not _DOCUMENT_TYPE_PATTERN.fullmatch(
        document_type
    ):
        errors.append(
            {
                "field": "document_type",
                "type": "string_pattern_mismatch",
                "message": "document_type 格式无效",
            }
        )
    if parse_status is not None and parse_status not in PARSE_STATUSES:
        errors.append(
            {
                "field": "parse_status",
                "type": "enum",
                "message": "parse_status 不在允许集合内",
            }
        )
    if include_versions not in {"true", "false"}:
        errors.append(
            {
                "field": "include_versions",
                "type": "boolean_parsing",
                "message": "include_versions 只能是 true 或 false",
            }
        )

    parsed_page = 1
    if not page.isascii() or not page.isdigit() or int(page) < 1:
        errors.append(
            {
                "field": "page",
                "type": "greater_than_equal",
                "message": "page 必须是大于等于 1 的整数",
            }
        )
    else:
        parsed_page = int(page)

    parsed_page_size = 20
    if (
        not page_size.isascii()
        or not page_size.isdigit()
        or not 1 <= int(page_size) <= 100
    ):
        errors.append(
            {
                "field": "page_size",
                "type": "less_than_equal",
                "message": "page_size 必须是 1 到 100 的整数",
            }
        )
    else:
        parsed_page_size = int(page_size)
    return errors, include_versions == "true", parsed_page, parsed_page_size


@router.get(
    "/bid-assessments/{assessment_id}",
    summary="获取研判 Assessment 快照",
    operation_id="getBidAssessment",
)
def get_assessment_endpoint(
    assessment_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the authoritative snapshot used by initial load and recovery flows."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _not_found_response(request_id)
    if not 1 <= len(assessment_id) <= 80:
        return _not_found_response(request_id)

    try:
        assessment = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == assessment_id)
            .one_or_none()
        )
        if assessment is None or (
            int(assessment.created_by) != int(current_user.id)
            and not has_admin_role(current_user)
        ):
            return _not_found_response(request_id)

        headers = _snapshot_headers(assessment)
        if _if_none_match_matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)

        snapshot = build_assessment_snapshot(db, assessment)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_assessment_snapshot_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Assessment 快照暂时无法读取",
            retryable=True,
            recovery={"action": "retry_snapshot_read"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "ok",
            "data": snapshot,
            "error": None,
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.get(
    "/bid-assessments/{assessment_id}/documents",
    summary="查询 Assessment 文件与可见版本清单",
    operation_id="listBidDocuments",
)
def list_bid_documents_endpoint(
    assessment_id: str,
    request: Request,
    manifest_id: str | None = None,
    document_type: str | None = None,
    parse_status: str | None = None,
    include_versions: str = "false",
    page: str = "1",
    page_size: str = "20",
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the authoritative Manifest-scoped document page for API-20."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _not_found_response(request_id)
    if not 1 <= len(assessment_id) <= 80:
        return _not_found_response(request_id)

    field_errors, include_all_versions, page_number, page_limit = (
        _document_list_field_errors(
            manifest_id=manifest_id,
            document_type=document_type,
            parse_status=parse_status,
            include_versions=include_versions,
            page=page,
            page_size=page_size,
        )
    )
    if field_errors:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="文件列表查询参数无效",
            retryable=False,
            field_errors=field_errors,
            recovery={"action": "correct_document_list_query"},
        )

    try:
        assessment = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == assessment_id)
            .one_or_none()
        )
        if assessment is None or (
            int(assessment.created_by) != int(current_user.id)
            and not has_admin_role(current_user)
        ):
            return _not_found_response(request_id)

        page_payload = build_bid_document_page(
            db,
            assessment,
            manifest_id=manifest_id,
            document_type=document_type,
            parse_status=parse_status,
            include_versions=include_all_versions,
            page=page_number,
            page_size=page_limit,
        )
        headers = bid_document_page_headers(assessment, page_payload)
        if _if_none_match_matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)
    except BidDocumentManifestNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_document_page_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
                "manifest_id": manifest_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="文件列表暂时无法读取",
            retryable=True,
            recovery={"action": "retry_document_list_read"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "ok",
            **page_payload,
            "error": None,
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.get(
    "/bid-assessments/{assessment_id}/lots",
    summary="查询 Assessment 标段候选",
    operation_id="listBidLotCandidates",
)
def list_bid_lot_candidates_endpoint(
    assessment_id: str,
    request: Request,
    manifest_id: str | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current or explicit Manifest lot projection without side effects."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _not_found_response(request_id)
    if not (
        1 <= len(assessment_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(assessment_id)
    ):
        return _not_found_response(request_id)
    if manifest_id is not None and not (
        1 <= len(manifest_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(manifest_id)
    ):
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="标段候选查询参数无效",
            retryable=False,
            field_errors=[
                {
                    "field": "manifest_id",
                    "type": "string_pattern_mismatch",
                    "message": "manifest_id 格式无效",
                }
            ],
            recovery={"action": "correct_lot_candidate_query"},
        )

    try:
        assessment = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == assessment_id)
            .one_or_none()
        )
        if assessment is None or (
            int(assessment.created_by) != int(current_user.id)
            and not has_admin_role(current_user)
        ):
            return _not_found_response(request_id)

        page = build_bid_lot_candidate_page(
            db,
            assessment,
            manifest_id=manifest_id,
        )
        headers = bid_lot_candidate_headers(assessment, page)
        if _if_none_match_matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)
    except BidLotManifestNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_lot_candidate_page_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
                "manifest_id": manifest_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="标段候选暂时无法读取",
            retryable=True,
            recovery={"action": "retry_lot_candidate_read"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "ok",
            "data": page,
            "error": None,
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments/{assessment_id}/lot-selection",
    status_code=202,
    summary="选择标段并固化 Assessment Scope",
    operation_id="selectBidLot",
)
async def select_bid_lot_endpoint(
    assessment_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Atomically bind one current, evidence-backed LotCandidate as Scope."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _not_found_response(request_id)
    if not (
        1 <= len(assessment_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(assessment_id)
    ):
        return _not_found_response(request_id)

    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供 Assessment If-Match",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-03 返回的单个强 ETag",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidLotSelectionIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="标段选择请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )

    try:
        if not _assessment_is_visible(
            db,
            assessment_id=assessment_id,
            current_user=current_user,
        ):
            return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_lot_selection_visibility_check_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Assessment 暂时无法读取",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=LOT_SELECTION_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "assessment_id": assessment_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: select_bid_lot(
                command_db,
                assessment_id=assessment_id,
                expected_assessment_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同标段选择请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同标段选择请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidLotSelectionVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="Assessment 已更新，请刷新后重试",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-assessments/{exc.assessment_id}"
                ),
            },
            recovery={"action": "get_latest_assessment_snapshot"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidLotSelectionManifestMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code=exc.code,
            message="只能选择当前资料清单中的标段候选",
            retryable=False,
            details={
                "provided_manifest_id": exc.provided_manifest_id,
                "current_manifest_id": exc.current_manifest_id,
            },
            recovery={
                "action": "get_current_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidLotCandidatesNotReady as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="标段候选尚未达到可选择状态",
            retryable=True,
            details={"generation_status": exc.status, "reason": exc.reason},
            recovery={
                "action": "wait_for_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidLotNotInManifest as exc:
        db.rollback()
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code=exc.code,
            message="标段候选不属于指定资料清单",
            retryable=False,
            details={"lot_id": exc.lot_id, "manifest_id": exc.manifest_id},
            recovery={
                "action": "get_current_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidLotScopeAlreadyBound as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Assessment 已绑定其他标段",
            retryable=False,
            details={
                "scope_id": exc.scope_id,
                "selected_lot_id": exc.selected_lot_id,
                "manifest_id": exc.manifest_id,
            },
            recovery={
                "action": "assessment.create_for_other_lot",
                "resource_url": (
                    f"/api/v1/bid-assessments/{assessment_id}/clone-for-lot"
                ),
            },
        )
    except BidLotSelectionStateConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Assessment 当前状态不允许选择标段",
            retryable=False,
            details={
                "lifecycle_status": exc.lifecycle_status,
                "current_status": exc.business_status,
                "allowed_statuses": ["awaiting_lot_selection"],
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidLotSelectionNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        logger.exception(
            "bid_lot_selection_integrity_conflict",
            extra={"request_id": request_id, "assessment_id": assessment_id},
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_LOT_SCOPE_ALREADY_BOUND",
            message="Assessment 标段 Scope 已由并发请求绑定",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_lot_selection_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="标段选择暂时无法保存",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_lot_selection_headers(
            response_body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments/{assessment_id}/clone-for-lot",
    status_code=201,
    summary="复用资料为其他标段创建独立研判",
    operation_id="cloneBidAssessmentForLot",
)
async def clone_bid_assessment_for_lot_endpoint(
    assessment_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an independently authorized Assessment for another lot."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _not_found_response(request_id)
    if not (
        1 <= len(assessment_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(assessment_id)
    ):
        return _not_found_response(request_id)

    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供源 Assessment If-Match",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-03 返回的单个强 ETag",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidAssessmentCloneForLotIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="另一标段研判创建请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )

    try:
        if not _assessment_is_visible(
            db,
            assessment_id=assessment_id,
            current_user=current_user,
        ):
            return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_assessment_clone_visibility_check_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="源 Assessment 暂时无法读取",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=CLONE_FOR_LOT_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "assessment_id": assessment_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: clone_bid_assessment_for_lot(
                command_db,
                assessment_id=assessment_id,
                expected_assessment_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同另一标段研判创建请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同的另一标段研判创建请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidAssessmentCloneVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="源 Assessment 已更新，请刷新后重试",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-assessments/{exc.assessment_id}"
                ),
            },
            recovery={"action": "get_latest_assessment_snapshot"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidAssessmentCloneManifestMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code=exc.code,
            message="只能从源 Assessment 的当前资料清单创建另一标段研判",
            retryable=False,
            details={
                "provided_manifest_id": exc.provided_manifest_id,
                "current_manifest_id": exc.current_manifest_id,
            },
            recovery={
                "action": "get_current_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidAssessmentCloneCandidatesNotReady as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="源资料的标段候选尚未达到可复用状态",
            retryable=True,
            details={"generation_status": exc.status, "reason": exc.reason},
            recovery={
                "action": "wait_for_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidAssessmentCloneLotNotInManifest as exc:
        db.rollback()
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code=exc.code,
            message="标段候选不属于源资料清单",
            retryable=False,
            details={"lot_id": exc.lot_id, "manifest_id": exc.manifest_id},
            recovery={
                "action": "get_current_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidAssessmentCloneSameLot as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="不能为源 Assessment 已绑定的同一标段重复创建研判",
            retryable=False,
            details={
                "source_lot_id": exc.source_lot_id,
                "requested_lot_id": exc.requested_lot_id,
                "reason": "same_lot_not_allowed",
            },
            recovery={
                "action": "get_current_lot_candidates",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/lots",
            },
        )
    except BidAssessmentCloneStateConflict as exc:
        db.rollback()
        recovery = {"action": "get_latest_assessment_snapshot"}
        if exc.reason == "source_scope_required":
            recovery = {
                "action": "lot.select",
                "resource_url": (
                    f"/api/v1/bid-assessments/{assessment_id}/lot-selection"
                ),
            }
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="源 Assessment 当前状态不允许创建另一标段研判",
            retryable=False,
            details={
                "lifecycle_status": exc.lifecycle_status,
                "business_status": exc.business_status,
                "reason": exc.reason,
            },
            recovery=recovery,
        )
    except BidAssessmentCloneNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        logger.exception(
            "bid_assessment_clone_integrity_conflict",
            extra={"request_id": request_id, "assessment_id": assessment_id},
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_ASSESSMENT_STATE_CONFLICT",
            message="另一标段研判已由并发请求创建或源资料已变化",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_assessment_clone_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="另一标段研判暂时无法创建",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_resource_headers(
            response_body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments/{assessment_id}/runs",
    status_code=202,
    summary="基于当前冻结输入创建重新研判 Run",
    operation_id="createBidAnalysisRun",
)
async def create_bid_analysis_run_endpoint(
    assessment_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """API-40 manual restart; the workflow still chooses the execution target."""

    request_id = _request_id(request)
    if not (
        settings.feature_bid_assessment_v1_runtime
        and settings.feature_bid_assessment_phase3_run_bootstrap
    ):
        return _not_found_response(request_id)
    if not (
        1 <= len(assessment_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(assessment_id)
    ):
        return _not_found_response(request_id)
    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供 Assessment If-Match",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-03 返回的单个强 ETag",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidRunCreateIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Run 创建请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )
    try:
        if not _assessment_is_visible(
            db,
            assessment_id=assessment_id,
            current_user=current_user,
        ):
            return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_run_create_visibility_check_failed",
            extra={"request_id": request_id, "assessment_id": assessment_id},
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Assessment 暂时无法读取",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=RUN_CREATE_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "assessment_id": assessment_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: create_manual_run(
                command_db,
                assessment_id=assessment_id,
                expected_assessment_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同 Run 创建请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同的 Run 创建请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidRunVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="Assessment 已更新，请刷新后重试",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": f"/api/v1/bid-assessments/{exc.assessment_id}",
            },
            recovery={"action": "get_latest_assessment_snapshot"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidActiveRunExists as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Assessment 已有活跃 Run",
            retryable=False,
            details={"run_id": exc.run_id, "status": exc.status},
            recovery={
                "action": "run.view_progress",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/runs/{exc.run_id}",
            },
        )
    except BidRunAlreadyExistsForInput as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="完全相同的冻结输入已经存在 Run",
            retryable=False,
            details={"run_id": exc.run_id, "status": exc.status},
            recovery={
                "action": "run.view_progress",
                "resource_url": f"/api/v1/bid-assessments/{assessment_id}/runs/{exc.run_id}",
            },
        )
    except BidRunInputNotReady as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Run 冻结输入尚未准备完成",
            retryable=False,
            details={"reason_codes": list(exc.reasons)},
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidRunNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        logger.exception(
            "bid_run_create_integrity_conflict",
            extra={"request_id": request_id, "assessment_id": assessment_id},
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_ACTIVE_RUN_EXISTS",
            message="Run 已由并发请求创建",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_run_create_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Run 暂时无法创建",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_run_command_headers(
            response_body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.get(
    "/bid-assessments/{assessment_id}/runs/{run_id}",
    summary="获取 Run 进度快照",
    operation_id="getBidAnalysisRun",
)
def get_bid_analysis_run_endpoint(
    assessment_id: str,
    run_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """API-41 projection; never exposes the internal Task DAG."""

    request_id = _request_id(request)
    if not (
        settings.feature_bid_assessment_v1_runtime
        and settings.feature_bid_assessment_phase3_run_bootstrap
    ):
        return _not_found_response(request_id)
    if not all(
        1 <= len(value) <= 80 and _REQUEST_ID_PATTERN.fullmatch(value)
        for value in (assessment_id, run_id)
    ):
        return _not_found_response(request_id)
    try:
        run = (
            db.query(BidAnalysisRun)
            .join(BidAssessment, BidAssessment.id == BidAnalysisRun.assessment_id)
            .filter(
                BidAnalysisRun.id == run_id,
                BidAnalysisRun.assessment_id == assessment_id,
            )
        )
        if not has_admin_role(current_user):
            run = run.filter(BidAssessment.created_by == int(current_user.id))
        run_row = run.one_or_none()
        if run_row is None:
            return _not_found_response(request_id)
        snapshot = build_run_progress_snapshot(db, run_row)
        headers = run_snapshot_headers(run_row, snapshot)
        if _if_none_match_matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_run_snapshot_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
                "run_id": run_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Run 进度暂时无法读取",
            retryable=True,
            recovery={"action": "retry_snapshot_read"},
        )
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "ok",
            "data": snapshot,
            "error": None,
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments/{assessment_id}/runs/{run_id}/cancel",
    status_code=202,
    summary="请求安全取消 Run",
    operation_id="cancelBidAnalysisRun",
)
async def cancel_bid_analysis_run_endpoint(
    assessment_id: str,
    run_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """API-42 persists cancellation; lifecycle maintenance performs the fence."""

    request_id = _request_id(request)
    if not (
        settings.feature_bid_assessment_v1_runtime
        and settings.feature_bid_assessment_phase3_run_bootstrap
        and settings.feature_bid_assessment_phase3_run_lifecycle
    ):
        return _not_found_response(request_id)
    if not all(
        1 <= len(value) <= 80 and _REQUEST_ID_PATTERN.fullmatch(value)
        for value in (assessment_id, run_id)
    ):
        return _not_found_response(request_id)
    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供 Run If-Match",
            retryable=False,
            recovery={"action": "get_latest_run_snapshot"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-41 返回的单个强 ETag",
            retryable=False,
            recovery={"action": "get_latest_run_snapshot"},
        )
    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidRunCancelIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Run 取消请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )
    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=RUN_CANCEL_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "assessment_id": assessment_id,
                "run_id": run_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: request_run_cancellation(
                command_db,
                assessment_id=assessment_id,
                run_id=run_id,
                expected_run_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同 Run 取消请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同的 Run 取消请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidRunLifecycleVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="Run 已更新，请刷新后重试",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-assessments/{exc.assessment_id}/runs/{exc.run_id}"
                ),
            },
            recovery={"action": "get_latest_run_snapshot"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidRunNotCancellable as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Run 当前状态不允许取消",
            retryable=False,
            details={
                "run_id": exc.run_id,
                "status": exc.status,
                "retryable": exc.retryable,
            },
            recovery={"action": "get_latest_run_snapshot"},
        )
    except BidRunLifecycleNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_run_cancel_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
                "run_id": run_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Run 取消请求暂时无法处理",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_run_command_headers(
            response_body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments/{assessment_id}/runs/{run_id}/retry",
    status_code=202,
    summary="从最近 Checkpoint 重试 Run",
    operation_id="retryBidAnalysisRun",
)
async def retry_bid_analysis_run_endpoint(
    assessment_id: str,
    run_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """API-43 creates fenced Attempts in the same failed, retryable Run."""

    request_id = _request_id(request)
    if not (
        settings.feature_bid_assessment_v1_runtime
        and settings.feature_bid_assessment_phase3_run_bootstrap
        and settings.feature_bid_assessment_phase3_run_lifecycle
    ):
        return _not_found_response(request_id)
    if not all(
        1 <= len(value) <= 80 and _REQUEST_ID_PATTERN.fullmatch(value)
        for value in (assessment_id, run_id)
    ):
        return _not_found_response(request_id)
    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供 Run If-Match",
            retryable=False,
            recovery={"action": "get_latest_run_snapshot"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-41 返回的单个强 ETag",
            retryable=False,
            recovery={"action": "get_latest_run_snapshot"},
        )
    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidRunRetryIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Run 重试请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )
    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=RUN_RETRY_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "assessment_id": assessment_id,
                "run_id": run_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: retry_run_from_latest_checkpoint(
                command_db,
                assessment_id=assessment_id,
                run_id=run_id,
                expected_run_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同 Run 重试请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同的 Run 重试请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidRunLifecycleVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="Run 已更新，请刷新后重试",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-assessments/{exc.assessment_id}/runs/{exc.run_id}"
                ),
            },
            recovery={"action": "get_latest_run_snapshot"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidLifecycleRunInputStale as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Run 冻结输入已经过期，不能在原 Run 下重试",
            retryable=False,
            details={"run_id": exc.run_id, "reason_codes": list(exc.reasons)},
            recovery={"action": "run.create_from_latest_manifest"},
        )
    except BidRunNotRetryable as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="Run 不满足检查点重试条件",
            retryable=False,
            details={
                "run_id": exc.run_id,
                "status": exc.status,
                "retryable": exc.retryable,
                "reason": exc.reason,
            },
            recovery={"action": "get_latest_run_snapshot"},
        )
    except BidRunLifecycleNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        logger.exception(
            "bid_run_retry_integrity_conflict",
            extra={
                "request_id": request_id,
                "assessment_id": assessment_id,
                "run_id": run_id,
            },
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_RUN_NOT_RETRYABLE",
            message="Run 已由并发请求恢复",
            retryable=False,
            recovery={"action": "get_latest_run_snapshot"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_run_retry_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
                "run_id": run_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Run 重试请求暂时无法处理",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_run_command_headers(
            response_body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.get(
    "/bid-document-versions/{version_id}",
    summary="获取研判文件版本详情",
    operation_id="getBidDocumentVersion",
)
def get_bid_document_version_endpoint(
    version_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return an immutable, Manifest-authorized DocumentVersion projection."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _document_version_not_found_response(request_id)
    if not (
        1 <= len(version_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(version_id)
    ):
        return _document_version_not_found_response(request_id)

    try:
        visible = load_visible_bid_document_version(
            db,
            version_id=version_id,
            actor_id=int(current_user.id),
            actor_is_admin=has_admin_role(current_user),
        )
        detail = build_bid_document_version_detail(db, visible)
        headers = bid_document_version_headers(detail)
        if _if_none_match_matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)
    except BidDocumentVersionNotFound:
        db.rollback()
        return _document_version_not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_document_version_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "version_id": version_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="文件版本详情暂时无法读取",
            retryable=True,
            recovery={"action": "retry_document_version_read"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "ok",
            "data": detail,
            "error": None,
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.get(
    "/bid-document-versions/{version_id}/download",
    summary="受控下载研判文件原始版本",
    operation_id="downloadBidDocumentVersion",
)
def download_bid_document_version_endpoint(
    version_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-authorize and stream one exact object without exposing MinIO identity."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _document_version_not_found_response(request_id)
    if not (
        1 <= len(version_id) <= 80
        and _REQUEST_ID_PATTERN.fullmatch(version_id)
    ):
        return _document_version_not_found_response(request_id)

    try:
        visible = load_visible_bid_document_version(
            db,
            version_id=version_id,
            actor_id=int(current_user.id),
            actor_is_admin=has_admin_role(current_user),
        )
    except BidDocumentVersionNotFound:
        db.rollback()
        return _document_version_not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_document_download_authorization_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "version_id": version_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="文件下载授权暂时无法确认",
            retryable=True,
            recovery={"action": "retry_document_download"},
        )

    file_object = visible.file_object
    if str(file_object.storage_status) != "available":
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="原文件暂时不可下载",
            retryable=True,
            recovery={"action": "retry_document_download"},
        )

    try:
        storage = get_bid_upload_object_storage()
        stream = storage.open_read(object_key=str(file_object.object_key))
    except Exception:
        # Some S3 clients include the object key in exception text. Keep this
        # public download log deliberately key-free as required by the frozen
        # storage-disclosure boundary.
        logger.warning(
            "bid_document_download_open_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "version_id": version_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="原文件暂时无法读取",
            retryable=True,
            recovery={"action": "retry_document_download"},
        )

    mime_type = safe_download_mime_type(str(file_object.mime_type))
    headers = {
        "Content-Disposition": safe_content_disposition(
            str(visible.version.original_filename)
        ),
        "Content-Type": mime_type,
        "Content-Length": str(int(file_object.size_bytes)),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Vary": "Authorization",
        "Accept-Ranges": "none",
        "Content-Security-Policy": "sandbox",
    }
    return StreamingResponse(
        iter_bid_download(
            stream,
            chunk_size=int(settings.bid_upload_read_chunk_bytes),
        ),
        status_code=200,
        headers=headers,
    )


@router.get(
    "/bid-upload-batches/{batch_id}",
    summary="查询研判资料上传批次",
    operation_id="getBidUploadBatch",
)
def get_upload_batch_endpoint(
    batch_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the authoritative upload snapshot for recovery and reconciliation."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _upload_batch_not_found_response(request_id)
    if not 1 <= len(batch_id) <= 80:
        return _upload_batch_not_found_response(request_id)

    try:
        batch = _visible_upload_batch(
            db,
            batch_id=batch_id,
            current_user=current_user,
        )
        if batch is None:
            return _upload_batch_not_found_response(request_id)

        headers = _upload_batch_snapshot_headers(batch)
        if _if_none_match_matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)

        snapshot = build_upload_batch_snapshot(db, batch)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_batch_snapshot_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "batch_id": batch_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传批次快照暂时无法读取",
            retryable=True,
            recovery={"action": "retry_snapshot_read"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "ok",
            "data": snapshot,
            "error": None,
            "request_id": request_id,
        },
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-upload-batches/{batch_id}/files",
    status_code=201,
    summary="流式上传单个研判资料文件",
    operation_id="uploadBidBatchFile",
)
async def upload_batch_file_endpoint(
    batch_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_content_sha256: str | None = Header(
        default=None,
        alias="X-Content-SHA256",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream, validate, store, and atomically register one batch file."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _upload_batch_not_found_response(request_id)
    if not 1 <= len(batch_id) <= 80:
        return _upload_batch_not_found_response(request_id)
    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )

    try:
        visible_batch = _visible_upload_batch(
            db,
            batch_id=batch_id,
            current_user=current_user,
        )
        if visible_batch is None:
            return _upload_batch_not_found_response(request_id)
        if str(visible_batch.status) not in {"draft", "uploading", "ready"}:
            return _upload_file_error_response(
                BidUploadFileBatchStateConflict(status=str(visible_batch.status)),
                request_id=request_id,
            )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_file_preflight_read_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "batch_id": batch_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传批次暂时无法读取",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )
    finally:
        db.rollback()

    content_type = str(request.headers.get("content-type") or "").lower()
    if not content_type.startswith("multipart/form-data"):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="API-12 只接受 multipart/form-data",
            retryable=False,
            recovery={"action": "fix_multipart_request"},
        )
    try:
        form = await request.form(
            max_files=1,
            max_fields=4,
            max_part_size=1024 * 1024,
        )
    except Exception:
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="multipart 请求无法解析",
            retryable=False,
            recovery={"action": "fix_multipart_request"},
        )

    allowed_parts = {
        "file",
        "client_file_id",
        "operation",
        "replace_document_id",
        "relative_path",
    }
    unknown_parts = sorted(set(form.keys()) - allowed_parts)
    repeated_parts = sorted(
        name for name in allowed_parts if len(form.getlist(name)) > 1
    )
    upload_file = form.get("file")
    if unknown_parts or repeated_parts or not isinstance(upload_file, StarletteUploadFile):
        field_errors: list[dict[str, Any]] = []
        field_errors.extend(
            {
                "field": name,
                "type": "extra_forbidden",
                "message": "Extra inputs are not permitted",
            }
            for name in unknown_parts
        )
        field_errors.extend(
            {
                "field": name,
                "type": "multiple_values",
                "message": "Only one value is permitted",
            }
            for name in repeated_parts
        )
        if not isinstance(upload_file, StarletteUploadFile):
            field_errors.append(
                {"field": "file", "type": "missing", "message": "Field required"}
            )
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="上传文件表单字段校验失败",
            retryable=False,
            field_errors=field_errors,
            recovery={"action": "fix_request_fields"},
        )

    raw_metadata = {
        "client_file_id": form.get("client_file_id"),
        "operation": form.get("operation"),
        "replace_document_id": form.get("replace_document_id") or None,
        "relative_path": form.get("relative_path") or None,
    }
    try:
        metadata = BidUploadFileCreateIn.model_validate(raw_metadata)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="上传文件表单字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )

    try:
        inspection = await inspect_bid_upload(
            upload_file,
            expected_sha256=expected_content_sha256,
        )
    except BidUploadFileError as exc:
        return _upload_file_error_response(exc, request_id=request_id)
    except Exception:
        logger.exception(
            "bid_upload_file_inspection_failed",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        return _upload_file_error_response(
            BidUploadFileContentInvalid(reason="inspection_failed"),
            request_id=request_id,
        )

    normalized_metadata = metadata.model_dump(mode="json")
    request_payload = {
        "batch_id": batch_id,
        "file": {
            "filename": inspection.filename,
            "declared_mime_type": inspection.declared_mime_type,
            "size_bytes": inspection.size_bytes,
            "sha256": inspection.sha256,
        },
        **normalized_metadata,
    }
    try:
        decision = begin_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_UPLOAD_FILE_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
            request_id=request_id,
            processing_timeout_seconds=max(
                60,
                int(settings.bid_upload_processing_timeout_seconds),
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同文件上传仍在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同文件上传",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_file_idempotency_reservation_failed",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="文件上传幂等状态暂时无法保存",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    if decision.replayed:
        replay_status = int(decision.response_status_code)
        replay_body = dict(decision.response_body or {})
        replay_headers = {
            "Idempotent-Replay": "true",
            "Cache-Control": "private, no-store",
        }
        if replay_status == 201:
            replay_headers = _upload_file_headers(replay_body, replayed=True)
        return JSONResponse(
            status_code=replay_status,
            content=replay_body,
            headers=replay_headers,
            media_type="application/json; charset=utf-8",
        )

    record_id = decision.record_id
    try:
        current_batch = _visible_upload_batch(
            db,
            batch_id=batch_id,
            current_user=current_user,
        )
        if current_batch is None:
            raise BidUploadFileResourceNotFound()
        if str(current_batch.status) not in {"draft", "uploading", "ready"}:
            raise BidUploadFileBatchStateConflict(status=str(current_batch.status))
        existing_client = (
            db.query(BidUploadBatchFile.id)
            .filter(
                BidUploadBatchFile.batch_id == batch_id,
                BidUploadBatchFile.client_file_id == metadata.client_file_id,
            )
            .first()
        )
        if existing_client is None:
            limits = current_upload_limits()
            current_count = (
                db.query(func.count(BidUploadBatchFile.id))
                .filter(BidUploadBatchFile.batch_id == batch_id)
                .scalar()
                or 0
            )
            current_bytes = (
                db.query(func.coalesce(func.sum(BidUploadBatchFile.size_bytes), 0))
                .filter(BidUploadBatchFile.batch_id == batch_id)
                .scalar()
                or 0
            )
            if int(current_count) + 1 > int(limits["max_files"]):
                raise BidUploadBatchTooLarge(
                    reason="max_files",
                    limit=int(limits["max_files"]),
                    observed=int(current_count) + 1,
                )
            if int(current_bytes) + inspection.size_bytes > int(
                limits["max_batch_bytes"]
            ):
                raise BidUploadBatchTooLarge(
                    reason="max_batch_bytes",
                    limit=int(limits["max_batch_bytes"]),
                    observed=int(current_bytes) + inspection.size_bytes,
                )
        db.rollback()
    except BidUploadFileError as exc:
        db.rollback()
        error_response = _upload_file_error_response(exc, request_id=request_id)
        try:
            _complete_upload_error_idempotency(
                db,
                record_id=record_id,
                response=error_response,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "bid_upload_file_pre_storage_rejection_persist_failed",
                extra={"request_id": request_id, "batch_id": batch_id},
            )
        return error_response
    except Exception:
        db.rollback()
        _fail_upload_idempotency(
            db,
            record_id=record_id,
            failure_code="BID_STORAGE_UNAVAILABLE",
        )
        logger.exception(
            "bid_upload_file_pre_storage_check_failed",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传批次限额暂时无法核对",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    batch_file_id = str(uuid.uuid4())
    storage = None
    stored_object = None
    try:
        existing_content = (
            db.query(BidFileObject.id)
            .filter(
                BidFileObject.sha256 == inspection.sha256,
                BidFileObject.size_bytes == inspection.size_bytes,
                BidFileObject.storage_status == "available",
            )
            .first()
        )
        db.rollback()
        if existing_content is None:
            storage = get_bid_upload_object_storage()
            object_key = build_temporary_object_key(
                batch_id=batch_id,
                batch_file_id=batch_file_id,
            )
            stored_object = await asyncio.to_thread(
                storage.put,
                stream=upload_file.file,
                object_key=object_key,
                size_bytes=inspection.size_bytes,
                mime_type=inspection.canonical_mime_type,
            )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_file_object_put_failed",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        _fail_upload_idempotency(
            db,
            record_id=record_id,
            failure_code="BID_STORAGE_UNAVAILABLE",
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="对象存储暂时无法接收文件",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    registration = None
    try:
        registration = register_bid_upload_file(
            db,
            batch_id=batch_id,
            batch_file_id=batch_file_id,
            actor_id=int(current_user.id),
            actor_ref=str(current_user.username),
            actor_is_admin=has_admin_role(current_user),
            request_id=request_id,
            inspection=inspection,
            stored_object=stored_object,
            **normalized_metadata,
        )
        complete_idempotent_request(
            db,
            record_id=record_id,
            response_status_code=registration.command.status_code,
            response_body=registration.command.body,
            resource_type=registration.command.resource_type,
            resource_id=registration.command.resource_id,
            response_ref=registration.command.response_ref,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        await _compensate_upload_object(
            storage=storage,
            object_key=(stored_object.object_key if stored_object else None),
            request_id=request_id,
        )
        stored_object = None
        try:
            registration = register_bid_upload_file(
                db,
                batch_id=batch_id,
                batch_file_id=batch_file_id,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                inspection=inspection,
                stored_object=None,
                **normalized_metadata,
            )
            complete_idempotent_request(
                db,
                record_id=record_id,
                response_status_code=registration.command.status_code,
                response_body=registration.command.body,
                resource_type=registration.command.resource_type,
                resource_id=registration.command.resource_id,
                response_ref=registration.command.response_ref,
            )
            db.commit()
        except BidUploadFileError as exc:
            db.rollback()
            error_response = _upload_file_error_response(exc, request_id=request_id)
            try:
                _complete_upload_error_idempotency(
                    db,
                    record_id=record_id,
                    response=error_response,
                )
            except Exception:
                db.rollback()
                _fail_upload_idempotency(
                    db,
                    record_id=record_id,
                    failure_code="BID_STORAGE_UNAVAILABLE",
                )
            return error_response
        except Exception:
            db.rollback()
            _fail_upload_idempotency(
                db,
                record_id=record_id,
                failure_code="BID_STORAGE_UNAVAILABLE",
            )
            logger.exception(
                "bid_upload_file_concurrent_recovery_failed",
                extra={"request_id": request_id, "batch_id": batch_id},
            )
            return _error_response(
                status_code=503,
                request_id=request_id,
                error_code="BID_STORAGE_UNAVAILABLE",
                message="并发上传恢复失败，请使用相同请求重试",
                retryable=True,
                recovery={"action": "retry_same_request"},
            )
    except BidUploadFileError as exc:
        db.rollback()
        await _compensate_upload_object(
            storage=storage,
            object_key=(stored_object.object_key if stored_object else None),
            request_id=request_id,
        )
        error_response = _upload_file_error_response(exc, request_id=request_id)
        if isinstance(exc, BidUploadFileStorageUnavailable):
            _fail_upload_idempotency(
                db,
                record_id=record_id,
                failure_code=exc.code,
            )
        else:
            try:
                _complete_upload_error_idempotency(
                    db,
                    record_id=record_id,
                    response=error_response,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "bid_upload_file_error_replay_persist_failed",
                    extra={"request_id": request_id, "batch_id": batch_id},
                )
        return error_response
    except Exception:
        db.rollback()
        await _compensate_upload_object(
            storage=storage,
            object_key=(stored_object.object_key if stored_object else None),
            request_id=request_id,
        )
        _fail_upload_idempotency(
            db,
            record_id=record_id,
            failure_code="BID_STORAGE_UNAVAILABLE",
        )
        logger.exception(
            "bid_upload_file_registration_failed",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传文件暂时无法登记",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    if registration is None:
        raise RuntimeError("upload registration unexpectedly missing")
    if stored_object is not None and not registration.object_consumed:
        await _compensate_upload_object(
            storage=storage,
            object_key=stored_object.object_key,
            request_id=request_id,
        )
    body = dict(registration.command.body)
    return JSONResponse(
        status_code=201,
        content=body,
        headers=_upload_file_headers(
            body,
            replayed=registration.replayed_existing_file,
        ),
        media_type="application/json; charset=utf-8",
    )


@router.delete(
    "/bid-upload-batches/{batch_id}/files/{file_id}",
    status_code=204,
    summary="移除未提交上传批次中的草稿文件",
    operation_id="deleteBidBatchFile",
)
async def delete_upload_batch_file_endpoint(
    batch_id: str,
    file_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Logically remove a BatchFile, then delete an unreferenced object."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _upload_batch_not_found_response(request_id)
    if not 1 <= len(batch_id) <= 80 or not 1 <= len(file_id) <= 80:
        return _upload_batch_not_found_response(request_id)

    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供 BatchFile If-Match",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-11/API-12 返回的单个强文件 ETag",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )

    removal: BidUploadFileRemoval | None = None

    def _remove(command_db: Session):
        nonlocal removal
        removal = remove_bid_upload_batch_file(
            command_db,
            batch_id=batch_id,
            file_id=file_id,
            expected_file_etag=provided_etag,
            actor_id=int(current_user.id),
            actor_ref=str(current_user.username),
            actor_is_admin=has_admin_role(current_user),
            request_id=request_id,
        )
        return removal.command

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="DELETE",
            route_template=_UPLOAD_FILE_ITEM_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "batch_id": batch_id,
                "file_id": file_id,
                "if_match": provided_etag,
            },
            request_id=request_id,
            handler=_remove,
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同文件移除请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同文件移除请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidUploadFileRemovalVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="BatchFile 已更新，请按最新批次快照重试",
            retryable=False,
            details={
                "batch_file_id": exc.file_id,
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-upload-batches/{exc.batch_id}"
                ),
            },
            recovery={"action": "get_latest_upload_batch"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidUploadFileRemovalBatchCommitted as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已进入提交阶段，不能移除草稿文件",
            retryable=False,
            details={"batch_status": exc.status},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadFileRemovalBatchStateConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次当前状态不允许移除文件",
            retryable=False,
            details={"batch_status": exc.status},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadFileRemovalNotFound:
        db.rollback()
        return _upload_batch_not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_file_remove_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "batch_id": batch_id,
                "file_id": file_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="草稿文件暂时无法移除",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    if removal is not None and removal.cleanup_object_key:
        await _delete_unreferenced_upload_object_after_commit(
            object_key=removal.cleanup_object_key,
            request_id=request_id,
        )
    receipt = dict(execution.body or {})
    return Response(
        status_code=204,
        headers=_removed_upload_file_headers(
            receipt,
            replayed=bool(execution.replayed),
        ),
    )


@router.post(
    "/bid-upload-batches/{batch_id}/deactivations",
    status_code=201,
    summary="登记下一 Manifest 的基线文档停用操作",
    operation_id="addBidDocumentDeactivation",
)
async def add_upload_batch_deactivations_endpoint(
    batch_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register logical removals from a change batch's base Manifest."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _upload_batch_not_found_response(request_id)
    if not 1 <= len(batch_id) <= 80:
        return _upload_batch_not_found_response(request_id)

    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供上传批次 If-Match",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-11 返回的单个强批次 ETag",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidUploadBatchDeactivationCreateIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="停用操作请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )

    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_UPLOAD_DEACTIVATION_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "batch_id": batch_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: add_bid_upload_batch_deactivations(
                command_db,
                batch_id=batch_id,
                expected_batch_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同停用操作正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同停用请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidUploadBatchDeactivationVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已更新，请按最新快照重新确认停用操作",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-upload-batches/{exc.batch_id}"
                ),
            },
            recovery={"action": "get_latest_upload_batch"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidUploadBatchDeactivationBatchCommitted as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已进入提交阶段，不能再登记停用操作",
            retryable=False,
            details={"batch_status": exc.status},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchDeactivationStateConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次当前状态不允许登记停用操作",
            retryable=False,
            details={"batch_status": exc.status},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchDeactivationNotAllowed as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="只有 change 上传批次可以登记基线文档停用",
            retryable=False,
            details={"batch_purpose": exc.purpose},
            recovery={"action": "create_change_upload_batch"},
        )
    except BidUploadBatchDeactivationBaselineStale as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次的基础 Manifest 已不是当前资料基线",
            retryable=False,
            details={
                "base_manifest_id": exc.base_manifest_id,
                "current_manifest_id": exc.current_manifest_id,
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidUploadBatchDeactivationTargetInvalid as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="停用目标不属于该批次的基础 Manifest",
            retryable=False,
            details={"invalid_document_ids": exc.invalid_document_ids},
            recovery={"action": "review_base_manifest_documents"},
        )
    except BidUploadBatchDeactivationConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="文档已使用不同原因登记停用",
            retryable=False,
            details={
                "document_id": exc.document_id,
                "existing_reason": exc.existing_reason,
            },
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchDeactivationNotFound:
        db.rollback()
        return _upload_batch_not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_UPLOAD_DEACTIVATION_CONFLICT",
            message="停用操作发生并发唯一性冲突，请刷新批次后重试",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_batch_deactivation_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "batch_id": batch_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="停用操作暂时无法保存",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    return JSONResponse(
        status_code=execution.status_code,
        content=execution.body,
        headers=_upload_batch_headers(
            execution.body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-upload-batches/{batch_id}/commit",
    status_code=202,
    summary="提交上传批次并生成不可变 Manifest",
    operation_id="commitBidUploadBatch",
)
async def commit_upload_batch_endpoint(
    batch_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Atomically commit batch operations, Manifest, events, audit, and replay."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _upload_batch_not_found_response(request_id)
    if not 1 <= len(batch_id) <= 80:
        return _upload_batch_not_found_response(request_id)
    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供上传批次 If-Match",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-11 返回的单个强批次 ETag",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidUploadBatchCommitIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="上传批次提交确认字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )
    normalized_payload = payload.model_dump(mode="json")

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_UPLOAD_COMMIT_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "batch_id": batch_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: commit_bid_upload_batch(
                command_db,
                batch_id=batch_id,
                expected_batch_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同批次提交请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同批次提交请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidUploadBatchCommitVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已更新，请按最新批次快照重新确认提交",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": f"/api/v1/bid-upload-batches/{exc.batch_id}",
            },
            recovery={"action": "get_latest_upload_batch"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidUploadBatchAlreadyCommitted as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已经提交，只有原幂等键可以重放原响应",
            retryable=False,
            details={
                "batch_status": exc.status,
                "committed_manifest_id": exc.committed_manifest_id,
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidUploadBatchExpectedCountMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="当前有效文件数与提交确认值不一致",
            retryable=False,
            details={"expected_file_count": exc.expected, "actual_file_count": exc.actual},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchExpectedDeactivationCountMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="当前停用操作数与提交确认值不一致",
            retryable=False,
            details={
                "expected_deactivation_count": exc.expected,
                "actual_deactivation_count": exc.actual,
            },
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchCommitBaselineStale as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次的基础 Manifest 已不是当前资料版本",
            retryable=False,
            details={
                "base_manifest_id": exc.base_manifest_id,
                "current_manifest_id": exc.current_manifest_id,
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidUploadBatchMergeConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="批次中的新增、替换和停用操作无法合并",
            retryable=False,
            details={"reasons": exc.reasons},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchNotReady as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次尚不能提交",
            retryable=False,
            details={
                "batch_status": exc.status,
                "blocking_errors": exc.blocking_errors,
            },
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchCommitNotFound:
        db.rollback()
        return _upload_batch_not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        logger.exception(
            "bid_upload_batch_commit_integrity_conflict",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_UPLOAD_BATCH_MERGE_CONFLICT",
            message="批次提交发生并发或不可变版本冲突，请刷新后重试",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_batch_commit_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "batch_id": batch_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传批次暂时无法提交",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    return JSONResponse(
        status_code=execution.status_code,
        content=execution.body,
        headers=_upload_commit_headers(
            execution.body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-upload-batches/{batch_id}/abandon",
    status_code=200,
    summary="放弃未提交上传批次",
    operation_id="abandonBidUploadBatch",
)
async def abandon_upload_batch_endpoint(
    batch_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Terminalize a draft batch; cleanup stays deferred and reference-aware."""

    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _upload_batch_not_found_response(request_id)
    if not 1 <= len(batch_id) <= 80:
        return _upload_batch_not_found_response(request_id)
    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )
    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供上传批次 If-Match",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-11 返回的单个强批次 ETag",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidUploadBatchAbandonIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="放弃批次原因校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )
    normalized_payload = payload.model_dump(mode="json")

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_UPLOAD_ABANDON_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload={
                "batch_id": batch_id,
                "if_match": provided_etag,
                "body": normalized_payload,
            },
            request_id=request_id,
            handler=lambda command_db: abandon_bid_upload_batch(
                command_db,
                batch_id=batch_id,
                expected_batch_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                actor_is_admin=has_admin_role(current_user),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同批次放弃请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同批次放弃请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidUploadBatchAbandonmentVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已更新，请按最新批次快照重新确认放弃",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": f"/api/v1/bid-upload-batches/{exc.batch_id}",
            },
            recovery={"action": "get_latest_upload_batch"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidUploadBatchAbandonmentAlreadyCommitted as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="已提交批次不能通过放弃操作撤销 Manifest",
            retryable=False,
            details={
                "batch_status": exc.status,
                "committed_manifest_id": exc.committed_manifest_id,
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidUploadBatchAlreadyAbandoned as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次已经放弃，只有原幂等键可以重放原响应",
            retryable=False,
            details={
                "abandoned_at": (
                    exc.abandoned_at.isoformat() if exc.abandoned_at else None
                )
            },
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchAbandonmentStateConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code=exc.code,
            message="上传批次当前状态不允许主动放弃",
            retryable=False,
            details={"batch_status": exc.status, "expired": exc.expired},
            recovery={"action": "get_latest_upload_batch"},
        )
    except BidUploadBatchAbandonmentNotFound:
        db.rollback()
        return _upload_batch_not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        logger.exception(
            "bid_upload_batch_abandon_integrity_conflict",
            extra={"request_id": request_id, "batch_id": batch_id},
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_UPLOAD_BATCH_NOT_READY",
            message="批次放弃发生并发状态冲突，请刷新后重试",
            retryable=False,
            recovery={"action": "get_latest_upload_batch"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_batch_abandon_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "batch_id": batch_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传批次暂时无法放弃",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    return JSONResponse(
        status_code=execution.status_code,
        content=execution.body,
        headers=_upload_batch_headers(
            execution.body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments/{assessment_id}/upload-batches",
    status_code=201,
    summary="创建研判资料上传批次",
    operation_id="createBidUploadBatch",
)
async def create_upload_batch_endpoint(
    assessment_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        return _not_found_response(request_id)
    if not 1 <= len(assessment_id) <= 80:
        return _not_found_response(request_id)

    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )

    if if_match is None:
        return _error_response(
            status_code=428,
            request_id=request_id,
            error_code="BID_PRECONDITION_REQUIRED",
            message="必须提供 Assessment If-Match",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    provided_etag = if_match.strip()
    if not _STRONG_ETAG_PATTERN.fullmatch(provided_etag):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="If-Match 必须是 API-03 返回的单个强 ETag",
            retryable=False,
            recovery={"action": "get_latest_assessment_snapshot"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidUploadBatchCreateIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="上传批次请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )

    try:
        if not _assessment_is_visible(
            db,
            assessment_id=assessment_id,
            current_user=current_user,
        ):
            return _not_found_response(request_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_batch_visibility_check_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Assessment 暂时无法读取",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    normalized_payload = payload.model_dump(mode="json")
    idempotency_payload = {
        "assessment_id": assessment_id,
        "if_match": provided_etag,
        "body": normalized_payload,
    }
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_UPLOAD_BATCH_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload=idempotency_payload,
            request_id=request_id,
            handler=lambda command_db: create_bid_upload_batch(
                command_db,
                assessment_id=assessment_id,
                expected_assessment_etag=provided_etag,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同上传批次请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidUploadBatchVersionMismatch as exc:
        db.rollback()
        return _error_response(
            status_code=412,
            request_id=request_id,
            error_code="BID_RESOURCE_VERSION_MISMATCH",
            message="Assessment 已更新，请刷新后重试",
            retryable=False,
            details={
                "provided_etag": exc.provided_etag,
                "current_etag": exc.current_etag,
                "current_resource_url": (
                    f"/api/v1/bid-assessments/{exc.assessment_id}"
                ),
            },
            recovery={"action": "get_latest_assessment_snapshot"},
            headers={
                "ETag": exc.current_etag,
                "X-Resource-Version": str(exc.current_row_version),
                "Cache-Control": "private, no-store",
            },
        )
    except BidUploadBatchAlreadyOpen as exc:
        db.rollback()
        resource_url = f"/api/v1/bid-upload-batches/{exc.batch_id}"
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_UPLOAD_BATCH_ALREADY_OPEN",
            message="已有可恢复的开放上传批次",
            retryable=False,
            details={
                "batch_id": exc.batch_id,
                "status": exc.status,
                "resource_url": resource_url,
            },
            recovery={"action": "resume_upload_batch", "resource_url": resource_url},
        )
    except BidUploadBatchBaselineStale as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_BASE_MANIFEST_STALE",
            message="资料基线已变化，请刷新后重新创建批次",
            retryable=False,
            details={
                "provided_manifest_id": exc.provided_manifest_id,
                "current_manifest_id": exc.current_manifest_id,
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidUploadBatchStateConflict as exc:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_ASSESSMENT_STATE_CONFLICT",
            message="Assessment 当前状态不允许创建该上传批次",
            retryable=False,
            details={
                "lifecycle_status": exc.lifecycle_status,
                "business_status": exc.business_status,
            },
            recovery={"action": "get_latest_assessment_snapshot"},
        )
    except BidUploadBatchAssessmentNotFound:
        db.rollback()
        return _not_found_response(request_id)
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(BidUploadBatch)
            .filter(
                BidUploadBatch.assessment_id == assessment_id,
                BidUploadBatch.status.in_(OPEN_BATCH_STATUSES),
                BidUploadBatch.open_slot_key.is_not(None),
            )
            .order_by(BidUploadBatch.created_at.desc(), BidUploadBatch.id.desc())
            .first()
        )
        if existing is not None:
            resource_url = f"/api/v1/bid-upload-batches/{existing.id}"
            return _error_response(
                status_code=409,
                request_id=request_id,
                error_code="BID_UPLOAD_BATCH_ALREADY_OPEN",
                message="已有可恢复的开放上传批次",
                retryable=False,
                details={
                    "batch_id": str(existing.id),
                    "status": str(existing.status),
                    "resource_url": resource_url,
                },
                recovery={
                    "action": "resume_upload_batch",
                    "resource_url": resource_url,
                },
            )
        logger.exception(
            "bid_upload_batch_integrity_conflict",
            extra={"request_id": request_id, "assessment_id": assessment_id},
        )
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_ASSESSMENT_STATE_CONFLICT",
            message="上传批次创建发生唯一性冲突",
            retryable=False,
            recovery={"action": "refresh_and_retry_with_new_key"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_upload_batch_create_transaction_failed",
            extra={
                "request_id": request_id,
                "actor_id": int(current_user.id),
                "assessment_id": assessment_id,
            },
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="上传批次暂时无法创建",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_upload_batch_headers(
            response_body,
            replayed=bool(execution.replayed),
        ),
        media_type="application/json; charset=utf-8",
    )


@router.post(
    "/bid-assessments",
    status_code=201,
    summary="创建研判 Assessment",
    operation_id="createBidAssessment",
)
async def create_assessment_endpoint(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_id = _request_id(request)
    if not settings.feature_bid_assessment_v1_runtime:
        raise HTTPException(status_code=404, detail="BID_RESOURCE_NOT_FOUND")

    if idempotency_key is None:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="缺少 Idempotency-Key",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "missing",
                    "message": "Field required",
                }
            ],
            recovery={"action": "supply_idempotency_key"},
        )
    try:
        validate_idempotency_key(idempotency_key)
    except BidIdempotencyError:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="Idempotency-Key 格式无效",
            retryable=False,
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "type": "value_error",
                    "message": "长度必须为 16–128 个可打印 ASCII 字符",
                }
            ],
            recovery={"action": "replace_idempotency_key"},
        )

    try:
        raw_payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error_response(
            status_code=400,
            request_id=request_id,
            error_code="BID_REQUEST_MALFORMED",
            message="请求体不是有效 JSON",
            retryable=False,
            recovery={"action": "fix_request_json"},
        )
    try:
        payload = BidAssessmentCreateIn.model_validate(raw_payload)
    except ValidationError as exc:
        return _error_response(
            status_code=422,
            request_id=request_id,
            error_code="BID_REQUEST_VALIDATION_FAILED",
            message="请求字段校验失败",
            retryable=False,
            field_errors=_field_errors(exc),
            recovery={"action": "fix_request_fields"},
        )

    normalized_payload = payload.model_dump(mode="json")
    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_ROUTE_TEMPLATE,
            idempotency_key=idempotency_key,
            request_payload=normalized_payload,
            request_id=request_id,
            handler=lambda command_db: create_bid_assessment(
                command_db,
                actor_id=int(current_user.id),
                actor_ref=str(current_user.username),
                request_id=request_id,
                **normalized_payload,
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_IN_PROGRESS",
            message="相同请求正在处理中",
            retryable=True,
            details={"retry_after_seconds": 2},
            recovery={"action": "retry_same_request"},
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_IDEMPOTENCY_KEY_REUSED",
            message="Idempotency-Key 已用于不同请求",
            retryable=False,
            recovery={"action": "use_new_idempotency_key"},
        )
    except BidAssessmentExternalRefConflict:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_ASSESSMENT_STATE_CONFLICT",
            message="external_ref 已关联其他 Assessment",
            retryable=False,
            details={"field": "external_ref"},
            recovery={"action": "change_external_ref"},
        )
    except IntegrityError:
        db.rollback()
        return _error_response(
            status_code=409,
            request_id=request_id,
            error_code="BID_ASSESSMENT_STATE_CONFLICT",
            message="Assessment 创建发生唯一性冲突",
            retryable=False,
            recovery={"action": "review_request_and_retry_with_new_key"},
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_assessment_create_transaction_failed",
            extra={"request_id": request_id, "actor_id": int(current_user.id)},
        )
        return _error_response(
            status_code=503,
            request_id=request_id,
            error_code="BID_STORAGE_UNAVAILABLE",
            message="Assessment 暂时无法创建",
            retryable=True,
            recovery={"action": "retry_same_request"},
        )

    response_body = dict(execution.body)
    return JSONResponse(
        status_code=int(execution.status_code),
        content=response_body,
        headers=_resource_headers(response_body, replayed=bool(execution.replayed)),
        media_type="application/json; charset=utf-8",
    )
