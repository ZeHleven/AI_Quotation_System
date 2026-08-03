from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


IMPORT_BATCH_STATUS_PREVIEWED = "previewed"
IMPORT_BATCH_STATUS_IMPORTED = "imported"
IMPORT_BATCH_STATUS_ACTIVATED = "activated"
IMPORT_BATCH_STATUS_FAILED = "failed"

QUOTA_VERSION_STATUS_DRAFT = "draft"
QUOTA_VERSION_STATUS_ACTIVE = "active"
QUOTA_VERSION_STATUS_ARCHIVED = "archived"

RESOURCE_TYPE_LABOR = "labor"
RESOURCE_TYPE_MAIN_MATERIAL = "main_material"
RESOURCE_TYPE_AUXILIARY_MATERIAL = "auxiliary_material"
RESOURCE_TYPE_MACHINERY = "machinery"
RESOURCE_TYPE_UNKNOWN = "unknown"


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class CostImportBatch(Base):
    __tablename__ = "cost_import_batches"
    __table_args__ = (
        UniqueConstraint("batch_uuid", name="uq_cost_import_batches_batch_uuid"),
        Index("ix_cost_import_batches_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_uuid = Column(String(36), nullable=False, unique=True, index=True)
    source_filename = Column(String(255), nullable=False)
    source_file_sha256 = Column(String(64), nullable=False, index=True)
    source_file_size = Column(Integer, nullable=True)
    parser_version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default=IMPORT_BATCH_STATUS_PREVIEWED, server_default=IMPORT_BATCH_STATUS_PREVIEWED, index=True)
    summary_json = Column(_long_text(), nullable=True)
    issues_json = Column(_long_text(), nullable=True)
    error_count = Column(Integer, nullable=False, default=0, server_default="0")
    warning_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    versions = relationship("EnterpriseQuotaVersion", back_populates="import_batch")


class EnterpriseQuotaVersion(Base):
    __tablename__ = "enterprise_quota_versions"
    __table_args__ = (
        UniqueConstraint("version_code", name="uq_enterprise_quota_versions_version_code"),
        Index("ix_enterprise_quota_versions_status_active", "status", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_code = Column(String(64), nullable=False, unique=True, index=True)
    version_name = Column(String(255), nullable=False)
    import_batch_id = Column(Integer, ForeignKey("cost_import_batches.id"), nullable=True, index=True)
    source_filename = Column(String(255), nullable=True)
    source_file_sha256 = Column(String(64), nullable=True, index=True)
    status = Column(String(24), nullable=False, default=QUOTA_VERSION_STATUS_DRAFT, server_default=QUOTA_VERSION_STATUS_DRAFT, index=True)
    is_active = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    summary_json = Column(_long_text(), nullable=True)
    notes = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    activated_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    activated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    archived_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    import_batch = relationship("CostImportBatch", back_populates="versions")
    sections = relationship(
        "EnterpriseQuotaSection",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="EnterpriseQuotaSection.sort_order",
    )
    items = relationship(
        "EnterpriseQuotaItem",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="EnterpriseQuotaItem.sort_order",
    )
    components = relationship(
        "EnterpriseQuotaComponent",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="EnterpriseQuotaComponent.sort_order",
    )
    resources = relationship(
        "EnterpriseCostResource",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="EnterpriseCostResource.sort_order",
    )


class EnterpriseQuotaSection(Base):
    __tablename__ = "enterprise_quota_sections"
    __table_args__ = (
        UniqueConstraint("version_id", "section_code", name="uq_enterprise_quota_sections_version_code"),
        Index("ix_enterprise_quota_sections_version_order", "version_id", "sort_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id"), nullable=False, index=True)
    section_code = Column(String(64), nullable=True, index=True)
    section_name = Column(String(255), nullable=True, index=True)
    source_sheet = Column(String(128), nullable=True)
    source_row_index = Column(Integer, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    raw_row_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    version = relationship("EnterpriseQuotaVersion", back_populates="sections")
    items = relationship(
        "EnterpriseQuotaItem",
        back_populates="section",
        order_by="EnterpriseQuotaItem.sort_order",
    )


class EnterpriseQuotaItem(Base):
    __tablename__ = "enterprise_quota_items"
    __table_args__ = (
        UniqueConstraint("version_id", "quota_code", name="uq_enterprise_quota_items_version_code"),
        Index("ix_enterprise_quota_items_version_order", "version_id", "sort_order"),
        Index("ix_enterprise_quota_items_section_order", "section_id", "sort_order"),
        Index("ix_enterprise_quota_items_name_unit", "item_name", "unit"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id"), nullable=False, index=True)
    section_id = Column(Integer, ForeignKey("enterprise_quota_sections.id"), nullable=True, index=True)
    quota_code = Column(String(64), nullable=True, index=True)
    item_name = Column(String(255), nullable=True, index=True)
    work_content = Column(_long_text(), nullable=True)
    worker_or_subtype = Column(String(128), nullable=True)
    unit = Column(String(64), nullable=True, index=True)
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True, index=True)
    labor_fee = Column(Float, nullable=True)
    main_material_fee = Column(Float, nullable=True)
    auxiliary_material_fee = Column(Float, nullable=True)
    machinery_fee = Column(Float, nullable=True)
    source_sheet = Column(String(128), nullable=True)
    source_row_index = Column(Integer, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    raw_row_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    version = relationship("EnterpriseQuotaVersion", back_populates="items")
    section = relationship("EnterpriseQuotaSection", back_populates="items")
    components = relationship(
        "EnterpriseQuotaComponent",
        back_populates="quota_item",
        order_by="EnterpriseQuotaComponent.sort_order",
    )


class EnterpriseCostResource(Base):
    __tablename__ = "enterprise_cost_resources"
    __table_args__ = (
        Index("ix_enterprise_cost_resources_version_type", "version_id", "resource_type"),
        Index("ix_enterprise_cost_resources_code_type", "resource_code", "resource_type"),
        Index("ix_enterprise_cost_resources_name_unit", "resource_name", "unit"),
        Index("ix_enterprise_cost_resources_source_row", "source_sheet", "source_row_index"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id"), nullable=False, index=True)
    resource_code = Column(String(64), nullable=True, index=True)
    resource_name = Column(String(255), nullable=True, index=True)
    resource_type = Column(String(32), nullable=False, default=RESOURCE_TYPE_UNKNOWN, server_default=RESOURCE_TYPE_UNKNOWN, index=True)
    unit = Column(String(64), nullable=True, index=True)
    price = Column(Float, nullable=True, index=True)
    tax_rate = Column(Float, nullable=True)
    computed_price = Column(Float, nullable=True)
    price_block_label = Column(String(64), nullable=True)
    source_sheet = Column(String(128), nullable=True)
    source_row_index = Column(Integer, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    raw_row_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    version = relationship("EnterpriseQuotaVersion", back_populates="resources")
    components = relationship("EnterpriseQuotaComponent", back_populates="resource")


class EnterpriseQuotaComponent(Base):
    __tablename__ = "enterprise_quota_components"
    __table_args__ = (
        Index("ix_enterprise_quota_components_version_type", "version_id", "component_type"),
        Index("ix_enterprise_quota_components_item_order", "quota_item_id", "sort_order"),
        Index("ix_enterprise_quota_components_parent_code", "version_id", "parent_quota_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id"), nullable=False, index=True)
    quota_item_id = Column(Integer, ForeignKey("enterprise_quota_items.id"), nullable=True, index=True)
    resource_id = Column(Integer, ForeignKey("enterprise_cost_resources.id"), nullable=True, index=True)
    parent_quota_code = Column(String(64), nullable=True, index=True)
    component_type = Column(String(64), nullable=True, index=True)
    resource_code = Column(String(64), nullable=True, index=True)
    resource_name = Column(String(255), nullable=True, index=True)
    worker_or_subtype = Column(String(128), nullable=True)
    unit = Column(String(64), nullable=True, index=True)
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    amount = Column(Float, nullable=True, index=True)
    fee_bucket = Column(String(32), nullable=True, index=True)
    source_sheet = Column(String(128), nullable=True)
    source_row_index = Column(Integer, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    raw_row_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    version = relationship("EnterpriseQuotaVersion", back_populates="components")
    quota_item = relationship("EnterpriseQuotaItem", back_populates="components")
    resource = relationship("EnterpriseCostResource", back_populates="components")
