"""add immutable budget project pricing foundation

Revision ID: 20260716_0051
Revises: 20260715_0050
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260716_0051"
down_revision: Union[str, None] = "20260715_0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext() -> sa.types.TypeEngine:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.create_table(
        "budget_project_pricing_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("parent_run_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_run_id", sa.Integer(), nullable=True),
        sa.Column("run_kind", sa.String(length=32), server_default="auto_match", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source_import_batch_id", sa.Integer(), nullable=False),
        sa.Column("source_import_revision_id", sa.Integer(), nullable=False),
        sa.Column("source_import_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_rows_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_json", _longtext(), nullable=False),
        sa.Column("quota_version_id", sa.Integer(), nullable=False),
        sa.Column("quota_version_code", sa.String(length=64), nullable=False),
        sa.Column("quota_version_name", sa.String(length=255), nullable=False),
        sa.Column("quota_source_file_sha256", sa.String(length=64), nullable=True),
        sa.Column("quota_catalog_sha256", sa.String(length=64), nullable=False),
        sa.Column("matching_engine_version", sa.String(length=64), nullable=False),
        sa.Column("pricing_engine_version", sa.String(length=64), nullable=False),
        sa.Column(
            "price_basis",
            sa.String(length=64),
            server_default="enterprise_quota_items.unit_price",
            nullable=False,
        ),
        sa.Column("tax_basis", sa.String(length=32), server_default="source_as_is", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="processing", nullable=False),
        sa.Column("completeness_status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("standard_item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("matched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unit_priced_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("amount_priced_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("review_required_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unmatched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unit_conflict_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quantity_unresolved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_price_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("breakdown_covered_line_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("priced_subtotal", sa.Numeric(precision=24, scale=6), server_default="0", nullable=False),
        sa.Column("total_cost", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("labor_subtotal", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("main_material_subtotal", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("auxiliary_material_subtotal", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("machinery_subtotal", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("summary_json", _longtext(), nullable=False),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("partial_acknowledged_by", sa.Integer(), nullable=True),
        sa.Column("partial_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("partial_acknowledgement_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail_json", _longtext(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["budget_project_pricing_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["superseded_by_run_id"], ["budget_project_pricing_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_import_batch_id"], ["budget_project_import_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_import_revision_id"], ["budget_project_import_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quota_version_id"], ["enterprise_quota_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partial_acknowledged_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["superseded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_uuid", name="uq_budget_pricing_runs_uuid"),
        sa.UniqueConstraint("project_id", "run_number", name="uq_budget_pricing_runs_project_number"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_runs_project_id", ["project_id"]),
        ("ix_budget_project_pricing_runs_superseded_by_run_id", ["superseded_by_run_id"]),
        ("ix_budget_project_pricing_runs_source_import_batch_id", ["source_import_batch_id"]),
        ("ix_budget_project_pricing_runs_source_import_snapshot_sha256", ["source_import_snapshot_sha256"]),
        ("ix_budget_project_pricing_runs_source_rows_sha256", ["source_rows_sha256"]),
        ("ix_budget_project_pricing_runs_quota_catalog_sha256", ["quota_catalog_sha256"]),
        ("ix_budget_project_pricing_runs_status", ["status"]),
        ("ix_budget_project_pricing_runs_completeness_status", ["completeness_status"]),
        ("ix_budget_project_pricing_runs_result_sha256", ["result_sha256"]),
        ("ix_budget_project_pricing_runs_created_by", ["created_by"]),
        ("ix_budget_pricing_runs_project_created", ["project_id", "created_at"]),
        ("ix_budget_pricing_runs_project_status", ["project_id", "status"]),
        ("ix_budget_pricing_runs_source_revision", ["source_import_revision_id"]),
        ("ix_budget_pricing_runs_quota_version", ["quota_version_id"]),
        ("ix_budget_pricing_runs_parent", ["parent_run_id"]),
    ):
        op.create_index(name, "budget_project_pricing_runs", columns)

    op.create_table(
        "budget_project_pricing_run_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_uuid", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source_row_key", sa.String(length=255), nullable=False),
        sa.Column("source_sheet", sa.String(length=255), nullable=False),
        sa.Column("source_raw_row_index", sa.Integer(), nullable=False),
        sa.Column("source_sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_row_snapshot_json", _longtext(), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("spec", _longtext(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("calculation_quantity", sa.Numeric(precision=20, scale=6), server_default="0", nullable=False),
        sa.Column("quantity_status", sa.String(length=32), nullable=False),
        sa.Column("match_status", sa.String(length=32), server_default="unmatched", nullable=False),
        sa.Column("unit_compatibility", sa.String(length=24), nullable=True),
        sa.Column("selected_quota_item_id", sa.Integer(), nullable=True),
        sa.Column("selected_quota_item_snapshot_json", _longtext(), nullable=True),
        sa.Column("selected_quota_item_snapshot_sha256", sa.String(length=64), nullable=True),
        sa.Column("selection_source", sa.String(length=24), nullable=True),
        sa.Column("match_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("match_reason_json", _longtext(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pricing_status", sa.String(length=32), server_default="pending_match", nullable=False),
        sa.Column("quota_unit_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("effective_unit_cost", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("amount_included", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("labor_unit_cost", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("main_material_unit_cost", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("auxiliary_material_unit_cost", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("machinery_unit_cost", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("labor_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("main_material_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("auxiliary_material_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("machinery_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("cost_breakdown_json", _longtext(), nullable=True),
        sa.Column("warnings_json", _longtext(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["budget_project_pricing_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["selected_quota_item_id"], ["enterprise_quota_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("line_uuid", name="uq_budget_pricing_lines_uuid"),
        sa.UniqueConstraint("run_id", "source_row_key", name="uq_budget_pricing_lines_run_source"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_run_lines_run_id", ["run_id"]),
        ("ix_budget_project_pricing_run_lines_source_sheet", ["source_sheet"]),
        ("ix_budget_project_pricing_run_lines_source_row_sha256", ["source_row_sha256"]),
        ("ix_budget_project_pricing_run_lines_item_name", ["item_name"]),
        ("ix_budget_project_pricing_run_lines_match_status", ["match_status"]),
        ("ix_budget_project_pricing_run_lines_selected_quota_item_id", ["selected_quota_item_id"]),
        ("ix_budget_project_pricing_run_lines_pricing_status", ["pricing_status"]),
        ("ix_budget_pricing_lines_run_order", ["run_id", "source_sort_order"]),
        ("ix_budget_pricing_lines_run_match", ["run_id", "match_status"]),
        ("ix_budget_pricing_lines_run_pricing", ["run_id", "pricing_status"]),
        ("ix_budget_pricing_lines_source_location", ["run_id", "source_sheet", "source_raw_row_index"]),
    ):
        op.create_index(name, "budget_project_pricing_run_lines", columns)

    op.create_table(
        "budget_project_pricing_match_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_line_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("quota_item_id", sa.Integer(), nullable=False),
        sa.Column("quota_item_snapshot_json", _longtext(), nullable=False),
        sa.Column("candidate_score", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("name_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("spec_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("unit_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("unit_compatibility", sa.String(length=24), nullable=True),
        sa.Column("selection_eligibility", sa.String(length=24), server_default="review_only", nullable=False),
        sa.Column("is_selected", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("evidence_json", _longtext(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["run_line_id"], ["budget_project_pricing_run_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quota_item_id"], ["enterprise_quota_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_line_id", "rank", name="uq_budget_pricing_candidates_line_rank"),
        sa.UniqueConstraint("run_line_id", "quota_item_id", name="uq_budget_pricing_candidates_line_item"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_match_candidates_run_line_id", ["run_line_id"]),
        ("ix_budget_pricing_candidates_line_score", ["run_line_id", "candidate_score"]),
        ("ix_budget_pricing_candidates_quota_item", ["quota_item_id"]),
    ):
        op.create_index(name, "budget_project_pricing_match_candidates", columns)

    op.create_table(
        "budget_project_pricing_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("event_json", _longtext(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["budget_project_pricing_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_uuid", name="uq_budget_pricing_events_uuid"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_events_project_id", ["project_id"]),
        ("ix_budget_project_pricing_events_run_id", ["run_id"]),
        ("ix_budget_project_pricing_events_event_type", ["event_type"]),
        ("ix_budget_project_pricing_events_actor_id", ["actor_id"]),
        ("ix_budget_pricing_events_run_created", ["run_id", "created_at"]),
        ("ix_budget_pricing_events_project_created", ["project_id", "created_at"]),
        ("ix_budget_pricing_events_actor_created", ["actor_id", "created_at"]),
    ):
        op.create_index(name, "budget_project_pricing_events", columns)

    op.add_column(
        "budget_project_profiles",
        sa.Column("active_pricing_run_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_budget_project_profiles_active_pricing_run_id",
        "budget_project_profiles",
        ["active_pricing_run_id"],
    )
    # SQLite cannot attach a new FK with ALTER TABLE ADD COLUMN.  Runtime
    # tests still exercise the four-table RESTRICT graph; MySQL gets the
    # formal profile pointer constraint used in production.
    if dialect != "sqlite":
        op.create_foreign_key(
            "fk_budget_profile_active_pricing_run",
            "budget_project_profiles",
            "budget_project_pricing_runs",
            ["active_pricing_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    # Clear the outward pointer before any audit/result table is removed.
    bind.execute(sa.text("UPDATE budget_project_profiles SET active_pricing_run_id=NULL"))
    if dialect != "sqlite":
        op.drop_constraint(
            "fk_budget_profile_active_pricing_run",
            "budget_project_profiles",
            type_="foreignkey",
        )
    op.drop_index(
        "ix_budget_project_profiles_active_pricing_run_id",
        table_name="budget_project_profiles",
    )
    op.drop_column("budget_project_profiles", "active_pricing_run_id")
    op.drop_table("budget_project_pricing_events")
    op.drop_table("budget_project_pricing_match_candidates")
    op.drop_table("budget_project_pricing_run_lines")
    op.drop_table("budget_project_pricing_runs")
