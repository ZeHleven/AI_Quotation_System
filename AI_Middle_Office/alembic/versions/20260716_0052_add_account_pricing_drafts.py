"""add account tenancy and mutable dual-mode pricing drafts

Revision ID: 20260716_0052
Revises: 20260716_0051
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260716_0052"
down_revision: Union[str, None] = "20260716_0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_ACCOUNT_UUID = "00000000-0000-4000-8000-000000000001"
_DEFAULT_ACCOUNT_CODE = "internal-default"


def _longtext() -> sa.types.TypeEngine:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_uuid", sa.String(length=36), nullable=False),
        sa.Column("account_code", sa.String(length=64), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("is_internal_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_uuid", name="uq_accounts_uuid"),
        sa.UniqueConstraint("account_code", name="uq_accounts_code"),
    )
    op.create_index("ix_accounts_status", "accounts", ["status"])
    op.create_index("ix_accounts_status_created", "accounts", ["status", "created_at"])

    op.create_table(
        "account_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("member_role", sa.String(length=32), server_default="member", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "user_id", name="uq_account_memberships_account_user"),
    )
    for name, columns in (
        ("ix_account_memberships_account_id", ["account_id"]),
        ("ix_account_memberships_user_id", ["user_id"]),
        ("ix_account_memberships_status", ["status"]),
        ("ix_account_memberships_is_default", ["is_default"]),
        ("ix_account_memberships_user_status", ["user_id", "status"]),
        ("ix_account_memberships_account_status", ["account_id", "status"]),
    ):
        op.create_index(name, "account_memberships", columns)

    op.create_table(
        "account_budget_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_account_budget_projects_project"),
        sa.UniqueConstraint("account_id", "project_id", name="uq_account_budget_projects_account_project"),
    )
    op.create_index("ix_account_budget_projects_account_id", "account_budget_projects", ["account_id"])
    op.create_index("ix_account_budget_projects_project_id", "account_budget_projects", ["project_id"])
    op.create_index("ix_account_budget_projects_account_created", "account_budget_projects", ["account_id", "created_at"])

    bind.execute(
        sa.text(
            "INSERT INTO accounts "
            "(account_uuid, account_code, account_name, status, is_internal_default, created_by) "
            "VALUES (:uuid, :code, :name, 'active', 1, NULL)"
        ),
        {"uuid": _DEFAULT_ACCOUNT_UUID, "code": _DEFAULT_ACCOUNT_CODE, "name": "默认内部账号"},
    )
    default_account_id = int(
        bind.execute(
            sa.text("SELECT id FROM accounts WHERE account_code=:code"),
            {"code": _DEFAULT_ACCOUNT_CODE},
        ).scalar_one()
    )
    bind.execute(
        sa.text(
            "INSERT INTO account_memberships "
            "(account_id, user_id, member_role, status, is_default, created_by) "
            "SELECT :account_id, id, 'member', 'active', 1, NULL FROM users"
        ),
        {"account_id": default_account_id},
    )
    bind.execute(
        sa.text(
            "INSERT INTO account_budget_projects (account_id, project_id, created_by) "
            "SELECT :account_id, project_id, created_by FROM budget_project_profiles"
        ),
        {"account_id": default_account_id},
    )

    op.create_table(
        "budget_project_pricing_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_uuid", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("pricing_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source_import_batch_id", sa.Integer(), nullable=False),
        sa.Column("source_import_revision_id", sa.Integer(), nullable=False),
        sa.Column("source_import_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_rows_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_json", _longtext(), nullable=False),
        sa.Column("enterprise_quota_version_id", sa.Integer(), nullable=True),
        sa.Column("enterprise_quota_catalog_sha256", sa.String(length=64), nullable=True),
        sa.Column("matching_engine_version", sa.String(length=64), nullable=False),
        sa.Column("pricing_engine_version", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("matched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("priced_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pending_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("manual_price_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quantity_unresolved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("priced_subtotal", sa.Numeric(precision=24, scale=6), server_default="0", nullable=False),
        sa.Column("total_cost", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("completeness_status", sa.String(length=24), server_default="partial", nullable=False),
        sa.Column("summary_json", _longtext(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_import_batch_id"], ["budget_project_import_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_import_revision_id"], ["budget_project_import_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["enterprise_quota_version_id"], ["enterprise_quota_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_uuid", name="uq_budget_pricing_drafts_uuid"),
        sa.UniqueConstraint("account_id", "project_id", name="uq_budget_pricing_drafts_account_project"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_drafts_account_id", ["account_id"]),
        ("ix_budget_project_pricing_drafts_project_id", ["project_id"]),
        ("ix_budget_project_pricing_drafts_pricing_mode", ["pricing_mode"]),
        ("ix_budget_pricing_drafts_account_updated", ["account_id", "updated_at"]),
        ("ix_budget_pricing_drafts_project_mode", ["project_id", "pricing_mode"]),
    ):
        op.create_index(name, "budget_project_pricing_drafts", columns)

    op.create_table(
        "budget_project_pricing_draft_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_uuid", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
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
        sa.Column("pricing_status", sa.String(length=32), server_default="pending_match", nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("match_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("match_evidence_json", _longtext(), nullable=True),
        sa.Column("selected_enterprise_quota_item_id", sa.Integer(), nullable=True),
        sa.Column("selected_source_snapshot_json", _longtext(), nullable=True),
        sa.Column("base_unit_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("manual_unit_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("effective_unit_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("amount_included", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("price_source", sa.String(length=32), server_default="none", nullable=False),
        sa.Column("warnings_json", _longtext(), nullable=True),
        sa.Column("line_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["draft_id"], ["budget_project_pricing_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_enterprise_quota_item_id"], ["enterprise_quota_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("line_uuid", name="uq_budget_pricing_draft_lines_uuid"),
        sa.UniqueConstraint("draft_id", "source_row_key", name="uq_budget_pricing_draft_lines_source"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_draft_lines_draft_id", ["draft_id"]),
        ("ix_budget_project_pricing_draft_lines_item_name", ["item_name"]),
        ("ix_budget_pricing_draft_lines_order", ["draft_id", "source_sort_order"]),
        ("ix_budget_pricing_draft_lines_match", ["draft_id", "match_status"]),
        ("ix_budget_pricing_draft_lines_pricing", ["draft_id", "pricing_status"]),
    ):
        op.create_index(name, "budget_project_pricing_draft_lines", columns)

    op.create_table(
        "budget_project_pricing_draft_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_uuid", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("from_mode", sa.String(length=32), nullable=True),
        sa.Column("to_mode", sa.String(length=32), nullable=True),
        sa.Column("from_revision", sa.Integer(), nullable=True),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("event_json", _longtext(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["draft_id"], ["budget_project_pricing_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_uuid", name="uq_budget_pricing_draft_events_uuid"),
    )
    for name, columns in (
        ("ix_budget_project_pricing_draft_events_draft_id", ["draft_id"]),
        ("ix_budget_project_pricing_draft_events_account_id", ["account_id"]),
        ("ix_budget_project_pricing_draft_events_project_id", ["project_id"]),
        ("ix_budget_project_pricing_draft_events_event_type", ["event_type"]),
        ("ix_budget_pricing_draft_events_draft_created", ["draft_id", "created_at"]),
        ("ix_budget_pricing_draft_events_account_created", ["account_id", "created_at"]),
    ):
        op.create_index(name, "budget_project_pricing_draft_events", columns)


def downgrade() -> None:
    op.drop_table("budget_project_pricing_draft_events")
    op.drop_table("budget_project_pricing_draft_lines")
    op.drop_table("budget_project_pricing_drafts")
    op.drop_table("account_budget_projects")
    op.drop_table("account_memberships")
    op.drop_table("accounts")
