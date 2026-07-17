from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.bidding import (
    BidFileFormatPlan,
    BidParseRun,
    BidProject,
    TenderBusinessObject,
    TenderRequirement,
    TenderResponseItem,
    TenderRisk,
)
from app.services.bidding_file_format import FORMAT_SOURCE_LABELS, PACKAGE_MODE_LABELS, get_bid_file_format_plan
from app.services.bidding_parser import loads_json
from app.services.bidding_response_matrix import (
    build_response_matrix_summary,
    is_superseded_response_item,
    response_item_primary_review_role,
    response_item_supporting_roles,
)


BID_DRAFT_OUTLINE_VERSION = "biz4b_bid_draft_outline_v1.3"
FORMAT_PLAN_OUTLINE_STATUSES = {"confirmed", "needs_revision", "draft"}

SECTION_DEFINITIONS = [
    {
        "section_type": "business",
        "section_title": "一、商务标响应",
        "owner_role": "经营",
        "description": "投标规则、递交要求、商务承诺、响应表等商务标内容。",
    },
    {
        "section_type": "qualification",
        "section_title": "二、资格审查资料",
        "owner_role": "经营",
        "description": "企业资质、业绩、人员、授权、证照和资格证明资料。",
    },
    {
        "section_type": "technical",
        "section_title": "三、技术标方案",
        "owner_role": "技术",
        "description": "施工组织设计、质量、安全、进度、材料、验收和专项技术措施。",
    },
    {
        "section_type": "pricing",
        "section_title": "四、报价文件与报价说明",
        "owner_role": "预算",
        "description": "报价口径、固定总价、漏项责任、调价边界、清单说明和报价预留。",
    },
    {
        "section_type": "legal",
        "section_title": "五、合同偏离与风险决策",
        "owner_role": "法务",
        "description": "合同责任、违约、付款、索赔、解除、偏离表和投标决策风险。",
    },
    {
        "section_type": "clarification",
        "section_title": "六、答疑问题清单",
        "owner_role": "经营",
        "description": "需要在标前澄清或内部决策的问题。",
    },
    {
        "section_type": "attachment",
        "section_title": "七、附件与签章清单",
        "owner_role": "经营",
        "description": "投标文件附件、签章、密封、格式文件和证明材料清单。",
    },
]

SECTION_ORDER = {item["section_type"]: index for index, item in enumerate(SECTION_DEFINITIONS, start=1)}
TERMINAL_STATUSES = {"done", "ignored"}
GROUP_SUFFIX_RE = re.compile(r"(?:（第\d+组）|\(第\d+组\))$")
TECHNICAL_PREFIX_RE = re.compile(r"^技术标[:：]\s*")
TECHNICAL_DOCUMENT_KEYWORDS = ("技术标", "施工组织", "技术方案", "施工方案", "质量", "安全文明", "进度计划", "材料控制", "验收")
PRICING_DOCUMENT_KEYWORDS = ("报价", "工程量清单", "清单", "计价", "综合单价", "投标总价", "价格")
QUALIFICATION_DOCUMENT_KEYWORDS = ("资格", "资质", "业绩", "项目经理", "安全生产许可证", "证照", "授权委托")
BUSINESS_DOCUMENT_KEYWORDS = ("商务标", "投标函", "承诺函", "响应表", "偏离表", "投标文件组成", "递交", "密封", "签章")
RESPONSE_TABLE_DOCUMENT_KEYWORDS = ("响应表", "偏离表", "响应/偏离", "偏离")
BUSINESS_COMPLIANCE_KEYWORDS = (
    "投标截止",
    "截止时间",
    "递交时间",
    "递交方式",
    "递交地点",
    "开标",
    "密封",
    "封装",
    "评标办法",
    "评分办法",
    "投标有效期",
    "答疑",
    "澄清",
    "文件组成",
)
BUSINESS_STRONG_COMPLIANCE_TITLE_KEYWORDS = (
    "投标截止",
    "截止时间",
    "递交方式",
    "递交时间",
    "密封要求",
    "密封",
    "评标办法",
    "评分办法",
    "投标有效期",
    "答疑/澄清截止",
    "答疑截止",
    "澄清截止",
)
BUSINESS_INPUT_KEYWORDS = (
    "授权",
    "委托",
    "签字",
    "签章",
    "盖章",
    "附件",
    "格式",
    "响应表",
    "偏离表",
    "资质",
    "证照",
    "业绩",
    "人员",
    "项目经理",
    "材料",
    "样本",
)
BUSINESS_DOCUMENT_INPUT_TITLE_KEYWORDS = (
    "响应/偏离表",
    "响应表",
    "偏离表",
    "商务标文件",
    "投标文件组成",
)
BUSINESS_RISK_DECISION_KEYWORDS = (
    "投标保证金",
    "保证金",
    "保函",
    "现金",
    "担保",
    "废标",
    "否决",
    "重大偏差",
    "不响应",
    "违约",
    "赔偿",
    "罚款",
    "扣款",
    "责任转嫁",
    "价格风险",
    "承诺函",
)
GENERIC_GROUP_TITLE_KEYWORDS = (
    "其他",
    "综合",
    "未分类",
    "未响应",
    "未命名",
    "杂项",
)
GENERIC_GROUP_TITLE_EXCLUSION_KEYWORDS = (
    "综合单价",
    "综合总价",
    "综合报价",
    "综合费率",
)
GENERIC_SPLIT_FAMILIES = [
    {
        "family": "bid_guarantee",
        "title": "投标保证金与保函",
        "keywords": ("投标保证金", "保证金", "保函", "担保", "现金"),
        "reason": "按保证金、保函、担保等资金/担保要求拆分。",
    },
    {
        "family": "rejection_deviation",
        "title": "废标/重大偏差响应边界",
        "keywords": ("废标", "否决", "重大偏差", "无效标", "不响应", "实质性响应"),
        "reason": "按废标、否决、重大偏差或实质性响应边界拆分。",
    },
    {
        "family": "submission_deadline",
        "title": "投标截止与递交时间",
        "keywords": ("投标截止", "截止时间", "递交时间", "递交截止", "逾期", "开标时间"),
        "reason": "按投标截止、递交时间、逾期受理等硬性时间规则拆分。",
    },
    {
        "family": "submission_seal",
        "title": "递交方式与密封要求",
        "keywords": ("递交方式", "递交地点", "密封", "封装", "封套", "份数", "正本", "副本"),
        "reason": "按递交方式、递交地点、密封封装和份数要求拆分。",
    },
    {
        "family": "validity_evaluation",
        "title": "投标有效期与评标办法",
        "keywords": ("投标有效期", "评标办法", "评分办法", "评审办法", "评分标准"),
        "reason": "按投标有效期、评标办法和评分标准等合规提醒拆分。",
    },
    {
        "family": "clarification",
        "title": "答疑澄清事项",
        "keywords": ("答疑", "澄清", "疑问", "提问", "不明确", "书面确认"),
        "reason": "按标前答疑、澄清和需书面确认事项拆分。",
    },
    {
        "family": "response_table",
        "title": "响应表/偏离表一致性",
        "keywords": ("响应表", "偏离表", "响应/偏离", "偏离说明", "一致性"),
        "reason": "按响应表、偏离表和跨册一致性要求拆分。",
    },
    {
        "family": "document_package",
        "title": "投标文件组成与签章附件",
        "keywords": ("投标文件组成", "商务标文件", "文件组成", "签章", "签字", "盖章", "附件", "授权", "格式"),
        "reason": "按投标文件组成、签章、授权、附件和格式要求拆分。",
    },
    {
        "family": "business_liability",
        "title": "商务承诺与责任边界",
        "keywords": ("承诺函", "违约", "赔偿", "罚款", "扣款", "责任", "责任转嫁"),
        "reason": "按商务承诺、违约责任和责任边界拆分。",
    },
]

FORMAT_MAPPING_KEYWORD_FAMILIES = {
    "bid_letter": ("投标函", "投标书", "投标承诺", "承诺函"),
    "pricing_summary": ("投标报价", "报价汇总", "报价明细", "投标总价", "报价一览"),
    "boq": ("工程量清单", "清单报价", "综合单价", "报价表", "计价清单"),
    "bid_bond": ("投标保证金", "投标保函", "保证金", "保函", "担保"),
    "clarification_reply": ("澄清", "答疑", "疑问回复", "标前"),
    "litigation_statement": ("诉讼", "仲裁", "无诉讼"),
    "optimization_value": ("优化建议", "造价影响", "图纸", "做法", "优化"),
    "commitment": ("承诺书", "承诺函", "承诺"),
    "integrity": ("廉洁", "廉洁协议", "廉洁承诺"),
    "contract_objection": ("合同异议", "合同", "商务条款", "偏离", "异议"),
    "legal_representative": ("法定代表人", "法人代表", "资格证明", "身份证明"),
    "authorization": ("授权委托", "授权", "委托"),
    "business_deviation": ("商务偏离", "商务条款偏离", "偏离表"),
    "technical_deviation": ("技术偏离", "技术规格偏离", "偏离表"),
    "business_license": ("营业执照", "资质证明", "资质证书", "资质"),
    "safety_license": ("安全生产许可证",),
    "project_manager_cert": ("项目经理", "建造师", "注册建造师"),
    "similar_experience": ("类似工程", "业绩", "工程经验"),
    "organization": ("组织架构", "项目管理机构", "管理人员", "项目班子"),
    "construction_plan": ("施工组织设计", "施工组织", "施工方案", "施工部署"),
    "schedule": ("施工进度", "进度计划", "总进度", "工期", "横道图"),
    "quality_safety": ("质量", "安全文明", "安全", "文明施工", "防火", "消防"),
    "protection": ("成品保护", "保护"),
    "material_plan": ("主要材料", "材料品牌", "材料样板", "采购计划", "材料"),
    "temporary_power": ("临时用电", "临电"),
    "site_office_storage": ("办公室", "工具间", "材料间", "临设"),
    "waste_management": ("垃圾", "堆场", "清理", "运输"),
    "key_difficulties": ("重难点", "重点难点", "难点"),
    "after_sales": ("售后服务", "保修", "质保"),
}
FORMAT_MAPPING_STOP_TERMS = {
    "投标",
    "文件",
    "格式",
    "资料",
    "部分",
    "要求",
    "目录",
    "清单",
    "证明",
    "方案",
    "计划",
    "管理",
    "项目",
    "工程",
}
FORMAT_MAPPING_RESPONSE_THRESHOLD = 38
FORMAT_MAPPING_REQUIREMENT_THRESHOLD = 30
FORMAT_MAPPING_RISK_THRESHOLD = 30
FORMAT_MAPPING_MAX_RESPONSE_ITEMS = 5
FORMAT_MAPPING_MAX_REQUIREMENTS = 10
FORMAT_MAPPING_MAX_RISKS = 8


