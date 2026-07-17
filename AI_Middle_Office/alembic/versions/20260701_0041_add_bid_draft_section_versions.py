"""add bid draft section versions

Revision ID: 20260701_0041
Revises: 20260701_0040
Create Date: 2026-07-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260701_0041"
down_revision: Union[str, None] = "20260701_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _large_text():
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        return mysql.LONGTEXT()
    return sa.Text()


def upgrade() -> None:
    columns = _columns("bid_draft_sections")
    if "content_version" not in columns:
        op.add_column(
            "bid_draft_sections",
            sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        )
    if "generation_decision_json" not in columns:
        op.add_column("bid_draft_sections", sa.Column("generation_decision_json", _large_text(), nullable=True))

    if "bid_draft_section_versions" in _tables():
        return
    op.create_table(
        "bid_draft_section_versions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("version_uuid", sa.String(length=36), nullable=False),
        sa.Column("draft_section_id", sa.Integer(), sa.ForeignKey("bid_draft_sections.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(length=32), nullable=False, server_default="generated"),
        sa.Column("content_markdown", _large_text(), nullable=False),
        sa.Column("editor_note", _large_text(), nullable=True),
        sa.Column("generator_type", sa.String(length=32), nullable=True),
        sa.Column("generator_model", sa.String(length=128), nullable=True),
        sa.Column("edited_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("version_uuid", name="uq_bid_draft_section_versions_uuid"),
        sa.UniqueConstraint("draft_section_id", "version_no", name="uq_bid_draft_section_versions_section_no"),
    )
    op.create_index("ix_bid_draft_section_versions_id", "bid_draft_section_versions", ["id"])
    op.create_index("ix_bid_draft_section_versions_version_uuid", "bid_draft_section_versions", ["version_uuid"], unique=True)
    op.create_index("ix_bid_draft_section_versions_draft_section_id", "bid_draft_section_versions", ["draft_section_id"])
    op.create_index("ix_bid_draft_section_versions_change_type", "bid_draft_section_versions", ["change_type"])
    op.create_index("ix_bid_draft_section_versions_edited_by", "bid_draft_section_versions", ["edited_by"])
    op.create_index("ix_bid_draft_section_versions_section", "bid_draft_section_versions", ["draft_section_id", "version_no"])


def downgrade() -> None:
    if "bid_draft_section_versions" in _tables():
        for index_name in (
            "ix_bid_draft_section_versions_section",
            "ix_bid_draft_section_versions_edited_by",
            "ix_bid_draft_section_versions_change_type",
            "ix_bid_draft_section_versions_draft_section_id",
            "ix_bid_draft_section_versions_version_uuid",
            "ix_bid_draft_section_versions_id",
        ):
            _drop_index_if_exists(index_name, "bid_draft_section_versions")
        op.drop_table("bid_draft_section_versions")

    columns = _columns("bid_draft_sections")
    if "generation_decision_json" in columns:
        op.drop_column("bid_draft_sections", "generation_decision_json")
    if "content_version" in columns:
        op.drop_column("bid_draft_sections", "content_version")
