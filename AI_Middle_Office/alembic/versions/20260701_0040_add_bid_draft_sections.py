"""add bid draft sections

Revision ID: 20260701_0040
Revises: 20260630_0039
Create Date: 2026-07-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260701_0040"
down_revision: Union[str, None] = "20260630_0039"
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
    if "bid_draft_sections" in _tables():
        return
    op.create_table(
        "bid_draft_sections",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("draft_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
        sa.Column("section_key", sa.String(length=255), nullable=False),
        sa.Column("section_title", sa.String(length=255), nullable=False),
        sa.Column("section_type", sa.String(length=64), nullable=False),
        sa.Column("owner_role", sa.String(length=64), nullable=True),
        sa.Column("draft_mode", sa.String(length=32), nullable=False, server_default="placeholder"),
        sa.Column("draft_status", sa.String(length=32), nullable=False, server_default="needs_input"),
        sa.Column("content_markdown", _large_text(), nullable=False),
        sa.Column("placeholders_json", _large_text(), nullable=True),
        sa.Column("source_response_item_uuids_json", _large_text(), nullable=True),
        sa.Column("source_requirement_ids_json", _large_text(), nullable=True),
        sa.Column("source_risk_ids_json", _large_text(), nullable=True),
        sa.Column("evidence_json", _large_text(), nullable=True),
        sa.Column("warnings_json", _large_text(), nullable=True),
        sa.Column("generator_type", sa.String(length=32), nullable=False, server_default="rule"),
        sa.Column("generator_model", sa.String(length=128), nullable=True),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("reviewer_note", _large_text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("draft_uuid", name="uq_bid_draft_sections_uuid"),
        sa.UniqueConstraint("parse_run_id", "section_key", name="uq_bid_draft_sections_run_section"),
    )
    op.create_index("ix_bid_draft_sections_id", "bid_draft_sections", ["id"])
    op.create_index("ix_bid_draft_sections_draft_uuid", "bid_draft_sections", ["draft_uuid"], unique=True)
    op.create_index("ix_bid_draft_sections_project_id", "bid_draft_sections", ["project_id"])
    op.create_index("ix_bid_draft_sections_parse_run_id", "bid_draft_sections", ["parse_run_id"])
    op.create_index("ix_bid_draft_sections_section_key", "bid_draft_sections", ["section_key"])
    op.create_index("ix_bid_draft_sections_section_type", "bid_draft_sections", ["section_type"])
    op.create_index("ix_bid_draft_sections_draft_mode", "bid_draft_sections", ["draft_mode"])
    op.create_index("ix_bid_draft_sections_draft_status", "bid_draft_sections", ["draft_status"])
    op.create_index("ix_bid_draft_sections_generator_type", "bid_draft_sections", ["generator_type"])
    op.create_index("ix_bid_draft_sections_review_status", "bid_draft_sections", ["review_status"])
    op.create_index("ix_bid_draft_sections_reviewed_by", "bid_draft_sections", ["reviewed_by"])
    op.create_index("ix_bid_draft_sections_created_by", "bid_draft_sections", ["created_by"])
    op.create_index("ix_bid_draft_sections_project_status", "bid_draft_sections", ["project_id", "review_status"])
    op.create_index("ix_bid_draft_sections_run_type", "bid_draft_sections", ["parse_run_id", "section_type"])
    op.create_index("ix_bid_draft_sections_run_section", "bid_draft_sections", ["parse_run_id", "section_key"])


def downgrade() -> None:
    if "bid_draft_sections" not in _tables():
        return
    for index_name in (
        "ix_bid_draft_sections_run_section",
        "ix_bid_draft_sections_run_type",
        "ix_bid_draft_sections_project_status",
        "ix_bid_draft_sections_created_by",
        "ix_bid_draft_sections_reviewed_by",
        "ix_bid_draft_sections_review_status",
        "ix_bid_draft_sections_generator_type",
        "ix_bid_draft_sections_draft_status",
        "ix_bid_draft_sections_draft_mode",
        "ix_bid_draft_sections_section_type",
        "ix_bid_draft_sections_section_key",
        "ix_bid_draft_sections_parse_run_id",
        "ix_bid_draft_sections_project_id",
        "ix_bid_draft_sections_draft_uuid",
        "ix_bid_draft_sections_id",
    ):
        _drop_index_if_exists(index_name, "bid_draft_sections")
    op.drop_table("bid_draft_sections")
