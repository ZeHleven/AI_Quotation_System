"""add commercial bid quote import snapshots

Revision ID: 20260722_0061
Revises: 20260720_0060
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260722_0061"
down_revision: Union[str, None] = "20260720_0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    if "bid_business_bid_quote_imports" in _tables():
        return
    op.create_table(
        "bid_business_bid_quote_imports",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("import_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("budget_project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pricing_draft_id", sa.Integer(), sa.ForeignKey("budget_project_pricing_drafts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("source_draft_uuid", sa.String(length=36), nullable=False),
        sa.Column("source_draft_revision", sa.Integer(), nullable=False),
        sa.Column("pricing_mode", sa.String(length=32), nullable=False),
        sa.Column("source_project_name", sa.String(length=255), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("snapshot_json", _long_text(), nullable=False),
        sa.Column("import_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("import_uuid", name="uq_bid_business_bid_quote_imports_uuid"),
        sa.UniqueConstraint("project_id", "version_no", name="uq_bid_business_bid_quote_imports_project_version"),
    )
    for name, columns, unique in (
        ("ix_bid_business_bid_quote_imports_id", ["id"], False),
        ("ix_bid_business_bid_quote_imports_import_uuid", ["import_uuid"], True),
        ("ix_bid_business_bid_quote_imports_project_id", ["project_id"], False),
        ("ix_bid_business_bid_quote_imports_budget_project_id", ["budget_project_id"], False),
        ("ix_bid_business_bid_quote_imports_pricing_draft_id", ["pricing_draft_id"], False),
        ("ix_bid_business_bid_quote_imports_status", ["status"], False),
        ("ix_bid_business_bid_quote_imports_source_draft_uuid", ["source_draft_uuid"], False),
        ("ix_bid_business_bid_quote_imports_created_by", ["created_by"], False),
        ("ix_bid_business_bid_quote_imports_project_status", ["project_id", "status"], False),
        ("ix_bid_business_bid_quote_imports_budget_project", ["budget_project_id"], False),
    ):
        op.create_index(name, "bid_business_bid_quote_imports", columns, unique=unique)


def downgrade() -> None:
    if "bid_business_bid_quote_imports" not in _tables():
        return
    for name in (
        "ix_bid_business_bid_quote_imports_budget_project",
        "ix_bid_business_bid_quote_imports_project_status",
        "ix_bid_business_bid_quote_imports_created_by",
        "ix_bid_business_bid_quote_imports_source_draft_uuid",
        "ix_bid_business_bid_quote_imports_status",
        "ix_bid_business_bid_quote_imports_pricing_draft_id",
        "ix_bid_business_bid_quote_imports_budget_project_id",
        "ix_bid_business_bid_quote_imports_project_id",
        "ix_bid_business_bid_quote_imports_import_uuid",
        "ix_bid_business_bid_quote_imports_id",
    ):
        _drop_index_if_exists(name, "bid_business_bid_quote_imports")
    op.drop_table("bid_business_bid_quote_imports")
