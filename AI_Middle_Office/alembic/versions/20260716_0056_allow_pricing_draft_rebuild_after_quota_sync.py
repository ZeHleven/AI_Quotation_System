"""Allow mutable pricing-draft rebuilds after account quota synchronization.

Revision ID: 20260716_0056
Revises: 20260716_0055
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0056"
down_revision: Union[str, None] = "20260716_0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "account_quota_sync_lines"
_OLD_FK = "account_quota_sync_lines_ibfk_4"
_NEW_FK = "fk_account_quota_sync_lines_draft_line_set_null"


def upgrade() -> None:
    # Sync lines already retain source_line_uuid, revision and immutable
    # source_snapshot_json.  Keep that audit evidence when the mutable draft
    # line is rebuilt, and only clear the optional live navigation link.
    op.drop_constraint(_OLD_FK, _TABLE, type_="foreignkey")
    op.alter_column(
        _TABLE,
        "draft_line_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        _NEW_FK,
        _TABLE,
        "budget_project_pricing_draft_lines",
        ["draft_line_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_NEW_FK, _TABLE, type_="foreignkey")
    op.alter_column(
        _TABLE,
        "draft_line_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        _OLD_FK,
        _TABLE,
        "budget_project_pricing_draft_lines",
        ["draft_line_id"],
        ["id"],
        ondelete="RESTRICT",
    )
