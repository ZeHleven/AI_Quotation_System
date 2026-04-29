from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
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
    result_json = Column(_long_text(), nullable=True)
    error_message = Column(_long_text(), nullable=True)
    events_json = Column(_long_text(), default="[]")
    trace_id = Column(String(64), index=True, nullable=True)
    celery_task_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
