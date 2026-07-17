"""add tender business objects

Revision ID: 20260630_0038
Revises: 20260629_0037
Create Date: 2026-06-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260630_0038"
down_revision: Union[str, None] = "20260629_0037"
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
    if "tender_business_objects" in _tables():
        return
    op.create_table(
        "tender_business_objects",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("object_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("bid_project_files.id"), nullable=True),
        sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("tender_requirements.id"), nullable=True),
        sa.Column("risk_id", sa.Integer(), sa.ForeignKey("tender_risks.id"), nullable=True),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_subtype", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("normalized_value", sa.String(length=255), nullable=True),
        sa.Column("normalized_json", _large_text(), nullable=True),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_location", sa.String(length=255), nullable=True),
        sa.Column("original_text", _large_text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("evidence_json", _large_text(), nullable=True),
        sa.Column("related_requirement_ids_json", _large_text(), nullable=True),
        sa.Column("related_risk_ids_json", _large_text(), nullable=True),
        sa.Column("document_section", sa.String(length=64), nullable=True),
        sa.Column("owner_role", sa.String(length=64), nullable=True),
        sa.Column("response_required", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("reviewer_note", _large_text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.6"),
        sa.Column("extraction_method", sa.String(length=64), nullable=False, server_default="rule"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("object_uuid", name="uq_tender_business_objects_uuid"),
    )
    op.create_index("ix_tender_business_objects_id", "tender_business_objects", ["id"])
    op.create_index("ix_tender_business_objects_object_uuid", "tender_business_objects", ["object_uuid"], unique=True)
    op.create_index("ix_tender_business_objects_project_id", "tender_business_objects", ["project_id"])
    op.create_index("ix_tender_business_objects_file_id", "tender_business_objects", ["file_id"])
    op.create_index("ix_tender_business_objects_parse_run_id", "tender_business_objects", ["parse_run_id"])
    op.create_index("ix_tender_business_objects_requirement_id", "tender_business_objects", ["requirement_id"])
    op.create_index("ix_tender_business_objects_risk_id", "tender_business_objects", ["risk_id"])
    op.create_index("ix_tender_business_objects_object_type", "tender_business_objects", ["object_type"])
    op.create_index("ix_tender_business_objects_object_subtype", "tender_business_objects", ["object_subtype"])
    op.create_index("ix_tender_business_objects_document_section", "tender_business_objects", ["document_section"])
    op.create_index("ix_tender_business_objects_response_required", "tender_business_objects", ["response_required"])
    op.create_index("ix_tender_business_objects_review_status", "tender_business_objects", ["review_status"])
    op.create_index("ix_tender_business_objects_reviewed_by", "tender_business_objects", ["reviewed_by"])
    op.create_index("ix_tender_business_objects_status", "tender_business_objects", ["status"])
    op.create_index("ix_tender_business_objects_project_type", "tender_business_objects", ["project_id", "object_type"])
    op.create_index("ix_tender_business_objects_run_type", "tender_business_objects", ["parse_run_id", "object_type"])
    op.create_index("ix_tender_business_objects_run_review", "tender_business_objects", ["parse_run_id", "review_status"])
    op.create_index("ix_tender_business_objects_run_response", "tender_business_objects", ["parse_run_id", "response_required"])


def downgrade() -> None:
    if "tender_business_objects" not in _tables():
        return
    for index_name in (
        "ix_tender_business_objects_run_response",
        "ix_tender_business_objects_run_review",
        "ix_tender_business_objects_run_type",
        "ix_tender_business_objects_project_type",
        "ix_tender_business_objects_status",
        "ix_tender_business_objects_reviewed_by",
        "ix_tender_business_objects_review_status",
        "ix_tender_business_objects_response_required",
        "ix_tender_business_objects_document_section",
        "ix_tender_business_objects_object_subtype",
        "ix_tender_business_objects_object_type",
        "ix_tender_business_objects_risk_id",
        "ix_tender_business_objects_requirement_id",
        "ix_tender_business_objects_parse_run_id",
        "ix_tender_business_objects_file_id",
        "ix_tender_business_objects_project_id",
        "ix_tender_business_objects_object_uuid",
        "ix_tender_business_objects_id",
    ):
        _drop_index_if_exists(index_name, "tender_business_objects")
    op.drop_table("tender_business_objects")