def generate_bid_draft_outline(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    *,
    package_key: str | None = None,
) -> dict[str, Any]:
    package_scope = _normalize_package_scope(package_key)
    response_items = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    visible_items = [item for item in response_items if not is_superseded_response_item(item)]
    requirements = (
        db.query(TenderRequirement)
        .filter(TenderRequirement.parse_run_id == run.id, TenderRequirement.status == "active")
        .order_by(TenderRequirement.id.asc())
        .all()
    )
    risks = db.query(TenderRisk).filter(TenderRisk.parse_run_id == run.id).order_by(TenderRisk.id.asc()).all()
    business_object_count = (
        db.query(TenderBusinessObject)
        .filter(TenderBusinessObject.parse_run_id == run.id, TenderBusinessObject.status == "active")
        .count()
    )

    format_plan = _usable_format_plan(db, run)
    if format_plan:
        sections = _build_outline_sections_from_format_plan(format_plan, visible_items, requirements, risks, package_key=package_scope)
        summary = _build_outline_summary(sections, visible_items, requirements, risks)
        summary.update(_format_plan_outline_summary(format_plan, sections))
        source_type = "file_format_plan"
    else:
        sections = _build_outline_sections(visible_items)
        if package_scope:
            sections = _filter_outline_sections_by_package(sections, package_scope)
        summary = _build_outline_summary(sections, visible_items, requirements, risks)
        summary["outline_source"] = "response_matrix"
        source_type = "response_matrix"
    summary["package_scope"] = package_scope or "all"
    summary["package_scope_label"] = _package_scope_label(package_scope)
    warnings = []
    if format_plan:
        warnings.extend(_format_plan_outline_warnings(format_plan))
        mapping_warning = _format_plan_mapping_warning(sections)
        if mapping_warning:
            warnings.append(mapping_warning)
    elif not visible_items:
        warnings.append(
            {
                "code": "NO_RESPONSE_MATRIX",
                "message": "未找到可用响应矩阵项，请先生成并复核响应矩阵后再生成投标书目录骨架。",
            }
        )
    if package_scope and not sections:
        warnings.append(
            {
                "code": "EMPTY_BID_DRAFT_PACKAGE",
                "message": f"{_package_scope_label(package_scope)}暂无已分配的目录项，请先在投标文件格式确认表中核对分册归属。",
            }
        )

    return {
        "outline_version": BID_DRAFT_OUTLINE_VERSION,
        "project_uuid": project.project_uuid,
        "project_name": project.project_name,
        "run_uuid": run.run_uuid,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "section_count": len(sections),
        "summary": summary,
        "source": {
            "source_type": source_type,
            "source_type_label": "投标文件格式确认表" if format_plan else "响应矩阵",
            "package_scope": package_scope or "all",
            "package_scope_label": _package_scope_label(package_scope),
            "response_item_count": len(visible_items),
            "stored_response_item_count": len(response_items),
            "requirement_count": len(requirements),
            "risk_count": len(risks),
            "business_object_count": business_object_count,
            "response_matrix_summary": build_response_matrix_summary(visible_items),
            **(_format_plan_source_payload(format_plan) if format_plan else {}),
        },
        "sections": sections,
        "warnings": warnings,
    }


def _usable_format_plan(db: Session, run: BidParseRun) -> BidFileFormatPlan | None:
    plan = get_bid_file_format_plan(db, run)
    if not plan or plan.review_status not in FORMAT_PLAN_OUTLINE_STATUSES:
        return None
    structure = loads_json(plan.structure_json, {}) if plan.structure_json else {}
    packages = structure.get("packages") if isinstance(structure, dict) else None
    if not isinstance(packages, list) or not packages:
        return None
    if not any(isinstance(package, dict) and package.get("items") for package in packages):
        return None
    return plan


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


def _filter_outline_sections_by_package(sections: list[dict[str, Any]], package_key: str) -> list[dict[str, Any]]:
    if not package_key:
        return sections
    allowed_parent_keys: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for section in sections:
        if _section_matches_package_scope(section, package_key):
            filtered.append(section)
            if section.get("level") == 1:
                allowed_parent_keys.add(section.get("section_key") or "")
    for section in sections:
        parent_key = section.get("parent_key")
        if parent_key and parent_key in allowed_parent_keys and section not in filtered:
            filtered.append(section)
    return filtered


def _section_matches_package_scope(section: dict[str, Any], package_key: str) -> bool:
    if section.get("package_key") == package_key:
        return True
    section_type = str(section.get("section_type") or "")
    if package_key == "technical":
        return section_type == "technical"
    if package_key == "business":
        return section_type in {"business", "qualification", "pricing", "legal", "clarification", "attachment"}
    if package_key == "unified":
        return section.get("package_key") == "unified"
    return True


def _build_outline_sections_from_format_plan(
    plan: BidFileFormatPlan,
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
    *,
    package_key: str | None = None,
) -> list[dict[str, Any]]:
    package_scope = _normalize_package_scope(package_key)
    structure = loads_json(plan.structure_json, {}) if plan.structure_json else {}
    packages = structure.get("packages") if isinstance(structure, dict) else []
    confirmed = plan.review_status == "confirmed"
    sections: list[dict[str, Any]] = []
    for package_index, package in enumerate(packages, start=1):
        if not isinstance(package, dict):
            continue
        current_package_key = _clean_key(package.get("package_key") or f"package_{package_index}")
        if package_scope and current_package_key != package_scope:
            continue
        parent_key = f"format-plan:{plan.plan_uuid}:package:{current_package_key}"
        child_sections = [
            _format_plan_item_section(
                plan,
                package,
                parent_key,
                item,
                package_index,
                item_index,
                confirmed,
                response_items,
                requirements,
                risks,
            )
            for item_index, item in enumerate(package.get("items") or [], start=1)
            if isinstance(item, dict)
        ]
        sections.append(_format_plan_package_section(plan, package, parent_key, child_sections, package_index, confirmed))
        sections.extend(child_sections)
    return sections


def _format_plan_package_section(
    plan: BidFileFormatPlan,
    package: dict[str, Any],
    parent_key: str,
    child_sections: list[dict[str, Any]],
    package_index: int,
    confirmed: bool,
) -> dict[str, Any]:
    package_key = _clean_key(package.get("package_key") or f"package_{package_index}")
    package_title = str(package.get("package_title") or "投标文件包").strip()
    section_type = _section_type_for_format_package(package)
    missing_inputs = [] if confirmed else ["投标文件格式表尚未确认；确认后再批量生成正文草稿。"]
    review_checklist = [
        "确认目录项是否完整覆盖甲方投标文件格式要求。",
        "确认商务标、技术标或统一投标文件的分册归属正确。",
        "确认固定表单、报价表、附件证明和正文章节的生成方式正确。",
    ]
    response_item_uuids = _unique_str(
        [
            response_item_uuid
            for section in child_sections
            for response_item_uuid in section.get("response_item_uuids") or []
        ]
    )
    requirement_ids = _unique_int(
        [requirement_id for section in child_sections for requirement_id in section.get("requirement_ids") or []]
    )
    risk_ids = _unique_int([risk_id for section in child_sections for risk_id in section.get("risk_ids") or []])
    risk_warnings = _unique(
        [warning for section in child_sections for warning in section.get("risk_warnings") or []]
    )[:5]
    source_mapping = {
        "status": "mapped" if any((section.get("source_mapping") or {}).get("status") == "mapped" for section in child_sections) else "unmapped",
        "confidence": _package_mapping_confidence(child_sections),
        "mapped_item_count": len([section for section in child_sections if (section.get("source_mapping") or {}).get("status") == "mapped"]),
        "total_item_count": len(child_sections),
    }
    return {
        "section_key": parent_key,
        "parent_key": None,
        "level": 1,
        "order_index": package_index * 1000,
        "section_type": section_type,
        "section_title": package_title,
        "owner_role": _dominant_format_owner_role(child_sections) or _default_owner_role(section_type),
        "description": str(package.get("description") or "按甲方投标文件格式确认表形成的文件包。"),
        "draft_status": "ready" if confirmed else "needs_input",
        "draft_mode": "review_note",
        "draft_mode_label": _draft_mode_label("review_note"),
        "can_generate_draft": False,
        "can_generate_placeholder_draft": False,
        "can_generate_formal_draft": False,
        "can_generate_review_note": False,
        "llm_eligible": False,
        "generation_decision": {
            "code": "format_package_summary",
            "label": "文件包汇总",
            "draft_mode": "review_note",
            "llm_eligible": False,
            "reason": "父项仅作为商务标/技术标文件包汇总，不直接生成正文。",
        },
        "response_item_count": len(response_item_uuids),
        "child_section_count": len(child_sections),
        "requirement_count": len(requirement_ids),
        "risk_count": len(risk_ids),
        "evidence_count": sum(section.get("evidence_count") or 0 for section in child_sections),
        "response_item_uuids": response_item_uuids,
        "requirement_ids": requirement_ids,
        "risk_ids": risk_ids,
        "review_roles": dict(Counter(section.get("owner_role") for section in child_sections if section.get("owner_role"))),
        "review_priorities": {},
        "review_waves": {},
        "missing_inputs": missing_inputs,
        "risk_warnings": risk_warnings,
        "review_checklist": review_checklist,
        "source_summary": f"来自投标文件格式确认表，文件包 {package_title}，包含 {len(child_sections)} 个目录项，已映射 {source_mapping['mapped_item_count']} 项。",
        "source_mapping": source_mapping,
        "outline_source": "file_format_plan",
        "format_plan_uuid": plan.plan_uuid,
        "format_plan_review_status": plan.review_status,
        "package_key": package_key,
        "package_title": package_title,
        "package_type": package.get("package_type"),
    }


