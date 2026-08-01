from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.bid_intake.retrieval_evaluation import (
    RetrievalEvalCase,
    RetrievalEvidenceGroup,
    RetrievalPrediction,
    build_dataset_quality_report,
    compare_eval_reports,
    evaluate_retrieval_predictions,
    load_eval_cases,
    render_markdown_report,
    sanitize_fact_coverage_for_evaluation,
    sanitize_query_plan_for_evaluation,
)
from app.agents.bid_intake.contracts import (
    EvidenceSufficiencyStatus,
    FactCoverageMode,
    FactCoverageState,
    FactSlotCoverage,
    FactSlotCoverageStatus,
)


DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "bid_intake"
    / "retrieval"
    / "v1"
    / "public_demo.jsonl"
)


def _case(**updates) -> RetrievalEvalCase:
    payload = {
        "eval_case_id": "EVAL-001",
        "case_id": "PROJECT-001",
        "source": "synthetic",
        "dataset_split": "development",
        "question": "付款条件是什么？",
        "expected_routing": {
            "query_count": 1,
            "mode_counts": {"exact": 1},
            "required_topics": [],
        },
        "gold_evidence": [
            {
                "evidence_id": "EV-GOLD",
                "relevance": 3,
                "required_text_fragments": ["支付比例"],
            }
        ],
        "expected_no_result": False,
        "gold_answer_points": ["支付比例"],
        "expected_missing_materials": [],
        "difficulty": "easy",
        "tags": ["exact"],
        "privacy": "public_synthetic",
        "annotation_status": "approved",
        "annotated_by": "annotator-a",
        "reviewed_by": "reviewer-b",
    }
    payload.update(updates)
    return RetrievalEvalCase.model_validate(payload)


def _prediction(
    *,
    returned: list[str],
    latency_ms: int = 20,
) -> RetrievalPrediction:
    return RetrievalPrediction(
        eval_case_id="EVAL-001",
        returned_evidence_ids=returned,
        query_plan={
            "query_count": 1,
            "topics": [],
            "query_tasks": [
                {
                    "query_id": "q1",
                    "requested_mode": "exact",
                }
            ],
        },
        result_status="ok",
        latency_ms=latency_ms,
    )


def test_public_demo_dataset_is_runnable_but_not_portfolio_ready():
    cases = load_eval_cases(DATASET_PATH)

    quality = build_dataset_quality_report(cases)

    assert len(cases) == 7
    assert quality["runnable"] is True
    assert quality["portfolio_ready"] is False
    assert quality["development_case_count"] == 5
    assert quality["holdout_case_count"] == 2
    assert quality["challenge_case_count"] == 0
    assert quality["benchmark_case_count"] == 7
    assert quality["project_split_leakage_count"] == 0
    assert len(quality["dataset_fingerprint"]) == 64


def test_approved_case_requires_an_independent_reviewer():
    payload = _case().model_dump(mode="json")
    payload["reviewed_by"] = payload["annotated_by"]

    with pytest.raises(ValidationError):
        RetrievalEvalCase.model_validate(payload)


def test_draft_case_can_wait_for_gold_but_cannot_run_formal_eval():
    draft = _case(
        gold_evidence=[],
        annotation_status="draft",
        reviewed_by=None,
    )

    quality = build_dataset_quality_report([draft])

    assert draft.gold_evidence == []
    assert quality["approved_case_count"] == 0
    assert quality["runnable"] is False
    assert quality["portfolio_ready"] is False


def test_reviewed_positive_case_requires_gold_evidence():
    payload = _case().model_dump(mode="json")
    payload["annotation_status"] = "reviewed"
    payload["gold_evidence"] = []

    with pytest.raises(ValidationError):
        RetrievalEvalCase.model_validate(payload)


def test_evaluator_calculates_recall_mrr_ndcg_and_routing():
    report = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[
            _prediction(returned=["EV-WRONG", "EV-GOLD"])
        ],
        top_k=5,
        experiment={"name": "baseline"},
    )

    result = report["case_results"][0]
    assert result["hit_at_k"] is True
    assert result["recall_at_k"] == 1.0
    assert result["precision_at_k"] == 0.2
    assert result["reciprocal_rank"] == 0.5
    assert 0 < result["ndcg_at_k"] < 1
    assert result["routing_exact_match"] is True
    assert report["overall"]["hit_rate_at_k"] == 1.0
    assert report["overall"]["mrr"] == 0.5
    assert report["overall"]["routing_exact_rate"] == 1.0
    assert report["overall"]["mean_latency_ms"] == 20
    assert report["by_project"]["PROJECT-001"]["case_count"] == 1
    assert report["by_project"]["PROJECT-001"]["mrr"] == 0.5


