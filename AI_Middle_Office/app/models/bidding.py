from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from app.core.database import Base


def _long_text():
    return Text().with_variant(LONGTEXT, "mysql")


class BidProject(Base):
    __tablename__ = "bid_projects"
    __table_args__ = (
        UniqueConstraint("project_uuid", name="uq_bid_projects_project_uuid"),
        Index("ix_bid_projects_status_updated", "status", "updated_at"),
        Index("ix_bid_projects_owner_status", "owner_user_id", "status"),
        Index("ix_bid_projects_created_by_status", "created_by", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_name = Column(String(255), nullable=False, index=True)
    tenderer_name = Column(String(255), nullable=True, index=True)
    tender_agency = Column(String(255), nullable=True)
    project_location = Column(String(255), nullable=True)
    project_type = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    tender_deadline_at = Column(DateTime(timezone=True), nullable=True, index=True)
    inquiry_deadline_at = Column(DateTime(timezone=True), nullable=True)
    bid_open_at = Column(DateTime(timezone=True), nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    summary_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    files = relationship("BidProjectFile", back_populates="project", cascade="all, delete-orphan", order_by="BidProjectFile.id")
    parse_runs = relationship("BidParseRun", back_populates="project", cascade="all, delete-orphan", order_by="BidParseRun.id")
    requirements = relationship("TenderRequirement", back_populates="project", cascade="all, delete-orphan")
    risks = relationship("TenderRisk", back_populates="project", cascade="all, delete-orphan")
    business_objects = relationship("TenderBusinessObject", back_populates="project", cascade="all, delete-orphan")
    response_items = relationship("TenderResponseItem", back_populates="project", cascade="all, delete-orphan")
    file_format_plans = relationship("BidFileFormatPlan", back_populates="project", cascade="all, delete-orphan")
    file_format_events = relationship("BidFileFormatPlanEvent", back_populates="project", cascade="all, delete-orphan")
    draft_sections = relationship("BidDraftSection", back_populates="project", cascade="all, delete-orphan")
    material_requirements = relationship("BidMaterialRequirement", back_populates="project", cascade="all, delete-orphan")
    material_requirement_events = relationship("BidMaterialRequirementEvent", back_populates="project", cascade="all, delete-orphan")


class BidProjectFile(Base):
    __tablename__ = "bid_project_files"
    __table_args__ = (
        UniqueConstraint("file_uuid", name="uq_bid_project_files_file_uuid"),
        Index("ix_bid_project_files_project_type", "project_id", "file_type"),
        Index("ix_bid_project_files_project_created", "project_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    file_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    file_type = Column(String(64), nullable=False, default="tender_document", server_default="tender_document", index=True)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0, server_default="0")
    sha256 = Column(String(64), nullable=False, index=True)
    parser_status = Column(String(32), nullable=False, default="parsed", server_default="parsed", index=True)
    parser_version = Column(String(64), nullable=False)
    extracted_text = Column(Text, nullable=True)
    segments_json = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=False, default=0, server_default="0")
    section_count = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="files")


class BidParseRun(Base):
    __tablename__ = "bid_parse_runs"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_bid_parse_runs_run_uuid"),
        Index("ix_bid_parse_runs_project_created", "project_id", "created_at"),
        Index("ix_bid_parse_runs_project_status", "project_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="running", server_default="running", index=True)
    parser_version = Column(String(64), nullable=False)
    input_file_ids_json = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="parse_runs")
    requirements = relationship("TenderRequirement", back_populates="parse_run", cascade="all, delete-orphan")
    risks = relationship("TenderRisk", back_populates="parse_run", cascade="all, delete-orphan")
    business_objects = relationship("TenderBusinessObject", back_populates="parse_run", cascade="all, delete-orphan")
    response_items = relationship("TenderResponseItem", back_populates="parse_run", cascade="all, delete-orphan")
    file_format_plans = relationship("BidFileFormatPlan", back_populates="parse_run", cascade="all, delete-orphan")
    file_format_events = relationship("BidFileFormatPlanEvent", back_populates="parse_run", cascade="all, delete-orphan")
    draft_sections = relationship("BidDraftSection", back_populates="parse_run", cascade="all, delete-orphan")
    material_requirements = relationship("BidMaterialRequirement", back_populates="parse_run", cascade="all, delete-orphan")
    material_requirement_events = relationship("BidMaterialRequirementEvent", back_populates="parse_run", cascade="all, delete-orphan")


class TenderRequirement(Base):
    __tablename__ = "tender_requirements"
    __table_args__ = (
        UniqueConstraint("requirement_uuid", name="uq_tender_requirements_requirement_uuid"),
        Index("ix_tender_requirements_project_type", "project_id", "requirement_type"),
        Index("ix_tender_requirements_run_type", "parse_run_id", "requirement_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    requirement_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey("bid_project_files.id"), nullable=True, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    requirement_type = Column(String(64), nullable=False, index=True)
    source_file = Column(String(255), nullable=True)
    source_location = Column(String(255), nullable=True)
    original_text = Column(Text, nullable=False)
    parsed_requirement = Column(Text, nullable=False)
    compliance_status = Column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    risk_level = Column(String(16), nullable=False, default="low", server_default="low", index=True)
    owner_role = Column(String(64), nullable=True)
    output_section = Column(String(128), nullable=True)
    confidence = Column(Float, nullable=False, default=0.6, server_default="0.6")
    extraction_method = Column(String(64), nullable=False, default="rule", server_default="rule")
    status = Column(String(32), nullable=False, default="active", server_default="active", index=True)
    reviewer_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="requirements")
    parse_run = relationship("BidParseRun", back_populates="requirements")
    file = relationship("BidProjectFile")


class TenderRisk(Base):
    __tablename__ = "tender_risks"
    __table_args__ = (
        UniqueConstraint("risk_uuid", name="uq_tender_risks_risk_uuid"),
        Index("ix_tender_risks_project_level", "project_id", "risk_level"),
        Index("ix_tender_risks_run_type", "parse_run_id", "risk_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    risk_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey("bid_project_files.id"), nullable=True, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    requirement_id = Column(Integer, ForeignKey("tender_requirements.id"), nullable=True, index=True)
    risk_type = Column(String(64), nullable=False, index=True)
    risk_level = Column(String(16), nullable=False, default="medium", server_default="medium", index=True)
    source_file = Column(String(255), nullable=True)
    source_location = Column(String(255), nullable=True)
    original_text = Column(Text, nullable=False)
    risk_explanation = Column(Text, nullable=False)
    impact_area = Column(String(128), nullable=True)
    suggested_action = Column(Text, nullable=True)
    is_blocking = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
    review_status = Column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    reviewer_note = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    confidence = Column(Float, nullable=False, default=0.6, server_default="0.6")
    extraction_method = Column(String(64), nullable=False, default="rule", server_default="rule")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="risks")
    parse_run = relationship("BidParseRun", back_populates="risks")
    file = relationship("BidProjectFile")
    requirement = relationship("TenderRequirement")


class TenderBusinessObject(Base):
    __tablename__ = "tender_business_objects"
    __table_args__ = (
        UniqueConstraint("object_uuid", name="uq_tender_business_objects_uuid"),
        Index("ix_tender_business_objects_project_type", "project_id", "object_type"),
        Index("ix_tender_business_objects_run_type", "parse_run_id", "object_type"),
        Index("ix_tender_business_objects_run_review", "parse_run_id", "review_status"),
        Index("ix_tender_business_objects_run_response", "parse_run_id", "response_required"),
    )

    id = Column(Integer, primary_key=True, index=True)
    object_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    file_id = Column(Integer, ForeignKey("bid_project_files.id"), nullable=True, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    requirement_id = Column(Integer, ForeignKey("tender_requirements.id"), nullable=True, index=True)
    risk_id = Column(Integer, ForeignKey("tender_risks.id"), nullable=True, index=True)
    object_type = Column(String(64), nullable=False, index=True)
    object_subtype = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    normalized_value = Column(String(255), nullable=True)
    normalized_json = Column(Text, nullable=True)
    source_file = Column(String(255), nullable=True)
    source_location = Column(String(255), nullable=True)
    original_text = Column(Text, nullable=False)
    source_count = Column(Integer, nullable=False, default=1, server_default="1")
    evidence_json = Column(Text, nullable=True)
    related_requirement_ids_json = Column(Text, nullable=True)
    related_risk_ids_json = Column(Text, nullable=True)
    document_section = Column(String(64), nullable=True, index=True)
    owner_role = Column(String(64), nullable=True)
    response_required = Column(Boolean, nullable=False, default=True, server_default="1", index=True)
    review_status = Column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    reviewer_note = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    confidence = Column(Float, nullable=False, default=0.6, server_default="0.6")
    extraction_method = Column(String(64), nullable=False, default="rule", server_default="rule")
    status = Column(String(32), nullable=False, default="active", server_default="active", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="business_objects")
    parse_run = relationship("BidParseRun", back_populates="business_objects")
    file = relationship("BidProjectFile")
    requirement = relationship("TenderRequirement")
    risk = relationship("TenderRisk")


class TenderResponseItem(Base):
    __tablename__ = "tender_response_items"
    __table_args__ = (
        UniqueConstraint("response_item_uuid", name="uq_tender_response_items_uuid"),
        UniqueConstraint("parse_run_id", "source_key", name="uq_tender_response_items_run_source"),
        Index("ix_tender_response_items_project_status", "project_id", "status"),
        Index("ix_tender_response_items_run_status", "parse_run_id", "status"),
        Index("ix_tender_response_items_run_action", "parse_run_id", "response_action"),
        Index("ix_tender_response_items_run_category", "parse_run_id", "response_category"),
    )

    id = Column(Integer, primary_key=True, index=True)
    response_item_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    business_object_id = Column(Integer, ForeignKey("tender_business_objects.id"), nullable=True, index=True)
    requirement_id = Column(Integer, ForeignKey("tender_requirements.id"), nullable=True, index=True)
    risk_id = Column(Integer, ForeignKey("tender_risks.id"), nullable=True, index=True)
    source_key = Column(String(128), nullable=False, index=True)
    response_category = Column(String(64), nullable=False, index=True)
    response_action = Column(String(64), nullable=False, index=True)
    response_title = Column(String(255), nullable=False)
    source_text = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=True)
    owner_role = Column(String(64), nullable=True)
    risk_level = Column(String(16), nullable=False, default="low", server_default="low", index=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    response_note = Column(Text, nullable=True)
    reviewer_note = Column(Text, nullable=True)
    created_from = Column(String(64), nullable=False, default="business_object", server_default="business_object", index=True)
    normalized_json = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="response_items")
    parse_run = relationship("BidParseRun", back_populates="response_items")
    business_object = relationship("TenderBusinessObject")
    requirement = relationship("TenderRequirement")
    risk = relationship("TenderRisk")


class BidFileFormatPlan(Base):
    __tablename__ = "bid_file_format_plans"
    __table_args__ = (
        UniqueConstraint("plan_uuid", name="uq_bid_file_format_plans_uuid"),
        UniqueConstraint("parse_run_id", name="uq_bid_file_format_plans_run"),
        Index("ix_bid_file_format_plans_project_status", "project_id", "review_status"),
        Index("ix_bid_file_format_plans_run_status", "parse_run_id", "review_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    plan_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    format_version = Column(String(64), nullable=False, index=True)
    format_source = Column(String(64), nullable=False, default="not_found", server_default="not_found", index=True)
    package_mode = Column(String(64), nullable=False, default="unknown", server_default="unknown", index=True)
    review_status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    structure_json = Column(Text, nullable=False)
    summary_json = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    reviewer_note = Column(Text, nullable=True)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="file_format_plans")
    parse_run = relationship("BidParseRun", back_populates="file_format_plans")
    material_requirements = relationship("BidMaterialRequirement", back_populates="format_plan")
    events = relationship(
        "BidFileFormatPlanEvent",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="BidFileFormatPlanEvent.id",
    )


class BidFileFormatPlanEvent(Base):
    __tablename__ = "bid_file_format_plan_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_bid_file_format_plan_events_uuid"),
        Index("ix_bid_file_format_plan_events_plan_created", "plan_id", "created_at"),
        Index("ix_bid_file_format_plan_events_project_created", "project_id", "created_at"),
        Index("ix_bid_file_format_plan_events_run_created", "parse_run_id", "created_at"),
        Index("ix_bid_file_format_plan_events_type", "event_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_uuid = Column(String(36), nullable=False, unique=True, index=True)
    plan_id = Column(Integer, ForeignKey("bid_file_format_plans.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    item_key = Column(String(255), nullable=True, index=True)
    item_title = Column(String(255), nullable=True)
    from_package_key = Column(String(64), nullable=True, index=True)
    to_package_key = Column(String(64), nullable=True, index=True)
    detail_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    plan = relationship("BidFileFormatPlan", back_populates="events")
    project = relationship("BidProject", back_populates="file_format_events")
    parse_run = relationship("BidParseRun", back_populates="file_format_events")


class BidDraftSection(Base):
    __tablename__ = "bid_draft_sections"
    __table_args__ = (
        UniqueConstraint("draft_uuid", name="uq_bid_draft_sections_uuid"),
        UniqueConstraint("parse_run_id", "section_key", name="uq_bid_draft_sections_run_section"),
        Index("ix_bid_draft_sections_project_status", "project_id", "review_status"),
        Index("ix_bid_draft_sections_run_type", "parse_run_id", "section_type"),
        Index("ix_bid_draft_sections_run_section", "parse_run_id", "section_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    draft_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    section_key = Column(String(255), nullable=False, index=True)
    section_title = Column(String(255), nullable=False)
    section_type = Column(String(64), nullable=False, index=True)
    owner_role = Column(String(64), nullable=True)
    draft_mode = Column(String(32), nullable=False, default="placeholder", server_default="placeholder", index=True)
    draft_status = Column(String(32), nullable=False, default="needs_input", server_default="needs_input", index=True)
    content_version = Column(Integer, nullable=False, default=1, server_default="1")
    content_markdown = Column(Text, nullable=False)
    placeholders_json = Column(Text, nullable=True)
    source_response_item_uuids_json = Column(Text, nullable=True)
    source_requirement_ids_json = Column(Text, nullable=True)
    source_risk_ids_json = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    generation_decision_json = Column(Text, nullable=True)
    generator_type = Column(String(32), nullable=False, default="rule", server_default="rule", index=True)
    generator_model = Column(String(128), nullable=True)
    review_status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    reviewer_note = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="draft_sections")
    parse_run = relationship("BidParseRun", back_populates="draft_sections")
    versions = relationship(
        "BidDraftSectionVersion",
        back_populates="draft_section",
        cascade="all, delete-orphan",
        order_by="BidDraftSectionVersion.version_no",
    )


class BidDraftSectionVersion(Base):
    __tablename__ = "bid_draft_section_versions"
    __table_args__ = (
        UniqueConstraint("version_uuid", name="uq_bid_draft_section_versions_uuid"),
        UniqueConstraint("draft_section_id", "version_no", name="uq_bid_draft_section_versions_section_no"),
        Index("ix_bid_draft_section_versions_section", "draft_section_id", "version_no"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_uuid = Column(String(36), nullable=False, unique=True, index=True)
    draft_section_id = Column(Integer, ForeignKey("bid_draft_sections.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    change_type = Column(String(32), nullable=False, default="generated", server_default="generated", index=True)
    content_markdown = Column(Text, nullable=False)
    editor_note = Column(Text, nullable=True)
    generator_type = Column(String(32), nullable=True)
    generator_model = Column(String(128), nullable=True)
    edited_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    draft_section = relationship("BidDraftSection", back_populates="versions")


class BidMaterialRequirement(Base):
    __tablename__ = "bid_material_requirements"
    __table_args__ = (
        UniqueConstraint("requirement_uuid", name="uq_bid_material_requirements_uuid"),
        UniqueConstraint("parse_run_id", "material_key", name="uq_bid_material_requirements_run_key"),
        Index("ix_bid_material_requirements_project_status", "project_id", "status"),
        Index("ix_bid_material_requirements_run_status", "parse_run_id", "status"),
        Index("ix_bid_material_requirements_run_category", "parse_run_id", "profile_category"),
        Index("ix_bid_material_requirements_format_item", "parse_run_id", "format_item_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    requirement_uuid = Column(String(36), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    format_plan_id = Column(Integer, ForeignKey("bid_file_format_plans.id"), nullable=True, index=True)
    format_item_key = Column(String(255), nullable=False, index=True)
    package_key = Column(String(64), nullable=True, index=True)
    package_title = Column(String(255), nullable=True)
    section_key = Column(String(255), nullable=True, index=True)
    item_title = Column(String(255), nullable=False)
    requirement_type = Column(String(64), nullable=False, index=True)
    profile_category = Column(String(64), nullable=True, index=True)
    material_key = Column(String(128), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    source_file = Column(String(255), nullable=True)
    source_location = Column(String(255), nullable=True)
    source_text = Column(Text, nullable=True)
    fulfillment_mode = Column(String(64), nullable=False, default="manual_upload", server_default="manual_upload", index=True)
    status = Column(String(32), nullable=False, default="missing", server_default="missing", index=True)
    priority = Column(String(16), nullable=False, default="normal", server_default="normal", index=True)
    owner_role = Column(String(64), nullable=True)
    candidate_profile_item_uuid = Column(String(36), nullable=True, index=True)
    submitted_profile_item_uuid = Column(String(36), nullable=True, index=True)
    submitted_file_id = Column(String(36), ForeignKey("file_objects.file_id"), nullable=True, index=True)
    submitted_value = Column(_long_text(), nullable=True)
    notes = Column(Text, nullable=True)
    normalized_json = Column(_long_text(), nullable=True)
    evidence_json = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("BidProject", back_populates="material_requirements")
    parse_run = relationship("BidParseRun", back_populates="material_requirements")
    format_plan = relationship("BidFileFormatPlan", back_populates="material_requirements")
    submitted_file = relationship("FileObject")
    events = relationship(
        "BidMaterialRequirementEvent",
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="BidMaterialRequirementEvent.id",
    )


class BidMaterialRequirementEvent(Base):
    __tablename__ = "bid_material_requirement_events"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_bid_material_requirement_events_uuid"),
        Index("ix_bid_material_requirement_events_requirement_created", "requirement_id", "created_at"),
        Index("ix_bid_material_requirement_events_project_created", "project_id", "created_at"),
        Index("ix_bid_material_requirement_events_run_created", "parse_run_id", "created_at"),
        Index("ix_bid_material_requirement_events_type", "event_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_uuid = Column(String(36), nullable=False, unique=True, index=True)
    requirement_id = Column(Integer, ForeignKey("bid_material_requirements.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("bid_projects.id"), nullable=False, index=True)
    parse_run_id = Column(Integer, ForeignKey("bid_parse_runs.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    old_status = Column(String(32), nullable=True)
    new_status = Column(String(32), nullable=True)
    detail_json = Column(_long_text(), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    requirement = relationship("BidMaterialRequirement", back_populates="events")
    project = relationship("BidProject", back_populates="material_requirement_events")
    parse_run = relationship("BidParseRun", back_populates="material_requirement_events")
