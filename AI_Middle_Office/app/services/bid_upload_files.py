"""API-12 bounded inspection, registration, and orphan compensation."""
from __future__ import annotations

import codecs
import hashlib
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidDocumentVersion,
    BidFileObject,
    BidManifestDocument,
    BidUploadBatch,
    BidUploadBatchDeactivation,
    BidUploadBatchFile,
)
from app.services.bid_assessment_eventing import append_audit_log, append_outbox_event
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_upload_batch_snapshots import (
    build_upload_batch_snapshot,
    current_upload_limits,
    derive_upload_batch_status,
)
from app.services.bid_upload_file_storage import (
    BidUploadObjectStorage,
    StoredBidUploadObject,
    normalized_upload_object_prefix,
)


WRITABLE_BATCH_STATUSES = ("draft", "uploading", "ready")
_SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
_CANONICAL_MIME_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "txt": "text/plain",
    "md": "text/markdown",
}
_DECLARED_MIME_TYPES: dict[str, set[str]] = {
    extension: {canonical, "application/octet-stream"}
    for extension, canonical in _CANONICAL_MIME_TYPES.items()
}
_DECLARED_MIME_TYPES["txt"].add("text/plain")
_DECLARED_MIME_TYPES["md"].update({"text/plain", "text/markdown"})


class BidUploadFileError(RuntimeError):
    code = "BID_FILE_CONTENT_INVALID"


class BidUploadFileTooLarge(BidUploadFileError):
    code = "BID_FILE_TOO_LARGE"

    def __init__(self, *, max_file_bytes: int, observed_bytes: int):
        super().__init__(self.code)
        self.max_file_bytes = int(max_file_bytes)
        self.observed_bytes = int(observed_bytes)


class BidUploadBatchTooLarge(BidUploadFileError):
    code = "BID_BATCH_TOO_LARGE"

    def __init__(self, *, reason: str, limit: int, observed: int):
        super().__init__(self.code)
        self.reason = reason
        self.limit = int(limit)
        self.observed = int(observed)


class BidUploadFileTypeUnsupported(BidUploadFileError):
    code = "BID_FILE_TYPE_UNSUPPORTED"

    def __init__(self, *, filename: str, extension: str | None, declared_mime: str):
        super().__init__(self.code)
        self.filename = filename
        self.extension = extension
        self.declared_mime = declared_mime


class BidUploadFileContentInvalid(BidUploadFileError):
    code = "BID_FILE_CONTENT_INVALID"

    def __init__(self, *, reason: str, expected_sha256: str | None = None, actual_sha256: str | None = None):
        super().__init__(self.code)
        self.reason = reason
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256


class BidUploadReplacementTargetInvalid(BidUploadFileError):
    code = "BID_REPLACEMENT_TARGET_INVALID"


class BidUploadClientFileConflict(BidUploadFileError):
    code = "BID_UPLOAD_CLIENT_FILE_CONFLICT"

    def __init__(self, *, existing_file_id: str):
        super().__init__(self.code)
        self.existing_file_id = existing_file_id


class BidUploadBatchStateConflict(BidUploadFileError):
    code = "BID_UPLOAD_BATCH_NOT_READY"

    def __init__(self, *, status: str):
        super().__init__(self.code)
        self.status = status


