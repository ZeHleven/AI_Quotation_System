from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class FileObject(Base):
    __tablename__ = "file_objects"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(String(36), unique=True, index=True, nullable=False)
    username = Column(String(64), index=True, nullable=False)
    purpose = Column(String(64), index=True, default="general", nullable=False)
    bucket = Column(String(128), nullable=False)
    object_name = Column(String(512), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    size_bytes = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
