"""allow pricing drafts to coexist per pricing mode

Revision ID: 20260720_0059
Revises: 20260717_0058
Create Date: 2026-07-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260720_0059"
down_revision: Union[str, None] = "20260717_0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_budget_pricing_drafts_account_project",
        "budget_project_pricing_drafts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_budget_pricing_drafts_account_project_mode",
        "budget_project_pricing_drafts",
        ["account_id", "project_id", "pricing_mode"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_budget_pricing_drafts_account_project_mode",
        "budget_project_pricing_drafts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_budget_pricing_drafts_account_project",
        "budget_project_pricing_drafts",
        ["account_id", "project_id"],
    )
