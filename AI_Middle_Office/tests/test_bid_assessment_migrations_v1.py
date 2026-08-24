from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, String, UniqueConstraint, create_engine, inspect, text

from app.core.database import Base
from app.models import bid_assessment_eventing as eventing_models
from app.models import registry as model_registry  # noqa: F401


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260810_0083_add_bid_assessment_foundation.py"
)
CONFIG_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260810_0084_add_bid_assessment_config_versions.py"
)
RUNTIME_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260810_0085_add_bid_assessment_runtime_skeleton.py"
)
EVENTING_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260810_0086_add_bid_eventing_idempotency_audit.py"
)
UPLOAD_RECEIVED_EVENT_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260810_0087_allow_bid_upload_file_received_event.py"
)
UPLOAD_REMOVED_EVENT_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0088_allow_bid_upload_file_removed_event.py"
)
EVENT_EXTENSION_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0089_allow_bid_upload_batch_deactivation_event.py"
)
UPLOAD_COMMIT_LINEAGE_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0090_add_bid_upload_commit_lineage.py"
)
UPLOAD_ABANDONMENT_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0091_add_bid_upload_abandonment.py"
)
PARSE_AUTHORITY_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0092_add_bid_document_parse_authority.py"
)
LOT_AUTHORITY_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0093_add_bid_lot_detection_authority.py"
)
RUN_RETRY_EVENT_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260811_0094_allow_bid_run_retry_requested_event.py"
)
TOOL_CONTEXT_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260812_0095_add_bid_context_tool_authority.py"
)
TOOL_EXECUTION_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260812_0096_add_bid_tool_dispatch_authority.py"
)
RUN_VALIDATION_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260812_0097_add_bid_run_validation_authority.py"
)
PLAN_CONTINUATION_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260812_0098_allow_bid_plan_continuation_event.py"
)
MODEL_EXECUTION_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260813_0099_add_bid_model_execution_authority.py"
)
FACT_AUTHORITY_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260813_0100_add_bid_fact_authority.py"
)
REPORT_AUTHORITY_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260813_0101_add_bid_preliminary_report_authority.py"
)
EVIDENCE_RETRIEVAL_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260814_0102_add_bid_evidence_retrieval_authority.py"
)
SEMANTIC_RETRIEVAL_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260815_0103_add_bid_evidence_semantic_authority.py"
)
ENTERPRISE_FACT_LINEAGE_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260817_0104_add_bid_enterprise_fact_lineage.py"
)
MVP_RELEASE_CANDIDATE_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260817_0105_add_bid_mvp_release_candidate.py"
)
ENTERPRISE_BUSINESS_BASELINE_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260817_0106_add_bid_enterprise_business_baseline.py"
)
ENTERPRISE_EVIDENCE_IMPORT_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260817_0107_add_bid_enterprise_evidence_import.py"
)
HARD_GATE_FACT_VERIFICATION_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "20260818_0108_add_bid_hard_gate_fact_verification.py"
)
DESIGN_PATH = PROJECT_ROOT / "docs" / "bid-assessment-phase1-migration-design-20260810.md"
MANIFEST_PATH = PROJECT_ROOT / "contracts" / "bid_assessment" / "v1" / "manifest.json"
STATE_PATH = PROJECT_ROOT / "contracts" / "bid_assessment" / "v1" / "state-transitions.json"
EVENT_CATALOG_PATH = PROJECT_ROOT / "contracts" / "bid_assessment" / "v1" / "event-catalog.json"

FOUNDATION_TABLES = {
    "bid_assessments",
    "bid_file_objects",
    "bid_documents",
    "bid_document_versions",
    "bid_document_manifests",
    "bid_manifest_documents",
    "bid_upload_batches",
    "bid_upload_batch_files",
    "bid_upload_batch_deactivations",
    "bid_lot_candidates",
    "bid_assessment_scopes",
}
MUTABLE_TABLES = {
    "bid_assessments",
    "bid_file_objects",
    "bid_upload_batches",
    "bid_upload_batch_files",
}
CONFIG_TABLES = {
    "bid_enterprise_snapshots",
    "bid_enterprise_snapshot_records",
    "bid_rule_sets",
    "bid_fact_catalog_versions",
    "bid_prompt_bundles",
    "bid_tool_registry_versions",
    "bid_model_profile_versions",
    "bid_formula_catalog_versions",
}
ARTIFACT_VERSION_TABLES = CONFIG_TABLES - {
    "bid_enterprise_snapshots",
    "bid_enterprise_snapshot_records",
}
RUNTIME_TABLES = {
    "bid_analysis_runs",
    "bid_plan_revisions",
    "bid_tasks",
    "bid_task_dependencies",
    "bid_task_attempts",
    "bid_checkpoints",
    "bid_async_operations",
    "bid_question_rounds",
    "bid_questions",
    "bid_answer_drafts",
    "bid_answer_sets",
    "bid_answers",
}
MUTABLE_RUNTIME_TABLES = {
    "bid_analysis_runs",
    "bid_plan_revisions",
    "bid_tasks",
    "bid_task_attempts",
    "bid_async_operations",
    "bid_question_rounds",
    "bid_questions",
    "bid_answer_drafts",
}
EVENTING_TABLES = {
    "bid_outbox_events",
    "bid_processed_events",
    "bid_public_events",
    "bid_idempotency_records",
    "bid_legacy_resource_links",
    "bid_audit_log",
}
MUTABLE_EVENTING_TABLES = {
    "bid_outbox_events",
    "bid_idempotency_records",
}
PARSE_AUTHORITY_TABLES = {
    "bid_document_parse_runs",
    "bid_document_parse_heads",
    "bid_document_parse_attempts",
    "bid_document_parse_events",
    "bid_document_parse_units",
    "bid_evidence_fragments",
}
LOT_AUTHORITY_TABLES = {
    "bid_lot_detection_runs",
    "bid_lot_detection_heads",
    "bid_lot_detection_attempts",
    "bid_lot_detection_events",
    "bid_lot_candidate_evidence",
}
TOOL_CONTEXT_TABLES = {
    "bid_context_manifests",
    "bid_tool_invocations",
    "bid_tool_results",
}
TOOL_EXECUTION_TABLES = {
    "bid_tool_dispatches",
    "bid_tool_dispatch_attempts",
}
RUN_VALIDATION_TABLES = {
    "bid_run_validations",
    "bid_run_validation_attempts",
}
MODEL_EXECUTION_TABLES = {
    "bid_model_calls",
    "bid_model_call_attempts",
    "bid_model_results",
}
FACT_AUTHORITY_TABLES = {
    "bid_fact_assertions",
    "bid_fact_evidence_links",
    "bid_fact_coverages",
    "bid_resolved_facts",
    "bid_resolved_fact_heads",
}
REPORT_AUTHORITY_TABLES = {
    "bid_hard_gate_results",
    "bid_preliminary_decisions",
    "bid_report_claims",
    "bid_claim_citations",
    "bid_report_validations",
    "bid_preliminary_reports",
}
EVIDENCE_RETRIEVAL_TABLES = {
    "bid_evidence_retrieval_indexes",
    "bid_evidence_retrieval_entries",
    "bid_evidence_retrieval_heads",
}
SEMANTIC_RETRIEVAL_TABLES = {
    "bid_evidence_semantic_indexes",
    "bid_evidence_semantic_entries",
    "bid_evidence_semantic_heads",
}
ENTERPRISE_FACT_LINEAGE_TABLES = {"bid_fact_enterprise_links"}
MVP_RELEASE_CANDIDATE_TABLES = {"bid_mvp_release_candidates"}
ENTERPRISE_BUSINESS_BASELINE_TABLES = {"bid_enterprise_business_baselines"}
ENTERPRISE_EVIDENCE_IMPORT_TABLES = {
    "bid_enterprise_evidence_items",
    "bid_enterprise_evidence_packages",
    "bid_enterprise_evidence_package_items",
}
HARD_GATE_FACT_VERIFICATION_TABLES = {
    "bid_hard_gate_comparison_baselines",
    "bid_hard_gate_comparison_evidence_links",
    "bid_fact_comparison_links",
}


def _alembic_script() -> ScriptDirectory:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _upgrade_calls(path: Path = MIGRATION_PATH) -> list[ast.Call]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    return [node for node in ast.walk(upgrade) if isinstance(node, ast.Call)]


