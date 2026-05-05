from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class Material(Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(String(64), unique=True, index=True, nullable=False)
    item_name = Column(String(255), index=True, nullable=False)
    unit_price = Column(Float, default=0.0)
    unit = Column(String(64), default="项")
    notes = Column(Text, default="")
    is_draft = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MaterialSnapshot(Base):
    __tablename__ = "material_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(String(32), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    username = Column(String(64), index=True, nullable=False)
    action = Column(String(64), index=True, nullable=False)
    reason = Column(String(255), default="")
    item_count = Column(Integer, default=0, nullable=False)
    data_json = Column(_long_text(), nullable=False)
