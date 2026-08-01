"""add mutable project quota resource workbench

Revision ID: 20260731_0076
Revises: 20260730_0075
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260731_0076"
down_revision: Union[str, None] = "20260730_0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    if "budget_project_quota_snapshots" not in _tables():
        op.create_table(
            "budget_project_quota_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("snapshot_uuid", sa.String(length=36), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("draft_id", sa.Integer(), sa.ForeignKey("budget_project_pricing_drafts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("draft_line_id", sa.Integer(), sa.ForeignKey("budget_project_pricing_draft_lines.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_enterprise_version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("source_enterprise_quota_item_id", sa.Integer(), sa.ForeignKey("enterprise_quota_items.id", ondelete="SET NULL"), nullable=True),
            sa.Column("section_path_json", _long_text(), nullable=False),
            sa.Column("quota_code", sa.String(length=64), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=False),
            sa.Column("work_content", _long_text(), nullable=True),
            sa.Column("specification", sa.String(length=255), nullable=True),
            sa.Column("brand", sa.String(length=255), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("labor_fee", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("main_material_fee", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("auxiliary_material_fee", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("machinery_fee", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("unit_price", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("enterprise_sync_version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("enterprise_synced_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("enterprise_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("snapshot_uuid", name="uq_budget_project_quota_snapshots_uuid"),
            sa.UniqueConstraint("draft_line_id", name="uq_budget_project_quota_snapshots_draft_line"),
        )
        for name, columns in (
            ("ix_budget_project_quota_snapshots_account_id", ["account_id"]),
            ("ix_budget_project_quota_snapshots_project_id", ["project_id"]),
            ("ix_budget_project_quota_snapshots_draft_id", ["draft_id"]),
            ("ix_budget_project_quota_snapshots_draft_line_id", ["draft_line_id"]),
            ("ix_budget_project_quota_snapshots_source_enterprise_version_id", ["source_enterprise_version_id"]),
            ("ix_budget_project_quota_snapshots_source_quota_item", ["source_enterprise_quota_item_id"]),
            ("ix_budget_project_quota_snapshots_enterprise_sync_version_id", ["enterprise_sync_version_id"]),
            ("ix_budget_project_quota_snapshots_project_updated", ["project_id", "updated_at"]),
            ("ix_budget_project_quota_snapshots_draft_order", ["draft_id", "draft_line_id"]),
        ):
            op.create_index(name, "budget_project_quota_snapshots", columns)

    if "budget_project_quota_resources" not in _tables():
        op.create_table(
            "budget_project_quota_resources",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("resource_uuid", sa.String(length=36), nullable=False),
            sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("budget_project_quota_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_enterprise_component_id", sa.Integer(), sa.ForeignKey("enterprise_quota_components.id", ondelete="SET NULL"), nullable=True),
            sa.Column("source_enterprise_resource_id", sa.Integer(), sa.ForeignKey("enterprise_cost_resources.id", ondelete="SET NULL"), nullable=True),
            sa.Column("origin", sa.String(length=32), nullable=False, server_default="enterprise_snapshot"),
            sa.Column("component_type", sa.String(length=64), nullable=True),
            sa.Column("resource_code", sa.String(length=64), nullable=True),
            sa.Column("resource_name", sa.String(length=255), nullable=False),
            sa.Column("worker_or_subtype", sa.String(length=128), nullable=True),
            sa.Column("work_content", _long_text(), nullable=True),
            sa.Column("specification", sa.String(length=255), nullable=True),
            sa.Column("brand", sa.String(length=255), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("unit_price", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("amount", sa.Numeric(20, 6), nullable=False, server_default="0"),
            sa.Column("fee_bucket", sa.String(length=32), nullable=False, server_default="auxiliary_material"),
            sa.Column("library_kind", sa.String(length=24), nullable=True),
            sa.Column("category", sa.String(length=128), nullable=True),
            sa.Column("calculation_rule", _long_text(), nullable=True),
            sa.Column("tax_rate", sa.Numeric(12, 6), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("resource_uuid", name="uq_budget_project_quota_resources_uuid"),
        )
        for name, columns in (
            ("ix_budget_project_quota_resources_snapshot_id", ["snapshot_id"]),
            ("ix_budget_project_quota_resources_source_enterprise_component_id", ["source_enterprise_component_id"]),
            ("ix_budget_project_quota_resources_source_enterprise_resource_id", ["source_enterprise_resource_id"]),
            ("ix_budget_project_quota_resources_resource_code", ["resource_code"]),
            ("ix_budget_project_quota_resources_resource_name", ["resource_name"]),
            ("ix_budget_project_quota_resources_snapshot_order", ["snapshot_id", "sort_order"]),
            ("ix_budget_project_quota_resources_snapshot_bucket", ["snapshot_id", "fee_bucket"]),
        ):
            op.create_index(name, "budget_project_quota_resources", columns)

    if "budget_project_quota_events" not in _tables():
        op.create_table(
            "budget_project_quota_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("event_uuid", sa.String(length=36), nullable=False),
            sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("budget_project_quota_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("event_type", sa.String(length=48), nullable=False),
            sa.Column("resource_uuid", sa.String(length=36), nullable=True),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("before_json", _long_text(), nullable=True),
            sa.Column("after_json", _long_text(), nullable=True),
            sa.Column("details_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("event_uuid", name="uq_budget_project_quota_events_uuid"),
        )
        for name, columns in (
            ("ix_budget_project_quota_events_snapshot_id", ["snapshot_id"]),
            ("ix_budget_project_quota_events_account_id", ["account_id"]),
            ("ix_budget_project_quota_events_project_id", ["project_id"]),
            ("ix_budget_project_quota_events_event_type", ["event_type"]),
            ("ix_budget_project_quota_events_resource_uuid", ["resource_uuid"]),
            ("ix_budget_project_quota_events_snapshot_created", ["snapshot_id", "created_at"]),
            ("ix_budget_project_quota_events_project_created", ["project_id", "created_at"]),
        ):
            op.create_index(name, "budget_project_quota_events", columns)


def downgrade() -> None:
    for table in (
        "budget_project_quota_events",
        "budget_project_quota_resources",
        "budget_project_quota_snapshots",
    ):
        if table in _tables():
            op.drop_table(table)