def _op_call_name(call: ast.Call) -> str | None:
    function = call.func
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "op"
    ):
        return function.attr
    return None


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_sql(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    return "\n".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def _tuple_constant(path: Path, constant_name: str) -> tuple[str, ...]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    assignments: dict[str, ast.expr] = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                assignments[node.target.id] = node.value

    def _values(node: ast.expr) -> tuple[str, ...]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return (node.value,)
        if isinstance(node, ast.Name):
            return _values(assignments[node.id])
        if isinstance(node, (ast.Tuple, ast.List)):
            values: list[str] = []
            for item in node.elts:
                if isinstance(item, ast.Starred):
                    values.extend(_values(item.value))
                else:
                    values.extend(_values(item))
            return tuple(values)
        raise AssertionError(f"unsupported tuple constant node: {ast.dump(node)}")

    return _values(assignments[constant_name])


def _load_migration_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"test_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0083_through_0108_form_one_linear_chain_from_confirmed_0082() -> None:
    script = _alembic_script()
    assert script.get_heads() == ["20260818_0108"]
    revision_0083 = script.get_revision("20260810_0083")
    revision_0084 = script.get_revision("20260810_0084")
    revision_0085 = script.get_revision("20260810_0085")
    revision_0086 = script.get_revision("20260810_0086")
    revision_0087 = script.get_revision("20260810_0087")
    revision_0088 = script.get_revision("20260811_0088")
    revision_0089 = script.get_revision("20260811_0089")
    revision_0090 = script.get_revision("20260811_0090")
    revision_0091 = script.get_revision("20260811_0091")
    revision_0092 = script.get_revision("20260811_0092")
    revision_0093 = script.get_revision("20260811_0093")
    revision_0094 = script.get_revision("20260811_0094")
    revision_0095 = script.get_revision("20260812_0095")
    revision_0096 = script.get_revision("20260812_0096")
    revision_0097 = script.get_revision("20260812_0097")
    revision_0098 = script.get_revision("20260812_0098")
    revision_0099 = script.get_revision("20260813_0099")
    revision_0100 = script.get_revision("20260813_0100")
    revision_0101 = script.get_revision("20260813_0101")
    revision_0102 = script.get_revision("20260814_0102")
    revision_0103 = script.get_revision("20260815_0103")
    revision_0104 = script.get_revision("20260817_0104")
    revision_0105 = script.get_revision("20260817_0105")
    revision_0106 = script.get_revision("20260817_0106")
    revision_0107 = script.get_revision("20260817_0107")
    revision_0108 = script.get_revision("20260818_0108")
    assert revision_0083 is not None
    assert revision_0084 is not None
    assert revision_0085 is not None
    assert revision_0086 is not None
    assert revision_0087 is not None
    assert revision_0088 is not None
    assert revision_0089 is not None
    assert revision_0090 is not None
    assert revision_0091 is not None
    assert revision_0092 is not None
    assert revision_0093 is not None
    assert revision_0094 is not None
    assert revision_0095 is not None
    assert revision_0096 is not None
    assert revision_0097 is not None
    assert revision_0098 is not None
    assert revision_0099 is not None
    assert revision_0100 is not None
    assert revision_0101 is not None
    assert revision_0102 is not None
    assert revision_0103 is not None
    assert revision_0104 is not None
    assert revision_0105 is not None
    assert revision_0106 is not None
    assert revision_0107 is not None
    assert revision_0108 is not None
    assert revision_0083.down_revision == "20260808_0082"
    assert revision_0084.down_revision == "20260810_0083"
    assert revision_0085.down_revision == "20260810_0084"
    assert revision_0086.down_revision == "20260810_0085"
    assert revision_0087.down_revision == "20260810_0086"
    assert revision_0088.down_revision == "20260810_0087"
    assert revision_0089.down_revision == "20260811_0088"
    assert revision_0090.down_revision == "20260811_0089"
    assert revision_0091.down_revision == "20260811_0090"
    assert revision_0092.down_revision == "20260811_0091"
    assert revision_0093.down_revision == "20260811_0092"
    assert revision_0094.down_revision == "20260811_0093"
    assert revision_0095.down_revision == "20260811_0094"
    assert revision_0096.down_revision == "20260812_0095"
    assert revision_0097.down_revision == "20260812_0096"
    assert revision_0098.down_revision == "20260812_0097"
    assert revision_0099.down_revision == "20260812_0098"
    assert revision_0100.down_revision == "20260813_0099"
    assert revision_0101.down_revision == "20260813_0100"
    assert revision_0102.down_revision == "20260813_0101"
    assert revision_0103.down_revision == "20260814_0102"
    assert revision_0104.down_revision == "20260815_0103"
    assert revision_0105.down_revision == "20260817_0104"
    assert revision_0106.down_revision == "20260817_0105"
    assert revision_0107.down_revision == "20260817_0106"
    assert revision_0108.down_revision == "20260817_0107"


def test_0092_creates_only_phase2_parse_authority_and_extends_outbox() -> None:
    calls = _upgrade_calls(PARSE_AUTHORITY_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == PARSE_AUTHORITY_TABLES
    assert not any(
        _op_call_name(call) in {"drop_table", "drop_column", "alter_column", "add_column"}
        for call in calls
    )
    assert PARSE_AUTHORITY_TABLES <= set(Base.metadata.tables)
    assert (
        "document_version_id",
        "parser_profile_version",
        "input_hash",
    ) in _unique_columns("bid_document_parse_runs")
    assert ("parse_run_id", "id") in _unique_columns("bid_evidence_fragments")
    assert ("id", "document_version_id") in _unique_columns(
        "bid_evidence_fragments"
    )


def test_0093_creates_lot_detection_lineage_and_refuses_fake_backfill() -> None:
    calls = _upgrade_calls(LOT_AUTHORITY_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == LOT_AUTHORITY_TABLES
    assert not any(_op_call_name(call) == "drop_table" for call in calls)
    source = LOT_AUTHORITY_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "SELECT COUNT(*) FROM bid_lot_candidates" in source
    assert "refuses to fabricate detection/evidence lineage" in source
    assert LOT_AUTHORITY_TABLES <= set(Base.metadata.tables)
    candidate_columns = set(Base.metadata.tables["bid_lot_candidates"].c.keys())
    assert {"detection_run_id", "confidence_level"} <= candidate_columns
    assert ("manifest_id", "input_hash") in _unique_columns(
        "bid_lot_detection_runs"
    )
    assert ("detection_run_id", "normalized_lot_key") in _unique_columns(
        "bid_lot_candidates"
    )


def test_0094_only_extends_outbox_for_run_retry_and_guards_downgrade() -> None:
    calls = _upgrade_calls(RUN_RETRY_EVENT_MIGRATION_PATH)
    assert [_op_call_name(call) for call in calls].count("drop_constraint") == 1
    assert [_op_call_name(call) for call in calls].count("create_check_constraint") == 1
    assert not any(
        _op_call_name(call)
        in {
            "create_table",
            "drop_table",
            "add_column",
            "drop_column",
            "alter_column",
            "create_foreign_key",
        }
        for call in calls
    )
    previous = _tuple_constant(
        RUN_RETRY_EVENT_MIGRATION_PATH,
        "PREVIOUS_OUTBOX_EVENT_TYPES",
    )
    current = _tuple_constant(
        RUN_RETRY_EVENT_MIGRATION_PATH,
        "OUTBOX_EVENT_TYPES",
    )
    assert current == (*previous, "bid.run.retry_requested.v1")
    assert set(current) == set(eventing_models.OUTBOX_EVENT_TYPES) - {
        "bid.run.stale.v1",
        "bid.plan.continuation_requested.v1",
    }
    assert len(current) == len(eventing_models.OUTBOX_EVENT_TYPES) - 2
    source = RUN_RETRY_EVENT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "WHERE event_type = 'bid.run.retry_requested.v1'" in source
    assert "would invalidate persisted run retry-requested events" in source


def test_phase3e_0095_creates_context_tool_authority_and_guards_placeholders() -> None:
    calls = _upgrade_calls(TOOL_CONTEXT_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == TOOL_CONTEXT_TABLES
    operation_names = [_op_call_name(call) for call in calls]
    assert operation_names.count("create_foreign_key") == 1
    assert not any(
        name in {"drop_table", "drop_column", "alter_column", "add_column"}
        for name in operation_names
    )
    source = TOOL_CONTEXT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "WHERE context_manifest_id IS NOT NULL" in source
    assert "refuses to reinterpret legacy checkpoint" in source
    assert "would erase immutable context/tool execution lineage" in source
    assert TOOL_CONTEXT_TABLES <= set(Base.metadata.tables)
    assert ("task_attempt_id", "manifest_hash") in _unique_columns(
        "bid_context_manifests"
    )
    assert ("task_attempt_id", "idempotency_key") in _unique_columns(
        "bid_tool_invocations"
    )
    assert ("invocation_id",) in _unique_columns("bid_tool_results")


def test_phase3f_0096_creates_dispatch_authority_and_guards_lineage() -> None:
    calls = _upgrade_calls(TOOL_EXECUTION_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == TOOL_EXECUTION_TABLES
    source = TOOL_EXECUTION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "uq_bid_async_operations_task_attempt_id" in source
    assert "would erase immutable Tool dispatch lineage" in source
    assert TOOL_EXECUTION_TABLES <= set(Base.metadata.tables)
    assert ("invocation_id",) in _unique_columns("bid_tool_dispatches")
    assert ("async_operation_id",) in _unique_columns("bid_tool_dispatches")
    assert ("dispatch_id", "fencing_token") in _unique_columns(
        "bid_tool_dispatch_attempts"
    )
    assert ("execution_key",) in _unique_columns("bid_tool_dispatch_attempts")
    assert ("task_id", "task_attempt_id", "id") in _unique_columns(
        "bid_async_operations"
    )

    checkpoint_fk = next(
        item
        for item in Base.metadata.tables["bid_checkpoints"].foreign_key_constraints
        if item.name == "fk_bid_checkpoints_context_manifest"
    )
    assert [element.target_fullname for element in checkpoint_fk.elements] == [
        "bid_context_manifests.id"
    ]


def test_phase3g_0097_creates_run_validation_authority_and_guards_lineage() -> None:
    calls = _upgrade_calls(RUN_VALIDATION_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == RUN_VALIDATION_TABLES
    source = RUN_VALIDATION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "bid.run.stale.v1" in source
    assert "would erase immutable Run validation/convergence lineage" in source
    assert RUN_VALIDATION_TABLES <= set(Base.metadata.tables)
    assert ("run_id",) in _unique_columns("bid_run_validations")
    assert ("source_event_id",) in _unique_columns("bid_run_validations")
    assert ("validation_id", "fencing_token") in _unique_columns(
        "bid_run_validation_attempts"
    )
    assert ("execution_key",) in _unique_columns("bid_run_validation_attempts")


def test_phase4a1_0098_only_extends_outbox_and_guards_lineage() -> None:
    calls = _upgrade_calls(PLAN_CONTINUATION_MIGRATION_PATH)
    assert not any(
        _op_call_name(call)
        in {
            "create_table",
            "drop_table",
            "add_column",
            "drop_column",
            "alter_column",
            "create_foreign_key",
        }
        for call in calls
    )
    previous = _tuple_constant(
        PLAN_CONTINUATION_MIGRATION_PATH,
        "PREVIOUS_OUTBOX_EVENT_TYPES",
    )
    current = _tuple_constant(
        PLAN_CONTINUATION_MIGRATION_PATH,
        "OUTBOX_EVENT_TYPES",
    )
    assert current == (*previous, "bid.plan.continuation_requested.v1")
    assert set(current) == set(eventing_models.OUTBOX_EVENT_TYPES)
    source = PLAN_CONTINUATION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "WHERE event_type = 'bid.plan.continuation_requested.v1'" in source
    assert "bid.plan.commit.envelope.v2" in source
    assert "would invalidate persisted Plan Continuation or SkillBinding lineage" in source


def test_phase4a2_0099_creates_only_model_execution_authority() -> None:
    calls = _upgrade_calls(MODEL_EXECUTION_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == MODEL_EXECUTION_TABLES
    assert not any(
        _op_call_name(call)
        in {"drop_table", "add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert MODEL_EXECUTION_TABLES <= set(Base.metadata.tables)
    assert ("task_id", "action_seq") in _unique_columns("bid_model_calls")
    assert ("task_id", "idempotency_key") in _unique_columns("bid_model_calls")
    assert ("async_operation_id",) in _unique_columns("bid_model_calls")
    assert ("task_attempt_id", "id") in _unique_columns("bid_checkpoints")
    assert ("model_call_id", "attempt_no") in _unique_columns(
        "bid_model_call_attempts"
    )
    assert ("provider_request_id",) in _unique_columns("bid_model_call_attempts")
    assert ("model_call_id",) in _unique_columns("bid_model_results")
    call_columns = Base.metadata.tables["bid_model_calls"].c
    result_columns = Base.metadata.tables["bid_model_results"].c
    assert "reserved_cost_microunits" in call_columns
    assert "actual_cost_microunits" in call_columns
    assert "actual_cost_microunits" in result_columns
    assert any(
        tuple(element.parent.name for element in constraint.elements)
        == ("task_id", "source_task_attempt_id")
        for constraint in Base.metadata.tables["bid_model_results"].foreign_key_constraints
    )
    assert any(
        tuple(element.parent.name for element in constraint.elements)
        == ("task_attempt_id", "checkpoint_id")
        for constraint in Base.metadata.tables["bid_model_calls"].foreign_key_constraints
    )
    source = MODEL_EXECUTION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase immutable ModelCall/Attempt/Result lineage" in source


def test_mvp1_0100_creates_fact_authority_and_guards_downgrade() -> None:
    calls = _upgrade_calls(FACT_AUTHORITY_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == FACT_AUTHORITY_TABLES
    assert not any(
        _op_call_name(call) in {"drop_table", "add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert FACT_AUTHORITY_TABLES <= set(Base.metadata.tables)
    assert ("run_id", "assertion_hash") in _unique_columns("bid_fact_assertions")
    assert ("assertion_id", "evidence_fragment_id") in _unique_columns(
        "bid_fact_evidence_links"
    ) or Base.metadata.tables["bid_fact_evidence_links"].primary_key.columns.keys() == [
        "assertion_id",
        "evidence_fragment_id",
    ]
    assert ("run_id", "fact_slot") in _unique_columns("bid_fact_coverages")
    assert ("run_id", "id") in _unique_columns("bid_resolved_facts")
    source = FACT_AUTHORITY_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase immutable fact lineage" in source


def test_mvp1_0101_creates_report_authority_and_guards_downgrade() -> None:
    calls = _upgrade_calls(REPORT_AUTHORITY_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == REPORT_AUTHORITY_TABLES
    assert not any(
        _op_call_name(call) in {"drop_table", "add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert REPORT_AUTHORITY_TABLES <= set(Base.metadata.tables)
    assert ("run_id", "gate_code") in _unique_columns("bid_hard_gate_results")
    assert ("run_id",) in _unique_columns("bid_preliminary_decisions")
    assert ("run_id", "claim_order") in _unique_columns("bid_report_claims")
    assert ("run_id",) in _unique_columns("bid_report_validations")
    assert ("assessment_id", "report_version") in _unique_columns(
        "bid_preliminary_reports"
    )
    source = REPORT_AUTHORITY_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase immutable preliminary-report lineage" in source


def test_pdf_c3_0102_creates_retrieval_authority_and_guards_downgrade() -> None:
    calls = _upgrade_calls(EVIDENCE_RETRIEVAL_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == EVIDENCE_RETRIEVAL_TABLES
    assert not any(
        _op_call_name(call) in {"drop_table", "add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert EVIDENCE_RETRIEVAL_TABLES <= set(Base.metadata.tables)
    assert (
        "document_version_id",
        "parse_run_id",
        "retrieval_profile_version",
    ) in _unique_columns("bid_evidence_retrieval_indexes")
    assert (
        "document_version_id",
        "retrieval_profile_version",
        "id",
        "parse_run_id",
    ) in _unique_columns("bid_evidence_retrieval_indexes")
    assert ("index_id", "retrieval_child_id") in _unique_columns(
        "bid_evidence_retrieval_entries"
    )
    source = EVIDENCE_RETRIEVAL_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase role-aware retrieval-index lineage" in source


def test_pdf_c3_0102_executes_and_guards_downgrade_on_isolated_sqlite(
    tmp_path,
) -> None:
    database_path = tmp_path / "pdf-c3-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for name, table in Base.metadata.tables.items()
            if name not in EVIDENCE_RETRIEVAL_TABLES
        ],
    )
    migration = _load_migration_module(EVIDENCE_RETRIEVAL_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            migration.op = operations
            migration.context = SimpleNamespace(is_offline_mode=lambda: False)
            migration.upgrade()
            assert EVIDENCE_RETRIEVAL_TABLES <= set(
                inspect(connection).get_table_names()
            )
            connection.execute(
                text(
                    "INSERT INTO bid_evidence_retrieval_indexes "
                    "(id, document_version_id, parse_run_id, retrieval_profile_version, "
                    "role_contract_version, source_result_hash, input_hash, status, "
                    "requested_at) VALUES ('index-test', 'version-test', 'parse-test', "
                    "'bid-evidence-retrieval-profile-v2-role-aware', "
                    "'bid.evidence.chunk.v2', :source_hash, :input_hash, 'queued', "
                    "CURRENT_TIMESTAMP)"
                ),
                {"source_hash": "a" * 64, "input_hash": "b" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0102 downgrade would erase role-aware retrieval-index lineage",
            ):
                migration.downgrade()
            connection.execute(text("DELETE FROM bid_evidence_retrieval_indexes"))
            migration.downgrade()
            assert EVIDENCE_RETRIEVAL_TABLES.isdisjoint(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()


def test_rq2a_0103_creates_semantic_authority_and_guards_downgrade() -> None:
    calls = _upgrade_calls(SEMANTIC_RETRIEVAL_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == SEMANTIC_RETRIEVAL_TABLES
    assert not any(
        _op_call_name(call)
        in {"drop_table", "add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert SEMANTIC_RETRIEVAL_TABLES <= set(Base.metadata.tables)
    assert (
        "retrieval_index_id",
        "semantic_profile_version",
    ) in _unique_columns("bid_evidence_semantic_indexes")
    assert (
        "document_version_id",
        "semantic_profile_version",
        "id",
        "retrieval_index_id",
    ) in _unique_columns("bid_evidence_semantic_indexes")
    assert (
        "semantic_index_id",
        "provider_record_id",
    ) in _unique_columns("bid_evidence_semantic_entries")
    source = SEMANTIC_RETRIEVAL_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase semantic-index lineage" in source


def test_rq2a_0103_executes_and_guards_downgrade_on_isolated_sqlite(
    tmp_path,
) -> None:
    database_path = tmp_path / "rq2a-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for name, table in Base.metadata.tables.items()
            if name not in SEMANTIC_RETRIEVAL_TABLES
        ],
    )
    migration = _load_migration_module(SEMANTIC_RETRIEVAL_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            migration.op = operations
            migration.context = SimpleNamespace(is_offline_mode=lambda: False)
            migration.upgrade()
            assert SEMANTIC_RETRIEVAL_TABLES <= set(
                inspect(connection).get_table_names()
            )
            connection.execute(
                text(
                    "INSERT INTO bid_evidence_retrieval_indexes "
                    "(id, document_version_id, parse_run_id, retrieval_profile_version, "
                    "role_contract_version, source_result_hash, input_hash, status, "
                    "entry_count, result_hash, requested_at, finished_at) VALUES "
                    "('retrieval-test', 'version-test', 'parse-test', "
                    "'bid-evidence-retrieval-profile-v2-role-aware', "
                    "'bid.evidence.chunk.v2', :parse_hash, :retrieval_input, 'ready', "
                    "1, :retrieval_result, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "parse_hash": "a" * 64,
                    "retrieval_input": "b" * 64,
                    "retrieval_result": "c" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO bid_evidence_semantic_indexes "
                    "(id, document_version_id, retrieval_index_id, "
                    "retrieval_profile_version, semantic_profile_version, provider_id, "
                    "embedding_model_id, embedding_model_revision, embedding_dimension, "
                    "distance_metric, normalized_embeddings, vector_namespace, "
                    "provider_request_id, source_result_hash, source_entry_count, "
                    "input_hash, status, requested_at) VALUES "
                    "('semantic-test', 'version-test', 'retrieval-test', "
                    "'bid-evidence-retrieval-profile-v2-role-aware', "
                    "'bid-evidence-semantic-profile-v1-rq2a-bce', 'bce-milvus', "
                    "'maidalun1020/bce-embedding-base_v1', :revision, 768, 'COSINE', 1, "
                    ":namespace, :request_id, :source_hash, 1, :input_hash, 'queued', "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "revision": "9c0d82af44af61abe171ffae23fde5740c0ec1a8",
                    "namespace": "bid-sem-" + "d" * 64,
                    "request_id": "bid-semantic-index:" + "d" * 64,
                    "source_hash": "c" * 64,
                    "input_hash": "d" * 64,
                },
            )
            with pytest.raises(
                RuntimeError,
                match="0103 downgrade would erase semantic-index lineage",
            ):
                migration.downgrade()
            connection.execute(text("DELETE FROM bid_evidence_semantic_indexes"))
            migration.downgrade()
            assert SEMANTIC_RETRIEVAL_TABLES.isdisjoint(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()


def test_phase4c1_0104_creates_only_enterprise_fact_lineage_and_guards_downgrade() -> None:
    calls = _upgrade_calls(ENTERPRISE_FACT_LINEAGE_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == ENTERPRISE_FACT_LINEAGE_TABLES
    assert not any(
        _op_call_name(call) in {"add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert ENTERPRISE_FACT_LINEAGE_TABLES <= set(Base.metadata.tables)
    table = Base.metadata.tables["bid_fact_enterprise_links"]
    assert {column.name for column in table.primary_key.columns} == {
        "assertion_id",
        "snapshot_record_id",
    }
    source = ENTERPRISE_FACT_LINEAGE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase enterprise snapshot to FactAssertion lineage" in source


def test_phase4c1_0104_executes_and_guards_downgrade_on_isolated_sqlite(
    tmp_path,
) -> None:
    database_path = tmp_path / "phase4c1-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for name, table in Base.metadata.tables.items()
            if name not in ENTERPRISE_FACT_LINEAGE_TABLES
        ],
    )
    migration = _load_migration_module(ENTERPRISE_FACT_LINEAGE_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            migration.op = operations
            migration.context = SimpleNamespace(is_offline_mode=lambda: False)
            migration.upgrade()
            assert ENTERPRISE_FACT_LINEAGE_TABLES <= set(
                inspect(connection).get_table_names()
            )
            connection.execute(
                text(
                    "INSERT INTO bid_fact_enterprise_links "
                    "(assertion_id, snapshot_record_id, record_type, source_record_id, "
                    "source_version, payload_hash, link_hash, created_at) VALUES "
                    "('assertion-test', 'record-test', 'I01', 'source-test', "
                    "'v1', :payload_hash, :link_hash, CURRENT_TIMESTAMP)"
                ),
                {"payload_hash": "a" * 64, "link_hash": "b" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0104 downgrade would erase enterprise snapshot to FactAssertion lineage",
            ):
                migration.downgrade()
            connection.execute(text("DELETE FROM bid_fact_enterprise_links"))
            migration.downgrade()
            assert ENTERPRISE_FACT_LINEAGE_TABLES.isdisjoint(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()


def test_phase4c3_0105_creates_only_immutable_release_authority() -> None:
    calls = _upgrade_calls(MVP_RELEASE_CANDIDATE_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == MVP_RELEASE_CANDIDATE_TABLES
    assert not any(
        _op_call_name(call) in {"add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert MVP_RELEASE_CANDIDATE_TABLES <= set(Base.metadata.tables)
    table = Base.metadata.tables["bid_mvp_release_candidates"]
    assert {column.name for column in table.primary_key.columns} == {"id"}
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_bid_mvp_release_candidates_run",
        "uq_bid_mvp_release_candidates_candidate_hash",
        "uq_bid_mvp_release_candidates_hash",
        "ck_bid_mvp_release_candidates_status",
    }
    source = MVP_RELEASE_CANDIDATE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase immutable MVP release acceptance lineage" in source


def test_phase4c3_0105_executes_and_guards_downgrade_on_isolated_sqlite(
    tmp_path,
) -> None:
    database_path = tmp_path / "phase4c3-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    migration = _load_migration_module(MVP_RELEASE_CANDIDATE_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            migration.op = operations
            migration.context = SimpleNamespace(is_offline_mode=lambda: False)
            migration.upgrade()
            assert MVP_RELEASE_CANDIDATE_TABLES <= set(
                inspect(connection).get_table_names()
            )
            connection.execute(
                text(
                    "INSERT INTO bid_mvp_release_candidates "
                    "(id, version, assessment_id, run_id, report_id, run_validation_id, "
                    "enterprise_snapshot_id, status, acceptance_outcome, reviewer_id, "
                    "review_note, review_json, source_hashes_json, manifest_json, "
                    "candidate_hash, release_hash, reviewed_at, created_at) VALUES "
                    "('rc-test', 'mvp-rc-test', 'assessment-test', 'run-test', "
                    "'report-test', 'validation-test', 'enterprise-test', 'frozen', "
                    "'accepted', 1, 'reviewed', '{}', '{}', '{}', :candidate_hash, "
                    ":release_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"candidate_hash": "a" * 64, "release_hash": "b" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0105 downgrade would erase immutable MVP release acceptance lineage",
            ):
                migration.downgrade()
            connection.execute(text("DELETE FROM bid_mvp_release_candidates"))
            migration.downgrade()
            assert MVP_RELEASE_CANDIDATE_TABLES.isdisjoint(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()


def test_phase4d1_0106_creates_only_immutable_business_baseline_authority() -> None:
    calls = _upgrade_calls(ENTERPRISE_BUSINESS_BASELINE_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == ENTERPRISE_BUSINESS_BASELINE_TABLES
    assert not any(
        _op_call_name(call) in {"add_column", "drop_column", "alter_column"}
        for call in calls
    )
    assert ENTERPRISE_BUSINESS_BASELINE_TABLES <= set(Base.metadata.tables)
    table = Base.metadata.tables["bid_enterprise_business_baselines"]
    assert {column.name for column in table.primary_key.columns} == {"id"}
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_bid_enterprise_business_baselines_snapshot",
        "uq_bid_enterprise_business_baselines_candidate_hash",
        "uq_bid_enterprise_business_baselines_hash",
        "ck_bid_enterprise_business_baselines_status",
    }
    source = ENTERPRISE_BUSINESS_BASELINE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "context.is_offline_mode()" in source
    assert "would erase immutable enterprise business-baseline lineage" in source


def test_phase4d1_0106_executes_and_guards_downgrade_on_isolated_sqlite(
    tmp_path,
) -> None:
    database_path = tmp_path / "phase4d1-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    migration = _load_migration_module(ENTERPRISE_BUSINESS_BASELINE_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            migration.op = operations
            migration.context = SimpleNamespace(is_offline_mode=lambda: False)
            migration.upgrade()
            assert ENTERPRISE_BUSINESS_BASELINE_TABLES <= set(
                inspect(connection).get_table_names()
            )
            connection.execute(
                text(
                    "INSERT INTO bid_enterprise_business_baselines "
                    "(id, version, snapshot_id, status, verification_outcome, reviewer_id, "
                    "review_note, slot_reviews_json, source_hashes_json, candidate_hash, "
                    "baseline_hash, reviewed_at, created_at) VALUES "
                    "('baseline-test', 'business-v1', 'enterprise-test', 'frozen', "
                    "'verified', 1, 'reviewed', '{}', '{}', :candidate_hash, "
                    ":baseline_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"candidate_hash": "a" * 64, "baseline_hash": "b" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0106 downgrade would erase immutable enterprise business-baseline lineage",
            ):
                migration.downgrade()
            connection.execute(text("DELETE FROM bid_enterprise_business_baselines"))
            migration.downgrade()
            assert ENTERPRISE_BUSINESS_BASELINE_TABLES.isdisjoint(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()


def test_phase4d2_0107_adds_only_evidence_authority_and_baseline_lineage() -> None:
    calls = _upgrade_calls(ENTERPRISE_EVIDENCE_IMPORT_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == ENTERPRISE_EVIDENCE_IMPORT_TABLES
    assert ENTERPRISE_EVIDENCE_IMPORT_TABLES <= set(Base.metadata.tables)
    baseline = Base.metadata.tables["bid_enterprise_business_baselines"]
    assert {"evidence_package_id", "evidence_package_hash"} <= {
        column.name for column in baseline.columns
    }
    source = ENTERPRISE_EVIDENCE_IMPORT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'batch_alter_table("bid_enterprise_business_baselines")' in source
    assert "context.is_offline_mode()" in source
    assert "would erase immutable enterprise-evidence lineage" in source


def test_phase4d3_0108_adds_only_comparison_authority_and_run_binding() -> None:
    calls = _upgrade_calls(HARD_GATE_FACT_VERIFICATION_MIGRATION_PATH)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == HARD_GATE_FACT_VERIFICATION_TABLES
    assert HARD_GATE_FACT_VERIFICATION_TABLES <= set(Base.metadata.tables)
    run = Base.metadata.tables["bid_analysis_runs"]
    assert {
        "hard_gate_comparison_baseline_id",
        "hard_gate_comparison_baseline_hash",
    } <= {column.name for column in run.columns}
    source = HARD_GATE_FACT_VERIFICATION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'revision: str = "20260818_0108"' in source
    assert 'down_revision: Union[str, None] = "20260817_0107"' in source
    assert "context.is_offline_mode()" in source
    assert "would erase immutable fact-verification lineage" in source


def test_phase4d3_0108_executes_and_guards_downgrade_on_isolated_sqlite(
    tmp_path,
) -> None:
    database_path = tmp_path / "phase4d3-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    migration = _load_migration_module(HARD_GATE_FACT_VERIFICATION_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            # 0108 only needs the 0107 run-table surface for its batch alter.
            # SQLite deliberately keeps FK enforcement disabled in this
            # isolated migration test, matching the preceding revision tests.
            for statement in (
                "CREATE TABLE bid_assessment_scopes ("
                "assessment_id VARCHAR(36) NOT NULL, id VARCHAR(36) NOT NULL, "
                "PRIMARY KEY (assessment_id, id))",
                "CREATE TABLE bid_document_manifests ("
                "assessment_id VARCHAR(36) NOT NULL, id VARCHAR(36) NOT NULL, "
                "PRIMARY KEY (assessment_id, id))",
                "CREATE TABLE bid_enterprise_snapshots ("
                "id VARCHAR(36) NOT NULL PRIMARY KEY)",
                "CREATE TABLE bid_enterprise_business_baselines ("
                "id VARCHAR(36) NOT NULL PRIMARY KEY)",
                "CREATE TABLE bid_enterprise_evidence_packages ("
                "id VARCHAR(36) NOT NULL PRIMARY KEY)",
                "CREATE TABLE bid_enterprise_evidence_items ("
                "id VARCHAR(36) NOT NULL PRIMARY KEY)",
                "CREATE TABLE bid_evidence_fragments ("
                "id VARCHAR(36) NOT NULL PRIMARY KEY)",
                "CREATE TABLE bid_fact_assertions ("
                "id VARCHAR(36) NOT NULL PRIMARY KEY)",
                "CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY)",
            ):
                connection.execute(text(statement))
            connection.execute(
                text(
                    "CREATE TABLE bid_analysis_runs ("
                    "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                    "assessment_id VARCHAR(36) NOT NULL, "
                    "UNIQUE (assessment_id, id))"
                )
            )
            operations = Operations(MigrationContext.configure(connection))
            migration.op = operations
            migration.context = SimpleNamespace(is_offline_mode=lambda: False)
            migration.upgrade()

            assert HARD_GATE_FACT_VERIFICATION_TABLES <= set(
                inspect(connection).get_table_names()
            )
            run_columns = {
                column["name"]
                for column in inspect(connection).get_columns("bid_analysis_runs")
            }
            assert {
                "hard_gate_comparison_baseline_id",
                "hard_gate_comparison_baseline_hash",
            } <= run_columns

            connection.execute(
                text(
                    "INSERT INTO bid_analysis_runs "
                    "(id, assessment_id, hard_gate_comparison_baseline_id, "
                    "hard_gate_comparison_baseline_hash) VALUES "
                    "('run-test', 'assessment-test', 'baseline-test', :baseline_hash)"
                ),
                {"baseline_hash": "a" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0108 downgrade would erase Run comparison-baseline lineage",
            ):
                migration.downgrade()
            connection.execute(
                text(
                    "UPDATE bid_analysis_runs SET "
                    "hard_gate_comparison_baseline_id = NULL, "
                    "hard_gate_comparison_baseline_hash = NULL"
                )
            )

            connection.execute(
                text(
                    "INSERT INTO bid_hard_gate_comparison_baselines "
                    "(id, version, assessment_id, source_run_id, scope_id, "
                    "manifest_id, manifest_hash, scope_hash, "
                    "enterprise_snapshot_id, enterprise_snapshot_hash, "
                    "business_baseline_id, business_baseline_hash, "
                    "evidence_package_id, evidence_package_hash, status, "
                    "verification_outcome, reviewer_id, review_note, facts_json, "
                    "source_hashes_json, candidate_hash, baseline_hash, reviewed_at, "
                    "created_at) VALUES "
                    "('baseline-test', 'comparison-v1', 'assessment-test', "
                    "'run-test', 'scope-test', 'manifest-test', :manifest_hash, "
                    ":scope_hash, 'snapshot-test', :snapshot_hash, "
                    "'business-test', :business_hash, 'package-test', "
                    ":package_hash, 'frozen', 'verified', 1, 'reviewed', '[]', "
                    "'{}', :candidate_hash, :baseline_hash, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "manifest_hash": "1" * 64,
                    "scope_hash": "2" * 64,
                    "snapshot_hash": "3" * 64,
                    "business_hash": "4" * 64,
                    "package_hash": "5" * 64,
                    "candidate_hash": "6" * 64,
                    "baseline_hash": "7" * 64,
                },
            )
            with pytest.raises(
                RuntimeError,
                match="0108 downgrade would erase immutable fact-verification lineage",
            ):
                migration.downgrade()
            connection.execute(text("DELETE FROM bid_hard_gate_comparison_baselines"))

            migration.downgrade()
            assert HARD_GATE_FACT_VERIFICATION_TABLES.isdisjoint(
                inspect(connection).get_table_names()
            )
            downgraded_columns = {
                column["name"]
                for column in inspect(connection).get_columns("bid_analysis_runs")
            }
            assert {
                "hard_gate_comparison_baseline_id",
                "hard_gate_comparison_baseline_hash",
            }.isdisjoint(downgraded_columns)
    finally:
        engine.dispose()


def test_mvp1_0100_0101_execute_and_guard_downgrade_on_isolated_sqlite(tmp_path) -> None:
    database_path = tmp_path / "mvp1-migration-execution.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    excluded = FACT_AUTHORITY_TABLES | REPORT_AUTHORITY_TABLES
    Base.metadata.create_all(
        engine,
        tables=[table for name, table in Base.metadata.tables.items() if name not in excluded],
    )
    migration_0100 = _load_migration_module(FACT_AUTHORITY_MIGRATION_PATH)
    migration_0101 = _load_migration_module(REPORT_AUTHORITY_MIGRATION_PATH)
    try:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            for module in (migration_0100, migration_0101):
                module.op = operations
                module.context = SimpleNamespace(is_offline_mode=lambda: False)
                module.upgrade()
            assert excluded <= set(inspect(connection).get_table_names())

            connection.execute(
                text(
                    "INSERT INTO bid_hard_gate_results "
                    "(id, run_id, task_id, gate_code, status, severity, "
                    "reason_codes_json, input_fact_ids_json, details_json, result_hash) "
                    "VALUES ('gate-test', 'run-test', 'task-test', 'HG01', 'unknown', "
                    "'block', '[]', '[]', '{}', :result_hash)"
                ),
                {"result_hash": "a" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0101 downgrade would erase immutable preliminary-report lineage",
            ):
                migration_0101.downgrade()
            connection.execute(text("DELETE FROM bid_hard_gate_results"))
            migration_0101.downgrade()
            assert REPORT_AUTHORITY_TABLES.isdisjoint(inspect(connection).get_table_names())

            connection.execute(
                text(
                    "INSERT INTO bid_fact_coverages "
                    "(id, run_id, fact_slot, status, assertion_count, "
                    "reason_codes_json, coverage_hash) "
                    "VALUES ('coverage-test', 'run-test', 'test.slot', 'missing', 0, "
                    "'[]', :coverage_hash)"
                ),
                {"coverage_hash": "b" * 64},
            )
            with pytest.raises(
                RuntimeError,
                match="0100 downgrade would erase immutable fact lineage",
            ):
                migration_0100.downgrade()
            connection.execute(text("DELETE FROM bid_fact_coverages"))
            migration_0100.downgrade()
            assert FACT_AUTHORITY_TABLES.isdisjoint(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_0083_upgrade_only_creates_the_reviewed_foundation_tables() -> None:
    calls = _upgrade_calls()
    assert not any(_op_call_name(call) in {"drop_table", "drop_column"} for call in calls)
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == FOUNDATION_TABLES


def test_0084_upgrade_only_creates_the_reviewed_config_tables() -> None:
    calls = _upgrade_calls(CONFIG_MIGRATION_PATH)
    assert not any(
        _op_call_name(call) in {"drop_table", "drop_column", "alter_column", "add_column"}
        for call in calls
    )
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == CONFIG_TABLES


def test_0085_upgrade_only_creates_reviewed_runtime_tables_and_pointer_constraints() -> None:
    calls = _upgrade_calls(RUNTIME_MIGRATION_PATH)
    assert not any(
        _op_call_name(call) in {"drop_table", "drop_column", "alter_column", "add_column"}
        for call in calls
    )
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == RUNTIME_TABLES
    created_foreign_keys = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_foreign_key"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_foreign_keys == {
        "fk_bid_assessments_active_run",
        "fk_bid_tasks_current_attempt",
    }


def test_0086_upgrade_only_creates_reviewed_eventing_and_audit_tables() -> None:
    calls = _upgrade_calls(EVENTING_MIGRATION_PATH)
    assert not any(
        _op_call_name(call)
        in {"drop_table", "drop_column", "alter_column", "add_column", "create_foreign_key"}
        for call in calls
    )
    created_tables = {
        call.args[0].value
        for call in calls
        if _op_call_name(call) == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert created_tables == EVENTING_TABLES


def test_0087_only_extends_the_outbox_event_type_check() -> None:
    calls = _upgrade_calls(UPLOAD_RECEIVED_EVENT_MIGRATION_PATH)
    operation_names = [_op_call_name(call) for call in calls]
    assert operation_names.count("drop_constraint") == 1
    assert operation_names.count("create_check_constraint") == 1
    assert not any(
        name in {
            "create_table",
            "drop_table",
            "add_column",
            "drop_column",
            "alter_column",
            "create_foreign_key",
        }
        for name in operation_names
    )
    source = UPLOAD_RECEIVED_EVENT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "bid.upload_file.received.v1" in source


def test_0088_only_extends_the_outbox_event_type_check() -> None:
    calls = _upgrade_calls(UPLOAD_REMOVED_EVENT_MIGRATION_PATH)
    operation_names = [_op_call_name(call) for call in calls]
    assert operation_names.count("drop_constraint") == 1
    assert operation_names.count("create_check_constraint") == 1
    assert not any(
        name in {
            "create_table",
            "drop_table",
            "add_column",
            "drop_column",
            "alter_column",
            "create_foreign_key",
        }
        for name in operation_names
    )
    source = UPLOAD_REMOVED_EVENT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "bid.upload_file.removed.v1" in source


def test_0089_only_extends_the_outbox_event_type_check() -> None:
    calls = _upgrade_calls(EVENT_EXTENSION_MIGRATION_PATH)
    operation_names = [_op_call_name(call) for call in calls]
    assert operation_names.count("drop_constraint") == 1
    assert operation_names.count("create_check_constraint") == 1
    assert not any(
        name in {
            "create_table",
            "drop_table",
            "add_column",
            "drop_column",
            "alter_column",
            "create_foreign_key",
        }
        for name in operation_names
    )
    source = EVENT_EXTENSION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "bid.upload_batch.deactivation_added.v1" in source


def test_0090_only_adds_upload_commit_lineage_and_guarded_downgrade() -> None:
    calls = _upgrade_calls(UPLOAD_COMMIT_LINEAGE_MIGRATION_PATH)
    operation_names = [_op_call_name(call) for call in calls]
    assert operation_names.count("add_column") == 3
    assert operation_names.count("create_foreign_key") == 1
    assert operation_names.count("create_unique_constraint") == 1
    assert operation_names.count("create_check_constraint") == 1
    assert not any(
        name in {"create_table", "drop_table", "drop_column", "alter_column"}
        for name in operation_names
    )
    source = UPLOAD_COMMIT_LINEAGE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "committed_manifest_id" in source
    assert "committed_at" in source
    assert "change_note" in source
    assert "would erase API-15 commit lineage" in source
    assert "guarded downgrade requires an online database connection" in source


def test_0091_adds_only_abandonment_timeline_and_outbox_contract() -> None:
    calls = _upgrade_calls(UPLOAD_ABANDONMENT_MIGRATION_PATH)
    operation_names = [_op_call_name(call) for call in calls]
    assert operation_names.count("add_column") == 4
    assert operation_names.count("create_check_constraint") == 3
    assert operation_names.count("create_index") == 1
    assert operation_names.count("drop_constraint") == 1
    assert not any(
        name in {
            "create_table",
            "drop_table",
            "drop_column",
            "alter_column",
            "create_foreign_key",
            "create_unique_constraint",
        }
        for name in operation_names
    )
    source = UPLOAD_ABANDONMENT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "bid.upload_batch.abandoned.v1" in source
    assert "cleanup_after" in source
    assert "cleanup_completed_at" in source
    assert "ix_bid_upload_batches_cleanup_due" in source
    assert "would erase API-16 abandonment lineage" in source
    assert "guarded downgrade requires an online database connection" in source


def test_new_domain_does_not_redefine_or_modify_legacy_bid_tables() -> None:
    for path in (
        MIGRATION_PATH,
        CONFIG_MIGRATION_PATH,
        RUNTIME_MIGRATION_PATH,
        EVENTING_MIGRATION_PATH,
        UPLOAD_RECEIVED_EVENT_MIGRATION_PATH,
        UPLOAD_REMOVED_EVENT_MIGRATION_PATH,
        EVENT_EXTENSION_MIGRATION_PATH,
        UPLOAD_COMMIT_LINEAGE_MIGRATION_PATH,
        UPLOAD_ABANDONMENT_MIGRATION_PATH,
        PARSE_AUTHORITY_MIGRATION_PATH,
        LOT_AUTHORITY_MIGRATION_PATH,
        RUN_RETRY_EVENT_MIGRATION_PATH,
        TOOL_CONTEXT_MIGRATION_PATH,
    ):
        migration_source = path.read_text(encoding="utf-8")
        assert '"bid_parse_runs"' not in migration_source
        assert '"bid_intake_' not in migration_source
        assert '"bid_projects"' not in migration_source
        assert "op.alter_column" not in migration_source
        if path not in {
            UPLOAD_COMMIT_LINEAGE_MIGRATION_PATH,
            UPLOAD_ABANDONMENT_MIGRATION_PATH,
            LOT_AUTHORITY_MIGRATION_PATH,
        }:
            assert "op.add_column" not in migration_source


def test_phase1_design_records_head_decision_and_parse_table_collision() -> None:
    design = DESIGN_PATH.read_text(encoding="utf-8")
    assert "目标 ECS 数据库 `alembic_version` 已只读确认：`20260808_0082`" in design
    assert "`bid_parse_runs`" in design
    assert "`bid_document_parse_runs`" in design
    assert "`bid_parse_runs`、`bid_intake_*`、`bid_evidence_*`" in design


def test_machine_contract_manifest_declares_database_migration_boundary() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["implementation_boundary"] == {
        "runtime_routes_registered": True,
        "database_migration_included": True,
        "legacy_runtime_modified": False,
    }


def test_foundation_models_are_registered_with_uuid_keys_and_mysql_options() -> None:
    assert FOUNDATION_TABLES <= set(Base.metadata.tables)
    for table_name in FOUNDATION_TABLES:
        table = Base.metadata.tables[table_name]
        assert "created_at" in table.c, table_name
        assert [column.name for column in table.primary_key.columns]
        for column in table.primary_key.columns:
            if column.name == "id":
                assert isinstance(column.type, String), table_name
                assert column.type.length == 36, table_name
                assert column.autoincrement is not True, table_name
        mysql_options = table.dialect_options["mysql"]
        assert mysql_options["engine"] == "InnoDB", table_name
        assert mysql_options["charset"] == "utf8mb4", table_name


def test_config_models_are_registered_with_uuid_keys_and_mysql_options() -> None:
    assert CONFIG_TABLES <= set(Base.metadata.tables)
    for table_name in CONFIG_TABLES:
        table = Base.metadata.tables[table_name]
        assert "created_at" in table.c, table_name
        assert isinstance(table.c.id.type, String), table_name
        assert table.c.id.type.length == 36, table_name
        assert table.c.id.primary_key, table_name
        mysql_options = table.dialect_options["mysql"]
        assert mysql_options["engine"] == "InnoDB", table_name
        assert mysql_options["charset"] == "utf8mb4", table_name


def test_runtime_models_are_registered_with_uuid_keys_and_mysql_options() -> None:
    assert RUNTIME_TABLES <= set(Base.metadata.tables)
    for table_name in RUNTIME_TABLES:
        table = Base.metadata.tables[table_name]
        assert "created_at" in table.c, table_name
        assert [column.name for column in table.primary_key.columns], table_name
        if "id" in table.c:
            assert isinstance(table.c.id.type, String), table_name
            assert table.c.id.type.length == 36, table_name
            assert table.c.id.primary_key, table_name
        mysql_options = table.dialect_options["mysql"]
        assert mysql_options["engine"] == "InnoDB", table_name
        assert mysql_options["charset"] == "utf8mb4", table_name


def test_eventing_models_are_registered_with_reviewed_keys_and_mysql_options() -> None:
    assert EVENTING_TABLES <= set(Base.metadata.tables)
    for table_name in EVENTING_TABLES:
        table = Base.metadata.tables[table_name]
        assert "created_at" in table.c, table_name
        assert [column.name for column in table.primary_key.columns], table_name
        if "id" in table.c:
            assert isinstance(table.c.id.type, String), table_name
            assert table.c.id.type.length == 36, table_name
            assert table.c.id.primary_key, table_name
        mysql_options = table.dialect_options["mysql"]
        assert mysql_options["engine"] == "InnoDB", table_name
        assert mysql_options["charset"] == "utf8mb4", table_name


def test_mutable_foundation_entities_have_optimistic_lock_columns() -> None:
    for table_name in MUTABLE_TABLES:
        columns = Base.metadata.tables[table_name].c
        assert {"created_at", "updated_at", "row_version"} <= set(columns.keys()), table_name
        assert columns.row_version.server_default is not None, table_name


def test_immutable_foundation_entities_do_not_expose_update_timestamps() -> None:
    for table_name in FOUNDATION_TABLES - MUTABLE_TABLES:
        assert "updated_at" not in Base.metadata.tables[table_name].c, table_name


def test_mutable_and_immutable_runtime_entities_have_distinct_update_contracts() -> None:
    for table_name in MUTABLE_RUNTIME_TABLES:
        columns = Base.metadata.tables[table_name].c
        assert {"created_at", "updated_at", "row_version"} <= set(columns.keys()), table_name
        assert columns.row_version.server_default is not None, table_name

    for table_name in RUNTIME_TABLES - MUTABLE_RUNTIME_TABLES:
        columns = Base.metadata.tables[table_name].c
        assert "updated_at" not in columns, table_name
        assert "row_version" not in columns, table_name


def test_eventing_mutability_matches_dispatch_and_append_only_boundaries() -> None:
    for table_name in MUTABLE_EVENTING_TABLES:
        columns = Base.metadata.tables[table_name].c
        assert {"created_at", "updated_at", "row_version"} <= set(columns.keys()), table_name

    for table_name in EVENTING_TABLES - MUTABLE_EVENTING_TABLES:
        columns = Base.metadata.tables[table_name].c
        assert "updated_at" not in columns, table_name
        assert "row_version" not in columns, table_name


def test_snapshot_records_are_immutable_but_config_heads_are_version_locked() -> None:
    snapshot_records = Base.metadata.tables["bid_enterprise_snapshot_records"].c
    assert "updated_at" not in snapshot_records
    assert "row_version" not in snapshot_records

    mutable_config_tables = CONFIG_TABLES - {"bid_enterprise_snapshot_records"}
    for table_name in mutable_config_tables:
        columns = Base.metadata.tables[table_name].c
        assert {"created_at", "updated_at", "row_version"} <= set(columns.keys()), table_name


def test_each_artifact_type_has_one_database_enforced_active_slot() -> None:
    for table_name in ARTIFACT_VERSION_TABLES:
        columns = Base.metadata.tables[table_name].c
        assert {
            "version",
            "status",
            "active_slot_key",
            "artifact_ref",
            "artifact_hash",
            "authored_by",
            "reviewed_by",
            "row_version",
        } <= set(columns.keys()), table_name
        assert ("version",) in _unique_columns(table_name), table_name
        assert ("artifact_hash",) in _unique_columns(table_name), table_name
        assert ("active_slot_key",) in _unique_columns(table_name), table_name
        lifecycle_sql = _check_sql(table_name)
        assert "status = 'active'" in lifecycle_sql, table_name
        assert "active_slot_key = 'active'" in lifecycle_sql, table_name
        assert "reviewed_by IS NOT NULL" in lifecycle_sql, table_name
        assert "retired_at IS NOT NULL" in lifecycle_sql, table_name


def test_enterprise_snapshot_freeze_and_record_identity_are_database_enforced() -> None:
    snapshot_checks = _check_sql("bid_enterprise_snapshots")
    assert "snapshot_hash IS NOT NULL" in snapshot_checks
    assert "frozen_by IS NOT NULL" in snapshot_checks
    assert "frozen_at IS NOT NULL" in snapshot_checks
    assert ("version",) in _unique_columns("bid_enterprise_snapshots")
    assert ("snapshot_hash",) in _unique_columns("bid_enterprise_snapshots")
    assert (
        "snapshot_id",
        "record_type",
        "source_record_id",
        "source_version",
    ) in _unique_columns("bid_enterprise_snapshot_records")


def test_model_and_formula_profiles_keep_structured_version_payloads() -> None:
    model_profile = Base.metadata.tables["bid_model_profile_versions"].c
    assert {
        "role_routing_json",
        "provider_identifiers_json",
        "model_identifiers_json",
    } <= set(model_profile.keys())
    assert "rounding_policy_json" in Base.metadata.tables["bid_formula_catalog_versions"].c


def test_assessment_and_upload_status_checks_cover_the_machine_contract() -> None:
    state_contract = json.loads(STATE_PATH.read_text(encoding="utf-8"))["state_machines"]
    assessment_sql = _check_sql("bid_assessments")
    upload_sql = _check_sql("bid_upload_batches")
    for state in state_contract["assessment_lifecycle"]["states"]:
        assert f"'{state}'" in assessment_sql
    for state in state_contract["assessment_business"]["states"]:
        assert f"'{state}'" in assessment_sql
    for state in state_contract["upload_batch"]["states"]:
        assert f"'{state}'" in upload_sql


def test_runtime_status_checks_cover_the_machine_contract() -> None:
    state_contract = json.loads(STATE_PATH.read_text(encoding="utf-8"))["state_machines"]
    table_by_machine = {
        "analysis_run": "bid_analysis_runs",
        "plan_revision": "bid_plan_revisions",
        "task": "bid_tasks",
        "async_operation": "bid_async_operations",
        "question": "bid_question_rounds",
    }
    for machine_name, table_name in table_by_machine.items():
        check_sql = _check_sql(table_name)
        for state in state_contract[machine_name]["states"]:
            assert f"'{state}'" in check_sql, (machine_name, state)

    question_sql = _check_sql("bid_questions")
    for state in state_contract["question"]["states"]:
        assert f"'{state}'" in question_sql, state


def test_run_freezes_all_configuration_versions_and_assessment_owned_inputs() -> None:
    run = Base.metadata.tables["bid_analysis_runs"]
    assert {
        "assessment_id",
        "scope_id",
        "manifest_id",
        "enterprise_snapshot_id",
        "rule_set_id",
        "fact_catalog_version_id",
        "prompt_bundle_id",
        "tool_registry_version_id",
        "model_profile_version_id",
        "formula_catalog_version_id",
        "evaluation_time",
        "input_fingerprint",
        "input_hash",
    } <= set(run.c.keys())

    scope_fk = next(
        constraint
        for constraint in run.foreign_key_constraints
        if constraint.name == "fk_bid_analysis_runs_scope"
    )
    manifest_fk = next(
        constraint
        for constraint in run.foreign_key_constraints
        if constraint.name == "fk_bid_analysis_runs_manifest"
    )
    assert [column.name for column in scope_fk.columns] == ["assessment_id", "scope_id"]
    assert [column.name for column in manifest_fk.columns] == ["assessment_id", "manifest_id"]


def test_runtime_entities_keep_explicit_direct_parent_foreign_keys() -> None:
    expected_targets = {
        ("bid_analysis_runs", "fk_bid_analysis_runs_assessment"): ["bid_assessments.id"],
        ("bid_tasks", "fk_bid_tasks_run"): ["bid_analysis_runs.id"],
        ("bid_async_operations", "fk_bid_async_operations_task"): ["bid_tasks.id"],
        ("bid_question_rounds", "fk_bid_question_rounds_assessment"): [
            "bid_assessments.id"
        ],
        ("bid_answer_sets", "fk_bid_answer_sets_run"): ["bid_analysis_runs.id"],
    }
    for (table_name, constraint_name), targets in expected_targets.items():
        constraint = next(
            item
            for item in Base.metadata.tables[table_name].foreign_key_constraints
            if item.name == constraint_name
        )
        assert [element.target_fullname for element in constraint.elements] == targets


def test_active_run_and_current_attempt_pointers_cannot_cross_aggregate_boundaries() -> None:
    assessment = Base.metadata.tables["bid_assessments"]
    active_run_fk = next(
        constraint
        for constraint in assessment.foreign_key_constraints
        if constraint.name == "fk_bid_assessments_active_run"
    )
    assert [column.name for column in active_run_fk.columns] == ["id", "active_run_id"]
    assert [element.target_fullname for element in active_run_fk.elements] == [
        "bid_analysis_runs.assessment_id",
        "bid_analysis_runs.id",
    ]

    task = Base.metadata.tables["bid_tasks"]
    current_attempt_fk = next(
        constraint
        for constraint in task.foreign_key_constraints
        if constraint.name == "fk_bid_tasks_current_attempt"
    )
    assert [column.name for column in current_attempt_fk.columns] == [
        "id",
        "current_attempt_id",
    ]
    assert [element.target_fullname for element in current_attempt_fk.elements] == [
        "bid_task_attempts.task_id",
        "bid_task_attempts.id",
    ]


def test_attempt_lease_and_checkpoint_fencing_invariants_are_database_enforced() -> None:
    attempt_checks = _check_sql("bid_task_attempts")
    assert "fencing_token >= 1" in attempt_checks
    assert "lease_owner IS NOT NULL" in attempt_checks
    assert "lease_until IS NOT NULL" in attempt_checks
    assert "heartbeat_at IS NOT NULL" in attempt_checks
    assert ("task_id", "attempt_no") in _unique_columns("bid_task_attempts")
    assert ("task_id", "fencing_token") in _unique_columns("bid_task_attempts")

    checkpoint = Base.metadata.tables["bid_checkpoints"]
    assert {
        "task_attempt_id",
        "fencing_token",
        "action_seq",
        "state_json",
        "state_hash",
    } <= set(checkpoint.c.keys())
    assert ("task_attempt_id", "action_seq") in _unique_columns("bid_checkpoints")


def test_plan_and_question_open_slots_are_database_enforced() -> None:
    assert ("run_id", "committed_slot_key") in _unique_columns("bid_plan_revisions")
    plan_checks = _check_sql("bid_plan_revisions")
    assert "committed_slot_key = 'committed'" in plan_checks
    assert "validated_hash IS NOT NULL" in plan_checks

    assert ("run_id", "open_slot_key") in _unique_columns("bid_question_rounds")
    round_checks = _check_sql("bid_question_rounds")
    assert "open_slot_key = 'published'" in round_checks
    assert "answered_at IS NOT NULL" in round_checks
    assert "withdrawn_at IS NOT NULL" in round_checks


def test_question_limit_drafts_and_immutable_answer_sets_are_database_enforced() -> None:
    assert "question_order BETWEEN 1 AND 3" in _check_sql("bid_questions")
    assert ("question_round_id", "question_order") in _unique_columns("bid_questions")
    assert ("question_round_id", "fact_slot") in _unique_columns("bid_questions")
    assert (
        "question_round_id",
        "question_id",
        "actor_id",
    ) in _unique_columns("bid_answer_drafts")
    assert ("question_round_id", "answer_set_hash") in _unique_columns("bid_answer_sets")
    assert ("answer_set_id", "question_id") in _unique_columns("bid_answers")
    assert ("question_id", "version") in _unique_columns("bid_answers")


def test_async_operation_idempotency_is_task_scoped() -> None:
    assert (
        "task_id",
        "operation_type",
        "input_hash",
    ) in _unique_columns("bid_async_operations")


def test_outbox_envelope_and_dispatch_lease_are_database_enforced() -> None:
    outbox = Base.metadata.tables["bid_outbox_events"]
    assert {
        "event_id",
        "event_type",
        "producer",
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "assessment_id",
        "run_id",
        "request_id",
        "causation_event_id",
        "payload_schema",
        "payload_json",
        "payload_hash",
        "dedupe_key",
        "status",
        "available_at",
        "attempts",
        "lease_owner",
        "lease_until",
    } <= set(outbox.c.keys())
    assert ("event_id",) in _unique_columns("bid_outbox_events")
    assert ("producer", "dedupe_key") in _unique_columns("bid_outbox_events")
    checks = _check_sql("bid_outbox_events")
    assert "lease_owner IS NOT NULL" in checks
    assert "lease_until IS NOT NULL" in checks
    assert "published_at IS NOT NULL" in checks
    assert "attempts >= 0" in checks

    run_fk = next(
        constraint
        for constraint in outbox.foreign_key_constraints
        if constraint.name == "fk_bid_outbox_events_run"
    )
    assert [column.name for column in run_fk.columns] == ["assessment_id", "run_id"]


def test_outbox_and_public_event_types_cover_the_machine_event_catalog() -> None:
    catalog = json.loads(EVENT_CATALOG_PATH.read_text(encoding="utf-8"))
    contract_outbox = set(catalog["outbox_events"])
    contract_public = {
        event["event_type"] for event in catalog["public_events"]
    }
    assert set(eventing_models.OUTBOX_EVENT_TYPES) == contract_outbox
    assert set(eventing_models.PUBLIC_EVENT_TYPES) == contract_public
    assert set(
        _tuple_constant(PLAN_CONTINUATION_MIGRATION_PATH, "OUTBOX_EVENT_TYPES")
    ) == contract_outbox
    assert set(
        _tuple_constant(RUN_VALIDATION_MIGRATION_PATH, "OUTBOX_EVENT_TYPES")
    ) == contract_outbox - {"bid.plan.continuation_requested.v1"}
    assert set(
        _tuple_constant(RUN_RETRY_EVENT_MIGRATION_PATH, "OUTBOX_EVENT_TYPES")
    ) == contract_outbox - {
        "bid.run.stale.v1",
        "bid.plan.continuation_requested.v1",
    }
    assert set(
        _tuple_constant(PARSE_AUTHORITY_MIGRATION_PATH, "OUTBOX_EVENT_TYPES")
    ) == contract_outbox - {
        "bid.run.retry_requested.v1",
        "bid.run.stale.v1",
        "bid.plan.continuation_requested.v1",
    }
    assert set(_tuple_constant(EVENTING_MIGRATION_PATH, "PUBLIC_EVENT_TYPES")) == contract_public
    outbox_checks = _check_sql("bid_outbox_events")
    for event_type in catalog["outbox_events"]:
        assert f"'{event_type}'" in outbox_checks, event_type

    public_checks = _check_sql("bid_public_events")
    for event in catalog["public_events"]:
        assert f"'{event['event_type']}'" in public_checks, event["event_type"]


def test_processed_event_primary_key_is_the_consumer_dedupe_boundary() -> None:
    table = Base.metadata.tables["bid_processed_events"]
    assert [column.name for column in table.primary_key.columns] == [
        "consumer_name",
        "event_id",
    ]
    event_fk = next(
        constraint
        for constraint in table.foreign_key_constraints
        if constraint.name == "fk_bid_processed_events_event"
    )
    assert [element.target_fullname for element in event_fk.elements] == [
        "bid_outbox_events.event_id"
    ]
    assert {"result_hash", "processed_at"} <= set(table.c.keys())


def test_public_events_are_ordered_per_assessment_and_projection_is_idempotent() -> None:
    assert ("assessment_id", "sequence_no") in _unique_columns("bid_public_events")
    assert ("event_id",) in _unique_columns("bid_public_events")
    assert (
        "source_event_id",
        "projection_key",
    ) in _unique_columns("bid_public_events")
    checks = _check_sql("bid_public_events")
    assert "sequence_no >= 1" in checks
    assert "resource_version >= 1" in checks
    assert "expires_at > created_at" in checks
    assert "origin_type = 'outbox' AND source_event_id IS NOT NULL" in checks
    assert "origin_type = 'stream_control' AND source_event_id IS NULL" in checks


def test_api_idempotency_scope_request_and_response_snapshot_are_persisted() -> None:
    table = Base.metadata.tables["bid_idempotency_records"]
    assert {
        "actor_id",
        "http_method",
        "route_template",
        "scope",
        "idempotency_key",
        "request_hash",
        "response_status_code",
        "response_snapshot_json",
        "response_ref",
        "response_hash",
        "resource_type",
        "resource_id",
        "status",
        "processing_expires_at",
        "expires_at",
    } <= set(table.c.keys())
    assert (
        "actor_id",
        "scope",
        "idempotency_key",
    ) in _unique_columns("bid_idempotency_records")
    checks = _check_sql("bid_idempotency_records")
    assert "LENGTH(idempotency_key) BETWEEN 16 AND 128" in checks
    assert "response_status_code BETWEEN 100 AND 599" in checks
    assert "response_snapshot_json IS NOT NULL OR response_ref IS NOT NULL" in checks
    assert "request_hash" in table.c


def test_legacy_links_are_typed_and_backed_by_real_foreign_keys() -> None:
    table = Base.metadata.tables["bid_legacy_resource_links"]
    assert (
        "legacy_system",
        "legacy_resource_type",
        "legacy_resource_id",
        "new_resource_type",
        "new_resource_id",
    ) in _unique_columns("bid_legacy_resource_links")
    checks = _check_sql("bid_legacy_resource_links")
    for resource_type in ("assessment", "manifest", "run"):
        assert f"new_resource_type = '{resource_type}'" in checks
    target_names = {
        element.target_fullname
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
    }
    assert {
        "bid_assessments.id",
        "bid_document_manifests.id",
        "bid_analysis_runs.id",
    } <= target_names


def test_audit_log_is_append_only_and_hash_addressed() -> None:
    table = Base.metadata.tables["bid_audit_log"]
    assert {
        "actor_type",
        "actor_id",
        "actor_ref",
        "action",
        "entity_type",
        "entity_id",
        "before_hash",
        "after_hash",
        "request_id",
        "metadata_hash",
        "record_hash",
        "occurred_at",
    } <= set(table.c.keys())
    assert ("record_hash",) in _unique_columns("bid_audit_log")
    assert "updated_at" not in table.c
    assert "row_version" not in table.c
    checks = _check_sql("bid_audit_log")
    assert "actor_type = 'user' AND actor_id IS NOT NULL" in checks
    assert "outcome IN ('succeeded', 'denied', 'failed')" in checks


def test_foundation_uniqueness_and_open_batch_slot_are_database_enforced() -> None:
    assert ("sha256", "size_bytes") in _unique_columns("bid_file_objects")
    assert ("document_id", "version_no") in _unique_columns("bid_document_versions")
    assert ("assessment_id", "version") in _unique_columns("bid_document_manifests")
    assert ("assessment_id", "version") in _unique_columns("bid_assessment_scopes")
    assert ("assessment_id", "open_slot_key") in _unique_columns("bid_upload_batches")
    assert ("committed_manifest_id",) in _unique_columns("bid_upload_batches")
    assert "open_slot_key = purpose" in _check_sql("bid_upload_batches")
    assert "committed_manifest_id IS NULL AND committed_at IS NULL" in _check_sql(
        "bid_upload_batches"
    )


def test_manifest_pointers_cannot_cross_assessment_acl_boundaries() -> None:
    assessment = Base.metadata.tables["bid_assessments"]
    current_manifest_fk = next(
        constraint
        for constraint in assessment.foreign_key_constraints
        if constraint.name == "fk_bid_assessments_current_manifest"
    )
    assert [column.name for column in current_manifest_fk.columns] == ["id", "current_manifest_id"]
    assert [element.target_fullname for element in current_manifest_fk.elements] == [
        "bid_document_manifests.assessment_id",
        "bid_document_manifests.id",
    ]

    upload_batch = Base.metadata.tables["bid_upload_batches"]
    base_manifest_fk = next(
        constraint
        for constraint in upload_batch.foreign_key_constraints
        if constraint.name == "fk_bid_upload_batches_base_manifest"
    )
    assert [column.name for column in base_manifest_fk.columns] == ["assessment_id", "base_manifest_id"]
    committed_manifest_fk = next(
        constraint
        for constraint in upload_batch.foreign_key_constraints
        if constraint.name == "fk_bid_upload_batches_committed_manifest"
    )
    assert [column.name for column in committed_manifest_fk.columns] == [
        "assessment_id",
        "committed_manifest_id",
    ]
    assert "change_note" in Base.metadata.tables["bid_document_manifests"].c


def test_foundation_foreign_keys_never_cascade_delete_evidence_lineage() -> None:
    for table_name in (
        FOUNDATION_TABLES
        | CONFIG_TABLES
        | RUNTIME_TABLES
        | EVENTING_TABLES
        | TOOL_CONTEXT_TABLES
        | TOOL_EXECUTION_TABLES
        | RUN_VALIDATION_TABLES
    ):
        for foreign_key in Base.metadata.tables[table_name].foreign_key_constraints:
            assert foreign_key.ondelete != "CASCADE", (table_name, foreign_key.name)


def test_active_run_pointer_is_completed_by_runtime_revision() -> None:
    assessment = Base.metadata.tables["bid_assessments"]
    active_run_foreign_keys = list(assessment.c.active_run_id.foreign_keys)
    assert len(active_run_foreign_keys) == 1
    assert active_run_foreign_keys[0].target_fullname == "bid_analysis_runs.id"
    assert "0085" in DESIGN_PATH.read_text(encoding="utf-8")