def test_evaluator_measures_candidate_pool_gold_loss_before_top_k():
    case = _case(
        gold_evidence=[
            {
                "evidence_id": "EV-GOLD-1",
                "relevance": 3,
                "required_text_fragments": ["支付比例"],
            },
            {
                "evidence_id": "EV-GOLD-2",
                "relevance": 3,
                "required_text_fragments": ["付款节点"],
            },
            {
                "evidence_id": "EV-GOLD-3",
                "relevance": 3,
                "required_text_fragments": ["质保金"],
            },
        ]
    )
    prediction = RetrievalPrediction(
        eval_case_id="EVAL-001",
        returned_evidence_ids=["EV-GOLD-1", "EV-WRONG"],
        candidate_pool_captured=True,
        candidate_pool_evidence_ids=[
            "EV-GOLD-1",
            "EV-GOLD-2",
            "EV-WRONG",
        ],
        candidate_pool_search_call_count=1,
        query_plan={
            "query_count": 1,
            "topics": [],
            "query_tasks": [
                {
                    "query_id": "q1",
                    "requested_mode": "exact",
                }
            ],
        },
        result_status="ok",
        latency_ms=20,
    )

    report = evaluate_retrieval_predictions(
        cases=[case],
        predictions=[prediction],
        top_k=5,
    )

    result = report["case_results"][0]
    assert result["candidate_pool_recall"] == 0.666667
    assert result["candidate_pool_gold_count"] == 2
    assert result["candidate_to_top_k_gold_retention"] == 0.5
    assert result["gold_dropped_from_candidate_pool_ids"] == [
        "EV-GOLD-2"
    ]
    assert report["overall"]["mean_candidate_pool_recall"] == (
        pytest.approx(2 / 3)
    )
    assert report["overall"][
        "mean_candidate_to_top_k_gold_retention"
    ] == 0.5
    assert report["overall"][
        "total_gold_dropped_from_candidate_pool"
    ] == 1


def test_old_prediction_without_candidate_capture_is_not_misread_as_zero():
    report = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[_prediction(returned=["EV-GOLD"])],
        top_k=5,
    )

    result = report["case_results"][0]
    assert result["candidate_pool_captured"] is False
    assert result["candidate_pool_recall"] is None
    assert report["overall"]["candidate_pool_assessment_rate"] == 0.0
    assert report["overall"]["mean_candidate_pool_recall"] is None


def test_evaluator_counts_context_groups_without_extra_top_k_slots():
    case = _case(
        gold_evidence=[
            {
                "evidence_id": "EV-ANCHOR-GOLD",
                "relevance": 3,
                "required_text_fragments": ["份数"],
            },
            {
                "evidence_id": "EV-CONTEXT-GOLD",
                "relevance": 3,
                "required_text_fragments": ["地点"],
            },
        ]
    )
    prediction = RetrievalPrediction(
        eval_case_id="EVAL-001",
        returned_evidence_ids=[
            "EV-ANCHOR-GOLD",
            "EV-WRONG-2",
            "EV-WRONG-3",
            "EV-WRONG-4",
            "EV-WRONG-5",
        ],
        returned_evidence_groups=[
            RetrievalEvidenceGroup(
                anchor_evidence_id="EV-ANCHOR-GOLD",
                context_evidence_ids=["EV-CONTEXT-GOLD"],
            ),
            RetrievalEvidenceGroup(
                anchor_evidence_id="EV-WRONG-2",
            ),
            RetrievalEvidenceGroup(
                anchor_evidence_id="EV-WRONG-3",
            ),
            RetrievalEvidenceGroup(
                anchor_evidence_id="EV-WRONG-4",
            ),
            RetrievalEvidenceGroup(
                anchor_evidence_id="EV-WRONG-5",
            ),
        ],
        query_plan={
            "query_count": 1,
            "topics": [],
            "query_tasks": [
                {
                    "query_id": "q1",
                    "requested_mode": "exact",
                }
            ],
        },
        result_status="ok",
        latency_ms=20,
    )

    report = evaluate_retrieval_predictions(
        cases=[case],
        predictions=[prediction],
        top_k=5,
    )

    result = report["case_results"][0]
    assert result["returned_anchor_evidence_ids"] == [
        "EV-ANCHOR-GOLD",
        "EV-WRONG-2",
        "EV-WRONG-3",
        "EV-WRONG-4",
        "EV-WRONG-5",
    ]
    assert result["returned_evidence_ids"] == [
        "EV-ANCHOR-GOLD",
        "EV-CONTEXT-GOLD",
        "EV-WRONG-2",
        "EV-WRONG-3",
        "EV-WRONG-4",
        "EV-WRONG-5",
    ]
    assert result["recall_at_k"] == 1.0
    assert result["precision_at_k"] == 0.333333
    assert result["reciprocal_rank"] == 1.0


