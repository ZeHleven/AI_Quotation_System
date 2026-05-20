from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import relationship

from app.core.database import Base


EVENT_TYPE_CREATE = "create"
EVENT_TYPE_STAGE_CHANGE = "stage_change"
EVENT_TYPE_CANCEL = "cancel"
EVENT_TYPE_TRANSFER = "transfer"
EVENT_TYPE_EDIT = "edit"
EVENT_TYPE_VALUES = {
    EVENT_TYPE_CREATE,
    EVENT_TYPE_STAGE_CHANGE,
    EVENT_TYPE_CANCEL,
    EVENT_TYPE_TRANSFER,
    EVENT_TYPE_EDIT,
}


class ClientInquiryEvent(Base):
    __tablename__ = "client_inquiry_events"
    __table_args__ = (
        Index("ix_client_inquiry_events_operator_id_operated_at", "operator_id", "operated_at"),
    )

    id = Column(Integer, primary_key=True)
    inquiry_id = Column(String(36), ForeignKey("client_inquiries.inquiry_id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    old_value = Column(String(64), nullable=True)
    new_value = Column(String(64), nullable=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    operated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    trace_id = Column(String(64), nullable=True)
    before_json = Column(JSON, nullable=True)
    after_json = Column(JSON, nullable=True)

    inquiry = relationship(
        "ClientInquiry",
        foreign_keys=[inquiry_id],
        primaryjoin="ClientInquiryEvent.inquiry_id == ClientInquiry.inquiry_id",
    )