def _format_plan_item_section(
    plan: BidFileFormatPlan,
    package: dict[str, Any],
    parent_key: str,
    item: dict[str, Any],
    package_index: int,
    item_index: int,
    confirmed: bool,
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
) -> dict[str, Any]:
    package_key = _clean_key(package.get("package_key") or f"package_{package_index}")
    package_title = str(package.get("package_title") or "投标文件包").strip()
    item_key = _clean_key(item.get("item_key") or item.get("base_item_key") or f"item_{item_index}", limit=96)
    section_type = _section_type_for_format_item(item, package)
    source_mapping = _map_format_item_to_sources(item, package, section_type, response_items, requirements, risks)
    decision = _format_plan_generation_decision(item, section_type, confirmed, source_mapping)
    missing_inputs = _format_plan_missing_inputs(item, package_title, confirmed, source_mapping)
    review_checklist = _unique(_format_plan_review_checklist(item, package_title) + source_mapping["review_checklist"])[:10]
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    aggregate = _aggregate_items(source_mapping["response_items"])
    response_item_uuids = [matched.response_item_uuid for matched in source_mapping["response_items"]]
    requirement_ids = _unique_int(aggregate["requirement_ids"] + [matched.id for matched in source_mapping["requirements"]])[
        :FORMAT_MAPPING_MAX_REQUIREMENTS
    ]
    risk_ids = _unique_int(aggregate["risk_ids"] + [matched.id for matched in source_mapping["risks"]])[
        :FORMAT_MAPPING_MAX_RISKS
    ]
    risk_warnings = _unique(aggregate["risk_warnings"] + [_risk_warning_from_risk(risk) for risk in source_mapping["risks"]])[:5]
    review_roles = dict(Counter({str(item.get("owner_role") or _default_owner_role(section_type)): 1}) + Counter(aggregate["review_roles"]))
    review_priorities = aggregate["review_priorities"]
    review_waves = aggregate["review_waves"]
    evidence_count = len(evidence) + aggregate["evidence_count"] + len(source_mapping["requirements"]) + len(source_mapping["risks"])
    return {
        "section_key": f"{parent_key}:item:{item_key}",
        "parent_key": parent_key,
        "level": 2,
        "order_index": package_index * 1000 + int(item.get("order_index") or item_index),
        "section_type": section_type,
        "section_title": str(item.get("item_title") or "未命名目录项").strip(),
        "owner_role": str(item.get("owner_role") or _default_owner_role(section_type)).strip(),
        "description": _format_plan_item_description(item, package_title),
        "draft_status": decision["draft_status"],
        "draft_mode": decision["draft_mode"],
        "draft_mode_label": _draft_mode_label(decision["draft_mode"]),
        "can_generate_draft": decision["draft_mode"] != "blocked",
        "can_generate_placeholder_draft": decision["draft_mode"] == "placeholder",
        "can_generate_formal_draft": decision["draft_mode"] == "formal",
        "can_generate_review_note": decision["draft_mode"] == "review_note",
        "llm_eligible": bool(decision.get("llm_eligible")),
        "generation_decision": {
            "code": decision["code"],
            "label": decision["label"],
            "draft_mode": decision["draft_mode"],
            "llm_eligible": bool(decision.get("llm_eligible")),
            "reason": decision["reason"],
        },
        "response_item_count": len(response_item_uuids),
        "child_section_count": 0,
        "requirement_count": len(requirement_ids),
        "risk_count": len(risk_ids),
        "evidence_count": evidence_count,
        "response_item_uuids": response_item_uuids,
        "requirement_ids": requirement_ids,
        "risk_ids": risk_ids,
        "review_roles": review_roles,
        "review_priorities": review_priorities,
        "review_waves": review_waves,
        "missing_inputs": missing_inputs,
        "risk_warnings": risk_warnings,
        "review_checklist": review_checklist,
        "source_summary": _format_item_source_summary(package_title, item, source_mapping),
        "source_mapping": _serialize_format_source_mapping(source_mapping),
        "outline_source": "file_format_plan",
        "format_plan_uuid": plan.plan_uuid,
        "format_plan_review_status": plan.review_status,
        "package_key": package_key,
        "package_title": package_title,
        "package_type": package.get("package_type"),
        "format_item_key": item.get("item_key") or item_key,
        "base_item_key": item.get("base_item_key"),
        "content_type": item.get("content_type"),
        "content_type_label": item.get("content_type_label"),
        "generation_strategy": item.get("generation_strategy"),
        "requires_signature": bool(item.get("requires_signature")),
        "requires_attachment": bool(item.get("requires_attachment")),
        "format_evidence": evidence[:6],
        "conflict_status": item.get("conflict_status"),
        "conflict_note": item.get("conflict_note"),
    }


def _map_format_item_to_sources(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    response_items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
) -> dict[str, Any]:
    keywords = _format_mapping_keywords(item)
    response_matches = _rank_response_item_matches(item, package, section_type, keywords, response_items)
    matched_response_items = [match["item"] for match in response_matches]
    requirement_by_id = {requirement.id: requirement for requirement in requirements}
    risk_by_id = {risk.id: risk for risk in risks}
    response_requirement_ids = _unique_int(
        [
            requirement_id
            for response_item in matched_response_items
            for requirement_id in _requirement_ids_for_response_item(response_item)
        ]
    )[:FORMAT_MAPPING_MAX_REQUIREMENTS]
    response_risk_ids = _unique_int(
        [risk_id for response_item in matched_response_items for risk_id in _risk_ids_for_response_item(response_item)]
    )[:FORMAT_MAPPING_MAX_RISKS]
    direct_requirement_matches = _rank_requirement_matches(item, package, section_type, keywords, requirements)
    direct_risk_matches = _rank_risk_matches(item, package, section_type, keywords, risks)

    matched_requirements = _dedupe_models_by_id(
        [requirement_by_id[requirement_id] for requirement_id in response_requirement_ids if requirement_id in requirement_by_id]
        + [match["item"] for match in direct_requirement_matches]
    )[:FORMAT_MAPPING_MAX_REQUIREMENTS]
    matched_risks = _dedupe_models_by_id(
        [risk_by_id[risk_id] for risk_id in response_risk_ids if risk_id in risk_by_id]
        + [match["item"] for match in direct_risk_matches]
    )[:FORMAT_MAPPING_MAX_RISKS]
    best_score = max(
        [0]
        + [match["score"] for match in response_matches]
        + [match["score"] for match in direct_requirement_matches]
        + [match["score"] for match in direct_risk_matches]
    )
    status = "mapped" if matched_response_items or matched_requirements or matched_risks else "unmapped"
    confidence = _mapping_confidence(best_score, status)
    aggregate = _aggregate_items(matched_response_items)
    review_checklist = []
    if matched_response_items:
        review_checklist.append(f"已映射 {len(matched_response_items)} 个响应矩阵项，生成正文时需逐项核对。")
    if matched_requirements:
        review_checklist.append(f"已映射 {len(matched_requirements)} 条招标要求，正文需覆盖要求原意。")
    if matched_risks:
        review_checklist.append(f"已映射 {len(matched_risks)} 条风险，正文或复核说明需保留处理意见。")
    review_checklist.extend(aggregate["review_checklist"])
    if status == "unmapped":
        review_checklist.append("暂未映射到响应矩阵/要求/风险，生成前需人工确认章节素材。")
    return {
        "status": status,
        "confidence": confidence,
        "score": round(min(best_score, 100) / 100, 2),
        "keywords": keywords[:12],
        "reason": _mapping_reason(status, confidence, response_matches, direct_requirement_matches, direct_risk_matches),
        "response_items": matched_response_items,
        "requirements": matched_requirements,
        "risks": matched_risks,
        "response_item_matches": response_matches[:FORMAT_MAPPING_MAX_RESPONSE_ITEMS],
        "requirement_matches": direct_requirement_matches[:FORMAT_MAPPING_MAX_REQUIREMENTS],
        "risk_matches": direct_risk_matches[:FORMAT_MAPPING_MAX_RISKS],
        "review_checklist": _unique(review_checklist)[:8],
    }


def _rank_response_item_matches(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    keywords: list[str],
    candidates: list[TenderResponseItem],
) -> list[dict[str, Any]]:
    matches = []
    for candidate in candidates:
        score, reasons = _score_response_item_match(item, package, section_type, keywords, candidate)
        if score >= FORMAT_MAPPING_RESPONSE_THRESHOLD:
            matches.append({"item": candidate, "score": min(score, 100), "reasons": reasons})
    return sorted(matches, key=lambda match: (-match["score"], match["item"].id))[:FORMAT_MAPPING_MAX_RESPONSE_ITEMS]


def _score_response_item_match(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    keywords: list[str],
    candidate: TenderResponseItem,
) -> tuple[int, list[str]]:
    title = str(candidate.response_title or "")
    text = _response_mapping_text(candidate)
    keyword_score, hits = _keyword_match_score(keywords, title, text)
    if not hits:
        return 0, []
    reasons = [f"关键词命中：{'、'.join(hits[:4])}"]
    score = keyword_score
    candidate_section = _section_type_for_item(candidate)
    if candidate_section == section_type:
        score += 24
        reasons.append("章节类型一致")
    elif _section_types_compatible(section_type, candidate_section):
        score += 12
        reasons.append("章节类型相邻")
    else:
        score -= 12
    owner_role = str(item.get("owner_role") or "")
    candidate_role = str(response_item_primary_review_role(candidate) or candidate.owner_role or "")
    if owner_role and candidate_role == owner_role:
        score += 8
        reasons.append("主责角色一致")
    if _response_action_compatible(section_type, str(candidate.response_action or ""), _normalized(candidate)):
        score += 8
        reasons.append("处理动作匹配")
    if candidate.risk_level == "high" and section_type in {"pricing", "legal", "business"}:
        score += 4
        reasons.append("含高风险复核事项")
    return max(score, 0), reasons[:5]


