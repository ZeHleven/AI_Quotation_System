"""Add background quote jobs for enterprise-ai pricing drafts.

Revision ID: 20260717_0058
Revises: 20260716_0057
Create Date: 2026-07-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260717_0058"
down_revision: Union[str, None] = "20260716_0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext_type():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    op.create_table(
        "budget_project_pricing_draft_quote_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_uuid", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("requested_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("progress_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_message", sa.String(length=512), nullable=True),
        sa.Column("total_line_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enterprise_priced_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ai_total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ai_completed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ai_failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_import_batch_id", sa.Integer(), nullable=False),
        sa.Column("source_import_revision_id", sa.Integer(), nullable=False),
        sa.Column("enterprise_quota_version_id", sa.Integer(), nullable=True),
        sa.Column("request_json", _longtext_type(), nullable=False),
        sa.Column("result_json", _longtext_type(), nullable=True),
        sa.Column("error_json", _longtext_type(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["budget_project_pricing_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_import_batch_id"], ["budget_project_import_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_import_revision_id"], ["budget_project_import_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["enterprise_quota_version_id"], ["enterprise_quota_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_uuid", name="uq_budget_pricing_draft_quote_jobs_uuid"),
    )
    op.create_index(
        "ix_budget_pricing_draft_quote_jobs_project_created",
        "budget_project_pricing_draft_quote_jobs",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_budget_pricing_draft_quote_jobs_draft_status",
        "budget_project_pricing_draft_quote_jobs",
        ["draft_id", "status"],
    )
    op.create_index(
        "ix_budget_pricing_draft_quote_jobs_account_created",
        "budget_project_pricing_draft_quote_jobs",
        ["account_id", "created_at"],
    )

    op.create_table(
        "budget_project_pricing_draft_quote_job_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("draft_line_id", sa.Integer(), nullable=False),
        sa.Column("line_uuid", sa.String(length=36), nullable=False),
        sa.Column("source_row_key", sa.String(length=255), nullable=False),
        sa.Column("source_sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="ai_pending", nullable=False),
        sa.Column("source", sa.String(length=32), server_default="ai_estimate", nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("result_json", _longtext_type(), nullable=True),
        sa.Column("error_json", _longtext_type(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["budget_project_pricing_draft_quote_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_line_id"], ["budget_project_pricing_draft_lines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "draft_line_id", name="uq_budget_pricing_draft_quote_job_lines_line"),
    )
    op.create_index(
        "ix_budget_pricing_draft_quote_job_lines_job_status",
        "budget_project_pricing_draft_quote_job_lines",
        ["job_id", "status"],
    )
    op.create_index(
        "ix_budget_pricing_draft_quote_job_lines_draft_line",
        "budget_project_pricing_draft_quote_job_lines",
        ["draft_line_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_budget_pricing_draft_quote_job_lines_draft_line", table_name="budget_project_pricing_draft_quote_job_lines")
    op.drop_index("ix_budget_pricing_draft_quote_job_lines_job_status", table_name="budget_project_pricing_draft_quote_job_lines")
    op.drop_table("budget_project_pricing_draft_quote_job_lines")
    op.drop_index("ix_budget_pricing_draft_quote_jobs_account_created", table_name="budget_project_pricing_draft_quote_jobs")
    op.drop_index("ix_budget_pricing_draft_quote_jobs_draft_status", table_name="budget_project_pricing_draft_quote_jobs")
    op.drop_index("ix_budget_pricing_draft_quote_jobs_project_created", table_name="budget_project_pricing_draft_quote_jobs")
    op.drop_table("budget_project_pricing_draft_quote_jobs")
