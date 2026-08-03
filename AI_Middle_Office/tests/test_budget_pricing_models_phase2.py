from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Numeric, Table, create_engine, inspect
from sqlalchemy.dialects import mysql

from app.models.budget_pricing import (
    BudgetProjectPricingEvent,
    BudgetProjectPricingMatchCandidate,
    BudgetProjectPricingRun,
    BudgetProjectPricingRunLine,
)
from app.models.budget_project import BudgetProjectProfile


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "alembic"
    / "versions"
    / "20260716_0051_add_budget_project_pricing_foundation.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("budget_pricing_0051", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_pre_0051_schema(connection) -> None:
    metadata = MetaData()
    for name in (
        "users",
        "projects",
        "budget_project_import_batches",
        "budget_project_import_revisions",
        "enterprise_quota_versions",
        "enterprise_quota_items",
        "budget_project_profiles",
    ):
        Table(name, metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(connection)


def test_budget_pricing_model_contract_is_numeric_append_only_and_restrictive():
    tables = {
        BudgetProjectPricingRun.__table__,
        BudgetProjectPricingRunLine.__table__,
        BudgetProjectPricingMatchCandidate.__table__,
        BudgetProjectPricingEvent.__table__,
    }
    assert {table.name for table in tables} == {
        "budget_project_pricing_runs",
        "budget_project_pricing_run_lines",
        "budget_project_pricing_match_candidates",
        "budget_project_pricing_events",
    }

    for table in tables:
        assert table.foreign_keys
        assert all(foreign_key.ondelete == "RESTRICT" for foreign_key in table.foreign_keys)
        assert all(len(index.name) <= 64 for index in table.indexes)

    for table in (
        BudgetProjectPricingRunLine.__table__,
        BudgetProjectPricingMatchCandidate.__table__,
        BudgetProjectPricingEvent.__table__,
    ):
        assert "updated_at" not in table.c

    numeric_contract = {
        BudgetProjectPricingRun.__table__.c.priced_subtotal: (24, 6),
        BudgetProjectPricingRun.__table__.c.total_cost: (24, 6),
        BudgetProjectPricingRunLine.__table__.c.calculation_quantity: (20, 6),
        BudgetProjectPricingRunLine.__table__.c.effective_unit_cost: (20, 6),
        BudgetProjectPricingRunLine.__table__.c.line_total: (24, 6),
        BudgetProjectPricingMatchCandidate.__table__.c.candidate_score: (9, 6),
    }
    for column, expected in numeric_contract.items():
        assert isinstance(column.type, Numeric)
        assert (column.type.precision, column.type.scale) == expected

    json_columns = (
        BudgetProjectPricingRun.__table__.c.source_snapshot_json,
        BudgetProjectPricingRun.__table__.c.summary_json,
        BudgetProjectPricingRunLine.__table__.c.source_row_snapshot_json,
        BudgetProjectPricingRunLine.__table__.c.selected_quota_item_snapshot_json,
        BudgetProjectPricingMatchCandidate.__table__.c.quota_item_snapshot_json,
        BudgetProjectPricingMatchCandidate.__table__.c.evidence_json,
        BudgetProjectPricingEvent.__table__.c.event_json,
    )
    for column in json_columns:
        assert column.type.compile(dialect=mysql.dialect()).upper() == "LONGTEXT"

    active_pointer = next(iter(BudgetProjectProfile.__table__.c.active_pricing_run_id.foreign_keys))
    assert active_pointer.target_fullname == "budget_project_pricing_runs.id"
    assert active_pointer.ondelete == "RESTRICT"


def test_0051_migration_up_down_runs_with_only_phase1_and_quota_parents():
    migration = _load_migration()
    assert migration.revision == "20260716_0051"
    assert migration.down_revision == "20260715_0050"

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    for frozen_table in (
        "cost_items",
        "cost_measurements",
        "project_cost_import",
    ):
        assert frozen_table not in source

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _minimal_pre_0051_schema(connection)
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = migration.op
        migration.op = operations
        try:
            migration.upgrade()
            database = inspect(connection)
            assert {
                "budget_project_pricing_runs",
                "budget_project_pricing_run_lines",
                "budget_project_pricing_match_candidates",
                "budget_project_pricing_events",
            }.issubset(database.get_table_names())
            assert "active_pricing_run_id" in {
                column["name"] for column in database.get_columns("budget_project_profiles")
            }
            assert all(
                fk["options"].get("ondelete") == "RESTRICT"
                for table_name in (
                    "budget_project_pricing_runs",
                    "budget_project_pricing_run_lines",
                    "budget_project_pricing_match_candidates",
                    "budget_project_pricing_events",
                )
                for fk in database.get_foreign_keys(table_name)
            )
            assert not database.get_foreign_keys("budget_project_profiles")

            migration.downgrade()
            database = inspect(connection)
            assert not {
                "budget_project_pricing_runs",
                "budget_project_pricing_run_lines",
                "budget_project_pricing_match_candidates",
                "budget_project_pricing_events",
            }.intersection(database.get_table_names())
            assert "active_pricing_run_id" not in {
                column["name"] for column in database.get_columns("budget_project_profiles")
            }
        finally:
            migration.op = original_op
