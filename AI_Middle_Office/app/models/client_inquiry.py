from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func

from app.core.database import Base


DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"
DIRECTION_VALUES = {DIRECTION_INBOUND, DIRECTION_OUTBOUND}

STAGE_INITIAL_CONTACT = "初步接触"
STAGE_REQUIREMENT_CONFIRMATION = "需求确认"
STAGE_QUOTING = "报价中"
STAGE_FOLLOWUP_NEGOTIATION = "跟进议价"
STAGE_WON = "成单"
STAGE_LOST = "丢单"
STAGE_VALUES = {
    STAGE_INITIAL_CONTACT,
    STAGE_REQUIREMENT_CONFIRMATION,
    STAGE_QUOTING,
    STAGE_FOLLOWUP_NEGOTIATION,
    STAGE_WON,
    STAGE_LOST,
}
STAGE_TERMINAL = {STAGE_WON, STAGE_LOST}


class ClientInquiry(Base):
    __tablename__ = "client_inquiries"
    __table_args__ = (
        Index("ix_client_inquiries_stage_next_followup_at", "stage", "next_followup_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    inquiry_id = Column(String(36), unique=True, index=True, nullable=False)
    source = Column(String(64), index=True, nullable=True)
    client_name = Column(String(128), nullable=True)
    client_phone = Column(String(64), nullable=True)
    inquiry_time = Column(DateTime(timezone=True), index=True, nullable=False)
    first_response_time = Column(DateTime(timezone=True), index=True, nullable=True)
    time_source = Column(String(24), index=True, nullable=False, default="default", server_default="default")
    responder_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    notes = Column(Text, nullable=True)
    first_quote_job_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    direction = Column(String(16), nullable=False, default=DIRECTION_INBOUND, server_default=DIRECTION_INBOUND)
    stage = Column(String(32), nullable=True)
    next_followup_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(Text, nullable=True)
