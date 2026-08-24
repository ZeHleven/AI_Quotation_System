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
PHASE3_RUNTIME_PROFILE = _load(CONTRACT_ROOT / "phase3-runtime-profile.json")
PHASE4A1_RUNTIME_PROFILE = _load(CONTRACT_ROOT / "phase4a1-runtime-profile.json")
PHASE4A2_RUNTIME_PROFILE = _load(CONTRACT_ROOT / "phase4a2-runtime-profile.json")
PHASE4B1_DEEPSEEK_PROFILE = _load(
    CONTRACT_ROOT / "phase4b1-deepseek-v4-flash-profile.json"
)
PHASE4B2_DEEPSEEK_MVP1_PROFILE = _load(
    CONTRACT_ROOT / "phase4b2-deepseek-isolated-mvp1-profile.json"
)
RQ2B_CANDIDATE_FUSION_PROFILE = _load(
    CONTRACT_ROOT / "rq2b-candidate-fusion-profile.json"
)
RQ2C_LIGHTWEIGHT_RERANK_PROFILE = _load(
    CONTRACT_ROOT / "rq2c-lightweight-rerank-profile.json"
)
RQ2_CLOSEOUT_PROFILE = _load(
    CONTRACT_ROOT / "rq2-closeout-cross-project-profile.json"
)
PHASE4C1_ENTERPRISE_HARD_GATES_PROFILE = _load(
    CONTRACT_ROOT / "phase4c1-enterprise-hard-gates-profile.json"
)
PHASE4C2_ENTERPRISE_BASELINE_ACCEPTANCE_PROFILE = _load(
    CONTRACT_ROOT / "phase4c2-enterprise-baseline-acceptance-profile.json"
)
PHASE4C3_MVP_RELEASE_CANDIDATE_PROFILE = _load(
    CONTRACT_ROOT / "phase4c3-mvp-release-candidate-profile.json"
)
PHASE4D1_BUSINESS_BASELINE_REVALIDATION_PROFILE = _load(
    CONTRACT_ROOT / "phase4d1-business-baseline-revalidation-profile.json"
)
PHASE4D2_ENTERPRISE_EVIDENCE_IMPORT_PROFILE = _load(
    CONTRACT_ROOT / "phase4d2-enterprise-evidence-import-profile.json"
)
PHASE4D3_FACT_VERIFICATION_PROFILE = _load(
    CONTRACT_ROOT / "phase4d3-fact-verification-comparability-profile.json"
)
SKILL_CATALOG = _load(CONTRACT_ROOT / "skills" / "catalog-1.0.0.json")
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
        "manifest_version": 3,
        "scope_id": "scope_01",
        "scope_version": 1,
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
    assert MANIFEST["source_spec"]["version"] == "v0.1-r62"
    assert MANIFEST["status"] == (
        "phase4d3_locally_validated_isolated_not_for_ecs"
    )
    assert MANIFEST["implementation_boundary"] == {
        "runtime_routes_registered": True,
        "database_migration_included": True,
        "legacy_runtime_modified": False,
    }
    for relative_path in MANIFEST["artifacts"].values():
        assert (PROJECT_ROOT / relative_path).is_file(), relative_path


def test_phase4a1_profile_and_skill_artifacts_freeze_the_slice_boundary() -> None:
    assert PHASE4A1_RUNTIME_PROFILE == {
        "schema": "bid-assessment-phase4a1-runtime-profile-v1",
        "profile_id": "bid-assessment-phase4a1-plan-continuation-skill-binding-v1",
        "source_spec_version": "v0.1-r28",
        "status": "verified_local_isolated",
        "master_feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_MVP",
        "slice_feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_PLAN_CONTINUATION",
        "requires": ["FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME"],
        "stages": ["P0", "P1", "P2", "P3", "P4"],
        "continuation_event": "bid.plan.continuation_requested.v1",
        "plan_envelope_schema": "bid.plan.commit.envelope.v2",
        "planner_version": "bid-deterministic-stage-planner-v2",
        "validator_version": "bid-plan-validator-v2",
        "run_validator_version": "bid-run-integrity-validator-v3",
        "skill_catalog": "contracts/bid_assessment/v1/skills/catalog-1.0.0.json",
        "task_catalog": "contracts/bid_assessment/v1/task-catalog-1.0.0-draft.1.json",
        "migration_head": "20260812_0098",
        "external_execution": {
            "real_model": False,
            "ocr_or_visual": False,
            "mcp": False,
            "public_network": False,
            "real_external_tool": False,
            "real_object_storage": False,
        },
    }
    assert SKILL_CATALOG["schema"] == "bid.skill.catalog.v1"
    assert SKILL_CATALOG["catalog_version"] == "1.0.0"
    assert len(SKILL_CATALOG["artifacts"]) == 8
    for filename in SKILL_CATALOG["artifacts"]:
        artifact = _load(CONTRACT_ROOT / "skills" / filename)
        assert artifact["schema"] == "bid.skill.artifact.v1"
        assert artifact["skill_id"]
        assert artifact["skill_version"]
        assert artifact["executor_kind"] in {"deterministic", "langgraph"}
        assert artifact["task_bindings"]
        for task_type, binding in artifact["task_bindings"].items():
            assert task_type
            assert set(binding) == {"action_contract", "output_schema"}
            assert binding["action_contract"]
            assert binding["output_schema"]


def test_phase4a2_profile_freezes_model_and_bounded_executor_boundary() -> None:
    assert PHASE4A2_RUNTIME_PROFILE == {
        "schema": "bid-assessment-phase4a2-runtime-profile-v1",
        "profile_id": "bid-assessment-phase4a2-model-langgraph-executor-v1",
        "source_spec_version": "v0.1-r30",
        "status": "verified_local_isolated",
        "master_feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_MVP",
        "slice_feature_flags": [
            "FEATURE_BID_ASSESSMENT_PHASE4_LOCAL_AGENT",
            "FEATURE_BID_ASSESSMENT_PHASE4_MODEL_EXECUTOR",
        ],
        "requires": [
            "FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME",
            "FEATURE_BID_ASSESSMENT_PHASE4_PLAN_CONTINUATION",
        ],
        "local_state_schema": "bid.local_agent.state.v1",
        "model_action_schema": "bid.task.action.v1",
        "executor_version": "bid-bounded-langgraph-executor-v1",
        "run_validator_version": "bid-run-integrity-validator-v4",
        "authority": [
            "bid_model_calls",
            "bid_model_call_attempts",
            "bid_model_results",
        ],
        "checkpoint_authority": "bid_checkpoints",
        "migration_head": "20260813_0099",
        "external_execution": {
            "provider_registered": False,
            "real_model": False,
            "ocr_or_visual": False,
            "mcp": False,
            "public_network": False,
            "real_external_tool": False,
            "real_object_storage": False,
        },
    }


