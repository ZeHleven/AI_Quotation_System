"""add formal business bid package records

Revision ID: 20260723_0062
Revises: 20260722_0061
Create Date: 2026-07-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260723_0062"
down_revision: Union[str, None] = "20260722_0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)} if table_name in _tables() else set()


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    if "bid_business_bid_formal_packages" in _tables():
        return
    op.create_table(
        "bid_business_bid_formal_packages",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("package_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
        sa.Column("quote_import_id", sa.Integer(), sa.ForeignKey("bid_business_bid_quote_imports.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="generated"),
        sa.Column("manifest_json", _long_text(), nullable=False),
        sa.Column("output_file_id", sa.String(length=36), sa.ForeignKey("file_objects.file_id"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("package_uuid", name="uq_bid_business_bid_formal_packages_uuid"),
    )
    for name, columns, unique in (
        ("ix_bid_business_bid_formal_packages_id", ["id"], False),
        ("ix_bid_business_bid_formal_packages_package_uuid", ["package_uuid"], True),
        ("ix_bid_business_bid_formal_packages_project_id", ["project_id"], False),
        ("ix_bid_business_bid_formal_packages_parse_run_id", ["parse_run_id"], False),
        ("ix_bid_business_bid_formal_packages_quote_import_id", ["quote_import_id"], False),
        ("ix_bid_business_bid_formal_packages_status", ["status"], False),
        ("ix_bid_business_bid_formal_packages_output_file_id", ["output_file_id"], False),
        ("ix_bid_business_bid_formal_packages_created_by", ["created_by"], False),
        ("ix_bid_business_bid_formal_packages_project_created", ["project_id", "created_at"], False),
        ("ix_bid_business_bid_formal_packages_run_created", ["parse_run_id", "created_at"], False),
        ("ix_bid_business_bid_formal_packages_quote_import", ["quote_import_id"], False),
    ):
        op.create_index(name, "bid_business_bid_formal_packages", columns, unique=unique)


def downgrade() -> None:
    if "bid_business_bid_formal_packages" not in _tables():
        return
    for name in (
        "ix_bid_business_bid_formal_packages_quote_import",
        "ix_bid_business_bid_formal_packages_run_created",
        "ix_bid_business_bid_formal_packages_project_created",
        "ix_bid_business_bid_formal_packages_created_by",
        "ix_bid_business_bid_formal_packages_output_file_id",
        "ix_bid_business_bid_formal_packages_status",
        "ix_bid_business_bid_formal_packages_quote_import_id",
        "ix_bid_business_bid_formal_packages_parse_run_id",
        "ix_bid_business_bid_formal_packages_project_id",
        "ix_bid_business_bid_formal_packages_package_uuid",
        "ix_bid_business_bid_formal_packages_id",
    ):
        _drop_index_if_exists(name, "bid_business_bid_formal_packages")
    op.drop_table("bid_business_bid_formal_packages")