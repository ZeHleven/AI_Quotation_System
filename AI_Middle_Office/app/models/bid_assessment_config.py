"""Frozen enterprise snapshots and versioned bid-assessment configuration."""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _artifact_table_args(table_name: str, *extra_constraints):
    return (
        *extra_constraints,
        CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=f"ck_{table_name}_status",
        ),
        CheckConstraint(
            "((status = 'draft' AND active_slot_key IS NULL) "
            "OR (status = 'active' AND active_slot_key = 'active' "
            "AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND activated_at IS NOT NULL) "
            "OR (status = 'retired' AND active_slot_key IS NULL AND retired_at IS NOT NULL))",
            name=f"ck_{table_name}_lifecycle",
        ),
        CheckConstraint("row_version >= 1", name=f"ck_{table_name}_row_version"),
        UniqueConstraint("version", name=f"uq_{table_name}_version"),
        UniqueConstraint("artifact_hash", name=f"uq_{table_name}_artifact_hash"),
        UniqueConstraint("active_slot_key", name=f"uq_{table_name}_active_slot"),
        Index(f"ix_{table_name}_status", "status"),
        Index(f"ix_{table_name}_created", "created_at"),
        TABLE_OPTIONS,
    )


class BidEnterpriseSnapshot(Base):
    __tablename__ = "bid_enterprise_snapshots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('building', 'frozen', 'failed', 'retired')",
            name="ck_bid_enterprise_snapshots_status",
        ),
        CheckConstraint(
            "((status IN ('building', 'failed')) "
            "OR (status IN ('frozen', 'retired') AND snapshot_hash IS NOT NULL "
            "AND frozen_by IS NOT NULL AND frozen_at IS NOT NULL))",
            name="ck_bid_enterprise_snapshots_freeze",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_enterprise_snapshots_row_version"),
        UniqueConstraint("version", name="uq_bid_enterprise_snapshots_version"),
        UniqueConstraint("snapshot_hash", name="uq_bid_enterprise_snapshots_hash"),
        Index("ix_bid_enterprise_snapshots_status", "status"),
        Index("ix_bid_enterprise_snapshots_as_of", "as_of"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    as_of = Column(DateTime(timezone=True), nullable=False)
    snapshot_hash = Column(String(64), nullable=True)
    source_catalog_version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="building", server_default="building")
    error_code = Column(String(100), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    frozen_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidEnterpriseSnapshotRecord(Base):
    __tablename__ = "bid_enterprise_snapshot_records"
    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_bid_enterprise_snapshot_records_validity",
        ),
        UniqueConstraint(
            "snapshot_id",
            "record_type",
            "source_record_id",
            "source_version",
            name="uq_bid_enterprise_snapshot_records_source",
        ),
        UniqueConstraint(
            "snapshot_id",
            "payload_hash",
            name="uq_bid_enterprise_snapshot_records_payload",
        ),
        Index("ix_bid_enterprise_snapshot_records_type", "snapshot_id", "record_type"),
        Index("ix_bid_enterprise_snapshot_records_source", "record_type", "source_record_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    snapshot_id = Column(
        String(36),
        ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    record_type = Column(String(64), nullable=False)
    source_record_id = Column(String(128), nullable=False)
    source_version = Column(String(64), nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    source_status = Column(String(32), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    object_ref = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidRuleSet(Base):
    __tablename__ = "bid_rule_sets"
    __table_args__ = _artifact_table_args(
        __tablename__,
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from",
            name="ck_bid_rule_sets_effective_window",
        ),
        CheckConstraint(
            "status = 'draft' OR effective_from IS NOT NULL",
            name="ck_bid_rule_sets_active_effective_from",
        ),
        Index("ix_bid_rule_sets_effective", "effective_from", "effective_to"),
    )

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    active_slot_key = Column(String(32), nullable=True)
    artifact_ref = Column(String(512), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    authored_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    change_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    test_cases_ref = Column(String(512), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidFactCatalogVersion(Base):
    __tablename__ = "bid_fact_catalog_versions"
    __table_args__ = _artifact_table_args(__tablename__)

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    active_slot_key = Column(String(32), nullable=True)
    artifact_ref = Column(String(512), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    schema_version = Column(String(64), nullable=False)
    authored_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    change_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidPromptBundle(Base):
    __tablename__ = "bid_prompt_bundles"
    __table_args__ = _artifact_table_args(__tablename__)

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    active_slot_key = Column(String(32), nullable=True)
    artifact_ref = Column(String(512), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    bundle_schema_version = Column(String(64), nullable=False)
    authored_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    change_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidToolRegistryVersion(Base):
    __tablename__ = "bid_tool_registry_versions"
    __table_args__ = _artifact_table_args(__tablename__)

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    active_slot_key = Column(String(32), nullable=True)
    artifact_ref = Column(String(512), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    registry_schema_version = Column(String(64), nullable=False)
    authored_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    change_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidModelProfileVersion(Base):
    __tablename__ = "bid_model_profile_versions"
    __table_args__ = _artifact_table_args(__tablename__)

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    active_slot_key = Column(String(32), nullable=True)
    artifact_ref = Column(String(512), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    role_routing_json = Column(JSON, nullable=False)
    provider_identifiers_json = Column(JSON, nullable=False)
    model_identifiers_json = Column(JSON, nullable=False)
    authored_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    change_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidFormulaCatalogVersion(Base):
    __tablename__ = "bid_formula_catalog_versions"
    __table_args__ = _artifact_table_args(__tablename__)

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    active_slot_key = Column(String(32), nullable=True)
    artifact_ref = Column(String(512), nullable=False)
    artifact_hash = Column(String(64), nullable=False)
    rounding_policy_json = Column(JSON, nullable=False)
    authored_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    change_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
