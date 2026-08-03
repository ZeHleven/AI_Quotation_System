"""add bidding mvp tables

Revision ID: 20260629_0037
Revises: 20260626_0036
Create Date: 2026-06-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260629_0037"
down_revision: Union[str, None] = "20260626_0036"
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
    tables = _tables()
    if "bid_projects" not in tables:
        op.create_table(
            "bid_projects",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("project_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_name", sa.String(length=255), nullable=False),
            sa.Column("tenderer_name", sa.String(length=255), nullable=True),
            sa.Column("tender_agency", sa.String(length=255), nullable=True),
            sa.Column("project_location", sa.String(length=255), nullable=True),
            sa.Column("project_type", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
            sa.Column("tender_deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("inquiry_deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("bid_open_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("summary_json", _large_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_uuid", name="uq_bid_projects_project_uuid"),
        )
        op.create_index("ix_bid_projects_id", "bid_projects", ["id"])
        op.create_index("ix_bid_projects_project_uuid", "bid_projects", ["project_uuid"], unique=True)
        op.create_index("ix_bid_projects_project_name", "bid_projects", ["project_name"])
        op.create_index("ix_bid_projects_tenderer_name", "bid_projects", ["tenderer_name"])
        op.create_index("ix_bid_projects_project_type", "bid_projects", ["project_type"])
        op.create_index("ix_bid_projects_status", "bid_projects", ["status"])
        op.create_index("ix_bid_projects_tender_deadline_at", "bid_projects", ["tender_deadline_at"])
        op.create_index("ix_bid_projects_owner_user_id", "bid_projects", ["owner_user_id"])
        op.create_index("ix_bid_projects_created_by", "bid_projects", ["created_by"])
        op.create_index("ix_bid_projects_status_updated", "bid_projects", ["status", "updated_at"])
        op.create_index("ix_bid_projects_owner_status", "bid_projects", ["owner_user_id", "status"])
        op.create_index("ix_bid_projects_created_by_status", "bid_projects", ["created_by", "status"])

    tables = _tables()
    if "bid_project_files" not in tables:
        op.create_table(
            "bid_project_files",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("file_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
            sa.Column("file_type", sa.String(length=64), nullable=False, server_default="tender_document"),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("parser_status", sa.String(length=32), nullable=False, server_default="parsed"),
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column("extracted_text", _large_text(), nullable=True),
            sa.Column("segments_json", _large_text(), nullable=True),
            sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("section_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", _large_text(), nullable=True),
            sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("file_uuid", name="uq_bid_project_files_file_uuid"),
        )
        op.create_index("ix_bid_project_files_id", "bid_project_files", ["id"])
        op.create_index("ix_bid_project_files_file_uuid", "bid_project_files", ["file_uuid"], unique=True)
        op.create_index("ix_bid_project_files_project_id", "bid_project_files", ["project_id"])
        op.create_index("ix_bid_project_files_file_type", "bid_project_files", ["file_type"])
        op.create_index("ix_bid_project_files_sha256", "bid_project_files", ["sha256"])
        op.create_index("ix_bid_project_files_parser_status", "bid_project_files", ["parser_status"])
        op.create_index("ix_bid_project_files_uploaded_by", "bid_project_files", ["uploaded_by"])
        op.create_index("ix_bid_project_files_project_type", "bid_project_files", ["project_id", "file_type"])
        op.create_index("ix_bid_project_files_project_created", "bid_project_files", ["project_id", "created_at"])

    tables = _tables()
    if "bid_parse_runs" not in tables:
        op.create_table(
            "bid_parse_runs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("run_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column("input_file_ids_json", _large_text(), nullable=True),
            sa.Column("summary_json", _large_text(), nullable=True),
            sa.Column("error_message", _large_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("run_uuid", name="uq_bid_parse_runs_run_uuid"),
        )
        op.create_index("ix_bid_parse_runs_id", "bid_parse_runs", ["id"])
        op.create_index("ix_bid_parse_runs_run_uuid", "bid_parse_runs", ["run_uuid"], unique=True)
        op.create_index("ix_bid_parse_runs_project_id", "bid_parse_runs", ["project_id"])
        op.create_index("ix_bid_parse_runs_status", "bid_parse_runs", ["status"])
        op.create_index("ix_bid_parse_runs_created_by", "bid_parse_runs", ["created_by"])
        op.create_index("ix_bid_parse_runs_project_created", "bid_parse_runs", ["project_id", "created_at"])
        op.create_index("ix_bid_parse_runs_project_status", "bid_parse_runs", ["project_id", "status"])

    tables = _tables()
    if "tender_requirements" not in tables:
        op.create_table(
            "tender_requirements",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("requirement_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
            sa.Column("file_id", sa.Integer(), sa.ForeignKey("bid_project_files.id"), nullable=True),
            sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
            sa.Column("requirement_type", sa.String(length=64), nullable=False),
            sa.Column("source_file", sa.String(length=255), nullable=True),
            sa.Column("source_location", sa.String(length=255), nullable=True),
            sa.Column("original_text", _large_text(), nullable=False),
            sa.Column("parsed_requirement", _large_text(), nullable=False),
            sa.Column("compliance_status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low"),
            sa.Column("owner_role", sa.String(length=64), nullable=True),
            sa.Column("output_section", sa.String(length=128), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.6"),
            sa.Column("extraction_method", sa.String(length=64), nullable=False, server_default="rule"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("reviewer_note", _large_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("requirement_uuid", name="uq_tender_requirements_requirement_uuid"),
        )
        op.create_index("ix_tender_requirements_id", "tender_requirements", ["id"])
        op.create_index("ix_tender_requirements_requirement_uuid", "tender_requirements", ["requirement_uuid"], unique=True)
        op.create_index("ix_tender_requirements_project_id", "tender_requirements", ["project_id"])
        op.create_index("ix_tender_requirements_file_id", "tender_requirements", ["file_id"])
        op.create_index("ix_tender_requirements_parse_run_id", "tender_requirements", ["parse_run_id"])
        op.create_index("ix_tender_requirements_requirement_type", "tender_requirements", ["requirement_type"])
        op.create_index("ix_tender_requirements_compliance_status", "tender_requirements", ["compliance_status"])
        op.create_index("ix_tender_requirements_risk_level", "tender_requirements", ["risk_level"])
        op.create_index("ix_tender_requirements_status", "tender_requirements", ["status"])
        op.create_index("ix_tender_requirements_project_type", "tender_requirements", ["project_id", "requirement_type"])
        op.create_index("ix_tender_requirements_run_type", "tender_requirements", ["parse_run_id", "requirement_type"])

    tables = _tables()
    if "tender_risks" not in tables:
        op.create_table(
            "tender_risks",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("risk_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("bid_projects.id"), nullable=False),
            sa.Column("file_id", sa.Integer(), sa.ForeignKey("bid_project_files.id"), nullable=True),
            sa.Column("parse_run_id", sa.Integer(), sa.ForeignKey("bid_parse_runs.id"), nullable=False),
            sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("tender_requirements.id"), nullable=True),
            sa.Column("risk_type", sa.String(length=64), nullable=False),
            sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="medium"),
            sa.Column("source_file", sa.String(length=255), nullable=True),
            sa.Column("source_location", sa.String(length=255), nullable=True),
            sa.Column("original_text", _large_text(), nullable=False),
            sa.Column("risk_explanation", _large_text(), nullable=False),
            sa.Column("impact_area", sa.String(length=128), nullable=True),
            sa.Column("suggested_action", _large_text(), nullable=True),
            sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("review_status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("reviewer_note", _large_text(), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.6"),
            sa.Column("extraction_method", sa.String(length=64), nullable=False, server_default="rule"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("risk_uuid", name="uq_tender_risks_risk_uuid"),
        )
        op.create_index("ix_tender_risks_id", "tender_risks", ["id"])
        op.create_index("ix_tender_risks_risk_uuid", "tender_risks", ["risk_uuid"], unique=True)
        op.create_index("ix_tender_risks_project_id", "tender_risks", ["project_id"])
        op.create_index("ix_tender_risks_file_id", "tender_risks", ["file_id"])
        op.create_index("ix_tender_risks_parse_run_id", "tender_risks", ["parse_run_id"])
        op.create_index("ix_tender_risks_requirement_id", "tender_risks", ["requirement_id"])
        op.create_index("ix_tender_risks_risk_type", "tender_risks", ["risk_type"])
        op.create_index("ix_tender_risks_risk_level", "tender_risks", ["risk_level"])
        op.create_index("ix_tender_risks_is_blocking", "tender_risks", ["is_blocking"])
        op.create_index("ix_tender_risks_reviewed_by", "tender_risks", ["reviewed_by"])
        op.create_index("ix_tender_risks_review_status", "tender_risks", ["review_status"])
        op.create_index("ix_tender_risks_project_level", "tender_risks", ["project_id", "risk_level"])
        op.create_index("ix_tender_risks_run_type", "tender_risks", ["parse_run_id", "risk_type"])


def downgrade() -> None:
    tables = _tables()
    if "tender_risks" in tables:
        for index_name in (
            "ix_tender_risks_run_type",
            "ix_tender_risks_project_level",
            "ix_tender_risks_review_status",
            "ix_tender_risks_reviewed_by",
            "ix_tender_risks_is_blocking",
            "ix_tender_risks_risk_level",
            "ix_tender_risks_risk_type",
            "ix_tender_risks_requirement_id",
            "ix_tender_risks_parse_run_id",
            "ix_tender_risks_file_id",
            "ix_tender_risks_project_id",
            "ix_tender_risks_risk_uuid",
            "ix_tender_risks_id",
        ):
            _drop_index_if_exists(index_name, "tender_risks")
        op.drop_table("tender_risks")

    tables = _tables()
    if "tender_requirements" in tables:
        for index_name in (
            "ix_tender_requirements_run_type",
            "ix_tender_requirements_project_type",
            "ix_tender_requirements_status",
            "ix_tender_requirements_risk_level",
            "ix_tender_requirements_compliance_status",
            "ix_tender_requirements_requirement_type",
            "ix_tender_requirements_parse_run_id",
            "ix_tender_requirements_file_id",
            "ix_tender_requirements_project_id",
            "ix_tender_requirements_requirement_uuid",
            "ix_tender_requirements_id",
        ):
            _drop_index_if_exists(index_name, "tender_requirements")
        op.drop_table("tender_requirements")

    tables = _tables()
    if "bid_parse_runs" in tables:
        for index_name in (
            "ix_bid_parse_runs_project_status",
            "ix_bid_parse_runs_project_created",
            "ix_bid_parse_runs_created_by",
            "ix_bid_parse_runs_status",
            "ix_bid_parse_runs_project_id",
            "ix_bid_parse_runs_run_uuid",
            "ix_bid_parse_runs_id",
        ):
            _drop_index_if_exists(index_name, "bid_parse_runs")
        op.drop_table("bid_parse_runs")

    tables = _tables()
    if "bid_project_files" in tables:
        for index_name in (
            "ix_bid_project_files_project_created",
            "ix_bid_project_files_project_type",
            "ix_bid_project_files_uploaded_by",
            "ix_bid_project_files_parser_status",
            "ix_bid_project_files_sha256",
            "ix_bid_project_files_file_type",
            "ix_bid_project_files_project_id",
            "ix_bid_project_files_file_uuid",
            "ix_bid_project_files_id",
        ):
            _drop_index_if_exists(index_name, "bid_project_files")
        op.drop_table("bid_project_files")

    tables = _tables()
    if "bid_projects" in tables:
        for index_name in (
            "ix_bid_projects_created_by_status",
            "ix_bid_projects_owner_status",
            "ix_bid_projects_status_updated",
            "ix_bid_projects_created_by",
            "ix_bid_projects_owner_user_id",
            "ix_bid_projects_tender_deadline_at",
            "ix_bid_projects_status",
            "ix_bid_projects_project_type",
            "ix_bid_projects_tenderer_name",
            "ix_bid_projects_project_name",
            "ix_bid_projects_project_uuid",
            "ix_bid_projects_id",
        ):
            _drop_index_if_exists(index_name, "bid_projects")
        op.drop_table("bid_projects")