def test_phase4b1_profile_freezes_deepseek_v4_flash_boundary() -> None:
    profile = PHASE4B1_DEEPSEEK_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4b1-deepseek-provider-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r46"
    assert profile["status"] == "validated_local_and_minimal_live"
    assert profile["feature_flag"] == (
        "FEATURE_BID_ASSESSMENT_PHASE4_DEEPSEEK_ADAPTER"
    )
    assert profile["provider"] == {
        "provider_ref": "deepseek",
        "adapter_kind": "openai_compatible_chat_completions",
        "official_host": "api.deepseek.com",
        "allowed_paths": ["/chat/completions", "/v1/chat/completions"],
        "https_required": True,
        "api_key_source": "BID_ASSESSMENT_MODEL_API_KEY_or_DEEPSEEK_API_KEY",
        "idempotency_header": "X-Idempotency-Key",
    }
    assert profile["model"] == {
        "model_ref": "deepseek-v4-flash",
        "thinking_mode": "disabled",
        "temperature": 0,
        "response_format": "json_object",
        "context_window_tokens": 1_048_576,
        "model_action_schema": "bid.task.action.v1",
    }
    assert profile["governance"]["context_manifest_only"] is True
    assert profile["governance"]["reasoning_content_persisted"] is False
    assert profile["cost_schedule"] == {
        "currency": "USD",
        "unit": "microunits_per_million_tokens",
        "price_version": "2026-08-16",
        "input_cache_hit": 2_800,
        "input_cache_miss": 140_000,
        "output": 280_000,
        "missing_cache_breakdown_policy": "all_prompt_tokens_as_cache_miss",
    }
    assert profile["local_lab"]["default_mode"] == "deterministic"
    assert profile["local_lab"]["opt_in_mode"] == "deepseek-v4-flash"
    assert profile["compatibility"] == {
        "new_database_migration_required": False,
        "alembic_head": "20260815_0103",
        "legacy_bid_intake_modified": False,
        "ecs_rollout_allowed": False,
    }


def test_phase4b2_profile_freezes_isolated_live_mvp1_result() -> None:
    profile = PHASE4B2_DEEPSEEK_MVP1_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4b2-deepseek-isolated-mvp1-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r47"
    assert profile["status"] == "validated_isolated_live_synthetic_full_chain"
    assert profile["local_lab"] == {
        "profile_version": "mvp1-local-deepseek-v4-flash-1.0.1",
        "bind_host": "127.0.0.1",
        "port": 9002,
        "database_kind": "sqlite",
        "object_storage_kind": "local",
        "queue_kind": "in_process",
        "isolated_root": ".local-mvp1-ds-b2",
        "synthetic_text_only": True,
    }
    assert profile["validated_run"]["run_status"] == "succeeded"
    assert profile["validated_run"]["report_status"] == "ready"
    assert profile["validated_run"]["task_count"] == 26
    assert profile["validated_run"]["model_call_count"] == 31
    assert profile["validated_run"]["tool_call_count"] == 20
    assert profile["validated_run"]["run_validation_failed_check_count"] == 0
    assert profile["usage_ledger"]["logical_call_cost_microunits"] == 11_430
    assert profile["usage_ledger"]["rejected_response_cost_microunits"] == 479
    assert profile["boundary"]["official_deepseek_https_only"] is True
    assert profile["boundary"]["external_mcp_used"] is False
    assert profile["boundary"]["ecs_rollout_allowed"] is False
    assert profile["compatibility"]["alembic_head"] == "20260815_0103"


def test_phase3_closeout_profile_freezes_full_chain_and_external_boundary() -> None:
    assert PHASE3_RUNTIME_PROFILE["schema"] == (
        "bid-assessment-phase3-runtime-profile-v1"
    )
    assert PHASE3_RUNTIME_PROFILE["source_spec_version"] == "v0.1-r25"
    assert PHASE3_RUNTIME_PROFILE["status"] == "verified_local_isolated"
    assert PHASE3_RUNTIME_PROFILE["master_feature_flag"] == (
        "FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME"
    )
    assert PHASE3_RUNTIME_PROFILE["required_feature_flags"] == [
        "FEATURE_BID_ASSESSMENT_V1_RUNTIME",
        "FEATURE_BID_ASSESSMENT_PHASE3_RUN_BOOTSTRAP",
        "FEATURE_BID_ASSESSMENT_PHASE3_PLANNER",
        "FEATURE_BID_ASSESSMENT_PHASE3_TASK_RUNTIME",
        "FEATURE_BID_ASSESSMENT_PHASE3_RUN_LIFECYCLE",
        "FEATURE_BID_ASSESSMENT_PHASE3_TOOL_CONTEXT",
        "FEATURE_BID_ASSESSMENT_PHASE3_TOOL_EXECUTOR",
        "FEATURE_BID_ASSESSMENT_PHASE3_RUN_VALIDATION",
    ]
    assert [stage["phase"] for stage in PHASE3_RUNTIME_PROFILE["ordered_stages"]] == [
        "3A", "3B", "3C", "3D", "3E", "3F", "3G"
    ]
    assert PHASE3_RUNTIME_PROFILE["migration_head"] == "20260812_0097"
    assert PHASE3_RUNTIME_PROFILE["run_validator_version"] == (
        "bid-run-integrity-validator-v2"
    )
    assert all(PHASE3_RUNTIME_PROFILE["terminal_invariants"].values())
    execution = PHASE3_RUNTIME_PROFILE["external_execution"]
    assert execution["enabled_adapters"] == ["documents.outline@local_readonly"]
    assert all(
        execution[key] is False
        for key in (
            "real_model",
            "ocr_or_visual",
            "public_network",
            "real_external_tool",
            "real_object_storage",
        )
    )


