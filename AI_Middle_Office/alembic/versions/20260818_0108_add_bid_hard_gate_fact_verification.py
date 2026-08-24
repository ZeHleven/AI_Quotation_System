"""add verified hard-gate comparison baseline authority

Revision ID: 20260818_0108
Revises: 20260817_0107
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260818_0108"
down_revision: Union[str, None] = "20260817_0107"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "bid_hard_gate_comparison_baselines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=72), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("enterprise_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("business_baseline_id", sa.String(length=36), nullable=False),
        sa.Column("business_baseline_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_package_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_package_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="frozen", nullable=False),
        sa.Column("verification_outcome", sa.String(length=40), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("source_hashes_json", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("baseline_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status = 'frozen'", name="ck_bid_hg_comparison_baselines_status"),
        sa.CheckConstraint(
            "verification_outcome IN ('verified', 'verified_with_follow_up')",
            name="ck_bid_hg_comparison_baselines_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "source_run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_hg_comparison_baselines_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "scope_id"],
            ["bid_assessment_scopes.assessment_id", "bid_assessment_scopes.id"],
            name="fk_bid_hg_comparison_baselines_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_hg_comparison_baselines_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enterprise_snapshot_id"],
            ["bid_enterprise_snapshots.id"],
            name="fk_bid_hg_comparison_baselines_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["business_baseline_id"],
            ["bid_enterprise_business_baselines.id"],
            name="fk_bid_hg_comparison_baselines_business",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_package_id"],
            ["bid_enterprise_evidence_packages.id"],
            name="fk_bid_hg_comparison_baselines_package",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name="fk_bid_hg_comparison_baselines_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_hard_gate_comparison_baselines"),
        sa.UniqueConstraint("version", name="uq_bid_hg_comparison_baselines_version"),
        sa.UniqueConstraint("candidate_hash", name="uq_bid_hg_comparison_baselines_candidate"),
        sa.UniqueConstraint("baseline_hash", name="uq_bid_hg_comparison_baselines_hash"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_hg_comparison_baselines_assessment",
        "bid_hard_gate_comparison_baselines",
        ["assessment_id", "reviewed_at"],
    )

    op.create_table(
        "bid_hard_gate_comparison_evidence_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("comparison_baseline_id", sa.String(length=36), nullable=False),
        sa.Column("fact_slot", sa.String(length=160), nullable=False),
        sa.Column("source_side", sa.String(length=16), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("evidence_identity", sa.String(length=80), nullable=False),
        sa.Column("evidence_item_id", sa.String(length=36), nullable=True),
        sa.Column("evidence_fragment_id", sa.String(length=36), nullable=True),
        sa.Column("document_version_id", sa.String(length=36), nullable=True),
        sa.Column("parse_run_id", sa.String(length=36), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("locator_hash", sa.String(length=64), nullable=True),
        sa.Column("link_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "source_side IN ('tender', 'enterprise')",
            name="ck_bid_hg_comparison_links_side",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('tender_atom', 'enterprise_item')",
            name="ck_bid_hg_comparison_links_kind",
        ),
        sa.CheckConstraint(
            "((evidence_kind = 'enterprise_item' AND evidence_item_id IS NOT NULL "
            "AND evidence_fragment_id IS NULL) OR "
            "(evidence_kind = 'tender_atom' AND evidence_fragment_id IS NOT NULL "
            "AND evidence_item_id IS NULL))",
            name="ck_bid_hg_comparison_links_target",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_baseline_id"],
            ["bid_hard_gate_comparison_baselines.id"],
            name="fk_bid_hg_comparison_links_baseline",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id"],
            ["bid_enterprise_evidence_items.id"],
            name="fk_bid_hg_comparison_links_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_fragment_id"],
            ["bid_evidence_fragments.id"],
            name="fk_bid_hg_comparison_links_atom",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_hard_gate_comparison_evidence_links"),
        sa.UniqueConstraint(
            "comparison_baseline_id", "fact_slot", "evidence_kind", "evidence_identity",
            name="uq_bid_hg_comparison_links_evidence",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_hg_comparison_links_fact",
        "bid_hard_gate_comparison_evidence_links",
        ["comparison_baseline_id", "fact_slot"],
    )

    op.create_table(
        "bid_fact_comparison_links",
        sa.Column("assertion_id", sa.String(length=36), nullable=False),
        sa.Column("comparison_baseline_id", sa.String(length=36), nullable=False),
        sa.Column("fact_slot", sa.String(length=160), nullable=False),
        sa.Column("fact_hash", sa.String(length=64), nullable=False),
        sa.Column("link_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assertion_id"],
            ["bid_fact_assertions.id"],
            name="fk_bid_fact_comparison_links_assertion",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["comparison_baseline_id"],
            ["bid_hard_gate_comparison_baselines.id"],
            name="fk_bid_fact_comparison_links_baseline",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("assertion_id", name="pk_bid_fact_comparison_links"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_fact_comparison_links_baseline",
        "bid_fact_comparison_links",
        ["comparison_baseline_id", "fact_slot"],
    )

    with op.batch_alter_table("bid_analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column("hard_gate_comparison_baseline_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("hard_gate_comparison_baseline_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_bid_analysis_runs_hg_comparison",
            "bid_hard_gate_comparison_baselines",
            ["hard_gate_comparison_baseline_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_bid_analysis_runs_hg_comparison_pair",
            "((hard_gate_comparison_baseline_id IS NULL AND "
            "hard_gate_comparison_baseline_hash IS NULL) OR "
            "(hard_gate_comparison_baseline_id IS NOT NULL AND "
            "hard_gate_comparison_baseline_hash IS NOT NULL))",
        )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0108 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable fact-verification checks"
        )
    bind = op.get_bind()
    bound_runs = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM bid_analysis_runs WHERE "
                "hard_gate_comparison_baseline_id IS NOT NULL OR "
                "hard_gate_comparison_baseline_hash IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    if bound_runs:
        raise RuntimeError("0108 downgrade would erase Run comparison-baseline lineage")
    for table_name in (
        "bid_fact_comparison_links",
        "bid_hard_gate_comparison_evidence_links",
        "bid_hard_gate_comparison_baselines",
    ):
        count = int(
            bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
        )
        if count:
            raise RuntimeError("0108 downgrade would erase immutable fact-verification lineage")

    with op.batch_alter_table("bid_analysis_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_bid_analysis_runs_hg_comparison_pair",
            type_="check",
        )
        batch_op.drop_constraint("fk_bid_analysis_runs_hg_comparison", type_="foreignkey")
        batch_op.drop_column("hard_gate_comparison_baseline_hash")
        batch_op.drop_column("hard_gate_comparison_baseline_id")
    op.drop_table("bid_fact_comparison_links")
    op.drop_table("bid_hard_gate_comparison_evidence_links")
    op.drop_table("bid_hard_gate_comparison_baselines")
