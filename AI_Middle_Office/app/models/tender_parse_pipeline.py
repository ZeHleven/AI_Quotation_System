from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


class BidTenderSourceObject(Base):
    """Immutable pointer from a bid project to an original stored upload."""

    __tablename__ = "bid_tender_source_objects"
    __table_args__ = (
        UniqueConstraint(
            "source_uuid",
            name="uq_bid_tender_source_objects_uuid",
        ),
        UniqueConstraint(
            "project_id",
            "document_key",
            "sha256",
            name="uq_bid_tender_source_objects_project_key_sha",
        ),
        Index(
            "ix_bid_tender_source_objects_project_created",
            "project_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_object_id = Column(
        String(36),
        ForeignKey("file_objects.file_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_key = Column(String(160), nullable=False, index=True)
    file_type = Column(
        String(64),
        nullable=False,
        default="tender_document",
        server_default="tender_document",
        index=True,
    )
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0, server_default="0")
    sha256 = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="stored",
        server_default="stored",
        index=True,
    )
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidTenderParseJob(Base):
    """Retryable parser job that promotes one stored source into evidence."""

    __tablename__ = "bid_tender_parse_jobs"
    __table_args__ = (
        UniqueConstraint(
            "job_uuid",
            name="uq_bid_tender_parse_jobs_uuid",
        ),
        UniqueConstraint(
            "source_object_id",
            "parser_version",
            name="uq_bid_tender_parse_jobs_source_parser",
        ),
        Index(
            "ix_bid_tender_parse_jobs_project_status_created",
            "project_id",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_object_id = Column(
        Integer,
        ForeignKey("bid_tender_source_objects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    stage = Column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    parser_version = Column(String(64), nullable=False, index=True)
    bid_project_file_id = Column(
        Integer,
        ForeignKey("bid_project_files.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    evidence_document_uuid = Column(String(36), nullable=True, index=True)
    celery_task_id = Column(String(160), nullable=True, index=True)
    error_code = Column(String(64), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BidTenderParseJobEvent(Base):
    """Append-only state transition and diagnostic record for a parser job."""

    __tablename__ = "bid_tender_parse_job_events"
    __table_args__ = (
        UniqueConstraint(
            "event_uuid",
            name="uq_bid_tender_parse_job_events_uuid",
        ),
        Index(
            "ix_bid_tender_parse_job_events_job_created",
            "parse_job_id",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_uuid = Column(String(36), nullable=False, unique=True, index=True)
    parse_job_id = Column(
        Integer,
        ForeignKey("bid_tender_parse_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    stage = Column(String(32), nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False, default=0, server_default="0")
    message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
