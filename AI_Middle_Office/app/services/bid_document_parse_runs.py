"""Durable scheduling primitives for Phase 2 document parsing.

All helpers flush but never commit. Callers own the transaction so ParseRun,
ParseHead, internal history, Manifest changes, and Outbox events can be made
visible atomically.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.bid_assessment import BidDocumentVersion, BidFileObject
from app.models.bid_assessment_documents import (
    BidDocumentParseEvent,
    BidDocumentParseHead,
    BidDocumentParseRun,
)
from app.services.bid_assessment_eventing import canonical_hash


PARSER_STRATEGY_VERSION = "bid-document-parse-strategy-v1"


class BidDocumentParseRunError(RuntimeError):
    code = "BID_DOCUMENT_PARSE_RUN_INVALID"


class BidDocumentParseVersionNotFound(BidDocumentParseRunError):
    code = "BID_RESOURCE_NOT_FOUND"


@dataclass(frozen=True)
class BidDocumentParseSchedule:
    run: BidDocumentParseRun
    created: bool
    head_changed: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def document_parse_input_hash(
    *,
    content_sha256: str,
    size_bytes: int,
    parser_profile_version: str,
) -> str:
    """Hash only immutable content and frozen parsing policy inputs."""

    return canonical_hash(
        {
            "content_sha256": str(content_sha256).lower(),
            "size_bytes": int(size_bytes),
            "parser_profile_version": str(parser_profile_version),
            "strategy_version": PARSER_STRATEGY_VERSION,
        }
    )


def ensure_document_parse_run(
    db: Session,
    *,
    document_version_id: str,
    parser_profile_version: str,
    requested_at: datetime | None = None,
) -> BidDocumentParseSchedule:
    """Create or reuse the logical ParseRun and select it as authority."""

    profile_version = str(parser_profile_version).strip()
    if not profile_version or len(profile_version) > 80:
        raise BidDocumentParseRunError("BID_DOCUMENT_PARSER_PROFILE_INVALID")

    row = (
        db.query(BidDocumentVersion, BidFileObject)
        .join(BidFileObject, BidFileObject.id == BidDocumentVersion.file_object_id)
        .filter(BidDocumentVersion.id == document_version_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise BidDocumentParseVersionNotFound()
    version, file_object = row
    input_hash = document_parse_input_hash(
        content_sha256=str(file_object.sha256),
        size_bytes=int(file_object.size_bytes),
        parser_profile_version=profile_version,
    )
    run = (
        db.query(BidDocumentParseRun)
        .filter(
            BidDocumentParseRun.document_version_id == version.id,
            BidDocumentParseRun.parser_profile_version == profile_version,
            BidDocumentParseRun.input_hash == input_hash,
        )
        .with_for_update()
        .one_or_none()
    )
    created = run is None
    current_time = requested_at or _utc_now()
    if run is None:
        run = BidDocumentParseRun(
            id=f"dpr_{uuid.uuid4().hex}",
            document_version_id=str(version.id),
            parser_profile_version=profile_version,
            input_hash=input_hash,
            status="queued",
            retryable=True,
            requested_at=current_time,
            page_count=0,
            sheet_count=0,
            ocr_status="not_requested",
            warning_count=0,
            row_version=1,
        )
        db.add(run)
        db.flush()
        payload = {
            "status": "queued",
            "document_version_id": str(version.id),
            "parser_profile_version": profile_version,
            "input_hash": input_hash,
        }
        db.add(
            BidDocumentParseEvent(
                id=f"dpe_{uuid.uuid4().hex}",
                run_id=str(run.id),
                attempt_id=None,
                sequence_no=1,
                event_type="parse.requested",
                from_status=None,
                to_status="queued",
                payload_json=payload,
                payload_hash=canonical_hash(payload),
            )
        )
        db.flush()

    head = (
        db.query(BidDocumentParseHead)
        .filter(BidDocumentParseHead.document_version_id == version.id)
        .with_for_update()
        .one_or_none()
    )
    head_changed = head is None or str(head.current_run_id) != str(run.id)
    if head is None:
        db.add(
            BidDocumentParseHead(
                document_version_id=str(version.id),
                current_run_id=str(run.id),
                row_version=1,
            )
        )
    elif head_changed:
        head.current_run_id = str(run.id)
        head.row_version = int(head.row_version) + 1
    db.flush()
    return BidDocumentParseSchedule(
        run=run,
        created=created,
        head_changed=head_changed,
    )

