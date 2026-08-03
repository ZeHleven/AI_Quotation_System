"""add enterprise profile tables

Revision ID: 20260703_0044
Revises: 20260701_0043
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260703_0044"
down_revision: Union[str, None] = "20260701_0043"
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
    if "enterprise_profile_items" not in _tables():
        op.create_table(
            "enterprise_profile_items",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("item_uuid", sa.String(length=36), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("subcategory", sa.String(length=128), nullable=True),
            sa.Column("profile_key", sa.String(length=128), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("content_text", _large_text(), nullable=True),
            sa.Column("structured_json", _large_text(), nullable=True),
            sa.Column("tags_json", _large_text(), nullable=True),
            sa.Column("applicable_scope", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=64), server_default="manual", nullable=False),
            sa.Column("confidentiality", sa.String(length=32), server_default="internal", nullable=False),
            sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
            sa.Column("valid_from", sa.Date(), nullable=True),
            sa.Column("valid_until", sa.Date(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("item_uuid", name="uq_enterprise_profile_items_uuid"),
        )
        op.create_index("ix_enterprise_profile_items_id", "enterprise_profile_items", ["id"])
        op.create_index("ix_enterprise_profile_items_item_uuid", "enterprise_profile_items", ["item_uuid"], unique=True)
        op.create_index("ix_enterprise_profile_items_category", "enterprise_profile_items", ["category"])
        op.create_index("ix_enterprise_profile_items_subcategory", "enterprise_profile_items", ["subcategory"])
        op.create_index("ix_enterprise_profile_items_profile_key", "enterprise_profile_items", ["profile_key"])
        op.create_index("ix_enterprise_profile_items_title", "enterprise_profile_items", ["title"])
        op.create_index("ix_enterprise_profile_items_source", "enterprise_profile_items", ["source"])
        op.create_index("ix_enterprise_profile_items_confidentiality", "enterprise_profile_items", ["confidentiality"])
        op.create_index("ix_enterprise_profile_items_status", "enterprise_profile_items", ["status"])
        op.create_index("ix_enterprise_profile_items_valid_from", "enterprise_profile_items", ["valid_from"])
        op.create_index("ix_enterprise_profile_items_valid_until", "enterprise_profile_items", ["valid_until"])
        op.create_index("ix_enterprise_profile_items_created_by", "enterprise_profile_items", ["created_by"])
        op.create_index("ix_enterprise_profile_items_updated_by", "enterprise_profile_items", ["updated_by"])
        op.create_index("ix_enterprise_profile_items_approved_by", "enterprise_profile_items", ["approved_by"])
        op.create_index("ix_enterprise_profile_items_approved_at", "enterprise_profile_items", ["approved_at"])
        op.create_index("ix_enterprise_profile_items_archived_by", "enterprise_profile_items", ["archived_by"])
        op.create_index("ix_enterprise_profile_items_category_status", "enterprise_profile_items", ["category", "status"])
        op.create_index("ix_enterprise_profile_items_status_valid_until", "enterprise_profile_items", ["status", "valid_until"])

    if "enterprise_profile_files" not in _tables():
        op.create_table(
            "enterprise_profile_files",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("attachment_uuid", sa.String(length=36), nullable=False),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("enterprise_profile_items.id"), nullable=False),
            sa.Column("file_id", sa.String(length=36), sa.ForeignKey("file_objects.file_id"), nullable=True),
            sa.Column("attachment_type", sa.String(length=64), server_default="source", nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_primary", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("attachment_uuid", name="uq_enterprise_profile_files_uuid"),
        )
        op.create_index("ix_enterprise_profile_files_id", "enterprise_profile_files", ["id"])
        op.create_index("ix_enterprise_profile_files_attachment_uuid", "enterprise_profile_files", ["attachment_uuid"], unique=True)
        op.create_index("ix_enterprise_profile_files_item_id", "enterprise_profile_files", ["item_id"])
        op.create_index("ix_enterprise_profile_files_file_id", "enterprise_profile_files", ["file_id"])
        op.create_index("ix_enterprise_profile_files_attachment_type", "enterprise_profile_files", ["attachment_type"])
        op.create_index("ix_enterprise_profile_files_is_primary", "enterprise_profile_files", ["is_primary"])
        op.create_index("ix_enterprise_profile_files_uploaded_by", "enterprise_profile_files", ["uploaded_by"])
        op.create_index("ix_enterprise_profile_files_item_type", "enterprise_profile_files", ["item_id", "attachment_type"])

    if "enterprise_profile_events" not in _tables():
        op.create_table(
            "enterprise_profile_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("event_uuid", sa.String(length=36), nullable=False),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("enterprise_profile_items.id"), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("old_status", sa.String(length=24), nullable=True),
            sa.Column("new_status", sa.String(length=24), nullable=True),
            sa.Column("detail_json", _large_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("event_uuid", name="uq_enterprise_profile_events_uuid"),
        )
        op.create_index("ix_enterprise_profile_events_id", "enterprise_profile_events", ["id"])
        op.create_index("ix_enterprise_profile_events_event_uuid", "enterprise_profile_events", ["event_uuid"], unique=True)
        op.create_index("ix_enterprise_profile_events_item_id", "enterprise_profile_events", ["item_id"])
        op.create_index("ix_enterprise_profile_events_event_type", "enterprise_profile_events", ["event_type"])
        op.create_index("ix_enterprise_profile_events_created_by", "enterprise_profile_events", ["created_by"])
        op.create_index("ix_enterprise_profile_events_item_created", "enterprise_profile_events", ["item_id", "created_at"])
        op.create_index("ix_enterprise_profile_events_type_created", "enterprise_profile_events", ["event_type", "created_at"])


def downgrade() -> None:
    for table_name, index_names in (
        (
            "enterprise_profile_events",
            (
                "ix_enterprise_profile_events_type_created",
                "ix_enterprise_profile_events_item_created",
                "ix_enterprise_profile_events_created_by",
                "ix_enterprise_profile_events_event_type",
                "ix_enterprise_profile_events_item_id",
                "ix_enterprise_profile_events_event_uuid",
                "ix_enterprise_profile_events_id",
            ),
        ),
        (
            "enterprise_profile_files",
            (
                "ix_enterprise_profile_files_item_type",
                "ix_enterprise_profile_files_uploaded_by",
                "ix_enterprise_profile_files_is_primary",
                "ix_enterprise_profile_files_attachment_type",
                "ix_enterprise_profile_files_file_id",
                "ix_enterprise_profile_files_item_id",
                "ix_enterprise_profile_files_attachment_uuid",
                "ix_enterprise_profile_files_id",
            ),
        ),
        (
            "enterprise_profile_items",
            (
                "ix_enterprise_profile_items_status_valid_until",
                "ix_enterprise_profile_items_category_status",
                "ix_enterprise_profile_items_archived_by",
                "ix_enterprise_profile_items_approved_at",
                "ix_enterprise_profile_items_approved_by",
                "ix_enterprise_profile_items_updated_by",
                "ix_enterprise_profile_items_created_by",
                "ix_enterprise_profile_items_valid_until",
                "ix_enterprise_profile_items_valid_from",
                "ix_enterprise_profile_items_status",
                "ix_enterprise_profile_items_confidentiality",
                "ix_enterprise_profile_items_source",
                "ix_enterprise_profile_items_title",
                "ix_enterprise_profile_items_profile_key",
                "ix_enterprise_profile_items_subcategory",
                "ix_enterprise_profile_items_category",
                "ix_enterprise_profile_items_item_uuid",
                "ix_enterprise_profile_items_id",
            ),
        ),
    ):
        if table_name in _tables():
            for index_name in index_names:
                _drop_index_if_exists(index_name, table_name)
            op.drop_table(table_name)
