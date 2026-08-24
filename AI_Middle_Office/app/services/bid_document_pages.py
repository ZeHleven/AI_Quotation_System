"""Authoritative, storage-safe document listing for an Assessment Manifest."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
)
from app.services.bid_document_parse_projections import (
    build_document_parse_summaries,
)


PARSE_STATUSES = frozenset(
    {
        "not_requested",
        "queued",
        "running",
        "succeeded",
        "partial",
        "failed",
    }
)
DOCUMENT_PAGE_CACHE_CONTROL = "private, no-cache, max-age=0, must-revalidate"


class BidDocumentManifestNotFound(LookupError):
    """The requested Manifest is not visible through the Assessment."""


def _utc_rfc3339(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _version_summary(
    version: BidDocumentVersion,
    file_object: BidFileObject,
) -> dict[str, Any]:
    version_id = str(version.id)
    return {
        "version_id": version_id,
        "version_no": int(version.version_no),
        "filename": str(version.original_filename),
        "size_bytes": int(file_object.size_bytes),
        "mime_type": str(file_object.mime_type),
        "sha256_prefix": str(file_object.sha256).lower()[:12],
        "created_at": _utc_rfc3339(version.created_at),
        "detail_url": f"/api/v1/bid-document-versions/{version_id}",
        "download_url": f"/api/v1/bid-document-versions/{version_id}/download",
    }


def _selected_manifest(
    db: Session,
    assessment: BidAssessment,
    manifest_id: str | None,
) -> BidDocumentManifest | None:
    selected_id = manifest_id or (
        str(assessment.current_manifest_id) if assessment.current_manifest_id else None
    )
    if selected_id is None:
        return None
    manifest = (
        db.query(BidDocumentManifest)
        .filter(
            BidDocumentManifest.id == selected_id,
            BidDocumentManifest.assessment_id == assessment.id,
        )
        .one_or_none()
    )
    if manifest is None:
        raise BidDocumentManifestNotFound(selected_id)
    return manifest


def build_bid_document_page(
    db: Session,
    assessment: BidAssessment,
    *,
    manifest_id: str | None,
    document_type: str | None,
    parse_status: str | None,
    include_versions: bool,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Build an Assessment-scoped page without exposing object-store internals."""

    manifest = _selected_manifest(db, assessment, manifest_id)
    current_manifest_id = (
        str(assessment.current_manifest_id) if assessment.current_manifest_id else None
    )
    selection = "explicit" if manifest_id is not None else "current"
    filters = {
        "document_type": document_type,
        "parse_status": parse_status,
    }

    if manifest is None:
        return {
            "data": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "manifest": None,
            "current_manifest_id": current_manifest_id,
            "manifest_selection": selection,
            "filters": filters,
            "include_versions": include_versions,
        }

    manifest_document_count = (
        db.query(BidManifestDocument)
        .filter(BidManifestDocument.manifest_id == manifest.id)
        .count()
    )
    manifest_summary = {
        "manifest_id": str(manifest.id),
        "version": int(manifest.version),
        "document_count": int(manifest_document_count),
        "committed_at": _utc_rfc3339(manifest.created_at),
        "is_current": str(manifest.id) == current_manifest_id,
    }

    selected_query = (
        db.query(
            BidManifestDocument,
            BidDocumentVersion,
            BidDocument,
            BidFileObject,
        )
        .join(
            BidDocumentVersion,
            BidDocumentVersion.id == BidManifestDocument.document_version_id,
        )
        .join(BidDocument, BidDocument.id == BidDocumentVersion.document_id)
        .join(BidFileObject, BidFileObject.id == BidDocumentVersion.file_object_id)
        .outerjoin(
            BidDocumentParseHead,
            BidDocumentParseHead.document_version_id == BidDocumentVersion.id,
        )
        .outerjoin(
            BidDocumentParseRun,
            BidDocumentParseRun.id == BidDocumentParseHead.current_run_id,
        )
        .filter(BidManifestDocument.manifest_id == manifest.id)
    )
    if document_type is not None:
        selected_query = selected_query.filter(
            BidDocument.document_type == document_type
        )
    if parse_status == "not_requested":
        selected_query = selected_query.filter(
            BidDocumentParseHead.document_version_id.is_(None)
        )
    elif parse_status is not None:
        selected_query = selected_query.filter(BidDocumentParseRun.status == parse_status)

    total = int(selected_query.count())
    selected_rows = (
        selected_query.order_by(
            BidManifestDocument.order_no.asc(),
            BidDocument.id.asc(),
            BidDocumentVersion.id.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    document_ids = [str(row[2].id) for row in selected_rows]
    selected_version_ids = [str(row[1].id) for row in selected_rows]
    parse_summaries = build_document_parse_summaries(db, selected_version_ids)

    visible_versions_by_document: dict[
        str, list[tuple[BidDocumentVersion, BidFileObject]]
    ] = {document_id: [] for document_id in document_ids}
    if document_ids:
        visible_rows = (
            db.query(BidDocumentVersion, BidFileObject)
            .join(
                BidManifestDocument,
                BidManifestDocument.document_version_id == BidDocumentVersion.id,
            )
            .join(
                BidDocumentManifest,
                BidDocumentManifest.id == BidManifestDocument.manifest_id,
            )
            .join(BidFileObject, BidFileObject.id == BidDocumentVersion.file_object_id)
            .filter(
                BidDocumentManifest.assessment_id == assessment.id,
                BidDocumentVersion.document_id.in_(document_ids),
            )
            .order_by(
                BidDocumentVersion.document_id.asc(),
                BidDocumentVersion.version_no.asc(),
                BidDocumentVersion.id.asc(),
            )
            .all()
        )
        seen_version_ids: set[str] = set()
        for version, file_object in visible_rows:
            version_id = str(version.id)
            if version_id in seen_version_ids:
                continue
            seen_version_ids.add(version_id)
            visible_versions_by_document[str(version.document_id)].append(
                (version, file_object)
            )

    current_version_id_by_document: dict[str, str] = {}
    if current_manifest_id is not None and document_ids:
        current_rows = (
            db.query(BidDocumentVersion.document_id, BidDocumentVersion.id)
            .join(
                BidManifestDocument,
                BidManifestDocument.document_version_id == BidDocumentVersion.id,
            )
            .filter(
                BidManifestDocument.manifest_id == current_manifest_id,
                BidDocumentVersion.document_id.in_(document_ids),
            )
            .all()
        )
        current_version_id_by_document = {
            str(document_id): str(version_id)
            for document_id, version_id in current_rows
        }

    items: list[dict[str, Any]] = []
    for member, selected_version, document, selected_file in selected_rows:
        document_id = str(document.id)
        selected_version_id = str(selected_version.id)
        parse_summary = parse_summaries[selected_version_id]
        visible_versions = visible_versions_by_document[document_id]
        summaries = [
            _version_summary(version, file_object)
            for version, file_object in visible_versions
        ]
        index_by_version_id = {
            summary["version_id"]: index for index, summary in enumerate(summaries)
        }
        selected_index = index_by_version_id[selected_version_id]
        current_version_id = current_version_id_by_document.get(document_id)
        current_summary = (
            summaries[index_by_version_id[current_version_id]]
            if current_version_id in index_by_version_id
            else None
        )
        items.append(
            {
                "document_id": document_id,
                "logical_name": str(document.logical_name),
                "document_type": str(document.document_type),
                "role": str(member.role),
                "order_no": int(member.order_no),
                "selected_version": _version_summary(
                    selected_version,
                    selected_file,
                ),
                "current_version": current_summary,
                "parse_status": parse_summary["status"],
                "parse_quality": parse_summary["quality"],
                "is_in_current_manifest": selected_version_id == current_version_id,
                "replacement_chain": {
                    "previous_version_id": (
                        summaries[selected_index - 1]["version_id"]
                        if selected_index > 0
                        else None
                    ),
                    "next_version_id": (
                        summaries[selected_index + 1]["version_id"]
                        if selected_index + 1 < len(summaries)
                        else None
                    ),
                    "latest_version_id": summaries[-1]["version_id"],
                    "visible_version_count": len(summaries),
                },
                "warnings": parse_summary["warnings"],
                "versions": summaries if include_versions else None,
            }
        )

    return {
        "data": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "manifest": manifest_summary,
        "current_manifest_id": current_manifest_id,
        "manifest_selection": selection,
        "filters": filters,
        "include_versions": include_versions,
    }


def bid_document_page_etag(
    assessment: BidAssessment,
    page_payload: dict[str, Any],
) -> str:
    """Hash the complete public projection, never an object-store identifier."""

    canonical = json.dumps(
        {
            "assessment_id": str(assessment.id),
            "assessment_row_version": int(assessment.row_version),
            "page": page_payload,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f'"bid-document-page:{assessment.id}:{fingerprint}"'


def bid_document_page_headers(
    assessment: BidAssessment,
    page_payload: dict[str, Any],
) -> dict[str, str]:
    return {
        "ETag": bid_document_page_etag(assessment, page_payload),
        "X-Resource-Version": str(int(assessment.row_version)),
        "Cache-Control": DOCUMENT_PAGE_CACHE_CONTROL,
        "Vary": "Authorization",
    }
