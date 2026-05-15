"""enhance material readability and snapshot diffs

Revision ID: 20260514_0010
Revises: 20260514_0009
Create Date: 2026-05-14
"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260514_0010"
down_revision: Union[str, None] = "20260514_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in _columns(table_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if name in _indexes(table_name):
        op.drop_index(name, table_name=table_name)


def upgrade() -> None:
    if "materials" in _tables():
        _add_column_if_missing("materials", sa.Column("category", sa.String(length=128), nullable=True))
        _add_column_if_missing("materials", sa.Column("spec", sa.String(length=255), nullable=True))
        _add_column_if_missing("materials", sa.Column("brand", sa.String(length=128), nullable=True))
        _add_column_if_missing("materials", sa.Column("supplier", sa.String(length=128), nullable=True))
        _add_column_if_missing("materials", sa.Column("region", sa.String(length=128), nullable=True))
        _add_column_if_missing(
            "materials",
            sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        )
        _add_column_if_missing(
            "materials",
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        )
        _add_column_if_missing("materials", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing(
            "materials",
            sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        )
        _add_column_if_missing("materials", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))

        _create_index_if_missing("ix_materials_category", "materials", ["category"])
        _create_index_if_missing("ix_materials_source", "materials", ["source"])
        _create_index_if_missing("ix_materials_status", "materials", ["status"])

        op.execute(
            sa.text(
                "UPDATE materials SET source = 'manual' "
                "WHERE source IS NULL OR TRIM(source) = ''"
            )
        )
        op.execute(
            sa.text(
                "UPDATE materials SET status = CASE WHEN is_draft = 1 THEN 'draft' ELSE 'active' END "
                "WHERE status IS NULL OR TRIM(status) = '' OR is_draft = 1"
            )
        )
        op.execute(sa.text("UPDATE materials SET usage_count = 0 WHERE usage_count IS NULL"))

    if "material_snapshots" in _tables():
        _add_column_if_missing(
            "material_snapshots",
            sa.Column("added_count", sa.Integer(), nullable=False, server_default="0"),
        )
        _add_column_if_missing(
            "material_snapshots",
            sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        )
        _add_column_if_missing(
            "material_snapshots",
            sa.Column("deleted_count", sa.Integer(), nullable=False, server_default="0"),
        )
        _add_column_if_missing("material_snapshots", sa.Column("diff_summary_json", _long_text(), nullable=True))

        empty_diff = json.dumps(
            {
                "added_count": 0,
                "updated_count": 0,
                "deleted_count": 0,
                "added": [],
                "updated": [],
                "deleted": [],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        op.execute(
            sa.text(
                "UPDATE material_snapshots SET added_count = 0 "
                "WHERE added_count IS NULL"
            )
        )
        op.execute(
            sa.text(
                "UPDATE material_snapshots SET updated_count = 0 "
                "WHERE updated_count IS NULL"
            )
        )
        op.execute(
            sa.text(
                "UPDATE material_snapshots SET deleted_count = 0 "
                "WHERE deleted_count IS NULL"
            )
        )
        op.execute(
            sa.text(
                "UPDATE material_snapshots SET diff_summary_json = :diff "
                "WHERE diff_summary_json IS NULL OR TRIM(diff_summary_json) = ''"
            ).bindparams(diff=empty_diff)
        )


def downgrade() -> None:
    if "material_snapshots" in _tables():
        for column_name in ("diff_summary_json", "deleted_count", "updated_count", "added_count"):
            _drop_column_if_exists("material_snapshots", column_name)

    if "materials" in _tables():
        _drop_index_if_exists("ix_materials_status", "materials")
        _drop_index_if_exists("ix_materials_source", "materials")
        _drop_index_if_exists("ix_materials_category", "materials")
        for column_name in (
            "last_used_at",
            "usage_count",
            "last_verified_at",
            "status",
            "source",
            "region",
            "supplier",
            "brand",
            "spec",
            "category",
        ):
            _drop_column_if_exists("materials", column_name)
