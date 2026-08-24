from __future__ import annotations

import pytest

from app.services.bid_retrieval_benchmark import (
    BASELINE_FUSION_PROFILE_VERSION,
    BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_VERSION,
    BENCHMARK_DATASET_SCHEMA_VERSION,
    BENCHMARK_FREEZE_SCHEMA_VERSION,
    BENCHMARK_PROFILE_VERSION,
    CANDIDATE_RERANK_PROFILE_VERSION,
    DEFAULT_ACCEPTANCE_THRESHOLDS,
    BenchmarkContractError,
    aggregate_project_reports,
    canonical_hash,
    materialize_project_silver_cases,
    validate_dataset,
    validate_development_snapshot,
    validate_development_holdout_isolation,
    validate_freeze,
    with_dataset_hash,
    with_development_snapshot_hash,
    with_freeze_hash,
)


def _sha(seed: int) -> str:
    return f"{seed:064x}"[-64:]


def _dataset(split: str) -> dict:
    project_count = 3 if split == "development" else 2
    start = 1 if split == "development" else 100
    projects = []
    cases = []
    for project_offset in range(project_count):
        project_no = start + project_offset
        project_id = f"project-{project_no}"
        projects.append(
            {
                "project_id": project_id,
                "project_family_hash": _sha(1000 + project_no),
                "prior_rq_exposure": (
                    "development" if split == "development" else "none"
                ),
                "source_documents": [
                    {
                        "document_id": f"doc-{project_no}",
                        "role": "primary_tender",
                        "sha256": _sha(2000 + project_no),
                        "page_count": 100,
                    }
                ],
            }
        )
        for case_offset in range(20):
            case_id = f"{project_id}-case-{case_offset + 1}"
            cases.append(
                {
                    "case_id": case_id,
                    "project_id": project_id,
                    "category": f"category-{case_offset % 5}",
                    "difficulty": ("hard" if case_offset % 3 == 0 else "medium"),
                    "question": f"{project_id} frozen question {case_offset + 1}?",
                    "answer_status": "answerable",
                    "targets": [
                        {
                            "target_id": f"target-{case_id}",
                            "evidence_role": "evidence_atom",
                            "required_text_fragments": [f"anchor {case_id}"],
                            "page_numbers": [case_offset + 1],
                            "match_policy": "all_fragments_same_atom",
                        }
                    ],
                    "annotation": {
                        "status": "approved",
                        "annotator_hash": _sha(3001),
                        "reviewer_hash": _sha(3002),
                    },
                }
            )
    return with_dataset_hash(
        {
            "schema_version": BENCHMARK_DATASET_SCHEMA_VERSION,
            "dataset_id": f"rq2-{split}-v1",
            "dataset_version": "1.0.0",
            "split": split,
            "status": "gold_approved" if split == "development" else "holdout_sealed",
            "sealed": split == "holdout",
            "maximum_formal_execution_count": 1 if split == "holdout" else 0,
            "privacy": "private_restricted",
            "projects": projects,
            "cases": cases,
        }
    )


def _development_snapshot(development: dict) -> dict:
    return with_development_snapshot_hash(
        {
            "schema_version": BENCHMARK_DEVELOPMENT_SNAPSHOT_SCHEMA_VERSION,
            "profile_version": BENCHMARK_PROFILE_VERSION,
            "snapshot_id": "RQ2-DEVELOPMENT-001",
            "status": "frozen_predevelopment",
            "dataset": {
                "dataset_id": development["dataset_id"],
                "dataset_hash": development["dataset_hash"],
                "file_sha256": _sha(4000),
                "project_count": 3,
                "case_count": 60,
                "restricted_path": "private/development.json",
            },
            "retrieval_profiles": {
                "baseline": BASELINE_FUSION_PROFILE_VERSION,
                "candidate": CANDIDATE_RERANK_PROFILE_VERSION,
            },
            "acceptance_thresholds": dict(DEFAULT_ACCEPTANCE_THRESHOLDS),
            "anti_leakage": {
                "project_specific_query_hints_forbidden": True,
                "threshold_change_after_snapshot_forbidden": True,
                "code_or_profile_change_requires_new_snapshot": True,
            },
            "frozen_artifacts": {
                "code_sha256": {"service.py": _sha(6001)},
                "contract_sha256": {"profile.json": _sha(6002)},
                "dependency_sha256": {"requirements.txt": _sha(6003)},
            },
        }
    )