def test_json_schema_bundle_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA_BUNDLE)
    assert SCHEMA_BUNDLE["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA_BUNDLE["$id"].endswith("/bid-assessment/v1/contracts.schema.json")


def test_rq2b_candidate_fusion_profile_freezes_v6_boundary() -> None:
    profile = RQ2B_CANDIDATE_FUSION_PROFILE
    assert profile["contract_version"] == "bid.evidence.candidate-fusion.v1"
    assert profile["profile_version"] == (
        "bid-evidence-candidate-fusion-profile-v1-rq2b"
    )
    assert profile["candidate_channels"]["lexical"]["weight"] == 1.0
    assert profile["candidate_channels"]["semantic"]["weight"] == 0.35
    assert profile["candidate_channels"]["overlap_bonus"]["weight"] == 0.2
    assert profile["fusion"]["raw_bm25_and_cosine_score_comparison"] is False
    assert profile["fusion"]["semantic_only_forced_promotion"] is False
    assert profile["fusion"]["reranker"] is False
    assert profile["fusion"]["result_hash_inputs"] == [
        "profile_version",
        "source_index_set_hash",
        "lexical_projection_set_hash",
        "semantic_index_set_hash",
        "query_plan_hash",
        "stable_fused_candidates",
    ]
    assert profile["frozen_adapter"] == {
        "tool_name": "evidence.search",
        "adapter_name": "bid-evidence-mcp-rq2b-search",
        "adapter_version": "v6-bm25f-semantic-fusion",
        "mode": "local_readonly",
        "replay_policy": "safe_idempotent",
    }
    assert profile["compatibility"]["new_database_migration_required"] is False
    assert profile["compatibility"]["alembic_head"] == "20260815_0103"


def test_rq2c_lightweight_rerank_profile_freezes_v7_boundary() -> None:
    profile = RQ2C_LIGHTWEIGHT_RERANK_PROFILE
    assert profile["contract_version"] == "bid.evidence.lightweight-rerank.v1"
    assert profile["profile_version"] == (
        "bid-evidence-rerank-profile-v1-rq2c-bce"
    )
    assert profile["frozen_candidate_input"]["max_candidates"] == 20
    assert profile["frozen_candidate_input"]["new_recall_allowed"] is False
    assert profile["reranker"]["model_id"] == (
        "maidalun1020/bce-reranker-base_v1"
    )
    assert profile["reranker"]["offline_cache_only"] is True
    assert profile["selection"]["no_promotion_identity"] is True
    assert profile["selection"]["full_result_resort"] is False
    assert profile["selection"]["maximum_promotions"] == 2
    assert profile["frozen_adapter"] == {
        "tool_name": "evidence.search",
        "adapter_name": "bid-evidence-mcp-rq2c-search",
        "adapter_version": "v7-bce-anchor-preserving-rerank",
        "mode": "local_readonly",
        "replay_policy": "safe_idempotent",
    }
    assert profile["compatibility"]["new_database_migration_required"] is False
    assert profile["compatibility"]["alembic_head"] == "20260815_0103"


def test_rq2_closeout_profile_freezes_cross_project_holdout_boundary() -> None:
    profile = RQ2_CLOSEOUT_PROFILE
    assert profile["contract_version"] == "bid.evidence.retrieval-benchmark.v1"
    assert profile["profile_version"] == (
        "bid-evidence-retrieval-benchmark-profile-v1-rq2-closeout"
    )
    assert profile["status"] == "implementation_complete_validation_pending"
    assert profile["portfolio"]["development"]["minimum_project_count"] == 3
    assert profile["portfolio"]["holdout"]["minimum_project_count"] == 2
    assert profile["portfolio"]["holdout"]["maximum_formal_execution_count"] == 1
    assert profile["portfolio"]["minimum_total_case_count"] == 100
    assert profile["frozen_retrieval"]["baseline_profile"] == (
        "bid-evidence-candidate-fusion-profile-v1-rq2b"
    )
    assert profile["frozen_retrieval"]["candidate_profile"] == (
        "bid-evidence-rerank-profile-v1-rq2c-bce"
    )
    assert profile["holdout_policy"]["development_gate_must_pass_before_holdout"] is True
    assert profile["holdout_policy"]["rerun_after_result_forbidden"] is True
    assert profile["holdout_policy"]["holdout_failure_may_define_new_rules"] is False
    assert profile["hard_invariants"]["atom_only_read"] is True
    assert profile["hard_invariants"]["project_family_overlap"] == 0
    assert profile["compatibility"]["new_database_migration_required"] is False
    assert profile["compatibility"]["alembic_head"] == "20260815_0103"


def test_every_machine_contract_schema_is_valid_and_has_a_unique_id() -> None:
    expected = {
        "contracts.schema.json",
        "tools.schema.json",
        "tool-execution.schema.json",
        "run-validation.schema.json",
        "task.schema.json",
        "planner.schema.json",
        "fact.schema.json",
        "dimension.schema.json",
        "decision.schema.json",
        "report.schema.json",
        "context.schema.json",
        "model-execution.schema.json",
        "model-roles.schema.json",
        "runtime-trace.schema.json",
        "evidence-chunk.schema.json",
        "pdf-native-layout.schema.json",
        "parse-quality.schema.json",
        "query-plan.schema.json",
        "lexical-search.schema.json",
        "evidence-retrieval.schema.json",
        "semantic-retrieval.schema.json",
        "candidate-fusion.schema.json",
        "lightweight-rerank.schema.json",
        "retrieval-benchmark.schema.json",
            "enterprise-capability.schema.json",
            "enterprise-business-baseline.schema.json",
            "enterprise-evidence-import.schema.json",
            "hard-gate-comparison-baseline.schema.json",
            "mvp-release-candidate.schema.json",
        "execute-preflight.schema.json",
        "execute-preflight-v2.schema.json",
    }
    assert set(SCHEMAS) == expected
    schema_ids = [schema["$id"] for schema in SCHEMAS.values()]
    assert len(schema_ids) == len(set(schema_ids))
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_phase4c1_profile_freezes_enterprise_snapshot_and_hard_gate_boundary() -> None:
    profile = PHASE4C1_ENTERPRISE_HARD_GATES_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4c1-enterprise-hard-gates-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r52"
    assert profile["status"] == "locally_validated"
    assert profile["enterprise_snapshot"]["slots"] == [
        f"I{index:02d}" for index in range(1, 12)
    ]
    assert profile["enterprise_snapshot"]["immutability"] == "new_version_only"
    assert profile["plan_continuation"]["task_type"] == "build_enterprise_snapshot"
    assert profile["hard_gates"]["codes"] == [
        f"HG{index:02d}" for index in range(1, 8)
    ]
    assert profile["hard_gates"]["input_authority"] == "resolved_facts_only"
    assert profile["hard_gates"]["unstructured_policy"] == "unknown"
    assert profile["hard_gates"]["raw_enterprise_payload_in_trace"] is False
    assert profile["runtime_lab"]["create_access"] == "local_execute_admin_only"
    assert profile["compatibility"] == {
        "new_database_migration_required": True,
        "alembic_head": "20260817_0104",
        "legacy_bid_intake_modified": False,
        "feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_CAPABILITY",
        "feature_flag_default": False,
        "ecs_rollout_allowed": False,
    }


def test_phase4c2_profile_freezes_baseline_validation_and_acceptance_boundary() -> None:
    profile = PHASE4C2_ENTERPRISE_BASELINE_ACCEPTANCE_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4c2-enterprise-baseline-acceptance-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r54"
    assert profile["status"] == "locally_validated"
    assert profile["baseline_validation"]["persistence"] == "none"
    assert profile["baseline_validation"]["slot_set"] == [
        f"I{index:02d}" for index in range(1, 12)
    ]
    assert profile["baseline_validation"]["unknown_policy"] == (
        "explicit_and_never_coerced_to_zero"
    )
    assert profile["baseline_validation"]["source_acceptance_policy"] == (
        "verified_or_imported_ready_self_reported_review_required"
    )
    assert profile["freeze_guard"]["candidate_hash_header"] == (
        "X-Enterprise-Candidate-Hash"
    )
    assert profile["freeze_guard"]["server_recomputes_candidate_hash"] is True
    assert profile["hard_gate_acceptance"]["codes"] == [
        f"HG{index:02d}" for index in range(1, 8)
    ]
    assert profile["hard_gate_acceptance"]["unknown_policy"] == (
        "never_promote_to_pass"
    )
    assert profile["compatibility"] == {
        "new_database_migration_required": False,
        "alembic_head": "20260817_0104",
        "legacy_bid_intake_modified": False,
        "feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_CAPABILITY",
        "feature_flag_default": False,
        "ecs_rollout_allowed": False,
    }


def test_phase4c3_profile_freezes_business_acceptance_and_release_boundary() -> None:
    profile = PHASE4C3_MVP_RELEASE_CANDIDATE_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4c3-mvp-release-candidate-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r57"
    assert profile["status"] == "locally_validated_first_real_pdf_rc_frozen"
    assert profile["business_acceptance"]["gate_codes"] == [
        f"HG{index:02d}" for index in range(1, 8)
    ]
    assert profile["business_acceptance"]["acceptance_is_quality_not_all_gates_pass"] is True
    assert profile["validation"]["persistence"] == "none"
    assert profile["validation"]["candidate_hash_stable"] is True
    assert profile["freeze"]["candidate_hash_header"] == (
        "X-MVP-RC-Candidate-Hash"
    )
    assert profile["freeze"]["server_recomputes_candidate_hash"] is True
    assert profile["freeze"]["one_release_candidate_per_run"] is True
    assert profile["freeze"]["emits_outbox_event"] is False
    assert profile["compatibility"] == {
        "new_database_migration_required": True,
        "alembic_head": "20260817_0105",
        "new_table": "bid_mvp_release_candidates",
        "legacy_bid_intake_modified": False,
        "feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_MVP_RELEASE_CANDIDATE",
        "feature_flag_default": False,
        "ecs_rollout_allowed": False,
    }


def test_phase4d1_profile_freezes_business_baseline_and_revalidation_boundary() -> None:
    profile = PHASE4D1_BUSINESS_BASELINE_REVALIDATION_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4d1-business-baseline-revalidation-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r59"
    assert profile["status"] == "locally_validated_isolated_not_for_ecs"
    assert profile["business_baseline"]["slot_set"] == [
        f"I{index:02d}" for index in range(1, 12)
    ]
    assert profile["business_baseline"]["unknown_policy"] == (
        "not_available_with_explicit_note"
    )
    assert profile["validation"]["persistence"] == "none"
    assert profile["freeze"]["candidate_hash_header"] == (
        "X-Enterprise-Business-Candidate-Hash"
    )
    assert profile["run_bootstrap"]["selects_only_business_verified_snapshot"] is True
    assert profile["decision_revalidation"]["reuses_phase4c3_release_authority"] is True
    assert profile["decision_revalidation"]["separate_decision_engine"] is False
    assert profile["compatibility"] == {
        "new_database_migration_required": True,
        "alembic_head": "20260817_0106",
        "new_table": "bid_enterprise_business_baselines",
        "legacy_bid_intake_modified": False,
        "feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_BUSINESS_BASELINE",
        "feature_flag_default": False,
        "ecs_rollout_allowed": False,
    }


def test_phase4d2_profile_freezes_enterprise_evidence_import_boundary() -> None:
    profile = PHASE4D2_ENTERPRISE_EVIDENCE_IMPORT_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4d2-enterprise-evidence-import-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r60"
    assert profile["status"] == (
        "locally_validated_pending_real_enterprise_data"
    )
    assert profile["evidence_item"]["content_addressed"] is True
    assert profile["evidence_item"]["filename_or_mime_slot_inference_forbidden"] is True
    assert profile["evidence_package"]["slot_set"] == [
        f"I{index:02d}" for index in range(1, 12)
    ]
    assert profile["evidence_package"]["mapping_policy"] == (
        "explicit_human_mapping_only"
    )
    assert profile["business_baseline_binding"][
        "item_must_belong_to_same_package_and_slot"
    ] is True
    assert profile["run_and_release_binding"]["run_bootstrap_rechecks_item_validity"] is True
    assert profile["compatibility"] == {
        "new_database_migration_required": True,
        "alembic_head": "20260817_0107",
        "new_tables": [
            "bid_enterprise_evidence_items",
            "bid_enterprise_evidence_packages",
            "bid_enterprise_evidence_package_items",
        ],
        "legacy_bid_intake_modified": False,
        "feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_EVIDENCE_IMPORT",
        "feature_flag_default": False,
        "ecs_rollout_allowed": False,
    }


def test_phase4d3_profile_freezes_verified_comparable_fact_boundary() -> None:
    profile = PHASE4D3_FACT_VERIFICATION_PROFILE
    assert profile["schema"] == (
        "bid-assessment-phase4d3-fact-verification-comparability-profile-v1"
    )
    assert profile["source_spec_version"] == "v0.1-r62"
    assert profile["status"] == "locally_validated_isolated_not_for_ecs"
    assert len(profile["fact_set"]["tender_slots"]) == 5
    assert len(profile["fact_set"]["enterprise_slots"]) == 11
    assert profile["fact_set"]["partial_never_counts_as_hard_gate_supported"] is True
    assert profile["evidence_policy"]["filename_mime_parser_hint_inference_forbidden"] is True
    assert profile["evidence_policy"]["model_output_is_not_verification_authority"] is True
    assert profile["runtime_binding"]["verified_fact_precedence"] == (
        "verified_comparison_fact_over_model_candidate"
    )
    assert profile["compatibility"] == {
        "new_database_migration_required": True,
        "alembic_head": "20260818_0108",
        "new_tables": [
            "bid_hard_gate_comparison_baselines",
            "bid_hard_gate_comparison_evidence_links",
            "bid_fact_comparison_links",
        ],
        "legacy_bid_intake_modified": False,
        "feature_flag": "FEATURE_BID_ASSESSMENT_PHASE4_FACT_VERIFICATION",
        "feature_flag_default": False,
        "ecs_rollout_allowed": False,
    }


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


def test_phase3f_tool_execution_contract_fixes_dispatch_and_adapter_envelopes() -> None:
    dispatch_validator = _cross_schema_validator(
        "tool-execution.schema.json", "ToolDispatchEnvelope"
    )
    dispatch_validator.validate(
        {
            "schema_version": "bid-tool-dispatch-envelope-v1",
            "invocation_id": "invocation_01",
            "operation_id": "operation_01",
            "assessment_id": "assessment_01",
            "run_id": "run_01",
            "task_id": "task_01",
            "task_attempt_id": "attempt_01",
            "context_manifest_id": "context_01",
            "manifest_id": "manifest_01",
            "tool_registry_version_id": "tools_01",
            "tool_name": "documents.outline",
            "arguments": {"document_version_id": "version_01"},
            "request_hash": "a" * 64,
            "provider_request_id": "bid-tool:invocation_01",
        }
    )
    result_validator = _cross_schema_validator(
        "tool-execution.schema.json", "ToolAdapterResult"
    )
    result_validator.validate(
        {
            "status": "ok",
            "summary": "Authoritative parsed document outline",
            "data": {"items": []},
            "evidence_refs": [],
            "warnings": [],
            "metrics": {"elapsed_ms": 0, "returned_items": 0},
            "truncated": False,
            "external_object_ref": None,
            "provider_receipt_id": "local:bid-tool:invocation_01",
            "actual_cost_microunits": 0,
        }
    )


def test_phase4a2_model_action_and_request_envelope_are_closed() -> None:
    state = {
        "schema": "bid.local_agent.state.v1",
        "run_id": "run_01",
        "task_id": "task_01",
        "task_attempt_id": "attempt_01",
        "fencing_token": 1,
        "task_contract_hash": "a" * 64,
        "skill_binding_hash": "b" * 64,
        "phase": "await_model",
        "action_seq": 1,
        "observed_model_result_refs": [],
        "observed_tool_result_refs": [],
        "candidate_refs": [],
        "missing_slots": [],
        "outstanding_operation_ref": "model-call:call_01",
        "stop_reason": None,
    }
    _cross_schema_validator("model-execution.schema.json", "LocalAgentState").validate(state)
    request = {
        "schema": "bid.model.request.v1",
        "model_call_id": "call_01",
        "assessment_id": "assessment_01",
        "run_id": "run_01",
        "task_id": "task_01",
        "task_attempt_id": "attempt_01",
        "fencing_token": 1,
        "action_seq": 1,
        "logical_role": "local_research",
        "provider_ref": "contract-provider",
        "model_ref": "contract-model-v1",
        "prompt_role": "local_research",
        "action_schema": "bid.task.action.v1",
        "context_manifest_id": "context_01",
        "context_manifest_hash": "c" * 64,
        "checkpoint_id": "checkpoint_01",
        "checkpoint_state_hash": "d" * 64,
        "task_contract_hash": "a" * 64,
        "model_profile_hash": "e" * 64,
        "model_route_hash": "2" * 64,
        "prompt_bundle_hash": "f" * 64,
        "input_token_limit": 8000,
        "output_token_limit": 2000,
        "cost_microunits_limit": 100000,
        "timeout_seconds": 120,
    }
    _cross_schema_validator("model-execution.schema.json", "ModelRequestEnvelope").validate(request)
    action_validator = _cross_schema_validator("model-execution.schema.json", "TaskAction")
    action_validator.validate(
        {
            "action_type": "request_tool",
            "tool_call_id": "tc_model_01",
            "tool_name": "documents.outline",
            "arguments": {"document_version_id": "version_01"},
            "reason_codes": ["NEED_DOCUMENT_STRUCTURE"],
        }
    )
    with pytest.raises(ValidationError):
        action_validator.validate(
            {
                "action_type": "finish",
                "completion_summary": "done",
                "output_candidate": None,
                "reason_codes": [],
                "chain_of_thought": "forbidden",
            }
        )
    claim = {
        "model_call_id": "call_01",
        "model_call_attempt_id": "call_attempt_01",
        "worker_id": "model-worker-01",
        "fencing_token": 1,
        "lease_until": "2026-08-13T18:00:00Z",
        "provider_ref": "contract-provider",
        "model_ref": "contract-model-v1",
        "replay_policy": "safe_idempotent",
        "provider_request_id": "bid-model:call_01:attempt:1",
        "request_envelope": request,
    }
    _cross_schema_validator("model-execution.schema.json", "ModelCallClaim").validate(
        claim
    )
    provider_result = {
        "action": {
            "action_type": "finish",
            "completion_summary": "bounded action complete",
            "output_candidate": None,
            "reason_codes": ["TASK_COMPLETE"],
        },
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "finish_reason": "stop",
        "provider_receipt_id": "receipt_01",
        "actual_cost_microunits": 0,
    }
    _cross_schema_validator(
        "model-execution.schema.json", "ModelProviderResult"
    ).validate(provider_result)
    _cross_schema_validator(
        "model-execution.schema.json", "ModelResultReceipt"
    ).validate(
        {
            "model_call_id": "call_01",
            "model_result_id": "result_01",
            "operation_id": "operation_01",
            "result_hash": "1" * 64,
            "action": provider_result["action"],
            "duplicate": False,
        }
    )
    machine = STATE_TRANSITIONS["state_machines"]["model_call"]
    assert set(machine["terminal_states"]) == {
        "succeeded", "failed", "cancelled", "uncertain", "dead_letter"
    }


def test_phase3g_run_validation_contract_fixes_claim_result_and_state_machine() -> None:
    _cross_schema_validator("run-validation.schema.json", "RunValidationClaim").validate(
        {
            "validation_id": "validation_01",
            "run_id": "run_01",
            "attempt_id": "validation_attempt_01",
            "attempt_no": 1,
            "fencing_token": 1,
            "worker_id": "validator-01",
            "lease_until": "2026-08-12T18:00:00Z",
        }
    )
    _cross_schema_validator("run-validation.schema.json", "RunValidationResult").validate(
        {
            "schema": "bid.run.validation.result.v1",
            "validator_version": "bid-run-integrity-validator-v2",
            "validation_id": "validation_01",
            "run_id": "run_01",
            "outcome": "passed",
            "retryable": False,
            "failure_code": None,
            "run_input_hash": "a" * 64,
            "validation_input_hash": "b" * 64,
            "checks": [{"code": "TASKS_TERMINAL_SUCCESS", "passed": True, "severity": "fatal", "detail": None}],
            "summary": {"check_count": 1, "passed_count": 1, "failed_count": 0, "failed_codes": []},
            "result_hash": "c" * 64,
        }
    )
    machine = STATE_TRANSITIONS["state_machines"]["run_validation"]
    assert set(machine["states"]) == {
        "requested", "leased", "running", "passed", "failed", "stale", "cancelled"
    }
    assert set(machine["terminal_states"]) == {"passed", "failed", "stale", "cancelled"}


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


def test_plan_commit_envelope_binds_input_proposal_registry_and_validation() -> None:
    validator = _cross_schema_validator("planner.schema.json", "PlanCommitEnvelope")
    planner_input = {
        "assessment": {
            "id": "assessment_01",
            "goal": "bid_go_no_go",
            "scope_id": "scope_01",
        },
        "bound_versions": _version_binding(),
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
        "allowed_task_types": ["bind_assessment_snapshot"],
        "planning_limits": {"max_dynamic_tasks": 8, "max_dependency_depth": 3},
    }
    proposal = {
        "proposal_id": "planp_01",
        "reason_codes": ["INITIAL_RUN_PLAN"],
        "add_tasks": [],
        "supersede_tasks": [],
        "questions": [],
        "expected_stage_after": "fact_baseline",
        "planner_confidence": "high",
    }
    envelope = {
        "schema": "bid.plan.commit.envelope.v1",
        "generator_version": "bid-deterministic-bootstrap-planner-v1",
        "validator_version": "bid-plan-validator-v1",
        "task_registry_version": "bid-assessment-standard-tasks-v1@1.0.0-draft.1",
        "task_registry_hash": "a" * 64,
        "run_input_hash": "b" * 64,
        "planner_input_hash": "c" * 64,
        "proposal_hash": "d" * 64,
        "planner_input": planner_input,
        "proposal": proposal,
        "validation": {
            "status": "accepted",
            "checks": [
                "task_type_allowlist",
                "acyclic_dependencies",
                "scope_version_consistency",
                "tool_profile_permissions",
                "budget_limits",
                "max_8_dynamic_tasks",
                "max_dependency_depth_3",
                "hard_gate_ordering",
                "report_validation_ordering",
            ],
            "validated_hash": "e" * 64,
        },
    }
    validator.validate(envelope)
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "decision_class": "recommend"})

    phase4_envelope = json.loads(json.dumps(envelope))
    phase4_envelope.update(
        {
            "schema": "bid.plan.commit.envelope.v2",
            "generator_version": "bid-deterministic-stage-planner-v2",
            "validator_version": "bid-plan-validator-v2",
            "task_catalog_ref": "task-catalog.json",
            "skill_catalog_ref": "catalog-1.0.0.json",
            "skill_catalog_version": "bid-assessment-mvp-skills@1.0.0",
            "skill_catalog_hash": "f" * 64,
            "stage": "P0",
            "final_stage": False,
        }
    )
    phase4_envelope["planner_input"]["run_kind"] = "preliminary"
    phase4_envelope["validation"]["checks"].extend(
        ["skill_binding_hash", "stage_sequence"]
    )
    validator.validate(phase4_envelope)
    invalid_phase4 = json.loads(json.dumps(phase4_envelope))
    invalid_phase4.pop("skill_catalog_hash")
    with pytest.raises(ValidationError):
        validator.validate(invalid_phase4)


