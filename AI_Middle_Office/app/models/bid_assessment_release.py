"""Phase 4C-3 immutable business acceptance and MVP release authority."""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class BidMvpReleaseCandidate(Base):
    """One immutable reviewer acceptance bound to one completed Run."""

    __tablename__ = "bid_mvp_release_candidates"
    __table_args__ = (
        CheckConstraint(
            "status = 'frozen'",
            name="ck_bid_mvp_release_candidates_status",
        ),
        CheckConstraint(
            "acceptance_outcome IN ('accepted', 'accepted_with_follow_up')",
            name="ck_bid_mvp_release_candidates_outcome",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_mvp_release_candidates_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "run_validation_id"],
            ["bid_run_validations.run_id", "bid_run_validations.id"],
            name="fk_bid_mvp_release_candidates_validation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", name="uq_bid_mvp_release_candidates_run"),
        UniqueConstraint("version", name="uq_bid_mvp_release_candidates_version"),
        UniqueConstraint(
            "candidate_hash",
            name="uq_bid_mvp_release_candidates_candidate_hash",
        ),
        UniqueConstraint("release_hash", name="uq_bid_mvp_release_candidates_hash"),
        Index(
            "ix_bid_mvp_release_candidates_assessment",
            "assessment_id",
            "reviewed_at",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    report_id = Column(
        String(36),
        ForeignKey("bid_preliminary_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_validation_id = Column(String(36), nullable=False)
    enterprise_snapshot_id = Column(
        String(36),
        ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(String(24), nullable=False, default="frozen", server_default="frozen")
    acceptance_outcome = Column(String(32), nullable=False)
    reviewer_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    review_note = Column(Text, nullable=False)
    review_json = Column(JSON, nullable=False)
    source_hashes_json = Column(JSON, nullable=False)
    manifest_json = Column(JSON, nullable=False)
    candidate_hash = Column(String(64), nullable=False)
    release_hash = Column(String(64), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEnterpriseEvidenceItem(Base):
    """One immutable, content-addressed enterprise source file."""

    __tablename__ = "bid_enterprise_evidence_items"
    __table_args__ = (
        CheckConstraint("status = 'frozen'", name="ck_bid_ent_evidence_items_status"),
        CheckConstraint(
            "evidence_class IN ('official_document', 'internal_system', 'audited_record')",
            name="ck_bid_ent_evidence_items_class",
        ),
        CheckConstraint("size_bytes > 0", name="ck_bid_ent_evidence_items_size"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_bid_ent_evidence_items_validity",
        ),
        UniqueConstraint(
            "source_record_id",
            "source_version",
            "content_sha256",
            name="uq_bid_ent_evidence_items_source",
        ),
        UniqueConstraint("item_hash", name="uq_bid_ent_evidence_items_hash"),
        Index(
            "ix_bid_ent_evidence_items_source",
            "source_record_id",
            "source_version",
        ),
        Index("ix_bid_ent_evidence_items_uploaded", "uploaded_at", "id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    status = Column(String(24), nullable=False, default="frozen", server_default="frozen")
    evidence_class = Column(String(40), nullable=False)
    source_record_id = Column(String(128), nullable=False)
    source_version = Column(String(64), nullable=False)
    source_label = Column(String(300), nullable=False)
    original_filename = Column(String(500), nullable=False)
    mime_type = Column(String(160), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    item_hash = Column(String(64), nullable=False)
    object_ref = Column(String(512), nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    uploaded_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEnterpriseEvidencePackage(Base):
    """Immutable selection and I01-I11 mapping of enterprise evidence items."""

    __tablename__ = "bid_enterprise_evidence_packages"
    __table_args__ = (
        CheckConstraint("status = 'frozen'", name="ck_bid_ent_evidence_packages_status"),
        UniqueConstraint("version", name="uq_bid_ent_evidence_packages_version"),
        UniqueConstraint(
            "candidate_hash",
            name="uq_bid_ent_evidence_packages_candidate",
        ),
        UniqueConstraint("package_hash", name="uq_bid_ent_evidence_packages_hash"),
        Index("ix_bid_ent_evidence_packages_frozen", "frozen_at", "id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="frozen", server_default="frozen")
    package_label = Column(String(300), nullable=False)
    change_note = Column(Text, nullable=False)
    as_of = Column(DateTime(timezone=True), nullable=False)
    manifest_json = Column(JSON, nullable=False)
    candidate_hash = Column(String(64), nullable=False)
    package_hash = Column(String(64), nullable=False)
    frozen_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    frozen_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEnterpriseEvidencePackageItem(Base):
    """One explicit evidence-item to capability-slot mapping."""

    __tablename__ = "bid_enterprise_evidence_package_items"
    __table_args__ = (
        CheckConstraint(
            "slot_code IN ('I01','I02','I03','I04','I05','I06','I07','I08','I09','I10','I11')",
            name="ck_bid_ent_evidence_pkg_items_slot",
        ),
        UniqueConstraint(
            "package_id",
            "evidence_item_id",
            "slot_code",
            name="uq_bid_ent_evidence_pkg_items_map",
        ),
        Index("ix_bid_ent_evidence_pkg_items_slot", "package_id", "slot_code"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    package_id = Column(
        String(36),
        ForeignKey("bid_enterprise_evidence_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_item_id = Column(
        String(36),
        ForeignKey("bid_enterprise_evidence_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    slot_code = Column(String(3), nullable=False)
    mapping_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidEnterpriseBusinessBaseline(Base):
    """Immutable reviewer attestation that a frozen snapshot is business-usable."""

    __tablename__ = "bid_enterprise_business_baselines"
    __table_args__ = (
        CheckConstraint(
            "status = 'frozen'",
            name="ck_bid_enterprise_business_baselines_status",
        ),
        CheckConstraint(
            "verification_outcome IN ('verified', 'verified_with_follow_up')",
            name="ck_bid_enterprise_business_baselines_outcome",
        ),
        UniqueConstraint(
            "snapshot_id",
            name="uq_bid_enterprise_business_baselines_snapshot",
        ),
        UniqueConstraint(
            "version",
            name="uq_bid_enterprise_business_baselines_version",
        ),
        UniqueConstraint(
            "candidate_hash",
            name="uq_bid_enterprise_business_baselines_candidate_hash",
        ),
        UniqueConstraint(
            "baseline_hash",
            name="uq_bid_enterprise_business_baselines_hash",
        ),
        Index(
            "ix_bid_enterprise_business_baselines_reviewed",
            "reviewed_at",
            "snapshot_id",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    version = Column(String(64), nullable=False)
    snapshot_id = Column(
        String(36),
        ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_package_id = Column(
        String(36),
        ForeignKey("bid_enterprise_evidence_packages.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_package_hash = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False, default="frozen", server_default="frozen")
    verification_outcome = Column(String(40), nullable=False)
    reviewer_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    review_note = Column(Text, nullable=False)
    slot_reviews_json = Column(JSON, nullable=False)
    source_hashes_json = Column(JSON, nullable=False)
    candidate_hash = Column(String(64), nullable=False)
    baseline_hash = Column(String(64), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidHardGateComparisonBaseline(Base):
    """Immutable, assessment-scoped verified facts used by HG01-HG07."""

    __tablename__ = "bid_hard_gate_comparison_baselines"
    __table_args__ = (
        CheckConstraint(
            "status = 'frozen'",
            name="ck_bid_hg_comparison_baselines_status",
        ),
        CheckConstraint(
            "verification_outcome IN ('verified', 'verified_with_follow_up')",
            name="ck_bid_hg_comparison_baselines_outcome",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "source_run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_hg_comparison_baselines_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "scope_id"],
            ["bid_assessment_scopes.assessment_id", "bid_assessment_scopes.id"],
            name="fk_bid_hg_comparison_baselines_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_hg_comparison_baselines_manifest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("version", name="uq_bid_hg_comparison_baselines_version"),
        UniqueConstraint(
            "candidate_hash",
            name="uq_bid_hg_comparison_baselines_candidate",
        ),
        UniqueConstraint(
            "baseline_hash",
            name="uq_bid_hg_comparison_baselines_hash",
        ),
        Index(
            "ix_bid_hg_comparison_baselines_assessment",
            "assessment_id",
            "reviewed_at",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    version = Column(String(72), nullable=False)
    assessment_id = Column(String(36), nullable=False)
    source_run_id = Column(String(36), nullable=False)
    scope_id = Column(String(36), nullable=False)
    manifest_id = Column(String(36), nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    scope_hash = Column(String(64), nullable=False)
    enterprise_snapshot_id = Column(
        String(36),
        ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    enterprise_snapshot_hash = Column(String(64), nullable=False)
    business_baseline_id = Column(
        String(36),
        ForeignKey("bid_enterprise_business_baselines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    business_baseline_hash = Column(String(64), nullable=False)
    evidence_package_id = Column(
        String(36),
        ForeignKey("bid_enterprise_evidence_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_package_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="frozen", server_default="frozen")
    verification_outcome = Column(String(40), nullable=False)
    reviewer_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    review_note = Column(Text, nullable=False)
    facts_json = Column(JSON, nullable=False)
    source_hashes_json = Column(JSON, nullable=False)
    candidate_hash = Column(String(64), nullable=False)
    baseline_hash = Column(String(64), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidHardGateComparisonEvidenceLink(Base):
    """Immutable evidence lineage for one verified comparable fact."""

    __tablename__ = "bid_hard_gate_comparison_evidence_links"
    __table_args__ = (
        CheckConstraint(
            "source_side IN ('tender', 'enterprise')",
            name="ck_bid_hg_comparison_links_side",
        ),
        CheckConstraint(
            "evidence_kind IN ('tender_atom', 'enterprise_item')",
            name="ck_bid_hg_comparison_links_kind",
        ),
        CheckConstraint(
            "((evidence_kind = 'enterprise_item' AND evidence_item_id IS NOT NULL "
            "AND evidence_fragment_id IS NULL) OR "
            "(evidence_kind = 'tender_atom' AND evidence_fragment_id IS NOT NULL "
            "AND evidence_item_id IS NULL))",
            name="ck_bid_hg_comparison_links_target",
        ),
        UniqueConstraint(
            "comparison_baseline_id",
            "fact_slot",
            "evidence_kind",
            "evidence_identity",
            name="uq_bid_hg_comparison_links_evidence",
        ),
        Index(
            "ix_bid_hg_comparison_links_fact",
            "comparison_baseline_id",
            "fact_slot",
        ),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    comparison_baseline_id = Column(
        String(36),
        ForeignKey("bid_hard_gate_comparison_baselines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fact_slot = Column(String(160), nullable=False)
    source_side = Column(String(16), nullable=False)
    evidence_kind = Column(String(32), nullable=False)
    evidence_identity = Column(String(80), nullable=False)
    evidence_item_id = Column(
        String(36),
        ForeignKey("bid_enterprise_evidence_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_fragment_id = Column(
        String(36),
        ForeignKey("bid_evidence_fragments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    document_version_id = Column(String(36), nullable=True)
    parse_run_id = Column(String(36), nullable=True)
    evidence_hash = Column(String(64), nullable=False)
    locator_hash = Column(String(64), nullable=True)
    link_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidFactComparisonLink(Base):
    """Run FactAssertion lineage to one frozen comparable fact."""

    __tablename__ = "bid_fact_comparison_links"
    __table_args__ = (
        Index(
            "ix_bid_fact_comparison_links_baseline",
            "comparison_baseline_id",
            "fact_slot",
        ),
        TABLE_OPTIONS,
    )

    assertion_id = Column(
        String(36),
        ForeignKey("bid_fact_assertions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    comparison_baseline_id = Column(
        String(36),
        ForeignKey("bid_hard_gate_comparison_baselines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fact_slot = Column(String(160), nullable=False)
    fact_hash = Column(String(64), nullable=False)
    link_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