def _freeze(development: dict, holdout: dict, snapshot: dict) -> dict:
    return with_freeze_hash(
        {
            "schema_version": BENCHMARK_FREEZE_SCHEMA_VERSION,
            "profile_version": BENCHMARK_PROFILE_VERSION,
            "freeze_id": "RQ2-CLOSEOUT-001",
            "status": "frozen_preholdout",
            "datasets": {
                "development": {
                    "dataset_id": development["dataset_id"],
                    "dataset_hash": development["dataset_hash"],
                    "file_sha256": _sha(4001),
                    "project_count": 3,
                    "case_count": 60,
                    "restricted_path": "private/development.json",
                },
                "holdout": {
                    "dataset_id": holdout["dataset_id"],
                    "dataset_hash": holdout["dataset_hash"],
                    "file_sha256": _sha(4002),
                    "project_count": 2,
                    "case_count": 40,
                    "restricted_path": "private/holdout.json",
                },
            },
            "retrieval_profiles": {
                "baseline": BASELINE_FUSION_PROFILE_VERSION,
                "candidate": CANDIDATE_RERANK_PROFILE_VERSION,
            },
            "acceptance_thresholds": dict(DEFAULT_ACCEPTANCE_THRESHOLDS),
            "development_acceptance": {
                "status": "passed",
                "report_sha256": _sha(5001),
                "snapshot_hash": snapshot["snapshot_hash"],
            },
            "holdout_execution": {
                "maximum_formal_execution_count": 1,
                "baseline_before_candidate": True,
                "rerun_after_result_forbidden": True,
                "result_may_not_change_thresholds": True,
            },
            "anti_leakage": {
                "project_level_split": True,
                "project_family_overlap_forbidden": True,
                "source_document_overlap_forbidden": True,
                "project_specific_query_hints_forbidden": True,
                "holdout_not_used_for_tuning": True,
                "holdout_failures_may_not_define_new_rules": True,
                "threshold_change_after_freeze_forbidden": True,
            },
            "frozen_artifacts": snapshot["frozen_artifacts"],
        }
    )


def _summary(project_id: str, *, candidate: bool) -> dict:
    metrics = {
        "case_count": 20,
        "hit_at_5": 1.0,
        "target_recall_at_5": 1.0,
        "mrr_at_5": 1.0,
        "ndcg_at_5": 1.0,
        "hit_at_8": 1.0,
        "target_recall_at_8": 1.0,
        "mrr_at_8": 1.0,
        "ndcg_at_8": 1.0,
        "read_target_recall_at_5": 1.0,
        "citable_target_availability": 1.0,
        "atom_only_violation_count": 0,
    }
    cases = []
    for offset in range(20):
        cases.append(
            {
                "case_id": f"{project_id}-case-{offset + 1}",
                "category": f"category-{offset % 5}",
                "target_count": 1,
                "citable_target_count": 1,
                "search_ms": 3000 if candidate else 1500,
                "metrics_at_5": {
                    "hit": True,
                    "target_recall": 1.0,
                    "mrr": 1.0,
                    "ndcg": 1.0,
                },
                "metrics_at_8": {
                    "hit": True,
                    "target_recall": 1.0,
                    "mrr": 1.0,
                    "ndcg": 1.0,
                },
                "read_target_recall_at_5": 1.0,
            }
        )
    return {
        "dataset_hash": _sha(7000 + int(project_id.split("-")[-1])),
        "profiles": {
            "candidate_fusion_profile_version": BASELINE_FUSION_PROFILE_VERSION,
            "rerank_profile_version": (
                CANDIDATE_RERANK_PROFILE_VERSION if candidate else "bid-evidence-rerank-profile-v0-disabled"
            ),
        },
        "retrieval": metrics,
        "case_metrics": cases,
    }


def test_dataset_and_holdout_isolation_contract() -> None:
    development = _dataset("development")
    holdout = _dataset("holdout")
    snapshot = _development_snapshot(development)
    assert validate_dataset(development)["case_count"] == 60
    assert validate_dataset(holdout)["project_count"] == 2
    assert validate_development_holdout_isolation(development, holdout) == {
        "project_id_overlap": 0,
        "project_family_overlap": 0,
        "document_hash_overlap": 0,
        "question_hash_overlap": 0,
    }
    assert validate_development_snapshot(
        snapshot,
        development=development,
    )["binding_hash"] == snapshot["snapshot_hash"]
    freeze = _freeze(development, holdout, snapshot)
    assert validate_freeze(
        freeze,
        development=development,
        holdout=holdout,
        development_snapshot=snapshot,
    )["freeze_hash"] == freeze["freeze_hash"]
    projected = materialize_project_silver_cases(
        development,
        project_id="project-1",
    )
    assert projected["schema_version"] == "bid.pdf-c3.silver-cases.v1"
    assert projected["source_benchmark_dataset_hash"] == development["dataset_hash"]
    assert len(projected["cases"]) == 20


def test_holdout_rejects_previously_exposed_project() -> None:
    holdout = _dataset("holdout")
    holdout["projects"][0]["prior_rq_exposure"] = "development"
    holdout = with_dataset_hash(holdout)
    with pytest.raises(BenchmarkContractError) as exc_info:
        validate_dataset(holdout)
    assert exc_info.value.code == "BENCHMARK_HOLDOUT_PROJECT_PREVIOUSLY_EXPOSED"


