"""add tender response items

Revision ID: 20260630_0039
Revises: 20260630_0038
Create Date: 2026-06-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260630_0039"
down_revision: Union[str, None] = "20260630_0038"
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
    if "tender_response_items" in _tables():
        return
    op.create_table(
        "tender_response_items",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("response_item_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
        sa.Column("business_object_id", sa.Integer(), sa.ForeignKey("tender_business_objects.id"), nullable=True),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("tender_requirements.id"), nullable=True),
        sa.Column("risk_id", sa.Integer(), sa.ForeignKey("tender_risks.id"), nullable=True),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("response_category", sa.String(length=64), nullable=False),
        sa.Column("response_action", sa.String(length=64), nullable=False),
        sa.Column("response_title", sa.String(length=255), nullable=False),
        sa.Column("source_text", _large_text(), nullable=False),
        sa.Column("evidence_json", _large_text(), nullable=True),
        sa.Column("owner_role", sa.String(length=64), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("response_note", _large_text(), nullable=True),
        sa.Column("reviewer_note", _large_text(), nullable=True),
        sa.Column("created_from", sa.String(length=64), nullable=False, server_default="business_object"),
        sa.Column("normalized_json", _large_text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("response_item_uuid", name="uq_tender_response_items_uuid"),
        sa.UniqueConstraint("parse_run_id", "source_key", name="uq_tender_response_items_run_source"),
    )
    op.create_index("ix_tender_response_items_id", "tender_response_items", ["id"])
    op.create_index("ix_tender_response_items_response_item_uuid", "tender_response_items", ["response_item_uuid"], unique=True)
    op.create_index("ix_tender_response_items_project_id", "tender_response_items", ["project_id"])
    op.create_index("ix_tender_response_items_parse_run_id", "tender_response_items", ["parse_run_id"])
    op.create_index("ix_tender_response_items_business_object_id", "tender_response_items", ["business_object_id"])
    op.create_index("ix_tender_response_items_requirement_id", "tender_response_items", ["requirement_id"])
    op.create_index("ix_tender_response_items_risk_id", "tender_response_items", ["risk_id"])
    op.create_index("ix_tender_response_items_source_key", "tender_response_items", ["source_key"])
    op.create_index("ix_tender_response_items_response_category", "tender_response_items", ["response_category"])
    op.create_index("ix_tender_response_items_response_action", "tender_response_items", ["response_action"])
    op.create_index("ix_tender_response_items_risk_level", "tender_response_items", ["risk_level"])
    op.create_index("ix_tender_response_items_status", "tender_response_items", ["status"])
    op.create_index("ix_tender_response_items_created_from", "tender_response_items", ["created_from"])
    op.create_index("ix_tender_response_items_reviewed_by", "tender_response_items", ["reviewed_by"])
    op.create_index("ix_tender_response_items_created_by", "tender_response_items", ["created_by"])
    op.create_index("ix_tender_response_items_project_status", "tender_response_items", ["project_id", "status"])
    op.create_index("ix_tender_response_items_run_status", "tender_response_items", ["parse_run_id", "status"])
    op.create_index("ix_tender_response_items_run_action", "tender_response_items", ["parse_run_id", "response_action"])
    op.create_index("ix_tender_response_items_run_category", "tender_response_items", ["parse_run_id", "response_category"])


def downgrade() -> None:
    if "tender_response_items" not in _tables():
        return
    for index_name in (
        "ix_tender_response_items_run_category",
        "ix_tender_response_items_run_action",
        "ix_tender_response_items_run_status",
        "ix_tender_response_items_project_status",
        "ix_tender_response_items_created_by",
        "ix_tender_response_items_reviewed_by",
        "ix_tender_response_items_created_from",
        "ix_tender_response_items_status",
        "ix_tender_response_items_risk_level",
        "ix_tender_response_items_response_action",
        "ix_tender_response_items_response_category",
        "ix_tender_response_items_source_key",
        "ix_tender_response_items_risk_id",
        "ix_tender_response_items_requirement_id",
        "ix_tender_response_items_business_object_id",
        "ix_tender_response_items_parse_run_id",
        "ix_tender_response_items_project_id",
        "ix_tender_response_items_response_item_uuid",
        "ix_tender_response_items_id",
    ):
        _drop_index_if_exists(index_name, "tender_response_items")
    op.drop_table("tender_response_items")
