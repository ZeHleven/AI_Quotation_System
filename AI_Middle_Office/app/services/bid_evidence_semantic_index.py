"""RQ2-A Child-only semantic-index lifecycle and recall.

The semantic index is an immutable derivative of one ready PDF-C3 retrieval
index.  Database rows freeze lineage and vector hashes; a controlled provider
owns the actual vectors.  Provider calls happen outside database transactions
and are fenced by a durable lease plus content-addressed request identities.
"""
from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.bid_assessment_retrieval import (
    BidEvidenceRetrievalEntry,
    BidEvidenceRetrievalHead,
    BidEvidenceRetrievalIndex,
)
from app.models.bid_assessment_semantic import (
    BidEvidenceSemanticEntry,
    BidEvidenceSemanticHead,
    BidEvidenceSemanticIndex,
)
from app.services.bid_assessment_eventing import as_utc, canonical_hash
from app.services.bid_evidence_chunk_builder import normalize_evidence_text
from app.services.bid_evidence_retrieval_index import (
    ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_semantic_vector_provider import (
    BidSemanticProviderError,
    BidSemanticProviderInvalid,
    BidSemanticVectorProvider,
    SemanticDocument,
    SemanticModelDescriptor,
    SemanticProviderHit,
    SemanticVectorReceipt,
    validate_descriptor,
)


SEMANTIC_INDEX_CONTRACT_VERSION = "bid.evidence.semantic-index.v1"
DISABLED_SEMANTIC_PROFILE_VERSION = "bid-evidence-semantic-profile-v0-disabled"
RQ2A_SEMANTIC_PROFILE_VERSION = "bid-evidence-semantic-profile-v1-rq2a-bce"
SEMANTIC_INDEX_CONSUMER = "bid-evidence-semantic-index-coordinator-v1"
SEMANTIC_QUERY_RRF_K = 60
SEMANTIC_QUERY_CANDIDATE_DEPTH = 40
SEMANTIC_UPSERT_BATCH_SIZE = 64


class BidEvidenceSemanticIndexError(RuntimeError):
    code = "BID_EVIDENCE_SEMANTIC_INDEX_ERROR"


class BidEvidenceSemanticIndexInvalid(BidEvidenceSemanticIndexError):
    code = "BID_EVIDENCE_SEMANTIC_INDEX_INVALID"


class BidEvidenceSemanticIndexNotReady(BidEvidenceSemanticIndexError):
    code = "BID_EVIDENCE_SEMANTIC_INDEX_NOT_READY"


class BidEvidenceSemanticIndexStale(BidEvidenceSemanticIndexError):
    code = "BID_EVIDENCE_SEMANTIC_INDEX_STALE"


class BidEvidenceSemanticFenceLost(BidEvidenceSemanticIndexError):
    code = "BID_EVIDENCE_SEMANTIC_FENCE_LOST"


@dataclass(frozen=True)
class BidEvidenceSemanticIndexSchedule:
    semantic_index_id: str
    retrieval_index_id: str
    status: str
    created: bool


@dataclass(frozen=True)
class BidEvidenceSemanticIndexClaim:
    semantic_index_id: str
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class BidEvidenceSemanticIndexBuild:
    semantic_index_id: str
    retrieval_index_id: str
    status: str
    entry_count: int
    result_hash: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class BidEvidenceSemanticIndexBatch:
    scanned: int
    ready: int
    retry_wait: int
    stale: int
    failed: int


@dataclass(frozen=True)
class _PreparedDocument:
    semantic_document: SemanticDocument
    retrieval_entry_id: str
    retrieval_index_id: str
    retrieval_child_id: str
    ordinal: int


@dataclass(frozen=True)
class _PreparedBuild:
    semantic_index_id: str
    retrieval_index_id: str
    document_version_id: str
    vector_namespace: str
    provider_request_id: str
    input_hash: str
    source_result_hash: str
    documents: tuple[_PreparedDocument, ...]


@dataclass(frozen=True)
class SemanticRecallHit:
    retrieval_child_id: str
    retrieval_child_key: str
    semantic_index_id: str
    retrieval_index_id: str
    semantic_score: float
    rank_score: float
    matched_queries: tuple[str, ...]
    vector_hash: str


@dataclass(frozen=True)
class SemanticRecallResult:
    semantic_profile_version: str
    semantic_index_set_hash: str
    model_descriptor: SemanticModelDescriptor
    hits: tuple[SemanticRecallHit, ...]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def semantic_index_input_hash(
    retrieval_index: BidEvidenceRetrievalIndex,
    descriptor: SemanticModelDescriptor,
) -> str:
    validate_descriptor(descriptor)
    if (
        str(retrieval_index.status) != "ready"
        or not retrieval_index.result_hash
        or str(retrieval_index.retrieval_profile_version)
        != ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
    ):
        raise BidEvidenceSemanticIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_INDEX_NOT_READY"
        )
    return canonical_hash(
        {
            "contract_version": SEMANTIC_INDEX_CONTRACT_VERSION,
            "semantic_profile_version": RQ2A_SEMANTIC_PROFILE_VERSION,
            "source_retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            "retrieval_index_id": str(retrieval_index.id),
            "document_version_id": str(retrieval_index.document_version_id),
            "source_result_hash": str(retrieval_index.result_hash),
            "source_entry_count": int(retrieval_index.entry_count),
            "model": descriptor.stable_payload(),
            "embedding_input": "pdf_c3_retrieval_entry.retrieval_text",
        }
    )


def semantic_vector_namespace(input_hash: str) -> str:
    if len(str(input_hash)) != 64:
        raise BidEvidenceSemanticIndexInvalid("BID_SEMANTIC_INPUT_HASH_INVALID")
    return f"bid-sem-{input_hash}"


