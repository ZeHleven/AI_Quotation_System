from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class QuoteHistory(Base):
    __tablename__ = "quote_history"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), index=True)
    quote_id = Column(String(64), index=True, nullable=True)
    quote_job_id = Column(String(36), index=True, nullable=True)
    trace_id = Column(String(64), index=True, nullable=True)
    request_text = Column(_long_text(), nullable=True)
    source_file_name = Column(String(255), nullable=True)
    display_title = Column(String(255), nullable=True)
    project_summary = Column(String(512), nullable=True)
    first_project_names = Column(Text, nullable=True)
    confirmed_by = Column(String(64), index=True, nullable=True)
    pushed_to_dingtalk = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    total_amount = Column(Float, default=0.0)
    item_count = Column(Integer, default=0)
    payload_json = Column(_long_text())

    items = relationship(
        "QuoteHistoryItem",
        back_populates="history",
        cascade="all, delete-orphan",
        order_by="QuoteHistoryItem.line_no",
    )


class QuoteHistoryItem(Base):
    __tablename__ = "quote_history_items"

    id = Column(Integer, primary_key=True, index=True)
    quote_history_id = Column(Integer, ForeignKey("quote_history.id"), index=True, nullable=False)
    line_no = Column(Integer, nullable=False)
    project_name = Column(String(255), index=True, nullable=True)
    space = Column(String(128), nullable=True)
    unit = Column(String(64), nullable=True)
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    total_price = Column(Float, nullable=True)
    material = Column(String(255), nullable=True)
    craft = Column(String(255), nullable=True)
    spec = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    raw_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    history = relationship("QuoteHistory", back_populates="items")
