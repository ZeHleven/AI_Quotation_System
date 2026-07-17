from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.bidding import (
    BidFileFormatPlan,
    BidFileFormatPlanEvent,
    BidParseRun,
    BidProject,
    BidProjectFile,
    TenderBusinessObject,
    TenderRequirement,
)
from app.services.bidding_parser import dumps_json, loads_json


BID_FILE_FORMAT_VERSION = "biz4b_bid_file_format_v1.0"
BID_FILE_FORMAT_REVIEW_STATUSES = {"draft", "confirmed", "needs_revision"}
BID_FILE_FORMAT_EDIT_EVENT_TYPES = {"add_item", "move_item", "remove_item", "edit_item"}
PACKAGE_SECTION_KIND = "format_package_section"

FORMAT_SOURCE_LABELS = {
    "external_format_file": "单独投标文件格式",
    "embedded_in_tender": "招标文件内嵌格式",
    "mixed": "招标文件+格式附件",
    "not_found": "未识别到明确格式",
}
PACKAGE_MODE_LABELS = {
    "separate_business_technical": "商务标/技术标分册",
    "unified_bid_file": "统一投标文件",
    "unknown": "待人工确认",
}


@dataclass(frozen=True)
class FormatEvidence:
    source_file: str
    source_location: str
    original_text: str
    source_kind: str = "file"
    package_hint: str | None = None


