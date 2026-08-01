from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import registry as model_registry  # noqa: F401
from app.models.bidding import BidProject
from app.models.tender_evidence import (
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
    BidEvidenceReadAudit,
)
from app.models.tender_evidence_index import BidEvidenceIndexJob
from app.services.tender_evidence_body_storage import (
    TenderEvidenceBodyError,
    TenderEvidenceBodyReader,
)

from .contracts import (
    DocumentManifest,
    EvidenceBlock,
    EvidenceLocator,
    EvidenceRefInput,
    EvidenceStructuralContext,
    VersionConflict,
)
from .local_repository import (
    EvidenceNotFoundError,
    TenderCaseNotFoundError,
    TenderEvidenceRepositoryError,
)
from .retrieval_router import RetrievalMode
from .hybrid_client import (
    TenderHybridSearch,
    TenderHybridSearchError,
)
from .structure_context import build_structural_context_map


logger = logging.getLogger(__name__)


class SqlAlchemyTenderEvidenceRepository:
    """MySQL/SQLite adapter for immutable tender evidence tables.

    Search is a bounded lexical fallback for Phase 2. A hybrid/vector index can
    replace only ``search`` later without changing the MCP contract.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        max_lexical_candidates: int = 2_000,
        hybrid_search_client: TenderHybridSearch | None = None,
        hybrid_index_schema_version: str = "tender-hybrid-v1",
        body_reader: TenderEvidenceBodyReader | None = None,
    ):
        self._session_factory = session_factory
        self._max_lexical_candidates = max(
            100,
            min(int(max_lexical_candidates), 10_000),
        )
        self._hybrid_search_client = hybrid_search_client
        self._hybrid_index_schema_version = hybrid_index_schema_version[:64]
        self._body_reader = body_reader or TenderEvidenceBodyReader()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        db = self._session_factory()
        try:
            yield db
        except (
            TenderCaseNotFoundError,
            EvidenceNotFoundError,
            TenderEvidenceRepositoryError,
        ):
            db.rollback()
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            raise TenderEvidenceRepositoryError(
                "tender evidence database operation failed"
            ) from exc
        finally:
            db.close()

    def get_manifest(self, *, case_id: str) -> DocumentManifest:
        with self._session() as db:
            project = _get_project(db, case_id)
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
                raise TenderEvidenceRepositoryError(
                    "the scoped tender case has no active evidence manifest"
                )
            try:
                payload = json.loads(manifest.snapshot_json)
                parsed = DocumentManifest.model_validate(payload)
            except (TypeError, ValueError) as exc:
                raise TenderEvidenceRepositoryError(
                    "the active tender evidence manifest is invalid"
                ) from exc
            if parsed.case_id != case_id:
                raise TenderEvidenceRepositoryError(
                    "the active tender evidence manifest scope is invalid"
                )
            return parsed

    def search(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: RetrievalMode = "hybrid",
    ) -> list[EvidenceBlock]:
        if search_mode not in {"exact", "semantic", "hybrid"}:
            raise ValueError(f"unsupported tender search mode: {search_mode}")
        if self._hybrid_search_client is not None:
            hybrid_blocks = self._search_hybrid(
                case_id=case_id,
                query=query,
                top_k=top_k,
                search_mode=search_mode,
            )
            if hybrid_blocks is not None:
                return hybrid_blocks
        return self._search_lexical(
            case_id=case_id,
            query=query,
            top_k=top_k,
        )

    def _search_hybrid(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
        search_mode: RetrievalMode,
    ) -> list[EvidenceBlock] | None:
        try:
            with self._session() as db:
                project = _get_project(db, case_id)
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
                    return None
                index_job = (
                    db.query(BidEvidenceIndexJob)
                    .filter(
                        BidEvidenceIndexJob.project_id == project.id,
                        BidEvidenceIndexJob.manifest_id == manifest.id,
                        BidEvidenceIndexJob.index_schema_version
                        == self._hybrid_index_schema_version,
                        BidEvidenceIndexJob.status == "completed",
                        BidEvidenceIndexJob.indexed_block_count
                        == BidEvidenceIndexJob.requested_block_count,
                    )
                    .one_or_none()
                )
                if index_job is None:
                    return None
                project_id = project.id
                manifest_version = manifest.version_no
                manifest_hash = manifest.manifest_hash

            hits = self._hybrid_search_client.search(
                case_id=case_id,
                manifest_version=manifest_version,
                manifest_hash=manifest_hash,
                query=query,
                top_k=top_k,
                search_mode=search_mode,
            )
            if not hits:
                return []
            evidence_ids = list(
                dict.fromkeys(
                    item.evidence_id for item in hits if item.evidence_id
                )
            )
            with self._session() as db:
                rows = (
                    db.query(BidEvidenceBlock, BidEvidenceDocument)
                    .join(
                        BidEvidenceDocument,
                        BidEvidenceDocument.id
                        == BidEvidenceBlock.document_id,
                    )
                    .filter(
                        BidEvidenceBlock.project_id == project_id,
                        BidEvidenceDocument.project_id == project_id,
                        BidEvidenceDocument.active.is_(True),
                        BidEvidenceDocument.parse_status != "failed",
                        BidEvidenceBlock.evidence_id.in_(evidence_ids),
                    )
                    .all()
                )
            by_evidence_id = {
                block.evidence_id: (block, document)
                for block, document in rows
            }
            ordered: list[EvidenceBlock] = []
            for hit in hits:
                stored = by_evidence_id.get(hit.evidence_id)
                if stored is None:
                    continue
                block, document = stored
                if block.block_id != hit.block_id:
                    continue
                ordered.append(
                    _to_contract_block(
                        block,
                        document,
                        content=self._read_content(block, document),
                    )
                )
                if len(ordered) >= top_k:
                    break
            return ordered
        except TenderHybridSearchError:
            logger.warning(
                "tender_hybrid_search_fallback",
                extra={
                    "case_id": case_id,
                    "event": "tender_hybrid_search_fallback",
                },
                exc_info=True,
            )
            return None
        except Exception:
            logger.exception(
                "unexpected tender hybrid search failure case_id=%s",
                case_id,
            )
            return None

    def _search_lexical(
        self,
        *,
        case_id: str,
        query: str,
        top_k: int,
    ) -> list[EvidenceBlock]:
        terms = _query_terms(query)
        with self._session() as db:
            project = _get_project(db, case_id)
            rows = (
                db.query(BidEvidenceBlock, BidEvidenceDocument)
                .join(
                    BidEvidenceDocument,
                    BidEvidenceDocument.id == BidEvidenceBlock.document_id,
                )
                .filter(
                    BidEvidenceBlock.project_id == project.id,
                    BidEvidenceDocument.project_id == project.id,
                    BidEvidenceDocument.active.is_(True),
                    BidEvidenceDocument.parse_status != "failed",
                )
                .order_by(
                    BidEvidenceDocument.document_key.asc(),
                    BidEvidenceBlock.block_order.asc(),
                )
                .limit(self._max_lexical_candidates)
                .all()
            )
            ranked: list[
                tuple[int, str, int, BidEvidenceBlock, BidEvidenceDocument]
            ] = []
            for block, document in rows:
                keywords = _load_keywords(block.keywords_json)
                content = self._read_content(block, document)
                score = _score_text(
                    content,
                    keywords=keywords,
                    query=query,
                    terms=terms,
                )
                if score <= 0:
                    continue
                ranked.append(
                    (
                        score,
                        document.document_key,
                        -block.block_order,
                        block,
                        document,
                    )
                )
            ranked.sort(
                key=lambda item: (item[0], item[1], item[2]),
                reverse=True,
            )
            return [
                _to_contract_block(
                    block,
                    document,
                    content=self._read_content(block, document),
                )
                for _, _, _, block, document in ranked[:top_k]
            ]

    def get_context(
        self,
        *,
        case_id: str,
        evidence_id: str,
        before_blocks: int,
        after_blocks: int,
    ) -> list[EvidenceBlock]:
        with self._session() as db:
            project = _get_project(db, case_id)
            selected = (
                db.query(BidEvidenceBlock, BidEvidenceDocument)
                .join(
                    BidEvidenceDocument,
                    BidEvidenceDocument.id == BidEvidenceBlock.document_id,
                )
                .filter(
                    BidEvidenceBlock.project_id == project.id,
                    BidEvidenceDocument.project_id == project.id,
                    BidEvidenceBlock.evidence_id == evidence_id,
                )
                .one_or_none()
            )
            if selected is None:
                raise EvidenceNotFoundError(
                    "the evidence_id does not exist in the scoped tender case"
                )
            selected_block, document = selected
            lower = max(0, selected_block.block_order - before_blocks)
            upper = selected_block.block_order + after_blocks
            blocks = (
                db.query(BidEvidenceBlock)
                .filter(
                    BidEvidenceBlock.document_id == document.id,
                    BidEvidenceBlock.block_order >= lower,
                    BidEvidenceBlock.block_order <= upper,
                )
                .order_by(BidEvidenceBlock.block_order.asc())
                .all()
            )
            return [
                _to_contract_block(
                    item,
                    document,
                    content=self._read_content(item, document),
                )
                for item in blocks
            ]

    def get_structural_context(
        self,
        *,
        case_id: str,
        evidence_ids: Sequence[str],
        max_heading_lookback: int = 12,
    ) -> dict[str, list[EvidenceStructuralContext]]:
        requested = list(dict.fromkeys(evidence_ids))
        if not requested:
            return {}
        with self._session() as db:
            project = _get_project(db, case_id)
            selected = (
                db.query(BidEvidenceBlock)
                .join(
                    BidEvidenceDocument,
                    BidEvidenceDocument.id
                    == BidEvidenceBlock.document_id,
                )
                .filter(
                    BidEvidenceBlock.project_id == project.id,
                    BidEvidenceDocument.project_id == project.id,
                    BidEvidenceDocument.active.is_(True),
                    BidEvidenceDocument.parse_status != "failed",
                    BidEvidenceBlock.evidence_id.in_(requested),
                )
                .all()
            )
            document_ids = {
                item.document_id for item in selected
            }
            if not document_ids:
                return {}
            rows = (
                db.query(BidEvidenceBlock, BidEvidenceDocument)
                .join(
                    BidEvidenceDocument,
                    BidEvidenceDocument.id
                    == BidEvidenceBlock.document_id,
                )
                .filter(
                    BidEvidenceBlock.project_id == project.id,
                    BidEvidenceDocument.project_id == project.id,
                    BidEvidenceDocument.id.in_(document_ids),
                    BidEvidenceDocument.active.is_(True),
                    BidEvidenceDocument.parse_status != "failed",
                )
                .order_by(
                    BidEvidenceDocument.id.asc(),
                    BidEvidenceBlock.block_order.asc(),
                )
                .all()
            )
            contents = self._body_reader.read_many(rows)
            blocks = [
                _to_contract_block(
                    block,
                    document,
                    content=contents[block.evidence_id],
                )
                for block, document in rows
            ]
        return build_structural_context_map(
            candidate_evidence_ids=requested,
            document_blocks=blocks,
            max_heading_lookback=max_heading_lookback,
        )

    def _read_content(
        self,
        block: BidEvidenceBlock,
        document: BidEvidenceDocument,
    ) -> str:
        try:
            return self._body_reader.read(document=document, block=block)
        except TenderEvidenceBodyError as exc:
            raise TenderEvidenceRepositoryError(
                "authoritative tender evidence body is unavailable"
            ) from exc

    def get_document_versions(
        self,
        *,
        case_id: str,
        document_key: str,
    ) -> tuple[list[dict[str, object]], list[VersionConflict]]:
        with self._session() as db:
            project = _get_project(db, case_id)
            documents = (
                db.query(BidEvidenceDocument)
                .filter(
                    BidEvidenceDocument.project_id == project.id,
                    BidEvidenceDocument.document_key == document_key,
                )
                .order_by(BidEvidenceDocument.version_no.asc())
                .all()
            )
            versions = [
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
            ]
            # Phase 2 stores structural versions but does not invent semantic
            # conflicts. The future conflict detector will populate this part.
            return versions, []

    def validate_refs(
        self,
        *,
        case_id: str,
        refs: Sequence[EvidenceRefInput],
        manifest_version: int,
    ) -> list[dict[str, object]]:
        with self._session() as db:
            project = _get_project(db, case_id)
            manifest = (
                db.query(BidEvidenceManifest)
                .filter(
                    BidEvidenceManifest.project_id == project.id,
                    BidEvidenceManifest.active.is_(True),
                )
                .order_by(BidEvidenceManifest.version_no.desc())
                .first()
            )
            is_current_manifest = bool(
                manifest and manifest.version_no == manifest_version
            )
            evidence_ids = [item.evidence_id for item in refs]
            rows = []
            if evidence_ids:
                rows = (
                    db.query(BidEvidenceBlock, BidEvidenceDocument)
                    .join(
                        BidEvidenceDocument,
                        BidEvidenceDocument.id == BidEvidenceBlock.document_id,
                    )
                    .filter(
                        BidEvidenceBlock.project_id == project.id,
                        BidEvidenceDocument.project_id == project.id,
                        BidEvidenceBlock.evidence_id.in_(evidence_ids),
                    )
                    .all()
                )
            by_evidence_id = {
                block.evidence_id: (block, document)
                for block, document in rows
            }
            results: list[dict[str, object]] = []
            for ref in refs:
                stored = by_evidence_id.get(ref.evidence_id)
                reasons: list[str] = []
                if not is_current_manifest:
                    reasons.append("manifest_version_mismatch")
                if stored is None:
                    reasons.append("evidence_not_found")
                else:
                    block, document = stored
                    if block.block_id != ref.block_id:
                        reasons.append("block_id_mismatch")
                    if document.evidence_document_uuid != ref.document_id:
                        reasons.append("document_id_mismatch")
                    if document.version_no != ref.document_version:
                        reasons.append("document_version_mismatch")
                    if block.content_hash != ref.content_hash:
                        reasons.append("content_hash_mismatch")
                    if not document.active or document.parse_status == "failed":
                        reasons.append("document_version_not_active")
                results.append(
                    {
                        "evidence_id": ref.evidence_id,
                        "valid": not reasons,
                        "reasons": reasons,
                    }
                )
            return results

    def record_context_read(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        subject: str,
        evidence_id: str,
        trace_id: str,
    ) -> None:
        with self._session() as db:
            project = _get_project(db, case_id)
            row = (
                db.query(BidEvidenceBlock, BidEvidenceDocument)
                .join(
                    BidEvidenceDocument,
                    BidEvidenceDocument.id == BidEvidenceBlock.document_id,
                )
                .filter(
                    BidEvidenceBlock.project_id == project.id,
                    BidEvidenceDocument.project_id == project.id,
                    BidEvidenceBlock.evidence_id == evidence_id,
                )
                .one_or_none()
            )
            if row is None:
                raise EvidenceNotFoundError(
                    "the evidence_id does not exist in the scoped tender case"
                )
            block, document = row
            db.add(
                BidEvidenceReadAudit(
                    audit_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    evidence_document_id=document.id,
                    evidence_block_id=block.id,
                    assessment_id=assessment_id,
                    agent_run_id=agent_run_id,
                    subject=subject,
                    capability="read_evidence_context",
                    trace_id=trace_id,
                )
            )
            db.commit()

    def get_context_read_ids(
        self,
        *,
        case_id: str,
        assessment_id: str,
        agent_run_id: str,
        evidence_ids: Sequence[str],
    ) -> set[str]:
        if not evidence_ids:
            return set()
        with self._session() as db:
            project = _get_project(db, case_id)
            rows = (
                db.query(BidEvidenceBlock.evidence_id)
                .join(
                    BidEvidenceReadAudit,
                    BidEvidenceReadAudit.evidence_block_id
                    == BidEvidenceBlock.id,
                )
                .filter(
                    BidEvidenceReadAudit.project_id == project.id,
                    BidEvidenceReadAudit.assessment_id == assessment_id,
                    BidEvidenceReadAudit.agent_run_id == agent_run_id,
                    BidEvidenceBlock.evidence_id.in_(list(evidence_ids)),
                )
                .distinct()
                .all()
            )
            return {str(item[0]) for item in rows}


def _get_project(db: Session, case_id: str) -> BidProject:
    project = (
        db.query(BidProject)
        .filter(BidProject.project_uuid == case_id)
        .one_or_none()
    )
    if project is None:
        raise TenderCaseNotFoundError("the scoped tender case does not exist")
    return project


def _to_contract_block(
    block: BidEvidenceBlock,
    document: BidEvidenceDocument,
    *,
    content: str,
) -> EvidenceBlock:
    locator_payload = _load_locator_payload(block.locator_json)
    return EvidenceBlock(
        evidence_id=block.evidence_id,
        block_id=block.block_id,
        document_id=document.evidence_document_uuid,
        document_key=document.document_key,
        document_version=document.version_no,
        block_order=block.block_order,
        locator=EvidenceLocator(
            page=block.page,
            sheet=block.sheet,
            cell_range=block.cell_range,
            section=block.section,
            source_location=str(
                locator_payload.get("source_location") or ""
            ).strip()
            or None,
        ),
        content_hash=block.content_hash,
        content=content,
        keywords=_load_keywords(block.keywords_json),
    )


def _load_locator_payload(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_keywords(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item)[:80] for item in parsed if str(item).strip()][:50]


def _query_terms(query: str) -> list[str]:
    normalized = query.casefold().strip()
    if not normalized:
        return []
    raw_terms = re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]+", normalized)
    terms: set[str] = set()
    for raw in raw_terms:
        terms.add(raw)
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw) and len(raw) > 2:
            terms.update(raw[index : index + 2] for index in range(len(raw) - 1))
    return sorted(terms, key=lambda item: (-len(item), item))


def _score_text(
    content: str,
    *,
    keywords: Sequence[str],
    query: str,
    terms: Sequence[str],
) -> int:
    normalized_query = query.casefold().strip()
    normalized_content = content.casefold()
    normalized_keywords = [item.casefold() for item in keywords]
    score = 0
    if normalized_query and normalized_query in normalized_content:
        score += 20
    for term in terms:
        if term in normalized_content:
            score += 2
        if any(term == keyword for keyword in normalized_keywords):
            score += 8
        elif any(term in keyword for keyword in normalized_keywords):
            score += 4
    return score