def test_negative_case_scores_empty_result_as_correct():
    negative = _case(
        gold_evidence=[],
        expected_no_result=True,
    )
    report = evaluate_retrieval_predictions(
        cases=[negative],
        predictions=[_prediction(returned=[])],
        top_k=5,
    )

    assert report["overall"]["positive_case_count"] == 0
    assert report["overall"]["negative_accuracy"] == 1.0
    assert report["case_results"][0]["hit_at_k"] is True


def test_report_comparison_records_improvement_and_regression():
    baseline = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[_prediction(returned=[])],
        top_k=5,
        experiment={"name": "before"},
    )
    candidate = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[_prediction(returned=["EV-GOLD"])],
        top_k=5,
        experiment={"name": "after"},
    )

    comparison = compare_eval_reports(
        baseline=baseline,
        candidate=candidate,
    )

    assert comparison["improvement_count"] == 1
    assert comparison["regression_count"] == 0
    assert comparison["metric_deltas"]["mrr"] == 1.0

    regressed = compare_eval_reports(
        baseline=candidate,
        candidate=baseline,
    )
    assert regressed["regression_count"] == 1


def test_report_comparison_detects_ranking_regression():
    baseline = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[_prediction(returned=["EV-GOLD"])],
    )
    candidate = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[
            _prediction(returned=["EV-WRONG", "EV-GOLD"])
        ],
    )

    comparison = compare_eval_reports(
        baseline=baseline,
        candidate=candidate,
    )

    assert comparison["regression_count"] == 1
    assert comparison["regressions"][0]["metric"] == "ndcg_at_k"
    assert comparison["regressions"][0]["metric_deltas"][
        "recall_at_k"
    ] == 0


def test_report_comparison_rejects_different_dataset():
    baseline = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[_prediction(returned=[])],
    )
    other = _case(eval_case_id="EVAL-OTHER")
    prediction = _prediction(returned=[])
    prediction = prediction.model_copy(
        update={"eval_case_id": "EVAL-OTHER"}
    )
    candidate = evaluate_retrieval_predictions(
        cases=[other],
        predictions=[prediction],
    )

    with pytest.raises(ValueError, match="fingerprints differ"):
        compare_eval_reports(
            baseline=baseline,
            candidate=candidate,
        )


def test_markdown_report_does_not_include_questions_or_source_text():
    report = evaluate_retrieval_predictions(
        cases=[_case()],
        predictions=[_prediction(returned=[])],
        experiment={
            "name": "safe-report",
            "change_note": "固定数据集对比。",
        },
    )

    markdown = render_markdown_report(report)

    assert "safe-report" in markdown
    assert "EVAL-001" in markdown
    assert "付款条件是什么" not in markdown
    assert "支付比例" not in markdown


