from __future__ import annotations

import pytest

from app.services.bid_lightweight_reranker import (
    BidLightweightRerankerUnavailable,
)
from app.services.bid_semantic_vector_provider import (
    BidSemanticProviderUnavailable,
)
from scripts.evaluate_bid_pure_agent_v6051 import (
    SCHEMA_VERSION,
    _rerank_candidates,
    guarded_optional_result,
    load_dataset,
)


def test_v6051_dataset_is_versioned_synthetic_and_has_both_model_domains() -> None:
    dataset = load_dataset()

    assert dataset["schema_version"] == SCHEMA_VERSION
    assert dataset["dataset_kind"] == "synthetic_only"
    assert len(dataset["embedding_cases"]) == 4
    assert len(dataset["reranker_cases"]) == 3
    assert {case["domain"] for case in dataset["embedding_cases"]} == {
        "bid_document",
        "enterprise_knowledge",
    }
    assert any(
        case.get("expect_lexical_miss") is True
        for case in dataset["embedding_cases"]
    )
    assert any(
        case.get("expect_improvement") is True
        for case in dataset["reranker_cases"]
    )
    assert any(
        case.get("expect_improvement") is False
        for case in dataset["reranker_cases"]
    )


@pytest.mark.parametrize(
    "error",
    (
        BidSemanticProviderUnavailable("embedding unavailable"),
        BidLightweightRerankerUnavailable("reranker unavailable"),
    ),
)
def test_v6051_optional_enhancement_discloses_degradation_and_keeps_baseline(
    error: Exception,
) -> None:
    baseline = ("baseline:a", "baseline:b")
    result = guarded_optional_result(
        lambda: (_ for _ in ()).throw(error),
        frozen_baseline=baseline,
        unavailable_errors=(
            BidSemanticProviderUnavailable,
            BidLightweightRerankerUnavailable,
        ),
    )

    assert result["status"] == "degraded"
    assert result["selected_keys"] == baseline
    assert result["baseline_preserved"] is True
    assert result["error_code"]


def test_v6051_invalid_contract_is_not_silently_downgraded() -> None:
    with pytest.raises(ValueError, match="invalid contract"):
        guarded_optional_result(
            lambda: (_ for _ in ()).throw(ValueError("invalid contract")),
            frozen_baseline=("baseline:a",),
            unavailable_errors=(
                BidSemanticProviderUnavailable,
                BidLightweightRerankerUnavailable,
            ),
        )


def test_v6051_reranker_candidate_pool_is_stable_bounded_and_non_citable() -> None:
    case = load_dataset()["reranker_cases"][0]
    first, first_labels, first_keys = _rerank_candidates(case)
    replay, replay_labels, replay_keys = _rerank_candidates(case)

    assert first == replay
    assert first_labels == replay_labels
    assert first_keys == replay_keys
    assert tuple(candidate.fusion_rank for candidate in first) == tuple(
        range(1, len(first) + 1)
    )
    assert len(first) <= 20
    assert len({candidate.child_key for candidate in first}) == len(first)
    assert all(candidate.child_key.startswith("chunk:") for candidate in first)
    assert all(candidate.parent_key.startswith("section:") for candidate in first)
    assert all(
        candidate.lexical_rank is not None or candidate.semantic_rank is not None
        for candidate in first
    )


def test_v6051_thresholds_require_value_no_regression_and_bounded_latency() -> None:
    thresholds = load_dataset()["thresholds"]

    assert thresholds["embedding_recall_at_3_min"] == 1.0
    assert thresholds["embedding_mrr_min"] >= 0.875
    assert thresholds["embedding_complement_case_count_min"] >= 1
    assert thresholds["reranker_improvement_case_count_min"] >= 1
    assert thresholds["reranker_regression_count_max"] == 0
    assert 0 < thresholds["embedding_cold_build_seconds_max"] <= 60
    assert 0 < thresholds["embedding_warm_query_seconds_max"] <= 10
    assert 0 < thresholds["reranker_warm_case_seconds_max"] <= 15
