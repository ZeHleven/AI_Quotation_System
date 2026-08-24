"""Assessment-authorized DocumentVersion projections and download descriptors."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Iterator
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidManifestDocument,
)
from app.services.bid_document_parse_projections import (
    build_document_parse_summary,
)


DOCUMENT_VERSION_CACHE_CONTROL = "private, no-cache, max-age=0, must-revalidate"
_SAFE_MIME_TYPE = re.compile(
    r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$"
)


class BidDocumentVersionNotFound(LookupError):
    """The version has no Manifest reference visible to the current actor."""


@dataclass(frozen=True)
class VisibleBidDocumentVersion:
    version: BidDocumentVersion
    document: BidDocument
    file_object: BidFileObject
    manifest_references: tuple[dict[str, Any], ...]


def _utc_rfc3339(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_visible_bid_document_version(
    db: Session,
    *,
    version_id: str,
    actor_id: int,
    actor_is_admin: bool,
) -> VisibleBidDocumentVersion:
    """Resolve metadata and ACL in one Manifest-rooted query.

    FileObject identity, hashes, upload ownership, and Document ownership are
    deliberately never authorization sources.
    """

    query = (
        db.query(
            BidDocumentVersion,
            BidDocument,
            BidFileObject,
            BidManifestDocument,
            BidDocumentManifest,
            BidAssessment,
        )
        .join(BidDocument, BidDocument.id == BidDocumentVersion.document_id)
        .join(BidFileObject, BidFileObject.id == BidDocumentVersion.file_object_id)
        .join(
            BidManifestDocument,
            BidManifestDocument.document_version_id == BidDocumentVersion.id,
        )
        .join(
            BidDocumentManifest,
            BidDocumentManifest.id == BidManifestDocument.manifest_id,
        )
        .join(
            BidAssessment,
            BidAssessment.id == BidDocumentManifest.assessment_id,
        )
        .filter(BidDocumentVersion.id == version_id)
    )
    if not actor_is_admin:
        query = query.filter(BidAssessment.created_by == int(actor_id))
    rows = query.order_by(
        BidAssessment.id.asc(),
        BidDocumentManifest.version.asc(),
        BidDocumentManifest.id.asc(),
        BidManifestDocument.order_no.asc(),
    ).all()
    if not rows:
        raise BidDocumentVersionNotFound(version_id)

    version, document, file_object = rows[0][:3]
    references = tuple(
        {
            "assessment_id": str(assessment.id),
            "assessment_url": f"/api/v1/bid-assessments/{assessment.id}",
            "manifest_id": str(manifest.id),
            "manifest_version": int(manifest.version),
            "is_current_manifest": (
                str(assessment.current_manifest_id) == str(manifest.id)
                if assessment.current_manifest_id is not None
                else False
            ),
            "role": str(member.role),
            "order_no": int(member.order_no),
        }
        for _, _, _, member, manifest, assessment in rows
    )
    return VisibleBidDocumentVersion(
        version=version,
        document=document,
        file_object=file_object,
        manifest_references=references,
    )


def _safe_upload_source(version: BidDocumentVersion) -> dict[str, Any]:
    metadata = version.source_metadata_json
    if not isinstance(metadata, dict) or metadata.get("source") != "bid_upload_batch":
        return {
            "source_type": "unknown",
            "operation": None,
            "relative_path": None,
        }
    operation = metadata.get("operation")
    if operation not in {"add", "replace"}:
        operation = None
    relative_path = metadata.get("relative_path")
    if not isinstance(relative_path, str):
        relative_path = None
    else:
        relative_path = relative_path.strip()[:1000] or None
    return {
        "source_type": "upload_batch",
        "operation": operation,
        "relative_path": relative_path,
    }


def build_bid_document_version_detail(
    db: Session,
    visible: VisibleBidDocumentVersion,
) -> dict[str, Any]:
    version = visible.version
    document = visible.document
    file_object = visible.file_object
    version_id = str(version.id)
    can_download = str(file_object.storage_status) == "available"
    return {
        "version_id": version_id,
        "document": {
            "document_id": str(document.id),
            "logical_name": str(document.logical_name),
            "document_type": str(document.document_type),
        },
        "version_no": int(version.version_no),
        "filename": str(version.original_filename),
        "size_bytes": int(file_object.size_bytes),
        "mime_type": str(file_object.mime_type),
        "sha256": str(file_object.sha256).lower(),
        "created_at": _utc_rfc3339(version.created_at),
        "upload_source": _safe_upload_source(version),
        "parse_summary": build_document_parse_summary(db, version_id),
        "manifest_references": list(visible.manifest_references),
        "allowed_actions": {
            "download": can_download,
            "download_url": (
                f"/api/v1/bid-document-versions/{version_id}/download"
                if can_download
                else None
            ),
        },
    }


def bid_document_version_etag(detail: dict[str, Any]) -> str:
    canonical = json.dumps(
        detail,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f'"bid-document-version:{detail["version_id"]}:{fingerprint}"'


def bid_document_version_headers(detail: dict[str, Any]) -> dict[str, str]:
    return {
        "ETag": bid_document_version_etag(detail),
        "X-Resource-Version": str(int(detail["version_no"])),
        "Cache-Control": DOCUMENT_VERSION_CACHE_CONTROL,
        "Vary": "Authorization",
    }


def safe_download_mime_type(value: str) -> str:
    candidate = str(value or "").strip()
    if not _SAFE_MIME_TYPE.fullmatch(candidate):
        return "application/octet-stream"
    return candidate.lower()


def safe_content_disposition(filename: str) -> str:
    normalized = str(filename or "").replace("\\", "/")
    normalized = PurePosixPath(normalized).name
    normalized = "".join(
        character for character in normalized if ord(character) >= 0x20 and ord(character) != 0x7F
    ).strip()
    if not normalized:
        normalized = "download"
    normalized = normalized[:500]
    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._-")
    if not ascii_fallback:
        suffix = PurePosixPath(normalized).suffix
        ascii_fallback = f"download{suffix}" if suffix else "download"
    ascii_fallback = ascii_fallback[:180]
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(normalized, safe='')}"
    )


def iter_bid_download(stream: BinaryIO, *, chunk_size: int) -> Iterator[bytes]:
    """Yield one object stream and always release its underlying HTTP pool slot."""

    try:
        while True:
            chunk = stream.read(max(4096, int(chunk_size)))
            if not chunk:
                break
            yield bytes(chunk)
    finally:
        try:
            stream.close()
        finally:
            release_connection = getattr(stream, "release_conn", None)
            if callable(release_connection):
                release_connection()