ITEM_PATTERNS: tuple[dict[str, Any], ...] = (
    {"key": "bid_letter", "title": "投标函/投标书", "keywords": ("投标函", "投标书"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "pricing_summary", "title": "投标报价汇总表及报价明细", "keywords": ("投标报价汇总表", "报价汇总表", "报价明细表", "投标报价一览表"), "package": "business", "content_type": "pricing_table", "owner_role": "预算"},
    {"key": "boq", "title": "工程量清单报价表", "keywords": ("工程量清单报价表", "工程量计价清单", "工程量清单"), "package": "business", "content_type": "pricing_table", "owner_role": "预算"},
    {"key": "bid_bond", "title": "投标保证金/投标保函", "keywords": ("投标保证金", "投标保函", "保证金递交回执", "保证金收据"), "package": "business", "content_type": "attachment_proof", "owner_role": "经营"},
    {"key": "clarification_reply", "title": "标前澄清/答疑回复", "keywords": ("标前澄清", "澄清回复", "疑问回复", "答疑回复"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "litigation_statement", "title": "诉讼仲裁资料或无诉讼证明", "keywords": ("诉讼", "仲裁", "无诉讼证明"), "package": "business", "content_type": "attachment_proof", "owner_role": "经营"},
    {"key": "optimization_value", "title": "图纸/做法优化建议及造价影响分析", "keywords": ("优化建议", "造价影响分析", "图纸类", "做法类"), "package": "business", "content_type": "draft_section", "owner_role": "预算"},
    {"key": "commitment", "title": "投标承诺书/承诺函", "keywords": ("投标承诺书", "承诺书", "承诺函"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "integrity", "title": "廉洁协议/廉洁承诺", "keywords": ("廉洁协议", "廉洁承诺"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "contract_objection", "title": "合同异议/商务要求承诺", "keywords": ("合同书有无异议", "合同异议", "商务要求", "商务条款"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "legal_representative", "title": "法定代表人资格证明书", "keywords": ("法定代表人资格证明书", "法定代表人身份证明书", "法人代表证明书"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "authorization", "title": "授权委托书", "keywords": ("授权委托书", "投标文件签署授权委托书", "法人授权委托书"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "business_deviation", "title": "商务条款偏离表", "keywords": ("商务条款偏离表", "商务偏离表"), "package": "business", "content_type": "fixed_form", "owner_role": "经营"},
    {"key": "technical_deviation", "title": "技术规格偏离表", "keywords": ("技术规格偏离表", "技术偏离表"), "package": "technical", "content_type": "fixed_form", "owner_role": "技术"},
    {"key": "business_license", "title": "营业执照及资质证明", "keywords": ("营业执照", "资质证明", "资质证书", "相关资质"), "package": "technical", "content_type": "qualification_attachment", "owner_role": "经营"},
    {"key": "safety_license", "title": "安全生产许可证", "keywords": ("安全生产许可证",), "package": "technical", "content_type": "qualification_attachment", "owner_role": "经营"},
    {"key": "project_manager_cert", "title": "项目经理/建造师证书", "keywords": ("项目经理", "一级建造师", "注册建造师"), "package": "technical", "content_type": "qualification_attachment", "owner_role": "经营"},
    {"key": "similar_experience", "title": "类似工程业绩", "keywords": ("类似工程经验", "主要业绩", "在建业绩", "已完成的类似工程"), "package": "technical", "content_type": "qualification_attachment", "owner_role": "经营"},
    {"key": "organization", "title": "项目组织架构/管理人员配置", "keywords": ("项目管理机构", "组织架构", "管理人员", "项目管理班子"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "construction_plan", "title": "施工组织设计/施工方案", "keywords": ("施工组织设计", "施工方案"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "schedule", "title": "施工总进度计划", "keywords": ("施工总进度计划", "施工进度计划", "进度网络图", "横道图"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "quality_safety", "title": "质量、安全文明施工保证措施", "keywords": ("质量", "安全文明", "防火施工", "文明施工", "保证措施"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "protection", "title": "成品保护方案", "keywords": ("成品保护",), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "material_plan", "title": "主要材料采购计划/材料品牌表", "keywords": ("主要材料", "采购计划", "材料品牌表", "材料样板", "甲指乙供"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "temporary_power", "title": "施工临时用电方案", "keywords": ("临时用电",), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "site_office_storage", "title": "办公室/工具间/材料间管理方案", "keywords": ("办公室", "工具间", "材料间"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "waste_management", "title": "垃圾清理与堆场管理方案", "keywords": ("垃圾", "堆场", "清理", "运输"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "key_difficulties", "title": "项目重难点分析", "keywords": ("重难点", "重点难点"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "after_sales", "title": "售后服务和保修承诺", "keywords": ("售后服务", "保修承诺", "质保"), "package": "technical", "content_type": "draft_section", "owner_role": "技术"},
    {"key": "qualification_files", "title": "资格证明文件", "keywords": ("资格证明文件", "资格声明", "证明投标单位是合格"), "package": "unified", "content_type": "qualification_attachment", "owner_role": "经营"},
    {"key": "goods_description", "title": "货物/服务说明表", "keywords": ("货物说明一览表", "服务及其辅助服务", "设备类表格"), "package": "unified", "content_type": "fixed_form", "owner_role": "技术"},
)

PACKAGING_KEYWORDS = ("装订", "密封", "正本", "副本", "电子标", "电子文档", "U盘", "份数", "封装", "密封袋")
PACKAGING_ONLY_ANCHOR_KEYWORDS = ("正本", "副本", "电子标书", "电子标", "电子文档", "U盘", "份数", "份", "独立装订", "密封", "封装", "密封袋")
SECTION_DIRECTORY_HINTS = ("主要包括", "必须包含", "应包括", "包括下列内容", "的要求", "目录", "7.2.", "7.3.", "一、", "二、", "三、", "四、", "五、")
FORMAT_MARKERS = ("投标文件格式", "第六章 投标文件格式", "第三章 格式", "第3章 格式", "商务标部分", "技术标部分", "投标书构成", "投标文件的组成")
EXPLICIT_SPLIT_MARKERS = ("商务标", "技术标")
UNIFIED_MARKERS = ("投标书构成", "投标单位编写的投标书应包括", "第三章 格式", "第3章 格式")


class BidFileFormatError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def preview_bid_file_format_plan(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    structure, summary, warnings = _build_structure(db, project, run)
    return {
        "plan_uuid": None,
        "format_version": BID_FILE_FORMAT_VERSION,
        "project_uuid": project.project_uuid,
        "project_name": project.project_name,
        "run_uuid": run.run_uuid,
        "review_status": "preview",
        "format_source": summary["format_source"],
        "format_source_label": FORMAT_SOURCE_LABELS.get(summary["format_source"], summary["format_source"]),
        "package_mode": summary["package_mode"],
        "package_mode_label": PACKAGE_MODE_LABELS.get(summary["package_mode"], summary["package_mode"]),
        "structure": structure,
        "summary": summary,
        "warnings": warnings,
        "reviewer_note": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "created_at": None,
        "updated_at": None,
    }


def get_bid_file_format_plan(db: Session, run: BidParseRun) -> BidFileFormatPlan | None:
    return db.query(BidFileFormatPlan).filter(BidFileFormatPlan.parse_run_id == run.id).first()


def generate_bid_file_format_plan(db: Session, project: BidProject, run: BidParseRun, *, created_by: int) -> BidFileFormatPlan:
    structure, summary, warnings = _build_structure(db, project, run)
    row = get_bid_file_format_plan(db, run)
    if not row:
        row = BidFileFormatPlan(
            plan_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            created_by=created_by,
            format_version=BID_FILE_FORMAT_VERSION,
            format_source=summary["format_source"],
            package_mode=summary["package_mode"],
            review_status="draft",
            structure_json=dumps_json(structure),
            summary_json=dumps_json(summary),
            warnings_json=dumps_json(warnings),
        )
        db.add(row)
    else:
        row.format_version = BID_FILE_FORMAT_VERSION
        row.format_source = summary["format_source"]
        row.package_mode = summary["package_mode"]
        row.review_status = "draft" if row.review_status != "confirmed" else "needs_revision"
        row.structure_json = dumps_json(structure)
        row.summary_json = dumps_json(summary)
        row.warnings_json = dumps_json(warnings)
        row.confirmed_by = None
        row.confirmed_at = None
    return row


def confirm_bid_file_format_plan(
    db: Session,
    plan: BidFileFormatPlan,
    *,
    reviewer_id: int,
    structure: dict[str, Any] | None = None,
    reviewer_note: str | None = None,
    edit_events: list[dict[str, Any]] | None = None,
) -> BidFileFormatPlan:
    if structure is not None:
        _validate_structure(structure)
        plan.structure_json = dumps_json(structure)
        summary = _summary_from_structure(structure, plan.format_source, plan.package_mode)
        plan.summary_json = dumps_json(summary)
    else:
        structure = loads_json(plan.structure_json, {})
        _validate_structure(structure)
    plan.review_status = "confirmed"
    plan.reviewer_note = (reviewer_note or "").strip()[:4000] or None
    plan.confirmed_by = reviewer_id
    plan.confirmed_at = datetime.now(timezone.utc)
    record_bid_file_format_plan_events(db, plan, edit_events or [], created_by=reviewer_id)
    return plan


def update_bid_file_format_plan_review(
    plan: BidFileFormatPlan,
    *,
    review_status: str,
    reviewer_note: str | None,
) -> BidFileFormatPlan:
    if review_status not in BID_FILE_FORMAT_REVIEW_STATUSES:
        raise BidFileFormatError("INVALID_BID_FILE_FORMAT_REVIEW_STATUS")
    plan.review_status = review_status
    plan.reviewer_note = (reviewer_note or "").strip()[:4000] or None
    if review_status != "confirmed":
        plan.confirmed_by = None
        plan.confirmed_at = None
    return plan


def serialize_bid_file_format_plan(plan: BidFileFormatPlan) -> dict[str, Any]:
    structure = loads_json(plan.structure_json, {})
    summary = loads_json(plan.summary_json, {}) or {}
    warnings = loads_json(plan.warnings_json, []) or []
    return {
        "plan_uuid": plan.plan_uuid,
        "format_version": plan.format_version,
        "project_uuid": plan.project.project_uuid if plan.project else None,
        "project_name": plan.project.project_name if plan.project else None,
        "run_uuid": plan.parse_run.run_uuid if plan.parse_run else None,
        "review_status": plan.review_status,
        "format_source": plan.format_source,
        "format_source_label": FORMAT_SOURCE_LABELS.get(plan.format_source, plan.format_source),
        "package_mode": plan.package_mode,
        "package_mode_label": PACKAGE_MODE_LABELS.get(plan.package_mode, plan.package_mode),
        "structure": structure,
        "summary": summary,
        "warnings": warnings,
        "reviewer_note": plan.reviewer_note,
        "confirmed_by": plan.confirmed_by,
        "confirmed_at": plan.confirmed_at.isoformat() if plan.confirmed_at else None,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        "edit_events": [serialize_bid_file_format_event(event) for event in (plan.events or [])][-50:],
    }


def record_bid_file_format_plan_events(
    db: Session,
    plan: BidFileFormatPlan,
    events: list[dict[str, Any]],
    *,
    created_by: int,
) -> None:
    for raw_event in events[:100]:
        event = _sanitize_edit_event(raw_event)
        if not event:
            continue
        db.add(
            BidFileFormatPlanEvent(
                event_uuid=str(uuid.uuid4()),
                plan_id=plan.id,
                project_id=plan.project_id,
                parse_run_id=plan.parse_run_id,
                event_type=event["event_type"],
                item_key=event.get("item_key"),
                item_title=event.get("item_title"),
                from_package_key=event.get("from_package_key"),
                to_package_key=event.get("to_package_key"),
                detail_json=dumps_json(event.get("detail") or {}),
                created_by=created_by,
            )
        )


def serialize_bid_file_format_event(event: BidFileFormatPlanEvent) -> dict[str, Any]:
    return {
        "event_uuid": event.event_uuid,
        "event_type": event.event_type,
        "item_key": event.item_key,
        "item_title": event.item_title,
        "from_package_key": event.from_package_key,
        "to_package_key": event.to_package_key,
        "detail": loads_json(event.detail_json, {}) if event.detail_json else {},
        "created_by": event.created_by,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _sanitize_edit_event(raw_event: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw_event, dict):
        return None
    event_type = str(raw_event.get("event_type") or "").strip()
    if event_type not in BID_FILE_FORMAT_EDIT_EVENT_TYPES:
        return None
    detail = raw_event.get("detail") if isinstance(raw_event.get("detail"), dict) else {}
    return {
        "event_type": event_type,
        "item_key": _clean(raw_event.get("item_key"), 255) or None,
        "item_title": _clean(raw_event.get("item_title"), 255) or None,
        "from_package_key": _clean(raw_event.get("from_package_key"), 64) or None,
        "to_package_key": _clean(raw_event.get("to_package_key"), 64) or None,
        "detail": {
            "from_package_title": _clean(detail.get("from_package_title"), 255) if isinstance(detail, dict) else None,
            "to_package_title": _clean(detail.get("to_package_title"), 255) if isinstance(detail, dict) else None,
            "content_type": _clean(detail.get("content_type"), 64) if isinstance(detail, dict) else None,
            "generation_strategy": _clean(detail.get("generation_strategy"), 64) if isinstance(detail, dict) else None,
            "owner_role": _clean(detail.get("owner_role"), 64) if isinstance(detail, dict) else None,
            "note": _clean(detail.get("note"), 500) if isinstance(detail, dict) else None,
        },
    }


def _build_structure(db: Session, project: BidProject, run: BidParseRun) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    evidence_pool = _collect_evidence(db, project, run)
    format_evidence = [item for item in evidence_pool if _contains_any(item.original_text, FORMAT_MARKERS)]
    split_evidence = [item for item in evidence_pool if _is_explicit_split_evidence(item.original_text)]
    unified_evidence = [item for item in evidence_pool if _contains_any(item.original_text, UNIFIED_MARKERS)]
    packaging_evidence = [item for item in evidence_pool if _contains_any(item.original_text, PACKAGING_KEYWORDS)]
    format_source = _format_source(project.files, format_evidence)
    package_mode = _package_mode(split_evidence, unified_evidence, format_evidence)
    packages = _build_packages(package_mode, evidence_pool)
    packaging_requirements = _build_packaging_requirements(packaging_evidence)
    structure = {
        "format_version": BID_FILE_FORMAT_VERSION,
        "format_source": format_source,
        "format_source_label": FORMAT_SOURCE_LABELS.get(format_source, format_source),
        "package_mode": package_mode,
        "package_mode_label": PACKAGE_MODE_LABELS.get(package_mode, package_mode),
        "packages": packages,
        "packaging_requirements": packaging_requirements,
        "source_evidence": [_serialize_evidence(item) for item in format_evidence[:12]],
    }
    summary = _summary_from_structure(structure, format_source, package_mode)
    summary["format_evidence_count"] = len(format_evidence)
    summary["packaging_requirement_count"] = len(packaging_requirements)
    warnings = _build_warnings(format_source, package_mode, packages, format_evidence)
    return structure, summary, warnings


def _collect_evidence(db: Session, project: BidProject, run: BidParseRun) -> list[FormatEvidence]:
    evidence: list[FormatEvidence] = []
    for file_obj in project.files:
        evidence.extend(_evidence_from_file(file_obj))
    requirements = (
        db.query(TenderRequirement)
        .filter(TenderRequirement.parse_run_id == run.id, TenderRequirement.status == "active")
        .order_by(TenderRequirement.id.asc())
        .all()
    )
    for requirement in requirements:
        text = " ".join(
            [
                str(requirement.original_text or ""),
                str(requirement.parsed_requirement or ""),
                str(requirement.output_section or ""),
                str(requirement.requirement_type or ""),
            ]
        )
        if _is_relevant_format_text(text):
            evidence.append(
                FormatEvidence(
                    source_file=requirement.source_file or "tender_requirement",
                    source_location=requirement.source_location or f"requirement:{requirement.id}",
                    original_text=_clean(text, 1200),
                    source_kind="requirement",
                )
            )
    objects = (
        db.query(TenderBusinessObject)
        .filter(TenderBusinessObject.parse_run_id == run.id, TenderBusinessObject.status == "active")
        .order_by(TenderBusinessObject.id.asc())
        .all()
    )
    for obj in objects:
        text = " ".join([str(obj.title or ""), str(obj.original_text or ""), str(obj.normalized_value or "")])
        if _is_relevant_format_text(text):
            evidence.append(
                FormatEvidence(
                    source_file=obj.source_file or "tender_business_object",
                    source_location=obj.source_location or f"business_object:{obj.id}",
                    original_text=_clean(text, 1200),
                    source_kind="business_object",
                )
            )
    return _dedupe_evidence(evidence)


def _evidence_from_file(file_obj: BidProjectFile) -> list[FormatEvidence]:
    result: list[FormatEvidence] = []
    segments = loads_json(file_obj.segments_json, []) if file_obj.segments_json else []
    if isinstance(segments, list):
        for index, segment in enumerate(segments, start=1):
            text = str(segment.get("text") if isinstance(segment, dict) else "")
            source_location = str(segment.get("source_location") or f"segment:{index}") if isinstance(segment, dict) else f"segment:{index}"
            result.extend(_package_section_evidence_from_text(text, file_obj.original_filename, source_location))
            if _is_relevant_format_text(text):
                result.append(
                    FormatEvidence(
                        source_file=file_obj.original_filename,
                        source_location=source_location,
                        original_text=_clean(text, 1200),
                        source_kind="file_segment",
                    )
                )
    if file_obj.extracted_text:
        result.extend(_package_section_evidence_from_text(file_obj.extracted_text, file_obj.original_filename, "extracted"))
        for index, snippet in enumerate(_split_relevant_snippets(file_obj.extracted_text), start=1):
            result.append(
                FormatEvidence(
                    source_file=file_obj.original_filename,
                    source_location=f"extracted:{index}",
                    original_text=_clean(snippet, 1200),
                    source_kind="file_text",
                )
            )
    return result


def _split_relevant_snippets(text: str) -> list[str]:
    lines = [_clean(line, 1200) for line in re.split(r"[\r\n]+", text or "") if _clean(line, 1200)]
    snippets: list[str] = []
    for index, line in enumerate(lines):
        if not _is_relevant_format_text(line):
            continue
        context = lines[max(0, index - 2) : min(len(lines), index + 8)]
        snippets.append(" ".join(context))
    if not snippets and _is_relevant_format_text(text):
        snippets.append(_clean(text, 4000))
    return snippets[:80]


def _package_section_evidence_from_text(text: str, source_file: str, source_location: str) -> list[FormatEvidence]:
    anchors = _find_package_section_anchors(text)
    if not anchors:
        return []
    result: list[FormatEvidence] = []
    for index, anchor in enumerate(anchors):
        package_key, start, title = anchor
        end = anchors[index + 1][1] if index + 1 < len(anchors) else min(len(text), start + 3000)
        section_text = _clean(text[start:end], 2000)
        if not _is_relevant_format_text(section_text):
            continue
        result.append(
            FormatEvidence(
                source_file=source_file,
                source_location=f"{source_location}:{title}",
                original_text=section_text,
                source_kind=PACKAGE_SECTION_KIND,
                package_hint=package_key,
            )
        )
    return result


def _find_package_section_anchors(text: str) -> list[tuple[str, int, str]]:
    if not text:
        return []
    patterns = (
        (
            "business",
            re.compile(
                r"(?P<title>(?:(?:\d+(?:\.\d+)+|[一二三四五六七八九十]+[、.．])\s*)?(?:对)?(?:商务标(?:部分|书)(?:目录|的要求|主要包括(?:下列内容)?|必须包含(?:以下内容)?|应包括(?:下列内容)?|包括下列内容)|商务标(?:的要求|主要包括(?:下列内容)?|必须包含(?:以下内容)?|应包括(?:下列内容)?|包括下列内容)))"
            ),
        ),
        (
            "technical",
            re.compile(
                r"(?P<title>(?:(?:\d+(?:\.\d+)+|[一二三四五六七八九十]+[、.．])\s*)?(?:对)?(?:技术标(?:部分|书)(?:目录|的要求|主要包括(?:下列内容)?|必须包含(?:以下内容)?|应包括(?:下列内容)?|包括下列内容)|技术标(?:的要求|主要包括(?:下列内容)?|必须包含(?:以下内容)?|应包括(?:下列内容)?|包括下列内容)))"
            ),
        ),
    )
    anchors: list[tuple[str, int, str]] = []
    for package_key, pattern in patterns:
        for match in pattern.finditer(text):
            title = _clean(match.group("title"), 80)
            if _is_packaging_only_anchor(text, match.start(), match.end()):
                continue
            anchors.append((package_key, match.start(), title or package_key))
    anchors.sort(key=lambda item: item[1])
    deduped: list[tuple[str, int, str]] = []
    for anchor in anchors:
        if deduped and abs(anchor[1] - deduped[-1][1]) < 8 and anchor[0] == deduped[-1][0]:
            continue
        deduped.append(anchor)
    return deduped


def _is_packaging_only_anchor(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 40) : start]
    after = text[end : min(len(text), end + 140)]
    window = before + text[start:end] + after
    if not _contains_any(window, PACKAGING_ONLY_ANCHOR_KEYWORDS):
        return False
    if _contains_any(window, SECTION_DIRECTORY_HINTS):
        return False
    if re.search(r"(?:\d+(?:\.\d+)+|[一二三四五六七八九十]+[、.．])\s*[^；;。\n]{0,80}(?:投标|施工|资质|证明|报价|方案|计划|偏离|承诺)", after):
        return False
    return True


def _build_packages(package_mode: str, evidence_pool: list[FormatEvidence]) -> list[dict[str, Any]]:
    if package_mode == "separate_business_technical":
        packages = [
            _package("business", "商务标", "business_bid", "商务报价、承诺、保证金、商务偏离、固定格式表单"),
            _package("technical", "技术标", "technical_bid", "施工组织设计、人员资质、进度质量安全、材料方案"),
        ]
    elif package_mode == "unified_bid_file":
        packages = [_package("unified", "统一投标文件", "unified_bid_file", "招标文件未明确分商务标/技术标，按投标书构成和格式表组织")]
    else:
        packages = [_package("unconfirmed", "待确认投标文件包", "unknown", "未识别到明确分册，需投标负责人确认")]

    package_by_key = {package["package_key"]: package for package in packages}
    found_items = _detect_format_items(evidence_pool, package_mode)
    for item in found_items:
        target_key = item["package_key"]
        if target_key not in package_by_key:
            target_key = "unified" if "unified" in package_by_key else packages[0]["package_key"]
        package_by_key[target_key]["items"].append(item)

    for package in packages:
        package["item_count"] = len(package["items"])
        package["draft_section_count"] = len([item for item in package["items"] if item["content_type"] == "draft_section"])
        package["fixed_form_count"] = len([item for item in package["items"] if item["content_type"] == "fixed_form"])
        package["attachment_count"] = len([item for item in package["items"] if item["content_type"] in {"attachment_proof", "qualification_attachment"}])
        package["pricing_table_count"] = len([item for item in package["items"] if item["content_type"] == "pricing_table"])
    return packages


def _detect_format_items(evidence_pool: list[FormatEvidence], package_mode: str) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    order = 1
    for evidence in evidence_pool:
        text = evidence.original_text
        for pattern in ITEM_PATTERNS:
            if not _pattern_matches(text, pattern):
                continue
            context_package = _context_package_for_pattern(evidence, pattern, package_mode)
            strong_context = evidence.package_hint in {"business", "technical"}
            package_key = _item_package_key(pattern, context_package, package_mode, strong_context=strong_context)
            key = (package_key, str(pattern["key"]))
            if key not in found:
                found[key] = {
                    "item_key": f"{package_key}:{pattern['key']}",
                    "base_item_key": pattern["key"],
                    "item_title": pattern["title"],
                    "package_key": package_key,
                    "default_package_key": pattern["package"],
                    "package_confidence": "section" if strong_context else "default",
                    "content_type": pattern["content_type"],
                    "content_type_label": _content_type_label(pattern["content_type"]),
                    "owner_role": pattern["owner_role"],
                    "generation_strategy": _generation_strategy(pattern["content_type"]),
                    "is_required": True,
                    "requires_signature": _requires_signature(pattern["title"], pattern["content_type"]),
                    "requires_attachment": pattern["content_type"] in {"attachment_proof", "qualification_attachment"},
                    "order_index": order,
                    "evidence": [],
                }
                order += 1
            evidence_payload = _serialize_evidence(evidence)
            _add_item_evidence(found[key], evidence_payload)
            if strong_context:
                found[key]["package_confidence"] = "section"
    return _resolve_cross_package_conflicts(sorted(found.values(), key=lambda item: item["order_index"]), package_mode)


def _add_item_evidence(item: dict[str, Any], evidence_payload: dict[str, str]) -> None:
    evidence_list = item.setdefault("evidence", [])
    if evidence_payload in evidence_list:
        return
    if evidence_payload.get("source_kind") == PACKAGE_SECTION_KIND:
        evidence_list.insert(0, evidence_payload)
        del evidence_list[5:]
        return
    if len(evidence_list) < 3:
        evidence_list.append(evidence_payload)


def _resolve_cross_package_conflicts(items: list[dict[str, Any]], package_mode: str) -> list[dict[str, Any]]:
    if package_mode != "separate_business_technical":
        return items
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item.get("base_item_key") or item.get("item_key")), []).append(item)

    resolved: list[dict[str, Any]] = []
    for group_items in grouped.values():
        package_keys = {str(item.get("package_key")) for item in group_items}
        if len(package_keys) <= 1:
            resolved.extend(group_items)
            continue
        retained = _choose_conflict_retained_item(group_items)
        retained["conflict_status"] = "cross_package_duplicate"
        retained["conflict_packages"] = sorted(package_keys)
        retained["conflict_note"] = "同名目录项曾在商务标/技术标均命中，系统已保留主归属，请人工确认。"
        for item in group_items:
            if item is retained:
                continue
            for evidence_payload in item.get("evidence") or []:
                _add_item_evidence(retained, evidence_payload)
        resolved.append(retained)

    return sorted(resolved, key=lambda item: item["order_index"])


def _choose_conflict_retained_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    section_items = [item for item in items if item.get("package_confidence") == "section"]
    if len(section_items) == 1:
        return section_items[0]
    default_package = str(items[0].get("default_package_key") or "")
    if default_package in {"business", "technical"}:
        for item in section_items or items:
            if item.get("package_key") == default_package:
                return item
    return sorted(section_items or items, key=lambda item: item["order_index"])[0]


def _build_packaging_requirements(packaging_evidence: list[FormatEvidence]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in packaging_evidence:
        text = evidence.original_text
        if not _contains_any(text, PACKAGING_KEYWORDS):
            continue
        title = _packaging_title(text)
        key = _slug(title)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "requirement_key": f"packaging:{key}",
                "requirement_title": title,
                "risk_level": "high" if _contains_any(text, ("密封", "废标", "无效", "正本", "副本")) else "medium",
                "owner_role": "经营",
                "evidence": [_serialize_evidence(evidence)],
            }
        )
        if len(result) >= 12:
            break
    return result


def _summary_from_structure(structure: dict[str, Any], format_source: str, package_mode: str) -> dict[str, Any]:
    packages = structure.get("packages") if isinstance(structure.get("packages"), list) else []
    items = [item for package in packages for item in package.get("items", []) if isinstance(item, dict)]
    counter = Counter(str(item.get("content_type") or "unknown") for item in items)
    return {
        "format_source": format_source,
        "format_source_label": FORMAT_SOURCE_LABELS.get(format_source, format_source),
        "package_mode": package_mode,
        "package_mode_label": PACKAGE_MODE_LABELS.get(package_mode, package_mode),
        "package_count": len(packages),
        "item_count": len(items),
        "fixed_form_count": counter.get("fixed_form", 0),
        "draft_section_count": counter.get("draft_section", 0),
        "pricing_table_count": counter.get("pricing_table", 0),
        "attachment_count": counter.get("attachment_proof", 0) + counter.get("qualification_attachment", 0),
        "manual_input_count": len([item for item in items if item.get("generation_strategy") in {"manual_upload", "manual_fill"}]),
        "conflict_count": len([item for item in items if item.get("conflict_status") == "cross_package_duplicate"]),
        "by_content_type": dict(counter),
    }


def _build_warnings(
    format_source: str,
    package_mode: str,
    packages: list[dict[str, Any]],
    format_evidence: list[FormatEvidence],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if format_source == "not_found" or package_mode == "unknown":
        warnings.append({"code": "FORMAT_NOT_CONFIDENT", "message": "未识别到明确投标文件格式，需人工确认后再生成投标书草稿。"})
    if package_mode == "unified_bid_file":
        warnings.append({"code": "UNIFIED_BID_FILE", "message": "本项目未明确分商务标/技术标，系统按统一投标文件格式组织。"})
    package_keys = {package.get("package_key") for package in packages}
    if package_mode == "separate_business_technical" and not {"business", "technical"}.issubset(package_keys):
        warnings.append({"code": "PACKAGE_SPLIT_INCOMPLETE", "message": "已识别分册要求，但商务标或技术标结构不完整。"})
    if not format_evidence:
        warnings.append({"code": "NO_FORMAT_EVIDENCE", "message": "没有找到可追溯的格式证据原文。"})
    conflict_count = sum(
        1
        for package in packages
        for item in package.get("items", [])
        if isinstance(item, dict) and item.get("conflict_status") == "cross_package_duplicate"
    )
    if conflict_count:
        warnings.append({"code": "CROSS_PACKAGE_DUPLICATE", "message": f"已发现 {conflict_count} 个目录项曾跨商务标/技术标重复命中，系统已去重并保留主归属，请人工复核。"})
    for package in packages:
        if not package.get("items"):
            warnings.append({"code": "EMPTY_PACKAGE", "message": f"{package.get('package_title') or '文件包'} 暂无识别出的目录项。"})
    return warnings


def _format_source(files: list[BidProjectFile], format_evidence: list[FormatEvidence]) -> str:
    has_external_format = any("投标文件格式" in (file_obj.original_filename or "") for file_obj in files)
    has_format_file_evidence = any("投标文件格式" in item.source_file for item in format_evidence)
    has_embedded = bool(format_evidence)
    if has_external_format or has_format_file_evidence:
        return "mixed" if has_embedded and len({item.source_file for item in format_evidence}) > 1 else "external_format_file"
    if has_embedded:
        return "embedded_in_tender"
    return "not_found"


def _package_mode(split_evidence: list[FormatEvidence], unified_evidence: list[FormatEvidence], format_evidence: list[FormatEvidence]) -> str:
    if split_evidence:
        return "separate_business_technical"
    if unified_evidence or format_evidence:
        return "unified_bid_file"
    return "unknown"


def _is_explicit_split_evidence(text: str) -> bool:
    return "商务标" in text and "技术标" in text


def _is_relevant_format_text(text: str) -> bool:
    return _contains_any(text, FORMAT_MARKERS + PACKAGING_KEYWORDS + tuple(keyword for pattern in ITEM_PATTERNS for keyword in pattern["keywords"]))


def _context_package(text: str, package_mode: str) -> str | None:
    business_pos = _first_pos(text, ("商务标", "商务标部分", "商务部分"))
    technical_pos = _first_pos(text, ("技术标", "技术标部分", "技术部分"))
    if business_pos is not None and (technical_pos is None or business_pos <= technical_pos):
        return "business"
    if technical_pos is not None:
        return "technical"
    if package_mode == "unified_bid_file":
        return "unified"
    return None


def _context_package_for_pattern(evidence: FormatEvidence, pattern: dict[str, Any], package_mode: str) -> str | None:
    if package_mode == "unified_bid_file":
        return "unified"
    if evidence.package_hint in {"business", "technical"}:
        return evidence.package_hint
    text = evidence.original_text
    keyword_pos = _first_pos(text, tuple(pattern.get("keywords") or ()))
    business_pos = _first_pos(text, ("商务标", "商务标部分", "商务部分"))
    technical_pos = _first_pos(text, ("技术标", "技术标部分", "技术部分"))
    if keyword_pos is not None:
        if technical_pos is not None and technical_pos <= keyword_pos and (business_pos is None or business_pos < technical_pos or keyword_pos - technical_pos < keyword_pos - business_pos):
            return "technical"
        if business_pos is not None and business_pos <= keyword_pos and (technical_pos is None or technical_pos < business_pos or keyword_pos - business_pos < keyword_pos - technical_pos):
            return "business"
    return _context_package(text, package_mode)


def _item_package_key(pattern: dict[str, Any], context_package: str | None, package_mode: str, *, strong_context: bool = False) -> str:
    if package_mode == "unified_bid_file":
        return "unified"
    if package_mode == "unknown":
        return "unconfirmed"
    if context_package in {"business", "technical"} and strong_context:
        return context_package
    package = str(pattern.get("package") or "business")
    if package in {"business", "technical"}:
        return package
    if context_package in {"business", "technical"}:
        return context_package
    if package == "unified":
        return "business"
    return package


def _package(package_key: str, title: str, package_type: str, description: str) -> dict[str, Any]:
    return {
        "package_key": package_key,
        "package_title": title,
        "package_type": package_type,
        "description": description,
        "review_status": "draft",
        "items": [],
    }


def _content_type_label(value: str) -> str:
    labels = {
        "fixed_form": "固定表单",
        "draft_section": "正文章节",
        "attachment_proof": "附件证明",
        "qualification_attachment": "资格附件",
        "pricing_table": "报价表",
    }
    return labels.get(value, value)


def _generation_strategy(content_type: str) -> str:
    if content_type == "draft_section":
        return "generate_draft"
    if content_type == "pricing_table":
        return "from_cost_quote"
    if content_type in {"attachment_proof", "qualification_attachment"}:
        return "manual_upload"
    return "manual_fill"


def _requires_signature(title: str, content_type: str) -> bool:
    return content_type == "fixed_form" or _contains_any(title, ("投标函", "承诺", "授权", "证明", "偏离表"))


def _pattern_matches(text: str, pattern: dict[str, Any]) -> bool:
    if pattern.get("key") == "bid_letter":
        return "投标函" in text or bool(re.search(r"(?<!商务标)(?<!技术标)投标书", text))
    return _contains_any(text, pattern["keywords"])


def _packaging_title(text: str) -> str:
    if "独立装订" in text:
        return "商务标/技术标独立装订"
    if "密封" in text or "封装" in text:
        return "投标文件密封与封装要求"
    if "正本" in text or "副本" in text:
        return "正本/副本份数要求"
    if "电子" in text or "U盘" in text:
        return "电子标书/U盘提交要求"
    return "投标文件装订与提交要求"


def _validate_structure(structure: dict[str, Any]) -> None:
    if not isinstance(structure, dict):
        raise BidFileFormatError("BID_FILE_FORMAT_STRUCTURE_REQUIRED")
    packages = structure.get("packages")
    if not isinstance(packages, list) or not packages:
        raise BidFileFormatError("BID_FILE_FORMAT_PACKAGES_REQUIRED")
    for package in packages:
        if not isinstance(package, dict) or not str(package.get("package_title") or "").strip():
            raise BidFileFormatError("BID_FILE_FORMAT_PACKAGE_TITLE_REQUIRED")
        items = package.get("items")
        if items is not None and not isinstance(items, list):
            raise BidFileFormatError("BID_FILE_FORMAT_ITEMS_INVALID")


def _dedupe_evidence(items: list[FormatEvidence]) -> list[FormatEvidence]:
    seen: set[tuple[str, str, str, str | None]] = set()
    result: list[FormatEvidence] = []
    for item in items:
        key = (item.source_file, item.source_location, item.original_text[:180], item.package_hint)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _serialize_evidence(item: FormatEvidence) -> dict[str, str]:
    payload = {
        "source_file": item.source_file,
        "source_location": item.source_location,
        "original_text": item.original_text,
        "source_kind": item.source_kind,
    }
    if item.package_hint:
        payload["package_hint"] = item.package_hint
    return payload


def _contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _first_pos(text: str, keywords: tuple[str, ...]) -> int | None:
    positions = [text.find(keyword) for keyword in keywords if text.find(keyword) >= 0]
    return min(positions) if positions else None


def _clean(text: str | None, limit: int = 600) -> str:
    value = re.sub(r"\s+", " ", str(text or "").replace("\u3000", " ")).strip()
    return value[:limit]


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text).strip("_")
    return cleaned[:48] or "item"
