from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class QuoteJob(Base):
    __tablename__ = "quote_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(36), unique=True, index=True, nullable=False)
    username = Column(String(64), index=True, nullable=False)
    status = Column(String(24), index=True, default="queued", nullable=False)
    stage = Column(String(64), default="queued")
    message = Column(_long_text(), default="")
    file_name = Column(String(255), nullable=True)
    file_mime_type = Column(String(128), nullable=True)
    file_object_id = Column(String(36), nullable=True)
    file_base64 = Column(_long_text(), nullable=True)
    request_summary = Column(String(255), nullable=True)
    source_file_name = Column(String(255), nullable=True)
    result_total_amount = Column(Float, nullable=True)
    result_item_count = Column(Integer, default=0, nullable=False)
    preview_project_names = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    failure_stage = Column(String(64), index=True, nullable=True)
    result_json = Column(_long_text(), nullable=True)
    error_message = Column(_long_text(), nullable=True)
    events_json = Column(_long_text(), default="[]")
    trace_id = Column(String(64), index=True, nullable=True)
    celery_task_id = Column(String(128), nullable=True)
    client_inquiry_id = Column(String(36), ForeignKey("client_inquiries.inquiry_id"), index=True, nullable=True)
    budget_project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True)
    budget_pricing_draft_id = Column(
        Integer,
        ForeignKey("budget_project_pricing_drafts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    budget_workspace_source_sha256 = Column(String(64), nullable=True)
    budget_workspace_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    events = relationship(
        "QuoteJobEvent",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="QuoteJobEvent.event_index",
    )
    budget_project = relationship("Project", foreign_keys=[budget_project_id])
    budget_pricing_draft = relationship(
        "BudgetProjectPricingDraft",
        foreign_keys=[budget_pricing_draft_id],
    )


class QuoteJobEvent(Base):
    __tablename__ = "quote_job_events"

    id = Column(Integer, primary_key=True, index=True)
    quote_job_id = Column(String(36), ForeignKey("quote_jobs.job_id"), index=True, nullable=False)
    event_index = Column(Integer, nullable=False)
    event_type = Column(String(32), index=True, nullable=False)
    stage = Column(String(64), index=True, nullable=True)
    message = Column(_long_text(), nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    payload_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job = relationship("QuoteJob", back_populates="events")
