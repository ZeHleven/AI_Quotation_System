from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class ExecutionTask(Base):
    __tablename__ = "execution_tasks"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    title = Column(String(255), nullable=False)
    source = Column(String(24), index=True, nullable=False, default="manual", server_default="manual")
    source_ref_id = Column(String(64), nullable=True, index=True)
    assignee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    status = Column(String(24), index=True, nullable=False, default="pending", server_default="pending")
    notes = Column(Text, nullable=True)

    events = relationship(
        "ExecutionTaskEvent",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ExecutionTaskEvent.id",
    )


class ExecutionTaskEvent(Base):
    __tablename__ = "execution_task_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    execution_task_id = Column(Integer, ForeignKey("execution_tasks.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)

    task = relationship("ExecutionTask", back_populates="events")
