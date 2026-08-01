from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .contracts import (
    AssessmentDraft,
    DocumentManifest,
    PolicyDecision,
    StrictModel,
)
from .policy import YamlBidPolicy


class CalibrationLabelBasis(str, Enum):
    PRE_BID_EXPERT_REVIEW = "pre_bid_expert_review"
    ACTUAL_PROJECT_OUTCOME = "actual_project_outcome"
    COMBINED = "combined"


class CalibrationActualOutcome(StrictModel):
    bid_submitted: bool | None = None
    won_bid: bool | None = None
    realized_margin_rate: float | None = Field(
        default=None,
        ge=-100,
        le=100,
    )
    payment_overdue: bool | None = None
    major_delivery_issue: bool | None = None
    note: str | None = Field(default=None, max_length=2000)


class CalibrationGoldLabel(StrictModel):
    expected_decision: PolicyDecision
    hard_stop_expected: bool = False
    label_basis: CalibrationLabelBasis
    rationale: str = Field(min_length=1, max_length=4000)
    actual_outcome: CalibrationActualOutcome | None = None

    @model_validator(mode="after")
    def validate_gold_label(self) -> "CalibrationGoldLabel":
        if self.expected_decision == PolicyDecision.MANUAL_REVIEW:
            raise ValueError("manual_review cannot be a calibration gold label")
        if (
            self.hard_stop_expected
            and self.expected_decision
            != PolicyDecision.RECOMMEND_NO_QUOTE
        ):
            raise ValueError(
                "hard-stop gold label must recommend no quote"
            )
        if self.label_basis in {
            CalibrationLabelBasis.ACTUAL_PROJECT_OUTCOME,
            CalibrationLabelBasis.COMBINED,
        } and self.actual_outcome is None:
            raise ValueError(
                "actual outcome basis requires actual_outcome"
            )
        return self


class PolicyCalibrationCase(StrictModel):
    schema_version: Literal["bid_policy_calibration_case_v1"] = (
        "bid_policy_calibration_case_v1"
    )
    case_id: str = Field(min_length=1, max_length=160)
    assessment_uuid: str = Field(min_length=1, max_length=36)
    project_uuid: str = Field(min_length=1, max_length=36)
    source: Literal["historical", "synthetic"]
    dataset_split: Literal["development", "holdout"]
    manifest: DocumentManifest
    assessment: AssessmentDraft
    gold_label: CalibrationGoldLabel


class PolicyCalibrationCaseResult(StrictModel):
    case_id: str
    dataset_split: Literal["development", "holdout"]
    expected_decision: PolicyDecision
    predicted_decision: PolicyDecision | None = None
    exact_match: bool = False
    unsafe_quote: bool = False
    hard_stop_expected: bool = False
    hard_stop_detected: bool = False
    score: float | None = None
    coverage: float | None = None
    error_code: str | None = None


class PolicyCalibrationMetrics(StrictModel):
    case_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    exact_match_count: int = Field(ge=0)
    exact_accuracy: float | None = Field(default=None, ge=0, le=1)
    unsafe_quote_count: int = Field(ge=0)
    hard_stop_case_count: int = Field(ge=0)
    hard_stop_detected_count: int = Field(ge=0)
    hard_stop_recall: float | None = Field(default=None, ge=0, le=1)
    expected_decision_counts: dict[str, int] = Field(default_factory=dict)
    predicted_decision_counts: dict[str, int] = Field(default_factory=dict)
    confusion_matrix: dict[str, dict[str, int]] = Field(
        default_factory=dict
    )


class PolicyCalibrationVersionResult(StrictModel):
    policy_version: str
    overall: PolicyCalibrationMetrics
    development: PolicyCalibrationMetrics
    holdout: PolicyCalibrationMetrics
    case_results: list[PolicyCalibrationCaseResult] = Field(
        default_factory=list
    )


class CalibrationGateCheck(StrictModel):
    code: str
    passed: bool
    message: str


class PolicyCalibrationReleaseGate(StrictModel):
    passed: bool
    checks: list[CalibrationGateCheck]


