"""add pricing draft breakdown json

Revision ID: 20260720_0060
Revises: 20260720_0059
Create Date: 2026-07-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260720_0060"
down_revision: Union[str, None] = "20260720_0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext_type():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    op.add_column(
        "budget_project_pricing_draft_lines",
        sa.Column("pricing_breakdown_json", _longtext_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budget_project_pricing_draft_lines", "pricing_breakdown_json")
