from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import registry as model_registry  # noqa: F401
from app.models.bidding import BidProject, BidProjectFile
from app.models.tender_evidence import (
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
)
from app.models.user import User  # noqa: F401 - register users FK metadata
from app.services.tender_evidence_locator import (
    normalize_tender_segment_locator,
)
from app.services.tender_evidence_indexing import ensure_evidence_index_job
from app.services.tender_evidence_body_storage import (
    BODY_STORAGE_BACKEND_MYSQL,
    MinioTenderEvidenceBodyStorage,
    TenderEvidenceBodyError,
    TenderEvidenceBodyStorage,
    build_evidence_body_package,
)


MAX_BLOCK_CHARACTERS = 12_000


class TenderEvidenceIngestError(ValueError):
    pass


class TenderEvidenceIngestConflict(TenderEvidenceIngestError):
    pass


@dataclass(frozen=True)
class TenderEvidenceIngestResult:
    case_id: str
    evidence_document_uuid: str
    document_key: str
    document_version: int
    manifest_version: int
    manifest_hash: str
    block_count: int
    index_job_uuid: str
    idempotent: bool


@dataclass(frozen=True)
class NormalizedBlock:
    block_order: int
    content: str
    page: int | None
    sheet: str | None
    cell_range: str | None
    section: str | None
    locator_json: dict[str, Any]
    keywords: tuple[str, ...]


