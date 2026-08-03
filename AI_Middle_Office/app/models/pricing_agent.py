"""Isolated pricing-agent v1 persistence.

These tables are deliberately separate from the frozen quote, budget-pricing,
account-quota, and enterprise-quota write paths.  The pricing agent may read
enterprise quota data, but it never mutates those source tables.
"""

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
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


ARCHIVE_STATUS_READY = "ready"
ARCHIVE_STATUS_FAILED = "failed"
ARCHIVE_STATUS_DISABLED = "disabled"

RUN_STATUS_PROCESSING = "processing"
RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_PARTIAL = "partial"
RUN_STATUS_FAILED = "failed"


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class PricingArchiveFile(Base):
    __tablename__ = "pricing_archive_files"
    __table_args__ = (
        UniqueConstraint("archive_uuid", name="uq_pricing_archive_files_uuid"),
        UniqueConstraint("account_id", "file_sha256", name="uq_pricing_archive_files_account_sha256"),
        Index("ix_pricing_archive_files_account_status", "account_id", "status"),
        Index("ix_pricing_archive_files_account_created", "account_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    archive_uuid = Column(String(36), nullable=False, unique=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    file_sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False, default=0, server_default="0")
    storage_backend = Column(String(24), nullable=False)
    storage_bucket = Column(String(128), nullable=True)
    storage_object_name = Column(String(512), nullable=False)
    parser_version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default=ARCHIVE_STATUS_READY, server_default=ARCHIVE_STATUS_READY)
    indexed_row_count = Column(Integer, nullable=False, default=0, server_default="0")
    rejected_row_count = Column(Integer, nullable=False, default=0, server_default="0")
    summary_json = Column(_long_text(), nullable=True)
    issues_json = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    lines = relationship(
        "PricingArchiveLine",
        back_populates="archive_file",
        cascade="all, delete-orphan",
        order_by="PricingArchiveLine.sort_order",
    )


class PricingArchiveLine(Base):
    __tablename__ = "pricing_archive_lines"
    __table_args__ = (
        UniqueConstraint(
            "archive_file_id",
            "source_sheet",
            "source_row_index",
            name="uq_pricing_archive_lines_file_sheet_row",
        ),
        Index("ix_pricing_archive_lines_account_name_unit", "account_id", "normalized_name", "normalized_unit"),
        Index("ix_pricing_archive_lines_account_code", "account_id", "normalized_code"),
        Index("ix_pricing_archive_lines_file_order", "archive_file_id", "sort_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    line_uuid = Column(String(36), nullable=False, unique=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    archive_file_id = Column(
        Integer,
        ForeignKey("pricing_archive_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_sheet = Column(String(128), nullable=False)
    source_row_index = Column(Integer, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    item_code = Column(String(128), nullable=True)
    item_name = Column(String(500), nullable=False)
    specification = Column(String(1000), nullable=True)
    unit = Column(String(64), nullable=True)
    quantity = Column(Numeric(20, 6), nullable=True)
    unit_price = Column(Numeric(20, 6), nullable=False)
    total_price = Column(Numeric(24, 6), nullable=True)
    normalized_code = Column(String(128), nullable=True, index=True)
    normalized_name = Column(String(500), nullable=False, index=True)
    normalized_spec = Column(String(1000), nullable=True)
    normalized_unit = Column(String(64), nullable=True, index=True)
    searchable = Column(Boolean, nullable=False, default=True, server_default="1", index=True)
    price_derivation = Column(String(32), nullable=False, default="source_unit_price", server_default="source_unit_price")
    fingerprint = Column(String(64), nullable=False, index=True)
    raw_text = Column(_long_text(), nullable=True)
    raw_row_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    archive_file = relationship("PricingArchiveFile", back_populates="lines")


class PricingAgentRun(Base):
    __tablename__ = "pricing_agent_runs"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_pricing_agent_runs_uuid"),
        Index("ix_pricing_agent_runs_account_created", "account_id", "created_at"),
        Index("ix_pricing_agent_runs_account_status", "account_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_uuid = Column(String(36), nullable=False, unique=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    mode = Column(String(24), nullable=False)
    status = Column(String(24), nullable=False, default=RUN_STATUS_PROCESSING, server_default=RUN_STATUS_PROCESSING)
    sources_json = Column(_long_text(), nullable=False)
    context_json = Column(_long_text(), nullable=False)
    request_json = Column(_long_text(), nullable=False)
    summary_json = Column(_long_text(), nullable=True)
    result_json = Column(_long_text(), nullable=True)
    error_json = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    confirmed_quote_job_id = Column(
        String(36),
        ForeignKey("quote_jobs.job_id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    confirmed_preview_draft_id = Column(
        Integer,
        ForeignKey("quote_preview_drafts.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    confirmed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmation_hash = Column(String(64), nullable=True, index=True)
    confirmation_json = Column(_long_text(), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lines = relationship(
        "PricingAgentRunLine",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="PricingAgentRunLine.sort_order",
    )


class PricingAgentRunLine(Base):
    __tablename__ = "pricing_agent_run_lines"
    __table_args__ = (
        UniqueConstraint("run_id", "row_key", name="uq_pricing_agent_run_lines_run_row_key"),
        Index("ix_pricing_agent_run_lines_run_order", "run_id", "sort_order"),
        Index("ix_pricing_agent_run_lines_source_match", "selected_source", "match_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    line_uuid = Column(String(36), nullable=False, unique=True, index=True)
    run_id = Column(Integer, ForeignKey("pricing_agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    row_key = Column(String(128), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    item_code = Column(String(128), nullable=True)
    item_name = Column(String(500), nullable=False)
    specification = Column(String(1000), nullable=True)
    quantity = Column(Numeric(20, 6), nullable=True)
    unit = Column(String(64), nullable=True)
    selected_source = Column(String(32), nullable=True, index=True)
    match_type = Column(String(32), nullable=True, index=True)
    unit_price = Column(Numeric(20, 6), nullable=True)
    total_price = Column(Numeric(24, 6), nullable=True)
    confidence = Column(Numeric(9, 6), nullable=True)
    requires_review = Column(Boolean, nullable=False, default=False, server_default="0")
    evidence_json = Column(_long_text(), nullable=True)
    candidates_json = Column(_long_text(), nullable=True)
    selection_origin = Column(String(24), nullable=False, default="automatic", server_default="automatic")
    selected_candidate_json = Column(_long_text(), nullable=True)
    manual_selected_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    manual_selected_at = Column(DateTime(timezone=True), nullable=True)
    decision_revision = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run = relationship("PricingAgentRun", back_populates="lines")
