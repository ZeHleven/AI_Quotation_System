"""API-15 atomic upload-batch commit and immutable Manifest construction."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidManifestDocument,
    BidUploadBatch,
    BidUploadBatchDeactivation,
    BidUploadBatchFile,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    canonical_hash,
)
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_assessment_snapshots import build_assessment_snapshot
from app.services.bid_upload_batch_snapshots import (
    build_upload_batch_snapshot,
    upload_batch_etag,
)


_STALEABLE_RUN_STATUSES = {
    "created",
    "planning",
    "queued",
    "running",
    "waiting_input",
    "waiting_operation",
    "validating",
}


class BidUploadBatchCommitError(RuntimeError):
    code = "BID_UPLOAD_BATCH_NOT_READY"


class BidUploadBatchCommitNotFound(BidUploadBatchCommitError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidUploadBatchCommitVersionMismatch(BidUploadBatchCommitError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(
        self,
        *,
        batch_id: str,
        provided_etag: str,
        current_etag: str,
        current_row_version: int,
    ) -> None:
        super().__init__(self.code)
        self.batch_id = batch_id
        self.provided_etag = provided_etag
        self.current_etag = current_etag
        self.current_row_version = int(current_row_version)


class BidUploadBatchAlreadyCommitted(BidUploadBatchCommitError):
    code = "BID_UPLOAD_BATCH_ALREADY_COMMITTED"

    def __init__(self, *, status: str, committed_manifest_id: str | None) -> None:
        super().__init__(self.code)
        self.status = status
        self.committed_manifest_id = committed_manifest_id


class BidUploadBatchNotReady(BidUploadBatchCommitError):
    code = "BID_UPLOAD_BATCH_NOT_READY"

    def __init__(self, *, status: str, blocking_errors: list[str]) -> None:
        super().__init__(self.code)
        self.status = status
        self.blocking_errors = list(blocking_errors)


class BidUploadBatchExpectedCountMismatch(BidUploadBatchCommitError):
    code = "BID_EXPECTED_FILE_COUNT_MISMATCH"

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(self.code)
        self.expected = int(expected)
        self.actual = int(actual)


class BidUploadBatchExpectedDeactivationCountMismatch(BidUploadBatchCommitError):
    code = "BID_EXPECTED_DEACTIVATION_COUNT_MISMATCH"

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(self.code)
        self.expected = int(expected)
        self.actual = int(actual)


class BidUploadBatchCommitBaselineStale(BidUploadBatchCommitError):
    code = "BID_BASE_MANIFEST_STALE"

    def __init__(
        self,
        *,
        base_manifest_id: str | None,
        current_manifest_id: str | None,
    ) -> None:
        super().__init__(self.code)
        self.base_manifest_id = base_manifest_id
        self.current_manifest_id = current_manifest_id


class BidUploadBatchMergeConflict(BidUploadBatchCommitError):
    code = "BID_UPLOAD_BATCH_MERGE_CONFLICT"

    def __init__(self, *, reasons: list[str]) -> None:
        super().__init__(self.code)
        self.reasons = list(reasons)


@dataclass(frozen=True)
class _BaselineMember:
    document_id: str
    document_version_id: str
    file_object_id: str
    role: str
    order_no: int


@dataclass(frozen=True)
class _ManifestMember:
    document_id: str
    document_version_id: str
    file_object_id: str
    role: str
    order_no: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_time(base: datetime, ordinal: int) -> datetime:
    return base + timedelta(microseconds=max(0, int(ordinal)))


def _parser_hint(filename: str) -> str | None:
    suffix = PurePosixPath(str(filename).replace("\\", "/")).suffix.lower().lstrip(".")
    return suffix[:64] or None


def _baseline_members(db: Session, manifest_id: str | None) -> list[_BaselineMember]:
    if manifest_id is None:
        return []
    rows = (
        db.query(BidManifestDocument, BidDocumentVersion)
        .join(
            BidDocumentVersion,
            BidDocumentVersion.id == BidManifestDocument.document_version_id,
        )
        .filter(BidManifestDocument.manifest_id == manifest_id)
        .order_by(
            BidManifestDocument.order_no.asc(),
            BidManifestDocument.document_version_id.asc(),
        )
        .all()
    )
    return [
        _BaselineMember(
            document_id=str(version.document_id),
            document_version_id=str(version.id),
            file_object_id=str(version.file_object_id),
            role=str(member.role),
            order_no=int(member.order_no),
        )
        for member, version in rows
    ]


def _merge_errors(
    *,
    purpose: str,
    files: list[BidUploadBatchFile],
    deactivations: list[BidUploadBatchDeactivation],
    baseline_members: list[_BaselineMember],
) -> list[str]:
    errors: list[str] = []
    baseline_by_document = {member.document_id: member for member in baseline_members}
    replacement_files = [row for row in files if str(row.operation) == "replace"]
    replacement_targets = [str(row.replace_document_id) for row in replacement_files]
    deactivation_targets = {str(row.document_id) for row in deactivations}

    if purpose == "initial":
        if replacement_files or deactivations or baseline_members:
            errors.append("initial 批次只能包含新增文件")
    else:
        invalid_targets = (
            set(replacement_targets) | deactivation_targets
        ) - set(baseline_by_document)
        if invalid_targets:
            errors.append("替换或停用目标不属于基础 Manifest")
    if len(replacement_targets) != len(set(replacement_targets)):
        errors.append("同一逻辑文档不能在一个批次中被替换多次")
    if set(replacement_targets) & deactivation_targets:
        errors.append("同一逻辑文档不能同时替换和停用")
    if any(
        row.replace_document_id
        and row.file_object_id
        and baseline_by_document.get(str(row.replace_document_id)) is not None
        and baseline_by_document[str(row.replace_document_id)].file_object_id
        == str(row.file_object_id)
        for row in replacement_files
    ):
        errors.append("替换文件与基础 Manifest 当前版本内容相同")

    return list(dict.fromkeys(errors))


def _new_document_version(
    db: Session,
    *,
    batch: BidUploadBatch,
    batch_file: BidUploadBatchFile,
    actor_id: int,
) -> tuple[BidDocument, BidDocumentVersion]:
    operation = str(batch_file.operation)
    source_metadata = {
        "source": "bid_upload_batch",
        "batch_id": str(batch.id),
        "batch_file_id": str(batch_file.id),
        "client_file_id": str(batch_file.client_file_id),
        "operation": operation,
        "replace_document_id": (
            str(batch_file.replace_document_id)
            if batch_file.replace_document_id
            else None
        ),
        "filename": str(batch_file.filename),
        "relative_path": batch_file.relative_path,
        "mime_type": str(batch_file.mime_type),
        "sha256": str(batch_file.sha256),
        "size_bytes": int(batch_file.size_bytes),
    }
    if operation == "add":
        document = BidDocument(
            id=str(uuid.uuid4()),
            logical_identity_key=f"upload:{batch.id}:{batch_file.id}"[:191],
            logical_name=str(batch_file.filename),
            document_type="uploaded_document",
            created_by=int(actor_id),
        )
        db.add(document)
        db.flush()
        version_no = 1
    else:
        document = (
            db.query(BidDocument)
            .filter(BidDocument.id == batch_file.replace_document_id)
            .with_for_update()
            .one()
        )
        version_no = int(
            db.query(func.max(BidDocumentVersion.version_no))
            .filter(BidDocumentVersion.document_id == document.id)
            .scalar()
            or 0
        ) + 1

    version = BidDocumentVersion(
        id=str(uuid.uuid4()),
        document_id=str(document.id),
        file_object_id=str(batch_file.file_object_id),
        version_no=version_no,
        original_filename=str(batch_file.filename),
        parser_hint=_parser_hint(str(batch_file.filename)),
        source_metadata_hash=canonical_hash(source_metadata),
        source_metadata_json=source_metadata,
        created_by=int(actor_id),
    )
    db.add(version)
    db.flush()
    return document, version


def commit_bid_upload_batch(
    db: Session,
    *,
    batch_id: str,
    expected_batch_etag: str,
    expected_file_count: int,
    expected_deactivation_count: int,
    change_note: str | None,
    confirm_start_analysis: bool,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    now: datetime | None = None,
) -> IdempotentCommandResult:
    """Commit add/replace/deactivate operations and all side effects atomically."""

    if confirm_start_analysis is not True:
        raise BidUploadBatchNotReady(
            status="ready",
            blocking_errors=["必须明确确认开始研判"],
        )
    current_time = _as_utc(now or _utc_now())
    batch = (
        db.query(BidUploadBatch)
        .filter(BidUploadBatch.id == batch_id)
        .with_for_update()
        .one_or_none()
    )
    if batch is None:
        raise BidUploadBatchCommitNotFound()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == batch.assessment_id)
        .with_for_update()
        .one()
    )
    if int(assessment.created_by) != int(actor_id) and not actor_is_admin:
        raise BidUploadBatchCommitNotFound()
    if str(assessment.lifecycle_status) != "active" or str(
        assessment.business_status
    ) == "superseded":
        raise BidUploadBatchNotReady(
            status=str(batch.status),
            blocking_errors=["Assessment 当前状态不允许提交资料版本"],
        )

    current_etag = upload_batch_etag(str(batch.id), int(batch.row_version))
    if expected_batch_etag != current_etag:
        raise BidUploadBatchCommitVersionMismatch(
            batch_id=str(batch.id),
            provided_etag=expected_batch_etag,
            current_etag=current_etag,
            current_row_version=int(batch.row_version),
        )
    if str(batch.status) in {"committing", "committed"}:
        raise BidUploadBatchAlreadyCommitted(
            status=str(batch.status),
            committed_manifest_id=(
                str(batch.committed_manifest_id)
                if batch.committed_manifest_id
                else None
            ),
        )

    files = (
        db.query(BidUploadBatchFile)
        .filter(BidUploadBatchFile.batch_id == batch.id)
        .order_by(BidUploadBatchFile.created_at.asc(), BidUploadBatchFile.id.asc())
        .with_for_update()
        .all()
    )
    deactivations = (
        db.query(BidUploadBatchDeactivation)
        .filter(BidUploadBatchDeactivation.batch_id == batch.id)
        .order_by(
            BidUploadBatchDeactivation.created_at.asc(),
            BidUploadBatchDeactivation.id.asc(),
        )
        .with_for_update()
        .all()
    )
    actual_file_count = sum(1 for row in files if str(row.status) == "ready")
    if int(expected_file_count) != actual_file_count:
        raise BidUploadBatchExpectedCountMismatch(
            expected=int(expected_file_count),
            actual=actual_file_count,
        )
    if int(expected_deactivation_count) != len(deactivations):
        raise BidUploadBatchExpectedDeactivationCountMismatch(
            expected=int(expected_deactivation_count),
            actual=len(deactivations),
        )

    current_manifest_id = (
        str(assessment.current_manifest_id) if assessment.current_manifest_id else None
    )
    base_manifest_id = str(batch.base_manifest_id) if batch.base_manifest_id else None
    if (
        (str(batch.purpose) == "initial" and current_manifest_id is not None)
        or (
            str(batch.purpose) == "change"
            and (base_manifest_id is None or base_manifest_id != current_manifest_id)
        )
    ):
        raise BidUploadBatchCommitBaselineStale(
            base_manifest_id=base_manifest_id,
            current_manifest_id=current_manifest_id,
        )
    baseline_members = _baseline_members(db, base_manifest_id)
    merge_errors = _merge_errors(
        purpose=str(batch.purpose),
        files=files,
        deactivations=deactivations,
        baseline_members=baseline_members,
    )
    if merge_errors:
        raise BidUploadBatchMergeConflict(reasons=merge_errors)

    before_batch = build_upload_batch_snapshot(db, batch)
    blocking_errors = list(before_batch["validation"]["blocking_errors"])
    if (
        str(batch.status) != "ready"
        or _as_utc(batch.expires_at) <= current_time
        or not before_batch["validation"]["can_commit"]
    ):
        if _as_utc(batch.expires_at) <= current_time:
            blocking_errors.append("上传批次已过期")
        raise BidUploadBatchNotReady(
            status=str(batch.status),
            blocking_errors=list(dict.fromkeys(blocking_errors)),
        )
    if any(str(row.status) != "ready" for row in files):
        raise BidUploadBatchNotReady(
            status=str(batch.status),
            blocking_errors=["并非全部批次文件都已就绪"],
        )

    file_object_ids = {str(row.file_object_id) for row in files if row.file_object_id}
    available_file_object_ids = {
        str(value[0])
        for value in db.query(BidFileObject.id)
        .filter(
            BidFileObject.id.in_(file_object_ids),
            BidFileObject.storage_status == "available",
        )
        .with_for_update()
        .all()
    } if file_object_ids else set()
    if any(
        not row.file_object_id
        or str(row.file_object_id) not in available_file_object_ids
        for row in files
    ):
        raise BidUploadBatchNotReady(
            status=str(batch.status),
            blocking_errors=["存在未绑定可用文件对象的批次文件"],
        )

    before_assessment = {
        "current_manifest_id": current_manifest_id,
        "active_run_id": (
            str(assessment.active_run_id) if assessment.active_run_id else None
        ),
        "business_status": str(assessment.business_status),
        "row_version": int(assessment.row_version),
    }
    batch.status = "committing"

    created_versions: list[tuple[BidUploadBatchFile, BidDocument, BidDocumentVersion]] = []
    replacement_version_by_document: dict[str, BidDocumentVersion] = {}
    addition_versions: list[tuple[BidUploadBatchFile, BidDocument, BidDocumentVersion]] = []
    for batch_file in files:
        document, version = _new_document_version(
            db,
            batch=batch,
            batch_file=batch_file,
            actor_id=int(actor_id),
        )
        created_versions.append((batch_file, document, version))
        if str(batch_file.operation) == "replace":
            replacement_version_by_document[str(document.id)] = version
        else:
            addition_versions.append((batch_file, document, version))

    deactivation_document_ids = {str(row.document_id) for row in deactivations}
    manifest_members: list[_ManifestMember] = []
    for baseline in baseline_members:
        if baseline.document_id in deactivation_document_ids:
            continue
        replacement = replacement_version_by_document.get(baseline.document_id)
        manifest_members.append(
            _ManifestMember(
                document_id=baseline.document_id,
                document_version_id=(
                    str(replacement.id) if replacement is not None else baseline.document_version_id
                ),
                file_object_id=(
                    str(replacement.file_object_id)
                    if replacement is not None
                    else baseline.file_object_id
                ),
                role=baseline.role,
                order_no=baseline.order_no,
            )
        )
    next_order = max((row.order_no for row in baseline_members), default=-1) + 1
    for offset, (_batch_file, document, version) in enumerate(addition_versions):
        manifest_members.append(
            _ManifestMember(
                document_id=str(document.id),
                document_version_id=str(version.id),
                file_object_id=str(version.file_object_id),
                role="uploaded_document",
                order_no=next_order + offset,
            )
        )
    manifest_members.sort(key=lambda row: (row.order_no, row.document_version_id))
    next_business_status = "preparing" if manifest_members else "awaiting_files"

    manifest_hash = canonical_hash(
        {
            "assessment_id": str(assessment.id),
            "members": [
                {
                    "document_id": row.document_id,
                    "document_version_id": row.document_version_id,
                    "file_object_id": row.file_object_id,
                    "role": row.role,
                    "order_no": row.order_no,
                }
                for row in manifest_members
            ],
        }
    )
    manifest_version = int(
        db.query(func.max(BidDocumentManifest.version))
        .filter(BidDocumentManifest.assessment_id == assessment.id)
        .scalar()
        or 0
    ) + 1
    manifest = BidDocumentManifest(
        id=str(uuid.uuid4()),
        assessment_id=str(assessment.id),
        version=manifest_version,
        manifest_hash=manifest_hash,
        change_note=change_note,
        committed_by=int(actor_id),
    )
    db.add(manifest)
    db.flush()
    for member in manifest_members:
        db.add(
            BidManifestDocument(
                manifest_id=str(manifest.id),
                document_version_id=member.document_version_id,
                role=member.role,
                order_no=member.order_no,
            )
        )
    db.flush()

    # Only Runs whose frozen input is the superseded current Manifest are
    # candidates.  Historical Runs tied to an older Manifest remain immutable,
    # even if legacy data accidentally left one in a non-terminal state.
    stale_runs = (
        db.query(BidAnalysisRun)
        .filter(
            BidAnalysisRun.assessment_id == assessment.id,
            BidAnalysisRun.manifest_id == current_manifest_id,
        )
        .with_for_update()
        .all()
        if current_manifest_id is not None
        else []
    )
    stale_run_before: dict[str, dict[str, Any]] = {}
    stale_run_rows: list[BidAnalysisRun] = []
    for run in stale_runs:
        status = str(run.status)
        if status not in _STALEABLE_RUN_STATUSES and not (
            status == "failed" and bool(run.retryable)
        ):
            continue
        stale_run_before[str(run.id)] = {
            "status": status,
            "retryable": bool(run.retryable),
            "row_version": int(run.row_version),
            "manifest_id": str(run.manifest_id),
        }
        run.status = "stale"
        run.retryable = False
        run.waiting_reason = "input_manifest_superseded"
        run.finished_at = current_time
        run.row_version = int(run.row_version) + 1
        stale_run_rows.append(run)

    assessment.current_manifest_id = str(manifest.id)
    assessment.active_run_id = None
    assessment.business_status = next_business_status
    assessment.updated_by = int(actor_id)
    assessment.row_version = int(assessment.row_version) + 1
    batch.status = "committed"
    batch.open_slot_key = None
    batch.committed_manifest_id = str(manifest.id)
    batch.committed_at = current_time
    batch.updated_by = int(actor_id)
    batch.row_version = int(batch.row_version) + 1
    db.flush()
    db.refresh(manifest)

    operation_id = f"op_{uuid.uuid4().hex}"
    assessment_snapshot = build_assessment_snapshot(db, assessment)
    manifest_summary = {
        "manifest_id": str(manifest.id),
        "version": int(manifest.version),
        "document_count": len(manifest_members),
        "manifest_hash": str(manifest.manifest_hash),
        "committed_at": _as_utc(manifest.created_at)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
    }
    response_body: dict[str, Any] = {
        "code": 202,
        "message": "资料版本已提交，系统开始处理",
        "data": {
            "manifest": manifest_summary,
            "operation": {
                "operation_id": operation_id,
                "status": "accepted",
                "status_url": f"/api/v1/bid-assessments/{assessment.id}",
            },
            "run": None,
            "assessment": assessment_snapshot,
            "batch": {
                "batch_id": str(batch.id),
                "status": str(batch.status),
                "row_version": int(batch.row_version),
                "etag": upload_batch_etag(str(batch.id), int(batch.row_version)),
            },
        },
        "error": None,
        "request_id": request_id,
    }

    event_ordinal = 0
    previous_event_id: str | None = None
    registered_events = []
    for batch_file, document, version in created_versions:
        event = append_outbox_event(
            db,
            event_type="bid.document.version_registered.v1",
            producer="bid-assessment-api-v1",
            aggregate_type="document_version",
            aggregate_id=str(version.id),
            aggregate_version=int(version.version_no),
            assessment_id=str(assessment.id),
            request_id=request_id,
            causation_event_id=previous_event_id,
            payload_schema="bid.document.version_registered.v1.payload",
            payload={
                "batch_id": str(batch.id),
                "manifest_id": str(manifest.id),
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "file_object_id": str(version.file_object_id),
                "operation": str(batch_file.operation),
                "version_no": int(version.version_no),
            },
            dedupe_key=f"document-version-registered:{version.id}",
            occurred_at=_event_time(current_time, event_ordinal),
        )
        registered_events.append(event)
        previous_event_id = event.event_id
        event_ordinal += 1

    manifest_event = append_outbox_event(
        db,
        event_type="bid.manifest.committed.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="manifest",
        aggregate_id=str(manifest.id),
        aggregate_version=int(manifest.version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        causation_event_id=previous_event_id,
        payload_schema="bid.manifest.committed.v1.payload",
        payload={
            "batch_id": str(batch.id),
            "manifest_id": str(manifest.id),
            "manifest_version": int(manifest.version),
            "manifest_hash": str(manifest.manifest_hash),
            "document_count": len(manifest_members),
            "operation_id": operation_id,
            "resource_version": int(assessment.row_version),
            "snapshot": assessment_snapshot,
        },
        dedupe_key=f"manifest-committed:{manifest.id}",
        occurred_at=_event_time(current_time, event_ordinal),
    )
    previous_event_id = manifest_event.event_id
    event_ordinal += 1

    stale_event = None
    if stale_run_rows:
        stale_event = append_outbox_event(
            db,
            event_type="bid.assessment.input_stale.v1",
            producer="bid-assessment-api-v1",
            aggregate_type="assessment",
            aggregate_id=str(assessment.id),
            aggregate_version=int(assessment.row_version),
            assessment_id=str(assessment.id),
            request_id=request_id,
            causation_event_id=previous_event_id,
            payload_schema="bid.assessment.input_stale.v1.payload",
            payload={
                "from": before_assessment["business_status"],
                "to": next_business_status,
                "recommended_view": str(assessment_snapshot["recommended_view"]),
                "allowed_actions": list(assessment_snapshot["allowed_actions"]),
                "previous_manifest_id": current_manifest_id,
                "manifest_id": str(manifest.id),
                "stale_run_ids": sorted(str(run.id) for run in stale_run_rows),
                "resource_version": int(assessment.row_version),
            },
            dedupe_key=f"assessment-input-stale:{manifest.id}",
            occurred_at=_event_time(current_time, event_ordinal),
        )
        previous_event_id = stale_event.event_id
        event_ordinal += 1

    parse_events = []
    for _batch_file, _document, version in created_versions:
        event = append_outbox_event(
            db,
            event_type="bid.document.parse_requested.v1",
            producer="bid-assessment-api-v1",
            aggregate_type="document_version",
            aggregate_id=str(version.id),
            aggregate_version=int(version.version_no),
            assessment_id=str(assessment.id),
            request_id=request_id,
            causation_event_id=previous_event_id,
            payload_schema="bid.document.parse_requested.v1.payload",
            payload={
                "batch_id": str(batch.id),
                "manifest_id": str(manifest.id),
                "document_version_id": str(version.id),
                "operation_id": operation_id,
            },
            dedupe_key=f"document-parse-requested:{manifest.id}:{version.id}",
            occurred_at=_event_time(current_time, event_ordinal),
        )
        parse_events.append(event)
        previous_event_id = event.event_id
        event_ordinal += 1

    for run in stale_run_rows:
        append_audit_log(
            db,
            actor_type="user",
            actor_id=int(actor_id),
            actor_ref=actor_ref,
            action="analysis_run.input_stale",
            entity_type="run",
            entity_id=str(run.id),
            assessment_id=str(assessment.id),
            outcome="succeeded",
            request_id=request_id,
            before=stale_run_before[str(run.id)],
            after={
                "status": str(run.status),
                "retryable": bool(run.retryable),
                "row_version": int(run.row_version),
                "manifest_id": str(run.manifest_id),
            },
            metadata={
                "new_manifest_id": str(manifest.id),
                "reason": "input_manifest_superseded",
            },
            correlation_id=(stale_event.event_id if stale_event is not None else None),
            occurred_at=current_time,
        )

    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="upload_batch.commit",
        entity_type="upload_batch",
        entity_id=str(batch.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before={"batch": before_batch, "assessment": before_assessment},
        after={
            "batch": response_body["data"]["batch"],
            "assessment": assessment_snapshot,
            "manifest": manifest_summary,
        },
        metadata={
            "http_method": "POST",
            "route_template": "/api/v1/bid-upload-batches/{batch_id}/commit",
            "batch_etag": current_etag,
            "purpose": str(batch.purpose),
            "base_manifest_id": base_manifest_id,
            "expected_file_count": int(expected_file_count),
            "expected_deactivation_count": int(expected_deactivation_count),
            "change_note": change_note,
            "add_count": len(addition_versions),
            "replace_count": len(replacement_version_by_document),
            "deactivate_count": len(deactivations),
            "carried_count": sum(
                1
                for member in baseline_members
                if member.document_id not in deactivation_document_ids
                and member.document_id not in replacement_version_by_document
            ),
            "stale_run_ids": sorted(str(run.id) for run in stale_run_rows),
            "registered_event_ids": [event.event_id for event in registered_events],
            "manifest_event_id": manifest_event.event_id,
            "stale_event_id": stale_event.event_id if stale_event is not None else None,
            "parse_event_ids": [event.event_id for event in parse_events],
            "planning_event": "deferred_until_parse_and_scope_ready",
        },
        correlation_id=manifest_event.event_id,
        occurred_at=current_time,
    )
    db.flush()
    return IdempotentCommandResult(
        status_code=202,
        body=response_body,
        resource_type="manifest",
        resource_id=str(manifest.id),
        response_ref=f"/api/v1/bid-assessments/{assessment.id}",
    )
