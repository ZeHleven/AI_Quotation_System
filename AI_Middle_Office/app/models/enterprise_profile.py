from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


ENTERPRISE_PROFILE_STATUS_DRAFT = "draft"
ENTERPRISE_PROFILE_STATUS_ACTIVE = "active"
ENTERPRISE_PROFILE_STATUS_ARCHIVED = "archived"
ENTERPRISE_PROFILE_STATUS_VALUES = {
    ENTERPRISE_PROFILE_STATUS_DRAFT,
    ENTERPRISE_PROFILE_STATUS_ACTIVE,
    ENTERPRISE_PROFILE_STATUS_ARCHIVED,
}

ENTERPRISE_PROFILE_CATEGORY_VALUES = {
    "basic_info",
    "certificate",
    "qualification",
    "personnel",
    "project_performance",
    "technical_solution",
    "attachment_asset",
    "commitment_template",
    "other",
}


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class EnterpriseProfileItem(Base):
    __tablename__ = "enterprise_profile_items"
    __table_args__ = (
        UniqueConstraint("item_uuid", name="uq_enterprise_profile_items_uuid"),
        Index("ix_enterprise_profile_items_category_status", "category", "status"),
        Index("ix_enterprise_profile_items_status_valid_until", "status", "valid_until"),
    )

    id = Column(Integer, primary_key=True, index=True)
    item_uuid = Column(String(36), nullable=False, unique=True, index=True)
    category = Column(String(64), nullable=False, index=True)
    subcategory = Column(String(128), nullable=True, index=True)
    profile_key = Column(String(128), nullable=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    summary = Column(Text, nullable=True)
    content_text = Column(_long_text(), nullable=True)
    structured_json = Column(_long_text(), nullable=True)
    tags_json = Column(_long_text(), nullable=True)
    applicable_scope = Column(Text, nullable=True)
    source = Column(String(64), nullable=False, default="manual", server_default="manual", index=True)
    confidentiality = Column(String(32), nullable=False, default="internal", server_default="internal", index=True)
    status = Column(
        String(24),
        nullable=False,
        default=ENTERPRISE_PROFILE_STATUS_DRAFT,
        server_default=ENTERPRISE_PROFILE_STATUS_DRAFT,
        index=True,
    )
    valid_from = Column(Date, nullable=True, index=True)
    valid_until = Column(Date, nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True, index=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    attachments = relationship(
        "EnterpriseProfileFile",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="EnterpriseProfileFile.created_at",
    )
    events = relationship(
        "EnterpriseProfileEvent",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="EnterpriseProfileEvent.created_at",
    )


class EnterpriseProfileFile(Base):
    __tablename__ = "enterprise_profile_files"
    __table_args__ = (
        UniqueConstraint("attachment_uuid", name="uq_enterprise_profile_files_uuid"),
        Index("ix_enterprise_profile_files_item_type", "item_id", "attachment_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    attachment_uuid = Column(String(36), nullable=False, unique=True, index=True)
    item_id = Column(Integer, ForeignKey("enterprise_profile_items.id"), nullable=False, index=True)
    file_id = Column(String(36), ForeignKey("file_objects.file_id"), nullable=True, index=True)
    attachment_type = Column(String(64), nullable=False, default="source", server_default="source", index=True)
    original_filename = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    item = relationship("EnterpriseProfileItem", back_populates="attachments")
    file_object = relationship("FileObject")


class EnterpriseProfileEvent(Base):
    __tablename__ = "enterprise_profile_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_enterprise_profile_events_uuid"),
        Index("ix_enterprise_profile_events_item_created", "item_id", "created_at"),
        Index("ix_enterprise_profile_events_type_created", "event_type", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_uuid = Column(String(36), nullable=False, unique=True, index=True)
    item_id = Column(Integer, ForeignKey("enterprise_profile_items.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    old_status = Column(String(24), nullable=True)
    new_status = Column(String(24), nullable=True)
    detail_json = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    item = relationship("EnterpriseProfileItem", back_populates="events")
