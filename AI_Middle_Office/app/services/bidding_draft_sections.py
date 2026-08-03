from __future__ import annotations

import uuid
import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bidding import (
    BidDraftSection,
    BidDraftSectionVersion,
    BidMaterialRequirement,
    BidParseRun,
    BidProject,
    BidProjectFile,
    TenderRequirement,
    TenderResponseItem,
    TenderRisk,
)
from app.models.enterprise_profile import ENTERPRISE_PROFILE_STATUS_ACTIVE, EnterpriseProfileItem
from app.models.user import User  # noqa: F401 - ensure users table is registered for draft foreign keys
from app.services.bidding_draft_outline import generate_bid_draft_outline
from app.services.bidding_llm_review import bidding_llm_model
from app.services.bidding_parser import dumps_json, loads_json
from app.services.model_gateway import post_json_via_gateway


BID_DRAFT_SECTION_GENERATOR_MODEL = "rule_section_draft_v1.3"
BID_TECHNICAL_COMPOSITION_DRAFT_MODEL = "biz4c2_technical_composition_draft_mvp_v3"
BID_TECHNICAL_COMPOSITION_LLM_DRAFT_PROMPT_VERSION = "biz4c2_technical_composition_draft_llm_v2"
BID_DRAFT_SECTION_LLM_PROMPT_VERSION = "biz4b_single_section_draft_llm_v3"
BID_DRAFT_REVIEW_STATUSES = {"draft", "reviewed", "needs_revision", "accepted"}
BID_DRAFT_GENERATOR_TYPES = {"rule", "llm"}
BID_DRAFT_TECHNICAL_TEMPLATE_HEADING = "## 企业能力/施工经验参考模板"

TECHNICAL_REQUIREMENT_FACT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "safety_civilized",
        "label": "安全文明施工",
        "keywords": ("安全文明", "安全生产", "文明施工", "防火", "消防", "动火", "扬尘", "噪声", "工完场清"),
    },
    {
        "key": "temporary_power",
        "label": "临时用电",
        "keywords": ("临时用电", "三级配电", "二级保护", "漏电保护", "一机一闸", "配电箱", "开关箱", "停送电"),
    },
    {
        "key": "waste_management",
        "label": "垃圾清运",
        "keywords": ("垃圾清理", "垃圾清运", "垃圾堆放", "垃圾运输", "垃圾堆场", "装修垃圾", "建筑垃圾", "分类收集", "袋装清运", "工完场清"),
    },
    {
        "key": "material_sample",
        "label": "材料样板",
        "keywords": ("材料样板", "主要材料样板", "样板提供", "样板报审", "封样", "封样确认", "回标前提供", "规格尺寸"),
    },
    {
        "key": "material_procurement",
        "label": "材料采购",
        "keywords": ("材料采购", "采购计划", "材料进场", "材料报审", "甲指乙供", "品牌规格", "供应周期", "供应商"),
    },
    {
        "key": "finished_product_protection",
        "label": "成品保护",
        "keywords": ("成品保护", "既有设施保护", "已完工程", "保护措施", "覆盖保护", "包裹保护", "交叉污染"),
    },
    {
        "key": "coordination",
        "label": "现场协调",
        "keywords": ("总承包", "总包", "监理", "交叉作业", "工作面移交", "界面划分", "接口界面", "配合协调", "专业配合"),
    },
)

TECHNICAL_COMPOSITION_REVIEW_FACT_KEYS_BY_INTENT: dict[str, tuple[str, ...]] = {
    "schedule_plan": ("coordination", "material_sample", "material_procurement"),
    "construction_organization": ("coordination", "finished_product_protection", "safety_civilized"),
    "safety_civil_fire": ("safety_civilized", "temporary_power", "waste_management"),
    "quality_assurance": ("material_sample", "finished_product_protection", "material_procurement"),
    "temporary_power_plan": ("temporary_power", "safety_civilized", "coordination"),
    "material_procurement_plan": ("material_procurement", "material_sample", "coordination"),
    "key_difficulty_analysis": (
        "coordination",
        "finished_product_protection",
        "material_sample",
        "temporary_power",
        "waste_management",
    ),
    "site_facility_management": ("coordination", "temporary_power", "finished_product_protection"),
    "waste_management_plan": ("waste_management", "safety_civilized", "coordination"),
    "material_sample_plan": ("material_sample", "material_procurement", "coordination"),
    "competitive_enhancement": (
        "coordination",
        "finished_product_protection",
        "material_procurement",
        "material_sample",
        "safety_civilized",
    ),
}


class BidDraftSectionError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def list_bid_draft_sections(db: Session, run: BidParseRun, *, package_key: str | None = None) -> list[BidDraftSection]:
    rows = (
        db.query(BidDraftSection)
        .filter(BidDraftSection.parse_run_id == run.id)
        .order_by(BidDraftSection.id.asc())
        .all()
    )
    rows = _filter_current_technical_composition_drafts(rows, run)
    package_scope = _normalize_package_scope(package_key)
    if package_scope:
        rows = [row for row in rows if _draft_section_package_key(row) == package_scope]
    return rows


async def generate_technical_bid_draft_from_composition(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    *,
    created_by: int,
    overwrite: bool = True,
    username: str | None = None,
) -> dict[str, Any]:
    plan = _technical_composition_plan_for_draft(run)
    components = [item for item in plan.get("components") or [] if isinstance(item, dict)]
    if not components:
        raise BidDraftSectionError("BID_TECHNICAL_COMPOSITION_NOT_GENERATED")

    current_section_keys = _technical_composition_current_section_keys(plan)
    stale_removed_count = _remove_stale_technical_composition_drafts(db, run, current_section_keys)

    material_rows = _current_technical_composition_material_rows(db, run, plan)
    material_by_component: dict[str, list[BidMaterialRequirement]] = {}
    for row in material_rows:
        normalized = loads_json(row.normalized_json, {}) or {}
        component_key = str((normalized.get("component") or {}).get("component_key") or row.format_item_key or "")
        if component_key:
            material_by_component.setdefault(component_key, []).append(row)

    generated: list[BidDraftSection] = []
    created_count = 0
    updated_count = 0
    skipped_count = 0
    placeholder_count = 0
    formal_count = 0
    llm_generated_count = 0
    llm_fallback_count = 0
    project_context = _technical_project_context(db, project, run)

    for index, component in enumerate(components, start=1):
        component_key = _technical_component_key(component, index)
        section_key = _technical_composition_section_key(component, index)
        existing = (
            db.query(BidDraftSection)
            .filter(BidDraftSection.parse_run_id == run.id, BidDraftSection.section_key == section_key)
            .first()
        )
        if existing and not overwrite:
            skipped_count += 1
            generated.append(existing)
            continue

        section_material_rows = material_by_component.get(component_key, [])
        content, evidence, placeholders, warnings, generation_decision = _build_technical_composition_draft_content(
            db,
            component,
            section_material_rows,
            order_index=index,
            project_context=project_context,
        )
        llm_entry = _technical_composition_draft_llm_entry(component, section_material_rows)
        generator_type = "rule"
        generator_model = BID_TECHNICAL_COMPOSITION_DRAFT_MODEL
        if llm_entry.get("eligible"):
            try:
                llm_content = await _build_technical_composition_llm_content_markdown(
                    db,
                    project,
                    run,
                    component,
                    section_material_rows,
                    evidence,
                    placeholders,
                    rule_content=content,
                    prior_section_context=_technical_composition_anti_repetition_context(generated),
                    order_index=index,
                    username=username,
                    trace_id=run.run_uuid,
                )
                llm_content = _ensure_technical_composition_placeholders(llm_content, placeholders)
                if not _technical_composition_llm_content_shape_ok(llm_content, content):
                    raise BidDraftSectionError("BID_TECHNICAL_COMPOSITION_LLM_THIN_CONTENT")
                content = llm_content
                generator_type = "llm"
                generator_model = bidding_llm_model()
                llm_generated_count += 1
                generation_decision["llm_entry"] = llm_entry
                generation_decision["llm_generation"] = {
                    "status": "success",
                    "prompt_version": BID_TECHNICAL_COMPOSITION_LLM_DRAFT_PROMPT_VERSION,
                    "model": generator_model,
                    "source_rule_model": BID_TECHNICAL_COMPOSITION_DRAFT_MODEL,
                    "content_char_count": len(content),
                    "paragraph_count": _technical_composition_paragraph_count(content),
                }
            except Exception as exc:
                llm_fallback_count += 1
                warnings = _unique_warnings(
                    warnings
                    + [
                        {
                            "level": "warn",
                            "code": "technical_composition_llm_fallback",
                            "message": "LLM 正文生成失败，已保留规则兜底草稿。",
                            "detail": _clip(getattr(exc, "code", None) or str(exc), 200),
                        }
                    ]
                )
                generation_decision["llm_entry"] = llm_entry
                generation_decision["llm_generation"] = {
                    "status": "fallback",
                    "prompt_version": BID_TECHNICAL_COMPOSITION_LLM_DRAFT_PROMPT_VERSION,
                    "model": bidding_llm_model(),
                    "source_rule_model": BID_TECHNICAL_COMPOSITION_DRAFT_MODEL,
                    "fallback_reason": _clip(getattr(exc, "code", None) or str(exc), 200),
                }
        else:
            generation_decision["llm_entry"] = llm_entry
            generation_decision["llm_generation"] = {
                "status": "not_applicable",
                "prompt_version": BID_TECHNICAL_COMPOSITION_LLM_DRAFT_PROMPT_VERSION,
                "source_rule_model": BID_TECHNICAL_COMPOSITION_DRAFT_MODEL,
                "reason": llm_entry.get("reason"),
            }
        content, evidence, placeholders, warnings, pending_resolution = _postprocess_technical_composition_pending_confirmations(
            db,
            project,
            run,
            component,
            content,
            evidence,
            placeholders,
            warnings,
            section_material_rows,
            created_by=created_by,
        )
        generation_decision["pending_confirmation_resolution"] = pending_resolution
        content, editorial_formalization = _formalize_llm_editorial_supplement_sections(content)
        generation_decision["llm_editorial_formalization"] = editorial_formalization
        generation_decision["quality_profile"]["placeholder_count"] = len(placeholders)
        generation_decision["quality_profile"]["quality_status"] = "needs_input" if placeholders else "ready"
        generation_decision["quality_profile"]["quality_status_label"] = "需人工补充" if placeholders else "可复核"
        generation_decision["acceptance_check"]["status"] = "warning" if placeholders else "pass"
        generation_decision["acceptance_check"]["status_label"] = "需补充后接收" if placeholders else "可进入人工复核"
        content, warnings, generation_decision = _apply_technical_composition_quality_self_review(
            content,
            warnings,
            generation_decision,
            component,
            project_context,
            placeholders,
        )
        source_requirement_ids = _unique_ints(
            [row.id for row in section_material_rows] + [int(item) for item in pending_resolution.get("material_requirement_ids") or []]
        )
        draft_mode = "placeholder" if placeholders else "formal"
        draft_status = "needs_input" if placeholders else "ready"
        if placeholders:
            placeholder_count += 1
        else:
            formal_count += 1

        if existing:
            draft = existing
            updated_count += 1
        else:
            draft = BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                section_key=section_key,
                content_version=0,
                created_by=created_by,
            )
            db.add(draft)
            created_count += 1

        draft.section_title = _technical_component_title(component, index)
        draft.section_type = "technical"
        draft.owner_role = "经营" if component.get("classification") == "fixed_enterprise_material" else "技术"
        draft.draft_mode = draft_mode
        draft.draft_status = draft_status
        draft.content_markdown = content
        draft.placeholders_json = dumps_json(placeholders)
        draft.source_response_item_uuids_json = dumps_json([])
        draft.source_requirement_ids_json = dumps_json(source_requirement_ids)
        draft.source_risk_ids_json = dumps_json([])
        draft.evidence_json = dumps_json(evidence)
        draft.warnings_json = dumps_json(warnings)
        draft.generation_decision_json = dumps_json(generation_decision)
        draft.generator_type = generator_type
        draft.generator_model = generator_model
        draft.review_status = "draft"
        draft.reviewer_note = None
        draft.reviewed_by = None
        draft.reviewed_at = None
        db.flush()
        _append_draft_version(
            db,
            draft,
            content_markdown=content,
            change_type="llm_generated" if generator_type == "llm" else ("generated" if not existing else "regenerated"),
            editor_id=created_by,
            editor_note="BIZ-4c-2 技术标组成一键生成",
            generator_type=generator_type,
            generator_model=generator_model,
        )
        generated.append(draft)

    db.flush()
    return {
        "version": "biz4c2_technical_bid_draft_mvp_v1",
        "source": "technical_composition_plan",
        "run_uuid": run.run_uuid,
        "component_count": len(components),
        "generated_count": len(generated),
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "stale_removed_count": stale_removed_count,
        "formal_count": formal_count,
        "placeholder_count": placeholder_count,
        "llm_generated_count": llm_generated_count,
        "llm_fallback_count": llm_fallback_count,
        "drafts": [serialize_bid_draft_section(row) for row in generated],
    }


async def generate_bid_draft_section(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    *,
    section_key: str,
    created_by: int,
    generator_type: str = "rule",
    package_key: str | None = None,
    username: str | None = None,
    trace_id: str | None = None,
) -> BidDraftSection:
    outline = generate_bid_draft_outline(db, project, run, package_key=package_key)
    section = _find_outline_section(outline, section_key)
    if not section:
        raise BidDraftSectionError("BID_DRAFT_SECTION_NOT_FOUND")
    generator_type = (generator_type or "rule").strip().lower()
    if generator_type not in BID_DRAFT_GENERATOR_TYPES:
        raise BidDraftSectionError("INVALID_BID_DRAFT_GENERATOR_TYPE")
    response_items = _response_items_for_section(db, run, section)
    requirements = _requirements_for_section(db, run, section)
    risks = _risks_for_section(db, run, section)
    evidence = _dedupe_evidence(
        _collect_evidence(response_items, requirements, risks)
        + _format_evidence_for_section(section)
        + _material_requirement_evidence_for_section(db, run, section)
        + _technical_composition_evidence_for_section(run, section)
    )
    base_generation_decision = _generation_decision(section)
    placeholders = _placeholders_for_section(section)
    warnings = _warnings_for_section(section, response_items, risks)
    quality_profile = _quality_profile_for_section(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        placeholders,
        base_generation_decision,
    )
    writing_plan = _writing_plan_for_section(section, response_items, requirements, risks, evidence, placeholders, quality_profile)
    generation_decision = _enriched_generation_decision(
        base_generation_decision,
        quality_profile,
        writing_plan,
        None,
        section=section,
        placeholders=placeholders,
    )
    rule_content = _build_content_markdown(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        placeholders,
        warnings,
        quality_profile,
        writing_plan,
    )
    quality_result = _quality_result_for_section(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        placeholders,
        quality_profile,
        writing_plan,
        rule_content,
        generator_type="rule",
    )
    content_evidence = _content_evidence_analysis(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        writing_plan,
        rule_content,
        review_source="rule_generated",
        diff_summary={},
    )
    semantic_quality = _semantic_quality_from_content_evidence(content_evidence)
    acceptance_check = _llm_acceptance_check(
        {},
        semantic_quality,
        quality_result,
        content_evidence=content_evidence,
    )
    generation_decision = _enriched_generation_decision(
        base_generation_decision,
        quality_profile,
        writing_plan,
        quality_result,
        section=section,
        placeholders=placeholders,
    )
    generation_decision["content_evidence"] = content_evidence
    generation_decision["semantic_quality"] = semantic_quality
    generation_decision["acceptance_check"] = acceptance_check
    warnings = _unique_warnings(warnings + _warnings_from_quality_result(quality_result))
    rule_content = _append_quality_result(rule_content, quality_result)
    generator_model = BID_DRAFT_SECTION_GENERATOR_MODEL
    content = rule_content
    existing = (
        db.query(BidDraftSection)
        .filter(BidDraftSection.parse_run_id == run.id, BidDraftSection.section_key == section["section_key"])
        .first()
    )
    llm_base_version = _latest_rule_base_version(existing) if existing else None
    if generator_type == "llm":
        llm_entry = generation_decision.get("llm_entry") if isinstance(generation_decision.get("llm_entry"), dict) else {}
        if not llm_entry.get("eligible"):
            raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_NOT_ALLOWED")
        base_rule_content = llm_base_version.content_markdown if llm_base_version else rule_content
        content = await _build_llm_content_markdown(
            section,
            response_items,
            requirements,
            risks,
            evidence,
            rule_content=rule_content,
            generation_context=generation_decision,
            username=username,
            trace_id=trace_id or run.run_uuid,
        )
        generator_model = bidding_llm_model()
        diff_summary = _llm_diff_summary(base_rule_content, content, base_version=llm_base_version)
        quality_result = _quality_result_for_section(
            section,
            response_items,
            requirements,
            risks,
            evidence,
            placeholders,
            quality_profile,
            writing_plan,
            content,
            generator_type="llm",
        )
        semantic_quality = _llm_semantic_quality(
            section,
            response_items,
            requirements,
            risks,
            evidence,
            writing_plan,
            base_rule_content,
            content,
            diff_summary,
        )
        content_evidence = _content_evidence_analysis(
            section,
            response_items,
            requirements,
            risks,
            evidence,
            writing_plan,
            content,
            review_source="llm_generated",
            diff_summary=diff_summary,
        )
        semantic_quality["content_evidence_status"] = content_evidence.get("status")
        semantic_quality["content_evidence_summary"] = content_evidence.get("summary")
        acceptance_check = _llm_acceptance_check(
            diff_summary,
            semantic_quality,
            quality_result,
            content_evidence=content_evidence,
        )
        generation_decision = _enriched_generation_decision(
            base_generation_decision,
            quality_profile,
            writing_plan,
            quality_result,
            section=section,
            placeholders=placeholders,
        )
        generation_decision["llm_enhancement"] = {
            "prompt_version": BID_DRAFT_SECTION_LLM_PROMPT_VERSION,
            "model": generator_model,
            "post_quality_status": quality_result.get("status"),
            "post_quality_status_label": quality_result.get("status_label"),
            "source_rule_model": BID_DRAFT_SECTION_GENERATOR_MODEL,
            "base_version_no": llm_base_version.version_no if llm_base_version else None,
            "base_change_type": llm_base_version.change_type if llm_base_version else "transient_rule_content",
            "base_generator_model": llm_base_version.generator_model if llm_base_version else BID_DRAFT_SECTION_GENERATOR_MODEL,
            "diff_summary": diff_summary,
            "semantic_quality": semantic_quality,
            "acceptance_check": acceptance_check,
        }
        generation_decision["diff_summary"] = diff_summary
        generation_decision["semantic_quality"] = semantic_quality
        generation_decision["content_evidence"] = content_evidence
        generation_decision["acceptance_check"] = acceptance_check
        warnings = _unique_warnings(warnings + _warnings_from_quality_result(quality_result))
        warnings = _unique_warnings(warnings + _warnings_from_semantic_quality(semantic_quality, acceptance_check))
        content = _append_quality_result(content, quality_result)
    if existing:
        draft = existing
    else:
        draft = BidDraftSection(
            draft_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            section_key=section["section_key"],
            content_version=0,
            created_by=created_by,
        )
        db.add(draft)

    draft.section_title = section["section_title"]
    draft.section_type = section["section_type"]
    draft.owner_role = section.get("owner_role")
    draft.draft_mode = section.get("draft_mode") or "placeholder"
    draft.draft_status = section.get("draft_status") or "needs_input"
    draft.content_markdown = content
    draft.placeholders_json = dumps_json(placeholders)
    draft.source_response_item_uuids_json = dumps_json(section.get("response_item_uuids") or [])
    draft.source_requirement_ids_json = dumps_json(section.get("requirement_ids") or [])
    draft.source_risk_ids_json = dumps_json(section.get("risk_ids") or [])
    draft.evidence_json = dumps_json(evidence)
    draft.warnings_json = dumps_json(warnings)
    draft.generation_decision_json = dumps_json(generation_decision)
    draft.generator_type = generator_type
    draft.generator_model = generator_model
    draft.review_status = "draft"
    draft.reviewer_note = None
    draft.reviewed_by = None
    draft.reviewed_at = None
    db.flush()
    _append_draft_version(
        db,
        draft,
        content_markdown=content,
        change_type="llm_generated" if generator_type == "llm" else "generated",
        editor_id=created_by,
        editor_note="单章节重新生成" if existing else "单章节初次生成",
        generator_type=generator_type,
        generator_model=generator_model,
    )
    db.refresh(draft)
    return draft


def update_bid_draft_section_content(
    db: Session,
    draft: BidDraftSection,
    *,
    content_markdown: str,
    editor_note: str | None,
    editor_id: int,
) -> BidDraftSection:
    content = (content_markdown or "").strip()
    if not content:
        raise BidDraftSectionError("BID_DRAFT_SECTION_CONTENT_REQUIRED")
    draft.content_markdown = content
    _refresh_draft_quality_after_manual_edit(db, draft, content, editor_id=editor_id)
    if draft.review_status == "accepted":
        draft.review_status = "reviewed"
    db.flush()
    _append_draft_version(
        db,
        draft,
        content_markdown=content,
        change_type="manual_edit",
        editor_id=editor_id,
        editor_note=(editor_note or "").strip()[:1000] or None,
        generator_type=draft.generator_type,
        generator_model=draft.generator_model,
    )
    db.refresh(draft)
    return draft


def update_bid_draft_section_review(
    db: Session,
    draft: BidDraftSection,
    *,
    review_status: str,
    reviewer_note: str | None,
    reviewer_id: int,
) -> BidDraftSection:
    if review_status not in BID_DRAFT_REVIEW_STATUSES:
        raise BidDraftSectionError("INVALID_BID_DRAFT_REVIEW_STATUS")
    if review_status == "accepted":
        _ensure_draft_acceptance_allowed(draft)
    draft.review_status = review_status
    draft.reviewer_note = (reviewer_note or "").strip()[:4000] or None
    draft.reviewed_by = reviewer_id
    draft.reviewed_at = datetime.now()
    db.flush()
    db.refresh(draft)
    return draft


def serialize_bid_draft_section(draft: BidDraftSection) -> dict[str, Any]:
    generation_decision = _generation_decision_from_draft(draft)
    quality_profile = generation_decision.get("quality_profile") if isinstance(generation_decision.get("quality_profile"), dict) else {}
    writing_plan = generation_decision.get("writing_plan") if isinstance(generation_decision.get("writing_plan"), dict) else {}
    quality_result = generation_decision.get("quality_result") if isinstance(generation_decision.get("quality_result"), dict) else {}
    llm_enhancement = generation_decision.get("llm_enhancement") if isinstance(generation_decision.get("llm_enhancement"), dict) else {}
    diff_summary = generation_decision.get("diff_summary") if isinstance(generation_decision.get("diff_summary"), dict) else {}
    semantic_quality = generation_decision.get("semantic_quality") if isinstance(generation_decision.get("semantic_quality"), dict) else {}
    acceptance_check = generation_decision.get("acceptance_check") if isinstance(generation_decision.get("acceptance_check"), dict) else {}
    content_evidence = generation_decision.get("content_evidence") if isinstance(generation_decision.get("content_evidence"), dict) else {}
    llm_entry = generation_decision.get("llm_entry") if isinstance(generation_decision.get("llm_entry"), dict) else {}
    if not llm_entry:
        llm_entry = _legacy_llm_entry(generation_decision, quality_profile, writing_plan, quality_result)
    versions = list(draft.versions or [])
    upgrade_hint = _draft_upgrade_hint(draft, versions)
    return {
        "id": draft.id,
        "draft_uuid": draft.draft_uuid,
        "project_id": draft.project_id,
        "parse_run_id": draft.parse_run_id,
        "section_key": draft.section_key,
        "section_title": draft.section_title,
        "section_type": draft.section_type,
        "package_key": _draft_section_package_key(draft),
        "package_title": generation_decision.get("package_title") if isinstance(generation_decision, dict) else None,
        "owner_role": draft.owner_role,
        "draft_mode": draft.draft_mode,
        "draft_status": draft.draft_status,
        "content_version": draft.content_version or 1,
        "content_markdown": draft.content_markdown,
        "placeholders": loads_json(draft.placeholders_json, []),
        "source_response_item_uuids": loads_json(draft.source_response_item_uuids_json, []),
        "source_requirement_ids": loads_json(draft.source_requirement_ids_json, []),
        "source_risk_ids": loads_json(draft.source_risk_ids_json, []),
        "evidence": loads_json(draft.evidence_json, []),
        "warnings": loads_json(draft.warnings_json, []),
        "generation_decision": generation_decision,
        "quality_profile": quality_profile,
        "writing_plan": writing_plan,
        "quality_result": quality_result,
        "llm_enhancement": llm_enhancement,
        "diff_summary": diff_summary,
        "semantic_quality": semantic_quality,
        "content_evidence": content_evidence,
        "acceptance_check": acceptance_check,
        "llm_entry": llm_entry,
        "llm_eligible": bool(llm_entry.get("eligible")),
        "generator_type": draft.generator_type,
        "generator_model": draft.generator_model,
        "versions": [_serialize_draft_version(row) for row in versions[-12:]],
        "upgrade_hint": upgrade_hint,
        "needs_upgrade": bool(upgrade_hint.get("needs_upgrade")),
        "review_status": draft.review_status,
        "reviewer_note": draft.reviewer_note,
        "reviewed_by": draft.reviewed_by,
        "reviewed_at": _format_dt(draft.reviewed_at),
        "created_by": draft.created_by,
        "created_at": _format_dt(draft.created_at),
        "updated_at": _format_dt(draft.updated_at),
    }


def _serialize_draft_version(row: BidDraftSectionVersion) -> dict[str, Any]:
    return {
        "version_uuid": row.version_uuid,
        "version_no": row.version_no,
        "change_type": row.change_type,
        "editor_note": row.editor_note,
        "generator_type": row.generator_type,
        "generator_model": row.generator_model,
        "edited_by": row.edited_by,
        "created_at": _format_dt(row.created_at),
    }


def _draft_upgrade_hint(draft: BidDraftSection, versions: list[BidDraftSectionVersion]) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    generator_type = (draft.generator_type or "rule").strip().lower()
    generator_model = (draft.generator_model or "").strip()
    content = draft.content_markdown or ""

    if generator_type == "rule" and generator_model != BID_DRAFT_SECTION_GENERATOR_MODEL:
        reasons.append(
            {
                "code": "old_rule_template",
                "message": f"规则模板已升级到 {BID_DRAFT_SECTION_GENERATOR_MODEL}，旧草稿建议重新生成。",
            }
        )
    if not loads_json(draft.generation_decision_json, {}):
        reasons.append(
            {
                "code": "missing_generation_decision",
                "message": "旧草稿缺少生成判定，无法准确区分可直接响应、需补资料和需风险决策。",
            }
        )
    if not versions:
        reasons.append(
            {
                "code": "missing_version_records",
                "message": "旧草稿没有版本记录，重新生成后可追踪规则生成、DeepSeek 生成和人工编辑。",
            }
        )
    if draft.section_type == "technical" and BID_DRAFT_TECHNICAL_TEMPLATE_HEADING not in content:
        reasons.append(
            {
                "code": "missing_technical_template",
                "message": "技术方案旧草稿未接入企业能力/施工经验模板，建议重新生成。",
            }
        )

    return {
        "needs_upgrade": bool(reasons),
        "level": "warning" if reasons else "ok",
        "message": "该章节由旧模板生成，建议点击“升级草稿”重新生成。" if reasons else "",
        "reasons": reasons,
        "latest_rule_model": BID_DRAFT_SECTION_GENERATOR_MODEL,
        "current_generator_model": draft.generator_model,
    }


def _generation_decision_from_draft(draft: BidDraftSection) -> dict[str, Any]:
    value = loads_json(draft.generation_decision_json, {})
    if isinstance(value, dict) and value:
        return value
    if draft.draft_mode == "formal":
        return {
            "code": "direct_response",
            "label": "可直接响应",
            "draft_mode": "formal",
            "llm_eligible": True,
            "reason": "历史章节未保存生成决策，按正式草稿兼容处理。",
        }
    if draft.draft_mode == "review_note" or draft.draft_status == "blocked":
        return {
            "code": "review_note",
            "label": "生成复核说明",
            "draft_mode": draft.draft_mode or "blocked",
            "llm_eligible": False,
            "reason": "历史章节未保存生成决策，按复核说明兼容处理。",
        }
    return {
        "code": "needs_input",
        "label": "需补资料",
        "draft_mode": draft.draft_mode or "placeholder",
        "llm_eligible": False,
        "reason": "历史章节未保存生成决策，按需补资料兼容处理。",
    }


def _normalize_package_scope(value: str | None) -> str | None:
    scope = (value or "").strip().lower()
    if scope in {"business", "technical", "unified"}:
        return scope
    return None


def _draft_section_package_key(draft: BidDraftSection) -> str | None:
    generation_decision = loads_json(draft.generation_decision_json, {}) if draft.generation_decision_json else {}
    if isinstance(generation_decision, dict):
        package_key = _normalize_package_scope(generation_decision.get("package_key"))
        if package_key:
            return package_key
    match = re.search(r":package:([^:]+)", draft.section_key or "")
    if match:
        package_key = _normalize_package_scope(match.group(1))
        if package_key:
            return package_key
    if draft.section_type == "technical":
        return "technical"
    if draft.section_type in {"business", "qualification", "pricing", "legal", "clarification", "attachment"}:
        return "business"
    return None


def _append_draft_version(
    db: Session,
    draft: BidDraftSection,
    *,
    content_markdown: str,
    change_type: str,
    editor_id: int,
    editor_note: str | None = None,
    generator_type: str | None = None,
    generator_model: str | None = None,
) -> BidDraftSectionVersion:
    current = (
        db.query(BidDraftSectionVersion.version_no)
        .filter(BidDraftSectionVersion.draft_section_id == draft.id)
        .order_by(BidDraftSectionVersion.version_no.desc())
        .first()
    )
    base_version = int(draft.content_version or 0)
    version_no = int(current[0]) + 1 if current else max(base_version + 1, 1)
    row = BidDraftSectionVersion(
        version_uuid=str(uuid.uuid4()),
        draft_section_id=draft.id,
        version_no=version_no,
        change_type=change_type,
        content_markdown=content_markdown,
        editor_note=editor_note,
        generator_type=generator_type,
        generator_model=generator_model,
        edited_by=editor_id,
    )
    db.add(row)
    draft.content_version = version_no
    db.flush()
    return row


def _latest_draft_version(draft: BidDraftSection | None) -> BidDraftSectionVersion | None:
    if not draft or not draft.versions:
        return None
    return sorted(draft.versions, key=lambda row: row.version_no or 0)[-1]


