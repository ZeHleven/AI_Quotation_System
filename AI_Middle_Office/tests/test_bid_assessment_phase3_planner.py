from __future__ import annotations

import copy

import pytest

from app.services.bid_plan_commit import (
    BidPlanValidationError,
    build_bootstrap_plan_proposal,
    validate_plan_proposal,
)
from app.services.bid_task_registry import load_standard_task_registry


def _planner_input() -> dict:
    registry = load_standard_task_registry()
    return {
        "assessment": {
            "id": "assessment_01",
            "goal": "bid_go_no_go",
            "scope_id": "scope_01",
        },
        "bound_versions": {
            "manifest_id": "manifest_01",
            "manifest_version": 1,
            "scope_id": "scope_01",
            "scope_version": 1,
            "enterprise_snapshot_version": "enterprise_v1",
            "rule_set_version": "rules_v1",
            "fact_catalog_version": "facts_v1",
            "prompt_bundle_version": "prompts_v1",
            "tool_registry_version": "tools_v1",
            "model_profile_version": "models_v1",
            "formula_catalog_version": "formulas_v1",
            "evaluation_time": "2026-08-11T10:00:00Z",
        },
        "workflow_stage": "planning",
        "document_inventory": [],
        "fact_slot_summary": {
            "coverage": {
                "resolved": 0,
                "missing": 0,
                "unavailable": 0,
                "not_assessed": 0,
                "blocked_by_parent": 0,
                "not_applicable": 0,
                "stale": 0,
            },
            "resolved_facts": {"supported": 0, "partial": 0, "conflicted": 0},
        },
        "gate_summary": [],
        "task_summary": [],
        "open_questions": [],
        "allowed_task_types": list(registry.task_order),
        "planning_limits": {"max_dynamic_tasks": 8, "max_dependency_depth": 3},
    }


def test_standard_registry_and_bootstrap_proposal_are_deterministic() -> None:
    registry = load_standard_task_registry()
    planner_input = _planner_input()
    first = build_bootstrap_plan_proposal(planner_input, registry=registry)
    second = build_bootstrap_plan_proposal(planner_input, registry=registry)

    assert len(registry.task_order) == 49
    assert set(registry.task_order) == set(registry.policies)
    assert all(policy.allowed_tools for policy in registry.policies.values())
    assert first == second
    assert len(first["add_tasks"]) == 8
    assert first["supersede_tasks"] == []
    assert first["expected_stage_after"] == "fact_baseline"
    result = validate_plan_proposal(
        planner_input,
        first,
        run_input_hash="a" * 64,
        registry=registry,
    )
    assert result.normalized_proposal == first
    assert len(result.validated_hash) == 64
    assert set(result.checks) == {
        "task_type_allowlist",
        "acyclic_dependencies",
        "scope_version_consistency",
        "tool_profile_permissions",
        "budget_limits",
        "max_8_dynamic_tasks",
        "max_dependency_depth_3",
        "hard_gate_ordering",
        "report_validation_ordering",
    }


@pytest.mark.parametrize(
    ("mutator", "violation"),
    [
        (
            lambda proposal: proposal["add_tasks"][0].update(
                {"tool_profile": "UNREGISTERED_PROFILE"}
            ),
            "tool_profile_permissions",
        ),
        (
            lambda proposal: proposal["add_tasks"][0].update(
                {"depends_on": [proposal["add_tasks"][1]["task_key"]]}
            ),
            "acyclic_dependencies",
        ),
        (
            lambda proposal: proposal["add_tasks"].append(
                copy.deepcopy(proposal["add_tasks"][-1])
            ),
            "max_8_dynamic_tasks",
        ),
    ],
)
def test_validator_rejects_profile_cycle_and_task_budget_bypass(
    mutator,
    violation: str,
) -> None:
    planner_input = _planner_input()
    proposal = build_bootstrap_plan_proposal(planner_input)
    mutator(proposal)

    with pytest.raises(BidPlanValidationError) as error:
        validate_plan_proposal(
            planner_input,
            proposal,
            run_input_hash="a" * 64,
        )
    assert any(violation in item for item in error.value.violations)


def test_validator_rejects_hard_gate_and_report_order_bypass() -> None:
    registry = load_standard_task_registry()
    planner_input = _planner_input()
    gate = registry.policies["evaluate_deadline_gate"].definition(
        task_key="gate.without.fact.conflict",
    )
    report = registry.policies["generate_preliminary_report"].definition(
        task_key="report.without.validation",
    )
    proposal = {
        "proposal_id": "planp_ordering",
        "reason_codes": ["ORDERING_TEST"],
        "add_tasks": [gate, report],
        "supersede_tasks": [],
        "questions": [],
        "expected_stage_after": "hard_gate",
        "planner_confidence": "high",
    }

    with pytest.raises(BidPlanValidationError) as error:
        validate_plan_proposal(
            planner_input,
            proposal,
            run_input_hash="b" * 64,
            registry=registry,
        )
    assert any("hard_gate_ordering" in item for item in error.value.violations)
    assert any("report_validation_ordering" in item for item in error.value.violations)
