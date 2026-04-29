from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class ModelCallLog(Base):
    __tablename__ = "model_call_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    trace_id = Column(String(64), index=True, nullable=True)
    username = Column(String(64), index=True, nullable=True)
    provider = Column(String(64), index=True, nullable=False)
    model = Column(String(128), index=True, nullable=False)
    endpoint_type = Column(String(64), index=True, nullable=False)
    status = Column(String(24), index=True, nullable=False)
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Float, default=0.0)
    input_chars = Column(Integer, default=0)
    output_chars = Column(Integer, default=0)
    estimated_cost = Column(Float, default=0.0)
    error_message = Column(Text, nullable=True)