def test_query_plan_sanitizer_removes_business_question_text():
    sanitized = sanitize_query_plan_for_evaluation(
        {
            "schema_version": "tender-query-plan/v1",
            "original_query": "某甲方秘密项目的付款条件是什么？",
            "queries": ["某甲方秘密项目的付款条件是什么？"],
            "atomic_queries": ["甲方秘密付款条件"],
            "query_count": 1,
            "strategy": "single_query",
            "topics": ["payment"],
            "per_query_candidate_top_k": 20,
            "final_top_k": 5,
            "supporting_queries": ["甲方秘密付款条件"],
            "supporting_query_count": 1,
            "supporting_topics": ["payment"],
            "supporting_strategy": "semantic_fact_companion",
            "fact_slot_queries": [
                "某甲方秘密项目纸质文件份数",
            ],
            "fact_slot_query_count": 1,
            "fact_slot_types": ["quantity"],
            "fact_slot_strategy": "compound_surface_fact_slots",
            "coverage_need_queries": [
                "某甲方秘密项目付款条件",
            ],
            "coverage_need_count": 1,
            "coverage_need_types": ["condition"],
            "coverage_need_subjects": ["甲方秘密付款节点"],
            "coverage_need_answer_shapes": ["condition_relation"],
            "coverage_strategy": "answer_signal_need_coverage",
            "coverage_selection_policy": (
                "predicate_aware_marginal_gain"
            ),
            "coverage_relation_shape_supported": True,
            "coverage_relation_shape_reason": None,
            "sufficiency_need_queries": [
                "某甲方秘密项目付款条件",
            ],
            "sufficiency_need_count": 1,
            "sufficiency_need_types": ["condition"],
            "sufficiency_need_subjects": ["甲方秘密付款节点"],
            "sufficiency_need_answer_shapes": [
                "condition_relation",
            ],
            "sufficiency_strategy": (
                "predicate_aware_relation_evidence_v1"
            ),
            "sufficiency_relation_shape_supported": True,
            "sufficiency_relation_shape_reason": None,
            "evidence_sufficiency_summary": {
                "enabled": True,
                "required_need_count": 1,
                "covered_need_count": 0,
                "sufficiency_status": "insufficient",
                "changes_result_selection": False,
                "additional_search_query_count": 0,
            },
            "coverage_selection_summary": {
                "enabled": True,
                "need_count": 1,
                "covered_need_count": 1,
                "selected_evidence_count": 1,
            },
            "adjacent_expansion_summary": {
                "enabled": True,
                "seed_count": 1,
                "context_read_count": 1,
                "context_block_count": 3,
                "added_candidate_count": 1,
                "existing_candidate_count": 1,
                "filtered_document_count": 0,
                "filtered_section_count": 0,
                "filtered_non_direct_count": 0,
                "error_count": 0,
            },
            "context_evidence_group_summary": {
                "enabled": True,
                "anchor_count": 5,
                "seed_count": 1,
                "context_read_count": 1,
                "context_block_count": 3,
                "grouped_anchor_count": 1,
                "member_count": 1,
                "existing_candidate_count": 0,
                "filtered_document_count": 0,
                "filtered_section_count": 0,
                "filtered_non_direct_count": 0,
                "filtered_no_coverage_count": 1,
                "error_count": 0,
                "max_members_per_anchor": 1,
            },
            "query_tasks": [
                {
                    "query_id": "q1",
                    "query": "某甲方秘密项目的付款条件是什么？",
                    "requested_mode": "exact",
                    "executed_mode": "exact",
                    "result_count": 2,
                    "reason_codes": ["fact_or_keyword_lookup"],
                }
            ],
            "supporting_query_tasks": [
                {
                    "query_id": "support1",
                    "query_kind": "supporting_fact",
                    "query": "甲方秘密付款条件",
                    "requested_mode": "exact",
                    "executed_mode": "exact",
                    "result_count": 2,
                    "reason_codes": ["fact_or_keyword_lookup"],
                }
            ],
            "fact_slot_query_tasks": [
                {
                    "query_id": "slot1",
                    "query_kind": "atomic_fact_slot",
                    "fact_slot_type": "quantity",
                    "query": "某甲方秘密项目纸质文件份数",
                    "requested_mode": "exact",
                    "executed_mode": "exact",
                    "result_count": 1,
                    "reason_codes": ["fact_or_keyword_lookup"],
                }
            ],
            "controlled_retry_query_tasks": [
                {
                    "query_id": "retry1",
                    "query_kind": "uncovered_fact_retry",
                    "coverage_need_index": 2,
                    "coverage_need_type": "condition",
                    "query": "某甲方秘密结算条件",
                    "requested_mode": "exact",
                    "executed_mode": "exact",
                    "result_count": 1,
                    "reason_codes": ["fact_or_keyword_lookup"],
                }
            ],
            "controlled_retry_summary": {
                "enabled": True,
                "triggered": True,
                "executed_retry_query_count": 1,
            },
            "total_search_query_count": 2,
        }
    )

    serialized = str(sanitized)
    assert "某甲方" not in serialized
    assert "query" not in sanitized["query_tasks"][0]
    assert sanitized["query_tasks"][0]["requested_mode"] == "exact"
    assert sanitized["topics"] == ["payment"]
    assert sanitized["per_query_candidate_top_k"] == 20
    assert sanitized["final_top_k"] == 5
    assert sanitized["supporting_query_count"] == 1
    assert sanitized["supporting_topics"] == ["payment"]
    assert (
        sanitized["supporting_query_tasks"][0]["query_kind"]
        == "supporting_fact"
    )
    assert "query" not in sanitized["supporting_query_tasks"][0]
    assert sanitized["fact_slot_query_count"] == 1
    assert sanitized["fact_slot_types"] == ["quantity"]
    assert (
        sanitized["fact_slot_query_tasks"][0]["query_kind"]
        == "atomic_fact_slot"
    )
    assert (
        sanitized["fact_slot_query_tasks"][0]["fact_slot_type"]
        == "quantity"
    )
    assert "query" not in sanitized["fact_slot_query_tasks"][0]
    assert (
        sanitized["controlled_retry_query_tasks"][0]["query_kind"]
        == "uncovered_fact_retry"
    )
    assert (
        "query"
        not in sanitized["controlled_retry_query_tasks"][0]
    )
    assert sanitized["controlled_retry_summary"]["triggered"] is True
    assert sanitized["total_search_query_count"] == 2
    assert sanitized["coverage_need_count"] == 1
    assert sanitized["coverage_need_types"] == ["condition"]
    assert sanitized["coverage_need_answer_shapes"] == [
        "condition_relation"
    ]
    assert sanitized["coverage_strategy"] == (
        "answer_signal_need_coverage"
    )
    assert sanitized["coverage_selection_policy"] == (
        "predicate_aware_marginal_gain"
    )
    assert sanitized["coverage_relation_shape_supported"] is True
    assert sanitized["coverage_relation_shape_reason"] is None
    assert "coverage_need_subjects" not in sanitized
    assert sanitized["sufficiency_need_count"] == 1
    assert sanitized["sufficiency_need_types"] == ["condition"]
    assert sanitized["sufficiency_need_answer_shapes"] == [
        "condition_relation"
    ]
    assert sanitized["sufficiency_strategy"] == (
        "predicate_aware_relation_evidence_v1"
    )
    assert (
        sanitized["sufficiency_relation_shape_supported"] is True
    )
    assert sanitized["sufficiency_relation_shape_reason"] is None
    assert "sufficiency_need_subjects" not in sanitized
    assert "sufficiency_need_queries" not in sanitized
    assert sanitized["evidence_sufficiency_summary"] == {
        "enabled": True,
        "required_need_count": 1,
        "covered_need_count": 0,
        "sufficiency_status": "insufficient",
        "changes_result_selection": False,
        "additional_search_query_count": 0,
    }
    assert sanitized["coverage_selection_summary"] == {
        "enabled": True,
        "need_count": 1,
        "covered_need_count": 1,
        "selected_evidence_count": 1,
    }
    assert sanitized["adjacent_expansion_summary"] == {
        "enabled": True,
        "seed_count": 1,
        "context_read_count": 1,
        "context_block_count": 3,
        "added_candidate_count": 1,
        "existing_candidate_count": 1,
        "filtered_document_count": 0,
        "filtered_section_count": 0,
        "filtered_non_direct_count": 0,
        "error_count": 0,
    }
    assert sanitized["context_evidence_group_summary"] == {
        "enabled": True,
        "anchor_count": 5,
        "seed_count": 1,
        "context_read_count": 1,
        "context_block_count": 3,
        "grouped_anchor_count": 1,
        "member_count": 1,
        "existing_candidate_count": 0,
        "filtered_document_count": 0,
        "filtered_section_count": 0,
        "filtered_non_direct_count": 0,
        "filtered_no_coverage_count": 1,
        "error_count": 0,
        "max_members_per_anchor": 1,
    }