def _provider_request_id(input_hash: str) -> str:
    return f"bid-semantic-index:{input_hash}"


def _descriptor_matches(
    row: BidEvidenceSemanticIndex,
    descriptor: SemanticModelDescriptor,
) -> bool:
    return (
        str(row.provider_id) == descriptor.provider_id
        and str(row.embedding_model_id) == descriptor.model_id
        and str(row.embedding_model_revision) == descriptor.model_revision
        and int(row.embedding_dimension) == descriptor.dimension
        and str(row.distance_metric).upper() == descriptor.distance_metric.upper()
        and bool(row.normalized_embeddings) == descriptor.normalized_embeddings
    )


def ensure_semantic_index(
    db: Session,
    *,
    retrieval_index: BidEvidenceRetrievalIndex,
    descriptor: SemanticModelDescriptor,
    max_attempts: int = 5,
    now: datetime | None = None,
) -> BidEvidenceSemanticIndexSchedule:
    validate_descriptor(descriptor)
    input_hash = semantic_index_input_hash(retrieval_index, descriptor)
    existing = (
        db.query(BidEvidenceSemanticIndex)
        .filter(
            BidEvidenceSemanticIndex.retrieval_index_id
            == str(retrieval_index.id),
            BidEvidenceSemanticIndex.semantic_profile_version
            == RQ2A_SEMANTIC_PROFILE_VERSION,
        )
        .one_or_none()
    )
    if existing is not None:
        if (
            str(existing.document_version_id)
            != str(retrieval_index.document_version_id)
            or str(existing.source_result_hash) != str(retrieval_index.result_hash)
            or int(existing.source_entry_count) != int(retrieval_index.entry_count)
            or str(existing.input_hash) != input_hash
            or not _descriptor_matches(existing, descriptor)
        ):
            raise BidEvidenceSemanticIndexInvalid(
                "BID_EVIDENCE_SEMANTIC_INDEX_IDEMPOTENCY_CONFLICT"
            )
        return BidEvidenceSemanticIndexSchedule(
            semantic_index_id=str(existing.id),
            retrieval_index_id=str(existing.retrieval_index_id),
            status=str(existing.status),
            created=False,
        )
    created_at = now or database_utc_now(db)
    row = BidEvidenceSemanticIndex(
        id=str(uuid.uuid4()),
        document_version_id=str(retrieval_index.document_version_id),
        retrieval_index_id=str(retrieval_index.id),
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        semantic_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        provider_id=descriptor.provider_id,
        embedding_model_id=descriptor.model_id,
        embedding_model_revision=descriptor.model_revision,
        embedding_dimension=descriptor.dimension,
        distance_metric=descriptor.distance_metric,
        normalized_embeddings=1 if descriptor.normalized_embeddings else 0,
        vector_namespace=semantic_vector_namespace(input_hash),
        provider_request_id=_provider_request_id(input_hash),
        source_result_hash=str(retrieval_index.result_hash),
        source_entry_count=int(retrieval_index.entry_count),
        input_hash=input_hash,
        status="queued",
        entry_count=0,
        attempt_count=0,
        max_attempts=max(1, min(int(max_attempts), 20)),
        fencing_token=0,
        row_version=1,
        requested_at=created_at,
    )
    db.add(row)
    db.flush()
    return BidEvidenceSemanticIndexSchedule(
        semantic_index_id=str(row.id),
        retrieval_index_id=str(row.retrieval_index_id),
        status=str(row.status),
        created=True,
    )


