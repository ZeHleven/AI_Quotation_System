"""add isolated pricing agent v1 archive and run tables

Revision ID: 20260730_0075
Revises: 20260729_0074
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260730_0075"
down_revision: Union[str, None] = "20260729_0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _long_text():
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    if "pricing_archive_files" not in _tables():
        op.create_table(
            "pricing_archive_files",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("archive_uuid", sa.String(length=36), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("file_sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("storage_backend", sa.String(length=24), nullable=False),
            sa.Column("storage_bucket", sa.String(length=128), nullable=True),
            sa.Column("storage_object_name", sa.String(length=512), nullable=False),
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"),
            sa.Column("indexed_row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rejected_row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary_json", _long_text(), nullable=True),
            sa.Column("issues_json", _long_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("archive_uuid", name="uq_pricing_archive_files_uuid"),
            sa.UniqueConstraint("account_id", "file_sha256", name="uq_pricing_archive_files_account_sha256"),
        )
        op.create_index("ix_pricing_archive_files_id", "pricing_archive_files", ["id"])
        op.create_index("ix_pricing_archive_files_archive_uuid", "pricing_archive_files", ["archive_uuid"])
        op.create_index("ix_pricing_archive_files_account_id", "pricing_archive_files", ["account_id"])
        op.create_index("ix_pricing_archive_files_file_sha256", "pricing_archive_files", ["file_sha256"])
        op.create_index("ix_pricing_archive_files_created_by", "pricing_archive_files", ["created_by"])
        op.create_index("ix_pricing_archive_files_account_status", "pricing_archive_files", ["account_id", "status"])
        op.create_index("ix_pricing_archive_files_account_created", "pricing_archive_files", ["account_id", "created_at"])

    if "pricing_archive_lines" not in _tables():
        op.create_table(
            "pricing_archive_lines",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("line_uuid", sa.String(length=36), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
            sa.Column(
                "archive_file_id",
                sa.Integer(),
                sa.ForeignKey("pricing_archive_files.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_sheet", sa.String(length=128), nullable=False),
            sa.Column("source_row_index", sa.Integer(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("item_code", sa.String(length=128), nullable=True),
            sa.Column("item_name", sa.String(length=500), nullable=False),
            sa.Column("specification", sa.String(length=1000), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Numeric(20, 6), nullable=True),
            sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
            sa.Column("total_price", sa.Numeric(24, 6), nullable=True),
            sa.Column("normalized_code", sa.String(length=128), nullable=True),
            sa.Column("normalized_name", sa.String(length=500), nullable=False),
            sa.Column("normalized_spec", sa.String(length=1000), nullable=True),
            sa.Column("normalized_unit", sa.String(length=64), nullable=True),
            sa.Column("searchable", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("price_derivation", sa.String(length=32), nullable=False, server_default="source_unit_price"),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("raw_text", _long_text(), nullable=True),
            sa.Column("raw_row_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("line_uuid", name="uq_pricing_archive_lines_uuid"),
            sa.UniqueConstraint(
                "archive_file_id",
                "source_sheet",
                "source_row_index",
                name="uq_pricing_archive_lines_file_sheet_row",
            ),
        )
        op.create_index("ix_pricing_archive_lines_id", "pricing_archive_lines", ["id"])
        op.create_index("ix_pricing_archive_lines_line_uuid", "pricing_archive_lines", ["line_uuid"])
        op.create_index("ix_pricing_archive_lines_account_id", "pricing_archive_lines", ["account_id"])
        op.create_index("ix_pricing_archive_lines_archive_file_id", "pricing_archive_lines", ["archive_file_id"])
        op.create_index("ix_pricing_archive_lines_normalized_code", "pricing_archive_lines", ["normalized_code"])
        op.create_index("ix_pricing_archive_lines_normalized_name", "pricing_archive_lines", ["normalized_name"])
        op.create_index("ix_pricing_archive_lines_normalized_unit", "pricing_archive_lines", ["normalized_unit"])
        op.create_index("ix_pricing_archive_lines_searchable", "pricing_archive_lines", ["searchable"])
        op.create_index("ix_pricing_archive_lines_fingerprint", "pricing_archive_lines", ["fingerprint"])
        op.create_index(
            "ix_pricing_archive_lines_account_name_unit",
            "pricing_archive_lines",
            ["account_id", "normalized_name", "normalized_unit"],
        )
        op.create_index(
            "ix_pricing_archive_lines_account_code",
            "pricing_archive_lines",
            ["account_id", "normalized_code"],
        )
        op.create_index(
            "ix_pricing_archive_lines_file_order",
            "pricing_archive_lines",
            ["archive_file_id", "sort_order"],
        )

    if "pricing_agent_runs" not in _tables():
        op.create_table(
            "pricing_agent_runs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("run_uuid", sa.String(length=36), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("mode", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="processing"),
            sa.Column("sources_json", _long_text(), nullable=False),
            sa.Column("context_json", _long_text(), nullable=False),
            sa.Column("request_json", _long_text(), nullable=False),
            sa.Column("summary_json", _long_text(), nullable=True),
            sa.Column("result_json", _long_text(), nullable=True),
            sa.Column("error_json", _long_text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("run_uuid", name="uq_pricing_agent_runs_uuid"),
        )
        op.create_index("ix_pricing_agent_runs_id", "pricing_agent_runs", ["id"])
        op.create_index("ix_pricing_agent_runs_run_uuid", "pricing_agent_runs", ["run_uuid"])
        op.create_index("ix_pricing_agent_runs_account_id", "pricing_agent_runs", ["account_id"])
        op.create_index("ix_pricing_agent_runs_created_by", "pricing_agent_runs", ["created_by"])
        op.create_index("ix_pricing_agent_runs_account_created", "pricing_agent_runs", ["account_id", "created_at"])
        op.create_index("ix_pricing_agent_runs_account_status", "pricing_agent_runs", ["account_id", "status"])

    if "pricing_agent_run_lines" not in _tables():
        op.create_table(
            "pricing_agent_run_lines",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("line_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("pricing_agent_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("row_key", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("item_code", sa.String(length=128), nullable=True),
            sa.Column("item_name", sa.String(length=500), nullable=False),
            sa.Column("specification", sa.String(length=1000), nullable=True),
            sa.Column("quantity", sa.Numeric(20, 6), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("selected_source", sa.String(length=32), nullable=True),
            sa.Column("match_type", sa.String(length=32), nullable=True),
            sa.Column("unit_price", sa.Numeric(20, 6), nullable=True),
            sa.Column("total_price", sa.Numeric(24, 6), nullable=True),
            sa.Column("confidence", sa.Numeric(9, 6), nullable=True),
            sa.Column("requires_review", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("evidence_json", _long_text(), nullable=True),
            sa.Column("candidates_json", _long_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("line_uuid", name="uq_pricing_agent_run_lines_uuid"),
            sa.UniqueConstraint("run_id", "row_key", name="uq_pricing_agent_run_lines_run_row_key"),
        )
        op.create_index("ix_pricing_agent_run_lines_id", "pricing_agent_run_lines", ["id"])
        op.create_index("ix_pricing_agent_run_lines_line_uuid", "pricing_agent_run_lines", ["line_uuid"])
        op.create_index("ix_pricing_agent_run_lines_run_id", "pricing_agent_run_lines", ["run_id"])
        op.create_index("ix_pricing_agent_run_lines_selected_source", "pricing_agent_run_lines", ["selected_source"])
        op.create_index("ix_pricing_agent_run_lines_match_type", "pricing_agent_run_lines", ["match_type"])
        op.create_index("ix_pricing_agent_run_lines_run_order", "pricing_agent_run_lines", ["run_id", "sort_order"])
        op.create_index(
            "ix_pricing_agent_run_lines_source_match",
            "pricing_agent_run_lines",
            ["selected_source", "match_type"],
        )


def downgrade() -> None:
    for table_name in (
        "pricing_agent_run_lines",
        "pricing_agent_runs",
        "pricing_archive_lines",
        "pricing_archive_files",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