class BidUploadFileResourceNotFound(BidUploadFileError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidUploadFileStorageUnavailable(BidUploadFileError):
    code = "BID_STORAGE_UNAVAILABLE"


@dataclass(frozen=True)
class BidUploadInspection:
    filename: str
    extension: str
    declared_mime_type: str
    canonical_mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BidUploadFileRegistration:
    command: IdempotentCommandResult
    batch_file_id: str
    batch_file_row_version: int
    batch_row_version: int
    object_consumed: bool
    replayed_existing_file: bool


@dataclass(frozen=True)
class BidUploadOrphanCleanupResult:
    scanned: int
    referenced: int
    deleted: int
    delete_failed: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_upload_filename(raw_filename: str | None) -> str:
    filename = str(raw_filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or len(filename) > 500 or any(ord(character) < 0x20 for character in filename):
        raise BidUploadFileContentInvalid(reason="filename_invalid")
    return filename


def normalize_expected_sha256(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise BidUploadFileContentInvalid(reason="sha256_header_invalid")
    return normalized


def _validate_magic(extension: str, head: bytes) -> None:
    if extension == "pdf" and head.find(b"%PDF-", 0, 1024) < 0:
        raise BidUploadFileContentInvalid(reason="pdf_magic_mismatch")
    if extension in {"docx", "xlsx", "xlsm"} and not head.startswith(
        (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    ):
        raise BidUploadFileContentInvalid(reason="office_zip_magic_mismatch")
    if extension == "png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BidUploadFileContentInvalid(reason="png_magic_mismatch")
    if extension in {"jpg", "jpeg"} and not head.startswith(b"\xff\xd8\xff"):
        raise BidUploadFileContentInvalid(reason="jpeg_magic_mismatch")


def _validate_office_container(stream, *, extension: str, compressed_bytes: int) -> None:
    required_member = "word/document.xml" if extension == "docx" else "xl/workbook.xml"
    try:
        with zipfile.ZipFile(stream) as archive:
            members = archive.infolist()
            names = {member.filename.replace("\\", "/") for member in members}
            if len(members) > 10000:
                raise BidUploadFileContentInvalid(reason="office_entry_count_exceeded")
            if "[Content_Types].xml" not in names or required_member not in names:
                raise BidUploadFileContentInvalid(reason="office_structure_invalid")
            if any(member.flag_bits & 0x1 for member in members):
                raise BidUploadFileContentInvalid(reason="office_archive_encrypted")
            total_uncompressed = sum(max(0, int(member.file_size)) for member in members)
            if total_uncompressed > 2 * 1024 * 1024 * 1024:
                raise BidUploadFileContentInvalid(reason="office_uncompressed_size_exceeded")
            if total_uncompressed > max(1, int(compressed_bytes)) * 100:
                raise BidUploadFileContentInvalid(reason="office_compression_ratio_exceeded")
    except BidUploadFileContentInvalid:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise BidUploadFileContentInvalid(reason="office_archive_invalid") from exc


async def inspect_bid_upload(
    upload_file,
    *,
    expected_sha256: str | None,
) -> BidUploadInspection:
    """Read in bounded chunks, hash, type-check, then rewind for object storage."""

    filename = normalize_upload_filename(upload_file.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    limits = current_upload_limits()
    declared_mime = str(upload_file.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if (
        not extension
        or extension not in limits["accepted_extensions"]
        or extension not in _CANONICAL_MIME_TYPES
        or declared_mime not in _DECLARED_MIME_TYPES[extension]
    ):
        raise BidUploadFileTypeUnsupported(
            filename=filename,
            extension=extension or None,
            declared_mime=declared_mime,
        )

    max_bytes = int(limits["max_file_bytes"])
    chunk_bytes = max(65536, min(int(settings.bid_upload_read_chunk_bytes), 8 * 1024 * 1024))
    normalized_expected = normalize_expected_sha256(expected_sha256)
    digest = hashlib.sha256()
    head = bytearray()
    total = 0
    text_decoder = (
        codecs.getincrementaldecoder("utf-8")("strict")
        if extension in {"txt", "md"}
        else None
    )
    try:
        while True:
            chunk = await upload_file.read(chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise BidUploadFileTooLarge(
                    max_file_bytes=max_bytes,
                    observed_bytes=total,
                )
            digest.update(chunk)
            if len(head) < 8192:
                head.extend(chunk[: 8192 - len(head)])
            if text_decoder is not None:
                if b"\x00" in chunk:
                    raise BidUploadFileContentInvalid(reason="text_contains_nul")
                try:
                    text_decoder.decode(chunk, final=False)
                except UnicodeDecodeError as exc:
                    raise BidUploadFileContentInvalid(reason="text_not_utf8") from exc
        if total < 1:
            raise BidUploadFileContentInvalid(reason="file_empty")
        if text_decoder is not None:
            try:
                text_decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise BidUploadFileContentInvalid(reason="text_not_utf8") from exc
        actual_sha256 = digest.hexdigest()
        if normalized_expected is not None and normalized_expected != actual_sha256:
            raise BidUploadFileContentInvalid(
                reason="sha256_mismatch",
                expected_sha256=normalized_expected,
                actual_sha256=actual_sha256,
            )
        _validate_magic(extension, bytes(head))
        if extension in {"docx", "xlsx", "xlsm"}:
            await upload_file.seek(0)
            _validate_office_container(
                upload_file.file,
                extension=extension,
                compressed_bytes=total,
            )
        return BidUploadInspection(
            filename=filename,
            extension=extension,
            declared_mime_type=declared_mime,
            canonical_mime_type=_CANONICAL_MIME_TYPES[extension],
            size_bytes=total,
            sha256=actual_sha256,
        )
    finally:
        await upload_file.seek(0)


def _verify_replace_target(
    db: Session,
    *,
    batch: BidUploadBatch,
    assessment: BidAssessment,
    operation: str,
    replace_document_id: str | None,
) -> None:
    if operation == "add":
        if replace_document_id is not None:
            raise BidUploadReplacementTargetInvalid()
        return
    if (
        str(batch.purpose) != "change"
        or not batch.base_manifest_id
        or str(assessment.current_manifest_id or "") != str(batch.base_manifest_id)
        or not replace_document_id
    ):
        raise BidUploadReplacementTargetInvalid()
    target_exists = (
        db.query(BidManifestDocument.manifest_id)
        .join(
            BidDocumentVersion,
            BidDocumentVersion.id == BidManifestDocument.document_version_id,
        )
        .filter(
            BidManifestDocument.manifest_id == batch.base_manifest_id,
            BidDocumentVersion.document_id == replace_document_id,
        )
        .first()
        is not None
    )
    if not target_exists:
        raise BidUploadReplacementTargetInvalid()


def _same_client_upload(
    row: BidUploadBatchFile,
    *,
    inspection: BidUploadInspection,
    operation: str,
    replace_document_id: str | None,
    relative_path: str | None,
) -> bool:
    return (
        str(row.status) in {"inspecting", "ready"}
        and
        str(row.sha256) == inspection.sha256
        and int(row.size_bytes) == inspection.size_bytes
        and str(row.filename) == inspection.filename
        and str(row.mime_type) == inspection.canonical_mime_type
        and str(row.operation) == operation
        and (str(row.replace_document_id) if row.replace_document_id else None)
        == replace_document_id
        and row.relative_path == relative_path
    )


def _response_body(
    db: Session,
    *,
    batch: BidUploadBatch,
    batch_file: BidUploadBatchFile,
    request_id: str,
) -> dict[str, Any]:
    duplicate = (
        db.query(BidUploadBatchFile.id)
        .filter(
            BidUploadBatchFile.batch_id == batch.id,
            BidUploadBatchFile.file_object_id == batch_file.file_object_id,
            BidUploadBatchFile.id != batch_file.id,
        )
        .order_by(BidUploadBatchFile.created_at.asc(), BidUploadBatchFile.id.asc())
        .first()
    )
    snapshot = build_upload_batch_snapshot(db, batch)
    return {
        "code": 200,
        "message": "文件已接收",
        "data": {
            "file": {
                "batch_file_id": str(batch_file.id),
                "client_file_id": str(batch_file.client_file_id),
                "filename": str(batch_file.filename),
                "status": str(batch_file.status),
                "size_bytes": int(batch_file.size_bytes),
                "sha256": str(batch_file.sha256),
                "row_version": int(batch_file.row_version),
                "duplicate_of": str(duplicate[0]) if duplicate is not None else None,
            },
            "batch": {
                "batch_id": str(batch.id),
                "row_version": int(batch.row_version),
                "can_commit": bool(snapshot["validation"]["can_commit"]),
            },
        },
        "error": None,
        "request_id": request_id,
    }


def register_bid_upload_file(
    db: Session,
    *,
    batch_id: str,
    batch_file_id: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    client_file_id: str,
    operation: str,
    replace_document_id: str | None,
    relative_path: str | None,
    inspection: BidUploadInspection,
    stored_object: StoredBidUploadObject | None,
    now: datetime | None = None,
) -> BidUploadFileRegistration:
    current_time = now or _utc_now()
    batch = (
        db.query(BidUploadBatch)
        .filter(BidUploadBatch.id == batch_id)
        .with_for_update()
        .one_or_none()
    )
    if batch is None:
        raise BidUploadFileResourceNotFound()
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == batch.assessment_id)
        .with_for_update()
        .one()
    )
    if int(assessment.created_by) != int(actor_id) and not actor_is_admin:
        raise BidUploadFileResourceNotFound()

    existing = (
        db.query(BidUploadBatchFile)
        .filter(
            BidUploadBatchFile.batch_id == batch.id,
            BidUploadBatchFile.client_file_id == client_file_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        if not _same_client_upload(
            existing,
            inspection=inspection,
            operation=operation,
            replace_document_id=replace_document_id,
            relative_path=relative_path,
        ):
            raise BidUploadClientFileConflict(existing_file_id=str(existing.id))
        body = _response_body(
            db,
            batch=batch,
            batch_file=existing,
            request_id=request_id,
        )
        return BidUploadFileRegistration(
            command=IdempotentCommandResult(
                status_code=201,
                body=body,
                resource_type="upload_file",
                resource_id=str(existing.id),
                response_ref=(
                    f"/api/v1/bid-upload-batches/{batch.id}/files/{existing.id}"
                ),
            ),
            batch_file_id=str(existing.id),
            batch_file_row_version=int(existing.row_version),
            batch_row_version=int(batch.row_version),
            object_consumed=False,
            replayed_existing_file=True,
        )

    if str(batch.status) not in WRITABLE_BATCH_STATUSES or _as_utc(batch.expires_at) <= _as_utc(current_time):
        raise BidUploadBatchStateConflict(status=str(batch.status))
    _verify_replace_target(
        db,
        batch=batch,
        assessment=assessment,
        operation=operation,
        replace_document_id=replace_document_id,
    )

    limits = current_upload_limits()
    file_count = (
        db.query(func.count(BidUploadBatchFile.id))
        .filter(BidUploadBatchFile.batch_id == batch.id)
        .scalar()
        or 0
    )
    total_bytes = (
        db.query(func.coalesce(func.sum(BidUploadBatchFile.size_bytes), 0))
        .filter(BidUploadBatchFile.batch_id == batch.id)
        .scalar()
        or 0
    )
    if int(file_count) + 1 > int(limits["max_files"]):
        raise BidUploadBatchTooLarge(
            reason="max_files",
            limit=int(limits["max_files"]),
            observed=int(file_count) + 1,
        )
    if int(total_bytes) + inspection.size_bytes > int(limits["max_batch_bytes"]):
        raise BidUploadBatchTooLarge(
            reason="max_batch_bytes",
            limit=int(limits["max_batch_bytes"]),
            observed=int(total_bytes) + inspection.size_bytes,
        )

    file_object = (
        db.query(BidFileObject)
        .filter(
            BidFileObject.sha256 == inspection.sha256,
            BidFileObject.size_bytes == inspection.size_bytes,
        )
        .with_for_update()
        .one_or_none()
    )
    object_consumed = False
    if file_object is None:
        if stored_object is None:
            raise BidUploadFileStorageUnavailable()
        file_object = BidFileObject(
            id=str(uuid.uuid4()),
            sha256=inspection.sha256,
            object_key=stored_object.object_key,
            size_bytes=inspection.size_bytes,
            mime_type=inspection.canonical_mime_type,
            storage_status="available",
            storage_etag=stored_object.storage_etag,
            created_by=int(actor_id),
            row_version=1,
        )
        db.add(file_object)
        db.flush()
        object_consumed = True
    elif str(file_object.storage_status) != "available":
        raise BidUploadFileStorageUnavailable()

    before = {
        "status": str(batch.status),
        "row_version": int(batch.row_version),
        "file_count": int(file_count),
        "total_bytes": int(total_bytes),
    }
    batch_file = BidUploadBatchFile(
        id=batch_file_id,
        batch_id=str(batch.id),
        file_object_id=str(file_object.id),
        replace_document_id=replace_document_id,
        client_file_id=client_file_id,
        operation=operation,
        filename=inspection.filename,
        relative_path=relative_path,
        size_bytes=inspection.size_bytes,
        mime_type=inspection.canonical_mime_type,
        sha256=inspection.sha256,
        temporary_object_ref=str(file_object.object_key),
        status="ready",
        error_code=None,
        row_version=1,
    )
    db.add(batch_file)
    db.flush()

    statuses = [
        str(value[0])
        for value in db.query(BidUploadBatchFile.status)
        .filter(BidUploadBatchFile.batch_id == batch.id)
        .all()
    ]
    deactivation_count = int(
        db.query(func.count(BidUploadBatchDeactivation.id))
        .filter(BidUploadBatchDeactivation.batch_id == batch.id)
        .scalar()
        or 0
    )
    batch.status = derive_upload_batch_status(
        statuses,
        deactivation_count=deactivation_count,
    )
    batch.row_version = int(batch.row_version) + 1
    batch.updated_by = int(actor_id)
    db.flush()

    body = _response_body(
        db,
        batch=batch,
        batch_file=batch_file,
        request_id=request_id,
    )
    snapshot = build_upload_batch_snapshot(db, batch)
    ready_count = sum(1 for row in snapshot["files"] if row["status"] == "ready")
    failed_count = sum(
        1 for row in snapshot["files"] if row["status"] in {"rejected", "failed"}
    )
    event = append_outbox_event(
        db,
        event_type="bid.upload_file.received.v1",
        producer="bid-assessment-api-v1",
        aggregate_type="upload_batch",
        aggregate_id=str(batch.id),
        aggregate_version=int(batch.row_version),
        assessment_id=str(assessment.id),
        request_id=request_id,
        payload_schema="bid.upload_file.received.v1.payload",
        payload={
            "batch_id": str(batch.id),
            "batch_file_id": str(batch_file.id),
            "status": str(batch.status),
            "ready_count": ready_count,
            "failed_count": failed_count,
            "resource_version": int(batch.row_version),
        },
        dedupe_key=f"upload-file-received:{batch_file.id}:v1",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="user",
        actor_id=int(actor_id),
        actor_ref=actor_ref,
        action="upload_file.receive",
        entity_type="upload_file",
        entity_id=str(batch_file.id),
        assessment_id=str(assessment.id),
        outcome="succeeded",
        request_id=request_id,
        before=before,
        after=body["data"],
        metadata={
            "http_method": "POST",
            "route_template": "/api/v1/bid-upload-batches/{batch_id}/files",
            "batch_id": str(batch.id),
            "content_sha256": inspection.sha256,
            "outbox_event_type": event.event_type,
        },
        correlation_id=event.event_id,
        occurred_at=current_time,
    )
    return BidUploadFileRegistration(
        command=IdempotentCommandResult(
            status_code=201,
            body=body,
            resource_type="upload_file",
            resource_id=str(batch_file.id),
            response_ref=(
                f"/api/v1/bid-upload-batches/{batch.id}/files/{batch_file.id}"
            ),
        ),
        batch_file_id=str(batch_file.id),
        batch_file_row_version=int(batch_file.row_version),
        batch_row_version=int(batch.row_version),
        object_consumed=object_consumed,
        replayed_existing_file=False,
    )


def cleanup_orphaned_bid_upload_objects(
    db: Session,
    *,
    storage: BidUploadObjectStorage,
    now: datetime | None = None,
    limit: int = 1000,
) -> BidUploadOrphanCleanupResult:
    """Delete only old upload objects with no authoritative database reference."""

    current_time = now or _utc_now()
    cutoff = _as_utc(current_time) - timedelta(
        seconds=max(3600, int(settings.bid_upload_orphan_grace_seconds))
    )
    candidates = [
        item
        for item in storage.list_candidates(
            prefix=normalized_upload_object_prefix(),
            limit=max(1, min(int(limit), 10000)),
        )
        if _as_utc(item.last_modified) <= cutoff
    ]
    if not candidates:
        return BidUploadOrphanCleanupResult(0, 0, 0, 0)
    keys = [item.object_key for item in candidates]
    referenced = {
        str(value[0])
        for value in db.query(BidFileObject.object_key)
        .filter(BidFileObject.object_key.in_(keys))
        .all()
    }
    referenced.update(
        str(value[0])
        for value in db.query(BidUploadBatchFile.temporary_object_ref)
        .filter(BidUploadBatchFile.temporary_object_ref.in_(keys))
        .all()
        if value[0]
    )
    deleted = 0
    delete_failed = 0
    for item in candidates:
        if item.object_key in referenced:
            continue
        try:
            storage.delete(object_key=item.object_key)
            deleted += 1
        except Exception:
            delete_failed += 1
    return BidUploadOrphanCleanupResult(
        scanned=len(candidates),
        referenced=len(referenced),
        deleted=deleted,
        delete_failed=delete_failed,
    )
