from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("project_code", name="uq_projects_project_code"),
        Index("ix_projects_status_risk", "status", "risk_level"),
        Index("ix_projects_manager_status", "project_manager_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    client_name = Column(String(128), nullable=True, index=True)
    address = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="planning", server_default="planning", index=True)
    risk_level = Column(String(24), nullable=False, default="normal", server_default="normal", index=True)
    progress_percent = Column(Integer, nullable=False, default=0, server_default="0")
    project_manager_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    owner_department = Column(String(128), nullable=True, index=True)
    planned_start_at = Column(DateTime(timezone=True), nullable=True, index=True)
    planned_finish_at = Column(DateTime(timezone=True), nullable=True, index=True)
    actual_start_at = Column(DateTime(timezone=True), nullable=True)
    actual_finish_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    stages = relationship(
        "ProjectStage",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectStage.sort_order",
    )
    tasks = relationship(
        "ProjectTask",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTask.id",
    )
    events = relationship(
        "ProjectTaskEvent",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTaskEvent.id",
    )
    evidences = relationship(
        "ProjectTaskEvidence",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTaskEvidence.id",
    )


class ProjectStage(Base):
    __tablename__ = "project_stages"
    __table_args__ = (
        UniqueConstraint("project_id", "stage_key", name="uq_project_stages_project_stage_key"),
        Index("ix_project_stages_project_order", "project_id", "sort_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_key = Column(String(64), nullable=False, index=True)
    stage_name = Column(String(128), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    weight_percent = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(String(24), nullable=False, default="todo", server_default="todo", index=True)
    progress_percent = Column(Integer, nullable=False, default=0, server_default="0")
    owner_role = Column(String(64), nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    planned_start_at = Column(DateTime(timezone=True), nullable=True)
    planned_finish_at = Column(DateTime(timezone=True), nullable=True)
    actual_start_at = Column(DateTime(timezone=True), nullable=True)
    actual_finish_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("Project", back_populates="stages")
    tasks = relationship(
        "ProjectTask",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="ProjectTask.id",
    )
    evidences = relationship(
        "ProjectTaskEvidence",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="ProjectTaskEvidence.id",
    )


class ProjectTask(Base):
    __tablename__ = "project_tasks"
    __table_args__ = (
        Index("ix_project_tasks_project_status", "project_id", "status"),
        Index("ix_project_tasks_owner_status", "owner_user_id", "status"),
        Index("ix_project_tasks_due_status", "due_at", "status"),
        Index("ix_project_tasks_evidence_policy", "evidence_policy"),
        Index("ix_project_tasks_is_key_node", "is_key_node"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_id = Column(Integer, ForeignKey("project_stages.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    evidence_requirement = Column(Text, nullable=True)
    evidence_policy = Column(String(32), nullable=False, default="none", server_default="none")
    is_key_node = Column(Boolean, nullable=False, default=False, server_default="0")
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    owner_role = Column(String(64), nullable=True)
    collaborators_json = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="todo", server_default="todo", index=True)
    progress_percent = Column(Integer, nullable=False, default=0, server_default="0")
    priority = Column(String(24), nullable=False, default="normal", server_default="normal", index=True)
    planned_start_at = Column(DateTime(timezone=True), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    blocked_reason = Column(Text, nullable=True)
    next_action = Column(Text, nullable=True)
    last_status_before_blocked = Column(String(24), nullable=True)
    source_type = Column(String(32), nullable=False, default="manual", server_default="manual", index=True)
    source_id = Column(String(64), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("Project", back_populates="tasks")
    stage = relationship("ProjectStage", back_populates="tasks")
    events = relationship(
        "ProjectTaskEvent",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ProjectTaskEvent.id",
    )
    evidences = relationship(
        "ProjectTaskEvidence",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ProjectTaskEvidence.id",
    )


class ProjectTaskEvidence(Base):
    __tablename__ = "project_task_evidences"
    __table_args__ = (
        Index("ix_project_task_evidences_task_status_created", "task_id", "status", "created_at"),
        Index("ix_project_task_evidences_project_status_created", "project_id", "status", "created_at"),
        Index("ix_project_task_evidences_stage_status", "stage_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_id = Column(Integer, ForeignKey("project_stages.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("project_tasks.id"), nullable=False, index=True)
    evidence_type = Column(String(32), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    file_object_id = Column(String(36), ForeignKey("file_objects.file_id"), nullable=True, index=True)
    external_url = Column(String(1024), nullable=True)
    external_provider = Column(String(64), nullable=True)
    requirement_snapshot = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active", index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    removed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    removed_at = Column(DateTime(timezone=True), nullable=True)
    remove_reason = Column(Text, nullable=True)

    project = relationship("Project", back_populates="evidences")
    stage = relationship("ProjectStage", back_populates="evidences")
    task = relationship("ProjectTask", back_populates="evidences")


class ProjectTaskEvent(Base):
    __tablename__ = "project_task_events"
    __table_args__ = (
        Index("ix_project_task_events_project_created", "project_id", "created_at"),
        Index("ix_project_task_events_task_created", "task_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_id = Column(Integer, ForeignKey("project_stages.id"), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey("project_tasks.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    message = Column(Text, nullable=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project", back_populates="events")
    task = relationship("ProjectTask", back_populates="events")
