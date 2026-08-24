from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.bid_assessment_pure.rag_adapters import (
    BidDocumentSearchAdapter,
    EnterpriseKnowledgeSearchAdapter,
    EvidenceReadAdapter,
    build_fake_registry,
)
from app.agents.bid_assessment_pure.registry import (
    BID_DOCUMENT_SEARCH,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
)
from app.agents.bid_assessment_pure.tool_runtime import ExecutionDeadline
from app.agents.bid_assessment_pure.tools import (
    BidDocumentSearchInput,
    EnterpriseKnowledgeSearchInput,
    EvidenceCandidatesOutput,
    EvidenceReadInput,
    EvidenceReadOutput,
    ToolExecutionContext,
)
from app.services.bid_evidence_chunk_builder import (
    StructuredEvidenceBlock,
    build_evidence_chunks,
)
from scripts.evaluate_bid_pure_agent_v604 import (
    OfflineRagEvaluationError,
    SyntheticOfflineRagHarness,
    evaluate_dataset,
    load_dataset,
)


def _harness() -> SyntheticOfflineRagHarness:
    return SyntheticOfflineRagHarness(load_dataset())


def _context(
    *,
    authorized_documents: tuple[str, ...] = ("document:synthetic-bid-a",),
    enterprise_scope_ref: str | None = "enterprise-scope:synthetic-a",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        user_ref="user:v604",
        tenant_ref="tenant:v604",
        conversation_ref="conversation:v604",
        task_ref="task:v604",
        state_version=1,
        context_snapshot_ref="context:v604",
        authorization_snapshot_ref="authorization:v604",
        authorized_document_refs=authorized_documents,
        enterprise_scope_ref=enterprise_scope_ref,
    )


def _deadline() -> ExecutionDeadline:
    return ExecutionDeadline(
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=5)
    )


def test_v604_structured_parse_chunk_hierarchy_and_replay_are_deterministic() -> None:
    first = _harness()
    replay = _harness()

    for domain in ("bid_document", "enterprise_knowledge"):
        index = first.indexes[domain]
        replay_index = replay.indexes[domain]
        roles = [fragment.fragment_role for fragment in index.chunk_result.fragments]
        parents = [
            fragment
            for fragment in index.chunk_result.fragments
            if fragment.fragment_role == "section_parent"
        ]
        children = list(index.child_by_ref.values())
        atoms = [
            atom
            for values in index.atoms_by_child_ref.values()
            for atom in values
        ]

        assert parents and children and atoms
        assert all(not item.is_citable for item in parents)
        assert all(not item.is_citable for item in children)
        assert all(item.is_citable for item in atoms)
        assert roles.count("retrieval_child") == len(children)
        assert all(atom.parent_key in index.child_by_ref for atom in atoms)
        assert index.chunk_result.result_hash == replay_index.chunk_result.result_hash
        assert index.source_index_set_hash == replay_index.source_index_set_hash
        assert index.lexical_corpus.corpus_hash == replay_index.lexical_corpus.corpus_hash

    assert "bid_revoked" not in first.indexes["bid_document"].child_ref_by_block_key
    assert (
        "enterprise_revoked"
        not in first.indexes["enterprise_knowledge"].child_ref_by_block_key
    )


def test_v604_long_chunk_overlap_is_bounded_traceable_and_stable() -> None:
    source = ("甲" * 499 + "。") * 3
    block = StructuredEvidenceBlock(
        block_key="synthetic-long",
        text=source,
        block_type="paragraph",
        page_no=1,
        ordinal=0,
        section_path=("合成长文本",),
    )
    first = build_evidence_chunks((block,), document_label="合成长文本")
    replay = build_evidence_chunks((block,), document_label="合成长文本")
    atoms = sorted(
        (
            fragment
            for fragment in first.fragments
            if fragment.fragment_role == "evidence_atom"
        ),
        key=lambda fragment: fragment.locator["split_index"],
    )

    assert len(atoms) >= 3
    assert atoms[0].locator["overlap_left_tokens"] == 0
    assert all(atom.locator["overlap_left_tokens"] == 80 for atom in atoms[1:])
    assert first.metrics["max_child_tokens"] <= 600
    assert first.metrics["overlap_estimated_tokens"] == 80 * (len(atoms) - 1)
    assert first.result_hash == replay.result_hash
    assert [atom.locator_hash for atom in atoms] == [
        atom.locator_hash
        for atom in replay.fragments
        if atom.fragment_role == "evidence_atom"
    ]


def test_v604_bm25f_exact_recall_and_domain_scope_are_correct() -> None:
    harness = _harness()
    deadline = harness.search_case("R01_bid_deadline_lexical")
    enterprise = harness.search_case("R04_enterprise_qualification_lexical")
    cross_scope = harness.search_case("R08_domain_scope_prevents_cross_leakage")

    bid_index = harness.indexes["bid_document"]
    enterprise_index = harness.indexes["enterprise_knowledge"]
    assert deadline.candidate_refs[0] == bid_index.child_ref_by_block_key["bid_deadline"]
    assert "table_key" in deadline.lexical_ranks[0].matched_channels
    assert enterprise.candidate_refs[0] == (
        enterprise_index.child_ref_by_block_key["enterprise_qualification"]
    )
    assert cross_scope.candidate_refs == ()
    assert all(
        ref in bid_index.child_by_ref for ref in deadline.candidate_refs
    )
    assert all(
        ref in enterprise_index.child_by_ref for ref in enterprise.candidate_refs
    )


