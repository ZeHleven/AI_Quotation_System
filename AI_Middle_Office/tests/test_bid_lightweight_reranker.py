from __future__ import annotations

import hashlib

import pytest

from app.services.bid_lightweight_reranker import (
    RQ2C_MAX_PROMOTIONS,
    RQ2C_MODEL_ID,
    RQ2C_MODEL_REVISION,
    RQ2C_PROVIDER_ID,
    BidLightweightRerankerInvalid,
    RerankCandidateInput,
    RerankProviderResult,
    RerankProviderScore,
    RerankerModelDescriptor,
    rerank_frozen_candidates,
    rerank_profile_payload,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(
    rank: int,
    *,
    parent: int | None = None,
    lexical_rank: int | None = None,
    semantic_rank: int | None = None,
) -> RerankCandidateInput:
    return RerankCandidateInput(
        child_id=f"child-{rank}",
        child_key=f"chunk:{_digest(f'child:{rank}')}",
        parent_key=f"section:{_digest(f'parent:{parent or rank}')}",
        fusion_rank=rank,
        fusion_score=round(1.0 / (60 + rank), 10),
        lexical_rank=lexical_rank if lexical_rank is not None else rank,
        semantic_rank=semantic_rank,
        retrieval_hash=_digest(f"retrieval:{rank}"),
        text=f"candidate evidence {rank}",
    )


class _Provider:
    descriptor = RerankerModelDescriptor(
        provider_id=RQ2C_PROVIDER_ID,
        model_id=RQ2C_MODEL_ID,
        model_revision=RQ2C_MODEL_REVISION,
        max_sequence_length=512,
        score_transform="sigmoid",
    )

    def __init__(self, scores: dict[str, float], *, duplicate: bool = False):
        self._scores = scores
        self._duplicate = duplicate

    def score(self, *, query, candidates):
        values = tuple(
            RerankProviderScore(
                child_key=value.child_key,
                score=self._scores[value.child_key],
            )
            for value in candidates
        )
        if self._duplicate:
            values = (*values, values[0])
        return RerankProviderResult(descriptor=self.descriptor, scores=values)


def _run(candidates, scores, *, top_k=5, fusion_hash="f" * 64, query_hash="q"):
    return rerank_frozen_candidates(
        query="履约保证金如何提交和返还",
        candidates=candidates,
        fusion_result_hash=fusion_hash,
        query_plan_hash=_digest(query_hash),
        top_k=top_k,
        provider=_Provider(scores),
    )


def test_rq2c_profile_freezes_bce_window_and_guarded_tail_replacement() -> None:
    profile = rerank_profile_payload()
    assert profile["candidate_window"] == 20
    assert profile["query_source"] == "rq1c_original_query_q1"
    assert profile["model"] == _Provider.descriptor.stable_payload()
    assert profile["selection"] == {
        "policy": "anchor_preserving_positive_margin_tail_replacement",
        "max_promotions": 2,
        "minimum_score": 0.3,
        "minimum_promotion_margin": 0.08,
        "protected": [
            "baseline_top1",
            "best_top_k_minus_2_lexical_anchors",
        ],
        "no_promotion_identity": True,
        "max_children_per_parent": 2,
    }
    assert profile["recall"] is False
    assert profile["candidate_pool_mutation"] is False


def test_zero_promotion_preserves_ordered_rq2b_baseline_exactly() -> None:
    candidates = tuple(_candidate(rank) for rank in range(1, 9))
    scores = {
        value.child_key: round(0.90 - value.fusion_rank * 0.02, 8)
        for value in candidates
    }
    result = _run(candidates, scores)
    assert result.promotion_count == 0
    assert result.final_child_keys == result.baseline_child_keys
    assert result.selected_child_ids == tuple(f"child-{rank}" for rank in range(1, 6))


def test_positive_margin_promotes_at_most_two_and_keeps_lexical_anchors() -> None:
    candidates = tuple(_candidate(rank) for rank in range(1, 9))
    scores = {value.child_key: 0.50 for value in candidates}
    scores[candidates[3].child_key] = 0.35
    scores[candidates[4].child_key] = 0.36
    scores[candidates[5].child_key] = 0.95
    scores[candidates[6].child_key] = 0.90
    scores[candidates[7].child_key] = 0.89
    result = _run(candidates, scores)
    assert result.promotion_count == RQ2C_MAX_PROMOTIONS
    assert result.final_child_keys[:3] == result.baseline_child_keys[:3]
    assert result.final_child_keys[3:] == (
        candidates[6].child_key,
        candidates[5].child_key,
    )
    promoted = [
        value for value in result.candidates if value.promotion_sequence is not None
    ]
    assert [value.promotion_sequence for value in promoted] == [1, 2]
    assert all(value.replaced_child_key for value in promoted)


def test_parent_diversity_may_not_be_worsened_by_promotion() -> None:
    candidates = (
        _candidate(1, parent=1),
        _candidate(2, parent=1),
        _candidate(3, parent=2),
        _candidate(4, parent=3),
        _candidate(5, parent=4),
        _candidate(6, parent=1),
    )
    scores = {value.child_key: 0.40 for value in candidates}
    scores[candidates[5].child_key] = 0.99
    result = _run(candidates, scores)
    assert result.promotion_count == 0
    assert result.final_child_keys == result.baseline_child_keys


def test_provider_must_return_exact_unique_bounded_score_set() -> None:
    candidates = tuple(_candidate(rank) for rank in range(1, 4))
    scores = {value.child_key: 0.5 for value in candidates}
    with pytest.raises(BidLightweightRerankerInvalid, match="PROVIDER_RESULT_INVALID"):
        rerank_frozen_candidates(
            query="test query",
            candidates=candidates,
            fusion_result_hash="f" * 64,
            query_plan_hash="a" * 64,
            top_k=2,
            provider=_Provider(scores, duplicate=True),
        )


def test_result_hash_is_stable_and_invalidates_on_fusion_or_query_plan() -> None:
    candidates = tuple(_candidate(rank) for rank in range(1, 7))
    scores = {value.child_key: 0.5 for value in candidates}
    first = _run(candidates, scores)
    replay = _run(candidates, scores)
    changed_fusion = _run(candidates, scores, fusion_hash="e" * 64)
    changed_query = _run(candidates, scores, query_hash="different")
    assert replay.result_hash == first.result_hash
    assert changed_fusion.result_hash != first.result_hash
    assert changed_query.result_hash != first.result_hash
