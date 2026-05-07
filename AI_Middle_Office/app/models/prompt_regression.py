from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class PromptRegressionCase(Base):
    __tablename__ = "prompt_regression_cases"

    id = Column(Integer, primary_key=True, index=True)
    source_feedback_id = Column(Integer, ForeignKey("quote_feedback.id"), unique=True, index=True, nullable=False)
    quote_id = Column(String(36), index=True, nullable=False)
    quote_job_id = Column(String(36), index=True, nullable=True)
    quote_history_id = Column(Integer, index=True, nullable=True)
    username = Column(String(64), index=True, nullable=False)
    case_name = Column(String(255), nullable=True)
    request_text = Column(_long_text(), nullable=True)

    source_status = Column(String(32), index=True, nullable=False)
    source_prompt_version = Column(String(128), index=True, nullable=True)
    source_workflow_version = Column(String(128), nullable=True)
    source_release_id = Column(String(128), nullable=True)
    rag_collection_alias = Column(String(128), nullable=True)
    material_snapshot_id = Column(String(32), nullable=True)

    ai_total_amount = Column(Float, nullable=True)
    expected_total_amount = Column(Float, nullable=True)
    amount_delta = Column(Float, nullable=True)
    amount_delta_ratio = Column(Float, nullable=True)
    ai_item_count = Column(Integer, default=0, nullable=False)
    expected_item_count = Column(Integer, default=0, nullable=False)
    correction_count = Column(Integer, default=0, nullable=False)
    format_error_count = Column(Integer, default=0, nullable=False)
    missing_item_count = Column(Integer, default=0, nullable=False)
    rejected = Column(Boolean, default=False, nullable=False)
    was_modified = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, index=True, nullable=False)
    locked = Column(Boolean, default=True, nullable=False)
    rejection_reason = Column(Text, nullable=True)

    ai_payload_json = Column(_long_text(), nullable=True)
    expected_payload_json = Column(_long_text(), nullable=True)
    corrections_json = Column(_long_text(), nullable=True)
    metadata_json = Column(_long_text(), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PromptRegressionRun(Base):
    __tablename__ = "prompt_regression_runs"

    id = Column(Integer, primary_key=True, index=True)
    triggered_by = Column(String(64), nullable=False)
    name = Column(String(255), nullable=True)
    status = Column(String(16), index=True, default="completed", nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    prompt_version = Column(String(128), index=True, nullable=True)
    baseline_prompt_version = Column(String(128), nullable=True)
    case_count = Column(Integer, default=0, nullable=False)
    confirmed_count = Column(Integer, default=0, nullable=False)
    rejected_count = Column(Integer, default=0, nullable=False)
    modified_count = Column(Integer, default=0, nullable=False)

    avg_abs_amount_delta = Column(Float, nullable=True)
    avg_abs_delta_ratio = Column(Float, nullable=True)
    exact_total_match_rate = Column(Float, nullable=True)
    format_error_rate = Column(Float, nullable=True)
    missing_item_rate = Column(Float, nullable=True)
    rejection_rate = Column(Float, nullable=True)
    score = Column(Float, nullable=True)

    metrics_json = Column(_long_text(), nullable=True)
    error = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