def test_phase3c_task_contract_lease_and_completion_receipt_are_closed() -> None:
    task_validator = _cross_schema_validator("task.schema.json", "TaskContract")
    task_contract = {
        "task_id": "task_01",
        "task_key": "phase3b.01.bind_assessment_snapshot",
        "task_type": "bind_assessment_snapshot",
        "objective": "Bind immutable inputs.",
        "scope": {
            "assessment_id": "assessment_01",
            "scope_id": "scope_01",
            "scope_version": 1,
            "lot_id": "lot_01",
        },
        "depends_on": [],
        "bound_versions": _version_binding(),
        "required_fact_slots": [],
        "allowed_tools": ["facts.query"],
        "context_profile": "DOCUMENT_SCOPE_V1",
        "budget": {
            "max_iterations": 3,
            "max_tool_calls": 4,
            "max_input_tokens": 8000,
            "max_output_tokens": 2000,
        },
        "completion_contract": "document_scope_result_v1",
        "stop_conditions": ["completion_contract_satisfied"],
        "failure_policy": "retry_then_fail",
        "output_version": "bid-task-output-v1",
    }
    task_validator.validate(task_contract)
    _cross_schema_validator("task.schema.json", "TaskLease").validate(
        {
            "task_id": "task_01",
            "attempt_id": "attempt_01",
            "attempt_no": 1,
            "worker_id": "worker:test",
            "fencing_token": 1,
            "lease_until": "2026-08-11T12:03:00Z",
            "task_contract": task_contract,
            "task_contract_hash": "a" * 64,
            "resume_checkpoint": None,
        }
    )
    _cross_schema_validator("task.schema.json", "TaskCompletionReceipt").validate(
        {
            "checkpoint_id": "checkpoint_01",
            "state_hash": "b" * 64,
            "output_hash": "c" * 64,
            "completion_contract": "document_scope_result_v1",
            "validator_version": "bid-task-output-validator-v1",
            "output_ref": None,
        }
    )
    with pytest.raises(ValidationError):
        task_validator.validate({**task_contract, "fencing_token": 1})


