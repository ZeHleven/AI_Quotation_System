"""add budget project workspace and immutable import batches

Revision ID: 20260714_0049
Revises: 20260713_0048
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260714_0049"
down_revision: Union[str, None] = "20260713_0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext() -> sa.types.TypeEngine:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    op.create_table(
        "budget_project_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workspace_status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.Integer(), nullable=True),
        sa.Column("archive_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["archived_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_budget_project_profiles_project"),
    )
    op.create_index("ix_budget_project_profiles_project_id", "budget_project_profiles", ["project_id"])
    op.create_index("ix_budget_project_profiles_workspace_status", "budget_project_profiles", ["workspace_status"])
    op.create_index("ix_budget_project_profiles_created_by", "budget_project_profiles", ["created_by"])
    op.create_index(
        "ix_budget_profiles_status_created",
        "budget_project_profiles",
        ["workspace_status", "created_at"],
    )

    op.create_table(
        "budget_project_import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_uuid", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_file_object_id", sa.Integer(), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_file_size", sa.Integer(), nullable=False),
        sa.Column("source_storage_mode", sa.String(length=32), server_default="metadata_only", nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="parsed", nullable=False),
        sa.Column("remap_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sheet_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_output_row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("standard_item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_quantity_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_quantity_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("original_preview_json", _longtext(), nullable=False),
        sa.Column("current_preview_json", _longtext(), nullable=False),
        sa.Column("issues_json", _longtext(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_file_object_id"], ["file_objects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_uuid", name="uq_budget_import_batches_uuid"),
    )
    op.create_index("ix_budget_project_import_batches_project_id", "budget_project_import_batches", ["project_id"])
    op.create_index(
        "ix_budget_project_import_batches_source_file_object_id",
        "budget_project_import_batches",
        ["source_file_object_id"],
    )
    op.create_index(
        "ix_budget_project_import_batches_source_file_sha256",
        "budget_project_import_batches",
        ["source_file_sha256"],
    )
    op.create_index("ix_budget_project_import_batches_status", "budget_project_import_batches", ["status"])
    op.create_index("ix_budget_project_import_batches_created_by", "budget_project_import_batches", ["created_by"])
    op.create_index(
        "ix_budget_import_project_created",
        "budget_project_import_batches",
        ["project_id", "created_at"],
    )

    op.create_table(
        "budget_project_import_sheet_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("header_row_index", sa.Integer(), nullable=True),
        sa.Column("detected_field_mapping_json", _longtext(), nullable=False),
        sa.Column("applied_field_mapping_json", _longtext(), nullable=False),
        sa.Column("detected_columns_json", _longtext(), nullable=False),
        sa.Column("current_columns_json", _longtext(), nullable=False),
        sa.Column("mapping_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["budget_project_import_batches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "sheet_name", name="uq_budget_import_sheet_batch_name"),
    )
    op.create_index(
        "ix_budget_project_import_sheet_mappings_batch_id",
        "budget_project_import_sheet_mappings",
        ["batch_id"],
    )
    op.create_index(
        "ix_budget_import_sheet_batch_revision",
        "budget_project_import_sheet_mappings",
        ["batch_id", "mapping_revision"],
    )

    op.create_table(
        "budget_project_standard_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("row_key", sa.String(length=255), nullable=False),
        sa.Column("source_sheet", sa.String(length=255), nullable=False),
        sa.Column("raw_row_index", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mapping_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("row_type", sa.String(length=32), nullable=False),
        sa.Column("is_standard_item", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("spec", _longtext(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("remark", _longtext(), nullable=True),
        sa.Column("raw_quantity", _longtext(), nullable=True),
        sa.Column("parser_quantity", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("calculation_quantity", sa.Numeric(precision=20, scale=6), server_default="0", nullable=False),
        sa.Column("quantity_status", sa.String(length=32), server_default="not_applicable", nullable=False),
        sa.Column("quantity_source_json", _longtext(), nullable=True),
        sa.Column("quantity_candidates_json", _longtext(), nullable=True),
        sa.Column("field_mapping_json", _longtext(), nullable=True),
        sa.Column("raw_text", _longtext(), nullable=True),
        sa.Column("raw_fields_json", _longtext(), nullable=True),
        sa.Column("raw_cells_json", _longtext(), nullable=True),
        sa.Column("warnings_json", _longtext(), nullable=True),
        sa.Column("confidence", sa.String(length=24), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("standard_row_json", _longtext(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["budget_project_import_batches.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "row_key", name="uq_budget_standard_rows_batch_key"),
    )
    op.create_index("ix_budget_project_standard_rows_batch_id", "budget_project_standard_rows", ["batch_id"])
    op.create_index("ix_budget_project_standard_rows_source_sheet", "budget_project_standard_rows", ["source_sheet"])
    op.create_index("ix_budget_project_standard_rows_row_type", "budget_project_standard_rows", ["row_type"])
    op.create_index(
        "ix_budget_project_standard_rows_is_standard_item",
        "budget_project_standard_rows",
        ["is_standard_item"],
    )
    op.create_index("ix_budget_project_standard_rows_item_name", "budget_project_standard_rows", ["item_name"])
    op.create_index(
        "ix_budget_project_standard_rows_quantity_status",
        "budget_project_standard_rows",
        ["quantity_status"],
    )
    op.create_index(
        "ix_budget_rows_batch_order", "budget_project_standard_rows", ["batch_id", "sort_order"]
    )
    op.create_index(
        "ix_budget_rows_batch_type_quantity",
        "budget_project_standard_rows",
        ["batch_id", "row_type", "quantity_status"],
    )


def downgrade() -> None:
    op.drop_table("budget_project_standard_rows")
    op.drop_table("budget_project_import_sheet_mappings")
    op.drop_table("budget_project_import_batches")
    op.drop_table("budget_project_profiles")