def _rank_requirement_matches(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    keywords: list[str],
    candidates: list[TenderRequirement],
) -> list[dict[str, Any]]:
    matches = []
    for candidate in candidates:
        score, reasons = _score_requirement_match(item, package, section_type, keywords, candidate)
        if score >= FORMAT_MAPPING_REQUIREMENT_THRESHOLD:
            matches.append({"item": candidate, "score": min(score, 100), "reasons": reasons})
    return sorted(matches, key=lambda match: (-match["score"], match["item"].id))[:FORMAT_MAPPING_MAX_REQUIREMENTS]


def _score_requirement_match(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    keywords: list[str],
    candidate: TenderRequirement,
) -> tuple[int, list[str]]:
    title = str(candidate.parsed_requirement or "")
    text = " ".join(
        [
            str(candidate.original_text or ""),
            str(candidate.parsed_requirement or ""),
            str(candidate.requirement_type or ""),
            str(candidate.output_section or ""),
            str(candidate.owner_role or ""),
        ]
    )
    keyword_score, hits = _keyword_match_score(keywords, title, text)
    if not hits:
        return 0, []
    reasons = [f"要求关键词命中：{'、'.join(hits[:4])}"]
    score = keyword_score
    if _requirement_section_compatible(section_type, candidate):
        score += 16
        reasons.append("要求输出章节匹配")
    owner_role = str(item.get("owner_role") or "")
    if owner_role and str(candidate.owner_role or "") == owner_role:
        score += 8
        reasons.append("要求主责一致")
    package_title = str(package.get("package_title") or "")
    if package_title and package_title in text:
        score += 8
        reasons.append("文件包文字命中")
    return max(score, 0), reasons[:5]


def _rank_risk_matches(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    keywords: list[str],
    candidates: list[TenderRisk],
) -> list[dict[str, Any]]:
    matches = []
    for candidate in candidates:
        score, reasons = _score_risk_match(item, package, section_type, keywords, candidate)
        if score >= FORMAT_MAPPING_RISK_THRESHOLD:
            matches.append({"item": candidate, "score": min(score, 100), "reasons": reasons})
    return sorted(matches, key=lambda match: (-match["score"], match["item"].id))[:FORMAT_MAPPING_MAX_RISKS]


def _score_risk_match(
    item: dict[str, Any],
    package: dict[str, Any],
    section_type: str,
    keywords: list[str],
    candidate: TenderRisk,
) -> tuple[int, list[str]]:
    title = str(candidate.risk_explanation or "")
    text = " ".join(
        [
            str(candidate.original_text or ""),
            str(candidate.risk_explanation or ""),
            str(candidate.risk_type or ""),
            str(candidate.impact_area or ""),
            str(candidate.suggested_action or ""),
        ]
    )
    keyword_score, hits = _keyword_match_score(keywords, title, text)
    if not hits:
        return 0, []
    reasons = [f"风险关键词命中：{'、'.join(hits[:4])}"]
    score = keyword_score
    if _risk_section_compatible(section_type, candidate):
        score += 14
        reasons.append("风险影响域匹配")
    if candidate.risk_level == "high" or candidate.is_blocking:
        score += 8
        reasons.append("高风险/阻断风险")
    package_title = str(package.get("package_title") or "")
    if package_title and package_title in text:
        score += 6
        reasons.append("文件包文字命中")
    return max(score, 0), reasons[:5]


def _format_mapping_keywords(item: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    base_key = str(item.get("base_item_key") or item.get("item_key") or "").split(":")[-1]
    keywords.extend(FORMAT_MAPPING_KEYWORD_FAMILIES.get(base_key, ()))
    keywords.extend(_terms_from_text(str(item.get("item_title") or "")))
    content_type = str(item.get("content_type") or "")
    if content_type == "pricing_table":
        keywords.extend(["报价", "工程量清单", "综合单价"])
    elif content_type == "qualification_attachment":
        keywords.extend(["资格", "资质", "证照", "业绩", "人员"])
    elif content_type == "attachment_proof":
        keywords.extend(["附件", "证明", "回执", "保函"])
    return _unique([keyword for keyword in keywords if keyword and keyword not in FORMAT_MAPPING_STOP_TERMS])[:20]


def _terms_from_text(text: str) -> list[str]:
    normalized = re.sub(r"[（）()【】\\[\\]<>《》:：;；,，.。/、]+", " ", text or "")
    pieces = []
    for chunk in normalized.split():
        pieces.extend(re.split(r"(?:及|与|和|或)", chunk))
        pieces.append(chunk)
    return [piece for piece in pieces if 2 <= len(piece) <= 18 and piece not in FORMAT_MAPPING_STOP_TERMS]


def _keyword_match_score(keywords: list[str], title: str, text: str) -> tuple[int, list[str]]:
    hits = []
    score = 0
    for keyword in keywords:
        if not keyword:
            continue
        if keyword in title:
            hits.append(keyword)
            score += 16 if len(keyword) >= 4 else 12
        elif keyword in text:
            hits.append(keyword)
            score += 11 if len(keyword) >= 4 else 8
    return min(score, 56), _unique(hits)


def _response_mapping_text(item: TenderResponseItem) -> str:
    normalized = _normalized(item)
    checklist = normalized.get("done_checklist") if isinstance(normalized.get("done_checklist"), list) else []
    workflow_actions = normalized.get("workflow_actions") if isinstance(normalized.get("workflow_actions"), list) else []
    return " ".join(
        [
            str(item.response_title or ""),
            str(item.source_text or ""),
            str(item.response_note or ""),
            str(item.response_category or ""),
            str(item.response_action or ""),
            str(item.owner_role or ""),
            str(normalized.get("review_action") or ""),
            " ".join(str(value or "") for value in checklist),
            " ".join(str(action.get("action") or "") for action in workflow_actions if isinstance(action, dict)),
        ]
    )


def _section_types_compatible(target: str, candidate: str) -> bool:
    if target == candidate:
        return True
    compatible = {
        "business": {"attachment", "qualification", "legal", "pricing"},
        "attachment": {"business", "qualification"},
        "qualification": {"business", "attachment", "technical"},
        "technical": {"qualification"},
        "pricing": {"business", "legal"},
        "legal": {"business", "pricing"},
        "clarification": {"business", "legal"},
    }
    return candidate in compatible.get(target, set())


def _response_action_compatible(section_type: str, action: str, normalized: dict[str, Any]) -> bool:
    review_action = str(normalized.get("review_action") or "")
    if section_type == "technical":
        return action == "document_preparation" or review_action == "write_technical_document"
    if section_type == "pricing":
        return action == "quote_allowance" or review_action == "budget_assessment"
    if section_type == "legal":
        return action == "legal_review" or review_action == "legal_assessment"
    if section_type == "qualification":
        return action == "qualification_material" or review_action == "prepare_qualification"
    if section_type == "clarification":
        return action == "clarification" or review_action == "clarification_question"
    return action in {"direct_response", "document_preparation", "reference"}


def _requirement_section_compatible(section_type: str, requirement: TenderRequirement) -> bool:
    text = " ".join([str(requirement.requirement_type or ""), str(requirement.output_section or ""), str(requirement.owner_role or "")])
    if section_type == "technical":
        return any(keyword in text for keyword in ("technical", "技术", "施工"))
    if section_type == "pricing":
        return any(keyword in text for keyword in ("bill", "报价", "预算", "清单", "造价"))
    if section_type == "legal":
        return any(keyword in text for keyword in ("contract", "合同", "法务", "偏离"))
    if section_type == "qualification":
        return any(keyword in text for keyword in ("qualification", "资格", "资质", "人员", "证照"))
    if section_type == "attachment":
        return any(keyword in text for keyword in ("附件", "证明", "资格", "资质", "材料"))
    if section_type == "clarification":
        return any(keyword in text for keyword in ("clarification", "答疑", "澄清"))
    return any(keyword in text for keyword in ("commercial", "submission", "商务", "递交", "投标"))


def _risk_section_compatible(section_type: str, risk: TenderRisk) -> bool:
    text = " ".join([str(risk.risk_type or ""), str(risk.impact_area or ""), str(risk.suggested_action or "")])
    if section_type == "pricing":
        return any(keyword in text for keyword in ("price", "报价", "预算", "费用", "成本", "漏项", "调价"))
    if section_type == "legal":
        return any(keyword in text for keyword in ("contract", "合同", "法务", "责任", "违约", "索赔", "付款"))
    if section_type == "technical":
        return any(keyword in text for keyword in ("technical", "技术", "质量", "进度", "安全", "材料"))
    if section_type == "qualification":
        return any(keyword in text for keyword in ("qualification", "资格", "资质", "人员", "证照"))
    return any(keyword in text for keyword in ("商务", "投标", "保证金", "递交", "密封", "废标"))


def _requirement_ids_for_response_item(item: TenderResponseItem) -> list[int]:
    normalized = _normalized(item)
    coverage = normalized.get("coverage") if isinstance(normalized.get("coverage"), dict) else {}
    return _unique_int(([item.requirement_id] if item.requirement_id else []) + _int_list(coverage.get("requirement_ids")))


def _risk_ids_for_response_item(item: TenderResponseItem) -> list[int]:
    normalized = _normalized(item)
    coverage = normalized.get("coverage") if isinstance(normalized.get("coverage"), dict) else {}
    return _unique_int(([item.risk_id] if item.risk_id else []) + _int_list(coverage.get("risk_ids")))


def _dedupe_models_by_id(items: list[Any]) -> list[Any]:
    seen: set[int] = set()
    result = []
    for item in items:
        item_id = getattr(item, "id", None)
        if not item_id or int(item_id) in seen:
            continue
        seen.add(int(item_id))
        result.append(item)
    return result


def _mapping_confidence(best_score: int, status: str) -> str:
    if status != "mapped":
        return "none"
    if best_score >= 78:
        return "high"
    if best_score >= 55:
        return "medium"
    return "low"


def _mapping_reason(
    status: str,
    confidence: str,
    response_matches: list[dict[str, Any]],
    requirement_matches: list[dict[str, Any]],
    risk_matches: list[dict[str, Any]],
) -> str:
    if status != "mapped":
        return "未找到稳定命中的响应矩阵、招标要求或风险项，需人工绑定素材。"
    parts = []
    if response_matches:
        parts.append(f"命中 {len(response_matches)} 个响应矩阵项")
    if requirement_matches:
        parts.append(f"命中 {len(requirement_matches)} 条招标要求")
    if risk_matches:
        parts.append(f"命中 {len(risk_matches)} 条风险")
    return f"{'，'.join(parts)}；映射置信度 {confidence}。"


def _serialize_format_source_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": mapping["status"],
        "confidence": mapping["confidence"],
        "score": mapping["score"],
        "reason": mapping["reason"],
        "keywords": mapping["keywords"],
        "response_item_matches": [
            {
                "response_item_uuid": match["item"].response_item_uuid,
                "response_title": match["item"].response_title,
                "score": round(match["score"] / 100, 2),
                "reasons": match["reasons"],
            }
            for match in mapping["response_item_matches"]
        ],
        "requirement_matches": [
            {
                "requirement_id": match["item"].id,
                "parsed_requirement": match["item"].parsed_requirement,
                "score": round(match["score"] / 100, 2),
                "reasons": match["reasons"],
            }
            for match in mapping["requirement_matches"]
        ],
        "risk_matches": [
            {
                "risk_id": match["item"].id,
                "risk_explanation": match["item"].risk_explanation,
                "risk_level": match["item"].risk_level,
                "score": round(match["score"] / 100, 2),
                "reasons": match["reasons"],
            }
            for match in mapping["risk_matches"]
        ],
    }


