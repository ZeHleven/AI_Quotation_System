"""add MVP-1 gates decision claims and preliminary report authority

Revision ID: 20260813_0101
Revises: 20260813_0100
Create Date: 2026-08-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260813_0101"
down_revision: Union[str, None] = "20260813_0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "bid_hard_gate_results",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False), sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("gate_code", sa.String(8), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False), sa.Column("input_fact_ids_json", sa.JSON(), nullable=False), sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("gate_code IN ('HG01', 'HG02', 'HG03', 'HG04', 'HG05', 'HG06', 'HG07')", name="ck_bid_hard_gate_results_code"),
        sa.CheckConstraint("status IN ('pass', 'fail', 'unknown', 'not_applicable')", name="ck_bid_hard_gate_results_status"),
        sa.CheckConstraint("severity IN ('block', 'warn', 'info')", name="ck_bid_hard_gate_results_severity"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_hard_gate_results_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_hard_gate_results"), sa.UniqueConstraint("run_id", "gate_code", name="uq_bid_hard_gate_results_gate"), **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_hard_gate_results_run", "bid_hard_gate_results", ["run_id", "status"])
    op.create_table(
        "bid_preliminary_decisions",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False), sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("rule_set_id", sa.String(36), nullable=False), sa.Column("formula_catalog_version_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False), sa.Column("investment_level", sa.String(16), nullable=False),
        sa.Column("failed_gate_count", sa.Integer(), nullable=False), sa.Column("unknown_gate_count", sa.Integer(), nullable=False), sa.Column("unknown_fact_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False), sa.Column("reason_codes_json", sa.JSON(), nullable=False), sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("decision IN ('bid', 'no_bid', 'conditional', 'insufficient')", name="ck_bid_preliminary_decisions_decision"),
        sa.CheckConstraint("investment_level IN ('low', 'medium', 'high', 'hold')", name="ck_bid_preliminary_decisions_investment"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_preliminary_decisions_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_set_id"], ["bid_rule_sets.id"], name="fk_bid_preliminary_decisions_rule_set", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["formula_catalog_version_id"], ["bid_formula_catalog_versions.id"], name="fk_bid_preliminary_decisions_formula", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_preliminary_decisions"), sa.UniqueConstraint("run_id", "id", name="uq_bid_preliminary_decisions_run_id"), sa.UniqueConstraint("run_id", name="uq_bid_preliminary_decisions_run"), **TABLE_OPTIONS,
    )
    op.create_table(
        "bid_report_claims",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False), sa.Column("task_id", sa.String(36), nullable=False), sa.Column("model_result_id", sa.String(36), nullable=True),
        sa.Column("claim_order", sa.Integer(), nullable=False), sa.Column("claim_type", sa.String(24), nullable=False), sa.Column("text", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("support_fact_ids_json", sa.JSON(), nullable=False), sa.Column("support_gate_ids_json", sa.JSON(), nullable=False), sa.Column("premise_or_trigger", sa.Text(), nullable=True),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False), sa.Column("claim_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("claim_type IN ('fact', 'calculation', 'inference', 'recommendation')", name="ck_bid_report_claims_type"),
        sa.CheckConstraint("status IN ('candidate', 'valid', 'invalid', 'stale')", name="ck_bid_report_claims_status"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_report_claims_task", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_result_id"], ["bid_model_results.id"], name="fk_bid_report_claims_model_result", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_report_claims"), sa.UniqueConstraint("run_id", "claim_order", name="uq_bid_report_claims_order"), sa.UniqueConstraint("run_id", "claim_hash", name="uq_bid_report_claims_hash"), **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_report_claims_run", "bid_report_claims", ["run_id", "status"])
    op.create_table(
        "bid_claim_citations",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("claim_id", sa.String(36), nullable=False), sa.Column("assertion_id", sa.String(36), nullable=True),
        sa.Column("evidence_fragment_id", sa.String(36), nullable=False), sa.Column("document_version_id", sa.String(36), nullable=False), sa.Column("locator_json", sa.JSON(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False), sa.Column("excerpt_hash", sa.String(64), nullable=False), sa.Column("citation_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["bid_report_claims.id"], name="fk_bid_claim_citations_claim", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assertion_id"], ["bid_fact_assertions.id"], name="fk_bid_claim_citations_assertion", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_fragment_id"], ["bid_evidence_fragments.id"], name="fk_bid_claim_citations_fragment", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_claim_citations"), sa.UniqueConstraint("claim_id", "evidence_fragment_id", name="uq_bid_claim_citations_fragment"), **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_claim_citations_fragment", "bid_claim_citations", ["evidence_fragment_id"])
    op.create_table(
        "bid_report_validations",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False), sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("validator_version", sa.String(80), nullable=False), sa.Column("checks_json", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("result_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('passed', 'failed', 'stale')", name="ck_bid_report_validations_status"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["bid_tasks.run_id", "bid_tasks.id"], name="fk_bid_report_validations_task", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_report_validations"), sa.UniqueConstraint("run_id", "id", name="uq_bid_report_validations_run_id"), sa.UniqueConstraint("run_id", name="uq_bid_report_validations_run"), **TABLE_OPTIONS,
    )
    op.create_table(
        "bid_preliminary_reports",
        sa.Column("id", sa.String(36), nullable=False), sa.Column("assessment_id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("decision_id", sa.String(36), nullable=False), sa.Column("validation_id", sa.String(36), nullable=False), sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("title", sa.String(300), nullable=False), sa.Column("executive_summary", sa.Text(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False), sa.Column("report_hash", sa.String(64), nullable=False), sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('ready', 'invalid', 'stale')", name="ck_bid_preliminary_reports_status"),
        sa.ForeignKeyConstraint(["assessment_id", "run_id"], ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"], name="fk_bid_preliminary_reports_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id", "decision_id"], ["bid_preliminary_decisions.run_id", "bid_preliminary_decisions.id"], name="fk_bid_preliminary_reports_decision", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id", "validation_id"], ["bid_report_validations.run_id", "bid_report_validations.id"], name="fk_bid_preliminary_reports_validation", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_bid_preliminary_reports"), sa.UniqueConstraint("run_id", name="uq_bid_preliminary_reports_run"), sa.UniqueConstraint("assessment_id", "report_version", name="uq_bid_preliminary_reports_version"), **TABLE_OPTIONS,
    )
    op.create_index("ix_bid_preliminary_reports_assessment", "bid_preliminary_reports", ["assessment_id", "generated_at"])


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0101 guarded downgrade requires an online database connection")
    bind = op.get_bind()
    tables = ("bid_preliminary_reports", "bid_report_validations", "bid_claim_citations", "bid_report_claims", "bid_preliminary_decisions", "bid_hard_gate_results")
    counts = {table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0) for table in tables}
    if any(counts.values()):
        raise RuntimeError("0101 downgrade would erase immutable preliminary-report lineage")
    for table in tables:
        op.drop_table(table)
