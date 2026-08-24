from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_bid_pure_agent_v608 import (
    DATASET_PATH,
    EXPECTED_DOMAINS,
    PERSISTENCE_CASE_IDS,
    SCHEMA_VERSION,
    V608EvaluationError,
    evaluate,
    load_dataset,
)


@pytest.fixture(scope="module")
def dataset() -> dict:
    return load_dataset(DATASET_PATH)


@pytest.fixture(scope="module")
def result(dataset: dict) -> dict:
    return evaluate(dataset)


def test_v608_dataset_freezes_six_guard_domains_and_isolation(dataset: dict) -> None:
    assert dataset["schema_version"] == SCHEMA_VERSION
    assert dataset["dataset_kind"] == "synthetic_runtime_governance"
    assert {row["domain"] for row in dataset["cases"]} == EXPECTED_DOMAINS
    assert len(dataset["cases"]) == 41
    assert dataset["isolation_contract"] == {
        "external_database_allowed": False,
        "sqlite_memory_allowed": True,
        "model_allowed": False,
        "business_documents_allowed": False,
        "embedding_reranker_allowed": False,
        "ocr_vision_allowed": False,
        "external_mcp_allowed": False,
        "ecs_allowed": False,
    }


def test_v608_runtime_profile_calibrates_bounded_loop_and_retry(dataset: dict) -> None:
    profile = dataset["runtime_profile"]

    assert profile["max_no_progress_actions"] == 2
    assert profile["max_retry_attempts"] == 2
    assert profile["max_answer_repairs"] == 2
    assert profile["max_parallel_read_calls"] == 1
    assert profile["model_timeout_ms"] == 30_000
    assert profile["tool_timeout_ms"] == 10_000


def test_v608_all_scenarios_and_gates_pass(result: dict) -> None:
    assert result["status"] == "passed"
    assert result["case_count"] == 41
    assert result["passed_count"] == 41
    assert result["pass_rate"] == 1.0
    assert all(result["gates"].values())
    assert all(row["passed"] for row in result["cases"])


def test_v608_budget_never_oversubscribes_and_settles_conservatively(
    result: dict,
) -> None:
    assert result["safety_metrics"]["budget_oversubscription_count"] == 0
    actual = {row["id"]: row["actual"] for row in result["cases"]}
    assert actual["BUD-02"] == "exhausted"
    assert actual["BUD-04"] == "conservative_settlement"
    assert actual["BUD-05"] == "overage_rejected"
    assert actual["BUD-06"] == "profile_expansion_rejected"


def test_v608_loop_warns_then_stops_and_resets_on_progress(result: dict) -> None:
    actual = {row["id"]: row["actual"] for row in result["cases"]}

    assert actual["LOOP-01"] == "pass"
    assert actual["LOOP-02"] == "warning"
    assert actual["LOOP-03"] == "stop"
    assert actual["LOOP-04"] == "repeat_warning"
    assert actual["LOOP-05"] == "cycle_warning"
    assert actual["LOOP-06"] == "progress_reset"
    assert result["safety_metrics"]["loop_stop_observation"] == 3


def test_v608_effect_fence_is_exactly_once(result: dict) -> None:
    assert result["safety_metrics"]["duplicate_effect_commit_count"] == 0
    actual = {row["id"]: row["actual"] for row in result["cases"]}
    assert actual["EFF-01"] == "reserve_new"
    assert actual["EFF-02"] == "reuse_result"
    assert actual["EFF-03"] == "await_existing"
    assert actual["EFF-04"] == "reconcile"
    assert actual["EFF-05"] == "scope_rejected"
    assert actual["EFF-06"] == "non_replayable_rejected"


def test_v608_cancel_and_persistence_isolate_late_results(result: dict) -> None:
    assert result["safety_metrics"]["late_result_acceptance_count"] == 0
    assert result["persistence_case_count"] == len(PERSISTENCE_CASE_IDS) == 4
    assert result["persistence_pass_rate"] == 1.0
    persistence = {
        row["id"]: row for row in result["cases"] if row["persistence"]
    }
    assert set(persistence) == PERSISTENCE_CASE_IDS
    assert all(row["passed"] for row in persistence.values())


def test_v608_direct_durable_boundary_is_exact(result: dict) -> None:
    assert result["safety_metrics"]["direct_durable_misclassification_count"] == 0
    actual = {row["id"]: row["actual"] for row in result["cases"]}
    assert actual["EXE-01"] == "direct"
    assert actual["EXE-02"] == "durable_by_duration"
    assert actual["EXE-03"] == "durable_by_requirements"
    assert actual["EXE-04"] == "parallel_limit_rejected"


def test_v608_recovery_never_replays_unsafe_effects(result: dict) -> None:
    assert result["safety_metrics"]["unsafe_recovery_replay_count"] == 0
    actual = {row["id"]: row["actual"] for row in result["cases"]}
    assert actual["REC-01"] == "terminal_no_action"
    assert actual["REC-03"] == "wait_for_user"
    assert actual["REC-04"] == "continue_accepted_observation"
    assert actual["REC-05"] == "consume_persisted_result"
    assert actual["REC-06"] == "reconcile_uncertain"
    assert actual["REC-07"] == "retry_safe"
    assert actual["REC-08"] == "retry_limit_blocked"
    assert actual["REC-11"] == "no_effect_continue"


def test_v608_result_exposes_only_local_isolation_summary(result: dict) -> None:
    assert result["isolation"] == {
        "database": "sqlite_memory_only",
        "model": False,
        "business_documents": False,
        "embedding_reranker": False,
        "ocr_vision": False,
        "external_mcp": False,
        "ecs": False,
    }
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "resume-token" not in serialized
    assert "lease_owner" not in serialized
    assert "effect-key" not in serialized


def test_v608_dataset_rejects_external_dependency_enablement(tmp_path: Path) -> None:
    payload = load_dataset(DATASET_PATH)
    payload["isolation_contract"]["model_allowed"] = True
    path = tmp_path / "invalid-v608.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(V608EvaluationError, match="external dependency"):
        load_dataset(path)
