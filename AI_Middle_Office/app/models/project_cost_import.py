from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


IMPORT_STATUS_REVIEWING = "reviewing"
IMPORT_STATUS_DRAFT_CREATED = "draft_created"
IMPORT_STATUS_FAILED = "failed"
CANDIDATE_STATUS_PENDING = "pending"
CANDIDATE_STATUS_APPROVED = "approved"
CANDIDATE_STATUS_REJECTED = "rejected"


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class ProjectCostImportBatch(Base):
    __tablename__ = "project_cost_import_batches"
    __table_args__ = (
        UniqueConstraint("batch_uuid", name="uq_project_cost_import_batches_uuid"),
        Index("ix_project_cost_import_batches_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_name = Column(String(255), nullable=False, index=True)
    source_name = Column(String(255), nullable=True)
    status = Column(String(24), nullable=False, default=IMPORT_STATUS_REVIEWING, server_default=IMPORT_STATUS_REVIEWING, index=True)
    parser_version = Column(String(64), nullable=False)
    file_count = Column(Integer, nullable=False, default=0, server_default="0")
    parsed_file_count = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_file_count = Column(Integer, nullable=False, default=0, server_default="0")
    observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    candidate_count = Column(Integer, nullable=False, default=0, server_default="0")
    high_confidence_count = Column(Integer, nullable=False, default=0, server_default="0")
    approved_count = Column(Integer, nullable=False, default=0, server_default="0")
    rejected_count = Column(Integer, nullable=False, default=0, server_default="0")
    source_manifest_json = Column(_long_text(), nullable=True)
    summary_json = Column(_long_text(), nullable=True)
    target_quota_version_id = Column(Integer, ForeignKey("enterprise_quota_versions.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    observations = relationship("EnterpriseResourcePriceObservation", back_populates="batch", cascade="all, delete-orphan", order_by="EnterpriseResourcePriceObservation.id")
    candidates = relationship("ProjectCostPriceCandidate", back_populates="batch", cascade="all, delete-orphan", order_by="ProjectCostPriceCandidate.id")
    target_quota_version = relationship("EnterpriseQuotaVersion")


class EnterpriseResourcePriceObservation(Base):
    __tablename__ = "enterprise_resource_price_observations"
    __table_args__ = (
        Index("ix_resource_price_observations_batch_source", "batch_id", "observation_type"),
        Index("ix_resource_price_observations_candidate_key", "batch_id", "candidate_key"),
        Index("ix_resource_price_observations_name_unit", "normalized_item_name", "unit"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("project_cost_import_batches.id"), nullable=False, index=True)
    observation_type = Column(String(32), nullable=False, default="order", server_default="order", index=True)
    source_file_name = Column(String(500), nullable=False)
    source_file_sha256 = Column(String(64), nullable=False, index=True)
    source_sheet = Column(String(128), nullable=True, index=True)
    source_row_index = Column(Integer, nullable=True, index=True)
    supplier_name = Column(String(255), nullable=True, index=True)
    order_no = Column(String(128), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    raw_item_name = Column(String(255), nullable=False)
    normalized_item_name = Column(String(255), nullable=False, index=True)
    brand = Column(String(255), nullable=True)
    spec = Column(String(500), nullable=True)
    unit = Column(String(64), nullable=True, index=True)
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True, index=True)
    amount = Column(Float, nullable=True)
    tax_included = Column(Boolean, nullable=True)
    tax_rate = Column(Float, nullable=True)
    freight_included = Column(Boolean, nullable=True)
    is_return = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    excluded_reason = Column(String(64), nullable=True, index=True)
    candidate_key = Column(String(64), nullable=True, index=True)
    raw_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    batch = relationship("ProjectCostImportBatch", back_populates="observations")


class ProjectCostPriceCandidate(Base):
    __tablename__ = "project_cost_price_candidates"
    __table_args__ = (
        UniqueConstraint("batch_id", "candidate_key", name="uq_project_cost_price_candidates_batch_key"),
        Index("ix_project_cost_price_candidates_batch_status", "batch_id", "status"),
        Index("ix_project_cost_price_candidates_risk_confidence", "risk_level", "confidence_score"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("project_cost_import_batches.id"), nullable=False, index=True)
    candidate_key = Column(String(64), nullable=False, index=True)
    normalized_item_name = Column(String(255), nullable=False, index=True)
    brand = Column(String(255), nullable=True)
    spec = Column(String(500), nullable=True)
    unit = Column(String(64), nullable=True, index=True)
    resource_type = Column(String(32), nullable=False, default="main_material", server_default="main_material", index=True)
    observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    supplier_count = Column(Integer, nullable=False, default=0, server_default="0")
    min_price = Column(Float, nullable=True)
    median_price = Column(Float, nullable=True)
    max_price = Column(Float, nullable=True)
    recommended_price = Column(Float, nullable=True)
    volatility_rate = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=False, default=0, server_default="0", index=True)
    risk_level = Column(String(16), nullable=False, default="medium", server_default="medium", index=True)
    status = Column(String(24), nullable=False, default=CANDIDATE_STATUS_PENDING, server_default=CANDIDATE_STATUS_PENDING, index=True)
    matched_resource_id = Column(Integer, ForeignKey("enterprise_cost_resources.id"), nullable=True, index=True)
    match_type = Column(String(32), nullable=True)
    match_confidence = Column(Float, nullable=True)
    review_note = Column(String(2000), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    draft_resource_id = Column(Integer, ForeignKey("enterprise_cost_resources.id"), nullable=True, index=True)
    evidence_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    batch = relationship("ProjectCostImportBatch", back_populates="candidates")
    matched_resource = relationship("EnterpriseCostResource", foreign_keys=[matched_resource_id])
    draft_resource = relationship("EnterpriseCostResource", foreign_keys=[draft_resource_id])