def _latest_rule_base_version(draft: BidDraftSection | None) -> BidDraftSectionVersion | None:
    if not draft or not draft.versions:
        return None
    versions = sorted(draft.versions, key=lambda row: row.version_no or 0)
    for row in reversed(versions):
        if row.change_type == "generated" and (row.generator_type or "rule") == "rule":
            return row
    return None


def _ensure_draft_acceptance_allowed(draft: BidDraftSection) -> None:
    generation_decision = loads_json(draft.generation_decision_json, {})
    acceptance_check = generation_decision.get("acceptance_check") if isinstance(generation_decision, dict) else {}
    if isinstance(acceptance_check, dict) and acceptance_check.get("status") == "blocked":
        raise BidDraftSectionError("BID_DRAFT_SECTION_ACCEPTANCE_BLOCKED")


def _refresh_draft_quality_after_manual_edit(
    db: Session,
    draft: BidDraftSection,
    content: str,
    *,
    editor_id: int,
) -> None:
    section = _section_for_existing_draft(db, draft)
    response_items = _response_items_for_section(db, draft.parse_run, section)
    requirements = _requirements_for_section(db, draft.parse_run, section)
    risks = _risks_for_section(db, draft.parse_run, section)
    evidence = loads_json(draft.evidence_json, [])
    if not evidence:
        evidence = _dedupe_evidence(
            _collect_evidence(response_items, requirements, risks)
            + _format_evidence_for_section(section)
            + _material_requirement_evidence_for_section(db, draft.parse_run, section)
            + _technical_composition_evidence_for_section(draft.parse_run, section)
        )
    placeholders = loads_json(draft.placeholders_json, [])
    generation_decision = _generation_decision_from_draft(draft)
    base_generation_decision = _generation_decision(section)
    quality_profile = (
        generation_decision.get("quality_profile")
        if isinstance(generation_decision.get("quality_profile"), dict)
        else _quality_profile_for_section(section, response_items, requirements, risks, evidence, placeholders, base_generation_decision)
    )
    writing_plan = (
        generation_decision.get("writing_plan")
        if isinstance(generation_decision.get("writing_plan"), dict)
        else _writing_plan_for_section(section, response_items, requirements, risks, evidence, placeholders, quality_profile)
    )
    quality_result = _quality_result_for_section(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        placeholders,
        quality_profile,
        writing_plan,
        content,
        generator_type=draft.generator_type or "rule",
    )
    base_version = _latest_rule_base_version(draft)
    base_content = base_version.content_markdown if base_version else content
    diff_summary = _llm_diff_summary(base_content, content, base_version=base_version) if base_version else {}
    semantic_quality = _llm_semantic_quality(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        writing_plan,
        base_content,
        content,
        diff_summary,
    )
    content_evidence = _content_evidence_analysis(
        section,
        response_items,
        requirements,
        risks,
        evidence,
        writing_plan,
        content,
        review_source="manual_edit",
        diff_summary=diff_summary,
    )
    semantic_quality["content_evidence_status"] = content_evidence.get("status")
    semantic_quality["content_evidence_summary"] = content_evidence.get("summary")
    acceptance_check = _llm_acceptance_check(
        diff_summary,
        semantic_quality,
        quality_result,
        content_evidence=content_evidence,
    )
    generation_decision["quality_profile"] = quality_profile
    generation_decision["writing_plan"] = writing_plan
    generation_decision["quality_result"] = quality_result
    generation_decision["diff_summary"] = diff_summary
    generation_decision["semantic_quality"] = semantic_quality
    generation_decision["content_evidence"] = content_evidence
    generation_decision["acceptance_check"] = acceptance_check
    generation_decision["manual_edit_review"] = {
        "reviewed_at": datetime.now().isoformat(),
        "editor_id": editor_id,
        "base_version_no": base_version.version_no if base_version else None,
        "status": acceptance_check.get("status"),
        "summary": acceptance_check.get("summary"),
    }
    draft.generation_decision_json = dumps_json(generation_decision)
    base_warnings = _warnings_for_section(section, response_items, risks)
    draft.warnings_json = dumps_json(
        _unique_warnings(
            base_warnings
            + _warnings_from_quality_result(quality_result)
            + _warnings_from_semantic_quality(semantic_quality, acceptance_check)
            + _warnings_from_content_evidence(content_evidence)
        )
    )


def _section_for_existing_draft(db: Session, draft: BidDraftSection) -> dict[str, Any]:
    try:
        outline = generate_bid_draft_outline(db, draft.project, draft.parse_run)
        section = _find_outline_section(outline, draft.section_key)
        if section:
            return section
    except Exception:
        pass
    generation_decision = _generation_decision_from_draft(draft)
    return {
        "section_key": draft.section_key,
        "section_title": draft.section_title,
        "section_type": draft.section_type,
        "owner_role": draft.owner_role,
        "draft_mode": draft.draft_mode,
        "draft_status": draft.draft_status,
        "response_item_uuids": loads_json(draft.source_response_item_uuids_json, []),
        "requirement_ids": loads_json(draft.source_requirement_ids_json, []),
        "risk_ids": loads_json(draft.source_risk_ids_json, []),
        "generation_decision": generation_decision,
        "format_evidence": [],
    }


def _find_outline_section(outline: dict[str, Any], section_key: str) -> dict[str, Any] | None:
    for section in outline.get("sections") or []:
        if section.get("section_key") == section_key:
            return section
    return None


def _generation_decision(section: dict[str, Any]) -> dict[str, Any]:
    value = section.get("generation_decision")
    if isinstance(value, dict) and value:
        return value
    draft_mode = section.get("draft_mode") or "placeholder"
    if draft_mode == "formal":
        return {
            "code": "direct_response",
            "label": "可直接响应",
            "draft_mode": "formal",
            "llm_eligible": True,
            "reason": "目录骨架判定为正式可成稿。",
        }
    if draft_mode in {"blocked", "review_note"}:
        return {
            "code": "review_note",
            "label": "生成复核说明",
            "draft_mode": draft_mode,
            "llm_eligible": False,
            "reason": "目录骨架判定为先复核，不进入 LLM 正文生成。",
        }
    return {
        "code": "needs_input",
        "label": "需补资料",
        "draft_mode": "placeholder",
        "llm_eligible": False,
        "reason": "目录骨架判定仍需人工补充。",
    }


def _response_items_for_section(db: Session, run: BidParseRun, section: dict[str, Any]) -> list[TenderResponseItem]:
    uuids = [str(item) for item in section.get("response_item_uuids") or [] if item]
    if not uuids:
        return []
    return (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id, TenderResponseItem.response_item_uuid.in_(uuids))
        .order_by(TenderResponseItem.id.asc())
        .all()
    )


def _requirements_for_section(db: Session, run: BidParseRun, section: dict[str, Any]) -> list[TenderRequirement]:
    ids = _int_list(section.get("requirement_ids"))
    if not ids:
        return []
    return (
        db.query(TenderRequirement)
        .filter(TenderRequirement.parse_run_id == run.id, TenderRequirement.id.in_(ids))
        .order_by(TenderRequirement.id.asc())
        .all()
    )


def _risks_for_section(db: Session, run: BidParseRun, section: dict[str, Any]) -> list[TenderRisk]:
    ids = _int_list(section.get("risk_ids"))
    if not ids:
        return []
    return db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id, TenderRisk.id.in_(ids)).order_by(TenderRisk.id.asc()).all()


