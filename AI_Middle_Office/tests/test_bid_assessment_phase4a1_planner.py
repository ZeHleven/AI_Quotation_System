from __future__ import annotations

import copy

import pytest

from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_plan_commit import validate_plan_proposal
from app.services.bid_plan_continuation import (
    PHASE4_TASK_CATALOG_REF,
    STAGES,
    build_stage_plan_proposal,
)
from app.services.bid_skill_registry import (
    load_skill_catalog,
    verify_frozen_skill_binding,
)
from app.services.bid_task_registry import load_standard_task_registry


def _planner_input() -> dict:
    registry = load_standard_task_registry(PHASE4_TASK_CATALOG_REF)
    return {
        "assessment": {
            "id": "assessment_01",
            "goal": "bid_go_no_go",
            "scope_id": "scope_01",
        },
        "run_kind": "preliminary",
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
            "evaluation_time": "2026-08-12T10:00:00Z",
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


def test_phase4a1_stage_templates_are_deterministic_bounded_and_ordered() -> None:
    registry = load_standard_task_registry(PHASE4_TASK_CATALOG_REF)
    skill_catalog = load_skill_catalog()
    planner_input = _planner_input()
    existing_tasks: list[dict] = []
    existing_dependencies: list[tuple[str, str]] = []
    observed_counts: list[int] = []

    for stage in STAGES:
        first = build_stage_plan_proposal(
            planner_input,
            stage_code=stage.code,
            registry=registry,
            skill_catalog=skill_catalog,
        )
        second = build_stage_plan_proposal(
            planner_input,
            stage_code=stage.code,
            registry=registry,
            skill_catalog=skill_catalog,
        )
        assert first == second
        observed_counts.append(len(first["add_tasks"]))
        assert len(first["add_tasks"]) <= 8
        if stage.code != "P0":
            assert all(item["depends_on"] for item in first["add_tasks"])

        for definition in first["add_tasks"]:
            policy = registry.policies[definition["task_type"]]
            expected_binding = skill_catalog.binding_for_task(
                definition["task_type"]
            ).as_dict()
            assert definition["skill_binding"] == expected_binding
            assert definition["allowed_tools"] == list(policy.allowed_tools)
            assert expected_binding["output_schema"] == definition["completion_contract"]

        stripped = {
            **first,
            "add_tasks": [
                {
                    key: value
                    for key, value in definition.items()
                    if key not in {"skill_binding", "allowed_tools"}
                }
                for definition in first["add_tasks"]
            ],
        }
        validation = validate_plan_proposal(
            planner_input,
            stripped,
            run_input_hash="a" * 64,
            registry=registry,
            existing_tasks=existing_tasks,
            existing_dependencies=existing_dependencies,
        )
        assert validation.normalized_proposal == stripped

        for definition in stripped["add_tasks"]:
            task_key = definition["task_key"]
            existing_tasks.append(
                {
                    "id": task_key,
                    "task_key": task_key,
                    "task_type": definition["task_type"],
                    "status": "succeeded",
                }
            )
            existing_dependencies.extend(
                (task_key, dependency) for dependency in definition["depends_on"]
            )

    assert observed_counts == [8, 6, 7, 4, 1]
    assert len(existing_tasks) == 26


def test_skill_catalog_is_content_addressed_and_tampering_fails_closed() -> None:
    catalog = load_skill_catalog()
    assert catalog.version == "bid-assessment-mvp-skills@1.0.0"
    assert len(catalog.artifacts) == 8
    assert len(catalog.task_index) == 26
    assert len(catalog.catalog_hash) == 64

    binding = catalog.binding_for_task("extract_tender_overview").as_dict()
    verified = verify_frozen_skill_binding(
        catalog_ref=catalog.catalog_ref,
        catalog_version=catalog.version,
        catalog_hash=catalog.catalog_hash,
        task_type="extract_tender_overview",
        binding=binding,
    )
    assert verified.as_dict() == binding

    tampered = copy.deepcopy(binding)
    tampered["skill_hash"] = canonical_hash({"tampered": True})
    with pytest.raises(RuntimeError, match="BID_SKILL_BINDING_HASH_MISMATCH"):
        verify_frozen_skill_binding(
            catalog_ref=catalog.catalog_ref,
            catalog_version=catalog.version,
            catalog_hash=catalog.catalog_hash,
            task_type="extract_tender_overview",
            binding=tampered,
        )
