from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "contracts" / "bid_assessment" / "v1"
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "bid_assessment" / "v1" / "contracts.schema.json"
TOOL_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "bid_assessment" / "v1" / "tools.schema.json"
SCHEMA_ROOT = PROJECT_ROOT / "schemas" / "bid_assessment" / "v1"
OPENAPI_PATH = PROJECT_ROOT / "openapi" / "bid-assessment-v1.openapi.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


MANIFEST = _load(CONTRACT_ROOT / "manifest.json")
STATE_TRANSITIONS = _load(CONTRACT_ROOT / "state-transitions.json")
ERROR_CODES = _load(CONTRACT_ROOT / "error-codes.json")
EVENT_CATALOG = _load(CONTRACT_ROOT / "event-catalog.json")
DECISION_COMPATIBILITY = _load(CONTRACT_ROOT / "decision-compatibility.json")
TASK_CATALOG = _load(CONTRACT_ROOT / "task-catalog.json")
SCHEMA_BUNDLE = _load(SCHEMA_PATH)
TOOL_SCHEMA = _load(TOOL_SCHEMA_PATH)
SCHEMAS = {path.name: _load(path) for path in SCHEMA_ROOT.glob("*.schema.json")}
SCHEMA_REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema))
    for schema in SCHEMAS.values()
)
OPENAPI = _load(OPENAPI_PATH)


