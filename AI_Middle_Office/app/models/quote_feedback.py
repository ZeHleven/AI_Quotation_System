from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class QuoteFeedback(Base):
    __tablename__ = "quote_feedback"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(String(36), unique=True, index=True, nullable=False)
    quote_job_id = Column(String(36), unique=True, index=True, nullable=True)
    quote_history_id = Column(Integer, index=True, nullable=True)
    username = Column(String(64), index=True, nullable=False)
    trace_id = Column(String(64), index=True, nullable=True)
    source = Column(String(32), index=True, default="async_job", nullable=False)
    status = Column(String(32), index=True, default="pending_review", nullable=False)
    request_text = Column(_long_text(), nullable=True)
    source_file_name = Column(String(255), nullable=True)
    project_summary = Column(String(512), nullable=True)
    change_summary = Column(String(512), nullable=True)
    top_changed_fields = Column(Text, nullable=True)
    reviewed_by = Column(String(64), index=True, nullable=True)

    ai_total_amount = Column(Float, nullable=True)
    final_total_amount = Column(Float, nullable=True)
    amount_delta = Column(Float, nullable=True)
    amount_delta_ratio = Column(Float, nullable=True)
    ai_item_count = Column(Integer, default=0, nullable=False)
    final_item_count = Column(Integer, default=0, nullable=False)
    was_modified = Column(Boolean, default=False, nullable=False)
    pushed_to_dingtalk = Column(Boolean, default=False, nullable=False)
    rejected = Column(Boolean, default=False, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    correction_summary_json = Column(_long_text(), nullable=True)

    dify_app_version = Column(String(128), nullable=True)
    dify_workflow_version = Column(String(128), nullable=True)
    dify_prompt_version = Column(String(128), nullable=True)
    dify_release_id = Column(String(128), nullable=True)
    quote_model_name = Column(String(128), nullable=True)
    vision_model_name = Column(String(128), nullable=True)
    rag_collection_alias = Column(String(128), nullable=True)
    material_snapshot_id = Column(String(32), nullable=True)

    ai_payload_json = Column(_long_text(), nullable=True)
    final_payload_json = Column(_long_text(), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)


class QuoteCorrection(Base):
    __tablename__ = "quote_corrections"

    id = Column(Integer, primary_key=True, index=True)
    feedback_id = Column(Integer, ForeignKey("quote_feedback.id"), index=True, nullable=False)
    quote_id = Column(String(36), index=True, nullable=False)
    quote_job_id = Column(String(36), index=True, nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    item_index = Column(Integer, nullable=True)
    project_name = Column(String(255), nullable=True)
    field_path = Column(String(255), nullable=False)
    field_label = Column(String(64), nullable=True)
    change_type = Column(String(32), index=True, nullable=True)
    before_value = Column(_long_text(), nullable=True)
    after_value = Column(_long_text(), nullable=True)
    before_display = Column(String(255), nullable=True)
    after_display = Column(String(255), nullable=True)
    delta_amount = Column(Float, nullable=True)
    reason_category = Column(String(64), nullable=True)
    reason_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QuoteRagTrace(Base):
    __tablename__ = "quote_rag_traces"

    id = Column(Integer, primary_key=True, index=True)
    feedback_id = Column(Integer, ForeignKey("quote_feedback.id"), index=True, nullable=False)
    quote_id = Column(String(36), index=True, nullable=False)
    quote_job_id = Column(String(36), index=True, nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    query_text = Column(_long_text(), nullable=True)
    item_index = Column(Integer, nullable=True)
    project_name = Column(String(255), nullable=True)
    material_id = Column(String(64), index=True, nullable=True)
    item_name = Column(String(255), nullable=True)
    rank = Column(Integer, nullable=True)
    score = Column(Float, nullable=True)
    collection_alias = Column(String(128), nullable=True)
    material_snapshot_id = Column(String(32), nullable=True)
    sent_to_prompt = Column(Boolean, default=True, nullable=False)
    cited_by_model = Column(Boolean, nullable=True)
    adopted_by_user = Column(Boolean, nullable=True)
    used_in_final_quote = Column(Boolean, nullable=True)
    match_reason = Column(String(255), nullable=True)
    raw_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
