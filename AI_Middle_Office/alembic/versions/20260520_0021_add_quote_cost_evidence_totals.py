"""add quote cost evidence totals

Revision ID: 20260520_0021
Revises: 20260520_0020
Create Date: 2026-05-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0021"
down_revision: Union[str, None] = "20260520_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("quote_cost_evidence", sa.Column("line_total_price", sa.Float(), nullable=True))
    _add_column_if_missing("quote_cost_evidence", sa.Column("line_total_source", sa.String(length=32), nullable=True))
    _add_column_if_missing("quote_cost_evidence", sa.Column("quote_total_price", sa.Float(), nullable=True))
    _add_column_if_missing("quote_cost_evidence", sa.Column("quote_total_source", sa.String(length=32), nullable=True))
    _add_column_if_missing("quote_cost_evidence", sa.Column("quote_reference_total_price", sa.Float(), nullable=True))


def downgrade() -> None:
    existing = _columns("quote_cost_evidence")
    for column_name in (
        "quote_reference_total_price",
        "quote_total_source",
        "quote_total_price",
        "line_total_source",
        "line_total_price",
    ):
        if column_name in existing:
            op.drop_column("quote_cost_evidence", column_name)
