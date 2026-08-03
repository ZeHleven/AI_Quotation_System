"""add project purchase cost import mvp tables

Revision ID: 20260713_0047
Revises: 20260713_0046
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260713_0047"
down_revision: Union[str, None] = "20260713_0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _large_text():
    return mysql.LONGTEXT() if op.get_bind().dialect.name == "mysql" else sa.Text()


def upgrade() -> None:
    if "project_cost_import_batches" not in _tables():
        op.create_table(
            "project_cost_import_batches",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("batch_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_name", sa.String(length=255), nullable=False),
            sa.Column("source_name", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=24), server_default="reviewing", nullable=False),
            sa.Column("parser_version", sa.String(length=64), nullable=False),
            sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("parsed_file_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("skipped_file_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("high_confidence_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("approved_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("rejected_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("source_manifest_json", _large_text(), nullable=True),
            sa.Column("summary_json", _large_text(), nullable=True),
            sa.Column("target_quota_version_id", sa.Integer(), sa.ForeignKey("enterprise_quota_versions.id"), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("batch_uuid", name="uq_project_cost_import_batches_uuid"),
        )
        op.create_index("ix_project_cost_import_batches_batch_uuid", "project_cost_import_batches", ["batch_uuid"], unique=True)
        op.create_index("ix_project_cost_import_batches_project_name", "project_cost_import_batches", ["project_name"])
        op.create_index("ix_project_cost_import_batches_status", "project_cost_import_batches", ["status"])
        op.create_index("ix_project_cost_import_batches_target_quota_version_id", "project_cost_import_batches", ["target_quota_version_id"])
        op.create_index("ix_project_cost_import_batches_created_by", "project_cost_import_batches", ["created_by"])
        op.create_index("ix_project_cost_import_batches_status_created", "project_cost_import_batches", ["status", "created_at"])

    if "enterprise_resource_price_observations" not in _tables():
        op.create_table(
            "enterprise_resource_price_observations",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("project_cost_import_batches.id"), nullable=False),
            sa.Column("observation_type", sa.String(length=32), server_default="order", nullable=False),
            sa.Column("source_file_name", sa.String(length=500), nullable=False),
            sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
            sa.Column("source_sheet", sa.String(length=128), nullable=True),
            sa.Column("source_row_index", sa.Integer(), nullable=True),
            sa.Column("supplier_name", sa.String(length=255), nullable=True),
            sa.Column("order_no", sa.String(length=128), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("raw_item_name", sa.String(length=255), nullable=False),
            sa.Column("normalized_item_name", sa.String(length=255), nullable=False),
            sa.Column("brand", sa.String(length=255), nullable=True),
            sa.Column("spec", sa.String(length=500), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit_price", sa.Float(), nullable=True),
            sa.Column("amount", sa.Float(), nullable=True),
            sa.Column("tax_included", sa.Boolean(), nullable=True),
            sa.Column("tax_rate", sa.Float(), nullable=True),
            sa.Column("freight_included", sa.Boolean(), nullable=True),
            sa.Column("is_return", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("excluded_reason", sa.String(length=64), nullable=True),
            sa.Column("candidate_key", sa.String(length=64), nullable=True),
            sa.Column("raw_json", _large_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_enterprise_resource_price_observations_batch_id", "enterprise_resource_price_observations", ["batch_id"])
        op.create_index("ix_enterprise_resource_price_observations_source_file_sha256", "enterprise_resource_price_observations", ["source_file_sha256"])
        op.create_index("ix_enterprise_resource_price_observations_normalized_item_name", "enterprise_resource_price_observations", ["normalized_item_name"])
        op.create_index("ix_enterprise_resource_price_observations_unit_price", "enterprise_resource_price_observations", ["unit_price"])
        op.create_index("ix_resource_price_observations_batch_source", "enterprise_resource_price_observations", ["batch_id", "observation_type"])
        op.create_index("ix_resource_price_observations_candidate_key", "enterprise_resource_price_observations", ["batch_id", "candidate_key"])
        op.create_index("ix_resource_price_observations_name_unit", "enterprise_resource_price_observations", ["normalized_item_name", "unit"])

    if "project_cost_price_candidates" not in _tables():
        op.create_table(
            "project_cost_price_candidates",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("project_cost_import_batches.id"), nullable=False),
            sa.Column("candidate_key", sa.String(length=64), nullable=False),
            sa.Column("normalized_item_name", sa.String(length=255), nullable=False),
            sa.Column("brand", sa.String(length=255), nullable=True),
            sa.Column("spec", sa.String(length=500), nullable=True),
            sa.Column("unit", sa.String(length=64), nullable=True),
            sa.Column("resource_type", sa.String(length=32), server_default="main_material", nullable=False),
            sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("supplier_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("min_price", sa.Float(), nullable=True),
            sa.Column("median_price", sa.Float(), nullable=True),
            sa.Column("max_price", sa.Float(), nullable=True),
            sa.Column("recommended_price", sa.Float(), nullable=True),
            sa.Column("volatility_rate", sa.Float(), nullable=True),
            sa.Column("confidence_score", sa.Float(), server_default="0", nullable=False),
            sa.Column("risk_level", sa.String(length=16), server_default="medium", nullable=False),
            sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
            sa.Column("matched_resource_id", sa.Integer(), sa.ForeignKey("enterprise_cost_resources.id"), nullable=True),
            sa.Column("match_type", sa.String(length=32), nullable=True),
            sa.Column("match_confidence", sa.Float(), nullable=True),
            sa.Column("review_note", sa.String(length=2000), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_resource_id", sa.Integer(), sa.ForeignKey("enterprise_cost_resources.id"), nullable=True),
            sa.Column("evidence_json", _large_text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("batch_id", "candidate_key", name="uq_project_cost_price_candidates_batch_key"),
        )
        op.create_index("ix_project_cost_price_candidates_batch_id", "project_cost_price_candidates", ["batch_id"])
        op.create_index("ix_project_cost_price_candidates_candidate_key", "project_cost_price_candidates", ["candidate_key"])
        op.create_index("ix_project_cost_price_candidates_normalized_item_name", "project_cost_price_candidates", ["normalized_item_name"])
        op.create_index("ix_project_cost_price_candidates_status", "project_cost_price_candidates", ["status"])
        op.create_index("ix_project_cost_price_candidates_matched_resource_id", "project_cost_price_candidates", ["matched_resource_id"])
        op.create_index("ix_project_cost_price_candidates_draft_resource_id", "project_cost_price_candidates", ["draft_resource_id"])
        op.create_index("ix_project_cost_price_candidates_batch_status", "project_cost_price_candidates", ["batch_id", "status"])
        op.create_index("ix_project_cost_price_candidates_risk_confidence", "project_cost_price_candidates", ["risk_level", "confidence_score"])


def downgrade() -> None:
    for table_name in (
        "project_cost_price_candidates",
        "enterprise_resource_price_observations",
        "project_cost_import_batches",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
