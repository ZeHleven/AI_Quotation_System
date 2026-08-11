"""Upload-batch snapshot and resource-version helpers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidDocumentVersion,
    BidManifestDocument,
    BidUploadBatch,
    BidUploadBatchDeactivation,
    BidUploadBatchFile,
)


_DEFAULT_EXTENSIONS = ("pdf", "docx", "xlsx", "xlsm", "png", "jpg", "jpeg", "txt", "md")


def _utc_rfc3339(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def upload_batch_etag(
    batch_id: str,
    row_version: int,
    *,
    limits: dict[str, Any] | None = None,
) -> str:
    limits_json = json.dumps(
        limits if limits is not None else current_upload_limits(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    limits_fingerprint = hashlib.sha256(limits_json.encode("utf-8")).hexdigest()[:12]
    return (
        f'"bid-upload-batch:{batch_id}:{int(row_version)}:'
        f'{limits_fingerprint}"'
    )


def accepted_upload_extensions() -> list[str]:
    values: list[str] = []
    for raw_value in settings.bid_upload_accepted_extensions:
        value = str(raw_value).strip().lower().lstrip(".")
        if value in _DEFAULT_EXTENSIONS and value not in values:
            values.append(value)
    return values or list(_DEFAULT_EXTENSIONS)


def current_upload_limits() -> dict[str, Any]:
    return {
        "max_files": max(1, int(settings.bid_upload_max_files)),
        "max_file_bytes": max(1, int(settings.bid_upload_max_file_bytes)),
        "max_batch_bytes": max(1, int(settings.bid_upload_max_batch_bytes)),
        "accepted_extensions": accepted_upload_extensions(),
    }


def upload_batch_file_etag(batch_file_id: str, row_version: int) -> str:
    return f'"bid-upload-file:{batch_file_id}:{int(row_version)}"'


def _batch_file_snapshot(row: BidUploadBatchFile) -> dict[str, Any]:
    return {
        "batch_file_id": str(row.id),
        "client_file_id": str(row.client_file_id),
        "filename": str(row.filename),
        "relative_path": row.relative_path,
        "operation": str(row.operation),
        "replace_document_id": (
            str(row.replace_document_id) if row.replace_document_id else None
        ),
        "size_bytes": int(row.size_bytes),
        "sha256": str(row.sha256),
        "mime_type": str(row.mime_type),
        "status": str(row.status),
        "error_code": row.error_code,
        "row_version": int(row.row_version),
        "etag": upload_batch_file_etag(str(row.id), int(row.row_version)),
    }


def derive_upload_batch_status(
    file_statuses: list[str],
    *,
    deactivation_count: int,
) -> str:
    """Derive the open-batch aggregate state from all pending operations."""

    normalized_statuses = {str(status) for status in file_statuses}
    if normalized_statuses and normalized_statuses != {"ready"}:
        return "uploading"
    if normalized_statuses == {"ready"} or int(deactivation_count) > 0:
        return "ready"
    return "draft"


def _validation_snapshot(
    db: Session,
    batch: BidUploadBatch,
    files: list[BidUploadBatchFile],
    deactivations: list[BidUploadBatchDeactivation],
) -> dict[str, Any]:
    blocking_errors: list[str] = []
    warnings: list[str] = []
    if str(batch.status) not in {"draft", "uploading", "ready"}:
        blocking_errors.append("上传批次当前状态不允许提交")
    if not files and not deactivations:
        blocking_errors.append(
            "尚未上传文件"
            if str(batch.purpose) == "initial"
            else "尚未添加文件或停用操作"
        )
    if any(str(row.status) in {"receiving", "inspecting"} for row in files):
        blocking_errors.append("仍有文件正在接收或检查")
    if any(str(row.status) in {"rejected", "failed"} for row in files):
        blocking_errors.append("存在校验失败的文件")
    ready_count = sum(1 for row in files if str(row.status) == "ready")
    if files and ready_count == 0 and not deactivations:
        blocking_errors.append("没有可提交的有效文件")

    replacement_targets = [
        str(row.replace_document_id)
        for row in files
        if str(row.operation) == "replace" and row.replace_document_id
    ]
    if len(replacement_targets) != len(set(replacement_targets)):
        blocking_errors.append("同一逻辑文档不能在一个批次中被替换多次")
    deactivation_targets = {str(row.document_id) for row in deactivations}
    if set(replacement_targets) & deactivation_targets:
        blocking_errors.append("同一逻辑文档不能同时替换和停用")

    if str(batch.purpose) == "initial":
        if batch.base_manifest_id is not None:
            blocking_errors.append("initial 批次不能绑定基础 Manifest")
        if replacement_targets or deactivations:
            blocking_errors.append("initial 批次只能包含新增文件")
    else:
        assessment = (
            db.query(BidAssessment)
            .filter(BidAssessment.id == batch.assessment_id)
            .one_or_none()
        )
        current_manifest_id = (
            str(assessment.current_manifest_id)
            if assessment is not None and assessment.current_manifest_id
            else None
        )
        base_manifest_id = str(batch.base_manifest_id) if batch.base_manifest_id else None
        if base_manifest_id is None or current_manifest_id != base_manifest_id:
            blocking_errors.append("基础 Manifest 已不是当前资料版本")
        baseline_rows = (
            db.query(
                BidDocumentVersion.document_id,
                BidDocumentVersion.file_object_id,
            )
            .join(
                BidManifestDocument,
                BidManifestDocument.document_version_id == BidDocumentVersion.id,
            )
            .filter(BidManifestDocument.manifest_id == base_manifest_id)
            .all()
            if base_manifest_id is not None
            else []
        )
        baseline_file_by_document = {
            str(document_id): str(file_object_id)
            for document_id, file_object_id in baseline_rows
        }
        baseline_document_ids = set(baseline_file_by_document)
        invalid_targets = (
            set(replacement_targets) | deactivation_targets
        ) - baseline_document_ids
        if invalid_targets:
            blocking_errors.append("替换或停用目标不属于基础 Manifest")
        if any(
            str(row.operation) == "replace"
            and row.replace_document_id
            and row.file_object_id
            and baseline_file_by_document.get(str(row.replace_document_id))
            == str(row.file_object_id)
            for row in files
        ):
            blocking_errors.append("替换文件与基础 Manifest 当前版本内容相同")
    blocking_errors = list(dict.fromkeys(blocking_errors))
    can_commit = str(batch.status) == "ready" and not blocking_errors
    return {
        "can_commit": can_commit,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
    }


def build_upload_batch_snapshot(db: Session, batch: BidUploadBatch) -> dict[str, Any]:
    """Build the frozen UploadBatchSnapshot from persisted state."""

    files = (
        db.query(BidUploadBatchFile)
        .filter(BidUploadBatchFile.batch_id == batch.id)
        .order_by(BidUploadBatchFile.created_at.asc(), BidUploadBatchFile.id.asc())
        .all()
    )
    deactivations = (
        db.query(BidUploadBatchDeactivation)
        .filter(BidUploadBatchDeactivation.batch_id == batch.id)
        .order_by(
            BidUploadBatchDeactivation.created_at.asc(),
            BidUploadBatchDeactivation.id.asc(),
        )
        .all()
    )
    return {
        "batch_id": str(batch.id),
        "assessment_id": str(batch.assessment_id),
        "purpose": str(batch.purpose),
        "status": str(batch.status),
        "base_manifest_id": str(batch.base_manifest_id) if batch.base_manifest_id else None,
        "abandon_reason": batch.abandon_reason,
        "abandoned_at": (
            _utc_rfc3339(batch.abandoned_at) if batch.abandoned_at else None
        ),
        "cleanup_after": (
            _utc_rfc3339(batch.cleanup_after) if batch.cleanup_after else None
        ),
        "cleanup_completed_at": (
            _utc_rfc3339(batch.cleanup_completed_at)
            if batch.cleanup_completed_at
            else None
        ),
        "row_version": int(batch.row_version),
        "limits": current_upload_limits(),
        "files": [_batch_file_snapshot(row) for row in files],
        "deactivations": sorted(str(row.document_id) for row in deactivations),
        "validation": _validation_snapshot(db, batch, files, deactivations),
        "expires_at": _utc_rfc3339(batch.expires_at),
    }
