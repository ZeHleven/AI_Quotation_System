"""Runtime, lease, checkpoint, async-operation, and question persistence models.

This module contains persistence structure only. Runtime state transitions,
lease compare-and-swap writes, and answer submission are added by later
services and must preserve the database invariants declared here.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
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

RUN_STATES = (
    "created",
    "planning",
    "queued",
    "running",
    "waiting_input",
    "waiting_operation",
    "validating",
    "succeeded",
    "failed",
    "stale",
    "cancelled",
)
PLAN_STATES = ("proposed", "validating", "committed", "rejected", "superseded")
TASK_STATES = (
    "blocked",
    "ready",
    "leased",
    "running",
    "waiting_operation",
    "waiting_input",
    "validating",
    "succeeded",
    "failed",
    "skipped",
    "stale",
    "cancelled",
)
ATTEMPT_STATES = (
    "created",
    "leased",
    "running",
    "waiting_operation",
    "waiting_input",
    "validating",
    "succeeded",
    "failed",
    "stale",
    "cancelled",
    "lease_expired",
)
ASYNC_STATES = (
    "created",
    "submitted",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)
QUESTION_STATES = (
    "candidate",
    "published",
    "answered",
    "expired",
    "withdrawn",
    "discarded",
    "superseded",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BidAnalysisRun(Base):
    __tablename__ = "bid_analysis_runs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(RUN_STATES)})",
            name="ck_bid_analysis_runs_status",
        ),
        CheckConstraint(
            "run_kind IN ('preliminary', 'deep', 'reanalysis')",
            name="ck_bid_analysis_runs_kind",
        ),
        CheckConstraint("run_sequence >= 1", name="ck_bid_analysis_runs_sequence"),
        CheckConstraint("row_version >= 1", name="ck_bid_analysis_runs_row_version"),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_analysis_runs_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "scope_id"],
            ["bid_assessment_scopes.assessment_id", "bid_assessment_scopes.id"],
            name="fk_bid_analysis_runs_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_analysis_runs_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "restart_of_run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_analysis_runs_restart",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("assessment_id", "id", name="uq_bid_analysis_runs_owner_id"),
        UniqueConstraint(
            "assessment_id",
            "run_sequence",
            name="uq_bid_analysis_runs_sequence",
        ),
        UniqueConstraint(
            "assessment_id",
            "input_hash",
            "run_kind",
            name="uq_bid_analysis_runs_input",
        ),
        Index("ix_bid_analysis_runs_assessment_status", "assessment_id", "status"),
        Index(
            "ix_bid_analysis_runs_fingerprint",
            "assessment_id",
            "input_fingerprint",
            "run_kind",
            "status",
        ),
        Index("ix_bid_analysis_runs_manifest", "manifest_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    scope_id = Column(String(36), nullable=False)
    manifest_id = Column(String(36), nullable=False)
    enterprise_snapshot_id = Column(
        String(36),
        ForeignKey("bid_enterprise_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rule_set_id = Column(
        String(36), ForeignKey("bid_rule_sets.id", ondelete="RESTRICT"), nullable=False
    )
    fact_catalog_version_id = Column(
        String(36),
        ForeignKey("bid_fact_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    prompt_bundle_id = Column(
        String(36), ForeignKey("bid_prompt_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    tool_registry_version_id = Column(
        String(36),
        ForeignKey("bid_tool_registry_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model_profile_version_id = Column(
        String(36),
        ForeignKey("bid_model_profile_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    formula_catalog_version_id = Column(
        String(36),
        ForeignKey("bid_formula_catalog_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    restart_of_run_id = Column(String(36), nullable=True)
    run_sequence = Column(Integer, nullable=False)
    run_kind = Column(String(24), nullable=False)
    status = Column(String(32), nullable=False, default="created", server_default="created")
    retryable = Column(Boolean, nullable=False, default=False, server_default="0")
    input_fingerprint = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    evaluation_time = Column(DateTime(timezone=True), nullable=False)
    current_stage = Column(String(80), nullable=True)
    waiting_reason = Column(String(500), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    last_checkpoint_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidPlanRevision(Base):
    __tablename__ = "bid_plan_revisions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(PLAN_STATES)})",
            name="ck_bid_plan_revisions_status",
        ),
        CheckConstraint("revision_no >= 1", name="ck_bid_plan_revisions_number"),
        CheckConstraint("row_version >= 1", name="ck_bid_plan_revisions_row_version"),
        CheckConstraint(
            "status NOT IN ('committed', 'rejected', 'superseded') OR validated_hash IS NOT NULL",
            name="ck_bid_plan_revisions_validated_hash",
        ),
        CheckConstraint(
            "((status = 'committed' AND committed_slot_key = 'committed' "
            "AND validated_hash IS NOT NULL AND committed_at IS NOT NULL) "
            "OR (status = 'superseded' AND committed_slot_key IS NULL "
            "AND validated_hash IS NOT NULL AND committed_at IS NOT NULL "
            "AND superseded_at IS NOT NULL) OR (status IN "
            "('proposed', 'validating', 'rejected') AND committed_slot_key IS NULL))",
            name="ck_bid_plan_revisions_committed_slot",
        ),
        UniqueConstraint("run_id", "id", name="uq_bid_plan_revisions_run_id"),
        UniqueConstraint("run_id", "revision_no", name="uq_bid_plan_revisions_number"),
        UniqueConstraint("run_id", "committed_slot_key", name="uq_bid_plan_revisions_committed"),
        Index("ix_bid_plan_revisions_run_status", "run_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("bid_analysis_runs.id", ondelete="RESTRICT"), nullable=False)
    revision_no = Column(Integer, nullable=False)
    proposal_json = Column(JSON, nullable=False)
    validated_hash = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False, default="proposed", server_default="proposed")
    committed_slot_key = Column(String(32), nullable=True)
    rejection_reason = Column(String(500), nullable=True)
    committed_at = Column(DateTime(timezone=True), nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidTask(Base):
    __tablename__ = "bid_tasks"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in_values(TASK_STATES)})", name="ck_bid_tasks_status"),
        CheckConstraint("row_version >= 1", name="ck_bid_tasks_row_version"),
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_analysis_runs.id"],
            name="fk_bid_tasks_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "plan_revision_id"],
            ["bid_plan_revisions.run_id", "bid_plan_revisions.id"],
            name="fk_bid_tasks_plan",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "current_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_tasks_current_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "id", name="uq_bid_tasks_run_id"),
        UniqueConstraint(
            "run_id",
            "task_key",
            "input_hash",
            name="uq_bid_tasks_logical_input",
        ),
        Index("ix_bid_tasks_run_status", "run_id", "status"),
        Index("ix_bid_tasks_type_status", "task_type", "status"),
        Index("ix_bid_tasks_current_attempt", "current_attempt_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False)
    plan_revision_id = Column(String(36), nullable=False)
    task_key = Column(String(160), nullable=False)
    task_type = Column(String(80), nullable=False)
    objective = Column(String(500), nullable=False)
    status = Column(String(32), nullable=False, default="blocked", server_default="blocked")
    input_hash = Column(String(64), nullable=False)
    tool_profile = Column(String(64), nullable=False)
    context_profile = Column(String(64), nullable=False)
    budget_profile = Column(String(64), nullable=False)
    completion_contract = Column(String(128), nullable=False)
    current_attempt_id = Column(String(36), nullable=True)
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidTaskDependency(Base):
    __tablename__ = "bid_task_dependencies"
    __table_args__ = (
        CheckConstraint("task_id <> depends_on_task_id", name="ck_bid_task_dependencies_not_self"),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_task_dependencies_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "depends_on_task_id"],
            ["bid_tasks.run_id", "bid_tasks.id"],
            name="fk_bid_task_dependencies_parent",
            ondelete="RESTRICT",
        ),
        Index("ix_bid_task_dependencies_parent", "depends_on_task_id"),
        TABLE_OPTIONS,
    )

    run_id = Column(String(36), nullable=False)
    task_id = Column(String(36), primary_key=True)
    depends_on_task_id = Column(String(36), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidTaskAttempt(Base):
    __tablename__ = "bid_task_attempts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(ATTEMPT_STATES)})",
            name="ck_bid_task_attempts_status",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_bid_task_attempts_number"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_task_attempts_fencing"),
        CheckConstraint("row_version >= 1", name="ck_bid_task_attempts_row_version"),
        CheckConstraint(
            "status NOT IN ('leased', 'running', 'validating') OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL AND heartbeat_at IS NOT NULL)",
            name="ck_bid_task_attempts_active_lease",
        ),
        UniqueConstraint("task_id", "id", name="uq_bid_task_attempts_task_id"),
        UniqueConstraint("task_id", "attempt_no", name="uq_bid_task_attempts_number"),
        UniqueConstraint("task_id", "fencing_token", name="uq_bid_task_attempts_fencing"),
        Index("ix_bid_task_attempts_task_status", "task_id", "status"),
        Index("ix_bid_task_attempts_lease", "status", "lease_until", "heartbeat_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey("bid_tasks.id", ondelete="RESTRICT"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="created", server_default="created")
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    fencing_token = Column(BigInteger, nullable=False)
    error_code = Column(String(100), nullable=True)
    error_detail_ref = Column(String(512), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    lease_reclaimed_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidCheckpoint(Base):
    __tablename__ = "bid_checkpoints"
    __table_args__ = (
        CheckConstraint("action_seq >= 0", name="ck_bid_checkpoints_action_seq"),
        CheckConstraint("fencing_token >= 1", name="ck_bid_checkpoints_fencing"),
        UniqueConstraint("task_attempt_id", "action_seq", name="uq_bid_checkpoints_action"),
        Index("ix_bid_checkpoints_attempt_created", "task_attempt_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_attempt_id = Column(
        String(36), ForeignKey("bid_task_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    fencing_token = Column(BigInteger, nullable=False)
    action_seq = Column(Integer, nullable=False)
    context_manifest_id = Column(String(36), nullable=True)
    state_json = Column(JSON, nullable=False)
    state_hash = Column(String(64), nullable=False)
    tool_refs_json = Column(JSON, nullable=True)
    budget_usage_json = Column(JSON, nullable=True)
    candidate_output_ref = Column(String(512), nullable=True)
    next_state = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidAsyncOperation(Base):
    __tablename__ = "bid_async_operations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(ASYNC_STATES)})",
            name="ck_bid_async_operations_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_bid_async_operations_retry"),
        CheckConstraint("row_version >= 1", name="ck_bid_async_operations_row_version"),
        ForeignKeyConstraint(
            ["task_id"],
            ["bid_tasks.id"],
            name="fk_bid_async_operations_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id", "task_attempt_id"],
            ["bid_task_attempts.task_id", "bid_task_attempts.id"],
            name="fk_bid_async_operations_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "task_id",
            "operation_type",
            "input_hash",
            name="uq_bid_async_operations_task_input",
        ),
        Index("ix_bid_async_operations_status_timeout", "status", "timeout_at"),
        Index("ix_bid_async_operations_attempt", "task_attempt_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), nullable=False)
    task_attempt_id = Column(String(36), nullable=False)
    operation_type = Column(String(64), nullable=False)
    provider_ref = Column(String(255), nullable=True)
    status = Column(String(24), nullable=False, default="created", server_default="created")
    input_hash = Column(String(64), nullable=False)
    result_ref = Column(String(512), nullable=True)
    error_code = Column(String(100), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    timeout_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidQuestionRound(Base):
    __tablename__ = "bid_question_rounds"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in_values(QUESTION_STATES)})",
            name="ck_bid_question_rounds_status",
        ),
        CheckConstraint("round_no >= 1", name="ck_bid_question_rounds_number"),
        CheckConstraint("row_version >= 1", name="ck_bid_question_rounds_row_version"),
        CheckConstraint(
            "((status = 'published' AND open_slot_key = 'published' "
            "AND published_at IS NOT NULL) OR (status IN "
            "('answered', 'expired', 'withdrawn', 'superseded') "
            "AND open_slot_key IS NULL AND published_at IS NOT NULL) "
            "OR (status IN ('candidate', 'discarded') AND open_slot_key IS NULL))",
            name="ck_bid_question_rounds_open_slot",
        ),
        CheckConstraint(
            "status <> 'answered' OR answered_at IS NOT NULL",
            name="ck_bid_question_rounds_answered_at",
        ),
        CheckConstraint(
            "status <> 'withdrawn' OR withdrawn_at IS NOT NULL",
            name="ck_bid_question_rounds_withdrawn_at",
        ),
        ForeignKeyConstraint(
            ["assessment_id"],
            ["bid_assessments.id"],
            name="fk_bid_question_rounds_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "run_id"],
            ["bid_analysis_runs.assessment_id", "bid_analysis_runs.id"],
            name="fk_bid_question_rounds_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "manifest_id"],
            ["bid_document_manifests.assessment_id", "bid_document_manifests.id"],
            name="fk_bid_question_rounds_manifest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "id", name="uq_bid_question_rounds_run_id"),
        UniqueConstraint("id", "manifest_id", name="uq_bid_question_rounds_manifest_id"),
        UniqueConstraint("run_id", "round_no", name="uq_bid_question_rounds_number"),
        UniqueConstraint("run_id", "open_slot_key", name="uq_bid_question_rounds_open_slot"),
        Index("ix_bid_question_rounds_assessment_status", "assessment_id", "status"),
        Index("ix_bid_question_rounds_run_created", "run_id", "created_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    assessment_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    manifest_id = Column(String(36), nullable=False)
    round_no = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="candidate", server_default="candidate")
    open_slot_key = Column(String(32), nullable=True)
    input_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    withdrawn_at = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidQuestion(Base):
    __tablename__ = "bid_questions"
    __table_args__ = (
        CheckConstraint("question_order BETWEEN 1 AND 3", name="ck_bid_questions_order"),
        CheckConstraint(
            f"status IN ({_in_values(QUESTION_STATES)})",
            name="ck_bid_questions_status",
        ),
        CheckConstraint(
            "question_type IN ('single_choice', 'boolean_unknown', 'number_with_unit', "
            "'money', 'date', 'short_text', 'document_upload')",
            name="ck_bid_questions_type",
        ),
        CheckConstraint(
            "priority IN ('critical', 'important', 'contextual')",
            name="ck_bid_questions_priority",
        ),
        CheckConstraint(
            "impact IN ('decision_critical', 'decision_sensitive', 'contextual')",
            name="ck_bid_questions_impact",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_questions_row_version"),
        UniqueConstraint("question_round_id", "id", name="uq_bid_questions_round_id"),
        UniqueConstraint("question_round_id", "question_order", name="uq_bid_questions_order"),
        UniqueConstraint("question_round_id", "fact_slot", name="uq_bid_questions_fact_slot"),
        Index("ix_bid_questions_round_status", "question_round_id", "status"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    question_round_id = Column(
        String(36), ForeignKey("bid_question_rounds.id", ondelete="RESTRICT"), nullable=False
    )
    question_order = Column(Integer, nullable=False)
    fact_slot = Column(String(160), nullable=False)
    question_type = Column(String(32), nullable=False)
    priority = Column(String(24), nullable=False)
    question_text = Column(String(1000), nullable=False)
    why_needed = Column(String(1000), nullable=False)
    impact = Column(String(32), nullable=False)
    impact_json = Column(JSON, nullable=True)
    answer_schema_json = Column(JSON, nullable=False)
    allow_unknown = Column(Boolean, nullable=False, default=False, server_default="0")
    status = Column(String(24), nullable=False, default="candidate", server_default="candidate")
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidAnswerDraft(Base):
    __tablename__ = "bid_answer_drafts"
    __table_args__ = (
        CheckConstraint(
            "answer_status IN ('answered', 'unknown')",
            name="ck_bid_answer_drafts_status",
        ),
        CheckConstraint(
            "((answer_status = 'answered' AND value_json IS NOT NULL) OR "
            "(answer_status = 'unknown' AND value_json IS NULL))",
            name="ck_bid_answer_drafts_value",
        ),
        CheckConstraint("row_version >= 1", name="ck_bid_answer_drafts_row_version"),
        ForeignKeyConstraint(
            ["question_round_id", "question_id"],
            ["bid_questions.question_round_id", "bid_questions.id"],
            name="fk_bid_answer_drafts_question",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "question_round_id",
            "question_id",
            "actor_id",
            name="uq_bid_answer_drafts_actor",
        ),
        Index("ix_bid_answer_drafts_actor_updated", "actor_id", "updated_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    question_round_id = Column(String(36), nullable=False)
    question_id = Column(String(36), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    answer_status = Column(String(24), nullable=False)
    value_json = Column(JSON, nullable=True)
    note = Column(Text, nullable=True)
    draft_hash = Column(String(64), nullable=False)
    row_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BidAnswerSet(Base):
    __tablename__ = "bid_answer_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["bid_analysis_runs.id"],
            name="fk_bid_answer_sets_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "question_round_id"],
            ["bid_question_rounds.run_id", "bid_question_rounds.id"],
            name="fk_bid_answer_sets_round_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["question_round_id", "manifest_id"],
            ["bid_question_rounds.id", "bid_question_rounds.manifest_id"],
            name="fk_bid_answer_sets_round_manifest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("question_round_id", "id", name="uq_bid_answer_sets_round_id"),
        UniqueConstraint(
            "question_round_id",
            "answer_set_hash",
            name="uq_bid_answer_sets_hash",
        ),
        Index("ix_bid_answer_sets_run_submitted", "run_id", "submitted_at"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    question_round_id = Column(String(36), nullable=False)
    run_id = Column(String(36), nullable=False)
    manifest_id = Column(String(36), nullable=False)
    answered_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=False)
    answer_set_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BidAnswer(Base):
    __tablename__ = "bid_answers"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_bid_answers_version"),
        CheckConstraint(
            "answer_status IN ('answered', 'unknown')",
            name="ck_bid_answers_status",
        ),
        CheckConstraint(
            "((answer_status = 'answered' AND value_json IS NOT NULL) OR "
            "(answer_status = 'unknown' AND value_json IS NULL))",
            name="ck_bid_answers_value",
        ),
        ForeignKeyConstraint(
            ["question_round_id", "answer_set_id"],
            ["bid_answer_sets.question_round_id", "bid_answer_sets.id"],
            name="fk_bid_answers_answer_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["question_round_id", "question_id"],
            ["bid_questions.question_round_id", "bid_questions.id"],
            name="fk_bid_answers_question",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("question_id", "version", name="uq_bid_answers_question_version"),
        UniqueConstraint(
            "answer_set_id",
            "question_id",
            name="uq_bid_answers_set_question",
        ),
        Index("ix_bid_answers_round", "question_round_id"),
        TABLE_OPTIONS,
    )

    id = Column(String(36), primary_key=True)
    question_round_id = Column(String(36), nullable=False)
    answer_set_id = Column(String(36), nullable=False)
    question_id = Column(String(36), nullable=False)
    version = Column(Integer, nullable=False)
    answer_status = Column(String(24), nullable=False)
    answer_text = Column(Text, nullable=True)
    value_json = Column(JSON, nullable=True)
    note = Column(Text, nullable=True)
    answer_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
