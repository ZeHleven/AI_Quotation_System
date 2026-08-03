"""Account-scoped editable quota catalog.

The account quota catalog is intentionally independent from the immutable
enterprise quota catalog.  Only ``active`` account items will be eligible for
account pricing in a later phase; this module itself contains no matching or
pricing integration.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


ACCOUNT_QUOTA_STATUS_DRAFT = "draft"
ACCOUNT_QUOTA_STATUS_ACTIVE = "active"
ACCOUNT_QUOTA_STATUS_ARCHIVED = "archived"
ACCOUNT_QUOTA_STATUS_VALUES = {
    ACCOUNT_QUOTA_STATUS_DRAFT,
    ACCOUNT_QUOTA_STATUS_ACTIVE,
    ACCOUNT_QUOTA_STATUS_ARCHIVED,
}

ACCOUNT_QUOTA_SOURCE_MANUAL = "manual"
ACCOUNT_QUOTA_SOURCE_IMPORTED = "imported"
ACCOUNT_QUOTA_SOURCE_PRICING_DRAFT_SYNC = "pricing_draft_sync"
ACCOUNT_QUOTA_SOURCE_AI_ESTIMATE = "ai_estimate"
ACCOUNT_QUOTA_SOURCE_VALUES = {
    ACCOUNT_QUOTA_SOURCE_MANUAL,
    ACCOUNT_QUOTA_SOURCE_IMPORTED,
    ACCOUNT_QUOTA_SOURCE_PRICING_DRAFT_SYNC,
    ACCOUNT_QUOTA_SOURCE_AI_ESTIMATE,
}

ACCOUNT_QUOTA_EVENT_CREATED = "created"
ACCOUNT_QUOTA_EVENT_UPDATED = "updated"
ACCOUNT_QUOTA_EVENT_STATUS_CHANGED = "status_changed"
ACCOUNT_QUOTA_EVENT_PRICING_DRAFT_SYNCED = "pricing_draft_synced"

ACCOUNT_QUOTA_SYNC_STATUS_COMPLETED = "completed"
ACCOUNT_QUOTA_SYNC_ACTION_CREATE = "create"
ACCOUNT_QUOTA_SYNC_ACTION_UPDATE_EXISTING = "update_existing"
ACCOUNT_QUOTA_SYNC_ACTION_SKIP = "skip"


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class AccountQuotaItem(Base):
    __tablename__ = "account_quota_items"
    __table_args__ = (
        UniqueConstraint("item_uuid", name="uq_account_quota_items_uuid"),
        UniqueConstraint("account_id", "fingerprint", name="uq_account_quota_items_account_fingerprint"),
        Index("ix_account_quota_items_account_status", "account_id", "status"),
        Index("ix_account_quota_items_account_updated", "account_id", "updated_at"),
        Index("ix_account_quota_items_account_name_unit", "account_id", "item_name", "unit"),
    )

    id = Column(Integer, primary_key=True)
    item_uuid = Column(String(36), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    quota_code = Column(String(64), nullable=True, index=True)
    item_name = Column(String(255), nullable=False, index=True)
    item_features = Column(_long_text(), nullable=True)
    spec = Column(_long_text(), nullable=True)
    unit = Column(String(64), nullable=False, index=True)
    unit_price = Column(Numeric(18, 6), nullable=False)
    fingerprint = Column(String(64), nullable=False, index=True)
    source = Column(
        String(32),
        nullable=False,
        default=ACCOUNT_QUOTA_SOURCE_MANUAL,
        server_default=ACCOUNT_QUOTA_SOURCE_MANUAL,
        index=True,
    )
    status = Column(
        String(24),
        nullable=False,
        default=ACCOUNT_QUOTA_STATUS_DRAFT,
        server_default=ACCOUNT_QUOTA_STATUS_DRAFT,
        index=True,
    )
    notes = Column(_long_text(), nullable=True)
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    account = relationship("Account")
    history = relationship(
        "AccountQuotaItemHistory",
        back_populates="item",
        order_by="AccountQuotaItemHistory.revision",
    )


class AccountQuotaItemHistory(Base):
    __tablename__ = "account_quota_item_history"
    __table_args__ = (
        UniqueConstraint("account_quota_item_id", "revision", name="uq_account_quota_history_item_revision"),
        Index("ix_account_quota_history_account_created", "account_id", "created_at"),
        Index("ix_account_quota_history_item_created", "account_quota_item_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    account_quota_item_id = Column(
        Integer,
        ForeignKey("account_quota_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    event_type = Column(String(32), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=False)
    before_snapshot_json = Column(_long_text(), nullable=True)
    after_snapshot_json = Column(_long_text(), nullable=False)
    reason = Column(_long_text(), nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    item = relationship("AccountQuotaItem", back_populates="history")
    account = relationship("Account")
    actor = relationship("User", foreign_keys=[actor_id])


class AccountQuotaSyncRun(Base):
    """One confirmed pricing-draft-to-account-quota synchronization.

    Preview is deliberately read-only.  A row is written here only after the
    operator confirms a concrete set of create/update/skip decisions.
    """

    __tablename__ = "account_quota_sync_runs"
    __table_args__ = (
        UniqueConstraint("sync_uuid", name="uq_account_quota_sync_runs_uuid"),
        Index("ix_account_quota_sync_runs_account_created", "account_id", "created_at"),
        Index("ix_account_quota_sync_runs_draft_created", "draft_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    sync_uuid = Column(String(36), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    draft_id = Column(Integer, ForeignKey("budget_project_pricing_drafts.id", ondelete="RESTRICT"), nullable=False, index=True)
    draft_revision = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default=ACCOUNT_QUOTA_SYNC_STATUS_COMPLETED, server_default=ACCOUNT_QUOTA_SYNC_STATUS_COMPLETED)
    requested_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_count = Column(Integer, nullable=False, default=0, server_default="0")
    updated_count = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_count = Column(Integer, nullable=False, default=0, server_default="0")
    reason = Column(_long_text(), nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    account = relationship("Account")
    project = relationship("Project")
    draft = relationship("BudgetProjectPricingDraft")
    actor = relationship("User", foreign_keys=[actor_id])
    lines = relationship(
        "AccountQuotaSyncLine",
        back_populates="sync_run",
        cascade="all, delete-orphan",
        order_by="AccountQuotaSyncLine.id",
    )


class AccountQuotaSyncLine(Base):
    """Immutable source/target evidence for one line inside a sync run."""

    __tablename__ = "account_quota_sync_lines"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "draft_line_id", name="uq_account_quota_sync_lines_run_draft_line"),
        Index("ix_account_quota_sync_lines_account_created", "account_id", "created_at"),
        Index("ix_account_quota_sync_lines_target", "account_quota_item_id"),
    )

    id = Column(Integer, primary_key=True)
    sync_run_id = Column(Integer, ForeignKey("account_quota_sync_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    draft_id = Column(Integer, ForeignKey("budget_project_pricing_drafts.id", ondelete="RESTRICT"), nullable=False, index=True)
    # The immutable source snapshot remains authoritative.  A pricing draft can
    # be rebuilt later, so its live line link must be optional rather than
    # blocking deletion of superseded mutable lines.
    draft_line_id = Column(Integer, ForeignKey("budget_project_pricing_draft_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    source_line_uuid = Column(String(36), nullable=False)
    source_line_revision = Column(Integer, nullable=False)
    fingerprint = Column(String(64), nullable=True, index=True)
    action = Column(String(32), nullable=False)
    outcome = Column(String(32), nullable=False)
    account_quota_item_id = Column(Integer, ForeignKey("account_quota_items.id", ondelete="RESTRICT"), nullable=True)
    target_item_revision = Column(Integer, nullable=True)
    source_snapshot_json = Column(_long_text(), nullable=False)
    target_before_snapshot_json = Column(_long_text(), nullable=True)
    target_after_snapshot_json = Column(_long_text(), nullable=True)
    result_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sync_run = relationship("AccountQuotaSyncRun", back_populates="lines")
    account = relationship("Account")
    draft = relationship("BudgetProjectPricingDraft")
    draft_line = relationship("BudgetProjectPricingDraftLine")
    account_quota_item = relationship("AccountQuotaItem")