def test_phase3d_cancel_retry_resume_and_event_contracts_are_frozen() -> None:
    _cross_schema_validator("contracts.schema.json", "CancelRunRequest").validate(
        {"reason": "负责人决定暂不继续"}
    )
    _cross_schema_validator("contracts.schema.json", "RetryRunRequest").validate(
        {"retry_mode": "from_latest_checkpoint", "note": None}
    )
    with pytest.raises(ValidationError):
        _cross_schema_validator("contracts.schema.json", "RetryRunRequest").validate(
            {"retry_mode": "restart_from_beginning", "note": None}
        )
    lease_validator = _cross_schema_validator("task.schema.json", "ResumeCheckpoint")
    lease_validator.validate(
        {
            "checkpoint_id": "checkpoint_01",
            "source_attempt_id": "attempt_01",
            "action_seq": 2,
            "state_hash": "a" * 64,
            "candidate_output_ref": None,
            "next_state": "running",
        }
    )
    contracts = EVENT_CATALOG["outbox_event_contracts"]
    assert contracts["bid.run.cancel_requested.v1"]["aggregate_type"] == "run"
    assert contracts["bid.run.cancelled.v1"]["aggregate_type"] == "run"
    assert contracts["bid.run.retry_requested.v1"]["required_payload_fields"] == [
        "run_id",
        "from",
        "to",
        "retryable",
        "retry_mode",
        "attempts",
        "completed_units",
        "total_units",
        "resource_version",
    ]
    transitions = STATE_TRANSITIONS["state_machines"]
    assert "cancelled" in transitions["analysis_run"]["transitions"]["validating"]
    assert "ready" in transitions["task"]["transitions"]["running"]
    assert "cancelled" in transitions["async_operation"]["transitions"]["created"]
    assert "cancelled" in transitions["async_operation"]["transitions"]["submitted"]
    dispatch_machine = transitions["tool_dispatch"]
    dispatch_states = set(
        SCHEMAS["tool-execution.schema.json"]["$defs"]["ToolDispatchStatus"]["enum"]
    )
    assert set(dispatch_machine["states"]) == dispatch_states
    assert set(dispatch_machine["transitions"]) == dispatch_states
    assert "uncertain" in dispatch_machine["transitions"]["sending"]
    for terminal in dispatch_machine["terminal_states"]:
        assert dispatch_machine["transitions"][terminal] == []


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
        "manifest_version": 3,
        "scope_id": "scope_01",
        "scope_version": 1,
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
        "task_attempt_id": "attempt_01",
        "fencing_token": 1,
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
        "task_contract_hash": "a" * 64,
        "context_profile": "TENDER_FACTS_V1",
        "budget_profile": "STANDARD_V1",
        "included_tool_result_ids": [],
        "included_model_result_ids": [],
        "working_state_hash": None,
        "component_token_estimates": {
            "base": 1200,
            "evidence": 13600,
            "total": 14800,
        },
        "hash": "d" * 64,
    }
    validator.validate(manifest)
    invalid = json.loads(json.dumps(manifest))
    invalid["excluded_due_to_budget"][0]["priority"] = "P1"
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_phase3e_tool_call_and_result_envelopes_are_scope_minimal_and_strict() -> None:
    call_validator = _validator("ToolCallEnvelope")
    call = {
        "tool_call_id": "tc_01",
        "tool_name": "facts.query",
        "arguments": {"fact_slots": ["tender.deadline"]},
        "task_id": "task_01",
        "scope_token": "ts_" + "a" * 64,
        "idempotency_key": "phase3e-contract-key-0001",
    }
    call_validator.validate(call)
    with pytest.raises(ValidationError):
        call_validator.validate({**call, "assessment_id": "assessment_01"})

    result_validator = _validator("ToolResultEnvelope")
    result = {
        "status": "partial",
        "summary": "Only part of the governed result is available",
        "data": {"resolved": 1},
        "result_ref": {
            "type": "tool_result",
            "id": "result_01",
            "expires_at": "2026-09-11T00:00:00Z",
        },
        "evidence_refs": ["evidence_01"],
        "operation_id": None,
        "truncated": True,
        "warnings": ["more data is available through a governed result slice"],
        "metrics": {"elapsed_ms": 10, "returned_items": 1},
    }
    result_validator.validate(result)
    with pytest.raises(ValidationError):
        result_validator.validate({**result, "raw_object_url": "minio://private/raw"})


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
    event_contracts = EVENT_CATALOG["outbox_event_contracts"]
    assert set(event_contracts) == {
        "bid.document.parse_requested.v1",
        "bid.document.parsed.v1",
        "bid.document.parse_failed.v1",
        "bid.manifest.parse_set_ready.v1",
        "bid.lot_detection.requested.v1",
        "bid.lots.detected.v1",
        "bid.lot_detection.failed.v1",
        "bid.lot.selected.v1",
        "bid.plan.requested.v1",
        "bid.run.created.v1",
        "bid.plan.committed.v1",
        "bid.plan.continuation_requested.v1",
        "bid.task.ready.v1",
        "bid.task.leased.v1",
        "bid.task.waiting_operation.v1",
        "bid.task.succeeded.v1",
        "bid.task.failed.v1",
        "bid.task.stale.v1",
        "bid.run.validation_requested.v1",
        "bid.run.cancel_requested.v1",
        "bid.run.cancelled.v1",
        "bid.run.retry_requested.v1",
        "bid.run.succeeded.v1",
        "bid.run.failed.v1",
        "bid.run.stale.v1",
    }
    assert set(event_contracts) <= set(outbox_types)
    assert event_contracts["bid.document.parse_requested.v1"][
        "required_payload_fields"
    ] == [
        "parse_run_id",
        "document_version_id",
        "input_hash",
        "parser_profile_version",
    ]
    assert {
        "status",
        "quality",
        "warnings",
        "error_code",
        "retryable",
    } <= set(
        event_contracts["bid.document.parse_failed.v1"][
            "required_payload_fields"
        ]
    )
    assert "lots_url" in event_contracts["bid.lots.detected.v1"][
        "required_payload_fields"
    ]
    assert event_contracts["bid.lot.selected.v1"] == {
        "aggregate_type": "scope",
        "required_payload_fields": [
            "scope_id",
            "lot_id",
            "manifest_id",
            "detection_run_id",
            "from",
            "to",
            "resource_version",
        ],
    }
    assert event_contracts["bid.plan.requested.v1"] == {
        "aggregate_type": "scope",
        "required_payload_fields": [
            "operation_id",
            "assessment_id",
            "scope_id",
            "manifest_id",
            "lot_id",
            "requested_run_kind",
            "resource_version",
        ],
    }
    assert event_contracts["bid.run.created.v1"]["aggregate_type"] == "run"
    assert {
        "run_id",
        "assessment_id",
        "scope_id",
        "manifest_id",
        "input_fingerprint",
        "input_hash",
        "evaluation_time",
        "progress_url",
    } <= set(event_contracts["bid.run.created.v1"]["required_payload_fields"])
    assert event_contracts["bid.plan.committed.v1"]["aggregate_type"] == (
        "plan_revision"
    )
    assert {
        "plan_revision_id",
        "run_id",
        "validated_hash",
        "task_count",
        "ready_task_count",
        "task_registry_version",
        "validator_version",
    } <= set(
        event_contracts["bid.plan.committed.v1"]["required_payload_fields"]
    )
    assert event_contracts["bid.plan.continuation_requested.v1"] == {
        "aggregate_type": "run",
        "required_payload_fields": [
            "run_id",
            "completed_plan_revision_id",
            "completed_stage",
            "next_stage",
            "from",
            "to",
            "stage_code",
            "status",
            "message",
            "completed_units",
            "total_units",
            "resource_version",
        ],
    }
    assert event_contracts["bid.task.ready.v1"]["aggregate_type"] == "task"
    assert {
        "task_id",
        "task_key",
        "task_type",
        "run_id",
        "plan_revision_id",
        "stage_code",
        "status",
        "message",
        "completed_units",
        "total_units",
        "resource_version",
    } == set(event_contracts["bid.task.ready.v1"]["required_payload_fields"])
    assert event_contracts["bid.task.leased.v1"]["aggregate_type"] == "task"
    assert {
        "attempt_id",
        "attempt_no",
        "lease_owner",
        "lease_until",
        "fencing_token",
        "task_contract_hash",
    } <= set(event_contracts["bid.task.leased.v1"]["required_payload_fields"])
    assert event_contracts["bid.task.succeeded.v1"]["aggregate_type"] == "task"
    assert {"checkpoint_id", "result_hash"} <= set(
        event_contracts["bid.task.succeeded.v1"]["required_payload_fields"]
    )
    assert {"error_code", "retryable", "retry_scheduled"} <= set(
        event_contracts["bid.task.failed.v1"]["required_payload_fields"]
    )
    assert event_contracts["bid.run.validation_requested.v1"]["aggregate_type"] == "run"


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


