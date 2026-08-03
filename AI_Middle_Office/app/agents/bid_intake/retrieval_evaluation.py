from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import FactCoverageState


RetrievalMode = Literal["exact", "semantic", "hybrid"]
DatasetSplit = Literal["development", "holdout", "challenge"]

MIN_PORTFOLIO_CASES = 30
MIN_PORTFOLIO_DEVELOPMENT_CASES = 20
MIN_PORTFOLIO_HOLDOUT_CASES = 10


class StrictEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldEvidence(StrictEvalModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    relevance: int = Field(default=3, ge=1, le=3)
    required_text_fragments: list[str] = Field(default_factory=list)


class RoutingExpectation(StrictEvalModel):
    query_count: int | None = Field(default=None, ge=1, le=10)
    mode_counts: dict[RetrievalMode, int] = Field(default_factory=dict)
    required_topics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mode_counts(self) -> "RoutingExpectation":
        if any(value < 0 for value in self.mode_counts.values()):
            raise ValueError("routing mode counts cannot be negative")
        if (
            self.query_count is not None
            and self.mode_counts
            and sum(self.mode_counts.values()) != self.query_count
        ):
            raise ValueError(
                "routing mode counts must add up to query_count"
            )
        return self


class RetrievalEvalCase(StrictEvalModel):
    schema_version: Literal["bid_intake_retrieval_eval_case_v1"] = (
        "bid_intake_retrieval_eval_case_v1"
    )
    eval_case_id: str = Field(min_length=1, max_length=160)
    case_id: str = Field(min_length=1, max_length=160)
    source: Literal["historical", "synthetic"]
    dataset_split: DatasetSplit
    question: str = Field(min_length=2, max_length=500)
    expected_routing: RoutingExpectation
    gold_evidence: list[GoldEvidence] = Field(default_factory=list)
    expected_no_result: bool = False
    gold_answer_points: list[str] = Field(default_factory=list)
    expected_missing_materials: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"]
    tags: list[str] = Field(default_factory=list)
    privacy: Literal[
        "public_synthetic",
        "private_anonymized",
        "private_restricted",
    ]
    annotation_status: Literal["draft", "reviewed", "approved"]
    annotated_by: str = Field(min_length=1, max_length=120)
    reviewed_by: str | None = Field(default=None, max_length=120)
    annotation_note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_gold_contract(self) -> "RetrievalEvalCase":
        if self.expected_no_result and self.gold_evidence:
            raise ValueError(
                "a no-result case cannot contain gold evidence"
            )
        if (
            not self.expected_no_result
            and not self.gold_evidence
            and self.annotation_status != "draft"
        ):
            raise ValueError(
                "reviewed or approved positive cases require gold evidence"
            )
        evidence_ids = [
            item.evidence_id for item in self.gold_evidence
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("gold evidence IDs must be unique")
        if self.annotation_status == "approved":
            if not self.reviewed_by:
                raise ValueError(
                    "approved cases require an independent reviewer"
                )
            if self.reviewed_by == self.annotated_by:
                raise ValueError(
                    "annotator and reviewer must be different"
                )
        return self


class RetrievalFactSlotCoverage(StrictEvalModel):
    slot_id: str = Field(min_length=8, max_length=64)
    slot_type: str = Field(min_length=1, max_length=80)
    status: Literal[
        "uncovered",
        "candidate_covered",
        "context_verified",
    ]
    candidate_evidence_ids: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)


class RetrievalFactCoverage(StrictEvalModel):
    schema_version: Literal[
        "bid_intake_retrieval_fact_coverage_v1"
    ] = "bid_intake_retrieval_fact_coverage_v1"
    sufficiency_status: Literal[
        "not_assessed",
        "candidate_sufficient",
        "insufficient",
    ]
    required_slot_count: int = Field(default=0, ge=0)
    covered_slot_count: int = Field(default=0, ge=0)
    verified_slot_count: int = Field(default=0, ge=0)
    coverage_rate: float | None = Field(default=None, ge=0, le=1)
    evaluated_search_count: int = Field(default=0, ge=0)
    observed_search_count: int = Field(default=0, ge=0)
    slots: list[RetrievalFactSlotCoverage] = Field(
        default_factory=list
    )
    notes: list[str] = Field(default_factory=list)


class RetrievalPrediction(StrictEvalModel):
    schema_version: Literal["bid_intake_retrieval_prediction_v1"] = (
        "bid_intake_retrieval_prediction_v1"
    )
    eval_case_id: str = Field(min_length=1, max_length=160)
    returned_evidence_ids: list[str] = Field(default_factory=list)
    returned_evidence_groups: list["RetrievalEvidenceGroup"] = Field(
        default_factory=list
    )
    candidate_pool_captured: bool = False
    candidate_pool_evidence_ids: list[str] = Field(default_factory=list)
    candidate_pool_search_call_count: int = Field(default=0, ge=0)
    returned_excerpts: dict[str, str] = Field(default_factory=dict)
    query_plan: dict[str, Any] = Field(default_factory=dict)
    fact_coverage: RetrievalFactCoverage | None = None
    result_status: str = "unknown"
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class RetrievalEvidenceGroup(StrictEvalModel):
    anchor_evidence_id: str = Field(min_length=1, max_length=160)
    context_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_group_members(self) -> "RetrievalEvidenceGroup":
        if self.anchor_evidence_id in self.context_evidence_ids:
            raise ValueError(
                "context evidence cannot duplicate the group anchor"
            )
        if len(self.context_evidence_ids) != len(
            set(self.context_evidence_ids)
        ):
            raise ValueError(
                "context evidence IDs must be unique within a group"
            )
        return self


def load_eval_cases(path: str | Path) -> list[RetrievalEvalCase]:
    return [
        RetrievalEvalCase.model_validate(item)
        for item in _load_jsonl(path)
    ]


def load_predictions(path: str | Path) -> list[RetrievalPrediction]:
    return [
        RetrievalPrediction.model_validate(item)
        for item in _load_jsonl(path)
    ]


def write_predictions(
    path: str | Path,
    predictions: Sequence[RetrievalPrediction],
) -> None:
    _write_jsonl(
        path,
        [
            item.model_dump(mode="json")
            for item in predictions
        ],
    )


def sanitize_query_plan_for_evaluation(
    query_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    source = query_plan if isinstance(query_plan, dict) else {}
    tasks = (
        source.get("query_tasks")
        if isinstance(source.get("query_tasks"), list)
        else []
    )
    supporting_tasks = (
        source.get("supporting_query_tasks")
        if isinstance(source.get("supporting_query_tasks"), list)
        else []
    )
    fact_slot_tasks = (
        source.get("fact_slot_query_tasks")
        if isinstance(source.get("fact_slot_query_tasks"), list)
        else []
    )
    controlled_retry_tasks = (
        source.get("controlled_retry_query_tasks")
        if isinstance(
            source.get("controlled_retry_query_tasks"),
            list,
        )
        else []
    )
    graph_summary = (
        source.get("selective_graph_expansion_summary")
        if isinstance(
            source.get("selective_graph_expansion_summary"),
            dict,
        )
        else {}
    )
    safe_tasks = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        safe_tasks.append(
            {
                key: item.get(key)
                for key in (
                    "query_id",
                    "query_kind",
                    "requested_mode",
                    "executed_mode",
                    "fallback_mode",
                    "fallback_triggered",
                    "result_count",
                    "reason_codes",
                    "confidence",
                )
                if key in item
            }
        )
    safe_supporting_tasks = []
    for item in supporting_tasks:
        if not isinstance(item, dict):
            continue
        safe_supporting_tasks.append(
            {
                key: item.get(key)
                for key in (
                    "query_id",
                    "query_kind",
                    "requested_mode",
                    "executed_mode",
                    "fallback_mode",
                    "fallback_triggered",
                    "result_count",
                    "reason_codes",
                    "confidence",
                )
                if key in item
            }
        )
    safe_fact_slot_tasks = []
    for item in fact_slot_tasks:
        if not isinstance(item, dict):
            continue
        safe_fact_slot_tasks.append(
            {
                key: item.get(key)
                for key in (
                    "query_id",
                    "query_kind",
                    "fact_slot_type",
                    "requested_mode",
                    "executed_mode",
                    "fallback_mode",
                    "fallback_triggered",
                    "result_count",
                    "reason_codes",
                    "confidence",
                )
                if key in item
            }
        )
    safe_controlled_retry_tasks = []
    for item in controlled_retry_tasks:
        if not isinstance(item, dict):
            continue
        safe_controlled_retry_tasks.append(
            {
                key: item.get(key)
                for key in (
                    "query_id",
                    "query_kind",
                    "coverage_need_index",
                    "coverage_need_type",
                    "requested_mode",
                    "executed_mode",
                    "fallback_mode",
                    "fallback_triggered",
                    "result_count",
                    "reason_codes",
                    "confidence",
                )
                if key in item
            }
        )
    safe_plan = {
        key: source.get(key)
        for key in (
            "schema_version",
            "strategy",
            "query_count",
            "topics",
            "per_query_candidate_top_k",
            "final_top_k",
            "supporting_query_count",
            "supporting_topics",
            "supporting_strategy",
            "fact_slot_query_count",
            "fact_slot_types",
            "fact_slot_strategy",
            "coverage_need_count",
            "coverage_need_types",
            "coverage_need_answer_shapes",
            "coverage_strategy",
            "coverage_selection_policy",
            "coverage_relation_shape_supported",
            "coverage_relation_shape_reason",
            "sufficiency_need_count",
            "sufficiency_need_types",
            "sufficiency_need_answer_shapes",
            "sufficiency_strategy",
            "sufficiency_relation_shape_supported",
            "sufficiency_relation_shape_reason",
            "evidence_sufficiency_summary",
            "routing_summary",
            "supporting_routing_summary",
            "fact_slot_routing_summary",
            "coverage_selection_summary",
            "adjacent_expansion_summary",
            "context_evidence_group_summary",
            "structured_context_summary",
            "structured_sibling_group_summary",
            "controlled_retry_routing_summary",
            "controlled_retry_summary",
            "selective_graph_expansion_summary",
            "total_search_query_count",
        )
        if key in source
    }
    safe_plan["query_tasks"] = safe_tasks
    safe_plan["supporting_query_tasks"] = safe_supporting_tasks
    safe_plan["fact_slot_query_tasks"] = safe_fact_slot_tasks
    safe_plan["controlled_retry_query_tasks"] = (
        safe_controlled_retry_tasks
    )
    if graph_summary:
        safe_graph_summary = {
            key: graph_summary.get(key)
            for key in (
                "enabled",
                "triggered",
                "document_roles",
                "relation_signals",
                "reason_codes",
                "graph_call_count",
                "max_hops",
                "max_seed_anchors",
                "max_expanded_evidence",
                "seed_count",
                "context_read_count",
                "reference_lookup_count",
                "unresolvable_reference_count",
                "resolved_reference_count",
                "missing_reference_target_count",
                "grouped_anchor_count",
                "expanded_evidence_count",
                "path_count",
                "edge_type_counts",
                "filtered_existing_count",
                "filtered_unverified_count",
                "error_count",
                "additional_llm_tokens",
                "skip_reason",
            )
            if key in graph_summary
        }
        paths = (
            graph_summary.get("paths")
            if isinstance(graph_summary.get("paths"), list)
            else []
        )
        safe_graph_summary["paths"] = [
            {
                key: item.get(key)
                for key in (
                    "hop_count",
                    "seed_evidence_id",
                    "edge_type",
                    "target_evidence_id",
                    "target_document_role",
                    "reference_type",
                )
                if key in item
            }
            for item in paths
            if isinstance(item, dict)
        ]
        safe_plan["selective_graph_expansion_summary"] = (
            safe_graph_summary
        )
    return safe_plan


def sanitize_fact_coverage_for_evaluation(
    state: FactCoverageState | None,
) -> RetrievalFactCoverage | None:
    if state is None:
        return None
    return RetrievalFactCoverage(
        sufficiency_status=state.sufficiency_status.value,
        required_slot_count=state.required_slot_count,
        covered_slot_count=state.covered_slot_count,
        verified_slot_count=state.verified_slot_count,
        coverage_rate=state.coverage_rate,
        evaluated_search_count=state.evaluated_search_count,
        observed_search_count=state.observed_search_count,
        slots=[
            RetrievalFactSlotCoverage(
                slot_id=item.slot_id,
                slot_type=item.slot_type,
                status=item.status.value,
                candidate_evidence_ids=list(
                    item.candidate_evidence_ids
                ),
                verified_evidence_ids=list(
                    item.verified_evidence_ids
                ),
            )
            for item in state.slots
        ],
        notes=list(state.notes),
    )


def dataset_fingerprint(
    cases: Sequence[RetrievalEvalCase],
) -> str:
    payload = [
        item.model_dump(mode="json")
        for item in sorted(
            cases,
            key=lambda value: value.eval_case_id,
        )
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_dataset_quality_report(
    cases: Sequence[RetrievalEvalCase],
) -> dict[str, Any]:
    case_ids = [item.eval_case_id for item in cases]
    duplicate_case_count = len(case_ids) - len(set(case_ids))
    project_splits: dict[str, set[str]] = {}
    for item in cases:
        project_splits.setdefault(item.case_id, set()).add(
            item.dataset_split
        )
    project_split_leakage_count = sum(
        len(splits) > 1 for splits in project_splits.values()
    )
    development_count = sum(
        item.dataset_split == "development" for item in cases
    )
    holdout_count = sum(
        item.dataset_split == "holdout" for item in cases
    )
    challenge_count = sum(
        item.dataset_split == "challenge" for item in cases
    )
    benchmark_case_count = development_count + holdout_count
    approved_count = sum(
        item.annotation_status == "approved" for item in cases
    )
    checks = [
        _quality_check(
            code="HAS_CASES",
            passed=bool(cases),
            current=len(cases),
            minimum=1,
            message="评测集至少包含一个可运行样本。",
        ),
        _quality_check(
            code="NO_DUPLICATE_EVAL_CASE_IDS",
            passed=duplicate_case_count == 0,
            current=duplicate_case_count,
            minimum=0,
            message="eval_case_id不得重复。",
        ),
        _quality_check(
            code="NO_PROJECT_SPLIT_LEAKAGE",
            passed=project_split_leakage_count == 0,
            current=project_split_leakage_count,
            minimum=0,
            message="同一项目不能同时进入Development与Holdout。",
        ),
        _quality_check(
            code="ALL_CASES_APPROVED",
            passed=approved_count == len(cases),
            current=approved_count,
            minimum=len(cases),
            message="正式对比前全部样本必须经过独立复核。",
        ),
        _quality_check(
            code="MIN_PORTFOLIO_CASES",
            passed=benchmark_case_count >= MIN_PORTFOLIO_CASES,
            current=benchmark_case_count,
            minimum=MIN_PORTFOLIO_CASES,
            message=(
                "用于稳定对比和项目表达的Development与Holdout"
                "样本合计不少于30个；Challenge不计入该门槛。"
            ),
        ),
        _quality_check(
            code="MIN_PORTFOLIO_DEVELOPMENT_CASES",
            passed=(
                development_count
                >= MIN_PORTFOLIO_DEVELOPMENT_CASES
            ),
            current=development_count,
            minimum=MIN_PORTFOLIO_DEVELOPMENT_CASES,
            message="Development样本不少于20个。",
        ),
        _quality_check(
            code="MIN_PORTFOLIO_HOLDOUT_CASES",
            passed=(
                holdout_count >= MIN_PORTFOLIO_HOLDOUT_CASES
            ),
            current=holdout_count,
            minimum=MIN_PORTFOLIO_HOLDOUT_CASES,
            message="Holdout样本不少于10个。",
        ),
    ]
    runnable_codes = {
        "HAS_CASES",
        "NO_DUPLICATE_EVAL_CASE_IDS",
        "NO_PROJECT_SPLIT_LEAKAGE",
        "ALL_CASES_APPROVED",
    }
    return {
        "schema_version": "bid_intake_retrieval_dataset_quality_v1",
        "dataset_fingerprint": dataset_fingerprint(cases),
        "case_count": len(cases),
        "benchmark_case_count": benchmark_case_count,
        "development_case_count": development_count,
        "holdout_case_count": holdout_count,
        "challenge_case_count": challenge_count,
        "approved_case_count": approved_count,
        "synthetic_case_count": sum(
            item.source == "synthetic" for item in cases
        ),
        "historical_case_count": sum(
            item.source == "historical" for item in cases
        ),
        "negative_case_count": sum(
            item.expected_no_result for item in cases
        ),
        "duplicate_case_count": duplicate_case_count,
        "project_split_leakage_count": (
            project_split_leakage_count
        ),
        "runnable": all(
            item["passed"]
            for item in checks
            if item["code"] in runnable_codes
        ),
        "portfolio_ready": all(
            item["passed"] for item in checks
        ),
        "checks": checks,
    }


def evaluate_retrieval_predictions(
    *,
    cases: Sequence[RetrievalEvalCase],
    predictions: Sequence[RetrievalPrediction],
    top_k: int = 5,
    experiment: dict[str, Any] | None = None,
    baseline_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bounded_top_k = max(1, min(int(top_k), 20))
    prediction_map = {
        item.eval_case_id: item for item in predictions
    }
    case_results = [
        _evaluate_case(
            case=item,
            prediction=prediction_map.get(item.eval_case_id),
            top_k=bounded_top_k,
        )
        for item in cases
    ]
    report = {
        "schema_version": "bid_intake_retrieval_eval_report_v1",
        "dataset_fingerprint": dataset_fingerprint(cases),
        "top_k": bounded_top_k,
        "experiment": experiment or {},
        "dataset_quality": build_dataset_quality_report(cases),
        "overall": _aggregate_metrics(case_results),
        "by_split": _group_metrics(
            case_results,
            key="dataset_split",
        ),
        "by_difficulty": _group_metrics(
            case_results,
            key="difficulty",
        ),
        "by_expected_route": _group_metrics(
            case_results,
            key="expected_route_label",
        ),
        "by_project": _group_metrics(
            case_results,
            key="case_id",
        ),
        "case_results": case_results,
    }
    if baseline_report is not None:
        report["comparison"] = compare_eval_reports(
            baseline=baseline_report,
            candidate=report,
        )
    return report


def compare_eval_reports(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if (
        baseline.get("dataset_fingerprint")
        != candidate.get("dataset_fingerprint")
    ):
        raise ValueError(
            "baseline and candidate dataset fingerprints differ"
        )
    metric_names = (
        "hit_rate_at_k",
        "mean_recall_at_k",
        "mean_precision_at_k",
        "mrr",
        "mean_ndcg_at_k",
        "mean_candidate_pool_recall",
        "candidate_pool_oracle_complete_rate",
        "mean_candidate_to_top_k_gold_retention",
        "total_gold_dropped_from_candidate_pool",
        "routing_exact_rate",
        "query_count_accuracy",
        "topic_recall",
        "negative_accuracy",
        "fact_gate_assessment_rate",
        "fact_gate_alignment_accuracy",
        "fact_gate_negative_accuracy",
        "fact_gate_false_sufficient_rate",
        "fact_gate_false_insufficient_rate",
        "mean_latency_ms",
        "p95_latency_ms",
    )
    baseline_metrics = baseline.get("overall") or {}
    candidate_metrics = candidate.get("overall") or {}
    deltas = {}
    for name in metric_names:
        before = baseline_metrics.get(name)
        after = candidate_metrics.get(name)
        if before is None or after is None:
            continue
        deltas[name] = round(float(after) - float(before), 6)

    baseline_cases = {
        str(item.get("eval_case_id")): item
        for item in baseline.get("case_results") or []
    }
    regressions = []
    improvements = []
    case_metric_names = (
        "recall_at_k",
        "ndcg_at_k",
        "reciprocal_rank",
    )
    for item in candidate.get("case_results") or []:
        case_id = str(item.get("eval_case_id") or "")
        before = baseline_cases.get(case_id)
        if before is None:
            continue
        metric_deltas = {
            name: round(
                float(item.get(name) or 0)
                - float(before.get(name) or 0),
                6,
            )
            for name in case_metric_names
        }
        changed_metrics = [
            name
            for name in case_metric_names
            if metric_deltas[name] != 0
        ]
        if not changed_metrics:
            continue
        decisive_metric = changed_metrics[0]
        comparison_item = {
            "eval_case_id": case_id,
            "metric": decisive_metric,
            "before": before.get(decisive_metric),
            "after": item.get(decisive_metric),
            "metric_deltas": metric_deltas,
        }
        if metric_deltas[decisive_metric] < 0:
            regressions.append(comparison_item)
        else:
            improvements.append(comparison_item)
    return {
        "baseline_experiment": baseline.get("experiment") or {},
        "candidate_experiment": candidate.get("experiment") or {},
        "metric_deltas": deltas,
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
        "regressions": regressions,
        "improvements": improvements,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    experiment = report.get("experiment") or {}
    overall = report.get("overall") or {}
    quality = report.get("dataset_quality") or {}
    lines = [
        "# 报价资料研判 Agent 检索评测报告",
        "",
        f"- 实验：{experiment.get('name') or '-'}",
        f"- 变更假设：{experiment.get('change_note') or '-'}",
        f"- 数据集指纹：`{report.get('dataset_fingerprint') or '-'}`",
        f"- 样本数：{overall.get('case_count') or 0}",
        f"- Top K：{report.get('top_k') or '-'}",
        f"- 可运行：{quality.get('runnable')}",
        f"- 作品集就绪：{quality.get('portfolio_ready')}",
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
    ]
    for label, key in (
        ("Hit@K", "hit_rate_at_k"),
        ("Recall@K", "mean_recall_at_k"),
        ("Precision@K", "mean_precision_at_k"),
        ("MRR", "mrr"),
        ("nDCG@K", "mean_ndcg_at_k"),
        ("候选池Gold Recall", "mean_candidate_pool_recall"),
        (
            "候选池到TopK的Gold保留率",
            "mean_candidate_to_top_k_gold_retention",
        ),
        (
            "候选池完整覆盖率",
            "candidate_pool_oracle_complete_rate",
        ),
        (
            "候选池中找到但TopK淘汰的Gold数",
            "total_gold_dropped_from_candidate_pool",
        ),
        ("路由完全正确率", "routing_exact_rate"),
        ("Query数量准确率", "query_count_accuracy"),
        ("主题召回率", "topic_recall"),
        ("负样本准确率", "negative_accuracy"),
        ("事实充分性门评估覆盖率", "fact_gate_assessment_rate"),
        ("事实充分性门一致率", "fact_gate_alignment_accuracy"),
        ("事实充分性门负样本准确率", "fact_gate_negative_accuracy"),
        (
            "事实充分性门误充分率",
            "fact_gate_false_sufficient_rate",
        ),
        (
            "事实充分性门误拒答率",
            "fact_gate_false_insufficient_rate",
        ),
        ("平均延迟(ms)", "mean_latency_ms"),
        ("P95延迟(ms)", "p95_latency_ms"),
        ("执行错误数", "error_count"),
    ):
        lines.append(f"| {label} | {_display(overall.get(key))} |")
    comparison = report.get("comparison")
    if isinstance(comparison, dict):
        lines.extend(
            [
                "",
                "## 相对基线",
                "",
                f"- 改善样本：{comparison.get('improvement_count', 0)}",
                f"- 回退样本：{comparison.get('regression_count', 0)}",
                "",
                "| 指标 | 变化 |",
                "|---|---:|",
            ]
        )
        for key, value in (
            comparison.get("metric_deltas") or {}
        ).items():
            lines.append(f"| {key} | {_signed(value)} |")
    lines.extend(
        [
            "",
            "## 失败与回退样本",
            "",
            "| Case | Error | Recall@K | 返回证据 |",
            "|---|---|---:|---|",
        ]
    )
    failed = [
        item
        for item in report.get("case_results") or []
        if item.get("error_code")
        or (
            not item.get("expected_no_result")
            and not item.get("hit_at_k")
        )
    ]
    if not failed:
        lines.append("| - | - | - | 无 |")
    else:
        for item in failed:
            lines.append(
                "| {case} | {error} | {recall} | {returned} |".format(
                    case=item.get("eval_case_id") or "-",
                    error=item.get("error_code") or "-",
                    recall=_display(item.get("recall_at_k")),
                    returned=", ".join(
                        item.get("returned_evidence_ids") or []
                    )
                    or "-",
                )
            )
    lines.append("")
    return "\n".join(lines)


def _evaluate_case(
    *,
    case: RetrievalEvalCase,
    prediction: RetrievalPrediction | None,
    top_k: int,
) -> dict[str, Any]:
    returned_groups = _prediction_evidence_groups(
        prediction=prediction,
        top_k=top_k,
    )
    returned_anchor_ids = [
        group.anchor_evidence_id for group in returned_groups
    ]
    returned = _flatten_evidence_groups(returned_groups)
    gold_relevance = {
        item.evidence_id: item.relevance
        for item in case.gold_evidence
    }
    relevant_returned = [
        evidence_id
        for evidence_id in returned
        if evidence_id in gold_relevance
    ]
    candidate_pool_captured = bool(
        prediction is not None
        and prediction.candidate_pool_captured
    )
    candidate_pool_ids = (
        list(dict.fromkeys(prediction.candidate_pool_evidence_ids))
        if candidate_pool_captured and prediction is not None
        else []
    )
    candidate_pool_gold_ids = [
        evidence_id
        for evidence_id in candidate_pool_ids
        if evidence_id in gold_relevance
    ]
    selected_gold_ids = list(dict.fromkeys(relevant_returned))
    dropped_gold_ids = [
        evidence_id
        for evidence_id in candidate_pool_gold_ids
        if evidence_id not in selected_gold_ids
    ]
    if case.expected_no_result:
        hit_at_k = not returned
        recall_at_k = 1.0 if not returned else 0.0
        precision_at_k = recall_at_k
        reciprocal_rank = recall_at_k
        ndcg_at_k = recall_at_k
    else:
        hit_at_k = bool(relevant_returned)
        recall_at_k = (
            len(set(relevant_returned))
            / len(gold_relevance)
        )
        precision_at_k = len(relevant_returned) / max(
            top_k,
            len(returned),
        )
        reciprocal_rank = _group_reciprocal_rank(
            returned_groups,
            set(gold_relevance),
        )
        ndcg_at_k = _group_ndcg(
            returned_groups=returned_groups,
            gold_relevance=gold_relevance,
            top_k=top_k,
        )

    query_plan = (
        prediction.query_plan
        if prediction is not None
        else {}
    )
    routing = _routing_metrics(
        expected=case.expected_routing,
        actual=query_plan,
    )
    expected_route_label = _route_label(
        case.expected_routing.mode_counts
    )
    fact_coverage = (
        prediction.fact_coverage
        if prediction is not None
        else None
    )
    fact_status = (
        fact_coverage.sufficiency_status
        if fact_coverage is not None
        else "not_assessed"
    )
    fact_assessed = fact_status != "not_assessed"
    fact_predicted_sufficient = (
        fact_status == "candidate_sufficient"
        if fact_assessed
        else None
    )
    oracle_top_k_complete = (
        not case.expected_no_result
        and recall_at_k == 1.0
    )
    candidate_pool_recall = (
        len(candidate_pool_gold_ids) / len(gold_relevance)
        if candidate_pool_captured
        and not case.expected_no_result
        and gold_relevance
        else None
    )
    candidate_to_top_k_gold_retention = (
        len(
            set(candidate_pool_gold_ids)
            & set(selected_gold_ids)
        )
        / len(set(candidate_pool_gold_ids))
        if candidate_pool_gold_ids
        else None
    )
    fact_alignment = (
        fact_predicted_sufficient == oracle_top_k_complete
        if fact_predicted_sufficient is not None
        else None
    )
    return {
        "eval_case_id": case.eval_case_id,
        "case_id": case.case_id,
        "dataset_split": case.dataset_split,
        "difficulty": case.difficulty,
        "tags": list(case.tags),
        "expected_route_label": expected_route_label,
        "expected_no_result": case.expected_no_result,
        "gold_evidence_ids": list(gold_relevance),
        "returned_anchor_evidence_ids": returned_anchor_ids,
        "returned_evidence_groups": [
            item.model_dump(mode="json")
            for item in returned_groups
        ],
        "returned_evidence_ids": returned,
        "candidate_pool_captured": candidate_pool_captured,
        "candidate_pool_evidence_ids": candidate_pool_ids,
        "candidate_pool_search_call_count": (
            prediction.candidate_pool_search_call_count
            if prediction is not None
            else 0
        ),
        "candidate_pool_gold_evidence_ids": (
            candidate_pool_gold_ids
        ),
        "candidate_pool_gold_count": len(candidate_pool_gold_ids),
        "candidate_pool_recall": (
            round(candidate_pool_recall, 6)
            if candidate_pool_recall is not None
            else None
        ),
        "candidate_pool_oracle_complete": (
            candidate_pool_recall == 1.0
            if candidate_pool_recall is not None
            else None
        ),
        "candidate_to_top_k_gold_retention": (
            round(candidate_to_top_k_gold_retention, 6)
            if candidate_to_top_k_gold_retention is not None
            else None
        ),
        "gold_dropped_from_candidate_pool_ids": dropped_gold_ids,
        "gold_dropped_from_candidate_pool_count": len(
            dropped_gold_ids
        ),
        "hit_at_k": hit_at_k,
        "recall_at_k": round(recall_at_k, 6),
        "precision_at_k": round(precision_at_k, 6),
        "reciprocal_rank": round(reciprocal_rank, 6),
        "ndcg_at_k": round(ndcg_at_k, 6),
        "fact_coverage_status": fact_status,
        "fact_coverage_rate": (
            fact_coverage.coverage_rate
            if fact_coverage is not None
            else None
        ),
        "fact_gate_assessed": fact_assessed,
        "fact_gate_predicted_sufficient": (
            fact_predicted_sufficient
        ),
        "oracle_top_k_complete": oracle_top_k_complete,
        "fact_gate_alignment": fact_alignment,
        "fact_gate_false_sufficient": (
            bool(fact_predicted_sufficient)
            and not oracle_top_k_complete
            if fact_predicted_sufficient is not None
            else None
        ),
        "fact_gate_false_insufficient": (
            not fact_predicted_sufficient
            and oracle_top_k_complete
            if fact_predicted_sufficient is not None
            else None
        ),
        **routing,
        "latency_ms": (
            prediction.latency_ms
            if prediction is not None
            else None
        ),
        "result_status": (
            prediction.result_status
            if prediction is not None
            else "missing_prediction"
        ),
        "error_code": (
            prediction.error_code
            if prediction is not None
            else "MISSING_PREDICTION"
        ),
    }


def _prediction_evidence_groups(
    *,
    prediction: RetrievalPrediction | None,
    top_k: int,
) -> list[RetrievalEvidenceGroup]:
    if prediction is None:
        return []
    if prediction.returned_evidence_groups:
        return list(prediction.returned_evidence_groups[:top_k])
    return [
        RetrievalEvidenceGroup(
            anchor_evidence_id=evidence_id,
            context_evidence_ids=[],
        )
        for evidence_id in prediction.returned_evidence_ids[:top_k]
    ]


def _flatten_evidence_groups(
    groups: Sequence[RetrievalEvidenceGroup],
) -> list[str]:
    flattened: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for evidence_id in (
            group.anchor_evidence_id,
            *group.context_evidence_ids,
        ):
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            flattened.append(evidence_id)
    return flattened


def _group_reciprocal_rank(
    groups: Sequence[RetrievalEvidenceGroup],
    relevant_ids: set[str],
) -> float:
    for rank, group in enumerate(groups, start=1):
        if any(
            evidence_id in relevant_ids
            for evidence_id in (
                group.anchor_evidence_id,
                *group.context_evidence_ids,
            )
        ):
            return 1.0 / rank
    return 0.0


def _group_ndcg(
    *,
    returned_groups: Sequence[RetrievalEvidenceGroup],
    gold_relevance: dict[str, int],
    top_k: int,
) -> float:
    group_relevance = [
        max(
            (
                gold_relevance.get(evidence_id, 0)
                for evidence_id in (
                    group.anchor_evidence_id,
                    *group.context_evidence_ids,
                )
            ),
            default=0,
        )
        for group in returned_groups[:top_k]
    ]
    dcg = sum(
        ((2**relevance) - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(
            group_relevance,
            start=1,
        )
        if relevance > 0
    )
    ideal_relevance = sorted(
        gold_relevance.values(),
        reverse=True,
    )[:top_k]
    ideal_dcg = sum(
        ((2**relevance) - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(
            ideal_relevance,
            start=1,
        )
    )
    return 0.0 if ideal_dcg == 0 else min(1.0, dcg / ideal_dcg)


def _routing_metrics(
    *,
    expected: RoutingExpectation,
    actual: dict[str, Any],
) -> dict[str, Any]:
    tasks = (
        actual.get("query_tasks")
        if isinstance(actual.get("query_tasks"), list)
        else []
    )
    actual_counts = Counter(
        str(item.get("requested_mode") or "")
        for item in tasks
        if isinstance(item, dict)
        and item.get("requested_mode")
    )
    actual_query_count = int(
        actual.get("query_count") or len(tasks) or 0
    )
    actual_topics = {
        str(item)
        for item in (actual.get("topics") or [])
        if str(item)
    }
    expected_counts = dict(expected.mode_counts)
    mode_counts_match = (
        dict(actual_counts) == expected_counts
        if expected_counts
        else None
    )
    query_count_match = (
        actual_query_count == expected.query_count
        if expected.query_count is not None
        else None
    )
    expected_topics = set(expected.required_topics)
    topic_recall = (
        len(expected_topics & actual_topics)
        / len(expected_topics)
        if expected_topics
        else None
    )
    checks = [
        value
        for value in (
            mode_counts_match,
            query_count_match,
            (
                topic_recall == 1.0
                if topic_recall is not None
                else None
            ),
        )
        if value is not None
    ]
    return {
        "expected_mode_counts": expected_counts,
        "actual_mode_counts": dict(actual_counts),
        "expected_query_count": expected.query_count,
        "actual_query_count": actual_query_count,
        "routing_exact_match": (
            all(checks) if checks else None
        ),
        "query_count_match": query_count_match,
        "topic_recall": (
            round(topic_recall, 6)
            if topic_recall is not None
            else None
        ),
    }


def _aggregate_metrics(
    results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    positives = [
        item for item in results if not item["expected_no_result"]
    ]
    negatives = [
        item for item in results if item["expected_no_result"]
    ]
    routing = [
        bool(item["routing_exact_match"])
        for item in results
        if item["routing_exact_match"] is not None
    ]
    query_counts = [
        bool(item["query_count_match"])
        for item in results
        if item["query_count_match"] is not None
    ]
    topic_recalls = [
        float(item["topic_recall"])
        for item in results
        if item["topic_recall"] is not None
    ]
    latencies = [
        int(item["latency_ms"])
        for item in results
        if item.get("latency_ms") is not None
    ]
    fact_assessed = [
        item for item in results if item["fact_gate_assessed"]
    ]
    fact_incomplete = [
        item
        for item in fact_assessed
        if not item["oracle_top_k_complete"]
    ]
    fact_complete = [
        item
        for item in fact_assessed
        if item["oracle_top_k_complete"]
    ]
    fact_negative = [
        item
        for item in fact_assessed
        if item["expected_no_result"]
    ]
    candidate_pool_assessed = [
        item
        for item in positives
        if item["candidate_pool_captured"]
        and item["candidate_pool_recall"] is not None
    ]
    candidate_pool_with_gold = [
        item
        for item in candidate_pool_assessed
        if item["candidate_pool_gold_count"] > 0
        and item["candidate_to_top_k_gold_retention"] is not None
    ]
    return {
        "case_count": len(results),
        "positive_case_count": len(positives),
        "negative_case_count": len(negatives),
        "error_count": sum(
            bool(item.get("error_code")) for item in results
        ),
        "hit_rate_at_k": _mean(
            [bool(item["hit_at_k"]) for item in positives]
        ),
        "mean_recall_at_k": _mean(
            [float(item["recall_at_k"]) for item in positives]
        ),
        "mean_precision_at_k": _mean(
            [float(item["precision_at_k"]) for item in positives]
        ),
        "mrr": _mean(
            [float(item["reciprocal_rank"]) for item in positives]
        ),
        "mean_ndcg_at_k": _mean(
            [float(item["ndcg_at_k"]) for item in positives]
        ),
        "routing_exact_rate": _mean(routing),
        "query_count_accuracy": _mean(query_counts),
        "topic_recall": _mean(topic_recalls),
        "negative_accuracy": _mean(
            [bool(item["hit_at_k"]) for item in negatives]
        ),
        "candidate_pool_assessment_rate": (
            len(candidate_pool_assessed) / len(positives)
            if positives
            else None
        ),
        "mean_candidate_pool_recall": _mean(
            [
                float(item["candidate_pool_recall"])
                for item in candidate_pool_assessed
            ]
        ),
        "candidate_pool_oracle_complete_rate": _mean(
            [
                bool(item["candidate_pool_oracle_complete"])
                for item in candidate_pool_assessed
            ]
        ),
        "mean_candidate_to_top_k_gold_retention": _mean(
            [
                float(item["candidate_to_top_k_gold_retention"])
                for item in candidate_pool_with_gold
            ]
        ),
        "total_gold_dropped_from_candidate_pool": sum(
            int(item["gold_dropped_from_candidate_pool_count"])
            for item in candidate_pool_assessed
        ),
        "fact_gate_assessment_rate": (
            len(fact_assessed) / len(results)
            if results
            else None
        ),
        "fact_gate_alignment_accuracy": _mean(
            [
                bool(item["fact_gate_alignment"])
                for item in fact_assessed
            ]
        ),
        "fact_gate_negative_accuracy": _mean(
            [
                not bool(item["fact_gate_predicted_sufficient"])
                for item in fact_negative
            ]
        ),
        "fact_gate_false_sufficient_rate": _mean(
            [
                bool(item["fact_gate_false_sufficient"])
                for item in fact_incomplete
            ]
        ),
        "fact_gate_false_insufficient_rate": _mean(
            [
                bool(item["fact_gate_false_insufficient"])
                for item in fact_complete
            ]
        ),
        "mean_latency_ms": (
            round(statistics.fmean(latencies), 3)
            if latencies
            else None
        ),
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def _group_metrics(
    results: Sequence[dict[str, Any]],
    *,
    key: str,
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        groups.setdefault(str(item.get(key) or "unknown"), []).append(
            item
        )
    return {
        label: _aggregate_metrics(rows)
        for label, rows in sorted(groups.items())
    }


def _reciprocal_rank(
    returned: Sequence[str],
    relevant: set[str],
) -> float:
    for rank, evidence_id in enumerate(returned, start=1):
        if evidence_id in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg(
    *,
    returned: Sequence[str],
    gold_relevance: dict[str, int],
    top_k: int,
) -> float:
    gains = [
        gold_relevance.get(evidence_id, 0)
        for evidence_id in returned[:top_k]
    ]
    actual = _dcg(gains)
    ideal = _dcg(
        sorted(gold_relevance.values(), reverse=True)[:top_k]
    )
    return actual / ideal if ideal else 0.0


def _dcg(relevances: Sequence[int]) -> float:
    return sum(
        ((2**relevance) - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def _mean(values: Sequence[float | bool]) -> float | None:
    if not values:
        return None
    return round(
        statistics.fmean(float(item) for item in values),
        6,
    )


def _percentile(
    values: Sequence[int],
    quantile: float,
) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            math.ceil(len(ordered) * quantile) - 1,
        ),
    )
    return ordered[index]


def _route_label(
    mode_counts: dict[RetrievalMode, int],
) -> str:
    active = [
        mode
        for mode in ("exact", "semantic", "hybrid")
        if mode_counts.get(mode, 0) > 0
    ]
    return "+".join(active) if active else "unlabelled"


def _quality_check(
    *,
    code: str,
    passed: bool,
    current: int,
    minimum: int,
    message: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "passed": passed,
        "current": current,
        "minimum": minimum,
        "message": message,
    }


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    rows = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSONL at {source}:{line_number}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"JSONL row must be an object at {source}:{line_number}"
            )
        rows.append(payload)
    return rows


def _write_jsonl(
    path: str | Path,
    rows: Sequence[dict[str, Any]],
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for item in rows
        ),
        encoding="utf-8",
    )


def _display(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _signed(value: Any) -> str:
    try:
        return f"{float(value):+.4f}"
    except (TypeError, ValueError):
        return str(value)
