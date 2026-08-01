from __future__ import annotations

from app.agents.bid_intake.calibration import (
    CalibrationGoldLabel,
    CalibrationLabelBasis,
    PolicyCalibrationCase,
    compare_policy_versions,
    evaluate_policy_cases,
)
from app.agents.bid_intake.contracts import (
    AssessmentDraft,
    PolicyDecision,
    PolicyFactorRating,
    PolicyFactorSource,
)
from app.agents.bid_intake.fake_adapters import (
    build_demo_draft,
    build_demo_evidence,
    build_demo_manifest,
)
from app.agents.bid_intake.policy import (
    DecisionThresholds,
    YamlBidPolicy,
)


def _draft(
    ratings: dict[str, PolicyFactorRating] | None = None,
) -> AssessmentDraft:
    payload = build_demo_draft(
        build_demo_evidence()
    ).model_dump(mode="json")
    for factor in payload["policy_factors"]:
        rating = (ratings or {}).get(factor["factor_id"])
        if rating is None:
            continue
        factor["rating"] = rating.value
        if rating == PolicyFactorRating.UNKNOWN:
            factor["source_type"] = PolicyFactorSource.UNKNOWN.value
            factor["source_note"] = None
            factor["confidence"] = 0
            factor["evidence_refs"] = []
    return AssessmentDraft.model_validate(payload)


def _case(
    index: int,
    *,
    expected: PolicyDecision,
    ratings: dict[str, PolicyFactorRating] | None = None,
    split: str = "development",
    hard_stop: bool = False,
) -> PolicyCalibrationCase:
    return PolicyCalibrationCase(
        case_id=f"case-{index}",
        assessment_uuid=f"00000000-0000-0000-0000-{index:012d}",
        project_uuid=f"10000000-0000-0000-0000-{index:012d}",
        source="synthetic",
        dataset_split=split,
        manifest=build_demo_manifest(),
        assessment=_draft(ratings),
        gold_label=CalibrationGoldLabel(
            expected_decision=expected,
            hard_stop_expected=hard_stop,
            label_basis=(
                CalibrationLabelBasis.PRE_BID_EXPERT_REVIEW
            ),
            rationale="确定性回归金标。",
        ),
    )


def test_calibration_metrics_cover_quote_no_quote_and_supplement():
    cases = [
        _case(1, expected=PolicyDecision.RECOMMEND_QUOTE),
        _case(
            2,
            expected=PolicyDecision.RECOMMEND_NO_QUOTE,
            ratings={
                "compliance_risk": PolicyFactorRating.CRITICAL,
            },
            hard_stop=True,
        ),
        _case(
            3,
            expected=PolicyDecision.NEED_SUPPLEMENT,
            ratings={
                "client_credit": PolicyFactorRating.UNKNOWN,
            },
        ),
        _case(
            4,
            expected=PolicyDecision.CONDITIONAL_QUOTE,
            ratings={
                "win_probability": PolicyFactorRating.ADVERSE,
            },
        ),
    ]

    result = evaluate_policy_cases(
        policy=YamlBidPolicy.from_active(),
        cases=cases,
    )

    assert result.overall.case_count == 4
    assert result.overall.exact_accuracy == 1
    assert result.overall.unsafe_quote_count == 0
    assert result.overall.hard_stop_recall == 1
    assert result.overall.confusion_matrix[
        PolicyDecision.NEED_SUPPLEMENT.value
    ] == {PolicyDecision.NEED_SUPPLEMENT.value: 1}


def test_release_gate_passes_only_with_sufficient_blind_coverage():
    cases: list[PolicyCalibrationCase] = []
    for index in range(1, 21):
        cases.append(
            _case(
                index,
                expected=PolicyDecision.RECOMMEND_QUOTE,
            )
        )
    for index in range(21, 26):
        cases.append(
            _case(
                index,
                expected=PolicyDecision.RECOMMEND_NO_QUOTE,
                ratings={
                    "compliance_risk": PolicyFactorRating.CRITICAL,
                },
                split="holdout",
                hard_stop=True,
            )
        )
    for index in range(26, 31):
        cases.append(
            _case(
                index,
                expected=PolicyDecision.RECOMMEND_QUOTE,
                split="holdout",
            )
        )

    policy = YamlBidPolicy.from_active()
    report = compare_policy_versions(
        baseline=policy,
        candidate=policy,
        cases=cases,
    )

    assert report.release_gate.passed is True
    assert report.candidate.holdout.exact_accuracy == 1
    assert report.candidate.holdout.hard_stop_recall == 1


def test_release_gate_detects_unsafe_candidate_regression():
    baseline = YamlBidPolicy.from_active()
    candidate_config = baseline._config.model_copy(  # noqa: SLF001
        update={
            "policy_version": "unsafe_candidate",
            "decision_thresholds": DecisionThresholds(
                recommend_quote_min=50,
                conditional_quote_min=40,
                min_coverage_for_quote=80,
                min_coverage_for_conditional=60,
            ),
        }
    )
    candidate = YamlBidPolicy(candidate_config)
    ratings = {
        "scope_cost_clarity": PolicyFactorRating.ADVERSE,
        "margin_potential": PolicyFactorRating.ADVERSE,
        "client_credit": PolicyFactorRating.ADVERSE,
        "delivery_capacity": PolicyFactorRating.ADVERSE,
    }
    cases = [
        _case(
            index,
            expected=PolicyDecision.RECOMMEND_NO_QUOTE,
            ratings=ratings,
            split="holdout" if index > 20 else "development",
        )
        for index in range(1, 31)
    ]

    report = compare_policy_versions(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
    )
    checks = {
        item.code: item.passed
        for item in report.release_gate.checks
    }

    assert report.candidate.holdout.unsafe_quote_count == 10
    assert checks["NO_UNSAFE_QUOTE_REGRESSION"] is False
    assert report.release_gate.passed is False
