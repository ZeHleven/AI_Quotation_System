"""Account-scoped mutable pricing drafts for P2-2A.

Drafts are intentionally separate from immutable ``budget_project_pricing_*``
runs.  Rebuilding, switching modes and editing a draft never creates or
mutates a formal pricing run.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship

from app.core.database import Base


PRICING_MODE_ENTERPRISE_AI = "enterprise_ai"
PRICING_MODE_ACCOUNT_STRICT = "account_strict"
PRICING_DRAFT_STATUS_ACTIVE = "active"
BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_QUEUED = "queued"
BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_RUNNING = "running"
BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_SUCCEEDED = "succeeded"
BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_PARTIAL_FAILED = "partial_failed"
BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_FAILED = "failed"
BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_ENTERPRISE_MATCHED = "enterprise_matched"
BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING = "ai_pending"
BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_RUNNING = "ai_running"
BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED = "ai_succeeded"
BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_FAILED = "ai_failed"
BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_SKIPPED = "skipped"


def _longtext_type():
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class BudgetProjectPricingDraft(Base):
    __tablename__ = "budget_project_pricing_drafts"
    __table_args__ = (
        UniqueConstraint("draft_uuid", name="uq_budget_pricing_drafts_uuid"),
        UniqueConstraint("account_id", "project_id", name="uq_budget_pricing_drafts_account_project"),
        Index("ix_budget_pricing_drafts_account_updated", "account_id", "updated_at"),
        Index("ix_budget_pricing_drafts_project_mode", "project_id", "pricing_mode"),
    )

    id = Column(Integer, primary_key=True)
    draft_uuid = Column(String(36), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    pricing_mode = Column(String(32), nullable=False, index=True)
    status = Column(String(24), nullable=False, default=PRICING_DRAFT_STATUS_ACTIVE, server_default=PRICING_DRAFT_STATUS_ACTIVE)
    revision = Column(Integer, nullable=False, default=1, server_default="1")

    source_import_batch_id = Column(Integer, ForeignKey("budget_project_import_batches.id", ondelete="RESTRICT"), nullable=False)
    source_import_revision_id = Column(Integer, ForeignKey("budget_project_import_revisions.id", ondelete="RESTRICT"), nullable=False)
    source_import_snapshot_sha256 = Column(String(64), nullable=False)
    source_rows_sha256 = Column(String(64), nullable=False)
    source_snapshot_json = Column(_longtext_type(), nullable=False)

    enterprise_quota_version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id", ondelete="RESTRICT"), nullable=True)
    enterprise_quota_catalog_sha256 = Column(String(64), nullable=True)
    account_quota_catalog_sha256 = Column(String(64), nullable=True)
    matching_engine_version = Column(String(64), nullable=False)
    pricing_engine_version = Column(String(64), nullable=False)

    row_count = Column(Integer, nullable=False, default=0, server_default="0")
    matched_count = Column(Integer, nullable=False, default=0, server_default="0")
    priced_count = Column(Integer, nullable=False, default=0, server_default="0")
    pending_count = Column(Integer, nullable=False, default=0, server_default="0")
    manual_price_count = Column(Integer, nullable=False, default=0, server_default="0")
    quantity_unresolved_count = Column(Integer, nullable=False, default=0, server_default="0")
    priced_subtotal = Column(Numeric(24, 6), nullable=False, default=0, server_default="0")
    total_cost = Column(Numeric(24, 6), nullable=True)
    completeness_status = Column(String(24), nullable=False, default="partial", server_default="partial")
    summary_json = Column(_longtext_type(), nullable=False)

    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    account = relationship("Account")
    project = relationship("Project")
    source_import_batch = relationship("BudgetProjectImportBatch", foreign_keys=[source_import_batch_id])
    source_import_revision = relationship("BudgetProjectImportRevision", foreign_keys=[source_import_revision_id])
    enterprise_quota_version = relationship("EnterpriseQuotaVersion", foreign_keys=[enterprise_quota_version_id])
    lines = relationship(
        "BudgetProjectPricingDraftLine",
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="BudgetProjectPricingDraftLine.source_sort_order",
    )
    events = relationship(
        "BudgetProjectPricingDraftEvent",
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="BudgetProjectPricingDraftEvent.id",
    )


class BudgetProjectPricingDraftLine(Base):
    __tablename__ = "budget_project_pricing_draft_lines"
    __table_args__ = (
        UniqueConstraint("line_uuid", name="uq_budget_pricing_draft_lines_uuid"),
        UniqueConstraint("draft_id", "source_row_key", name="uq_budget_pricing_draft_lines_source"),
        Index("ix_budget_pricing_draft_lines_order", "draft_id", "source_sort_order"),
        Index("ix_budget_pricing_draft_lines_match", "draft_id", "match_status"),
        Index("ix_budget_pricing_draft_lines_pricing", "draft_id", "pricing_status"),
    )

    id = Column(Integer, primary_key=True)
    line_uuid = Column(String(36), nullable=False)
    draft_id = Column(Integer, ForeignKey("budget_project_pricing_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_row_key = Column(String(255), nullable=False)
    source_sheet = Column(String(255), nullable=False)
    source_raw_row_index = Column(Integer, nullable=False)
    source_sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    source_row_sha256 = Column(String(64), nullable=False)
    source_row_snapshot_json = Column(_longtext_type(), nullable=False)
    item_name = Column(String(255), nullable=True, index=True)
    spec = Column(_longtext_type(), nullable=True)
    unit = Column(String(64), nullable=True)
    calculation_quantity = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    quantity_status = Column(String(32), nullable=False)

    match_status = Column(String(32), nullable=False, default="unmatched", server_default="unmatched")
    pricing_status = Column(String(32), nullable=False, default="pending_match", server_default="pending_match")
    candidate_count = Column(Integer, nullable=False, default=0, server_default="0")
    match_score = Column(Numeric(9, 6), nullable=True)
    match_evidence_json = Column(_longtext_type(), nullable=True)
    selected_enterprise_quota_item_id = Column(Integer, ForeignKey("enterprise_quota_items.id", ondelete="RESTRICT"), nullable=True)
    selected_account_quota_item_id = Column(Integer, ForeignKey("account_quota_items.id", ondelete="RESTRICT"), nullable=True)
    selected_source_snapshot_json = Column(_longtext_type(), nullable=True)

    base_unit_price = Column(Numeric(20, 6), nullable=True)
    ai_estimated_unit_price = Column(Numeric(20, 6), nullable=True)
    ai_estimate_snapshot_json = Column(_longtext_type(), nullable=True)
    manual_unit_price = Column(Numeric(20, 6), nullable=True)
    effective_unit_price = Column(Numeric(20, 6), nullable=True)
    line_total = Column(Numeric(24, 6), nullable=True)
    amount_included = Column(Boolean, nullable=False, default=False, server_default="0")
    price_source = Column(String(32), nullable=False, default="none", server_default="none")
    warnings_json = Column(_longtext_type(), nullable=True)
    line_revision = Column(Integer, nullable=False, default=1, server_default="1")
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    draft = relationship("BudgetProjectPricingDraft", back_populates="lines")
    selected_enterprise_quota_item = relationship("EnterpriseQuotaItem", foreign_keys=[selected_enterprise_quota_item_id])
    selected_account_quota_item = relationship("AccountQuotaItem", foreign_keys=[selected_account_quota_item_id])


class BudgetProjectPricingDraftEvent(Base):
    __tablename__ = "budget_project_pricing_draft_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_budget_pricing_draft_events_uuid"),
        Index("ix_budget_pricing_draft_events_draft_created", "draft_id", "created_at"),
        Index("ix_budget_pricing_draft_events_account_created", "account_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    event_uuid = Column(String(36), nullable=False)
    draft_id = Column(Integer, ForeignKey("budget_project_pricing_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type = Column(String(48), nullable=False, index=True)
    from_mode = Column(String(32), nullable=True)
    to_mode = Column(String(32), nullable=True)
    from_revision = Column(Integer, nullable=True)
    to_revision = Column(Integer, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    event_json = Column(_longtext_type(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    draft = relationship("BudgetProjectPricingDraft", back_populates="events")
    account = relationship("Account")
    project = relationship("Project")
    actor = relationship("User", foreign_keys=[actor_id])


class BudgetProjectPricingDraftQuoteJob(Base):
    __tablename__ = "budget_project_pricing_draft_quote_jobs"
    __table_args__ = (
        UniqueConstraint("job_uuid", name="uq_budget_pricing_draft_quote_jobs_uuid"),
        Index("ix_budget_pricing_draft_quote_jobs_project_created", "project_id", "created_at"),
        Index("ix_budget_pricing_draft_quote_jobs_draft_status", "draft_id", "status"),
        Index("ix_budget_pricing_draft_quote_jobs_account_created", "account_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    job_uuid = Column(String(36), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    draft_id = Column(Integer, ForeignKey("budget_project_pricing_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_mode = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default=BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_QUEUED, server_default=BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_QUEUED)
    progress_percent = Column(Integer, nullable=False, default=0, server_default="0")
    current_message = Column(String(512), nullable=True)

    total_line_count = Column(Integer, nullable=False, default=0, server_default="0")
    enterprise_priced_count = Column(Integer, nullable=False, default=0, server_default="0")
    ai_total_count = Column(Integer, nullable=False, default=0, server_default="0")
    ai_completed_count = Column(Integer, nullable=False, default=0, server_default="0")
    ai_failed_count = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_count = Column(Integer, nullable=False, default=0, server_default="0")

    source_import_batch_id = Column(Integer, ForeignKey("budget_project_import_batches.id", ondelete="RESTRICT"), nullable=False)
    source_import_revision_id = Column(Integer, ForeignKey("budget_project_import_revisions.id", ondelete="RESTRICT"), nullable=False)
    enterprise_quota_version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id", ondelete="RESTRICT"), nullable=True)
    request_json = Column(_longtext_type(), nullable=False)
    result_json = Column(_longtext_type(), nullable=True)
    error_json = Column(_longtext_type(), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    account = relationship("Account")
    project = relationship("Project")
    draft = relationship("BudgetProjectPricingDraft")
    created_by_user = relationship("User", foreign_keys=[created_by])
    lines = relationship(
        "BudgetProjectPricingDraftQuoteJobLine",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="BudgetProjectPricingDraftQuoteJobLine.source_sort_order",
    )


class BudgetProjectPricingDraftQuoteJobLine(Base):
    __tablename__ = "budget_project_pricing_draft_quote_job_lines"
    __table_args__ = (
        UniqueConstraint("job_id", "draft_line_id", name="uq_budget_pricing_draft_quote_job_lines_line"),
        Index("ix_budget_pricing_draft_quote_job_lines_job_status", "job_id", "status"),
        Index("ix_budget_pricing_draft_quote_job_lines_draft_line", "draft_line_id"),
    )

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("budget_project_pricing_draft_quote_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_line_id = Column(Integer, ForeignKey("budget_project_pricing_draft_lines.id", ondelete="CASCADE"), nullable=False, index=True)
    line_uuid = Column(String(36), nullable=False)
    source_row_key = Column(String(255), nullable=False)
    source_sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    item_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default=BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING, server_default=BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING)
    source = Column(String(32), nullable=False, default="ai_estimate", server_default="ai_estimate")
    provider = Column(String(32), nullable=True)
    model = Column(String(128), nullable=True)
    unit_price = Column(Numeric(20, 6), nullable=True)
    result_json = Column(_longtext_type(), nullable=True)
    error_json = Column(_longtext_type(), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    job = relationship("BudgetProjectPricingDraftQuoteJob", back_populates="lines")
    draft_line = relationship("BudgetProjectPricingDraftLine")
