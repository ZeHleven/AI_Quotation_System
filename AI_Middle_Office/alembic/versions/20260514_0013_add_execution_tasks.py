"""add phase3 execution tasks

Revision ID: 20260514_0013
Revises: 20260514_0012
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0013"
down_revision: Union[str, None] = "20260514_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _unique_constraints(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    existing = _indexes(table_name) | _unique_constraints(table_name)
    if name not in existing:
        op.create_index(name, table_name, columns, unique=unique)


def _drop_index_if_exists(name: str, table_name: str) -> None:
    existing = _indexes(table_name) | _unique_constraints(table_name)
    if name in existing:
        op.drop_index(name, table_name=table_name)


def upgrade() -> None:
    if "execution_tasks" not in _tables():
        op.create_table(
            "execution_tasks",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("source", sa.String(length=24), nullable=False, server_default="manual"),
            sa.Column("source_ref_id", sa.String(length=64), nullable=True),
            sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("notes", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_execution_tasks_id", "execution_tasks", ["id"])
    _create_index_if_missing("ix_execution_tasks_source", "execution_tasks", ["source"])
    _create_index_if_missing("ix_execution_tasks_source_ref_id", "execution_tasks", ["source_ref_id"])
    _create_index_if_missing("ix_execution_tasks_assignee_id", "execution_tasks", ["assignee_id"])
    _create_index_if_missing("ix_execution_tasks_due_at", "execution_tasks", ["due_at"])
    _create_index_if_missing("ix_execution_tasks_completed_at", "execution_tasks", ["completed_at"])
    _create_index_if_missing("ix_execution_tasks_status", "execution_tasks", ["status"])

    if "execution_task_events" not in _tables():
        op.create_table(
            "execution_task_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("execution_task_id", sa.Integer(), sa.ForeignKey("execution_tasks.id"), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("from_status", sa.String(length=24), nullable=True),
            sa.Column("to_status", sa.String(length=24), nullable=True),
            sa.Column("operator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
        )
    _create_index_if_missing("ix_execution_task_events_id", "execution_task_events", ["id"])
    _create_index_if_missing("ix_execution_task_events_execution_task_id", "execution_task_events", ["execution_task_id"])
    _create_index_if_missing("ix_execution_task_events_event_type", "execution_task_events", ["event_type"])
    _create_index_if_missing("ix_execution_task_events_operator_id", "execution_task_events", ["operator_id"])
    _create_index_if_missing("ix_execution_task_events_trace_id", "execution_task_events", ["trace_id"])


def downgrade() -> None:
    if "execution_task_events" in _tables():
        op.drop_table("execution_task_events")
    if "execution_tasks" in _tables():
        op.drop_table("execution_tasks")
