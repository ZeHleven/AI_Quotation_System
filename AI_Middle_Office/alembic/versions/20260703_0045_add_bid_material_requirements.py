"""add bid material requirement checklist

Revision ID: 20260703_0045
Revises: 20260703_0044
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260703_0045"
down_revision: Union[str, None] = "20260703_0044"
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
    if "bid_material_requirements" not in _tables():
        op.create_table(
            "bid_material_requirements",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("requirement_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
            sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
            sa.Column("format_plan_id", sa.Integer(), sa.ForeignKey("bid_file_format_plans.id"), nullable=True),
            sa.Column("format_item_key", sa.String(length=255), nullable=False),
            sa.Column("package_key", sa.String(length=64), nullable=True),
            sa.Column("package_title", sa.String(length=255), nullable=True),
            sa.Column("section_key", sa.String(length=255), nullable=True),
            sa.Column("item_title", sa.String(length=255), nullable=False),
            sa.Column("requirement_type", sa.String(length=64), nullable=False),
            sa.Column("profile_category", sa.String(length=64), nullable=True),
            sa.Column("material_key", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source_file", sa.String(length=255), nullable=True),
            sa.Column("source_location", sa.String(length=255), nullable=True),
            sa.Column("source_text", sa.Text(), nullable=True),
            sa.Column("fulfillment_mode", sa.String(length=64), server_default="manual_upload", nullable=False),
            sa.Column("status", sa.String(length=32), server_default="missing", nullable=False),
            sa.Column("priority", sa.String(length=16), server_default="normal", nullable=False),
            sa.Column("owner_role", sa.String(length=64), nullable=True),
            sa.Column("candidate_profile_item_uuid", sa.String(length=36), nullable=True),
            sa.Column("submitted_profile_item_uuid", sa.String(length=36), nullable=True),
            sa.Column("submitted_file_id", sa.String(length=36), sa.ForeignKey("file_objects.file_id"), nullable=True),
            sa.Column("submitted_value", _large_text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("normalized_json", _large_text(), nullable=True),
            sa.Column("evidence_json", _large_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("requirement_uuid", name="uq_bid_material_requirements_uuid"),
            sa.UniqueConstraint("parse_run_id", "material_key", name="uq_bid_material_requirements_run_key"),
        )
        op.create_index("ix_bid_material_requirements_id", "bid_material_requirements", ["id"])
        op.create_index("ix_bid_material_requirements_requirement_uuid", "bid_material_requirements", ["requirement_uuid"], unique=True)
        op.create_index("ix_bid_material_requirements_project_id", "bid_material_requirements", ["project_id"])
        op.create_index("ix_bid_material_requirements_parse_run_id", "bid_material_requirements", ["parse_run_id"])
        op.create_index("ix_bid_material_requirements_format_plan_id", "bid_material_requirements", ["format_plan_id"])
        op.create_index("ix_bid_material_requirements_format_item_key", "bid_material_requirements", ["format_item_key"])
        op.create_index("ix_bid_material_requirements_package_key", "bid_material_requirements", ["package_key"])
        op.create_index("ix_bid_material_requirements_section_key", "bid_material_requirements", ["section_key"])
        op.create_index("ix_bid_material_requirements_requirement_type", "bid_material_requirements", ["requirement_type"])
        op.create_index("ix_bid_material_requirements_profile_category", "bid_material_requirements", ["profile_category"])
        op.create_index("ix_bid_material_requirements_material_key", "bid_material_requirements", ["material_key"])
        op.create_index("ix_bid_material_requirements_fulfillment_mode", "bid_material_requirements", ["fulfillment_mode"])
        op.create_index("ix_bid_material_requirements_status", "bid_material_requirements", ["status"])
        op.create_index("ix_bid_material_requirements_priority", "bid_material_requirements", ["priority"])
        op.create_index("ix_bid_material_requirements_candidate_profile_item_uuid", "bid_material_requirements", ["candidate_profile_item_uuid"])
        op.create_index("ix_bid_material_requirements_submitted_profile_item_uuid", "bid_material_requirements", ["submitted_profile_item_uuid"])
        op.create_index("ix_bid_material_requirements_submitted_file_id", "bid_material_requirements", ["submitted_file_id"])
        op.create_index("ix_bid_material_requirements_created_by", "bid_material_requirements", ["created_by"])
        op.create_index("ix_bid_material_requirements_updated_by", "bid_material_requirements", ["updated_by"])
        op.create_index("ix_bid_material_requirements_reviewed_by", "bid_material_requirements", ["reviewed_by"])
        op.create_index("ix_bid_material_requirements_project_status", "bid_material_requirements", ["project_id", "status"])
        op.create_index("ix_bid_material_requirements_run_status", "bid_material_requirements", ["parse_run_id", "status"])
        op.create_index("ix_bid_material_requirements_run_category", "bid_material_requirements", ["parse_run_id", "profile_category"])
        op.create_index("ix_bid_material_requirements_format_item", "bid_material_requirements", ["parse_run_id", "format_item_key"])

    if "bid_material_requirement_events" not in _tables():
        op.create_table(
            "bid_material_requirement_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("event_uuid", sa.String(length=36), nullable=False),
            sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("bid_material_requirements.id"), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
            sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("old_status", sa.String(length=32), nullable=True),
            sa.Column("new_status", sa.String(length=32), nullable=True),
            sa.Column("detail_json", _large_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("event_uuid", name="uq_bid_material_requirement_events_uuid"),
        )
        op.create_index("ix_bid_material_requirement_events_id", "bid_material_requirement_events", ["id"])
        op.create_index("ix_bid_material_requirement_events_event_uuid", "bid_material_requirement_events", ["event_uuid"], unique=True)
        op.create_index("ix_bid_material_requirement_events_requirement_id", "bid_material_requirement_events", ["requirement_id"])
        op.create_index("ix_bid_material_requirement_events_project_id", "bid_material_requirement_events", ["project_id"])
        op.create_index("ix_bid_material_requirement_events_parse_run_id", "bid_material_requirement_events", ["parse_run_id"])
        op.create_index("ix_bid_material_requirement_events_event_type", "bid_material_requirement_events", ["event_type"])
        op.create_index("ix_bid_material_requirement_events_created_by", "bid_material_requirement_events", ["created_by"])
        op.create_index(
            "ix_bid_material_requirement_events_requirement_created",
            "bid_material_requirement_events",
            ["requirement_id", "created_at"],
        )
        op.create_index(
            "ix_bid_material_requirement_events_project_created",
            "bid_material_requirement_events",
            ["project_id", "created_at"],
        )
        op.create_index(
            "ix_bid_material_requirement_events_run_created",
            "bid_material_requirement_events",
            ["parse_run_id", "created_at"],
        )
        op.create_index("ix_bid_material_requirement_events_type", "bid_material_requirement_events", ["event_type"])


def downgrade() -> None:
    for table_name, index_names in (
        (
            "bid_material_requirement_events",
            (
                "ix_bid_material_requirement_events_type",
                "ix_bid_material_requirement_events_run_created",
                "ix_bid_material_requirement_events_project_created",
                "ix_bid_material_requirement_events_requirement_created",
                "ix_bid_material_requirement_events_created_by",
                "ix_bid_material_requirement_events_event_type",
                "ix_bid_material_requirement_events_parse_run_id",
                "ix_bid_material_requirement_events_project_id",
                "ix_bid_material_requirement_events_requirement_id",
                "ix_bid_material_requirement_events_event_uuid",
                "ix_bid_material_requirement_events_id",
            ),
        ),
        (
            "bid_material_requirements",
            (
                "ix_bid_material_requirements_format_item",
                "ix_bid_material_requirements_run_category",
                "ix_bid_material_requirements_run_status",
                "ix_bid_material_requirements_project_status",
                "ix_bid_material_requirements_reviewed_by",
                "ix_bid_material_requirements_updated_by",
                "ix_bid_material_requirements_created_by",
                "ix_bid_material_requirements_submitted_file_id",
                "ix_bid_material_requirements_submitted_profile_item_uuid",
                "ix_bid_material_requirements_candidate_profile_item_uuid",
                "ix_bid_material_requirements_priority",
                "ix_bid_material_requirements_status",
                "ix_bid_material_requirements_fulfillment_mode",
                "ix_bid_material_requirements_material_key",
                "ix_bid_material_requirements_profile_category",
                "ix_bid_material_requirements_requirement_type",
                "ix_bid_material_requirements_section_key",
                "ix_bid_material_requirements_package_key",
                "ix_bid_material_requirements_format_item_key",
                "ix_bid_material_requirements_format_plan_id",
                "ix_bid_material_requirements_parse_run_id",
                "ix_bid_material_requirements_project_id",
                "ix_bid_material_requirements_requirement_uuid",
                "ix_bid_material_requirements_id",
            ),
        ),
    ):
        if table_name in _tables():
            for index_name in index_names:
                _drop_index_if_exists(index_name, table_name)
            op.drop_table(table_name)
