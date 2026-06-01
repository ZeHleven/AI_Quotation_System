from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT(), "mysql")


PREVIEW_DRAFT_STATUS_EDITING = "editing"
PREVIEW_DRAFT_STATUS_PUSHED = "pushed"
PREVIEW_DRAFT_STATUS_DISCARDED = "discarded"
PREVIEW_DRAFT_STATUSES = {
    PREVIEW_DRAFT_STATUS_EDITING,
    PREVIEW_DRAFT_STATUS_PUSHED,
    PREVIEW_DRAFT_STATUS_DISCARDED,
}


class QuotePreviewDraft(Base):
    __tablename__ = "quote_preview_drafts"
    __table_args__ = (
        Index("ix_quote_preview_drafts_status_updated", "status", "updated_at"),
        Index("ix_quote_preview_drafts_username_status", "username", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    quote_job_id = Column(String(36), ForeignKey("quote_jobs.job_id"), unique=True, index=True, nullable=False)
    quote_id = Column(String(64), nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String(64), index=True, nullable=False)
    status = Column(String(24), index=True, nullable=False, default=PREVIEW_DRAFT_STATUS_EDITING, server_default=PREVIEW_DRAFT_STATUS_EDITING)
    draft_json = Column(_long_text(), nullable=False)
    row_count = Column(Integer, nullable=False, default=0, server_default="0")
    priced_row_count = Column(Integer, nullable=False, default=0, server_default="0")
    unpriced_row_count = Column(Integer, nullable=False, default=0, server_default="0")
    version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    pushed_at = Column(DateTime(timezone=True), nullable=True)
    discarded_at = Column(DateTime(timezone=True), nullable=True)
