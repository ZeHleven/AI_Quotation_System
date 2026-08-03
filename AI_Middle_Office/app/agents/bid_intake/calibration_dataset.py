from __future__ import annotations

from typing import Literal

from pydantic import Field

from .calibration import PolicyCalibrationCase
from .contracts import PolicyDecision, StrictModel


MIN_TOTAL_CASES = 30
MIN_DEVELOPMENT_CASES = 20
MIN_HOLDOUT_CASES = 10
MIN_NO_QUOTE_CASES = 5
MIN_HARD_STOP_CASES = 3
MIN_DEVELOPMENT_NO_QUOTE_CASES = 3
MIN_DEVELOPMENT_PURSUIT_CASES = 3


class CalibrationDatasetLabelRef(StrictModel):
    label_uuid: str
    label_version: int = Field(ge=1)
    assessment_uuid: str
    project_uuid: str
    reviewed_by: int = Field(ge=1)
    review_uuid: str


class PolicyCalibrationDatasetSnapshot(StrictModel):
    schema_version: Literal["bid_policy_calibration_dataset_v1"] = (
        "bid_policy_calibration_dataset_v1"
    )
    dataset_uuid: str
    dataset_version: str
    cases: list[PolicyCalibrationCase]
    label_refs: list[CalibrationDatasetLabelRef]


class CalibrationDatasetQualityCheck(StrictModel):
    code: str
    passed: bool
    current: int
    minimum: int | None = None
    message: str


class CalibrationDatasetQualityReport(StrictModel):
    schema_version: Literal["bid_policy_dataset_quality_v1"] = (
        "bid_policy_dataset_quality_v1"
    )
    ready_to_freeze: bool
    approved_case_count: int
    development_case_count: int
    holdout_case_count: int
    no_quote_case_count: int
    hard_stop_case_count: int
    development_no_quote_case_count: int
    development_pursuit_case_count: int
    pending_review_count: int = Field(ge=0)
    rejected_review_count: int = Field(ge=0)
    invalid_case_count: int = Field(ge=0)
    duplicate_case_id_count: int = Field(ge=0)
    project_split_leakage_count: int = Field(ge=0)
    expected_decision_counts: dict[str, int]
    checks: list[CalibrationDatasetQualityCheck]


def build_dataset_quality_report(
    *,
    cases: list[PolicyCalibrationCase],
    pending_review_count: int = 0,
    rejected_review_count: int = 0,
    invalid_case_count: int = 0,
) -> CalibrationDatasetQualityReport:
    development = [
        case for case in cases if case.dataset_split == "development"
    ]
    holdout = [
        case for case in cases if case.dataset_split == "holdout"
    ]
    decision_counts: dict[str, int] = {}
    for case in cases:
        decision = case.gold_label.expected_decision.value
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    no_quote_count = decision_counts.get(
        PolicyDecision.RECOMMEND_NO_QUOTE.value,
        0,
    )
    hard_stop_count = sum(
        case.gold_label.hard_stop_expected for case in cases
    )
    development_no_quote = sum(
        case.gold_label.expected_decision
        == PolicyDecision.RECOMMEND_NO_QUOTE
        for case in development
    )
    pursuit = {
        PolicyDecision.RECOMMEND_QUOTE,
        PolicyDecision.CONDITIONAL_QUOTE,
    }
    development_pursuit = sum(
        case.gold_label.expected_decision in pursuit
        for case in development
    )
    case_ids = [case.case_id for case in cases]
    duplicate_case_ids = len(case_ids) - len(set(case_ids))
    project_splits: dict[str, set[str]] = {}
    for case in cases:
        project_splits.setdefault(case.project_uuid, set()).add(
            case.dataset_split
        )
    project_leakage = sum(
        len(splits) > 1 for splits in project_splits.values()
    )
    checks = [
        _minimum_check(
            "MIN_TOTAL_CASES",
            len(cases),
            MIN_TOTAL_CASES,
            "复核通过的总样本不少于30个。",
        ),
        _minimum_check(
            "MIN_DEVELOPMENT_CASES",
            len(development),
            MIN_DEVELOPMENT_CASES,
            "Development样本不少于20个。",
        ),
        _minimum_check(
            "MIN_HOLDOUT_CASES",
            len(holdout),
            MIN_HOLDOUT_CASES,
            "Holdout样本不少于10个。",
        ),
        _minimum_check(
            "MIN_NO_QUOTE_CASES",
            no_quote_count,
            MIN_NO_QUOTE_CASES,
            "不报价金标不少于5个。",
        ),
        _minimum_check(
            "MIN_HARD_STOP_CASES",
            hard_stop_count,
            MIN_HARD_STOP_CASES,
            "硬红线金标不少于3个。",
        ),
        _minimum_check(
            "MIN_DEVELOPMENT_NO_QUOTE_CASES",
            development_no_quote,
            MIN_DEVELOPMENT_NO_QUOTE_CASES,
            "Development不报价金标不少于3个。",
        ),
        _minimum_check(
            "MIN_DEVELOPMENT_PURSUIT_CASES",
            development_pursuit,
            MIN_DEVELOPMENT_PURSUIT_CASES,
            "Development报价/有条件报价金标不少于3个。",
        ),
        CalibrationDatasetQualityCheck(
            code="NO_INVALID_CASES",
            passed=invalid_case_count == 0,
            current=invalid_case_count,
            minimum=0,
            message="复核通过的金标快照必须全部可解析。",
        ),
        CalibrationDatasetQualityCheck(
            code="NO_DUPLICATE_CASE_IDS",
            passed=duplicate_case_ids == 0,
            current=duplicate_case_ids,
            minimum=0,
            message="冻结数据集中不得出现重复case_id。",
        ),
        CalibrationDatasetQualityCheck(
            code="NO_PROJECT_SPLIT_LEAKAGE",
            passed=project_leakage == 0,
            current=project_leakage,
            minimum=0,
            message="同一项目不得同时进入Development与Holdout。",
        ),
    ]
    return CalibrationDatasetQualityReport(
        ready_to_freeze=all(check.passed for check in checks),
        approved_case_count=len(cases),
        development_case_count=len(development),
        holdout_case_count=len(holdout),
        no_quote_case_count=no_quote_count,
        hard_stop_case_count=hard_stop_count,
        development_no_quote_case_count=development_no_quote,
        development_pursuit_case_count=development_pursuit,
        pending_review_count=pending_review_count,
        rejected_review_count=rejected_review_count,
        invalid_case_count=invalid_case_count,
        duplicate_case_id_count=duplicate_case_ids,
        project_split_leakage_count=project_leakage,
        expected_decision_counts=decision_counts,
        checks=checks,
    )


def _minimum_check(
    code: str,
    current: int,
    minimum: int,
    message: str,
) -> CalibrationDatasetQualityCheck:
    return CalibrationDatasetQualityCheck(
        code=code,
        passed=current >= minimum,
        current=current,
        minimum=minimum,
        message=message,
    )
