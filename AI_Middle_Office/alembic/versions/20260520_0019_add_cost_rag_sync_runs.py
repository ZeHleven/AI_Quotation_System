"""add cost rag sync runs

Revision ID: 20260520_0019
Revises: 20260520_0018
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_0019"
down_revision: Union[str, None] = "20260520_0018"
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
    if "cost_rag_sync_runs" not in _tables():
        op.create_table(
            "cost_rag_sync_runs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("source", sa.String(length=64), server_default="cost_items.active", nullable=False),
            sa.Column("status", sa.String(length=24), server_default="running", nullable=False),
            sa.Column("requested_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("synced_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("rag_service_url", sa.String(length=255), nullable=True),
            sa.Column("http_status", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("triggered_by", sa.Integer(), nullable=True),
            sa.Column("triggered_by_username", sa.String(length=64), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], name="fk_cost_rag_sync_runs_triggered_by"),
        )

    _create_index_if_missing("ix_cost_rag_sync_runs_id", "cost_rag_sync_runs", ["id"])
    _create_index_if_missing("ix_cost_rag_sync_runs_source", "cost_rag_sync_runs", ["source"])
    _create_index_if_missing("ix_cost_rag_sync_runs_status", "cost_rag_sync_runs", ["status"])
    _create_index_if_missing("ix_cost_rag_sync_runs_triggered_by", "cost_rag_sync_runs", ["triggered_by"])
    _create_index_if_missing("ix_cost_rag_sync_runs_started_at", "cost_rag_sync_runs", ["started_at"])
    _create_index_if_missing(
        "ix_cost_rag_sync_runs_status_started_at",
        "cost_rag_sync_runs",
        ["status", "started_at"],
    )


def downgrade() -> None:
    if "cost_rag_sync_runs" in _tables():
        op.drop_table("cost_rag_sync_runs")