def _format_item_source_summary(package_title: str, item: dict[str, Any], mapping: dict[str, Any]) -> str:
    base = f"来自投标文件格式确认表：{package_title} / {item.get('content_type_label') or item.get('content_type') or '目录项'}。"
    if mapping["status"] != "mapped":
        return base + " 暂未映射到响应矩阵/要求/风险。"
    return (
        base
        + f" 已映射响应 {len(mapping['response_items'])} 项、要求 {len(mapping['requirements'])} 条、风险 {len(mapping['risks'])} 条。"
    )


def _risk_warning_from_risk(risk: TenderRisk) -> str:
    text = (risk.risk_explanation or risk.original_text or "").strip().replace("\n", " ")
    if len(text) > 100:
        text = text[:100] + "..."
    return text or str(risk.risk_type or "风险项")


def _package_mapping_confidence(child_sections: list[dict[str, Any]]) -> str:
    confidence_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    mapped_confidences = [
        (section.get("source_mapping") or {}).get("confidence") or "none"
        for section in child_sections
        if (section.get("source_mapping") or {}).get("status") == "mapped"
    ]
    if not mapped_confidences:
        return "none"
    ranked = sorted(mapped_confidences, key=lambda value: confidence_order.get(value, 0), reverse=True)
    return ranked[0]