def test_api21_freezes_manifest_acl_safe_source_parse_placeholder_and_etag() -> None:
    path_item = OPENAPI["paths"]["/bid-document-versions/{version_id}"]
    operation = path_item["get"]
    parameters = _operation_parameters(path_item, operation)
    headers = {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter["in"] == "header"
    }
    assert set(headers) == {"If-None-Match"}
    assert headers["If-None-Match"]["required"] is False
    assert operation["responses"]["200"] == {
        "$ref": "#/components/responses/DocumentVersionOk"
    }
    assert "304" in operation["responses"]
    response = OPENAPI["components"]["responses"]["DocumentVersionOk"]
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-cache, max-age=0, must-revalidate"
    )
    assert response["headers"]["Vary"]["schema"]["const"] == "Authorization"

    value = {
        "code": 200,
        "message": "ok",
        "data": {
            "version_id": "version_01",
            "document": {
                "document_id": "document_01",
                "logical_name": "招标文件",
                "document_type": "tender_document",
            },
            "version_no": 1,
            "filename": "招标文件.pdf",
            "size_bytes": 123,
            "mime_type": "application/pdf",
            "sha256": "a" * 64,
            "created_at": "2026-08-11T01:00:00Z",
            "upload_source": {
                "source_type": "upload_batch",
                "operation": "add",
                "relative_path": "招标资料/招标文件.pdf",
            },
            "parse_summary": {
                "status": "not_requested",
                "latest_run_id": None,
                "requested_at": None,
                "started_at": None,
                "finished_at": None,
                "quality": None,
                "warnings": [],
            },
            "manifest_references": [
                {
                    "assessment_id": "assessment_01",
                    "assessment_url": "/api/v1/bid-assessments/assessment_01",
                    "manifest_id": "manifest_01",
                    "manifest_version": 1,
                    "is_current_manifest": True,
                    "role": "tender_document",
                    "order_no": 0,
                }
            ],
            "allowed_actions": {
                "download": True,
                "download_url": (
                    "/api/v1/bid-document-versions/version_01/download"
                ),
            },
        },
        "error": None,
        "request_id": "request_01",
    }
    _validator("DocumentVersionResponse").validate(value)
    serialized = json.dumps(
        SCHEMA_BUNDLE["$defs"]["DocumentVersionDetail"],
        ensure_ascii=False,
    )
    for forbidden in {
        "object_key",
        "storage_etag",
        "storage_status",
        "file_object_id",
        "source_metadata_json",
        "source_metadata_hash",
        "parser_hint",
        "logical_identity_key",
        "created_by",
        "batch_id",
        "batch_file_id",
        "client_file_id",
        "replace_document_id",
    }:
        assert forbidden not in serialized


