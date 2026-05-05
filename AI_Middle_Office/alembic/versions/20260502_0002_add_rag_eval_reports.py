"""add rag eval reports

Revision ID: 20260502_0002
Revises: 20260428_0001
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260502_0002"
down_revision: Union[str, None] = "20260428_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "rag_eval_reports" not in _tables():
        op.create_table(
            "rag_eval_reports",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("triggered_by", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("top_k", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("case_count", sa.Integer(), nullable=True),
            sa.Column("hit_rate", sa.Float(), nullable=True),
            sa.Column("mrr", sa.Float(), nullable=True),
            sa.Column("by_level_json", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("report_path", sa.String(length=256), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("rag_eval_reports")
