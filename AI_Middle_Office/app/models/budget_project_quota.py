"""Mutable project quota snapshots and resource composition rows.

These tables are deliberately separate from both the immutable pricing runs
and the enterprise quota master.  Project edits are local until a privileged
user explicitly creates or updates an enterprise-quota draft version.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship

from app.core.database import Base


def _longtext_type():
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class BudgetProjectQuotaSnapshot(Base):
    __tablename__ = "budget_project_quota_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_uuid", name="uq_budget_project_quota_snapshots_uuid"),
        UniqueConstraint("draft_line_id", name="uq_budget_project_quota_snapshots_draft_line"),
        Index("ix_budget_project_quota_snapshots_project_updated", "project_id", "updated_at"),
        Index("ix_budget_project_quota_snapshots_draft_order", "draft_id", "draft_line_id"),
        Index("ix_budget_project_quota_snapshots_source_quota_item", "source_enterprise_quota_item_id"),
    )

    id = Column(Integer, primary_key=True)
    snapshot_uuid = Column(String(36), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    draft_id = Column(Integer, ForeignKey("budget_project_pricing_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_line_id = Column(Integer, ForeignKey("budget_project_pricing_draft_lines.id", ondelete="CASCADE"), nullable=False, index=True)

    source_enterprise_version_id = Column(
        Integer,
        ForeignKey("enterprise_quota_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_enterprise_quota_item_id = Column(
        Integer,
        ForeignKey("enterprise_quota_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    section_path_json = Column(_longtext_type(), nullable=False)
    quota_code = Column(String(64), nullable=True, index=True)
    item_name = Column(String(255), nullable=False)
    work_content = Column(_longtext_type(), nullable=True)
    specification = Column(String(255), nullable=True)
    brand = Column(String(255), nullable=True)
    unit = Column(String(64), nullable=True)

    labor_fee = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    main_material_fee = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    auxiliary_material_fee = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    machinery_fee = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    unit_price = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    revision = Column(Integer, nullable=False, default=1, server_default="1")

    enterprise_sync_version_id = Column(
        Integer,
        ForeignKey("enterprise_quota_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    enterprise_synced_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    enterprise_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    draft = relationship("BudgetProjectPricingDraft")
    draft_line = relationship("BudgetProjectPricingDraftLine")
    source_enterprise_version = relationship(
        "EnterpriseQuotaVersion",
        foreign_keys=[source_enterprise_version_id],
    )
    source_enterprise_quota_item = relationship(
        "EnterpriseQuotaItem",
        foreign_keys=[source_enterprise_quota_item_id],
    )
    enterprise_sync_version = relationship(
        "EnterpriseQuotaVersion",
        foreign_keys=[enterprise_sync_version_id],
    )
    resources = relationship(
        "BudgetProjectQuotaResource",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="BudgetProjectQuotaResource.sort_order, BudgetProjectQuotaResource.id",
    )
    events = relationship(
        "BudgetProjectQuotaEvent",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="BudgetProjectQuotaEvent.created_at, BudgetProjectQuotaEvent.id",
    )


class BudgetProjectQuotaResource(Base):
    __tablename__ = "budget_project_quota_resources"
    __table_args__ = (
        UniqueConstraint("resource_uuid", name="uq_budget_project_quota_resources_uuid"),
        Index("ix_budget_project_quota_resources_snapshot_order", "snapshot_id", "sort_order"),
        Index("ix_budget_project_quota_resources_snapshot_bucket", "snapshot_id", "fee_bucket"),
    )

    id = Column(Integer, primary_key=True)
    resource_uuid = Column(String(36), nullable=False)
    snapshot_id = Column(
        Integer,
        ForeignKey("budget_project_quota_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_enterprise_component_id = Column(
        Integer,
        ForeignKey("enterprise_quota_components.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_enterprise_resource_id = Column(
        Integer,
        ForeignKey("enterprise_cost_resources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    origin = Column(String(32), nullable=False, default="enterprise_snapshot", server_default="enterprise_snapshot")
    component_type = Column(String(64), nullable=True)
    resource_code = Column(String(64), nullable=True, index=True)
    resource_name = Column(String(255), nullable=False, index=True)
    worker_or_subtype = Column(String(128), nullable=True)
    work_content = Column(_longtext_type(), nullable=True)
    specification = Column(String(255), nullable=True)
    brand = Column(String(255), nullable=True)
    unit = Column(String(64), nullable=True)
    quantity = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    unit_price = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    amount = Column(Numeric(20, 6), nullable=False, default=0, server_default="0")
    fee_bucket = Column(String(32), nullable=False, default="auxiliary_material", server_default="auxiliary_material")
    library_kind = Column(String(24), nullable=True)
    category = Column(String(128), nullable=True)
    calculation_rule = Column(_longtext_type(), nullable=True)
    tax_rate = Column(Numeric(12, 6), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    snapshot = relationship("BudgetProjectQuotaSnapshot", back_populates="resources")
    source_enterprise_component = relationship("EnterpriseQuotaComponent")
    source_enterprise_resource = relationship("EnterpriseCostResource")


class BudgetProjectQuotaEvent(Base):
    __tablename__ = "budget_project_quota_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_budget_project_quota_events_uuid"),
        Index("ix_budget_project_quota_events_snapshot_created", "snapshot_id", "created_at"),
        Index("ix_budget_project_quota_events_project_created", "project_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    event_uuid = Column(String(36), nullable=False)
    snapshot_id = Column(
        Integer,
        ForeignKey("budget_project_quota_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type = Column(String(48), nullable=False, index=True)
    resource_uuid = Column(String(36), nullable=True, index=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    before_json = Column(_longtext_type(), nullable=True)
    after_json = Column(_longtext_type(), nullable=True)
    details_json = Column(_longtext_type(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    snapshot = relationship("BudgetProjectQuotaSnapshot", back_populates="events")
    actor = relationship("User", foreign_keys=[actor_id])
