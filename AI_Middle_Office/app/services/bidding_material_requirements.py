from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.bidding import (
    BidFileFormatPlan,
    BidMaterialRequirement,
    BidMaterialRequirementEvent,
    BidParseRun,
    BidProject,
)
from app.models.enterprise_profile import ENTERPRISE_PROFILE_STATUS_ACTIVE, EnterpriseProfileItem
from app.models.file_object import FileObject
from app.models.user import User
from app.services.bidding_file_format import get_bid_file_format_plan
from app.services.bidding_parser import dumps_json, loads_json
from app.services.enterprise_profile import clean_text, list_active_profile_candidates, serialize_item


BID_MATERIAL_REQUIREMENT_VERSION = "biz4c_material_requirements_v1.0"
BID_MATERIAL_PROFILE_CANDIDATE_MIN_SCORE = 0.30
BID_MATERIAL_PROFILE_CATEGORY_OVERRIDE_SCORE = 0.42
BID_MATERIAL_REQUIREMENT_STATUSES = {
    "missing",
    "candidate_found",
    "submitted",
    "approved",
    "applied",
    "not_applicable",
}
BID_MATERIAL_REQUIREMENT_TYPES = {
    "profile",
    "field",
    "attachment",
    "section_text",
    "form_value",
    "pricing",
    "other",
}
BID_MATERIAL_FULFILLMENT_MODES = {
    "enterprise_profile",
    "manual_upload",
    "manual_fill",
    "generate_draft",
    "from_cost_quote",
}
RESOLVED_STATUSES = {"approved", "applied", "not_applicable"}
MANUAL_LOCKED_STATUSES = {"submitted", "approved", "applied", "not_applicable"}


class BidMaterialRequirementError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _payload_dict(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    if hasattr(payload, "dict"):
        return payload.dict(exclude_unset=True)
    return dict(payload or {})


def list_bid_material_requirements(
    db: Session,
    run: BidParseRun,
    *,
    status_filter: str | None = None,
    requirement_type: str | None = None,
    package_key: str | None = None,
) -> list[BidMaterialRequirement]:
    query = (
        db.query(BidMaterialRequirement)
        .options(selectinload(BidMaterialRequirement.events))
        .filter(BidMaterialRequirement.parse_run_id == run.id)
    )
    package_scope = _normalize_package_scope(package_key)
    if package_scope:
        query = query.filter(BidMaterialRequirement.package_key == package_scope)
    if package_scope == "technical" and _should_use_technical_composition_requirements(db, run):
        current_material_keys = _current_technical_composition_material_keys(run)
        if current_material_keys:
            query = query.filter(BidMaterialRequirement.material_key.in_(current_material_keys))
        elif _has_current_technical_composition_plan(run):
            query = query.filter(BidMaterialRequirement.id == -1)
        else:
            query = query.filter(BidMaterialRequirement.section_key.like("technical_composition:%"))
    if status_filter:
        statuses = [item.strip() for item in status_filter.split(",") if item.strip()]
        invalid = [item for item in statuses if item not in BID_MATERIAL_REQUIREMENT_STATUSES]
        if invalid:
            raise BidMaterialRequirementError("INVALID_BID_MATERIAL_REQUIREMENT_STATUS")
        if statuses:
            query = query.filter(BidMaterialRequirement.status.in_(statuses))
    if requirement_type:
        if requirement_type not in BID_MATERIAL_REQUIREMENT_TYPES:
            raise BidMaterialRequirementError("INVALID_BID_MATERIAL_REQUIREMENT_TYPE")
        query = query.filter(BidMaterialRequirement.requirement_type == requirement_type)
    rows = query.order_by(BidMaterialRequirement.id.asc()).all()
    rows = [row for row in rows if not _is_obsolete_enterprise_material_signature_row(row)]
    return sorted(rows, key=_row_sort_key)


def _should_use_technical_composition_requirements(db: Session, run: BidParseRun) -> bool:
    if _has_current_technical_composition_plan(run):
        return True
    return (
        db.query(BidMaterialRequirement.id)
        .filter(
            BidMaterialRequirement.parse_run_id == run.id,
            BidMaterialRequirement.package_key == "technical",
            BidMaterialRequirement.section_key.like("technical_composition:%"),
        )
        .first()
        is not None
    )


def _has_current_technical_composition_plan(run: BidParseRun) -> bool:
    summary = loads_json(run.summary_json, {}) or {}
    plan = summary.get("technical_composition_plan")
    return isinstance(plan, dict) and plan.get("status") == "generated"


def _current_technical_composition_material_keys(run: BidParseRun) -> set[str]:
    summary = loads_json(run.summary_json, {}) or {}
    plan = summary.get("technical_composition_plan")
    if not isinstance(plan, dict):
        return set()
    requirement_sync = plan.get("requirement_sync")
    if not isinstance(requirement_sync, dict):
        return set()
    rows = requirement_sync.get("rows")
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("material_key"))
        for row in rows
        if isinstance(row, dict) and row.get("material_key")
    }


