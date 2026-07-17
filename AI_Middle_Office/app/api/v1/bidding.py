from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.bidding import (
    BidFileFormatPlan,
    BidMaterialRequirement,
    BidParseRun,
    BidProject,
    BidProjectFile,
    BidDraftSection,
    TenderBusinessObject,
    TenderRequirement,
    TenderResponseItem,
    TenderRisk,
)
from app.models.user import User
from app.services.bidding_parser import (
    BIDDING_PARSER_VERSION,
    TenderParseError,
    analyze_tender_segments,
    dumps_json,
    extract_tender_text,
    loads_json,
)
from app.services.bidding_business_objects import build_business_object_summary, build_tender_business_objects
from app.services.bidding_llm_review import (
    build_business_object_review_context,
    clean_llm_review_payload,
    review_uncertain_business_objects_with_deepseek,
)
from app.services.bidding_risk_cards import build_risk_card_summary, cluster_tender_risks_to_cards
from app.services.bidding_file_format import (
    BID_FILE_FORMAT_REVIEW_STATUSES,
    BidFileFormatError,
    confirm_bid_file_format_plan,
    generate_bid_file_format_plan,
    get_bid_file_format_plan,
    preview_bid_file_format_plan,
    serialize_bid_file_format_plan,
    update_bid_file_format_plan_review,
)
from app.services.bidding_material_requirements import (
    BID_MATERIAL_REQUIREMENT_STATUSES,
    BidMaterialRequirementError,
    build_bid_material_requirement_summary,
    generate_bid_material_requirements,
    get_bid_material_requirement_by_uuid,
    list_bid_material_requirements,
    serialize_bid_material_requirement,
    update_bid_material_requirement,
)
from app.services.bidding_technical_composition import (
    BidTechnicalCompositionError,
    generate_bid_technical_composition_plan,
    get_bid_technical_composition_plan,
)
from app.services.bidding_draft_outline import generate_bid_draft_outline
from app.services.bidding_draft_sections import (
    BID_DRAFT_REVIEW_STATUSES,
    BidDraftSectionError,
    generate_bid_draft_section,
    generate_technical_bid_draft_from_composition,
    list_bid_draft_sections,
    serialize_bid_draft_section,
    update_bid_draft_section_content,
    update_bid_draft_section_review,
)
from app.services.bidding_response_matrix import (
    RESPONSE_ACTIONS,
    RESPONSE_STATUSES,
    build_response_matrix_summary,
    generate_response_matrix_items,
    is_valid_response_review_role,
    is_superseded_response_item,
    response_item_matches_review_role,
    response_item_primary_review_role,
    response_item_review_roles,
    response_item_supporting_roles,
)
from app.services.bidding_tender_analysis import (
    analyze_tender_risk_clause_with_llm,
    build_tender_risk_clause_export_document,
    build_tender_analysis_export_document,
    build_tender_analysis_preview_with_semantic_summary,
    get_cached_tender_risk_clause_llm,
)
from app.services.bidding_technical_word_export import (
    BidTechnicalWordExportError,
    build_technical_bid_draft_export_document,
    build_technical_bid_final_export_document,
    build_technical_bid_final_export_quality_report,
)
from app.services.rbac import has_any_role


router = APIRouter()
logger = logging.getLogger(__name__)


BIDDING_FILE_TYPES = {
    "tender_document",
    "bill_of_quantities",
    "drawing",
    "addendum",
    "clarification",
    "contract",
    "brand_table",
    "other",
}
PRIMARY_TENDER_FILE_SUFFIXES = {".pdf", ".docx"}
PROJECT_STATUSES = {"draft", "files_uploaded", "parsed", "reviewing", "archived"}
RISK_REVIEW_STATUSES = {"pending", "confirmed", "ignored", "to_clarify", "to_quote_allowance"}
BUSINESS_OBJECT_REVIEW_STATUSES = {"pending", "confirmed", "ignored", "to_clarify", "to_quote_allowance"}
BUSINESS_OBJECT_LLM_DECISION_ACTIONS = {"accept", "reject", "modify"}


class BidProjectCreate(BaseModel):
    project_name: str
    tenderer_name: Optional[str] = None
    tender_agency: Optional[str] = None
    project_location: Optional[str] = None
    project_type: Optional[str] = None
    tender_deadline_at: Optional[str] = None
    inquiry_deadline_at: Optional[str] = None
    bid_open_at: Optional[str] = None
    owner_user_id: Optional[int] = None


class BidProjectUpdate(BaseModel):
    project_name: Optional[str] = None
    tenderer_name: Optional[str] = None
    tender_agency: Optional[str] = None
    project_location: Optional[str] = None
    project_type: Optional[str] = None
    status: Optional[str] = None
    tender_deadline_at: Optional[str] = None
    inquiry_deadline_at: Optional[str] = None
    bid_open_at: Optional[str] = None
    owner_user_id: Optional[int] = None


class BidParseRequest(BaseModel):
    file_uuids: list[str] = Field(default_factory=list)


class RiskReviewRequest(BaseModel):
    review_status: str
    reviewer_note: Optional[str] = None


class BusinessObjectReviewRequest(BaseModel):
    review_status: str
    reviewer_note: Optional[str] = None


class BusinessObjectLlmReviewRequest(BaseModel):
    run_uuid: Optional[str] = "latest"
    limit: int = Field(default=25, ge=1, le=50)
    force: bool = False
    only_pending: bool = True
    object_uuids: list[str] = Field(default_factory=list)


class BusinessObjectLlmReviewDecisionRequest(BaseModel):
    action: str
    reviewer_note: Optional[str] = None
    modified_review: dict[str, Any] = Field(default_factory=dict)


class ResponseMatrixGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"


class BidDraftOutlineGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"
    package_key: Optional[str] = None


class BidFileFormatPlanGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"


class BidFileFormatPlanConfirmRequest(BaseModel):
    structure: Optional[dict[str, Any]] = None
    reviewer_note: Optional[str] = None
    edit_events: list[dict[str, Any]] = Field(default_factory=list)


class BidFileFormatPlanReviewRequest(BaseModel):
    review_status: str
    reviewer_note: Optional[str] = None


class BidMaterialRequirementGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"
    package_key: Optional[str] = None


class BidTechnicalCompositionGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"


class BidMaterialRequirementUpdateRequest(BaseModel):
    status: Optional[str] = None
    submitted_profile_item_uuid: Optional[str] = None
    submitted_profile_item_uuids: Optional[list[str]] = None
    submitted_file_id: Optional[str] = None
    submitted_file_ids: Optional[list[str]] = None
    submitted_value: Optional[str] = None
    notes: Optional[str] = None


class BidDraftSectionGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"
    section_key: str
    generator_type: Optional[str] = "rule"
    package_key: Optional[str] = None


class BidTechnicalDraftGenerateRequest(BaseModel):
    run_uuid: Optional[str] = "latest"
    overwrite: bool = True


class BidDraftSectionReviewRequest(BaseModel):
    review_status: str
    reviewer_note: Optional[str] = None


class BidDraftSectionContentUpdateRequest(BaseModel):
    content_markdown: str
    editor_note: Optional[str] = None


class ResponseItemUpdateRequest(BaseModel):
    response_action: Optional[str] = None
    status: Optional[str] = None
    owner_role: Optional[str] = None
    response_note: Optional[str] = None
    reviewer_note: Optional[str] = None


def _ensure_feature_enabled() -> None:
    if not settings.feature_bidding_mvp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NOT_FOUND")


