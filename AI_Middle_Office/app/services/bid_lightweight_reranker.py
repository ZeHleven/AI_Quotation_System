"""RQ2-C bounded cross-encoder reranking over a frozen RQ2-B candidate pool.

The reranker is deliberately a selector, not a second recall channel.  It
scores at most the first 20 immutable fused Retrieval Children and may replace
at most two unprotected tail positions in the exact RQ2-B baseline.  It never
reads source files, changes evidence roles, or turns Search hits into citable
evidence.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from app.services.bid_assessment_eventing import canonical_hash


LIGHTWEIGHT_RERANK_CONTRACT_VERSION = "bid.evidence.lightweight-rerank.v1"
DISABLED_RERANK_PROFILE_VERSION = "bid-evidence-rerank-profile-v0-disabled"
RQ2C_RERANK_PROFILE_VERSION = "bid-evidence-rerank-profile-v1-rq2c-bce"
RQ2C_PROVIDER_ID = "bce-cross-encoder-local"
RQ2C_MODEL_ID = "maidalun1020/bce-reranker-base_v1"
RQ2C_MODEL_REVISION = "eb7650fca1d81e2856fbd0d522488844aa502735"
RQ2C_SCORE_TRANSFORM = "sigmoid"
RQ2C_MAX_SEQUENCE_LENGTH = 512
RQ2C_CANDIDATE_WINDOW = 20
RQ2C_MAX_PROMOTIONS = 2
RQ2C_MIN_SCORE = 0.30
RQ2C_MIN_PROMOTION_MARGIN = 0.08
RQ2C_MAX_CHILDREN_PER_PARENT = 2

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_CHILD_KEY = re.compile(r"^(?:child|chunk):[a-f0-9]{64}$")
_PARENT_KEY = re.compile(r"^section:[a-f0-9]{64}$")


class BidLightweightRerankerError(RuntimeError):
    code = "BID_EVIDENCE_RERANK_INVALID"
    retryable = False


class BidLightweightRerankerUnavailable(BidLightweightRerankerError):
    code = "BID_RERANK_PROVIDER_UNAVAILABLE"
    retryable = True


class BidLightweightRerankerInvalid(BidLightweightRerankerError):
    code = "BID_RERANK_PROVIDER_INVALID"


@dataclass(frozen=True)
class RerankerModelDescriptor:
    provider_id: str
    model_id: str
    model_revision: str
    max_sequence_length: int
    score_transform: str

    def stable_payload(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "max_sequence_length": self.max_sequence_length,
            "score_transform": self.score_transform,
        }


@dataclass(frozen=True)
class RerankCandidateInput:
    child_id: str
    child_key: str
    parent_key: str
    fusion_rank: int
    fusion_score: float
    lexical_rank: int | None
    semantic_rank: int | None
    retrieval_hash: str
    text: str

    def stable_input_payload(self, *, query: str) -> dict[str, object]:
        return {
            "child_key": self.child_key,
            "parent_key": self.parent_key,
            "fusion_rank": self.fusion_rank,
            "fusion_score": self.fusion_score,
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "retrieval_hash": self.retrieval_hash,
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class RerankProviderScore:
    child_key: str
    score: float


@dataclass(frozen=True)
class RerankProviderResult:
    descriptor: RerankerModelDescriptor
    scores: tuple[RerankProviderScore, ...]


class BidRerankerProvider(Protocol):
    @property
    def descriptor(self) -> RerankerModelDescriptor: ...

    def score(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidateInput],
    ) -> RerankProviderResult: ...


@dataclass(frozen=True)
class RerankedCandidate:
    child_id: str
    child_key: str
    fusion_rank: int
    rerank_score: float
    input_hash: str
    protected_anchor: bool
    selected: bool
    final_rank: int | None
    promotion_sequence: int | None
    replaced_child_key: str | None

    def stable_payload(self) -> dict[str, object]:
        return {
            "child_key": self.child_key,
            "fusion_rank": self.fusion_rank,
            "rerank_score": self.rerank_score,
            "input_hash": self.input_hash,
            "protected_anchor": self.protected_anchor,
            "selected": self.selected,
            "final_rank": self.final_rank,
            "promotion_sequence": self.promotion_sequence,
            "replaced_child_key": self.replaced_child_key,
        }


@dataclass(frozen=True)
class LightweightRerankResult:
    candidates: tuple[RerankedCandidate, ...]
    selected_child_ids: tuple[str, ...]
    fusion_result_hash: str
    query_plan_hash: str
    model_descriptor: RerankerModelDescriptor
    top_k: int
    baseline_child_keys: tuple[str, ...]
    final_child_keys: tuple[str, ...]
    protected_child_keys: tuple[str, ...]
    promotion_count: int
    result_hash: str

    def stable_payload(self) -> dict[str, object]:
        return {
            "contract_version": LIGHTWEIGHT_RERANK_CONTRACT_VERSION,
            "profile_version": RQ2C_RERANK_PROFILE_VERSION,
            "fusion_result_hash": self.fusion_result_hash,
            "query_plan_hash": self.query_plan_hash,
            "model": self.model_descriptor.stable_payload(),
            "candidate_window": len(self.candidates),
            "top_k": self.top_k,
            "baseline_child_keys": list(self.baseline_child_keys),
            "final_child_keys": list(self.final_child_keys),
            "protected_child_keys": list(self.protected_child_keys),
            "promotion_count": self.promotion_count,
            "candidates": [value.stable_payload() for value in self.candidates],
        }

    def to_payload(self) -> dict[str, object]:
        return {**self.stable_payload(), "result_hash": self.result_hash}


def rerank_profile_payload() -> dict[str, object]:
    return {
        "contract_version": LIGHTWEIGHT_RERANK_CONTRACT_VERSION,
        "profile_version": RQ2C_RERANK_PROFILE_VERSION,
        "candidate_source": "rq2b_frozen_fusion_order",
        "candidate_window": RQ2C_CANDIDATE_WINDOW,
        "query_source": "rq1c_original_query_q1",
        "model": RerankerModelDescriptor(
            provider_id=RQ2C_PROVIDER_ID,
            model_id=RQ2C_MODEL_ID,
            model_revision=RQ2C_MODEL_REVISION,
            max_sequence_length=RQ2C_MAX_SEQUENCE_LENGTH,
            score_transform=RQ2C_SCORE_TRANSFORM,
        ).stable_payload(),
        "selection": {
            "policy": "anchor_preserving_positive_margin_tail_replacement",
            "max_promotions": RQ2C_MAX_PROMOTIONS,
            "minimum_score": RQ2C_MIN_SCORE,
            "minimum_promotion_margin": RQ2C_MIN_PROMOTION_MARGIN,
            "protected": [
                "baseline_top1",
                "best_top_k_minus_2_lexical_anchors",
            ],
            "no_promotion_identity": True,
            "max_children_per_parent": RQ2C_MAX_CHILDREN_PER_PARENT,
        },
        "recall": False,
        "candidate_pool_mutation": False,
        "generation_model": False,
    }


def validate_reranker_descriptor(descriptor: RerankerModelDescriptor) -> None:
    expected = rerank_profile_payload()["model"]
    if descriptor.stable_payload() != expected:
        raise BidLightweightRerankerInvalid("BID_RERANK_MODEL_PROFILE_MISMATCH")


class LocalBceCrossEncoderReranker:
    """Lazy, cache-only BCE sequence-classification adapter."""

    def __init__(
        self,
        *,
        model_path: str = "",
        model_cache_dir: str = "",
        offline: bool = True,
        batch_size: int = 8,
    ):
        normalized_path = str(model_path).strip()
        if normalized_path and normalized_path != RQ2C_MODEL_ID:
            path_parts = normalized_path.replace("\\", "/").split("/")
            if RQ2C_MODEL_REVISION not in path_parts:
                raise BidLightweightRerankerInvalid(
                    "BID_RERANK_MODEL_PATH_REVISION_MISMATCH"
                )
        if not offline:
            raise BidLightweightRerankerInvalid("BID_RERANK_OFFLINE_REQUIRED")
        if not 1 <= int(batch_size) <= RQ2C_CANDIDATE_WINDOW:
            raise BidLightweightRerankerInvalid("BID_RERANK_BATCH_SIZE_INVALID")
        self._model_path = normalized_path or RQ2C_MODEL_ID
        self._model_cache_dir = str(model_cache_dir).strip() or None
        self._batch_size = int(batch_size)
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._lock = threading.RLock()
        self._descriptor = RerankerModelDescriptor(
            provider_id=RQ2C_PROVIDER_ID,
            model_id=RQ2C_MODEL_ID,
            model_revision=RQ2C_MODEL_REVISION,
            max_sequence_length=RQ2C_MAX_SEQUENCE_LENGTH,
            score_transform=RQ2C_SCORE_TRANSFORM,
        )

    @property
    def descriptor(self) -> RerankerModelDescriptor:
        return self._descriptor

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._model is not None and self._tokenizer is not None:
                return self._tokenizer, self._model
            try:
                from transformers import (
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                kwargs: dict[str, Any] = {
                    "cache_dir": self._model_cache_dir,
                    "local_files_only": True,
                }
                if self._model_path == RQ2C_MODEL_ID:
                    kwargs["revision"] = RQ2C_MODEL_REVISION
                self._tokenizer = AutoTokenizer.from_pretrained(
                    self._model_path,
                    **kwargs,
                )
                self._model = AutoModelForSequenceClassification.from_pretrained(
                    self._model_path,
                    **kwargs,
                )
                self._model.eval()
            except Exception as exc:  # pragma: no cover - runtime dependency
                raise BidLightweightRerankerUnavailable(
                    "BID_RERANK_MODEL_UNAVAILABLE"
                ) from exc
            return self._tokenizer, self._model

    def score(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidateInput],
    ) -> RerankProviderResult:
        tokenizer, model = self._load()
        try:
            import torch

            values: list[RerankProviderScore] = []
            for offset in range(0, len(candidates), self._batch_size):
                batch = candidates[offset : offset + self._batch_size]
                encoded = tokenizer(
                    [(query, value.text) for value in batch],
                    padding=True,
                    truncation=True,
                    max_length=RQ2C_MAX_SEQUENCE_LENGTH,
                    return_tensors="pt",
                )
                with torch.no_grad():
                    logits = model(**encoded).logits.reshape(-1)
                    scores = torch.sigmoid(logits).detach().cpu().tolist()
                values.extend(
                    RerankProviderScore(
                        child_key=value.child_key,
                        score=round(float(score), 8),
                    )
                    for value, score in zip(batch, scores, strict=True)
                )
        except BidLightweightRerankerError:
            raise
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise BidLightweightRerankerUnavailable(
                "BID_RERANK_EXECUTION_FAILED"
            ) from exc
        return RerankProviderResult(
            descriptor=self.descriptor,
            scores=tuple(values),
        )


def configured_bid_reranker_provider(settings: Any) -> BidRerankerProvider:
    if str(getattr(settings, "bid_evidence_reranker_provider_id", "")) != RQ2C_PROVIDER_ID:
        raise BidLightweightRerankerInvalid("BID_RERANK_PROVIDER_NOT_CONFIGURED")
    return LocalBceCrossEncoderReranker(
        model_path=str(getattr(settings, "bid_evidence_reranker_model_path", "")),
        model_cache_dir=str(
            getattr(settings, "bid_evidence_reranker_model_cache_dir", "")
        ),
        offline=bool(getattr(settings, "bid_evidence_reranker_offline", True)),
        batch_size=int(getattr(settings, "bid_evidence_reranker_batch_size", 8)),
    )


def _validate_candidate_window(
    candidates: Sequence[RerankCandidateInput],
) -> tuple[RerankCandidateInput, ...]:
    values = tuple(candidates)
    if not 1 <= len(values) <= RQ2C_CANDIDATE_WINDOW:
        raise BidLightweightRerankerInvalid("BID_RERANK_CANDIDATE_WINDOW_INVALID")
    if tuple(value.fusion_rank for value in values) != tuple(
        range(1, len(values) + 1)
    ):
        raise BidLightweightRerankerInvalid("BID_RERANK_FUSION_ORDER_INVALID")
    if len({value.child_id for value in values}) != len(values) or len(
        {value.child_key for value in values}
    ) != len(values):
        raise BidLightweightRerankerInvalid("BID_RERANK_CANDIDATE_ID_COLLISION")
    for value in values:
        if (
            not value.child_id
            or _CHILD_KEY.fullmatch(value.child_key) is None
            or _PARENT_KEY.fullmatch(value.parent_key) is None
            or _HEX64.fullmatch(value.retrieval_hash) is None
            or not value.text.strip()
            or not math.isfinite(float(value.fusion_score))
            or value.fusion_score < 0
            or (
                value.lexical_rank is not None
                and not 1 <= value.lexical_rank <= 40
            )
            or (
                value.semantic_rank is not None
                and not 1 <= value.semantic_rank <= 40
            )
            or value.lexical_rank is None
            and value.semantic_rank is None
        ):
            raise BidLightweightRerankerInvalid("BID_RERANK_CANDIDATE_INVALID")
    return values


def _diversified_baseline(
    candidates: Sequence[RerankCandidateInput],
    *,
    top_k: int,
) -> list[RerankCandidateInput]:
    selected: list[RerankCandidateInput] = []
    overflow: list[RerankCandidateInput] = []
    counts: Counter[str] = Counter()
    for candidate in candidates:
        if counts[candidate.parent_key] < RQ2C_MAX_CHILDREN_PER_PARENT:
            selected.append(candidate)
            counts[candidate.parent_key] += 1
        else:
            overflow.append(candidate)
        if len(selected) >= top_k:
            break
    if len(selected) < top_k:
        selected.extend(overflow[: top_k - len(selected)])
    return selected


def _protected_anchors(
    baseline: Sequence[RerankCandidateInput],
) -> set[str]:
    if not baseline:
        return set()
    protected = {baseline[0].child_key}
    lexical_floor = max(1, len(baseline) - RQ2C_MAX_PROMOTIONS)
    lexical = sorted(
        (value for value in baseline if value.lexical_rank is not None),
        key=lambda value: (value.lexical_rank, value.fusion_rank, value.child_key),
    )
    protected.update(value.child_key for value in lexical[:lexical_floor])
    return protected


def _parent_limit_holds(
    selected: Sequence[RerankCandidateInput],
    *,
    replacement_index: int,
    candidate: RerankCandidateInput,
) -> bool:
    before_max = max(
        Counter(value.parent_key for value in selected).values(),
        default=0,
    )
    simulated = list(selected)
    simulated[replacement_index] = candidate
    after_max = max(
        Counter(value.parent_key for value in simulated).values(),
        default=0,
    )
    return after_max <= max(RQ2C_MAX_CHILDREN_PER_PARENT, before_max)


def rerank_frozen_candidates(
    *,
    query: str,
    candidates: Sequence[RerankCandidateInput],
    fusion_result_hash: str,
    query_plan_hash: str,
    top_k: int,
    provider: BidRerankerProvider,
) -> LightweightRerankResult:
    normalized_query = str(query).strip()
    values = _validate_candidate_window(candidates)
    if (
        not normalized_query
        or len(normalized_query) > 500
        or _HEX64.fullmatch(str(fusion_result_hash)) is None
        or _HEX64.fullmatch(str(query_plan_hash)) is None
        or not 1 <= int(top_k) <= 8
    ):
        raise BidLightweightRerankerInvalid("BID_RERANK_REQUEST_INVALID")
    bounded_top_k = min(int(top_k), len(values))
    baseline = _diversified_baseline(values, top_k=bounded_top_k)
    protected = _protected_anchors(baseline)
    try:
        provider_result = provider.score(query=normalized_query, candidates=values)
    except BidLightweightRerankerError:
        raise
    except Exception as exc:
        raise BidLightweightRerankerUnavailable(
            "BID_RERANK_PROVIDER_UNAVAILABLE"
        ) from exc
    validate_reranker_descriptor(provider_result.descriptor)
    score_by_key: dict[str, float] = {}
    for value in provider_result.scores:
        score = float(value.score)
        if (
            value.child_key in score_by_key
            or not math.isfinite(score)
            or not 0.0 <= score <= 1.0
        ):
            raise BidLightweightRerankerInvalid("BID_RERANK_PROVIDER_RESULT_INVALID")
        score_by_key[value.child_key] = round(score, 8)
    if set(score_by_key) != {value.child_key for value in values}:
        raise BidLightweightRerankerInvalid("BID_RERANK_PROVIDER_RESULT_INVALID")

    selected = list(baseline)
    promotion_by_key: dict[str, tuple[int, str]] = {}
    ranked_proposals = sorted(
        values,
        key=lambda value: (
            -score_by_key[value.child_key],
            value.fusion_rank,
            value.child_key,
        ),
    )
    for proposal in ranked_proposals:
        if len(promotion_by_key) >= RQ2C_MAX_PROMOTIONS:
            break
        selected_keys = {value.child_key for value in selected}
        proposal_score = score_by_key[proposal.child_key]
        if proposal.child_key in selected_keys or proposal_score < RQ2C_MIN_SCORE:
            continue
        victim_index = None
        victim_key = None
        for index in range(len(selected) - 1, -1, -1):
            victim = selected[index]
            if victim.child_key in protected:
                continue
            if (
                proposal_score - score_by_key[victim.child_key]
                < RQ2C_MIN_PROMOTION_MARGIN
            ):
                continue
            if not _parent_limit_holds(
                selected,
                replacement_index=index,
                candidate=proposal,
            ):
                continue
            victim_index = index
            victim_key = victim.child_key
            break
        if victim_index is None or victim_key is None:
            continue
        selected[victim_index] = proposal
        promotion_by_key[proposal.child_key] = (
            len(promotion_by_key) + 1,
            victim_key,
        )

    final_rank_by_key = {
        value.child_key: rank for rank, value in enumerate(selected, 1)
    }
    reranked_candidates = tuple(
        RerankedCandidate(
            child_id=value.child_id,
            child_key=value.child_key,
            fusion_rank=value.fusion_rank,
            rerank_score=score_by_key[value.child_key],
            input_hash=canonical_hash(
                value.stable_input_payload(query=normalized_query)
            ),
            protected_anchor=value.child_key in protected,
            selected=value.child_key in final_rank_by_key,
            final_rank=final_rank_by_key.get(value.child_key),
            promotion_sequence=(
                promotion_by_key[value.child_key][0]
                if value.child_key in promotion_by_key
                else None
            ),
            replaced_child_key=(
                promotion_by_key[value.child_key][1]
                if value.child_key in promotion_by_key
                else None
            ),
        )
        for value in values
    )
    pending = LightweightRerankResult(
        candidates=reranked_candidates,
        selected_child_ids=tuple(value.child_id for value in selected),
        fusion_result_hash=str(fusion_result_hash),
        query_plan_hash=str(query_plan_hash),
        model_descriptor=provider_result.descriptor,
        top_k=bounded_top_k,
        baseline_child_keys=tuple(value.child_key for value in baseline),
        final_child_keys=tuple(value.child_key for value in selected),
        protected_child_keys=tuple(
            value.child_key for value in baseline if value.child_key in protected
        ),
        promotion_count=len(promotion_by_key),
        result_hash="",
    )
    return LightweightRerankResult(
        candidates=pending.candidates,
        selected_child_ids=pending.selected_child_ids,
        fusion_result_hash=pending.fusion_result_hash,
        query_plan_hash=pending.query_plan_hash,
        model_descriptor=pending.model_descriptor,
        top_k=pending.top_k,
        baseline_child_keys=pending.baseline_child_keys,
        final_child_keys=pending.final_child_keys,
        protected_child_keys=pending.protected_child_keys,
        promotion_count=pending.promotion_count,
        result_hash=canonical_hash(pending.stable_payload()),
    )


__all__ = [
    "DISABLED_RERANK_PROFILE_VERSION",
    "LIGHTWEIGHT_RERANK_CONTRACT_VERSION",
    "RQ2C_CANDIDATE_WINDOW",
    "RQ2C_MAX_PROMOTIONS",
    "RQ2C_MIN_PROMOTION_MARGIN",
    "RQ2C_MIN_SCORE",
    "RQ2C_MODEL_ID",
    "RQ2C_MODEL_REVISION",
    "RQ2C_PROVIDER_ID",
    "RQ2C_RERANK_PROFILE_VERSION",
    "BidLightweightRerankerError",
    "BidLightweightRerankerInvalid",
    "BidLightweightRerankerUnavailable",
    "BidRerankerProvider",
    "LightweightRerankResult",
    "LocalBceCrossEncoderReranker",
    "RerankCandidateInput",
    "RerankProviderResult",
    "RerankProviderScore",
    "RerankerModelDescriptor",
    "configured_bid_reranker_provider",
    "rerank_frozen_candidates",
    "rerank_profile_payload",
    "validate_reranker_descriptor",
]