def test_api22_freezes_full_authorized_stream_and_defers_range_support() -> None:
    path_item = OPENAPI["paths"][
        "/bid-document-versions/{version_id}/download"
    ]
    operation = path_item["get"]
    parameters = _operation_parameters(path_item, operation)
    assert not any(parameter["name"] == "Range" for parameter in parameters)
    assert set(operation["responses"]) == {"200", "default"}
    assert operation["responses"]["200"] == {
        "$ref": "#/components/responses/DocumentDownloadOk"
    }
    response = OPENAPI["components"]["responses"]["DocumentDownloadOk"]
    assert set(response["headers"]) == {
        "Content-Disposition",
        "Content-Length",
        "X-Content-Type-Options",
        "Cache-Control",
        "Vary",
        "Accept-Ranges",
        "Content-Security-Policy",
    }
    assert response["headers"]["Accept-Ranges"]["schema"]["const"] == "none"
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-store"
    )
    assert "206" not in operation["responses"]


def test_api30_freezes_generation_lifecycle_zero_candidates_and_private_etag() -> None:
    path_item = OPENAPI["paths"]["/bid-assessments/{assessment_id}/lots"]
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
    assert set(query_parameters) == {"manifest_id"}
    assert headers["If-None-Match"]["required"] is False
    assert operation["responses"]["200"] == {
        "$ref": "#/components/responses/LotCandidatesOk"
    }
    assert "304" in operation["responses"]
    response = OPENAPI["components"]["responses"]["LotCandidatesOk"]
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-cache, max-age=0, must-revalidate"
    )
    assert response["headers"]["Vary"]["schema"]["const"] == "Authorization"
    assert set(SCHEMA_BUNDLE["$defs"]["LotGenerationStatus"]["enum"]) == {
        "not_started",
        "queued",
        "running",
        "succeeded",
        "failed",
        "stale",
    }

    _validator("LotCandidatePageResponse").validate(
        {
            "code": 200,
            "message": "ok",
            "data": {
                "assessment_id": "assessment_01",
                "manifest": {
                    "manifest_id": "manifest_01",
                    "version": 1,
                    "manifest_hash": "a" * 64,
                    "is_current_manifest": True,
                },
                "generation": {
                    "status": "succeeded",
                    "detection_run_id": "detection_01",
                    "parse_set_hash": "b" * 64,
                    "candidate_count": 0,
                    "retryable": False,
                    "error_code": None,
                    "requested_at": "2026-08-11T01:00:00Z",
                    "started_at": "2026-08-11T01:00:01Z",
                    "finished_at": "2026-08-11T01:00:02Z",
                },
                "candidates": [],
                "selection_required": False,
                "selected_lot_id": None,
                "blocking_reason": {
                    "code": "no_supported_lot",
                    "message": "资料中没有可由直接内容证据支持的标段候选",
                },
                "allowed_actions": [],
            },
            "error": None,
            "request_id": "request_01",
        }
    )