class PolicyCalibrationComparison(StrictModel):
    schema_version: Literal["bid_policy_calibration_report_v1"] = (
        "bid_policy_calibration_report_v1"
    )
    baseline_policy_version: str
    candidate_policy_version: str
    dataset_case_count: int
    baseline: PolicyCalibrationVersionResult
    candidate: PolicyCalibrationVersionResult
    release_gate: PolicyCalibrationReleaseGate
    warnings: list[str] = Field(default_factory=list)


def evaluate_policy_cases(
    *,
    policy: YamlBidPolicy,
    cases: list[PolicyCalibrationCase],
) -> PolicyCalibrationVersionResult:
    results: list[PolicyCalibrationCaseResult] = []
    for case in cases:
        try:
            evaluation = policy.evaluate(
                draft=case.assessment,
                manifest=case.manifest,
            )
            predicted = evaluation.decision
            expected = case.gold_label.expected_decision
            hard_stop_detected = bool(evaluation.hard_rule_hits)
            results.append(
                PolicyCalibrationCaseResult(
                    case_id=case.case_id,
                    dataset_split=case.dataset_split,
                    expected_decision=expected,
                    predicted_decision=predicted,
                    exact_match=predicted == expected,
                    unsafe_quote=_is_unsafe_quote(
                        predicted=predicted,
                        expected=expected,
                        hard_stop_expected=(
                            case.gold_label.hard_stop_expected
                        ),
                    ),
                    hard_stop_expected=(
                        case.gold_label.hard_stop_expected
                    ),
                    hard_stop_detected=hard_stop_detected,
                    score=evaluation.score,
                    coverage=evaluation.coverage,
                )
            )
        except Exception as exc:
            results.append(
                PolicyCalibrationCaseResult(
                    case_id=case.case_id,
                    dataset_split=case.dataset_split,
                    expected_decision=(
                        case.gold_label.expected_decision
                    ),
                    hard_stop_expected=(
                        case.gold_label.hard_stop_expected
                    ),
                    error_code=type(exc).__name__,
                )
            )
    return PolicyCalibrationVersionResult(
        policy_version=policy.version,
        overall=_metrics(results),
        development=_metrics(
            [
                item
                for item in results
                if item.dataset_split == "development"
            ]
        ),
        holdout=_metrics(
            [
                item
                for item in results
                if item.dataset_split == "holdout"
            ]
        ),
        case_results=results,
    )


def compare_policy_versions(
    *,
    baseline: YamlBidPolicy,
    candidate: YamlBidPolicy,
    cases: list[PolicyCalibrationCase],
) -> PolicyCalibrationComparison:
    baseline_result = evaluate_policy_cases(
        policy=baseline,
        cases=cases,
    )
    candidate_result = evaluate_policy_cases(
        policy=candidate,
        cases=cases,
    )
    gate_metrics_baseline = _gate_metrics(baseline_result)
    gate_metrics_candidate = _gate_metrics(candidate_result)
    composition_metrics = candidate_result.overall
    checks = [
        CalibrationGateCheck(
            code="MIN_TOTAL_CASES",
            passed=len(cases) >= 30,
            message=f"至少需要30个金标样本，当前{len(cases)}个。",
        ),
        CalibrationGateCheck(
            code="MIN_HOLDOUT_CASES",
            passed=candidate_result.holdout.case_count >= 10,
            message=(
                "至少需要10个未参与调参的holdout样本，"
                f"当前{candidate_result.holdout.case_count}个。"
            ),
        ),
        CalibrationGateCheck(
            code="MIN_NO_QUOTE_CASES",
            passed=(
                composition_metrics.expected_decision_counts.get(
                    PolicyDecision.RECOMMEND_NO_QUOTE.value,
                    0,
                )
                >= 5
            ),
            message="评测集至少需要5个“不报价”金标样本。",
        ),
        CalibrationGateCheck(
            code="MIN_HARD_STOP_CASES",
            passed=composition_metrics.hard_stop_case_count >= 3,
            message="评测集至少需要3个硬红线金标样本。",
        ),
        CalibrationGateCheck(
            code="NO_EVALUATION_ERRORS",
            passed=gate_metrics_candidate.error_count == 0,
            message="候选政策回放不得出现解析或执行错误。",
        ),
        CalibrationGateCheck(
            code="NO_UNSAFE_QUOTE_REGRESSION",
            passed=(
                gate_metrics_candidate.unsafe_quote_count
                <= gate_metrics_baseline.unsafe_quote_count
            ),
            message="候选政策不得增加应拒绝项目被建议报价的数量。",
        ),
        CalibrationGateCheck(
            code="HARD_STOP_RECALL_100",
            passed=(
                gate_metrics_candidate.hard_stop_case_count > 0
                and gate_metrics_candidate.hard_stop_recall == 1
            ),
            message="硬红线召回率必须达到100%。",
        ),
        CalibrationGateCheck(
            code="ACCURACY_NOT_REGRESSED",
            passed=(
                _accuracy(gate_metrics_candidate)
                >= _accuracy(gate_metrics_baseline)
            ),
            message="候选政策的金标一致率不得低于当前active版本。",
        ),
        CalibrationGateCheck(
            code="MIN_EXACT_ACCURACY",
            passed=_accuracy(gate_metrics_candidate) >= 0.8,
            message="候选政策金标一致率至少达到80%。",
        ),
    ]
    warnings: list[str] = []
    if not cases:
        warnings.append("尚无历史金标样本，不能校准或发布候选政策。")
    if baseline.version == candidate.version:
        warnings.append("当前为active版本自检，尚未比较新的候选政策。")
    return PolicyCalibrationComparison(
        baseline_policy_version=baseline.version,
        candidate_policy_version=candidate.version,
        dataset_case_count=len(cases),
        baseline=baseline_result,
        candidate=candidate_result,
        release_gate=PolicyCalibrationReleaseGate(
            passed=all(item.passed for item in checks),
            checks=checks,
        ),
        warnings=warnings,
    )


