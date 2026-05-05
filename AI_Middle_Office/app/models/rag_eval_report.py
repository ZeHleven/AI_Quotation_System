from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class RagEvalReport(Base):
    __tablename__ = "rag_eval_reports"

    id = Column(Integer, primary_key=True, index=True)
    triggered_by = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    top_k = Column(Integer, nullable=False, default=5)
    case_count = Column(Integer, nullable=True)
    hit_rate = Column(Float, nullable=True)
    mrr = Column(Float, nullable=True)
    by_level_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    report_path = Column(String(256), nullable=True)