def test_api31_freezes_scope_binding_response_headers_and_no_placeholder_run() -> None:
    path_item = OPENAPI["paths"][
        "/bid-assessments/{assessment_id}/lot-selection"
    ]
    operation = path_item["post"]
    headers = {
        parameter["name"]: parameter
        for parameter in _operation_parameters(path_item, operation)
        if parameter["in"] == "header"
    }
    assert operation["x-api-id"] == "API-31"
    assert operation["operationId"] == "selectBidLot"
    assert set(headers) == {"Idempotency-Key", "If-Match"}
    assert all(parameter["required"] is True for parameter in headers.values())
    assert operation["responses"]["202"] == {
        "$ref": "#/components/responses/LotSelected"
    }

    response = OPENAPI["components"]["responses"]["LotSelected"]
    assert set(response["headers"]) == {
        "Location",
        "ETag",
        "X-Resource-Version",
        "Idempotent-Replay",
        "Cache-Control",
    }
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-store"
    )
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LotSelectionResponse"
    }
    request_value = {
        "manifest_id": "manifest_01",
        "lot_id": "lot_01",
        "selection_note": None,
    }
    _validator("LotSelectionRequest").validate(request_value)
    with pytest.raises(ValidationError):
        _validator("LotSelectionRequest").validate(
            {"manifest_id": "manifest_01", "lot_id": "lot_01"}
        )


    value = {
        "code": 202,
        "message": "标段已选择，研判规划已受理",
        "data": {
            "scope": {
                "scope_id": "scope_01",
                "lot_id": "lot_01",
                "lot_code": "1",
                "lot_name": "室内装饰工程",
                "scope_version": 1,
            },
            "accepted_operation": {
                "operation_id": "op_01",
                "status": "accepted",
                "status_url": "/api/v1/bid-assessments/assessment_01",
            },
            "run": None,
            "assessment": {
                "assessment_id": "assessment_01",
                "title": "办公楼投标研判",
                "client_name": "某甲方",
                "internal_note": None,
                "lifecycle_status": "active",
                "business_status": "preliminary_analyzing",
                "row_version": 5,
                "scope": {
                    "scope_id": "scope_01",
                    "lot_id": "lot_01",
                    "lot_code": "1",
                    "lot_name": "室内装饰工程",
                    "scope_version": 1,
                },
                "current_manifest": {
                    "manifest_id": "manifest_01",
                    "version": 1,
                    "document_count": 1,
                    "committed_at": "2026-08-11T01:00:00Z",
                },
                "active_run": None,
                "latest_reports": {"preliminary": None, "deep": None},
                "blocking_reason": None,
                "recommended_view": "progress",
                "primary_action": None,
                "allowed_actions": [],
                "created_at": "2026-08-11T00:00:00Z",
                "updated_at": "2026-08-11T01:00:00Z",
            },
        },
        "error": None,
        "request_id": "request_01",
    }
    _validator("LotSelectionResponse").validate(value)
    invalid = json.loads(json.dumps(value))
    invalid["data"]["run"] = {"run_id": "placeholder_run"}
    with pytest.raises(ValidationError):
        _validator("LotSelectionResponse").validate(invalid)


def test_api32_freezes_independent_clone_request_and_response_headers() -> None:
    path_item = OPENAPI["paths"][
        "/bid-assessments/{assessment_id}/clone-for-lot"
    ]
    operation = path_item["post"]
    headers = {
        parameter["name"]: parameter
        for parameter in _operation_parameters(path_item, operation)
        if parameter["in"] == "header"
    }
    assert operation["x-api-id"] == "API-32"
    assert operation["operationId"] == "cloneBidAssessmentForLot"
    assert set(headers) == {"Idempotency-Key", "If-Match"}
    assert all(parameter["required"] is True for parameter in headers.values())
    assert operation["responses"]["201"] == {
        "$ref": "#/components/responses/ClonedAssessmentCreated"
    }

    response = OPENAPI["components"]["responses"]["ClonedAssessmentCreated"]
    assert set(response["headers"]) == {
        "Location",
        "ETag",
        "X-Resource-Version",
        "Idempotent-Replay",
        "Cache-Control",
    }
    assert response["headers"]["Cache-Control"]["schema"]["const"] == (
        "private, no-store"
    )
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AssessmentResponse"
    }
    _validator("CloneForLotRequest").validate(
        {
            "source_manifest_id": "manifest_01",
            "lot_id": "lot_02",
            "title": "某办公楼—机电标段投标研判",
        }
    )
    with pytest.raises(ValidationError):
        _validator("CloneForLotRequest").validate(
            {
                "source_manifest_id": "manifest_01",
                "lot_id": "lot_02",
                "title": "机电标段投标研判",
                "copy_source_acl": True,
            }
        )


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