def ingest_bid_project_file(
    db: Session,
    *,
    project_uuid: str,
    file_uuid: str,
    document_key: str | None = None,
    document_type: str | None = None,
    created_by: int | None = None,
    body_storage: TenderEvidenceBodyStorage | None = None,
    externalize_body: bool | None = None,
) -> TenderEvidenceIngestResult:
    """Promote one parsed BidProjectFile into immutable evidence versions.

    The caller owns commit/rollback. Repeating the same project/file request is
    idempotent and does not create a new document or manifest version.
    """

    normalized_project_uuid = project_uuid.strip()
    normalized_file_uuid = file_uuid.strip()
    if not normalized_project_uuid or not normalized_file_uuid:
        raise TenderEvidenceIngestError("project_uuid and file_uuid are required")

    project = (
        db.query(BidProject)
        .filter(BidProject.project_uuid == normalized_project_uuid)
        .with_for_update()
        .one_or_none()
    )
    if project is None:
        raise TenderEvidenceIngestError("bid project does not exist")
    source = (
        db.query(BidProjectFile)
        .filter(
            BidProjectFile.project_id == project.id,
            BidProjectFile.file_uuid == normalized_file_uuid,
        )
        .one_or_none()
    )
    if source is None:
        raise TenderEvidenceIngestError(
            "parsed source file does not exist in the scoped bid project"
        )
    if source.parser_status != "parsed":
        raise TenderEvidenceIngestError("source file has not completed parsing")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", source.sha256 or ""):
        raise TenderEvidenceIngestError("source file has no valid SHA-256")

    normalized_key = normalize_document_key(
        document_key or _default_document_key(source)
    )
    normalized_type = _clean_text(document_type or source.file_type)[:64]
    if not normalized_type:
        raise TenderEvidenceIngestError("document_type is required")

    existing_source = (
        db.query(BidEvidenceDocument)
        .filter(
            BidEvidenceDocument.project_id == project.id,
            BidEvidenceDocument.source_file_id == source.id,
        )
        .one_or_none()
    )
    if existing_source is not None:
        if (
            existing_source.document_key != normalized_key
            or existing_source.document_type != normalized_type
        ):
            raise TenderEvidenceIngestConflict(
                "source file was already ingested with another document identity"
            )
        return _existing_result(db, project, existing_source)

    existing_hash = (
        db.query(BidEvidenceDocument)
        .filter(
            BidEvidenceDocument.project_id == project.id,
            BidEvidenceDocument.document_key == normalized_key,
            BidEvidenceDocument.sha256 == source.sha256.lower(),
            BidEvidenceDocument.parser_version == source.parser_version,
        )
        .order_by(BidEvidenceDocument.version_no.desc())
        .first()
    )
    if existing_hash is not None:
        return _existing_result(db, project, existing_hash)

    blocks = normalize_source_blocks(source)
    if not blocks:
        raise TenderEvidenceIngestError(
            "source file contains no usable parsed evidence blocks"
        )

    current_documents = (
        db.query(BidEvidenceDocument)
        .filter(
            BidEvidenceDocument.project_id == project.id,
            BidEvidenceDocument.document_key == normalized_key,
        )
        .order_by(BidEvidenceDocument.version_no.asc())
        .all()
    )
    next_version = (
        max((item.version_no for item in current_documents), default=0) + 1
    )
    now = datetime.now(timezone.utc)
    for item in current_documents:
        if item.active:
            item.active = False
            item.superseded_at = now

    document_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"tender-evidence-document:{project.project_uuid}:"
                f"{normalized_key}:{next_version}:{source.sha256.lower()}"
            ),
        )
    )
    prepared_blocks: list[dict[str, Any]] = []
    for item in blocks:
        content_hash = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        evidence_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"tender-evidence-block:{document_uuid}:"
                f"{item.block_order}:{content_hash}"
            ),
        )
        prepared_blocks.append(
            {
                "item": item,
                "evidence_id": f"EV-{evidence_uuid}",
                "block_id": f"BLK-{evidence_uuid}",
                "content_hash": content_hash,
            }
        )

    should_externalize = (
        (
            settings.tender_evidence_body_storage_enabled
            and settings.minio_enabled
        )
        if externalize_body is None
        else bool(externalize_body)
    )
    stored_body = None
    if should_externalize:
        resolved_body_storage = body_storage or MinioTenderEvidenceBodyStorage()
        source_segments = _load_source_segments(source)
        package_bytes, package_sha256 = build_evidence_body_package(
            case_id=project.project_uuid,
            document_id=document_uuid,
            document_key=normalized_key,
            document_version=next_version,
            source_file_uuid=source.file_uuid,
            source_sha256=source.sha256.lower(),
            parser_version=source.parser_version,
            extracted_text=source.extracted_text or "",
            source_segments=source_segments,
            blocks=[
                {
                    "evidence_id": str(item["evidence_id"]),
                    "block_id": str(item["block_id"]),
                    "block_order": item["item"].block_order,
                    "content_hash": str(item["content_hash"]),
                    "content": item["item"].content,
                    "locator": item["item"].locator_json,
                    "keywords": list(item["item"].keywords),
                }
                for item in prepared_blocks
            ],
        )
        try:
            stored_body = resolved_body_storage.put(
                case_id=project.project_uuid,
                document_id=document_uuid,
                content=package_bytes,
                sha256=package_sha256,
            )
        except TenderEvidenceBodyError as exc:
            raise TenderEvidenceIngestError(
                "parsed tender evidence body could not be externalized"
            ) from exc

    evidence_document = BidEvidenceDocument(
        evidence_document_uuid=document_uuid,
        project_id=project.id,
        source_file_id=source.id,
        document_key=normalized_key,
        document_type=normalized_type,
        version_no=next_version,
        original_filename=source.original_filename,
        sha256=source.sha256.lower(),
        parser_version=source.parser_version,
        body_storage_backend=(
            stored_body.backend
            if stored_body is not None
            else BODY_STORAGE_BACKEND_MYSQL
        ),
        body_bucket=stored_body.bucket if stored_body is not None else None,
        body_object_name=(
            stored_body.object_name if stored_body is not None else None
        ),
        body_sha256=stored_body.sha256 if stored_body is not None else None,
        body_size_bytes=(
            stored_body.size_bytes if stored_body is not None else 0
        ),
        body_schema_version=(
            stored_body.schema_version if stored_body is not None else None
        ),
        parse_status="ready",
        active=True,
        created_by=created_by or source.uploaded_by,
        activated_at=now,
    )
    db.add(evidence_document)
    db.flush()

    for prepared in prepared_blocks:
        item = prepared["item"]
        db.add(
            BidEvidenceBlock(
                evidence_id=str(prepared["evidence_id"]),
                block_id=str(prepared["block_id"]),
                project_id=project.id,
                document_id=evidence_document.id,
                block_order=item.block_order,
                page=item.page,
                sheet=item.sheet,
                cell_range=item.cell_range,
                section=item.section,
                locator_json=_dump_json(item.locator_json),
                content_hash=str(prepared["content_hash"]),
                content=None if stored_body is not None else item.content,
                content_length=len(item.content),
                keywords_json=_dump_json(list(item.keywords)),
            )
        )
    if stored_body is not None:
        source.parsed_artifact_bucket = stored_body.bucket
        source.parsed_artifact_object_name = stored_body.object_name
        source.parsed_artifact_sha256 = stored_body.sha256
        source.parsed_artifact_size_bytes = stored_body.size_bytes
        source.parsed_artifact_schema_version = stored_body.schema_version
        source.extracted_text = None
        source.segments_json = None
    db.flush()

    manifest = _create_manifest_snapshot(
        db,
        project=project,
        created_by=created_by or source.uploaded_by,
        now=now,
    )
    db.flush()
    active_block_count = _active_block_count(db, project.id)
    index_job = ensure_evidence_index_job(
        db,
        project_id=project.id,
        manifest=manifest,
        requested_block_count=active_block_count,
        created_by=created_by or source.uploaded_by,
    )
    return TenderEvidenceIngestResult(
        case_id=project.project_uuid,
        evidence_document_uuid=document_uuid,
        document_key=normalized_key,
        document_version=next_version,
        manifest_version=manifest.version_no,
        manifest_hash=manifest.manifest_hash,
        block_count=len(blocks),
        index_job_uuid=index_job.job_uuid,
        idempotent=False,
    )


