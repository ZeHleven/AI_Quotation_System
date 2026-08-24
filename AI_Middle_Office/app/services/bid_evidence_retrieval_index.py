"""Deterministic PDF-C3 role-aware retrieval-index lifecycle.

No embedding provider, network service, raw file, OCR, model, or legacy
``bid_intake_*`` authority is used here.  The index is rebuilt only from one
terminal Phase 2 ParseRun and is selectable only while it matches ParseHead.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidEvidenceFragment,
)
from app.models.bid_assessment_eventing import BidOutboxEvent
from app.models.bid_assessment_retrieval import (
    BidEvidenceRetrievalEntry,
    BidEvidenceRetrievalHead,
    BidEvidenceRetrievalIndex,
)
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    canonical_hash,
    process_outbox_event_once,
)
from app.services.bid_evidence_chunk_builder import normalize_evidence_text
from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    BidParseQualityGateBlocked,
    BidParseQualityGateError,
    assert_parse_run_consumer_allowed,
)


ROLE_CONTRACT_VERSION = "bid.evidence.chunk.v2"
ROLE_AWARE_RETRIEVAL_PROFILE_VERSION = (
    "bid-evidence-retrieval-profile-v2-role-aware"
)
LEGACY_RETRIEVAL_PROFILE_VERSION = "bid-evidence-retrieval-profile-v1-legacy"
PDF_C2_PARSER_PROFILE_VERSION = "bid-document-parser-profile-v2-pdf-native-layout"
PDF_RQ1A_PARSER_PROFILE_VERSION = (
    "bid-document-parser-profile-v3-pdf-structure-rq1a"
)
ROLE_AWARE_PARSER_PROFILE_VERSIONS = frozenset(
    {
        PDF_C2_PARSER_PROFILE_VERSION,
        PDF_RQ1A_PARSER_PROFILE_VERSION,
        PDF_RQ1B_PARSER_PROFILE_VERSION,
    }
)
RETRIEVAL_INDEX_CONTRACT_VERSION = "bid.evidence.retrieval-index.v1"
RETRIEVAL_INDEX_CONSUMER = "bid-evidence-retrieval-index-coordinator-v1"
DOCUMENT_PARSED_EVENT = "bid.document.parsed.v1"


class BidEvidenceRetrievalIndexError(RuntimeError):
    code = "BID_EVIDENCE_RETRIEVAL_INDEX_ERROR"


class BidEvidenceRetrievalIndexInvalid(BidEvidenceRetrievalIndexError):
    code = "BID_EVIDENCE_RETRIEVAL_INDEX_INVALID"


@dataclass(frozen=True)
class BidEvidenceRetrievalIndexSchedule:
    index_id: str
    parse_run_id: str
    status: str
    created: bool


@dataclass(frozen=True)
class BidEvidenceRetrievalIndexBuild:
    index_id: str
    parse_run_id: str
    status: str
    entry_count: int
    result_hash: str | None


@dataclass(frozen=True)
class BidEvidenceRetrievalIndexBatch:
    scanned: int
    ready: int
    stale: int
    failed: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _role(row: BidEvidenceFragment) -> str:
    return str((row.locator_json or {}).get("fragment_role") or "")


def _evidence_key(row: BidEvidenceFragment) -> str:
    return str((row.locator_json or {}).get("evidence_key") or "")


def _is_citable(row: BidEvidenceFragment) -> bool:
    return (row.locator_json or {}).get("is_citable") is True


def _validate_fragment_integrity(row: BidEvidenceFragment) -> None:
    locator = dict(row.locator_json or {})
    if str(locator.get("schema_version") or "") != ROLE_CONTRACT_VERSION:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_ROLE_CONTRACT_INVALID"
        )
    if not _evidence_key(row):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_LOGICAL_KEY_MISSING"
        )
    normalized = str(row.normalized_text or "").strip()
    if not normalized or _sha256(normalized) != str(row.text_hash):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_TEXT_HASH_MISMATCH"
        )
    if canonical_hash(locator) != str(row.locator_hash):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_LOCATOR_HASH_MISMATCH"
        )
    if str(locator.get("text_hash") or "") != str(row.text_hash):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_SOURCE_TEXT_HASH_MISMATCH"
        )


def retrieval_index_input_hash(parse_run: BidDocumentParseRun) -> str:
    if not parse_run.result_hash:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_PARSE_RESULT_MISSING"
        )
    quality_report = assert_parse_run_consumer_allowed(
        parse_run,
        consumer="retrieval_index",
    )
    payload = {
        "contract_version": RETRIEVAL_INDEX_CONTRACT_VERSION,
        "profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        "role_contract_version": ROLE_CONTRACT_VERSION,
        "document_version_id": str(parse_run.document_version_id),
        "parse_run_id": str(parse_run.id),
        "source_result_hash": str(parse_run.result_hash),
    }
    if quality_report is not None:
        payload["parse_quality_result_hash"] = str(quality_report["result_hash"])
    return canonical_hash(payload)


def ensure_role_aware_retrieval_index(
    db: Session,
    *,
    parse_run_id: str,
    requested_at: datetime | None = None,
) -> BidEvidenceRetrievalIndexSchedule:
    parse_run = (
        db.query(BidDocumentParseRun)
        .filter(BidDocumentParseRun.id == str(parse_run_id))
        .with_for_update()
        .one_or_none()
    )
    if parse_run is None:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_DOCUMENT_PARSE_RUN_NOT_FOUND"
        )
    if str(parse_run.parser_profile_version) not in ROLE_AWARE_PARSER_PROFILE_VERSIONS:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_PARSER_PROFILE_INVALID"
        )
    if str(parse_run.status) not in {"succeeded", "partial"} or not parse_run.result_hash:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_PARSE_NOT_READY"
        )
    assert_parse_run_consumer_allowed(parse_run, consumer="retrieval_index")
    input_hash = retrieval_index_input_hash(parse_run)
    row = (
        db.query(BidEvidenceRetrievalIndex)
        .filter(
            BidEvidenceRetrievalIndex.document_version_id
            == str(parse_run.document_version_id),
            BidEvidenceRetrievalIndex.parse_run_id == str(parse_run.id),
            BidEvidenceRetrievalIndex.retrieval_profile_version
            == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        )
        .with_for_update()
        .one_or_none()
    )
    if row is not None:
        if (
            str(row.input_hash) != input_hash
            or str(row.source_result_hash) != str(parse_run.result_hash)
            or str(row.role_contract_version) != ROLE_CONTRACT_VERSION
        ):
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_INDEX_INPUT_DRIFT"
            )
        return BidEvidenceRetrievalIndexSchedule(
            index_id=str(row.id),
            parse_run_id=str(row.parse_run_id),
            status=str(row.status),
            created=False,
        )
    row = BidEvidenceRetrievalIndex(
        id=f"bri_{uuid.uuid4().hex}",
        document_version_id=str(parse_run.document_version_id),
        parse_run_id=str(parse_run.id),
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        role_contract_version=ROLE_CONTRACT_VERSION,
        source_result_hash=str(parse_run.result_hash),
        input_hash=input_hash,
        status="queued",
        parent_count=0,
        child_count=0,
        atom_count=0,
        entry_count=0,
        row_version=1,
        requested_at=requested_at or _utc_now(),
    )
    db.add(row)
    db.flush()
    return BidEvidenceRetrievalIndexSchedule(
        index_id=str(row.id),
        parse_run_id=str(row.parse_run_id),
        status="queued",
        created=True,
    )


def consume_document_parsed_for_retrieval_index(
    db: Session,
    *,
    event_id: str,
) -> ProcessedEventResult:
    """Schedule the derivative exactly once; the queued row is recovery truth."""

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) != DOCUMENT_PARSED_EVENT:
            return {"ignored": True, "event_type": str(event.event_type)}
        payload = dict(event.payload_json or {})
        parse_run_id = str(payload.get("parse_run_id") or "")
        document_version_id = str(payload.get("document_version_id") or "")
        result_hash = str(payload.get("result_hash") or "")
        if not parse_run_id or not document_version_id or not result_hash:
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_EVENT_INVALID"
            )
        parse_run = (
            session.query(BidDocumentParseRun)
            .filter(BidDocumentParseRun.id == parse_run_id)
            .one_or_none()
        )
        if parse_run is None:
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_DOCUMENT_PARSE_RUN_NOT_FOUND"
            )
        if str(parse_run.parser_profile_version) not in ROLE_AWARE_PARSER_PROFILE_VERSIONS:
            return {
                "ignored": True,
                "reason": "parser_profile_not_role_aware",
                "parse_run_id": parse_run_id,
            }
        if (
            str(parse_run.document_version_id) != document_version_id
            or str(parse_run.result_hash or "") != result_hash
        ):
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_EVENT_LINEAGE_MISMATCH"
            )
        try:
            scheduled = ensure_role_aware_retrieval_index(
                session,
                parse_run_id=parse_run_id,
                requested_at=event.occurred_at,
            )
        except BidParseQualityGateBlocked:
            return {
                "ignored": True,
                "reason": "parse_quality_gate_blocked",
                "parse_run_id": parse_run_id,
            }
        except BidParseQualityGateError:
            return {
                "ignored": True,
                "reason": "parse_quality_gate_invalid",
                "parse_run_id": parse_run_id,
            }
        return {
            "ignored": False,
            "index_id": scheduled.index_id,
            "parse_run_id": parse_run_id,
            "status": scheduled.status,
            "created": scheduled.created,
        }

    return process_outbox_event_once(
        db,
        consumer_name=RETRIEVAL_INDEX_CONSUMER,
        event_id=event_id,
        handler=_handler,
    )


def _entry_payload(
    *,
    child: BidEvidenceFragment,
    parent: BidEvidenceFragment,
    atoms: list[BidEvidenceFragment],
    retrieval_text: str,
    retrieval_hash: str,
) -> dict[str, Any]:
    child_locator = dict(child.locator_json or {})
    atom_descriptors = [
        {
            "evidence_key": _evidence_key(atom),
            "locator_hash": str(atom.locator_hash),
            "text_hash": str(atom.text_hash),
        }
        for atom in atoms
    ]
    source_atoms_hash = canonical_hash(atom_descriptors)
    payload = {
        "retrieval_child_key": _evidence_key(child),
        "section_parent_key": _evidence_key(parent),
        "ordinal": int(child.ordinal),
        "page_start": int(child_locator.get("page_no") or 0),
        "page_end": int(
            child_locator.get("page_end")
            or child_locator.get("page_no")
            or 0
        ),
        "retrieval_hash": retrieval_hash,
        "child_text_hash": str(child.text_hash),
        "source_atom_keys": [item["evidence_key"] for item in atom_descriptors],
        "source_atoms_hash": source_atoms_hash,
    }
    payload["entry_hash"] = canonical_hash(payload)
    payload["retrieval_text"] = retrieval_text
    return payload


def build_role_aware_retrieval_index(
    db: Session,
    *,
    index_id: str,
    now: datetime | None = None,
) -> BidEvidenceRetrievalIndexBuild:
    current_time = now or _utc_now()
    index = (
        db.query(BidEvidenceRetrievalIndex)
        .filter(BidEvidenceRetrievalIndex.id == str(index_id))
        .with_for_update()
        .one_or_none()
    )
    if index is None:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_INDEX_NOT_FOUND"
        )
    if str(index.status) in {"ready", "failed", "stale"}:
        return BidEvidenceRetrievalIndexBuild(
            index_id=str(index.id),
            parse_run_id=str(index.parse_run_id),
            status=str(index.status),
            entry_count=int(index.entry_count),
            result_hash=str(index.result_hash) if index.result_hash else None,
        )
    parse_run = (
        db.query(BidDocumentParseRun)
        .filter(BidDocumentParseRun.id == index.parse_run_id)
        .one()
    )
    parse_head = (
        db.query(BidDocumentParseHead)
        .filter(
            BidDocumentParseHead.document_version_id
            == str(index.document_version_id)
        )
        .with_for_update()
        .one_or_none()
    )
    if parse_head is None or str(parse_head.current_run_id) != str(index.parse_run_id):
        index.status = "stale"
        index.invalidated_at = current_time
        index.finished_at = current_time
        index.error_code = None
        index.row_version = int(index.row_version) + 1
        db.flush()
        return BidEvidenceRetrievalIndexBuild(
            index_id=str(index.id),
            parse_run_id=str(index.parse_run_id),
            status="stale",
            entry_count=0,
            result_hash=None,
        )
    if (
        str(parse_run.status) not in {"succeeded", "partial"}
        or str(parse_run.result_hash or "") != str(index.source_result_hash)
        or retrieval_index_input_hash(parse_run) != str(index.input_hash)
    ):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_INDEX_SOURCE_DRIFT"
        )
    assert_parse_run_consumer_allowed(parse_run, consumer="retrieval_index")
    if (
        db.query(BidEvidenceRetrievalEntry.id)
        .filter(BidEvidenceRetrievalEntry.index_id == index.id)
        .first()
        is not None
    ):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_INDEX_ALREADY_WRITTEN"
        )

    index.status = "building"
    index.started_at = current_time
    index.finished_at = None
    index.error_code = None
    index.row_version = int(index.row_version) + 1
    fragments = (
        db.query(BidEvidenceFragment)
        .filter(BidEvidenceFragment.parse_run_id == str(index.parse_run_id))
        .order_by(BidEvidenceFragment.ordinal.asc(), BidEvidenceFragment.id.asc())
        .all()
    )
    if not fragments:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_SOURCE_EMPTY"
        )
    for fragment in fragments:
        _validate_fragment_integrity(fragment)
    logical_keys = [_evidence_key(row) for row in fragments]
    if len(logical_keys) != len(set(logical_keys)):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_LOGICAL_KEY_DUPLICATE"
        )
    by_id = {str(row.id): row for row in fragments}
    parents = [row for row in fragments if _role(row) == "section_parent"]
    children = [row for row in fragments if _role(row) == "retrieval_child"]
    atoms = [row for row in fragments if _role(row) == "evidence_atom"]
    if len(parents) + len(children) + len(atoms) != len(fragments):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_FRAGMENT_ROLE_INVALID"
        )
    if not children or not atoms:
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_CITABLE_BODY_MISSING"
        )
    atoms_by_child: dict[str, list[BidEvidenceFragment]] = {}
    for atom in atoms:
        if not atom.parent_id or not _is_citable(atom):
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_ATOM_ROLE_INVALID"
            )
        atoms_by_child.setdefault(str(atom.parent_id), []).append(atom)

    stable_entries: list[dict[str, Any]] = []
    for child in children:
        parent = by_id.get(str(child.parent_id or ""))
        if (
            parent is None
            or _role(parent) != "section_parent"
            or parent.parent_id is not None
            or _is_citable(parent)
            or _is_citable(child)
        ):
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_HIERARCHY_INVALID"
            )
        source_atoms = sorted(
            atoms_by_child.get(str(child.id), []),
            key=lambda row: (int(row.ordinal), str(row.id)),
        )
        if not source_atoms:
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_CHILD_WITHOUT_ATOMS"
            )
        child_locator = dict(child.locator_json or {})
        context_prefix = str(child_locator.get("context_prefix") or "").strip()
        retrieval_text = normalize_evidence_text(
            f"{context_prefix}\n\n{str(child.normalized_text)}"
        )
        retrieval_hash = _sha256(retrieval_text)
        if (
            not context_prefix
            or retrieval_hash != str(child_locator.get("retrieval_hash") or "")
        ):
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_TEXT_DERIVATION_INVALID"
            )
        payload = _entry_payload(
            child=child,
            parent=parent,
            atoms=source_atoms,
            retrieval_text=retrieval_text,
            retrieval_hash=retrieval_hash,
        )
        if payload["page_start"] < 1 or payload["page_end"] < payload["page_start"]:
            raise BidEvidenceRetrievalIndexInvalid(
                "BID_EVIDENCE_RETRIEVAL_PAGE_RANGE_INVALID"
            )
        stable_entries.append({key: value for key, value in payload.items() if key != "retrieval_text"})
        db.add(
            BidEvidenceRetrievalEntry(
                id=f"bre_{uuid.uuid4().hex}",
                index_id=str(index.id),
                document_version_id=str(index.document_version_id),
                parse_run_id=str(index.parse_run_id),
                retrieval_child_id=str(child.id),
                retrieval_child_key=payload["retrieval_child_key"],
                section_parent_id=str(parent.id),
                section_parent_key=payload["section_parent_key"],
                ordinal=int(payload["ordinal"]),
                page_start=int(payload["page_start"]),
                page_end=int(payload["page_end"]),
                retrieval_text=retrieval_text,
                retrieval_hash=retrieval_hash,
                child_text_hash=str(child.text_hash),
                source_atom_ids_json=[str(atom.id) for atom in source_atoms],
                source_atom_keys_json=list(payload["source_atom_keys"]),
                source_atom_count=len(source_atoms),
                source_atoms_hash=str(payload["source_atoms_hash"]),
                entry_hash=str(payload["entry_hash"]),
            )
        )
    db.flush()

    result_hash = canonical_hash(
        {
            "contract_version": RETRIEVAL_INDEX_CONTRACT_VERSION,
            "profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            "role_contract_version": ROLE_CONTRACT_VERSION,
            "input_hash": str(index.input_hash),
            "entries": stable_entries,
        }
    )
    # Recheck ParseHead under the promotion lock. A newly queued parse must
    # fence this derivative even if all source rows themselves were valid.
    db.refresh(parse_head)
    if str(parse_head.current_run_id) != str(index.parse_run_id):
        raise BidEvidenceRetrievalIndexInvalid(
            "BID_EVIDENCE_RETRIEVAL_HEAD_CHANGED_DURING_BUILD"
        )
    head = (
        db.query(BidEvidenceRetrievalHead)
        .filter(
            BidEvidenceRetrievalHead.document_version_id
            == str(index.document_version_id),
            BidEvidenceRetrievalHead.retrieval_profile_version
            == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        )
        .with_for_update()
        .one_or_none()
    )
    if head is None:
        head = BidEvidenceRetrievalHead(
            document_version_id=str(index.document_version_id),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            current_index_id=str(index.id),
            current_parse_run_id=str(index.parse_run_id),
            row_version=1,
        )
        db.add(head)
    else:
        previous_index_id = str(head.current_index_id)
        if previous_index_id != str(index.id):
            previous = (
                db.query(BidEvidenceRetrievalIndex)
                .filter(BidEvidenceRetrievalIndex.id == previous_index_id)
                .with_for_update()
                .one_or_none()
            )
            if previous is not None and str(previous.status) == "ready":
                previous.status = "stale"
                previous.invalidated_at = current_time
                previous.row_version = int(previous.row_version) + 1
            head.current_index_id = str(index.id)
            head.current_parse_run_id = str(index.parse_run_id)
            head.row_version = int(head.row_version) + 1
    index.status = "ready"
    index.parent_count = len(parents)
    index.child_count = len(children)
    index.atom_count = len(atoms)
    index.entry_count = len(stable_entries)
    index.result_hash = result_hash
    index.error_code = None
    index.finished_at = current_time
    index.invalidated_at = None
    index.row_version = int(index.row_version) + 1
    db.flush()
    return BidEvidenceRetrievalIndexBuild(
        index_id=str(index.id),
        parse_run_id=str(index.parse_run_id),
        status="ready",
        entry_count=len(stable_entries),
        result_hash=result_hash,
    )


def invalidate_stale_retrieval_indexes(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    current_time = now or _utc_now()
    rows = (
        db.query(BidEvidenceRetrievalIndex)
        .filter(
            BidEvidenceRetrievalIndex.retrieval_profile_version
            == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            BidEvidenceRetrievalIndex.status.in_(("queued", "ready")),
        )
        .with_for_update()
        .all()
    )
    parse_heads = {
        str(row.document_version_id): str(row.current_run_id)
        for row in db.query(BidDocumentParseHead).all()
    }
    invalidated = 0
    for index in rows:
        if parse_heads.get(str(index.document_version_id)) == str(index.parse_run_id):
            continue
        index.status = "stale"
        index.invalidated_at = current_time
        index.finished_at = index.finished_at or current_time
        index.error_code = None
        index.row_version = int(index.row_version) + 1
        head = (
            db.query(BidEvidenceRetrievalHead)
            .filter(
                BidEvidenceRetrievalHead.document_version_id
                == str(index.document_version_id),
                BidEvidenceRetrievalHead.retrieval_profile_version
                == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                BidEvidenceRetrievalHead.current_index_id == str(index.id),
            )
            .one_or_none()
        )
        if head is not None:
            db.delete(head)
        invalidated += 1
    db.flush()
    return invalidated


def reconcile_current_role_aware_parse_heads(
    db: Session,
    *,
    limit: int = 100,
) -> int:
    """Backfill current terminal C2 ParseRuns whose parsed event is historical.

    The Outbox consumer is the low-latency path.  This scan is the recovery
    truth for documents parsed before C3 was enabled or while event delivery
    was interrupted.
    """

    parse_run_ids = [
        str(row[0])
        for row in (
            db.query(BidDocumentParseRun.id)
            .join(
                BidDocumentParseHead,
                BidDocumentParseHead.current_run_id == BidDocumentParseRun.id,
            )
            .outerjoin(
                BidEvidenceRetrievalIndex,
                (
                    BidEvidenceRetrievalIndex.parse_run_id
                    == BidDocumentParseRun.id
                )
                & (
                    BidEvidenceRetrievalIndex.retrieval_profile_version
                    == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
                ),
            )
            .filter(
                BidDocumentParseRun.parser_profile_version.in_(
                    tuple(sorted(ROLE_AWARE_PARSER_PROFILE_VERSIONS))
                ),
                BidDocumentParseRun.status.in_(("succeeded", "partial")),
                BidDocumentParseRun.result_hash.isnot(None),
                BidEvidenceRetrievalIndex.id.is_(None),
            )
            .order_by(
                BidDocumentParseRun.finished_at.asc(),
                BidDocumentParseRun.id.asc(),
            )
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
    ]
    created = 0
    for parse_run_id in parse_run_ids:
        try:
            scheduled = ensure_role_aware_retrieval_index(
                db,
                parse_run_id=parse_run_id,
            )
        except BidParseQualityGateBlocked:
            continue
        except BidParseQualityGateError:
            continue
        created += int(scheduled.created)
    return created


def _record_failure(
    db: Session,
    *,
    index_id: str,
    error_code: str,
    now: datetime,
) -> None:
    row = (
        db.query(BidEvidenceRetrievalIndex)
        .filter(BidEvidenceRetrievalIndex.id == str(index_id))
        .with_for_update()
        .one_or_none()
    )
    if row is None or str(row.status) in {"ready", "stale"}:
        return
    row.status = "failed"
    row.error_code = str(error_code or BidEvidenceRetrievalIndexInvalid.code)[:100]
    row.finished_at = now
    row.row_version = int(row.row_version) + 1
    db.flush()


def process_pending_retrieval_indexes(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    limit: int = 20,
) -> BidEvidenceRetrievalIndexBatch:
    maintenance_db = session_factory()
    try:
        with maintenance_db.begin():
            stale = invalidate_stale_retrieval_indexes(maintenance_db)
            reconcile_current_role_aware_parse_heads(
                maintenance_db,
                limit=max(1, min(int(limit) * 5, 500)),
            )
            index_ids = [
                str(row[0])
                for row in (
                    maintenance_db.query(BidEvidenceRetrievalIndex.id)
                    .filter(BidEvidenceRetrievalIndex.status == "queued")
                    .order_by(BidEvidenceRetrievalIndex.requested_at.asc())
                    .limit(max(1, min(int(limit), 100)))
                    .all()
                )
            ]
    finally:
        maintenance_db.close()
    ready = failed = 0
    for index_id in index_ids:
        db = session_factory()
        try:
            with db.begin():
                result = build_role_aware_retrieval_index(db, index_id=index_id)
            if result.status == "ready":
                ready += 1
            elif result.status == "stale":
                stale += 1
        except BidEvidenceRetrievalIndexInvalid as exc:
            db.rollback()
            failure_db = session_factory()
            try:
                with failure_db.begin():
                    _record_failure(
                        failure_db,
                        index_id=index_id,
                        error_code=str(exc)[:100] or exc.code,
                        now=_utc_now(),
                    )
            finally:
                failure_db.close()
            failed += 1
        finally:
            db.close()
    return BidEvidenceRetrievalIndexBatch(
        scanned=len(index_ids),
        ready=ready,
        stale=stale,
        failed=failed,
    )
