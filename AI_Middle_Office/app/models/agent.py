from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.sql import func

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(36), unique=True, index=True, nullable=False)
    agent_type = Column(String(64), index=True, nullable=False)
    target_type = Column(String(64), index=True, nullable=False)
    target_id = Column(String(128), index=True, nullable=False)
    trigger_source = Column(String(32), index=True, default="manual", nullable=False)
    trigger_ref_type = Column(String(64), index=True, nullable=True)
    trigger_ref_id = Column(String(128), index=True, nullable=True)
    status = Column(String(24), index=True, default="running", nullable=False)
    risk_level = Column(String(24), index=True, nullable=True)
    recommendation = Column(String(64), index=True, nullable=True)
    summary = Column(_long_text(), nullable=True)
    output_json = Column(_long_text(), nullable=True)
    created_by = Column(String(64), index=True, nullable=False)
    trace_id = Column(String(64), index=True, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(36), ForeignKey("agent_runs.run_id"), index=True, nullable=False)
    tool_name = Column(String(128), index=True, nullable=False)
    input_json = Column(_long_text(), nullable=True)
    output_summary = Column(_long_text(), nullable=True)
    output_json = Column(_long_text(), nullable=True)
    status = Column(String(24), index=True, default="success", nullable=False)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentFinding(Base):
    __tablename__ = "agent_findings"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(36), ForeignKey("agent_runs.run_id"), index=True, nullable=False)
    finding_type = Column(String(64), index=True, nullable=False)
    severity = Column(String(24), index=True, nullable=False)
    target_ref = Column(String(255), index=True, nullable=True)
    title = Column(String(255), nullable=False)
    evidence_json = Column(_long_text(), nullable=True)
    suggestion = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentSuggestion(Base):
    __tablename__ = "agent_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    suggestion_id = Column(String(36), unique=True, index=True, nullable=False)
    run_id = Column(String(36), ForeignKey("agent_runs.run_id"), index=True, nullable=False)
    agent_type = Column(String(64), index=True, nullable=False)
    target_type = Column(String(64), index=True, nullable=False)
    target_id = Column(String(128), index=True, nullable=False)
    suggestion_type = Column(String(64), index=True, nullable=False)
    status = Column(String(32), index=True, default="pending_review", nullable=False)
    priority = Column(String(24), index=True, default="medium", nullable=False)
    target_ref = Column(String(255), index=True, nullable=True)
    target_line_no = Column(Integer, index=True, nullable=True)
    title = Column(String(255), nullable=False)
    rationale = Column(_long_text(), nullable=True)
    risk_note = Column(_long_text(), nullable=True)
    current_snapshot_json = Column(_long_text(), nullable=True)
    proposed_snapshot_json = Column(_long_text(), nullable=True)
    execution_result_json = Column(_long_text(), nullable=True)
    final_result_json = Column(_long_text(), nullable=True)
    estimated_saving_amount = Column(Float, nullable=True)
    estimated_saving_rate = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    requires_approval = Column(Boolean, default=True, nullable=False)
    created_by = Column(String(64), index=True, nullable=False)
    decided_by = Column(String(64), index=True, nullable=True)
    decision_note = Column(_long_text(), nullable=True)
    executed_by = Column(String(64), index=True, nullable=True)
    final_confirmed_by = Column(String(64), index=True, nullable=True)
    final_note = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    final_confirmed_at = Column(DateTime(timezone=True), nullable=True)


class AgentSuggestionEvent(Base):
    __tablename__ = "agent_suggestion_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(36), unique=True, index=True, nullable=False)
    suggestion_id = Column(String(36), ForeignKey("agent_suggestions.suggestion_id"), index=True, nullable=False)
    run_id = Column(String(36), ForeignKey("agent_runs.run_id"), index=True, nullable=False)
    event_type = Column(String(64), index=True, nullable=False)
    actor = Column(String(64), index=True, nullable=True)
    note = Column(_long_text(), nullable=True)
    payload_json = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentSchedulerRun(Base):
    __tablename__ = "agent_scheduler_runs"
    __table_args__ = (
        UniqueConstraint("scheduler_key", "run_date", "triggered_by", name="uq_agent_scheduler_runs_key_date_actor"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scheduler_key = Column(String(64), index=True, nullable=False)
    run_date = Column(Date(), index=True, nullable=False)
    status = Column(String(24), index=True, default="running", nullable=False)
    scheduled_at = Column(DateTime(timezone=True), index=True, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    triggered_by = Column(String(64), index=True, default="system_scheduler", nullable=False)
    candidate_count = Column(Integer, default=0, nullable=False)
    created_run_count = Column(Integer, default=0, nullable=False)
    skipped_duplicate_count = Column(Integer, default=0, nullable=False)
    skipped_invalid_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    result_json = Column(_long_text(), nullable=True)
    error_message = Column(_long_text(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
