"""Phase 1 persistence models for the isolated bid-assessment data domain.

These tables intentionally do not reuse the legacy ``bid_intake_*`` runtime or
the old bidding module's ``bid_parse_runs`` table.  State changes are expected
to go through the bid-assessment state service once that service is added.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


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


class BidAssessment(Base):
    __tablename__ = "bid_assessments"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived')",
            name="ck_bid_assessments_lifecycle_status",
        ),
        CheckConstraint(
            f"business_status IN ({_in_values(ASSESSMENT_BUSINESS_STATES)})",
            name="ck_bid_assessments_business_status",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_assessments_row_version"),
        ForeignKeyConstraint(
            ["superseded_by_assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_assessments_superseded_by",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "current_manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_assessments_current_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "active_run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_assessments_active_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("external_ref", name="uq_bid_assessments_external_ref"),
        Index("ix_bid_assessments_lifecycle_business", "lifecycle_status", "business_status"),
        Index("ix_bid_assessments_created_by", "created_by"),
        Index("ix_bid_assessments_current_manifest", "current_manifest_id"),
        Index("ix_bid_assessments_active_run", "active_run_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    title = Column(String(300), nullable=False)
    client_name = Column(String(300), nullable=False)
    internal_note = Column(Text, nullable=True)
    external_ref = Column(String(128), nullable=True)
    lifecycle_status = Column(String(24), nullable=False, default="active", server_default="active")
    business_status = Column(String(32), nullable=False, default="draft", server_default="draft")
    current_manifest_id = Column(String(36), nullable=True)
    # The FK is added by the runtime-skeleton revision after bid_analysis_runs exists.
    active_run_id = Column(String(36), nullable=True)
    superseded_by_assessment_id = Column(String(36), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidFileObject(Base):
    __tablename__ = "bid_file_objects"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_bid_file_objects_size"),
        CheckConstraint(
            "storage_status IN ('pending', 'available', 'quarantined', 'missing')",
            name="ck_bid_file_objects_storage_status",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_file_objects_row_version"),
        UniqueConstraint("sha256", "size_bytes", name="uq_bid_file_objects_content"),
        UniqueConstraint("object_key", name="uq_bid_file_objects_object_key"),
        Index("ix_bid_file_objects_sha256", "sha256"),
        Index("ix_bid_file_objects_storage_status", "storage_status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    sha256 = Column(String(64), nullable=False)
    object_key = Column(String(512), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(200), nullable=False)
    storage_status = Column(String(24), nullable=False, default="pending", server_default="pending")
    storage_etag = Column(String(255), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidDocument(Base):
    __tablename__ = "bid_documents"
    __table_args__ = (
        UniqueConstraint("logical_identity_key", name="uq_bid_documents_identity"),
        Index("ix_bid_documents_type", "document_type"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    logical_identity_key = Column(String(191), nullable=False)
    logical_name = Column(String(500), nullable=False)
    document_type = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidDocumentVersion(Base):
    __tablename__ = "bid_document_versions"
    __table_args__ = (
        CheckConstraint("version_no >= 1", name="ck_bid_document_versions_version"),
        UniqueConstraint("document_id", "version_no", name="uq_bid_document_versions_number"),
        UniqueConstraint("document_id", "file_object_id", name="uq_bid_document_versions_file"),
        Index("ix_bid_document_versions_document", "document_id"),
        Index("ix_bid_document_versions_file", "file_object_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("bid_documents.id", ondelete="RESTRICT"), nullable=False)
    file_object_id = Column(
        String(36),
        ForeignKey("bid_file_objects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_no = Column(Integer, nullable=False)
    original_filename = Column(String(500), nullable=False)
    parser_hint = Column(String(64), nullable=True)
    source_metadata_hash = Column(String(64), nullable=False)
    source_metadata_json = Column(JSON, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidDocumentManifest(Base):
    __tablename__ = "bid_document_manifests"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_bid_document_manifests_version"),
        UniqueConstraint("assessment_id", "id", name="uq_bid_document_manifests_owner_id"),
        UniqueConstraint("assessment_id", "version", name="uq_bid_document_manifests_version"),
        UniqueConstraint("assessment_id", "manifest_hash", name="uq_bid_document_manifests_hash"),
        Index("ix_bid_document_manifests_assessment_created", "assessment_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(
        String(36),
        ForeignKey("bid_assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version = Column(Integer, nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    change_note = Column(Text, nullable=True)
    committed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidManifestDocument(Base):
    __tablename__ = "bid_manifest_documents"
    __table_args__ = (
        CheckConstraint("order_no >= 0", name="ck_bid_manifest_documents_order"),
        UniqueConstraint("manifest_id", "order_no", name="uq_bid_manifest_documents_order"),
        Index("ix_bid_manifest_documents_document_version", "document_version_id"),
        TABLE_OPTIONS,
    )

    manifest_id = Column(
        String(36),
        ForeignKey("bid_document_manifests.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    document_version_id = Column(
        String(36),
        ForeignKey("bid_document_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role = Column(String(64), nullable=False)
    order_no = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidUploadBatch(Base):
    __tablename__ = "bid_upload_batches"
    __table_args__ = (
        CheckConstraint("purpose IN ('initial', 'change')", name="ck_bid_upload_batches_purpose"),
        CheckConstraint(
            f"status IN ({_in_values(UPLOAD_BATCH_STATES)})",
            name="ck_bid_upload_batches_status",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_upload_batches_row_version"),
        CheckConstraint(
            "((status IN ('draft', 'uploading', 'ready', 'committing') AND open_slot_key = purpose) "
            "OR (status IN ('committed', 'abandoned', 'expired', 'failed') AND open_slot_key IS NULL))",
            name="ck_bid_upload_batches_open_slot",
        ),
        CheckConstraint(
            "((status = 'committed' AND committed_manifest_id IS NOT NULL "
            "AND committed_at IS NOT NULL) OR "
            "(status <> 'committed' AND committed_manifest_id IS NULL "
            "AND committed_at IS NULL))",
            name="ck_bid_upload_batches_commit_result",
        ),
        CheckConstraint(
            "((status = 'abandoned' AND abandon_reason IS NOT NULL "
            "AND abandoned_at IS NOT NULL AND cleanup_after IS NOT NULL) OR "
            "(status <> 'abandoned' AND abandon_reason IS NULL "
            "AND abandoned_at IS NULL AND cleanup_after IS NULL "
            "AND cleanup_completed_at IS NULL))",
            name="ck_bid_upload_batches_abandonment",
        ),
        CheckConstraint(
            "(cleanup_completed_at IS NULL OR "
            "(abandoned_at IS NOT NULL AND cleanup_completed_at >= abandoned_at))",
            name="ck_bid_upload_batches_cleanup_order",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "base_manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_upload_batches_base_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "committed_manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_upload_batches_committed_manifest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("assessment_id", "open_slot_key", name="uq_bid_upload_batches_open_slot"),
        UniqueConstraint("committed_manifest_id", name="uq_bid_upload_batches_committed_manifest"),
        Index("ix_bid_upload_batches_assessment_status", "assessment_id", "status"),
        Index("ix_bid_upload_batches_expires", "expires_at"),
        Index(
            "ix_bid_upload_batches_cleanup_due",
            "status",
            "cleanup_completed_at",
            "cleanup_after",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(
        String(36),
        ForeignKey("bid_assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_manifest_id = Column(String(36), nullable=True)
    committed_manifest_id = Column(String(36), nullable=True)
    purpose = Column(String(24), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    open_slot_key = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    committed_at = Column(DateTime(timezone=True), nullable=True)
    abandon_reason = Column(String(500), nullable=True)
    abandoned_at = Column(DateTime(timezone=True), nullable=True)
    cleanup_after = Column(DateTime(timezone=True), nullable=True)
    cleanup_completed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidUploadBatchFile(Base):
    __tablename__ = "bid_upload_batch_files"
    __table_args__ = (
        CheckConstraint("operation IN ('add', 'replace')", name="ck_bid_upload_batch_files_operation"),
        CheckConstraint(
            "((operation = 'replace' AND replace_document_id IS NOT NULL) "
            "OR (operation = 'add' AND replace_document_id IS NULL))",
            name="ck_bid_upload_batch_files_replace_target",
        ),
        CheckConstraint("size_bytes >= 1", name="ck_bid_upload_batch_files_size"),
        CheckConstraint(
            f"status IN ({_in_values(UPLOAD_FILE_STATES)})",
            name="ck_bid_upload_batch_files_status",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_upload_batch_files_row_version"),
        UniqueConstraint("batch_id", "client_file_id", name="uq_bid_upload_batch_files_client"),
        Index("ix_bid_upload_batch_files_batch_status", "batch_id", "status"),
        Index("ix_bid_upload_batch_files_sha256", "sha256"),
        Index("ix_bid_upload_batch_files_object", "file_object_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    batch_id = Column(String(36), ForeignKey("bid_upload_batches.id", ondelete="RESTRICT"), nullable=False)
    file_object_id = Column(
        String(36),
        ForeignKey("bid_file_objects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    replace_document_id = Column(
        String(36),
        ForeignKey("bid_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    client_file_id = Column(String(128), nullable=False)
    operation = Column(String(24), nullable=False)
    filename = Column(String(500), nullable=False)
    relative_path = Column(String(1000), nullable=True)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    temporary_object_ref = Column(String(512), nullable=True)
    status = Column(String(24), nullable=False, default="receiving", server_default="receiving")
    error_code = Column(String(100), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidUploadBatchDeactivation(Base):
    __tablename__ = "bid_upload_batch_deactivations"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "document_id",
            name="uq_bid_upload_batch_deactivations_document",
        ),
        Index("ix_bid_upload_batch_deactivations_document", "document_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    batch_id = Column(String(36), ForeignKey("bid_upload_batches.id", ondelete="RESTRICT"), nullable=False)
    document_id = Column(String(36), ForeignKey("bid_documents.id", ondelete="RESTRICT"), nullable=False)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidLotCandidate(Base):
    __tablename__ = "bid_lot_candidates"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_bid_lot_candidates_confidence"),
        UniqueConstraint("manifest_id", "normalized_lot_key", name="uq_bid_lot_candidates_key"),
        UniqueConstraint("manifest_id", "candidate_hash", name="uq_bid_lot_candidates_hash"),
        Index("ix_bid_lot_candidates_manifest", "manifest_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    manifest_id = Column(
        String(36),
        ForeignKey("bid_document_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lot_code = Column(String(128), nullable=True)
    lot_name = Column(String(500), nullable=False)
    scope_summary = Column(Text, nullable=True)
    normalized_lot_key = Column(String(191), nullable=False)
    source_status = Column(String(32), nullable=False)
    confidence = Column(Numeric(10, 6), nullable=False)
    candidate_hash = Column(String(64), nullable=False)
    warnings_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidAssessmentScope(Base):
    __tablename__ = "bid_assessment_scopes"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_bid_assessment_scopes_version"),
        CheckConstraint("scope_type = 'lot'", name="ck_bid_assessment_scopes_type"),
        UniqueConstraint("assessment_id", "id", name="uq_bid_assessment_scopes_owner_id"),
        UniqueConstraint("assessment_id", "version", name="uq_bid_assessment_scopes_version"),
        UniqueConstraint("assessment_id", "scope_hash", name="uq_bid_assessment_scopes_hash"),
        Index("ix_bid_assessment_scopes_assessment_created", "assessment_id", "created_at"),
        Index("ix_bid_assessment_scopes_lot_candidate", "source_lot_candidate_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(
        String(36),
        ForeignKey("bid_assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version = Column(Integer, nullable=False)
    scope_type = Column(String(24), nullable=False, default="lot", server_default="lot")
    source_lot_candidate_id = Column(
        String(36),
        ForeignKey("bid_lot_candidates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    selected_lot_snapshot_json = Column(JSON, nullable=False)
    scope_hash = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