def generate_bid_material_requirements(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    *,
    user: User,
    package_key: str | None = None,
) -> tuple[list[BidMaterialRequirement], dict[str, Any]]:
    plan = get_bid_file_format_plan(db, run)
    if not plan:
        raise BidMaterialRequirementError("BID_FILE_FORMAT_PLAN_REQUIRED")
    structure = loads_json(plan.structure_json, {}) or {}
    package_scope = _normalize_package_scope(package_key)
    slots = _build_requirement_slots(db, plan, structure, user, package_key=package_scope)
    if not slots:
        raise BidMaterialRequirementError("BID_FILE_FORMAT_ITEMS_REQUIRED")

    existing_by_key = {
        row.material_key: row
        for row in db.query(BidMaterialRequirement).filter(BidMaterialRequirement.parse_run_id == run.id).all()
    }
    created = 0
    updated = 0
    refreshed = 0

    for slot in slots:
        row = existing_by_key.get(slot["material_key"])
        if row is None:
            row = BidMaterialRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                created_by=user.id,
                **_row_fields_from_slot(slot),
            )
            db.add(row)
            db.flush()
            created += 1
            _record_event(db, row, event_type="generated", user=user, detail={"source": "format_plan"})
            continue

        old_status = row.status
        _refresh_row_from_slot(row, slot, user)
        if old_status != row.status:
            refreshed += 1
            _record_event(
                db,
                row,
                event_type="candidate_refreshed",
                user=user,
                old_status=old_status,
                new_status=row.status,
                detail={"candidate_profile_item_uuid": row.candidate_profile_item_uuid},
            )
        else:
            updated += 1

    stale_removed = _remove_stale_generated_rows(db, run, slots, package_scope=package_scope)
    rows = list_bid_material_requirements(db, run, package_key=package_scope)
    generation = {
        "version": BID_MATERIAL_REQUIREMENT_VERSION,
        "created_count": created,
        "updated_count": updated,
        "refreshed_count": refreshed,
        "stale_removed_count": stale_removed,
        "source": "bid_file_format_plan",
        "format_plan_uuid": plan.plan_uuid,
        "format_plan_review_status": plan.review_status,
        "enterprise_profile_enabled": bool(settings.feature_enterprise_profile),
        "package_scope": package_scope or "all",
        "package_scope_label": _package_scope_label(package_scope),
    }
    return rows, generation


def _remove_stale_generated_rows(
    db: Session,
    run: BidParseRun,
    slots: list[dict[str, Any]],
    *,
    package_scope: str | None,
) -> int:
    valid_keys = {slot["material_key"] for slot in slots}
    query = db.query(BidMaterialRequirement).filter(BidMaterialRequirement.parse_run_id == run.id)
    if package_scope:
        query = query.filter(BidMaterialRequirement.package_key == package_scope)
    removed = 0
    for row in query.all():
        if _is_obsolete_enterprise_material_signature_row(row):
            db.delete(row)
            removed += 1
            continue
        if row.material_key in valid_keys:
            continue
        if row.status not in {"missing", "candidate_found"} or _has_manual_submission(row):
            continue
        db.delete(row)
        removed += 1
    if removed:
        db.flush()
    return removed


def _is_obsolete_enterprise_material_signature_row(row: BidMaterialRequirement) -> bool:
    text = f"{row.item_title or ''} {row.title or ''} {row.material_key or ''}"
    if "签字盖章" not in text:
        return False
    material_key = row.material_key or ""
    if not (material_key.endswith(":signature_stamp") or material_key.endswith(":signature_stamp_profile")):
        return False
    category = row.profile_category
    if not category:
        base_key = (row.format_item_key or row.material_key or "").split(":")[-1]
        category, _, _ = _profile_category_for_item(base_key, row.item_title or row.title or "", "")
    return _signature_stamp_is_enterprise_material("", category)


def get_bid_material_requirement_by_uuid(db: Session, requirement_uuid: str) -> BidMaterialRequirement | None:
    return (
        db.query(BidMaterialRequirement)
        .options(selectinload(BidMaterialRequirement.events))
        .filter(BidMaterialRequirement.requirement_uuid == requirement_uuid)
        .first()
    )