def test_fact_coverage_sanitizer_removes_labels_and_trace_ids():
    sanitized = sanitize_fact_coverage_for_evaluation(
        FactCoverageState(
            mode=FactCoverageMode.SHADOW,
            sufficiency_status=(
                EvidenceSufficiencyStatus.INSUFFICIENT
            ),
            required_slot_count=1,
            covered_slot_count=0,
            verified_slot_count=0,
            coverage_rate=0.0,
            evaluated_search_count=1,
            observed_search_count=1,
            slots=[
                FactSlotCoverage(
                    slot_id="fact-12345678",
                    label="敏感项目的投标保证金金额",
                    slot_type="amount",
                    status=FactSlotCoverageStatus.UNCOVERED,
                    source_trace_ids=["private-trace-id"],
                )
            ],
        )
    )

    assert sanitized is not None
    payload = sanitized.model_dump(mode="json")
    assert "label" not in payload["slots"][0]
    assert "source_trace_ids" not in payload["slots"][0]
    assert "敏感项目" not in str(payload)
    assert "private-trace-id" not in str(payload)


def test_fact_gate_metrics_separate_retrieval_from_abstention():
    positive = _case(eval_case_id="EVAL-POSITIVE")
    negative = _case(
        eval_case_id="EVAL-NEGATIVE",
        expected_no_result=True,
        gold_evidence=[],
    )
    common_plan = {
        "query_count": 1,
        "query_tasks": [
            {
                "query_id": "q1",
                "requested_mode": "exact",
            }
        ],
    }
    predictions = [
        RetrievalPrediction(
            eval_case_id=positive.eval_case_id,
            returned_evidence_ids=["EV-GOLD"],
            query_plan=common_plan,
            fact_coverage={
                "sufficiency_status": "candidate_sufficient",
                "required_slot_count": 1,
                "covered_slot_count": 1,
                "verified_slot_count": 0,
                "coverage_rate": 1.0,
                "evaluated_search_count": 1,
                "observed_search_count": 1,
                "slots": [
                    {
                        "slot_id": "fact-positive",
                        "slot_type": "condition",
                        "status": "candidate_covered",
                        "candidate_evidence_ids": ["EV-GOLD"],
                    }
                ],
            },
            result_status="ok",
            latency_ms=10,
        ),
        RetrievalPrediction(
            eval_case_id=negative.eval_case_id,
            returned_evidence_ids=["EV-WEAK"],
            query_plan=common_plan,
            fact_coverage={
                "sufficiency_status": "insufficient",
                "required_slot_count": 1,
                "covered_slot_count": 0,
                "verified_slot_count": 0,
                "coverage_rate": 0.0,
                "evaluated_search_count": 1,
                "observed_search_count": 1,
                "slots": [
                    {
                        "slot_id": "fact-negative",
                        "slot_type": "entity_fact",
                        "status": "uncovered",
                    }
                ],
            },
            result_status="ok",
            latency_ms=10,
        ),
    ]

    report = evaluate_retrieval_predictions(
        cases=[positive, negative],
        predictions=predictions,
        top_k=5,
    )

    assert report["overall"]["negative_accuracy"] == 0.0
    assert report["overall"]["fact_gate_assessment_rate"] == 1.0
    assert report["overall"]["fact_gate_alignment_accuracy"] == 1.0
    assert report["overall"]["fact_gate_negative_accuracy"] == 1.0
    assert report["overall"]["fact_gate_false_sufficient_rate"] == 0.0
    assert report["overall"]["fact_gate_false_insufficient_rate"] == 0.0


def test_quality_report_detects_project_split_leakage():
    holdout_payload = deepcopy(_case().model_dump(mode="json"))
    holdout_payload["eval_case_id"] = "EVAL-002"
    holdout_payload["dataset_split"] = "holdout"
    holdout = RetrievalEvalCase.model_validate(holdout_payload)

    quality = build_dataset_quality_report(
        [_case(), holdout]
    )

    assert quality["runnable"] is False
    assert quality["project_split_leakage_count"] == 1


def test_challenge_split_is_separate_from_portfolio_floor():
    challenge = _case(
        eval_case_id="CHALLENGE-001",
        case_id="PROJECT-CHALLENGE-001",
        dataset_split="challenge",
    )

    quality = build_dataset_quality_report([challenge])

    assert quality["case_count"] == 1
    assert quality["benchmark_case_count"] == 0
    assert quality["development_case_count"] == 0
    assert quality["holdout_case_count"] == 0
    assert quality["challenge_case_count"] == 1
    portfolio_check = next(
        item
        for item in quality["checks"]
        if item["code"] == "MIN_PORTFOLIO_CASES"
    )
    assert portfolio_check["current"] == 0
    assert portfolio_check["passed"] is False
