"""drop cost item market daily prices

Revision ID: 20260609_0034
Revises: 20260608_0033
Create Date: 2026-06-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260609_0034"
down_revision: Union[str, None] = "20260608_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _foreign_keys(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
        if foreign_key.get("name")
    }


def upgrade() -> None:
    table_name = "cost_item_market_daily_prices"
    if table_name not in _tables():
        return
    for foreign_key_name in _foreign_keys(table_name):
        op.drop_constraint(foreign_key_name, table_name, type_="foreignkey")
    for index_name in (
        "ix_cost_item_market_prices_item_name",
        "ix_cost_item_market_prices_city_date",
        "ix_cost_item_market_prices_item_city_date",
        "ix_cost_item_market_daily_prices_created_by",
        "ix_cost_item_market_daily_prices_price_date",
        "ix_cost_item_market_daily_prices_city",
        "ix_cost_item_market_daily_prices_cost_item_id",
        "ix_cost_item_market_daily_prices_id",
    ):
        if index_name in _indexes(table_name):
            op.drop_index(index_name, table_name=table_name)
    op.drop_table(table_name)


def downgrade() -> None:
    # The market price table was removed before becoming a supported product surface.
    pass
