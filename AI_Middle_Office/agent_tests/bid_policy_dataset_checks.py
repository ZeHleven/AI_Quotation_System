from __future__ import annotations

from app.agents.bid_intake.calibration_dataset import (
    build_dataset_quality_report,
)
from app.agents.bid_intake.contracts import (
    PolicyDecision,
    PolicyFactorRating,
)

from .bid_policy_calibration_checks import _case
from .bid_policy_candidate_checks import _proposal_cases


def _ready_cases():
    cases = _proposal_cases()
    cases.extend(
        _case(
            100 + index,
            expected=(
                PolicyDecision.RECOMMEND_NO_QUOTE
                if index <= 5
                else PolicyDecision.RECOMMEND_QUOTE
            ),
            ratings=(
                {
                    "compliance_risk": (
                        PolicyFactorRating.CRITICAL
                    )
                }
                if index <= 5
                else None
            ),
            split="holdout",
            hard_stop=index <= 5,
        )
        for index in range(1, 11)
    )
    return cases


def test_dataset_quality_requires_reviewed_sample_composition():
    report = build_dataset_quality_report(
        cases=_proposal_cases()[:10],
        pending_review_count=2,
        rejected_review_count=1,
    )

    assert report.ready_to_freeze is False
    checks = {item.code: item.passed for item in report.checks}
    assert checks["MIN_TOTAL_CASES"] is False
    assert checks["MIN_HOLDOUT_CASES"] is False
    assert report.pending_review_count == 2


def test_dataset_quality_accepts_complete_reviewed_dataset():
    report = build_dataset_quality_report(cases=_ready_cases())

    assert report.ready_to_freeze is True
    assert report.approved_case_count == 30
    assert report.development_case_count == 20
    assert report.holdout_case_count == 10
    assert report.no_quote_case_count == 11
    assert report.hard_stop_case_count == 8
    assert all(item.passed for item in report.checks)


def test_dataset_quality_detects_project_split_leakage():
    cases = _ready_cases()
    cases[-1] = cases[-1].model_copy(
        update={"project_uuid": cases[0].project_uuid}
    )

    report = build_dataset_quality_report(cases=cases)
    checks = {item.code: item.passed for item in report.checks}

    assert report.ready_to_freeze is False
    assert report.project_split_leakage_count == 1
    assert checks["NO_PROJECT_SPLIT_LEAKAGE"] is False