def _clean_text(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    return value[:limit]


def _require_bidding_access(current_user: User) -> None:
    if not has_any_role(current_user, {"admin", "system_admin", "staff", "manager", "quote_user", "quote_operator"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def _can_view_all(current_user: User) -> bool:
    return has_any_role(current_user, {"admin", "system_admin", "manager", "quote_operator"})


def _can_manage_project(current_user: User, project: BidProject) -> bool:
    return _can_view_all(current_user) or project.created_by == current_user.id or project.owner_user_id == current_user.id


def _require_project_access(current_user: User, project: BidProject) -> None:
    if not _can_manage_project(current_user, project):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_PROJECT_NOT_FOUND")


def _parse_dt(value: str | None, field_name: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    normalized = value.replace("T", " ").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"INVALID_{field_name.upper()}")


def _filename_stem(filename: str | None) -> str | None:
    stem = Path(filename or "").stem.strip()
    return stem[:255] or None


def _safe_download_stem(value: str | None) -> str:
    text = "".join(char if char not in '\\/:*?"<>|\r\n\t' else "_" for char in str(value or "").strip())
    return (text[:80].strip(" ._") or "tender_analysis")


def _ensure_primary_tender_file(filename: str | None) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in PRIMARY_TENDER_FILE_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PRIMARY_TENDER_FILE_TYPE")


def _get_user_or_current(db: Session, user_id: int | None, current_user: User) -> User:
    if user_id is None:
        return current_user
    if not _can_view_all(current_user) and user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_OWNER_USER")
    return user


def _project_query(db: Session, current_user: User):
    query = db.query(BidProject)
    if not _can_view_all(current_user):
        query = query.filter(or_(BidProject.created_by == current_user.id, BidProject.owner_user_id == current_user.id))
    return query


def _get_project(db: Session, project_uuid: str, current_user: User) -> BidProject:
    project = db.query(BidProject).filter(BidProject.project_uuid == project_uuid).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_PROJECT_NOT_FOUND")
    _require_project_access(current_user, project)
    return project


def _latest_parse_run(db: Session, project_id: int) -> BidParseRun | None:
    return (
        db.query(BidParseRun)
        .filter(BidParseRun.project_id == project_id, BidParseRun.status == "completed")
        .order_by(BidParseRun.id.desc())
        .first()
    )


def _format_dt(value: Any) -> str | None:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _summary_counts(db: Session, project: BidProject) -> dict[str, Any]:
    latest = _latest_parse_run(db, project.id)
    return {
        "file_count": db.query(BidProjectFile).filter(BidProjectFile.project_id == project.id).count(),
        "parse_run_count": db.query(BidParseRun).filter(BidParseRun.project_id == project.id).count(),
        "latest_parse_run": _serialize_parse_run(latest) if latest else None,
        "requirement_count": db.query(TenderRequirement).filter(TenderRequirement.project_id == project.id).count(),
        "risk_count": db.query(TenderRisk).filter(TenderRisk.project_id == project.id).count(),
        "business_object_count": db.query(TenderBusinessObject).filter(TenderBusinessObject.project_id == project.id).count(),
        "response_item_count": db.query(TenderResponseItem).filter(TenderResponseItem.project_id == project.id).count(),
        "pending_business_object_count": db.query(TenderBusinessObject).filter(
            TenderBusinessObject.project_id == project.id,
            TenderBusinessObject.review_status == "pending",
        ).count(),
        "pending_risk_count": db.query(TenderRisk).filter(TenderRisk.project_id == project.id, TenderRisk.review_status == "pending").count(),
        "high_risk_count": db.query(TenderRisk).filter(TenderRisk.project_id == project.id, TenderRisk.risk_level == "high").count(),
    }


def _serialize_project(project: BidProject, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "id": project.id,
        "project_uuid": project.project_uuid,
        "project_name": project.project_name,
        "tenderer_name": project.tenderer_name,
        "tender_agency": project.tender_agency,
        "project_location": project.project_location,
        "project_type": project.project_type,
        "status": project.status,
        "tender_deadline_at": _format_dt(project.tender_deadline_at),
        "inquiry_deadline_at": _format_dt(project.inquiry_deadline_at),
        "bid_open_at": _format_dt(project.bid_open_at),
        "owner_user_id": project.owner_user_id,
        "created_by": project.created_by,
        "summary": loads_json(project.summary_json, {}),
        "created_at": _format_dt(project.created_at),
        "updated_at": _format_dt(project.updated_at),
    }
    if summary is not None:
        data["counts"] = summary
    return data


def _serialize_file(file_obj: BidProjectFile, *, include_text: bool = False) -> dict[str, Any]:
    data = {
        "id": file_obj.id,
        "file_uuid": file_obj.file_uuid,
        "project_id": file_obj.project_id,
        "file_type": file_obj.file_type,
        "original_filename": file_obj.original_filename,
        "content_type": file_obj.content_type,
        "size_bytes": file_obj.size_bytes,
        "sha256": file_obj.sha256,
        "parser_status": file_obj.parser_status,
        "parser_version": file_obj.parser_version,
        "page_count": file_obj.page_count,
        "section_count": file_obj.section_count,
        "error_message": file_obj.error_message,
        "uploaded_by": file_obj.uploaded_by,
        "created_at": _format_dt(file_obj.created_at),
    }
    if include_text:
        data["extracted_text"] = file_obj.extracted_text
        data["segments"] = loads_json(file_obj.segments_json, [])
    return data


async def _build_bid_project_file_from_upload(
    *,
    project: BidProject,
    file: UploadFile,
    file_type: str,
    current_user: User,
) -> BidProjectFile:
    if file_type not in BIDDING_FILE_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_BID_FILE_TYPE")
    content = await file.read()
    try:
        extracted = await asyncio.to_thread(extract_tender_text, content, file.filename, file.content_type)
    except TenderParseError as exc:
        logger.warning(
            "Bidding file parse rejected project_uuid=%s filename=%r content_type=%r size_bytes=%d file_header=%s reason=%s",
            project.project_uuid,
            file.filename,
            file.content_type,
            len(content),
            content[:8].hex(),
            exc,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return BidProjectFile(
        file_uuid=str(uuid.uuid4()),
        project_id=project.id,
        file_type=file_type,
        original_filename=extracted["filename"],
        content_type=file.content_type,
        size_bytes=len(content),
        sha256=extracted["sha256"],
        parser_status="parsed",
        parser_version=extracted["parser_version"],
        extracted_text=extracted["text"],
        segments_json=dumps_json(extracted["segments"]),
        page_count=extracted["page_count"],
        section_count=extracted["section_count"],
        uploaded_by=current_user.id,
    )


def _serialize_parse_run(run: BidParseRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "run_uuid": run.run_uuid,
        "project_id": run.project_id,
        "status": run.status,
        "parser_version": run.parser_version,
        "input_file_ids": loads_json(run.input_file_ids_json, []),
        "summary": loads_json(run.summary_json, {}),
        "error_message": run.error_message,
        "created_by": run.created_by,
        "started_at": _format_dt(run.started_at),
        "finished_at": _format_dt(run.finished_at),
        "created_at": _format_dt(run.created_at),
    }


def _serialize_requirement(item: TenderRequirement) -> dict[str, Any]:
    return {
        "id": item.id,
        "requirement_uuid": item.requirement_uuid,
        "project_id": item.project_id,
        "file_id": item.file_id,
        "parse_run_id": item.parse_run_id,
        "requirement_type": item.requirement_type,
        "source_file": item.source_file,
        "source_location": item.source_location,
        "original_text": item.original_text,
        "parsed_requirement": item.parsed_requirement,
        "compliance_status": item.compliance_status,
        "risk_level": item.risk_level,
        "owner_role": item.owner_role,
        "output_section": item.output_section,
        "confidence": item.confidence,
        "extraction_method": item.extraction_method,
        "status": item.status,
        "reviewer_note": item.reviewer_note,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }


def _serialize_risk(item: TenderRisk) -> dict[str, Any]:
    return {
        "id": item.id,
        "risk_uuid": item.risk_uuid,
        "project_id": item.project_id,
        "file_id": item.file_id,
        "parse_run_id": item.parse_run_id,
        "requirement_id": item.requirement_id,
        "risk_type": item.risk_type,
        "risk_level": item.risk_level,
        "source_file": item.source_file,
        "source_location": item.source_location,
        "original_text": item.original_text,
        "risk_explanation": item.risk_explanation,
        "impact_area": item.impact_area,
        "suggested_action": item.suggested_action,
        "is_blocking": bool(item.is_blocking),
        "review_status": item.review_status,
        "reviewer_note": item.reviewer_note,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": _format_dt(item.reviewed_at),
        "confidence": item.confidence,
        "extraction_method": item.extraction_method,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }


def _serialize_business_object(item: TenderBusinessObject) -> dict[str, Any]:
    return {
        "id": item.id,
        "object_uuid": item.object_uuid,
        "project_id": item.project_id,
        "file_id": item.file_id,
        "parse_run_id": item.parse_run_id,
        "requirement_id": item.requirement_id,
        "risk_id": item.risk_id,
        "object_type": item.object_type,
        "object_subtype": item.object_subtype,
        "title": item.title,
        "normalized_value": item.normalized_value,
        "normalized": loads_json(item.normalized_json, {}),
        "source_file": item.source_file,
        "source_location": item.source_location,
        "original_text": item.original_text,
        "source_count": item.source_count,
        "evidence": loads_json(item.evidence_json, []),
        "related_requirement_ids": loads_json(item.related_requirement_ids_json, []),
        "related_risk_ids": loads_json(item.related_risk_ids_json, []),
        "document_section": item.document_section,
        "owner_role": item.owner_role,
        "response_required": bool(item.response_required),
        "review_status": item.review_status,
        "reviewer_note": item.reviewer_note,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": _format_dt(item.reviewed_at),
        "confidence": item.confidence,
        "extraction_method": item.extraction_method,
        "status": item.status,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }


def _serialize_response_item(item: TenderResponseItem) -> dict[str, Any]:
    business_object = item.business_object
    requirement = item.requirement
    risk = item.risk
    normalized = loads_json(item.normalized_json, {})
    coverage = normalized.get("coverage") if isinstance(normalized.get("coverage"), dict) else {}
    workflow_actions = normalized.get("workflow_actions") if isinstance(normalized.get("workflow_actions"), list) else []
    quality_flags = normalized.get("quality_flags") if isinstance(normalized.get("quality_flags"), list) else []
    return {
        "id": item.id,
        "response_item_uuid": item.response_item_uuid,
        "project_id": item.project_id,
        "parse_run_id": item.parse_run_id,
        "business_object_id": item.business_object_id,
        "business_object_uuid": business_object.object_uuid if business_object else None,
        "business_object_title": business_object.title if business_object else None,
        "requirement_id": item.requirement_id,
        "requirement_uuid": requirement.requirement_uuid if requirement else None,
        "risk_id": item.risk_id,
        "risk_uuid": risk.risk_uuid if risk else None,
        "source_key": item.source_key,
        "response_category": item.response_category,
        "response_action": item.response_action,
        "response_title": item.response_title,
        "source_text": item.source_text,
        "evidence": loads_json(item.evidence_json, []),
        "owner_role": item.owner_role,
        "risk_level": item.risk_level,
        "status": item.status,
        "response_note": item.response_note,
        "reviewer_note": item.reviewer_note,
        "created_from": item.created_from,
        "normalized": normalized,
        "linked_actions": workflow_actions,
        "coverage": coverage,
        "coverage_explanation": normalized.get("coverage_explanation") or coverage.get("explanation"),
        "covered_requirement_count": len(coverage.get("requirement_ids") or []),
        "covered_risk_count": len(coverage.get("risk_ids") or []),
        "review_roles": response_item_review_roles(item),
        "primary_review_role": response_item_primary_review_role(item),
        "supporting_roles": response_item_supporting_roles(item),
        "review_action": normalized.get("review_action"),
        "review_action_label": normalized.get("review_action_label"),
        "done_criteria": normalized.get("done_criteria"),
        "done_checklist": normalized.get("done_checklist") if isinstance(normalized.get("done_checklist"), list) else [],
        "coverage_classification": normalized.get("coverage_classification"),
        "granularity_level": normalized.get("granularity_level"),
        "quality_score": normalized.get("quality_score"),
        "task_display_type": normalized.get("task_display_type"),
        "task_display_label": normalized.get("task_display_label"),
        "task_group_key": normalized.get("task_group_key"),
        "task_group_parent_title": normalized.get("task_group_parent_title"),
        "task_group_index": normalized.get("task_group_index"),
        "task_group_child_count": normalized.get("task_group_child_count"),
        "has_group_children": bool(normalized.get("has_group_children")),
        "review_priority": normalized.get("review_priority"),
        "review_priority_label": normalized.get("review_priority_label"),
        "review_wave": normalized.get("review_wave"),
        "review_wave_label": normalized.get("review_wave_label"),
        "priority_reason": normalized.get("priority_reason"),
        "quality_flags": quality_flags,
        "quality_explanation": normalized.get("quality_explanation"),
        "split_parent_uuid": normalized.get("split_parent_uuid"),
        "split_parent_title": normalized.get("split_parent_title"),
        "superseded": bool(normalized.get("superseded_by")),
        "reviewed_by": item.reviewed_by,
        "reviewed_at": _format_dt(item.reviewed_at),
        "created_by": item.created_by,
        "created_at": _format_dt(item.created_at),
        "updated_at": _format_dt(item.updated_at),
    }


@router.post("/admin/bidding/projects", summary="创建投标项目")
async def create_bid_project(
    payload: BidProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    name = _clean_text(payload.project_name, 255)
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_NAME_REQUIRED")
    owner = _get_user_or_current(db, payload.owner_user_id, current_user)
    project = BidProject(
        project_uuid=str(uuid.uuid4()),
        project_name=name,
        tenderer_name=_clean_text(payload.tenderer_name, 255),
        tender_agency=_clean_text(payload.tender_agency, 255),
        project_location=_clean_text(payload.project_location, 255),
        project_type=_clean_text(payload.project_type, 64),
        status="draft",
        tender_deadline_at=_parse_dt(payload.tender_deadline_at, "tender_deadline_at"),
        inquiry_deadline_at=_parse_dt(payload.inquiry_deadline_at, "inquiry_deadline_at"),
        bid_open_at=_parse_dt(payload.bid_open_at, "bid_open_at"),
        owner_user_id=owner.id,
        created_by=current_user.id,
        summary_json=dumps_json({"biz_stage": "BIZ-4a", "mvp": True}),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return api_ok(_serialize_project(project, summary=_summary_counts(db, project)))


@router.post("/admin/bidding/projects/from-tender-file", summary="上传甲方招标文件并创建投标项目")
async def create_bid_project_from_tender_file(
    file: UploadFile = File(...),
    project_name: str | None = Form(None),
    tenderer_name: str | None = Form(None),
    tender_agency: str | None = Form(None),
    project_location: str | None = Form(None),
    project_type: str | None = Form(None),
    tender_deadline_at: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    _ensure_primary_tender_file(file.filename)
    name = _clean_text(project_name, 255) or _filename_stem(file.filename)
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_NAME_REQUIRED")
    project = BidProject(
        project_uuid=str(uuid.uuid4()),
        project_name=name,
        tenderer_name=_clean_text(tenderer_name, 255),
        tender_agency=_clean_text(tender_agency, 255),
        project_location=_clean_text(project_location, 255),
        project_type=_clean_text(project_type, 64),
        status="draft",
        tender_deadline_at=_parse_dt(tender_deadline_at, "tender_deadline_at"),
        owner_user_id=current_user.id,
        created_by=current_user.id,
        summary_json=dumps_json({"biz_stage": "BIZ-4a", "mvp": True, "input_mode": "primary_tender_file"}),
    )
    db.add(project)
    db.flush()
    file_obj = await _build_bid_project_file_from_upload(
        project=project,
        file=file,
        file_type="tender_document",
        current_user=current_user,
    )
    project.status = "files_uploaded"
    db.add(file_obj)
    db.commit()
    db.refresh(project)
    db.refresh(file_obj)
    return api_ok(
        {
            "project": _serialize_project(project, summary=_summary_counts(db, project)),
            "file": _serialize_file(file_obj, include_text=False),
        }
    )


@router.get("/admin/bidding/projects", summary="查询投标项目")
async def list_bid_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    query = _project_query(db, current_user)
    if status_filter:
        statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
        invalid = [item for item in statuses if item not in PROJECT_STATUSES]
        if invalid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_BID_PROJECT_STATUS")
        query = query.filter(BidProject.status.in_(statuses))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                BidProject.project_name.like(pattern),
                BidProject.tenderer_name.like(pattern),
                BidProject.tender_agency.like(pattern),
                BidProject.project_location.like(pattern),
            )
        )
    total = query.count()
    projects = query.order_by(BidProject.updated_at.desc(), BidProject.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return api_page(
        [_serialize_project(project, summary=_summary_counts(db, project)) for project in projects],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/admin/bidding/projects/{project_uuid}", summary="查询投标项目详情")
async def get_bid_project(
    project_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    return api_ok(_serialize_project(project, summary=_summary_counts(db, project)))


@router.patch("/admin/bidding/projects/{project_uuid}", summary="更新投标项目")
async def update_bid_project(
    project_uuid: str,
    payload: BidProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    if payload.project_name is not None:
        name = _clean_text(payload.project_name, 255)
        if not name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PROJECT_NAME_REQUIRED")
        project.project_name = name
    for field_name, limit in (
        ("tenderer_name", 255),
        ("tender_agency", 255),
        ("project_location", 255),
        ("project_type", 64),
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(project, field_name, _clean_text(value, limit))
    if payload.status is not None:
        if payload.status not in PROJECT_STATUSES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_BID_PROJECT_STATUS")
        project.status = payload.status
    if payload.owner_user_id is not None:
        project.owner_user_id = _get_user_or_current(db, payload.owner_user_id, current_user).id
    for field_name in ("tender_deadline_at", "inquiry_deadline_at", "bid_open_at"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(project, field_name, _parse_dt(value, field_name))
    db.commit()
    db.refresh(project)
    return api_ok(_serialize_project(project, summary=_summary_counts(db, project)))


@router.post("/admin/bidding/projects/{project_uuid}/files", summary="上传并抽取招标资料文本")
async def upload_bid_project_file(
    project_uuid: str,
    file: UploadFile = File(...),
    file_type: str = Form("tender_document"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    file_obj = await _build_bid_project_file_from_upload(
        project=project,
        file=file,
        file_type=file_type,
        current_user=current_user,
    )
    project.status = "files_uploaded" if project.status == "draft" else project.status
    db.add(file_obj)
    db.commit()
    db.refresh(file_obj)
    return api_ok(_serialize_file(file_obj, include_text=False))


@router.get("/admin/bidding/projects/{project_uuid}/files", summary="查询投标项目资料")
async def list_bid_project_files(
    project_uuid: str,
    include_text: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    files = db.query(BidProjectFile).filter(BidProjectFile.project_id == project.id).order_by(BidProjectFile.id.desc()).all()
    return api_ok([_serialize_file(item, include_text=include_text) for item in files])


def _segments_from_files(files: list[BidProjectFile]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for file_obj in files:
        file_segments = loads_json(file_obj.segments_json, [])
        if file_segments:
            for segment in file_segments:
                segment["file_id"] = file_obj.id
                segment["source_file"] = segment.get("source_file") or file_obj.original_filename
                segments.append(segment)
            continue
        if file_obj.extracted_text:
            segments.append(
                {
                    "file_id": file_obj.id,
                    "source_file": file_obj.original_filename,
                    "source_location": "已抽取文本",
                    "text": file_obj.extracted_text,
                }
            )
    return segments


@router.post("/admin/bidding/projects/{project_uuid}/parse", summary="解析招标要求和合同风险")
async def parse_bid_project(
    project_uuid: str,
    payload: BidParseRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    query = db.query(BidProjectFile).filter(BidProjectFile.project_id == project.id, BidProjectFile.parser_status == "parsed")
    requested_file_uuids = [item.strip() for item in (payload.file_uuids if payload else []) if item.strip()]
    if requested_file_uuids:
        query = query.filter(BidProjectFile.file_uuid.in_(requested_file_uuids))
    files = query.order_by(BidProjectFile.id.asc()).all()
    if not files:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="NO_PARSED_BID_FILES")

    run = BidParseRun(
        run_uuid=str(uuid.uuid4()),
        project_id=project.id,
        status="running",
        parser_version=BIDDING_PARSER_VERSION,
        input_file_ids_json=dumps_json([item.file_uuid for item in files]),
        created_by=current_user.id,
    )
    db.add(run)
    db.flush()
    try:
        segments = _segments_from_files(files)
        result = analyze_tender_segments(segments)
        file_by_name = {file_obj.original_filename: file_obj for file_obj in files}
        for item in result["requirements"]:
            source_file = item.get("source_file")
            file_obj = file_by_name.get(source_file)
            db.add(
                TenderRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    file_id=file_obj.id if file_obj else None,
                    parse_run_id=run.id,
                    requirement_type=item["requirement_type"],
                    source_file=source_file,
                    source_location=item.get("source_location"),
                    original_text=item["original_text"],
                    parsed_requirement=item["parsed_requirement"],
                    compliance_status=item["compliance_status"],
                    risk_level=item["risk_level"],
                    owner_role=item.get("owner_role"),
                    output_section=item.get("output_section"),
                    confidence=item.get("confidence", 0.6),
                    extraction_method=item.get("extraction_method", "rule"),
                    status="active",
                )
            )
        db.flush()
        for item in result["risks"]:
            source_file = item.get("source_file")
            file_obj = file_by_name.get(source_file)
            db.add(
                TenderRisk(
                    risk_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    file_id=file_obj.id if file_obj else None,
                    parse_run_id=run.id,
                    risk_type=item["risk_type"],
                    risk_level=item["risk_level"],
                    source_file=source_file,
                    source_location=item.get("source_location"),
                    original_text=item["original_text"],
                    risk_explanation=item["risk_explanation"],
                    impact_area=item.get("impact_area"),
                    suggested_action=item.get("suggested_action"),
                    is_blocking=bool(item.get("is_blocking")),
                    review_status="pending",
                    confidence=item.get("confidence", 0.6),
                    extraction_method=item.get("extraction_method", "rule"),
                )
            )
        db.flush()
        run_requirements = (
            db.query(TenderRequirement)
            .filter(TenderRequirement.parse_run_id == run.id)
            .order_by(TenderRequirement.id.asc())
            .all()
        )
        run_risks = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id).order_by(TenderRisk.id.asc()).all()
        business_objects = build_tender_business_objects(run_requirements, run_risks)
        for item in business_objects:
            related_requirement_ids = item.get("related_requirement_ids") or []
            related_risk_ids = item.get("related_risk_ids") or []
            db.add(
                TenderBusinessObject(
                    object_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    file_id=item.get("file_id"),
                    parse_run_id=run.id,
                    requirement_id=related_requirement_ids[0] if related_requirement_ids else None,
                    risk_id=related_risk_ids[0] if related_risk_ids else None,
                    object_type=item["object_type"],
                    object_subtype=item["object_subtype"],
                    title=item["title"],
                    normalized_value=item.get("normalized_value"),
                    normalized_json=dumps_json(item.get("normalized_json") or {}),
                    source_file=item.get("source_file"),
                    source_location=item.get("source_location"),
                    original_text=item.get("original_text") or item["title"],
                    source_count=int(item.get("source_count") or 1),
                    evidence_json=dumps_json(item.get("evidence") or []),
                    related_requirement_ids_json=dumps_json(related_requirement_ids),
                    related_risk_ids_json=dumps_json(related_risk_ids),
                    document_section=item.get("document_section"),
                    owner_role=item.get("owner_role"),
                    response_required=bool(item.get("response_required", True)),
                    review_status="pending",
                    confidence=float(item.get("confidence") or 0.6),
                    extraction_method=item.get("extraction_method") or "rule_business_object_v1",
                    status="active",
                )
            )
        risk_cards = cluster_tender_risks_to_cards(run_risks, parse_run_id=run.id)
        summary = dict(result["summary"])
        summary["risk_card_summary"] = build_risk_card_summary(risk_cards, risk_count=len(run_risks))
        summary["business_object_summary"] = build_business_object_summary(business_objects)
        run.status = "completed"
        run.summary_json = dumps_json(summary)
        run.finished_at = datetime.now()
        project.status = "parsed"
        project.summary_json = dumps_json({"biz_stage": "BIZ-4a", "latest_parse_summary": summary})
        db.commit()
        db.refresh(run)
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:4000]
        run.finished_at = datetime.now()
        db.commit()
        raise
    return api_ok(_serialize_parse_run(run))


@router.get("/admin/bidding/projects/{project_uuid}/parse-runs", summary="查询招标解析版本")
async def list_bid_parse_runs(
    project_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    runs = db.query(BidParseRun).filter(BidParseRun.project_id == project.id).order_by(BidParseRun.id.desc()).all()
    return api_ok([_serialize_parse_run(item) for item in runs])


@router.get("/admin/bidding/projects/{project_uuid}/tender-analysis/preview", summary="预览招标文件分析成果表")
async def preview_tender_analysis(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    preview = await build_tender_analysis_preview_with_semantic_summary(
        db,
        project,
        run,
        username=current_user.username,
        trace_id=run.run_uuid,
    )
    return api_ok(preview)


@router.get("/admin/bidding/projects/{project_uuid}/tender-analysis/export", summary="导出招标文件分析成果表 Word")
async def export_tender_analysis(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    preview = await build_tender_analysis_preview_with_semantic_summary(
        db,
        project,
        run,
        username=current_user.username,
        trace_id=run.run_uuid,
    )
    content = build_tender_analysis_export_document(preview)
    filename = f"{_safe_download_stem(project.project_name)}_投标重要信息提取_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=tender_analysis.docx; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/admin/bidding/projects/{project_uuid}/risk-clause/preview", summary="预览LLM风险条款清单")
async def preview_tender_risk_clause_llm(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    return api_ok(get_cached_tender_risk_clause_llm(run))


@router.post("/admin/bidding/projects/{project_uuid}/risk-clause/analyze", summary="LLM生成风险条款清单")
async def analyze_tender_risk_clause_llm(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    force: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    result = await analyze_tender_risk_clause_with_llm(
        db,
        project,
        run,
        username=current_user.username,
        trace_id=f"{run.run_uuid}:risk_clause",
        force=force,
    )
    return api_ok(result)


@router.get("/admin/bidding/projects/{project_uuid}/risk-clause/export", summary="导出LLM风险条款清单 Word")
async def export_tender_risk_clause_llm(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    risk_clause = get_cached_tender_risk_clause_llm(run)
    content = build_tender_risk_clause_export_document(project, risk_clause)
    filename = f"{_safe_download_stem(project.project_name)}_风险条款清单_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=tender_risk_clause.docx; filename*=UTF-8''{quote(filename)}",
        },
    )


def _resolve_run(db: Session, project: BidProject, run_uuid: str | None) -> BidParseRun:
    query = db.query(BidParseRun).filter(BidParseRun.project_id == project.id, BidParseRun.status == "completed")
    if run_uuid and run_uuid != "latest":
        run = query.filter(BidParseRun.run_uuid == run_uuid).first()
    else:
        run = query.order_by(BidParseRun.id.desc()).first()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_PARSE_RUN_NOT_FOUND")
    return run


@router.get("/admin/bidding/projects/{project_uuid}/business-objects", summary="查询结构化投标业务对象")
async def list_tender_business_objects(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    object_type: Optional[str] = None,
    object_subtype: Optional[str] = None,
    review_status: Optional[str] = None,
    response_required: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(80, ge=1, le=300),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    query = db.query(TenderBusinessObject).filter(TenderBusinessObject.parse_run_id == run.id)
    if object_type:
        query = query.filter(TenderBusinessObject.object_type == object_type)
    if object_subtype:
        query = query.filter(TenderBusinessObject.object_subtype == object_subtype)
    if review_status:
        query = query.filter(TenderBusinessObject.review_status == review_status)
    if response_required is not None:
        query = query.filter(TenderBusinessObject.response_required.is_(response_required))
    all_items = query.order_by(
        TenderBusinessObject.object_type.asc(),
        TenderBusinessObject.object_subtype.asc(),
        TenderBusinessObject.id.asc(),
    ).all()
    page_items = all_items[(page - 1) * page_size : page * page_size]
    serialized_all = [_serialize_business_object(item) for item in all_items]
    return api_page(
        [_serialize_business_object(item) for item in page_items],
        total=len(all_items),
        page=page,
        page_size=page_size,
        run_uuid=run.run_uuid,
        summary=build_business_object_summary(serialized_all),
    )


@router.post("/admin/bidding/projects/{project_uuid}/business-objects/llm-review", summary="DeepSeek 复核不确定投标业务对象")
async def review_uncertain_tender_business_objects_with_llm(
    project_uuid: str,
    payload: BusinessObjectLlmReviewRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    if not settings.feature_bidding_llm_review:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="BIDDING_LLM_REVIEW_DISABLED")
    payload = payload or BusinessObjectLlmReviewRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    result = await review_uncertain_business_objects_with_deepseek(
        db,
        run,
        username=current_user.username,
        trace_id=run.run_uuid,
        limit=payload.limit,
        force=payload.force,
        only_pending=payload.only_pending,
        object_uuids=payload.object_uuids,
    )
    if result.get("reviewed_count"):
        objects = (
            db.query(TenderBusinessObject)
            .filter(TenderBusinessObject.parse_run_id == run.id)
            .order_by(TenderBusinessObject.id.asc())
            .all()
        )
        result["business_object_summary"] = build_business_object_summary(
            [_serialize_business_object(item) for item in objects]
        )
    return api_ok(result)


@router.patch("/admin/bidding/business-objects/{object_uuid}/llm-review", summary="处理 DeepSeek 投标业务对象复核建议")
async def decide_tender_business_object_llm_review(
    object_uuid: str,
    payload: BusinessObjectLlmReviewDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    if not settings.feature_bidding_llm_review:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="BIDDING_LLM_REVIEW_DISABLED")

    action = (payload.action or "").strip().lower()
    if action not in BUSINESS_OBJECT_LLM_DECISION_ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_BIDDING_LLM_REVIEW_ACTION")

    item = db.query(TenderBusinessObject).filter(TenderBusinessObject.object_uuid == object_uuid).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TENDER_BUSINESS_OBJECT_NOT_FOUND")
    _require_project_access(current_user, item.project)

    normalized = loads_json(item.normalized_json, {}) if item.normalized_json else {}
    llm_review = normalized.get("llm_review") if isinstance(normalized.get("llm_review"), dict) else {}
    reviewer_note = _clean_text(payload.reviewer_note, 4000)

    if action in {"accept", "modify"} and not llm_review:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="BIDDING_LLM_REVIEW_NOT_AVAILABLE")
    if action == "reject" and not reviewer_note:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="BIDDING_LLM_REVIEW_REJECT_NOTE_REQUIRED")
    if action == "modify" and not payload.modified_review:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="BIDDING_LLM_REVIEW_MODIFIED_REVIEW_REQUIRED")

    effective_review: dict[str, Any] | None = None
    manual_edit: dict[str, Any] | None = None
    if action == "accept":
        effective_review = dict(llm_review)
        status_value = "accepted"
    elif action == "reject":
        status_value = "rejected"
    else:
        context = build_business_object_review_context(item)
        manual_edit = {**llm_review, **payload.modified_review}
        effective_review = clean_llm_review_payload({"object_review": manual_edit}, context)
        effective_review["read_only"] = False
        effective_review["manual_modified"] = True
        status_value = "modified"

    now = datetime.now(timezone.utc).isoformat()
    normalized["llm_review_status"] = status_value
    normalized["llm_review_decision_action"] = action
    normalized["llm_review_decision_note"] = reviewer_note
    normalized["llm_review_decided_by"] = current_user.id
    normalized["llm_review_decided_by_username"] = current_user.username
    normalized["llm_review_decided_at"] = now
    normalized["llm_review_effective"] = effective_review
    if manual_edit is not None:
        normalized["llm_review_manual_edit"] = manual_edit
    item.normalized_json = dumps_json(normalized)
    db.commit()
    db.refresh(item)
    return api_ok(_serialize_business_object(item))


@router.post("/admin/bidding/projects/{project_uuid}/response-matrix/generate", summary="生成投标响应矩阵初稿")
async def generate_tender_response_matrix(
    project_uuid: str,
    payload: ResponseMatrixGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    payload = payload or ResponseMatrixGenerateRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    result = generate_response_matrix_items(db, run, created_by=current_user.id)
    db.commit()
    return api_ok(result)


@router.get("/admin/bidding/projects/{project_uuid}/response-matrix", summary="查询投标响应矩阵")
async def list_tender_response_matrix(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    status: Optional[str] = None,
    response_action: Optional[str] = None,
    risk_level: Optional[str] = None,
    created_from: Optional[str] = None,
    review_role: Optional[str] = None,
    include_superseded: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    if not is_valid_response_review_role(review_role):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_REVIEW_ROLE")
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    query = db.query(TenderResponseItem).filter(TenderResponseItem.parse_run_id == run.id)
    if status:
        query = query.filter(TenderResponseItem.status == status)
    if response_action:
        query = query.filter(TenderResponseItem.response_action == response_action)
    if risk_level:
        query = query.filter(TenderResponseItem.risk_level == risk_level)
    if created_from:
        query = query.filter(TenderResponseItem.created_from == created_from)
    all_items = query.order_by(TenderResponseItem.id.asc()).all()
    if not include_superseded:
        all_items = [item for item in all_items if not is_superseded_response_item(item)]
    if review_role:
        all_items = [item for item in all_items if response_item_matches_review_role(item, review_role)]
    page_items = all_items[(page - 1) * page_size : page * page_size]
    return api_page(
        [_serialize_response_item(item) for item in page_items],
        total=len(all_items),
        page=page,
        page_size=page_size,
        run_uuid=run.run_uuid,
        summary=build_response_matrix_summary(all_items),
    )


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/format-plan", summary="预览投标文件格式识别结果")
async def get_bid_file_format_plan_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    plan = get_bid_file_format_plan(db, run)
    if plan:
        return api_ok(serialize_bid_file_format_plan(plan), review_statuses=sorted(BID_FILE_FORMAT_REVIEW_STATUSES))
    return api_ok(preview_bid_file_format_plan(db, project, run), review_statuses=sorted(BID_FILE_FORMAT_REVIEW_STATUSES))


@router.post("/admin/bidding/projects/{project_uuid}/bid-draft/format-plan/generate", summary="生成投标文件格式确认表")
async def generate_bid_file_format_plan_api(
    project_uuid: str,
    payload: BidFileFormatPlanGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    payload = payload or BidFileFormatPlanGenerateRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    plan = generate_bid_file_format_plan(db, project, run, created_by=current_user.id)
    db.commit()
    db.refresh(plan)
    return api_ok(serialize_bid_file_format_plan(plan), review_statuses=sorted(BID_FILE_FORMAT_REVIEW_STATUSES))


@router.patch("/admin/bidding/bid-draft/format-plan/{plan_uuid}/confirm", summary="确认投标文件格式")
async def confirm_bid_file_format_plan_api(
    plan_uuid: str,
    payload: BidFileFormatPlanConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    plan = db.query(BidFileFormatPlan).filter(BidFileFormatPlan.plan_uuid == plan_uuid).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_FILE_FORMAT_PLAN_NOT_FOUND")
    _require_project_access(current_user, plan.project)
    try:
        confirm_bid_file_format_plan(
            db,
            plan,
            reviewer_id=current_user.id,
            structure=payload.structure,
            reviewer_note=payload.reviewer_note,
            edit_events=payload.edit_events,
        )
    except BidFileFormatError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    db.commit()
    db.refresh(plan)
    return api_ok(serialize_bid_file_format_plan(plan), review_statuses=sorted(BID_FILE_FORMAT_REVIEW_STATUSES))


@router.patch("/admin/bidding/bid-draft/format-plan/{plan_uuid}/review", summary="更新投标文件格式复核状态")
async def review_bid_file_format_plan_api(
    plan_uuid: str,
    payload: BidFileFormatPlanReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    plan = db.query(BidFileFormatPlan).filter(BidFileFormatPlan.plan_uuid == plan_uuid).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_FILE_FORMAT_PLAN_NOT_FOUND")
    _require_project_access(current_user, plan.project)
    try:
        update_bid_file_format_plan_review(
            plan,
            review_status=(payload.review_status or "").strip(),
            reviewer_note=payload.reviewer_note,
        )
    except BidFileFormatError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    db.commit()
    db.refresh(plan)
    return api_ok(serialize_bid_file_format_plan(plan), review_statuses=sorted(BID_FILE_FORMAT_REVIEW_STATUSES))


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/technical-composition", summary="查询技术标投标文件组成识别结果")
async def get_bid_technical_composition_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    return api_ok(get_bid_technical_composition_plan(db, project, run), run_uuid=run.run_uuid)


@router.post("/admin/bidding/projects/{project_uuid}/bid-draft/technical-composition/generate", summary="LLM识别技术标投标文件组成并同步资料需求")
async def generate_bid_technical_composition_api(
    project_uuid: str,
    payload: BidTechnicalCompositionGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    payload = payload or BidTechnicalCompositionGenerateRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    try:
        plan = await generate_bid_technical_composition_plan(db, project, run, user=current_user)
    except BidTechnicalCompositionError as exc:
        status_code = status.HTTP_409_CONFLICT if exc.code in {"NO_PARSED_BID_FILES", "BID_TECHNICAL_COMPOSITION_EMPTY_TEXT"} else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    db.commit()
    return api_ok(
        get_bid_technical_composition_plan(db, project, run),
        run_uuid=run.run_uuid,
        generation=plan.get("requirement_sync") or {},
    )


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/material-requirements", summary="查询投标资料需求补齐清单")
async def list_bid_material_requirements_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    status_filter: Optional[str] = Query(None, alias="status"),
    requirement_type: Optional[str] = None,
    package_key: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    try:
        rows = list_bid_material_requirements(
            db,
            run,
            status_filter=status_filter,
            requirement_type=requirement_type,
            package_key=package_key,
        )
    except BidMaterialRequirementError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    return api_ok(
        [serialize_bid_material_requirement(row) for row in rows],
        run_uuid=run.run_uuid,
        total=len(rows),
        summary=build_bid_material_requirement_summary(rows),
        package_key=package_key or "all",
        statuses=sorted(BID_MATERIAL_REQUIREMENT_STATUSES),
    )


@router.post("/admin/bidding/projects/{project_uuid}/bid-draft/material-requirements/generate", summary="生成投标资料需求补齐清单")
async def generate_bid_material_requirements_api(
    project_uuid: str,
    payload: BidMaterialRequirementGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    payload = payload or BidMaterialRequirementGenerateRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    try:
        rows, generation = generate_bid_material_requirements(db, project, run, user=current_user, package_key=payload.package_key)
    except BidMaterialRequirementError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    db.commit()
    rows = list_bid_material_requirements(db, run, package_key=payload.package_key)
    return api_ok(
        [serialize_bid_material_requirement(row) for row in rows],
        run_uuid=run.run_uuid,
        total=len(rows),
        summary=build_bid_material_requirement_summary(rows),
        generation=generation,
        package_key=payload.package_key or "all",
        statuses=sorted(BID_MATERIAL_REQUIREMENT_STATUSES),
    )


@router.patch("/admin/bidding/bid-draft/material-requirements/{requirement_uuid}", summary="更新投标资料补齐状态")
async def update_bid_material_requirement_api(
    requirement_uuid: str,
    payload: BidMaterialRequirementUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    requirement = get_bid_material_requirement_by_uuid(db, requirement_uuid)
    if not requirement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_MATERIAL_REQUIREMENT_NOT_FOUND")
    _require_project_access(current_user, requirement.project)
    try:
        update_bid_material_requirement(db, requirement, user=current_user, payload=payload)
    except BidMaterialRequirementError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.code in {"SUBMITTED_FILE_NOT_FOUND", "ENTERPRISE_PROFILE_ACTIVE_ITEM_NOT_FOUND"} else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=exc.code) from exc
    db.commit()
    requirement = get_bid_material_requirement_by_uuid(db, requirement_uuid)
    return api_ok(serialize_bid_material_requirement(requirement), statuses=sorted(BID_MATERIAL_REQUIREMENT_STATUSES))


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/outline", summary="预览投标书目录骨架")
async def get_bid_draft_outline(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    package_key: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    return api_ok(generate_bid_draft_outline(db, project, run, package_key=package_key))


@router.post("/admin/bidding/projects/{project_uuid}/bid-draft/outline/generate", summary="生成投标书目录骨架")
async def generate_bid_draft_outline_api(
    project_uuid: str,
    payload: BidDraftOutlineGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    payload = payload or BidDraftOutlineGenerateRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    return api_ok(generate_bid_draft_outline(db, project, run, package_key=payload.package_key))


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/sections", summary="查询投标书章节草稿")
async def list_bid_draft_sections_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    package_key: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    rows = list_bid_draft_sections(db, run, package_key=package_key)
    return api_ok(
        [serialize_bid_draft_section(row) for row in rows],
        run_uuid=run.run_uuid,
        package_key=package_key or "all",
        total=len(rows),
        review_statuses=sorted(BID_DRAFT_REVIEW_STATUSES),
    )


@router.post("/admin/bidding/projects/{project_uuid}/bid-draft/sections/generate", summary="生成单章节投标书正文草稿")
async def generate_bid_draft_section_api(
    project_uuid: str,
    payload: BidDraftSectionGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    section_key = (payload.section_key or "").strip()
    if not section_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="SECTION_KEY_REQUIRED")
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    try:
        draft = await generate_bid_draft_section(
            db,
            project,
            run,
            section_key=section_key,
            created_by=current_user.id,
            generator_type=payload.generator_type or "rule",
            package_key=payload.package_key,
            username=current_user.username,
            trace_id=run.run_uuid,
        )
    except BidDraftSectionError as exc:
        status_code = status.HTTP_404_NOT_FOUND if exc.code == "BID_DRAFT_SECTION_NOT_FOUND" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    db.commit()
    db.refresh(draft)
    return api_ok(serialize_bid_draft_section(draft))


@router.post("/admin/bidding/projects/{project_uuid}/bid-draft/technical-draft/generate", summary="基于投标文件组成识别一键生成技术标草案")
async def generate_technical_bid_draft_api(
    project_uuid: str,
    payload: BidTechnicalDraftGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    payload = payload or BidTechnicalDraftGenerateRequest()
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, payload.run_uuid)
    try:
        result = await generate_technical_bid_draft_from_composition(
            db,
            project,
            run,
            created_by=current_user.id,
            overwrite=payload.overwrite,
            username=current_user.username,
        )
    except BidDraftSectionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    db.commit()
    return api_ok(result, run_uuid=run.run_uuid)


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/technical-draft/export", summary="导出技术标 Word 草稿")
async def export_technical_bid_draft_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    try:
        content = build_technical_bid_draft_export_document(db, project, run)
    except BidTechnicalWordExportError as exc:
        code = status.HTTP_409_CONFLICT if exc.code in {
            "BID_TECHNICAL_COMPOSITION_NOT_GENERATED",
            "BID_TECHNICAL_DRAFT_NOT_GENERATED",
        } else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=exc.details or exc.code) from exc
    filename = f"{_safe_download_stem(project.project_name)}_技术标草稿_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=technical_bid_draft.docx; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/technical-final/export", summary="导出正式技术标 Word")
async def export_technical_bid_final_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    try:
        content = build_technical_bid_final_export_document(db, project, run)
    except BidTechnicalWordExportError as exc:
        code = status.HTTP_409_CONFLICT if exc.code in {
            "BID_TECHNICAL_COMPOSITION_NOT_GENERATED",
            "BID_TECHNICAL_DRAFT_NOT_GENERATED",
            "BID_TECHNICAL_FINAL_EXPORT_BLOCKED",
        } else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=exc.details or exc.code) from exc
    filename = f"{_safe_download_stem(project.project_name)}_技术标正式稿_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=technical_bid_final.docx; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/admin/bidding/projects/{project_uuid}/bid-draft/technical-final/quality", summary="查询正式技术标导出质量报告")
async def get_technical_bid_final_quality_api(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    try:
        report = build_technical_bid_final_export_quality_report(db, project, run)
    except BidTechnicalWordExportError as exc:
        code = status.HTTP_409_CONFLICT if exc.code in {
            "BID_TECHNICAL_COMPOSITION_NOT_GENERATED",
            "BID_TECHNICAL_DRAFT_NOT_GENERATED",
        } else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=exc.details or exc.code) from exc
    return api_ok(report, run_uuid=run.run_uuid)


@router.patch("/admin/bidding/bid-draft/sections/{draft_uuid}/content", summary="编辑投标书章节草稿正文")
async def update_bid_draft_section_content_api(
    draft_uuid: str,
    payload: BidDraftSectionContentUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    draft = db.query(BidDraftSection).filter(BidDraftSection.draft_uuid == draft_uuid).first()
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_DRAFT_SECTION_NOT_FOUND")
    _require_project_access(current_user, draft.project)
    try:
        update_bid_draft_section_content(
            db,
            draft,
            content_markdown=payload.content_markdown,
            editor_note=payload.editor_note,
            editor_id=current_user.id,
        )
    except BidDraftSectionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    db.commit()
    db.refresh(draft)
    return api_ok(serialize_bid_draft_section(draft))


@router.patch("/admin/bidding/bid-draft/sections/{draft_uuid}/review", summary="复核投标书章节草稿")
async def review_bid_draft_section_api(
    draft_uuid: str,
    payload: BidDraftSectionReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    draft = db.query(BidDraftSection).filter(BidDraftSection.draft_uuid == draft_uuid).first()
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BID_DRAFT_SECTION_NOT_FOUND")
    _require_project_access(current_user, draft.project)
    try:
        update_bid_draft_section_review(
            db,
            draft,
            review_status=(payload.review_status or "").strip(),
            reviewer_note=payload.reviewer_note,
            reviewer_id=current_user.id,
        )
    except BidDraftSectionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code) from exc
    db.commit()
    db.refresh(draft)
    return api_ok(serialize_bid_draft_section(draft))


@router.patch("/admin/bidding/response-items/{response_item_uuid}", summary="轻编辑投标响应矩阵项")
async def update_tender_response_item(
    response_item_uuid: str,
    payload: ResponseItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    item = db.query(TenderResponseItem).filter(TenderResponseItem.response_item_uuid == response_item_uuid).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TENDER_RESPONSE_ITEM_NOT_FOUND")
    _require_project_access(current_user, item.project)
    field_set = payload.model_fields_set if hasattr(payload, "model_fields_set") else getattr(payload, "__fields_set__", set())
    if "response_action" in field_set:
        action = (payload.response_action or "").strip()
        if action not in RESPONSE_ACTIONS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_RESPONSE_ACTION")
        item.response_action = action
    if "status" in field_set:
        next_status = (payload.status or "").strip()
        if next_status not in RESPONSE_STATUSES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_RESPONSE_STATUS")
        item.status = next_status
    if "owner_role" in field_set:
        item.owner_role = _clean_text(payload.owner_role, 64)
    if "response_note" in field_set:
        item.response_note = _clean_text(payload.response_note, 4000)
    if "reviewer_note" in field_set:
        item.reviewer_note = _clean_text(payload.reviewer_note, 4000)
    item.reviewed_by = current_user.id
    item.reviewed_at = datetime.now()
    db.commit()
    db.refresh(item)
    return api_ok(_serialize_response_item(item))


@router.get("/admin/bidding/projects/{project_uuid}/requirements", summary="查询招标要求清单")
async def list_tender_requirements(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    requirement_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    query = db.query(TenderRequirement).filter(TenderRequirement.parse_run_id == run.id)
    if requirement_type:
        query = query.filter(TenderRequirement.requirement_type == requirement_type)
    if risk_level:
        query = query.filter(TenderRequirement.risk_level == risk_level)
    total = query.count()
    items = query.order_by(TenderRequirement.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return api_page([_serialize_requirement(item) for item in items], total=total, page=page, page_size=page_size, run_uuid=run.run_uuid)


@router.patch("/admin/bidding/business-objects/{object_uuid}/review", summary="人工复核投标业务对象")
async def review_tender_business_object(
    object_uuid: str,
    payload: BusinessObjectReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    item = db.query(TenderBusinessObject).filter(TenderBusinessObject.object_uuid == object_uuid).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TENDER_BUSINESS_OBJECT_NOT_FOUND")
    _require_project_access(current_user, item.project)
    if payload.review_status not in BUSINESS_OBJECT_REVIEW_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_BUSINESS_OBJECT_REVIEW_STATUS")
    if payload.review_status in {"ignored", "to_clarify", "to_quote_allowance"} and not _clean_text(payload.reviewer_note, 2000):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="BUSINESS_OBJECT_REVIEW_NOTE_REQUIRED")
    item.review_status = payload.review_status
    item.reviewer_note = _clean_text(payload.reviewer_note, 4000)
    item.reviewed_by = current_user.id
    item.reviewed_at = datetime.now()
    db.commit()
    db.refresh(item)
    return api_ok(_serialize_business_object(item))


@router.get("/admin/bidding/projects/{project_uuid}/risk-cards", summary="查询聚类后的投标风险卡片")
async def list_tender_risk_cards(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    risk_level: Optional[str] = None,
    review_status: Optional[str] = None,
    risk_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    query = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id)
    if risk_level:
        query = query.filter(TenderRisk.risk_level == risk_level)
    if review_status:
        query = query.filter(TenderRisk.review_status == review_status)
    if risk_type:
        query = query.filter(TenderRisk.risk_type == risk_type)
    risks = query.order_by(TenderRisk.id.asc()).all()
    cards = cluster_tender_risks_to_cards(risks, parse_run_id=run.id)
    return api_ok(
        {
            "run_uuid": run.run_uuid,
            "summary": build_risk_card_summary(cards, risk_count=len(risks)),
            "cards": cards,
        }
    )


@router.get("/admin/bidding/projects/{project_uuid}/risks", summary="查询合同风险和废标风险")
async def list_tender_risks(
    project_uuid: str,
    run_uuid: Optional[str] = Query("latest"),
    risk_level: Optional[str] = None,
    review_status: Optional[str] = None,
    risk_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    query = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id)
    if risk_level:
        query = query.filter(TenderRisk.risk_level == risk_level)
    if review_status:
        query = query.filter(TenderRisk.review_status == review_status)
    if risk_type:
        query = query.filter(TenderRisk.risk_type == risk_type)
    total = query.count()
    items = query.order_by(TenderRisk.risk_level.asc(), TenderRisk.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return api_page([_serialize_risk(item) for item in items], total=total, page=page, page_size=page_size, run_uuid=run.run_uuid)


@router.patch("/admin/bidding/projects/{project_uuid}/risk-cards/{card_id}/review", summary="按风险卡片批量复核")
async def review_tender_risk_card(
    project_uuid: str,
    card_id: str,
    payload: RiskReviewRequest,
    run_uuid: Optional[str] = Query("latest"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    project = _get_project(db, project_uuid, current_user)
    run = _resolve_run(db, project, run_uuid)
    if payload.review_status not in RISK_REVIEW_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_RISK_REVIEW_STATUS")
    if payload.review_status in {"ignored", "to_clarify", "to_quote_allowance"} and not _clean_text(payload.reviewer_note, 2000):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="RISK_REVIEW_NOTE_REQUIRED")

    risks = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id).order_by(TenderRisk.id.asc()).all()
    cards = cluster_tender_risks_to_cards(risks, parse_run_id=run.id)
    card = next((item for item in cards if item.get("card_id") == card_id), None)
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TENDER_RISK_CARD_NOT_FOUND")

    member_uuids = [item for item in card.get("member_risk_uuids", []) if item]
    member_risks = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id, TenderRisk.risk_uuid.in_(member_uuids)).all()
    note = _clean_text(payload.reviewer_note, 4000)
    for risk in member_risks:
        risk.review_status = payload.review_status
        risk.reviewer_note = note
        risk.reviewed_by = current_user.id
        risk.reviewed_at = datetime.now()
    db.commit()

    refreshed = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id).order_by(TenderRisk.id.asc()).all()
    refreshed_cards = cluster_tender_risks_to_cards(refreshed, parse_run_id=run.id)
    refreshed_card = next((item for item in refreshed_cards if item.get("card_id") == card_id), card)
    return api_ok(
        {
            "run_uuid": run.run_uuid,
            "updated_risk_count": len(member_risks),
            "card": refreshed_card,
            "summary": build_risk_card_summary(refreshed_cards, risk_count=len(refreshed)),
        }
    )


@router.patch("/admin/bidding/risks/{risk_uuid}/review", summary="人工复核合同风险")
async def review_tender_risk(
    risk_uuid: str,
    payload: RiskReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_feature_enabled()
    _require_bidding_access(current_user)
    risk = db.query(TenderRisk).filter(TenderRisk.risk_uuid == risk_uuid).first()
    if not risk:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TENDER_RISK_NOT_FOUND")
    _require_project_access(current_user, risk.project)
    if payload.review_status not in RISK_REVIEW_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_RISK_REVIEW_STATUS")
    if payload.review_status in {"ignored", "to_clarify", "to_quote_allowance"} and not _clean_text(payload.reviewer_note, 2000):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="RISK_REVIEW_NOTE_REQUIRED")
    risk.review_status = payload.review_status
    risk.reviewer_note = _clean_text(payload.reviewer_note, 4000)
    risk.reviewed_by = current_user.id
    risk.reviewed_at = datetime.now()
    db.commit()
    db.refresh(risk)
    return api_ok(_serialize_risk(risk))
