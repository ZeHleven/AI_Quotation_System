"""Authoritative facts, gates, decisions, claims, and reports for MVP-1."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
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


class BidFactAssertion(Base):
    __tablename__ = "bid_fact_assertions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'accepted', 'rejected', 'superseded', 'stale')",
            name="ck_bid_fact_assertions_status",
        ),
        CheckConstraint(
            "scope_type IN ('assessment', 'lot')",
            name="ck_bid_fact_assertions_scope_type",
        ),
        CheckConstraint(
            "source_type IN ('document', 'enterprise', 'owner_answer', 'system', 'system_scope')",
            name="ck_bid_fact_assertions_source_type",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_bid_fact_assertions_confidence",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_fact_assertions_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_fact_assertions_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "source_task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_fact_assertions_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "assertion_hash", name="uq_bid_fact_assertions_hash"),
        Index("ix_bid_fact_assertions_run_slot", "run_id", "fact_slot", "status"),
        Index("ix_bid_fact_assertions_task", "task_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    source_task_attempt_id = Column(String(36), nullable=False)
    model_result_id = Column(
        String(36), ForeignKey("bid_model_results.id", ondelete="RESTRICT"), nullable=True
    )
    fact_catalog_version_id = Column(
        String(36),
        ForeignKey("bid_fact_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fact_slot = Column(String(160), nullable=False)
    scope_type = Column(String(24), nullable=False)
    scope_id = Column(String(80), nullable=False)
    value_type = Column(String(48), nullable=False)
    value_json = Column(JSON, nullable=False)
    value_hash = Column(String(64), nullable=False)
    source_type = Column(String(32), nullable=False)
    confidence = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False, default="candidate", server_default="candidate")
    asserted_at = Column(DateTime(timezone=True), nullable=False)
    assertion_hash = Column(String(64), nullable=False)
    reason_codes_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidFactEvidenceLink(Base):
    __tablename__ = "bid_fact_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["manifest_id", "document_version_id"],
            ["bid_manifest_documents.manifest_id", "bid_manifest_documents.document_version_id"],
            name="fk_bid_fact_evidence_manifest_document",
            ondelete="RESTRICT",
        ),
        Index("ix_bid_fact_evidence_fragment", "evidence_fragment_id"),
        Index("ix_bid_fact_evidence_manifest", "manifest_id", "parse_run_id"),
        TABLE_OPTIONS,
    )

    assertion_id = Column(
        String(36),
        ForeignKey("bid_fact_assertions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    evidence_fragment_id = Column(
        String(36),
        ForeignKey("bid_evidence_fragments.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    manifest_id = Column(String(36), nullable=False)
    parse_run_id = Column(
        String(36), ForeignKey("bid_document_parse_runs.id", ondelete="RESTRICT"), nullable=False
    )
    document_version_id = Column(String(36), nullable=False)
    evidence_text_hash = Column(String(64), nullable=False)
    locator_hash = Column(String(64), nullable=False)
    context_read = Column(Boolean, nullable=False, default=True, server_default="1")
    link_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidFactEnterpriseLink(Base):
    """Immutable provenance from an enterprise FactAssertion to a frozen record."""

    __tablename__ = "bid_fact_enterprise_links"
    __table_args__ = (
        Index("ix_bid_fact_enterprise_links_record", "snapshot_record_id"),
        TABLE_OPTIONS,
    )

    assertion_id = Column(
        String(36),
        ForeignKey("bid_fact_assertions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_record_id = Column(
        String(36),
        ForeignKey("bid_enterprise_snapshot_records.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    record_type = Column(String(64), nullable=False)
    source_record_id = Column(String(128), nullable=False)
    source_version = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    link_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidFactCoverage(Base):
    __tablename__ = "bid_fact_coverages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('not_assessed', 'unavailable', 'blocked_by_parent', 'missing', "
            "'resolved', 'conflicted', 'not_applicable', 'stale')",
            name="ck_bid_fact_coverages_status",
        ),
        UniqueConstraint("run_id", "fact_slot", name="uq_bid_fact_coverages_slot"),
        Index("ix_bid_fact_coverages_run_status", "run_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(
        String(36), ForeignKey("bid_analysis_runs.id", ondelete="RESTRICT"), nullable=False
    )
    fact_slot = Column(String(160), nullable=False)
    status = Column(String(24), nullable=False)
    assertion_count = Column(Integer, nullable=False, default=0, server_default="0")
    reason_codes_json = Column(JSON, nullable=False)
    coverage_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidResolvedFact(Base):
    __tablename__ = "bid_resolved_facts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('supported', 'partial', 'conflicted', 'unknown', 'not_applicable', 'stale')",
            name="ck_bid_resolved_facts_status",
        ),
        CheckConstraint(
            "scope_type IN ('assessment', 'lot')",
            name="ck_bid_resolved_facts_scope_type",
        ),
        UniqueConstraint(
            "run_id", "fact_slot", "scope_type", "scope_id", "resolution_hash",
            name="uq_bid_resolved_facts_version",
        ),
        UniqueConstraint("run_id", "id", name="uq_bid_resolved_facts_run_id"),
        Index("ix_bid_resolved_facts_run_slot", "run_id", "fact_slot", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(
        String(36), ForeignKey("bid_analysis_runs.id", ondelete="RESTRICT"), nullable=False
    )
    fact_slot = Column(String(160), nullable=False)
    scope_type = Column(String(24), nullable=False)
    scope_id = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False)
    value_type = Column(String(48), nullable=True)
    value_json = Column(JSON, nullable=True)
    source_assertion_ids_json = Column(JSON, nullable=False)
    reason_codes_json = Column(JSON, nullable=False)
    resolution_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidResolvedFactHead(Base):
    __tablename__ = "bid_resolved_fact_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "resolved_fact_id"],
            ["bid_resolved_facts.run_id", "bid_resolved_facts.id"],
            name="fk_bid_resolved_fact_heads_fact",
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_resolved_fact_heads_row_version"),
        TABLE_OPTIONS,
    )

    run_id = Column(String(36), primary_key=True)
    fact_slot = Column(String(160), primary_key=True)
    scope_type = Column(String(24), primary_key=True)
    scope_id = Column(String(80), primary_key=True)
    resolved_fact_id = Column(String(36), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidHardGateResult(Base):
    __tablename__ = "bid_hard_gate_results"
    __table_args__ = (
        CheckConstraint(
            "gate_code IN ('HG01', 'HG02', 'HG03', 'HG04', 'HG05', 'HG06', 'HG07')",
            name="ck_bid_hard_gate_results_code",
        ),
        CheckConstraint(
            "status IN ('pass', 'fail', 'unknown', 'not_applicable')",
            name="ck_bid_hard_gate_results_status",
        ),
        CheckConstraint(
            "severity IN ('block', 'warn', 'info')",
            name="ck_bid_hard_gate_results_severity",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_hard_gate_results_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "gate_code", name="uq_bid_hard_gate_results_gate"),
        Index("ix_bid_hard_gate_results_run", "run_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    gate_code = Column(String(8), nullable=False)
    status = Column(String(24), nullable=False)
    severity = Column(String(16), nullable=False)
    reason_codes_json = Column(JSON, nullable=False)
    input_fact_ids_json = Column(JSON, nullable=False)
    details_json = Column(JSON, nullable=False)
    result_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidPreliminaryDecision(Base):
    __tablename__ = "bid_preliminary_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('bid', 'no_bid', 'conditional', 'insufficient')",
            name="ck_bid_preliminary_decisions_decision",
        ),
        CheckConstraint(
            "investment_level IN ('low', 'medium', 'high', 'hold')",
            name="ck_bid_preliminary_decisions_investment",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_preliminary_decisions_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "id", name="uq_bid_preliminary_decisions_run_id"),
        UniqueConstraint("run_id", name="uq_bid_preliminary_decisions_run"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    rule_set_id = Column(String(36), ForeignKey("bid_rule_sets.id", ondelete="RESTRICT"), nullable=False)
    formula_catalog_version_id = Column(
        String(36),
        ForeignKey("bid_formula_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision = Column(String(24), nullable=False)
    investment_level = Column(String(16), nullable=False)
    failed_gate_count = Column(Integer, nullable=False)
    unknown_gate_count = Column(Integer, nullable=False)
    unknown_fact_count = Column(Integer, nullable=False)
    summary = Column(Text, nullable=False)
    reason_codes_json = Column(JSON, nullable=False)
    input_hash = Column(String(64), nullable=False)
    decision_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidReportClaim(Base):
    __tablename__ = "bid_report_claims"
    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('fact', 'calculation', 'inference', 'recommendation')",
            name="ck_bid_report_claims_type",
        ),
        CheckConstraint(
            "status IN ('candidate', 'valid', 'invalid', 'stale')",
            name="ck_bid_report_claims_status",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_report_claims_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "claim_order", name="uq_bid_report_claims_order"),
        UniqueConstraint("run_id", "claim_hash", name="uq_bid_report_claims_hash"),
        Index("ix_bid_report_claims_run", "run_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    model_result_id = Column(
        String(36), ForeignKey("bid_model_results.id", ondelete="RESTRICT"), nullable=True
    )
    claim_order = Column(Integer, nullable=False)
    claim_type = Column(String(24), nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String(24), nullable=False)
    support_fact_ids_json = Column(JSON, nullable=False)
    support_gate_ids_json = Column(JSON, nullable=False)
    premise_or_trigger = Column(Text, nullable=True)
    reason_codes_json = Column(JSON, nullable=False)
    claim_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidClaimCitation(Base):
    __tablename__ = "bid_claim_citations"
    __table_args__ = (
        UniqueConstraint(
            "claim_id", "evidence_fragment_id", name="uq_bid_claim_citations_fragment"
        ),
        Index("ix_bid_claim_citations_fragment", "evidence_fragment_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    claim_id = Column(
        String(36), ForeignKey("bid_report_claims.id", ondelete="RESTRICT"), nullable=False
    )
    assertion_id = Column(
        String(36), ForeignKey("bid_fact_assertions.id", ondelete="RESTRICT"), nullable=True
    )
    evidence_fragment_id = Column(
        String(36),
        ForeignKey("bid_evidence_fragments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_version_id = Column(String(36), nullable=False)
    locator_json = Column(JSON, nullable=False)
    excerpt = Column(Text, nullable=False)
    excerpt_hash = Column(String(64), nullable=False)
    citation_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidReportValidation(Base):
    __tablename__ = "bid_report_validations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('passed', 'failed', 'stale')",
            name="ck_bid_report_validations_status",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_report_validations_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "id", name="uq_bid_report_validations_run_id"),
        UniqueConstraint("run_id", name="uq_bid_report_validations_run"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), nullable=False)
    status = Column(String(24), nullable=False)
    validator_version = Column(String(80), nullable=False)
    checks_json = Column(JSON, nullable=False)
    input_hash = Column(String(64), nullable=False)
    result_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidPreliminaryReport(Base):
    __tablename__ = "bid_preliminary_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'invalid', 'stale')",
            name="ck_bid_preliminary_reports_status",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_preliminary_reports_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "decision_id"],
            ["bid_preliminary_decisions.run_id", "bid_preliminary_decisions.id"],
            name="fk_bid_preliminary_reports_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "validation_id"],
            ["bid_report_validations.run_id", "bid_report_validations.id"],
            name="fk_bid_preliminary_reports_validation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", name="uq_bid_preliminary_reports_run"),
        UniqueConstraint(
            "assessment_id", "report_version", name="uq_bid_preliminary_reports_version"
        ),
        Index("ix_bid_preliminary_reports_assessment", "assessment_id", "generated_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    decision_id = Column(String(36), nullable=False)
    validation_id = Column(String(36), nullable=False)
    report_version = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False)
    title = Column(String(300), nullable=False)
    executive_summary = Column(Text, nullable=False)
    report_json = Column(JSON, nullable=False)
    report_hash = Column(String(64), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
