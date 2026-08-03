"""add account-scoped editable quota catalog

Revision ID: 20260716_0053
Revises: 20260716_0052
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260716_0053"
down_revision: Union[str, None] = "20260716_0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext() -> sa.types.TypeEngine:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    op.create_table(
        "account_quota_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_uuid", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("quota_code", sa.String(length=64), nullable=True),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("item_features", _longtext(), nullable=True),
        sa.Column("spec", _longtext(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("notes", _longtext(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_uuid", name="uq_account_quota_items_uuid"),
        sa.UniqueConstraint("account_id", "fingerprint", name="uq_account_quota_items_account_fingerprint"),
    )
    for name, columns in (
        ("ix_account_quota_items_account_id", ["account_id"]),
        ("ix_account_quota_items_quota_code", ["quota_code"]),
        ("ix_account_quota_items_item_name", ["item_name"]),
        ("ix_account_quota_items_unit", ["unit"]),
        ("ix_account_quota_items_fingerprint", ["fingerprint"]),
        ("ix_account_quota_items_source", ["source"]),
        ("ix_account_quota_items_status", ["status"]),
        ("ix_account_quota_items_created_by", ["created_by"]),
        ("ix_account_quota_items_updated_by", ["updated_by"]),
        ("ix_account_quota_items_account_status", ["account_id", "status"]),
        ("ix_account_quota_items_account_updated", ["account_id", "updated_at"]),
        ("ix_account_quota_items_account_name_unit", ["account_id", "item_name", "unit"]),
    ):
        op.create_index(name, "account_quota_items", columns)

    op.create_table(
        "account_quota_item_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_quota_item_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("before_snapshot_json", _longtext(), nullable=True),
        sa.Column("after_snapshot_json", _longtext(), nullable=False),
        sa.Column("reason", _longtext(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_quota_item_id"], ["account_quota_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_quota_item_id", "revision", name="uq_account_quota_history_item_revision"),
    )
    for name, columns in (
        ("ix_account_quota_item_history_account_quota_item_id", ["account_quota_item_id"]),
        ("ix_account_quota_item_history_account_id", ["account_id"]),
        ("ix_account_quota_item_history_event_type", ["event_type"]),
        ("ix_account_quota_item_history_actor_id", ["actor_id"]),
        ("ix_account_quota_history_account_created", ["account_id", "created_at"]),
        ("ix_account_quota_history_item_created", ["account_quota_item_id", "created_at"]),
    ):
        op.create_index(name, "account_quota_item_history", columns)


def downgrade() -> None:
    op.drop_table("account_quota_item_history")
    op.drop_table("account_quota_items")
