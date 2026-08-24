"""Deterministic V604 offline RAG and recall evaluation.

The evaluator accepts only versioned synthetic structured blocks. It performs no
PDF I/O, database access, embedding/reranker/model call, network request, MCP
call, OCR, or vision work. The semantic channel is an explicit precomputed rank
fixture so V604 can validate fusion boundaries without crossing into V605.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Literal, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_assessment_pure.tool_runtime import (
    BindingExecutionResult,
    ToolProvenanceRecord,
)
from app.agents.bid_assessment_pure.tools import (
    EvidenceAtom,
    EvidenceCandidate,
    EvidenceCandidatesOutput,
    EvidenceReadOutput,
    ToolExecutionContext,
)
from app.services.bid_evidence_chunk_builder import (
    EvidenceChunkBuildResult,
    EvidenceChunkFragment,
    StructuredEvidenceBlock,
    build_evidence_chunks,
)
from app.services.bid_field_aware_lexical import (
    FieldAwareLexicalCorpus,
    FieldAwareLexicalRank,
    LexicalAtomSource,
    LexicalChildSource,
    build_field_aware_lexical_corpus,
    rank_field_aware_bm25f,
)
from app.services.bid_hybrid_candidate_fusion import (
    CandidateChannelHit,
    CandidateFusionResult,
    fuse_candidate_channels,
)


DATASET_PATH = (
    PROJECT_ROOT
    / "evals"
    / "bid_assessment"
    / "v604-offline-rag-synthetic-cases.json"
)
SCHEMA_VERSION = "bid.pure_agent.v604.offline_rag.v1"
Domain = Literal["bid_document", "enterprise_knowledge"]


class OfflineRagEvaluationError(RuntimeError):
    pass


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise OfflineRagEvaluationError("unsupported V604 dataset schema")
    if payload.get("dataset_kind") != "synthetic_only":
        raise OfflineRagEvaluationError("V604 accepts synthetic-only datasets")
    domains = payload.get("domains")
    cases = payload.get("cases")
    if not isinstance(domains, list) or len(domains) != 2:
        raise OfflineRagEvaluationError("V604 requires exactly two source domains")
    if not isinstance(cases, list) or not cases:
        raise OfflineRagEvaluationError("V604 requires retrieval cases")
    domain_names = [item.get("domain") for item in domains if isinstance(item, dict)]
    if sorted(domain_names) != ["bid_document", "enterprise_knowledge"]:
        raise OfflineRagEvaluationError("V604 source domains are invalid")
    case_ids = [item.get("id") for item in cases if isinstance(item, dict)]
    if len(case_ids) != len(cases) or len(case_ids) != len(set(case_ids)):
        raise OfflineRagEvaluationError("V604 case ids must be unique")
    return payload


def _structured_block(payload: Mapping[str, Any]) -> StructuredEvidenceBlock:
    required = {
        "block_key",
        "text",
        "block_type",
        "page_no",
        "ordinal",
        "section_path",
        "active",
    }
    if set(payload) != required:
        raise OfflineRagEvaluationError("V604 structured block fields are invalid")
    if not isinstance(payload["active"], bool):
        raise OfflineRagEvaluationError("V604 block active flag must be boolean")
    return StructuredEvidenceBlock(
        block_key=str(payload["block_key"]),
        text=str(payload["text"]),
        block_type=str(payload["block_type"]),
        page_no=int(payload["page_no"]),
        ordinal=int(payload["ordinal"]),
        section_path=tuple(str(value) for value in payload["section_path"]),
    )


@dataclass(frozen=True)
class OfflineDomainIndex:
    domain: Domain
    source_scope_ref: str
    source_version_ref: str
    chunk_result: EvidenceChunkBuildResult
    lexical_corpus: FieldAwareLexicalCorpus
    child_by_ref: Mapping[str, EvidenceChunkFragment]
    atoms_by_child_ref: Mapping[str, tuple[EvidenceChunkFragment, ...]]
    child_ref_by_block_key: Mapping[str, str]
    source_index_set_hash: str

    def block_keys_for_child(self, child_ref: str) -> tuple[str, ...]:
        child = self.child_by_ref[child_ref]
        return child.source_block_keys


@dataclass(frozen=True)
class OfflineSearchResult:
    case_id: str
    domain: Domain
    lexical_ranks: tuple[FieldAwareLexicalRank, ...]
    fusion: CandidateFusionResult
    binding_result: BindingExecutionResult

    @property
    def candidate_refs(self) -> tuple[str, ...]:
        output = self.binding_result.structured_content
        return tuple(candidate.evidence_ref for candidate in output.candidates)


def _build_domain_index(payload: Mapping[str, Any]) -> OfflineDomainIndex:
    domain = str(payload.get("domain"))
    if domain not in {"bid_document", "enterprise_knowledge"}:
        raise OfflineRagEvaluationError("V604 domain is invalid")
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise OfflineRagEvaluationError("V604 domain requires blocks")
    parsed = tuple(_structured_block(item) for item in raw_blocks)
    active_blocks = tuple(
        block
        for block, raw in zip(parsed, raw_blocks, strict=True)
        if raw["active"]
    )
    if not active_blocks:
        raise OfflineRagEvaluationError("V604 domain has no active blocks")
    chunk_result = build_evidence_chunks(
        active_blocks,
        document_label=str(payload.get("document_label") or "synthetic"),
    )
    children = tuple(
        fragment
        for fragment in chunk_result.fragments
        if fragment.fragment_role == "retrieval_child"
    )
    atoms = tuple(
        fragment
        for fragment in chunk_result.fragments
        if fragment.fragment_role == "evidence_atom"
    )
    atoms_by_child = {
        child.evidence_key: tuple(
            atom for atom in atoms if atom.parent_key == child.evidence_key
        )
        for child in children
    }
    lexical_sources = tuple(
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
    child_ref_by_block_key: dict[str, str] = {}
    for child in children:
        for block_key in child.source_block_keys:
            if block_key in child_ref_by_block_key:
                raise OfflineRagEvaluationError("V604 block maps to multiple children")
            child_ref_by_block_key[block_key] = child.evidence_key
    return OfflineDomainIndex(
        domain=domain,  # type: ignore[arg-type]
        source_scope_ref=str(payload.get("source_scope_ref")),
        source_version_ref=str(payload.get("source_version_ref")),
        chunk_result=chunk_result,
        lexical_corpus=build_field_aware_lexical_corpus(lexical_sources),
        child_by_ref={child.evidence_key: child for child in children},
        atoms_by_child_ref=atoms_by_child,
        child_ref_by_block_key=child_ref_by_block_key,
        source_index_set_hash=_sha256(
            {
                "chunk_result_hash": chunk_result.result_hash,
                "active_block_keys": sorted(block.block_key for block in active_blocks),
            }
        ),
    )


def _public_locator(domain: Domain, fragment: EvidenceChunkFragment) -> str:
    page = int(fragment.locator.get("page_no") or 1)
    if domain == "bid_document":
        return f"page:{page}"
    return f"profile:page:{page}"


class SyntheticOfflineRagHarness:
    """In-memory V604 source implementing the Pure Agent RAG source ports."""

    def __init__(self, dataset: Mapping[str, Any]) -> None:
        self.dataset = dict(dataset)
        self.indexes = {
            index.domain: index
            for index in (
                _build_domain_index(domain_payload)
                for domain_payload in self.dataset["domains"]
            )
        }
        self.cases = {case["id"]: dict(case) for case in self.dataset["cases"]}
        self.case_by_domain_query = {
            (case["domain"], case["query"]): dict(case)
            for case in self.dataset["cases"]
        }

    def _semantic_hits(
        self,
        *,
        index: OfflineDomainIndex,
        block_ranking: Sequence[str],
    ) -> tuple[CandidateChannelHit, ...]:
        hits: list[CandidateChannelHit] = []
        seen: set[str] = set()
        for block_key in block_ranking:
            child_ref = index.child_ref_by_block_key.get(str(block_key))
            # Revoked, stale, unscoped or unknown blocks never enter the channel.
            if child_ref is None or child_ref in seen:
                continue
            seen.add(child_ref)
            hits.append(
                CandidateChannelHit(
                    child_id=child_ref,
                    child_key=child_ref,
                    rank=len(hits) + 1,
                    source_score=round(1.0 - len(hits) * 0.05, 4),
                )
            )
        return tuple(hits)

    def search_case(self, case_id: str, *, top_k: int = 5) -> OfflineSearchResult:
        try:
            case = self.cases[case_id]
        except KeyError as exc:
            raise OfflineRagEvaluationError("V604 case was not found") from exc
        return self.search(
            domain=case["domain"],
            query=case["query"],
            field_codes=case.get("field_codes", ()),
            answer_shapes=case.get("answer_shapes", ()),
            semantic_block_ranking=case.get("semantic_block_ranking", ()),
            case_id=case_id,
            top_k=top_k,
        )

    def search(
        self,
        *,
        domain: Domain,
        query: str,
        field_codes: Sequence[str] = (),
        answer_shapes: Sequence[str] = (),
        semantic_block_ranking: Sequence[str] = (),
        case_id: str = "ad-hoc",
        top_k: int = 5,
    ) -> OfflineSearchResult:
        if domain not in self.indexes:
            raise OfflineRagEvaluationError("V604 search domain is unavailable")
        if not 1 <= top_k <= 40:
            raise OfflineRagEvaluationError("V604 top_k must be between 1 and 40")
        index = self.indexes[domain]
        lexical_ranks = rank_field_aware_bm25f(
            query,
            index.lexical_corpus,
            field_codes=field_codes,
            answer_shapes=answer_shapes,
        )
        lexical_hits = tuple(
            CandidateChannelHit(
                child_id=rank.child_id,
                child_key=rank.child_id,
                rank=position,
                source_score=rank.score,
            )
            for position, rank in enumerate(lexical_ranks[:40], 1)
        )
        semantic_hits = self._semantic_hits(
            index=index,
            block_ranking=semantic_block_ranking,
        )
        fusion = fuse_candidate_channels(
            lexical=lexical_hits,
            semantic=semantic_hits,
            source_index_set_hash=index.source_index_set_hash,
            lexical_projection_set_hash=index.lexical_corpus.corpus_hash,
            semantic_index_set_hash=_sha256(
                {
                    "domain": domain,
                    "precomputed_block_ranking": list(semantic_block_ranking),
                }
            ),
            query_plan_hash=_sha256(
                {
                    "query": query,
                    "field_codes": list(field_codes),
                    "answer_shapes": list(answer_shapes),
                }
            ),
        )
        selected = fusion.candidates[:top_k]
        candidates = tuple(
            EvidenceCandidate(
                evidence_ref=row.child_key,
                excerpt=index.child_by_ref[row.child_key].normalized_text,
                locator=_public_locator(domain, index.child_by_ref[row.child_key]),
                citable=False,
            )
            for row in selected
        )
        provenance = tuple(
            ToolProvenanceRecord(
                output_ref=candidate.evidence_ref,
                source_domain=domain,
                source_scope_ref=index.source_scope_ref,
                source_version_ref=index.source_version_ref,
                content_hash=(
                    "sha256:"
                    + index.child_by_ref[candidate.evidence_ref].retrieval_hash
                ),
                locator=candidate.locator,
                citable=False,
            )
            for candidate in candidates
        )
        return OfflineSearchResult(
            case_id=case_id,
            domain=domain,
            lexical_ranks=lexical_ranks,
            fusion=fusion,
            binding_result=BindingExecutionResult(
                structured_content=EvidenceCandidatesOutput(candidates=candidates),
                provenance=provenance,
            ),
        )

    def _case_search(self, domain: Domain, query: str) -> OfflineSearchResult:
        case = self.case_by_domain_query.get((domain, query))
        if case is None:
            return self.search(domain=domain, query=query)
        return self.search_case(str(case["id"]))

    def _assert_context_scope(
        self,
        *,
        domain: Domain,
        context: ToolExecutionContext,
    ) -> None:
        scope = self.indexes[domain].source_scope_ref
        if domain == "bid_document" and scope not in context.authorized_document_refs:
            raise OfflineRagEvaluationError("bid document scope is not authorized")
        if domain == "enterprise_knowledge" and context.enterprise_scope_ref != scope:
            raise OfflineRagEvaluationError("enterprise scope is not authorized")

    async def search_bid_documents(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        self._assert_context_scope(domain="bid_document", context=context)
        return self._case_search("bid_document", query).binding_result

    async def search_enterprise_knowledge(
        self,
        *,
        query: str,
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        self._assert_context_scope(domain="enterprise_knowledge", context=context)
        return self._case_search("enterprise_knowledge", query).binding_result

    def read(
        self,
        evidence_refs: Sequence[str],
        *,
        context: ToolExecutionContext | None = None,
    ) -> BindingExecutionResult:
        if not evidence_refs or len(evidence_refs) > 32:
            raise OfflineRagEvaluationError("V604 evidence read size is invalid")
        located: list[tuple[OfflineDomainIndex, EvidenceChunkFragment]] = []
        for evidence_ref in evidence_refs:
            matches = [
                index
                for index in self.indexes.values()
                if evidence_ref in index.child_by_ref
            ]
            if len(matches) != 1:
                raise OfflineRagEvaluationError("V604 evidence ref is stale or ambiguous")
            index = matches[0]
            if context is not None:
                self._assert_context_scope(domain=index.domain, context=context)
            for atom in index.atoms_by_child_ref[evidence_ref]:
                located.append((index, atom))
        if not located or len(located) > 32:
            raise OfflineRagEvaluationError("V604 evidence ref has no readable atoms")
        evidence = tuple(
            EvidenceAtom(
                evidence_ref=atom.evidence_key,
                text=atom.normalized_text,
                locator=_public_locator(index.domain, atom),
                citable=True,
            )
            for index, atom in located
        )
        provenance = tuple(
            ToolProvenanceRecord(
                output_ref=item.evidence_ref,
                source_domain=index.domain,
                source_scope_ref=index.source_scope_ref,
                source_version_ref=index.source_version_ref,
                content_hash="sha256:" + atom.text_hash,
                locator=item.locator,
                citable=True,
            )
            for item, (index, atom) in zip(evidence, located, strict=True)
        )
        return BindingExecutionResult(
            structured_content=EvidenceReadOutput(evidence=evidence),
            provenance=provenance,
        )

    async def read_evidence(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context: ToolExecutionContext,
    ) -> BindingExecutionResult:
        return self.read(evidence_refs, context=context)


def evaluate_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    harness = SyntheticOfflineRagHarness(dataset)
    positive_recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    zero_results: list[float] = []
    atom_upgrades: list[float] = []
    per_case: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        result = harness.search_case(case["id"], top_k=5)
        index = harness.indexes[result.domain]
        expected_refs = {
            index.child_ref_by_block_key[block_key]
            for block_key in case["expected_block_keys"]
        }
        top_refs = result.candidate_refs
        top_three = set(top_refs[:3])
        case_checks: dict[str, bool] = {}
        if expected_refs:
            recall = len(top_three & expected_refs) / len(expected_refs)
            positive_recalls.append(recall)
            case_checks["expected_recall"] = recall == 1.0
            first_rank = next(
                (
                    position
                    for position, ref in enumerate(top_refs, 1)
                    if ref in expected_refs
                ),
                None,
            )
            reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
            selected_ref = next((ref for ref in top_refs if ref in expected_refs), None)
            if selected_ref is None:
                atom_upgrades.append(0.0)
            else:
                read = harness.read((selected_ref,))
                output = read.structured_content
                atom_upgrades.append(
                    float(
                        bool(output.evidence)
                        and all(item.citable for item in output.evidence)
                        and all(item.citable for item in read.provenance)
                    )
                )
        else:
            recall = None
            first_rank = None
            zero_results.append(float(not top_refs))
            case_checks["safe_empty_result"] = not top_refs
        top_channels = (
            list(result.fusion.candidates[0].matched_channels)
            if result.fusion.candidates
            else []
        )
        if "expected_top_channels" in case:
            case_checks["expected_top_channels"] = (
                top_channels == case["expected_top_channels"]
            )
        if case.get("expect_lexical_miss") is True:
            case_checks["expected_lexical_miss"] = not result.lexical_ranks
        per_case.append(
            {
                "id": case["id"],
                "domain": case["domain"],
                "candidate_count": len(top_refs),
                "recall_at_3": recall,
                "first_relevant_rank": first_rank,
                "lexical_candidate_count": len(result.lexical_ranks),
                "top_channels": top_channels,
                "checks": case_checks,
                "passed": all(case_checks.values()),
                "result_hash": result.fusion.result_hash,
            }
        )
    metrics = {
        "recall_at_3": round(sum(positive_recalls) / len(positive_recalls), 6),
        "mean_reciprocal_rank": round(
            sum(reciprocal_ranks) / len(reciprocal_ranks), 6
        ),
        "zero_result_precision": round(sum(zero_results) / len(zero_results), 6),
        "atom_upgrade_success": round(sum(atom_upgrades) / len(atom_upgrades), 6),
    }
    thresholds = dataset["thresholds"]
    checks = {
        "recall_at_3": metrics["recall_at_3"]
        >= float(thresholds["recall_at_3_min"]),
        "mean_reciprocal_rank": metrics["mean_reciprocal_rank"]
        >= float(thresholds["mean_reciprocal_rank_min"]),
        "zero_result_precision": metrics["zero_result_precision"]
        >= float(thresholds["zero_result_precision_min"]),
        "atom_upgrade_success": metrics["atom_upgrade_success"]
        >= float(thresholds["atom_upgrade_success_min"]),
        "case_expectations": all(item["passed"] for item in per_case),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": dataset["dataset_kind"],
        "case_count": len(dataset["cases"]),
        "metrics": metrics,
        "checks": checks,
        "passed": all(checks.values()),
        "per_case": per_case,
        "index_hashes": {
            domain: index.source_index_set_hash
            for domain, index in sorted(harness.indexes.items())
        },
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic synthetic V604 offline RAG evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    report = evaluate_dataset(load_dataset(args.dataset))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.resolve().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_PATH",
    "OfflineDomainIndex",
    "OfflineRagEvaluationError",
    "OfflineSearchResult",
    "SCHEMA_VERSION",
    "SyntheticOfflineRagHarness",
    "evaluate_dataset",
    "load_dataset",
]