def test_v604_precomputed_semantic_rank_and_rrf_are_bounded_and_stable() -> None:
    harness = _harness()
    semantic = harness.search_case("R03_bid_performance_semantic_synonym")
    hybrid = harness.search_case("R02_bid_guarantee_hybrid_overlap")
    replay = harness.search_case("R02_bid_guarantee_hybrid_overlap")
    index = harness.indexes["bid_document"]

    assert semantic.lexical_ranks == ()
    assert semantic.candidate_refs == (
        index.child_ref_by_block_key["bid_performance"],
    )
    assert semantic.fusion.candidates[0].matched_channels == ("semantic_bce",)
    assert hybrid.candidate_refs[0] == index.child_ref_by_block_key["bid_guarantee"]
    assert hybrid.fusion.candidates[0].matched_channels == (
        "lexical_bm25f",
        "semantic_bce",
    )
    assert len(set(hybrid.candidate_refs)) == len(hybrid.candidate_refs)
    assert hybrid.fusion.result_hash == replay.fusion.result_hash
    assert hybrid.fusion.source_index_set_hash == index.source_index_set_hash
    assert hybrid.fusion.lexical_projection_set_hash == index.lexical_corpus.corpus_hash


def test_v604_zero_result_and_revoked_content_are_safe_empty_successes() -> None:
    harness = _harness()
    absent = harness.search_case("R06_zero_result_is_not_failure")
    revoked = harness.search_case("R07_revoked_bid_block_excluded")

    for result in (absent, revoked):
        output = result.binding_result.structured_content
        assert isinstance(output, EvidenceCandidatesOutput)
        assert output.candidates == ()
        assert result.binding_result.provenance == ()
        assert result.fusion.candidates == ()


def test_v604_search_candidate_upgrades_to_citable_atom_through_adapters() -> None:
    harness = _harness()
    registry = build_fake_registry()
    context = _context()
    bid_adapter = BidDocumentSearchAdapter(harness)
    enterprise_adapter = EnterpriseKnowledgeSearchAdapter(harness)
    read_adapter = EvidenceReadAdapter(harness)

    bid_result = asyncio.run(
        bid_adapter.execute(
            definition=registry.get(BID_DOCUMENT_SEARCH),
            arguments=BidDocumentSearchInput(query="投标保证金50万元"),
            context=context,
            deadline=_deadline(),
        )
    )
    enterprise_result = asyncio.run(
        enterprise_adapter.execute(
            definition=registry.get(ENTERPRISE_KNOWLEDGE_SEARCH),
            arguments=EnterpriseKnowledgeSearchInput(
                query="建筑装修装饰工程专业承包一级资质"
            ),
            context=context,
            deadline=_deadline(),
        )
    )
    candidate_refs = (
        bid_result.structured_content.candidates[0].evidence_ref,
        enterprise_result.structured_content.candidates[0].evidence_ref,
    )
    read_result = asyncio.run(
        read_adapter.execute(
            definition=registry.get(EVIDENCE_READ),
            arguments=EvidenceReadInput(evidence_refs=candidate_refs),
            context=context,
            deadline=_deadline(),
        )
    )

    assert all(not item.citable for item in bid_result.structured_content.candidates)
    assert all(not item.citable for item in enterprise_result.structured_content.candidates)
    assert all(not item.citable for item in bid_result.provenance)
    assert all(not item.citable for item in enterprise_result.provenance)
    assert isinstance(read_result.structured_content, EvidenceReadOutput)
    assert all(item.citable for item in read_result.structured_content.evidence)
    assert all(item.citable for item in read_result.provenance)
    assert set(candidate_refs).isdisjoint(
        {item.evidence_ref for item in read_result.structured_content.evidence}
    )
    assert {item.source_domain for item in read_result.provenance} == {
        "bid_document",
        "enterprise_knowledge",
    }


def test_v604_source_ports_fail_closed_on_unauthorized_scope() -> None:
    harness = _harness()
    registry = build_fake_registry()

    with pytest.raises(OfflineRagEvaluationError, match="not authorized"):
        asyncio.run(
            BidDocumentSearchAdapter(harness).execute(
                definition=registry.get(BID_DOCUMENT_SEARCH),
                arguments=BidDocumentSearchInput(query="投标截止时间"),
                context=_context(authorized_documents=()),
                deadline=_deadline(),
            )
        )
    with pytest.raises(OfflineRagEvaluationError, match="not authorized"):
        asyncio.run(
            EnterpriseKnowledgeSearchAdapter(harness).execute(
                definition=registry.get(ENTERPRISE_KNOWLEDGE_SEARCH),
                arguments=EnterpriseKnowledgeSearchInput(query="企业资质"),
                context=_context(enterprise_scope_ref=None),
                deadline=_deadline(),
            )
        )


def test_v604_recall_report_meets_frozen_synthetic_thresholds() -> None:
    report = evaluate_dataset(load_dataset())

    assert report["case_count"] == 8
    assert report["metrics"] == {
        "recall_at_3": 1.0,
        "mean_reciprocal_rank": 1.0,
        "zero_result_precision": 1.0,
        "atom_upgrade_success": 1.0,
    }
    assert report["checks"] == {
        "recall_at_3": True,
        "mean_reciprocal_rank": True,
        "zero_result_precision": True,
        "atom_upgrade_success": True,
        "case_expectations": True,
    }
    assert report["passed"] is True
    assert all(item["passed"] for item in report["per_case"])
    assert all(len(value) == 64 for value in report["index_hashes"].values())
