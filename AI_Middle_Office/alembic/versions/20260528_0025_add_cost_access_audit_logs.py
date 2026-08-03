"""add cost access audit logs

Revision ID: 20260528_0025
Revises: 20260527_0024
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260528_0025"
down_revision: Union[str, None] = "20260527_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "cost_access_audit_logs" in _tables():
        return
    op.create_table(
        "cost_access_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("roles_snapshot", sa.Text(), nullable=True),
        sa.Column("request_path", sa.String(length=255), nullable=True),
        sa.Column("request_method", sa.String(length=16), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("filters_json", sa.Text(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="success"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cost_access_audit_logs_id", "cost_access_audit_logs", ["id"])
    op.create_index("ix_cost_access_audit_logs_action", "cost_access_audit_logs", ["action"])
    op.create_index("ix_cost_access_audit_logs_resource_type", "cost_access_audit_logs", ["resource_type"])
    op.create_index("ix_cost_access_audit_logs_user_id", "cost_access_audit_logs", ["user_id"])
    op.create_index("ix_cost_access_audit_logs_username", "cost_access_audit_logs", ["username"])
    op.create_index("ix_cost_access_audit_logs_status", "cost_access_audit_logs", ["status"])
    op.create_index("ix_cost_access_audit_logs_created_at", "cost_access_audit_logs", ["created_at"])
    op.create_index("ix_cost_access_audit_logs_action_created", "cost_access_audit_logs", ["action", "created_at"])
    op.create_index("ix_cost_access_audit_logs_user_created", "cost_access_audit_logs", ["user_id", "created_at"])
    op.create_index("ix_cost_access_audit_logs_resource", "cost_access_audit_logs", ["resource_type", "resource_id"])


def downgrade() -> None:
    if "cost_access_audit_logs" not in _tables():
        return
    op.drop_index("ix_cost_access_audit_logs_resource", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_user_created", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_action_created", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_created_at", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_status", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_username", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_user_id", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_resource_type", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_action", table_name="cost_access_audit_logs")
    op.drop_index("ix_cost_access_audit_logs_id", table_name="cost_access_audit_logs")
    op.drop_table("cost_access_audit_logs")