def update_bid_material_requirement(
    db: Session,
    requirement: BidMaterialRequirement,
    *,
    user: User,
    payload: Mapping[str, Any] | Any,
) -> BidMaterialRequirement:
    data = _payload_dict(payload)
    if not data:
        return requirement

    old_status = requirement.status
    old_snapshot = _manual_snapshot(requirement)
    manual_submission_touched = False

    if "submitted_profile_item_uuids" in data:
        item_uuids = _clean_uuid_list(data.get("submitted_profile_item_uuids"))
        for item_uuid in item_uuids:
            _get_active_profile_item(db, item_uuid)
        requirement.submitted_profile_item_uuid = item_uuids[0] if item_uuids else None
        _set_manual_submission_values(requirement, profile_item_uuids=item_uuids)
        manual_submission_touched = True
    if "submitted_profile_item_uuid" in data:
        item_uuid = clean_text(data.get("submitted_profile_item_uuid"), 36)
        if item_uuid:
            _get_active_profile_item(db, item_uuid)
        requirement.submitted_profile_item_uuid = item_uuid
        _set_manual_submission_values(requirement, profile_item_uuids=[item_uuid] if item_uuid else [])
        manual_submission_touched = True
    if "submitted_file_ids" in data:
        file_ids = _clean_uuid_list(data.get("submitted_file_ids"))
        for file_id in file_ids:
            if not db.query(FileObject).filter(FileObject.file_id == file_id).first():
                raise BidMaterialRequirementError("SUBMITTED_FILE_NOT_FOUND")
        requirement.submitted_file_id = file_ids[0] if file_ids else None
        _set_manual_submission_values(requirement, file_ids=file_ids)
        manual_submission_touched = True
    if "submitted_file_id" in data:
        file_id = clean_text(data.get("submitted_file_id"), 36)
        if file_id and not db.query(FileObject).filter(FileObject.file_id == file_id).first():
            raise BidMaterialRequirementError("SUBMITTED_FILE_NOT_FOUND")
        requirement.submitted_file_id = file_id
        _set_manual_submission_values(requirement, file_ids=[file_id] if file_id else [])
        manual_submission_touched = True
    if "submitted_value" in data:
        requirement.submitted_value = clean_text(data.get("submitted_value"))
    if "notes" in data:
        requirement.notes = clean_text(data.get("notes"))

    next_status = clean_text(data.get("status"), 32) if "status" in data else None
    if next_status:
        if next_status not in BID_MATERIAL_REQUIREMENT_STATUSES:
            raise BidMaterialRequirementError("INVALID_BID_MATERIAL_REQUIREMENT_STATUS")
        requirement.status = next_status
    elif old_status in {"missing", "candidate_found"} and _has_manual_submission(requirement):
        requirement.status = "submitted"

    if requirement.status in {"approved", "applied"} and not _has_acceptable_evidence(requirement):
        raise BidMaterialRequirementError("BID_MATERIAL_REQUIREMENT_EVIDENCE_REQUIRED")
    if requirement.status in {"approved", "applied"}:
        requirement.reviewed_by = user.id
        requirement.reviewed_at = datetime.now(timezone.utc)

    requirement.updated_by = user.id
    if old_status != requirement.status or old_snapshot != _manual_snapshot(requirement):
        _record_event(
            db,
            requirement,
            event_type="updated",
            user=user,
            old_status=old_status,
            new_status=requirement.status,
            detail={
                "submitted_profile_item_uuid": requirement.submitted_profile_item_uuid,
                "submitted_profile_item_uuids": _submitted_profile_item_uuids(requirement),
                "submitted_file_id": requirement.submitted_file_id,
                "submitted_file_ids": _submitted_file_ids(requirement),
                "has_submitted_value": bool(requirement.submitted_value),
                "notes": requirement.notes,
                "manual_submission_touched": manual_submission_touched,
            },
        )
    return requirement


def serialize_bid_material_requirement(row: BidMaterialRequirement) -> dict[str, Any]:
    normalized = loads_json(row.normalized_json, {}) or {}
    evidence = loads_json(row.evidence_json, []) or []
    return {
        "requirement_uuid": row.requirement_uuid,
        "project_uuid": row.project.project_uuid if row.project else None,
        "run_uuid": row.parse_run.run_uuid if row.parse_run else None,
        "format_plan_uuid": row.format_plan.plan_uuid if row.format_plan else None,
        "format_item_key": row.format_item_key,
        "package_key": row.package_key,
        "package_title": row.package_title,
        "section_key": row.section_key,
        "item_title": row.item_title,
        "requirement_type": row.requirement_type,
        "profile_category": row.profile_category,
        "material_key": row.material_key,
        "title": row.title,
        "description": row.description,
        "source_file": row.source_file,
        "source_location": row.source_location,
        "source_text": row.source_text,
        "fulfillment_mode": row.fulfillment_mode,
        "status": row.status,
        "priority": row.priority,
        "owner_role": row.owner_role,
        "candidate_profile_item_uuid": row.candidate_profile_item_uuid,
        "submitted_profile_item_uuid": row.submitted_profile_item_uuid,
        "submitted_profile_item_uuids": _submitted_profile_item_uuids(row),
        "submitted_file_id": row.submitted_file_id,
        "submitted_file_ids": _submitted_file_ids(row),
        "submitted_value": row.submitted_value,
        "notes": row.notes,
        "normalized": normalized,
        "evidence": evidence,
        "candidates": normalized.get("candidates") or [],
        "candidate_profile_item": normalized.get("candidate_profile_item"),
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "events": [serialize_bid_material_requirement_event(event) for event in (row.events or [])][-20:],
    }


