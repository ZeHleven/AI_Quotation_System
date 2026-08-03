"""add quote preview drafts

Revision ID: 20260527_0024
Revises: 20260526_0023
Create Date: 2026-05-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260527_0024"
down_revision: Union[str, None] = "20260526_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "quote_preview_drafts" in _tables():
        return
    op.create_table(
        "quote_preview_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("quote_job_id", sa.String(length=36), sa.ForeignKey("quote_jobs.job_id"), nullable=False),
        sa.Column("quote_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="editing"),
        sa.Column("draft_json", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priced_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unpriced_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_quote_preview_drafts_id", "quote_preview_drafts", ["id"])
    op.create_index("ix_quote_preview_drafts_quote_job_id", "quote_preview_drafts", ["quote_job_id"], unique=True)
    op.create_index("ix_quote_preview_drafts_trace_id", "quote_preview_drafts", ["trace_id"])
    op.create_index("ix_quote_preview_drafts_user_id", "quote_preview_drafts", ["user_id"])
    op.create_index("ix_quote_preview_drafts_username", "quote_preview_drafts", ["username"])
    op.create_index("ix_quote_preview_drafts_status", "quote_preview_drafts", ["status"])
    op.create_index("ix_quote_preview_drafts_status_updated", "quote_preview_drafts", ["status", "updated_at"])
    op.create_index("ix_quote_preview_drafts_username_status", "quote_preview_drafts", ["username", "status"])


def downgrade() -> None:
    if "quote_preview_drafts" not in _tables():
        return
    op.drop_index("ix_quote_preview_drafts_username_status", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_status_updated", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_status", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_username", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_user_id", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_trace_id", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_quote_job_id", table_name="quote_preview_drafts")
    op.drop_index("ix_quote_preview_drafts_id", table_name="quote_preview_drafts")
    op.drop_table("quote_preview_drafts")
