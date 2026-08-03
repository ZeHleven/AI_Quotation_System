"""add project task evidence policy fields

Revision ID: 20260601_0028
Revises: 20260601_0027
Create Date: 2026-06-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260601_0028"
down_revision: Union[str, None] = "20260601_0027"
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


def upgrade() -> None:
    if "project_tasks" not in _tables():
        return

    columns = _columns("project_tasks")
    if "evidence_requirement" not in columns:
        op.add_column("project_tasks", sa.Column("evidence_requirement", sa.Text(), nullable=True))
    if "evidence_policy" not in columns:
        op.add_column(
            "project_tasks",
            sa.Column("evidence_policy", sa.String(length=32), nullable=False, server_default="none"),
        )
    if "is_key_node" not in columns:
        op.add_column(
            "project_tasks",
            sa.Column("is_key_node", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    indexes = _indexes("project_tasks")
    if "ix_project_tasks_evidence_policy" not in indexes:
        op.create_index("ix_project_tasks_evidence_policy", "project_tasks", ["evidence_policy"])
    if "ix_project_tasks_is_key_node" not in indexes:
        op.create_index("ix_project_tasks_is_key_node", "project_tasks", ["is_key_node"])


def downgrade() -> None:
    if "project_tasks" not in _tables():
        return

    indexes = _indexes("project_tasks")
    if "ix_project_tasks_is_key_node" in indexes:
        op.drop_index("ix_project_tasks_is_key_node", table_name="project_tasks")
    if "ix_project_tasks_evidence_policy" in indexes:
        op.drop_index("ix_project_tasks_evidence_policy", table_name="project_tasks")

    columns = _columns("project_tasks")
    if "is_key_node" in columns:
        op.drop_column("project_tasks", "is_key_node")
    if "evidence_policy" in columns:
        op.drop_column("project_tasks", "evidence_policy")
    if "evidence_requirement" in columns:
        op.drop_column("project_tasks", "evidence_requirement")
