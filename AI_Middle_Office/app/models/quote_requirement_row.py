from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class QuoteRequirementRow(Base):
    __tablename__ = "quote_job_requirement_rows"

    id = Column(Integer, primary_key=True, index=True)
    quote_job_id = Column(String(36), ForeignKey("quote_jobs.job_id"), index=True, nullable=False)
    requirement_row_key = Column(String(128), index=True, nullable=True)
    source_sheet = Column(String(255), nullable=True)
    raw_row_index = Column(Integer, nullable=True)
    item_name = Column(String(255), nullable=True)
    spec = Column(Text, nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String(64), nullable=True)
    remark = Column(Text, nullable=True)
    raw_text = Column(_long_text(), nullable=True)
    raw_cells_json = Column(_long_text(), nullable=True)
    row_json = Column(_long_text(), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
