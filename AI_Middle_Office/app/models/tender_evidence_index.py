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


class BidEvidenceIndexJob(Base):
    """Reliable outbox job for one immutable evidence manifest snapshot."""

    __tablename__ = "bid_evidence_index_jobs"
    __table_args__ = (
        UniqueConstraint(
            "job_uuid",
            name="uq_bid_evidence_index_jobs_uuid",
        ),
        UniqueConstraint(
            "manifest_id",
            "index_schema_version",
            name="uq_bid_evidence_index_jobs_manifest_schema",
        ),
        Index(
            "ix_bid_evidence_index_jobs_project_status_created",
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
    manifest_id = Column(
        Integer,
        ForeignKey("bid_evidence_manifests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    manifest_version = Column(Integer, nullable=False, index=True)
    manifest_hash = Column(String(64), nullable=False, index=True)
    index_schema_version = Column(String(64), nullable=False, index=True)
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
    requested_block_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    indexed_block_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    search_service_url = Column(String(500), nullable=True)
    http_status = Column(Integer, nullable=True)
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
