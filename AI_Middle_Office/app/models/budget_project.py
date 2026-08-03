from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship

from app.core.database import Base


BUDGET_IMPORT_STATUS_PARSED = "parsed"
BUDGET_IMPORT_STATUS_CONFIRMED = "confirmed"
BUDGET_IMPORT_STATUS_ACTIVE = "active"
BUDGET_IMPORT_STATUS_SUPERSEDED = "superseded"


def _longtext_type():
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class BudgetProjectProfile(Base):
    """Budget workspace metadata for the canonical ``projects`` aggregate."""

    __tablename__ = "budget_project_profiles"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_budget_project_profiles_project"),
        Index("ix_budget_profiles_status_created", "workspace_status", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    # A budget workspace is an extension of the canonical Project aggregate.
    # Project deletion must never silently erase budget import audit data.
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    workspace_status = Column(String(24), nullable=False, default="active", server_default="active", index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    archive_reason = Column(Text, nullable=True)
    active_import_batch_id = Column(
        Integer,
        ForeignKey("budget_project_import_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    active_import_revision_id = Column(
        Integer,
        ForeignKey("budget_project_import_revisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    active_pricing_run_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("Project")
    active_import_batch = relationship(
        "BudgetProjectImportBatch",
        foreign_keys=[active_import_batch_id],
        post_update=True,
    )
    active_import_revision = relationship(
        "BudgetProjectImportRevision",
        foreign_keys=[active_import_revision_id],
        post_update=True,
    )
    active_pricing_run = relationship(
        "BudgetProjectPricingRun",
        foreign_keys=[active_pricing_run_id],
        post_update=True,
    )


class BudgetProjectImportBatch(Base):
    __tablename__ = "budget_project_import_batches"
    __table_args__ = (
        UniqueConstraint("batch_uuid", name="uq_budget_import_batches_uuid"),
        Index("ix_budget_import_project_created", "project_id", "created_at"),
        Index("ix_budget_import_project_status", "project_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    batch_uuid = Column(String(36), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_file_object_id = Column(Integer, ForeignKey("file_objects.id", ondelete="SET NULL"), nullable=True, index=True)
    source_filename = Column(String(255), nullable=False)
    source_file_sha256 = Column(String(64), nullable=False, index=True)
    source_file_size = Column(Integer, nullable=False)
    source_storage_mode = Column(String(32), nullable=False, default="metadata_only", server_default="metadata_only")
    parser_version = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False, default="parsed", server_default="parsed", index=True)
    remap_revision = Column(Integer, nullable=False, default=0, server_default="0")
    current_revision_id = Column(
        Integer,
        ForeignKey("budget_project_import_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    confirmed_revision_id = Column(
        Integer,
        ForeignKey("budget_project_import_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    activated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    superseded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    sheet_count = Column(Integer, nullable=False, default=0, server_default="0")
    total_output_row_count = Column(Integer, nullable=False, default=0, server_default="0")
    standard_item_count = Column(Integer, nullable=False, default=0, server_default="0")
    valid_quantity_count = Column(Integer, nullable=False, default=0, server_default="0")
    invalid_quantity_count = Column(Integer, nullable=False, default=0, server_default="0")
    original_preview_json = Column(_longtext_type(), nullable=False)
    current_preview_json = Column(_longtext_type(), nullable=False)
    issues_json = Column(_longtext_type(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("Project")
    source_file_object = relationship("FileObject")
    sheet_mappings = relationship(
        "BudgetProjectImportSheetMapping",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="BudgetProjectImportSheetMapping.id",
    )
    rows = relationship(
        "BudgetProjectStandardRow",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="BudgetProjectStandardRow.sort_order",
    )
    revisions = relationship(
        "BudgetProjectImportRevision",
        back_populates="batch",
        foreign_keys="BudgetProjectImportRevision.batch_id",
        order_by="BudgetProjectImportRevision.revision_number",
    )
    lifecycle_events = relationship(
        "BudgetProjectImportLifecycleEvent",
        back_populates="batch",
        foreign_keys="BudgetProjectImportLifecycleEvent.batch_id",
        order_by="BudgetProjectImportLifecycleEvent.id",
    )
    current_revision = relationship(
        "BudgetProjectImportRevision",
        foreign_keys=[current_revision_id],
        post_update=True,
    )
    confirmed_revision = relationship(
        "BudgetProjectImportRevision",
        foreign_keys=[confirmed_revision_id],
        post_update=True,
    )


class BudgetProjectImportRevision(Base):
    """Append-only snapshot of one parsed/remapped bill revision."""

    __tablename__ = "budget_project_import_revisions"
    __table_args__ = (
        UniqueConstraint("revision_uuid", name="uq_budget_import_revisions_uuid"),
        UniqueConstraint("batch_id", "revision_number", name="uq_budget_import_revision_number"),
        Index("ix_budget_import_revision_batch_created", "batch_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    revision_uuid = Column(String(36), nullable=False)
    batch_id = Column(
        Integer,
        ForeignKey("budget_project_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_number = Column(Integer, nullable=False)
    revision_kind = Column(String(24), nullable=False, default="remap", server_default="remap")
    snapshot_sha256 = Column(String(64), nullable=False, index=True)
    preview_json = Column(_longtext_type(), nullable=False)
    sheet_mappings_json = Column(_longtext_type(), nullable=False)
    standard_rows_json = Column(_longtext_type(), nullable=False)
    summary_json = Column(_longtext_type(), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    batch = relationship(
        "BudgetProjectImportBatch",
        back_populates="revisions",
        foreign_keys=[batch_id],
    )


class BudgetProjectImportLifecycleEvent(Base):
    """Append-only state transition audit for import confirmation/activation."""

    __tablename__ = "budget_project_import_lifecycle_events"
    __table_args__ = (
        Index("ix_budget_import_event_project_created", "project_id", "created_at"),
        Index("ix_budget_import_event_batch_created", "batch_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("budget_project_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_id = Column(
        Integer,
        ForeignKey("budget_project_import_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(32), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_json = Column(_longtext_type(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    batch = relationship(
        "BudgetProjectImportBatch",
        back_populates="lifecycle_events",
        foreign_keys=[batch_id],
    )
    revision = relationship("BudgetProjectImportRevision", foreign_keys=[revision_id])


class BudgetProjectImportSheetMapping(Base):
    __tablename__ = "budget_project_import_sheet_mappings"
    __table_args__ = (
        UniqueConstraint("batch_id", "sheet_name", name="uq_budget_import_sheet_batch_name"),
        Index("ix_budget_import_sheet_batch_revision", "batch_id", "mapping_revision"),
    )

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("budget_project_import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    sheet_name = Column(String(255), nullable=False)
    sheet_role = Column(String(32), nullable=False, default="bill", server_default="bill", index=True)
    header_row_index = Column(Integer, nullable=True)
    detected_field_mapping_json = Column(_longtext_type(), nullable=False)
    applied_field_mapping_json = Column(_longtext_type(), nullable=False)
    detected_columns_json = Column(_longtext_type(), nullable=False)
    current_columns_json = Column(_longtext_type(), nullable=False)
    mapping_revision = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    batch = relationship("BudgetProjectImportBatch", back_populates="sheet_mappings")


class BudgetProjectStandardRow(Base):
    __tablename__ = "budget_project_standard_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_key", name="uq_budget_standard_rows_batch_key"),
        Index("ix_budget_rows_batch_order", "batch_id", "sort_order"),
        Index("ix_budget_rows_batch_type_quantity", "batch_id", "row_type", "quantity_status"),
    )

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("budget_project_import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    row_key = Column(String(255), nullable=False)
    source_sheet = Column(String(255), nullable=False, index=True)
    sheet_role = Column(String(32), nullable=False, default="bill", server_default="bill", index=True)
    raw_row_index = Column(Integer, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    mapping_revision = Column(Integer, nullable=False, default=0, server_default="0")
    row_type = Column(String(32), nullable=False, index=True)
    is_standard_item = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    item_name = Column(String(255), nullable=True, index=True)
    spec = Column(_longtext_type(), nullable=True)
    unit = Column(String(64), nullable=True)
    remark = Column(_longtext_type(), nullable=True)
    raw_quantity = Column(_longtext_type(), nullable=True)
    parser_quantity = Column(Numeric(20, 6), nullable=True)
    calculation_quantity = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    quantity_status = Column(String(32), nullable=False, default="not_applicable", server_default="not_applicable", index=True)
    quantity_source_json = Column(_longtext_type(), nullable=True)
    quantity_candidates_json = Column(_longtext_type(), nullable=True)
    field_mapping_json = Column(_longtext_type(), nullable=True)
    raw_text = Column(_longtext_type(), nullable=True)
    raw_fields_json = Column(_longtext_type(), nullable=True)
    raw_cells_json = Column(_longtext_type(), nullable=True)
    warnings_json = Column(_longtext_type(), nullable=True)
    confidence = Column(String(24), nullable=True)
    requires_confirmation = Column(Boolean, nullable=False, default=False, server_default="0")
    standard_row_json = Column(_longtext_type(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    batch = relationship("BudgetProjectImportBatch", back_populates="rows")
