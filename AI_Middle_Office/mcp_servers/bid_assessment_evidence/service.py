"""Evidence MCP application service over authoritative Phase 2 fragments.

The service deliberately has no raw-file, parser, object-store, network, or
legacy ``bid_intake_*`` access.  A caller supplies the server-authoritative
Run/Manifest scope; tool arguments cannot broaden it.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidEvidenceFragment,
)
from app.models.bid_assessment_retrieval import (
    BidEvidenceRetrievalEntry,
    BidEvidenceRetrievalHead,
    BidEvidenceRetrievalIndex,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_evidence_retrieval_index import (
    LEGACY_RETRIEVAL_PROFILE_VERSION,
    ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
    retrieval_index_input_hash,
)
from app.services.bid_evidence_semantic_index import (
    DISABLED_SEMANTIC_PROFILE_VERSION,
    RQ2A_SEMANTIC_PROFILE_VERSION,
    BidEvidenceSemanticIndexError,
    recall_semantic_children,
)
from app.services.bid_field_aware_lexical import (
    CHANNEL_ORDER,
    FIELD_AWARE_CHILD_RRF_WEIGHT,
    FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    FIELD_AWARE_PARENT_WEIGHT,
    LEGACY_CHILD_RRF_WEIGHT,
    LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
    MAX_CHILDREN_PER_PARENT,
    ORIGINAL_QUERY_ANCHOR_WEIGHT,
    STRUCTURED_FIELD_RRF_WEIGHT,
    LexicalAtomSource,
    LexicalChildSource,
    build_field_aware_lexical_corpus,
    cache_field_aware_corpus,
    get_cached_field_aware_corpus,
    rank_field_aware_bm25f,
)
from app.services.bid_hybrid_candidate_fusion import (
    DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
    RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
    RQ2B_CHANNEL_CANDIDATE_DEPTH,
    BidHybridCandidateFusionError,
    CandidateChannelHit,
    fuse_candidate_channels,
)
from app.services.bid_lightweight_reranker import (
    DISABLED_RERANK_PROFILE_VERSION,
    RQ2C_CANDIDATE_WINDOW,
    RQ2C_RERANK_PROFILE_VERSION,
    BidLightweightRerankerError,
    BidRerankerProvider,
    RerankCandidateInput,
    rerank_frozen_candidates,
)
from app.services.bid_query_optimizer import (
    LEGACY_QUERY_PLANNER_PROFILE_VERSION,
    QUERY_OPTIMIZER_PROFILE_VERSION,
    optimize_bid_evidence_query,
)
from app.services.bid_semantic_vector_provider import (
    BidSemanticProviderError,
    BidSemanticVectorProvider,
)
from mcp_servers.tender_evidence.query_planner import plan_tender_query
from mcp_servers.tender_evidence.retrieval_router import route_tender_query


MCP_CONTRACT_VERSION = "bid-assessment-evidence-mcp/v1"
ROLE_AWARE_MCP_CONTRACT_VERSION = "bid-assessment-evidence-mcp/v2"
MAX_SCOPED_CANDIDATES = 5000
MAX_READ_ENTRIES = 64
MAX_READ_ITEMS = 12
MAX_READ_CHARS = 12000
RRF_K = 60


class BidEvidenceMcpError(RuntimeError):
    code = "BID_EVIDENCE_MCP_ERROR"


@dataclass(frozen=True)
class BidEvidenceMcpScope:
    assessment_id: str
    run_id: str
    manifest_id: str


@dataclass(frozen=True)
class _RoleAwareIndexScope:
    manifest_hash: str
    document_roles: dict[str, str]
    indexes: tuple[BidEvidenceRetrievalIndex, ...]
    index_set_hash: str


@dataclass(frozen=True)
class _TextCandidate:
    key: str
    text: str
    tie_key: str = ""


def _tokens(value: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(value or "")).lower().strip()
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9_.:%/-]*", normalized)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese_terms: list[str] = []
    for run in chinese_runs:
        if len(run) <= 2:
            chinese_terms.append(run)
            continue
        chinese_terms.extend(run[index : index + 2] for index in range(len(run) - 1))
        chinese_terms.extend(run[index : index + 3] for index in range(len(run) - 2))
    return ascii_terms + chinese_terms


def _bm25_rank_candidates(
    query: str,
    rows: list[_TextCandidate],
) -> list[tuple[str, float]]:
    query_terms = _tokens(query)
    if not query_terms or not rows:
        return []
    documents = [_tokens(row.text) for row in rows]
    doc_count = len(documents)
    avg_length = sum(len(value) for value in documents) / max(doc_count, 1)
    frequencies: Counter[str] = Counter()
    for terms in documents:
        frequencies.update(set(terms))
    scores: list[tuple[str, float, str]] = []
    for row, terms in zip(rows, documents):
        tf = Counter(terms)
        length = len(terms)
        score = 0.0
        for term in set(query_terms):
            if not tf[term]:
                continue
            inverse = math.log(1 + (doc_count - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            numerator = tf[term] * 2.2
            denominator = tf[term] + 1.2 * (0.25 + 0.75 * length / max(avg_length, 1.0))
            score += inverse * numerator / denominator
        normalized_query = re.sub(r"\s+", "", query.lower())
        normalized_text = re.sub(r"\s+", "", row.text.lower())
        if len(normalized_query) >= 2 and normalized_query in normalized_text:
            score += 4.0
        if score > 0:
            scores.append(
                (row.key, round(score, 8), str(row.tie_key or row.key))
            )
    scores.sort(key=lambda item: (-item[1], item[2], item[0]))
    return [(key, score) for key, score, _tie_key in scores]


def _bm25_rank(query: str, rows: list[BidEvidenceFragment]) -> list[tuple[str, float]]:
    return _bm25_rank_candidates(
        query,
        [
            _TextCandidate(key=str(row.id), text=str(row.normalized_text or ""))
            for row in rows
        ],
    )


def _excerpt(text: str, query: str, limit: int = 900) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    anchors = [term for term in _tokens(query) if len(term) >= 2]
    position = min(
        (normalized.lower().find(term) for term in anchors if normalized.lower().find(term) >= 0),
        default=0,
    )
    start = max(0, position - limit // 4)
    end = min(len(normalized), start + limit)
    return ("…" if start else "") + normalized[start:end] + ("…" if end < len(normalized) else "")


def _is_citable_locator(value: Any) -> bool:
    locator = dict(value or {}) if isinstance(value, dict) else {}
    return locator.get("is_citable") is True


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_role_fragment(
    row: BidEvidenceFragment,
    *,
    expected_role: str,
    citable: bool,
) -> dict[str, Any]:
    locator = dict(row.locator_json or {})
    normalized = str(row.normalized_text or "").strip()
    if (
        str(locator.get("schema_version") or "") != "bid.evidence.chunk.v2"
        or str(locator.get("fragment_role") or "") != expected_role
        or (locator.get("is_citable") is True) != citable
        or not normalized
        or _sha256(normalized) != str(row.text_hash)
        or str(locator.get("text_hash") or "") != str(row.text_hash)
        or canonical_hash(locator) != str(row.locator_hash)
    ):
        raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
    return locator


def _validate_retrieval_entry(
    entry: BidEvidenceRetrievalEntry,
    child: BidEvidenceFragment,
) -> dict[str, Any]:
    locator = _validate_role_fragment(
        child,
        expected_role="retrieval_child",
        citable=False,
    )
    source_atom_ids = [str(value) for value in entry.source_atom_ids_json or []]
    source_atom_keys = [str(value) for value in entry.source_atom_keys_json or []]
    stable_payload = {
        "retrieval_child_key": str(entry.retrieval_child_key),
        "section_parent_key": str(entry.section_parent_key),
        "ordinal": int(entry.ordinal),
        "page_start": int(entry.page_start),
        "page_end": int(entry.page_end),
        "retrieval_hash": str(entry.retrieval_hash),
        "child_text_hash": str(entry.child_text_hash),
        "source_atom_keys": source_atom_keys,
        "source_atoms_hash": str(entry.source_atoms_hash),
    }
    stable_payload["entry_hash"] = canonical_hash(stable_payload)
    if (
        str(entry.retrieval_child_id) != str(child.id)
        or str(entry.parse_run_id) != str(child.parse_run_id)
        or str(entry.document_version_id) != str(child.document_version_id)
        or str(entry.section_parent_id) != str(child.parent_id or "")
        or str(entry.retrieval_child_key) != str(locator.get("evidence_key") or "")
        or str(entry.child_text_hash) != str(child.text_hash)
        or _sha256(str(entry.retrieval_text)) != str(entry.retrieval_hash)
        or str(locator.get("retrieval_hash") or "") != str(entry.retrieval_hash)
        or int(entry.source_atom_count) != len(source_atom_ids)
        or len(source_atom_ids) != len(source_atom_keys)
        or len(source_atom_ids) < 1
        or len(source_atom_ids) != len(set(source_atom_ids))
        or str(entry.entry_hash) != str(stable_payload["entry_hash"])
    ):
        raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
    return locator


def _validate_entry_atoms(
    entry: BidEvidenceRetrievalEntry,
    atoms_by_id: dict[str, BidEvidenceFragment],
) -> None:
    source_atom_ids = [str(value) for value in entry.source_atom_ids_json or []]
    source_atom_keys = [str(value) for value in entry.source_atom_keys_json or []]
    descriptors: list[dict[str, str]] = []
    for atom_id, atom_key in zip(source_atom_ids, source_atom_keys):
        atom = atoms_by_id.get(atom_id)
        if atom is None:
            raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
        locator = _validate_role_fragment(
            atom,
            expected_role="evidence_atom",
            citable=True,
        )
        if (
            str(atom.parent_id or "") != str(entry.retrieval_child_id)
            or str(atom.parse_run_id) != str(entry.parse_run_id)
            or str(atom.document_version_id) != str(entry.document_version_id)
            or str(locator.get("evidence_key") or "") != atom_key
        ):
            raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
        descriptors.append(
            {
                "evidence_key": atom_key,
                "locator_hash": str(atom.locator_hash),
                "text_hash": str(atom.text_hash),
            }
        )
    if canonical_hash(descriptors) != str(entry.source_atoms_hash):
        raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")


class BidEvidenceMcpService:
    def __init__(
        self,
        db: Session,
        *,
        scope: BidEvidenceMcpScope,
        retrieval_profile_version: str = LEGACY_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version: str = LEGACY_QUERY_PLANNER_PROFILE_VERSION,
        lexical_search_profile_version: str = LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
        semantic_search_profile_version: str = DISABLED_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version: str = (
            DISABLED_CANDIDATE_FUSION_PROFILE_VERSION
        ),
        semantic_provider: BidSemanticVectorProvider | None = None,
        rerank_profile_version: str = DISABLED_RERANK_PROFILE_VERSION,
        reranker_provider: BidRerankerProvider | None = None,
    ):
        self._db = db
        self._scope = scope
        self._retrieval_profile_version = str(retrieval_profile_version)
        self._query_optimizer_profile_version = str(query_optimizer_profile_version)
        self._lexical_search_profile_version = str(lexical_search_profile_version)
        self._semantic_search_profile_version = str(semantic_search_profile_version)
        self._candidate_fusion_profile_version = str(
            candidate_fusion_profile_version
        )
        self._semantic_provider = semantic_provider
        self._rerank_profile_version = str(rerank_profile_version)
        self._reranker_provider = reranker_provider
        if self._retrieval_profile_version not in {
            LEGACY_RETRIEVAL_PROFILE_VERSION,
            ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        }:
            raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_PROFILE_DISABLED")
        if self._query_optimizer_profile_version not in {
            LEGACY_QUERY_PLANNER_PROFILE_VERSION,
            QUERY_OPTIMIZER_PROFILE_VERSION,
        }:
            raise BidEvidenceMcpError("BID_EVIDENCE_QUERY_PROFILE_DISABLED")
        if (
            self._query_optimizer_profile_version == QUERY_OPTIMIZER_PROFILE_VERSION
            and self._retrieval_profile_version != ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
        ):
            raise BidEvidenceMcpError("BID_EVIDENCE_QUERY_PROFILE_DISABLED")
        if self._lexical_search_profile_version not in {
            LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
            FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        }:
            raise BidEvidenceMcpError("BID_EVIDENCE_LEXICAL_PROFILE_DISABLED")
        if (
            self._lexical_search_profile_version
            == FIELD_AWARE_LEXICAL_PROFILE_VERSION
            and (
                self._retrieval_profile_version
                != ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
                or self._query_optimizer_profile_version
                != QUERY_OPTIMIZER_PROFILE_VERSION
            )
        ):
            raise BidEvidenceMcpError("BID_EVIDENCE_LEXICAL_PROFILE_DISABLED")
        if self._semantic_search_profile_version not in {
            DISABLED_SEMANTIC_PROFILE_VERSION,
            RQ2A_SEMANTIC_PROFILE_VERSION,
        }:
            raise BidEvidenceMcpError("BID_EVIDENCE_SEMANTIC_PROFILE_DISABLED")
        if (
            self._semantic_search_profile_version == RQ2A_SEMANTIC_PROFILE_VERSION
            and (
                self._retrieval_profile_version
                != ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
                or self._query_optimizer_profile_version
                != QUERY_OPTIMIZER_PROFILE_VERSION
                or self._lexical_search_profile_version
                != FIELD_AWARE_LEXICAL_PROFILE_VERSION
                or self._semantic_provider is None
            )
        ):
            raise BidEvidenceMcpError("BID_EVIDENCE_SEMANTIC_PROFILE_DISABLED")
        if self._candidate_fusion_profile_version not in {
            DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
            RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
        }:
            raise BidEvidenceMcpError("BID_EVIDENCE_FUSION_PROFILE_DISABLED")
        if (
            self._candidate_fusion_profile_version
            == RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
            and (
                self._retrieval_profile_version
                != ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
                or self._query_optimizer_profile_version
                != QUERY_OPTIMIZER_PROFILE_VERSION
                or self._lexical_search_profile_version
                != FIELD_AWARE_LEXICAL_PROFILE_VERSION
                or self._semantic_search_profile_version
                != RQ2A_SEMANTIC_PROFILE_VERSION
                or self._semantic_provider is None
            )
        ):
            raise BidEvidenceMcpError("BID_EVIDENCE_FUSION_PROFILE_DISABLED")
        if self._rerank_profile_version not in {
            DISABLED_RERANK_PROFILE_VERSION,
            RQ2C_RERANK_PROFILE_VERSION,
        }:
            raise BidEvidenceMcpError("BID_EVIDENCE_RERANK_PROFILE_DISABLED")
        if (
            self._rerank_profile_version == RQ2C_RERANK_PROFILE_VERSION
            and (
                self._candidate_fusion_profile_version
                != RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
                or self._reranker_provider is None
            )
        ):
            raise BidEvidenceMcpError("BID_EVIDENCE_RERANK_PROFILE_DISABLED")
        authorized_run = (
            db.query(BidAnalysisRun.id)
            .filter(
                BidAnalysisRun.id == str(scope.run_id),
                BidAnalysisRun.assessment_id == str(scope.assessment_id),
                BidAnalysisRun.manifest_id == str(scope.manifest_id),
            )
            .one_or_none()
        )
        if authorized_run is None:
            raise BidEvidenceMcpError("BID_EVIDENCE_SCOPE_INVALID")

    @property
    def role_aware(self) -> bool:
        return self._retrieval_profile_version == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION

    @property
    def rq1c_query_optimizer(self) -> bool:
        return self._query_optimizer_profile_version == QUERY_OPTIMIZER_PROFILE_VERSION

    @property
    def rq1d_field_aware_lexical(self) -> bool:
        return (
            self._lexical_search_profile_version
            == FIELD_AWARE_LEXICAL_PROFILE_VERSION
        )

    @property
    def rq2a_semantic_recall(self) -> bool:
        return (
            self._semantic_search_profile_version
            == RQ2A_SEMANTIC_PROFILE_VERSION
        )

    @property
    def rq2b_candidate_fusion(self) -> bool:
        return (
            self._candidate_fusion_profile_version
            == RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
        )

    @property
    def rq2c_lightweight_rerank(self) -> bool:
        return self._rerank_profile_version == RQ2C_RERANK_PROFILE_VERSION

    def _scoped_query(self):
        return (
            self._db.query(BidEvidenceFragment)
            .join(
                BidManifestDocument,
                BidManifestDocument.document_version_id
                == BidEvidenceFragment.document_version_id,
            )
            .join(
                BidDocumentParseHead,
                (BidDocumentParseHead.document_version_id
                 == BidEvidenceFragment.document_version_id)
                & (BidDocumentParseHead.current_run_id
                   == BidEvidenceFragment.parse_run_id),
            )
            .filter(BidManifestDocument.manifest_id == self._scope.manifest_id)
        )

    def _rows_by_id(self, evidence_ids: Iterable[str]) -> list[BidEvidenceFragment]:
        normalized = sorted(set(str(value) for value in evidence_ids))
        if not normalized:
            return []
        rows = self._scoped_query().filter(BidEvidenceFragment.id.in_(tuple(normalized))).all()
        if {str(row.id) for row in rows} != set(normalized):
            raise BidEvidenceMcpError("BID_EVIDENCE_REFERENCE_OUT_OF_SCOPE")
        return rows

    def _role_aware_index_scope(self) -> _RoleAwareIndexScope:
        manifest = (
            self._db.query(BidDocumentManifest)
            .filter(
                BidDocumentManifest.id == self._scope.manifest_id,
                BidDocumentManifest.assessment_id == self._scope.assessment_id,
            )
            .one_or_none()
        )
        if manifest is None:
            raise BidEvidenceMcpError("BID_EVIDENCE_SCOPE_INVALID")
        members = (
            self._db.query(BidManifestDocument)
            .filter(BidManifestDocument.manifest_id == self._scope.manifest_id)
            .order_by(
                BidManifestDocument.order_no.asc(),
                BidManifestDocument.document_version_id.asc(),
            )
            .all()
        )
        if not members:
            raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_NOT_READY")
        document_roles = {
            str(row.document_version_id): str(row.role) for row in members
        }
        version_ids = tuple(document_roles)
        heads = {
            str(row.document_version_id): row
            for row in (
                self._db.query(BidEvidenceRetrievalHead)
                .filter(
                    BidEvidenceRetrievalHead.document_version_id.in_(version_ids),
                    BidEvidenceRetrievalHead.retrieval_profile_version
                    == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                )
                .all()
            )
        }
        parse_heads = {
            str(row.document_version_id): row
            for row in (
                self._db.query(BidDocumentParseHead)
                .filter(BidDocumentParseHead.document_version_id.in_(version_ids))
                .all()
            )
        }
        index_ids = tuple(
            str(row.current_index_id) for row in heads.values()
        )
        indexes_by_id = {
            str(row.id): row
            for row in (
                self._db.query(BidEvidenceRetrievalIndex)
                .filter(BidEvidenceRetrievalIndex.id.in_(index_ids))
                .all()
                if index_ids
                else []
            )
        }
        indexes: list[BidEvidenceRetrievalIndex] = []
        parse_runs = {
            str(row.id): row
            for row in (
                self._db.query(BidDocumentParseRun)
                .filter(BidDocumentParseRun.id.in_(tuple(index.parse_run_id for index in indexes_by_id.values())))
                .all()
                if indexes_by_id
                else []
            )
        }
        for member in members:
            version_id = str(member.document_version_id)
            head = heads.get(version_id)
            parse_head = parse_heads.get(version_id)
            index = (
                indexes_by_id.get(str(head.current_index_id))
                if head is not None
                else None
            )
            parse_run = (
                parse_runs.get(str(index.parse_run_id))
                if index is not None
                else None
            )
            if (
                head is None
                or parse_head is None
                or index is None
                or str(index.status) != "ready"
                or not index.result_hash
                or str(head.current_parse_run_id) != str(parse_head.current_run_id)
                or str(index.parse_run_id) != str(parse_head.current_run_id)
                or str(index.document_version_id) != version_id
                or str(index.retrieval_profile_version)
                != ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
            ):
                raise BidEvidenceMcpError(
                    "BID_EVIDENCE_RETRIEVAL_INDEX_NOT_READY"
                )
            if (
                parse_run is None
                or str(parse_run.result_hash or "") != str(index.source_result_hash)
                or retrieval_index_input_hash(parse_run) != str(index.input_hash)
            ):
                raise BidEvidenceMcpError(
                    "BID_EVIDENCE_RETRIEVAL_INDEX_INVALID"
                )
            indexes.append(index)
        index_set_hash = canonical_hash(
            {
                "retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                "manifest_id": self._scope.manifest_id,
                "manifest_hash": str(manifest.manifest_hash),
                "documents": [
                    {
                        "document_version_id": str(member.document_version_id),
                        "document_role": str(member.role),
                        "order_no": int(member.order_no),
                        "index_result_hash": str(index.result_hash),
                    }
                    for member, index in zip(members, indexes)
                ],
            }
        )
        return _RoleAwareIndexScope(
            manifest_hash=str(manifest.manifest_hash),
            document_roles=document_roles,
            indexes=tuple(indexes),
            index_set_hash=index_set_hash,
        )

    def _search_role_aware(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query or len(query) > 500:
            raise BidEvidenceMcpError("BID_EVIDENCE_QUERY_INVALID")
        top_k = min(max(int(arguments.get("top_k", 5)), 1), 8)
        index_scope = self._role_aware_index_scope()
        allowed_versions = set(index_scope.document_roles)
        requested_versions = {
            str(value) for value in arguments.get("document_version_ids") or []
        }
        if requested_versions:
            allowed_versions &= requested_versions
        requested_roles = {
            str(value) for value in arguments.get("document_roles") or []
        }
        if requested_roles:
            allowed_versions = {
                version_id
                for version_id in allowed_versions
                if index_scope.document_roles[version_id] in requested_roles
            }
        document_types = {
            str(value) for value in arguments.get("document_types") or []
        }
        if document_types and allowed_versions:
            typed_versions = {
                str(row[0])
                for row in (
                    self._db.query(BidDocumentVersion.id)
                    .join(
                        BidDocument,
                        BidDocument.id == BidDocumentVersion.document_id,
                    )
                    .filter(
                        BidDocumentVersion.id.in_(tuple(allowed_versions)),
                        BidDocument.document_type.in_(tuple(document_types)),
                    )
                    .all()
                )
            }
            allowed_versions &= typed_versions
        index_ids = tuple(str(row.id) for row in index_scope.indexes)
        rows = (
            self._db.query(BidEvidenceRetrievalEntry, BidEvidenceFragment)
            .join(
                BidEvidenceFragment,
                BidEvidenceFragment.id
                == BidEvidenceRetrievalEntry.retrieval_child_id,
            )
            .filter(
                BidEvidenceRetrievalEntry.index_id.in_(index_ids),
                BidEvidenceRetrievalEntry.document_version_id.in_(
                    tuple(allowed_versions)
                ),
            )
            .order_by(
                BidEvidenceRetrievalEntry.document_version_id.asc(),
                BidEvidenceRetrievalEntry.ordinal.asc(),
                BidEvidenceRetrievalEntry.id.asc(),
            )
            .limit(MAX_SCOPED_CANDIDATES + 1)
            .all()
            if allowed_versions
            else []
        )
        corpus_truncated = len(rows) > MAX_SCOPED_CANDIDATES
        rows = rows[:MAX_SCOPED_CANDIDATES]
        parent_ids = tuple(
            sorted({str(entry.section_parent_id) for entry, _child in rows})
        )
        parent_by_id = {
            str(row.id): row
            for row in (
                self._db.query(BidEvidenceFragment)
                .filter(BidEvidenceFragment.id.in_(parent_ids))
                .all()
                if parent_ids
                else []
            )
        }
        parent_text = {
            parent_id: str(row.normalized_text)
            for parent_id, row in parent_by_id.items()
        }
        for entry, child in rows:
            _validate_retrieval_entry(entry, child)
            parent = parent_by_id.get(str(entry.section_parent_id))
            if parent is None:
                raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
            parent_locator = _validate_role_fragment(
                parent,
                expected_role="section_parent",
                citable=False,
            )
            if (
                str(parent.parse_run_id) != str(entry.parse_run_id)
                or str(parent.document_version_id) != str(entry.document_version_id)
                or str(parent_locator.get("evidence_key") or "")
                != str(entry.section_parent_key)
            ):
                raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
        if self.rq1c_query_optimizer:
            plan = optimize_bid_evidence_query(query)
            query_plan_payload = plan.to_payload()
            planned_query_items = [
                {
                    "text": item.text,
                    "kind": item.kind,
                    "weight": item.weight,
                    "field_codes": list(item.field_codes),
                    "answer_shapes": list(item.answer_shapes),
                }
                for item in plan.query_items
            ]
        else:
            plan = plan_tender_query(
                query,
                max_query_count=3,
                enable_semantic_fact_companion=True,
                enable_atomic_fact_slots=True,
            )
            query_plan_payload = plan.to_payload()
            planned_query_items = [
                {
                    "text": value,
                    "kind": "planned",
                    "weight": 1.0,
                    "field_codes": [],
                    "answer_shapes": [],
                }
                for value in list(plan.queries)[:3]
            ]
        aggregate: defaultdict[str, float] = defaultdict(float)
        matched: defaultdict[str, list[str]] = defaultdict(list)
        routes: list[dict[str, Any]] = []
        warnings: set[str] = set()
        entry_by_child = {
            str(entry.retrieval_child_id): (entry, child)
            for entry, child in rows
        }
        parent_children: defaultdict[str, list[str]] = defaultdict(list)
        parent_stable_keys: dict[str, str] = {}
        for entry, _child in rows:
            parent_id = str(entry.section_parent_id)
            parent_key = str(entry.section_parent_key)
            if (
                parent_id in parent_stable_keys
                and parent_stable_keys[parent_id] != parent_key
            ):
                raise BidEvidenceMcpError(
                    "BID_EVIDENCE_RETRIEVAL_INDEX_INVALID"
                )
            parent_stable_keys[parent_id] = parent_key
            parent_children[parent_id].append(
                str(entry.retrieval_child_id)
            )
        fusion_semantic_result = None
        if self.rq2b_candidate_fusion:
            try:
                fusion_semantic_result = recall_semantic_children(
                    self._db,
                    provider=self._semantic_provider,
                    source_indexes=index_scope.indexes,
                    source_index_set_hash=index_scope.index_set_hash,
                    allowed_document_versions=sorted(allowed_versions),
                    allowed_retrieval_child_ids=sorted(entry_by_child),
                    query_items=planned_query_items,
                    top_k=RQ2B_CHANNEL_CANDIDATE_DEPTH,
                )
            except BidSemanticProviderError as exc:
                public_code = (
                    "BID_SEMANTIC_PROVIDER_UNAVAILABLE"
                    if bool(getattr(exc, "retryable", False))
                    else "BID_SEMANTIC_PROVIDER_INVALID"
                )
                raise BidEvidenceMcpError(public_code) from exc
            except BidEvidenceSemanticIndexError as exc:
                raise BidEvidenceMcpError(exc.code) from exc
        if self.rq2a_semantic_recall and not self.rq2b_candidate_fusion:
            routes = []
            for query_index, planned_item in enumerate(planned_query_items):
                route_payload = route_tender_query(
                    str(planned_item["text"])
                ).to_payload(
                    query_id=f"q{query_index + 1}",
                    query_kind=str(planned_item["kind"]),
                )
                route_payload["executed_mode"] = "semantic"
                routes.append(route_payload)
            try:
                semantic_result = recall_semantic_children(
                    self._db,
                    provider=self._semantic_provider,
                    source_indexes=index_scope.indexes,
                    source_index_set_hash=index_scope.index_set_hash,
                    allowed_document_versions=sorted(allowed_versions),
                    allowed_retrieval_child_ids=sorted(entry_by_child),
                    query_items=planned_query_items,
                    top_k=top_k,
                )
            except BidSemanticProviderError as exc:
                public_code = (
                    "BID_SEMANTIC_PROVIDER_UNAVAILABLE"
                    if bool(getattr(exc, "retryable", False))
                    else "BID_SEMANTIC_PROVIDER_INVALID"
                )
                raise BidEvidenceMcpError(public_code) from exc
            except BidEvidenceSemanticIndexError as exc:
                raise BidEvidenceMcpError(exc.code) from exc
            semantic_hits = []
            for ranked in semantic_result.hits:
                pair = entry_by_child.get(ranked.retrieval_child_id)
                if pair is None:
                    raise BidEvidenceMcpError(
                        "BID_EVIDENCE_SEMANTIC_PROVIDER_HIT_OUT_OF_SCOPE"
                    )
                entry, child = pair
                if str(entry.index_id) != ranked.retrieval_index_id:
                    raise BidEvidenceMcpError(
                        "BID_EVIDENCE_SEMANTIC_INDEX_INVALID"
                    )
                semantic_hits.append(
                    {
                        "evidence_id": str(child.id),
                        "fragment_role": "retrieval_child",
                        "is_citable": False,
                        "document_version_id": str(entry.document_version_id),
                        "document_role": index_scope.document_roles[
                            str(entry.document_version_id)
                        ],
                        "parse_run_id": str(entry.parse_run_id),
                        "retrieval_index_id": str(entry.index_id),
                        "semantic_index_id": ranked.semantic_index_id,
                        "section_parent_id": str(entry.section_parent_id),
                        "source_atom_ids": list(entry.source_atom_ids_json or []),
                        "locator": dict(child.locator_json or {}),
                        "locator_hash": str(child.locator_hash),
                        "text_hash": str(child.text_hash),
                        "retrieval_hash": str(entry.retrieval_hash),
                        "excerpt": _excerpt(str(child.normalized_text), query),
                        "score": ranked.rank_score,
                        "semantic_score": ranked.semantic_score,
                        "semantic_vector_hash": ranked.vector_hash,
                        "matched_queries": list(ranked.matched_queries),
                        "context_read": False,
                    }
                )
            warnings = (
                ["SCOPED_CORPUS_CANDIDATE_LIMIT_REACHED"]
                if corpus_truncated
                else []
            )
            payload = {
                "contract": ROLE_AWARE_MCP_CONTRACT_VERSION,
                "retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                "query_optimizer_profile_version": QUERY_OPTIMIZER_PROFILE_VERSION,
                "lexical_search_profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
                "semantic_search_profile_version": RQ2A_SEMANTIC_PROFILE_VERSION,
                "status": "ok" if semantic_hits else "no_result",
                "run_id": self._scope.run_id,
                "manifest_id": self._scope.manifest_id,
                "manifest_hash": index_scope.manifest_hash,
                "index_set_hash": index_scope.index_set_hash,
                "semantic_index_set_hash": semantic_result.semantic_index_set_hash,
                "semantic_model": semantic_result.model_descriptor.stable_payload(),
                "query_plan": query_plan_payload,
                "retrieval_routes": routes,
                "retrieval_mode": "role_aware_child_semantic_query_rrf_rq2a",
                "hits": semantic_hits,
                "warnings": warnings,
            }
            payload["result_hash"] = canonical_hash(payload)
            return payload
        child_candidates = [
            _TextCandidate(
                key=str(entry.retrieval_child_id),
                text=str(entry.retrieval_text),
                tie_key=(
                    str(entry.retrieval_child_key)
                    if self.rq1d_field_aware_lexical
                    else ""
                ),
            )
            for entry, _child in rows
        ]
        parent_candidates = [
            _TextCandidate(
                key=parent_id,
                text=text,
                tie_key=(
                    parent_stable_keys[parent_id]
                    if self.rq1d_field_aware_lexical
                    else ""
                ),
            )
            for parent_id, text in sorted(parent_text.items())
            if text.strip()
        ]
        lexical_corpus = None
        projection_by_child = {}
        if self.rq1d_field_aware_lexical and rows:
            expected_entry_hashes = {
                str(entry.retrieval_child_id): str(entry.entry_hash)
                for entry, _child in rows
            }
            lexical_cache_key = canonical_hash(
                {
                    "profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
                    "index_set_hash": index_scope.index_set_hash,
                    "allowed_document_versions": sorted(allowed_versions),
                }
            )
            lexical_corpus = get_cached_field_aware_corpus(
                lexical_cache_key,
                expected_entry_hashes=expected_entry_hashes,
            )
            if lexical_corpus is None:
                atom_ids = tuple(
                    sorted(
                        {
                            str(atom_id)
                            for entry, _child in rows
                            for atom_id in entry.source_atom_ids_json or []
                        }
                    )
                )
                atoms_by_id = {
                    str(atom.id): atom
                    for atom in (
                        self._db.query(BidEvidenceFragment)
                        .filter(BidEvidenceFragment.id.in_(atom_ids))
                        .all()
                        if atom_ids
                        else []
                    )
                }
                if len(atoms_by_id) != len(atom_ids):
                    raise BidEvidenceMcpError(
                        "BID_EVIDENCE_RETRIEVAL_INDEX_INVALID"
                    )
                lexical_sources: list[LexicalChildSource] = []
                for entry, child in rows:
                    _validate_entry_atoms(entry, atoms_by_id)
                    child_locator = dict(child.locator_json or {})
                    lexical_atoms: list[LexicalAtomSource] = []
                    for atom_id in entry.source_atom_ids_json or []:
                        atom = atoms_by_id[str(atom_id)]
                        atom_locator = dict(atom.locator_json or {})
                        lexical_atoms.append(
                            LexicalAtomSource(
                                evidence_id=str(atom.id),
                                text=str(atom.normalized_text),
                                block_type=str(
                                    atom_locator.get("block_type") or "paragraph"
                                ),
                                section_path=tuple(
                                    str(value)
                                    for value in atom_locator.get("section_path") or []
                                ),
                            )
                        )
                    lexical_sources.append(
                        LexicalChildSource(
                            child_id=str(child.id),
                            child_key=str(entry.retrieval_child_key),
                            entry_hash=str(entry.entry_hash),
                            child_text=str(child.normalized_text),
                            section_path=tuple(
                                str(value)
                                for value in child_locator.get("section_path") or []
                            ),
                            atoms=tuple(lexical_atoms),
                        )
                    )
                lexical_corpus = build_field_aware_lexical_corpus(
                    lexical_sources
                )
                cache_field_aware_corpus(lexical_cache_key, lexical_corpus)
            projection_by_child = lexical_corpus.projection_map()
        matched_channels: defaultdict[str, set[str]] = defaultdict(set)
        for query_index, planned_item in enumerate(planned_query_items):
            planned_query = str(planned_item["text"])
            query_weight = float(planned_item["weight"])
            route = route_tender_query(planned_query)
            route_payload = route.to_payload(
                query_id=f"q{query_index + 1}",
                query_kind=str(planned_item["kind"]),
            )
            if self.rq2b_candidate_fusion:
                route_payload["executed_mode"] = "hybrid"
            routes.append(route_payload)
            if (
                route.mode in {"semantic", "hybrid"}
                and not self.rq2b_candidate_fusion
            ):
                warnings.add("SEMANTIC_BACKEND_UNAVAILABLE_BM25_FALLBACK")
            if lexical_corpus is not None:
                field_ranks = rank_field_aware_bm25f(
                    planned_query,
                    lexical_corpus,
                    field_codes=list(planned_item.get("field_codes") or []),
                    answer_shapes=list(planned_item.get("answer_shapes") or []),
                )
                for rank, ranked in enumerate(field_ranks, 1):
                    field_weight = (
                        STRUCTURED_FIELD_RRF_WEIGHT
                        if set(ranked.matched_channels)
                        & {"table_key", "table_value", "table_row"}
                        else FIELD_AWARE_CHILD_RRF_WEIGHT
                    )
                    aggregate[ranked.child_id] += (
                        field_weight
                        * query_weight
                        / (RRF_K + rank)
                    )
                    matched_channels[ranked.child_id].update(
                        ranked.matched_channels
                    )
                    if planned_query not in matched[ranked.child_id]:
                        matched[ranked.child_id].append(planned_query)
                for rank, (child_id, score) in enumerate(
                    _bm25_rank_candidates(planned_query, child_candidates), 1
                ):
                    aggregate[child_id] += (
                        LEGACY_CHILD_RRF_WEIGHT
                        * query_weight
                        / (RRF_K + rank)
                    )
                    if query_index == 0:
                        aggregate[child_id] += (
                            ORIGINAL_QUERY_ANCHOR_WEIGHT / (RRF_K + rank)
                        )
                    if score > 0 and planned_query not in matched[child_id]:
                        matched[child_id].append(planned_query)
            else:
                for rank, (child_id, score) in enumerate(
                    _bm25_rank_candidates(planned_query, child_candidates), 1
                ):
                    aggregate[child_id] += query_weight / (RRF_K + rank)
                    if score > 0 and len(matched[child_id]) < len(planned_query_items):
                        matched[child_id].append(planned_query)
            for rank, (parent_id, _score) in enumerate(
                _bm25_rank_candidates(planned_query, parent_candidates), 1
            ):
                parent_weight = (
                    FIELD_AWARE_PARENT_WEIGHT
                    if lexical_corpus is not None
                    else 0.35
                )
                parent_boost = (parent_weight * query_weight) / (RRF_K + rank)
                for child_id in parent_children.get(parent_id, []):
                    aggregate[child_id] += parent_boost
        ranked_child_ids = sorted(
            aggregate,
            key=lambda value: (
                -aggregate[value],
                str(entry_by_child[value][0].retrieval_child_key),
                value,
            ),
        )
        fusion_result = None
        fused_by_child = {}
        semantic_by_child = {}
        rerank_result = None
        reranked_by_child = {}
        if self.rq2b_candidate_fusion:
            if fusion_semantic_result is None:
                raise BidEvidenceMcpError(
                    "BID_EVIDENCE_CANDIDATE_FUSION_INVALID"
                )
            lexical_projection_set_hash = (
                lexical_corpus.corpus_hash
                if lexical_corpus is not None
                else canonical_hash(
                    {
                        "contract_version": "bid.evidence.lexical-search.v1",
                        "profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
                        "source_index_set_hash": index_scope.index_set_hash,
                        "projections": [],
                    }
                )
            )
            query_plan_hash = str(query_plan_payload.get("plan_hash") or "")
            lexical_candidates = [
                CandidateChannelHit(
                    child_id=child_id,
                    child_key=str(
                        entry_by_child[child_id][0].retrieval_child_key
                    ),
                    rank=rank,
                    source_score=float(aggregate[child_id]),
                )
                for rank, child_id in enumerate(
                    ranked_child_ids[:RQ2B_CHANNEL_CANDIDATE_DEPTH],
                    1,
                )
            ]
            semantic_candidates = []
            for rank, semantic_hit in enumerate(
                fusion_semantic_result.hits[:RQ2B_CHANNEL_CANDIDATE_DEPTH],
                1,
            ):
                child_id = str(semantic_hit.retrieval_child_id)
                pair = entry_by_child.get(child_id)
                if (
                    pair is None
                    or str(pair[0].retrieval_child_key)
                    != str(semantic_hit.retrieval_child_key)
                    or str(pair[0].index_id)
                    != str(semantic_hit.retrieval_index_id)
                ):
                    raise BidEvidenceMcpError(
                        "BID_EVIDENCE_SEMANTIC_PROVIDER_HIT_OUT_OF_SCOPE"
                    )
                semantic_by_child[child_id] = semantic_hit
                semantic_candidates.append(
                    CandidateChannelHit(
                        child_id=child_id,
                        child_key=str(semantic_hit.retrieval_child_key),
                        rank=rank,
                        source_score=float(semantic_hit.rank_score),
                    )
                )
            try:
                fusion_result = fuse_candidate_channels(
                    lexical=lexical_candidates,
                    semantic=semantic_candidates,
                    source_index_set_hash=index_scope.index_set_hash,
                    lexical_projection_set_hash=lexical_projection_set_hash,
                    semantic_index_set_hash=(
                        fusion_semantic_result.semantic_index_set_hash
                    ),
                    query_plan_hash=query_plan_hash,
                )
            except BidHybridCandidateFusionError as exc:
                raise BidEvidenceMcpError(exc.code) from exc
            fused_by_child = {
                value.child_id: value for value in fusion_result.candidates
            }
            ranked_child_ids = [
                value.child_id for value in fusion_result.candidates
            ]
            if self.rq2c_lightweight_rerank and fusion_result.candidates:
                try:
                    rerank_result = rerank_frozen_candidates(
                        query=str(query_plan_payload.get("original_query") or query),
                        candidates=tuple(
                            RerankCandidateInput(
                                child_id=value.child_id,
                                child_key=value.child_key,
                                parent_key=str(
                                    entry_by_child[value.child_id][0].section_parent_key
                                ),
                                fusion_rank=rank,
                                fusion_score=value.fusion_score,
                                lexical_rank=value.lexical_rank,
                                semantic_rank=value.semantic_rank,
                                retrieval_hash=str(
                                    entry_by_child[value.child_id][0].retrieval_hash
                                ),
                                text=str(
                                    entry_by_child[value.child_id][0].retrieval_text
                                ),
                            )
                            for rank, value in enumerate(
                                fusion_result.candidates[:RQ2C_CANDIDATE_WINDOW],
                                1,
                            )
                        ),
                        fusion_result_hash=fusion_result.result_hash,
                        query_plan_hash=query_plan_hash,
                        top_k=top_k,
                        provider=self._reranker_provider,
                    )
                except BidLightweightRerankerError as exc:
                    raise BidEvidenceMcpError(str(exc) or exc.code) from exc
                reranked_by_child = {
                    value.child_id: value for value in rerank_result.candidates
                }
                ranked_child_ids = list(rerank_result.selected_child_ids)
        if lexical_corpus is not None:
            ordered: list[str] = []
            overflow: list[str] = []
            parent_counts: Counter[str] = Counter()
            for child_id in ranked_child_ids:
                entry, _child = entry_by_child[child_id]
                parent_id = str(entry.section_parent_id)
                if parent_counts[parent_id] < MAX_CHILDREN_PER_PARENT:
                    ordered.append(child_id)
                    parent_counts[parent_id] += 1
                else:
                    overflow.append(child_id)
                if len(ordered) >= top_k:
                    break
            if len(ordered) < top_k:
                ordered.extend(overflow[: top_k - len(ordered)])
        else:
            ordered = ranked_child_ids[:top_k]
        hits = []
        for child_id in ordered:
            entry, child = entry_by_child[child_id]
            fused = fused_by_child.get(child_id)
            reranked = reranked_by_child.get(child_id)
            semantic_hit = semantic_by_child.get(child_id)
            hit_queries = list(matched[child_id])
            if semantic_hit is not None:
                for semantic_query in semantic_hit.matched_queries:
                    if semantic_query not in hit_queries:
                        hit_queries.append(semantic_query)
            hit = {
                "evidence_id": child_id,
                "fragment_role": "retrieval_child",
                "is_citable": False,
                "document_version_id": str(entry.document_version_id),
                "document_role": index_scope.document_roles[
                    str(entry.document_version_id)
                ],
                "parse_run_id": str(entry.parse_run_id),
                "retrieval_index_id": str(entry.index_id),
                "section_parent_id": str(entry.section_parent_id),
                "source_atom_ids": list(entry.source_atom_ids_json or []),
                "locator": dict(child.locator_json or {}),
                "locator_hash": str(child.locator_hash),
                "text_hash": str(child.text_hash),
                "retrieval_hash": str(entry.retrieval_hash),
                "excerpt": _excerpt(str(child.normalized_text), query),
                "score": (
                    reranked.rerank_score
                    if reranked is not None
                    else fused.fusion_score
                    if fused is not None
                    else round(aggregate[child_id], 10)
                ),
                "matched_queries": hit_queries,
                "context_read": False,
            }
            if lexical_corpus is not None:
                projection = projection_by_child[child_id]
                hit["matched_channels"] = sorted(matched_channels[child_id])
                hit["lexical_projection_hash"] = projection.projection_hash
            if fused is not None:
                hit["fusion_score"] = fused.fusion_score
                hit["fusion_rank"] = next(
                    index
                    for index, value in enumerate(fusion_result.candidates, 1)
                    if value.child_id == child_id
                )
                hit["fusion_channels"] = list(fused.matched_channels)
                hit["lexical_rank"] = fused.lexical_rank
                hit["semantic_rank"] = fused.semantic_rank
                hit["lexical_source_score"] = fused.lexical_source_score
                hit["semantic_source_score"] = fused.semantic_source_score
                if semantic_hit is not None:
                    hit["semantic_index_id"] = semantic_hit.semantic_index_id
                    hit["semantic_score"] = semantic_hit.semantic_score
                    hit["semantic_vector_hash"] = semantic_hit.vector_hash
            if reranked is not None:
                hit["rerank_score"] = reranked.rerank_score
                hit["rerank_input_hash"] = reranked.input_hash
                hit["rerank_protected_anchor"] = reranked.protected_anchor
                hit["rerank_promotion_sequence"] = (
                    reranked.promotion_sequence
                )
                hit["rerank_replaced_child_key"] = (
                    reranked.replaced_child_key
                )
            hits.append(hit)
        if corpus_truncated:
            warnings.add("SCOPED_CORPUS_CANDIDATE_LIMIT_REACHED")
        payload = {
            "contract": ROLE_AWARE_MCP_CONTRACT_VERSION,
            "retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            "status": "ok" if hits else "no_result",
            "run_id": self._scope.run_id,
            "manifest_id": self._scope.manifest_id,
            "manifest_hash": index_scope.manifest_hash,
            "index_set_hash": index_scope.index_set_hash,
            "query_plan": query_plan_payload,
            "retrieval_routes": routes,
            "retrieval_mode": (
                "role_aware_child_bm25f_semantic_bce_guarded_rerank_rq2c"
                if self.rq2c_lightweight_rerank
                else "role_aware_child_bm25f_semantic_weighted_rrf_rq2b"
                if self.rq2b_candidate_fusion
                else "role_aware_child_bm25_baseline_bm25f_parent_weighted_rrf_rq1d"
                if self.rq1d_field_aware_lexical
                else "role_aware_child_bm25_parent_weighted_rrf_rq1c"
                if self.rq1c_query_optimizer
                else "role_aware_child_bm25_parent_rrf"
            ),
            "hits": hits,
            "warnings": sorted(warnings),
        }
        if self.rq1c_query_optimizer:
            payload["query_optimizer_profile_version"] = (
                QUERY_OPTIMIZER_PROFILE_VERSION
            )
        if self.rq1d_field_aware_lexical:
            payload["lexical_search_profile_version"] = (
                FIELD_AWARE_LEXICAL_PROFILE_VERSION
            )
        if self.rq2b_candidate_fusion:
            payload["semantic_search_profile_version"] = (
                RQ2A_SEMANTIC_PROFILE_VERSION
            )
            payload["candidate_fusion_profile_version"] = (
                RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
            )
            payload["semantic_index_set_hash"] = (
                fusion_semantic_result.semantic_index_set_hash
            )
            payload["semantic_model"] = (
                fusion_semantic_result.model_descriptor.stable_payload()
            )
            payload["candidate_fusion"] = fusion_result.to_payload()
        if self.rq2c_lightweight_rerank:
            payload["rerank_profile_version"] = RQ2C_RERANK_PROFILE_VERSION
            payload["rerank_model"] = (
                self._reranker_provider.descriptor.stable_payload()
            )
            if rerank_result is not None:
                payload["candidate_rerank"] = rerank_result.to_payload()
        if lexical_corpus is not None:
            payload["lexical_projection_set_hash"] = lexical_corpus.corpus_hash
            payload["boilerplate_term_count"] = len(
                lexical_corpus.boilerplate_terms
            )
            payload["lexical_projection"] = {
                "schema_version": "bid.evidence.lexical-search.v1",
                "profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
                "source_index_set_hash": index_scope.index_set_hash,
                "projection_set_hash": lexical_corpus.corpus_hash,
                "projection_count": len(lexical_corpus.projections),
                "boilerplate_term_count": len(
                    lexical_corpus.boilerplate_terms
                ),
                "channels": list(CHANNEL_ORDER),
            }
        elif self.rq1d_field_aware_lexical:
            warnings.add("SCOPED_LEXICAL_CORPUS_EMPTY")
            payload["warnings"] = sorted(warnings)
        payload["result_hash"] = canonical_hash(payload)
        return payload

    def _read_role_aware(self, arguments: dict[str, Any]) -> dict[str, Any]:
        evidence_ids = sorted(
            {str(value) for value in arguments.get("evidence_ids") or []}
        )
        if not 1 <= len(evidence_ids) <= 4:
            raise BidEvidenceMcpError("BID_EVIDENCE_READ_IDS_INVALID")
        expansion = str(arguments.get("expansion") or "none")
        if expansion not in {"none", "neighbors", "parent_section", "bounded_pages"}:
            raise BidEvidenceMcpError("BID_EVIDENCE_EXPANSION_INVALID")
        radius = min(max(int(arguments.get("radius", 1)), 0), 2)
        max_pages = min(max(int(arguments.get("max_pages", 2)), 1), 4)
        index_scope = self._role_aware_index_scope()
        index_ids = tuple(str(row.id) for row in index_scope.indexes)
        anchors = self._rows_by_id(evidence_ids)
        if {str(row.id) for row in anchors} != set(evidence_ids):
            raise BidEvidenceMcpError("BID_EVIDENCE_REFERENCE_OUT_OF_SCOPE")
        direct_child_ids: set[str] = set()
        direct_atom_ids: set[str] = set()
        base_child_ids: set[str] = set()
        anchor_pages: set[int] = set()
        for anchor in anchors:
            role = str((anchor.locator_json or {}).get("fragment_role") or "")
            if role == "retrieval_child" and not _is_citable_locator(anchor.locator_json):
                direct_child_ids.add(str(anchor.id))
                base_child_ids.add(str(anchor.id))
            elif role == "evidence_atom" and _is_citable_locator(anchor.locator_json):
                if not anchor.parent_id:
                    raise BidEvidenceMcpError(
                        "BID_EVIDENCE_REFERENCE_ROLE_INVALID"
                    )
                direct_atom_ids.add(str(anchor.id))
                base_child_ids.add(str(anchor.parent_id))
            else:
                raise BidEvidenceMcpError("BID_EVIDENCE_REFERENCE_ROLE_INVALID")
            page_no = (anchor.locator_json or {}).get("page_no")
            if page_no is not None:
                anchor_pages.add(int(page_no))
        base_entries = (
            self._db.query(BidEvidenceRetrievalEntry)
            .filter(
                BidEvidenceRetrievalEntry.index_id.in_(index_ids),
                BidEvidenceRetrievalEntry.retrieval_child_id.in_(
                    tuple(base_child_ids)
                ),
            )
            .all()
        )
        entry_by_child = {
            str(row.retrieval_child_id): row for row in base_entries
        }
        if set(entry_by_child) != base_child_ids:
            raise BidEvidenceMcpError("BID_EVIDENCE_REFERENCE_OUT_OF_SCOPE")
        selected_entries: dict[str, BidEvidenceRetrievalEntry] = dict(entry_by_child)
        expansion_truncated = False
        if expansion != "none":
            for base in base_entries:
                sibling_query = (
                    self._db.query(BidEvidenceRetrievalEntry)
                    .filter(
                        BidEvidenceRetrievalEntry.index_id == str(base.index_id),
                        BidEvidenceRetrievalEntry.section_parent_id
                        == str(base.section_parent_id),
                    )
                )
                if expansion == "neighbors":
                    before = (
                        sibling_query.filter(
                            BidEvidenceRetrievalEntry.ordinal < int(base.ordinal)
                        )
                        .order_by(
                            BidEvidenceRetrievalEntry.ordinal.desc(),
                            BidEvidenceRetrievalEntry.id.desc(),
                        )
                        .limit(radius)
                        .all()
                    )
                    after = (
                        sibling_query.filter(
                            BidEvidenceRetrievalEntry.ordinal > int(base.ordinal)
                        )
                        .order_by(
                            BidEvidenceRetrievalEntry.ordinal.asc(),
                            BidEvidenceRetrievalEntry.id.asc(),
                        )
                        .limit(radius)
                        .all()
                    )
                    siblings = list(reversed(before)) + [base] + after
                else:
                    if expansion == "bounded_pages":
                        scoped_pages = anchor_pages or {int(base.page_start)}
                        sibling_query = sibling_query.filter(
                            BidEvidenceRetrievalEntry.page_start
                            <= max(scoped_pages) + max_pages - 1,
                            BidEvidenceRetrievalEntry.page_end
                            >= min(scoped_pages) - max_pages + 1,
                        )
                    siblings = (
                        sibling_query.order_by(
                            BidEvidenceRetrievalEntry.ordinal.asc(),
                            BidEvidenceRetrievalEntry.id.asc(),
                        )
                        .limit(MAX_READ_ENTRIES + 1)
                        .all()
                    )
                    if len(siblings) > MAX_READ_ENTRIES:
                        expansion_truncated = True
                        siblings = siblings[:MAX_READ_ENTRIES]
                for sibling in siblings:
                    selected_entries[str(sibling.retrieval_child_id)] = sibling
        selected_children = {
            str(row.id): row
            for row in self._rows_by_id(selected_entries)
        }
        if set(selected_children) != set(selected_entries):
            raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
        for child_id, entry in selected_entries.items():
            _validate_retrieval_entry(entry, selected_children[child_id])
        integrity_atom_ids = {
            str(atom_id)
            for entry in selected_entries.values()
            for atom_id in entry.source_atom_ids_json or []
        }
        integrity_atoms = {
            str(row.id): row for row in self._rows_by_id(integrity_atom_ids)
        }
        if set(integrity_atoms) != integrity_atom_ids:
            raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
        for entry in selected_entries.values():
            _validate_entry_atoms(entry, integrity_atoms)
        atom_ids: set[str] = set(direct_atom_ids)
        for child_id, entry in selected_entries.items():
            if expansion == "none" and child_id not in direct_child_ids:
                continue
            atom_ids.update(str(value) for value in entry.source_atom_ids_json or [])
        if not atom_ids <= set(integrity_atoms):
            raise BidEvidenceMcpError("BID_EVIDENCE_REFERENCE_ROLE_INVALID")
        atoms = [integrity_atoms[atom_id] for atom_id in atom_ids]
        selected_child_ids = set(selected_entries)
        valid_atoms: list[BidEvidenceFragment] = []
        for atom in atoms:
            locator = dict(atom.locator_json or {})
            source_entry = selected_entries.get(str(atom.parent_id or ""))
            if (
                source_entry is None
                or str(atom.id)
                not in {str(value) for value in source_entry.source_atom_ids_json or []}
            ):
                raise BidEvidenceMcpError("BID_EVIDENCE_RETRIEVAL_INDEX_INVALID")
            if expansion == "bounded_pages" and anchor_pages:
                page_no = locator.get("page_no")
                if page_no is None or not any(
                    abs(int(page_no) - page) < max_pages for page in anchor_pages
                ):
                    continue
            valid_atoms.append(atom)
        valid_atoms.sort(
            key=lambda row: (
                str(row.document_version_id),
                int(row.ordinal),
                str(row.id),
            )
        )
        items = []
        remaining_chars = MAX_READ_CHARS
        for atom in valid_atoms:
            if len(items) >= MAX_READ_ITEMS or remaining_chars <= 0:
                break
            text = str(atom.normalized_text)[: min(4000, remaining_chars)]
            if not text:
                continue
            entry = selected_entries[str(atom.parent_id)]
            items.append(
                {
                    "evidence_id": str(atom.id),
                    "fragment_role": "evidence_atom",
                    "is_citable": True,
                    "document_version_id": str(atom.document_version_id),
                    "document_role": index_scope.document_roles[
                        str(atom.document_version_id)
                    ],
                    "parse_run_id": str(atom.parse_run_id),
                    "retrieval_index_id": str(entry.index_id),
                    "source_child_id": str(atom.parent_id),
                    "section_parent_id": str(entry.section_parent_id),
                    "locator": dict(atom.locator_json or {}),
                    "locator_hash": str(atom.locator_hash),
                    "text": text,
                    "text_hash": str(atom.text_hash),
                    "context_read": True,
                    "is_anchor": (
                        str(atom.id) in direct_atom_ids
                        or str(atom.parent_id) in direct_child_ids
                    ),
                }
            )
            remaining_chars -= len(text)
        payload = {
            "contract": ROLE_AWARE_MCP_CONTRACT_VERSION,
            "retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            "status": "ok",
            "run_id": self._scope.run_id,
            "manifest_id": self._scope.manifest_id,
            "manifest_hash": index_scope.manifest_hash,
            "index_set_hash": index_scope.index_set_hash,
            "expansion": expansion,
            "items": items,
            "warnings": sorted(
                {
                    *(
                        {"EXPANSION_ENTRY_LIMIT_REACHED"}
                        if expansion_truncated
                        else set()
                    ),
                    *(
                        {"RESULT_TRUNCATED"}
                        if len(valid_atoms) > len(items)
                        else set()
                    ),
                }
            ),
        }
        payload["result_hash"] = canonical_hash(payload)
        return payload

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.role_aware:
            return self._search_role_aware(arguments)
        query = str(arguments.get("query") or "").strip()
        if not query or len(query) > 500:
            raise BidEvidenceMcpError("BID_EVIDENCE_QUERY_INVALID")
        top_k = min(max(int(arguments.get("top_k", 5)), 1), 8)
        scoped = self._scoped_query()
        document_version_ids = sorted(
            set(str(value) for value in arguments.get("document_version_ids") or [])
        )
        if document_version_ids:
            scoped = scoped.filter(
                BidEvidenceFragment.document_version_id.in_(tuple(document_version_ids))
            )
        document_types = sorted(
            set(str(value) for value in arguments.get("document_types") or [])
        )
        if document_types:
            scoped = (
                scoped.join(
                    BidDocumentVersion,
                    BidDocumentVersion.id == BidEvidenceFragment.document_version_id,
                )
                .join(BidDocument, BidDocument.id == BidDocumentVersion.document_id)
                .filter(BidDocument.document_type.in_(tuple(document_types)))
            )
        rows = (
            scoped.order_by(
                BidEvidenceFragment.document_version_id.asc(),
                BidEvidenceFragment.ordinal.asc(),
                BidEvidenceFragment.id.asc(),
            )
            .limit(MAX_SCOPED_CANDIDATES + 1)
            .all()
        )
        corpus_truncated = len(rows) > MAX_SCOPED_CANDIDATES
        rows = rows[:MAX_SCOPED_CANDIDATES]
        plan = plan_tender_query(
            query,
            max_query_count=3,
            enable_semantic_fact_companion=True,
            enable_atomic_fact_slots=True,
        )
        planned_queries = list(plan.queries)[:3]
        aggregate: defaultdict[str, float] = defaultdict(float)
        matched: defaultdict[str, list[str]] = defaultdict(list)
        routes = []
        warnings: set[str] = set()
        for query_index, planned_query in enumerate(planned_queries):
            route = route_tender_query(planned_query)
            routes.append(route.to_payload(query_id=f"q{query_index + 1}", query_kind="planned"))
            if route.mode in {"semantic", "hybrid"}:
                warnings.add("SEMANTIC_BACKEND_UNAVAILABLE_BM25_FALLBACK")
            for rank, (evidence_id, score) in enumerate(_bm25_rank(planned_query, rows), 1):
                aggregate[evidence_id] += 1.0 / (RRF_K + rank)
                if score > 0 and len(matched[evidence_id]) < 3:
                    matched[evidence_id].append(planned_query)
        by_id = {str(row.id): row for row in rows}
        ordered = sorted(aggregate, key=lambda value: (-aggregate[value], value))[:top_k]
        hits = []
        for evidence_id in ordered:
            row = by_id[evidence_id]
            hits.append(
                {
                    "evidence_id": evidence_id,
                    "document_version_id": str(row.document_version_id),
                    "parse_run_id": str(row.parse_run_id),
                    "locator": dict(row.locator_json or {}),
                    "locator_hash": str(row.locator_hash),
                    "text_hash": str(row.text_hash),
                    "excerpt": _excerpt(str(row.normalized_text), query),
                    "score": round(aggregate[evidence_id], 10),
                    "matched_queries": matched[evidence_id],
                    "context_read": False,
                }
            )
        if corpus_truncated:
            warnings.add("SCOPED_CORPUS_CANDIDATE_LIMIT_REACHED")
        payload = {
            "contract": MCP_CONTRACT_VERSION,
            "status": "ok" if hits else "no_result",
            "run_id": self._scope.run_id,
            "manifest_id": self._scope.manifest_id,
            "query_plan": plan.to_payload(),
            "retrieval_routes": routes,
            "retrieval_mode": "bm25_rrf",
            "hits": hits,
            "warnings": sorted(warnings),
        }
        payload["result_hash"] = canonical_hash(payload)
        return payload

    def read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.role_aware:
            return self._read_role_aware(arguments)
        evidence_ids = sorted(set(str(value) for value in arguments.get("evidence_ids") or []))
        if not 1 <= len(evidence_ids) <= 4:
            raise BidEvidenceMcpError("BID_EVIDENCE_READ_IDS_INVALID")
        anchors = self._rows_by_id(evidence_ids)
        expansion = str(arguments.get("expansion") or "none")
        radius = min(max(int(arguments.get("radius", 1)), 0), 2)
        expanded: dict[str, BidEvidenceFragment] = {str(row.id): row for row in anchors}
        for anchor in anchors:
            query = self._scoped_query().filter(
                BidEvidenceFragment.parse_run_id == anchor.parse_run_id
            )
            if expansion == "neighbors":
                query = query.filter(
                    BidEvidenceFragment.ordinal.between(
                        max(0, int(anchor.ordinal) - radius), int(anchor.ordinal) + radius
                    )
                )
            elif expansion == "parent_section":
                identifiers = [str(anchor.id)]
                if anchor.parent_id:
                    identifiers.append(str(anchor.parent_id))
                query = query.filter(
                    or_(
                        BidEvidenceFragment.id.in_(tuple(identifiers)),
                        BidEvidenceFragment.parent_id.in_(tuple(identifiers)),
                    )
                )
            elif expansion == "bounded_pages":
                page = (anchor.locator_json or {}).get("page_no")
                max_pages = min(max(int(arguments.get("max_pages", 2)), 1), 4)
                candidates = query.order_by(BidEvidenceFragment.ordinal.asc()).all()
                for row in candidates:
                    row_page = (row.locator_json or {}).get("page_no")
                    if page is not None and row_page is not None and abs(int(row_page) - int(page)) < max_pages:
                        expanded[str(row.id)] = row
                continue
            elif expansion != "none":
                raise BidEvidenceMcpError("BID_EVIDENCE_EXPANSION_INVALID")
            for row in query.order_by(BidEvidenceFragment.ordinal.asc()).limit(40).all():
                expanded[str(row.id)] = row
        ordered = sorted(
            expanded.values(),
            key=lambda row: (str(row.document_version_id), int(row.ordinal), str(row.id)),
        )
        items = []
        remaining_chars = MAX_READ_CHARS
        for row in ordered:
            if len(items) >= MAX_READ_ITEMS or remaining_chars <= 0:
                break
            text = str(row.normalized_text)[: min(4000, remaining_chars)]
            if not text:
                continue
            items.append({
                "evidence_id": str(row.id),
                "document_version_id": str(row.document_version_id),
                "parse_run_id": str(row.parse_run_id),
                "locator": dict(row.locator_json or {}),
                "locator_hash": str(row.locator_hash),
                "text": text,
                "text_hash": str(row.text_hash),
                "context_read": True,
                "is_anchor": str(row.id) in set(evidence_ids),
            })
            remaining_chars -= len(text)
        payload = {
            "contract": MCP_CONTRACT_VERSION,
            "status": "ok",
            "run_id": self._scope.run_id,
            "manifest_id": self._scope.manifest_id,
            "expansion": expansion,
            "items": items,
            "warnings": ["RESULT_TRUNCATED"] if len(expanded) > len(items) else [],
        }
        payload["result_hash"] = canonical_hash(payload)
        return payload
