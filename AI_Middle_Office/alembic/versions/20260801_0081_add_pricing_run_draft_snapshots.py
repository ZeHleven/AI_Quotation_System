"""add immutable full-draft snapshots for pricing versions

Revision ID: 20260801_0081
Revises: 20260731_0080
Create Date: 2026-08-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260801_0081"
down_revision: Union[str, None] = "20260731_0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _large_text():
    return mysql.LONGTEXT() if op.get_bind().dialect.name == "mysql" else sa.Text()


def upgrade() -> None:
    op.create_table(
        "budget_project_pricing_run_draft_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("snapshot_uuid", sa.String(length=36), nullable=False),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_draft_id",
            sa.Integer(),
            sa.ForeignKey("budget_project_pricing_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_draft_uuid", sa.String(length=36), nullable=False),
        sa.Column("source_draft_revision", sa.Integer(), nullable=False),
        sa.Column("pricing_mode", sa.String(length=32), nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", _large_text(), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "snapshot_uuid",
            name="uq_budget_pricing_run_draft_snapshots_uuid",
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_budget_pricing_run_draft_snapshots_run",
        ),
    )
    op.create_index(
        "ix_budget_pricing_run_draft_snapshots_run_id",
        "budget_project_pricing_run_draft_snapshots",
        ["run_id"],
    )
    op.create_index(
        "ix_budget_pricing_run_draft_snapshots_account_id",
        "budget_project_pricing_run_draft_snapshots",
        ["account_id"],
    )
    op.create_index(
        "ix_budget_pricing_run_draft_snapshots_project_id",
        "budget_project_pricing_run_draft_snapshots",
        ["project_id"],
    )
    op.create_index(
        "ix_budget_pricing_run_draft_snapshots_snapshot_sha256",
        "budget_project_pricing_run_draft_snapshots",
        ["snapshot_sha256"],
    )
    op.create_index(
        "ix_budget_pricing_run_draft_snapshots_project_created",
        "budget_project_pricing_run_draft_snapshots",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_budget_pricing_run_draft_snapshots_account_created",
        "budget_project_pricing_run_draft_snapshots",
        ["account_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("budget_project_pricing_run_draft_snapshots")
