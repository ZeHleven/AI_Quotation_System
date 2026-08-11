"""add bid assessment input and scope foundation

Revision ID: 20260810_0083
Revises: 20260808_0082
Create Date: 2026-08-10
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0083"
down_revision: Union[str, None] = "20260808_0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

ASSESSMENT_BUSINESS_STATES = (
    "draft",
    "awaiting_files",
    "preparing",
    "awaiting_lot_selection",
    "preliminary_analyzing",
    "preliminary_ready",
    "awaiting_owner_input",
    "deep_analyzing",
    "validating",
    "deep_ready",
    "stale_input",
    "failed",
    "cancelled",
    "superseded",
)
UPLOAD_BATCH_STATES = (
    "draft",
    "uploading",
    "ready",
    "committing",
    "committed",
    "abandoned",
    "expired",
    "failed",
)
UPLOAD_FILE_STATES = ("receiving", "inspecting", "ready", "rejected", "failed")


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "bid_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("client_name", sa.String(length=300), nullable=False),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column("lifecycle_status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("business_status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("current_manifest_id", sa.String(length=36), nullable=True),
        sa.Column("active_run_id", sa.String(length=36), nullable=True),
        sa.Column("superseded_by_assessment_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'archived')",
            name="ck_bid_assessments_lifecycle_status",
        ),
        sa.CheckConstraint(
            f"business_status IN ({_in_values(ASSESSMENT_BUSINESS_STATES)})",
            name="ck_bid_assessments_business_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_assessments_row_version"),
        sa.ForeignKeyConstraint(
            ["superseded_by_assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_assessments_superseded_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_assessments"),
        sa.UniqueConstraint("external_ref", name="uq_bid_assessments_external_ref"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_assessments_lifecycle_business",
        "bid_assessments",
        ["lifecycle_status", "business_status"],
    )
    op.create_index("ix_bid_assessments_created_by", "bid_assessments", ["created_by"])
    op.create_index("ix_bid_assessments_current_manifest", "bid_assessments", ["current_manifest_id"])
    op.create_index("ix_bid_assessments_active_run", "bid_assessments", ["active_run_id"])

    op.create_table(
        "bid_file_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("storage_status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("storage_etag", sa.String(length=255), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_bid_file_objects_size"),
        sa.CheckConstraint(
            "storage_status IN ('pending', 'available', 'quarantined', 'missing')",
            name="ck_bid_file_objects_storage_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_file_objects_row_version"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_file_objects"),
        sa.UniqueConstraint("sha256", "size_bytes", name="uq_bid_file_objects_content"),
        sa.UniqueConstraint("object_key", name="uq_bid_file_objects_object_key"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_file_objects_sha256", "bid_file_objects", ["sha256"])
    op.create_index("ix_bid_file_objects_storage_status", "bid_file_objects", ["storage_status"])

    op.create_table(
        "bid_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("logical_identity_key", sa.String(length=191), nullable=False),
        sa.Column("logical_name", sa.String(length=500), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bid_documents"),
        sa.UniqueConstraint("logical_identity_key", name="uq_bid_documents_identity"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_documents_type", "bid_documents", ["document_type"])

    op.create_table(
        "bid_document_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("bid_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "file_object_id",
            sa.String(length=36),
            sa.ForeignKey("bid_file_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("parser_hint", sa.String(length=64), nullable=True),
        sa.Column("source_metadata_hash", sa.String(length=64), nullable=False),
        sa.Column("source_metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="ck_bid_document_versions_version"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_document_versions"),
        sa.UniqueConstraint("document_id", "version_no", name="uq_bid_document_versions_number"),
        sa.UniqueConstraint("document_id", "file_object_id", name="uq_bid_document_versions_file"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_document_versions_document", "bid_document_versions", ["document_id"])
    op.create_index("ix_bid_document_versions_file", "bid_document_versions", ["file_object_id"])

    op.create_table(
        "bid_document_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "assessment_id",
            sa.String(length=36),
            sa.ForeignKey("bid_assessments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "committed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_bid_document_manifests_version"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_document_manifests"),
        sa.UniqueConstraint("assessment_id", "id", name="uq_bid_document_manifests_owner_id"),
        sa.UniqueConstraint("assessment_id", "version", name="uq_bid_document_manifests_version"),
        sa.UniqueConstraint("assessment_id", "manifest_hash", name="uq_bid_document_manifests_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_document_manifests_assessment_created",
        "bid_document_manifests",
        ["assessment_id", "created_at"],
    )

    op.create_foreign_key(
        "fk_bid_assessments_current_manifest",
        "bid_assessments",
        "bid_document_manifests",
        ["id", "current_manifest_id"],
        ["assessment_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "bid_manifest_documents",
        sa.Column(
            "manifest_id",
            sa.String(length=36),
            sa.ForeignKey("bid_document_manifests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.String(length=36),
            sa.ForeignKey("bid_document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("order_no >= 0", name="ck_bid_manifest_documents_order"),
        sa.PrimaryKeyConstraint("manifest_id", "document_version_id", name="pk_bid_manifest_documents"),
        sa.UniqueConstraint("manifest_id", "order_no", name="uq_bid_manifest_documents_order"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_manifest_documents_document_version",
        "bid_manifest_documents",
        ["document_version_id"],
    )

    op.create_table(
        "bid_upload_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "assessment_id",
            sa.String(length=36),
            sa.ForeignKey("bid_assessments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("base_manifest_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="draft", nullable=False),
        sa.Column("open_slot_key", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("purpose IN ('initial', 'change')", name="ck_bid_upload_batches_purpose"),
        sa.CheckConstraint(
            f"status IN ({_in_values(UPLOAD_BATCH_STATES)})",
            name="ck_bid_upload_batches_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_upload_batches_row_version"),
        sa.CheckConstraint(
            "((status IN ('draft', 'uploading', 'ready', 'committing') AND open_slot_key = purpose) "
            "OR (status IN ('committed', 'abandoned', 'expired', 'failed') AND open_slot_key IS NULL))",
            name="ck_bid_upload_batches_open_slot",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "base_manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_upload_batches_base_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_upload_batches"),
        sa.UniqueConstraint("assessment_id", "open_slot_key", name="uq_bid_upload_batches_open_slot"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_upload_batches_assessment_status",
        "bid_upload_batches",
        ["assessment_id", "status"],
    )
    op.create_index("ix_bid_upload_batches_expires", "bid_upload_batches", ["expires_at"])

    op.create_table(
        "bid_upload_batch_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("bid_upload_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "file_object_id",
            sa.String(length=36),
            sa.ForeignKey("bid_file_objects.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "replace_document_id",
            sa.String(length=36),
            sa.ForeignKey("bid_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("client_file_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("relative_path", sa.String(length=1000), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("temporary_object_ref", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="receiving", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("operation IN ('add', 'replace')", name="ck_bid_upload_batch_files_operation"),
        sa.CheckConstraint(
            "((operation = 'replace' AND replace_document_id IS NOT NULL) "
            "OR (operation = 'add' AND replace_document_id IS NULL))",
            name="ck_bid_upload_batch_files_replace_target",
        ),
        sa.CheckConstraint("size_bytes >= 1", name="ck_bid_upload_batch_files_size"),
        sa.CheckConstraint(
            f"status IN ({_in_values(UPLOAD_FILE_STATES)})",
            name="ck_bid_upload_batch_files_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_upload_batch_files_row_version"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_upload_batch_files"),
        sa.UniqueConstraint("batch_id", "client_file_id", name="uq_bid_upload_batch_files_client"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_upload_batch_files_batch_status",
        "bid_upload_batch_files",
        ["batch_id", "status"],
    )
    op.create_index("ix_bid_upload_batch_files_sha256", "bid_upload_batch_files", ["sha256"])
    op.create_index("ix_bid_upload_batch_files_object", "bid_upload_batch_files", ["file_object_id"])

    op.create_table(
        "bid_upload_batch_deactivations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "batch_id",
            sa.String(length=36),
            sa.ForeignKey("bid_upload_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("bid_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bid_upload_batch_deactivations"),
        sa.UniqueConstraint(
            "batch_id",
            "document_id",
            name="uq_bid_upload_batch_deactivations_document",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_upload_batch_deactivations_document",
        "bid_upload_batch_deactivations",
        ["document_id"],
    )

    op.create_table(
        "bid_lot_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "manifest_id",
            sa.String(length=36),
            sa.ForeignKey("bid_document_manifests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lot_code", sa.String(length=128), nullable=True),
        sa.Column("lot_name", sa.String(length=500), nullable=False),
        sa.Column("scope_summary", sa.Text(), nullable=True),
        sa.Column("normalized_lot_key", sa.String(length=191), nullable=False),
        sa.Column("source_status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(10, 6), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_bid_lot_candidates_confidence"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_lot_candidates"),
        sa.UniqueConstraint("manifest_id", "normalized_lot_key", name="uq_bid_lot_candidates_key"),
        sa.UniqueConstraint("manifest_id", "candidate_hash", name="uq_bid_lot_candidates_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_lot_candidates_manifest", "bid_lot_candidates", ["manifest_id"])

    op.create_table(
        "bid_assessment_scopes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "assessment_id",
            sa.String(length=36),
            sa.ForeignKey("bid_assessments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=24), server_default="lot", nullable=False),
        sa.Column(
            "source_lot_candidate_id",
            sa.String(length=36),
            sa.ForeignKey("bid_lot_candidates.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("selected_lot_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_bid_assessment_scopes_version"),
        sa.CheckConstraint("scope_type = 'lot'", name="ck_bid_assessment_scopes_type"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_assessment_scopes"),
        sa.UniqueConstraint("assessment_id", "version", name="uq_bid_assessment_scopes_version"),
        sa.UniqueConstraint("assessment_id", "scope_hash", name="uq_bid_assessment_scopes_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_assessment_scopes_assessment_created",
        "bid_assessment_scopes",
        ["assessment_id", "created_at"],
    )
    op.create_index(
        "ix_bid_assessment_scopes_lot_candidate",
        "bid_assessment_scopes",
        ["source_lot_candidate_id"],
    )


def downgrade() -> None:
    op.drop_table("bid_assessment_scopes")
    op.drop_table("bid_lot_candidates")
    op.drop_table("bid_upload_batch_deactivations")
    op.drop_table("bid_upload_batch_files")
    op.drop_table("bid_upload_batches")
    op.drop_table("bid_manifest_documents")
    op.drop_constraint(
        "fk_bid_assessments_current_manifest",
        "bid_assessments",
        type_="foreignkey",
    )
    op.drop_table("bid_document_manifests")
    op.drop_table("bid_document_versions")
    op.drop_table("bid_documents")
    op.drop_table("bid_file_objects")
    op.drop_table("bid_assessments")