def _validator(definition: str) -> Draft202012Validator:
    schema = {
        "$schema": SCHEMA_BUNDLE["$schema"],
        "$defs": SCHEMA_BUNDLE["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_enum(definition: str) -> set[str]:
    return set(SCHEMA_BUNDLE["$defs"][definition]["enum"])


def _cross_schema_validator(schema_file: str, definition: str | None = None) -> Draft202012Validator:
    target = SCHEMAS[schema_file]
    ref = target["$id"]
    if definition:
        ref = f"{ref}#/$defs/{definition}"
    return Draft202012Validator(
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": ref},
        registry=SCHEMA_REGISTRY,
        format_checker=FormatChecker(),
    )


def _version_binding() -> dict[str, Any]:
    return {
        "manifest_id": "mft_01",
        "scope_id": "scope_01",
        "enterprise_snapshot_version": "enterprise_v8",
        "rule_set_version": "rules_v1",
        "fact_catalog_version": "facts_v1",
        "prompt_bundle_version": "prompts_v1",
        "tool_registry_version": "tools_v1",
        "model_profile_version": "models_v1",
        "formula_catalog_version": "formulas_v1",
        "evaluation_time": "2026-08-10T03:01:00Z",
    }


def _operation_parameters(path_item: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
    resolved: list[dict[str, Any]] = []
    for parameter in parameters:
        ref = parameter.get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            resolved.append(OPENAPI["components"]["parameters"][name])
        else:
            resolved.append(parameter)
    return resolved


def _operations() -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    result: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for path, path_item in OPENAPI["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation:
                result.append((method, path, path_item, operation))
    return result


def _refs(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [value["$ref"]] if "$ref" in value else []
        for child in value.values():
            found.extend(_refs(child))
        return found
    if isinstance(value, list):
        found: list[str] = []
        for child in value:
            found.extend(_refs(child))
        return found
    return []


def _resolve_json_pointer(document: Any, fragment: str) -> Any:
    current = document
    if not fragment:
        return current
    assert fragment.startswith("/"), fragment
    for raw_token in fragment[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def test_manifest_points_to_existing_phase0_artifacts() -> None:
    assert MANIFEST["contract_id"] == "bid-assessment-v1"
    assert MANIFEST["source_spec"]["version"] == "v0.1-r12"
    assert MANIFEST["implementation_boundary"] == {
        "runtime_routes_registered": True,
        "database_migration_included": True,
        "legacy_runtime_modified": False,
    }
    for relative_path in MANIFEST["artifacts"].values():
        assert (PROJECT_ROOT / relative_path).is_file(), relative_path


def test_json_schema_bundle_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA_BUNDLE)
    assert SCHEMA_BUNDLE["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA_BUNDLE["$id"].endswith("/bid-assessment/v1/contracts.schema.json")


def test_every_machine_contract_schema_is_valid_and_has_a_unique_id() -> None:
    expected = {
        "contracts.schema.json",
        "tools.schema.json",
        "task.schema.json",
        "planner.schema.json",
        "fact.schema.json",
        "dimension.schema.json",
        "decision.schema.json",
        "report.schema.json",
        "context.schema.json",
        "model-roles.schema.json",
    }
    assert set(SCHEMAS) == expected
    schema_ids = [schema["$id"] for schema in SCHEMAS.values()]
    assert len(schema_ids) == len(set(schema_ids))
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_every_schema_reference_resolves_offline() -> None:
    schemas_by_id = {schema["$id"]: schema for schema in SCHEMAS.values()}
    for schema_name, schema in SCHEMAS.items():
        for ref in _refs(schema):
            target_ref, _, fragment = ref.partition("#")
            if not target_ref:
                target = schema
            elif target_ref.startswith("https://"):
                target = schemas_by_id[target_ref]
            else:
                target = SCHEMAS[target_ref]
            resolved = _resolve_json_pointer(target, fragment)
            assert isinstance(resolved, (dict, bool)), (schema_name, ref)


def test_all_25_model_visible_tool_schemas_are_valid_and_closed() -> None:
    Draft202012Validator.check_schema(TOOL_SCHEMA)
    tool_names = set(TOOL_SCHEMA["properties"]["tool_name"]["enum"])
    registry = TOOL_SCHEMA["x-tool-registry"]
    assert len(tool_names) == 25
    assert set(registry) == tool_names
    for tool_name, ref in registry.items():
        definition_name = ref.rsplit("/", 1)[-1]
        assert definition_name in TOOL_SCHEMA["$defs"], tool_name
        assert TOOL_SCHEMA["$defs"][definition_name]["additionalProperties"] is False


def test_standard_task_and_tool_catalogs_are_exact_and_cross_schema_consistent() -> None:
    task_types = [entry["task_type"] for entry in TASK_CATALOG["tasks"]]
    task_schema = SCHEMAS["task.schema.json"]
    assert len(task_types) == len(set(task_types)) == 49
    assert set(task_types) == set(task_schema["$defs"]["TaskType"]["enum"])
    assert set(task_schema["$defs"]["ToolName"]["enum"]) == set(
        TOOL_SCHEMA["properties"]["tool_name"]["enum"]
    )
    assert {entry["category"] for entry in TASK_CATALOG["tasks"]} == {
        "document_scope_version",
        "tender_fact_extraction",
        "enterprise_data",
        "hard_gate",
        "dimension_analysis",
        "synthesis_validation_report",
    }


def test_plan_proposal_is_bounded_and_cannot_contain_a_final_decision() -> None:
    validator = _cross_schema_validator("planner.schema.json", "PlanProposal")
    proposal = {
        "proposal_id": "proposal_01",
        "reason_codes": ["MISSING_ECONOMICS_FACTS"],
        "add_tasks": [],
        "supersede_tasks": [],
        "questions": [],
        "expected_stage_after": "synthesis",
        "planner_confidence": "high",
    }
    validator.validate(proposal)
    with pytest.raises(ValidationError):
        validator.validate({**proposal, "decision_class": "recommend"})
    too_many_tasks = dict(proposal)
    too_many_tasks["add_tasks"] = [
        {
            "task_key": f"inventory_documents:v{i}",
            "task_type": "inventory_documents",
            "objective": "盘点文件",
            "depends_on": [],
            "required_fact_slots": [],
            "tool_profile": "FACT_EXTRACTION",
            "context_profile": "FACT_EXTRACTION",
            "budget_profile": "STANDARD",
            "completion_contract": "inventory_v1",
        }
        for i in range(9)
    ]
    with pytest.raises(ValidationError):
        validator.validate(too_many_tasks)


def test_planner_status_summaries_keep_coverage_and_fact_quality_separate() -> None:
    coverage_validator = _cross_schema_validator("planner.schema.json", "CoverageCounts")
    resolved_validator = _cross_schema_validator("planner.schema.json", "ResolvedFactCounts")
    coverage = {
        "resolved": 38,
        "missing": 8,
        "unavailable": 1,
        "not_assessed": 0,
        "blocked_by_parent": 2,
        "not_applicable": 7,
        "stale": 0,
    }
    resolved = {"supported": 31, "partial": 5, "conflicted": 2}
    coverage_validator.validate(coverage)
    resolved_validator.validate(resolved)
    with pytest.raises(ValidationError):
        coverage_validator.validate({**coverage, "supported": 31})
    with pytest.raises(ValidationError):
        resolved_validator.validate({**resolved, "missing": 8})
    assert SCHEMAS["planner.schema.json"]["x-semantic-validations"]["checks"] == [
        "task_type_allowlist",
        "acyclic_dependencies",
        "scope_version_consistency",
        "tool_profile_permissions",
        "budget_limits",
        "max_8_dynamic_tasks",
        "max_dependency_depth_3",
        "hard_gate_ordering",
        "report_validation_ordering",
    ]


def test_local_research_tool_action_uses_the_same_strict_argument_contract() -> None:
    action_validator = _cross_schema_validator("model-roles.schema.json", "LocalResearchToolAction")
    action = {
        "action": "tool_call",
        "tool_name": "evidence.search",
        "arguments": {"query": "投标截止时间", "top_k": 5},
        "purpose_code": "FIND_DEADLINE_EVIDENCE",
    }
    action_validator.validate(action)
    with pytest.raises(ValidationError):
        action_validator.validate({**action, "arguments": {"query": "投标截止时间", "top_k": 9}})
    with pytest.raises(ValidationError):
        action_validator.validate({**action, "tool": {"tool_name": "evidence.search"}})

    calculation_validator = _cross_schema_validator("model-roles.schema.json", "CalculationRequest")
    calculation_validator.validate({
        "tool_name": "calculate.bid_labor_cost",
        "arguments": {"workload_calculation_id": "calc_workload_01", "rate_card_version": "rate_v1"},
    })
    with pytest.raises(ValidationError):
        calculation_validator.validate({
            "tool_name": "evidence.search",
            "arguments": {"query": "投标截止时间"},
        })


def test_tool_schema_rejects_old_fact_status_and_old_parameter_names() -> None:
    validator = Draft202012Validator(TOOL_SCHEMA, format_checker=FormatChecker())
    validator.validate({
        "tool_name": "facts.query",
        "arguments": {
            "fact_slots": ["tender.submission.deadline"],
            "statuses": ["supported"],
            "coverage_statuses": ["missing"],
        },
    })
    with pytest.raises(ValidationError):
        validator.validate({
            "tool_name": "facts.query",
            "arguments": {"fact_slots": ["tender.submission.deadline"], "statuses": ["missing"]},
        })
    with pytest.raises(ValidationError):
        validator.validate({
            "tool_name": "enterprise.projects.search",
            "arguments": {"query": "办公楼", "project_type": ["decoration"]},
        })
    with pytest.raises(ValidationError):
        validator.validate({
            "tool_name": "calculate.bid_labor_cost",
            "arguments": {"workload_lines": [], "rate_card_version": "rate_v1"},
        })


def test_tool_money_and_ratio_arguments_reject_json_numbers() -> None:
    validator = Draft202012Validator(TOOL_SCHEMA, format_checker=FormatChecker())
    valid = {
        "tool_name": "calculate.fund_occupation_cost",
        "arguments": {
            "amount": {"amount": "100000.0000", "currency": "CNY"},
            "start_date": "2026-08-10",
            "end_date": "2026-08-20",
            "annual_rate": "0.050000",
            "refundable": True,
        },
    }
    validator.validate(valid)
    invalid = json.loads(json.dumps(valid))
    invalid["arguments"]["annual_rate"] = 0.05
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_table_read_region_freezes_consistent_semantic_limits() -> None:
    limits = TOOL_SCHEMA["$defs"]["TablesReadRegionArguments"]["x-semantic-limits"]
    assert limits == {"max_rows": 10, "max_columns": 12, "max_cells": 120}


@pytest.mark.parametrize(
    ("definition", "accepted", "rejected"),
    [
        ("MoneyDecimalString", "100000.0000", 100000.0),
        ("MoneyDecimalString", "0.0000", "0.00"),
        ("NonnegativeDecimalString", "2.500000", -2.5),
        ("RatioDecimalString", "0.850000", 0.85),
        ("RatioDecimalString", "1.000000", "1.000001"),
    ],
)
def test_decimal_contracts_reject_json_numbers_and_invalid_precision(
    definition: str, accepted: Any, rejected: Any
) -> None:
    validator = _validator(definition)
    validator.validate(accepted)
    with pytest.raises(ValidationError):
        validator.validate(rejected)


def test_create_assessment_request_forbids_unknown_fields() -> None:
    validator = _validator("CreateAssessmentRequest")
    valid = {
        "title": "办公楼装饰项目投标研判",
        "client_name": "某甲方",
        "internal_note": None,
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate({**valid, "force_auto_approve": True})


def test_run_version_binding_is_complete_and_uses_fixed_utc_time() -> None:
    validator = _validator("VersionBinding")
    binding = {
        "manifest_id": "mft_01",
        "scope_id": "scope_01",
        "enterprise_snapshot_version": "enterprise_v8",
        "rule_set_version": "rules_v1",
        "fact_catalog_version": "facts_v1",
        "prompt_bundle_version": "prompts_v1",
        "tool_registry_version": "tools_v1",
        "model_profile_version": "models_v1",
        "formula_catalog_version": "formulas_v1",
        "evaluation_time": "2026-08-10T03:01:00Z",
    }
    validator.validate(binding)
    incomplete = dict(binding)
    incomplete.pop("formula_catalog_version")
    with pytest.raises(ValidationError):
        validator.validate(incomplete)
    with pytest.raises(ValidationError):
        validator.validate({**binding, "evaluation_time": "2026-08-10T11:01:00+08:00"})


def test_fact_coverage_and_resolved_fact_statuses_are_disjoint() -> None:
    coverage = _schema_enum("SlotCoverageStatus")
    resolved = _schema_enum("ResolvedFactStatus")
    assert coverage == {
        "not_assessed",
        "unavailable",
        "blocked_by_parent",
        "missing",
        "resolved",
        "not_applicable",
        "stale",
    }
    assert resolved == {"supported", "partial", "conflicted"}
    assert coverage.isdisjoint(resolved)


def test_fact_assertion_candidate_is_closed_and_document_assertions_need_evidence() -> None:
    validator = _cross_schema_validator("fact.schema.json", "FactAssertionCandidate")
    candidate = {
        "fact_slot": "tender.submission.deadline",
        "value": "2026-08-18T01:30:00Z",
        "value_type": "datetime",
        "scope": {"type": "lot", "id": "lot_01"},
        "source_type": "document",
        "evidence_ids": ["ev_01"],
        "confidence": "high",
        "asserted_at": "2026-08-10T03:01:00Z",
    }
    validator.validate(candidate)
    with pytest.raises(ValidationError):
        validator.validate({**candidate, "made_up_by_model": True})
    with pytest.raises(ValidationError):
        validator.validate({**candidate, "evidence_ids": []})
    money_candidate = {
        **candidate,
        "fact_slot": "commercial.price_cap",
        "value_type": "money",
        "value": {"amount": "100000.0000", "currency": "CNY"},
    }
    validator.validate(money_candidate)
    with pytest.raises(ValidationError):
        validator.validate({**money_candidate, "value": {"amount": 100000.0, "currency": "CNY"}})


def test_fact_slot_resolution_enforces_coverage_resolved_fact_invariant() -> None:
    validator = _cross_schema_validator("fact.schema.json", "FactSlotResolution")
    resolved_fact = {
        "resolved_fact_id": "rf_01",
        "run_id": "run_01",
        "fact_slot": "tender.submission.deadline",
        "scope": {"type": "lot", "id": "lot_01"},
        "status": "supported",
        "value": "2026-08-18T01:30:00Z",
        "value_type": "datetime",
        "assertions": [{"assertion_id": "ast_01", "disposition": "accepted"}],
        "resolution_rule": "CR3_TIME",
        "version": 1,
    }
    coverage = {
        "coverage_id": "coverage_01",
        "run_id": "run_01",
        "fact_slot": "tender.submission.deadline",
        "scope": {"type": "lot", "id": "lot_01"},
        "status": "resolved",
        "reason_codes": [],
        "version": 1,
        "current_resolved_fact_id": "rf_01",
    }
    validator.validate({"coverage": coverage, "resolved_fact": resolved_fact})
    with pytest.raises(ValidationError):
        validator.validate({"coverage": coverage, "resolved_fact": None})
    missing = {**coverage, "status": "missing", "current_resolved_fact_id": None}
    validator.validate({"coverage": missing, "resolved_fact": None})
    with pytest.raises(ValidationError):
        validator.validate({"coverage": missing, "resolved_fact": resolved_fact})


@pytest.mark.parametrize(
    ("rating", "score"),
    [
        ("strong_positive", 100),
        ("positive", 75),
        ("neutral", 50),
        ("negative", 25),
        ("strong_negative", 0),
        ("unknown", None),
    ],
)
def test_dimension_rating_score_mapping_is_deterministic(rating: str, score: int | None) -> None:
    validator = _cross_schema_validator("dimension.schema.json", "DimensionResult")
    result = {
        "dimension_code": "bid_investment",
        "rating": rating,
        "score": score,
        "sufficiency": "usable",
        "coverage": "0.860000",
        "positive_findings": [],
        "negative_findings": [],
        "unknown_fact_slots": [],
        "conditions": [],
        "summary_claim_id": None,
    }
    validator.validate(result)
    wrong_score = 74 if score != 74 else 73
    with pytest.raises(ValidationError):
        validator.validate({**result, "score": wrong_score})


def _decision(decision_class: str, investment_level: str) -> dict[str, Any]:
    return {
        "decision_id": "decision_01",
        "analysis_run_id": "run_01",
        "decision_class": decision_class,
        "investment_level": investment_level,
        "known_score": "75.000000",
        "known_weight": "0.850000",
        "coverage": "0.870000",
        "information_quality": "adequate",
        "conditions": [],
        "rule_version": "rules_v1",
        "bound_versions": _version_binding(),
        "input_hash": "a" * 64,
    }


def test_decision_schema_executes_the_frozen_compatibility_matrix() -> None:
    validator = _cross_schema_validator("decision.schema.json", "DecisionResult")
    allowed = {
        (decision, level)
        for decision, levels in DECISION_COMPATIBILITY["allowed_pairs"].items()
        for level in levels
    }
    all_pairs = {
        (decision, level)
        for decision in DECISION_COMPATIBILITY["decision_classes"]
        for level in DECISION_COMPATIBILITY["investment_levels"]
    }
    for pair in allowed:
        validator.validate(_decision(*pair))
    for pair in all_pairs - allowed:
        with pytest.raises(ValidationError):
            validator.validate(_decision(*pair))


def test_report_fact_claim_requires_resolved_fact_support_and_direct_citation() -> None:
    validator = _cross_schema_validator("report.schema.json", "Claim")
    claim = {
        "claim_id": "claim_01",
        "claim_type": "fact",
        "text": "投标截止时间为 2026 年 8 月 18 日。",
        "impact": "high",
        "supports": [{"support_type": "resolved_fact", "support_id": "rf_01"}],
        "citations": [{
            "citation_id": "citation_01",
            "evidence_id": "ev_01",
            "locator_snapshot": {
                "document_version_id": "docv_01",
                "locator_type": "page",
                "locator": "page=12",
            },
            "quote_hash": "b" * 64,
            "display_label": "招标文件第 12 页",
        }],
        "claim_hash": "c" * 64,
    }
    validator.validate(claim)
    with pytest.raises(ValidationError):
        validator.validate({**claim, "citations": []})
    with pytest.raises(ValidationError):
        validator.validate({**claim, "supports": [{"support_type": "finding", "support_id": "finding_01"}]})


def test_context_manifest_never_excludes_p0_or_p1_due_to_token_budget() -> None:
    validator = _cross_schema_validator("context.schema.json", "ContextManifest")
    manifest = {
        "context_manifest_id": "ctx_01",
        "task_id": "task_01",
        "role": "local_research",
        "bound_versions": _version_binding(),
        "included_fact_ids": [],
        "included_calculation_ids": [],
        "included_evidence": [],
        "dependency_output_ids": [],
        "excluded_due_to_budget": [{
            "resource_type": "evidence",
            "resource_id": "ev_02",
            "priority": "P3",
            "reason": "token_budget",
        }],
        "token_estimate": 14800,
        "compression_level": 1,
        "assembler_version": "assembler_v1",
        "hash": "d" * 64,
    }
    validator.validate(manifest)
    invalid = json.loads(json.dumps(manifest))
    invalid["excluded_due_to_budget"][0]["priority"] = "P1"
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_synthesizer_output_cannot_emit_final_decision_fields() -> None:
    validator = _cross_schema_validator("model-roles.schema.json", "SynthesizerOutput")
    output = {
        "cross_dimension_findings": [],
        "condition_candidates": [],
        "decision_tensions": [],
        "summary_claim_candidates": [],
        "unresolved_critical_unknowns": [],
        "cannot_synthesize": False,
    }
    validator.validate(output)
    with pytest.raises(ValidationError):
        validator.validate({**output, "decision_class": "recommend"})


@pytest.mark.parametrize(
    ("machine_name", "schema_definition"),
    [
        ("assessment_lifecycle", "AssessmentLifecycleStatus"),
        ("assessment_business", "AssessmentBusinessStatus"),
        ("upload_batch", "UploadBatchStatus"),
        ("analysis_run", "AnalysisRunStatus"),
        ("plan_revision", "PlanRevisionStatus"),
        ("task", "TaskStatus"),
        ("report", "ReportStatus"),
        ("question", "QuestionStatus"),
        ("async_operation", "AsyncOperationStatus"),
    ],
)
def test_state_machine_catalog_matches_schema_enums(
    machine_name: str, schema_definition: str
) -> None:
    machine = STATE_TRANSITIONS["state_machines"][machine_name]
    states = set(machine["states"])
    assert states == _schema_enum(schema_definition)
    assert set(machine["transitions"]) == states
    for source, targets in machine["transitions"].items():
        assert set(targets) <= states, source
    for terminal in machine["terminal_states"]:
        assert machine["transitions"][terminal] == []


def test_cancelled_assessment_is_recoverable_but_cancelled_run_is_terminal() -> None:
    assessment = STATE_TRANSITIONS["state_machines"]["assessment_business"]
    run = STATE_TRANSITIONS["state_machines"]["analysis_run"]
    assert set(assessment["transitions"]["cancelled"]) == {
        "preparing",
        "awaiting_files",
        "superseded",
    }
    assert run["transitions"]["cancelled"] == []
    assert "cancelled" in run["terminal_states"]


def test_upload_batch_can_return_from_ready_to_uploading() -> None:
    upload = STATE_TRANSITIONS["state_machines"]["upload_batch"]
    assert "uploading" in upload["transitions"]["ready"]
    assert "committed" in upload["transitions"]["ready"]


def test_api15_atomic_input_supersession_can_enter_preparing_directly() -> None:
    assessment = STATE_TRANSITIONS["state_machines"]["assessment_business"]
    for state in {
        "awaiting_lot_selection",
        "preliminary_analyzing",
        "preliminary_ready",
        "awaiting_owner_input",
        "deep_analyzing",
        "validating",
        "deep_ready",
        "stale_input",
        "failed",
        "cancelled",
    }:
        assert "preparing" in assessment["transitions"][state]
        assert "awaiting_files" in assessment["transitions"][state]
    run = STATE_TRANSITIONS["state_machines"]["analysis_run"]
    assert "stale" in run["transitions"]["failed"]


def test_error_catalog_is_unique_and_matches_error_schema() -> None:
    entries = ERROR_CODES["errors"]
    codes = [entry["code"] for entry in entries]
    assert len(codes) == len(set(codes))
    assert set(codes) == _schema_enum("BidErrorCode")
    assert all(re.fullmatch(r"BID_[A-Z0-9_]+", code) for code in codes)
    assert all(entry["http_status"] in {400, 401, 403, 404, 409, 412, 413, 415, 422, 428, 429, 503} for entry in entries)


def test_public_and_outbox_event_catalogs_are_stable_and_unique() -> None:
    public_types = [event["event_type"] for event in EVENT_CATALOG["public_events"]]
    outbox_types = EVENT_CATALOG["outbox_events"]
    assert len(public_types) == len(set(public_types))
    assert set(public_types) == _schema_enum("PublicEventType")
    assert len(outbox_types) == len(set(outbox_types))
    assert all(re.fullmatch(r"bid\.[a-z0-9_.]+\.v1", name) for name in outbox_types)
    assert "bid.run.cancel_requested.v1" in outbox_types
    assert "bid.run.cancelled.v1" in outbox_types


def test_decision_compatibility_matrix_has_only_frozen_pairs() -> None:
    assert set(DECISION_COMPATIBILITY["decision_classes"]) == _schema_enum("DecisionClass")
    assert set(DECISION_COMPATIBILITY["investment_levels"]) == _schema_enum("InvestmentLevel")
    allowed = {
        (decision, level)
        for decision, levels in DECISION_COMPATIBILITY["allowed_pairs"].items()
        for level in levels
    }
    assert allowed == {
        ("recommend", "limited"),
        ("recommend", "full"),
        ("conditional", "low_cost_verification"),
        ("conditional", "limited"),
        ("not_recommend", "stop"),
        ("not_recommend", "low_cost_verification"),
        ("insufficient", "stop"),
        ("insufficient", "low_cost_verification"),
    }
    assert ("recommend", "stop") not in allowed
    assert ("insufficient", "limited") not in allowed


def test_openapi_freezes_all_35_external_operations() -> None:
    operations = _operations()
    api_ids = [operation[3]["x-api-id"] for operation in operations]
    operation_ids = [operation[3]["operationId"] for operation in operations]
    assert OPENAPI["openapi"] == "3.1.0"
    assert OPENAPI["servers"] == [{"url": "/api/v1"}]
    assert len(operations) == 35
    assert len(api_ids) == len(set(api_ids)) == 35
    assert len(operation_ids) == len(set(operation_ids)) == 35
    assert set(api_ids) == {
        "API-01", "API-02", "API-03", "API-04",
        "API-10", "API-11", "API-12", "API-13", "API-14", "API-15", "API-16",
        "API-20", "API-21", "API-22",
        "API-30", "API-31", "API-32",
        "API-40", "API-41", "API-42", "API-43", "API-44",
        "API-50", "API-51", "API-52", "API-53",
        "API-60", "API-61", "API-62", "API-63",
        "API-70", "API-71", "API-72", "API-73", "API-80",
    }
    assert all("default" in operation[3]["responses"] for operation in operations)
    assert all(not path.startswith("/admin/bidding/") for _, path, _, _ in operations)


def test_every_openapi_path_placeholder_has_one_required_path_parameter() -> None:
    for method, path, path_item, operation in _operations():
        placeholders = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", path))
        parameters = _operation_parameters(path_item, operation)
        path_parameters = [parameter for parameter in parameters if parameter["in"] == "path"]
        names = [parameter["name"] for parameter in path_parameters]
        assert set(names) == placeholders, (method, path)
        assert len(names) == len(set(names)), (method, path)
        assert all(parameter["required"] is True for parameter in path_parameters), (method, path)


def test_every_state_changing_openapi_operation_requires_idempotency_key() -> None:
    for method, path, path_item, operation in _operations():
        if method not in {"post", "put", "patch", "delete"}:
            continue
        parameters = _operation_parameters(path_item, operation)
        headers = {parameter["name"]: parameter for parameter in parameters if parameter["in"] == "header"}
        assert headers["Idempotency-Key"]["required"] is True, (method, path)


def test_if_match_is_required_only_for_frozen_mutable_resource_commands() -> None:
    expected = {
        "API-04", "API-10", "API-13", "API-14", "API-15", "API-16",
        "API-31", "API-32", "API-40", "API-42", "API-43",
        "API-52", "API-53", "API-72", "API-80",
    }
    actual: set[str] = set()
    for _, _, path_item, operation in _operations():
        parameters = _operation_parameters(path_item, operation)
        if any(parameter.get("name") == "If-Match" for parameter in parameters):
            actual.add(operation["x-api-id"])
    assert actual == expected


def test_api12_freezes_flat_multipart_hash_and_specific_response_contract() -> None:
    path_item = OPENAPI["paths"]["/bid-upload-batches/{batch_id}/files"]
    operation = path_item["post"]
    parameters = _operation_parameters(path_item, operation)
    headers = {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter["in"] == "header"
    }
    assert headers["Idempotency-Key"]["required"] is True
    assert headers["X-Content-SHA256"]["required"] is False
    multipart = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert multipart["additionalProperties"] is False
    assert multipart["required"] == ["file", "client_file_id", "operation"]
    assert set(multipart["properties"]) == {
        "file",
        "client_file_id",
        "operation",
        "replace_document_id",
        "relative_path",
    }
    assert "metadata" not in multipart["properties"]
    assert operation["responses"]["201"] == {
        "$ref": "#/components/responses/UploadFileCreated"
    }
    response = OPENAPI["components"]["responses"]["UploadFileCreated"]
    assert set(response["headers"]) == {
        "Location",
        "ETag",
        "X-Resource-Version",
        "X-Batch-ETag",
        "X-Batch-Resource-Version",
        "Idempotent-Replay",
    }
    _validator("UploadFileResponse").validate(
        {
            "code": 200,
            "message": "文件已接收",
            "data": {
                "file": {
                    "batch_file_id": "file_01",
                    "client_file_id": "client_01",
                    "filename": "招标文件.pdf",
                    "status": "ready",
                    "size_bytes": 123,
                    "sha256": "a" * 64,
                    "row_version": 1,
                    "duplicate_of": None,
                },
                "batch": {
                    "batch_id": "batch_01",
                    "row_version": 2,
                    "can_commit": True,
                },
            },
            "error": None,
            "request_id": "request_01",
        }
    )


def test_api16_requires_reason_and_returns_versioned_abandonment_snapshot() -> None:
    path_item = OPENAPI["paths"]["/bid-upload-batches/{batch_id}/abandon"]
    operation = path_item["post"]
    assert operation["requestBody"]["required"] is True
    request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert request_ref == "#/components/schemas/AbandonUploadBatchRequest"
    assert operation["responses"]["200"]["$ref"] == (
        "#/components/responses/UploadBatchAbandoned"
    )
    request_validator = _validator("AbandonUploadBatchRequest")
    request_validator.validate({"reason": "用户重新整理资料"})
    with pytest.raises(ValidationError):
        request_validator.validate({})
    with pytest.raises(ValidationError):
        request_validator.validate({"reason": ""})
    with pytest.raises(ValidationError):
        request_validator.validate({"reason": "ok", "force_delete": True})
    snapshot = SCHEMA_BUNDLE["$defs"]["UploadBatchSnapshot"]
    assert {
        "abandon_reason",
        "abandoned_at",
        "cleanup_after",
        "cleanup_completed_at",
    } <= set(snapshot["required"])
    assert "bid.upload_batch.abandoned.v1" in EVENT_CATALOG["outbox_events"]


def test_api20_freezes_manifest_filters_private_cache_and_safe_projection() -> None:
    path_item = OPENAPI["paths"]["/bid-assessments/{assessment_id}/documents"]
    operation = path_item["get"]
    parameters = _operation_parameters(path_item, operation)
    query_parameters = {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter["in"] == "query"
    }
    headers = {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter["in"] == "header"
    }
    assert set(query_parameters) == {
        "manifest_id",
        "document_type",
        "parse_status",
        "include_versions",
        "page",
        "page_size",
    }
    assert query_parameters["page_size"]["schema"]["maximum"] == 100
    assert query_parameters["include_versions"]["schema"]["default"] is False
    assert set(query_parameters["parse_status"]["schema"]["enum"]) == {
        "not_requested",
        "queued",
        "running",
        "succeeded",
        "partial",
        "failed",
    }
    assert headers["If-None-Match"]["required"] is False
    assert operation["responses"]["200"] == {
        "$ref": "#/components/responses/DocumentPageOk"
    }
    assert "304" in operation["responses"]
    response_contract = OPENAPI["components"]["responses"]["DocumentPageOk"]
    assert response_contract["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-cache, max-age=0, must-revalidate"
    )
    assert response_contract["headers"]["Vary"]["schema"]["const"] == (
        "Authorization"
    )

    value = {
        "code": 200,
        "message": "ok",
        "data": [
            {
                "document_id": "document_01",
                "logical_name": "招标文件",
                "document_type": "tender_document",
                "role": "tender_document",
                "order_no": 0,
                "selected_version": {
                    "version_id": "version_01",
                    "version_no": 1,
                    "filename": "招标文件.pdf",
                    "size_bytes": 123,
                    "mime_type": "application/pdf",
                    "sha256_prefix": "a" * 12,
                    "created_at": "2026-08-11T01:00:00Z",
                    "detail_url": "/api/v1/bid-document-versions/version_01",
                    "download_url": "/api/v1/bid-document-versions/version_01/download",
                },
                "current_version": None,
                "parse_status": "not_requested",
                "parse_quality": None,
                "is_in_current_manifest": False,
                "replacement_chain": {
                    "previous_version_id": None,
                    "next_version_id": None,
                    "latest_version_id": "version_01",
                    "visible_version_count": 1,
                },
                "warnings": [],
                "versions": None,
            }
        ],
        "error": None,
        "request_id": "request_01",
        "total": 1,
        "page": 1,
        "page_size": 20,
        "manifest": {
            "manifest_id": "manifest_01",
            "version": 1,
            "document_count": 1,
            "committed_at": "2026-08-11T01:00:00Z",
            "is_current": False,
        },
        "current_manifest_id": None,
        "manifest_selection": "explicit",
        "filters": {"document_type": None, "parse_status": None},
        "include_versions": False,
    }
    _validator("DocumentPageResponse").validate(value)
    serialized_schema = json.dumps(
        SCHEMA_BUNDLE["$defs"]["DocumentSummary"],
        ensure_ascii=False,
    )
    for forbidden in {
        "object_key",
        "storage_etag",
        "file_object_id",
        "source_metadata_json",
        "source_metadata_hash",
        "parser_hint",
        "logical_identity_key",
        "created_by",
    }:
        assert forbidden not in serialized_schema


def test_api13_freezes_file_etag_empty_204_and_batch_version_headers() -> None:
    path_item = OPENAPI["paths"][
        "/bid-upload-batches/{batch_id}/files/{file_id}"
    ]
    operation = path_item["delete"]
    parameters = _operation_parameters(path_item, operation)
    headers = {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter["in"] == "header"
    }
    assert set(headers) == {"Idempotency-Key", "If-Match"}
    assert headers["Idempotency-Key"]["required"] is True
    assert headers["If-Match"]["required"] is True
    assert operation["responses"]["204"] == {
        "$ref": "#/components/responses/UploadFileDeleted"
    }
    response = OPENAPI["components"]["responses"]["UploadFileDeleted"]
    assert "content" not in response
    assert set(response["headers"]) == {
        "X-Batch-ETag",
        "X-Batch-Resource-Version",
        "Idempotent-Replay",
        "Cache-Control",
    }
    assert response["headers"]["X-Batch-ETag"]["required"] is True
    assert response["headers"]["X-Batch-Resource-Version"]["required"] is True
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-store"
    )

    file_schema = SCHEMA_BUNDLE["$defs"]["UploadBatchFile"]
    assert {"row_version", "etag"} <= set(file_schema["required"])
    assert file_schema["properties"]["etag"]["pattern"].startswith(
        '^"bid-upload-file:'
    )


def test_api14_freezes_atomic_document_set_and_batch_snapshot_response() -> None:
    request_schema = SCHEMA_BUNDLE["$defs"]["DeactivationRequest"]
    assert request_schema["required"] == ["document_ids", "reason"]
    document_ids = request_schema["properties"]["document_ids"]
    assert document_ids["minItems"] == 1
    assert document_ids["maxItems"] == 100
    assert document_ids["uniqueItems"] is True
    _validator("DeactivationRequest").validate(
        {
            "document_ids": ["document_01", "document_02"],
            "reason": "补遗已明确附件不再适用",
        }
    )

    path_item = OPENAPI["paths"][
        "/bid-upload-batches/{batch_id}/deactivations"
    ]
    operation = path_item["post"]
    assert operation["responses"]["201"] == {
        "$ref": "#/components/responses/UploadBatchDeactivationCreated"
    }
    response = OPENAPI["components"]["responses"][
        "UploadBatchDeactivationCreated"
    ]
    assert set(response["headers"]) == {
        "Location",
        "ETag",
        "X-Resource-Version",
        "Idempotent-Replay",
        "Cache-Control",
    }
    assert all(
        response["headers"][name]["required"] is True
        for name in ("Location", "ETag", "X-Resource-Version", "Cache-Control")
    )
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-store"
    )


def test_api15_freezes_commit_confirmation_and_authoritative_recovery_response() -> None:
    request_schema = SCHEMA_BUNDLE["$defs"]["CommitUploadBatchRequest"]
    assert request_schema["required"] == [
        "expected_file_count",
        "expected_deactivation_count",
        "change_note",
        "confirm_start_analysis",
    ]
    assert request_schema["properties"]["expected_file_count"]["minimum"] == 0
    assert (
        request_schema["properties"]["expected_deactivation_count"]["minimum"]
        == 0
    )
    assert request_schema["properties"]["confirm_start_analysis"]["const"] is True
    _validator("CommitUploadBatchRequest").validate(
        {
            "expected_file_count": 0,
            "expected_deactivation_count": 1,
            "change_note": "仅停用旧附件",
            "confirm_start_analysis": True,
        }
    )

    operation = OPENAPI["paths"]["/bid-upload-batches/{batch_id}/commit"]["post"]
    assert operation["responses"]["202"] == {
        "$ref": "#/components/responses/UploadBatchCommitted"
    }
    response = OPENAPI["components"]["responses"]["UploadBatchCommitted"]
    assert set(response["headers"]) == {
        "Location",
        "ETag",
        "X-Resource-Version",
        "X-Batch-ETag",
        "X-Batch-Resource-Version",
        "Idempotent-Replay",
        "Cache-Control",
    }
    assert all(
        response["headers"][name]["required"] is True
        for name in (
            "Location",
            "ETag",
            "X-Resource-Version",
            "X-Batch-ETag",
            "X-Batch-Resource-Version",
            "Cache-Control",
        )
    )
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-store"
    )


def test_openapi_external_schema_references_resolve_to_bundle_definitions() -> None:
    definitions = SCHEMA_BUNDLE["$defs"]
    for name, schema in OPENAPI["components"]["schemas"].items():
        ref = schema["$ref"]
        relative_path, fragment = ref.split("#", 1)
        assert (OPENAPI_PATH.parent / relative_path).resolve() == SCHEMA_PATH.resolve(), name
        assert fragment.startswith("/$defs/"), name
        assert fragment.rsplit("/", 1)[-1] in definitions, name
    for name, schema in OPENAPI["components"]["schemas"].items():
        if name.endswith("Request"):
            definition = schema["$ref"].rsplit("/", 1)[-1]
            assert definitions[definition]["additionalProperties"] is False


def test_dangerous_action_codes_are_not_exposed() -> None:
    actions = _schema_enum("ActionCode")
    assert "run.view_progress" in actions
    assert actions.isdisjoint({
        "report.edit",
        "report.delete",
        "evidence.delete",
        "gate.force_pass",
        "project.auto_approve",
        "bid.auto_submit",
    })
