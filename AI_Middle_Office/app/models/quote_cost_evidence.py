from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class QuoteCostEvidence(Base):
    __tablename__ = "quote_cost_evidence"

    id = Column(Integer, primary_key=True, index=True)
    feedback_id = Column(Integer, ForeignKey("quote_feedback.id"), index=True, nullable=False)
    quote_id = Column(String(36), index=True, nullable=False)
    quote_job_id = Column(String(36), index=True, nullable=True)
    quote_history_id = Column(Integer, index=True, nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    username = Column(String(64), index=True, nullable=False)
    source = Column(String(32), index=True, default="preview", nullable=False)
    status = Column(String(32), index=True, default="pending_review", nullable=False)

    item_index = Column(Integer, nullable=False)
    project_name = Column(String(255), nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String(64), nullable=True)
    ai_unit_price = Column(Float, nullable=True)
    ai_total_price = Column(Float, nullable=True)
    final_unit_price = Column(Float, nullable=True)
    final_total_price = Column(Float, nullable=True)
    line_total_price = Column(Float, nullable=True)
    line_total_source = Column(String(32), nullable=True)
    quote_total_price = Column(Float, nullable=True)
    quote_total_source = Column(String(32), nullable=True)
    quote_reference_total_price = Column(Float, nullable=True)
    manual_modified = Column(Boolean, default=False, nullable=False)
    adopted_cost_reference = Column(Boolean, nullable=True)

    cost_item_id = Column(Integer, index=True, nullable=True)
    cost_item_name_snapshot = Column(String(255), nullable=True)
    cost_item_category_snapshot = Column(String(128), nullable=True)
    cost_item_subcategory_snapshot = Column(String(128), nullable=True)
    cost_item_unit_snapshot = Column(String(64), nullable=True)
    cost_item_status_snapshot = Column(String(24), nullable=True)
    reference_price = Column(Float, nullable=True)
    reference_total = Column(Float, nullable=True)
    reference_price_source = Column(String(64), nullable=True)
    reference_price_source_label = Column(String(64), nullable=True)
    match_type = Column(String(64), nullable=True)
    match_type_label = Column(String(64), nullable=True)
    match_reason = Column(Text, nullable=True)
    price_delta = Column(Float, nullable=True)
    price_delta_rate = Column(Float, nullable=True)
    fallback_applied = Column(Boolean, default=False, nullable=False)

    ai_basis = Column(Text, nullable=True)
    cost_context_basis = Column(Text, nullable=True)
    comparison = Column(Text, nullable=True)
    cost_item_url = Column(String(255), nullable=True)
    cost_reference_json = Column(_long_text(), nullable=True)
    quote_explanation_json = Column(_long_text(), nullable=True)
    cost_item_snapshot_json = Column(_long_text(), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
