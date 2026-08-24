"""add MVP-1 fact assertion and resolution authority

Revision ID: 20260813_0100
Revises: 20260813_0099
Create Date: 2026-08-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260813_0100"
down_revision: Union[str, None] = "20260813_0099"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_fact_assertions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("assessment_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("source_task_attempt_id", sa.String(36), nullable=False),
        sa.Column("model_result_id", sa.String(36), nullable=True),
        sa.Column("fact_catalog_version_id", sa.String(36), nullable=False),
        sa.Column("fact_slot", sa.String(160), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(80), nullable=False),
        sa.Column("value_type", sa.String(48), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), server_default="candidate", nullable=False),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assertion_hash", sa.String(64), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('candidate', 'accepted', 'rejected', 'superseded', 'stale')",
            name="ck_bid_fact_assertions_status",
        ),
        sa.CheckConstraint("scope_type IN ('assessment', 'lot')", name="ck_bid_fact_assertions_scope_type"),
        sa.CheckConstraint(
            "source_type IN ('document', 'enterprise', 'owner_answer', 'system', 'system_scope')",
            name="ck_bid_fact_assertions_source_type",
        ),
        sa.CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_bid_fact_assertions_confidence"),
        sa.ForeignKeyConstraint(["assessment_id", "run_id"], ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"], name="fk_bid_fact_assertions_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_fact_assertions_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id", "source_task_attempt_id"], ["bid_task_attempts.task_id", "bid_task_attempts.id"], name="fk_bid_fact_assertions_attempt", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_result_id"], ["bid_model_results.id"], name="fk_bid_fact_assertions_model_result", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fact_catalog_version_id"], ["bid_fact_catalog_versions.id"], name="fk_bid_fact_assertions_catalog", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_fact_assertions"),
        sa.UniqueConstraint("run_id", "assertion_hash", name="uq_bid_fact_assertions_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_fact_assertions_run_slot", "bid_fact_assertions", ["run_id", "fact_slot", "status"])
    op.create_index("ix_bid_fact_assertions_task", "bid_fact_assertions", ["task_id", "created_at"])

    op.create_table(
        "bid_fact_evidence_links",
        sa.Column("assertion_id", sa.String(36), nullable=False),
        sa.Column("evidence_fragment_id", sa.String(36), nullable=False),
        sa.Column("manifest_id", sa.String(36), nullable=False),
        sa.Column("parse_run_id", sa.String(36), nullable=False),
        sa.Column("document_version_id", sa.String(36), nullable=False),
        sa.Column("evidence_text_hash", sa.String(64), nullable=False),
        sa.Column("locator_hash", sa.String(64), nullable=False),
        sa.Column("context_read", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("link_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assertion_id"], ["bid_fact_assertions.id"], name="fk_bid_fact_evidence_assertion", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_fragment_id"], ["bid_evidence_fragments.id"], name="fk_bid_fact_evidence_fragment", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parse_run_id"], ["bid_document_parse_runs.id"], name="fk_bid_fact_evidence_parse_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["manifest_id", "document_version_id"], ["bid_manifest_documents.manifest_id", "bid_manifest_documents.document_version_id"], name="fk_bid_fact_evidence_manifest_document", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("assertion_id", "evidence_fragment_id", name="pk_bid_fact_evidence_links"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_fact_evidence_fragment", "bid_fact_evidence_links", ["evidence_fragment_id"])
    op.create_index("ix_bid_fact_evidence_manifest", "bid_fact_evidence_links", ["manifest_id", "parse_run_id"])

    op.create_table(
        "bid_fact_coverages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("fact_slot", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("assertion_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("coverage_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('not_assessed', 'unavailable', 'blocked_by_parent', 'missing', 'resolved', 'conflicted', 'not_applicable', 'stale')",
            name="ck_bid_fact_coverages_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_analysis_runs.id"], name="fk_bid_fact_coverages_run", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_fact_coverages"),
        sa.UniqueConstraint("run_id", "fact_slot", name="uq_bid_fact_coverages_slot"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_fact_coverages_run_status", "bid_fact_coverages", ["run_id", "status"])

    op.create_table(
        "bid_resolved_facts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("fact_slot", sa.String(160), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("value_type", sa.String(48), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("source_assertion_ids_json", sa.JSON(), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("resolution_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('supported', 'partial', 'conflicted', 'unknown', 'not_applicable', 'stale')", name="ck_bid_resolved_facts_status"),
        sa.CheckConstraint("scope_type IN ('assessment', 'lot')", name="ck_bid_resolved_facts_scope_type"),
        sa.ForeignKeyConstraint(["run_id"], ["bid_analysis_runs.id"], name="fk_bid_resolved_facts_run", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_resolved_facts"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_resolved_facts_run_id"),
        sa.UniqueConstraint("run_id", "fact_slot", "scope_type", "scope_id", "resolution_hash", name="uq_bid_resolved_facts_version"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_resolved_facts_run_slot", "bid_resolved_facts", ["run_id", "fact_slot", "created_at"])

    op.create_table(
        "bid_resolved_fact_heads",
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("fact_slot", sa.String(160), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(80), nullable=False),
        sa.Column("resolved_fact_id", sa.String(36), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_resolved_fact_heads_row_version"),
        sa.ForeignKeyConstraint(["run_id", "resolved_fact_id"], ["bid_resolved_facts.run_id", "bid_resolved_facts.id"], name="fk_bid_resolved_fact_heads_fact", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "fact_slot", "scope_type", "scope_id", name="pk_bid_resolved_fact_heads"),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0100 guarded downgrade requires an online database connection")
    bind = op.get_bind()
    tables = ("bid_resolved_fact_heads", "bid_resolved_facts", "bid_fact_coverages", "bid_fact_evidence_links", "bid_fact_assertions")
    counts = {table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0) for table in tables}
    if any(counts.values()):
        raise RuntimeError("0100 downgrade would erase immutable fact lineage")
    for table in tables:
        op.drop_table(table)
