"""Add account-quota match evidence to mutable pricing drafts.

Revision ID: 20260716_0055
Revises: 20260716_0054
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0055"
down_revision: Union[str, None] = "20260716_0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budget_project_pricing_drafts",
        sa.Column("account_quota_catalog_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "budget_project_pricing_draft_lines",
        sa.Column("selected_account_quota_item_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_budget_pricing_draft_lines_selected_account_quota",
        "budget_project_pricing_draft_lines",
        "account_quota_items",
        ["selected_account_quota_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_budget_pricing_draft_lines_selected_account_quota",
        "budget_project_pricing_draft_lines",
        ["selected_account_quota_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_budget_pricing_draft_lines_selected_account_quota", table_name="budget_project_pricing_draft_lines")
    op.drop_constraint("fk_budget_pricing_draft_lines_selected_account_quota", "budget_project_pricing_draft_lines", type_="foreignkey")
    op.drop_column("budget_project_pricing_draft_lines", "selected_account_quota_item_id")
    op.drop_column("budget_project_pricing_drafts", "account_quota_catalog_sha256")