def test_dataset_hash_and_threshold_changes_fail_closed() -> None:
    development = _dataset("development")
    development["cases"][0]["question"] = "mutated after approval"
    with pytest.raises(BenchmarkContractError) as exc_info:
        validate_dataset(development)
    assert exc_info.value.code == "BENCHMARK_DATASET_HASH_MISMATCH"

    clean_development = _dataset("development")
    holdout = _dataset("holdout")
    snapshot = _development_snapshot(clean_development)
    freeze = _freeze(clean_development, holdout, snapshot)
    freeze["acceptance_thresholds"]["macro_hit_at_5_min"] = 0.1
    freeze = with_freeze_hash(freeze)
    with pytest.raises(BenchmarkContractError) as freeze_exc:
        validate_freeze(freeze)
    assert freeze_exc.value.code == "BENCHMARK_THRESHOLD_VALUE_MISMATCH"


def test_cross_project_aggregate_uses_macro_gates_and_zero_regressions() -> None:
    development = _dataset("development")
    holdout = _dataset("holdout")
    snapshot = _development_snapshot(development)
    reports = []
    for project in development["projects"]:
        project_id = project["project_id"]
        baseline = _summary(project_id, candidate=False)
        candidate = _summary(project_id, candidate=True)
        project_dataset_hash = canonical_hash(
            materialize_project_silver_cases(development, project_id=project_id)
        )
        baseline["dataset_hash"] = project_dataset_hash
        candidate["dataset_hash"] = project_dataset_hash
        reports.append(
            {
                "project_id": project_id,
                "dataset_hash": development["dataset_hash"],
                "document_sha256": [project["source_documents"][0]["sha256"]],
                "baseline_summary": baseline,
                "candidate_summary": candidate,
                "ab_summary": {
                    "schema_version": "bid.pdf-c3.lightweight-rerank-ab.v1",
                    "dataset_hash": baseline["dataset_hash"],
                    "document_sha256": project["source_documents"][0]["sha256"],
                    "invariants": {
                        "same_database": True,
                        "same_parse_head": True,
                        "same_retrieval_index_head": True,
                        "same_semantic_index_head": True,
                        "frozen_fusion_candidate_pool_identical": True,
                        "zero_promotion_identity": True,
                        "atom_only_violation_unchanged": True,
                        "deterministic_replay_match": True,
                    },
                },
            }
        )
    report = aggregate_project_reports(
        dataset=development,
        project_reports=reports,
        development_snapshot=snapshot,
    )
    assert report["project_count"] == 3
    assert report["case_count"] == 60
    assert report["quality_regression_count"] == 0
    assert report["gate"]["status"] == "passed"


def test_cross_project_aggregate_rejects_any_case_regression() -> None:
    development = _dataset("development")
    holdout = _dataset("holdout")
    snapshot = _development_snapshot(development)
    reports = []
    for project in development["projects"]:
        project_id = project["project_id"]
        baseline = _summary(project_id, candidate=False)
        candidate = _summary(project_id, candidate=True)
        project_dataset_hash = canonical_hash(
            materialize_project_silver_cases(development, project_id=project_id)
        )
        baseline["dataset_hash"] = project_dataset_hash
        candidate["dataset_hash"] = project_dataset_hash
        if not reports:
            candidate["case_metrics"][0]["metrics_at_5"]["target_recall"] = 0.0
            candidate["retrieval"]["target_recall_at_5"] = 0.95
        reports.append(
            {
                "project_id": project_id,
                "dataset_hash": development["dataset_hash"],
                "document_sha256": [project["source_documents"][0]["sha256"]],
                "baseline_summary": baseline,
                "candidate_summary": candidate,
                "ab_summary": {
                    "schema_version": "bid.pdf-c3.lightweight-rerank-ab.v1",
                    "dataset_hash": baseline["dataset_hash"],
                    "document_sha256": project["source_documents"][0]["sha256"],
                    "invariants": {
                        name: True
                        for name in (
                            "same_database",
                            "same_parse_head",
                            "same_retrieval_index_head",
                            "same_semantic_index_head",
                            "frozen_fusion_candidate_pool_identical",
                            "zero_promotion_identity",
                            "atom_only_violation_unchanged",
                            "deterministic_replay_match",
                        )
                    },
                },
            }
        )
    report = aggregate_project_reports(
        dataset=development,
        project_reports=reports,
        development_snapshot=snapshot,
    )
    assert report["quality_regression_count"] == 1
    assert report["gate"]["status"] == "failed"
    assert "per_case_quality_regression_count" in report["gate"]["failed_checks"]
