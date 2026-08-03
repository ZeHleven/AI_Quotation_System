"""add pricing draft to account quota synchronization audit

Revision ID: 20260716_0054
Revises: 20260716_0053
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260716_0054"
down_revision: Union[str, None] = "20260716_0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext() -> sa.types.TypeEngine:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    op.create_table(
        "account_quota_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_uuid", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="completed", nullable=False),
        sa.Column("requested_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reason", _longtext(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["budget_project_pricing_drafts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_uuid", name="uq_account_quota_sync_runs_uuid"),
    )
    for name, columns in (
        ("ix_account_quota_sync_runs_account_id", ["account_id"]),
        ("ix_account_quota_sync_runs_project_id", ["project_id"]),
        ("ix_account_quota_sync_runs_draft_id", ["draft_id"]),
        ("ix_account_quota_sync_runs_actor_id", ["actor_id"]),
        ("ix_account_quota_sync_runs_account_created", ["account_id", "created_at"]),
        ("ix_account_quota_sync_runs_draft_created", ["draft_id", "created_at"]),
    ):
        op.create_index(name, "account_quota_sync_runs", columns)

    op.create_table(
        "account_quota_sync_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_run_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("draft_line_id", sa.Integer(), nullable=False),
        sa.Column("source_line_uuid", sa.String(length=36), nullable=False),
        sa.Column("source_line_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("account_quota_item_id", sa.Integer(), nullable=True),
        sa.Column("target_item_revision", sa.Integer(), nullable=True),
        sa.Column("source_snapshot_json", _longtext(), nullable=False),
        sa.Column("target_before_snapshot_json", _longtext(), nullable=True),
        sa.Column("target_after_snapshot_json", _longtext(), nullable=True),
        sa.Column("result_json", _longtext(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["sync_run_id"], ["account_quota_sync_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["budget_project_pricing_drafts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_line_id"], ["budget_project_pricing_draft_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_quota_item_id"], ["account_quota_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_run_id", "draft_line_id", name="uq_account_quota_sync_lines_run_draft_line"),
    )
    for name, columns in (
        ("ix_account_quota_sync_lines_sync_run_id", ["sync_run_id"]),
        ("ix_account_quota_sync_lines_account_id", ["account_id"]),
        ("ix_account_quota_sync_lines_draft_id", ["draft_id"]),
        ("ix_account_quota_sync_lines_draft_line_id", ["draft_line_id"]),
        ("ix_account_quota_sync_lines_fingerprint", ["fingerprint"]),
        ("ix_account_quota_sync_lines_account_created", ["account_id", "created_at"]),
        ("ix_account_quota_sync_lines_target", ["account_quota_item_id"]),
    ):
        op.create_index(name, "account_quota_sync_lines", columns)


def downgrade() -> None:
    op.drop_table("account_quota_sync_lines")
    op.drop_table("account_quota_sync_runs")