def invalidate_stale_semantic_indexes(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    changed = 0
    invalidated_at = now or database_utc_now(db)
    rows = (
        db.query(BidEvidenceSemanticIndex)
        .filter(BidEvidenceSemanticIndex.status.in_(("queued", "building", "ready", "failed")))
        .with_for_update()
        .all()
    )
    for row in rows:
        source = (
            db.query(BidEvidenceRetrievalIndex)
            .filter(BidEvidenceRetrievalIndex.id == str(row.retrieval_index_id))
            .one_or_none()
        )
        head = (
            db.query(BidEvidenceRetrievalHead)
            .filter(
                BidEvidenceRetrievalHead.document_version_id
                == str(row.document_version_id),
                BidEvidenceRetrievalHead.retrieval_profile_version
                == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            )
            .one_or_none()
        )
        stale = (
            source is None
            or head is None
            or str(source.status) != "ready"
            or not source.result_hash
            or str(head.current_index_id) != str(row.retrieval_index_id)
            or str(source.result_hash) != str(row.source_result_hash)
        )
        if not stale:
            continue
        row.status = "stale"
        row.invalidated_at = invalidated_at
        row.finished_at = row.finished_at or invalidated_at
        row.lease_owner = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.row_version = int(row.row_version) + 1
        semantic_head = (
            db.query(BidEvidenceSemanticHead)
            .filter(
                BidEvidenceSemanticHead.document_version_id
                == str(row.document_version_id),
                BidEvidenceSemanticHead.semantic_profile_version
                == RQ2A_SEMANTIC_PROFILE_VERSION,
                BidEvidenceSemanticHead.current_semantic_index_id == str(row.id),
            )
            .one_or_none()
        )
        if semantic_head is not None:
            db.delete(semantic_head)
        changed += 1
    db.flush()
    return changed


def reconcile_current_retrieval_heads(
    db: Session,
    *,
    descriptor: SemanticModelDescriptor,
    max_attempts: int = 5,
    limit: int = 500,
    now: datetime | None = None,
) -> int:
    validate_descriptor(descriptor)
    heads = (
        db.query(BidEvidenceRetrievalHead)
        .filter(
            BidEvidenceRetrievalHead.retrieval_profile_version
            == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
        )
        .order_by(BidEvidenceRetrievalHead.document_version_id.asc())
        .limit(max(1, min(int(limit), 5000)))
        .all()
    )
    created = 0
    for head in heads:
        source = (
            db.query(BidEvidenceRetrievalIndex)
            .filter(
                BidEvidenceRetrievalIndex.id == str(head.current_index_id),
                BidEvidenceRetrievalIndex.document_version_id
                == str(head.document_version_id),
                BidEvidenceRetrievalIndex.status == "ready",
            )
            .one_or_none()
        )
        if source is None:
            continue
        scheduled = ensure_semantic_index(
            db,
            retrieval_index=source,
            descriptor=descriptor,
            max_attempts=max_attempts,
            now=now,
        )
        created += int(scheduled.created)
    return created


def _claim_semantic_index(
    *,
    session_factory: Callable[[], Session],
    semantic_index_id: str,
    worker_id: str,
    lease_seconds: int,
) -> BidEvidenceSemanticIndexClaim | None:
    db = session_factory()
    try:
        with db.begin():
            row = (
                db.query(BidEvidenceSemanticIndex)
                .filter(BidEvidenceSemanticIndex.id == str(semantic_index_id))
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise BidEvidenceSemanticIndexInvalid(
                    "BID_EVIDENCE_SEMANTIC_INDEX_NOT_FOUND"
                )
            now = database_utc_now(db)
            if str(row.status) in {"ready", "failed", "stale"}:
                return None
            if (
                str(row.status) == "building"
                and row.lease_expires_at is not None
                and as_utc(row.lease_expires_at) > now
            ):
                return None
            if int(row.attempt_count) >= int(row.max_attempts):
                row.status = "failed"
                row.error_code = "BID_EVIDENCE_SEMANTIC_MAX_ATTEMPTS"
                row.finished_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                row.heartbeat_at = None
                row.row_version = int(row.row_version) + 1
                return None
            row.status = "building"
            row.attempt_count = int(row.attempt_count) + 1
            row.fencing_token = int(row.fencing_token) + 1
            row.lease_owner = str(worker_id)[:128]
            row.lease_expires_at = now + timedelta(
                seconds=max(60, min(int(lease_seconds), 3600))
            )
            row.heartbeat_at = now
            row.started_at = row.started_at or now
            row.finished_at = None
            row.error_code = None
            row.row_version = int(row.row_version) + 1
            db.flush()
            return BidEvidenceSemanticIndexClaim(
                semantic_index_id=str(row.id),
                worker_id=str(row.lease_owner),
                fencing_token=int(row.fencing_token),
                lease_expires_at=as_utc(row.lease_expires_at),
            )
    finally:
        db.close()


def _prepare_build(
    *,
    session_factory: Callable[[], Session],
    claim: BidEvidenceSemanticIndexClaim,
    descriptor: SemanticModelDescriptor,
) -> _PreparedBuild:
    db = session_factory()
    try:
        row = (
            db.query(BidEvidenceSemanticIndex)
            .filter(BidEvidenceSemanticIndex.id == claim.semantic_index_id)
            .one_or_none()
        )
        now = database_utc_now(db)
        if (
            row is None
            or str(row.status) != "building"
            or str(row.lease_owner) != claim.worker_id
            or int(row.fencing_token) != claim.fencing_token
            or row.lease_expires_at is None
            or as_utc(row.lease_expires_at) <= now
        ):
            raise BidEvidenceSemanticFenceLost(
                "BID_EVIDENCE_SEMANTIC_FENCE_LOST"
            )
        source = (
            db.query(BidEvidenceRetrievalIndex)
            .filter(BidEvidenceRetrievalIndex.id == str(row.retrieval_index_id))
            .one_or_none()
        )
        head = (
            db.query(BidEvidenceRetrievalHead)
            .filter(
                BidEvidenceRetrievalHead.document_version_id
                == str(row.document_version_id),
                BidEvidenceRetrievalHead.retrieval_profile_version
                == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            )
            .one_or_none()
        )
        if (
            source is None
            or head is None
            or str(source.status) != "ready"
            or str(head.current_index_id) != str(source.id)
            or str(source.id) != str(row.retrieval_index_id)
            or str(source.result_hash or "") != str(row.source_result_hash)
        ):
            raise BidEvidenceSemanticIndexStale(
                "BID_EVIDENCE_SEMANTIC_SOURCE_STALE"
            )
        expected_input_hash = semantic_index_input_hash(source, descriptor)
        if (
            str(row.input_hash) != expected_input_hash
            or str(row.vector_namespace)
            != semantic_vector_namespace(expected_input_hash)
            or str(row.provider_request_id)
            != _provider_request_id(expected_input_hash)
            or not _descriptor_matches(row, descriptor)
        ):
            raise BidEvidenceSemanticIndexInvalid(
                "BID_EVIDENCE_SEMANTIC_INDEX_INVALID"
            )
        entries = (
            db.query(BidEvidenceRetrievalEntry)
            .filter(BidEvidenceRetrievalEntry.index_id == str(source.id))
            .order_by(
                BidEvidenceRetrievalEntry.ordinal.asc(),
                BidEvidenceRetrievalEntry.retrieval_child_key.asc(),
            )
            .all()
        )
        if len(entries) != int(source.entry_count) or not entries:
            raise BidEvidenceSemanticIndexInvalid(
                "BID_EVIDENCE_SEMANTIC_SOURCE_INVALID"
            )
        documents: list[_PreparedDocument] = []
        for entry in entries:
            text = normalize_evidence_text(str(entry.retrieval_text))
            if not text:
                raise BidEvidenceSemanticIndexInvalid(
                    "BID_EVIDENCE_SEMANTIC_TEXT_EMPTY"
                )
            text_hash = _sha256_text(text)
            record_id = canonical_hash(
                {
                    "contract_version": SEMANTIC_INDEX_CONTRACT_VERSION,
                    "semantic_profile_version": RQ2A_SEMANTIC_PROFILE_VERSION,
                    "semantic_input_hash": str(row.input_hash),
                    "vector_namespace": str(row.vector_namespace),
                    "retrieval_child_key": str(entry.retrieval_child_key),
                    "source_entry_hash": str(entry.entry_hash),
                    "embedding_text_hash": text_hash,
                }
            )
            documents.append(
                _PreparedDocument(
                    semantic_document=SemanticDocument(
                        provider_record_id=record_id,
                        retrieval_child_id=str(entry.retrieval_child_id),
                        retrieval_child_key=str(entry.retrieval_child_key),
                        source_entry_hash=str(entry.entry_hash),
                        embedding_text_hash=text_hash,
                        text=text,
                    ),
                    retrieval_entry_id=str(entry.id),
                    retrieval_index_id=str(entry.index_id),
                    retrieval_child_id=str(entry.retrieval_child_id),
                    ordinal=int(entry.ordinal),
                )
            )
        return _PreparedBuild(
            semantic_index_id=str(row.id),
            retrieval_index_id=str(source.id),
            document_version_id=str(row.document_version_id),
            vector_namespace=str(row.vector_namespace),
            provider_request_id=str(row.provider_request_id),
            input_hash=str(row.input_hash),
            source_result_hash=str(row.source_result_hash),
            documents=tuple(documents),
        )
    finally:
        db.close()


def heartbeat_semantic_index(
    *,
    session_factory: Callable[[], Session],
    claim: BidEvidenceSemanticIndexClaim,
    lease_seconds: int,
) -> datetime:
    """Extend one live build lease without holding provider I/O in a transaction."""

    db = session_factory()
    try:
        with db.begin():
            row = (
                db.query(BidEvidenceSemanticIndex)
                .filter(BidEvidenceSemanticIndex.id == claim.semantic_index_id)
                .with_for_update()
                .one_or_none()
            )
            now = database_utc_now(db)
            if (
                row is None
                or str(row.status) != "building"
                or str(row.lease_owner) != claim.worker_id
                or int(row.fencing_token) != claim.fencing_token
                or row.lease_expires_at is None
                or as_utc(row.lease_expires_at) <= now
            ):
                raise BidEvidenceSemanticFenceLost(
                    "BID_EVIDENCE_SEMANTIC_FENCE_LOST"
                )
            lease_until = now + timedelta(
                seconds=max(60, min(int(lease_seconds), 3600))
            )
            row.lease_expires_at = lease_until
            row.heartbeat_at = now
            row.row_version = int(row.row_version) + 1
            db.flush()
            return lease_until
    finally:
        db.close()


def _validate_receipts(
    prepared: _PreparedBuild,
    receipts: Sequence[SemanticVectorReceipt],
    descriptor: SemanticModelDescriptor,
) -> dict[str, SemanticVectorReceipt]:
    by_record = {row.provider_record_id: row for row in receipts}
    expected = {
        row.semantic_document.provider_record_id: row.semantic_document
        for row in prepared.documents
    }
    if len(by_record) != len(receipts) or set(by_record) != set(expected):
        raise BidEvidenceSemanticIndexInvalid(
            "BID_EVIDENCE_SEMANTIC_PROVIDER_RECEIPT_INVALID"
        )
    for record_id, document in expected.items():
        receipt = by_record[record_id]
        if (
            receipt.retrieval_child_key != document.retrieval_child_key
            or receipt.source_entry_hash != document.source_entry_hash
            or receipt.embedding_text_hash != document.embedding_text_hash
            or len(str(receipt.vector_hash)) != 64
            or int(receipt.vector_dimension) != descriptor.dimension
        ):
            raise BidEvidenceSemanticIndexInvalid(
                "BID_EVIDENCE_SEMANTIC_PROVIDER_RECEIPT_INVALID"
            )
    return by_record


def _complete_build(
    *,
    session_factory: Callable[[], Session],
    claim: BidEvidenceSemanticIndexClaim,
    prepared: _PreparedBuild,
    receipts: Sequence[SemanticVectorReceipt],
    descriptor: SemanticModelDescriptor,
) -> BidEvidenceSemanticIndexBuild:
    receipt_by_record = _validate_receipts(prepared, receipts, descriptor)
    db = session_factory()
    try:
        with db.begin():
            row = (
                db.query(BidEvidenceSemanticIndex)
                .filter(BidEvidenceSemanticIndex.id == claim.semantic_index_id)
                .with_for_update()
                .one_or_none()
            )
            now = database_utc_now(db)
            if (
                row is None
                or str(row.status) != "building"
                or str(row.lease_owner) != claim.worker_id
                or int(row.fencing_token) != claim.fencing_token
                or row.lease_expires_at is None
                or as_utc(row.lease_expires_at) <= now
            ):
                raise BidEvidenceSemanticFenceLost(
                    "BID_EVIDENCE_SEMANTIC_FENCE_LOST"
                )
            source_head = (
                db.query(BidEvidenceRetrievalHead)
                .filter(
                    BidEvidenceRetrievalHead.document_version_id
                    == prepared.document_version_id,
                    BidEvidenceRetrievalHead.retrieval_profile_version
                    == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                )
                .with_for_update()
                .one_or_none()
            )
            source = (
                db.query(BidEvidenceRetrievalIndex)
                .filter(
                    BidEvidenceRetrievalIndex.id == prepared.retrieval_index_id
                )
                .one_or_none()
            )
            if (
                source_head is None
                or source is None
                or str(source_head.current_index_id) != prepared.retrieval_index_id
                or str(source.status) != "ready"
                or str(source.result_hash or "") != prepared.source_result_hash
                or semantic_index_input_hash(source, descriptor)
                != prepared.input_hash
            ):
                raise BidEvidenceSemanticIndexStale(
                    "BID_EVIDENCE_SEMANTIC_SOURCE_STALE"
                )
            existing_count = (
                db.query(BidEvidenceSemanticEntry)
                .filter(
                    BidEvidenceSemanticEntry.semantic_index_id
                    == prepared.semantic_index_id
                )
                .count()
            )
            if existing_count:
                raise BidEvidenceSemanticIndexInvalid(
                    "BID_EVIDENCE_SEMANTIC_PARTIAL_ROWS_PRESENT"
                )
            stable_entries: list[dict[str, object]] = []
            for document in sorted(
                prepared.documents,
                key=lambda item: (
                    item.ordinal,
                    item.semantic_document.retrieval_child_key,
                ),
            ):
                semantic_document = document.semantic_document
                receipt = receipt_by_record[semantic_document.provider_record_id]
                entry_payload = {
                    "retrieval_child_key": semantic_document.retrieval_child_key,
                    "source_entry_hash": semantic_document.source_entry_hash,
                    "embedding_text_hash": semantic_document.embedding_text_hash,
                    "provider_record_id": semantic_document.provider_record_id,
                    "vector_hash": receipt.vector_hash,
                    "vector_dimension": receipt.vector_dimension,
                    "ordinal": document.ordinal,
                }
                entry_hash = canonical_hash(entry_payload)
                db.add(
                    BidEvidenceSemanticEntry(
                        id=str(uuid.uuid4()),
                        semantic_index_id=prepared.semantic_index_id,
                        retrieval_index_id=document.retrieval_index_id,
                        retrieval_entry_id=document.retrieval_entry_id,
                        retrieval_child_id=document.retrieval_child_id,
                        retrieval_child_key=semantic_document.retrieval_child_key,
                        source_entry_hash=semantic_document.source_entry_hash,
                        embedding_text_hash=semantic_document.embedding_text_hash,
                        provider_record_id=semantic_document.provider_record_id,
                        vector_hash=receipt.vector_hash,
                        vector_dimension=receipt.vector_dimension,
                        ordinal=document.ordinal,
                        entry_hash=entry_hash,
                    )
                )
                stable_entries.append({**entry_payload, "entry_hash": entry_hash})
            result_payload = {
                "contract_version": SEMANTIC_INDEX_CONTRACT_VERSION,
                "semantic_profile_version": RQ2A_SEMANTIC_PROFILE_VERSION,
                "retrieval_index_id": prepared.retrieval_index_id,
                "source_result_hash": prepared.source_result_hash,
                "input_hash": prepared.input_hash,
                "vector_namespace": prepared.vector_namespace,
                "model": descriptor.stable_payload(),
                "entries": stable_entries,
            }
            result_hash = canonical_hash(result_payload)
            row.status = "ready"
            row.entry_count = len(stable_entries)
            row.result_hash = result_hash
            row.error_code = None
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.row_version = int(row.row_version) + 1
            head = (
                db.query(BidEvidenceSemanticHead)
                .filter(
                    BidEvidenceSemanticHead.document_version_id
                    == prepared.document_version_id,
                    BidEvidenceSemanticHead.semantic_profile_version
                    == RQ2A_SEMANTIC_PROFILE_VERSION,
                )
                .with_for_update()
                .one_or_none()
            )
            if head is None:
                head = BidEvidenceSemanticHead(
                    document_version_id=prepared.document_version_id,
                    semantic_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
                    current_semantic_index_id=prepared.semantic_index_id,
                    current_retrieval_index_id=prepared.retrieval_index_id,
                    row_version=1,
                )
                db.add(head)
            else:
                head.current_semantic_index_id = prepared.semantic_index_id
                head.current_retrieval_index_id = prepared.retrieval_index_id
                head.row_version = int(head.row_version) + 1
            db.flush()
            return BidEvidenceSemanticIndexBuild(
                semantic_index_id=prepared.semantic_index_id,
                retrieval_index_id=prepared.retrieval_index_id,
                status="ready",
                entry_count=len(stable_entries),
                result_hash=result_hash,
            )
    finally:
        db.close()


def _settle_error(
    *,
    session_factory: Callable[[], Session],
    claim: BidEvidenceSemanticIndexClaim,
    error_code: str,
    retryable: bool,
    stale: bool = False,
) -> BidEvidenceSemanticIndexBuild:
    db = session_factory()
    try:
        with db.begin():
            row = (
                db.query(BidEvidenceSemanticIndex)
                .filter(BidEvidenceSemanticIndex.id == claim.semantic_index_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise BidEvidenceSemanticIndexInvalid(
                    "BID_EVIDENCE_SEMANTIC_INDEX_NOT_FOUND"
                )
            if (
                str(row.status) != "building"
                or str(row.lease_owner) != claim.worker_id
                or int(row.fencing_token) != claim.fencing_token
            ):
                raise BidEvidenceSemanticFenceLost(
                    "BID_EVIDENCE_SEMANTIC_FENCE_LOST"
                )
            now = database_utc_now(db)
            if (
                row.lease_expires_at is None
                or as_utc(row.lease_expires_at) <= now
            ):
                raise BidEvidenceSemanticFenceLost(
                    "BID_EVIDENCE_SEMANTIC_FENCE_LOST"
                )
            if stale:
                row.status = "stale"
                row.invalidated_at = now
                row.finished_at = now
            elif retryable and int(row.attempt_count) < int(row.max_attempts):
                row.status = "queued"
                row.finished_at = None
            else:
                row.status = "failed"
                row.finished_at = now
            row.error_code = str(error_code or BidEvidenceSemanticIndexError.code)[:100]
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.row_version = int(row.row_version) + 1
            db.flush()
            return BidEvidenceSemanticIndexBuild(
                semantic_index_id=str(row.id),
                retrieval_index_id=str(row.retrieval_index_id),
                status=str(row.status),
                entry_count=int(row.entry_count),
                result_hash=str(row.result_hash) if row.result_hash else None,
                error_code=str(row.error_code),
            )
    finally:
        db.close()


def build_semantic_index(
    *,
    session_factory: Callable[[], Session],
    semantic_index_id: str,
    provider: BidSemanticVectorProvider,
    worker_id: str,
    lease_seconds: int = 900,
) -> BidEvidenceSemanticIndexBuild:
    validate_descriptor(provider.descriptor)
    claim = _claim_semantic_index(
        session_factory=session_factory,
        semantic_index_id=semantic_index_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    if claim is None:
        db = session_factory()
        try:
            row = (
                db.query(BidEvidenceSemanticIndex)
                .filter(BidEvidenceSemanticIndex.id == str(semantic_index_id))
                .one()
            )
            return BidEvidenceSemanticIndexBuild(
                semantic_index_id=str(row.id),
                retrieval_index_id=str(row.retrieval_index_id),
                status=str(row.status),
                entry_count=int(row.entry_count),
                result_hash=str(row.result_hash) if row.result_hash else None,
                error_code=str(row.error_code) if row.error_code else None,
            )
        finally:
            db.close()
    try:
        prepared = _prepare_build(
            session_factory=session_factory,
            claim=claim,
            descriptor=provider.descriptor,
        )
        receipts: list[SemanticVectorReceipt] = []
        heartbeat_semantic_index(
            session_factory=session_factory,
            claim=claim,
            lease_seconds=lease_seconds,
        )
        for batch_index, batch_start in enumerate(
            range(0, len(prepared.documents), SEMANTIC_UPSERT_BATCH_SIZE),
            1,
        ):
            batch = prepared.documents[
                batch_start : batch_start + SEMANTIC_UPSERT_BATCH_SIZE
            ]
            receipts.extend(
                provider.upsert_documents(
                    namespace=prepared.vector_namespace,
                    provider_request_id=(
                        f"{prepared.provider_request_id}:{batch_index:05d}"
                    ),
                    documents=[row.semantic_document for row in batch],
                )
            )
            heartbeat_semantic_index(
                session_factory=session_factory,
                claim=claim,
                lease_seconds=lease_seconds,
            )
        return _complete_build(
            session_factory=session_factory,
            claim=claim,
            prepared=prepared,
            receipts=receipts,
            descriptor=provider.descriptor,
        )
    except BidEvidenceSemanticIndexStale as exc:
        return _settle_error(
            session_factory=session_factory,
            claim=claim,
            error_code=str(exc) or exc.code,
            retryable=False,
            stale=True,
        )
    except BidSemanticProviderError as exc:
        return _settle_error(
            session_factory=session_factory,
            claim=claim,
            error_code=str(exc) or exc.code,
            retryable=bool(getattr(exc, "retryable", False)),
        )
    except BidEvidenceSemanticFenceLost:
        raise
    except Exception as exc:
        return _settle_error(
            session_factory=session_factory,
            claim=claim,
            error_code=(str(exc) or BidEvidenceSemanticIndexInvalid.code)[:100],
            retryable=False,
        )


def process_pending_semantic_indexes(
    *,
    provider: BidSemanticVectorProvider,
    session_factory: Callable[[], Session] = SessionLocal,
    worker_id: str = SEMANTIC_INDEX_CONSUMER,
    limit: int = 20,
    lease_seconds: int = 900,
    max_attempts: int = 5,
) -> BidEvidenceSemanticIndexBatch:
    validate_descriptor(provider.descriptor)
    maintenance_db = session_factory()
    try:
        with maintenance_db.begin():
            stale = invalidate_stale_semantic_indexes(maintenance_db)
            reconcile_current_retrieval_heads(
                maintenance_db,
                descriptor=provider.descriptor,
                max_attempts=max_attempts,
                limit=max(1, min(int(limit) * 10, 5000)),
            )
            now = database_utc_now(maintenance_db)
            candidate_rows = (
                maintenance_db.query(BidEvidenceSemanticIndex)
                .filter(BidEvidenceSemanticIndex.status.in_(("queued", "building")))
                .order_by(BidEvidenceSemanticIndex.requested_at.asc())
                .limit(max(1, min(int(limit) * 3, 300)))
                .all()
            )
            index_ids = []
            for row in candidate_rows:
                if (
                    str(row.status) == "building"
                    and row.lease_expires_at is not None
                    and as_utc(row.lease_expires_at) > now
                ):
                    continue
                index_ids.append(str(row.id))
                if len(index_ids) >= max(1, min(int(limit), 100)):
                    break
    finally:
        maintenance_db.close()
    ready = retry_wait = failed = 0
    for semantic_index_id in index_ids:
        result = build_semantic_index(
            session_factory=session_factory,
            semantic_index_id=semantic_index_id,
            provider=provider,
            worker_id=f"{worker_id}:{semantic_index_id}"[:128],
            lease_seconds=lease_seconds,
        )
        if result.status == "ready":
            ready += 1
        elif result.status == "queued":
            retry_wait += 1
        elif result.status == "stale":
            stale += 1
        elif result.status == "failed":
            failed += 1
    return BidEvidenceSemanticIndexBatch(
        scanned=len(index_ids),
        ready=ready,
        retry_wait=retry_wait,
        stale=stale,
        failed=failed,
    )


def _validate_provider_hit(
    hit: SemanticProviderHit,
    entry: BidEvidenceSemanticEntry,
) -> None:
    if (
        hit.provider_record_id != str(entry.provider_record_id)
        or hit.retrieval_child_key != str(entry.retrieval_child_key)
        or hit.source_entry_hash != str(entry.source_entry_hash)
        or hit.embedding_text_hash != str(entry.embedding_text_hash)
        or hit.vector_hash != str(entry.vector_hash)
        or not (-1.000001 <= float(hit.score) <= 1.000001)
    ):
        raise BidEvidenceSemanticIndexInvalid(
            "BID_EVIDENCE_SEMANTIC_PROVIDER_HIT_INVALID"
        )


def recall_semantic_children(
    db: Session,
    *,
    provider: BidSemanticVectorProvider,
    source_indexes: Sequence[BidEvidenceRetrievalIndex],
    source_index_set_hash: str,
    allowed_document_versions: Sequence[str],
    allowed_retrieval_child_ids: Sequence[str] | None,
    query_items: Sequence[Mapping[str, Any]],
    top_k: int,
    per_query_depth: int = SEMANTIC_QUERY_CANDIDATE_DEPTH,
) -> SemanticRecallResult:
    descriptor = provider.descriptor
    validate_descriptor(descriptor)
    allowed = {str(value) for value in allowed_document_versions}
    allowed_children = (
        {str(value) for value in allowed_retrieval_child_ids}
        if allowed_retrieval_child_ids is not None
        else None
    )
    selected_sources = tuple(
        sorted(
            (
                row
                for row in source_indexes
                if str(row.document_version_id) in allowed
            ),
            key=lambda row: str(row.document_version_id),
        )
    )
    if not selected_sources:
        return SemanticRecallResult(
            semantic_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
            semantic_index_set_hash=canonical_hash(
                {
                    "profile_version": RQ2A_SEMANTIC_PROFILE_VERSION,
                    "source_index_set_hash": source_index_set_hash,
                    "allowed_document_versions": sorted(allowed),
                    "indexes": [],
                    "model": descriptor.stable_payload(),
                }
            ),
            model_descriptor=descriptor,
            hits=(),
        )
    version_ids = tuple(str(row.document_version_id) for row in selected_sources)
    heads = {
        str(row.document_version_id): row
        for row in (
            db.query(BidEvidenceSemanticHead)
            .filter(
                BidEvidenceSemanticHead.document_version_id.in_(version_ids),
                BidEvidenceSemanticHead.semantic_profile_version
                == RQ2A_SEMANTIC_PROFILE_VERSION,
            )
            .all()
        )
    }
    semantic_ids = tuple(str(row.current_semantic_index_id) for row in heads.values())
    semantic_indexes = {
        str(row.id): row
        for row in (
            db.query(BidEvidenceSemanticIndex)
            .filter(BidEvidenceSemanticIndex.id.in_(semantic_ids))
            .all()
            if semantic_ids
            else []
        )
    }
    selected_semantic: list[BidEvidenceSemanticIndex] = []
    for source in selected_sources:
        version_id = str(source.document_version_id)
        head = heads.get(version_id)
        semantic = (
            semantic_indexes.get(str(head.current_semantic_index_id))
            if head is not None
            else None
        )
        if (
            head is None
            or semantic is None
            or str(semantic.status) != "ready"
            or not semantic.result_hash
            or str(head.current_retrieval_index_id) != str(source.id)
            or str(semantic.retrieval_index_id) != str(source.id)
            or str(semantic.source_result_hash) != str(source.result_hash)
            or str(semantic.input_hash)
            != semantic_index_input_hash(source, descriptor)
            or not _descriptor_matches(semantic, descriptor)
        ):
            raise BidEvidenceSemanticIndexNotReady(
                "BID_EVIDENCE_SEMANTIC_INDEX_NOT_READY"
            )
        selected_semantic.append(semantic)
    entries = (
        db.query(BidEvidenceSemanticEntry)
        .filter(
            BidEvidenceSemanticEntry.semantic_index_id.in_(
                tuple(str(row.id) for row in selected_semantic)
            )
        )
        .all()
    )
    entries_by_index: defaultdict[str, dict[str, BidEvidenceSemanticEntry]] = defaultdict(dict)
    for entry in entries:
        index_id = str(entry.semantic_index_id)
        record_id = str(entry.provider_record_id)
        if record_id in entries_by_index[index_id]:
            raise BidEvidenceSemanticIndexInvalid(
                "BID_EVIDENCE_SEMANTIC_INDEX_INVALID"
            )
        entries_by_index[index_id][record_id] = entry
    for semantic in selected_semantic:
        if len(entries_by_index[str(semantic.id)]) != int(semantic.entry_count):
            raise BidEvidenceSemanticIndexInvalid(
                "BID_EVIDENCE_SEMANTIC_INDEX_INVALID"
            )
    index_set_hash = canonical_hash(
        {
            "profile_version": RQ2A_SEMANTIC_PROFILE_VERSION,
            "source_index_set_hash": source_index_set_hash,
            "allowed_document_versions": sorted(allowed),
            "indexes": [
                {
                    "document_version_id": str(row.document_version_id),
                    "retrieval_index_id": str(row.retrieval_index_id),
                    "semantic_index_id": str(row.id),
                    "result_hash": str(row.result_hash),
                    "vector_namespace": str(row.vector_namespace),
                }
                for row in selected_semantic
            ],
            "model": descriptor.stable_payload(),
        }
    )
    aggregate: defaultdict[str, float] = defaultdict(float)
    max_scores: defaultdict[str, float] = defaultdict(lambda: -1.0)
    matched_queries: defaultdict[str, list[str]] = defaultdict(list)
    entry_by_child: dict[str, BidEvidenceSemanticEntry] = {}
    for query_index, item in enumerate(query_items):
        query = str(item.get("text") or "").strip()
        if not query:
            continue
        weight = max(0.0, min(float(item.get("weight") or 1.0), 2.0))
        query_candidates: list[tuple[SemanticProviderHit, BidEvidenceSemanticEntry]] = []
        for semantic in selected_semantic:
            seen_provider_records: set[str] = set()
            provider_hits = provider.search(
                namespace=str(semantic.vector_namespace),
                query=query,
                top_k=max(1, min(int(per_query_depth), 100)),
            )
            if len(provider_hits) > max(1, min(int(per_query_depth), 100)):
                raise BidEvidenceSemanticIndexInvalid(
                    "BID_EVIDENCE_SEMANTIC_PROVIDER_HIT_INVALID"
                )
            provider_entries = entries_by_index[str(semantic.id)]
            for hit in provider_hits:
                if hit.provider_record_id in seen_provider_records:
                    raise BidEvidenceSemanticIndexInvalid(
                        "BID_EVIDENCE_SEMANTIC_PROVIDER_HIT_INVALID"
                    )
                seen_provider_records.add(hit.provider_record_id)
                entry = provider_entries.get(hit.provider_record_id)
                if entry is None:
                    raise BidEvidenceSemanticIndexInvalid(
                        "BID_EVIDENCE_SEMANTIC_PROVIDER_HIT_OUT_OF_SCOPE"
                    )
                _validate_provider_hit(hit, entry)
                if (
                    allowed_children is not None
                    and str(entry.retrieval_child_id) not in allowed_children
                ):
                    continue
                query_candidates.append((hit, entry))
        query_candidates.sort(
            key=lambda pair: (
                -float(pair[0].score),
                str(pair[1].retrieval_child_key),
                str(pair[1].retrieval_child_id),
            )
        )
        for rank, (hit, entry) in enumerate(
            query_candidates[: max(1, min(int(per_query_depth), 100))],
            1,
        ):
            child_id = str(entry.retrieval_child_id)
            previous = entry_by_child.get(child_id)
            if previous is not None and str(previous.entry_hash) != str(entry.entry_hash):
                raise BidEvidenceSemanticIndexInvalid(
                    "BID_EVIDENCE_SEMANTIC_CHILD_COLLISION"
                )
            entry_by_child[child_id] = entry
            aggregate[child_id] += weight / (SEMANTIC_QUERY_RRF_K + rank)
            max_scores[child_id] = max(max_scores[child_id], float(hit.score))
            if query not in matched_queries[child_id]:
                matched_queries[child_id].append(query)
    ordered = sorted(
        aggregate,
        key=lambda child_id: (
            -aggregate[child_id],
            -max_scores[child_id],
            str(entry_by_child[child_id].retrieval_child_key),
            child_id,
        ),
    )[: max(1, min(int(top_k), 100))]
    hits = tuple(
        SemanticRecallHit(
            retrieval_child_id=child_id,
            retrieval_child_key=str(entry_by_child[child_id].retrieval_child_key),
            semantic_index_id=str(entry_by_child[child_id].semantic_index_id),
            retrieval_index_id=str(entry_by_child[child_id].retrieval_index_id),
            semantic_score=round(max_scores[child_id], 8),
            rank_score=round(aggregate[child_id], 10),
            matched_queries=tuple(matched_queries[child_id]),
            vector_hash=str(entry_by_child[child_id].vector_hash),
        )
        for child_id in ordered
    )
    return SemanticRecallResult(
        semantic_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        semantic_index_set_hash=index_set_hash,
        model_descriptor=descriptor,
        hits=hits,
    )


__all__ = [
    "BidEvidenceSemanticFenceLost",
    "BidEvidenceSemanticIndexBatch",
    "BidEvidenceSemanticIndexBuild",
    "BidEvidenceSemanticIndexError",
    "BidEvidenceSemanticIndexInvalid",
    "BidEvidenceSemanticIndexNotReady",
    "BidEvidenceSemanticIndexSchedule",
    "BidEvidenceSemanticIndexStale",
    "DISABLED_SEMANTIC_PROFILE_VERSION",
    "RQ2A_SEMANTIC_PROFILE_VERSION",
    "SEMANTIC_INDEX_CONTRACT_VERSION",
    "SEMANTIC_QUERY_CANDIDATE_DEPTH",
    "SEMANTIC_QUERY_RRF_K",
    "SEMANTIC_UPSERT_BATCH_SIZE",
    "SemanticRecallHit",
    "SemanticRecallResult",
    "build_semantic_index",
    "ensure_semantic_index",
    "heartbeat_semantic_index",
    "invalidate_stale_semantic_indexes",
    "process_pending_semantic_indexes",
    "recall_semantic_children",
    "reconcile_current_retrieval_heads",
    "semantic_index_input_hash",
    "semantic_vector_namespace",
]
