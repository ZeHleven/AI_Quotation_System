from sqlalchemy import Column, DateTime, Double, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


MEASUREMENT_STATUS_DRAFT = "draft"
MEASUREMENT_STATUS_LOCKED = "locked"
MEASUREMENT_STATUS_ARCHIVED = "archived"
LINE_TYPE_ITEM = "item"
LINE_TYPE_MEASURE = "measure"
PRICING_MODE_BREAKDOWN = "breakdown"
PRICING_MODE_COMPOSITE = "composite"


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class CostMeasurement(Base):
    __tablename__ = "cost_measurements"
    __table_args__ = (
        UniqueConstraint("measurement_uuid", name="uq_cost_measurements_uuid"),
        UniqueConstraint("measurement_code", name="uq_cost_measurements_code"),
        Index("ix_cost_measurements_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    measurement_uuid = Column(String(36), nullable=False, unique=True, index=True)
    measurement_code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    project_name = Column(String(255), nullable=True, index=True)
    status = Column(String(24), nullable=False, default=MEASUREMENT_STATUS_DRAFT, server_default=MEASUREMENT_STATUS_DRAFT, index=True)
    source_filename = Column(String(255), nullable=True)
    source_file_sha256 = Column(String(64), nullable=True, index=True)
    quota_version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id"), nullable=True, index=True)
    management_rate = Column(Double, nullable=False, default=0.03, server_default="0.03")
    profit_rate = Column(Double, nullable=False, default=0.05, server_default="0.05")
    tax_rate = Column(Double, nullable=False, default=0.09, server_default="0.09")
    line_count = Column(Integer, nullable=False, default=0, server_default="0")
    review_line_count = Column(Integer, nullable=False, default=0, server_default="0")
    matched_quota_count = Column(Integer, nullable=False, default=0, server_default="0")
    direct_cost = Column(Double, nullable=False, default=0, server_default="0")
    management_fee = Column(Double, nullable=False, default=0, server_default="0")
    profit_fee = Column(Double, nullable=False, default=0, server_default="0")
    pretax_total = Column(Double, nullable=False, default=0, server_default="0")
    tax_total = Column(Double, nullable=False, default=0, server_default="0")
    grand_total = Column(Double, nullable=False, default=0, server_default="0")
    source_summary_json = Column(_long_text(), nullable=True)
    notes = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    locked_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    lines = relationship("CostMeasurementLine", back_populates="measurement", cascade="all, delete-orphan", order_by="CostMeasurementLine.sort_order")
    events = relationship("CostMeasurementEvent", back_populates="measurement", cascade="all, delete-orphan", order_by="CostMeasurementEvent.id")


class CostMeasurementLine(Base):
    __tablename__ = "cost_measurement_lines"
    __table_args__ = (
        UniqueConstraint("measurement_id", "line_key", name="uq_cost_measurement_lines_measurement_key"),
        Index("ix_cost_measurement_lines_measurement_order", "measurement_id", "sort_order"),
        Index("ix_cost_measurement_lines_review", "measurement_id", "review_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    measurement_id = Column(Integer, ForeignKey("cost_measurements.id"), nullable=False, index=True)
    line_key = Column(String(128), nullable=False, index=True)
    quota_item_id = Column(Integer, ForeignKey("enterprise_quota_items.id"), nullable=True, index=True)
    source_sheet = Column(String(128), nullable=True, index=True)
    source_row_index = Column(Integer, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    sequence_no = Column(String(64), nullable=True)
    section_name = Column(String(255), nullable=True, index=True)
    item_name = Column(String(255), nullable=False, index=True)
    feature = Column(_long_text(), nullable=True)
    unit = Column(String(64), nullable=True, index=True)
    quantity = Column(Double, nullable=False, default=0, server_default="0")
    line_type = Column(String(24), nullable=False, default=LINE_TYPE_ITEM, server_default=LINE_TYPE_ITEM)
    pricing_mode = Column(String(24), nullable=False, default=PRICING_MODE_BREAKDOWN, server_default=PRICING_MODE_BREAKDOWN)
    price_source = Column(String(32), nullable=False, default="historical_excel", server_default="historical_excel")
    source_unit_price = Column(Double, nullable=True)
    source_total_price = Column(Double, nullable=True)
    labor_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    main_material_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    material_loss_rate = Column(Double, nullable=False, default=0, server_default="0")
    auxiliary_machinery_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    subcontract_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    direct_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    management_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    profit_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    calculated_unit_price = Column(Double, nullable=False, default=0, server_default="0")
    calculated_total_price = Column(Double, nullable=False, default=0, server_default="0")
    source_variance = Column(Double, nullable=False, default=0, server_default="0")
    review_status = Column(String(24), nullable=False, default="pending", server_default="pending", index=True)
    warnings_json = Column(_long_text(), nullable=True)
    source_row_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    measurement = relationship("CostMeasurement", back_populates="lines")
    quota_item = relationship("EnterpriseQuotaItem")


class CostMeasurementEvent(Base):
    __tablename__ = "cost_measurement_events"
    __table_args__ = (Index("ix_cost_measurement_events_measurement_created", "measurement_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    measurement_id = Column(Integer, ForeignKey("cost_measurements.id"), nullable=False, index=True)
    line_id = Column(Integer, ForeignKey("cost_measurement_lines.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    message = Column(String(2000), nullable=True)
    payload_json = Column(_long_text(), nullable=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    measurement = relationship("CostMeasurement", back_populates="events")