def _format_plan_generation_decision(
    item: dict[str, Any],
    section_type: str,
    confirmed: bool,
    source_mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = str(item.get("generation_strategy") or "").strip()
    content_type = str(item.get("content_type") or "").strip()
    mapped = bool(source_mapping and source_mapping.get("status") == "mapped")
    if not confirmed:
        return {
            "code": "format_plan_needs_confirmation",
            "label": "需确认格式",
            "draft_status": "needs_input",
            "draft_mode": "placeholder",
            "llm_eligible": False,
            "reason": "投标文件格式表尚未确认，先生成目录预览和占位草稿，不进入正式正文生成。",
        }
    if strategy == "generate_draft" or content_type == "draft_section":
        if not mapped:
            return {
                "code": "needs_source_mapping",
                "label": "需绑定素材",
                "draft_status": "needs_input",
                "draft_mode": "placeholder",
                "llm_eligible": False,
                "reason": "该目录项虽为正文章节，但尚未映射到响应矩阵、招标要求或风险证据，先生成占位草稿并人工绑定素材。",
            }
        if source_mapping and source_mapping.get("confidence") == "low":
            return {
                "code": "weak_source_mapping",
                "label": "需确认素材",
                "draft_status": "needs_input",
                "draft_mode": "placeholder",
                "llm_eligible": False,
                "reason": "该目录项已映射到部分素材，但置信度偏低，需人工确认后再生成正式正文。",
            }
        return {
            "code": "format_draft_section",
            "label": "可生成正文",
            "draft_status": "ready",
            "draft_mode": "formal",
            "llm_eligible": section_type in {"business", "technical", "pricing"},
            "reason": "该目录项在格式确认表中标记为正文章节，可作为商务标/技术标正文草稿入口。",
        }
    if strategy == "from_cost_quote" or content_type == "pricing_table":
        return {
            "code": "from_cost_quote",
            "label": "接入报价链路",
            "draft_status": "needs_input",
            "draft_mode": "placeholder",
            "llm_eligible": False,
            "reason": "报价表应由报价/成本链路生成或人工导入，本章节先保留报价复核占位。",
        }
    if strategy == "manual_upload" or content_type in {"attachment_proof", "qualification_attachment"}:
        return {
            "code": "manual_upload",
            "label": "需上传附件",
            "draft_status": "needs_input",
            "draft_mode": "placeholder",
            "llm_eligible": False,
            "reason": "该目录项属于证明材料或附件，应由负责人上传/绑定文件，不由 LLM 编造。",
        }
    return {
        "code": "manual_fill",
        "label": "需人工填表",
        "draft_status": "needs_input",
        "draft_mode": "placeholder",
        "llm_eligible": False,
        "reason": "该目录项属于甲方固定表单或固定格式，应按格式填写，不直接自由生成。",
    }


def _format_plan_missing_inputs(
    item: dict[str, Any],
    package_title: str,
    confirmed: bool,
    source_mapping: dict[str, Any] | None = None,
) -> list[str]:
    missing = []
    if not confirmed:
        missing.append("投标文件格式表尚未确认；确认后再进入正式草稿生成。")
    if source_mapping and source_mapping.get("status") != "mapped":
        missing.append("尚未映射到响应矩阵/招标要求/风险证据，需人工绑定章节素材。")
    elif source_mapping and source_mapping.get("confidence") == "low":
        missing.append("已映射到相关素材，但置信度偏低，需人工确认匹配是否准确。")
    strategy = str(item.get("generation_strategy") or "")
    content_type = str(item.get("content_type") or "")
    if strategy == "from_cost_quote" or content_type == "pricing_table":
        missing.append("需接入报价清单、成本库和报价复核结果。")
    elif strategy == "manual_upload" or content_type in {"attachment_proof", "qualification_attachment"}:
        missing.append("需上传或绑定对应证明材料/附件文件。")
    elif strategy == "manual_fill" or content_type == "fixed_form":
        missing.append("需按甲方固定格式填写签字、盖章、日期、承诺或偏离内容。")
    if item.get("requires_signature"):
        missing.append("需确认签字/盖章责任人和签章页。")
    if item.get("requires_attachment"):
        missing.append("需确认附件是否齐全并归入对应文件包。")
    if item.get("conflict_status") == "cross_package_duplicate":
        missing.append("该目录项曾跨商务标/技术标重复命中，需确认最终归属。")
    if not missing:
        missing.append(f"需按{package_title}确认章节素材、企业能力和项目适配信息。")
    return _unique(missing)[:6]


def _format_plan_review_checklist(item: dict[str, Any], package_title: str) -> list[str]:
    checklist = [
        f"确认该目录项归属到【{package_title}】是否正确。",
        "确认目录名称、顺序和甲方格式要求一致。",
    ]
    strategy = str(item.get("generation_strategy") or "")
    content_type = str(item.get("content_type") or "")
    if strategy == "generate_draft" or content_type == "draft_section":
        checklist.append("确认正文素材、企业能力/施工经验和招标要求证据已绑定。")
    if strategy == "from_cost_quote" or content_type == "pricing_table":
        checklist.append("确认报价口径、清单版本、调价边界和汇总表一致。")
    if strategy == "manual_upload" or content_type in {"attachment_proof", "qualification_attachment"}:
        checklist.append("确认附件文件有效期、主体名称、签章和清晰度。")
    if item.get("requires_signature"):
        checklist.append("确认签字/盖章页不遗漏。")
    if item.get("requires_attachment"):
        checklist.append("确认附件与目录项一一对应。")
    return _unique(checklist)[:8]


def _format_plan_item_description(item: dict[str, Any], package_title: str) -> str:
    parts = [package_title]
    if item.get("content_type_label"):
        parts.append(str(item["content_type_label"]))
    if item.get("generation_strategy"):
        parts.append(f"生成方式：{item['generation_strategy']}")
    return " / ".join(parts)


def _section_type_for_format_package(package: dict[str, Any]) -> str:
    package_key = str(package.get("package_key") or "")
    package_title = str(package.get("package_title") or "")
    if package_key == "technical" or "技术" in package_title:
        return "technical"
    return "business"


def _section_type_for_format_item(item: dict[str, Any], package: dict[str, Any]) -> str:
    content_type = str(item.get("content_type") or "")
    owner_role = str(item.get("owner_role") or "")
    package_type = _section_type_for_format_package(package)
    title = str(item.get("item_title") or "")
    if content_type == "pricing_table" or owner_role == "预算":
        return "pricing"
    if content_type == "qualification_attachment":
        return "qualification"
    if content_type == "attachment_proof":
        return "attachment"
    if owner_role == "法务" or any(keyword in title for keyword in ("合同", "偏离", "异议")):
        return "legal"
    if package_type == "technical" or owner_role == "技术":
        return "technical"
    return "business"


def _dominant_format_owner_role(sections: list[dict[str, Any]]) -> str | None:
    counter = Counter(section.get("owner_role") for section in sections if section.get("owner_role"))
    return counter.most_common(1)[0][0] if counter else None


def _format_plan_source_payload(plan: BidFileFormatPlan) -> dict[str, Any]:
    structure = loads_json(plan.structure_json, {}) if plan.structure_json else {}
    summary = loads_json(plan.summary_json, {}) if plan.summary_json else {}
    packages = structure.get("packages") if isinstance(structure, dict) else []
    return {
        "format_plan_uuid": plan.plan_uuid,
        "format_plan_review_status": plan.review_status,
        "format_plan_confirmed_at": plan.confirmed_at.isoformat() if plan.confirmed_at else None,
        "format_source": plan.format_source,
        "format_source_label": FORMAT_SOURCE_LABELS.get(plan.format_source, plan.format_source),
        "package_mode": plan.package_mode,
        "package_mode_label": PACKAGE_MODE_LABELS.get(plan.package_mode, plan.package_mode),
        "format_package_count": len(packages) if isinstance(packages, list) else 0,
        "format_item_count": int(summary.get("item_count") or 0) if isinstance(summary, dict) else 0,
    }


def _format_plan_outline_summary(plan: BidFileFormatPlan, sections: list[dict[str, Any]]) -> dict[str, Any]:
    leaf_sections = [section for section in sections if section["level"] == 2]
    mapped_sections = [section for section in leaf_sections if (section.get("source_mapping") or {}).get("status") == "mapped"]
    confidence_counter = Counter((section.get("source_mapping") or {}).get("confidence") or "none" for section in leaf_sections)
    linked_response_item_uuids = _unique_str(
        [uuid for section in leaf_sections for uuid in section.get("response_item_uuids") or []]
    )
    linked_requirement_ids = _unique_int(
        [requirement_id for section in leaf_sections for requirement_id in section.get("requirement_ids") or []]
    )
    linked_risk_ids = _unique_int([risk_id for section in leaf_sections for risk_id in section.get("risk_ids") or []])
    return {
        "outline_source": "file_format_plan",
        "format_plan_review_status": plan.review_status,
        "format_plan_confirmed": plan.review_status == "confirmed",
        "format_package_count": len([section for section in sections if section["level"] == 1]),
        "format_item_count": len(leaf_sections),
        "mapped_format_item_count": len(mapped_sections),
        "unmapped_format_item_count": len(leaf_sections) - len(mapped_sections),
        "format_mapping_coverage_rate": round(len(mapped_sections) / max(len(leaf_sections), 1), 2),
        "linked_response_item_count": len(linked_response_item_uuids),
        "linked_requirement_count": len(linked_requirement_ids),
        "linked_risk_count": len(linked_risk_ids),
        "manual_input_section_count": len(
            [
                section
                for section in leaf_sections
                if section.get("generation_strategy") in {"manual_upload", "manual_fill", "from_cost_quote"}
            ]
        ),
        "by_package_key": dict(Counter(str(section.get("package_key")) for section in leaf_sections if section.get("package_key"))),
        "by_content_type": dict(Counter(str(section.get("content_type")) for section in leaf_sections if section.get("content_type"))),
        "by_mapping_confidence": dict(confidence_counter),
    }


def _format_plan_outline_warnings(plan: BidFileFormatPlan) -> list[dict[str, str]]:
    warnings = []
    if plan.review_status != "confirmed":
        warnings.append(
            {
                "code": "FORMAT_PLAN_NOT_CONFIRMED",
                "message": "投标文件格式表尚未确认，本次仅按当前格式表预览目录骨架；确认格式后再生成正式正文草稿。",
            }
        )
    for warning in loads_json(plan.warnings_json, []) if plan.warnings_json else []:
        if isinstance(warning, dict) and warning.get("code") and warning.get("message"):
            warnings.append({"code": str(warning["code"]), "message": str(warning["message"])})
    return warnings[:8]


def _format_plan_mapping_warning(sections: list[dict[str, Any]]) -> dict[str, str] | None:
    leaf_sections = [section for section in sections if section.get("level") == 2 and section.get("outline_source") == "file_format_plan"]
    unmapped_count = len([section for section in leaf_sections if (section.get("source_mapping") or {}).get("status") != "mapped"])
    if not leaf_sections or not unmapped_count:
        return None
    return {
        "code": "FORMAT_ITEM_MAPPING_INCOMPLETE",
        "message": f"仍有 {unmapped_count} 个格式目录项未映射到响应矩阵/招标要求/风险，生成正文前需人工绑定或补充响应矩阵。",
    }


def _build_outline_sections(items: list[TenderResponseItem]) -> list[dict[str, Any]]:
    items_by_section: dict[str, list[TenderResponseItem]] = defaultdict(list)
    groups_by_section: dict[str, dict[str, list[TenderResponseItem]]] = defaultdict(lambda: defaultdict(list))

    for item in items:
        section_type = _section_type_for_item(item)
        items_by_section[section_type].append(item)
        groups_by_section[section_type][_group_title_for_item(item)].append(item)

    sections: list[dict[str, Any]] = []
    for definition in SECTION_DEFINITIONS:
        section_type = definition["section_type"]
        parent_key = f"outline:{section_type}"
        section_items = items_by_section.get(section_type, [])
        child_sections = [
            _child_section(section_type, parent_key, title, group_items, child_index)
            for child_index, (title, group_items) in enumerate(
                sorted(
                    groups_by_section.get(section_type, {}).items(),
                    key=lambda entry: min(item.id for item in entry[1]),
                ),
                start=1,
            )
        ]
        sections.append(_parent_section(definition, parent_key, section_items, child_sections))
        sections.extend(child_sections)
    return sections


def _parent_section(
    definition: dict[str, str],
    section_key: str,
    items: list[TenderResponseItem],
    child_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregate = _aggregate_items(items)
    missing_inputs = _missing_inputs_for_section(definition["section_type"], items, aggregate)
    if not items:
        missing_inputs = ["暂无响应矩阵项；需要先生成响应矩阵或人工补充该章节任务。"]
    status = _draft_status(definition["section_type"], items, missing_inputs)
    generation_decision = _generation_decision(definition["section_type"], items, status, missing_inputs)
    draft_mode = generation_decision["draft_mode"]
    return {
        "section_key": section_key,
        "parent_key": None,
        "level": 1,
        "order_index": SECTION_ORDER[definition["section_type"]] * 100,
        "section_type": definition["section_type"],
        "section_title": definition["section_title"],
        "owner_role": definition["owner_role"],
        "description": definition["description"],
        "draft_status": status,
        "draft_mode": draft_mode,
        "draft_mode_label": _draft_mode_label(draft_mode),
        "can_generate_draft": bool(items) and status != "blocked",
        "can_generate_placeholder_draft": draft_mode == "placeholder",
        "can_generate_formal_draft": draft_mode == "formal",
        "can_generate_review_note": draft_mode == "review_note",
        "llm_eligible": bool(generation_decision.get("llm_eligible")),
        "generation_decision": generation_decision,
        "response_item_count": len(items),
        "child_section_count": len(child_sections),
        "requirement_count": len(aggregate["requirement_ids"]),
        "risk_count": len(aggregate["risk_ids"]),
        "evidence_count": aggregate["evidence_count"],
        "response_item_uuids": aggregate["response_item_uuids"],
        "requirement_ids": aggregate["requirement_ids"],
        "risk_ids": aggregate["risk_ids"],
        "review_roles": aggregate["review_roles"],
        "review_priorities": aggregate["review_priorities"],
        "review_waves": aggregate["review_waves"],
        "missing_inputs": missing_inputs,
        "risk_warnings": aggregate["risk_warnings"],
        "review_checklist": aggregate["review_checklist"],
        "source_summary": _source_summary(items, definition["section_type"]),
    }


def _child_section(
    section_type: str,
    parent_key: str,
    title: str,
    items: list[TenderResponseItem],
    child_index: int,
) -> dict[str, Any]:
    aggregate = _aggregate_items(items)
    missing_inputs = _missing_inputs_for_section(section_type, items, aggregate)
    status = _draft_status(section_type, items, missing_inputs)
    generation_decision = _generation_decision(section_type, items, status, missing_inputs)
    owner_role = _dominant_role(items) or _default_owner_role(section_type)
    draft_mode = generation_decision["draft_mode"]
    split_meta = _generic_split_metadata(title, items)
    return {
        "section_key": f"{parent_key}:task:{child_index}",
        "parent_key": parent_key,
        "level": 2,
        "order_index": SECTION_ORDER.get(section_type, 99) * 100 + child_index,
        "section_type": section_type,
        "section_title": title,
        "owner_role": owner_role,
        "description": _child_description(section_type, title, items),
        "draft_status": status,
        "draft_mode": draft_mode,
        "draft_mode_label": _draft_mode_label(draft_mode),
        "can_generate_draft": bool(items) and status != "blocked",
        "can_generate_placeholder_draft": draft_mode == "placeholder",
        "can_generate_formal_draft": draft_mode == "formal",
        "can_generate_review_note": draft_mode == "review_note",
        "llm_eligible": bool(generation_decision.get("llm_eligible")),
        "generation_decision": generation_decision,
        "response_item_count": len(items),
        "child_section_count": 0,
        "requirement_count": len(aggregate["requirement_ids"]),
        "risk_count": len(aggregate["risk_ids"]),
        "evidence_count": aggregate["evidence_count"],
        "response_item_uuids": aggregate["response_item_uuids"],
        "requirement_ids": aggregate["requirement_ids"],
        "risk_ids": aggregate["risk_ids"],
        "review_roles": aggregate["review_roles"],
        "review_priorities": aggregate["review_priorities"],
        "review_waves": aggregate["review_waves"],
        "missing_inputs": missing_inputs,
        "risk_warnings": aggregate["risk_warnings"],
        "review_checklist": aggregate["review_checklist"],
        "source_summary": _source_summary(items, section_type),
        "split_from_generic_title": split_meta["split_from_generic_title"],
        "original_group_title": split_meta["original_group_title"],
        "split_family": split_meta["split_family"],
        "split_confidence": split_meta["split_confidence"],
        "split_reason": split_meta["split_reason"],
        "needs_secondary_split": split_meta["needs_secondary_split"],
    }


def _aggregate_items(items: list[TenderResponseItem]) -> dict[str, Any]:
    requirement_ids: set[int] = set()
    risk_ids: set[int] = set()
    response_item_uuids: list[str] = []
    evidence_count = 0
    risk_warnings: list[str] = []
    review_checklist: list[str] = []
    review_roles: Counter[str] = Counter()
    review_priorities: Counter[str] = Counter()
    review_waves: Counter[str] = Counter()

    for item in items:
        response_item_uuids.append(item.response_item_uuid)
        normalized = _normalized(item)
        coverage = normalized.get("coverage") if isinstance(normalized.get("coverage"), dict) else {}
        requirement_ids.update(_int_list(coverage.get("requirement_ids")))
        risk_ids.update(_int_list(coverage.get("risk_ids")))
        if item.requirement_id:
            requirement_ids.add(int(item.requirement_id))
        if item.risk_id:
            risk_ids.add(int(item.risk_id))
        evidence = loads_json(item.evidence_json, []) if item.evidence_json else []
        if isinstance(evidence, list):
            evidence_count += len(evidence)
        else:
            evidence_count += int(coverage.get("evidence_count") or 0)
        primary_role = response_item_primary_review_role(item)
        if primary_role:
            review_roles[primary_role] += 1
        for role in response_item_supporting_roles(item):
            if role:
                review_roles[role] += 1
        if normalized.get("review_priority"):
            review_priorities[str(normalized["review_priority"])] += 1
        if normalized.get("review_wave"):
            review_waves[str(normalized["review_wave"])] += 1
        for checklist_item in normalized.get("done_checklist") if isinstance(normalized.get("done_checklist"), list) else []:
            text = str(checklist_item).strip()
            if text and text not in review_checklist:
                review_checklist.append(text)
        if item.risk_level == "high" or normalized.get("review_priority") in {"P0", "P1"}:
            warning = _risk_warning(item)
            if warning and warning not in risk_warnings:
                risk_warnings.append(warning)

    return {
        "response_item_uuids": response_item_uuids,
        "requirement_ids": sorted(requirement_ids),
        "risk_ids": sorted(risk_ids),
        "evidence_count": evidence_count,
        "risk_warnings": risk_warnings[:5],
        "review_checklist": review_checklist[:8],
        "review_roles": dict(review_roles),
        "review_priorities": dict(review_priorities),
        "review_waves": dict(review_waves),
    }


def _build_outline_summary(
    sections: list[dict[str, Any]],
    items: list[TenderResponseItem],
    requirements: list[TenderRequirement],
    risks: list[TenderRisk],
) -> dict[str, Any]:
    leaf_sections = [section for section in sections if section["level"] == 2]
    by_section_type = Counter(section["section_type"] for section in sections)
    by_owner_role = Counter(section["owner_role"] for section in sections if section.get("owner_role"))
    by_draft_status = Counter(section["draft_status"] for section in sections)
    by_draft_mode = Counter(section.get("draft_mode") for section in sections if section.get("draft_mode"))
    by_split_family = Counter(section.get("split_family") for section in leaf_sections if section.get("split_family"))
    return {
        "section_count": len(sections),
        "parent_section_count": len([section for section in sections if section["level"] == 1]),
        "task_section_count": len(leaf_sections),
        "response_item_count": len(items),
        "requirement_count": len(requirements),
        "risk_count": len(risks),
        "blocked_section_count": by_draft_status.get("blocked", 0),
        "needs_input_section_count": by_draft_status.get("needs_input", 0),
        "ready_section_count": by_draft_status.get("ready", 0),
        "can_generate_draft_count": len([section for section in leaf_sections if section.get("can_generate_draft")]),
        "placeholder_draft_count": len([section for section in leaf_sections if section.get("can_generate_placeholder_draft")]),
        "review_note_count": len([section for section in leaf_sections if section.get("can_generate_review_note")]),
        "formal_draft_ready_count": len([section for section in leaf_sections if section.get("can_generate_formal_draft")]),
        "llm_eligible_count": len([section for section in leaf_sections if section.get("llm_eligible")]),
        "generic_split_section_count": len([section for section in leaf_sections if section.get("split_from_generic_title")]),
        "secondary_split_needed_count": len([section for section in leaf_sections if section.get("needs_secondary_split")]),
        "by_section_type": dict(by_section_type),
        "by_owner_role": dict(by_owner_role),
        "by_draft_status": dict(by_draft_status),
        "by_draft_mode": dict(by_draft_mode),
        "by_split_family": dict(by_split_family),
    }


def _section_type_for_item(item: TenderResponseItem) -> str:
    normalized = _normalized(item)
    action = str(item.response_action or "")
    category = str(item.response_category or "")
    review_action = str(normalized.get("review_action") or "")
    primary_role = str(response_item_primary_review_role(item) or item.owner_role or "")
    workflow_actions = {
        str(action_item.get("action"))
        for action_item in normalized.get("workflow_actions")
        if isinstance(action_item, dict) and action_item.get("action")
    } if isinstance(normalized.get("workflow_actions"), list) else set()
    text = " ".join(
        [
            str(item.response_title or ""),
            str(item.source_text or ""),
            str(item.response_note or ""),
            category,
            action,
            review_action,
            primary_role,
        ]
    )

    if category == "bid_rule":
        if action == "clarification" or item.status == "to_clarify" or review_action == "clarification_question":
            return "clarification"
        return "business"
    if category == "qualification" or action == "qualification_material" or review_action == "prepare_qualification":
        return "qualification"
    if category == "technical_requirement":
        return "technical"
    if category == "pricing_constraint":
        if action == "legal_review" or primary_role == "法务" or review_action == "legal_assessment":
            return "legal"
        if action == "clarification" or item.status == "to_clarify" or review_action == "clarification_question":
            return "clarification"
        return "pricing"
    if category == "contract_clause":
        if action == "quote_allowance" or primary_role == "预算" or review_action == "budget_assessment":
            return "pricing"
        if action == "clarification" or item.status == "to_clarify" or review_action == "clarification_question":
            return "clarification"
        return "legal"
    if category == "document_checklist":
        return _document_checklist_section(text)

    if action == "clarification" or "clarification" in workflow_actions or item.status == "to_clarify" or review_action == "clarification_question":
        return "clarification"
    if action == "legal_review" or "legal_review" in workflow_actions or primary_role == "法务" or review_action == "legal_assessment":
        return "legal"
    if action == "quote_allowance" or "quote_allowance" in workflow_actions or primary_role == "预算" or review_action == "budget_assessment":
        return "pricing"
    if review_action == "write_technical_document" or primary_role == "技术" or "技术标" in text:
        return "technical"
    return "business"


def _group_title_for_item(item: TenderResponseItem) -> str:
    title = _raw_group_title_for_item(item)
    if not _is_generic_group_title(title):
        return title
    family = _generic_split_family_for_item(item)
    if family:
        return str(family["title"])[:80]
    return f"{title}（待二次拆分）"[:80]


def _raw_group_title_for_item(item: TenderResponseItem) -> str:
    normalized = _normalized(item)
    title = str(normalized.get("task_group_parent_title") or item.response_title or "未命名响应任务").strip()
    title = GROUP_SUFFIX_RE.sub("", title).strip()
    title = TECHNICAL_PREFIX_RE.sub("", title).strip()
    return title[:80] or "未命名响应任务"


def _is_generic_group_title(title: str) -> bool:
    text = str(title or "").strip()
    if not text:
        return True
    if _contains_any(text, GENERIC_GROUP_TITLE_EXCLUSION_KEYWORDS):
        return False
    return _contains_any(text, GENERIC_GROUP_TITLE_KEYWORDS)


def _generic_split_family_for_item(item: TenderResponseItem) -> dict[str, Any] | None:
    text = _generic_split_search_text(item)
    for family in GENERIC_SPLIT_FAMILIES:
        if _contains_any(text, tuple(family["keywords"])):
            return family
    return None


def _generic_split_search_text(item: TenderResponseItem) -> str:
    normalized = _normalized(item)
    checklist = normalized.get("done_checklist") if isinstance(normalized.get("done_checklist"), list) else []
    return " ".join(
        [
            str(item.response_title or ""),
            str(item.source_text or ""),
            str(item.response_note or ""),
            str(item.response_category or ""),
            str(item.response_action or ""),
            str(normalized.get("review_action") or ""),
            " ".join(str(value or "") for value in checklist),
        ]
    )


def _generic_split_metadata(title: str, items: list[TenderResponseItem]) -> dict[str, Any]:
    generic_items = [item for item in items if _is_generic_group_title(_raw_group_title_for_item(item))]
    if not generic_items:
        return {
            "split_from_generic_title": False,
            "original_group_title": None,
            "split_family": None,
            "split_confidence": None,
            "split_reason": None,
            "needs_secondary_split": False,
        }

    original_titles = _unique([_raw_group_title_for_item(item) for item in generic_items])
    matched_families = [_generic_split_family_for_item(item) for item in generic_items]
    matched_families = [family for family in matched_families if family]
    family_counter: Counter[str] = Counter(str(family["family"]) for family in matched_families)
    dominant_family_key = family_counter.most_common(1)[0][0] if family_counter else None
    dominant_family = next(
        (family for family in GENERIC_SPLIT_FAMILIES if family["family"] == dominant_family_key),
        None,
    )
    matched_ratio = len(matched_families) / max(len(generic_items), 1)
    confidence = round(0.62 + min(matched_ratio, 1.0) * 0.33, 2) if dominant_family else 0.35
    needs_secondary_split = not dominant_family or confidence < 0.75
    original_title = " / ".join(original_titles[:3])

    if dominant_family:
        reason = f"从泛化标题【{original_title}】按“{dominant_family['title']}”拆分：{dominant_family['reason']}"
        split_family = str(dominant_family["family"])
    else:
        reason = f"泛化标题【{original_title}】未匹配到稳定业务关键词，需人工或 LLM 辅助二次拆分。"
        split_family = "generic_unresolved"

    return {
        "split_from_generic_title": True,
        "original_group_title": original_title,
        "split_family": split_family,
        "split_confidence": confidence,
        "split_reason": reason,
        "needs_secondary_split": needs_secondary_split,
    }


def _missing_inputs_for_section(section_type: str, items: list[TenderResponseItem], aggregate: dict[str, Any]) -> list[str]:
    if not items:
        return ["暂无响应矩阵项；需要先生成响应矩阵或人工补充该章节任务。"]
    active_items = [item for item in items if item.status not in TERMINAL_STATUSES]
    missing: list[str] = []
    if not active_items:
        return []
    missing.append(f"仍有 {len(active_items)} 个响应任务未完成复核。")
    missing.append(_section_input_hint(section_type))
    if aggregate.get("risk_warnings") and section_type in {"pricing", "legal", "clarification"}:
        missing.append("存在高风险或第一波复核事项，需先完成内部决策。")
    return _unique(missing)[:5]


def _document_checklist_section(text: str) -> str:
    if _contains_any(text, QUALIFICATION_DOCUMENT_KEYWORDS):
        return "qualification"
    if _contains_any(text, RESPONSE_TABLE_DOCUMENT_KEYWORDS):
        return "business"
    if _contains_any(text, PRICING_DOCUMENT_KEYWORDS):
        return "pricing"
    if _contains_any(text, BUSINESS_DOCUMENT_KEYWORDS):
        return "business"
    if _contains_any(text, TECHNICAL_DOCUMENT_KEYWORDS):
        return "technical"
    return "attachment"


def _section_input_hint(section_type: str) -> str:
    hints = {
        "technical": "需补充企业施工组织、质量、安全、进度或材料控制等技术素材。",
        "qualification": "需绑定资质、业绩、人员、证照或授权附件。",
        "pricing": "需预算确认报价口径、价格预留、漏项责任和调价边界。",
        "legal": "需法务确认偏离意见、责任边界、可接受条件或投标决策建议。",
        "clarification": "需确认是否提交标前答疑，并形成可提交的问题文本。",
        "attachment": "需确认附件、签章、密封和格式文件是否齐全。",
        "business": "需经营确认商务承诺、递交规则和响应表位置。",
    }
    return hints.get(section_type, "需人工补充章节复核材料。")


def _draft_status(section_type: str, items: list[TenderResponseItem], missing_inputs: list[str]) -> str:
    if not items:
        return "blocked"
    if section_type == "business":
        business_decision = _business_generation_decision_code(items)
        if business_decision in {"risk_decision", "needs_input"}:
            return "needs_input"
        return "ready"
    for item in items:
        normalized = _normalized(item)
        if item.status in TERMINAL_STATUSES:
            continue
        if normalized.get("review_priority") == "P0":
            return "blocked"
        if section_type in {"legal", "clarification"} and item.risk_level == "high":
            return "blocked"
    if missing_inputs:
        return "needs_input"
    return "ready"


def _generation_decision(
    section_type: str,
    items: list[TenderResponseItem],
    status: str,
    missing_inputs: list[str],
) -> dict[str, Any]:
    if not items or status == "blocked":
        return {
            "code": "review_note",
            "label": "生成复核说明",
            "draft_mode": "blocked",
            "llm_eligible": False,
            "reason": "章节无可用响应项或存在阻断风险，只生成复核说明。",
        }
    if section_type == "business":
        business_decision = _business_generation_decision_code(items)
        if business_decision == "risk_decision":
            return {
                "code": "risk_decision",
                "label": "需风险决策",
                "draft_mode": "review_note",
                "llm_eligible": False,
                "reason": "商务规则涉及保证金、废标、重大偏差、违约责任或其他经营风险，先由负责人确认响应边界。",
            }
        if business_decision == "needs_input":
            return {
                "code": "needs_input",
                "label": "需补资料",
                "draft_mode": "placeholder",
                "llm_eligible": False,
                "reason": "商务规则仍需补充递交、签章、附件或响应表定位信息。",
            }
        if business_decision == "compliance_reminder":
            return {
                "code": "compliance_reminder",
                "label": "硬性合规提醒",
                "draft_mode": "formal",
                "llm_eligible": True,
                "reason": "该商务 P0 属于投标截止、递交、密封、评标办法等硬性规则，应准确响应和提醒执行，不作为风险决策处理。",
            }
        return {
            "code": "direct_response",
            "label": "可直接响应",
            "draft_mode": "formal",
            "llm_eligible": True,
            "reason": "商务规则低风险且证据可追溯，可生成正式响应草稿。",
        }
    if status == "ready":
        return {
            "code": "direct_response",
            "label": "可直接响应",
            "draft_mode": "formal",
            "llm_eligible": True,
            "reason": "章节已满足规则生成条件，可由 LLM 进一步润色正文。",
        }
    return {
        "code": "needs_input",
        "label": "需补资料",
        "draft_mode": "placeholder",
        "llm_eligible": False,
        "reason": "章节仍有人工输入或复核缺口，先生成带占位的规则草稿。",
    }


def _business_generation_decision_code(items: list[TenderResponseItem]) -> str:
    active_items = [item for item in items if item.status not in TERMINAL_STATUSES]
    if not active_items:
        return "direct_response"
    if any(_business_item_needs_risk_decision(item) for item in active_items):
        return "risk_decision"
    if any(_business_item_needs_input(item) for item in active_items):
        return "needs_input"
    if any(_business_item_is_compliance_reminder(item) for item in active_items):
        return "compliance_reminder"
    if all(item.response_category in {"bid_rule", "document_checklist"} for item in active_items):
        return "direct_response"
    return "needs_input"


def _business_rules_can_directly_respond(items: list[TenderResponseItem]) -> bool:
    if not items:
        return False
    return _business_generation_decision_code(items) in {"direct_response", "compliance_reminder"}


def _business_item_needs_risk_decision(item: TenderResponseItem) -> bool:
    text = _business_item_text(item)
    normalized = _normalized(item)
    if _business_item_has_strong_compliance_title(item):
        return False
    if _business_item_has_document_input_title(item):
        return False
    if _contains_any(text, BUSINESS_RISK_DECISION_KEYWORDS):
        return True
    risk_ids = _int_list((normalized.get("coverage") or {}).get("risk_ids") if isinstance(normalized.get("coverage"), dict) else [])
    if item.risk_level == "high" and not _business_item_is_compliance_reminder(item):
        return True
    return bool(risk_ids) and not _business_item_is_compliance_reminder(item)


def _business_item_needs_input(item: TenderResponseItem) -> bool:
    if _business_item_has_strong_compliance_title(item):
        return False
    if _business_item_has_document_input_title(item):
        return True
    text = _business_item_text(item)
    if _contains_any(text, BUSINESS_INPUT_KEYWORDS):
        return True
    return item.response_category == "document_checklist" and not _business_item_is_compliance_reminder(item)


def _business_item_is_compliance_reminder(item: TenderResponseItem) -> bool:
    text = _business_item_text(item)
    normalized = _normalized(item)
    return (
        _business_item_has_strong_compliance_title(item)
        or _contains_any(text, BUSINESS_COMPLIANCE_KEYWORDS)
        or normalized.get("review_priority") == "P0"
    )


def _business_item_has_strong_compliance_title(item: TenderResponseItem) -> bool:
    title = str(item.response_title or "")
    return _contains_any(title, BUSINESS_STRONG_COMPLIANCE_TITLE_KEYWORDS)


def _business_item_has_document_input_title(item: TenderResponseItem) -> bool:
    title = str(item.response_title or "")
    return _contains_any(title, BUSINESS_DOCUMENT_INPUT_TITLE_KEYWORDS)


def _business_item_text(item: TenderResponseItem) -> str:
    return " ".join(
        [
            str(item.response_title or ""),
            str(item.source_text or ""),
            str(item.response_note or ""),
            str(item.response_action or ""),
            str(item.response_category or ""),
        ]
    )


def _draft_mode_label(value: str) -> str:
    labels = {
        "formal": "正式可成稿",
        "placeholder": "可带占位起草",
        "review_note": "生成复核说明",
        "blocked": "暂不建议生成正文",
    }
    return labels.get(value, "暂不建议生成正文")


def _dominant_role(items: list[TenderResponseItem]) -> str | None:
    counter: Counter[str] = Counter()
    for item in items:
        role = response_item_primary_review_role(item) or item.owner_role
        if role:
            counter[str(role)] += 1
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _default_owner_role(section_type: str) -> str:
    for definition in SECTION_DEFINITIONS:
        if definition["section_type"] == section_type:
            return definition["owner_role"]
    return "经营"


def _source_summary(items: list[TenderResponseItem], section_type: str) -> str:
    if not items:
        return "该章节暂未从响应矩阵找到可落位事项。"
    action_count = Counter(item.response_action for item in items)
    high_risk_count = len([item for item in items if item.risk_level == "high"])
    main_action = action_count.most_common(1)[0][0] if action_count else "-"
    return f"来自 {len(items)} 个响应矩阵项，主动作 {main_action}，高风险 {high_risk_count} 项，章节类型 {section_type}。"


def _child_description(section_type: str, title: str, items: list[TenderResponseItem]) -> str:
    first = items[0] if items else None
    source = (first.response_note or first.source_text or "") if first else ""
    source = str(source).strip().replace("\n", " ")
    if len(source) > 120:
        source = source[:120] + "..."
    return source or f"围绕“{title}”形成投标书章节任务。"


def _risk_warning(item: TenderResponseItem) -> str:
    text = (item.response_note or item.source_text or "").strip().replace("\n", " ")
    if len(text) > 100:
        text = text[:100] + "..."
    return f"{item.response_title}：{text}" if text else item.response_title


def _normalized(item: TenderResponseItem) -> dict[str, Any]:
    value = loads_json(item.normalized_json, {}) if item.normalized_json else {}
    return value if isinstance(value, dict) else {}


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


def _unique_int(values: list[Any]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result


def _unique_str(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _clean_key(value: Any, *, limit: int = 64) -> str:
    text = re.sub(r"[^0-9A-Za-z_\-:\u4e00-\u9fff]+", "_", str(value or "")).strip("_")
    return text[:limit] or "item"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)
