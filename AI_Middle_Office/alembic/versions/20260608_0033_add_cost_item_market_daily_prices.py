"""reserve removed cost item market daily prices revision

Revision ID: 20260608_0033
Revises: 20260608_0032
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "20260608_0033"
down_revision: Union[str, None] = "20260608_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The market price table design was replaced by live web-search snapshots.
    pass


def downgrade() -> None:
    pass
