"""add bid file format plan events

Revision ID: 20260701_0043
Revises: 20260701_0042
Create Date: 2026-07-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260701_0043"
down_revision: Union[str, None] = "20260701_0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _large_text():
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        return mysql.LONGTEXT()
    return sa.Text()


def upgrade() -> None:
    if "bid_file_format_plan_events" in _tables():
        return
    op.create_table(
        "bid_file_format_plan_events",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("event_uuid", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("bid_file_format_plans.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("item_key", sa.String(length=255), nullable=True),
        sa.Column("item_title", sa.String(length=255), nullable=True),
        sa.Column("from_package_key", sa.String(length=64), nullable=True),
        sa.Column("to_package_key", sa.String(length=64), nullable=True),
        sa.Column("detail_json", _large_text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_uuid", name="uq_bid_file_format_plan_events_uuid"),
    )
    op.create_index("ix_bid_file_format_plan_events_id", "bid_file_format_plan_events", ["id"])
    op.create_index("ix_bid_file_format_plan_events_event_uuid", "bid_file_format_plan_events", ["event_uuid"], unique=True)
    op.create_index("ix_bid_file_format_plan_events_plan_id", "bid_file_format_plan_events", ["plan_id"])
    op.create_index("ix_bid_file_format_plan_events_project_id", "bid_file_format_plan_events", ["project_id"])
    op.create_index("ix_bid_file_format_plan_events_parse_run_id", "bid_file_format_plan_events", ["parse_run_id"])
    op.create_index("ix_bid_file_format_plan_events_event_type", "bid_file_format_plan_events", ["event_type"])
    op.create_index("ix_bid_file_format_plan_events_item_key", "bid_file_format_plan_events", ["item_key"])
    op.create_index("ix_bid_file_format_plan_events_from_package_key", "bid_file_format_plan_events", ["from_package_key"])
    op.create_index("ix_bid_file_format_plan_events_to_package_key", "bid_file_format_plan_events", ["to_package_key"])
    op.create_index("ix_bid_file_format_plan_events_created_by", "bid_file_format_plan_events", ["created_by"])
    op.create_index("ix_bid_file_format_plan_events_plan_created", "bid_file_format_plan_events", ["plan_id", "created_at"])
    op.create_index("ix_bid_file_format_plan_events_project_created", "bid_file_format_plan_events", ["project_id", "created_at"])
    op.create_index("ix_bid_file_format_plan_events_run_created", "bid_file_format_plan_events", ["parse_run_id", "created_at"])
    op.create_index("ix_bid_file_format_plan_events_type", "bid_file_format_plan_events", ["event_type"])


def downgrade() -> None:
    if "bid_file_format_plan_events" not in _tables():
        return
    for index_name in (
        "ix_bid_file_format_plan_events_type",
        "ix_bid_file_format_plan_events_run_created",
        "ix_bid_file_format_plan_events_project_created",
        "ix_bid_file_format_plan_events_plan_created",
        "ix_bid_file_format_plan_events_created_by",
        "ix_bid_file_format_plan_events_to_package_key",
        "ix_bid_file_format_plan_events_from_package_key",
        "ix_bid_file_format_plan_events_item_key",
        "ix_bid_file_format_plan_events_event_type",
        "ix_bid_file_format_plan_events_parse_run_id",
        "ix_bid_file_format_plan_events_project_id",
        "ix_bid_file_format_plan_events_plan_id",
        "ix_bid_file_format_plan_events_event_uuid",
        "ix_bid_file_format_plan_events_id",
    ):
        _drop_index_if_exists(index_name, "bid_file_format_plan_events")
    op.drop_table("bid_file_format_plan_events")
