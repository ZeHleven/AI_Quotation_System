from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.mysql import LONGTEXT

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class BidEvidenceDocument(Base):
    """Immutable parsed document version used by the tender evidence MCP."""

    __tablename__ = "bid_evidence_documents"
    __table_args__ = (
        UniqueConstraint(
            "evidence_document_uuid",
            name="uq_bid_evidence_documents_uuid",
        ),
        UniqueConstraint(
            "project_id",
            "source_file_id",
            name="uq_bid_evidence_documents_project_source",
        ),
        UniqueConstraint(
            "project_id",
            "document_key",
            "version_no",
            name="uq_bid_evidence_documents_project_key_version",
        ),
        Index(
            "ix_bid_evidence_documents_project_active",
            "project_id",
            "active",
        ),
        Index(
            "ix_bid_evidence_documents_project_key_version",
            "project_id",
            "document_key",
            "version_no",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    evidence_document_uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_file_id = Column(
        Integer,
        ForeignKey("bid_project_files.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_key = Column(String(160), nullable=False, index=True)
    document_type = Column(String(64), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    original_filename = Column(String(255), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    parser_version = Column(String(64), nullable=False)
    body_storage_backend = Column(
        String(32),
        nullable=False,
        default="mysql_legacy",
        server_default="mysql_legacy",
        index=True,
    )
    body_bucket = Column(String(128), nullable=True)
    body_object_name = Column(String(512), nullable=True)
    body_sha256 = Column(String(64), nullable=True, index=True)
    body_size_bytes = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    body_schema_version = Column(String(64), nullable=True)
    parse_status = Column(
        String(16),
        nullable=False,
        default="ready",
        server_default="ready",
        index=True,
    )
    active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
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
    activated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    superseded_at = Column(DateTime(timezone=True), nullable=True)


class BidEvidenceBlock(Base):
    """Immutable normalized text block with a stable content hash."""

    __tablename__ = "bid_evidence_blocks"
    __table_args__ = (
        UniqueConstraint("evidence_id", name="uq_bid_evidence_blocks_evidence_id"),
        UniqueConstraint("block_id", name="uq_bid_evidence_blocks_block_id"),
        UniqueConstraint(
            "document_id",
            "block_order",
            name="uq_bid_evidence_blocks_document_order",
        ),
        Index(
            "ix_bid_evidence_blocks_project_document_order",
            "project_id",
            "document_id",
            "block_order",
        ),
        Index(
            "ix_bid_evidence_blocks_project_content_hash",
            "project_id",
            "content_hash",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    evidence_id = Column(String(80), nullable=False, unique=True, index=True)
    block_id = Column(String(80), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id = Column(
        Integer,
        ForeignKey("bid_evidence_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    block_order = Column(Integer, nullable=False)
    page = Column(Integer, nullable=True)
    sheet = Column(String(160), nullable=True)
    cell_range = Column(String(80), nullable=True)
    section = Column(String(500), nullable=True)
    locator_json = Column(_long_text(), nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    content = Column(_long_text(), nullable=True)
    content_length = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    keywords_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BidEvidenceManifest(Base):
    """Append-only manifest snapshot for one project evidence state."""

    __tablename__ = "bid_evidence_manifests"
    __table_args__ = (
        UniqueConstraint(
            "manifest_uuid",
            name="uq_bid_evidence_manifests_uuid",
        ),
        UniqueConstraint(
            "project_id",
            "version_no",
            name="uq_bid_evidence_manifests_project_version",
        ),
        Index(
            "ix_bid_evidence_manifests_project_active",
            "project_id",
            "active",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    manifest_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)
    manifest_hash = Column(String(64), nullable=False, index=True)
    snapshot_json = Column(_long_text(), nullable=False)
    active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
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
    superseded_at = Column(DateTime(timezone=True), nullable=True)


class BidEvidenceReadAudit(Base):
    """Append-only audit event for one context read performed by an Agent run."""

    __tablename__ = "bid_evidence_read_audits"
    __table_args__ = (
        UniqueConstraint(
            "audit_uuid",
            name="uq_bid_evidence_read_audits_uuid",
        ),
        Index(
            "ix_bid_evidence_read_audits_project_run_created",
            "project_id",
            "agent_run_id",
            "created_at",
        ),
        Index(
            "ix_bid_evidence_read_audits_project_evidence_run",
            "project_id",
            "evidence_block_id",
            "agent_run_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    audit_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_document_id = Column(
        Integer,
        ForeignKey("bid_evidence_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_block_id = Column(
        Integer,
        ForeignKey("bid_evidence_blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assessment_id = Column(String(160), nullable=False, index=True)
    agent_run_id = Column(String(160), nullable=False, index=True)
    subject = Column(String(200), nullable=False, index=True)
    capability = Column(
        String(64),
        nullable=False,
        default="read_evidence_context",
        server_default="read_evidence_context",
        index=True,
    )
    trace_id = Column(String(160), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