def _collect_evidence(
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in response_items:
        for raw in loads_json(item.evidence_json, []) if item.evidence_json else []:
            if isinstance(raw, dict):
                evidence.append(
                    {
                        "source_kind": raw.get("source_kind") or item.created_from or "response_item",
                        "source_file": raw.get("source_file"),
                        "source_location": raw.get("source_location"),
                        "original_text": _clip(raw.get("original_text") or item.source_text, 280),
                        "response_item_uuid": item.response_item_uuid,
                        "response_title": item.response_title,
                    }
                )
    for item in requirements[:6]:
        evidence.append(
            {
                "source_kind": "requirement",
                "source_file": item.source_file,
                "source_location": item.source_location,
                "original_text": _clip(item.original_text, 280),
                "requirement_id": item.id,
            }
        )
    for item in risks[:6]:
        evidence.append(
            {
                "source_kind": "risk",
                "source_file": item.source_file,
                "source_location": item.source_location,
                "original_text": _clip(item.original_text, 280),
                "risk_id": item.id,
                "risk_level": item.risk_level,
            }
        )
    return _dedupe_evidence(evidence)[:12]


def _format_evidence_for_section(section: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    for raw in section.get("format_evidence") or []:
        if not isinstance(raw, dict):
            continue
        evidence.append(
            {
                "source_kind": raw.get("source_kind") or "file_format_plan",
                "source_file": raw.get("source_file"),
                "source_location": raw.get("source_location"),
                "original_text": _clip(raw.get("original_text"), 280),
                "format_item_key": section.get("format_item_key"),
            }
        )
    return evidence[:8]


def _technical_composition_plan_for_draft(run: BidParseRun) -> dict[str, Any]:
    summary = loads_json(run.summary_json, {}) or {}
    plan = summary.get("technical_composition_plan")
    return plan if isinstance(plan, dict) and plan.get("status") == "generated" else {}


def _filter_current_technical_composition_drafts(
    rows: list[BidDraftSection],
    run: BidParseRun,
) -> list[BidDraftSection]:
    plan = _technical_composition_plan_for_draft(run)
    current_keys = _technical_composition_current_section_keys(plan)
    if not current_keys:
        return rows
    return [
        row
        for row in rows
        if not _is_technical_composition_draft(row) or row.section_key in current_keys
    ]


def _technical_composition_current_section_keys(plan: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for index, component in enumerate(plan.get("components") or [], start=1):
        if isinstance(component, dict):
            keys.add(_technical_composition_section_key(component, index))
    return keys


def _technical_composition_section_key(component: dict[str, Any], index: int) -> str:
    source_item_key = _technical_source_item_key(component.get("source_item_no"))
    if source_item_key:
        return f"technical_composition:{source_item_key}"
    return f"technical_composition:{_technical_component_key(component, index)}"


def _technical_source_item_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"\d+(?:\.\d+)+", text)
    if match:
        text = match.group(0)
    key = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", text).strip("_").lower()
    return key[:80]


def _technical_composition_section_no(component: dict[str, Any]) -> str:
    for value in (
        component.get("source_item_no"),
        component.get("component_key"),
        component.get("component_title"),
    ):
        text = str(value or "").replace("_", ".")
        match = re.search(r"\d+(?:\.\d+)+", text)
        if match:
            return match.group(0)
    return ""


TECHNICAL_COMPOSITION_INTENT_TEMPLATES: dict[str, dict[str, Any]] = {
    "schedule_plan": {
        "label": "施工总进度计划",
        "section_numbers": ("7.3.9",),
        "aliases": (
            "施工总进度计划",
            "总进度计划",
            "施工进度计划",
            "总工期",
            "工期计划",
            "材料进场时间",
            "设备进场时间",
            "schedule",
        ),
        "headings": (
            "编制依据",
            "工期响应与编制原则",
            "总体进度安排",
            "阶段施工计划",
            "主要材料进场计划",
            "主要设备及机械进场计划",
            "劳动力与交叉作业协调",
            "进度检查、纠偏与验收移交",
        ),
        "writing_requirements": (
            "施工总进度计划必须形成正式进度计划章节，不得只写响应总工期的概述。",
            "必须覆盖工期响应、总体进度安排、阶段施工计划、主要材料进场计划、主要设备及机械进场计划、劳动力组织、交叉作业协调、进度检查、纠偏和验收移交。",
            "应体现装饰装修工程的施工准备、深化与样板、基层及隐蔽、面层及安装、整改复验、材料报审、设备报验、工作面移交和成品保护节点。",
            "未提供具体开工日期、总工期天数或材料设备到场日期时，不得编造具体日期；应使用招标文件约定、开工令、总控计划倒排、对应工序前完成报审进场等可复核表达。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "construction_organization": {
        "label": "施工组织设计",
        "section_numbers": ("7.3.10",),
        "aliases": (
            "施工组织设计",
            "施工组织",
            "施工部署",
            "组织架构",
            "组织设计",
            "施工流程",
            "工序衔接",
            "construction organization",
            "construction plan",
        ),
        "headings": (
            "编制依据",
            "施工组织总体部署",
            "项目组织架构与职责",
            "施工流程与工序衔接",
            "资源投入与现场平面管理",
            "质量、安全文明与成品保护",
            "进度协调与验收移交",
            "应急与沟通机制",
        ),
        "writing_requirements": (
            "施工组织设计必须形成正式技术标章节，不得只写一段概述或待补充清单。",
            "必须覆盖施工组织总体部署、项目组织架构与职责、施工流程与工序衔接、资源投入与现场平面管理、质量安全文明与成品保护、进度协调与验收移交、应急与沟通机制。",
            "应体现装饰装修工程的分区施工、样板先行、材料进场、隐蔽验收、交叉作业协调、垃圾清运、临电消防和成品保护管理。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "safety_civil_fire": {
        "label": "安全文明防火施工方案",
        "section_numbers": ("7.3.15",),
        "aliases": (
            "安全生产",
            "文明施工",
            "防火施工",
            "消防管理",
            "安全文明",
            "动火",
            "临时用电",
            "安全措施",
            "civilized construction",
            "fire",
            "safety",
        ),
        "headings": (
            "编制依据",
            "安全生产管理目标与责任体系",
            "安全教育交底与作业许可",
            "临时用电、动火与消防管理",
            "文明施工与环境保护",
            "高处、临边及交叉作业控制",
            "检查整改、应急处置与资料闭环",
        ),
        "writing_requirements": (
            "安全生产、文明施工、防火施工方案和保证措施必须形成正式专项章节，不得只写原则性承诺。",
            "必须覆盖安全责任体系、安全教育交底、作业许可、临时用电、动火消防、文明施工、环境保护、高处临边及交叉作业、检查整改、应急处置和资料闭环。",
            "应体现装饰装修工程的粉尘噪声控制、垃圾清运、材料堆放、消防通道、个人防护、特殊工种持证、动火审批、临电巡检和成品保护管理。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "quality_assurance": {
        "label": "施工质量保障措施",
        "section_numbers": ("7.3.16",),
        "aliases": (
            "施工质量保障",
            "质量保障措施",
            "质量保证措施",
            "质量保证体系",
            "质量控制措施",
            "施工质量",
            "隐蔽验收",
            "实测实量",
            "质量通病",
            "quality assurance",
            "quality control",
        ),
        "headings": (
            "编制依据",
            "质量目标与管理责任体系",
            "样板引路与技术交底",
            "材料设备进场与报审复核",
            "工序过程控制与隐蔽验收",
            "实测实量与质量通病防治",
            "成品保护、整改复验与资料闭环",
        ),
        "writing_requirements": (
            "施工质量保障措施必须形成正式专项章节，不得只写质量承诺或口号。",
            "必须覆盖质量目标与责任体系、样板引路、技术交底、材料设备进场报审、工序过程控制、隐蔽验收、实测实量、质量通病防治、成品保护、整改复验和资料闭环。",
            "应体现装饰装修工程的基层处理、龙骨及隐蔽工程、面层安装、收口细部、机电末端配合、观感质量、检验批资料和竣工移交质量控制。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "temporary_power_plan": {
        "label": "施工临时用电施工方案",
        "section_numbers": ("7.3.13",),
        "aliases": (
            "施工临时用电施工方案",
            "施工临时用电",
            "临时用电施工方案",
            "临时用电方案",
            "临时用电",
            "三级配电",
            "二级保护",
            "一机一闸",
            "漏电保护",
            "配电箱",
            "temporary power",
        ),
        "headings": (
            "编制依据",
            "临时用电管理目标与组织职责",
            "配电系统与箱体布置",
            "线路敷设、照明与机具用电控制",
            "动火、潮湿区域及交叉作业用电管理",
            "巡检维护、停送电与应急处置",
            "验收记录、整改复查与资料闭环",
        ),
        "writing_requirements": (
            "施工临时用电施工方案必须形成正式专项章节，不得只写“按现场条件待确认”。",
            "必须覆盖临时用电组织管理、配电系统、配电箱布置、三级配电二级保护、一机一闸一漏一箱、线路敷设、照明和机具用电、动火及潮湿区域用电、巡检维护、停送电、应急处置和资料闭环。",
            "未提供具体接驳点、容量或配电箱数量时，不得编造具体参数；应使用服从现场审批、总承包管理、进场复核和专项验收等可复核表达。",
            "除缺少硬事实必须保留人工入口外，不得输出“待确认”“待补充/待复核”等内部待办标题。",
        ),
    },
    "material_procurement_plan": {
        "label": "主要材料采购计划",
        "section_numbers": ("7.3.14",),
        "aliases": (
            "主要材料采购计划",
            "材料采购计划",
            "材料采购",
            "甲指乙供",
            "材料进场",
            "材料报审",
            "样板确认",
            "供应商",
            "替代材料",
            "procurement",
        ),
        "headings": (
            "编制依据",
            "采购组织原则与责任分工",
            "材料需求计划与进场批次",
            "品牌规格、样板与报审复核",
            "供应周期、运输到场与现场验收",
            "堆放领用、追溯管理与成品保护",
            "甲指乙供、替代审批与风险纠偏",
        ),
        "writing_requirements": (
            "主要材料采购计划必须形成正式采购组织章节，不得只写材料按需采购或待图纸确认。",
            "必须覆盖采购责任分工、需求计划、进场批次、品牌规格复核、样板确认、材料报审、供应商协调、运输到场、进场验收、堆放领用、追溯管理、甲指乙供配合、替代审批和风险纠偏。",
            "应体现装饰装修工程材料品类多、观感要求高、定制周期、环保性能、样板先行、批次供应和成品保护的管理要求。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "key_difficulty_analysis": {
        "label": "项目重难点分析",
        "section_numbers": ("7.3.19",),
        "aliases": (
            "项目重难点分析",
            "重难点分析",
            "重点难点",
            "施工难点",
            "技术难点",
            "风险识别",
            "难点对策",
            "重点分析",
            "key difficulty",
            "difficulty analysis",
        ),
        "headings": (
            "编制依据",
            "项目特点与重难点识别",
            "工期组织与交叉作业控制",
            "材料样板、报审与供应保障",
            "隐蔽工程、细部收口与观感质量控制",
            "成品保护、既有设施与现场秩序维护",
            "安全文明、临电消防与垃圾清运控制",
            "重难点跟踪、纠偏与验收移交",
        ),
        "writing_requirements": (
            "项目重难点分析必须形成“难点识别 + 对策措施 + 检查闭环”的正式技术标章节，不得只写笼统风险提示。",
            "必须覆盖工期紧张、交叉作业、材料样板和报审、供应保障、隐蔽工程、细部收口、观感质量、成品保护、既有设施保护、安全文明、临电消防、垃圾清运、界面协调和验收移交。",
            "应结合当前项目范围、施工区域、工期、质量目标和现场管理要求进行项目化表达。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "site_facility_management": {
        "label": "办公室、工具间、材料间管理方案",
        "section_numbers": ("7.3.11",),
        "aliases": (
            "办公室、工具间、材料间",
            "办公室工具间材料间",
            "办公室管理",
            "工具间管理",
            "材料间管理",
            "临时设施管理",
            "现场平面管理",
            "仓储管理",
            "site facility",
            "site storage",
        ),
        "headings": (
            "编制依据",
            "临时设施布置原则与管理目标",
            "办公室管理",
            "工具间管理",
            "材料间管理",
            "消防、临电、文明施工与成品保护",
            "台账检查、责任落实与动态调整",
        ),
        "writing_requirements": (
            "办公室、工具间、材料间管理方案必须形成正式现场管理章节，不得只写服从总包安排。",
            "必须覆盖临时设施布置原则、办公室管理、工具间管理、材料间管理、消防临电、文明施工、成品保护、台账检查、责任落实和动态调整。",
            "应体现装饰装修工程材料品类多、工具机具多、交叉作业多、通道和消防要求高的现场管理特点。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "waste_management_plan": {
        "label": "垃圾清理、堆放、运输、堆场管理方案",
        "section_numbers": ("7.3.12",),
        "aliases": (
            "垃圾清理",
            "垃圾堆放",
            "垃圾运输",
            "垃圾堆场",
            "垃圾清运",
            "建筑垃圾",
            "装修垃圾",
            "废料清运",
            "工完场清",
            "waste management",
        ),
        "headings": (
            "编制依据",
            "垃圾管理目标与责任分工",
            "分类收集与日常清理",
            "临时堆放点与堆场管理",
            "场内运输、外运配合与路线控制",
            "扬尘、噪声、消防和安全文明控制",
            "检查记录、整改销项与资料闭环",
        ),
        "writing_requirements": (
            "垃圾清理、堆放、运输、堆场管理方案必须形成正式现场文明施工章节，不得只写及时清运。",
            "必须覆盖垃圾分类、日常清理、临时堆放、堆场管理、场内运输、外运配合、路线控制、扬尘噪声、消防安全、文明施工、检查记录和整改闭环。",
            "应体现装修拆改、切割打磨、包装废料、边角料和交叉作业产生垃圾的管理特点。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "material_sample_plan": {
        "label": "主要材料样板提供计划",
        "section_numbers": ("7.3.17",),
        "aliases": (
            "主要材料样板",
            "材料样板提供",
            "材料样板",
            "样板提供计划",
            "样板报审",
            "样板确认",
            "封样",
            "样板管理",
            "material sample",
            "sample submission",
        ),
        "headings": (
            "编制依据",
            "样板提供原则与责任分工",
            "样板清单、规格复核与报审计划",
            "样板制作、封样确认与留存管理",
            "样板与采购、进场和施工质量联动",
            "变更替代、复核纠偏与资料闭环",
        ),
        "writing_requirements": (
            "主要材料样板提供计划必须形成正式样板管理章节，不得只写按发包人要求提供。",
            "必须覆盖样板清单、规格尺寸复核、样板制作、样板报审、封样确认、留存管理、采购联动、进场复核、变更替代、资料闭环和责任分工。",
            "应体现装饰装修工程颜色、纹理、观感、环保性能、尺寸规格和节点收口样板确认的重要性。",
            "除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
    "competitive_enhancement": {
        "label": "提升投标竞争力内容",
        "section_numbers": ("7.3.20",),
        "aliases": (
            "提升投标竞争力",
            "投标竞争力",
            "竞争力内容",
            "竞争优势",
            "技术优势",
            "增值服务",
            "优化建议",
            "合理化建议",
            "value added",
            "competitive advantage",
        ),
        "headings": (
            "编制依据",
            "投标竞争力提升总体思路",
            "进度、组织与协同优势",
            "质量、样板与精细化管控优势",
            "安全文明、成品保护与现场秩序优势",
            "材料供应、成本控制与风险响应优势",
            "服务承诺、资料移交与持续改进",
        ),
        "writing_requirements": (
            "提升投标竞争力内容必须形成正式优势说明章节，不得写成空泛宣传语。",
            "必须覆盖进度组织、协同管理、质量样板、精细化管控、安全文明、成品保护、材料供应、风险响应、服务承诺、资料移交和持续改进。",
            "应基于当前项目范围、工期、质量目标和现场管理要求提出可执行、可复核的优势措施。",
            "不得虚构企业资质、人员、业绩或奖项；除缺少硬事实必须保留人工入口外，不得输出“待人工完善”“待补充/待复核”等内部待办标题。",
        ),
    },
}


TECHNICAL_COMPOSITION_INTENT_BY_SECTION_NO = {
    section_no: intent
    for intent, template in TECHNICAL_COMPOSITION_INTENT_TEMPLATES.items()
    for section_no in template.get("section_numbers") or ()
}


def _technical_composition_intent(component: dict[str, Any]) -> str:
    text = _technical_composition_intent_text(component)
    normalized = re.sub(r"\s+", "", text.lower())
    scores: dict[str, int] = {}
    for intent, template in TECHNICAL_COMPOSITION_INTENT_TEMPLATES.items():
        score = 0
        for alias in template.get("aliases") or ():
            needle = re.sub(r"\s+", "", alias.lower())
            if not needle:
                continue
            if needle in normalized:
                score += 3 if len(needle) >= 4 else 1
        scores[intent] = score

    section_intent = TECHNICAL_COMPOSITION_INTENT_BY_SECTION_NO.get(_technical_composition_section_no(component))
    if section_intent:
        scores[section_intent] = scores.get(section_intent, 0) + 2

    best_intent, best_score = max(scores.items(), key=lambda item: item[1], default=("", 0))
    return best_intent if best_score > 0 else ""


def _technical_composition_intent_template(intent: str) -> dict[str, Any]:
    template = TECHNICAL_COMPOSITION_INTENT_TEMPLATES.get(str(intent or ""))
    return template if isinstance(template, dict) else {}


def _technical_composition_intent_text(component: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("source_item_no", "component_key", "component_title", "classification_reason", "draft_instruction"):
        parts.append(str(component.get(key) or ""))
    for item in component.get("source_evidence") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("original_text") or item.get("text") or ""))
    for need in component.get("information_needs") or []:
        if not isinstance(need, dict):
            continue
        for key in ("need_title", "query", "reason", "polished_text"):
            parts.append(str(need.get(key) or ""))
    return " ".join(part for part in parts if part)


def _is_technical_composition_draft(row: BidDraftSection) -> bool:
    return str(row.section_key or "").startswith("technical_composition:")


def _remove_stale_technical_composition_drafts(
    db: Session,
    run: BidParseRun,
    current_section_keys: set[str],
) -> int:
    if not current_section_keys:
        return 0
    rows = (
        db.query(BidDraftSection)
        .filter(
            BidDraftSection.parse_run_id == run.id,
            BidDraftSection.section_key.like("technical_composition:%"),
        )
        .all()
    )
    removed = 0
    for row in rows:
        if row.section_key in current_section_keys:
            continue
        if not _technical_composition_draft_safe_to_remove(db, row):
            continue
        db.delete(row)
        removed += 1
    if removed:
        db.flush()
    return removed


def _technical_composition_draft_safe_to_remove(db: Session, row: BidDraftSection) -> bool:
    if row.review_status != "draft" or row.reviewed_by or row.reviewer_note:
        return False
    manual_version = (
        db.query(BidDraftSectionVersion.id)
        .filter(
            BidDraftSectionVersion.draft_section_id == row.id,
            BidDraftSectionVersion.change_type == "manual_edit",
        )
        .first()
    )
    return manual_version is None


def _current_technical_composition_material_rows(
    db: Session,
    run: BidParseRun,
    plan: dict[str, Any],
) -> list[BidMaterialRequirement]:
    requirement_sync = plan.get("requirement_sync") if isinstance(plan.get("requirement_sync"), dict) else {}
    current_keys = {
        str(row.get("material_key"))
        for row in requirement_sync.get("rows") or []
        if isinstance(row, dict) and row.get("material_key")
    }
    query = db.query(BidMaterialRequirement).filter(
        BidMaterialRequirement.parse_run_id == run.id,
        BidMaterialRequirement.package_key == "technical",
        BidMaterialRequirement.section_key.like("technical_composition:%"),
        BidMaterialRequirement.status.in_(["submitted", "approved", "applied"]),
    )
    if current_keys:
        query = query.filter(BidMaterialRequirement.material_key.in_(current_keys))
    return query.order_by(BidMaterialRequirement.id.asc()).all()


def _technical_component_key(component: dict[str, Any], index: int) -> str:
    raw = component.get("component_key") or component.get("source_item_no") or component.get("component_title") or f"component_{index}"
    key = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(raw)).strip("_").lower()
    return (key or f"component_{index}")[:120]


def _technical_component_title(component: dict[str, Any], index: int) -> str:
    title = str(component.get("component_title") or "").strip()
    source_item_no = str(component.get("source_item_no") or "").strip()
    if source_item_no and title and not title.startswith(source_item_no):
        return f"{source_item_no} {title}"[:255]
    return (title or f"技术标组成项 {index}")[:255]


def _build_technical_composition_draft_content(
    db: Session,
    component: dict[str, Any],
    material_rows: list[BidMaterialRequirement],
    *,
    order_index: int,
    project_context: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    title = _technical_component_title(component, order_index)
    classification = str(component.get("classification") or "manual_input")
    needs = [item for item in component.get("information_needs") or [] if isinstance(item, dict)]
    profile_needs = [item for item in needs if item.get("source_type") == "enterprise_profile"]
    tender_needs = [item for item in needs if item.get("source_type") == "tender_document"]
    manual_needs = [item for item in needs if item.get("source_type") == "manual_input"]
    evidence = _dedupe_evidence(
        _technical_component_source_evidence(component)
        + _technical_need_source_evidence(needs)
        + [item for row in material_rows for item in _material_requirement_row_evidence(db, row)]
    )
    placeholders: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if profile_needs and not material_rows:
        placeholders.append(
            {
                "code": "enterprise_profile_material_missing",
                "label": "固定企业资料待补齐",
                "detail": "该组成项需要企业资料库内容，但当前资料补齐清单没有可用资料。",
            }
        )
    for need in manual_needs:
        placeholders.append(
            {
                "code": "manual_input_required",
                "label": str(need.get("need_title") or "人工补充内容"),
                "detail": str(need.get("reason") or need.get("query") or "需结合项目情况补充。")[:500],
            }
        )
    if classification in {"manual_input", "mixed"} and not tender_needs and not material_rows:
        warnings.append(
            {
                "code": "composition_draft_placeholder",
                "message": "该组成项缺少可直接落稿的企业资料或招标文件润色内容，已生成占位草稿。",
            }
        )

    content_lines = [
        f"# {title}",
        "",
        "## 编制依据",
        f"- 技术标组成识别：{_technical_composition_class_label(classification)}。",
    ]
    for item in evidence[:6]:
        source_file = item.get("source_file") or "招标文件"
        source_location = item.get("source_location") or "-"
        original_text = _clip(item.get("original_text"), 180)
        if original_text:
            content_lines.append(f"- {source_file} / {source_location}：{original_text}")
    if len(content_lines) == 4:
        content_lines.append("- 暂无可追溯证据，需人工复核后完善。")

    profile_lines = _technical_profile_material_lines(db, material_rows)
    if profile_lines:
        content_lines.extend(["", "## 企业资料引用", *profile_lines])

    section_no = _technical_composition_section_no(component)
    intent = _technical_composition_intent(component)
    response_lines = _technical_response_draft_lines(component, needs, material_rows)
    if intent == "schedule_plan":
        content_lines.extend(_technical_schedule_plan_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "construction_organization":
        content_lines.extend(_technical_construction_organization_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "safety_civil_fire":
        content_lines.extend(_technical_safety_civil_fire_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "quality_assurance":
        content_lines.extend(_technical_quality_assurance_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "temporary_power_plan":
        content_lines.extend(_technical_temporary_power_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "material_procurement_plan":
        content_lines.extend(_technical_material_procurement_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "key_difficulty_analysis":
        content_lines.extend(_technical_key_difficulty_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "site_facility_management":
        content_lines.extend(_technical_site_facility_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "waste_management_plan":
        content_lines.extend(_technical_waste_management_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "material_sample_plan":
        content_lines.extend(_technical_material_sample_rule_lines(component, needs, material_rows, project_context=project_context))
    elif intent == "competitive_enhancement":
        content_lines.extend(_technical_competitive_enhancement_rule_lines(component, needs, material_rows, project_context=project_context))
    else:
        content_lines.extend(["", "## 投标响应草稿", *response_lines])

    if placeholders:
        content_lines.extend(["", "## 待人工完善"])
        for item in placeholders:
            content_lines.append(f"- 【待补充】{item['label']}：{item.get('detail') or ''}")

    generation_decision = {
        "code": "technical_composition_mvp",
        "label": "技术标组成生成草案",
        "source": "technical_composition",
        "package_key": "technical",
        "package_title": "技术标",
        "component_key": component.get("component_key"),
        "component_title": component.get("component_title"),
        "source_item_no": component.get("source_item_no"),
        "section_no": section_no,
        "section_intent": intent,
        "classification": classification,
        "classification_label": _technical_composition_class_label(classification),
        "draft_mode": "placeholder" if placeholders else "formal",
        "reason": component.get("classification_reason") or "基于投标文件组成识别结果生成技术标章节草稿。",
        "quality_profile": {
            "quality_status": "needs_input" if placeholders else "ready",
            "quality_status_label": "需人工补充" if placeholders else "可复核",
            "evidence_count": len(evidence),
            "material_requirement_count": len(material_rows),
            "placeholder_count": len(placeholders),
        },
        "writing_plan": {
            "target_output": "technical_bid_section",
            "target_output_label": "技术标章节草稿",
            "suggested_headings": _technical_composition_rule_suggested_headings(component),
            "must_cover_requirements": [str(item.get("need_title") or item.get("query") or "") for item in needs if item],
            "review_focus": ["核对企业资料是否正确", "核对招标文件响应是否完整", "补齐占位内容"],
        },
        "acceptance_check": {
            "status": "warning" if placeholders else "pass",
            "status_label": "需补充后接收" if placeholders else "可进入人工复核",
            "summary": f"已生成草稿，证据 {len(evidence)} 条，占位 {len(placeholders)} 项。",
        },
    }
    content, warnings, generation_decision = _apply_technical_composition_quality_self_review(
        "\n".join(content_lines).strip(),
        warnings,
        generation_decision,
        component,
        project_context,
        placeholders,
    )
    return content, evidence, placeholders, warnings, generation_decision


def _technical_composition_draft_llm_entry(
    component: dict[str, Any],
    material_rows: list[BidMaterialRequirement],
) -> dict[str, Any]:
    classification = str(component.get("classification") or "manual_input")
    needs = [item for item in component.get("information_needs") or [] if isinstance(item, dict)]
    source_types = {str(item.get("source_type") or "") for item in needs}
    title = _technical_component_title(component, 0)
    title_text = f"{component.get('source_item_no') or ''} {title} {component.get('draft_instruction') or ''}"
    scheme_like = _technical_composition_scheme_like(component)
    fixed_material_only = (
        classification == "fixed_enterprise_material"
        and source_types <= {"enterprise_profile", ""}
        and not scheme_like
    )
    if fixed_material_only:
        return {
            "eligible": False,
            "reason": "固定企业资料章节按资料引用生成，不进入自由正文扩写。",
            "classification": classification,
            "scheme_like": False,
            "source_types": sorted(source_types),
        }
    if classification in {"tender_extracted_content", "mixed", "manual_input"} and (
        scheme_like or "tender_document" in source_types or "manual_input" in source_types
    ):
        return {
            "eligible": True,
            "reason": "方案型或项目化编写章节，需要基于招标文件、企业资料和项目背景生成成段技术标正文。",
            "classification": classification,
            "scheme_like": scheme_like,
            "source_types": sorted(source_types),
            "material_requirement_count": len(material_rows),
        }
    if scheme_like:
        return {
            "eligible": True,
            "reason": "标题语义属于技术方案/保证措施/组织设计类章节，进入 LLM 正文扩写。",
            "classification": classification,
            "scheme_like": True,
            "source_types": sorted(source_types),
            "material_requirement_count": len(material_rows),
        }
    return {
        "eligible": False,
        "reason": "该组成项更接近资料附件或固定信息引用，保留规则草稿。",
        "classification": classification,
        "scheme_like": False,
        "source_types": sorted(source_types),
        "title_probe": _clip(title_text, 160),
    }


def _technical_composition_scheme_like(component: dict[str, Any]) -> bool:
    source_item_no = str(component.get("source_item_no") or "")
    match = re.search(r"\b7\.3\.(\d+)\b", source_item_no)
    if match and 10 <= int(match.group(1)) <= 20:
        return True
    text = " ".join(
        str(part or "")
        for part in [
            source_item_no,
            component.get("component_title"),
            component.get("classification_reason"),
            component.get("draft_instruction"),
        ]
    )
    keywords = (
        "方案",
        "措施",
        "施工组织",
        "组织设计",
        "施工部署",
        "进度计划",
        "质量保证",
        "安全文明",
        "文明施工",
        "环境保护",
        "重点难点",
        "难点分析",
        "管理方案",
        "保证措施",
        "协调",
        "资源配置",
        "劳动力",
        "机械设备",
        "材料供应",
        "成品保护",
        "应急预案",
        "项目管理",
        "method statement",
        "construction plan",
        "safety",
        "quality",
        "schedule",
    )
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


async def _build_technical_composition_llm_content_markdown(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    component: dict[str, Any],
    material_rows: list[BidMaterialRequirement],
    evidence: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    *,
    rule_content: str,
    prior_section_context: dict[str, Any] | None = None,
    order_index: int,
    username: str | None,
    trace_id: str | None,
) -> str:
    provider = (settings.bidding_llm_provider or "deepseek").strip().lower()
    model = bidding_llm_model()
    if provider != "deepseek":
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_PROVIDER_NOT_SUPPORTED")
    if not (settings.deepseek_api_key or "").strip():
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_NOT_CONFIGURED")
    title = _technical_component_title(component, order_index)
    payload = {
        "model": model,
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是建筑装饰工程技术标正文编制助手，负责把投标文件组成中的方案型章节写成可复核的中文正文。"
                    "必须基于输入的招标文件证据、企业资料和项目背景组织内容，不得编造企业资质、人员姓名、证书编号、业绩项目、报价金额或工期压缩承诺。"
                    "对证据不足的硬事实使用【待确认：...】保留人工入口；不要把待补齐事项写成已经完成。"
                    "输出必须是严格 JSON，字段为 content_markdown；content_markdown 只包含该章节 Markdown 正文。"
                ),
            },
            {
                "role": "system",
                "content": (
                    "全文去重硬约束：本章只能回答本章职责，不得机械复述招标目录编号，不得使用“围绕7.3.x”作为行文开头；"
                    "不得重复前文章节的完整句、通用开场白或通用收尾承诺；同一管理动作如已在前章完整说明，本章只写与当前专业有关的参数、责任、频率、验收和记录。"
                    "禁止反复写“所产生的各项费用均已包括于投标金额中”。输出前自行删除与 document_anti_repetition 中短语相同或高度近似的句子。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt_version": BID_TECHNICAL_COMPOSITION_LLM_DRAFT_PROMPT_VERSION,
                        "task": "technical_composition_section_rich_markdown",
                        "project": _technical_composition_project_payload(project),
                        "project_context": _technical_project_context(db, project, run),
                        "project_facts": _technical_project_facts(db, project, run),
                        "document_anti_repetition": prior_section_context or {},
                        "component": {
                            "component_key": component.get("component_key"),
                            "section_title": title,
                            "source_item_no": component.get("source_item_no"),
                            "classification": component.get("classification"),
                            "classification_reason": component.get("classification_reason"),
                            "draft_instruction": component.get("draft_instruction"),
                        },
                        "information_needs": _technical_composition_need_payload(component),
                        "tender_evidence": evidence[:10],
                        "tender_background_segments": _technical_composition_context_segments(db, project, run, component),
                        "enterprise_profile_materials": _technical_composition_material_payload(db, material_rows),
                        "manual_placeholders": _technical_composition_placeholder_payload(placeholders),
                        "rule_fallback_markdown": rule_content,
                        "expected_headings": _technical_composition_rule_suggested_headings(component),
                        "writing_requirements": _technical_composition_writing_requirements(component),
                        "output_schema": {"content_markdown": "完整 Markdown 技术标章节正文"},
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="bidding_technical_composition_draft",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key.strip()}"},
        timeout=settings.bidding_llm_timeout_seconds,
        username=username,
        trace_id=f"bid-tech-draft:{trace_id or run.run_uuid}:{component.get('component_key') or order_index}",
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_FAILED")
    try:
        response_payload = response.json()
        content = response_payload["choices"][0]["message"]["content"]
    except Exception as exc:
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_BAD_RESPONSE") from exc
    parsed = _extract_json_object(content)
    markdown = str(parsed.get("content_markdown") or "").strip()
    if not markdown:
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_EMPTY_CONTENT")
    return markdown + "\n"


def _technical_composition_anti_repetition_context(drafts: list[BidDraftSection]) -> dict[str, Any]:
    avoid_phrases = [
        "所产生的各项费用均已包括于投标金额中",
    ]
    section_signatures: list[dict[str, Any]] = []
    for draft in drafts[-10:]:
        content = str(draft.content_markdown or "")
        sentences = _technical_anti_repetition_sentences(content)
        signature_phrases = _unique_text(sentences[:2] + sentences[-1:])
        headings = _unique_text(
            [
                re.sub(r"^#{1,6}\s*", "", line).strip()
                for line in content.splitlines()
                if re.match(r"^#{1,6}\s+\S", line.strip())
            ]
        )[:8]
        avoid_phrases.extend(signature_phrases)
        section_signatures.append(
            {
                "section_title": draft.section_title or draft.section_key or "-",
                "headings": headings,
                "opening_and_closing_phrases": signature_phrases,
            }
        )
    return {
        "previous_section_count": len(drafts),
        "section_signatures": section_signatures,
        "avoid_exact_phrases": _unique_text(avoid_phrases)[:32],
        "rules": [
            "不得以围绕7.3.x、根据7.3.x要求等目录复述句开篇",
            "同一完整句不得跨章节重复",
            "通用管理动作只在主责章节完整展开，其他章节只写专业差异",
            "优先写责任岗位、实施步骤、检查频率、验收依据和形成记录",
        ],
    }


def _technical_anti_repetition_sentences(content: str) -> list[str]:
    result: list[str] = []
    for raw_line in str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("|") or line.startswith("```"):
            continue
        line = re.sub(r"^(?:[-*]|\d+[.)、])\s+", "", line)
        for part in re.split(r"(?<=[。！？；])", line):
            sentence = re.sub(r"\s+", " ", part).strip()
            if len(re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", sentence)) >= 10:
                result.append(sentence[:180])
    return result


def _technical_composition_project_payload(project: BidProject) -> dict[str, Any]:
    summary = loads_json(project.summary_json, {}) or {}
    return {
        "project_name": project.project_name,
        "tenderer_name": project.tenderer_name,
        "tender_agency": project.tender_agency,
        "project_location": project.project_location,
        "project_type": project.project_type,
        "summary": _compact_prompt_mapping(summary),
    }


def _technical_composition_need_payload(component: dict[str, Any]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for need in component.get("information_needs") or []:
        if not isinstance(need, dict):
            continue
        payload.append(
            {
                "need_key": need.get("need_key"),
                "need_title": need.get("need_title"),
                "source_type": need.get("source_type"),
                "profile_category": need.get("profile_category"),
                "query": _clip(need.get("query"), 300),
                "reason": _clip(need.get("reason"), 300),
                "polished_text": _clip(need.get("polished_text"), 800),
                "source_evidence": _technical_need_source_evidence([need])[:4],
            }
        )
    return payload


def _technical_composition_material_payload(
    db: Session,
    material_rows: list[BidMaterialRequirement],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in material_rows:
        profile_items = []
        for item_uuid in _material_requirement_profile_item_uuids(row):
            item = _enterprise_profile_item_for_material(db, item_uuid)
            if not item:
                continue
            attachments = [
                attachment.original_filename or attachment.description or attachment.file_id
                for attachment in item.attachments or []
                if attachment.original_filename or attachment.description or attachment.file_id
            ]
            profile_items.append(
                {
                    "item_uuid": item.item_uuid,
                    "title": item.title,
                    "category": item.category,
                    "subcategory": item.subcategory,
                    "summary": _clip(item.summary, 300),
                    "content_text": _clip(item.content_text, 1200),
                    "attachments": attachments[:8],
                }
            )
        payload.append(
            {
                "requirement_uuid": row.requirement_uuid,
                "title": row.title,
                "description": _clip(row.description, 300),
                "submitted_value": _clip(row.submitted_value, 800),
                "submitted_file_ids": _material_requirement_file_ids(row),
                "profile_items": profile_items,
            }
        )
    return payload[:12]


def _technical_project_context(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    facts = _technical_project_facts(db, project, run)
    work_zone_names = _technical_context_work_zone_names(project, run, facts)
    zone_base = "、".join(work_zone_names)
    return {
        "project_name": project.project_name,
        "work_zone_names": work_zone_names,
        "work_zone_phrase": f"{zone_base}及相关配合区域" if zone_base else "本项目各施工区域及相关配合区域",
        "schedule_zone_phrase": f"{zone_base}及相关专业工作面" if zone_base else "各施工段及相关专业工作面",
        "affected_zone_phrase": f"{zone_base}及周边受影响区域" if zone_base else "各施工区域及周边受影响区域",
        "schedule": facts.get("schedule") or {},
        "quality": facts.get("quality") or {},
        "scope": facts.get("scope") or {},
        "technical_requirements": facts.get("technical_requirements") or {},
    }


def _technical_project_context_phrase(
    project_context: dict[str, Any] | None,
    key: str,
    fallback: str,
) -> str:
    if isinstance(project_context, dict):
        value = str(project_context.get(key) or "").strip()
        if value:
            return value
    return fallback


def _technical_context_work_zone_names(
    project: BidProject | None,
    run: BidParseRun | None,
    facts: dict[str, Any] | None = None,
) -> list[str]:
    names: list[str] = []
    names.extend(_work_zone_names_from_project_name(getattr(project, "project_name", None)))
    schedule = (facts or {}).get("schedule") or {}
    for zone in _schedule_zones(schedule):
        name = _normalize_work_zone_name(zone.get("name"))
        if name:
            names.append(name)
    scope = (facts or {}).get("scope") or {}
    names.extend(_work_zone_names_from_value(scope.get("work_zones") or []))

    for obj in [loads_json(getattr(project, "summary_json", None), {}) or {}, loads_json(getattr(run, "summary_json", None), {}) or {}]:
        for key in ("work_zones", "施工区域", "施工范围", "工程范围", "project_scope", "scope"):
            if key in obj:
                names.extend(_work_zone_names_from_value(obj.get(key)))
    return _unique_text([item for item in names if item])[:6]


def _work_zone_names_from_project_name(value: Any) -> list[str]:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return []
    candidates: list[str] = []
    candidates.extend(match.group(0) for match in re.finditer(r"\d+#楼\d+(?:F|层)办公区", text, flags=re.I))
    if "商业街区" in text:
        candidates.insert(0, "商业街区")
    return _unique_text([_normalize_work_zone_name(item) for item in candidates if _normalize_work_zone_name(item)])


def _work_zone_names_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for part in value for item in _work_zone_names_from_value(part)]
    if isinstance(value, dict):
        result: list[str] = []
        for key in ("name", "zone", "area", "区域", "施工区域", "范围"):
            if key in value:
                result.extend(_work_zone_names_from_value(value.get(key)))
        return result
    text = re.sub(r"\s+", "", str(value or "")).strip()
    if not text:
        return []
    parts = re.split(r"[、,，;/；]|及|和|与", text)
    return [_normalize_work_zone_name(part) for part in parts if _normalize_work_zone_name(part)]


def _normalize_work_zone_name(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip("：:，,。；;、")
    text = re.sub(r"(施工区域|施工范围|工程范围|招标范围|范围包括|包括|室内装修|装修工程|专业分包工程)+$", "", text)
    text = text.strip("：:，,。；;、")
    text = re.sub(r"6#楼\s*32\s*(?:层|F)\s*办公(?:区)?", "6#楼32F办公区", text, flags=re.I)
    if text in {"6#楼32F办公", "6#楼32层办公"}:
        text = "6#楼32F办公区"
    if text in {"商业街", "商业街区"}:
        text = "商业街区"
    if text == "层办公区":
        return ""
    if any(
        token in text
        for token in (
            "分段不连续开工",
            "开工日期",
            "合同工期",
            "所产生的各项费用",
            "费用均已包括",
            "投标金额",
            "投标报价",
        )
    ):
        return ""
    if len(text) < 2:
        return ""
    if text in {"本项目", "本工程", "项目", "工程", "施工", "装修", "室内"}:
        return ""
    if len(text) > 28:
        return ""
    return text


def _schedule_zones(schedule: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(schedule, dict):
        return []
    zones = schedule.get("zones")
    if isinstance(zones, list):
        normalized = []
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            raw_name = str(zone.get("name") or zone.get("zone_name") or zone.get("area") or "").strip()
            name = _normalize_work_zone_name(raw_name) or ("本项目" if raw_name in {"本项目", "本工程"} else "")
            duration = zone.get("duration_days") or zone.get("days") or zone.get("contract_days")
            start_date = zone.get("start_date") or zone.get("start")
            item = {"name": name, "duration_days": duration, "start_date": start_date}
            if name or duration or start_date:
                normalized.append(item)
        if normalized:
            return normalized
    return []


def _schedule_fact_sentence(schedule: dict[str, Any] | None) -> str:
    parts = []
    for zone in _schedule_zones(schedule):
        name = _normalize_work_zone_name(zone.get("name")) or "对应施工区域"
        duration_text = _schedule_duration_days_text(zone.get("duration_days"))
        start_date = str(zone.get("start_date") or "").strip()
        detail = name
        if start_date:
            detail += f"开工日期暂定 {start_date}"
        if duration_text:
            detail += f"，合同工期{duration_text}"
        parts.append(detail)
    if parts:
        return "；".join(parts) + "；实际开工时间以发包人发出的开工令为准，工期包括星期六、日及法定节假日"
    return "实际开工时间、总工期及分区节点按照招标文件约定和发包人发出的开工令执行"


def _schedule_contract_duration_phrase(schedule: dict[str, Any] | None) -> str:
    parts = []
    for zone in _schedule_zones(schedule):
        name = _normalize_work_zone_name(zone.get("name")) or "对应施工区域"
        duration_text = _schedule_duration_days_text(zone.get("duration_days"))
        start_date = str(zone.get("start_date") or "").strip()
        if duration_text:
            suffix = f"（暂定{start_date}开工）" if start_date else ""
            parts.append(f"{name}合同工期{duration_text}{suffix}")
    if parts:
        return "，".join(parts) + "，实际开工时间以发包人开工令为准"
    return "招标文件约定总工期及发包人开工令要求"


def _schedule_contract_window_phrase(schedule: dict[str, Any] | None) -> str:
    parts = []
    for zone in _schedule_zones(schedule):
        name = _normalize_work_zone_name(zone.get("name")) or "对应施工区域"
        duration_text = _schedule_duration_days_text(zone.get("duration_days"))
        if duration_text:
            parts.append(f"{name}{duration_text}")
    if parts:
        return "、".join(parts) + "的合同工期内"
    return "招标文件约定的总工期内"


def _schedule_duration_days_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{int(value)}天"
    except (TypeError, ValueError):
        text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith(("天", "日历天")):
        return text
    return f"{text}天"


def _technical_composition_placeholder_payload(placeholders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "code": item.get("code"),
            "label": item.get("label"),
            "detail": _clip(item.get("detail"), 500),
        }
        for item in placeholders[:10]
    ]


def _technical_composition_context_segments(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    component: dict[str, Any],
) -> list[dict[str, Any]]:
    files = _technical_composition_files_for_run(db, project, run)
    probes = _technical_composition_context_probes(component)
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    order = 0
    for file_obj in files:
        segments = loads_json(file_obj.segments_json, []) if file_obj.segments_json else []
        if isinstance(segments, list) and segments:
            for index, segment in enumerate(segments, start=1):
                if not isinstance(segment, dict):
                    continue
                text = re.sub(r"\s+", " ", str(segment.get("text") or segment.get("content") or segment.get("original_text") or "")).strip()
                if len(text) < 8:
                    continue
                order += 1
                candidates.append(
                    (
                        _technical_composition_segment_score(text, probes),
                        order,
                        {
                            "source_file": segment.get("source_file") or file_obj.original_filename,
                            "source_location": segment.get("source_location") or segment.get("location") or f"片段{index}",
                            "text": _clip(text, 1000),
                        },
                    )
                )
        elif file_obj.extracted_text:
            chunks = _technical_composition_text_chunks(file_obj.extracted_text)
            for index, text in enumerate(chunks, start=1):
                order += 1
                candidates.append(
                    (
                        _technical_composition_segment_score(text, probes),
                        order,
                        {
                            "source_file": file_obj.original_filename,
                            "source_location": f"全文片段{index}",
                            "text": _clip(text, 1000),
                        },
                    )
                )
    selected = [item for score, _, item in sorted(candidates, key=lambda row: (-row[0], row[1]))[:10] if score > 0]
    if not selected:
        selected = [item for _, _, item in candidates[:6]]
    return selected


def _technical_composition_files_for_run(db: Session, project: BidProject, run: BidParseRun) -> list[BidProjectFile]:
    input_file_uuids = [str(item) for item in loads_json(run.input_file_ids_json, []) or [] if item]
    query = db.query(BidProjectFile).filter(BidProjectFile.project_id == project.id, BidProjectFile.parser_status == "parsed")
    if input_file_uuids:
        query = query.filter(BidProjectFile.file_uuid.in_(input_file_uuids))
    files = query.order_by(BidProjectFile.id.asc()).all()
    if files:
        return files
    return (
        db.query(BidProjectFile)
        .filter(BidProjectFile.project_id == project.id, BidProjectFile.parser_status == "parsed")
        .order_by(BidProjectFile.id.asc())
        .all()
    )


def _technical_composition_context_probes(component: dict[str, Any]) -> list[str]:
    probes = [
        component.get("source_item_no"),
        component.get("component_title"),
        component.get("classification_reason"),
        component.get("draft_instruction"),
        "工程概况",
        "招标范围",
        "工期",
        "质量",
        "安全",
        "文明施工",
        "施工组织",
        "技术标",
        "装修",
    ]
    for need in component.get("information_needs") or []:
        if not isinstance(need, dict):
            continue
        probes.extend([need.get("need_title"), need.get("query"), need.get("polished_text"), need.get("reason")])
    terms: list[str] = []
    for item in probes:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        terms.append(text)
        terms.extend(_keyword_terms(text)[:8])
    return _unique_text([term for term in terms if len(term) >= 2])[:80]


def _technical_composition_segment_score(text: str, probes: list[str]) -> int:
    score = 0
    compact = re.sub(r"\s+", "", text.lower())
    for probe in probes:
        needle = re.sub(r"\s+", "", str(probe or "").lower())
        if len(needle) < 2:
            continue
        if needle in compact:
            score += 6 if len(needle) >= 5 else 2
    anchors = ("招标范围", "工程概况", "工期", "质量", "安全", "文明施工", "施工组织", "技术标", "装修", "分包工程")
    score += sum(1 for anchor in anchors if anchor in text)
    return score


def _technical_composition_text_chunks(text: str, *, chunk_size: int = 1200) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    return [cleaned[start : start + chunk_size] for start in range(0, min(len(cleaned), chunk_size * 12), chunk_size)]


def _compact_prompt_mapping(value: Any, *, limit: int = 1200) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            text = str(item or "").strip()
            if text:
                result[str(key)[:80]] = _clip(text, 200)
        elif isinstance(item, dict):
            nested = _compact_prompt_mapping(item, limit=400)
            if nested:
                result[str(key)[:80]] = nested
        elif isinstance(item, list):
            compact_items = [_clip(str(part), 120) for part in item[:6] if str(part or "").strip()]
            if compact_items:
                result[str(key)[:80]] = compact_items
        if len(str(result)) >= limit:
            break
    return result


def _ensure_technical_composition_placeholders(content: str, placeholders: list[dict[str, Any]]) -> str:
    cleaned = str(content or "").strip()
    if not placeholders:
        return cleaned + "\n"
    missing_lines = []
    for item in placeholders:
        label = str(item.get("label") or "待确认事项").strip()
        detail = str(item.get("detail") or "").strip()
        if label and label in cleaned:
            continue
        missing_lines.append(f"- 【待确认：{label}】{detail}")
    if not missing_lines:
        return cleaned + "\n"
    return "\n".join([cleaned, "", "## 待人工确认", *missing_lines]).strip() + "\n"


_LLM_EDITORIAL_SUPPLEMENT_HEADING_RE = re.compile(r"^(#{1,6})\s*(?:待人工完善|待补充事项|待补充)\s*$")


def _formalize_llm_editorial_supplement_sections(content: str) -> tuple[str, dict[str, Any]]:
    lines = str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    formalized_blocks = 0
    formalized_items = 0
    index = 0
    while index < len(lines):
        heading_match = _LLM_EDITORIAL_SUPPLEMENT_HEADING_RE.match(lines[index].strip())
        if not heading_match:
            result.append(lines[index])
            index += 1
            continue
        heading_level = len(heading_match.group(1))
        block_end = index + 1
        while block_end < len(lines):
            next_heading = re.match(r"^(#{1,6})\s+\S", lines[block_end].strip())
            if next_heading and len(next_heading.group(1)) <= heading_level:
                break
            block_end += 1
        block_lines = lines[index + 1 : block_end]
        transformed_lines: list[str] = []
        block_item_count = 0
        block_is_safe = True
        for block_line in block_lines:
            stripped = block_line.strip()
            if not stripped:
                transformed_lines.append(block_line)
                continue
            bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
            if not bullet_match:
                block_is_safe = False
                break
            formal_sentence = _formal_llm_editorial_supplement_sentence(bullet_match.group(1))
            if not formal_sentence:
                block_is_safe = False
                break
            transformed_lines.append(f"- {formal_sentence}")
            block_item_count += 1
        if block_is_safe and block_item_count:
            result.append(f"{heading_match.group(1)} 深化实施与资料衔接")
            result.extend(transformed_lines)
            formalized_blocks += 1
            formalized_items += block_item_count
        else:
            result.extend(lines[index:block_end])
        index = block_end
    updated = "\n".join(result).strip() + "\n"
    return updated, {
        "version": "biz4c2_llm_editorial_supplement_formalization_v1",
        "status": "formalized" if formalized_blocks else "not_applicable",
        "formalized_block_count": formalized_blocks,
        "formalized_item_count": formalized_items,
    }


def _formal_llm_editorial_supplement_sentence(value: str) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip("。；; ")
    if "企业资质" in text and ("项目管理团队" in text or "人员" in text) and "业绩" in text:
        return "企业资质、项目管理团队及类似工程业绩资料按投标文件相应资格审查及资信章节统一响应，本施工方案不重复列示。"
    if "现场实际情况" in text and "细化" in text and any(token in text for token in ("基层", "接口", "界面")):
        return "中标进场后结合施工图、现场复核结果及相关专业界面细化基层做法和接口处理，经发包人及监理审批后实施。"
    if "内部工法" in text and "验收标准" in text:
        return "项目实施采用企业成熟工法和内部质量标准；企业标准高于招标文件及现行规范时从严执行，存在差异时以发包人审批意见为准。"
    return None


_PENDING_CONFIRMATION_RE = re.compile(r"【待确认(?:[:：]([^】]+))?】")
_PENDING_CONFIRMATION_SECTIONS = {
    "7.3.6",
    "7.3.8",
    "7.3.9",
    "7.3.10",
    "7.3.11",
    "7.3.12",
    "7.3.13",
    "7.3.14",
    "7.3.15",
    "7.3.16",
    "7.3.17",
    "7.3.18",
    "7.3.19",
    "7.3.20",
}


def _postprocess_technical_composition_pending_confirmations(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    component: dict[str, Any],
    content: str,
    evidence: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement] | None = None,
    *,
    created_by: int,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    section_no = _technical_component_source_no(component)
    original_content = str(content or "")
    markers_before = _pending_confirmation_markers(original_content)
    resolution: dict[str, Any] = {
        "section_no": section_no,
        "marker_count_before": len(markers_before),
        "marker_count_after": len(markers_before),
        "fact_replacement_count": 0,
        "material_requirement_ids": [],
        "status": "not_applicable",
    }
    if not section_no or not markers_before:
        return content, evidence, placeholders, warnings, resolution

    material_rows = material_rows or []
    facts = _technical_project_facts(db, project, run)
    updated_content, fact_evidence, fact_count = _replace_pending_confirmations_with_tender_facts(
        original_content,
        section_no=section_no,
        facts=facts,
    )
    if section_no == "7.3.18":
        brand_content, brand_evidence, brand_count = _technical_brand_table_content_from_materials(
            db,
            component,
            material_rows,
        )
        if brand_content:
            updated_content = brand_content
            fact_evidence.extend(brand_evidence)
            fact_count += brand_count

    marker_specs: list[dict[str, Any]] = []
    if section_no == "7.3.8":
        if _has_usable_personnel_bundle_material(db, material_rows):
            updated_content, formalized_count = _formalize_scheme_pending_markers(
                updated_content,
                section_no=section_no,
            )
            fact_count += formalized_count
        else:
            marker_specs = _personnel_pending_requirement_specs(_pending_confirmation_markers(updated_content))
    elif _pending_confirmation_markers(updated_content):
        preserve_unresolved = section_no not in _PENDING_CONFIRMATION_SECTIONS
        updated_content, formalized_count = _formalize_scheme_pending_markers(
            updated_content,
            section_no=section_no,
            preserve_unresolved=preserve_unresolved,
        )
        fact_count += formalized_count
        remaining_for_requirements = _pending_confirmation_markers(updated_content)
        if remaining_for_requirements:
            marker_specs = _generic_pending_requirement_specs(
                section_no,
                component,
                remaining_for_requirements,
            )



    created_requirement_ids: list[int] = []
    spec_by_key = {spec["key"]: spec for spec in marker_specs}
    for spec in spec_by_key.values():
        row = _upsert_pending_confirmation_requirement(db, project, run, component, spec, evidence, created_by=created_by)
        created_requirement_ids.append(row.id)
    if spec_by_key:
        updated_content = _replace_pending_markers_with_requirement_refs(updated_content, spec_by_key)

    remaining_markers = _pending_confirmation_markers(updated_content)
    if remaining_markers:
        warnings = _unique_warnings(
            warnings
            + [
                {
                    "level": "warn",
                    "code": "technical_composition_pending_confirmation_remaining",
                    "message": f"{section_no} still has {len(remaining_markers)} pending confirmation marker(s).",
                }
            ]
        )
    if created_requirement_ids:
        placeholders = _dedupe_pending_placeholders(
            placeholders
            + [
                {
                    "code": "pending_confirmation_material_requirement",
                    "label": spec["title"],
                    "detail": spec["description"],
                    "material_requirement_id": requirement_id,
                }
                for spec, requirement_id in zip(spec_by_key.values(), created_requirement_ids)
            ]
        )
    evidence = _dedupe_evidence(evidence + fact_evidence)
    resolution.update(
        {
            "status": "resolved" if not remaining_markers else "needs_review",
            "marker_count_after": len(remaining_markers),
            "fact_replacement_count": fact_count,
            "material_requirement_ids": _unique_ints(created_requirement_ids),
            "material_requirement_titles": [spec["title"] for spec in marker_specs],
        }
    )
    return updated_content.strip() + "\n", evidence, placeholders, warnings, resolution


def _technical_component_source_no(component: dict[str, Any]) -> str:
    for value in (component.get("source_item_no"), component.get("component_key"), component.get("component_title")):
        match = re.search(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", str(value or ""))
        if match:
            return match.group(1)
    return ""


def _pending_confirmation_markers(content: str) -> list[dict[str, str]]:
    markers: list[dict[str, str]] = []
    for match in _PENDING_CONFIRMATION_RE.finditer(str(content or "")):
        label = re.sub(r"\s+", " ", match.group(1) or "其他主要管理人员补充").strip()
        markers.append({"marker": match.group(0), "label": label})
    return markers


def _technical_project_facts(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for file_obj in _technical_composition_files_for_run(db, project, run):
        text = file_obj.extracted_text or ""
        if not text:
            continue
        if "scope" not in facts:
            scope = _extract_scope_fact(file_obj, text)
            if scope:
                facts["scope"] = scope
        generic_schedule = _extract_generic_schedule_fact(file_obj, text) if "schedule" not in facts else {}
        schedule_match = re.search(
            r"商业街区开工日期暂定\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?"
            r"6#楼\s*32\s*层办公区开工日期暂定\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?"
            r"合同工期：商业街区工期\s*(\d+)\s*天，6#楼\s*32\s*层办公区工期\s*(\d+)\s*天",
            text,
            re.S,
        )
        if schedule_match and "schedule" not in facts:
            groups = schedule_match.groups()
            commercial_start = _format_chinese_date(groups[0], groups[1], groups[2])
            office_start = _format_chinese_date(groups[3], groups[4], groups[5])
            facts["schedule"] = {
                "commercial_start": commercial_start,
                "office_start": office_start,
                "commercial_days": int(groups[6]),
                "office_days": int(groups[7]),
                "zones": [
                    {"name": "商业街区", "start_date": commercial_start, "duration_days": int(groups[6])},
                    {"name": "6#楼32F办公区", "start_date": office_start, "duration_days": int(groups[7])},
                ],
                "evidence": _fact_evidence(file_obj, text, schedule_match.start(), "第四条 合同工期"),
            }
            facts["schedule"]["sentence"] = _schedule_fact_sentence(facts["schedule"])
        elif generic_schedule and "schedule" not in facts:
            facts["schedule"] = generic_schedule
        compact_text = re.sub(r"\s+", "", text)
        if "quality" not in facts:
            quality = _extract_generic_quality_fact(file_obj, text)
            if quality:
                facts["quality"] = quality
        requirement_facts = _extract_technical_requirement_facts(file_obj, text)
        if requirement_facts:
            technical_requirements = facts.setdefault("technical_requirements", {})
            for key, fact in requirement_facts.items():
                technical_requirements.setdefault(key, fact)
        quality_match = re.search(
            r"本项目质量目标为\s*([^，。；;\n]+).*?合格标准外，还需达到分项工程与奖项要求匹配的质量要求",
            text,
            re.S,
        )
        compact_quality_match = re.search(
            r"本项目质量目标为([^，。；;]+).*?合格标准外，还需达到分项工程与奖项要求匹配的质量要求",
            compact_text,
            re.S,
        )
        if quality_match and "quality" not in facts:
            goal = re.sub(r"\s+", "", quality_match.group(1))
            facts["quality"] = {
                "goal": goal,
                "sentence": (
                    f"本项目质量目标为{goal}，本工程除达到合同图纸及技术规范要求及有关政府部门验收之合格标准外，"
                    "还需达到分项工程与奖项要求匹配的质量要求"
                ),
                "evidence": _fact_evidence(file_obj, text, quality_match.start(), "第五条 工程质量"),
            }
        elif compact_quality_match and "quality" not in facts:
            goal = compact_quality_match.group(1)
            position = max(0, text.find("本项目质量目标为"))
            facts["quality"] = {
                "goal": goal,
                "sentence": (
                    f"本项目质量目标为{goal}，本工程除达到合同图纸及技术规范要求及有关政府部门验收之合格标准外，"
                    "还需达到分项工程与奖项要求匹配的质量要求"
                ),
                "evidence": _fact_evidence(file_obj, text, position, "第五条 工程质量"),
            }
    return facts


def _extract_scope_fact(file_obj: BidProjectFile, text: str) -> dict[str, Any]:
    candidates: list[tuple[int, int, str, str]] = []
    patterns = (
        (r"(?:招标范围|工程范围|施工范围)(?:[:：]|包括|为)\s*([^。\n；;]{8,220})", "招标范围", 0),
        (r"(?:建设内容)(?:[:：]|包括|为)\s*([^。\n；;]{8,220})", "建设内容", 1),
        (r"(?:本工程|本项目)(?:范围|内容)?(?:包括|为)\s*([^。\n；;]{8,220})", "工程范围", 1),
        (r"(?:工程概况)(?:[:：]|包括|为)\s*([^。\n；;]{8,220})", "工程概况", 2),
    )
    for pattern, label, priority in patterns:
        for match in re.finditer(pattern, text, re.S):
            candidates.append((priority, match.start(), match.group(1), label))
    if not candidates:
        return {}
    _priority, position, raw_scope, label = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    scope_text = re.sub(r"\s+", "", raw_scope).strip("：:，,。；;、")
    scope_text = re.split(
        r"(?:质量标准|质量目标|工期|需重点|应重点|重点关注|分段不连续开工|开工日期|实际开工|所产生的各项费用|费用均已包括|投标金额|投标报价)",
        scope_text,
        maxsplit=1,
    )[0].strip("：:，,。；;、")
    work_zones = _extract_work_zones_from_scope_text(scope_text)
    if not scope_text and not work_zones:
        return {}
    return {
        "scope_text": _clip(scope_text, 300),
        "work_zones": work_zones,
        "evidence": _fact_evidence(file_obj, text, position, label),
    }


def _extract_work_zones_from_scope_text(text: str) -> list[str]:
    raw = re.sub(r"\s+", "", str(text or ""))
    if not raw:
        return []
    protected = raw.replace("以及", "及")
    parts = re.split(r"[、,，;/；]|及|和|与", protected)
    names: list[str] = []
    for part in parts:
        cleaned = re.sub(r"(室内装饰装修|室内装修|装饰装修|装修|改造|施工|工程|区域|范围|内容|专业分包|专业工程)$", "", part)
        name = _normalize_work_zone_name(cleaned)
        if not name:
            continue
        if any(
            token in name
            for token in (
                "材料",
                "设备",
                "质量",
                "工期",
                "安全",
                "文明",
                "图纸",
                "规范",
                "给排水",
                "电气",
                "末端",
                "分段不连续开工",
                "所产生的各项费用",
                "投标金额",
                "投标报价",
            )
        ):
            continue
        names.append(name)
    return _unique_text(names)[:8]


def _extract_generic_schedule_fact(file_obj: BidProjectFile, text: str) -> dict[str, Any]:
    zones: list[dict[str, Any]] = []
    zone_duration_pattern = re.compile(
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9#（）()·\-]{2,36}?)(?:工期|合同工期)\s*(?P<days>\d+)\s*(?:日历天|天)",
        re.S,
    )
    for match in zone_duration_pattern.finditer(text):
        name = _normalize_work_zone_name(match.group("name"))
        if not name or name in {"合同", "总", "计划", "本项目", "本工程"}:
            continue
        zones.append({"name": name, "duration_days": int(match.group("days"))})
    zones = _dedupe_schedule_zones(zones)
    total_match = re.search(r"(?:总工期|合同工期|计划工期)\s*(?:为|：|:)?\s*(\d+)\s*(?:日历天|天)", text)
    start_match = re.search(r"(?:计划开工日期|开工日期|暂定开工日期)\s*(?:为|：|:)?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not zones and not total_match:
        return {}
    schedule: dict[str, Any] = {
        "zones": zones,
        "evidence": _fact_evidence(file_obj, text, min([m.start() for m in [total_match, start_match] if m] or [0]), "工期要求"),
    }
    if total_match:
        schedule["total_days"] = int(total_match.group(1))
        if not zones:
            schedule["zones"] = [{"name": "本项目", "duration_days": int(total_match.group(1))}]
    if start_match:
        schedule["start_date"] = _format_chinese_date(start_match.group(1), start_match.group(2), start_match.group(3))
        for zone in schedule.get("zones") or []:
            zone.setdefault("start_date", schedule["start_date"])
    schedule["sentence"] = _schedule_fact_sentence(schedule)
    return schedule


def _dedupe_schedule_zones(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for zone in zones:
        name = _normalize_work_zone_name(zone.get("name"))
        days = zone.get("duration_days")
        key = f"{name}:{days}"
        if not name or key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "duration_days": days, "start_date": zone.get("start_date")})
    return result[:8]


def _extract_generic_quality_fact(file_obj: BidProjectFile, text: str) -> dict[str, Any]:
    patterns = (
        (r"(?:质量目标|质量标准|工程质量)\s*(?:为|：|:)\s*([^，。；;\n]{2,80})", "质量要求"),
        (r"(?:达到|符合)\s*([^，。；;\n]{2,80}?(?:合格|优良|优质|鲁班奖|省优|市优)[^，。；;\n]*)", "质量要求"),
    )
    for pattern, label in patterns:
        match = re.search(pattern, text, re.S)
        if not match:
            continue
        goal = re.sub(r"\s+", "", match.group(1)).strip("：:，,。；;、")
        if not goal:
            continue
        return {
            "goal": goal,
            "sentence": f"本项目质量要求为{goal}",
            "evidence": _fact_evidence(file_obj, text, match.start(), label),
        }
    return {}


def _extract_technical_requirement_facts(file_obj: BidProjectFile, text: str) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for definition in TECHNICAL_REQUIREMENT_FACT_DEFINITIONS:
        fact = _extract_generic_technical_requirement_fact(
            file_obj,
            text,
            key=str(definition["key"]),
            label=str(definition["label"]),
            keywords=tuple(str(item) for item in definition["keywords"]),
        )
        if fact:
            facts[str(definition["key"])] = fact
    return facts


def _extract_generic_technical_requirement_fact(
    file_obj: BidProjectFile,
    text: str,
    *,
    key: str,
    label: str,
    keywords: tuple[str, ...],
) -> dict[str, Any]:
    candidates: list[tuple[int, int, int, str, list[str]]] = []
    for chunk in _technical_requirement_candidate_chunks(text):
        matched = [keyword for keyword in keywords if keyword and keyword in chunk]
        if not matched:
            continue
        keyword_positions = [text.find(keyword) for keyword in matched if text.find(keyword) >= 0]
        position = min(keyword_positions) if keyword_positions else max(0, text.find(chunk[:20]))
        score = len(matched) * 100 - min(len(chunk), 260)
        candidates.append((score, -position, len(chunk), chunk, matched))
    if not candidates:
        return {}

    _score, negative_position, _length, summary, matched_keywords = sorted(candidates, reverse=True)[0]
    position = abs(negative_position)
    clean_summary = _clip(summary.strip("：:，,。；;、"), 260)
    return {
        "key": key,
        "label": label,
        "summary": clean_summary,
        "keywords": _unique_text(matched_keywords)[:10],
        "evidence": _fact_evidence(file_obj, text, position, label),
    }


def _technical_requirement_candidate_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    normalized = re.sub(r"\r\n?", "\n", str(text or ""))
    for raw in re.split(r"[。\n；;]+", normalized):
        chunk = re.sub(r"\s+", " ", raw).strip("：:，,。；;、 ")
        if not chunk:
            continue
        if len(chunk) <= 260:
            chunks.append(chunk)
            continue
        for sub_chunk in re.split(r"[，,]+", chunk):
            sub_chunk = sub_chunk.strip("：:，,。；;、 ")
            if 8 <= len(sub_chunk) <= 260:
                chunks.append(sub_chunk)
    return _unique_text(chunks)[:120]


def _format_chinese_date(year: str, month: str, day: str) -> str:
    return f"{int(year)}年{int(month)}月{int(day)}日"


def _fact_evidence(file_obj: BidProjectFile, text: str, position: int, section_label: str) -> dict[str, Any]:
    page = "-"
    page_matches = list(re.finditer(r"\[第(\d+)页\]", text[:position]))
    if page_matches:
        page = f"第{page_matches[-1].group(1)}页"
    snippet = re.sub(r"\s+", " ", text[max(0, position - 120) : position + 520]).strip()
    return {
        "source_kind": "tender_document_fact",
        "source_file": file_obj.original_filename,
        "source_location": f"{page} {section_label}".strip(),
        "original_text": _clip(snippet, 520),
    }


def _replace_pending_confirmations_with_tender_facts(
    content: str,
    *,
    section_no: str,
    facts: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    updated = str(content or "")
    evidence: list[dict[str, Any]] = []
    replacement_count = 0
    schedule = facts.get("schedule") or {}
    quality = facts.get("quality") or {}
    if section_no in {"7.3.6", "7.3.9"} and schedule:
        before = updated
        updated = re.sub(r"工期天数【待确认[:：][^】]+】", _schedule_fact_sentence(schedule), updated)
        updated = re.sub(
            r"【待确认[:：]总工期为XX日历天[^】]*】",
            _schedule_contract_duration_phrase(schedule),
            updated,
        )
        updated = re.sub(
            r"工期【待确认[:：]天数】内",
            _schedule_contract_window_phrase(schedule),
            updated,
        )
        updated = _replace_schedule_plan_markers(updated, schedule=schedule)
        if updated != before:
            replacement_count += before.count("【待确认") - updated.count("【待确认")
            evidence.append(schedule["evidence"])
    if section_no in {"7.3.6", "7.3.16"} and quality:
        before = updated
        updated = re.sub(r"达到【待确认[:：]合同约定的质量标准[^】]*】", "达到合同图纸及技术规范要求及有关政府部门验收之合格标准", updated)
        updated = re.sub(r"争创【待确认[:：]具体奖项名称[^】]*】", f"确保达到{quality['goal']}对应的分项工程质量要求", updated)
        updated = re.sub(r"（注：文中【待确认[:：][^】]+】部分需根据实际招标文件及企业情况补充明确信息。）", "", updated)
        if updated != before:
            replacement_count += before.count("【待确认") - updated.count("【待确认")
            evidence.append(quality["evidence"])
    return updated, evidence, max(0, replacement_count)


def _replace_schedule_plan_markers(content: str, *, schedule: dict[str, Any] | None = None) -> str:
    updated = str(content or "")
    zone_phrase = "各施工区域" if _schedule_zones(schedule) else "各施工段"
    for value in (
        "按总控计划前置完成",
        f"按{zone_phrase}流水组织",
        "按基层及隐蔽工程验收节点推进",
        "按样板确认及面层材料到场情况组织",
        "按设备报验及安装工作面移交条件组织",
    ):
        updated = updated.replace("计划用时【待确认：X日历天】", f"计划用时{value}", 1)
    replacements = [
        ("开工后第【待确认】天", "开工令发出并具备进场条件后"),
        ("开工后第【待确认】天", "施工准备及样板确认完成后"),
        ("基层施工阶段【待确认】天", "基层施工阶段按总控计划组织"),
        ("面层施工前【待确认】天", "面层施工前完成材料报审及样板确认"),
        ("安装阶段前【待确认】天", "安装阶段前完成设备报验及工作面移交"),
        ("面层后期【待确认】天", "面层后期按验收节点组织"),
        ("安装阶段【待确认】天", "安装阶段按专业穿插计划组织"),
        ("安装末期【待确认】天", "安装末期按整改复验及移交计划组织"),
    ]
    for old, new in replacements:
        updated = updated.replace(old, new, 1)
    return updated


def _technical_brand_table_content_from_materials(
    db: Session,
    component: dict[str, Any],
    material_rows: list[BidMaterialRequirement],
) -> tuple[str, list[dict[str, Any]], int]:
    profile_items: list[EnterpriseProfileItem] = []
    for row in material_rows:
        if not _material_row_looks_like_brand_table(row):
            continue
        for item_uuid in _material_requirement_profile_item_uuids(row):
            item = _enterprise_profile_item_for_material(db, item_uuid)
            if item and item not in profile_items:
                profile_items.append(item)
    if not profile_items:
        return "", [], 0

    item = profile_items[0]
    structured = loads_json(item.structured_json, {}) if item.structured_json else {}
    raw_table = ""
    if isinstance(structured, dict):
        raw_table = str(structured.get("raw_table_text") or "").strip()
    body_text = raw_table or str(item.content_text or item.summary or "").strip()
    body_text = _clean_brand_table_text(body_text)
    if not body_text:
        return "", [], 0
    title = _technical_component_title(component, 0)
    content = "\n".join(
        [
            f"# {title}",
            "",
            "## 编制依据",
            f"- 企业资料库：{item.title}",
            "- 招标文件要求投标单位提供拟采用的材料品牌表。",
            "",
            "## 投标单位拟采用的材料品牌表",
            body_text,
            "",
            "## 投标响应说明",
            "我方拟采用的主要材料品牌以本表为准，材料进场前按发包人、监理及总承包单位要求完成样板、品牌、规格、检测报告及合格证等报审手续，经审批后用于本工程。",
        ]
    )
    evidence = [
        {
            "source_kind": "enterprise_profile_material",
            "source_file": item.title,
            "source_location": item.profile_key or item.subcategory or item.category,
            "original_text": _clip(body_text, 520),
        }
    ]
    return content, evidence, 1


def _material_row_looks_like_brand_table(row: BidMaterialRequirement) -> bool:
    text = " ".join(
        str(part or "")
        for part in [
            row.title,
            row.item_title,
            row.description,
            row.profile_category,
            row.material_key,
            row.format_item_key,
        ]
    )
    normalized = re.sub(r"\s+", "", text)
    return "品牌" in normalized or "brand" in normalized.lower()


def _clean_brand_table_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^# .+?\n+", "", text.strip())
    text = re.sub(r"(?m)^来源[:：].*$", "", text)
    text = re.sub(r"(?m)^用途[:：].*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _formalize_scheme_pending_markers(
    content: str,
    *,
    section_no: str,
    preserve_unresolved: bool = False,
) -> tuple[str, int]:
    replacement_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacement_count
        label = re.sub(r"\s+", " ", match.group(1) or "").strip()
        replacement = _formal_pending_marker_text(label, section_no=section_no)
        if preserve_unresolved and replacement.strip() == label:
            return match.group(0)
        replacement_count += 1
        return replacement

    updated = _PENDING_CONFIRMATION_RE.sub(replace, str(content or ""))
    return updated, replacement_count


def _formal_pending_marker_text(label: str, *, section_no: str) -> str:
    text = str(label or "").strip()
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return "结合施工图预算、现场条件、深化设计及发包人审批意见确定"
    if any(token in compact for token in ("项目经理", "技术负责人", "安全负责人", "安全员", "人员姓名", "人员资质", "证书编号", "职称", "学历", "工作年限", "施工经验", "项目经历", "类似项目业绩")):
        return "详见本技术标“拟派驻主要管理人员简历和资格证书”章节"
    if "施工图预算" in compact:
        return "依据施工图预算、深化设计和经审批的材料采购计划确定"
    if any(token in compact for token in ("现场条件", "现场深化", "深化设计", "出图", "总平面图")):
        return "结合现场条件深化完善，并按总包、监理及发包人审批意见实施"
    if any(token in compact for token in ("甲方提供", "电源接入点", "变压器容量", "剩余容量")):
        return "以甲方或总包确认的临时用电接入点及现场复核容量为准"
    if "总承包指定区域" in compact or "自行规划" in compact:
        return "按总承包指定区域及经审批的现场平面布置执行"
    if "业主指定样式" in compact:
        return "按业主或发包人确认样式执行"
    if any(token in compact for token in ("清运单位", "资质")) and section_no == "7.3.12":
        return "选用具备相应资质的合规清运单位，进场后按发包人要求报审"
    if "联系方式" in compact:
        return "由项目部指定专人负责，并在进场后向总包、监理及发包人报备联系方式"
    if "预算额度" in compact:
        return "纳入项目安全文明施工及垃圾清运专项费用统筹控制"
    if "合格供应商" in compact:
        return "优先选用经业主或发包人审批的合格供应商"
    if "甲指" in compact or "甲供" in compact:
        return "按发包人指定品牌、甲供材料计划及现场交接要求执行"
    if "平台名称" in compact:
        return "采用项目管理协同平台、BIM协同及现场例会机制进行统筹管控"
    if len(text) >= 18 and any(token in text for token in ("我司", "将在", "已成功", "提供专项")):
        return text
    return text


def _has_usable_personnel_bundle_material(
    db: Session,
    material_rows: list[BidMaterialRequirement],
) -> bool:
    for row in material_rows:
        if row.status not in {"submitted", "approved", "applied"}:
            continue
        row_text = " ".join(str(part or "") for part in [row.title, row.item_title, row.description])
        if not _looks_like_personnel_bundle_text(row_text):
            continue
        for item_uuid in _material_requirement_profile_item_uuids(row):
            item = _enterprise_profile_item_for_material(db, item_uuid)
            if not item:
                continue
            item_text = " ".join(str(part or "") for part in [item.title, item.summary, item.content_text])
            if _looks_like_personnel_bundle_text(item_text):
                return True
    return False


def _looks_like_personnel_bundle_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    personnel_hits = sum(
        1
        for token in ("项目经理", "主要管理人员", "技术负责人", "安全负责人", "人员信息", "人员简历", "岗位及资格证书")
        if token in compact
    )
    return personnel_hits >= 1 and any(token in compact for token in ("人员", "项目经理", "负责人", "证书", "简历"))


def _personnel_pending_requirement_specs(markers: list[dict[str, str]]) -> list[dict[str, Any]]:
    specs_by_key: dict[str, dict[str, Any]] = {}
    for marker in markers:
        label = marker.get("label") or ""
        if "技术负责人" in label:
            key = "technical_lead_resume_certificates"
            title = "技术负责人完整简历及资格证书"
            description = "需补充技术负责人姓名、职称、学历、注册/职称证书编号、施工年限、类似项目技术负责人经历及对应证书附件。"
        elif "安全负责人" in label or "安全管理" in label or "C证" in label or "C3" in label:
            key = "safety_lead_resume_certificate"
            title = "安全负责人完整简历及安全生产考核证书"
            description = "需补充安全负责人姓名、职称、学历、安全生产考核合格证书编号、从事安全管理年限和类似装修工程安全管理经历。"
        elif "其他" in label or "主要管理人员" in label or "管理人员" in label:
            key = "other_management_staff_roster_certificates"
            title = "其他主要管理人员名单、岗位及证书"
            description = "需补充施工员、材料员、资料员、质量员等主要管理人员的姓名、岗位、证书编号、职责分工及证书附件。"
        else:
            key = "project_manager_full_resume"
            title = "项目经理完整简历信息"
            description = "需补充项目经理职称、学历及专业、参加工作时间、从事项目经理年限、类似项目经历、项目规模、开竣工日期、担任职务和质量情况。"
        spec = specs_by_key.setdefault(
            key,
            {
                "key": key,
                "title": title,
                "description": description,
                "profile_category": "personnel",
                "fulfillment_mode": "enterprise_profile",
                "markers": [],
            },
        )
        spec["markers"].append(marker)
    return list(specs_by_key.values())


def _generic_pending_requirement_specs(
    section_no: str,
    component: dict[str, Any],
    markers: list[dict[str, str]],
) -> list[dict[str, Any]]:
    title = f"{section_no} 资料补充"
    labels = "；".join(_unique_text([item.get("label") for item in markers if item.get("label")])[:8])
    return [
        {
            "key": f"{re.sub(r'[^0-9a-zA-Z]+', '_', section_no).strip('_')}_pending_confirmation",
            "title": title,
            "description": f"{component.get('component_title') or section_no} 存在未落地信息，需补充：{labels}",
            "profile_category": "other",
            "fulfillment_mode": "manual_upload",
            "markers": markers,
        }
    ]


def _upsert_pending_confirmation_requirement(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    component: dict[str, Any],
    spec: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    created_by: int,
) -> BidMaterialRequirement:
    section_key = _technical_composition_section_key(component, 0)
    material_key = f"technical_composition:pending:{_safe_ascii_key(section_key)}:{_safe_ascii_key(spec['key'])}"[:128]
    row = (
        db.query(BidMaterialRequirement)
        .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.material_key == material_key)
        .first()
    )
    if not row:
        row = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            format_plan_id=None,
            format_item_key=str(component.get("component_key") or section_key)[:255],
            package_key="technical",
            package_title="技术标",
            section_key=section_key,
            item_title=str(component.get("component_title") or spec["title"])[:255],
            requirement_type="profile" if spec.get("fulfillment_mode") == "enterprise_profile" else "manual",
            profile_category=spec.get("profile_category"),
            material_key=material_key,
            title=spec["title"],
            fulfillment_mode=spec.get("fulfillment_mode") or "manual_upload",
            status="missing",
            priority="high",
            owner_role="经营",
            created_by=created_by,
            updated_by=created_by,
        )
        db.add(row)
        db.flush()
    row.format_item_key = str(component.get("component_key") or section_key)[:255]
    row.package_key = "technical"
    row.package_title = "技术标"
    row.section_key = section_key
    row.item_title = str(component.get("component_title") or spec["title"])[:255]
    row.requirement_type = "profile" if spec.get("fulfillment_mode") == "enterprise_profile" else "manual"
    row.profile_category = spec.get("profile_category")
    row.title = spec["title"]
    row.description = spec["description"]
    row.fulfillment_mode = spec.get("fulfillment_mode") or "manual_upload"
    row.priority = "high"
    row.owner_role = "经营"
    row.source_file = (evidence[0] or {}).get("source_file") if evidence else None
    row.source_location = (evidence[0] or {}).get("source_location") if evidence else section_key
    row.source_text = (evidence[0] or {}).get("original_text") if evidence else None
    if not (row.submitted_profile_item_uuid or row.submitted_file_id or row.submitted_value or row.status in {"submitted", "approved", "applied"}):
        row.status = "missing"
        row.candidate_profile_item_uuid = None
        row.notes = "由技术标草案正文中的未落地信息自动转为明确资料需求，请补齐后重新生成对应章节。"
    row.normalized_json = dumps_json(
        {
            "version": "biz4c2_pending_confirmation_requirement_v1",
            "extractor": "technical_draft_pending_confirmation_postprocess",
            "component": {
                "component_key": component.get("component_key"),
                "component_title": component.get("component_title"),
                "source_item_no": component.get("source_item_no"),
            },
            "pending_markers": spec.get("markers") or [],
            "material_identity": material_key,
        }
    )
    row.evidence_json = dumps_json(evidence[:8])
    row.updated_by = created_by
    return row


def _replace_pending_markers_with_requirement_refs(content: str, specs_by_key: dict[str, dict[str, Any]]) -> str:
    title_by_marker: dict[str, str] = {}
    for spec in specs_by_key.values():
        for marker in spec.get("markers") or []:
            title_by_marker[marker.get("marker") or ""] = spec["title"]

    def replace(match: re.Match[str]) -> str:
        marker = match.group(0)
        title = title_by_marker.get(marker) or "资料需求清单"
        return f"见资料需求清单《{title}》"

    return _PENDING_CONFIRMATION_RE.sub(replace, content)


def _dedupe_pending_placeholders(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = f"{item.get('code')}:{item.get('label')}:{item.get('material_requirement_id')}"
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _unique_ints(values: list[Any]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        result.append(number)
    return result


def _safe_ascii_key(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9:_-]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw or uuid.uuid4().hex[:8]


def _technical_composition_llm_content_shape_ok(content: str, rule_content: str) -> bool:
    text = str(content or "").strip()
    if len(text) < 260:
        return False
    paragraph_count = _technical_composition_paragraph_count(text)
    if paragraph_count < 4:
        return False
    generic_sentence = "严格响应招标文件关于"
    if generic_sentence in text and len(text) < max(360, len(str(rule_content or "")) + 80):
        return False
    return True


def _technical_composition_paragraph_count(content: str) -> int:
    count = 0
    for raw_line in str(content or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line.startswith("#") or line.startswith("|"):
            continue
        if line in {"---", "-"}:
            continue
        if len(line) >= 18:
            count += 1
    return count


def _apply_technical_composition_quality_self_review(
    content: str,
    warnings: list[dict[str, Any]],
    generation_decision: dict[str, Any],
    component: dict[str, Any],
    project_context: dict[str, Any] | None,
    placeholders: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    review = _technical_composition_quality_self_review(content, component, project_context, placeholders)
    auto_enhancement: dict[str, Any] = {
        "version": "biz4c2_p3_rule_auto_enhancement_v1",
        "status": "not_needed",
        "applied": False,
        "reason": "章节自评已达到保守规则补强阈值。",
        "added_fact_keys": [],
        "added_blocks": [],
    }
    enhanced_content = content

    if review.get("auto_enhance_recommended") and not placeholders:
        enhanced_content, added_blocks, added_fact_keys = _auto_enhance_technical_composition_content(
            content,
            component,
            project_context,
            review,
        )
        if enhanced_content != content:
            post_review = _technical_composition_quality_self_review(enhanced_content, component, project_context, placeholders)
            auto_enhancement = {
                "version": "biz4c2_p3_rule_auto_enhancement_v1",
                "status": "applied",
                "applied": True,
                "reason": "章节生成后自评发现深度或项目事实落地不足，已追加保守规则补强段落。",
                "added_fact_keys": added_fact_keys,
                "added_blocks": added_blocks,
                "before_score": review.get("score"),
                "after_score": post_review.get("score"),
            }
            review = post_review
            warnings = _unique_warnings(
                warnings
                + [
                    {
                        "level": "info",
                        "code": "technical_composition_auto_enhanced",
                        "message": "章节生成后自评发现深度或项目事实落地不足，已自动追加保守补强段落。",
                        "detail": {"added_fact_keys": added_fact_keys, "added_blocks": added_blocks},
                    }
                ]
            )

    generation_decision = dict(generation_decision)
    quality_profile = dict(generation_decision.get("quality_profile") or {})
    quality_profile.update(
        {
            "composition_quality_status": review.get("status"),
            "composition_quality_score": review.get("score"),
            "composition_quality_label": review.get("status_label"),
        }
    )
    generation_decision["quality_profile"] = quality_profile
    generation_decision["composition_quality_review"] = review
    generation_decision["auto_enhancement"] = auto_enhancement
    return enhanced_content, warnings, generation_decision


def _technical_composition_quality_self_review(
    content: str,
    component: dict[str, Any],
    project_context: dict[str, Any] | None,
    placeholders: list[dict[str, Any]],
) -> dict[str, Any]:
    text = _technical_composition_plain_text(content)
    normalized_text = _normalize_text_for_fact_match(text)
    intent = _technical_composition_intent(component)
    paragraph_count = _technical_composition_paragraph_count(content)
    visible_length = len(re.sub(r"\s+", "", text))
    expected_fact_keys = list(TECHNICAL_COMPOSITION_REVIEW_FACT_KEYS_BY_INTENT.get(intent, ()))
    available_fact_keys = [
        key for key in expected_fact_keys if _technical_project_requirement_fact(project_context, key)
    ]
    used_fact_keys = [
        key for key in available_fact_keys if _technical_composition_fact_reflected(normalized_text, _technical_project_requirement_fact(project_context, key))
    ]
    missing_fact_keys = [key for key in available_fact_keys if key not in used_fact_keys]
    coverage_ratio = (len(used_fact_keys) / len(available_fact_keys)) if available_fact_keys else None
    process_terms = ("责任", "计划", "报审", "交底", "检查", "验收", "整改", "复查", "闭环", "台账")
    process_term_count = sum(1 for term in process_terms if term in text)

    issues: list[dict[str, Any]] = []
    score = 100
    if placeholders:
        score -= 25
        issues.append({"code": "manual_placeholder_remaining", "message": "章节仍存在待人工补充内容。"})
    if paragraph_count < 5:
        score -= 18
        issues.append({"code": "paragraph_depth_weak", "message": "正文段落数量不足。", "evidence": {"paragraph_count": paragraph_count}})
    if visible_length < 220:
        score -= 16
        issues.append({"code": "visible_length_weak", "message": "正文有效字数偏少。", "evidence": {"visible_length": visible_length}})
    if available_fact_keys and missing_fact_keys:
        penalty = min(24, 8 * len(missing_fact_keys))
        score -= penalty
        issues.append(
            {
                "code": "project_fact_coverage_weak",
                "message": "已抽取的项目专项要求未充分进入正文。",
                "evidence": {"missing_fact_keys": missing_fact_keys, "available_fact_keys": available_fact_keys},
            }
        )
    if process_term_count < 5 and intent in TECHNICAL_COMPOSITION_REVIEW_FACT_KEYS_BY_INTENT:
        score -= 10
        issues.append(
            {
                "code": "implementation_loop_weak",
                "message": "措施中的责任、检查、验收、整改闭环表达不足。",
                "evidence": {"process_term_count": process_term_count},
            }
        )

    score = max(0, min(100, score))
    auto_enhance_recommended = (
        not placeholders
        and intent in TECHNICAL_COMPOSITION_REVIEW_FACT_KEYS_BY_INTENT
        and (bool(missing_fact_keys) or paragraph_count < 5 or visible_length < 220 or process_term_count < 5)
    )
    if placeholders:
        status = "needs_input"
        status_label = "需人工补充"
    elif score >= 85 and not missing_fact_keys:
        status = "pass"
        status_label = "已项目化"
    elif auto_enhance_recommended:
        status = "needs_enhancement"
        status_label = "需补强"
    else:
        status = "warning"
        status_label = "需复核"

    return {
        "version": "biz4c2_p3_composition_quality_review_v1",
        "status": status,
        "status_label": status_label,
        "score": score,
        "intent": intent,
        "paragraph_count": paragraph_count,
        "visible_length": visible_length,
        "process_term_count": process_term_count,
        "expected_fact_keys": expected_fact_keys,
        "available_fact_keys": available_fact_keys,
        "used_fact_keys": used_fact_keys,
        "missing_fact_keys": missing_fact_keys,
        "fact_coverage_ratio": coverage_ratio,
        "auto_enhance_recommended": auto_enhance_recommended,
        "issues": issues,
    }


def _auto_enhance_technical_composition_content(
    content: str,
    component: dict[str, Any],
    project_context: dict[str, Any] | None,
    review: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    if "## 项目要求落地补充" in content or "## 执行检查与闭环补充" in content:
        return content, [], []

    added_lines: list[str] = []
    added_blocks: list[str] = []
    added_fact_keys: list[str] = []
    missing_fact_keys = tuple(str(item) for item in review.get("missing_fact_keys") or [] if str(item or "").strip())
    component_title = str(component.get("component_title") or "\u672c\u5206\u9879\u5de5\u7a0b").strip()
    fact_sentences = _technical_template_requirement_fact_sentences(project_context, missing_fact_keys, subject=component_title)
    if fact_sentences:
        added_lines.extend(["", "## 项目要求落地补充"])
        added_lines.extend(fact_sentences)
        added_lines.append(
            "上述项目专项要求在进场交底、样板确认、材料报审、现场巡检、问题整改和资料归档中同步落地；项目部按责任人、完成时限、复查结果和影像资料形成台账，避免专项要求停留在原则性表述。"
        )
        added_lines[-1] = f"{component_title}\u76f8\u5173\u4e13\u9879\u8981\u6c42\u5728\u8fdb\u573a\u4ea4\u5e95\u3001\u6837\u677f\u786e\u8ba4\u3001\u6750\u6599\u62a5\u5ba1\u3001\u73b0\u573a\u5de1\u68c0\u3001\u95ee\u9898\u6574\u6539\u548c\u8d44\u6599\u5f52\u6863\u4e2d\u540c\u6b65\u843d\u5730\uff1b{component_title}\u7531\u9879\u76ee\u90e8\u6309\u8d23\u4efb\u4eba\u3001\u5b8c\u6210\u65f6\u9650\u3001\u590d\u67e5\u7ed3\u679c\u548c\u5f71\u50cf\u8d44\u6599\u5f62\u6210\u53f0\u8d26\uff0c\u907f\u514d\u505c\u7559\u5728\u539f\u5219\u6027\u8868\u8ff0\u3002"
        added_blocks.append("项目要求落地补充")
        added_fact_keys.extend(missing_fact_keys)

    paragraph_count = int(review.get("paragraph_count") or 0)
    visible_length = int(review.get("visible_length") or 0)
    process_term_count = int(review.get("process_term_count") or 0)
    if paragraph_count < 5 or visible_length < 220 or process_term_count < 5:
        title = str(component.get("component_title") or _technical_component_title(component, 0) or "本章节").strip()
        work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
        added_lines.extend(
            [
                "",
                "## 执行检查与闭环补充",
                f"围绕“{title}”，项目部将结合{work_zone_phrase}建立计划、交底、实施、检查、整改、复验和归档闭环。每项措施明确责任岗位、实施时点、检查频次和验收标准，发现偏差时形成问题清单、整改责任、完成期限和复查记录。",
                "涉及发包人、监理、总承包单位或其他专业配合事项时，及时通过会议纪要、工作面移交记录、报审资料和现场确认单固化责任界面，确保本章节措施能够在施工全过程被执行、被检查、被追溯。",
                "资料管理方面同步归集交底记录、巡检记录、验收记录、整改复查记录和影像资料，并在周例会中复盘未完成事项，保证章节承诺与现场实施进度、质量安全检查和竣工移交资料保持一致。",
            ]
        )
        added_blocks.append("执行检查与闭环补充")

    if not added_lines:
        return content, [], []
    return f"{content.rstrip()}\n" + "\n".join(added_lines).rstrip(), _unique_text(added_blocks), _unique_text(added_fact_keys)


def _technical_composition_plain_text(content: str) -> str:
    text = re.sub(r"```.*?```", " ", str(content or ""), flags=re.S)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`>|-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_text_for_fact_match(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("６", "6").replace("＃", "#")


def _technical_composition_fact_reflected(normalized_text: str, fact: dict[str, Any]) -> bool:
    keywords = [str(item).strip() for item in fact.get("keywords") or [] if str(item).strip()]
    if any(_normalize_text_for_fact_match(keyword) in normalized_text for keyword in keywords):
        return True
    summary = str(fact.get("summary") or "").strip()
    if not summary:
        return False
    normalized_summary = _normalize_text_for_fact_match(summary)
    if len(normalized_summary) >= 12 and normalized_summary[:24] in normalized_text:
        return True
    fragments = [
        _normalize_text_for_fact_match(item)
        for item in re.split(r"[，,。；;、\s]+", summary)
        if len(_normalize_text_for_fact_match(item)) >= 4
    ]
    return any(fragment in normalized_text for fragment in fragments[:6])


def _technical_component_source_evidence(component: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_kind": "technical_composition_source",
            "source_file": item.get("source_file") or "招标文件",
            "source_location": item.get("source_location") or component.get("source_item_no") or "-",
            "original_text": _clip(item.get("original_text") or item.get("text"), 480),
            "component_key": component.get("component_key"),
        }
        for item in component.get("source_evidence") or []
        if isinstance(item, dict) and (item.get("original_text") or item.get("text"))
    ]


def _technical_need_source_evidence(needs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = []
    for need in needs:
        if need.get("source_type") == "tender_document":
            text = need.get("polished_text") or need.get("query") or need.get("reason")
            if text:
                evidence.append(
                    {
                        "source_kind": "technical_composition_need",
                        "source_file": "技术标组成识别",
                        "source_location": need.get("need_title") or need.get("need_key") or "-",
                        "original_text": _clip(text, 480),
                        "need_key": need.get("need_key"),
                    }
                )
        for item in need.get("source_evidence") or []:
            if isinstance(item, dict) and (item.get("original_text") or item.get("text")):
                evidence.append(
                    {
                        "source_kind": "technical_composition_need_source",
                        "source_file": item.get("source_file") or "招标文件",
                        "source_location": item.get("source_location") or need.get("need_title") or "-",
                        "original_text": _clip(item.get("original_text") or item.get("text"), 480),
                        "need_key": need.get("need_key"),
                    }
                )
    return evidence


def _technical_profile_material_lines(db: Session, material_rows: list[BidMaterialRequirement]) -> list[str]:
    lines = []
    for row in material_rows:
        profile_titles = []
        for item_uuid in _material_requirement_profile_item_uuids(row):
            item = _enterprise_profile_item_for_material(db, item_uuid)
            if not item:
                continue
            profile_titles.append(item.title)
            text = _clip(_enterprise_profile_evidence_text(item, row), 320)
            if text:
                lines.append(f"- {row.title}：采用企业资料《{item.title}》。{text}")
        if not profile_titles and row.submitted_value:
            lines.append(f"- {row.title}：{_clip(row.submitted_value, 320)}")
    return lines or ["- 暂未绑定可写入正文的企业资料，需人工复核。"]


def _technical_response_draft_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
) -> list[str]:
    lines: list[str] = []
    tender_texts = [
        _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 600)
        for need in needs
        if need.get("source_type") == "tender_document"
    ]
    tender_texts = [item for item in tender_texts if item]
    if material_rows:
        material_titles = "、".join(row.title for row in material_rows[:6])
        lines.append(f"我方已按招标文件要求准备并引用相关企业资料，包括{material_titles}。上述资料将作为本技术标相应章节及附件的填写依据。")
    for text in tender_texts:
        lines.append(text)
    if not lines:
        title = component.get("component_title") or "本章节"
        lines.append(f"我方将严格响应招标文件关于“{title}”的要求，结合项目实际组织实施，并在正式投标文件中补充完善具体措施、人员安排及实施计划。")
    return [f"- {line}" for line in lines]


def _technical_schedule_plan_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_texts = [
        _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 260)
        for need in needs
        if need.get("source_type") in {"tender_document", "manual_input"} and (need.get("polished_text") or need.get("query") or need.get("reason"))
    ]
    requirement_summary = "；".join(_unique_text(requirement_texts)[:3]) or "围绕总工期、主要材料与设备进场时间、阶段施工节点和进度纠偏要求组织实施"
    material_titles = "、".join(str(row.title or row.item_title or "").strip() for row in material_rows[:4] if str(row.title or row.item_title or "").strip())
    material_sentence = (
        f"已绑定的进度、材料或设备相关资料包括{material_titles}，本章将其作为材料设备进场和资源组织安排的支撑材料。"
        if material_titles
        else "本章以招标文件工期要求、施工图纸深化进度、材料设备报审周期、现场工作面移交条件和装饰装修施工逻辑为编制基础。"
    )
    title = str(component.get("component_title") or "施工总进度计划").strip()
    schedule_zone_phrase = _technical_project_context_phrase(project_context, "schedule_zone_phrase", "各施工段及相关专业工作面")
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("coordination", "material_sample", "material_procurement"),
        subject=title,
    )
    return [
        "",
        "## 工期响应与编制原则",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方严格响应招标文件约定的总工期、分区工期及开工令要求，不擅自压缩或变更工期承诺；进度计划按{schedule_zone_phrase}分别组织，形成总控计划、阶段计划、周计划和日协调相结合的进度管理体系。",
        material_sentence,
        *fact_sentences,
        "",
        "## 总体进度安排",
        "总体进度按施工准备、深化复核与样板确认、基层及隐蔽工程、面层及安装工程、整改复验与竣工移交五个阶段展开。各区域根据现场移交条件采取分区流水、专业穿插和关键节点控制方式推进，优先保障图纸会审、材料报审、样板确认、隐蔽验收、机电末端配合和成品保护等影响总工期的前置条件。",
        "",
        "## 阶段施工计划",
        "施工准备阶段完成进场手续、临时设施、施工交底、测量复核、材料计划和劳动力组织；深化及样板阶段完成关键节点复核、样板报审和做法确认；基层及隐蔽阶段重点控制基层处理、龙骨安装、管线配合和隐蔽验收；面层及安装阶段组织饰面、门窗、五金、机电末端和收口施工；竣工阶段集中完成清洁、修补、调试、资料整理和移交验收。",
        "",
        "## 主要材料进场计划",
        "主要材料进场实行计划申报、样板确认、品牌规格复核、进场验收、分类堆放和领用记录制度。基层材料、龙骨及辅材在基层施工前完成报审和到场复验；饰面材料、门窗五金、涂料、石材或板材等在对应面层施工前完成样板确认和分批进场；易损、定制或影响观感的材料按施工段倒排采购、加工、运输和保护时间，避免因材料滞后影响工作面连续施工。",
        "",
        "## 主要设备及机械进场计划",
        "主要设备及机具按施工阶段分批进场，测量仪器、临时配电箱、消防器材和基础工具随施工准备同步配置；切割、打磨、钻孔、搬运、移动操作平台、脚手架或吊篮等机具根据施工区域和工序需要提前检查、报验和布置；清洁、成品保护和调试所需设备在后期收口阶段及时配置，确保施工、验收和移交流程衔接顺畅。",
        "",
        "## 劳动力与交叉作业协调",
        "劳动力投入随施工段和工序转换动态调整，优先保证测量放线、基层隐蔽、面层安装、机电末端配合、成品保护和整改收口等关键岗位。多专业交叉作业时通过周计划会、日碰头会和工作面移交记录明确施工顺序、通道占用、材料运输、临电使用和成品保护责任，减少窝工、返工和相互污染。",
        "",
        "## 进度检查、纠偏与验收移交",
        "项目部建立日跟踪、周检查和节点复盘机制，对材料到场、样板确认、隐蔽验收、关键工序完成、问题整改和验收移交进行清单化管理。出现材料供应、工作面移交、交叉作业、设计深化或验收整改影响进度时，及时提出纠偏措施，调整资源投入和作业顺序，并形成协调记录、责任分解和闭环复查，确保施工总进度计划可执行、可检查、可追踪。",
    ]


def _technical_construction_organization_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_texts = [
        _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 260)
        for need in needs
        if need.get("source_type") in {"tender_document", "manual_input"} and (need.get("polished_text") or need.get("query") or need.get("reason"))
    ]
    requirement_summary = "；".join(_unique_text(requirement_texts)[:3]) or "围绕招标范围、施工组织、安全文明施工、进度控制、质量验收和成品保护等要求组织实施"
    material_titles = "、".join(str(row.title or row.item_title or "").strip() for row in material_rows[:4] if str(row.title or row.item_title or "").strip())
    material_sentence = (
        f"已绑定的企业技术资料包括{material_titles}，本章将其作为施工组织管理经验和项目执行能力的支撑材料。"
        if material_titles
        else "本章以招标文件、项目范围、现场施工条件和企业装饰装修工程管理经验为编制基础。"
    )
    title = str(component.get("component_title") or "施工组织设计").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("coordination", "finished_product_protection", "safety_civilized"),
        subject=title,
    )
    return [
        "",
        "## 施工组织总体部署",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方将采用项目经理负责制，按照先策划、后样板、再分区展开、过程验收和移交收口的原则组织施工，确保各专业工序、材料供应、现场协调和验收节点形成闭环。",
        material_sentence,
        *fact_sentences,
        "",
        "## 项目组织架构与职责",
        "项目实施阶段设置项目经理、技术负责人、安全负责人、质量管理、材料管理、资料管理和各专业施工班组责任人。项目经理统筹进度、资源、界面和对外协调；技术负责人负责技术交底、深化配合、施工方法和验收资料；安全负责人负责临电、动火、消防、文明施工和日常巡查；质量管理人员负责样板确认、隐蔽验收、实测实量和整改复验。",
        "",
        "## 施工流程与工序衔接",
        f"施工流程按现场移交、图纸会审和技术交底、样板先行、基层处理、隐蔽工程施工、面层施工、机电末端配合、成品保护、分项验收和竣工移交组织。{work_zone_phrase}根据工作面移交条件分区推进，优先保障测量放线、材料确认、隐蔽验收和交叉作业协调，减少返工和窝工。",
        "",
        "## 资源投入与现场平面管理",
        "劳动力、材料、机械和临时设施按照总进度计划和分区施工节奏动态配置。材料进场实行计划申报、品牌规格复核、样板确认、分类堆放和领用记录；办公室、工具间、材料间及垃圾临时堆放点按现场平面布置和总承包管理要求设置，并保持通道畅通、标识清晰和消防设施有效。",
        "",
        "## 质量、安全文明与成品保护",
        "质量控制坚持样板引路、技术交底、过程旁站、隐蔽验收和整改闭环。安全文明施工执行入场教育、班前交底、临电巡检、动火审批、消防器材配置、粉尘噪声控制和垃圾及时清运。已完成饰面、门窗、机电末端、公共通道和周边既有设施采取覆盖、封闭、分区警示和专人巡查措施，避免交叉施工造成污染、磕碰或损坏。",
        "",
        "## 进度协调与验收移交",
        "进度管理采用总控计划、周计划和日协调机制，围绕材料到场、样板确认、隐蔽验收、专业交叉、关键区域移交和整改销项设置节点控制。项目部定期与发包人、监理及总承包单位沟通现场条件、接口问题和验收安排，对影响工期的事项及时形成协调记录、责任分解和跟踪闭环。",
        "",
        "## 应急与沟通机制",
        "针对材料供应、工作面移交、交叉作业、临时用电、安全消防、质量整改和极端天气等情况建立应急协调机制。出现影响质量、安全或工期的事项时，项目部及时组织专题协调、制定处置措施、报审确认并跟踪复验，确保施工组织设计能够随现场条件动态优化并保持可执行性。",
    ]


def _technical_safety_civil_fire_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_texts = [
        _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 260)
        for need in needs
        if need.get("source_type") in {"tender_document", "manual_input"} and (need.get("polished_text") or need.get("query") or need.get("reason"))
    ]
    requirement_summary = "；".join(_unique_text(requirement_texts)[:3]) or "围绕安全生产、文明施工、防火施工和过程检查整改要求组织实施"
    material_titles = "、".join(str(row.title or row.item_title or "").strip() for row in material_rows[:4] if str(row.title or row.item_title or "").strip())
    material_sentence = (
        f"已绑定的企业安全文明施工资料包括{material_titles}，本章将其作为管理制度、检查记录和现场措施的支撑材料。"
        if material_titles
        else "本章以招标文件、现行安全文明施工管理要求、装修工程现场作业特点和总承包管理要求为编制基础。"
    )
    title = str(component.get("component_title") or "安全生产、文明施工、防火施工方案和保证措施").strip()
    affected_zone_phrase = _technical_project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("safety_civilized", "temporary_power", "waste_management"),
        subject=title,
    )
    return [
        "",
        "## 安全生产管理目标与责任体系",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方坚持安全第一、预防为主、综合治理的原则，建立项目经理牵头、专职安全管理人员日常检查、各班组长现场落实的安全生产责任体系，将安全文明施工、防火管理、临时用电和成品保护纳入每日施工组织和周例会协调内容。",
        material_sentence,
        *fact_sentences,
        "",
        "## 安全教育交底与作业许可",
        "所有进场人员在作业前完成入场安全教育、班前安全交底和分部分项技术安全交底，特殊工种按规定持证上岗。涉及动火、临时用电、高处作业、交叉作业、夜间施工或影响公共通道的作业，先完成方案交底、作业许可、现场防护和监护人员安排，未经确认不得擅自施工。",
        "",
        "## 临时用电、动火与消防管理",
        "施工临时用电执行三级配电、逐级保护、一机一闸一漏一箱和专人巡检制度，电箱、线路、照明和移动工具按现场审批方案布置并保持标识清晰。动火作业执行审批、清理、隔离、监护和复查流程，作业点配置有效灭火器材，易燃材料分类堆放并远离火源，疏散通道、消防通道和安全出口保持畅通。",
        "",
        "## 文明施工与环境保护",
        f"现场文明施工按分区围护、材料定置、标识清晰、通道畅通、工完场清的要求执行。装修垃圾分类收集、及时清运，粉尘较大的切割、打磨和拆除作业采取湿法、封闭或吸尘措施；噪声、异味和扬尘控制服从总承包单位及现场管理要求，避免影响{affected_zone_phrase}正常秩序。",
        "",
        "## 高处、临边及交叉作业控制",
        "涉及登高、临边、洞口、脚手架、移动操作平台或吊篮等作业时，先检查作业面、防护栏杆、生命绳、脚手板、平台稳定性和安全带挂设条件，确认合格后方可施工。多专业交叉施工时明确作业时段、作业区域、材料运输路线和成品保护责任，设置警示隔离和专人协调，防止坠落、物体打击、磕碰污染和重复拆改。",
        "",
        "## 检查整改、应急处置与资料闭环",
        "项目部建立日巡查、周检查和专项检查机制，对临电、动火、消防、通道、材料堆放、垃圾清运、个人防护和高风险作业形成检查记录。发现隐患立即落实整改责任、整改期限和复查确认；发生突发事件时按应急预案组织停工警戒、人员疏散、初期处置、信息报送和复盘改进，确保安全文明施工资料与现场管理同步闭环。",
    ]


def _technical_quality_assurance_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_texts = [
        _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 260)
        for need in needs
        if need.get("source_type") in {"tender_document", "manual_input"} and (need.get("polished_text") or need.get("query") or need.get("reason"))
    ]
    requirement_summary = "；".join(_unique_text(requirement_texts)[:3]) or "围绕施工质量目标、材料报审、样板引路、过程验收、整改复验和资料闭环组织实施"
    material_titles = "、".join(str(row.title or row.item_title or "").strip() for row in material_rows[:4] if str(row.title or row.item_title or "").strip())
    material_sentence = (
        f"已绑定的企业质量管理资料包括{material_titles}，本章将其作为质量制度、检查流程和验收资料的支撑材料。"
        if material_titles
        else "本章以招标文件、施工图纸、现行施工质量验收规范、项目现场条件和装饰装修工程质量管理经验为编制基础。"
    )
    title = str(component.get("component_title") or "重要的施工质量保障措施").strip()
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("material_sample", "finished_product_protection", "material_procurement"),
        subject=title,
    )
    return [
        "",
        "## 质量目标与管理责任体系",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方建立项目经理负责、技术负责人牵头、质量管理人员全过程检查、班组自检互检和资料同步归档的质量管理体系，将质量目标分解到材料报审、样板确认、工序交接、隐蔽验收、实测实量、整改复验和竣工移交各环节。",
        material_sentence,
        *fact_sentences,
        "",
        "## 样板引路与技术交底",
        "各分项工程施工前先组织图纸会审、深化复核和技术交底，明确施工做法、节点收口、允许偏差、成品保护和验收标准。重点工序实行样板先行，经发包人、监理及总承包单位确认后作为大面积施工依据，班组进场施工前按样板标准和质量控制点进行交底，减少做法不统一和返工风险。",
        "",
        "## 材料设备进场与报审复核",
        "主要材料、半成品和机电末端设备进场前按品牌、规格、型号、环保性能、合格证明、检测报告和样板确认情况进行报审复核。材料到场后执行验收、标识、分类堆放和领用记录制度，不符合招标文件、图纸、样板或规范要求的材料不得用于本工程，并及时退场或更换。",
        "",
        "## 工序过程控制与隐蔽验收",
        "施工过程按测量放线、基层处理、龙骨及隐蔽工程、面层安装、收口细部、机电末端配合、清洁保护和分项验收进行质量控制。隐蔽工程、关键节点和工序交接必须完成自检、专检、监理验收和影像资料留存后方可进入下一道工序，避免因隐蔽缺陷造成后期拆改。",
        "",
        "## 实测实量与质量通病防治",
        "项目部对墙顶地平整度、垂直度、接缝高低差、阴阳角方正、开孔定位、门窗收口、饰面污染、空鼓开裂、色差划伤和机电末端安装观感等进行实测实量和观感检查。针对装饰装修工程常见的基层空鼓、收口粗糙、接缝不顺、饰面污染、标高偏差和交叉施工损坏，提前设置预控措施、检查频次和整改标准。",
        "",
        "## 成品保护、整改复验与资料闭环",
        "已完成工程面采取覆盖、包裹、封闭、警示和专人巡查等保护措施，材料运输、交叉作业和清洁移交时同步明确保护责任。质量问题实行问题登记、原因分析、责任落实、限期整改、复查销项和资料归档闭环，竣工前统一整理材料报审、隐蔽验收、检验批、整改复验、影像记录和移交资料，确保质量保障措施可追溯、可检查、可验收。",
    ]


def _technical_temporary_power_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕施工临时用电、配电系统、线路敷设、用电巡检、动火消防和应急处置要求组织实施",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "临时用电、安全文明或消防管理资料",
        "本章以招标文件、现场总承包管理要求、临时用电安全管理要求和装饰装修施工用电特点为编制基础。",
    )
    title = str(component.get("component_title") or "施工临时用电施工方案").strip()
    affected_zone_phrase = _technical_project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    schedule_sentence = _technical_template_schedule_sentence(project_context)
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("temporary_power", "safety_civilized", "coordination"),
        subject=title,
    )
    return [
        "",
        "## 临时用电管理目标与组织职责",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方坚持安全可靠、分级管理、审批先行、专人维护的原则，进场后结合现场接驳条件、总承包单位管理要求和施工工作面布置完成临时用电复核、报审和验收，未经确认不得擅自接电、改线或超负荷使用。",
        material_sentence,
        schedule_sentence,
        *fact_sentences,
        "",
        "## 配电系统与箱体布置",
        "临时用电系统按三级配电、二级保护和一机一闸一漏一箱原则组织，配电箱、开关箱和移动用电设备按施工区域、作业面和运输通道合理布置。配电箱设置防雨、防砸、接地、编号、责任人和警示标识，箱内电器元件、漏电保护器、接线端子和保护接零经检查合格后投入使用；箱体位置随工作面转换动态调整，但必须保持通道畅通、消防距离和操作空间满足现场管理要求。",
        "",
        "## 线路敷设、照明与机具用电控制",
        "电缆线路采用架空、穿管、桥架或沿墙保护敷设方式，穿越通道、门洞、临边和材料运输路线时采取防压、防磨、防绊倒和醒目标识措施。施工照明按作业面、通道、材料堆放区和夜间巡查需要布置，潮湿、狭小或金属构件较多区域采用符合安全要求的照明和保护措施。切割、打磨、钻孔、搅拌、吸尘、搬运等移动机具使用前检查电缆、插头、漏保、外壳接地和防护装置，严禁私拉乱接和带病运行。",
        "",
        "## 动火、潮湿区域及交叉作业用电管理",
        f"涉及{affected_zone_phrase}内动火、打磨、切割、潮湿作业、高处作业或多专业交叉施工时，临时用电与动火审批、消防监护、通道管理和成品保护同步交底。易燃材料堆放区、成品保护区和公共通道附近不得随意设置高温设备或临时接线；需要调整线路或增加用电点时，由电工复核负荷、保护措施和现场条件后报审实施，避免因线路混乱、过载、漏电或火花引发安全事故。",
        "",
        "## 巡检维护、停送电与应急处置",
        "项目部建立每日巡检、重点作业前复查和异常天气后专项检查机制，对配电箱、漏电保护器、线路绝缘、接地保护、移动机具、照明设施和消防器材进行检查记录。停送电执行申请、确认、挂牌、复核和通知流程，维修、迁移或拆除线路时先切断电源并设专人监护。发生跳闸、漏电、线路发热、设备冒烟、人员触电或火情时，立即停电隔离、组织初期处置、保护现场并按应急流程报告和复查整改。",
        "",
        "## 验收记录、整改复查与资料闭环",
        "临时用电设施投入使用前完成方案交底、箱体检查、线路敷设验收、漏电保护试验和责任人确认；使用过程中形成巡检记录、维修记录、停送电记录、隐患整改记录和复查销项资料。对检查发现的问题明确整改责任、整改期限和复查人员，未完成整改不得继续使用相关用电点，确保临时用电施工方案在现场可执行、可检查、可追溯。",
    ]


def _technical_material_procurement_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕主要材料采购、品牌规格复核、样板确认、材料报审、分批进场和质量验收要求组织实施",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "材料品牌、样板、采购或供应管理资料",
        "本章以招标文件、施工图纸、材料品牌及样板要求、总进度计划和现场材料堆放条件为编制基础。",
    )
    title = str(component.get("component_title") or "主要材料采购计划").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    schedule_sentence = _technical_template_schedule_sentence(project_context)
    quality_sentence = _technical_template_quality_sentence(project_context)
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("material_procurement", "material_sample", "coordination"),
        subject=title,
    )
    return [
        "",
        "## 采购组织原则与责任分工",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方按照计划先行、样板确认、报审复核、分批供应、进场验收和领用追溯的原则组织主要材料采购，项目经理统筹采购节点，材料管理人员负责计划、报审、到货和台账，技术及质量人员负责品牌规格、样板、检测资料和现场使用条件复核。",
        material_sentence,
        schedule_sentence,
        quality_sentence,
        *fact_sentences,
        "",
        "## 材料需求计划与进场批次",
        f"材料需求计划根据施工图纸、深化排版、样板确认、施工段划分和{work_zone_phrase}工作面移交条件编制，按基层材料、龙骨及辅材、饰面材料、门窗五金、涂料胶粘剂、机电末端配套材料和成品保护材料等类别分批组织。对定制、加工周期长、观感要求高或影响关键工序的材料提前锁定规格、数量、加工周期和运输安排，避免材料滞后影响现场连续施工。",
        "",
        "## 品牌规格、样板与报审复核",
        "主要材料采购前核对招标文件、品牌表、图纸、样板和技术参数要求，按规定提交样板、合格证明、检测报告、环保性能资料、规格型号和供货来源。经发包人、监理及总承包单位确认后方可批量采购；涉及颜色、纹理、尺寸、收口效果或环保性能的材料坚持样板先行和封样管理，现场批量材料与确认样板不一致时不得投入使用。",
        "",
        "## 供应周期、运输到场与现场验收",
        "采购计划按施工总进度倒排询价、下单、加工、运输、到场和复验节点，并与供应商明确批次、包装、防潮防损、运输路线和到场时间。材料到场后由材料、质量和施工班组共同验收，核对品牌、规格、数量、外观、批号、合格证明和检测资料；发现破损、色差、污染、数量偏差或资料不全时及时隔离标识、退换处理并记录。",
        "",
        "## 堆放领用、追溯管理与成品保护",
        "现场材料按类别、规格、批次和使用区域分类堆放，设置标识、保护、防潮、防火和防污染措施，易损、贵重、定制或影响观感的材料采取专人管理和限额领用。材料领用建立台账，记录使用区域、班组、数量和批次，便于质量追溯；已进场材料和已完成工程面同步落实成品保护，避免搬运、交叉作业和二次污染造成损耗。",
        "",
        "## 甲指乙供、替代审批与风险纠偏",
        "涉及甲指乙供材料时，提前与发包人、供应单位和总承包单位确认品牌规格、供货周期、到场条件、验收标准和责任界面。因停产、供货周期、现场条件或设计深化导致材料调整时，不擅自替代，必须按程序提交替代原因、技术参数、样板、价格和质量证明；替代材料必须经审批确认后使用。对供应滞后、样板未确认、运输破损或进场复验不合格等风险，及时启动备选供应、批次调整、工序穿插和责任跟踪机制，确保采购计划服务施工进度和质量目标。",
    ]


def _technical_key_difficulty_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕项目特点、工期组织、交叉作业、材料供应、质量观感、成品保护、安全文明和验收移交风险组织分析",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "类似工程经验、技术方案或现场管理资料",
        "本章以招标文件、项目范围、现场施工条件、工期质量要求和企业装饰装修项目管理经验为编制基础。",
    )
    title = str(component.get("component_title") or "项目重难点分析").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    affected_zone_phrase = _technical_project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    scope_sentence = _technical_template_scope_sentence(project_context)
    schedule_sentence = _technical_template_schedule_sentence(project_context)
    quality_sentence = _technical_template_quality_sentence(project_context)
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("coordination", "finished_product_protection", "material_sample", "temporary_power", "waste_management"),
        subject=title,
    )
    return [
        "",
        "## 项目特点与重难点识别",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方将从施工范围、工期组织、材料供应、质量观感、安全文明、成品保护和验收移交等方面识别重难点，并采用“难点识别、原因分析、对策措施、责任落实、检查复盘”的闭环方式组织实施。",
        material_sentence,
        scope_sentence,
        schedule_sentence,
        quality_sentence,
        *fact_sentences,
        "",
        "## 工期组织与交叉作业控制",
        f"难点：{work_zone_phrase}施工工序多、材料品类多，易受工作面移交、图纸深化、样板确认、材料到场和多专业交叉作业影响。对策：以总控计划为主线，将施工准备、深化样板、基层隐蔽、面层安装、机电末端配合、整改复验和移交验收分解为可检查节点，通过周计划、日协调、工作面移交单和问题销项表控制节奏，优先保障关键工序和关键区域连续施工。",
        "",
        "## 材料样板、报审与供应保障",
        "难点：装饰装修材料对品牌、规格、颜色、纹理、环保性能和观感一致性要求高，定制材料或甲指乙供材料可能影响工期。对策：提前建立主要材料报审和样板确认台账，按施工段倒排采购、加工、运输和进场时间；到场后执行品牌规格复核、外观检查、资料核验和批次标识，不合格材料隔离退换，替代材料必须经审批确认后使用。",
        "",
        "## 隐蔽工程、细部收口与观感质量控制",
        "难点：基层处理、龙骨安装、管线配合、隐蔽验收、阴阳角、接缝、开孔、门窗收口和机电末端位置直接影响后期观感和返工风险。对策：坚持样板引路、技术交底、测量复核、隐蔽验收和实测实量制度，重点节点先做样板、先验收后展开，细部收口由技术负责人组织复核，质量问题形成整改责任、整改期限和复验销项。",
        "",
        "## 成品保护、既有设施与现场秩序维护",
        f"难点：施工期间材料运输、交叉作业、垃圾清运和人员流动可能影响已完工程、既有设施和{affected_zone_phrase}现场秩序。对策：对公共通道、已完饰面、门窗五金、机电末端、材料堆放区和既有设施设置覆盖、包裹、隔离、警示和专人巡查措施；材料运输和垃圾清运按指定路线、时段和堆放要求执行，做到工完场清、损坏追责和及时修复。",
        "",
        "## 安全文明、临电消防与垃圾清运控制",
        "难点：装修施工涉及临时用电、动火、切割打磨、粉尘噪声、材料堆放和可燃物管理，安全文明风险较集中。对策：执行入场教育、班前交底、临电巡检、动火审批、消防器材配置、易燃材料分区堆放、粉尘噪声控制和垃圾分类清运制度；高处、临边、潮湿或交叉作业设置专项防护和监护，发现隐患立即整改复查。",
        "",
        "## 重难点跟踪、纠偏与验收移交",
        "项目部建立重难点清单和动态跟踪机制，对每项难点明确责任人、控制节点、检查频次和验收标准。对影响质量、安全、工期或移交的事项及时组织专题协调，形成问题台账、整改措施、完成时限和复查记录；竣工前统一梳理材料报审、隐蔽验收、检验批、整改销项、成品保护和移交资料，确保重难点对策落实到施工全过程并可追溯。",
    ]


def _technical_site_facility_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕办公室、工具间、材料间布置、消防临电、文明施工、台账管理和动态调整要求组织实施",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "现场平面、临时设施或材料管理资料",
        "本章以招标文件、现场总承包管理要求、施工区域划分、材料机具使用特点和文明施工要求为编制基础。",
    )
    title = str(component.get("component_title") or "办公室、工具间、材料间管理方案").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    affected_zone_phrase = _technical_project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("coordination", "temporary_power", "finished_product_protection"),
        subject=title,
    )
    return [
        "",
        "## 临时设施布置原则与管理目标",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。办公室、工具间和材料间布置坚持服从现场总平面、满足施工组织、保障消防安全、便于材料周转和保持文明施工的原则，进场后结合{work_zone_phrase}工作面移交、通道条件和总承包单位要求完成布置复核和报审确认。",
        material_sentence,
        *fact_sentences,
        "",
        "## 办公室管理",
        "现场办公室作为项目管理、技术交底、资料整理和会议协调场所，设置必要的项目组织架构、进度计划、质量安全责任、应急联络和文明施工要求。办公室内资料分类归档，会议纪要、交底记录、检查记录和整改台账及时更新；办公区域保持整洁、用电安全、消防器材有效，不堆放易燃材料和施工废料。",
        "",
        "## 工具间管理",
        "工具间用于集中存放小型机具、检测工具、劳保用品、周转防护材料和易损耗工具，实行分类摆放、标识清晰、领用登记、维修保养和归还检查制度。电动工具使用前检查绝缘、插头、外壳、开关和防护装置，损坏或不合格工具不得投入使用；切割、打磨、钻孔等机具按施工阶段领用，避免散放在公共通道、成品区域或消防疏散路径内。",
        "",
        "## 材料间管理",
        "材料间按材料类别、规格、批次、使用区域和防护要求分类设置，基层材料、饰面材料、五金配件、胶粘剂、涂料、成品保护材料和易损材料分区堆放。材料进场后核对品牌、规格、数量、合格证明和样板确认情况，建立收发存台账；对防潮、防火、防污染、防变形要求较高的材料采取垫高、覆盖、封闭或专人管理措施，确保材料状态满足施工质量要求。",
        "",
        "## 消防、临电、文明施工与成品保护",
        f"办公室、工具间、材料间周边保持通道畅通、标识清晰、消防器材有效，严禁私拉乱接、违规动火和混放易燃物。材料搬运和工具领用不得影响{affected_zone_phrase}正常秩序，已完工程面和既有设施采取覆盖、隔离、警示和专人巡查措施。每日作业结束后落实工完场清、门窗关闭、断电检查和垃圾清理，避免材料散落、工具遗失和交叉污染。",
        "",
        "## 台账检查、责任落实与动态调整",
        "项目部明确办公室、工具间、材料间责任人，建立设施检查、工具领用、材料收发、消防巡查、临电检查和问题整改台账。随着施工阶段、工作面移交和材料批次变化，及时调整材料间容量、堆放区域和工具配置；发现通道占用、材料混放、消防隐患、工具损坏或台账不符时，立即落实整改责任和复查销项，确保临时设施管理可检查、可追溯、可持续优化。",
    ]


def _technical_waste_management_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕垃圾分类清理、临时堆放、场内运输、外运配合、扬尘噪声和文明施工要求组织实施",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "文明施工、垃圾清运或现场管理资料",
        "本章以招标文件、现场文明施工要求、装修垃圾产生特点、运输通道条件和总承包管理制度为编制基础。",
    )
    title = str(component.get("component_title") or "垃圾清理、堆放、运输、堆场管理方案").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    affected_zone_phrase = _technical_project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("waste_management", "safety_civilized", "coordination"),
        subject=title,
    )
    return [
        "",
        "## 垃圾管理目标与责任分工",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方坚持分类收集、及时清理、定点堆放、封闭运输、工完场清和责任到人的原则，项目部明确垃圾清运责任人、班组清理责任、运输路线和检查频次，确保垃圾管理不影响{work_zone_phrase}施工组织和现场秩序。",
        material_sentence,
        *fact_sentences,
        "",
        "## 分类收集与日常清理",
        "装修施工产生的包装物、边角料、基层拆改废料、切割打磨粉尘、一般废弃物和可回收材料按类别收集，不同类别垃圾不混堆、不乱倒。各班组每日作业结束前清理本作业面，重点区域随产随清；切割、打磨、拆除和搬运作业产生的粉尘、碎屑和废料及时收集，防止污染已完工程、公共通道和材料堆放区。",
        "",
        "## 临时堆放点与堆场管理",
        "临时垃圾堆放点按现场总平面和总承包单位要求设置，避开消防通道、安全出口、材料堆放区、成品保护区和主要运输通道。堆放点设置围挡、标识、防扬散和防渗漏措施，严禁超范围、超高度和长时间堆放；易燃包装物、木材边角料和可燃废料及时分拣清运，不与电箱、动火点或热源相邻堆放。",
        "",
        "## 场内运输、外运配合与路线控制",
        f"垃圾场内运输按指定时间、路线和容器组织，运输过程中采取袋装、桶装、覆盖或封闭措施，减少遗撒、扬尘和碰撞。穿越{affected_zone_phrase}或公共通道时设置必要的警示、保护和清洁措施，运输后及时清扫路线；需要外运时配合发包人、总承包单位或物业管理要求办理出场、计量、消纳和车辆清洁手续，确保外运过程合规有序。",
        "",
        "## 扬尘、噪声、消防和安全文明控制",
        "粉尘较大的拆除、切割、打磨和清扫作业采用湿法、吸尘、封闭或局部围挡措施；垃圾装袋、装车和转运过程控制噪声和扬尘，避免影响周边区域。垃圾堆放和运输不得堵塞消防通道、疏散通道和临时用电维护空间，作业人员按要求佩戴防护用品，搬运尖锐、重物或易碎废料时采取防割、防砸和防滑措施。",
        "",
        "## 检查记录、整改销项与资料闭环",
        "项目部建立垃圾清理、堆放点巡查、运输记录、外运交接和问题整改台账，对通道占用、垃圾混放、堆放超限、扬尘遗撒、消防隐患和清运不及时等问题明确整改责任、完成时限和复查结果。垃圾管理情况纳入每日文明施工检查和周例会协调，确保现场整洁、道路畅通、资料可追溯。",
    ]


def _technical_material_sample_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕主要材料样板清单、规格尺寸复核、样板报审、封样确认、采购进场联动和资料闭环要求组织实施",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "材料样板、品牌表或材料管理资料",
        "本章以招标文件、主要材料品牌及样板要求、施工图纸、材料采购计划和质量验收标准为编制基础。",
    )
    title = str(component.get("component_title") or "主要材料样板提供计划").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    schedule_sentence = _technical_template_schedule_sentence(project_context)
    quality_sentence = _technical_template_quality_sentence(project_context)
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("material_sample", "material_procurement", "coordination"),
        subject=title,
    )
    return [
        "",
        "## 样板提供原则与责任分工",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方坚持样板先行、规格复核、报审确认、封样留存和批量一致的原则组织主要材料样板管理，材料管理人员负责样板清单和提交计划，技术质量人员负责规格、尺寸、颜色、纹理、环保性能和节点适配复核。",
        material_sentence,
        schedule_sentence,
        quality_sentence,
        *fact_sentences,
        "",
        "## 样板清单、规格复核与报审计划",
        f"样板清单根据招标文件、图纸、品牌表、采购计划和{work_zone_phrase}施工内容编制，重点覆盖饰面材料、涂料胶粘剂、五金配件、门窗配套、机电末端、收口材料和影响观感质量的定制材料。样板提交前复核品牌、规格、型号、颜色、纹理、尺寸、环保性能、检测资料和适用部位，按施工总进度倒排样板报审时间，避免样板确认滞后影响采购和施工。",
        "",
        "## 样板制作、封样确认与留存管理",
        "样板制作和提交按发包人、监理及总承包单位要求执行，必要时结合节点做法、收口效果和安装条件制作实物样板或组合样板。经确认的样板及时封样、编号、拍照、登记和妥善保存，记录确认时间、确认人员、适用区域和技术要求；未确认样板不得作为批量采购和大面积施工依据。",
        "",
        "## 样板与采购、进场和施工质量联动",
        "主要材料采购、到场验收和现场施工均以确认样板为基准，进场材料按品牌、规格、颜色、纹理、尺寸、外观和检测资料与封样样板进行比对。批量材料与样板不一致、资料不全、外观缺陷或环保性能不满足要求时，及时隔离标识、退换处理或重新报审；施工班组进场前按样板标准进行技术交底，确保施工效果与样板一致。",
        "",
        "## 变更替代、复核纠偏与资料闭环",
        "因供货周期、停产、设计深化或现场条件变化需要调整材料样板时，必须提交替代原因、技术参数、样板实物、质量证明和适用部位，经审批确认后实施。项目部建立样板报审、确认、封样、领用复核、变更替代和现场比对台账，定期检查样板保存状态和执行情况，确保样板管理贯穿采购、进场、施工和验收全过程。",
    ]


def _technical_competitive_enhancement_rule_lines(
    component: dict[str, Any],
    needs: list[dict[str, Any]],
    material_rows: list[BidMaterialRequirement],
    *,
    project_context: dict[str, Any] | None = None,
) -> list[str]:
    requirement_summary = _technical_template_requirement_summary(
        needs,
        "围绕进度组织、质量样板、安全文明、材料供应、风险响应、资料移交和持续服务提升投标竞争力",
    )
    material_sentence = _technical_template_material_sentence(
        material_rows,
        "企业技术管理、类似经验或服务承诺资料",
        "本章以招标文件、当前项目范围、工期质量目标、现场管理要求和企业装饰装修施工管理能力为编制基础。",
    )
    title = str(component.get("component_title") or "提升投标竞争力内容").strip()
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    scope_sentence = _technical_template_scope_sentence(project_context)
    schedule_sentence = _technical_template_schedule_sentence(project_context)
    quality_sentence = _technical_template_quality_sentence(project_context)
    fact_sentences = _technical_template_requirement_fact_sentences(
        project_context,
        ("coordination", "finished_product_protection", "material_procurement", "material_sample", "safety_civilized"),
        subject=title,
    )
    return [
        "",
        "## 投标竞争力提升总体思路",
        f"本章节针对“{title}”进行项目化编制，重点响应：{requirement_summary}。我方竞争力提升不以空泛承诺为主，而是围绕{work_zone_phrase}施工组织、质量样板、安全文明、材料供应、成品保护、资料移交和风险响应形成可执行、可检查、可追溯的管理措施。",
        material_sentence,
        scope_sentence,
        schedule_sentence,
        quality_sentence,
        *fact_sentences,
        "",
        "## 进度、组织与协同优势",
        "我方将以总控计划、周计划、日协调和工作面移交记录为核心，提前识别图纸深化、样板确认、材料到场、隐蔽验收和交叉作业对进度的影响。通过分区流水、专业穿插、关键节点复盘和问题销项机制提升组织效率，减少窝工、返工和等待时间，确保施工组织与发包人、监理、总承包单位管理节奏保持一致。",
        "",
        "## 质量、样板与精细化管控优势",
        "质量管理方面坚持样板先行、技术交底、过程检查、隐蔽验收、实测实量和整改复验。对饰面观感、细部收口、机电末端配合、材料批次一致性和成品保护等影响交付效果的内容实施重点控制；材料样板、施工样板和节点样板确认后作为班组交底、进场验收和质量复核依据，提升一次成优率。",
        "",
        "## 安全文明、成品保护与现场秩序优势",
        "安全文明方面执行入场教育、临电巡检、动火审批、消防器材配置、粉尘噪声控制、垃圾分类清运和工完场清制度。对公共通道、既有设施、已完工程面、材料堆放区和交叉作业面设置保护、隔离、警示和巡查措施，降低污染、磕碰、损坏和现场秩序扰动风险，为发包人创造稳定可控的施工环境。",
        "",
        "## 材料供应、成本控制与风险响应优势",
        "材料管理方面建立样板确认、采购计划、供应周期、进场验收、领用追溯和替代审批闭环，重点材料提前锁定品牌规格、加工周期和进场批次。对供货滞后、样板不一致、运输破损、设计深化和现场条件变化等风险，及时组织备选供应、工序调整、资源补充和审批沟通，保障施工连续性和成本受控。",
        "",
        "## 服务承诺、资料移交与持续改进",
        "我方将把投标阶段形成的进度、质量、安全、材料、样板、成品保护和重难点管理措施转化为进场交底和过程检查清单。施工过程中及时提交报审资料、隐蔽验收资料、检验批资料、整改销项资料和竣工移交资料；对发包人、监理及总承包单位提出的问题快速响应、闭环整改并复盘改进，确保竞争力内容能够落到现场实施和最终交付。",
    ]


def _technical_template_requirement_summary(needs: list[dict[str, Any]], fallback: str) -> str:
    requirement_texts = [
        _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 260)
        for need in needs
        if need.get("source_type") in {"tender_document", "manual_input"} and (need.get("polished_text") or need.get("query") or need.get("reason"))
    ]
    return "；".join(_unique_text(requirement_texts)[:3]) or fallback


def _technical_template_material_sentence(
    material_rows: list[BidMaterialRequirement],
    material_label: str,
    fallback: str,
) -> str:
    material_titles = "、".join(str(row.title or row.item_title or "").strip() for row in material_rows[:4] if str(row.title or row.item_title or "").strip())
    if material_titles:
        return f"已绑定的{material_label}包括{material_titles}，本章将其作为正式技术标编制和现场实施安排的支撑材料。"
    return fallback


def _technical_template_requirement_fact_sentences(
    project_context: dict[str, Any] | None,
    keys: tuple[str, ...],
    subject: str | None = None,
) -> list[str]:
    sentences: list[str] = []
    for key in keys:
        fact = _technical_project_requirement_fact(project_context, key)
        summary = str(fact.get("summary") or "").strip() if isinstance(fact, dict) else ""
        if not summary:
            continue
        label = str(fact.get("label") or _technical_requirement_fact_label(key)).strip()
        if subject:
            label = f"\u9488\u5bf9{subject}\uff0c{label}"
        sentences.append(f"{label}要求方面，招标文件提示“{_clip(summary.strip('。；;'), 180)}”，本章按该要求细化执行措施、责任分工和检查闭环。")
    return _unique_text(sentences)


def _technical_project_requirement_fact(project_context: dict[str, Any] | None, key: str) -> dict[str, Any]:
    technical_requirements = (project_context or {}).get("technical_requirements") or {}
    if isinstance(technical_requirements, dict) and isinstance(technical_requirements.get(key), dict):
        return technical_requirements.get(key) or {}
    return {}


def _technical_requirement_fact_label(key: str) -> str:
    for definition in TECHNICAL_REQUIREMENT_FACT_DEFINITIONS:
        if definition.get("key") == key:
            return str(definition.get("label") or key)
    return key


def _technical_template_schedule_sentence(project_context: dict[str, Any] | None) -> str:
    schedule = (project_context or {}).get("schedule") or {}
    sentence = str(schedule.get("sentence") or "").strip() if isinstance(schedule, dict) else ""
    if sentence:
        return f"工期组织方面，{sentence}，本章相关计划和控制措施按该工期事实倒排。"
    duration = schedule.get("total_duration_days") if isinstance(schedule, dict) else None
    if isinstance(duration, int) and duration > 0:
        return f"工期组织方面，招标文件已明确总工期为{duration}天，本章相关计划和控制措施按该工期事实倒排。"
    return "工期组织方面，未抽取到具体开工日期或工期天数时，本章不编造具体日期，按招标文件约定、开工令和总控计划倒排实施。"


def _technical_template_quality_sentence(project_context: dict[str, Any] | None) -> str:
    quality = (project_context or {}).get("quality") or {}
    goal = str(quality.get("goal") or "").strip() if isinstance(quality, dict) else ""
    if goal:
        return f"质量控制方面，招标文件质量目标为“{goal}”，本章措施按该目标组织材料、过程和验收闭环。"
    return "质量控制方面，未抽取到明确质量目标时，本章按招标文件、施工图纸和现行验收规范组织过程控制和验收闭环。"


def _technical_template_scope_sentence(project_context: dict[str, Any] | None) -> str:
    scope = (project_context or {}).get("scope") or {}
    scope_text = str(scope.get("scope_text") or "").strip() if isinstance(scope, dict) else ""
    if scope_text:
        return f"工程范围方面，已抽取到招标范围：{_clip(scope_text, 160)}。"
    work_zone_phrase = _technical_project_context_phrase(project_context, "work_zone_phrase", "")
    if work_zone_phrase:
        return f"工程范围方面，本章围绕{work_zone_phrase}组织重难点识别和措施落地。"
    return "工程范围方面，本章围绕招标文件明确的施工范围、现场界面和配合区域组织重难点识别和措施落地。"


def _technical_composition_rule_suggested_headings(component: dict[str, Any]) -> list[str]:
    intent = _technical_composition_intent(component)
    headings = _technical_composition_intent_template(intent).get("headings")
    if headings:
        return [str(item) for item in headings if str(item or "").strip()]
    return ["编制依据", "企业资料引用", "投标响应草稿", "待人工完善"]


def _technical_composition_writing_requirements(component: dict[str, Any]) -> list[str]:
    requirements = [
        "正文使用中文技术标写法，围绕本项目形成可直接复核的成段内容。",
        "方案型章节至少写出目标/原则、实施安排、过程控制、检查验收或闭环改进等内容。",
        "优先使用招标范围、质量、安全文明施工、工期、施工组织等项目背景信息进行项目化表达。",
        "企业资料只作为能力、经验、附件或固定信息引用，不得虚构资料库没有的资质、人员、证书或业绩。",
        "保留必要的【待确认：...】占位，但不要只输出占位句。",
    ]
    intent = _technical_composition_intent(component)
    requirements.extend(
        str(item)
        for item in _technical_composition_intent_template(intent).get("writing_requirements") or ()
        if str(item or "").strip()
    )
    return requirements


def _technical_composition_class_label(value: str) -> str:
    labels = {
        "fixed_enterprise_material": "固定企业资料",
        "tender_extracted_content": "招标文件抽取内容",
        "mixed": "企业资料与招标文件组合",
        "manual_input": "需人工补充/方案编写",
    }
    return labels.get(value or "", value or "未分类")


def _material_requirement_evidence_for_section(
    db: Session,
    run: BidParseRun,
    section: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = (
        db.query(BidMaterialRequirement)
        .filter(
            BidMaterialRequirement.parse_run_id == run.id,
            BidMaterialRequirement.status.in_(["submitted", "approved", "applied"]),
        )
        .order_by(BidMaterialRequirement.id.asc())
        .all()
    )
    evidence: list[dict[str, Any]] = []
    for row in rows:
        if not _material_requirement_matches_section(row, section):
            continue
        evidence.extend(_material_requirement_row_evidence(db, row))
    return _dedupe_evidence(evidence)[:12]


def _material_requirement_matches_section(row: BidMaterialRequirement, section: dict[str, Any]) -> bool:
    section_package = _normalize_package_scope(section.get("package_key"))
    row_package = _normalize_package_scope(row.package_key)
    if section_package and row_package and section_package != row_package:
        return False
    section_key = str(section.get("section_key") or "")
    format_item_key = str(section.get("format_item_key") or "")
    if row.section_key and section_key and row.section_key == section_key:
        return True
    if row.format_item_key and format_item_key and row.format_item_key == format_item_key:
        return True
    section_title = str(section.get("section_title") or "").strip()
    if section_package and row_package == section_package and row.item_title and section_title and row.item_title == section_title:
        return True
    return False


def _material_requirement_row_evidence(db: Session, row: BidMaterialRequirement) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item_uuid in _material_requirement_profile_item_uuids(row):
        item = _enterprise_profile_item_for_material(db, item_uuid)
        if item:
            text = _enterprise_profile_evidence_text(item, row)
            if text:
                evidence.append(
                    {
                        "source_kind": "enterprise_profile",
                        "source_file": f"企业资料库：{item.title}",
                        "source_location": row.title,
                        "original_text": _clip(text, 480),
                        "requirement_uuid": row.requirement_uuid,
                        "profile_item_uuid": item.item_uuid,
                        "profile_category": item.category,
                    }
                )
    if row.submitted_value:
        evidence.append(
            {
                "source_kind": "material_requirement",
                "source_file": "技术标资料补齐清单" if row.package_key == "technical" else "投标资料补齐清单",
                "source_location": row.title,
                "original_text": _clip(row.submitted_value, 480),
                "requirement_uuid": row.requirement_uuid,
            }
        )
    for file_id in _material_requirement_file_ids(row):
        evidence.append(
            {
                "source_kind": "material_requirement",
                "source_file": "投标资料补齐清单附件",
                "source_location": row.title,
                "original_text": f"已上传补齐附件：{file_id}",
                "requirement_uuid": row.requirement_uuid,
                "submitted_file_id": file_id,
            }
        )
    return evidence


def _technical_composition_evidence_for_section(run: BidParseRun, section: dict[str, Any]) -> list[dict[str, Any]]:
    if _normalize_package_scope(section.get("package_key")) != "technical":
        return []
    summary = loads_json(run.summary_json, {}) or {}
    plan = summary.get("technical_composition_plan")
    if not isinstance(plan, dict):
        return []
    evidence: list[dict[str, Any]] = []
    for component in plan.get("components") or []:
        if not isinstance(component, dict) or not _technical_composition_matches_section(component, section):
            continue
        for need in component.get("information_needs") or []:
            if not isinstance(need, dict) or need.get("source_type") != "tender_document":
                continue
            polished_text = _clip(need.get("polished_text") or need.get("query") or need.get("reason"), 600)
            if not polished_text:
                continue
            first_evidence = (need.get("source_evidence") or component.get("source_evidence") or [{}])[0]
            evidence.append(
                {
                    "source_kind": "technical_composition",
                    "source_file": first_evidence.get("source_file") or "技术标组成识别",
                    "source_location": first_evidence.get("source_location") or need.get("need_title") or component.get("component_title"),
                    "original_text": polished_text,
                    "component_key": component.get("component_key"),
                    "need_key": need.get("need_key"),
                }
            )
    return evidence[:8]


def _technical_composition_matches_section(component: dict[str, Any], section: dict[str, Any]) -> bool:
    component_key = str(component.get("component_key") or "")
    component_title = str(component.get("component_title") or "").strip()
    section_key = str(section.get("section_key") or "")
    format_item_key = str(section.get("format_item_key") or "")
    section_title = str(section.get("section_title") or "").strip()
    if component_key and component_key in {section_key, format_item_key}:
        return True
    if component_key and (component_key in section_key or component_key in format_item_key):
        return True
    if component_title and section_title and component_title == section_title:
        return True
    return _text_similarity(component_title, section_title) >= 0.42


def _text_similarity(left: str, right: str) -> float:
    left_norm = re.sub(r"[\W_]+", "", str(left or "").lower())
    right_norm = re.sub(r"[\W_]+", "", str(right or "").lower())
    if not left_norm or not right_norm:
        return 0.0
    overlap = len(set(left_norm) & set(right_norm)) / max(len(set(left_norm)), 1)
    return max(overlap, SequenceMatcher(None, left_norm, right_norm).ratio())


def _material_requirement_profile_item_uuids(row: BidMaterialRequirement) -> list[str]:
    normalized = loads_json(row.normalized_json, {}) or {}
    manual = normalized.get("manual_submission") if isinstance(normalized.get("manual_submission"), dict) else {}
    return _unique_text([*_list_value(manual.get("profile_item_uuids")), row.submitted_profile_item_uuid])


def _material_requirement_file_ids(row: BidMaterialRequirement) -> list[str]:
    normalized = loads_json(row.normalized_json, {}) or {}
    manual = normalized.get("manual_submission") if isinstance(normalized.get("manual_submission"), dict) else {}
    return _unique_text([*_list_value(manual.get("file_ids")), row.submitted_file_id])


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _enterprise_profile_item_for_material(db: Session, item_uuid: str | None) -> EnterpriseProfileItem | None:
    if not item_uuid:
        return None
    return (
        db.query(EnterpriseProfileItem)
        .filter(
            EnterpriseProfileItem.item_uuid == item_uuid,
            EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE,
        )
        .first()
    )


def _enterprise_profile_evidence_text(item: EnterpriseProfileItem, row: BidMaterialRequirement) -> str:
    if item.content_text:
        return item.content_text
    if item.summary:
        return item.summary
    attachment_names = [
        attachment.original_filename or attachment.description or attachment.file_id
        for attachment in item.attachments or []
        if attachment.original_filename or attachment.description or attachment.file_id
    ]
    if attachment_names:
        return f"已选择企业资料库附件：{'、'.join(attachment_names[:5])}"
    return row.submitted_value or row.description or item.title


def _placeholders_for_section(section: dict[str, Any]) -> list[dict[str, Any]]:
    if section.get("draft_mode") != "placeholder":
        return []
    placeholders = []
    for index, missing in enumerate(section.get("missing_inputs") or [], start=1):
        placeholders.append(
            {
                "placeholder_key": f"P{index}",
                "text": f"【待补充：{missing}】",
                "reason": missing,
                "owner_role": section.get("owner_role") or "经营",
            }
        )
    return placeholders


def _warnings_for_section(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    risks: list[TenderRisk],
) -> list[dict[str, Any]]:
    warnings = []
    if section.get("draft_mode") == "blocked":
        warnings.append(
            {
                "level": "blocking",
                "message": "该章节存在阻断事项，本次只生成阻断说明，不生成可提交正文。",
            }
        )
    if section.get("draft_mode") == "review_note":
        warnings.append(
            {
                "level": "review",
                "message": "该章节需要先完成负责人复核，本次生成复核说明，不直接生成投标正文。",
            }
        )
    for warning in section.get("risk_warnings") or []:
        warnings.append({"level": "risk", "message": str(warning)})
    for item in response_items:
        if item.risk_level == "high":
            warnings.append({"level": "high", "message": f"{item.response_title} 为高风险响应项。"})
    for item in risks:
        if item.is_blocking or item.risk_level == "high":
            warnings.append({"level": item.risk_level or "risk", "message": _clip(item.risk_explanation or item.original_text, 180)})
    return _unique_warnings(warnings)[:8]


def _quality_profile_for_section(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    generation_decision: dict[str, Any],
) -> dict[str, Any]:
    content_type = str(section.get("content_type") or "")
    generation_strategy = str(section.get("generation_strategy") or "")
    source_mapping = section.get("source_mapping") if isinstance(section.get("source_mapping"), dict) else {}
    mapping_status = str(source_mapping.get("status") or "")
    mapping_confidence = str(source_mapping.get("confidence") or "")
    blockers: list[str] = []
    material_gaps: list[str] = []
    warnings: list[str] = []

    if section.get("draft_mode") == "blocked" or any(risk.is_blocking for risk in risks):
        blockers.append("存在阻断风险或目录骨架判定为暂不生成正文。")
    if section.get("format_plan_review_status") and section.get("format_plan_review_status") != "confirmed":
        material_gaps.append("投标文件格式表尚未确认。")
    if generation_decision.get("draft_mode") == "placeholder":
        material_gaps.append("目录骨架判定为占位草稿，仍需补充材料。")
    if generation_decision.get("draft_mode") == "review_note":
        material_gaps.append("目录骨架判定为先生成复核说明。")
    if content_type in {"fixed_form"} or generation_strategy == "manual_fill":
        material_gaps.append("该目录项属于甲方固定表单，应按格式填写，不自由生成正文。")
    if content_type == "pricing_table" or generation_strategy == "from_cost_quote":
        material_gaps.append("该目录项应接入报价/成本链路后再形成可提交内容。")
    if content_type in {"attachment_proof", "qualification_attachment"} or generation_strategy == "manual_upload":
        material_gaps.append("该目录项需要上传或绑定附件证明材料。")
    if mapping_status and mapping_status != "mapped":
        material_gaps.append("目录项尚未映射到响应矩阵、招标要求或风险证据。")
    if mapping_confidence == "low":
        warnings.append("目录项映射置信度偏低，需要人工确认素材匹配。")
    if not evidence:
        material_gaps.append("缺少可追溯原文证据。")
    if generation_decision.get("draft_mode") == "formal" and not response_items and not requirements:
        warnings.append("正式草稿缺少响应矩阵项或招标要求支撑。")

    if blockers:
        quality_status = "blocked_by_risk"
    elif material_gaps:
        quality_status = "needs_material"
    elif warnings:
        quality_status = "needs_mapping_review"
    else:
        quality_status = "ready_for_draft"

    llm_allowed = quality_status == "ready_for_draft" and bool(generation_decision.get("llm_eligible"))
    return {
        "quality_status": quality_status,
        "quality_status_label": _quality_status_label(quality_status),
        "content_type": content_type or None,
        "generation_strategy": generation_strategy or None,
        "mapping_status": mapping_status or None,
        "mapping_confidence": mapping_confidence or None,
        "response_item_count": len(response_items),
        "requirement_count": len(requirements),
        "risk_count": len(risks),
        "evidence_count": len(evidence),
        "placeholder_count": len(placeholders),
        "llm_allowed": llm_allowed,
        "blockers": _unique_text(blockers),
        "material_gaps": _unique_text(material_gaps),
        "warnings": _unique_text(warnings),
    }


def _writing_plan_for_section(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    quality_profile: dict[str, Any],
) -> dict[str, Any]:
    section_type = str(section.get("section_type") or "business")
    target_output = _target_output_for_section(section, quality_profile)
    must_cover = [_clip(item.parsed_requirement or item.original_text, 120) for item in requirements[:8]]
    response_tasks = [_clip(item.response_title, 100) for item in response_items[:8]]
    risk_points = [
        {
            "risk_level": risk.risk_level,
            "risk_explanation": _clip(risk.risk_explanation or risk.original_text, 120),
            "suggested_action": _clip(risk.suggested_action, 120),
        }
        for risk in risks[:6]
    ]
    usable_facts = _usable_facts_for_plan(section, response_items, requirements, evidence)
    forbidden_claims = _forbidden_claims_for_section(section, risks)
    placeholder_texts = [placeholder["text"] for placeholder in placeholders[:8] if placeholder.get("text")]
    return {
        "target_output": target_output,
        "target_output_label": _target_output_label(target_output),
        "suggested_headings": _suggested_headings_for_section(section_type, section.get("section_title")),
        "must_cover_requirements": must_cover,
        "response_tasks": response_tasks,
        "risk_points": risk_points,
        "usable_facts": usable_facts,
        "forbidden_claims": forbidden_claims,
        "placeholders": placeholder_texts,
        "review_focus": _review_focus_for_section(section, quality_profile, risks),
    }


def _quality_result_for_section(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    quality_profile: dict[str, Any],
    writing_plan: dict[str, Any],
    content_markdown: str,
    *,
    generator_type: str,
) -> dict[str, Any]:
    checks = [
        _quality_check(
            "format_source",
            "格式来源",
            "pass" if section.get("outline_source") != "file_format_plan" or section.get("format_plan_review_status") == "confirmed" else "warn",
            "投标文件格式表已确认。" if section.get("format_plan_review_status") == "confirmed" else "格式表未确认，正式成稿前需确认。",
        ),
        _quality_check(
            "source_mapping",
            "素材映射",
            "pass" if quality_profile.get("mapping_status") in {None, "", "mapped"} and quality_profile.get("mapping_confidence") != "low" else "warn",
            "目录项已映射到可用素材。" if quality_profile.get("mapping_status") == "mapped" else "目录项素材映射不足。",
        ),
        _quality_check(
            "evidence",
            "证据追溯",
            "pass" if evidence else ("fail" if quality_profile.get("quality_status") == "ready_for_draft" else "warn"),
            "已保留原文证据。" if evidence else "缺少原文证据，正式成稿前需补充。",
        ),
        _quality_check(
            "requirement_coverage",
            "要求覆盖",
            "pass" if requirements or section.get("generation_strategy") in {"manual_upload", "manual_fill", "from_cost_quote"} else "warn",
            f"已关联 {len(requirements)} 条招标要求。" if requirements else "未关联招标要求，需人工确认覆盖范围。",
        ),
        _quality_check(
            "risk_preserved",
            "风险保留",
            "pass" if not risks or "## 风险与偏离处理" in content_markdown or section.get("draft_mode") in {"blocked", "review_note"} else "warn",
            f"已关联 {len(risks)} 条风险并保留复核入口。" if risks else "本章节未关联风险。",
        ),
        _quality_check(
            "manual_content_boundary",
            "生成边界",
            "pass" if _manual_content_boundary_ok(section) else "warn",
            "内容类型与生成方式匹配。" if _manual_content_boundary_ok(section) else "固定表单/附件/报价表不应自由生成正文。",
        ),
        _quality_check(
            "placeholders",
            "占位提示",
            "pass" if not quality_profile.get("material_gaps") or placeholders else "warn",
            "缺口已显式保留占位。" if placeholders else "如存在缺口，应保留可复核占位。",
        ),
    ]
    if generator_type == "llm":
        checks.extend(
            [
                _quality_check(
                    "llm_entry_gate",
                    "LLM入口",
                    "pass"
                    if quality_profile.get("quality_status") == "ready_for_draft"
                    and writing_plan.get("target_output") == "formal_draft"
                    else "fail",
                    "仅对已判定可生成正文的章节做 LLM 增强。"
                    if quality_profile.get("quality_status") == "ready_for_draft"
                    and writing_plan.get("target_output") == "formal_draft"
                    else "LLM 增强越过了质量画像或写作计划边界。",
                ),
                _quality_check(
                    "llm_content_shape",
                    "正文形态",
                    "pass" if _llm_content_shape_ok(content_markdown) else "warn",
                    "LLM 输出包含可复核 Markdown 正文。"
                    if _llm_content_shape_ok(content_markdown)
                    else "LLM 输出偏短或缺少章节结构，建议人工复核后再接受。",
                ),
                _quality_check(
                    "llm_forbidden_claims",
                    "禁写内容",
                    "fail" if _content_has_forbidden_claims(content_markdown) else "pass",
                    "未发现明显越权承诺。"
                    if not _content_has_forbidden_claims(content_markdown)
                    else "正文疑似包含保证中标、无条件承担全部风险等越权承诺。",
                ),
            ]
        )
    status = _quality_result_status(quality_profile, checks)
    return {
        "status": status,
        "status_label": _quality_result_status_label(status),
        "generator_type": generator_type,
        "summary": _quality_result_summary(status, quality_profile, checks),
        "checks": checks,
    }


def _enriched_generation_decision(
    generation_decision: dict[str, Any],
    quality_profile: dict[str, Any],
    writing_plan: dict[str, Any],
    quality_result: dict[str, Any] | None,
    *,
    section: dict[str, Any] | None = None,
    placeholders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = dict(generation_decision)
    if section:
        result["package_key"] = section.get("package_key")
        result["package_title"] = section.get("package_title")
        result["outline_source"] = section.get("outline_source")
    result["quality_profile"] = quality_profile
    result["writing_plan"] = writing_plan
    if quality_result:
        result["quality_result"] = quality_result
    llm_entry = _llm_entry_for_section(result, quality_profile, writing_plan, quality_result, section or {}, placeholders or [])
    result["llm_entry"] = llm_entry
    result["llm_eligible"] = bool(llm_entry.get("eligible"))
    return result


def _llm_entry_for_section(
    generation_decision: dict[str, Any],
    quality_profile: dict[str, Any],
    writing_plan: dict[str, Any],
    quality_result: dict[str, Any] | None,
    section: dict[str, Any],
    placeholders: list[dict[str, Any]],
) -> dict[str, Any]:
    blocked_reasons: list[str] = []
    if not generation_decision.get("llm_eligible"):
        blocked_reasons.append("目录骨架未判定为可进入 LLM 正文增强。")
    if section and section.get("draft_mode") != "formal":
        blocked_reasons.append("章节不是正式正文草稿模式。")
    if quality_profile.get("quality_status") != "ready_for_draft":
        blocked_reasons.append(quality_profile.get("quality_status_label") or "章节质量画像未达到可生成正文状态。")
    if writing_plan.get("target_output") != "formal_draft":
        blocked_reasons.append(writing_plan.get("target_output_label") or "写作计划目标不是正式正文草稿。")
    if quality_result and quality_result.get("status") in {"blocked", "needs_material"}:
        blocked_reasons.append(quality_result.get("summary") or "质检结果不允许进入 LLM 增强。")
    if placeholders:
        blocked_reasons.append("章节仍存在待补充占位符。")
    if section and not _manual_content_boundary_ok(section):
        blocked_reasons.append("固定表单、报价表或附件证明不允许自由生成正文。")
    eligible = not _unique_text(blocked_reasons)
    return {
        "eligible": eligible,
        "status": "eligible" if eligible else "blocked",
        "status_label": "可 LLM 增强" if eligible else "不可 LLM 增强",
        "action_label": "DeepSeek 润色正文",
        "provider": (settings.bidding_llm_provider or "deepseek").strip().lower(),
        "model": bidding_llm_model(),
        "prompt_version": BID_DRAFT_SECTION_LLM_PROMPT_VERSION,
        "quality_gate_status": quality_result.get("status") if quality_result else None,
        "quality_gate_status_label": quality_result.get("status_label") if quality_result else None,
        "blocked_reasons": _unique_text(blocked_reasons),
        "note": "LLM 只能基于规则草稿、写作计划和证据改写，不允许新增未提供的企业能力、人员、报价或承诺。",
    }


def _legacy_llm_entry(
    generation_decision: dict[str, Any],
    quality_profile: dict[str, Any],
    writing_plan: dict[str, Any],
    quality_result: dict[str, Any],
) -> dict[str, Any]:
    if not quality_profile or not writing_plan or not quality_result:
        return {
            "eligible": False,
            "status": "blocked",
            "status_label": "不可 LLM 增强",
            "action_label": "DeepSeek 润色正文",
            "provider": (settings.bidding_llm_provider or "deepseek").strip().lower(),
            "model": bidding_llm_model(),
            "prompt_version": BID_DRAFT_SECTION_LLM_PROMPT_VERSION,
            "quality_gate_status": None,
            "quality_gate_status_label": None,
            "blocked_reasons": ["旧草稿缺少章节质量画像、写作计划或质检结果，请先重新生成/升级草稿。"],
            "note": "LLM 增强需要先通过章节质量闸口。",
        }
    return _llm_entry_for_section(generation_decision, quality_profile, writing_plan, quality_result, {}, [])


def _warnings_from_quality_result(quality_result: dict[str, Any]) -> list[dict[str, Any]]:
    warnings = []
    for check in quality_result.get("checks") or []:
        if check.get("status") in {"warn", "fail"}:
            warnings.append({"level": check["status"], "message": f"{check.get('label')}: {check.get('message')}"})
    return warnings[:8]


def _warnings_from_semantic_quality(
    semantic_quality: dict[str, Any],
    acceptance_check: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for item in semantic_quality.get("unsupported_claims") or []:
        warnings.append({"level": "fail", "message": f"无证据表达: {item}"})
    for item in semantic_quality.get("missing_coverages") or []:
        warnings.append({"level": "warn", "message": f"覆盖不足: {item}"})
    if acceptance_check.get("status") == "blocked":
        warnings.append({"level": "blocking", "message": acceptance_check.get("summary") or "LLM增强稿接受前检查未通过。"})
    return warnings[:8]


def _build_content_markdown(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    quality_profile: dict[str, Any],
    writing_plan: dict[str, Any],
) -> str:
    lines = [
        f"# {section.get('section_title') or '投标书章节草稿'}",
        "",
        f"- 章节类型：{_section_type_label(section.get('section_type'))}",
        f"- 主责角色：{section.get('owner_role') or '-'}",
        f"- 草稿模式：{section.get('draft_mode_label') or section.get('draft_mode') or '-'}",
        f"- 生成方式：规则模板（{BID_DRAFT_SECTION_GENERATOR_MODEL}）",
    ]
    if section.get("outline_source") == "file_format_plan":
        lines.append(f"- 目录来源：投标文件格式确认表 / {section.get('package_title') or '-'}")
        if section.get("content_type_label") or section.get("generation_strategy"):
            lines.append(
                f"- 格式属性：{section.get('content_type_label') or section.get('content_type') or '-'} / {section.get('generation_strategy') or '-'}"
            )
        source_mapping = section.get("source_mapping") if isinstance(section.get("source_mapping"), dict) else {}
        if source_mapping:
            lines.append(f"- 映射结果：{source_mapping.get('reason') or source_mapping.get('status') or '-'}")
    if section.get("split_from_generic_title"):
        lines.append(f"- 拆分来源：{section.get('original_group_title') or '综合类章节'}")
        if section.get("split_reason"):
            lines.append(f"- 拆分理由：{section.get('split_reason')}")
    lines.append("")
    lines.extend(_quality_profile_lines(quality_profile))
    lines.extend(_writing_plan_lines(writing_plan))
    if section.get("draft_mode") in {"blocked", "review_note"}:
        lines.extend(_review_note_section_lines(section, warnings))
    else:
        lines.extend(_draft_body_lines(section, response_items, requirements, risks, placeholders, writing_plan))
    lines.extend(_source_lines(response_items, requirements, risks, evidence))
    return "\n".join(lines).strip() + "\n"


def _review_note_section_lines(section: dict[str, Any], warnings: list[dict[str, Any]]) -> list[str]:
    is_blocked = section.get("draft_mode") == "blocked"
    headline = (
        "本章节存在阻断事项，当前不生成可提交正文。请完成复核或决策后重新生成。"
        if is_blocked
        else "本章节需要先完成经营/预算/法务复核，当前生成复核说明，不直接生成可提交正文。"
    )
    reason_title = "## 阻断原因" if is_blocked else "## 复核原因"
    lines = [
        f"> {headline}",
        "",
        reason_title,
    ]
    if warnings:
        for item in warnings[:6]:
            lines.append(f"- {item.get('message')}")
    else:
        lines.append("- 存在高优先级、风险决策或未完成复核事项。")
    lines.extend(["", "## 待处理动作"])
    for item in section.get("missing_inputs") or ["请负责人完成该章节响应矩阵复核。"]:
        lines.append(f"- {item}")
    if not is_blocked:
        lines.extend(
            [
                "",
                "## 复核后可转正文口径",
                "- 若负责人确认可响应，可重新生成正式草稿或由 LLM 仅润色已确认内容。",
                "- 若负责人确认需要偏离、澄清或报价预留，应先回填响应矩阵处理动作后再生成正文。",
            ]
        )
    return lines


def _quality_profile_lines(profile: dict[str, Any]) -> list[str]:
    lines = [
        "## 章节质量画像",
        f"- 质量状态：{profile.get('quality_status_label') or profile.get('quality_status') or '-'}",
        f"- 素材情况：响应 {profile.get('response_item_count') or 0} 项，要求 {profile.get('requirement_count') or 0} 条，风险 {profile.get('risk_count') or 0} 条，证据 {profile.get('evidence_count') or 0} 条。",
        f"- LLM 处理：{'允许' if profile.get('llm_allowed') else '不允许'}",
    ]
    for item in profile.get("blockers") or []:
        lines.append(f"- 阻断：{item}")
    for item in profile.get("material_gaps") or []:
        lines.append(f"- 缺口：{item}")
    for item in profile.get("warnings") or []:
        lines.append(f"- 提醒：{item}")
    lines.append("")
    return lines


def _writing_plan_lines(plan: dict[str, Any]) -> list[str]:
    lines = [
        "## 写作计划",
        f"- 目标产出：{plan.get('target_output_label') or plan.get('target_output') or '-'}",
    ]
    headings = plan.get("suggested_headings") or []
    if headings:
        lines.append(f"- 建议小标题：{'、'.join(str(item) for item in headings[:8])}")
    if plan.get("must_cover_requirements"):
        lines.append("- 必须覆盖要求：")
        for item in plan["must_cover_requirements"][:6]:
            lines.append(f"  - {item}")
    if plan.get("response_tasks"):
        lines.append("- 响应矩阵任务：")
        for item in plan["response_tasks"][:6]:
            lines.append(f"  - {item}")
    if plan.get("risk_points"):
        lines.append("- 风险处理口径：")
        for item in plan["risk_points"][:5]:
            suffix = f"；建议：{item.get('suggested_action')}" if item.get("suggested_action") else ""
            lines.append(f"  - {item.get('risk_level') or '-'}：{item.get('risk_explanation') or '-'}{suffix}")
    if plan.get("forbidden_claims"):
        lines.append("- 禁止编造/误写：")
        for item in plan["forbidden_claims"][:5]:
            lines.append(f"  - {item}")
    lines.append("")
    return lines


def _draft_body_lines(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    placeholders: list[dict[str, Any]],
    writing_plan: dict[str, Any],
) -> list[str]:
    section_type = section.get("section_type") or "business"
    lines = [
        "## 响应立场",
        _response_position(section_type),
        "",
        "## 具体措施与说明",
    ]
    if writing_plan.get("suggested_headings"):
        lines.append(f"- 本章节建议按“{'、'.join(writing_plan['suggested_headings'][:5])}”组织正文，正式提交前可由负责人调整顺序。")
    for item in response_items[:8]:
        lines.append(f"- {_response_sentence(section_type, item)}")
    if not response_items:
        if section.get("outline_source") == "file_format_plan":
            lines.append(
                f"- 本章节来自甲方投标文件格式要求【{section.get('package_title') or '投标文件'}】，应按确认格式表准备对应内容。"
            )
            if section.get("generation_strategy") == "manual_upload":
                lines.append("- 该项以附件/证明材料为主，不由系统编造正文；请上传或绑定有效文件。")
            elif section.get("generation_strategy") == "from_cost_quote":
                lines.append("- 该项应接入报价清单、成本库和报价复核结果，正文仅保留报价说明占位。")
            elif section.get("generation_strategy") == "manual_fill":
                lines.append("- 该项属于固定表单或固定格式，请按甲方格式填写签章、日期、承诺或偏离内容。")
        else:
            lines.append("- 本章节暂无响应矩阵项，请人工补充章节内容。")

    if section_type == "technical":
        lines.extend(_technical_template_lines(section, response_items))

    if risks:
        lines.extend(["", "## 风险与偏离处理"])
        for risk in risks[:6]:
            action = risk.suggested_action or "请相关负责人复核风险边界和响应口径。"
            lines.append(f"- {risk.risk_explanation or risk.original_text} 处理建议：{action}")

    if requirements:
        lines.extend(["", "## 招标要求响应"])
        for requirement in requirements[:6]:
            lines.append(f"- 针对“{_clip(requirement.parsed_requirement or requirement.original_text, 90)}”，本章节按招标文件要求进行响应。")

    if placeholders:
        lines.extend(["", "## 待补充/待复核"])
        for placeholder in placeholders:
            lines.append(f"- {placeholder['text']}")
    return lines


def _technical_template_lines(section: dict[str, Any], response_items: list[TenderResponseItem]) -> list[str]:
    templates = _enterprise_technical_templates(section, response_items)
    lines = ["", BID_DRAFT_TECHNICAL_TEMPLATE_HEADING]
    for item in templates:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## 需项目负责人补实的信息",
            "- 【待补充：类似项目名称、规模、施工周期与甲方评价】",
            "- 【待补充：拟派项目经理、技术负责人、安全员等人员安排】",
        ]
    )
    return lines


def _enterprise_technical_templates(section: dict[str, Any], response_items: list[TenderResponseItem]) -> list[str]:
    text = " ".join([str(section.get("section_title") or ""), *(str(item.response_title or "") for item in response_items)])
    templates = [
        "结合装饰工程经验，按深化设计、材料确认、样板先行、分区施工、过程验收、成品保护组织实施。",
        "施工过程中设置质量、安全、进度、材料和文明施工责任人，形成日检查、周协调、节点验收机制。",
    ]
    if any(keyword in text for keyword in ("质量", "验收", "样板")):
        templates.append("质量控制采用样板引路、隐蔽验收、实测实量和整改闭环，关键材料进场前完成品牌、规格和样板确认。")
    if any(keyword in text for keyword in ("安全", "文明", "消防")):
        templates.append("安全文明施工按现场围挡、防火、临电、动火审批、垃圾清运和交叉作业协调要求执行。")
    if any(keyword in text for keyword in ("进度", "工期", "计划")):
        templates.append("进度管理采用总控计划、周计划和日协调机制，重点关注材料到场、交叉作业和验收移交节点。")
    return templates[:5]


def _source_lines(
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
) -> list[str]:
    lines = ["", "## 来源依据", "### 响应矩阵项"]
    if response_items:
        for item in response_items[:10]:
            lines.append(f"- {item.response_title}（{item.response_item_uuid}）")
    else:
        lines.append("- 无")
    lines.append("### 要求/风险")
    lines.append(f"- 关联要求：{len(requirements)} 条")
    lines.append(f"- 关联风险：{len(risks)} 条")
    lines.append("### 原文证据")
    if evidence:
        for item in evidence[:8]:
            location = " / ".join(str(part) for part in [item.get("source_file"), item.get("source_location")] if part)
            lines.append(f"- {location or '-'}：{item.get('original_text') or '-'}")
    else:
        lines.append("- 无")
    return lines


def _response_position(section_type: str) -> str:
    positions = {
        "business": "我方已充分理解招标文件的商务规则、递交要求和响应表要求，并将在投标文件中逐项响应。",
        "qualification": "我方将按招标文件要求准备企业资质、业绩、人员、证照及授权资料。",
        "technical": "我方将结合装饰工程施工组织、质量、安全、进度、材料和验收要求编制技术响应方案。",
        "pricing": "我方将按招标文件报价口径、清单边界和风险责任要求进行报价说明与复核。",
        "legal": "本章节用于提示合同偏离、责任边界和法务复核意见，正式提交前需完成法务确认。",
        "clarification": "本章节用于汇总需向招标人澄清或内部决策的问题，正式提交前需确认是否发出答疑。",
        "attachment": "我方将按招标文件清单准备附件、签章、密封和证明材料。",
    }
    return positions.get(section_type, "我方将按招标文件要求完成本章节响应。")


def _response_sentence(section_type: str, item: TenderResponseItem) -> str:
    text = item.response_note or item.source_text or item.response_title
    if section_type == "technical":
        return f"围绕“{item.response_title}”，编制对应施工措施，并保留原文要求：{_clip(text, 120)}"
    if section_type == "pricing":
        return f"围绕“{item.response_title}”，由预算复核报价口径和价格风险：{_clip(text, 120)}"
    if section_type == "legal":
        return f"围绕“{item.response_title}”，由法务确认责任边界和偏离意见：{_clip(text, 120)}"
    if section_type == "qualification":
        return f"围绕“{item.response_title}”，准备并绑定对应资格证明材料：{_clip(text, 120)}"
    if section_type == "clarification":
        return f"围绕“{item.response_title}”，判断是否形成标前答疑问题：{_clip(text, 120)}"
    return f"围绕“{item.response_title}”，在投标文件中作出明确响应：{_clip(text, 120)}"


def _quality_status_label(value: str) -> str:
    labels = {
        "ready_for_draft": "可生成正文",
        "needs_material": "需补材料",
        "needs_mapping_review": "需确认映射",
        "blocked_by_risk": "风险阻断",
    }
    return labels.get(value, value or "-")


def _target_output_for_section(section: dict[str, Any], quality_profile: dict[str, Any]) -> str:
    if quality_profile.get("quality_status") == "blocked_by_risk":
        return "review_note"
    strategy = str(section.get("generation_strategy") or "")
    content_type = str(section.get("content_type") or "")
    if strategy == "from_cost_quote" or content_type == "pricing_table":
        return "pricing_review_note"
    if strategy == "manual_upload" or content_type in {"attachment_proof", "qualification_attachment"}:
        return "material_checklist"
    if strategy == "manual_fill" or content_type == "fixed_form":
        return "fixed_form_instruction"
    if quality_profile.get("quality_status") != "ready_for_draft":
        return "placeholder_draft"
    return "formal_draft"


def _target_output_label(value: str) -> str:
    labels = {
        "formal_draft": "正式正文草稿",
        "placeholder_draft": "占位草稿",
        "fixed_form_instruction": "固定表单填写说明",
        "pricing_review_note": "报价复核说明",
        "material_checklist": "附件/证明材料清单",
        "review_note": "复核说明",
    }
    return labels.get(value, value or "-")


def _suggested_headings_for_section(section_type: str, title: str | None) -> list[str]:
    text = str(title or "")
    if section_type == "technical":
        headings = ["编制依据", "施工组织安排", "质量控制措施", "安全文明施工", "进度保障措施"]
        if any(keyword in text for keyword in ("材料", "品牌", "样板")):
            headings.append("材料与样板管理")
        if "成品保护" in text:
            headings.append("成品保护措施")
        return headings[:7]
    if section_type == "pricing":
        return ["报价范围", "计价口径", "风险边界", "待预算确认事项"]
    if section_type == "legal":
        return ["合同条款响应", "风险边界", "偏离/澄清建议", "待决策事项"]
    if section_type == "qualification":
        return ["资料清单", "有效性检查", "待补附件"]
    if section_type == "attachment":
        return ["附件名称", "来源文件", "签章/有效期检查", "待补资料"]
    if section_type == "clarification":
        return ["疑问事项", "涉及条款", "建议提问文本", "内部决策"]
    return ["响应立场", "具体响应", "签章/格式要求", "待补资料"]


def _usable_facts_for_plan(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    evidence: list[dict[str, Any]],
) -> list[str]:
    facts: list[str] = []
    for item in response_items[:4]:
        facts.append(_clip(item.response_note or item.source_text or item.response_title, 120))
    for item in requirements[:4]:
        facts.append(_clip(item.parsed_requirement or item.original_text, 120))
    for item in evidence[:4]:
        facts.append(_clip(item.get("original_text"), 120))
    return _unique_text(facts)[:8]


def _forbidden_claims_for_section(section: dict[str, Any], risks: list[TenderRisk]) -> list[str]:
    claims = [
        "不得编造企业人员、业绩、证照、附件或签章状态。",
        "不得编造报价金额、优惠、工期压缩或甲方未确认的承诺。",
    ]
    section_type = str(section.get("section_type") or "")
    strategy = str(section.get("generation_strategy") or "")
    content_type = str(section.get("content_type") or "")
    if section_type in {"legal", "pricing"} or risks:
        claims.append("不得把未决风险、合同偏离或报价预留写成无条件接受。")
    if strategy in {"manual_upload", "manual_fill", "from_cost_quote"} or content_type in {"fixed_form", "pricing_table", "attachment_proof", "qualification_attachment"}:
        claims.append("不得把固定表单、报价表或附件证明自由改写成正式正文。")
    return _unique_text(claims)


def _review_focus_for_section(section: dict[str, Any], quality_profile: dict[str, Any], risks: list[TenderRisk]) -> list[str]:
    focus = []
    if quality_profile.get("material_gaps"):
        focus.extend(quality_profile["material_gaps"][:3])
    if quality_profile.get("warnings"):
        focus.extend(quality_profile["warnings"][:3])
    if risks:
        focus.append("确认风险处理口径、偏离意见或报价预留是否已获负责人确认。")
    if not focus:
        focus.append("复核正文是否覆盖招标要求且未超出证据范围。")
    return _unique_text(focus)[:6]


def _quality_check(code: str, label: str, status: str, message: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "message": message}


def _manual_content_boundary_ok(section: dict[str, Any]) -> bool:
    content_type = str(section.get("content_type") or "")
    strategy = str(section.get("generation_strategy") or "")
    if content_type in {"fixed_form", "pricing_table", "attachment_proof", "qualification_attachment"}:
        return section.get("draft_mode") != "formal"
    if strategy in {"manual_upload", "manual_fill", "from_cost_quote"}:
        return section.get("draft_mode") != "formal"
    return True


def _quality_result_status(quality_profile: dict[str, Any], checks: list[dict[str, str]]) -> str:
    if quality_profile.get("quality_status") == "blocked_by_risk" or any(check["status"] == "fail" for check in checks):
        return "blocked"
    if quality_profile.get("quality_status") == "needs_material":
        return "needs_material"
    if quality_profile.get("quality_status") == "needs_mapping_review" or any(check["status"] == "warn" for check in checks):
        return "needs_review"
    return "pass"


def _quality_result_status_label(value: str) -> str:
    labels = {
        "pass": "通过",
        "needs_review": "需复核",
        "needs_material": "需补材料",
        "blocked": "阻断",
    }
    return labels.get(value, value or "-")


def _quality_result_summary(status: str, quality_profile: dict[str, Any], checks: list[dict[str, str]]) -> str:
    issue_count = len([check for check in checks if check.get("status") in {"warn", "fail"}])
    if status == "pass":
        return "章节满足当前规则生成质量要求，可进入人工复核或 LLM 润色。"
    if status == "blocked":
        return "章节存在阻断或证据缺失，不应作为可提交正文。"
    if status == "needs_material":
        return f"章节仍需补充材料或外部链路结果，共 {issue_count} 个质检提醒。"
    return f"章节需人工复核映射、风险或占位，共 {issue_count} 个质检提醒。"


def _llm_diff_summary(base_content: str, llm_content: str, *, base_version: BidDraftSectionVersion | None) -> dict[str, Any]:
    base_lines = _meaningful_lines(base_content)
    llm_lines = _meaningful_lines(llm_content)
    base_line_set = set(base_lines)
    llm_line_set = set(llm_lines)
    added_lines = [line for line in llm_lines if line not in base_line_set]
    removed_lines = [line for line in base_lines if line not in llm_line_set]
    base_headings = _markdown_headings(base_content)
    llm_headings = _markdown_headings(llm_content)
    added_headings = [item for item in llm_headings if item not in base_headings]
    removed_headings = [item for item in base_headings if item not in llm_headings]
    base_terms = _keyword_terms(base_content)
    llm_terms = _keyword_terms(llm_content)
    added_terms = [item for item in llm_terms if item not in base_terms]
    removed_terms = [item for item in base_terms if item not in llm_terms]
    risk_markers = ["## 风险与偏离处理", "## 待补充", "【待补充", "待处理动作", "## 来源依据"]
    removed_risk_markers = [marker for marker in risk_markers if marker in base_content and marker not in llm_content]
    return {
        "base_version_no": base_version.version_no if base_version else None,
        "base_change_type": base_version.change_type if base_version else "transient_rule_content",
        "base_generator_model": base_version.generator_model if base_version else BID_DRAFT_SECTION_GENERATOR_MODEL,
        "added_line_count": len(added_lines),
        "removed_line_count": len(removed_lines),
        "preserved_heading_count": len([item for item in base_headings if item in llm_headings]),
        "added_headings": added_headings[:8],
        "removed_headings": removed_headings[:8],
        "added_keywords": added_terms[:12],
        "removed_keywords": removed_terms[:12],
        "added_line_samples": added_lines[:5],
        "removed_line_samples": removed_lines[:5],
        "risk_removed": bool(removed_risk_markers),
        "removed_risk_markers": removed_risk_markers,
    }


def _llm_semantic_quality(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    writing_plan: dict[str, Any],
    base_content: str,
    llm_content: str,
    diff_summary: dict[str, Any],
) -> dict[str, Any]:
    corpus = "\n".join(
        [
            base_content,
            *(item.response_title or "" for item in response_items),
            *(item.response_note or "" for item in response_items),
            *(item.source_text or "" for item in response_items),
            *(item.parsed_requirement or item.original_text or "" for item in requirements),
            *(item.risk_explanation or item.original_text or "" for item in risks),
            *(str(item.get("original_text") or "") for item in evidence),
        ]
    )
    response_coverages = [
        {
            "title": item.response_title,
            "covered": _text_has_overlap(llm_content, item.response_title, item.response_note, item.source_text),
        }
        for item in response_items[:12]
    ]
    requirement_coverages = [
        {
            "requirement": _clip(item.parsed_requirement or item.original_text, 120),
            "covered": _text_has_overlap(llm_content, item.parsed_requirement, item.original_text),
        }
        for item in requirements[:12]
    ]
    risk_coverages = [
        {
            "risk": _clip(item.risk_explanation or item.original_text, 120),
            "covered": _text_has_overlap(llm_content, item.risk_explanation, item.original_text) or any(
                marker in llm_content for marker in ["风险", "偏离", "复核", "待确认"]
            ),
        }
        for item in risks[:8]
    ]
    plan_coverages = [
        {
            "item": _clip(str(item), 120),
            "covered": _text_has_overlap(llm_content, str(item)),
        }
        for item in (writing_plan.get("must_cover_requirements") or [])[:8]
    ]
    missing_coverages = [
        item["title"] for item in response_coverages if item.get("title") and not item.get("covered")
    ]
    missing_coverages.extend(item["requirement"] for item in requirement_coverages if item.get("requirement") and not item.get("covered"))
    missing_coverages.extend(item["risk"] for item in risk_coverages if item.get("risk") and not item.get("covered"))
    missing_coverages.extend(item["item"] for item in plan_coverages if item.get("item") and not item.get("covered"))
    unsupported_claims = _unsupported_claims(llm_content, corpus)
    warnings: list[str] = []
    if diff_summary.get("risk_removed"):
        warnings.append("LLM稿疑似删除了风险、偏离、待补充或来源依据标记。")
    if unsupported_claims:
        warnings.append("LLM稿存在疑似无证据或越权表达。")
    if missing_coverages:
        warnings.append("LLM稿疑似遗漏部分响应项、招标要求或风险点。")
    status = "blocked" if unsupported_claims or diff_summary.get("risk_removed") else ("needs_review" if missing_coverages or warnings else "pass")
    return {
        "status": status,
        "status_label": _semantic_quality_status_label(status),
        "summary": _semantic_quality_summary(status, missing_coverages, unsupported_claims, diff_summary),
        "response_coverages": response_coverages,
        "requirement_coverages": requirement_coverages,
        "risk_coverages": risk_coverages,
        "plan_coverages": plan_coverages,
        "missing_coverages": _unique_text(missing_coverages)[:12],
        "unsupported_claims": unsupported_claims,
        "warnings": _unique_text(warnings),
    }


def _llm_acceptance_check(
    diff_summary: dict[str, Any],
    semantic_quality: dict[str, Any],
    quality_result: dict[str, Any],
    *,
    content_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    content_evidence = content_evidence or {}
    if quality_result.get("status") in {"blocked", "needs_material"}:
        blockers.append(quality_result.get("summary") or "生成后质检未通过。")
    if semantic_quality.get("status") == "blocked":
        blockers.append(semantic_quality.get("summary") or "证据对齐质检阻断。")
    if content_evidence.get("status") == "blocked":
        blockers.append(content_evidence.get("summary") or "段落级证据追溯阻断。")
    if diff_summary.get("risk_removed"):
        blockers.append("LLM稿疑似删除风险、偏离、待补充或来源依据标记。")
    for item in semantic_quality.get("unsupported_claims") or []:
        blockers.append(f"疑似无证据表达：{item}")
    for item in content_evidence.get("unsupported_blocks") or []:
        blockers.append(f"无依据段落：{_clip(item.get('block_text'), 80)}")
    if quality_result.get("status") == "needs_review":
        warnings.append(quality_result.get("summary") or "生成后质检需要人工复核。")
    if content_evidence.get("status") == "needs_review":
        warnings.append(content_evidence.get("summary") or "段落级证据追溯需要人工复核。")
    for item in semantic_quality.get("missing_coverages") or []:
        warnings.append(f"疑似覆盖不足：{item}")
    for item in content_evidence.get("missing_coverages") or []:
        warnings.append(f"未覆盖证据项：{item}")
    status = "blocked" if blockers else ("needs_review" if warnings else "pass")
    return {
        "status": status,
        "status_label": _acceptance_status_label(status),
        "summary": _acceptance_summary(status, blockers, warnings),
        "blockers": _unique_text(blockers)[:8],
        "warnings": _unique_text(warnings)[:8],
        "can_accept": status != "blocked",
    }


def _semantic_quality_from_content_evidence(content_evidence: dict[str, Any]) -> dict[str, Any]:
    status = content_evidence.get("status") or "needs_review"
    return {
        "status": status,
        "status_label": _semantic_quality_status_label(status),
        "summary": content_evidence.get("summary") or "已完成段落级证据追溯。",
        "response_coverages": [],
        "requirement_coverages": [],
        "risk_coverages": [],
        "plan_coverages": [],
        "missing_coverages": content_evidence.get("missing_coverages") or [],
        "unsupported_claims": content_evidence.get("unsupported_claims") or [],
        "warnings": content_evidence.get("warnings") or [],
        "content_evidence_status": status,
        "content_evidence_summary": content_evidence.get("summary"),
    }


def _content_evidence_analysis(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    writing_plan: dict[str, Any],
    content_markdown: str,
    *,
    review_source: str,
    diff_summary: dict[str, Any],
) -> dict[str, Any]:
    content = str(content_markdown or "")
    corpus = "\n".join(
        [
            *(item.response_title or "" for item in response_items),
            *(item.response_note or "" for item in response_items),
            *(item.source_text or "" for item in response_items),
            *(item.parsed_requirement or item.original_text or "" for item in requirements),
            *(item.risk_explanation or item.original_text or "" for item in risks),
            *(str(item.get("original_text") or "") for item in evidence),
            *(str(item) for item in writing_plan.get("usable_facts") or []),
        ]
    )
    evidence_sources = _content_evidence_sources(response_items, requirements, risks, evidence, writing_plan)
    blocks = _content_blocks_for_evidence(content)
    unsupported_claims = _unsupported_claims(content, corpus)
    block_results: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        claim_hits = _unsupported_claims(block["block_text"], corpus)
        best = _best_evidence_source(block["block_text"], evidence_sources)
        warnings: list[str] = []
        if claim_hits:
            evidence_status = "unsupported"
            warnings.extend(f"疑似无证据或越权表达：{item}" for item in claim_hits)
        elif best:
            evidence_status = "supported"
        else:
            evidence_status = "needs_review"
            warnings.append("未匹配到明确来源证据，请人工确认该段是否应保留。")
        block_results.append(
            {
                "block_index": index,
                "block_type": block["block_type"],
                "heading": block.get("heading"),
                "block_text": _clip(block["block_text"], 220),
                "evidence_status": evidence_status,
                "evidence_status_label": _evidence_status_label(evidence_status),
                "confidence": round(float(best.get("confidence", 0.0)) if best else 0.0, 2),
                "supporting_evidence": best,
                "warnings": _unique_text(warnings),
            }
        )
    required_items = _required_content_coverages(response_items, requirements, risks, writing_plan)
    coverage_results = []
    for item in required_items:
        covered = _text_has_overlap(content, item.get("title"), item.get("text"))
        coverage_results.append({**item, "covered": bool(covered)})
    missing_coverages = [
        item.get("title") or _clip(item.get("text"), 100)
        for item in coverage_results
        if not item.get("covered")
    ]
    unsupported_blocks = [item for item in block_results if item.get("evidence_status") == "unsupported"]
    review_blocks = [item for item in block_results if item.get("evidence_status") == "needs_review"]
    warnings = []
    if diff_summary.get("risk_removed"):
        warnings.append("正文疑似删除了风险、偏离、待补充或来源依据标记。")
    if missing_coverages:
        warnings.append("正文疑似存在必覆盖项遗漏。")
    if review_blocks:
        warnings.append("部分段落未匹配到明确证据。")
    if unsupported_claims or unsupported_blocks or diff_summary.get("risk_removed"):
        status = "blocked"
    elif missing_coverages or review_blocks:
        status = "needs_review"
    else:
        status = "pass"
    covered_count = len([item for item in coverage_results if item.get("covered")])
    summary = _content_evidence_summary(
        status,
        block_count=len(block_results),
        supported_count=len([item for item in block_results if item.get("evidence_status") == "supported"]),
        review_count=len(review_blocks),
        missing_count=len(missing_coverages),
    )
    return {
        "version": "biz4b_content_evidence_v1",
        "review_source": review_source,
        "status": status,
        "status_label": _content_evidence_status_label(status),
        "summary": summary,
        "block_count": len(block_results),
        "supported_block_count": len([item for item in block_results if item.get("evidence_status") == "supported"]),
        "needs_review_block_count": len(review_blocks),
        "unsupported_block_count": len(unsupported_blocks),
        "coverage_summary": {
            "required_count": len(coverage_results),
            "covered_count": covered_count,
            "missing_count": len(missing_coverages),
        },
        "blocks": block_results[:30],
        "coverage_items": coverage_results[:30],
        "missing_coverages": _unique_text(missing_coverages)[:12],
        "unsupported_claims": unsupported_claims,
        "unsupported_blocks": unsupported_blocks[:8],
        "warnings": _unique_text(warnings),
        "section_title": section.get("section_title"),
    }


def _content_evidence_sources(
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    writing_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in response_items[:20]:
        sources.append(
            {
                "source_type": "response_item",
                "source_id": item.response_item_uuid,
                "title": item.response_title,
                "text": " ".join(part for part in [item.response_title, item.response_note, item.source_text] if part),
                "source_file": None,
                "source_location": None,
            }
        )
    for item in requirements[:20]:
        sources.append(
            {
                "source_type": "requirement",
                "source_id": item.id,
                "title": _clip(item.parsed_requirement or item.original_text, 80),
                "text": item.parsed_requirement or item.original_text,
                "source_file": item.source_file,
                "source_location": item.source_location,
            }
        )
    for item in risks[:16]:
        sources.append(
            {
                "source_type": "risk",
                "source_id": item.id,
                "title": _clip(item.risk_explanation or item.original_text, 80),
                "text": " ".join(part for part in [item.risk_explanation, item.suggested_action, item.original_text] if part),
                "source_file": item.source_file,
                "source_location": item.source_location,
            }
        )
    for index, item in enumerate(evidence[:24], start=1):
        if not isinstance(item, dict):
            continue
        sources.append(
            {
                "source_type": item.get("source_kind") or "evidence",
                "source_id": item.get("response_item_uuid") or item.get("requirement_id") or item.get("risk_id") or index,
                "title": item.get("response_title") or _clip(item.get("original_text"), 80),
                "text": item.get("original_text") or "",
                "source_file": item.get("source_file"),
                "source_location": item.get("source_location"),
            }
        )
    for index, item in enumerate(writing_plan.get("usable_facts") or [], start=1):
        sources.append(
            {
                "source_type": "writing_plan",
                "source_id": f"usable_fact:{index}",
                "title": _clip(str(item), 80),
                "text": str(item),
                "source_file": None,
                "source_location": None,
            }
        )
    return [item for item in sources if str(item.get("text") or item.get("title") or "").strip()]


def _content_blocks_for_evidence(content: str) -> list[dict[str, str]]:
    internal_headings = {"章节质量画像", "写作计划", "规则质检结果", "来源依据"}
    metadata_prefixes = (
        "- 章节类型",
        "- 主责角色",
        "- 草稿模式",
        "- 生成方式",
        "- 目录来源",
        "- 格式属性",
        "- 映射结果",
        "- 拆分来源",
        "- 拆分理由",
    )
    blocks: list[dict[str, str]] = []
    current_heading = ""
    skip_section = False
    for raw_line in str(content or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line.startswith("#"):
            current_heading = re.sub(r"^#+\s*", "", line).strip()
            skip_section = current_heading in internal_headings
            continue
        if skip_section or line.startswith(metadata_prefixes):
            continue
        if line.startswith("|") or line.startswith("---"):
            continue
        block_type = "bullet" if line.startswith(("-", "*", "1.", "2.", "3.")) else "paragraph"
        text = re.sub(r"^[-*]\s*", "", line)
        text = re.sub(r"^\d+[.)、]\s*", "", text).strip()
        if len(text) < 10:
            continue
        blocks.append({"block_type": block_type, "heading": current_heading, "block_text": text})
    return blocks[:60]


def _best_evidence_source(block_text: str, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for source in sources:
        source_text = " ".join(str(source.get(key) or "") for key in ("title", "text"))
        score = _overlap_score(block_text, source_text)
        if score > best_score:
            best_score = score
            best = source
    if not best or best_score < 0.18:
        return None
    return {
        "source_type": best.get("source_type"),
        "source_type_label": _evidence_source_type_label(best.get("source_type")),
        "source_id": best.get("source_id"),
        "title": best.get("title") or _clip(best.get("text"), 80),
        "source_file": best.get("source_file"),
        "source_location": best.get("source_location"),
        "confidence": min(best_score, 0.98),
    }


def _overlap_score(content: str, source_text: str) -> float:
    content_text = str(content or "")
    source = str(source_text or "")
    if not content_text.strip() or not source.strip():
        return 0.0
    if source in content_text or content_text in source:
        return 0.92
    content_terms = set(_keyword_terms(content_text))
    source_terms = set(_keyword_terms(source))
    if not content_terms or not source_terms:
        clipped = _clip(source, 16)
        return 0.65 if clipped and clipped in content_text else 0.0
    common = [term for term in source_terms if term in content_text or term in content_terms]
    if not common:
        return 0.0
    return min(0.9, len(common) / max(1, min(len(source_terms), 6)))


def _required_content_coverages(
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    writing_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in response_items[:12]:
        items.append(
            {
                "source_type": "response_item",
                "source_id": item.response_item_uuid,
                "title": item.response_title,
                "text": " ".join(part for part in [item.response_title, item.response_note, item.source_text] if part),
            }
        )
    for item in requirements[:12]:
        text = item.parsed_requirement or item.original_text
        items.append({"source_type": "requirement", "source_id": item.id, "title": _clip(text, 100), "text": text})
    for item in risks[:8]:
        text = item.risk_explanation or item.original_text
        items.append({"source_type": "risk", "source_id": item.id, "title": _clip(text, 100), "text": text})
    for index, item in enumerate(writing_plan.get("must_cover_requirements") or [], start=1):
        items.append({"source_type": "writing_plan", "source_id": f"must:{index}", "title": _clip(str(item), 100), "text": str(item)})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = f"{item.get('source_type')}:{item.get('source_id')}:{item.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _warnings_from_content_evidence(content_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if content_evidence.get("status") == "blocked":
        warnings.append({"level": "blocking", "message": content_evidence.get("summary") or "段落证据追溯未通过。"})
    elif content_evidence.get("status") == "needs_review":
        warnings.append({"level": "warn", "message": content_evidence.get("summary") or "段落证据追溯需要人工复核。"})
    for item in content_evidence.get("missing_coverages") or []:
        warnings.append({"level": "warn", "message": f"段落证据未覆盖: {item}"})
    return warnings[:8]


def _content_evidence_status_label(status: str) -> str:
    labels = {
        "pass": "段落证据通过",
        "needs_review": "段落需复核",
        "blocked": "段落证据阻断",
    }
    return labels.get(status, status or "-")


def _content_evidence_summary(
    status: str,
    *,
    block_count: int,
    supported_count: int,
    review_count: int,
    missing_count: int,
) -> str:
    if status == "pass":
        return f"共 {block_count} 个正文段落，{supported_count} 个已匹配来源证据，未发现阻断项。"
    if status == "blocked":
        return "段落级证据追溯发现越权表达、无依据敏感内容或风险/来源标记删除。"
    return f"共 {block_count} 个正文段落，{review_count} 个需人工确认，{missing_count} 个必覆盖项疑似遗漏。"


def _evidence_status_label(status: str) -> str:
    labels = {
        "supported": "已支撑",
        "needs_review": "需复核",
        "unsupported": "无依据",
    }
    return labels.get(status, status or "-")


def _evidence_source_type_label(value: Any) -> str:
    labels = {
        "response_item": "响应矩阵",
        "requirement": "招标要求",
        "risk": "风险卡",
        "file_format_plan": "格式确认表",
        "enterprise_profile": "企业资料库",
        "material_requirement": "资料补齐清单",
        "writing_plan": "写作计划",
        "evidence": "来源证据",
    }
    return labels.get(str(value or ""), str(value or "来源证据"))


def _meaningful_lines(content: str) -> list[str]:
    lines: list[str] = []
    for line in str(content or "").splitlines():
        text = re.sub(r"\s+", " ", line).strip()
        if not text or len(text) < 4:
            continue
        if text.startswith("- 质检") or text.startswith("## 规则质检结果"):
            continue
        lines.append(text)
    return _unique_text(lines)


def _markdown_headings(content: str) -> list[str]:
    headings = []
    for line in str(content or "").splitlines():
        text = line.strip()
        if text.startswith("#"):
            headings.append(re.sub(r"^#+\s*", "", text).strip())
    return _unique_text(headings)


def _keyword_terms(content: str) -> list[str]:
    chunks = re.split(r"[\s，。；、：:,.!?！？（）()\[\]【】#\-]+", str(content or ""))
    stopwords = {"本章节", "招标文件", "投标文件", "进行", "要求", "响应", "说明", "正文", "草稿"}
    terms = []
    for chunk in chunks:
        text = chunk.strip()
        if len(text) < 2 or len(text) > 24 or text in stopwords:
            continue
        if text.isdigit():
            continue
        terms.append(text)
    return _unique_text(terms)


def _text_has_overlap(content: str, *sources: Any) -> bool:
    content_text = str(content or "")
    source_text = " ".join(str(item or "") for item in sources if item)
    if not source_text.strip():
        return True
    terms = _keyword_terms(source_text)
    if not terms:
        clipped = _clip(source_text, 20)
        return bool(clipped and clipped in content_text)
    return any(term in content_text for term in terms[:8])


def _unsupported_claims(content: str, corpus: str) -> list[str]:
    text = re.sub(r"\s+", "", str(content or ""))
    corpus_text = re.sub(r"\s+", "", str(corpus or ""))
    direct_forbidden = ["保证中标", "确保中标", "最低价中标", "无条件接受全部风险", "无条件承担全部风险"]
    claims = [item for item in direct_forbidden if item in text]
    sensitive_terms = ["类似业绩", "项目经理", "一级建造师", "工程师", "证书编号", "人民币", "万元", "日历天", "确保工期", "保证工期"]
    for term in sensitive_terms:
        if term in text and term not in corpus_text:
            claims.append(term)
    return _unique_text(claims)[:10]


def _semantic_quality_status_label(status: str) -> str:
    labels = {
        "pass": "证据对齐通过",
        "needs_review": "需人工复核",
        "blocked": "证据对齐阻断",
    }
    return labels.get(status, status or "-")


def _semantic_quality_summary(
    status: str,
    missing_coverages: list[str],
    unsupported_claims: list[str],
    diff_summary: dict[str, Any],
) -> str:
    if status == "pass":
        return "LLM增强稿未发现明显新增无证据内容，关键素材仍有覆盖。"
    if status == "blocked":
        if unsupported_claims:
            return f"LLM增强稿存在 {len(unsupported_claims)} 个疑似无证据或越权表达。"
        if diff_summary.get("risk_removed"):
            return "LLM增强稿疑似删除了风险、偏离、待补充或来源依据标记。"
    return f"LLM增强稿仍需人工复核，疑似遗漏 {len(missing_coverages)} 个覆盖项。"


def _acceptance_status_label(status: str) -> str:
    labels = {
        "pass": "可接受",
        "needs_review": "接受前需复核",
        "blocked": "禁止直接接受",
    }
    return labels.get(status, status or "-")


def _acceptance_summary(status: str, blockers: list[str], warnings: list[str]) -> str:
    if status == "pass":
        return "接受前检查未发现阻断项，可由负责人复核后接受。"
    if status == "blocked":
        return f"接受前检查存在 {len(blockers)} 个阻断项，请修改或重新生成后再接受。"
    return f"接受前检查存在 {len(warnings)} 个提醒项，负责人需复核后接受。"


def _llm_content_shape_ok(content: str) -> bool:
    text = str(content or "").strip()
    if len(text) < 120:
        return False
    return "#" in text or "\n-" in text or "。" in text


def _content_has_forbidden_claims(content: str) -> bool:
    text = re.sub(r"\s+", "", str(content or ""))
    forbidden_patterns = [
        "保证中标",
        "确保中标",
        "最低价中标",
        "无条件接受全部风险",
        "无条件承担全部风险",
        "自行承担全部风险",
    ]
    return any(pattern in text for pattern in forbidden_patterns)


def _append_quality_result(content: str, quality_result: dict[str, Any]) -> str:
    cleaned = content.strip()
    if "## 规则质检结果" in cleaned:
        cleaned = re.sub(r"\n## 规则质检结果\n.*$", "", cleaned, flags=re.S).strip()
    lines = [
        cleaned,
        "",
        "## 规则质检结果",
        f"- 质检状态：{quality_result.get('status_label') or quality_result.get('status') or '-'}",
        f"- 质检摘要：{quality_result.get('summary') or '-'}",
    ]
    for check in quality_result.get("checks") or []:
        lines.append(f"- {check.get('label')}：{check.get('status')}；{check.get('message')}")
    return "\n".join(lines).strip() + "\n"


async def _build_llm_content_markdown(
    section: dict[str, Any],
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    evidence: list[dict[str, Any]],
    *,
    rule_content: str,
    generation_context: dict[str, Any],
    username: str | None,
    trace_id: str | None,
) -> str:
    provider = (settings.bidding_llm_provider or "deepseek").strip().lower()
    model = bidding_llm_model()
    if provider != "deepseek":
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_PROVIDER_NOT_SUPPORTED")
    if not (settings.deepseek_api_key or "").strip():
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_NOT_CONFIGURED")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是装饰工程投标文件章节正文助手。只处理系统已经判定可直接生成正文的章节。"
                    "必须基于输入的规则草稿、响应矩阵和证据改写，不得编造企业人员、业绩、报价金额或招标文件没有的信息。"
                    "必须遵守写作计划和禁写内容，不能把待复核事项写成确定承诺。"
                    "输出只写该章节 Markdown 正文，保留必要的风险/复核提示和来源依据。输出严格 JSON。"
                ),
            },
            {
                "role": "system",
                "content": (
                    "避免模板化复述：仅保留本章必需内容，不得用目录编号复述任务，不得重复通用开场白、收尾承诺或费用已包含类句子；"
                    "优先把抽象表态改为责任岗位、动作、频率、验收依据和记录。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt_version": BID_DRAFT_SECTION_LLM_PROMPT_VERSION,
                        "task": "single_bid_section_markdown",
                        "section": {
                            "section_key": section.get("section_key"),
                            "section_title": section.get("section_title"),
                            "section_type": section.get("section_type"),
                            "owner_role": section.get("owner_role"),
                            "generation_decision": generation_context,
                            "source_mapping": section.get("source_mapping") or {},
                        },
                        "quality_profile": generation_context.get("quality_profile") or {},
                        "writing_plan": generation_context.get("writing_plan") or {},
                        "quality_result": generation_context.get("quality_result") or {},
                        "document_anti_repetition": generation_context.get("document_anti_repetition") or {},
                        "rule_content_markdown": rule_content,
                        "response_items": [
                            {
                                "title": item.response_title,
                                "category": item.response_category,
                                "action": item.response_action,
                                "risk_level": item.risk_level,
                                "status": item.status,
                                "response_note": item.response_note,
                                "source_text": _clip(item.source_text, 500),
                            }
                            for item in response_items[:12]
                        ],
                        "requirements": [
                            {
                                "parsed_requirement": _clip(item.parsed_requirement, 300),
                                "original_text": _clip(item.original_text, 300),
                                "source_location": item.source_location,
                            }
                            for item in requirements[:8]
                        ],
                        "risks": [
                            {
                                "risk_level": item.risk_level,
                                "risk_explanation": _clip(item.risk_explanation, 300),
                                "suggested_action": _clip(item.suggested_action, 300),
                                "is_blocking": bool(item.is_blocking),
                            }
                            for item in risks[:8]
                        ],
                        "evidence": evidence[:8],
                        "constraints": [
                            "不得新增输入中没有的企业业绩、人员姓名、证书编号、报价金额、工期压缩承诺。",
                            "不得删除风险、偏离、待复核、待补充事项。",
                            "不得把占位符或复核事项改写成已经完成。",
                            "如招标要求或证据不足，应保留可人工处理的待补充说明。",
                        ],
                        "output_schema": {"content_markdown": "完整 Markdown 章节正文"},
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="bidding_single_section_draft",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key.strip()}"},
        timeout=settings.bidding_llm_timeout_seconds,
        username=username,
        trace_id=trace_id,
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_FAILED")
    try:
        response_payload = response.json()
        content = response_payload["choices"][0]["message"]["content"]
    except Exception as exc:
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_BAD_RESPONSE") from exc
    parsed = _extract_json_object(content)
    markdown = str(parsed.get("content_markdown") or "").strip()
    if not markdown:
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_EMPTY_CONTENT")
    return markdown + "\n"


def _extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_BAD_JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise BidDraftSectionError("BID_DRAFT_SECTION_LLM_BAD_JSON")
    return payload


def _section_type_label(value: str | None) -> str:
    labels = {
        "business": "商务标",
        "qualification": "资格资料",
        "technical": "技术标",
        "pricing": "报价文件",
        "legal": "合同/法务",
        "clarification": "答疑清单",
        "attachment": "附件清单",
    }
    return labels.get(value or "", value or "-")


def _format_dt(value: Any) -> str | None:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit]


def _unique_text(items: list[Any]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = (item.get("source_file"), item.get("source_location"), item.get("original_text"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _unique_warnings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        message = item.get("message")
        if not message or message in seen:
            continue
        seen.add(message)
        result.append(item)
    return result
