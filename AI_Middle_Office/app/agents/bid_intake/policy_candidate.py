from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from .calibration import (
    PolicyCalibrationCase,
    PolicyCalibrationMetrics,
    PolicyCalibrationVersionResult,
    evaluate_policy_cases,
)
from .contracts import PolicyDecision, StrictModel
from .policy import (
    BidPolicyConfig,
    DecisionThresholds,
    YamlBidPolicy,
)


MIN_DEVELOPMENT_CASES = 20
MIN_DEVELOPMENT_NO_QUOTE_CASES = 3
MIN_DEVELOPMENT_PURSUIT_CASES = 3
THRESHOLD_OFFSETS = (-5.0, -2.5, 0.0, 2.5, 5.0)


class CandidateProposalError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.details = details or {}


class CandidateObjective(StrictModel):
    unsafe_quote_count: int = Field(ge=0)
    hard_stop_miss_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    exact_match_count: int = Field(ge=0)
    missed_pursuit_count: int = Field(ge=0)
    threshold_distance: float = Field(ge=0)


class PolicyCandidateProposal(StrictModel):
    schema_version: Literal["bid_policy_candidate_proposal_v1"] = (
        "bid_policy_candidate_proposal_v1"
    )
    search_method: Literal["constrained_threshold_grid_v1"] = (
        "constrained_threshold_grid_v1"
    )
    base_policy_version: str
    candidate_policy_version: str
    development_dataset_fingerprint: str
    development_case_count: int = Field(ge=0)
    development_expected_decision_counts: dict[str, int]
    changed_fields: dict[str, dict[str, float]]
    baseline_objective: CandidateObjective
    candidate_objective: CandidateObjective
    baseline_result: PolicyCalibrationVersionResult
    candidate_result: PolicyCalibrationVersionResult
    candidate_config: BidPolicyConfig


def propose_threshold_candidate(
    *,
    base_policy: YamlBidPolicy,
    cases: list[PolicyCalibrationCase],
    candidate_version: str,
) -> PolicyCandidateProposal:
    development_cases = [
        case for case in cases if case.dataset_split == "development"
    ]
    readiness = _development_readiness(development_cases)
    if readiness["case_count"] < MIN_DEVELOPMENT_CASES:
        raise CandidateProposalError(
            "CALIBRATION_DEVELOPMENT_SAMPLE_INSUFFICIENT",
            details=readiness,
        )
    if (
        readiness["no_quote_count"]
        < MIN_DEVELOPMENT_NO_QUOTE_CASES
        or readiness["pursuit_count"]
        < MIN_DEVELOPMENT_PURSUIT_CASES
    ):
        raise CandidateProposalError(
            "CALIBRATION_DEVELOPMENT_CLASS_COVERAGE_INSUFFICIENT",
            details=readiness,
        )

    baseline = evaluate_policy_cases(
        policy=base_policy,
        cases=development_cases,
    )
    if baseline.development.error_count:
        raise CandidateProposalError(
            "CALIBRATION_DEVELOPMENT_EVALUATION_ERRORS",
            details={
                **readiness,
                "error_count": baseline.development.error_count,
            },
        )
    base_thresholds = BidPolicyConfig.model_validate(
        base_policy.config_snapshot
    ).decision_thresholds
    baseline_objective = _objective(
        result=baseline,
        base_thresholds=base_thresholds,
        candidate_thresholds=base_thresholds,
    )
    best: tuple[
        tuple[int, int, int, int, int, float],
        BidPolicyConfig,
        PolicyCalibrationVersionResult,
        CandidateObjective,
    ] | None = None
    for quote_min in _threshold_values(
        base_thresholds.recommend_quote_min
    ):
        for conditional_min in _threshold_values(
            base_thresholds.conditional_quote_min
        ):
            if conditional_min > quote_min:
                continue
            if quote_min - conditional_min < 5:
                continue
            candidate_config = BidPolicyConfig.model_validate(
                {
                    **base_policy.config_snapshot,
                    "policy_version": candidate_version,
                    "status": "candidate",
                    "decision_thresholds": {
                        **base_thresholds.model_dump(mode="json"),
                        "recommend_quote_min": quote_min,
                        "conditional_quote_min": conditional_min,
                    },
                }
            )
            candidate_policy = YamlBidPolicy(candidate_config)
            result = evaluate_policy_cases(
                policy=candidate_policy,
                cases=development_cases,
            )
            objective = _objective(
                result=result,
                base_thresholds=base_thresholds,
                candidate_thresholds=(
                    candidate_config.decision_thresholds
                ),
            )
            rank = _rank(objective)
            if best is None or rank < best[0]:
                best = (rank, candidate_config, result, objective)

    if best is None:
        raise CandidateProposalError("NO_VALID_CANDIDATE_SEARCH_SPACE")
    _, candidate_config, candidate_result, candidate_objective = best
    if _material_rank(candidate_objective) >= _material_rank(
        baseline_objective
    ):
        raise CandidateProposalError(
            "NO_BETTER_CANDIDATE_FOUND",
            details={
                **readiness,
                "baseline_objective": baseline_objective.model_dump(
                    mode="json"
                ),
            },
        )

    candidate_thresholds = candidate_config.decision_thresholds
    return PolicyCandidateProposal(
        base_policy_version=base_policy.version,
        candidate_policy_version=candidate_version,
        development_dataset_fingerprint=calibration_case_fingerprint(
            development_cases
        ),
        development_case_count=len(development_cases),
        development_expected_decision_counts=(
            baseline.development.expected_decision_counts
        ),
        changed_fields={
            "decision_thresholds.recommend_quote_min": {
                "before": (
                    base_thresholds.recommend_quote_min
                ),
                "after": (
                    candidate_thresholds.recommend_quote_min
                ),
            },
            "decision_thresholds.conditional_quote_min": {
                "before": (
                    base_thresholds.conditional_quote_min
                ),
                "after": (
                    candidate_thresholds.conditional_quote_min
                ),
            },
        },
        baseline_objective=baseline_objective,
        candidate_objective=candidate_objective,
        baseline_result=_without_case_results(baseline),
        candidate_result=_without_case_results(candidate_result),
        candidate_config=candidate_config,
    )


