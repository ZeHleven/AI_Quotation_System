"""add agent run trigger fields

Revision ID: 20260608_0031
Revises: 20260608_0030
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260608_0031"
down_revision: Union[str, None] = "20260608_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if "agent_runs" not in _tables():
        return

    _add_column_if_missing(
        "agent_runs",
        sa.Column("trigger_source", sa.String(length=32), nullable=False, server_default="manual"),
    )
    _add_column_if_missing(
        "agent_runs",
        sa.Column("trigger_ref_type", sa.String(length=64), nullable=True),
    )
    _add_column_if_missing(
        "agent_runs",
        sa.Column("trigger_ref_id", sa.String(length=128), nullable=True),
    )

    _create_index_if_missing("agent_runs", "ix_agent_runs_trigger_source", ["trigger_source"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_trigger_ref_type", ["trigger_ref_type"])
    _create_index_if_missing("agent_runs", "ix_agent_runs_trigger_ref_id", ["trigger_ref_id"])


def downgrade() -> None:
    if "agent_runs" not in _tables():
        return

    for index_name in (
        "ix_agent_runs_trigger_ref_id",
        "ix_agent_runs_trigger_ref_type",
        "ix_agent_runs_trigger_source",
    ):
        if index_name in _indexes("agent_runs"):
            op.drop_index(index_name, table_name="agent_runs")

    columns = _columns("agent_runs")
    for column_name in ("trigger_ref_id", "trigger_ref_type", "trigger_source"):
        if column_name in columns:
            op.drop_column("agent_runs", column_name)
