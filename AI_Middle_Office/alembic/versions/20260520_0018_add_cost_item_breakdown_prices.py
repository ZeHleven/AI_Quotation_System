"""add cost item breakdown prices

Revision ID: 20260520_0018
Revises: 20260520_0017
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0018"
down_revision: Union[str, None] = "20260520_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COST_ITEM_COLUMNS = [
    "client_labor_price",
    "client_main_material_price",
    "client_auxiliary_material_price",
    "client_direct_fee",
    "client_management_profit",
    "subcontract_labor_price",
    "subcontract_main_material_price",
    "subcontract_auxiliary_material_price",
]

HISTORY_BREAKDOWN_COLUMNS = [
    f"{prefix}_{name}"
    for name in COST_ITEM_COLUMNS
    for prefix in ("old", "new")
]


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_float_column_if_missing(table_name: str, column_name: str) -> None:
    if column_name not in _columns(table_name):
        op.add_column(table_name, sa.Column(column_name, sa.Float(), nullable=True))


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    for column_name in COST_ITEM_COLUMNS:
        _add_float_column_if_missing("cost_items", column_name)
    for column_name in HISTORY_BREAKDOWN_COLUMNS:
        _add_float_column_if_missing("cost_item_history", column_name)


def downgrade() -> None:
    for column_name in reversed(HISTORY_BREAKDOWN_COLUMNS):
        _drop_column_if_exists("cost_item_history", column_name)
    for column_name in reversed(COST_ITEM_COLUMNS):
        _drop_column_if_exists("cost_items", column_name)
