from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base


COST_STATUS_DRAFT = "draft"
COST_STATUS_ACTIVE = "active"
COST_STATUS_ARCHIVED = "archived"
COST_STATUS_VALUES = {COST_STATUS_DRAFT, COST_STATUS_ACTIVE, COST_STATUS_ARCHIVED}

COST_SOURCE_MANUAL = "manual"
COST_SOURCE_IMPORTED = "imported"
COST_SOURCE_AI_SUGGESTED = "ai_suggested"
COST_SOURCE_VALUES = {COST_SOURCE_MANUAL, COST_SOURCE_IMPORTED, COST_SOURCE_AI_SUGGESTED}

PRICE_TYPE_LABOR = "labor"
PRICE_TYPE_MATERIAL = "material"
PRICE_TYPE_COMBINED = "combined"
PRICE_TYPE_VALUES = {PRICE_TYPE_LABOR, PRICE_TYPE_MATERIAL, PRICE_TYPE_COMBINED}

CHANGE_TYPE_PRICE = "price_change"
CHANGE_TYPE_STATUS = "status_change"


class CostItem(Base):
    __tablename__ = "cost_items"
    __table_args__ = (
        Index("ix_cost_items_category_subcategory", "category", "subcategory"),
        Index("ix_cost_items_status_price_type", "status", "price_type"),
        Index("ix_cost_items_duplicate_key", "category", "subcategory", "item_name", "unit"),
    )

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(128), index=True, nullable=False)
    subcategory = Column(String(128), index=True, nullable=True)
    item_name = Column(String(255), index=True, nullable=False)
    spec = Column(Text, nullable=True)
    unit = Column(String(64), nullable=False)
    price = Column(Float, nullable=False)
    client_tax_excluded_price = Column(Float, nullable=True)
    client_labor_price = Column(Float, nullable=True)
    client_main_material_price = Column(Float, nullable=True)
    client_auxiliary_material_price = Column(Float, nullable=True)
    client_direct_fee = Column(Float, nullable=True)
    client_management_profit = Column(Float, nullable=True)
    subcontract_composite_price = Column(Float, nullable=True)
    subcontract_labor_price = Column(Float, nullable=True)
    subcontract_main_material_price = Column(Float, nullable=True)
    subcontract_auxiliary_material_price = Column(Float, nullable=True)
    crew_benchmark_price = Column(Float, nullable=True)
    price_type = Column(String(24), index=True, nullable=False, default=PRICE_TYPE_COMBINED, server_default=PRICE_TYPE_COMBINED)
    status = Column(String(24), index=True, nullable=False, default=COST_STATUS_DRAFT, server_default=COST_STATUS_DRAFT)
    source = Column(String(32), index=True, nullable=False, default=COST_SOURCE_MANUAL, server_default=COST_SOURCE_MANUAL)
    effective_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    history = relationship("CostItemHistory", back_populates="cost_item", cascade="all, delete-orphan")


class CostItemHistory(Base):
    __tablename__ = "cost_item_history"

    id = Column(Integer, primary_key=True, index=True)
    cost_item_id = Column(Integer, ForeignKey("cost_items.id"), nullable=False, index=True)
    old_price = Column(Float, nullable=True)
    new_price = Column(Float, nullable=True)
    old_client_tax_excluded_price = Column(Float, nullable=True)
    new_client_tax_excluded_price = Column(Float, nullable=True)
    old_client_labor_price = Column(Float, nullable=True)
    new_client_labor_price = Column(Float, nullable=True)
    old_client_main_material_price = Column(Float, nullable=True)
    new_client_main_material_price = Column(Float, nullable=True)
    old_client_auxiliary_material_price = Column(Float, nullable=True)
    new_client_auxiliary_material_price = Column(Float, nullable=True)
    old_client_direct_fee = Column(Float, nullable=True)
    new_client_direct_fee = Column(Float, nullable=True)
    old_client_management_profit = Column(Float, nullable=True)
    new_client_management_profit = Column(Float, nullable=True)
    old_subcontract_composite_price = Column(Float, nullable=True)
    new_subcontract_composite_price = Column(Float, nullable=True)
    old_subcontract_labor_price = Column(Float, nullable=True)
    new_subcontract_labor_price = Column(Float, nullable=True)
    old_subcontract_main_material_price = Column(Float, nullable=True)
    new_subcontract_main_material_price = Column(Float, nullable=True)
    old_subcontract_auxiliary_material_price = Column(Float, nullable=True)
    new_subcontract_auxiliary_material_price = Column(Float, nullable=True)
    old_crew_benchmark_price = Column(Float, nullable=True)
    new_crew_benchmark_price = Column(Float, nullable=True)
    old_status = Column(String(24), nullable=True)
    new_status = Column(String(24), nullable=True)
    change_type = Column(String(32), index=True, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    change_reason = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    cost_item = relationship("CostItem", back_populates="history")


class CostRagSyncRun(Base):
    __tablename__ = "cost_rag_sync_runs"
    __table_args__ = (
        Index("ix_cost_rag_sync_runs_started_at", "started_at"),
        Index("ix_cost_rag_sync_runs_status_started_at", "status", "started_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(64), index=True, nullable=False, default="cost_items.active", server_default="cost_items.active")
    status = Column(String(24), index=True, nullable=False, default="running", server_default="running")
    requested_count = Column(Integer, nullable=False, default=0, server_default="0")
    synced_count = Column(Integer, nullable=False, default=0, server_default="0")
    message = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    rag_service_url = Column(String(255), nullable=True)
    http_status = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    triggered_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    triggered_by_username = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
