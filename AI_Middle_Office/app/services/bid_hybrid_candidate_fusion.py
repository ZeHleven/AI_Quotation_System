"""Deterministic RQ2-B lexical/semantic candidate fusion.

RQ2-B combines two already-governed Child-only recall channels.  It does not
read source documents, create facts, cite evidence, or execute a reranker.
All scores are rank-derived so backend-specific BM25 and cosine magnitudes are
never compared directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.services.bid_assessment_eventing import canonical_hash


CANDIDATE_FUSION_CONTRACT_VERSION = "bid.evidence.candidate-fusion.v1"
DISABLED_CANDIDATE_FUSION_PROFILE_VERSION = (
    "bid-evidence-candidate-fusion-profile-v0-disabled"
)
RQ2B_CANDIDATE_FUSION_PROFILE_VERSION = (
    "bid-evidence-candidate-fusion-profile-v1-rq2b"
)
RQ2B_FUSION_RRF_K = 60
RQ2B_LEXICAL_WEIGHT = 1.0
RQ2B_SEMANTIC_WEIGHT = 0.35
RQ2B_OVERLAP_BONUS_WEIGHT = 0.20
RQ2B_CHANNEL_CANDIDATE_DEPTH = 40


class BidHybridCandidateFusionError(RuntimeError):
    code = "BID_EVIDENCE_CANDIDATE_FUSION_INVALID"


@dataclass(frozen=True)
class CandidateChannelHit:
    child_id: str
    child_key: str
    rank: int
    source_score: float


@dataclass(frozen=True)
class FusedCandidate:
    child_id: str
    child_key: str
    fusion_score: float
    lexical_rank: int | None
    semantic_rank: int | None
    lexical_source_score: float | None
    semantic_source_score: float | None
    matched_channels: tuple[str, ...]

    def stable_payload(self) -> dict[str, object]:
        return {
            "child_key": self.child_key,
            "fusion_score": self.fusion_score,
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "lexical_source_score": self.lexical_source_score,
            "semantic_source_score": self.semantic_source_score,
            "matched_channels": list(self.matched_channels),
        }


@dataclass(frozen=True)
class CandidateFusionResult:
    candidates: tuple[FusedCandidate, ...]
    source_index_set_hash: str
    lexical_projection_set_hash: str
    semantic_index_set_hash: str
    query_plan_hash: str
    result_hash: str

    def stable_payload(self) -> dict[str, object]:
        return {
            "contract_version": CANDIDATE_FUSION_CONTRACT_VERSION,
            "profile_version": RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
            "source_index_set_hash": self.source_index_set_hash,
            "lexical_projection_set_hash": self.lexical_projection_set_hash,
            "semantic_index_set_hash": self.semantic_index_set_hash,
            "query_plan_hash": self.query_plan_hash,
            "lexical_candidate_count": sum(
                value.lexical_rank is not None for value in self.candidates
            ),
            "semantic_candidate_count": sum(
                value.semantic_rank is not None for value in self.candidates
            ),
            "union_candidate_count": len(self.candidates),
            "overlap_candidate_count": sum(
                len(value.matched_channels) == 2 for value in self.candidates
            ),
            "candidates": [value.stable_payload() for value in self.candidates],
        }

    def to_payload(self) -> dict[str, object]:
        return {**self.stable_payload(), "result_hash": self.result_hash}


def fusion_profile_payload() -> dict[str, object]:
    return {
        "contract_version": CANDIDATE_FUSION_CONTRACT_VERSION,
        "profile_version": RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
        "rrf_k": RQ2B_FUSION_RRF_K,
        "channel_candidate_depth": RQ2B_CHANNEL_CANDIDATE_DEPTH,
        "weights": {
            "lexical": RQ2B_LEXICAL_WEIGHT,
            "semantic": RQ2B_SEMANTIC_WEIGHT,
            "cross_channel_overlap_bonus": RQ2B_OVERLAP_BONUS_WEIGHT,
        },
        "score_policy": "rank_only_weighted_rrf",
        "tie_break": "matched_channel_count_then_stable_child_key",
        "semantic_only_forced_promotion": False,
        "reranker": False,
    }


def _channel_map(
    values: Sequence[CandidateChannelHit],
    *,
    channel: str,
) -> dict[str, CandidateChannelHit]:
    if len(values) > RQ2B_CHANNEL_CANDIDATE_DEPTH:
        raise BidHybridCandidateFusionError(
            "BID_EVIDENCE_CANDIDATE_FUSION_DEPTH_EXCEEDED"
        )
    by_key: dict[str, CandidateChannelHit] = {}
    ids: dict[str, str] = {}
    ranks: set[int] = set()
    for value in values:
        child_id = str(value.child_id).strip()
        child_key = str(value.child_key).strip()
        rank = int(value.rank)
        if (
            not child_id
            or re.fullmatch(r"(?:child|chunk):[a-f0-9]{64}", child_key) is None
            or not 1 <= rank <= RQ2B_CHANNEL_CANDIDATE_DEPTH
            or rank in ranks
            or child_key in by_key
            or not -1_000_000.0 <= float(value.source_score) <= 1_000_000.0
        ):
            raise BidHybridCandidateFusionError(
                "BID_EVIDENCE_CANDIDATE_FUSION_CHANNEL_INVALID:"
                + channel
            )
        existing_key = ids.get(child_id)
        if existing_key is not None and existing_key != child_key:
            raise BidHybridCandidateFusionError(
                "BID_EVIDENCE_CANDIDATE_FUSION_CHILD_ID_COLLISION"
            )
        by_key[child_key] = value
        ids[child_id] = child_key
        ranks.add(rank)
    return by_key


def fuse_candidate_channels(
    *,
    lexical: Sequence[CandidateChannelHit],
    semantic: Sequence[CandidateChannelHit],
    source_index_set_hash: str,
    lexical_projection_set_hash: str,
    semantic_index_set_hash: str,
    query_plan_hash: str,
) -> CandidateFusionResult:
    provenance_hashes = (
        source_index_set_hash,
        lexical_projection_set_hash,
        semantic_index_set_hash,
        query_plan_hash,
    )
    if any(
        re.fullmatch(r"[a-f0-9]{64}", str(value)) is None
        for value in provenance_hashes
    ):
        raise BidHybridCandidateFusionError(
            "BID_EVIDENCE_CANDIDATE_FUSION_PROVENANCE_INVALID"
        )
    lexical_by_key = _channel_map(lexical, channel="lexical")
    semantic_by_key = _channel_map(semantic, channel="semantic")
    key_by_child_id: dict[str, str] = {}
    for hit in (*lexical_by_key.values(), *semantic_by_key.values()):
        existing_key = key_by_child_id.get(hit.child_id)
        if existing_key is not None and existing_key != hit.child_key:
            raise BidHybridCandidateFusionError(
                "BID_EVIDENCE_CANDIDATE_FUSION_CHILD_ID_COLLISION"
            )
        key_by_child_id[hit.child_id] = hit.child_key
    candidates: list[FusedCandidate] = []
    for child_key in sorted(set(lexical_by_key) | set(semantic_by_key)):
        lexical_hit = lexical_by_key.get(child_key)
        semantic_hit = semantic_by_key.get(child_key)
        selected_hit = lexical_hit or semantic_hit
        if selected_hit is None:  # Defensive; the union above is non-empty.
            raise BidHybridCandidateFusionError(
                "BID_EVIDENCE_CANDIDATE_FUSION_CHANNEL_INVALID"
            )
        child_id = selected_hit.child_id
        if (
            lexical_hit is not None
            and semantic_hit is not None
            and lexical_hit.child_id != semantic_hit.child_id
        ):
            raise BidHybridCandidateFusionError(
                "BID_EVIDENCE_CANDIDATE_FUSION_CHILD_ID_MISMATCH"
            )
        score = 0.0
        channels: list[str] = []
        if lexical_hit is not None:
            score += RQ2B_LEXICAL_WEIGHT / (
                RQ2B_FUSION_RRF_K + lexical_hit.rank
            )
            channels.append("lexical_bm25f")
        if semantic_hit is not None:
            score += RQ2B_SEMANTIC_WEIGHT / (
                RQ2B_FUSION_RRF_K + semantic_hit.rank
            )
            channels.append("semantic_bce")
        if lexical_hit is not None and semantic_hit is not None:
            score += RQ2B_OVERLAP_BONUS_WEIGHT / (
                RQ2B_FUSION_RRF_K
                + min(lexical_hit.rank, semantic_hit.rank)
            )
        candidates.append(
            FusedCandidate(
                child_id=child_id,
                child_key=child_key,
                fusion_score=round(score, 10),
                lexical_rank=(lexical_hit.rank if lexical_hit else None),
                semantic_rank=(semantic_hit.rank if semantic_hit else None),
                lexical_source_score=(
                    round(float(lexical_hit.source_score), 10)
                    if lexical_hit
                    else None
                ),
                semantic_source_score=(
                    round(float(semantic_hit.source_score), 10)
                    if semantic_hit
                    else None
                ),
                matched_channels=tuple(channels),
            )
        )
    candidates.sort(
        key=lambda value: (
            -value.fusion_score,
            -len(value.matched_channels),
            value.child_key,
            value.child_id,
        )
    )
    pending = CandidateFusionResult(
        candidates=tuple(candidates),
        source_index_set_hash=str(source_index_set_hash),
        lexical_projection_set_hash=str(lexical_projection_set_hash),
        semantic_index_set_hash=str(semantic_index_set_hash),
        query_plan_hash=str(query_plan_hash),
        result_hash="",
    )
    return CandidateFusionResult(
        candidates=pending.candidates,
        source_index_set_hash=pending.source_index_set_hash,
        lexical_projection_set_hash=pending.lexical_projection_set_hash,
        semantic_index_set_hash=pending.semantic_index_set_hash,
        query_plan_hash=pending.query_plan_hash,
        result_hash=canonical_hash(pending.stable_payload()),
    )


__all__ = [
    "CANDIDATE_FUSION_CONTRACT_VERSION",
    "DISABLED_CANDIDATE_FUSION_PROFILE_VERSION",
    "RQ2B_CANDIDATE_FUSION_PROFILE_VERSION",
    "RQ2B_CHANNEL_CANDIDATE_DEPTH",
    "RQ2B_FUSION_RRF_K",
    "RQ2B_LEXICAL_WEIGHT",
    "RQ2B_OVERLAP_BONUS_WEIGHT",
    "RQ2B_SEMANTIC_WEIGHT",
    "BidHybridCandidateFusionError",
    "CandidateChannelHit",
    "CandidateFusionResult",
    "FusedCandidate",
    "fuse_candidate_channels",
    "fusion_profile_payload",
]
