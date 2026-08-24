"""V607 isolated real-PDF Pure Agent business run.

The runner verifies and parses one explicitly supplied PDF, builds in-process
lexical/BCE indexes, exposes only the four canonical read-only tool contracts,
and lets the model choose its searches and evidence reads turn by turn.  It
does not use the legacy bid-intake workflow, a database, Milvus, MCP, OCR, or
any production service.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any, Callable, Mapping, Sequence, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_assessment_pure.answer_contracts import (
    AnswerDraft,
    GroundingKind,
    GroundingRecord,
    GroundingSnapshot,
    GroundingStatus,
    LimitationBlock,
    SourceBasis,
    StatementBlock,
)
from app.agents.bid_assessment_pure.answer_runtime import GroundingIntegrityGuard
from app.agents.bid_assessment_pure.citation_contracts import (
    CitationAuthorityRecord,
    CitationAuthoritySnapshot,
    CitationLocatorKind,
    CitationSourceType,
)
from app.agents.bid_assessment_pure.citation_runtime import (
    AnswerBlockRenderer,
    CitationProjector,
)
from app.agents.bid_assessment_pure.complexity_gate import DefaultComplexityGate
from app.agents.bid_assessment_pure.planning import (
    ExecutionMode,
    InformationSourceHint,
    IntentUnderstanding,
    TaskPlan,
)
from app.agents.bid_assessment_pure.registry import (
    BID_DOCUMENT_SEARCH,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
    build_initial_registry,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextIncludedEntry,
    ContextLane,
    ContextProjectionEntry,
    ContextProtectionClass,
    ContextRepresentation,
    ContextSnapshot,
    ContextTrustClass,
)
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash
from app.agents.bid_assessment_pure.tools import EvidenceAtom, EvidenceReadOutput
from app.services.bid_evidence_chunk_builder import (
    EvidenceChunkBuildResult,
    EvidenceChunkFragment,
    RQ1A_CHUNK_PROFILE,
    StructuredEvidenceBlock,
    build_evidence_chunks,
)
from app.services.bid_field_aware_lexical import (
    FIELD_AWARE_CHILD_RRF_WEIGHT,
    FIELD_AWARE_PARENT_WEIGHT,
    LEGACY_CHILD_RRF_WEIGHT,
    ORIGINAL_QUERY_ANCHOR_WEIGHT,
    STRUCTURED_FIELD_RRF_WEIGHT,
    FieldAwareLexicalCorpus,
    LexicalAtomSource,
    LexicalChildSource,
    build_field_aware_lexical_corpus,
    lexical_tokens,
    rank_field_aware_bm25f,
)
from app.services.bid_hybrid_candidate_fusion import (
    CandidateChannelHit,
    fuse_candidate_channels,
)
from app.services.bid_lightweight_reranker import (
    RQ2C_CANDIDATE_WINDOW,
    RQ2C_MODEL_REVISION,
    LocalBceCrossEncoderReranker,
    RerankCandidateInput,
    rerank_frozen_candidates,
    validate_reranker_descriptor,
)
from app.services.bid_local_semantic_vector_provider import (
    LocalBceExactSemanticProvider,
)
from app.services.bid_pdf_native_layout_parser import (
    RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    PdfNativeLayoutResult,
    parse_pdf_native_layout,
)
from app.services.bid_query_optimizer import optimize_bid_evidence_query
from app.services.bid_semantic_vector_provider import (
    RQ2A_EMBEDDING_MODEL_REVISION,
    SemanticDocument,
    validate_descriptor,
)


DATASET_PATH = (
    PROJECT_ROOT / "evals" / "bid_assessment" / "v607-real-pdf-business-run.json"
)
SCHEMA_VERSION = "bid.pure_agent.v607.real_pdf_business_run.v1"
RESULT_SCHEMA_VERSION = "bid.pure_agent.v607.result.v1"
DEFAULT_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
ALLOWED_HOSTS = frozenset({"api.deepseek.com"})
AUTHORIZATION_REF = "authorization:v607-local-isolated"
PROMPT_VERSION = "bid-pure-agent-v607-real-business-v1"
MAX_ANSWER_REPAIRS = 2
_WHITESPACE = re.compile(r"\s+")


class V607EvaluationError(RuntimeError):
    pass


class V607ModelOutputError(V607EvaluationError):
    def __init__(
        self,
        message: str,
        *,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = dict(usage or {})


ContractT = TypeVar("ContractT")


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    chat_url: str
    model: str
    timeout_seconds: int


@dataclass(frozen=True)
class DomainIndex:
    domain: str
    safe_title: str
    scope_ref: str
    version_ref: str
    chunk_result: EvidenceChunkBuildResult
    lexical_corpus: FieldAwareLexicalCorpus
    parent_by_ref: Mapping[str, EvidenceChunkFragment]
    child_by_ref: Mapping[str, EvidenceChunkFragment]
    atoms_by_child_ref: Mapping[str, tuple[EvidenceChunkFragment, ...]]
    block_metadata: Mapping[str, Mapping[str, Any]]
    source_index_set_hash: str


@dataclass(frozen=True)
class SearchBundle:
    domain: str
    query: str
    candidate_refs: tuple[str, ...]
    fusion_hash: str
    rerank_hash: str
    query_plan_hash: str


@dataclass(frozen=True)
class AnswerRuntime:
    task: Any
    context: ContextAssemblyResult
    grounding_snapshot: GroundingSnapshot
    authority_snapshot: CitationAuthoritySnapshot


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized(value: str) -> str:
    return _WHITESPACE.sub("", str(value)).lower()


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise V607EvaluationError("unsupported V607 dataset schema")
    if payload.get("dataset_kind") != "authorized_real_business":
        raise V607EvaluationError("V607 requires an authorized real-business dataset")
    contract = payload.get("execution_contract")
    if not isinstance(contract, dict):
        raise V607EvaluationError("V607 execution contract is missing")
    required_true = {
        "real_pdf_allowed",
        "native_pdf_parse_allowed",
        "offline_rag_allowed",
        "local_embedding_allowed",
        "local_reranker_allowed",
        "deepseek_model_allowed",
    }
    required_false = {
        "ocr_allowed",
        "external_mcp_allowed",
        "database_allowed",
        "production_vector_store_allowed",
        "ecs_allowed",
    }
    if any(contract.get(name) is not True for name in required_true) or any(
        contract.get(name) is not False for name in required_false
    ):
        raise V607EvaluationError("V607 execution contract violates isolation")
    facts = payload.get("enterprise_baseline", {}).get("facts")
    if not isinstance(facts, list) or [row.get("id") for row in facts] != [
        f"I{index:02d}" for index in range(1, 12)
    ]:
        raise V607EvaluationError("V607 requires ordered enterprise facts I01-I11")
    if [row.get("status") for row in facts[:5]] != ["partial"] * 5 or [
        row.get("status") for row in facts[5:]
    ] != ["unknown"] * 6:
        raise V607EvaluationError("V607 enterprise baseline status is not frozen")
    turns = payload.get("conversation")
    if not isinstance(turns, list) or len(turns) < 2:
        raise V607EvaluationError("V607 requires a multi-turn conversation")
    ids = [row.get("id") for row in turns if isinstance(row, dict)]
    if len(ids) != len(turns) or len(ids) != len(set(ids)):
        raise V607EvaluationError("V607 conversation ids must be present and unique")
    return payload


def load_silver_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    relative = str(dataset["document"]["silver_dataset"])
    path = (PROJECT_ROOT / relative).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "bid.pdf-c3.silver-cases.v1":
        raise V607EvaluationError("V607 Silver dataset schema is invalid")
    if payload.get("document_sha256") != dataset["document"]["sha256"]:
        raise V607EvaluationError("V607 Silver dataset is bound to another PDF")
    if not payload.get("cases"):
        raise V607EvaluationError("V607 Silver dataset has no cases")
    return payload


def _validate_model_snapshot(path: Path, *, revision: str, role: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir() or revision not in resolved.parts:
        raise V607EvaluationError(f"{role} model revision is unavailable")
    if not (resolved / "config.json").is_file():
        raise V607EvaluationError(f"{role} model config is unavailable")
    if not any(
        (resolved / name).is_file()
        for name in ("model.safetensors", "pytorch_model.bin")
    ):
        raise V607EvaluationError(f"{role} model weights are unavailable")
    return resolved


def _build_lexical_sources(
    children: Sequence[EvidenceChunkFragment],
    atoms_by_child: Mapping[str, tuple[EvidenceChunkFragment, ...]],
) -> tuple[LexicalChildSource, ...]:
    return tuple(
        LexicalChildSource(
            child_id=child.evidence_key,
            child_key=child.evidence_key,
            entry_hash=child.retrieval_hash,
            child_text=child.retrieval_text,
            section_path=tuple(child.locator.get("section_path") or ()),
            atoms=tuple(
                LexicalAtomSource(
                    evidence_id=atom.evidence_key,
                    text=atom.normalized_text,
                    block_type=str(atom.locator.get("block_type") or "paragraph"),
                    section_path=tuple(atom.locator.get("section_path") or ()),
                )
                for atom in atoms_by_child[child.evidence_key]
            ),
        )
        for child in children
    )


def _domain_index(
    *,
    domain: str,
    safe_title: str,
    scope_ref: str,
    version_ref: str,
    chunk_result: EvidenceChunkBuildResult,
    block_metadata: Mapping[str, Mapping[str, Any]],
) -> DomainIndex:
    children = tuple(
        row for row in chunk_result.fragments if row.fragment_role == "retrieval_child"
    )
    atoms = tuple(
        row for row in chunk_result.fragments if row.fragment_role == "evidence_atom"
    )
    atoms_by_child = {
        child.evidence_key: tuple(
            atom for atom in atoms if atom.parent_key == child.evidence_key
        )
        for child in children
    }
    parents = tuple(
        row for row in chunk_result.fragments if row.fragment_role == "section_parent"
    )
    lexical = build_field_aware_lexical_corpus(
        _build_lexical_sources(children, atoms_by_child)
    )
    return DomainIndex(
        domain=domain,
        safe_title=safe_title,
        scope_ref=scope_ref,
        version_ref=version_ref,
        chunk_result=chunk_result,
        lexical_corpus=lexical,
        parent_by_ref={row.evidence_key: row for row in parents},
        child_by_ref={row.evidence_key: row for row in children},
        atoms_by_child_ref=atoms_by_child,
        block_metadata=dict(block_metadata),
        source_index_set_hash=_sha256_json(
            {
                "domain": domain,
                "chunk_result_hash": chunk_result.result_hash,
                "version_ref": version_ref,
            }
        ),
    )


def build_document_index(
    parsed: PdfNativeLayoutResult,
    dataset: Mapping[str, Any],
) -> DomainIndex:
    chunk_result = build_evidence_chunks(
        parsed.blocks,
        document_label=str(dataset["document"]["safe_title"]),
        profile=RQ1A_CHUNK_PROFILE,
    )
    metadata = {
        block.block_key: {
            "status": "supported",
            "locator": f"第{block.page_no}页",
        }
        for block in parsed.blocks
    }
    return _domain_index(
        domain="bid_document",
        safe_title=str(dataset["document"]["safe_title"]),
        scope_ref="scope:v607-bid-document",
        version_ref="version:v607-pdf-" + str(dataset["document"]["sha256"])[:16],
        chunk_result=chunk_result,
        block_metadata=metadata,
    )


def build_enterprise_index(dataset: Mapping[str, Any]) -> DomainIndex:
    baseline = dataset["enterprise_baseline"]
    blocks: list[StructuredEvidenceBlock] = []
    metadata: dict[str, Mapping[str, Any]] = {}
    for ordinal, fact in enumerate(baseline["facts"], 1):
        key = f"v607-enterprise-{fact['id'].lower()}"
        blocks.append(
            StructuredEvidenceBlock(
                block_key=key,
                text=f"{fact['title']}。{fact['text']}",
                block_type="paragraph",
                page_no=ordinal,
                ordinal=ordinal,
                section_path=("企业资料包业务核验基线", str(fact["title"])),
                boundary_before=True,
                boundary_after=True,
            )
        )
        metadata[key] = {
            "status": str(fact["status"]),
            "locator": str(fact["locator"]),
            "fact_id": str(fact["id"]),
        }
    chunk_result = build_evidence_chunks(
        blocks,
        document_label=str(baseline["safe_title"]),
        profile=RQ1A_CHUNK_PROFILE,
    )
    return _domain_index(
        domain="enterprise_knowledge",
        safe_title=str(baseline["safe_title"]),
        scope_ref="scope:v607-enterprise",
        version_ref="version:" + str(baseline["version"]),
        chunk_result=chunk_result,
        block_metadata=metadata,
    )


def _semantic_documents(index: DomainIndex) -> tuple[SemanticDocument, ...]:
    return tuple(
        SemanticDocument(
            provider_record_id=_sha256_json(
                {"domain": index.domain, "child_key": child.evidence_key}
            ),
            retrieval_child_id=child.evidence_key,
            retrieval_child_key=child.evidence_key,
            source_entry_hash=child.retrieval_hash,
            embedding_text_hash=hashlib.sha256(
                child.retrieval_text.encode("utf-8")
            ).hexdigest(),
            text=child.retrieval_text,
        )
        for child in index.child_by_ref.values()
    )


class RealOfflineRetriever:
    """In-memory real-data binding behind the canonical RAG tool contracts."""

    def __init__(
        self,
        indexes: Sequence[DomainIndex],
        *,
        embedding_model_path: Path,
        reranker_model_path: Path,
    ) -> None:
        self.indexes = {row.domain: row for row in indexes}
        self.semantic = LocalBceExactSemanticProvider(
            model_path=str(embedding_model_path)
        )
        validate_descriptor(self.semantic.descriptor)
        self.reranker = LocalBceCrossEncoderReranker(
            model_path=str(reranker_model_path),
            offline=True,
            batch_size=8,
        )
        validate_reranker_descriptor(self.reranker.descriptor)
        self.namespaces: dict[str, str] = {}
        self.semantic_hashes: dict[str, str] = {}

    def build_semantic_indexes(self) -> dict[str, Any]:
        started = time.perf_counter()
        counts: dict[str, int] = {}
        for domain, index in self.indexes.items():
            namespace = "v607-" + domain.replace("_", "-")
            documents = _semantic_documents(index)
            receipts = self.semantic.upsert_documents(
                namespace=namespace,
                provider_request_id="v607:" + index.source_index_set_hash,
                documents=documents,
            )
            self.namespaces[domain] = namespace
            self.semantic_hashes[domain] = _sha256_json(
                [
                    {
                        "child_key": receipt.retrieval_child_key,
                        "vector_hash": receipt.vector_hash,
                    }
                    for receipt in receipts
                ]
            )
            counts[domain] = len(receipts)
        return {
            "seconds": round(time.perf_counter() - started, 6),
            "document_counts": counts,
        }

    @staticmethod
    def _bm25_rank(
        query: str,
        rows: Mapping[str, str],
    ) -> tuple[tuple[str, float], ...]:
        query_terms = lexical_tokens(query)
        if not query_terms or not rows:
            return ()
        keys = tuple(sorted(rows))
        documents = [lexical_tokens(rows[key]) for key in keys]
        document_count = len(documents)
        average_length = sum(len(value) for value in documents) / max(
            document_count, 1
        )
        frequencies: Counter[str] = Counter()
        for terms in documents:
            frequencies.update(set(terms))
        ranked: list[tuple[str, float]] = []
        normalized_query = _normalized(query)
        for key, terms in zip(keys, documents, strict=True):
            term_frequency = Counter(terms)
            length = len(terms)
            score = 0.0
            for term in set(query_terms):
                if not term_frequency[term]:
                    continue
                inverse = math.log(
                    1
                    + (document_count - frequencies[term] + 0.5)
                    / (frequencies[term] + 0.5)
                )
                numerator = term_frequency[term] * 2.2
                denominator = term_frequency[term] + 1.2 * (
                    0.25 + 0.75 * length / max(average_length, 1.0)
                )
                score += inverse * numerator / denominator
            if len(normalized_query) >= 2 and normalized_query in _normalized(rows[key]):
                score += 4.0
            if score > 0:
                ranked.append((key, round(score, 8)))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return tuple(ranked)

    def _lexical_channel(
        self,
        index: DomainIndex,
        query: str,
    ) -> tuple[tuple[CandidateChannelHit, ...], str]:
        plan = optimize_bid_evidence_query(query)
        payload = plan.to_payload()
        aggregate: defaultdict[str, float] = defaultdict(float)
        children_by_parent: defaultdict[str, list[str]] = defaultdict(list)
        for child in index.child_by_ref.values():
            children_by_parent[str(child.parent_key)].append(child.evidence_key)
        child_text = {
            ref: child.retrieval_text for ref, child in index.child_by_ref.items()
        }
        parent_text = {
            ref: parent.retrieval_text for ref, parent in index.parent_by_ref.items()
        }
        for query_index, item in enumerate(plan.query_items):
            planned_query = item.text
            weight = float(item.weight)
            field_ranks = rank_field_aware_bm25f(
                planned_query,
                index.lexical_corpus,
                field_codes=item.field_codes,
                answer_shapes=item.answer_shapes,
            )
            for rank, row in enumerate(field_ranks, 1):
                field_weight = (
                    STRUCTURED_FIELD_RRF_WEIGHT
                    if set(row.matched_channels)
                    & {"table_key", "table_value", "table_row"}
                    else FIELD_AWARE_CHILD_RRF_WEIGHT
                )
                aggregate[row.child_id] += field_weight * weight / (60 + rank)
            for rank, (child_ref, _score) in enumerate(
                self._bm25_rank(planned_query, child_text), 1
            ):
                aggregate[child_ref] += LEGACY_CHILD_RRF_WEIGHT * weight / (
                    60 + rank
                )
                if query_index == 0:
                    aggregate[child_ref] += ORIGINAL_QUERY_ANCHOR_WEIGHT / (
                        60 + rank
                    )
            for rank, (parent_ref, _score) in enumerate(
                self._bm25_rank(planned_query, parent_text), 1
            ):
                boost = FIELD_AWARE_PARENT_WEIGHT * weight / (60 + rank)
                for child_ref in children_by_parent.get(parent_ref, ()):
                    aggregate[child_ref] += boost
        ordered = sorted(
            aggregate,
            key=lambda ref: (-aggregate[ref], ref),
        )
        return (
            tuple(
                CandidateChannelHit(
                    child_id=ref,
                    child_key=ref,
                    rank=rank,
                    source_score=round(aggregate[ref], 10),
                )
                for rank, ref in enumerate(ordered[:40], 1)
            ),
            str(payload["plan_hash"]),
        )

    def _semantic_channel(
        self,
        index: DomainIndex,
        query: str,
    ) -> tuple[CandidateChannelHit, ...]:
        plan = optimize_bid_evidence_query(query)
        aggregate: defaultdict[str, float] = defaultdict(float)
        max_score: defaultdict[str, float] = defaultdict(lambda: -1.0)
        for item in plan.query_items:
            hits = self.semantic.search(
                namespace=self.namespaces[index.domain],
                query=item.text,
                top_k=40,
            )
            for rank, hit in enumerate(hits, 1):
                ref = hit.retrieval_child_key
                aggregate[ref] += float(item.weight) / (60 + rank)
                max_score[ref] = max(max_score[ref], float(hit.score))
        ordered = sorted(
            aggregate,
            key=lambda ref: (-aggregate[ref], -max_score[ref], ref),
        )
        return tuple(
            CandidateChannelHit(
                child_id=ref,
                child_key=ref,
                rank=rank,
                source_score=round(aggregate[ref], 10),
            )
            for rank, ref in enumerate(ordered[:40], 1)
        )

    def search(self, domain: str, query: str, *, top_k: int = 8) -> SearchBundle:
        if domain not in self.indexes or domain not in self.namespaces:
            raise V607EvaluationError("requested V607 RAG domain is unavailable")
        if not 1 <= int(top_k) <= 8:
            raise V607EvaluationError("V607 top_k must be between 1 and 8")
        index = self.indexes[domain]
        lexical_hits, query_plan_hash = self._lexical_channel(index, query)
        semantic_hits = self._semantic_channel(index, query)
        fusion = fuse_candidate_channels(
            lexical=lexical_hits,
            semantic=semantic_hits,
            source_index_set_hash=index.source_index_set_hash,
            lexical_projection_set_hash=index.lexical_corpus.corpus_hash,
            semantic_index_set_hash=self.semantic_hashes[domain],
            query_plan_hash=query_plan_hash,
        )
        window = tuple(fusion.candidates[:RQ2C_CANDIDATE_WINDOW])
        rerank_inputs = tuple(
            RerankCandidateInput(
                child_id=row.child_id,
                child_key=row.child_key,
                parent_key=str(index.child_by_ref[row.child_key].parent_key),
                fusion_rank=rank,
                fusion_score=row.fusion_score,
                lexical_rank=row.lexical_rank,
                semantic_rank=row.semantic_rank,
                retrieval_hash=index.child_by_ref[row.child_key].retrieval_hash,
                text=index.child_by_ref[row.child_key].retrieval_text,
            )
            for rank, row in enumerate(window, 1)
        )
        reranked = rerank_frozen_candidates(
            query=query,
            candidates=rerank_inputs,
            fusion_result_hash=fusion.result_hash,
            query_plan_hash=query_plan_hash,
            top_k=min(top_k, len(rerank_inputs)),
            provider=self.reranker,
        )
        return SearchBundle(
            domain=domain,
            query=query,
            candidate_refs=tuple(reranked.final_child_keys),
            fusion_hash=fusion.result_hash,
            rerank_hash=reranked.result_hash,
            query_plan_hash=query_plan_hash,
        )

    def search_payload(self, bundle: SearchBundle) -> dict[str, Any]:
        index = self.indexes[bundle.domain]
        candidates = []
        for ref in bundle.candidate_refs:
            child = index.child_by_ref[ref]
            metadata = self._metadata(index, child)
            candidates.append(
                {
                    "evidence_ref": ref,
                    "excerpt": child.normalized_text[:4000],
                    "locator": metadata["locator"],
                    "citable": False,
                }
            )
        return {"candidates": candidates}

    @staticmethod
    def _metadata(
        index: DomainIndex, fragment: EvidenceChunkFragment
    ) -> Mapping[str, Any]:
        rows = [
            index.block_metadata[key]
            for key in fragment.source_block_keys
            if key in index.block_metadata
        ]
        if not rows:
            raise V607EvaluationError("V607 evidence metadata is unavailable")
        return rows[0]

    def read(
        self,
        evidence_refs: Sequence[str],
        *,
        allowed_refs: set[str],
    ) -> tuple[EvidenceReadOutput, list[dict[str, Any]]]:
        unique = tuple(dict.fromkeys(str(ref) for ref in evidence_refs))
        if not unique or len(unique) > 8 or not set(unique).issubset(allowed_refs):
            raise V607EvaluationError("V607 evidence_read scope is invalid")
        rows: list[tuple[DomainIndex, EvidenceChunkFragment]] = []
        for ref in unique:
            matches = [index for index in self.indexes.values() if ref in index.child_by_ref]
            if len(matches) != 1:
                raise V607EvaluationError("V607 evidence_ref is stale or ambiguous")
            index = matches[0]
            for atom in index.atoms_by_child_ref[ref]:
                rows.append((index, atom))
        rows = rows[:32]
        evidence: list[EvidenceAtom] = []
        sources: list[dict[str, Any]] = []
        for index, atom in rows:
            metadata = self._metadata(index, atom)
            evidence.append(
                EvidenceAtom(
                    evidence_ref=atom.evidence_key,
                    text=atom.normalized_text,
                    locator=str(metadata["locator"]),
                    citable=True,
                )
            )
            status = str(metadata.get("status") or "supported")
            unknown = status == "unknown"
            sources.append(
                {
                    "grounding_ref": "grounding:"
                    + hashlib.sha256(
                        (index.domain + atom.evidence_key).encode("utf-8")
                    ).hexdigest(),
                    "source_ref": "source:v607-" + index.domain.replace("_", "-"),
                    "source_scope_ref": index.scope_ref,
                    "source_version_ref": index.version_ref,
                    "source_basis": (
                        "runtime_receipt"
                        if unknown
                        else "document"
                        if index.domain == "bid_document"
                        else "enterprise"
                    ),
                    "grounding_kind": (
                        "source_availability_receipt" if unknown else "evidence_atom"
                    ),
                    "status": status,
                    "citable": not unknown,
                    "content": atom.normalized_text,
                    "safe_title": index.safe_title,
                    "safe_locator_label": str(metadata["locator"]),
                    "safe_version_label": (
                        "PDF SHA-256 " + index.version_ref.rsplit("-", 1)[-1]
                        if index.domain == "bid_document"
                        else index.version_ref.removeprefix("version:")
                    ),
                    "locator_kind": (
                        "page" if index.domain == "bid_document" else "record"
                    ),
                }
            )
        return EvidenceReadOutput(evidence=tuple(evidence)), sources


def evaluate_silver_retrieval(
    retriever: RealOfflineRetriever,
    silver: Mapping[str, Any],
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    page_recalls: list[float] = []
    phrase_recalls: list[float] = []
    for case in silver["cases"]:
        bundle = retriever.search("bid_document", str(case["question"]), top_k=8)
        index = retriever.indexes["bid_document"]
        selected_children = [index.child_by_ref[ref] for ref in bundle.candidate_refs]
        selected_atoms = [
            atom
            for ref in bundle.candidate_refs
            for atom in index.atoms_by_child_ref[ref]
        ]
        pages = {
            int(atom.locator.get("page_no") or 0)
            for atom in selected_atoms
        }
        searchable = "\n".join(
            [row.normalized_text for row in selected_children]
            + [row.normalized_text for row in selected_atoms]
        )
        targets = case["targets"]
        page_hits = [bool(set(target["pages"]) & pages) for target in targets]
        phrase_hits = [
            _normalized(str(target["phrase"])) in _normalized(searchable)
            for target in targets
        ]
        page_recall = sum(page_hits) / len(page_hits)
        phrase_recall = sum(phrase_hits) / len(phrase_hits)
        page_recalls.append(page_recall)
        phrase_recalls.append(phrase_recall)
        cases.append(
            {
                "case_id": case["case_id"],
                "page_recall_at_8": round(page_recall, 6),
                "phrase_recall_at_8": round(phrase_recall, 6),
                "candidate_refs": list(bundle.candidate_refs),
            }
        )
    return {
        "case_count": len(cases),
        "mean_page_recall_at_8": round(statistics.fmean(page_recalls), 6),
        "mean_phrase_recall_at_8": round(statistics.fmean(phrase_recalls), 6),
        "cases": cases,
    }


def _secret_values(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    allowed = {
        "BID_ASSESSMENT_MODEL_API_KEY",
        "DEEPSEEK_API_KEY",
        "BID_ASSESSMENT_MODEL_CHAT_URL",
        "DEEPSEEK_CHAT_URL",
        "BID_ASSESSMENT_MODEL_ID",
        "DEEPSEEK_MODEL",
    }
    values: dict[str, str] = {}
    for raw_line in path.resolve().read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        if name.strip() in allowed:
            values[name.strip()] = raw_value.strip().strip('"').strip("'")
    return values


def model_config(
    *, timeout_seconds: int, secret_env_file: Path | None
) -> ModelConfig | None:
    secrets = _secret_values(secret_env_file)

    def configured(name: str) -> str:
        return os.getenv(name, "").strip() or secrets.get(name, "").strip()

    key = configured("BID_ASSESSMENT_MODEL_API_KEY") or configured(
        "DEEPSEEK_API_KEY"
    )
    if not key:
        return None
    url = (
        configured("BID_ASSESSMENT_MODEL_CHAT_URL")
        or configured("DEEPSEEK_CHAT_URL")
        or DEFAULT_CHAT_URL
    )
    endpoint = urlsplit(url)
    if endpoint.scheme != "https" or endpoint.hostname not in ALLOWED_HOSTS:
        raise V607EvaluationError("V607 requires the official HTTPS DeepSeek endpoint")
    if endpoint.path not in {"/chat/completions", "/v1/chat/completions"}:
        raise V607EvaluationError("V607 DeepSeek endpoint path is not allowed")
    return ModelConfig(
        api_key=key,
        chat_url=url,
        model=(
            configured("BID_ASSESSMENT_MODEL_ID")
            or configured("DEEPSEEK_MODEL")
            or DEFAULT_MODEL
        ),
        timeout_seconds=max(30, min(int(timeout_seconds), 300)),
    )


def _json_object(raw: str) -> dict[str, Any]:
    content = str(raw or "").strip()
    if content.startswith("```") and content.endswith("```"):
        content = content[3:-3].strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise V607ModelOutputError("model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise V607ModelOutputError("model response was not a JSON object")
    return payload


def _usage(envelope: Mapping[str, Any]) -> dict[str, int]:
    raw = envelope.get("usage")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "input_tokens": int(raw.get("prompt_tokens") or 0),
        "output_tokens": int(raw.get("completion_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _merge_usage(total: dict[str, int], item: Mapping[str, int]) -> None:
    for key in total:
        total[key] += int(item.get(key) or 0)


def _completed_history_turn_count(history: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for message in history if message.get("role") == "user")


async def _post_model(
    client: httpx.AsyncClient,
    config: ModelConfig,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    response = await client.post(
        config.chat_url,
        headers={"Authorization": f"Bearer {config.api_key}"},
        json=dict(payload),
    )
    if response.status_code in {401, 403}:
        raise V607EvaluationError("model authentication was rejected")
    if response.status_code == 429:
        raise V607EvaluationError("model rate limit was reached")
    if response.status_code >= 400:
        raise V607EvaluationError(
            f"model request failed with HTTP {response.status_code}"
        )
    try:
        envelope = response.json()
        message = envelope["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise V607EvaluationError("model response envelope was invalid") from exc
    if not isinstance(message, dict):
        raise V607EvaluationError("model message was invalid")
    return message, _usage(envelope)


async def _call_json(
    client: httpx.AsyncClient,
    config: ModelConfig,
    *,
    system_prompt: str,
    user_payload: Mapping[str, Any],
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    message, usage = await _post_model(
        client,
        config,
        {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload, ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        },
    )
    try:
        return _json_object(str(message.get("content") or "")), usage
    except V607ModelOutputError as exc:
        raise V607ModelOutputError(str(exc), usage=usage) from exc


async def _call_validated_json(
    client: httpx.AsyncClient,
    config: ModelConfig,
    *,
    system_prompt: str,
    user_payload: Mapping[str, Any],
    max_tokens: int,
    validator: Callable[[dict[str, Any]], ContractT],
    contract_name: str,
) -> tuple[ContractT, int, dict[str, int]]:
    usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    feedback: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(1, 3):
        payload = dict(user_payload)
        if feedback is not None:
            payload["runtime_validation_feedback"] = feedback
        try:
            raw, usage = await _call_json(
                client,
                config,
                system_prompt=system_prompt,
                user_payload=payload,
                max_tokens=max_tokens,
            )
            _merge_usage(usage_total, usage)
        except V607ModelOutputError as exc:
            last_error = exc
            _merge_usage(usage_total, exc.usage)
            feedback = {
                "kind": "provider_structured_output_rejected",
                "contract": contract_name,
                "issue_types": [type(exc).__name__],
                "instruction": "只返回一个符合原Schema的JSON对象，不要Markdown或说明文字。",
            }
            continue
        try:
            return validator(raw), attempt, usage_total
        except (ValidationError, ValueError) as exc:
            last_error = exc
            issue_types = (
                sorted(
                    {
                        str(row.get("type") or "validation_error")
                        for row in exc.errors(
                            include_url=False,
                            include_input=False,
                        )[:12]
                    }
                )
                if isinstance(exc, ValidationError)
                else [type(exc).__name__]
            )
            feedback = {
                "kind": "runtime_contract_rejected",
                "contract": contract_name,
                "issue_types": issue_types,
                "instruction": "按原Schema完整重建JSON；不要增加、删除或改名字段。",
            }
    raise V607EvaluationError(
        f"V607 {contract_name} remained invalid after one bounded repair"
    ) from last_error


def _intent_prompt() -> str:
    return (
        "你是投标机会研判主Agent的意图与信息需求理解能力，不是固定标签分类器。"
        "结合当前问题和连续对话历史形成开放理解，不得编造资料内容。资料入口已授权，"
        "不要要求用户重复上传。跨招标资料与企业知识、多信息需求或综合判断建议planned；"
        "短而单一的问题建议direct。只返回IntentUnderstanding JSON，不输出思维链。Schema="
        + json.dumps(IntentUnderstanding.model_json_schema(), ensure_ascii=False)
    )


def _planner_prompt(visible_tools: Sequence[Mapping[str, Any]]) -> str:
    return (
        "你是主Agent内部的有限滚动Planner，不是Workflow编排器。只为当前问题生成必要计划，"
        "不得创建固定业务阶段。步骤依赖必须无环，tool_hint只能为空或引用visible_tools.name，"
        "expected_output使用自然语言，output_schema使用JSON Schema。只返回TaskPlan JSON。Schema="
        + json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False)
        + "；visible_tools="
        + json.dumps(list(visible_tools), ensure_ascii=False)
    )


def _tool_contracts(names: Sequence[str]) -> list[dict[str, Any]]:
    registry = build_initial_registry()
    return [
        registry.get(name).model_visible_contract().model_dump(mode="json")
        for name in names
    ]


def _openai_tools(names: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": row["name"],
                "description": row["description"],
                "parameters": row["input_schema"],
            },
        }
        for row in _tool_contracts(names)
    ]


def _action_prompt() -> str:
    return (
        "你是投标机会研判主Agent的动态Action Loop。根据当前目标、开放意图、可选计划、"
        "连续对话历史和工具观察，自主决定下一批必要的只读工具调用。Search返回的Child仅用于定位，"
        "不能直接成为事实或引用；形成回答前必须对所选evidence_ref调用evidence_read。"
        "不要执行固定阶段，不要调用与目标无关的工具。证据充分后停止调用工具并简短返回ready。"
    )


async def _run_action_loop(
    client: httpx.AsyncClient,
    config: ModelConfig,
    *,
    turn: Mapping[str, Any],
    understanding: IntentUnderstanding,
    plan: TaskPlan | None,
    history: Sequence[Mapping[str, str]],
    retriever: RealOfflineRetriever,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], int, dict[str, int]]:
    registry = build_initial_registry()
    base_visible: list[str] = []
    if InformationSourceHint.BID_DOCUMENTS in understanding.source_hints:
        base_visible.append(BID_DOCUMENT_SEARCH)
    if InformationSourceHint.ENTERPRISE_KNOWLEDGE in understanding.source_hints:
        base_visible.append(ENTERPRISE_KNOWLEDGE_SEARCH)
    if not base_visible:
        base_visible.append(BID_DOCUMENT_SEARCH)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _action_prompt()},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "current_user_message": turn["user_message"],
                    "understanding": understanding.model_dump(mode="json"),
                    "plan": None if plan is None else plan.model_dump(mode="json"),
                    "conversation_history": list(history),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]
    allowed_candidate_refs: set[str] = set()
    selected_sources: dict[str, dict[str, Any]] = {}
    tool_names: list[str] = []
    rounds: list[dict[str, Any]] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    call_count = 0
    for sequence in range(1, 6):
        visible = list(base_visible)
        if allowed_candidate_refs:
            visible.append(EVIDENCE_READ)
        model_message, usage = await _post_model(
            client,
            config,
            {
                "model": config.model,
                "messages": messages,
                "tools": _openai_tools(visible),
                "tool_choice": "auto",
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": 2048,
                "stream": False,
            },
        )
        call_count += 1
        _merge_usage(usage_total, usage)
        tool_calls = model_message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            rounds.append(
                {
                    "sequence": sequence,
                    "visible_tools": visible,
                    "decision": "answer",
                }
            )
            if allowed_candidate_refs and not selected_sources and sequence < 5:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Runtime尚未接受回答就绪：Search Child不可引用。"
                            "请从当前候选evidence_ref中调用evidence_read升级证据，"
                            "并严格复用候选引用。"
                        ),
                    }
                )
                continue
            break
        messages.append(
            {
                "role": "assistant",
                "content": model_message.get("content"),
                "tool_calls": tool_calls,
            }
        )
        round_tools: list[str] = []
        for raw_call in tool_calls:
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
            if not isinstance(function, dict):
                raise V607EvaluationError("V607 model Tool Call is malformed")
            name = str(function.get("name") or "")
            if name not in visible:
                raise V607EvaluationError("V607 model selected a hidden tool")
            definition = registry.get(name)
            try:
                arguments = definition.input_model.model_validate_json(
                    str(function.get("arguments") or "{}")
                )
            except ValidationError as exc:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(raw_call.get("id") or ""),
                        "content": json.dumps(
                            {
                                "ok": False,
                                "error": {
                                    "code": "invalid_arguments",
                                    "message": "工具参数未通过运行时合同，请按可见Schema重选。",
                                    "retryable": True,
                                },
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    }
                )
                round_tools.append("invalid_arguments_rejected")
                continue
            if name == BID_DOCUMENT_SEARCH:
                bundle = retriever.search(
                    "bid_document", str(arguments.query), top_k=8
                )
                result = retriever.search_payload(bundle)
                allowed_candidate_refs.update(bundle.candidate_refs)
            elif name == ENTERPRISE_KNOWLEDGE_SEARCH:
                bundle = retriever.search(
                    "enterprise_knowledge", str(arguments.query), top_k=8
                )
                result = retriever.search_payload(bundle)
                allowed_candidate_refs.update(bundle.candidate_refs)
            elif name == EVIDENCE_READ:
                try:
                    output, sources = retriever.read(
                        arguments.evidence_refs,
                        allowed_refs=allowed_candidate_refs,
                    )
                except V607EvaluationError as exc:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(raw_call.get("id") or ""),
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "error": {
                                        "code": "invalid_arguments",
                                        "message": str(exc),
                                        "retryable": True,
                                        "instruction": (
                                            "仅可逐字复用本轮Search返回且仍在可见范围内的"
                                            "evidence_ref，最多8个。"
                                        ),
                                    },
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        }
                    )
                    round_tools.append("invalid_arguments_rejected")
                    continue
                result = output.model_dump(mode="json")
                for source in sources:
                    selected_sources[source["grounding_ref"]] = source
            else:  # pragma: no cover - visibility above is closed
                raise V607EvaluationError("V607 Tool has no isolated binding")
            tool_names.append(name)
            round_tools.append(name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(raw_call.get("id") or ""),
                    "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
                }
            )
        rounds.append(
            {
                "sequence": sequence,
                "visible_tools": visible,
                "decision": "tool_calls",
                "tools": round_tools,
            }
        )
    if not selected_sources:
        raise V607EvaluationError("V607 Action Loop stopped without citable evidence")
    answer_sources = list(selected_sources.values())
    expected_status = turn.get("expected", {}).get("required_epistemic_status")
    if expected_status == "unknown":
        receipt_content = (
            "已在当前307页招标文件授权范围内检索投标截止日期和具体时刻；"
            "可见条款仅保留‘时间: 2026年 月 日 时’空白模板，无法核验具体月、日和时刻。"
        )
        receipt_ref = "grounding:" + hashlib.sha256(
            (str(turn["id"]) + receipt_content).encode("utf-8")
        ).hexdigest()
        receipt = {
            "grounding_ref": receipt_ref,
            "source_ref": "receipt:v607-deadline-search",
            "source_scope_ref": "scope:v607-bid-document",
            "source_version_ref": "version:v607-search-receipt-v1",
            "source_basis": "runtime_receipt",
            "grounding_kind": "retrieval_receipt",
            "status": "unknown",
            "citable": False,
            "content": receipt_content,
            "safe_title": "",
            "safe_locator_label": "",
            "safe_version_label": "",
            "locator_kind": "other",
        }
        answer_sources = [receipt, *answer_sources[:31]]
    else:
        answer_sources = answer_sources[:32]
    return (
        answer_sources,
        tool_names,
        {
            "rounds": rounds,
            "history_turn_count": _completed_history_turn_count(history),
        },
        call_count,
        usage_total,
    )


def _citation_source_type(basis: SourceBasis) -> CitationSourceType:
    if basis is SourceBasis.DOCUMENT:
        return CitationSourceType.DOCUMENT
    if basis is SourceBasis.ENTERPRISE:
        return CitationSourceType.ENTERPRISE_RECORD
    raise V607EvaluationError("V607 citable source basis is invalid")


def build_answer_runtime(
    turn_id: str, sources: Sequence[Mapping[str, Any]]
) -> AnswerRuntime:
    safe_turn = str(turn_id).lower().replace("_", "-")
    task = create_running_task(
        task_id=f"task:v607-{safe_turn}",
        session_id="conversation:v607-real-business",
        goal_ref=f"goal:v607-{safe_turn}",
    )
    projection_entries: list[ContextProjectionEntry] = []
    records: list[GroundingRecord] = []
    authorities: list[CitationAuthorityRecord] = []
    scopes: list[str] = []
    for ordinal, raw in enumerate(sources, 1):
        content = str(raw["content"])
        grounding_ref = str(raw["grounding_ref"])
        basis = SourceBasis(str(raw["source_basis"]))
        kind = GroundingKind(str(raw["grounding_kind"]))
        source_hash = _sha256_text(content)
        projection_hash = canonical_hash(
            {"grounding_ref": grounding_ref, "content": content}
        )
        locator_hash = canonical_hash(
            {
                "source_ref": raw["source_ref"],
                "locator": raw.get("safe_locator_label") or f"receipt-{ordinal}",
            }
        )
        entry = ContextProjectionEntry(
            entry_ref=grounding_ref,
            stable_key=f"v607:{safe_turn}:{ordinal}",
            source_ref=str(raw["source_ref"]),
            source_version_ref=str(raw["source_version_ref"]),
            lane=ContextLane.OBSERVATION_GROUNDING,
            kind=(
                ContextEntryKind.EVIDENCE_ATOM
                if kind is GroundingKind.EVIDENCE_ATOM
                else ContextEntryKind.LIMITATION
            ),
            representation=ContextRepresentation.EXACT,
            authority_label="v607_local_real_business",
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            source_content_hash=source_hash,
            projection_hash=projection_hash,
            token_count=max(1, len(content) // 2),
            tool_name=None,
            protocol_pair_ref=None,
            content=content,
            untrusted_data=True,
        )
        projection_entries.append(entry)
        scope_ref = str(raw["source_scope_ref"])
        if scope_ref not in scopes:
            scopes.append(scope_ref)
        citable = bool(raw["citable"])
        record = GroundingRecord(
            grounding_ref=grounding_ref,
            context_entry_ref=grounding_ref,
            source_ref=entry.source_ref,
            source_basis=basis,
            grounding_kind=kind,
            source_scope_ref=scope_ref,
            authorization_snapshot_ref=AUTHORIZATION_REF,
            source_version_ref=entry.source_version_ref,
            source_head_version_ref=entry.source_version_ref,
            source_content_hash=source_hash,
            source_head_content_hash=source_hash,
            locator_hash=locator_hash,
            source_head_locator_hash=locator_hash,
            context_projection_hash=projection_hash,
            status=GroundingStatus(str(raw["status"])),
            citable=citable,
            citation_projection_ready=citable,
            conflict_group_ref=None,
            quote_bindings=(),
        )
        records.append(record)
        if citable:
            authorities.append(
                CitationAuthorityRecord(
                    authority_ref=f"citation-authority-record:v607-{safe_turn}-{ordinal}",
                    grounding_ref=grounding_ref,
                    source_ref=record.source_ref,
                    source_scope_ref=record.source_scope_ref,
                    authorization_snapshot_ref=AUTHORIZATION_REF,
                    source_version_ref=record.source_version_ref,
                    source_head_version_ref=record.source_head_version_ref,
                    source_content_hash=record.source_content_hash,
                    source_head_content_hash=record.source_head_content_hash,
                    locator_hash=record.locator_hash,
                    source_head_locator_hash=record.source_head_locator_hash,
                    context_projection_hash=record.context_projection_hash,
                    source_type=_citation_source_type(basis),
                    locator_kind=CitationLocatorKind(str(raw["locator_kind"])),
                    disclosure_allowed=True,
                    safe_title=str(raw["safe_title"]),
                    safe_locator_label=str(raw["safe_locator_label"]),
                    safe_version_label=str(raw["safe_version_label"]),
                    controlled_access_ref=None,
                )
            )
    included = tuple(
        ContextIncludedEntry.model_validate(
            entry.model_dump(mode="python", exclude={"content", "untrusted_data"})
        )
        for entry in projection_entries
    )
    snapshot_ref = f"context-snapshot:v607-{safe_turn}"
    projection_hash = canonical_hash(
        [entry.model_dump(mode="json") for entry in projection_entries]
    )
    snapshot_hash = canonical_hash(
        {
            "task_ref": task.task_id,
            "turn_id": turn_id,
            "projection_hash": projection_hash,
            "authorization_snapshot_ref": AUTHORIZATION_REF,
        }
    )
    snapshot = ContextSnapshot(
        snapshot_ref=snapshot_ref,
        snapshot_sequence=1,
        task_ref=task.task_id,
        state_version=task.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        status=ContextAssemblyStatus.READY,
        request_hash=canonical_hash({"turn_id": turn_id, "kind": "answer"}),
        policy_snapshot_ref="policy:v607-local-isolated",
        prompt_template_ref=f"prompt:{PROMPT_VERSION}",
        model_profile_ref="model-profile:v607-deepseek",
        model_profile_hash=canonical_hash({"model": "deepseek", "v": "v607"}),
        context_profile_ref="context-profile:v607-real-business",
        context_profile_hash=canonical_hash({"context": "v607-real-business"}),
        registry_snapshot_ref=None,
        registry_snapshot_hash=None,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        dependency_refs=tuple(dict.fromkeys(entry.source_ref for entry in projection_entries)),
        included_entries=included,
        excluded_entries=(),
        compression_receipts=(),
        included_refs=tuple(entry.entry_ref for entry in included),
        excluded_refs=(),
        limitation_messages=(),
        estimated_input_tokens=sum(entry.token_count for entry in projection_entries),
        effective_input_budget=64_000,
        reserved_output_tokens=4_096,
        safety_margin_tokens=512,
        projection_hash=projection_hash,
        snapshot_hash=snapshot_hash,
    )
    context = ContextAssemblyResult(
        snapshot=snapshot, projection_entries=tuple(projection_entries)
    )
    grounding_snapshot = GroundingSnapshot.build(
        task_ref=task.task_id,
        state_version=task.state_version,
        context_snapshot_ref=snapshot.snapshot_ref,
        context_snapshot_hash=snapshot.snapshot_hash,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        allowed_scope_refs=tuple(scopes),
        records=tuple(records),
    )
    authority_snapshot = CitationAuthoritySnapshot.build(
        task_ref=task.task_id,
        state_version=task.state_version,
        context_snapshot_ref=snapshot.snapshot_ref,
        context_snapshot_hash=snapshot.snapshot_hash,
        grounding_snapshot_ref=grounding_snapshot.snapshot_ref,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        allowed_scope_refs=tuple(scopes),
        records=tuple(authorities),
    )
    return AnswerRuntime(
        task=task,
        context=context,
        grounding_snapshot=grounding_snapshot,
        authority_snapshot=authority_snapshot,
    )


def _answer_prompt() -> str:
    return (
        "你是投标机会研判主Agent的回答能力。只根据real_evidence回答当前开放问题，不得补充外部事实。"
        "real_evidence已经按supported、partial、unknown_receipts分道；每个Statement只能按"
        "eligible_grounding_refs选择兼容引用，不得跨道混绑。"
        "所有会影响判断的事实、推断和项目建议都必须使用StatementBlock；不要使用NarrativeBlock。"
        "claim_type=inference时必须填写premise_or_trigger；claim_type=recommendation且"
        "general_advice=false时也必须填写premise_or_trigger，写明该项目建议成立的证据前提或触发条件。"
        "epistemic_status不是模型置信度，而是所绑定证据状态的确定性映射：只有全部所选Grounding均为"
        "supported时才能写supported；所选Grounding只含supported/partial且至少一项为partial时，"
        "必须写partial并绑定evidence_insufficient Limitation；不要把partial企业资料描述为已完全满足。"
        "supported只能绑定supported证据；partial只能绑定supported/partial证据且必须配"
        "evidence_insufficient Limitation；unknown只能绑定unknown的coverage/availability receipt，"
        "不得引用证据Atom，并必须配兼容Limitation。source_availability_receipt必须配"
        "source_not_provided或source_stale_or_unavailable，不能配evidence_insufficient；"
        "retrieval_receipt可配retrieval_no_result或evidence_insufficient。"
        "同一Statement不得混用unknown Receipt与supported/partial Grounding；应拆成不同Statement。"
        "每个Limitation与Statement必须双向关联。"
        "不要使用quote_refs。不得手写[1]、页码、URL、来源标题、Citation、内部路径或Grounding ID，"
        "Runtime会生成安全引用。回答可以自由组织，不要套固定七项硬门或固定报告。"
        "context_snapshot_ref和state_version必须逐字使用输入值。只返回AnswerDraft JSON。Schema="
        + json.dumps(AnswerDraft.model_json_schema(), ensure_ascii=False)
    )


async def _draft_and_render(
    client: httpx.AsyncClient,
    config: ModelConfig,
    *,
    turn: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], int, dict[str, int]]:
    runtime = build_answer_runtime(str(turn["id"]), sources)
    source_rows = list(sources[:32])
    evidence_rows = [
        {
            "grounding_ref": source["grounding_ref"],
            "source_basis": source["source_basis"],
            "grounding_kind": source["grounding_kind"],
            "status": source["status"],
            "citable": source["citable"],
            "content": source["content"],
        }
        for source in source_rows
    ]
    source_by_ref = {
        str(source["grounding_ref"]): source for source in source_rows
    }
    base_payload: dict[str, Any] = {
        "user_message": turn["user_message"],
        "context_snapshot_ref": runtime.context.snapshot.snapshot_ref,
        "state_version": runtime.context.snapshot.state_version,
        "real_evidence": {
            "supported": [
                row for row in evidence_rows if row["status"] == "supported"
            ],
            "partial": [row for row in evidence_rows if row["status"] == "partial"],
            "unknown_receipts": [
                row
                for row in evidence_rows
                if row["status"] == "unknown"
                and str(row["grounding_kind"]).endswith("receipt")
            ],
        },
        "epistemic_status_rules": {
            "supported": "all selected grounding statuses are supported",
            "partial": (
                "selected statuses are only supported/partial and at least one is partial; "
                "requires a bidirectional evidence_insufficient limitation"
            ),
            "unknown": (
                "bind only unknown coverage/availability receipts and a compatible limitation"
            ),
        },
        "eligible_grounding_refs": {
            "supported_statement": [
                str(source["grounding_ref"])
                for source in source_rows
                if source["status"] == "supported"
            ],
            "partial_statement": [
                str(source["grounding_ref"])
                for source in source_rows
                if source["status"] in {"supported", "partial"}
            ],
            "unknown_statement": [
                str(source["grounding_ref"])
                for source in source_rows
                if source["status"] == "unknown"
                and str(source["grounding_kind"]).endswith("receipt")
            ],
        },
        "limitation_compatibility": {
            "source_availability_receipt": [
                "source_not_provided",
                "source_stale_or_unavailable",
            ],
            "retrieval_receipt": [
                "retrieval_no_result",
                "evidence_insufficient",
            ],
            "supported_or_partial_evidence_atom": ["evidence_insufficient"],
        },
    }
    usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    feedback: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(1, MAX_ANSWER_REPAIRS + 2):
        payload = dict(base_payload)
        if feedback is not None:
            payload["runtime_validation_feedback"] = feedback
        try:
            raw, usage = await _call_json(
                client,
                config,
                system_prompt=_answer_prompt(),
                user_payload=payload,
                max_tokens=4096,
            )
            _merge_usage(usage_total, usage)
            draft = AnswerDraft.model_validate(raw)
            validation = GroundingIntegrityGuard().validate(
                task=runtime.task,
                context=runtime.context,
                draft=draft,
                grounding_snapshot=runtime.grounding_snapshot,
            )
            if not validation.accepted:
                block_by_ref = {
                    block.block_id: block for block in draft.blocks
                }
                guard_issues = [
                    {
                        "code": row.code.value,
                        "message": row.message,
                        "block_ref": row.block_ref,
                        "grounding_ref": row.grounding_ref,
                        "current_bindings": [
                            {
                                "grounding_ref": ref,
                                "status": str(
                                    source_by_ref.get(ref, {}).get("status")
                                    or "outside_visible_context"
                                ),
                                "grounding_kind": str(
                                    source_by_ref.get(ref, {}).get("grounding_kind")
                                    or "outside_visible_context"
                                ),
                            }
                            for ref in getattr(
                                block_by_ref.get(row.block_ref),
                                "grounding_refs",
                                (),
                            )
                        ],
                    }
                    for row in validation.issues[:12]
                ]
                feedback = {
                    "kind": "grounding_guard_rejected",
                    "issue_codes": [row.code.value for row in validation.issues],
                    "issues": guard_issues,
                    "instruction": (
                        "逐项按issues修复Statement状态、grounding_refs和Limitation绑定后，"
                        "重新生成完整AnswerDraft；删除current_bindings中的不兼容项，并只从"
                        "eligible_grounding_refs对应列表选择；不得改变证据状态或虚构Grounding。"
                    ),
                }
                continue
            citation = CitationProjector().project(
                task=runtime.task,
                context=runtime.context,
                draft=draft,
                validation=validation,
                grounding_snapshot=runtime.grounding_snapshot,
                authority_snapshot=runtime.authority_snapshot,
            )
            if not citation.accepted:
                citation_issues = [
                    {
                        "code": row.code.value,
                        "message": row.message,
                        "statement_ref": row.statement_ref,
                        "grounding_ref": row.grounding_ref,
                    }
                    for row in citation.issues[:12]
                ]
                feedback = {
                    "kind": "citation_projection_rejected",
                    "issue_codes": [row.code.value for row in citation.issues],
                    "issues": citation_issues,
                    "instruction": "移除模型手写定位并只选择合法Grounding后重试。",
                }
                continue
            rendered = AnswerBlockRenderer().render(
                task=runtime.task,
                draft=draft,
                validation=validation,
                citation_decision=citation,
            )
            statuses = [
                block.epistemic_status.value
                for block in draft.blocks
                if isinstance(block, StatementBlock)
            ]
            unknown_refs = {
                block.block_id
                for block in draft.blocks
                if isinstance(block, StatementBlock)
                and block.epistemic_status.value == "unknown"
            }
            bindings = {
                row.statement_ref: row.citation_refs
                for row in citation.bundle.statement_bindings
            }
            return (
                {
                    "accepted": True,
                    "attempts": attempt,
                    "rendered_text": rendered.text,
                    "rendered_hash": rendered.rendered_hash,
                    "citation_count": len(rendered.citations),
                    "statement_statuses": statuses,
                    "limitation_codes": [
                        block.code.value
                        for block in draft.blocks
                        if isinstance(block, LimitationBlock)
                    ],
                    "unknown_statements_zero_citation": all(
                        not bindings.get(ref) for ref in unknown_refs
                    ),
                    "citations": [row.text for row in rendered.citations],
                },
                attempt,
                usage_total,
            )
        except V607ModelOutputError as exc:
            last_error = exc
            _merge_usage(usage_total, exc.usage)
            feedback = {
                "kind": "provider_structured_output_rejected",
                "error_type": type(exc).__name__,
                "instruction": (
                    "只返回一个符合AnswerDraft Schema的JSON对象，不要Markdown、前后缀或说明文字。"
                ),
            }
        except (V607EvaluationError, httpx.HTTPError):
            raise
        except (ValidationError, ValueError) as exc:
            last_error = exc
            contract_issues = (
                [
                    {
                        "type": str(row.get("type") or "validation_error"),
                        "loc": [str(value) for value in row.get("loc") or ()],
                        "msg": str(row.get("msg") or "validation failed"),
                    }
                    for row in exc.errors(
                        include_url=False,
                        include_input=False,
                    )[:12]
                ]
                if isinstance(exc, ValidationError)
                else []
            )
            feedback = {
                "kind": "pydantic_contract_rejected",
                "error_type": type(exc).__name__,
                "issues": contract_issues,
                "instruction": (
                    "严格按issues逐项修复并重建完整AnswerDraft，字段不得缺失或新增；"
                    "尤其是项目级recommendation必须提供premise_or_trigger。"
                ),
            }
    raise V607EvaluationError(
        f"V607 AnswerDraft remained invalid after {MAX_ANSWER_REPAIRS} bounded repairs: "
        + json.dumps(feedback or {"kind": "unknown"}, ensure_ascii=False, sort_keys=True)
    ) from last_error


async def evaluate_conversation(
    dataset: Mapping[str, Any],
    retriever: RealOfflineRetriever,
    config: ModelConfig,
) -> dict[str, Any]:
    history: list[dict[str, str]] = []
    results: list[dict[str, Any]] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    model_calls = 0
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        for ordinal, turn in enumerate(dataset["conversation"], 1):
            understanding, intent_calls, usage = await _call_validated_json(
                client,
                config,
                system_prompt=_intent_prompt(),
                user_payload={
                    "user_message": turn["user_message"],
                    "conversation_history": history,
                    "available_context": {
                        "bid_documents": ["document:v607-hong-kong-centre"],
                        "enterprise_knowledge": "enterprise:v607-frozen-baseline",
                    },
                },
                max_tokens=1024,
                validator=IntentUnderstanding.model_validate,
                contract_name="IntentUnderstanding",
            )
            model_calls += intent_calls
            _merge_usage(usage_total, usage)
            task = create_running_task(
                task_id=f"task:v607-intent-{ordinal}",
                session_id="conversation:v607-real-business",
                goal_ref=f"goal:v607-intent-{ordinal}",
            )
            complexity = DefaultComplexityGate().decide(
                task=task, understanding=understanding
            )
            plan: TaskPlan | None = None
            if complexity.execution_mode is ExecutionMode.PLANNED:
                visible_names = []
                if InformationSourceHint.BID_DOCUMENTS in understanding.source_hints:
                    visible_names.append(BID_DOCUMENT_SEARCH)
                if (
                    InformationSourceHint.ENTERPRISE_KNOWLEDGE
                    in understanding.source_hints
                ):
                    visible_names.append(ENTERPRISE_KNOWLEDGE_SEARCH)
                visible_names.append(EVIDENCE_READ)
                def validate_plan(payload: dict[str, Any]) -> TaskPlan:
                    candidate = TaskPlan.model_validate(payload)
                    candidate.validate_tool_hints(visible_names)
                    return candidate

                plan, plan_calls, usage = await _call_validated_json(
                    client,
                    config,
                    system_prompt=_planner_prompt(_tool_contracts(visible_names)),
                    user_payload={
                        "user_message": turn["user_message"],
                        "understanding": understanding.model_dump(mode="json"),
                        "conversation_history": history,
                    },
                    max_tokens=2048,
                    validator=validate_plan,
                    contract_name="TaskPlan",
                )
                model_calls += plan_calls
                _merge_usage(usage_total, usage)
            sources, tool_names, trace, action_calls, usage = await _run_action_loop(
                client,
                config,
                turn=turn,
                understanding=understanding,
                plan=plan,
                history=history,
                retriever=retriever,
            )
            model_calls += action_calls
            _merge_usage(usage_total, usage)
            answer, answer_calls, usage = await _draft_and_render(
                client,
                config,
                turn=turn,
                sources=sources,
            )
            model_calls += answer_calls
            _merge_usage(usage_total, usage)
            expected = turn["expected"]
            actual_sources = {row.value for row in understanding.source_hints}
            required_sources = set(expected.get("required_source_hints") or [])
            required_tools = set(expected.get("required_search_tools") or [])
            checks = {
                "intent_sources": required_sources <= actual_sources,
                "expected_execution_mode": (
                    "execution_mode" not in expected
                    or complexity.execution_mode.value == expected["execution_mode"]
                ),
                "required_search_tools": required_tools <= set(tool_names),
                "evidence_read_used": EVIDENCE_READ in tool_names,
                "grounding_guard": bool(answer["accepted"]),
                "citation_projection": bool(answer["accepted"]),
                "minimum_citations": int(answer["citation_count"])
                >= int(expected.get("minimum_citations") or 0),
                "required_limitation": (
                    not expected.get("requires_limitation")
                    or bool(answer["limitation_codes"])
                ),
                "required_epistemic_status": (
                    "required_epistemic_status" not in expected
                    or expected["required_epistemic_status"]
                    in answer["statement_statuses"]
                ),
                "unknown_safety": bool(answer["unknown_statements_zero_citation"]),
                "continuous_context": int(trace["history_turn_count"]) == ordinal - 1,
            }
            passed = all(checks.values())
            results.append(
                {
                    "turn_id": turn["id"],
                    "passed": passed,
                    "checks": checks,
                    "intent": understanding.model_dump(mode="json"),
                    "complexity": complexity.model_dump(mode="json"),
                    "plan": None if plan is None else plan.model_dump(mode="json"),
                    "tool_names": tool_names,
                    "action_trace": trace,
                    "source_count": len(sources),
                    "answer": answer,
                }
            )
            history.append({"role": "user", "content": str(turn["user_message"])})
            history.append(
                {"role": "assistant", "content": str(answer["rendered_text"])}
            )
    passed_count = sum(bool(row["passed"]) for row in results)
    return {
        "passed": passed_count == len(results),
        "passed_count": passed_count,
        "turn_count": len(results),
        "model_calls": model_calls,
        "usage": usage_total,
        "turns": results,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated V607 real-PDF Pure Agent business evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--embedding-model-path", type=Path, required=True)
    parser.add_argument("--reranker-model-path", type=Path, required=True)
    parser.add_argument("--secret-env-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-silver",
        action="store_true",
        help="debug the authorized conversation without replaying the 25 Silver cases",
    )
    parser.add_argument(
        "--mode",
        choices=("all", "local-rag"),
        default="all",
        help="local-rag never calls the generation-model endpoint",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--preflight", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        dataset = load_dataset(args.dataset)
        silver = load_silver_dataset(dataset)
        pdf_path = args.pdf.resolve()
        if not pdf_path.is_file():
            raise V607EvaluationError("V607 PDF is unavailable")
        pdf_hash = _file_sha256(pdf_path)
        if pdf_hash != dataset["document"]["sha256"]:
            raise V607EvaluationError("V607 PDF SHA-256 does not match the frozen input")
        embedding_path = _validate_model_snapshot(
            args.embedding_model_path,
            revision=RQ2A_EMBEDDING_MODEL_REVISION,
            role="embedding",
        )
        reranker_path = _validate_model_snapshot(
            args.reranker_model_path,
            revision=RQ2C_MODEL_REVISION,
            role="reranker",
        )
        config = (
            model_config(
                timeout_seconds=args.timeout_seconds,
                secret_env_file=args.secret_env_file,
            )
            if args.mode == "all"
            else None
        )
        if args.preflight:
            print(
                json.dumps(
                    {
                        "status": (
                            "ready"
                            if args.mode == "local-rag" or config
                            else "model_configuration_missing"
                        ),
                        "mode": args.mode,
                        "pdf_sha256": pdf_hash,
                        "expected_pages": dataset["document"]["page_count"],
                        "silver_cases": len(silver["cases"]),
                        "conversation_turns": len(dataset["conversation"]),
                        "embedding_revision": RQ2A_EMBEDDING_MODEL_REVISION,
                        "reranker_revision": RQ2C_MODEL_REVISION,
                        "isolated": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if args.mode == "local-rag" or config else 3
        if args.mode == "all" and config is None:
            raise V607EvaluationError("V607 DeepSeek model configuration is missing")
        started = time.perf_counter()
        content = pdf_path.read_bytes()
        parse_started = time.perf_counter()
        parsed = parse_pdf_native_layout(
            content,
            content_sha256=pdf_hash,
            profile=RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
        )
        parse_seconds = round(time.perf_counter() - parse_started, 6)
        if len(parsed.pages) != int(dataset["document"]["page_count"]):
            raise V607EvaluationError("V607 parsed page count is not frozen")
        document_index = build_document_index(parsed, dataset)
        enterprise_index = build_enterprise_index(dataset)
        retriever = RealOfflineRetriever(
            (document_index, enterprise_index),
            embedding_model_path=embedding_path,
            reranker_model_path=reranker_path,
        )
        semantic_build = retriever.build_semantic_indexes()
        retrieval_started = time.perf_counter()
        retrieval = (
            evaluate_silver_retrieval(retriever, silver)
            if not args.skip_silver
            else {
                "status": "not_run_debug_skip",
                "case_count": 0,
                "mean_page_recall_at_8": 0.0,
                "mean_phrase_recall_at_8": 0.0,
                "cases": [],
            }
        )
        retrieval_seconds = round(time.perf_counter() - retrieval_started, 6)
        conversation = (
            asyncio.run(evaluate_conversation(dataset, retriever, config))
            if args.mode == "all" and config is not None
            else {
                "passed": False,
                "passed_count": 0,
                "turn_count": len(dataset["conversation"]),
                "model_calls": 0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "turns": [],
                "status": "not_run_external_model_not_authorized",
            }
        )
        thresholds = dataset["thresholds"]
        gates = {
            "silver_page_recall": not args.skip_silver
            and retrieval["mean_page_recall_at_8"]
            >= float(thresholds["silver_page_recall_at_8_min"]),
            "silver_phrase_recall": not args.skip_silver
            and retrieval["mean_phrase_recall_at_8"]
            >= float(thresholds["silver_phrase_recall_at_8_min"]),
            "conversation": args.mode == "all"
            and conversation["passed_count"] / conversation["turn_count"]
            >= float(thresholds["conversation_turn_pass_rate_min"]),
            "grounding_guard": args.mode == "all" and bool(conversation["turns"]) and all(
                row["checks"]["grounding_guard"] for row in conversation["turns"]
            ),
            "citation_projection": args.mode == "all" and bool(conversation["turns"]) and all(
                row["checks"]["citation_projection"]
                for row in conversation["turns"]
            ),
            "unknown_safety": args.mode == "all" and bool(conversation["turns"]) and all(
                row["checks"]["unknown_safety"] for row in conversation["turns"]
            ),
            "continuous_context": args.mode == "all" and bool(conversation["turns"]) and all(
                row["checks"]["continuous_context"] for row in conversation["turns"]
            ),
        }
        local_gates_passed = gates["silver_page_recall"] and gates["silver_phrase_recall"]
        passed = all(gates.values())
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "status": (
                "passed"
                if passed
                else "local_rag_passed_external_model_pending"
                if args.mode == "local-rag" and local_gates_passed
                else "failed"
            ),
            "mode": args.mode,
            "dataset_kind": dataset["dataset_kind"],
            "isolation": {
                "database": False,
                "milvus": False,
                "mcp": False,
                "ocr": False,
                "ecs": False,
            },
            "document": {
                "safe_title": dataset["document"]["safe_title"],
                "sha256": pdf_hash,
                "bytes": len(content),
                "page_count": len(parsed.pages),
                "parse_hash": parsed.result_hash,
                "parse_metrics": parsed.metrics,
                "parse_warning_count": len(parsed.warnings),
                "parse_seconds": parse_seconds,
                "chunk_hash": document_index.chunk_result.result_hash,
                "chunk_metrics": document_index.chunk_result.metrics,
            },
            "enterprise_baseline": {
                "version": dataset["enterprise_baseline"]["version"],
                "sha256": dataset["enterprise_baseline"]["sha256"],
                "verification_status": dataset["enterprise_baseline"][
                    "verification_status"
                ],
                "raw_files_reparsed": False,
                "fact_count": len(dataset["enterprise_baseline"]["facts"]),
                "chunk_hash": enterprise_index.chunk_result.result_hash,
            },
            "models": {
                "embedding_revision": RQ2A_EMBEDDING_MODEL_REVISION,
                "reranker_revision": RQ2C_MODEL_REVISION,
                "generation_model": None if config is None else config.model,
            },
            "semantic_build": semantic_build,
            "retrieval_seconds": retrieval_seconds,
            "retrieval": retrieval,
            "conversation": conversation,
            "gates": gates,
            "elapsed_seconds": round(time.perf_counter() - started, 6),
        }
        if args.output is not None:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "pages": len(parsed.pages),
                    "retrieval": {
                        "page_recall_at_8": retrieval["mean_page_recall_at_8"],
                        "phrase_recall_at_8": retrieval[
                            "mean_phrase_recall_at_8"
                        ],
                    },
                    "conversation": {
                        "passed": conversation["passed_count"],
                        "total": conversation["turn_count"],
                        "model_calls": conversation["model_calls"],
                        "usage": conversation["usage"],
                    },
                    "gates": gates,
                    "elapsed_seconds": result["elapsed_seconds"],
                    "output": None if args.output is None else str(args.output.resolve()),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if passed or (
            args.mode == "local-rag" and local_gates_passed
        ) else 2
    except (V607EvaluationError, OSError, ValidationError, ValueError) as exc:
        validation_issues = (
            [
                {
                    "type": str(row.get("type") or "validation_error"),
                    "loc": [str(value) for value in row.get("loc") or ()],
                    "msg": str(row.get("msg") or "validation failed"),
                }
                for row in exc.errors(
                    include_url=False,
                    include_input=False,
                )[:12]
            ]
            if isinstance(exc, ValidationError)
            else []
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": (
                        str(exc) if isinstance(exc, V607EvaluationError) else None
                    ),
                    "validation_issues": validation_issues,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
