from __future__ import annotations

import pytest

from app.services.bid_hybrid_candidate_fusion import (
    RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
    BidHybridCandidateFusionError,
    CandidateChannelHit,
    fuse_candidate_channels,
    fusion_profile_payload,
)


def _hit(child_id: str, child_key: str, rank: int, score: float):
    return CandidateChannelHit(
        child_id=child_id,
        child_key=child_key,
        rank=rank,
        source_score=score,
    )


def _fuse(*, lexical, semantic, hash_seed: str = "a"):
    return fuse_candidate_channels(
        lexical=lexical,
        semantic=semantic,
        source_index_set_hash=hash_seed * 64,
        lexical_projection_set_hash="b" * 64,
        semantic_index_set_hash="c" * 64,
        query_plan_hash="d" * 64,
    )


def test_rq2b_fusion_is_lexical_first_and_rewards_cross_channel_overlap():
    result = _fuse(
        lexical=[
            _hit("child-a", "child:" + "a" * 64, 1, 0.3),
            _hit("child-b", "child:" + "b" * 64, 2, 0.2),
        ],
        semantic=[
            _hit("child-b", "child:" + "b" * 64, 1, 0.9),
            _hit("child-c", "child:" + "c" * 64, 2, 0.8),
        ],
    )
    assert [row.child_id for row in result.candidates] == [
        "child-b",
        "child-a",
        "child-c",
    ]
    assert result.candidates[0].matched_channels == (
        "lexical_bm25f",
        "semantic_bce",
    )
    assert result.candidates[1].fusion_score > result.candidates[2].fusion_score
    assert len(result.result_hash) == 64


def test_rq2b_fusion_hash_uses_stable_child_keys_not_runtime_ids():
    first = _fuse(
        lexical=[_hit("runtime-a", "chunk:" + "a" * 64, 1, 0.1)],
        semantic=[_hit("runtime-a", "chunk:" + "a" * 64, 1, 0.9)],
    )
    replay = _fuse(
        lexical=[_hit("runtime-b", "chunk:" + "a" * 64, 1, 0.1)],
        semantic=[_hit("runtime-b", "chunk:" + "a" * 64, 1, 0.9)],
    )
    assert replay.result_hash == first.result_hash


def test_rq2b_fusion_rejects_rank_and_stable_key_collisions():
    with pytest.raises(BidHybridCandidateFusionError):
        _fuse(
            lexical=[
                _hit("child-a", "child:" + "a" * 64, 1, 0.1),
                _hit("child-b", "child:" + "b" * 64, 1, 0.2),
            ],
            semantic=[],
        )
    with pytest.raises(BidHybridCandidateFusionError):
        _fuse(
            lexical=[_hit("child-a", "child:" + "a" * 64, 1, 0.1)],
            semantic=[_hit("child-a", "child:" + "b" * 64, 1, 0.9)],
        )


def test_rq2b_fusion_hash_invalidates_on_frozen_provenance_change():
    lexical = [_hit("child-a", "child:" + "a" * 64, 1, 0.1)]
    semantic = [_hit("child-a", "child:" + "a" * 64, 1, 0.9)]
    first = _fuse(lexical=lexical, semantic=semantic, hash_seed="a")
    changed = _fuse(lexical=lexical, semantic=semantic, hash_seed="e")
    assert first.result_hash != changed.result_hash
    assert first.to_payload()["source_index_set_hash"] == "a" * 64


def test_rq2b_profile_freezes_rank_only_candidate_fusion():
    profile = fusion_profile_payload()
    assert profile["profile_version"] == RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
    assert profile["channel_candidate_depth"] == 40
    assert profile["weights"] == {
        "lexical": 1.0,
        "semantic": 0.35,
        "cross_channel_overlap_bonus": 0.2,
    }
    assert profile["semantic_only_forced_promotion"] is False
    assert profile["reranker"] is False