def _is_unsafe_quote(
    *,
    predicted: PolicyDecision,
    expected: PolicyDecision,
    hard_stop_expected: bool,
) -> bool:
    pursuit = {
        PolicyDecision.RECOMMEND_QUOTE,
        PolicyDecision.CONDITIONAL_QUOTE,
    }
    return predicted in pursuit and (
        expected == PolicyDecision.RECOMMEND_NO_QUOTE
        or hard_stop_expected
    )


def _metrics(
    results: list[PolicyCalibrationCaseResult],
) -> PolicyCalibrationMetrics:
    evaluated = [
        item for item in results if item.error_code is None
    ]
    exact_count = sum(item.exact_match for item in evaluated)
    hard_cases = [
        item for item in evaluated if item.hard_stop_expected
    ]
    hard_detected = sum(
        item.hard_stop_detected for item in hard_cases
    )
    expected_counts: dict[str, int] = {}
    predicted_counts: dict[str, int] = {}
    confusion: dict[str, dict[str, int]] = {}
    for item in evaluated:
        expected = item.expected_decision.value
        predicted = (
            item.predicted_decision.value
            if item.predicted_decision is not None
            else "error"
        )
        expected_counts[expected] = expected_counts.get(expected, 0) + 1
        predicted_counts[predicted] = predicted_counts.get(predicted, 0) + 1
        row = confusion.setdefault(expected, {})
        row[predicted] = row.get(predicted, 0) + 1
    return PolicyCalibrationMetrics(
        case_count=len(results),
        evaluated_count=len(evaluated),
        error_count=len(results) - len(evaluated),
        exact_match_count=exact_count,
        exact_accuracy=(
            round(exact_count / len(evaluated), 4)
            if evaluated
            else None
        ),
        unsafe_quote_count=sum(
            item.unsafe_quote for item in evaluated
        ),
        hard_stop_case_count=len(hard_cases),
        hard_stop_detected_count=hard_detected,
        hard_stop_recall=(
            round(hard_detected / len(hard_cases), 4)
            if hard_cases
            else None
        ),
        expected_decision_counts=expected_counts,
        predicted_decision_counts=predicted_counts,
        confusion_matrix=confusion,
    )


def _gate_metrics(
    result: PolicyCalibrationVersionResult,
) -> PolicyCalibrationMetrics:
    if result.holdout.case_count:
        return result.holdout
    return result.overall


def _accuracy(metrics: PolicyCalibrationMetrics) -> float:
    return float(metrics.exact_accuracy or 0)