def serialize_bid_material_requirement_event(event: BidMaterialRequirementEvent) -> dict[str, Any]:
    return {
        "event_uuid": event.event_uuid,
        "event_type": event.event_type,
        "old_status": event.old_status,
        "new_status": event.new_status,
        "detail": loads_json(event.detail_json, {}) or {},
        "created_by": event.created_by,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def build_bid_material_requirement_summary(rows: list[BidMaterialRequirement]) -> dict[str, Any]:
    status_counter = Counter(row.status for row in rows)
    type_counter = Counter(row.requirement_type for row in rows)
    mode_counter = Counter(row.fulfillment_mode for row in rows)
    category_counter = Counter(row.profile_category or "none" for row in rows)
    total = len(rows)
    resolved = sum(status_counter.get(status, 0) for status in RESOLVED_STATUSES)
    open_count = total - resolved
    return {
        "total": total,
        "missing_count": status_counter.get("missing", 0),
        "candidate_found_count": status_counter.get("candidate_found", 0),
        "submitted_count": status_counter.get("submitted", 0),
        "approved_count": status_counter.get("approved", 0),
        "applied_count": status_counter.get("applied", 0),
        "not_applicable_count": status_counter.get("not_applicable", 0),
        "resolved_count": resolved,
        "open_count": open_count,
        "high_priority_open_count": sum(1 for row in rows if row.priority == "high" and row.status not in RESOLVED_STATUSES),
        "enterprise_profile_requirement_count": mode_counter.get("enterprise_profile", 0),
        "manual_upload_count": mode_counter.get("manual_upload", 0),
        "manual_fill_count": mode_counter.get("manual_fill", 0),
        "completion_rate": round(resolved / total, 4) if total else 0,
        "by_status": dict(status_counter),
        "by_type": dict(type_counter),
        "by_mode": dict(mode_counter),
        "by_category": dict(category_counter),
    }


def _build_requirement_slots(
    db: Session,
    plan: BidFileFormatPlan,
    structure: dict[str, Any],
    user: User,
    *,
    package_key: str | None = None,
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    order_index = 1
    for item, package in _iter_format_items(structure):
        current_package_key = clean_text(package.get("package_key"), 64) if isinstance(package, dict) else None
        if package_key and current_package_key != package_key:
            continue
        for slot in _slots_for_format_item(item, package):
            slot["order_index"] = order_index
            slot["format_plan_id"] = plan.id
            slot["format_plan_uuid"] = plan.plan_uuid
            slot["format_plan_review_status"] = plan.review_status
            _attach_candidates(db, user, slot)
            slots.append(slot)
            order_index += 1
    return _dedupe_slots(slots)


def _iter_format_items(structure: dict[str, Any]):
    packages = structure.get("packages") if isinstance(structure.get("packages"), list) else []
    for package in packages:
        if not isinstance(package, dict):
            continue
        for item in package.get("items") or []:
            if isinstance(item, dict):
                yield item, package


def _slots_for_format_item(item: dict[str, Any], package: dict[str, Any]) -> list[dict[str, Any]]:
    item_key = clean_text(item.get("item_key"), 255) or clean_text(item.get("base_item_key"), 128) or f"item:{uuid.uuid4().hex[:8]}"
    base_item_key = clean_text(item.get("base_item_key"), 128) or item_key.split(":")[-1]
    item_title = clean_text(item.get("item_title"), 255) or "未命名目录项"
    content_type = clean_text(item.get("content_type"), 64) or "other"
    package_key = clean_text(item.get("package_key"), 64) or clean_text(package.get("package_key"), 64)
    package_title = clean_text(package.get("package_title"), 255) or package_key
    owner_role = clean_text(item.get("owner_role"), 64)
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    source = evidence[0] if evidence and isinstance(evidence[0], dict) else {}

    primary = _primary_slot(
        item_key=item_key,
        base_item_key=base_item_key,
        item_title=item_title,
        content_type=content_type,
        package_key=package_key,
        package_title=package_title,
        owner_role=owner_role,
        source=source,
        evidence=evidence,
    )
    slots = [primary]
    category, _, _ = _profile_category_for_item(base_item_key, item_title, content_type)
    if item.get("requires_signature") and not _signature_stamp_is_enterprise_material(content_type, category):
        slots.append(
            _signature_stamp_slot(
                item_key=item_key,
                base_item_key=base_item_key,
                item_title=item_title,
                content_type=content_type,
                package_key=package_key,
                package_title=package_title,
                owner_role=owner_role,
                source=source,
                evidence=evidence,
            )
        )
    if item.get("requires_attachment") and content_type not in {"attachment_proof", "qualification_attachment"}:
        slots.append(
            _slot(
                item_key=item_key,
                slot_key="attachment_file",
                item_title=item_title,
                package_key=package_key,
                package_title=package_title,
                owner_role=owner_role,
                requirement_type="attachment",
                profile_category="attachment_asset",
                fulfillment_mode="enterprise_profile",
                title=f"{item_title}附件材料",
                description="该目录项要求提供附件或证明材料，优先从企业资料库选择，缺失时人工上传。",
                priority="high",
                source=source,
                evidence=evidence,
                keyword=item_title,
            )
        )
    return slots


def _signature_stamp_slot(
    *,
    item_key: str,
    base_item_key: str,
    item_title: str,
    content_type: str,
    package_key: str | None,
    package_title: str | None,
    owner_role: str | None,
    source: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return _slot(
        item_key=item_key,
        slot_key="signature_stamp",
        item_title=item_title,
        package_key=package_key,
        package_title=package_title,
        owner_role=owner_role or "经营",
        requirement_type="field",
        profile_category=None,
        fulfillment_mode="manual_fill",
        title=f"{item_title}签字盖章确认",
        description="该目录项需要签字、盖章或授权签署确认，生成草稿前需明确签署口径。",
        priority="high",
        source=source,
        evidence=evidence,
        keyword="签字盖章",
    )


def _signature_stamp_is_enterprise_material(content_type: str, category: str | None) -> bool:
    if content_type in {"attachment_proof", "qualification_attachment"}:
        return True
    return category in {"certificate", "qualification", "personnel", "project_performance", "attachment_asset"}


def _primary_slot(
    *,
    item_key: str,
    base_item_key: str,
    item_title: str,
    content_type: str,
    package_key: str | None,
    package_title: str | None,
    owner_role: str | None,
    source: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    category, slot_key, keyword = _profile_category_for_item(base_item_key, item_title, content_type)
    if content_type == "pricing_table":
        return _slot(
            item_key=item_key,
            slot_key="pricing_input",
            item_title=item_title,
            package_key=package_key,
            package_title=package_title,
            owner_role=owner_role or "预算",
            requirement_type="pricing",
            profile_category=None,
            fulfillment_mode="from_cost_quote",
            title=f"{item_title}报价数据",
            description="该目录项需要从报价清单或成本报价结果取数，后续生成投标文件时引用报价链路结果。",
            priority="high",
            source=source,
            evidence=evidence,
            keyword=item_title,
        )
    if content_type in {"attachment_proof", "qualification_attachment"}:
        return _slot(
            item_key=item_key,
            slot_key=slot_key or "proof_attachment",
            item_title=item_title,
            package_key=package_key,
            package_title=package_title,
            owner_role=owner_role or "经营",
            requirement_type="profile" if category else "attachment",
            profile_category=category or "attachment_asset",
            fulfillment_mode="enterprise_profile",
            title=f"{item_title}资料",
            description="该目录项需要证照、资质、人员、业绩或证明附件，优先从企业资料库匹配。",
            priority="high",
            source=source,
            evidence=evidence,
            keyword=keyword or item_title,
        )
    if content_type == "draft_section":
        return _slot(
            item_key=item_key,
            slot_key=slot_key or "section_material",
            item_title=item_title,
            package_key=package_key,
            package_title=package_title,
            owner_role=owner_role or "技术",
            requirement_type="section_text",
            profile_category=category or "technical_solution",
            fulfillment_mode="enterprise_profile",
            title=f"{item_title}编制素材",
            description="该目录项需要技术方案、项目组织、进度质量安全措施等正文素材，优先从企业资料库取可复用内容。",
            priority="normal",
            source=source,
            evidence=evidence,
            keyword=keyword or item_title,
        )
    if category:
        return _slot(
            item_key=item_key,
            slot_key=slot_key or "profile_value",
            item_title=item_title,
            package_key=package_key,
            package_title=package_title,
            owner_role=owner_role,
            requirement_type="form_value",
            profile_category=category,
            fulfillment_mode="enterprise_profile",
            title=f"{item_title}填写依据",
            description="该固定表单需要企业基础信息或承诺模板，优先从企业资料库匹配后再人工确认。",
            priority="high" if category in {"basic_info", "commitment_template"} else "normal",
            source=source,
            evidence=evidence,
            keyword=keyword or item_title,
        )
    return _slot(
        item_key=item_key,
        slot_key="manual_form_value",
        item_title=item_title,
        package_key=package_key,
        package_title=package_title,
        owner_role=owner_role,
        requirement_type="form_value" if content_type == "fixed_form" else "other",
        profile_category=None,
        fulfillment_mode="manual_fill",
        title=f"{item_title}人工填写内容",
        description="该目录项需要人工确认表格字段、响应口径或项目特定信息。",
        priority="normal",
        source=source,
        evidence=evidence,
        keyword=item_title,
    )


def _slot(
    *,
    item_key: str,
    slot_key: str,
    item_title: str,
    package_key: str | None,
    package_title: str | None,
    owner_role: str | None,
    requirement_type: str,
    profile_category: str | None,
    fulfillment_mode: str,
    title: str,
    description: str,
    priority: str,
    source: dict[str, Any],
    evidence: list[dict[str, Any]],
    keyword: str,
) -> dict[str, Any]:
    safe_slot_key = _safe_key(slot_key)
    material_key = f"{_safe_key(item_key)}:{safe_slot_key}"[:128]
    return {
        "format_item_key": item_key,
        "section_key": _safe_key(item_key).replace(":", "_")[:255],
        "package_key": package_key,
        "package_title": package_title,
        "item_title": item_title,
        "material_key": material_key,
        "slot_key": safe_slot_key,
        "requirement_type": requirement_type if requirement_type in BID_MATERIAL_REQUIREMENT_TYPES else "other",
        "profile_category": profile_category,
        "fulfillment_mode": fulfillment_mode if fulfillment_mode in BID_MATERIAL_FULFILLMENT_MODES else "manual_fill",
        "title": title[:255],
        "description": description,
        "priority": priority if priority in {"high", "normal", "low"} else "normal",
        "owner_role": owner_role,
        "source_file": clean_text(source.get("source_file"), 255),
        "source_location": clean_text(source.get("source_location"), 255),
        "source_text": clean_text(source.get("original_text")),
        "keyword": _keyword(keyword),
        "evidence": evidence[:5],
    }


def _profile_category_for_item(base_item_key: str, title: str, content_type: str) -> tuple[str | None, str | None, str | None]:
    text = f"{base_item_key} {title}"
    if _contains(text, ("营业执照", "business_license")):
        return "certificate", "business_license", "营业执照"
    if _contains(text, ("安全生产许可证", "safety_license")):
        return "certificate", "safety_license", "安全生产许可证"
    if _contains(text, ("资质", "资格证明", "qualification")):
        return "qualification", "qualification", "资质"
    if _contains(text, ("项目经理", "建造师", "管理人员", "项目管理机构", "组织架构", "人员配置")):
        return "personnel", "personnel", "项目经理"
    if _contains(text, ("类似", "业绩", "经验", "project_performance")):
        return "project_performance", "project_performance", _performance_keyword(title)
    if _contains(text, ("承诺", "廉洁", "保修", "质保", "售后")):
        return "commitment_template", "commitment_template", "承诺"
    if _contains(text, ("法定代表人", "授权委托", "投标函", "投标书")):
        return "basic_info", "basic_info", "企业基本信息"
    if content_type == "draft_section":
        return "technical_solution", "technical_solution", _technical_keyword(title)
    return None, None, None


def _attach_candidates(db: Session, user: User, slot: dict[str, Any]) -> None:
    candidate_map: dict[str, EnterpriseProfileItem] = {}
    if slot.get("fulfillment_mode") == "enterprise_profile" and slot.get("profile_category") and settings.feature_enterprise_profile:
        for keyword in _candidate_keywords_for_slot(slot):
            for item in _safe_candidate_query(db, user, category=slot.get("profile_category"), keyword=keyword):
                candidate_map[item.item_uuid] = item
    query_text = _candidate_query_text(slot)
    scored_candidates = sorted(
        (
            (_profile_candidate_match_score(query_text, item), item)
            for item in candidate_map.values()
        ),
        key=lambda pair: (pair[0], pair[1].updated_at or pair[1].created_at),
        reverse=True,
    )
    candidate_payloads = []
    eligible_payloads = []
    expected_category = slot.get("profile_category")
    for score, item in scored_candidates[:8]:
        quality = _profile_candidate_match_quality(query_text, expected_category, item, score)
        payload = _candidate_payload(item, match_score=score)
        payload["match_eligible"] = bool(quality.get("eligible"))
        payload["match_reject_reason"] = None if quality.get("eligible") else quality.get("reason")
        payload["match_category_ok"] = bool(quality.get("category_match"))
        payload["match_phrase_hits"] = quality.get("phrase_hits") or []
        if score >= BID_MATERIAL_PROFILE_CANDIDATE_MIN_SCORE or payload["match_eligible"]:
            candidate_payloads.append(payload)
        if payload["match_eligible"]:
            eligible_payloads.append(payload)
    candidate_payloads = candidate_payloads[:5]
    slot["candidate_profile_item_uuid"] = eligible_payloads[0]["item_uuid"] if eligible_payloads else None
    slot["candidate_profile_item"] = eligible_payloads[0] if eligible_payloads else None
    slot["candidates"] = candidate_payloads
    slot["status"] = "candidate_found" if eligible_payloads else "missing"


def _candidate_keywords_for_slot(slot: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in (
        slot.get("keyword"),
        slot.get("item_title"),
        slot.get("title"),
    ):
        keyword = clean_text(value, 80)
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
    return result


def _candidate_query_text(slot: dict[str, Any]) -> str:
    return " ".join(
        clean_text(value, 255) or ""
        for value in (
            slot.get("keyword"),
            slot.get("item_title"),
            slot.get("title"),
            slot.get("description"),
        )
        if value
    )


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _profile_candidate_match_score(query: str, item: EnterpriseProfileItem) -> float:
    haystack_parts = [
        item.title,
        item.summary,
        item.content_text,
        item.profile_key,
        item.subcategory,
        item.tags_json,
    ]
    haystack = " ".join(str(value or "") for value in haystack_parts)
    q = _normalize_match_text(query)
    h = _normalize_match_text(haystack)
    if not q or not h:
        return 0.0
    char_overlap = len(set(q) & set(h)) / max(len(set(q)), 1)
    sequence = SequenceMatcher(None, q, h[: max(len(q) * 3, 24)]).ratio()
    substring = 0.22 if q in h else 0.0
    phrase_bonus = 0.0
    for phrase in _candidate_keywords_for_match(query):
        if phrase and phrase in h:
            phrase_bonus = max(phrase_bonus, min(0.24, 0.08 + len(phrase) / 80))
    return min(1.0, char_overlap * 0.5 + sequence * 0.28 + substring + phrase_bonus)


def _profile_candidate_match_quality(
    query: str,
    category: str | None,
    item: EnterpriseProfileItem,
    score: float,
) -> dict[str, Any]:
    text = _normalize_match_text(_profile_candidate_text(item))
    phrases = _profile_candidate_required_phrases(query, category)
    phrase_hits = [phrase for phrase in phrases if _normalize_match_text(phrase) in text]
    category_match = bool(category and item.category == category)
    semantic_override = bool(phrase_hits) and (
        score >= BID_MATERIAL_PROFILE_CATEGORY_OVERRIDE_SCORE
        or (len(phrase_hits) >= 3 and score >= BID_MATERIAL_PROFILE_CANDIDATE_MIN_SCORE)
    )
    strong_category_keyword_match = category_match and bool(phrase_hits) and score >= 0.18
    if score < BID_MATERIAL_PROFILE_CANDIDATE_MIN_SCORE and not strong_category_keyword_match:
        return {
            "eligible": False,
            "reason": "score_below_threshold",
            "category_match": category_match,
            "phrase_hits": phrase_hits,
        }
    if phrases and not phrase_hits:
        return {
            "eligible": False,
            "reason": "required_phrase_missing",
            "category_match": category_match,
            "phrase_hits": phrase_hits,
        }
    if category and not category_match and not semantic_override:
        return {
            "eligible": False,
            "reason": "category_mismatch",
            "category_match": category_match,
            "phrase_hits": phrase_hits,
        }
    return {
        "eligible": True,
        "reason": "category_match" if category_match else "semantic_override",
        "category_match": category_match,
        "phrase_hits": phrase_hits,
    }


def _profile_candidate_text(item: EnterpriseProfileItem) -> str:
    return " ".join(
        str(value or "")
        for value in [item.title, item.summary, item.profile_key, item.subcategory, item.tags_json]
    )


def _profile_candidate_required_phrases(query: str, category: str | None) -> list[str]:
    normalized = _normalize_match_text(query)
    phrases: list[str] = []
    if category == "project_performance" or any(token in normalized for token in ("类似", "业绩", "经验", "合同", "performance", "experience", "contract")):
        phrases.extend(["类似", "业绩", "经验", "合同", "performance", "experience", "contract"])
    if category == "personnel" or any(token in normalized for token in ("项目经理", "建造师", "人员", "证书", "资格证", "注册证", "personnel", "certificate")):
        phrases.extend(["项目经理", "建造师", "人员", "证书", "资格证", "注册证", "personnel", "certificate"])
    if category == "certificate" or any(token in normalized for token in ("营业执照", "安全生产许可证", "许可证", "license", "permit")):
        phrases.extend(["营业执照", "安全生产许可证", "许可证", "license", "permit"])
    if category == "qualification" or any(token in normalized for token in ("资质", "资格证明", "qualification")):
        phrases.extend(["资质", "资格证明", "qualification"])
    if category == "commitment_template" or any(token in normalized for token in ("授权委托", "承诺", "保修", "commitment", "authorization")):
        phrases.extend(["授权委托", "承诺", "保修", "commitment", "authorization"])
    if category == "basic_info" or any(token in normalized for token in ("法定代表人", "企业基本", "投标函", "basic")):
        phrases.extend(["法定代表人", "企业基本", "投标函", "basic"])
    if category == "technical_solution" or any(token in normalized for token in ("方案", "措施", "施工组织", "质量", "安全", "文明施工", "technical", "solution")):
        phrases.extend(["方案", "措施", "施工组织", "质量", "安全", "文明施工", "technical", "solution"])
    return _unique_text(phrases)


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _candidate_keywords_for_match(value: str) -> list[str]:
    tokens = re.split(r"[\s,，、；;：:/\\（）()《》【】\[\]]+", clean_text(value, 300) or "")
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = clean_text(token, 40)
        if not token or token in seen or len(token) < 2:
            continue
        seen.add(token)
        result.append(_normalize_match_text(token))
    return result


def _safe_candidate_query(db: Session, user: User, *, category: str | None, keyword: str | None) -> list[EnterpriseProfileItem]:
    try:
        return list_active_profile_candidates(db, user, category=category, keyword=keyword, limit=5)
    except HTTPException:
        return []


def _candidate_payload(item: EnterpriseProfileItem, *, match_score: float | None = None) -> dict[str, Any]:
    data = serialize_item(item, detail=False)
    payload = {
        "item_uuid": data.get("item_uuid"),
        "category": data.get("category"),
        "subcategory": data.get("subcategory"),
        "profile_key": data.get("profile_key"),
        "title": data.get("title"),
        "summary": data.get("summary"),
        "valid_until": data.get("valid_until"),
        "attachment_count": data.get("attachment_count"),
        "quality_issues": data.get("quality_issues") or [],
    }
    if match_score is not None:
        payload["match_score"] = round(match_score, 4)
    return payload


def _row_fields_from_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_plan_id": slot.get("format_plan_id"),
        "format_item_key": slot["format_item_key"],
        "package_key": slot.get("package_key"),
        "package_title": slot.get("package_title"),
        "section_key": slot.get("section_key"),
        "item_title": slot["item_title"],
        "requirement_type": slot["requirement_type"],
        "profile_category": slot.get("profile_category"),
        "material_key": slot["material_key"],
        "title": slot["title"],
        "description": slot.get("description"),
        "source_file": slot.get("source_file"),
        "source_location": slot.get("source_location"),
        "source_text": slot.get("source_text"),
        "fulfillment_mode": slot["fulfillment_mode"],
        "status": slot.get("status") or "missing",
        "priority": slot.get("priority") or "normal",
        "owner_role": slot.get("owner_role"),
        "candidate_profile_item_uuid": slot.get("candidate_profile_item_uuid"),
        "normalized_json": dumps_json(_normalized_payload_from_slot(slot)),
        "evidence_json": dumps_json(slot.get("evidence") or []),
    }


def _refresh_row_from_slot(row: BidMaterialRequirement, slot: dict[str, Any], user: User) -> None:
    row.format_plan_id = slot.get("format_plan_id") or row.format_plan_id
    row.format_item_key = slot["format_item_key"]
    row.package_key = slot.get("package_key")
    row.package_title = slot.get("package_title")
    row.section_key = slot.get("section_key")
    row.item_title = slot["item_title"]
    row.requirement_type = slot["requirement_type"]
    row.profile_category = slot.get("profile_category")
    row.title = slot["title"]
    row.description = slot.get("description")
    row.source_file = slot.get("source_file")
    row.source_location = slot.get("source_location")
    row.source_text = slot.get("source_text")
    row.fulfillment_mode = slot["fulfillment_mode"]
    row.priority = slot.get("priority") or "normal"
    row.owner_role = slot.get("owner_role")
    row.candidate_profile_item_uuid = slot.get("candidate_profile_item_uuid")
    previous_normalized = loads_json(row.normalized_json, {}) or {}
    normalized = _normalized_payload_from_slot(slot)
    manual_submission = previous_normalized.get("manual_submission")
    if isinstance(manual_submission, dict):
        normalized["manual_submission"] = manual_submission
    row.normalized_json = dumps_json(normalized)
    row.evidence_json = dumps_json(slot.get("evidence") or [])
    if row.status not in MANUAL_LOCKED_STATUSES:
        row.status = slot.get("status") or "missing"
    row.updated_by = user.id


def _normalized_payload_from_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": BID_MATERIAL_REQUIREMENT_VERSION,
        "slot_key": slot.get("slot_key"),
        "keyword": slot.get("keyword"),
        "order_index": slot.get("order_index"),
        "format_plan_uuid": slot.get("format_plan_uuid"),
        "format_plan_review_status": slot.get("format_plan_review_status"),
        "candidate_profile_item": slot.get("candidate_profile_item"),
        "candidates": slot.get("candidates") or [],
        "extractor": "format_plan_rule",
        "llm_status": "reserved",
    }


def _record_event(
    db: Session,
    row: BidMaterialRequirement,
    *,
    event_type: str,
    user: User,
    old_status: str | None = None,
    new_status: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        BidMaterialRequirementEvent(
            event_uuid=str(uuid.uuid4()),
            requirement_id=row.id,
            project_id=row.project_id,
            parse_run_id=row.parse_run_id,
            event_type=event_type,
            old_status=old_status,
            new_status=new_status or row.status,
            detail_json=dumps_json(detail or {}),
            created_by=user.id,
        )
    )


def _manual_snapshot(row: BidMaterialRequirement) -> tuple[Any, ...]:
    return (
        tuple(_submitted_profile_item_uuids(row)),
        tuple(_submitted_file_ids(row)),
        row.submitted_profile_item_uuid,
        row.submitted_file_id,
        row.submitted_value,
        row.notes,
    )


def _has_manual_submission(row: BidMaterialRequirement) -> bool:
    return bool(
        _submitted_profile_item_uuids(row)
        or _submitted_file_ids(row)
        or row.submitted_profile_item_uuid
        or row.submitted_file_id
        or clean_text(row.submitted_value)
    )


def _manual_submission(row: BidMaterialRequirement) -> dict[str, Any]:
    normalized = loads_json(row.normalized_json, {}) or {}
    manual = normalized.get("manual_submission")
    return dict(manual) if isinstance(manual, dict) else {}


def _set_manual_submission_values(
    row: BidMaterialRequirement,
    *,
    profile_item_uuids: list[str] | None = None,
    file_ids: list[str] | None = None,
) -> None:
    normalized = loads_json(row.normalized_json, {}) or {}
    manual = normalized.get("manual_submission")
    if not isinstance(manual, dict):
        manual = {}
    if profile_item_uuids is not None:
        manual["profile_item_uuids"] = _clean_uuid_list(profile_item_uuids)
    if file_ids is not None:
        manual["file_ids"] = _clean_uuid_list(file_ids)
    normalized["manual_submission"] = manual
    row.normalized_json = dumps_json(normalized)


def _submitted_profile_item_uuids(row: BidMaterialRequirement) -> list[str]:
    manual = _manual_submission(row)
    values = _clean_uuid_list(manual.get("profile_item_uuids"))
    if values:
        return values
    return _clean_uuid_list([row.submitted_profile_item_uuid])


def _submitted_file_ids(row: BidMaterialRequirement) -> list[str]:
    manual = _manual_submission(row)
    values = _clean_uuid_list(manual.get("file_ids"))
    if values:
        return values
    return _clean_uuid_list([row.submitted_file_id])


def _clean_uuid_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value, 36)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _has_acceptable_evidence(row: BidMaterialRequirement) -> bool:
    if row.fulfillment_mode in {"from_cost_quote", "generate_draft"}:
        return True
    return _has_manual_submission(row) or bool(row.candidate_profile_item_uuid)


def _get_active_profile_item(db: Session, item_uuid: str) -> EnterpriseProfileItem:
    item = (
        db.query(EnterpriseProfileItem)
        .filter(
            EnterpriseProfileItem.item_uuid == item_uuid,
            EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE,
        )
        .first()
    )
    if not item:
        raise BidMaterialRequirementError("ENTERPRISE_PROFILE_ACTIVE_ITEM_NOT_FOUND")
    return item


def _row_sort_key(row: BidMaterialRequirement) -> tuple[Any, ...]:
    normalized = loads_json(row.normalized_json, {}) or {}
    return (
        _package_order(row.package_key),
        int(normalized.get("order_index") or row.id or 0),
        row.id or 0,
    )


def _normalize_package_scope(value: str | None) -> str | None:
    scope = (value or "").strip().lower()
    if scope in {"business", "technical", "unified"}:
        return scope
    return None


def _package_scope_label(value: str | None) -> str:
    labels = {
        "business": "商务标",
        "technical": "技术标",
        "unified": "统一投标文件",
        None: "全部投标文件",
        "": "全部投标文件",
    }
    return labels.get(value, value or "全部投标文件")


def _package_order(value: str | None) -> int:
    return {"business": 10, "technical": 20, "unified": 30}.get(value or "", 99)


def _safe_key(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9:_-]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw or f"slot_{uuid.uuid4().hex[:8]}"


def _keyword(value: str | None) -> str:
    raw = clean_text(value, 64) or ""
    for token in ("营业执照", "安全生产许可证", "资质", "项目经理", "建造师", "业绩", "承诺", "授权", "投标函"):
        if token in raw:
            if token == "业绩" and ("类似" in raw or "经验" in raw):
                return raw[:32]
            return token
    return raw[:32]


def _technical_keyword(title: str) -> str:
    for token in ("施工组织设计", "施工方案", "进度", "质量", "安全", "文明施工", "成品保护", "重难点", "临时用电", "售后", "保修"):
        if token in title:
            return token
    return title[:32]


def _performance_keyword(title: str) -> str:
    raw = clean_text(title, 64) or ""
    if "类似" in raw and "业绩" in raw:
        return "类似工程业绩"
    if "类似" in raw and "经验" in raw:
        return "类似工程经验"
    if raw:
        return raw[:32]
    return "类似工程业绩"


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _dedupe_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slot in slots:
        key = slot["material_key"]
        if key in seen:
            continue
        seen.add(key)
        result.append(slot)
    return result
