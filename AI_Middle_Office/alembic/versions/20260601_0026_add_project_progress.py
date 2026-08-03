"""add project progress tables

Revision ID: 20260601_0026
Revises: 20260528_0025
Create Date: 2026-06-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260601_0026"
down_revision: Union[str, None] = "20260528_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "projects" not in tables:
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("project_code", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("client_name", sa.String(length=128), nullable=True),
            sa.Column("address", sa.String(length=255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="planning"),
            sa.Column("risk_level", sa.String(length=24), nullable=False, server_default="normal"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("project_manager_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("owner_department", sa.String(length=128), nullable=True),
            sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("planned_finish_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_finish_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_code", name="uq_projects_project_code"),
        )
        op.create_index("ix_projects_id", "projects", ["id"])
        op.create_index("ix_projects_project_code", "projects", ["project_code"], unique=True)
        op.create_index("ix_projects_name", "projects", ["name"])
        op.create_index("ix_projects_client_name", "projects", ["client_name"])
        op.create_index("ix_projects_status", "projects", ["status"])
        op.create_index("ix_projects_risk_level", "projects", ["risk_level"])
        op.create_index("ix_projects_project_manager_id", "projects", ["project_manager_id"])
        op.create_index("ix_projects_owner_department", "projects", ["owner_department"])
        op.create_index("ix_projects_planned_start_at", "projects", ["planned_start_at"])
        op.create_index("ix_projects_planned_finish_at", "projects", ["planned_finish_at"])
        op.create_index("ix_projects_created_by", "projects", ["created_by"])
        op.create_index("ix_projects_status_risk", "projects", ["status", "risk_level"])
        op.create_index("ix_projects_manager_status", "projects", ["project_manager_id", "status"])

    tables = _tables()
    if "project_stages" not in tables:
        op.create_table(
            "project_stages",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("stage_key", sa.String(length=64), nullable=False),
            sa.Column("stage_name", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("weight_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="todo"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("owner_role", sa.String(length=64), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("planned_finish_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_finish_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "stage_key", name="uq_project_stages_project_stage_key"),
        )
        op.create_index("ix_project_stages_id", "project_stages", ["id"])
        op.create_index("ix_project_stages_project_id", "project_stages", ["project_id"])
        op.create_index("ix_project_stages_stage_key", "project_stages", ["stage_key"])
        op.create_index("ix_project_stages_status", "project_stages", ["status"])
        op.create_index("ix_project_stages_owner_user_id", "project_stages", ["owner_user_id"])
        op.create_index("ix_project_stages_project_order", "project_stages", ["project_id", "sort_order"])

    tables = _tables()
    if "project_tasks" not in tables:
        op.create_table(
            "project_tasks",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("stage_id", sa.Integer(), sa.ForeignKey("project_stages.id"), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("owner_role", sa.String(length=64), nullable=True),
            sa.Column("collaborators_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="todo"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("priority", sa.String(length=24), nullable=False, server_default="normal"),
            sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("blocked_reason", sa.Text(), nullable=True),
            sa.Column("next_action", sa.Text(), nullable=True),
            sa.Column("last_status_before_blocked", sa.String(length=24), nullable=True),
            sa.Column("source_type", sa.String(length=32), nullable=False, server_default="manual"),
            sa.Column("source_id", sa.String(length=64), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_project_tasks_id", "project_tasks", ["id"])
        op.create_index("ix_project_tasks_project_id", "project_tasks", ["project_id"])
        op.create_index("ix_project_tasks_stage_id", "project_tasks", ["stage_id"])
        op.create_index("ix_project_tasks_title", "project_tasks", ["title"])
        op.create_index("ix_project_tasks_owner_user_id", "project_tasks", ["owner_user_id"])
        op.create_index("ix_project_tasks_status", "project_tasks", ["status"])
        op.create_index("ix_project_tasks_priority", "project_tasks", ["priority"])
        op.create_index("ix_project_tasks_due_at", "project_tasks", ["due_at"])
        op.create_index("ix_project_tasks_completed_at", "project_tasks", ["completed_at"])
        op.create_index("ix_project_tasks_source_type", "project_tasks", ["source_type"])
        op.create_index("ix_project_tasks_source_id", "project_tasks", ["source_id"])
        op.create_index("ix_project_tasks_created_by", "project_tasks", ["created_by"])
        op.create_index("ix_project_tasks_project_status", "project_tasks", ["project_id", "status"])
        op.create_index("ix_project_tasks_owner_status", "project_tasks", ["owner_user_id", "status"])
        op.create_index("ix_project_tasks_due_status", "project_tasks", ["due_at", "status"])

    tables = _tables()
    if "project_task_events" not in tables:
        op.create_table(
            "project_task_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("stage_id", sa.Integer(), sa.ForeignKey("project_stages.id"), nullable=True),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("project_tasks.id"), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("from_status", sa.String(length=24), nullable=True),
            sa.Column("to_status", sa.String(length=24), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_project_task_events_id", "project_task_events", ["id"])
        op.create_index("ix_project_task_events_project_id", "project_task_events", ["project_id"])
        op.create_index("ix_project_task_events_stage_id", "project_task_events", ["stage_id"])
        op.create_index("ix_project_task_events_task_id", "project_task_events", ["task_id"])
        op.create_index("ix_project_task_events_event_type", "project_task_events", ["event_type"])
        op.create_index("ix_project_task_events_actor_user_id", "project_task_events", ["actor_user_id"])
        op.create_index("ix_project_task_events_project_created", "project_task_events", ["project_id", "created_at"])
        op.create_index("ix_project_task_events_task_created", "project_task_events", ["task_id", "created_at"])


def downgrade() -> None:
    tables = _tables()
    if "project_task_events" in tables:
        op.drop_index("ix_project_task_events_task_created", table_name="project_task_events")
        op.drop_index("ix_project_task_events_project_created", table_name="project_task_events")
        op.drop_index("ix_project_task_events_actor_user_id", table_name="project_task_events")
        op.drop_index("ix_project_task_events_event_type", table_name="project_task_events")
        op.drop_index("ix_project_task_events_task_id", table_name="project_task_events")
        op.drop_index("ix_project_task_events_stage_id", table_name="project_task_events")
        op.drop_index("ix_project_task_events_project_id", table_name="project_task_events")
        op.drop_index("ix_project_task_events_id", table_name="project_task_events")
        op.drop_table("project_task_events")
    tables = _tables()
    if "project_tasks" in tables:
        op.drop_index("ix_project_tasks_due_status", table_name="project_tasks")
        op.drop_index("ix_project_tasks_owner_status", table_name="project_tasks")
        op.drop_index("ix_project_tasks_project_status", table_name="project_tasks")
        op.drop_index("ix_project_tasks_created_by", table_name="project_tasks")
        op.drop_index("ix_project_tasks_source_id", table_name="project_tasks")
        op.drop_index("ix_project_tasks_source_type", table_name="project_tasks")
        op.drop_index("ix_project_tasks_completed_at", table_name="project_tasks")
        op.drop_index("ix_project_tasks_due_at", table_name="project_tasks")
        op.drop_index("ix_project_tasks_priority", table_name="project_tasks")
        op.drop_index("ix_project_tasks_status", table_name="project_tasks")
        op.drop_index("ix_project_tasks_owner_user_id", table_name="project_tasks")
        op.drop_index("ix_project_tasks_title", table_name="project_tasks")
        op.drop_index("ix_project_tasks_stage_id", table_name="project_tasks")
        op.drop_index("ix_project_tasks_project_id", table_name="project_tasks")
        op.drop_index("ix_project_tasks_id", table_name="project_tasks")
        op.drop_table("project_tasks")
    tables = _tables()
    if "project_stages" in tables:
        op.drop_index("ix_project_stages_project_order", table_name="project_stages")
        op.drop_index("ix_project_stages_owner_user_id", table_name="project_stages")
        op.drop_index("ix_project_stages_status", table_name="project_stages")
        op.drop_index("ix_project_stages_stage_key", table_name="project_stages")
        op.drop_index("ix_project_stages_project_id", table_name="project_stages")
        op.drop_index("ix_project_stages_id", table_name="project_stages")
        op.drop_table("project_stages")
    tables = _tables()
    if "projects" in tables:
        op.drop_index("ix_projects_manager_status", table_name="projects")
        op.drop_index("ix_projects_status_risk", table_name="projects")
        op.drop_index("ix_projects_created_by", table_name="projects")
        op.drop_index("ix_projects_planned_finish_at", table_name="projects")
        op.drop_index("ix_projects_planned_start_at", table_name="projects")
        op.drop_index("ix_projects_owner_department", table_name="projects")
        op.drop_index("ix_projects_project_manager_id", table_name="projects")
        op.drop_index("ix_projects_risk_level", table_name="projects")
        op.drop_index("ix_projects_status", table_name="projects")
        op.drop_index("ix_projects_client_name", table_name="projects")
        op.drop_index("ix_projects_name", table_name="projects")
        op.drop_index("ix_projects_project_code", table_name="projects")
        op.drop_index("ix_projects_id", table_name="projects")
        op.drop_table("projects")
