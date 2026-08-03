"""add bid file format plans

Revision ID: 20260701_0042
Revises: 20260701_0041
Create Date: 2026-07-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260701_0042"
down_revision: Union[str, None] = "20260701_0041"
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
    if "bid_file_format_plans" in _tables():
        return
    op.create_table(
        "bid_file_format_plans",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("plan_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
        sa.Column("format_version", sa.String(length=64), nullable=False),
        sa.Column("format_source", sa.String(length=64), nullable=False, server_default="not_found"),
        sa.Column("package_mode", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("structure_json", _large_text(), nullable=False),
        sa.Column("summary_json", _large_text(), nullable=True),
        sa.Column("warnings_json", _large_text(), nullable=True),
        sa.Column("reviewer_note", _large_text(), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plan_uuid", name="uq_bid_file_format_plans_uuid"),
        sa.UniqueConstraint("parse_run_id", name="uq_bid_file_format_plans_run"),
    )
    op.create_index("ix_bid_file_format_plans_id", "bid_file_format_plans", ["id"])
    op.create_index("ix_bid_file_format_plans_plan_uuid", "bid_file_format_plans", ["plan_uuid"], unique=True)
    op.create_index("ix_bid_file_format_plans_project_id", "bid_file_format_plans", ["project_id"])
    op.create_index("ix_bid_file_format_plans_parse_run_id", "bid_file_format_plans", ["parse_run_id"])
    op.create_index("ix_bid_file_format_plans_format_version", "bid_file_format_plans", ["format_version"])
    op.create_index("ix_bid_file_format_plans_format_source", "bid_file_format_plans", ["format_source"])
    op.create_index("ix_bid_file_format_plans_package_mode", "bid_file_format_plans", ["package_mode"])
    op.create_index("ix_bid_file_format_plans_review_status", "bid_file_format_plans", ["review_status"])
    op.create_index("ix_bid_file_format_plans_confirmed_by", "bid_file_format_plans", ["confirmed_by"])
    op.create_index("ix_bid_file_format_plans_created_by", "bid_file_format_plans", ["created_by"])
    op.create_index("ix_bid_file_format_plans_project_status", "bid_file_format_plans", ["project_id", "review_status"])
    op.create_index("ix_bid_file_format_plans_run_status", "bid_file_format_plans", ["parse_run_id", "review_status"])


def downgrade() -> None:
    if "bid_file_format_plans" not in _tables():
        return
    for index_name in (
        "ix_bid_file_format_plans_run_status",
        "ix_bid_file_format_plans_project_status",
        "ix_bid_file_format_plans_created_by",
        "ix_bid_file_format_plans_confirmed_by",
        "ix_bid_file_format_plans_review_status",
        "ix_bid_file_format_plans_package_mode",
        "ix_bid_file_format_plans_format_source",
        "ix_bid_file_format_plans_format_version",
        "ix_bid_file_format_plans_parse_run_id",
        "ix_bid_file_format_plans_project_id",
        "ix_bid_file_format_plans_plan_uuid",
        "ix_bid_file_format_plans_id",
    ):
        _drop_index_if_exists(index_name, "bid_file_format_plans")
    op.drop_table("bid_file_format_plans")
