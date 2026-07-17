"""Add AI estimate fields to mutable budget pricing draft lines.

Revision ID: 20260716_0057
Revises: 20260716_0056
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260716_0057"
down_revision: Union[str, None] = "20260716_0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext_type():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    op.add_column(
        "budget_project_pricing_draft_lines",
        sa.Column("ai_estimated_unit_price", sa.Numeric(20, 6), nullable=True),
    )
    op.add_column(
        "budget_project_pricing_draft_lines",
        sa.Column("ai_estimate_snapshot_json", _longtext_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budget_project_pricing_draft_lines", "ai_estimate_snapshot_json")
    op.drop_column("budget_project_pricing_draft_lines", "ai_estimated_unit_price")
