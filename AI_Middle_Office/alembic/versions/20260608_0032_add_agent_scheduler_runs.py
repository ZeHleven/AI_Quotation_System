"""add agent scheduler runs

Revision ID: 20260608_0032
Revises: 20260608_0031
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260608_0032"
down_revision: Union[str, None] = "20260608_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _long_text() -> sa.Text:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if "agent_scheduler_runs" not in _tables():
        op.create_table(
            "agent_scheduler_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("scheduler_key", sa.String(length=64), nullable=False),
            sa.Column("run_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("triggered_by", sa.String(length=64), nullable=False, server_default="system_scheduler"),
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_duplicate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_invalid_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("result_json", _long_text(), nullable=True),
            sa.Column("error_message", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scheduler_key",
                "run_date",
                "triggered_by",
                name="uq_agent_scheduler_runs_key_date_actor",
            ),
        )

    _create_index_if_missing("agent_scheduler_runs", "ix_agent_scheduler_runs_scheduler_key", ["scheduler_key"])
    _create_index_if_missing("agent_scheduler_runs", "ix_agent_scheduler_runs_run_date", ["run_date"])
    _create_index_if_missing("agent_scheduler_runs", "ix_agent_scheduler_runs_status", ["status"])
    _create_index_if_missing("agent_scheduler_runs", "ix_agent_scheduler_runs_scheduled_at", ["scheduled_at"])
    _create_index_if_missing("agent_scheduler_runs", "ix_agent_scheduler_runs_triggered_by", ["triggered_by"])


def downgrade() -> None:
    if "agent_scheduler_runs" not in _tables():
        return
    for index_name in (
        "ix_agent_scheduler_runs_triggered_by",
        "ix_agent_scheduler_runs_scheduled_at",
        "ix_agent_scheduler_runs_status",
        "ix_agent_scheduler_runs_run_date",
        "ix_agent_scheduler_runs_scheduler_key",
    ):
        if index_name in _indexes("agent_scheduler_runs"):
            op.drop_index(index_name, table_name="agent_scheduler_runs")
    op.drop_table("agent_scheduler_runs")
