from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class ClientInquiry(Base):
    __tablename__ = "client_inquiries"

    id = Column(Integer, primary_key=True, index=True)
    inquiry_id = Column(String(36), unique=True, index=True, nullable=False)
    source = Column(String(64), index=True, nullable=True)
    client_name = Column(String(128), nullable=True)
    client_phone = Column(String(64), nullable=True)
    inquiry_time = Column(DateTime(timezone=True), index=True, nullable=False)
    first_response_time = Column(DateTime(timezone=True), index=True, nullable=False)
    time_source = Column(String(24), index=True, nullable=False, default="default", server_default="default")
    responder_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    notes = Column(Text, nullable=True)
    first_quote_job_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
