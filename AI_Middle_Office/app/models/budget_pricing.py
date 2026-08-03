"""Immutable project-budget pricing snapshots for Phase 2.

The pricing aggregate is intentionally isolated from the legacy quote, cost
measurement, project cost import and RAG chains.  A run binds one confirmed
budget import revision to one enterprise quota version.  Run lines,
candidates and events are append-only evidence; a reprice creates a child run
instead of rewriting prior evidence.
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
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship

from app.core.database import Base


PRICING_RUN_STATUS_PROCESSING = "processing"
PRICING_RUN_STATUS_READY = "ready"
PRICING_RUN_STATUS_CONFIRMED = "confirmed"
PRICING_RUN_STATUS_SUPERSEDED = "superseded"
PRICING_RUN_STATUS_FAILED = "failed"

PRICING_COMPLETENESS_PENDING = "pending"
PRICING_COMPLETENESS_PARTIAL = "partial"
PRICING_COMPLETENESS_COMPLETE = "complete"

PRICING_MATCH_AUTO = "auto_matched"
PRICING_MATCH_MANUAL = "manual_matched"
PRICING_MATCH_AMBIGUOUS = "ambiguous"
PRICING_MATCH_UNMATCHED = "unmatched"
PRICING_MATCH_UNIT_CONFLICT = "unit_conflict"

PRICING_LINE_STATUS_PRICED = "priced"
PRICING_LINE_STATUS_QUANTITY_UNRESOLVED = "quantity_unresolved"
PRICING_LINE_STATUS_MISSING_UNIT_PRICE = "missing_unit_price"
PRICING_LINE_STATUS_PENDING_MATCH = "pending_match"
PRICING_LINE_STATUS_UNIT_CONFLICT = "unit_conflict"


def _longtext_type():
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class BudgetProjectPricingRun(Base):
    """One versioned, reproducible pricing result for a budget project."""

    __tablename__ = "budget_project_pricing_runs"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_budget_pricing_runs_uuid"),
        UniqueConstraint(
            "project_id",
            "run_number",
            name="uq_budget_pricing_runs_project_number",
        ),
        Index("ix_budget_pricing_runs_project_created", "project_id", "created_at"),
        Index("ix_budget_pricing_runs_project_status", "project_id", "status"),
        Index("ix_budget_pricing_runs_source_revision", "source_import_revision_id"),
        Index("ix_budget_pricing_runs_quota_version", "quota_version_id"),
        Index("ix_budget_pricing_runs_parent", "parent_run_id"),
    )

    id = Column(Integer, primary_key=True)
    run_uuid = Column(String(36), nullable=False)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    run_number = Column(Integer, nullable=False)
    parent_run_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    superseded_by_run_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    run_kind = Column(String(32), nullable=False, default="auto_match", server_default="auto_match")
    reason = Column(Text, nullable=True)

    source_import_batch_id = Column(
        Integer,
        ForeignKey("budget_project_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_import_revision_id = Column(
        Integer,
        ForeignKey("budget_project_import_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_import_snapshot_sha256 = Column(String(64), nullable=False, index=True)
    source_rows_sha256 = Column(String(64), nullable=False, index=True)
    source_snapshot_json = Column(_longtext_type(), nullable=False)

    quota_version_id = Column(
        Integer,
        ForeignKey("enterprise_quota_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quota_version_code = Column(String(64), nullable=False)
    quota_version_name = Column(String(255), nullable=False)
    quota_source_file_sha256 = Column(String(64), nullable=True)
    quota_catalog_sha256 = Column(String(64), nullable=False, index=True)

    matching_engine_version = Column(String(64), nullable=False)
    pricing_engine_version = Column(String(64), nullable=False)
    price_basis = Column(
        String(64),
        nullable=False,
        default="enterprise_quota_items.unit_price",
        server_default="enterprise_quota_items.unit_price",
    )
    tax_basis = Column(
        String(32),
        nullable=False,
        default="source_as_is",
        server_default="source_as_is",
    )

    status = Column(
        String(24),
        nullable=False,
        default=PRICING_RUN_STATUS_PROCESSING,
        server_default=PRICING_RUN_STATUS_PROCESSING,
        index=True,
    )
    completeness_status = Column(
        String(24),
        nullable=False,
        default=PRICING_COMPLETENESS_PENDING,
        server_default=PRICING_COMPLETENESS_PENDING,
        index=True,
    )
    standard_item_count = Column(Integer, nullable=False, default=0, server_default="0")
    matched_count = Column(Integer, nullable=False, default=0, server_default="0")
    unit_priced_count = Column(Integer, nullable=False, default=0, server_default="0")
    amount_priced_count = Column(Integer, nullable=False, default=0, server_default="0")
    review_required_count = Column(Integer, nullable=False, default=0, server_default="0")
    unmatched_count = Column(Integer, nullable=False, default=0, server_default="0")
    unit_conflict_count = Column(Integer, nullable=False, default=0, server_default="0")
    quantity_unresolved_count = Column(Integer, nullable=False, default=0, server_default="0")
    missing_price_count = Column(Integer, nullable=False, default=0, server_default="0")
    breakdown_covered_line_count = Column(Integer, nullable=False, default=0, server_default="0")

    priced_subtotal = Column(Numeric(24, 6), nullable=False, default=0, server_default="0")
    total_cost = Column(Numeric(24, 6), nullable=True)
    labor_subtotal = Column(Numeric(24, 6), nullable=True)
    main_material_subtotal = Column(Numeric(24, 6), nullable=True)
    auxiliary_material_subtotal = Column(Numeric(24, 6), nullable=True)
    machinery_subtotal = Column(Numeric(24, 6), nullable=True)
    summary_json = Column(_longtext_type(), nullable=False)
    result_sha256 = Column(String(64), nullable=True, index=True)

    partial_acknowledged_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    partial_acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    partial_acknowledgement_reason = Column(Text, nullable=True)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    superseded_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_detail_json = Column(_longtext_type(), nullable=True)

    project = relationship("Project")
    source_import_batch = relationship("BudgetProjectImportBatch", foreign_keys=[source_import_batch_id])
    source_import_revision = relationship(
        "BudgetProjectImportRevision",
        foreign_keys=[source_import_revision_id],
    )
    quota_version = relationship("EnterpriseQuotaVersion", foreign_keys=[quota_version_id])
    parent_run = relationship(
        "BudgetProjectPricingRun",
        remote_side=[id],
        foreign_keys=[parent_run_id],
        back_populates="child_runs",
    )
    child_runs = relationship(
        "BudgetProjectPricingRun",
        foreign_keys="BudgetProjectPricingRun.parent_run_id",
        back_populates="parent_run",
        order_by="BudgetProjectPricingRun.run_number",
    )
    superseded_by_run = relationship(
        "BudgetProjectPricingRun",
        remote_side=[id],
        foreign_keys=[superseded_by_run_id],
    )
    lines = relationship(
        "BudgetProjectPricingRunLine",
        back_populates="run",
        order_by="BudgetProjectPricingRunLine.source_sort_order",
    )
    events = relationship(
        "BudgetProjectPricingEvent",
        back_populates="run",
        order_by="BudgetProjectPricingEvent.id",
    )
    draft_snapshot = relationship(
        "BudgetProjectPricingRunDraftSnapshot",
        back_populates="run",
        uselist=False,
        cascade="all, delete-orphan",
    )


class BudgetProjectPricingRunDraftSnapshot(Base):
    """Immutable full quote-draft payload bound to one pricing version."""

    __tablename__ = "budget_project_pricing_run_draft_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_uuid",
            name="uq_budget_pricing_run_draft_snapshots_uuid",
        ),
        UniqueConstraint("run_id", name="uq_budget_pricing_run_draft_snapshots_run"),
        Index(
            "ix_budget_pricing_run_draft_snapshots_project_created",
            "project_id",
            "created_at",
        ),
        Index(
            "ix_budget_pricing_run_draft_snapshots_account_created",
            "account_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    snapshot_uuid = Column(String(36), nullable=False)
    run_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_draft_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_drafts.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_draft_uuid = Column(String(36), nullable=False)
    source_draft_revision = Column(Integer, nullable=False)
    pricing_mode = Column(String(32), nullable=False)
    row_count = Column(Integer, nullable=False, default=0, server_default="0")
    snapshot_sha256 = Column(String(64), nullable=False, index=True)
    snapshot_json = Column(_longtext_type(), nullable=False)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run = relationship("BudgetProjectPricingRun", back_populates="draft_snapshot")
    account = relationship("Account")
    project = relationship("Project")
    source_draft = relationship("BudgetProjectPricingDraft")
    creator = relationship("User", foreign_keys=[created_by])


class BudgetProjectPricingRunLine(Base):
    """Append-only source, match and calculated evidence for one bill row."""

    __tablename__ = "budget_project_pricing_run_lines"
    __table_args__ = (
        UniqueConstraint("line_uuid", name="uq_budget_pricing_lines_uuid"),
        UniqueConstraint("run_id", "source_row_key", name="uq_budget_pricing_lines_run_source"),
        Index("ix_budget_pricing_lines_run_order", "run_id", "source_sort_order"),
        Index("ix_budget_pricing_lines_run_match", "run_id", "match_status"),
        Index("ix_budget_pricing_lines_run_pricing", "run_id", "pricing_status"),
        Index("ix_budget_pricing_lines_source_location", "run_id", "source_sheet", "source_raw_row_index"),
    )

    id = Column(Integer, primary_key=True)
    line_uuid = Column(String(36), nullable=False)
    run_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_row_key = Column(String(255), nullable=False)
    source_sheet = Column(String(255), nullable=False, index=True)
    source_raw_row_index = Column(Integer, nullable=False)
    source_sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    source_row_sha256 = Column(String(64), nullable=False, index=True)
    source_row_snapshot_json = Column(_longtext_type(), nullable=False)
    item_name = Column(String(255), nullable=True, index=True)
    spec = Column(_longtext_type(), nullable=True)
    unit = Column(String(64), nullable=True)
    calculation_quantity = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    quantity_status = Column(String(32), nullable=False)

    match_status = Column(
        String(32),
        nullable=False,
        default=PRICING_MATCH_UNMATCHED,
        server_default=PRICING_MATCH_UNMATCHED,
        index=True,
    )
    unit_compatibility = Column(String(24), nullable=True)
    selected_quota_item_id = Column(
        Integer,
        ForeignKey("enterprise_quota_items.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    selected_quota_item_snapshot_json = Column(_longtext_type(), nullable=True)
    # The hash remains part of the immutable evidence but is not searched in
    # the MVP.  Avoid an auto-generated MySQL index name longer than 64 chars.
    selected_quota_item_snapshot_sha256 = Column(String(64), nullable=True)
    selection_source = Column(String(24), nullable=True)
    match_score = Column(Numeric(9, 6), nullable=True)
    match_reason_json = Column(_longtext_type(), nullable=True)
    candidate_count = Column(Integer, nullable=False, default=0, server_default="0")

    pricing_status = Column(
        String(32),
        nullable=False,
        default=PRICING_LINE_STATUS_PENDING_MATCH,
        server_default=PRICING_LINE_STATUS_PENDING_MATCH,
        index=True,
    )
    quota_unit_price = Column(Numeric(20, 6), nullable=True)
    effective_unit_cost = Column(Numeric(20, 6), nullable=True)
    line_total = Column(Numeric(24, 6), nullable=True)
    amount_included = Column(Boolean, nullable=False, default=False, server_default="0")
    labor_unit_cost = Column(Numeric(20, 6), nullable=True)
    main_material_unit_cost = Column(Numeric(20, 6), nullable=True)
    auxiliary_material_unit_cost = Column(Numeric(20, 6), nullable=True)
    machinery_unit_cost = Column(Numeric(20, 6), nullable=True)
    labor_total = Column(Numeric(24, 6), nullable=True)
    main_material_total = Column(Numeric(24, 6), nullable=True)
    auxiliary_material_total = Column(Numeric(24, 6), nullable=True)
    machinery_total = Column(Numeric(24, 6), nullable=True)
    cost_breakdown_json = Column(_longtext_type(), nullable=True)
    warnings_json = Column(_longtext_type(), nullable=True)

    decision_reason = Column(Text, nullable=True)
    decided_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run = relationship("BudgetProjectPricingRun", back_populates="lines")
    selected_quota_item = relationship("EnterpriseQuotaItem", foreign_keys=[selected_quota_item_id])
    candidates = relationship(
        "BudgetProjectPricingMatchCandidate",
        back_populates="run_line",
        order_by="BudgetProjectPricingMatchCandidate.rank",
    )


class BudgetProjectPricingMatchCandidate(Base):
    """Append-only ranked enterprise-quota candidate for one pricing line."""

    __tablename__ = "budget_project_pricing_match_candidates"
    __table_args__ = (
        UniqueConstraint("run_line_id", "rank", name="uq_budget_pricing_candidates_line_rank"),
        UniqueConstraint(
            "run_line_id",
            "quota_item_id",
            name="uq_budget_pricing_candidates_line_item",
        ),
        Index("ix_budget_pricing_candidates_line_score", "run_line_id", "candidate_score"),
        Index("ix_budget_pricing_candidates_quota_item", "quota_item_id"),
    )

    id = Column(Integer, primary_key=True)
    run_line_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_run_lines.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rank = Column(Integer, nullable=False)
    quota_item_id = Column(
        Integer,
        ForeignKey("enterprise_quota_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quota_item_snapshot_json = Column(_longtext_type(), nullable=False)
    candidate_score = Column(Numeric(9, 6), nullable=False)
    name_score = Column(Numeric(9, 6), nullable=True)
    spec_score = Column(Numeric(9, 6), nullable=True)
    unit_score = Column(Numeric(9, 6), nullable=True)
    match_type = Column(String(32), nullable=False)
    unit_compatibility = Column(String(24), nullable=True)
    selection_eligibility = Column(String(24), nullable=False, default="review_only", server_default="review_only")
    is_selected = Column(Boolean, nullable=False, default=False, server_default="0")
    evidence_json = Column(_longtext_type(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run_line = relationship("BudgetProjectPricingRunLine", back_populates="candidates")
    quota_item = relationship("EnterpriseQuotaItem", foreign_keys=[quota_item_id])


class BudgetProjectPricingEvent(Base):
    """Append-only lifecycle and manual-decision audit for a pricing run."""

    __tablename__ = "budget_project_pricing_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_budget_pricing_events_uuid"),
        Index("ix_budget_pricing_events_run_created", "run_id", "created_at"),
        Index("ix_budget_pricing_events_project_created", "project_id", "created_at"),
        Index("ix_budget_pricing_events_actor_created", "actor_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    event_uuid = Column(String(36), nullable=False)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(32), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=False)
    actor_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_json = Column(_longtext_type(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project")
    run = relationship("BudgetProjectPricingRun", back_populates="events")
    actor = relationship("User", foreign_keys=[actor_id])