def calibration_case_fingerprint(
    cases: list[PolicyCalibrationCase],
) -> str:
    payload = [
        case.model_dump(mode="json")
        for case in sorted(cases, key=lambda item: item.case_id)
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _development_readiness(
    cases: list[PolicyCalibrationCase],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        decision = case.gold_label.expected_decision.value
        counts[decision] = counts.get(decision, 0) + 1
    pursuit_count = sum(
        counts.get(decision.value, 0)
        for decision in (
            PolicyDecision.RECOMMEND_QUOTE,
            PolicyDecision.CONDITIONAL_QUOTE,
        )
    )
    return {
        "case_count": len(cases),
        "minimum_case_count": MIN_DEVELOPMENT_CASES,
        "no_quote_count": counts.get(
            PolicyDecision.RECOMMEND_NO_QUOTE.value,
            0,
        ),
        "minimum_no_quote_count": (
            MIN_DEVELOPMENT_NO_QUOTE_CASES
        ),
        "pursuit_count": pursuit_count,
        "minimum_pursuit_count": MIN_DEVELOPMENT_PURSUIT_CASES,
    }


def _threshold_values(value: float) -> list[float]:
    return sorted(
        {
            round(max(0.0, min(100.0, value + offset)), 2)
            for offset in THRESHOLD_OFFSETS
        }
    )


def _objective(
    *,
    result: PolicyCalibrationVersionResult,
    base_thresholds: DecisionThresholds,
    candidate_thresholds: DecisionThresholds,
) -> CandidateObjective:
    metrics = result.development
    pursuit = {
        PolicyDecision.RECOMMEND_QUOTE,
        PolicyDecision.CONDITIONAL_QUOTE,
    }
    missed_pursuit_count = sum(
        1
        for item in result.case_results
        if (
            item.dataset_split == "development"
            and item.expected_decision in pursuit
            and item.predicted_decision not in pursuit
        )
    )
    return CandidateObjective(
        unsafe_quote_count=metrics.unsafe_quote_count,
        hard_stop_miss_count=(
            metrics.hard_stop_case_count
            - metrics.hard_stop_detected_count
        ),
        error_count=metrics.error_count,
        exact_match_count=metrics.exact_match_count,
        missed_pursuit_count=missed_pursuit_count,
        threshold_distance=round(
            abs(
                candidate_thresholds.recommend_quote_min
                - base_thresholds.recommend_quote_min
            )
            + abs(
                candidate_thresholds.conditional_quote_min
                - base_thresholds.conditional_quote_min
            ),
            2,
        ),
    )


def _rank(
    objective: CandidateObjective,
) -> tuple[int, int, int, int, int, float]:
    return (
        objective.unsafe_quote_count,
        objective.hard_stop_miss_count,
        objective.error_count,
        -objective.exact_match_count,
        objective.missed_pursuit_count,
        objective.threshold_distance,
    )


def _material_rank(
    objective: CandidateObjective,
) -> tuple[int, int, int, int, int]:
    return _rank(objective)[:-1]


def _without_case_results(
    result: PolicyCalibrationVersionResult,
) -> PolicyCalibrationVersionResult:
    return result.model_copy(update={"case_results": []})
