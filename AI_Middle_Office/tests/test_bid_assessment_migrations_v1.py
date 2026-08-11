from __future__ import annotations

import ast
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, String, UniqueConstraint

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
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == constant_name for target in node.targets)
    )
    return tuple(ast.literal_eval(assignment.value))


def test_0083_through_0091_form_one_linear_chain_from_confirmed_0082() -> None:
    script = _alembic_script()
    assert script.get_heads() == ["20260811_0091"]
    revision_0083 = script.get_revision("20260810_0083")
    revision_0084 = script.get_revision("20260810_0084")
    revision_0085 = script.get_revision("20260810_0085")
    revision_0086 = script.get_revision("20260810_0086")
    revision_0087 = script.get_revision("20260810_0087")
    revision_0088 = script.get_revision("20260811_0088")
    revision_0089 = script.get_revision("20260811_0089")
    revision_0090 = script.get_revision("20260811_0090")
    revision_0091 = script.get_revision("20260811_0091")
    assert revision_0083 is not None
    assert revision_0084 is not None
    assert revision_0085 is not None
    assert revision_0086 is not None
    assert revision_0087 is not None
    assert revision_0088 is not None
    assert revision_0089 is not None
    assert revision_0090 is not None
    assert revision_0091 is not None
    assert revision_0083.down_revision == "20260808_0082"
    assert revision_0084.down_revision == "20260810_0083"
    assert revision_0085.down_revision == "20260810_0084"
    assert revision_0086.down_revision == "20260810_0085"
    assert revision_0087.down_revision == "20260810_0086"
    assert revision_0088.down_revision == "20260810_0087"
    assert revision_0089.down_revision == "20260811_0088"
    assert revision_0090.down_revision == "20260811_0089"
    assert revision_0091.down_revision == "20260811_0090"


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
    ):
        migration_source = path.read_text(encoding="utf-8")
        assert '"bid_parse_runs"' not in migration_source
        assert '"bid_intake_' not in migration_source
        assert '"bid_projects"' not in migration_source
        assert "op.alter_column" not in migration_source
        if path not in {
            UPLOAD_COMMIT_LINEAGE_MIGRATION_PATH,
            UPLOAD_ABANDONMENT_MIGRATION_PATH,
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
        _tuple_constant(UPLOAD_ABANDONMENT_MIGRATION_PATH, "OUTBOX_EVENT_TYPES")
    ) == contract_outbox
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
    for table_name in FOUNDATION_TABLES | CONFIG_TABLES | RUNTIME_TABLES | EVENTING_TABLES:
        for foreign_key in Base.metadata.tables[table_name].foreign_key_constraints:
            assert foreign_key.ondelete != "CASCADE", (table_name, foreign_key.name)


def test_active_run_pointer_is_completed_by_runtime_revision() -> None:
    assessment = Base.metadata.tables["bid_assessments"]
    active_run_foreign_keys = list(assessment.c.active_run_id.foreign_keys)
    assert len(active_run_foreign_keys) == 1
    assert active_run_foreign_keys[0].target_fullname == "bid_analysis_runs.id"
    assert "0085" in DESIGN_PATH.read_text(encoding="utf-8")
