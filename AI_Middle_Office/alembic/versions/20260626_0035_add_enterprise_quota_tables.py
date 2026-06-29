"""add enterprise quota master data tables

Revision ID: 20260626_0035
Revises: 20260609_0034
Create Date: 2026-06-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260626_0035"
down_revision: Union[str, None] = "20260609_0034"
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


def upgrade() -> None:
    tables = _tables()
    if "cost_import_batches" not in tables:
        op.create_table(
            "cost_import_batches",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("batch_uuid", sa.String(length=36), nullable=False),
            sa.Column("source_filename", sa.String(length=255), nullable=False),
            sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
            sa.Column("source_file_size", sa.Integer(), nullable=True),
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="previewed"),
            sa.Column("summary_json", sa.Text(), nullable=True),
            sa.Column("issues_json", sa.Text(), nullable=True),
            sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("batch_uuid", name="uq_cost_import_batches_batch_uuid"),
        )
        op.create_index("ix_cost_import_batches_id", "cost_import_batches", ["id"])
        op.create_index("ix_cost_import_batches_batch_uuid", "cost_import_batches", ["batch_uuid"], unique=True)
        op.create_index("ix_cost_import_batches_source_file_sha256", "cost_import_batches", ["source_file_sha256"])
        op.create_index("ix_cost_import_batches_status", "cost_import_batches", ["status"])
        op.create_index("ix_cost_import_batches_created_by", "cost_import_batches", ["created_by"])
        op.create_index("ix_cost_import_batches_status_created", "cost_import_batches", ["status", "created_at"])

    tables = _tables()
    if "enterprise_quota_versions" not in tables:
        op.create_table(
            "enterprise_quota_versions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("version_code", sa.String(length=64), nullable=False),
            sa.Column("version_name", sa.String(length=255), nullable=False),
            sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("cost_import_batches.id"), nullable=True),
            sa.Column("source_filename", sa.String(length=255), nullable=True),
            sa.Column("source_file_sha256", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("summary_json", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("activated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("version_code", name="uq_enterprise_quota_versions_version_code"),
        )
        op.create_index("ix_enterprise_quota_versions_id", "enterprise_quota_versions", ["id"])
        op.create_index("ix_enterprise_quota_versions_version_code", "enterprise_quota_versions", ["version_code"], unique=True)
        op.create_index("ix_enterprise_quota_versions_import_batch_id", "enterprise_quota_versions", ["import_batch_id"])
        op.create_index("ix_enterprise_quota_versions_source_file_sha256", "enterprise_quota_versions", ["source_file_sha256"])
        op.create_index("ix_enterprise_quota_versions_status", "enterprise_quota_versions", ["status"])
        op.create_index("ix_enterprise_quota_versions_is_active", "enterprise_quota_versions", ["is_active"])
        op.create_index("ix_enterprise_quota_versions_created_by", "enterprise_quota_versions", ["created_by"])
        op.create_index("ix_enterprise_quota_versions_activated_by", "enterprise_quota_versions", ["activated_by"])
        op.create_index("ix_enterprise_quota_versions_activated_at", "enterprise_quota_versions", ["activated_at"])
        op.create_index("ix_enterprise_quota_versions_archived_by", "enterprise_quota_versions", ["archived_by"])
        op.create_index("ix_enterprise_quota_versions_status_active", "enterprise_quota_versions", ["status", "is_active"])

    tables = _tables()
    if "enterprise_quota_sections" not in tables:
        op.create_table(
            "enterprise_quota_sections",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id"), nullable=False),
            sa.Column("section_code", sa.String(length=64), nullable=True),
            sa.Column("section_name", sa.String(length=255), nullable=True),
            sa.Column("source_sheet", sa.String(length=128), nullable=True),
            sa.Column("source_row_index", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("raw_row_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("version_id", "section_code", name="uq_enterprise_quota_sections_version_code"),
        )
        op.create_index("ix_enterprise_quota_sections_id", "enterprise_quota_sections", ["id"])
        op.create_index("ix_enterprise_quota_sections_version_id", "enterprise_quota_sections", ["version_id"])
        op.create_index("ix_enterprise_quota_sections_section_code", "enterprise_quota_sections", ["section_code"])
        op.create_index("ix_enterprise_quota_sections_section_name", "enterprise_quota_sections", ["section_name"])
        op.create_index("ix_enterprise_quota_sections_source_row_index", "enterprise_quota_sections", ["source_row_index"])
        op.create_index("ix_enterprise_quota_sections_version_order", "enterprise_quota_sections", ["version_id", "sort_order"])

    tables = _tables()
    if "enterprise_quota_items" not in tables:
        op.create_table(
            "enterprise_quota_items",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id"), nullable=False),
            sa.Column("section_id", sa.Integer(), sa.ForeignKey("enterprise_quota_sections.id"), nullable=True),
            sa.Column("quota_code", sa.String(length=64), nullable=True),
            sa.Column("item_name", sa.String(length=255), nullable=True),
            sa.Column("work_content", sa.Text(), nullable=True),
            sa.Column("worker_or_subtype", sa.String(length=128), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit_price", sa.Float(), nullable=True),
            sa.Column("labor_fee", sa.Float(), nullable=True),
            sa.Column("main_material_fee", sa.Float(), nullable=True),
            sa.Column("auxiliary_material_fee", sa.Float(), nullable=True),
            sa.Column("machinery_fee", sa.Float(), nullable=True),
            sa.Column("source_sheet", sa.String(length=128), nullable=True),
            sa.Column("source_row_index", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("raw_row_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("version_id", "quota_code", name="uq_enterprise_quota_items_version_code"),
        )
        op.create_index("ix_enterprise_quota_items_id", "enterprise_quota_items", ["id"])
        op.create_index("ix_enterprise_quota_items_version_id", "enterprise_quota_items", ["version_id"])
        op.create_index("ix_enterprise_quota_items_section_id", "enterprise_quota_items", ["section_id"])
        op.create_index("ix_enterprise_quota_items_quota_code", "enterprise_quota_items", ["quota_code"])
        op.create_index("ix_enterprise_quota_items_item_name", "enterprise_quota_items", ["item_name"])
        op.create_index("ix_enterprise_quota_items_unit", "enterprise_quota_items", ["unit"])
        op.create_index("ix_enterprise_quota_items_unit_price", "enterprise_quota_items", ["unit_price"])
        op.create_index("ix_enterprise_quota_items_source_row_index", "enterprise_quota_items", ["source_row_index"])
        op.create_index("ix_enterprise_quota_items_version_order", "enterprise_quota_items", ["version_id", "sort_order"])
        op.create_index("ix_enterprise_quota_items_section_order", "enterprise_quota_items", ["section_id", "sort_order"])
        op.create_index("ix_enterprise_quota_items_name_unit", "enterprise_quota_items", ["item_name", "unit"])

    tables = _tables()
    if "enterprise_cost_resources" not in tables:
        op.create_table(
            "enterprise_cost_resources",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id"), nullable=False),
            sa.Column("resource_code", sa.String(length=64), nullable=True),
            sa.Column("resource_name", sa.String(length=255), nullable=True),
            sa.Column("resource_type", sa.String(length=32), nullable=False, server_default="unknown"),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("tax_rate", sa.Float(), nullable=True),
            sa.Column("computed_price", sa.Float(), nullable=True),
            sa.Column("price_block_label", sa.String(length=64), nullable=True),
            sa.Column("source_sheet", sa.String(length=128), nullable=True),
            sa.Column("source_row_index", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("raw_row_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_enterprise_cost_resources_id", "enterprise_cost_resources", ["id"])
        op.create_index("ix_enterprise_cost_resources_version_id", "enterprise_cost_resources", ["version_id"])
        op.create_index("ix_enterprise_cost_resources_resource_code", "enterprise_cost_resources", ["resource_code"])
        op.create_index("ix_enterprise_cost_resources_resource_name", "enterprise_cost_resources", ["resource_name"])
        op.create_index("ix_enterprise_cost_resources_resource_type", "enterprise_cost_resources", ["resource_type"])
        op.create_index("ix_enterprise_cost_resources_unit", "enterprise_cost_resources", ["unit"])
        op.create_index("ix_enterprise_cost_resources_price", "enterprise_cost_resources", ["price"])
        op.create_index("ix_enterprise_cost_resources_source_row_index", "enterprise_cost_resources", ["source_row_index"])
        op.create_index("ix_enterprise_cost_resources_version_type", "enterprise_cost_resources", ["version_id", "resource_type"])
        op.create_index("ix_enterprise_cost_resources_code_type", "enterprise_cost_resources", ["resource_code", "resource_type"])
        op.create_index("ix_enterprise_cost_resources_name_unit", "enterprise_cost_resources", ["resource_name", "unit"])
        op.create_index("ix_enterprise_cost_resources_source_row", "enterprise_cost_resources", ["source_sheet", "source_row_index"])

    tables = _tables()
    if "enterprise_quota_components" not in tables:
        op.create_table(
            "enterprise_quota_components",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id"), nullable=False),
            sa.Column("quota_item_id", sa.Integer(), sa.ForeignKey("enterprise_quota_items.id"), nullable=True),
            sa.Column("resource_id", sa.Integer(), sa.ForeignKey("enterprise_cost_resources.id"), nullable=True),
            sa.Column("parent_quota_code", sa.String(length=64), nullable=True),
            sa.Column("component_type", sa.String(length=64), nullable=True),
            sa.Column("resource_code", sa.String(length=64), nullable=True),
            sa.Column("resource_name", sa.String(length=255), nullable=True),
            sa.Column("worker_or_subtype", sa.String(length=128), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit_price", sa.Float(), nullable=True),
            sa.Column("amount", sa.Float(), nullable=True),
            sa.Column("fee_bucket", sa.String(length=32), nullable=True),
            sa.Column("source_sheet", sa.String(length=128), nullable=True),
            sa.Column("source_row_index", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("raw_row_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_enterprise_quota_components_id", "enterprise_quota_components", ["id"])
        op.create_index("ix_enterprise_quota_components_version_id", "enterprise_quota_components", ["version_id"])
        op.create_index("ix_enterprise_quota_components_quota_item_id", "enterprise_quota_components", ["quota_item_id"])
        op.create_index("ix_enterprise_quota_components_resource_id", "enterprise_quota_components", ["resource_id"])
        op.create_index("ix_enterprise_quota_components_parent_quota_code", "enterprise_quota_components", ["parent_quota_code"])
        op.create_index("ix_enterprise_quota_components_component_type", "enterprise_quota_components", ["component_type"])
        op.create_index("ix_enterprise_quota_components_resource_code", "enterprise_quota_components", ["resource_code"])
        op.create_index("ix_enterprise_quota_components_resource_name", "enterprise_quota_components", ["resource_name"])
        op.create_index("ix_enterprise_quota_components_unit", "enterprise_quota_components", ["unit"])
        op.create_index("ix_enterprise_quota_components_amount", "enterprise_quota_components", ["amount"])
        op.create_index("ix_enterprise_quota_components_fee_bucket", "enterprise_quota_components", ["fee_bucket"])
        op.create_index("ix_enterprise_quota_components_source_row_index", "enterprise_quota_components", ["source_row_index"])
        op.create_index("ix_enterprise_quota_components_version_type", "enterprise_quota_components", ["version_id", "component_type"])
        op.create_index("ix_enterprise_quota_components_item_order", "enterprise_quota_components", ["quota_item_id", "sort_order"])
        op.create_index("ix_enterprise_quota_components_parent_code", "enterprise_quota_components", ["version_id", "parent_quota_code"])


def downgrade() -> None:
    tables = _tables()
    if "enterprise_quota_components" in tables:
        for index_name in (
            "ix_enterprise_quota_components_parent_code",
            "ix_enterprise_quota_components_item_order",
            "ix_enterprise_quota_components_version_type",
            "ix_enterprise_quota_components_source_row_index",
            "ix_enterprise_quota_components_fee_bucket",
            "ix_enterprise_quota_components_amount",
            "ix_enterprise_quota_components_unit",
            "ix_enterprise_quota_components_resource_name",
            "ix_enterprise_quota_components_resource_code",
            "ix_enterprise_quota_components_component_type",
            "ix_enterprise_quota_components_parent_quota_code",
            "ix_enterprise_quota_components_resource_id",
            "ix_enterprise_quota_components_quota_item_id",
            "ix_enterprise_quota_components_version_id",
            "ix_enterprise_quota_components_id",
        ):
            _drop_index_if_exists(index_name, "enterprise_quota_components")
        op.drop_table("enterprise_quota_components")

    tables = _tables()
    if "enterprise_cost_resources" in tables:
        for index_name in (
            "ix_enterprise_cost_resources_source_row",
            "ix_enterprise_cost_resources_name_unit",
            "ix_enterprise_cost_resources_code_type",
            "ix_enterprise_cost_resources_version_type",
            "ix_enterprise_cost_resources_source_row_index",
            "ix_enterprise_cost_resources_price",
            "ix_enterprise_cost_resources_unit",
            "ix_enterprise_cost_resources_resource_type",
            "ix_enterprise_cost_resources_resource_name",
            "ix_enterprise_cost_resources_resource_code",
            "ix_enterprise_cost_resources_version_id",
            "ix_enterprise_cost_resources_id",
        ):
            _drop_index_if_exists(index_name, "enterprise_cost_resources")
        op.drop_table("enterprise_cost_resources")

    tables = _tables()
    if "enterprise_quota_items" in tables:
        for index_name in (
            "ix_enterprise_quota_items_name_unit",
            "ix_enterprise_quota_items_section_order",
            "ix_enterprise_quota_items_version_order",
            "ix_enterprise_quota_items_source_row_index",
            "ix_enterprise_quota_items_unit_price",
            "ix_enterprise_quota_items_unit",
            "ix_enterprise_quota_items_item_name",
            "ix_enterprise_quota_items_quota_code",
            "ix_enterprise_quota_items_section_id",
            "ix_enterprise_quota_items_version_id",
            "ix_enterprise_quota_items_id",
        ):
            _drop_index_if_exists(index_name, "enterprise_quota_items")
        op.drop_table("enterprise_quota_items")

    tables = _tables()
    if "enterprise_quota_sections" in tables:
        for index_name in (
            "ix_enterprise_quota_sections_version_order",
            "ix_enterprise_quota_sections_source_row_index",
            "ix_enterprise_quota_sections_section_name",
            "ix_enterprise_quota_sections_section_code",
            "ix_enterprise_quota_sections_version_id",
            "ix_enterprise_quota_sections_id",
        ):
            _drop_index_if_exists(index_name, "enterprise_quota_sections")
        op.drop_table("enterprise_quota_sections")

    tables = _tables()
    if "enterprise_quota_versions" in tables:
        for index_name in (
            "ix_enterprise_quota_versions_status_active",
            "ix_enterprise_quota_versions_archived_by",
            "ix_enterprise_quota_versions_activated_at",
            "ix_enterprise_quota_versions_activated_by",
            "ix_enterprise_quota_versions_created_by",
            "ix_enterprise_quota_versions_is_active",
            "ix_enterprise_quota_versions_status",
            "ix_enterprise_quota_versions_source_file_sha256",
            "ix_enterprise_quota_versions_import_batch_id",
            "ix_enterprise_quota_versions_version_code",
            "ix_enterprise_quota_versions_id",
        ):
            _drop_index_if_exists(index_name, "enterprise_quota_versions")
        op.drop_table("enterprise_quota_versions")

    tables = _tables()
    if "cost_import_batches" in tables:
        for index_name in (
            "ix_cost_import_batches_status_created",
            "ix_cost_import_batches_created_by",
            "ix_cost_import_batches_status",
            "ix_cost_import_batches_source_file_sha256",
            "ix_cost_import_batches_batch_uuid",
            "ix_cost_import_batches_id",
        ):
            _drop_index_if_exists(index_name, "cost_import_batches")
        op.drop_table("cost_import_batches")