def normalize_source_blocks(source: BidProjectFile) -> list[NormalizedBlock]:
    segments = _load_source_segments(source)

    raw_blocks: list[dict[str, Any]] = []
    if isinstance(segments, list):
        raw_blocks.extend(item for item in segments if isinstance(item, dict))
    if not raw_blocks and _clean_text(source.extracted_text):
        raw_blocks = [
            {
                "text": paragraph,
                "source_location": f"文本片段{index}",
            }
            for index, paragraph in enumerate(
                _split_paragraphs(source.extracted_text or ""),
                start=1,
            )
        ]

    normalized: list[NormalizedBlock] = []
    next_order = 0
    for raw in raw_blocks:
        content = _normalize_content(raw.get("text") or raw.get("content"))
        if not content:
            continue
        keywords = _normalize_keywords(raw.get("keywords"))
        locator = normalize_tender_segment_locator(raw)
        for chunk in _split_long_content(content):
            normalized.append(
                NormalizedBlock(
                    block_order=next_order,
                    content=chunk,
                    page=locator.page,
                    sheet=locator.sheet,
                    cell_range=locator.cell_range,
                    section=locator.section,
                    locator_json=locator.to_json_dict(),
                    keywords=keywords,
                )
            )
            next_order += 1
    return normalized


def _load_source_segments(source: BidProjectFile) -> list[dict[str, Any]]:
    try:
        segments = json.loads(source.segments_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(segments, list):
        return []
    return [item for item in segments if isinstance(item, dict)]


def normalize_document_key(value: str) -> str:
    normalized = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff._-]+",
        "-",
        _clean_text(value),
    ).strip("._-")
    if not normalized:
        raise TenderEvidenceIngestError("document_key is invalid")
    return normalized[:160]


def _default_document_key(source: BidProjectFile) -> str:
    stem = Path(source.original_filename or "").stem
    return f"{source.file_type}-{stem}"


