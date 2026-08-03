"""add quote requirement rows

Revision ID: 20260526_0022
Revises: 20260520_0021
Create Date: 2026-05-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260526_0022"
down_revision: Union[str, None] = "20260520_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _index_names(table_name):
        op.create_index(name, table_name, columns)


def upgrade() -> None:
    if "quote_job_requirement_rows" not in _tables():
        op.create_table(
            "quote_job_requirement_rows",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("quote_job_id", sa.String(length=36), nullable=False),
            sa.Column("requirement_row_key", sa.String(length=128), nullable=True),
            sa.Column("source_sheet", sa.String(length=255), nullable=True),
            sa.Column("raw_row_index", sa.Integer(), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=True),
            sa.Column("spec", sa.Text(), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("raw_text", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True),
            sa.Column("raw_cells_json", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True),
            sa.Column("row_json", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["quote_job_id"], ["quote_jobs.job_id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_quote_job_requirement_rows_id", "quote_job_requirement_rows", ["id"])
    _create_index_if_missing("ix_quote_job_requirement_rows_quote_job_id", "quote_job_requirement_rows", ["quote_job_id"])
    _create_index_if_missing(
        "ix_quote_job_requirement_rows_requirement_row_key",
        "quote_job_requirement_rows",
        ["requirement_row_key"],
    )


def downgrade() -> None:
    if "quote_job_requirement_rows" in _tables():
        op.drop_table("quote_job_requirement_rows")
