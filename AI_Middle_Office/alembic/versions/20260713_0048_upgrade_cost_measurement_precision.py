"""upgrade cost measurement numeric precision

Revision ID: 20260713_0048
Revises: 20260713_0047
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_0048"
down_revision: Union[str, None] = "20260713_0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MEASUREMENT_COLUMNS = (
    "management_rate",
    "profit_rate",
    "tax_rate",
    "direct_cost",
    "management_fee",
    "profit_fee",
    "pretax_total",
    "tax_total",
    "grand_total",
)

LINE_COLUMNS = (
    "quantity",
    "source_unit_price",
    "source_total_price",
    "labor_unit_price",
    "main_material_unit_price",
    "material_loss_rate",
    "auxiliary_machinery_unit_price",
    "subcontract_unit_price",
    "direct_unit_price",
    "management_unit_price",
    "profit_unit_price",
    "calculated_unit_price",
    "calculated_total_price",
    "source_variance",
)

NULLABLE_LINE_COLUMNS = {"source_unit_price", "source_total_price"}


def _column_types(table_name: str) -> dict[str, sa.types.TypeEngine]:
    return {
        column["name"]: column["type"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _upgrade_table(table_name: str, columns: tuple[str, ...], nullable_columns: set[str]) -> None:
    current_types = _column_types(table_name)
    for column_name in columns:
        current_type = current_types.get(column_name)
        if current_type is None or isinstance(current_type, sa.Double):
            continue
        op.alter_column(
            table_name,
            column_name,
            existing_type=current_type,
            type_=sa.Double(),
            existing_nullable=column_name in nullable_columns,
        )


def _downgrade_table(table_name: str, columns: tuple[str, ...], nullable_columns: set[str]) -> None:
    current_types = _column_types(table_name)
    for column_name in columns:
        current_type = current_types.get(column_name)
        if current_type is None or isinstance(current_type, sa.Float) and not isinstance(current_type, sa.Double):
            continue
        op.alter_column(
            table_name,
            column_name,
            existing_type=current_type,
            type_=sa.Float(),
            existing_nullable=column_name in nullable_columns,
        )


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cost_measurements" in tables:
        _upgrade_table("cost_measurements", MEASUREMENT_COLUMNS, set())
    if "cost_measurement_lines" in tables:
        _upgrade_table("cost_measurement_lines", LINE_COLUMNS, NULLABLE_LINE_COLUMNS)


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "cost_measurement_lines" in tables:
        _downgrade_table("cost_measurement_lines", LINE_COLUMNS, NULLABLE_LINE_COLUMNS)
    if "cost_measurements" in tables:
        _downgrade_table("cost_measurements", MEASUREMENT_COLUMNS, set())
