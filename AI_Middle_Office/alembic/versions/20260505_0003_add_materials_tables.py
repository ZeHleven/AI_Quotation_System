"""add materials tables

Revision ID: 20260505_0003
Revises: 20260502_0002
Create Date: 2026-05-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260505_0003"
down_revision: Union[str, None] = "20260502_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    existing_tables = _tables()

    if "materials" not in existing_tables:
        op.create_table(
            "materials",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("material_id", sa.String(length=64), nullable=False),
            sa.Column("item_name", sa.String(length=255), nullable=False),
            sa.Column("unit_price", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        )
    _create_index_if_missing("ix_materials_id", "materials", ["id"])
    _create_index_if_missing("ix_materials_material_id", "materials", ["material_id"], unique=True)
    _create_index_if_missing("ix_materials_item_name", "materials", ["item_name"])

    if "material_snapshots" not in existing_tables:
        op.create_table(
            "material_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("snapshot_id", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("data_json", _long_text(), nullable=False),
        )
    _create_index_if_missing("ix_material_snapshots_id", "material_snapshots", ["id"])
    _create_index_if_missing("ix_material_snapshots_snapshot_id", "material_snapshots", ["snapshot_id"], unique=True)
    _create_index_if_missing("ix_material_snapshots_username", "material_snapshots", ["username"])
    _create_index_if_missing("ix_material_snapshots_action", "material_snapshots", ["action"])


def downgrade() -> None:
    op.drop_table("material_snapshots")
    op.drop_table("materials")
