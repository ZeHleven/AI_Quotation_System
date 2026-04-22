from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class QuoteHistory(Base):
    __tablename__ = "quote_history"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    total_amount = Column(Float, default=0.0)
    item_count = Column(Integer, default=0)
    payload_json = Column(Text)   # 完整报价明细 JSON
