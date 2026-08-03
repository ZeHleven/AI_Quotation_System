"""add project task evidences

Revision ID: 20260601_0027
Revises: 20260601_0026
Create Date: 2026-06-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260601_0027"
down_revision: Union[str, None] = "20260601_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "project_task_evidences" in _tables():
        return
    op.create_table(
        "project_task_evidences",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("project_stages.id"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("project_tasks.id"), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_object_id", sa.String(length=36), sa.ForeignKey("file_objects.file_id"), nullable=True),
        sa.Column("external_url", sa.String(length=1024), nullable=True),
        sa.Column("external_provider", sa.String(length=64), nullable=True),
        sa.Column("requirement_snapshot", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remove_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_project_task_evidences_id", "project_task_evidences", ["id"])
    op.create_index("ix_project_task_evidences_project_id", "project_task_evidences", ["project_id"])
    op.create_index("ix_project_task_evidences_stage_id", "project_task_evidences", ["stage_id"])
    op.create_index("ix_project_task_evidences_task_id", "project_task_evidences", ["task_id"])
    op.create_index("ix_project_task_evidences_evidence_type", "project_task_evidences", ["evidence_type"])
    op.create_index("ix_project_task_evidences_file_object_id", "project_task_evidences", ["file_object_id"])
    op.create_index("ix_project_task_evidences_status", "project_task_evidences", ["status"])
    op.create_index("ix_project_task_evidences_created_by", "project_task_evidences", ["created_by"])
    op.create_index("ix_project_task_evidences_removed_by", "project_task_evidences", ["removed_by"])
    op.create_index(
        "ix_project_task_evidences_task_status_created",
        "project_task_evidences",
        ["task_id", "status", "created_at"],
    )
    op.create_index(
        "ix_project_task_evidences_project_status_created",
        "project_task_evidences",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_project_task_evidences_stage_status",
        "project_task_evidences",
        ["stage_id", "status"],
    )


def downgrade() -> None:
    if "project_task_evidences" not in _tables():
        return
    op.drop_index("ix_project_task_evidences_stage_status", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_project_status_created", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_task_status_created", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_removed_by", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_created_by", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_status", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_file_object_id", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_evidence_type", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_task_id", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_stage_id", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_project_id", table_name="project_task_evidences")
    op.drop_index("ix_project_task_evidences_id", table_name="project_task_evidences")
    op.drop_table("project_task_evidences")
