from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class KnowledgeCandidate(Base):
    __tablename__ = "knowledge_candidates"

    id = Column(Integer, primary_key=True, index=True)
    candidate_key = Column(String(160), unique=True, index=True, nullable=False)
    source_type = Column(String(32), index=True, nullable=False)
    candidate_kind = Column(String(32), index=True, nullable=False)
    status = Column(String(24), index=True, default="pending", nullable=False)

    source_feedback_id = Column(Integer, ForeignKey("quote_feedback.id"), index=True, nullable=True)
    source_correction_id = Column(Integer, ForeignKey("quote_corrections.id"), index=True, nullable=True)
    source_rag_trace_id = Column(Integer, ForeignKey("quote_rag_traces.id"), index=True, nullable=True)
    quote_id = Column(String(36), index=True, nullable=True)
    quote_job_id = Column(String(36), index=True, nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    username = Column(String(64), index=True, nullable=True)

    item_name = Column(String(255), index=True, nullable=True)
    unit_price = Column(Float, nullable=True)
    unit = Column(String(64), nullable=True)
    notes = Column(_long_text(), nullable=True)
    existing_material_id = Column(String(64), index=True, nullable=True)
    suggested_material_id = Column(String(64), nullable=True)
    material_id = Column(String(64), index=True, nullable=True)
    confidence_score = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    evidence_json = Column(_long_text(), nullable=True)

    created_by = Column(String(64), default="system", nullable=False)
    reviewed_by = Column(String(64), nullable=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_draft_material = Column(Boolean, default=True, nullable=False)
