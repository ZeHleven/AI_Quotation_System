"""add bid assessment enterprise snapshots and config versions

Revision ID: 20260810_0084
Revises: 20260810_0083
Create Date: 2026-08-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0084"
down_revision: Union[str, None] = "20260810_0083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _artifact_version_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("active_slot_key", sa.String(length=32), nullable=True),
        sa.Column("artifact_ref", sa.String(length=512), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "authored_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _artifact_version_constraints(table_name: str) -> tuple[sa.Constraint, ...]:
    short_name = {
        "bid_rule_sets": "bid_rule_sets",
        "bid_fact_catalog_versions": "bid_fact_catalog_versions",
        "bid_prompt_bundles": "bid_prompt_bundles",
        "bid_tool_registry_versions": "bid_tool_registry_versions",
        "bid_model_profile_versions": "bid_model_profile_versions",
        "bid_formula_catalog_versions": "bid_formula_catalog_versions",
    }[table_name]
    return (
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=f"ck_{short_name}_status",
        ),
        sa.CheckConstraint(
            "((status = 'draft' AND active_slot_key IS NULL) "
            "OR (status = 'active' AND active_slot_key = 'active' "
            "AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND activated_at IS NOT NULL) "
            "OR (status = 'retired' AND active_slot_key IS NULL AND retired_at IS NOT NULL))",
            name=f"ck_{short_name}_lifecycle",
        ),
        sa.CheckConstraint("row_version >= 1", name=f"ck_{short_name}_row_version"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{short_name}"),
        sa.UniqueConstraint("version", name=f"uq_{short_name}_version"),
        sa.UniqueConstraint("artifact_hash", name=f"uq_{short_name}_artifact_hash"),
        sa.UniqueConstraint("active_slot_key", name=f"uq_{short_name}_active_slot"),
    )


def _create_artifact_indexes(table_name: str) -> None:
    op.create_index(f"ix_{table_name}_status", table_name, ["status"])
    op.create_index(f"ix_{table_name}_created", table_name, ["created_at"])


def upgrade() -> None:
    op.create_table(
        "bid_enterprise_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("source_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="building", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "frozen_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('building', 'frozen', 'failed', 'retired')",
            name="ck_bid_enterprise_snapshots_status",
        ),
        sa.CheckConstraint(
            "((status IN ('building', 'failed')) "
            "OR (status IN ('frozen', 'retired') AND snapshot_hash IS NOT NULL "
            "AND frozen_by IS NOT NULL AND frozen_at IS NOT NULL))",
            name="ck_bid_enterprise_snapshots_freeze",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_enterprise_snapshots_row_version"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_enterprise_snapshots"),
        sa.UniqueConstraint("version", name="uq_bid_enterprise_snapshots_version"),
        sa.UniqueConstraint("snapshot_hash", name="uq_bid_enterprise_snapshots_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_enterprise_snapshots_status", "bid_enterprise_snapshots", ["status"])
    op.create_index("ix_bid_enterprise_snapshots_as_of", "bid_enterprise_snapshots", ["as_of"])

    op.create_table(
        "bid_enterprise_snapshot_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.String(length=36),
            sa.ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("record_type", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_status", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("object_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_bid_enterprise_snapshot_records_validity",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_enterprise_snapshot_records"),
        sa.UniqueConstraint(
            "snapshot_id",
            "record_type",
            "source_record_id",
            "source_version",
            name="uq_bid_enterprise_snapshot_records_source",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "payload_hash",
            name="uq_bid_enterprise_snapshot_records_payload",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_enterprise_snapshot_records_type",
        "bid_enterprise_snapshot_records",
        ["snapshot_id", "record_type"],
    )
    op.create_index(
        "ix_bid_enterprise_snapshot_records_source",
        "bid_enterprise_snapshot_records",
        ["record_type", "source_record_id"],
    )

    op.create_table(
        "bid_rule_sets",
        *_artifact_version_columns(),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_cases_ref", sa.String(length=512), nullable=False),
        *_artifact_version_constraints("bid_rule_sets"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from",
            name="ck_bid_rule_sets_effective_window",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR effective_from IS NOT NULL",
            name="ck_bid_rule_sets_active_effective_from",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_rule_sets_effective", "bid_rule_sets", ["effective_from", "effective_to"])
    _create_artifact_indexes("bid_rule_sets")

    op.create_table(
        "bid_fact_catalog_versions",
        *_artifact_version_columns(),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        *_artifact_version_constraints("bid_fact_catalog_versions"),
        **TABLE_OPTIONS,
    )
    _create_artifact_indexes("bid_fact_catalog_versions")

    op.create_table(
        "bid_prompt_bundles",
        *_artifact_version_columns(),
        sa.Column("bundle_schema_version", sa.String(length=64), nullable=False),
        *_artifact_version_constraints("bid_prompt_bundles"),
        **TABLE_OPTIONS,
    )
    _create_artifact_indexes("bid_prompt_bundles")

    op.create_table(
        "bid_tool_registry_versions",
        *_artifact_version_columns(),
        sa.Column("registry_schema_version", sa.String(length=64), nullable=False),
        *_artifact_version_constraints("bid_tool_registry_versions"),
        **TABLE_OPTIONS,
    )
    _create_artifact_indexes("bid_tool_registry_versions")

    op.create_table(
        "bid_model_profile_versions",
        *_artifact_version_columns(),
        sa.Column("role_routing_json", sa.JSON(), nullable=False),
        sa.Column("provider_identifiers_json", sa.JSON(), nullable=False),
        sa.Column("model_identifiers_json", sa.JSON(), nullable=False),
        *_artifact_version_constraints("bid_model_profile_versions"),
        **TABLE_OPTIONS,
    )
    _create_artifact_indexes("bid_model_profile_versions")

    op.create_table(
        "bid_formula_catalog_versions",
        *_artifact_version_columns(),
        sa.Column("rounding_policy_json", sa.JSON(), nullable=False),
        *_artifact_version_constraints("bid_formula_catalog_versions"),
        **TABLE_OPTIONS,
    )
    _create_artifact_indexes("bid_formula_catalog_versions")


def downgrade() -> None:
    op.drop_table("bid_formula_catalog_versions")
    op.drop_table("bid_model_profile_versions")
    op.drop_table("bid_tool_registry_versions")
    op.drop_table("bid_prompt_bundles")
    op.drop_table("bid_fact_catalog_versions")
    op.drop_table("bid_rule_sets")
    op.drop_table("bid_enterprise_snapshot_records")
    op.drop_table("bid_enterprise_snapshots")