def _existing_result(
    db: Session,
    project: BidProject,
    document: BidEvidenceDocument,
) -> TenderEvidenceIngestResult:
    manifest = (
        db.query(BidEvidenceManifest)
        .filter(
            BidEvidenceManifest.project_id == project.id,
            BidEvidenceManifest.active.is_(True),
        )
        .order_by(BidEvidenceManifest.version_no.desc())
        .first()
    )
    if manifest is None:
        raise TenderEvidenceIngestConflict(
            "ingested evidence document has no active manifest"
        )
    block_count = (
        db.query(func.count(BidEvidenceBlock.id))
        .filter(BidEvidenceBlock.document_id == document.id)
        .scalar()
        or 0
    )
    active_block_count = _active_block_count(db, project.id)
    index_job = ensure_evidence_index_job(
        db,
        project_id=project.id,
        manifest=manifest,
        requested_block_count=active_block_count,
        created_by=document.created_by,
    )
    return TenderEvidenceIngestResult(
        case_id=project.project_uuid,
        evidence_document_uuid=document.evidence_document_uuid,
        document_key=document.document_key,
        document_version=document.version_no,
        manifest_version=manifest.version_no,
        manifest_hash=manifest.manifest_hash,
        block_count=int(block_count),
        index_job_uuid=index_job.job_uuid,
        idempotent=True,
    )


def _active_block_count(db: Session, project_id: int) -> int:
    return int(
        db.query(func.count(BidEvidenceBlock.id))
        .join(
            BidEvidenceDocument,
            BidEvidenceDocument.id == BidEvidenceBlock.document_id,
        )
        .filter(
            BidEvidenceBlock.project_id == project_id,
            BidEvidenceDocument.project_id == project_id,
            BidEvidenceDocument.active.is_(True),
            BidEvidenceDocument.parse_status != "failed",
        )
        .scalar()
        or 0
    )


def _create_manifest_snapshot(
    db: Session,
    *,
    project: BidProject,
    created_by: int,
    now: datetime,
) -> BidEvidenceManifest:
    documents = (
        db.query(BidEvidenceDocument)
        .filter(BidEvidenceDocument.project_id == project.id)
        .order_by(
            BidEvidenceDocument.document_key.asc(),
            BidEvidenceDocument.version_no.asc(),
            BidEvidenceDocument.id.asc(),
        )
        .all()
    )
    active_manifests = (
        db.query(BidEvidenceManifest)
        .filter(
            BidEvidenceManifest.project_id == project.id,
            BidEvidenceManifest.active.is_(True),
        )
        .all()
    )
    current_version = (
        db.query(func.max(BidEvidenceManifest.version_no))
        .filter(BidEvidenceManifest.project_id == project.id)
        .scalar()
        or 0
    )
    next_version = int(current_version) + 1
    manifest_core = {
        "case_id": project.project_uuid,
        "manifest_version": next_version,
        "documents": [
            {
                "document_id": item.evidence_document_uuid,
                "document_key": item.document_key,
                "file_name": item.original_filename,
                "document_type": item.document_type,
                "document_version": item.version_no,
                "sha256": item.sha256,
                "parse_status": item.parse_status,
                "active": bool(item.active),
            }
            for item in documents
        ],
    }
    canonical = json.dumps(
        manifest_core,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    snapshot = {**manifest_core, "manifest_hash": manifest_hash}
    for item in active_manifests:
        item.active = False
        item.superseded_at = now
    manifest_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"tender-evidence-manifest:{project.project_uuid}:"
                f"{next_version}:{manifest_hash}"
            ),
        )
    )
    manifest = BidEvidenceManifest(
        manifest_uuid=manifest_uuid,
        project_id=project.id,
        version_no=next_version,
        manifest_hash=manifest_hash,
        snapshot_json=_dump_json(snapshot),
        active=True,
        created_by=created_by,
    )
    db.add(manifest)
    return manifest


def _split_paragraphs(value: str) -> list[str]:
    return [
        item
        for item in (
            _normalize_content(part)
            for part in re.split(r"(?:\r?\n){2,}", value)
        )
        if item
    ]


def _split_long_content(value: str) -> Iterable[str]:
    for start in range(0, len(value), MAX_BLOCK_CHARACTERS):
        chunk = value[start : start + MAX_BLOCK_CHARACTERS].strip()
        if chunk:
            yield chunk


def _normalize_content(value: Any) -> str:
    return re.sub(r"[ \t\f\v]+", " ", str(value or "")).strip()


def _normalize_keywords(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    keywords: list[str] = []
    for item in value:
        normalized = _clean_text(item)[:80]
        if normalized and normalized not in keywords:
            keywords.append(normalized)
    return tuple(keywords[:50])


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _dump_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
