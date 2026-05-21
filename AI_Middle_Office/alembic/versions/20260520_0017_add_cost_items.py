"""add biz2a cost items

Revision ID: 20260520_0017
Revises: 20260520_0016
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0017"
down_revision: Union[str, None] = "20260520_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    if "cost_items" not in _tables():
        op.create_table(
            "cost_items",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("category", sa.String(length=128), nullable=False),
            sa.Column("subcategory", sa.String(length=128), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=False),
            sa.Column("spec", sa.Text(), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=False),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("client_tax_excluded_price", sa.Float(), nullable=True),
            sa.Column("subcontract_composite_price", sa.Float(), nullable=True),
            sa.Column("crew_benchmark_price", sa.Float(), nullable=True),
            sa.Column("price_type", sa.String(length=24), server_default="combined", nullable=False),
            sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
            sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_cost_items_created_by"),
        )

    _create_index_if_missing("ix_cost_items_id", "cost_items", ["id"])
    _create_index_if_missing("ix_cost_items_category", "cost_items", ["category"])
    _create_index_if_missing("ix_cost_items_subcategory", "cost_items", ["subcategory"])
    _create_index_if_missing("ix_cost_items_item_name", "cost_items", ["item_name"])
    _create_index_if_missing("ix_cost_items_price_type", "cost_items", ["price_type"])
    _create_index_if_missing("ix_cost_items_status", "cost_items", ["status"])
    _create_index_if_missing("ix_cost_items_source", "cost_items", ["source"])
    _create_index_if_missing("ix_cost_items_created_by", "cost_items", ["created_by"])
    _create_index_if_missing("ix_cost_items_category_subcategory", "cost_items", ["category", "subcategory"])
    _create_index_if_missing("ix_cost_items_status_price_type", "cost_items", ["status", "price_type"])
    _create_index_if_missing(
        "ix_cost_items_duplicate_key",
        "cost_items",
        ["category", "subcategory", "item_name", "unit"],
    )

    if "cost_item_history" not in _tables():
        op.create_table(
            "cost_item_history",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("cost_item_id", sa.Integer(), nullable=False),
            sa.Column("old_price", sa.Float(), nullable=True),
            sa.Column("new_price", sa.Float(), nullable=True),
            sa.Column("old_client_tax_excluded_price", sa.Float(), nullable=True),
            sa.Column("new_client_tax_excluded_price", sa.Float(), nullable=True),
            sa.Column("old_subcontract_composite_price", sa.Float(), nullable=True),
            sa.Column("new_subcontract_composite_price", sa.Float(), nullable=True),
            sa.Column("old_crew_benchmark_price", sa.Float(), nullable=True),
            sa.Column("new_crew_benchmark_price", sa.Float(), nullable=True),
            sa.Column("old_status", sa.String(length=24), nullable=True),
            sa.Column("new_status", sa.String(length=24), nullable=True),
            sa.Column("change_type", sa.String(length=32), nullable=False),
            sa.Column("changed_by", sa.Integer(), nullable=True),
            sa.Column("change_reason", sa.Text(), nullable=True),
            sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["cost_item_id"], ["cost_items.id"], name="fk_cost_item_history_cost_item_id"),
            sa.ForeignKeyConstraint(["changed_by"], ["users.id"], name="fk_cost_item_history_changed_by"),
        )

    _create_index_if_missing("ix_cost_item_history_id", "cost_item_history", ["id"])
    _create_index_if_missing("ix_cost_item_history_cost_item_id", "cost_item_history", ["cost_item_id"])
    _create_index_if_missing("ix_cost_item_history_change_type", "cost_item_history", ["change_type"])
    _create_index_if_missing("ix_cost_item_history_changed_by", "cost_item_history", ["changed_by"])


def downgrade() -> None:
    if "cost_item_history" in _tables():
        op.drop_table("cost_item_history")
    if "cost_items" in _tables():
        op.drop_table("cost_items")
