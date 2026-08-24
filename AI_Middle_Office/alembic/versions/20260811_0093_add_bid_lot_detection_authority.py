"""add Phase 2 Manifest-scoped lot detection authority

Revision ID: 20260811_0093
Revises: 20260811_0092
Create Date: 2026-08-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260811_0093"
down_revision: Union[str, None] = "20260811_0092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _require_empty_candidate_skeleton() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0093 requires an online local/development database so the legacy "
            "candidate skeleton can be proven empty before lineage constraints change"
        )
    count = int(
        op.get_bind().execute(
            sa.text("SELECT COUNT(*) FROM bid_lot_candidates")
        ).scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            "0093 refuses to fabricate detection/evidence lineage for existing "
            "bid_lot_candidates; export and explicitly reconcile those rows first"
        )


def upgrade() -> None:
    _require_empty_candidate_skeleton()

    op.create_table(
        "bid_lot_detection_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("parse_set_hash", sa.String(length=64), nullable=False),
        sa.Column("detector_version", sa.String(length=80), nullable=False),
        sa.Column("rule_set_version", sa.String(length=80), nullable=False),
        sa.Column("normalizer_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'stale')",
            name="ck_bid_lot_detection_runs_status",
        ),
        sa.CheckConstraint("candidate_count >= 0", name="ck_bid_lot_detection_runs_count"),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_lot_detection_runs_row_version"),
        sa.CheckConstraint(
            "((status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'stale') AND finished_at IS NOT NULL))",
            name="ck_bid_lot_detection_runs_timestamps",
        ),
        sa.CheckConstraint(
            "((status = 'succeeded' AND result_hash IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'stale')))",
            name="ck_bid_lot_detection_runs_result",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"],
            ["bid_document_manifests.id"],
            name="fk_bid_lot_detection_runs_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_lot_detection_runs"),
        sa.UniqueConstraint("manifest_id", "input_hash", name="uq_bid_lot_detection_runs_input"),
        sa.UniqueConstraint("manifest_id", "id", name="uq_bid_lot_detection_runs_manifest_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_lot_detection_runs_manifest_status",
        "bid_lot_detection_runs",
        ["manifest_id", "status"],
    )
    op.create_index(
        "ix_bid_lot_detection_runs_status_requested",
        "bid_lot_detection_runs",
        ["status", "requested_at"],
    )

    op.create_table(
        "bid_lot_detection_heads",
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("current_run_id", sa.String(length=36), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_bid_lot_detection_heads_row_version"),
        sa.ForeignKeyConstraint(
            ["manifest_id", "current_run_id"],
            ["bid_lot_detection_runs.manifest_id", "bid_lot_detection_runs.id"],
            name="fk_bid_lot_detection_heads_current_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("manifest_id", name="pk_bid_lot_detection_heads"),
        sa.UniqueConstraint("current_run_id", name="uq_bid_lot_detection_heads_current_run"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_lot_detection_heads_current_run",
        "bid_lot_detection_heads",
        ["current_run_id"],
    )

    op.create_table(
        "bid_lot_detection_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("error_class", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_no >= 1", name="ck_bid_lot_detection_attempts_number"),
        sa.CheckConstraint(
            "status IN ('leased', 'running', 'succeeded', 'failed', 'expired', 'cancelled')",
            name="ck_bid_lot_detection_attempts_status",
        ),
        sa.CheckConstraint("fencing_token >= 1", name="ck_bid_lot_detection_attempts_fencing"),
        sa.CheckConstraint(
            "lease_owner IS NOT NULL AND lease_until IS NOT NULL",
            name="ck_bid_lot_detection_attempts_lease",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_bid_lot_detection_attempts_time_order",
        ),
        sa.CheckConstraint(
            "((status IN ('succeeded', 'failed', 'expired', 'cancelled') "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('leased', 'running') AND finished_at IS NULL))",
            name="ck_bid_lot_detection_attempts_terminal",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["bid_lot_detection_runs.id"],
            name="fk_bid_lot_detection_attempts_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_lot_detection_attempts"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_bid_lot_detection_attempts_number"),
        sa.UniqueConstraint("run_id", "id", name="uq_bid_lot_detection_attempts_run_id"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_lot_detection_attempts_lease",
        "bid_lot_detection_attempts",
        ["status", "lease_until"],
    )
    op.create_index(
        "ix_bid_lot_detection_attempts_run_status",
        "bid_lot_detection_attempts",
        ["run_id", "status"],
    )

    op.create_table(
        "bid_lot_detection_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sequence_no >= 1", name="ck_bid_lot_detection_events_sequence"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["bid_lot_detection_runs.id"],
            name="fk_bid_lot_detection_events_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "attempt_id"],
            ["bid_lot_detection_attempts.run_id", "bid_lot_detection_attempts.id"],
            name="fk_bid_lot_detection_events_attempt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bid_lot_detection_events"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_bid_lot_detection_events_sequence"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_lot_detection_events_run_created",
        "bid_lot_detection_events",
        ["run_id", "created_at"],
    )

    op.drop_constraint("uq_bid_lot_candidates_key", "bid_lot_candidates", type_="unique")
    op.drop_constraint("uq_bid_lot_candidates_hash", "bid_lot_candidates", type_="unique")
    op.add_column(
        "bid_lot_candidates",
        sa.Column("detection_run_id", sa.String(length=36), nullable=False),
    )
    op.add_column(
        "bid_lot_candidates",
        sa.Column("confidence_level", sa.String(length=16), nullable=False),
    )
    op.create_check_constraint(
        "ck_bid_lot_candidates_confidence_level",
        "bid_lot_candidates",
        "confidence_level IN ('high', 'medium', 'low')",
    )
    op.create_check_constraint(
        "ck_bid_lot_candidates_source_status",
        "bid_lot_candidates",
        "source_status IN ('detected', 'system_scope')",
    )
    op.create_foreign_key(
        "fk_bid_lot_candidates_detection_run",
        "bid_lot_candidates",
        "bid_lot_detection_runs",
        ["manifest_id", "detection_run_id"],
        ["manifest_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_bid_lot_candidates_manifest_id",
        "bid_lot_candidates",
        ["id", "manifest_id"],
    )
    op.create_unique_constraint(
        "uq_bid_lot_candidates_key",
        "bid_lot_candidates",
        ["detection_run_id", "normalized_lot_key"],
    )
    op.create_unique_constraint(
        "uq_bid_lot_candidates_hash",
        "bid_lot_candidates",
        ["detection_run_id", "candidate_hash"],
    )
    op.create_index(
        "ix_bid_lot_candidates_detection_run",
        "bid_lot_candidates",
        ["detection_run_id"],
    )

    op.create_table(
        "bid_lot_candidate_evidence",
        sa.Column("lot_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("support_role", sa.String(length=24), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("display_label", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "support_role IN ('identity', 'code', 'name', 'scope', 'overall_scope')",
            name="ck_bid_lot_candidate_evidence_role",
        ),
        sa.CheckConstraint("display_order >= 0", name="ck_bid_lot_candidate_evidence_order"),
        sa.ForeignKeyConstraint(
            ["lot_candidate_id", "manifest_id"],
            ["bid_lot_candidates.id", "bid_lot_candidates.manifest_id"],
            name="fk_bid_lot_candidate_evidence_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "document_version_id"],
            ["bid_evidence_fragments.id", "bid_evidence_fragments.document_version_id"],
            name="fk_bid_lot_candidate_evidence_fragment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id", "document_version_id"],
            ["bid_manifest_documents.manifest_id", "bid_manifest_documents.document_version_id"],
            name="fk_bid_lot_candidate_evidence_manifest_document",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "lot_candidate_id",
            "evidence_id",
            name="pk_bid_lot_candidate_evidence",
        ),
        sa.UniqueConstraint(
            "lot_candidate_id",
            "display_order",
            name="uq_bid_lot_candidate_evidence_order",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_bid_lot_candidate_evidence_fragment",
        "bid_lot_candidate_evidence",
        ["evidence_id"],
    )
    op.create_index(
        "ix_bid_lot_candidate_evidence_manifest",
        "bid_lot_candidate_evidence",
        ["manifest_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0093 guarded downgrade requires an online database connection; "
            "offline SQL would bypass immutable lot lineage checks"
        )
    bind = op.get_bind()
    tables = (
        "bid_lot_candidate_evidence",
        "bid_lot_detection_heads",
        "bid_lot_detection_attempts",
        "bid_lot_detection_events",
        "bid_lot_detection_runs",
        "bid_lot_candidates",
    )
    nonempty = {
        table: int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        for table in tables
    }
    if any(nonempty.values()):
        raise RuntimeError(
            "0093 downgrade would erase immutable lot detection/candidate lineage; "
            "export and explicitly remove Phase 2 rows first"
        )

    op.drop_table("bid_lot_candidate_evidence")
    op.drop_index("ix_bid_lot_candidates_detection_run", table_name="bid_lot_candidates")
    op.drop_constraint("uq_bid_lot_candidates_hash", "bid_lot_candidates", type_="unique")
    op.drop_constraint("uq_bid_lot_candidates_key", "bid_lot_candidates", type_="unique")
    op.drop_constraint("uq_bid_lot_candidates_manifest_id", "bid_lot_candidates", type_="unique")
    op.drop_constraint("fk_bid_lot_candidates_detection_run", "bid_lot_candidates", type_="foreignkey")
    op.drop_constraint("ck_bid_lot_candidates_source_status", "bid_lot_candidates", type_="check")
    op.drop_constraint("ck_bid_lot_candidates_confidence_level", "bid_lot_candidates", type_="check")
    op.drop_column("bid_lot_candidates", "confidence_level")
    op.drop_column("bid_lot_candidates", "detection_run_id")
    op.create_unique_constraint(
        "uq_bid_lot_candidates_key",
        "bid_lot_candidates",
        ["manifest_id", "normalized_lot_key"],
    )
    op.create_unique_constraint(
        "uq_bid_lot_candidates_hash",
        "bid_lot_candidates",
        ["manifest_id", "candidate_hash"],
    )
    op.drop_table("bid_lot_detection_events")
    op.drop_table("bid_lot_detection_attempts")
    op.drop_table("bid_lot_detection_heads")
    op.drop_table("bid_lot_detection_runs")
