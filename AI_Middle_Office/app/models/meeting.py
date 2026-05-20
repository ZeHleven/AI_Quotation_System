from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class MeetingNote(Base):
    __tablename__ = "meeting_notes"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft", index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    cancel_reason = Column(Text, nullable=True)
    ai_status = Column(String(24), nullable=False, default="pending", server_default="pending", index=True)
    prompt_version = Column(String(64), nullable=True)
    extraction_error = Column(Text, nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)

    drafts = relationship(
        "TaskDraft",
        back_populates="meeting_note",
        cascade="all, delete-orphan",
        order_by="TaskDraft.id",
    )
    revisions = relationship(
        "MeetingNoteRevision",
        back_populates="meeting_note",
        cascade="all, delete-orphan",
        order_by="MeetingNoteRevision.id",
    )


class MeetingNoteRevision(Base):
    __tablename__ = "meeting_note_revisions"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    meeting_note_id = Column(Integer, ForeignKey("meeting_notes.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    previous_content_sha256 = Column(String(64), nullable=False)
    trace_id = Column(String(64), nullable=True, index=True)
    task_ids_json = Column(Text, nullable=True)

    meeting_note = relationship("MeetingNote", back_populates="revisions")


class TaskDraft(Base):
    __tablename__ = "task_drafts"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    meeting_note_id = Column(Integer, ForeignKey("meeting_notes.id"), nullable=False, index=True)
    revision_id = Column(Integer, ForeignKey("meeting_note_revisions.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    normalized_title = Column(String(255), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="pending_review", server_default="pending_review", index=True)
    source_sentence = Column(Text, nullable=False)
    suggested_assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    suggested_due_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    confirmed_due_at = Column(DateTime(timezone=True), nullable=True)
    accepted_task_id = Column(Integer, ForeignKey("execution_tasks.id"), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    prompt_version = Column(String(64), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)

    meeting_note = relationship("MeetingNote", back_populates="drafts")
