from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.models.bidding import BidDraftSection
from app.services.bidding_draft_sections import _technical_composition_intent


BID_TECHNICAL_QUALITY_GATE_VERSION = "biz4c4_technical_formal_quality_gate_v2"
BID_TECHNICAL_FORMAL_PROFILE_VERSION = "biz4c4_p0_formal_profile_v1"
BID_TECHNICAL_REQUIREMENT_COVERAGE_VERSION = "biz4c4_p2_requirement_coverage_v1"
BID_TECHNICAL_REQUIREMENT_REINFORCEMENT_VERSION = "biz4c4_p3_requirement_reinforcement_v1"
BID_TECHNICAL_TEXT_HYGIENE_VERSION = "biz4c4_p10_text_hygiene_v1"
BID_TECHNICAL_TEMPLATE_REPETITION_VERSION = "biz4c4_p11_template_repetition_v1"

TEXT_HYGIENE_RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        "editorial_residue",
        r"本应原文|原文应为|以下为重写后的|请替换为正式内容",
        "正文包含编辑过程或改写提示残留。",
        "定位命中句并按招标文件事实重写，不得把编辑说明带入正式技术标。",
    ),
    (
        "dangling_project_scope",
        r"(?:^|[，。；：、\s])层办公区",
        "正文包含疑似被截断的施工区域名称。",
        "从项目事实表回填完整楼栋、楼层和区域名称后再导出。",
    ),
    (
        "duplicated_function_word",
        r"的的(?!确)|了了|和和|及及|与与|为为|在在|对对|按按|应应|将将|需需|并并|或或|由由",
        "正文包含连续重复虚词。",
        "修正命中句的拼接或模型输出残留，并复核相邻句是否仍然通顺。",
    ),
    (
        "unresolved_template_token",
        r"\{\{[^{}\n]{1,80}\}\}|\$\{[^{}\n]{1,80}\}|\[\[(?:TODO|TBD|PLACEHOLDER)[^\]\n]*\]\]",
        "正文包含尚未解析的模板变量。",
        "从项目事实表解析该变量；字段缺失时转为明确人工填写位并阻止正式稿输出。",
    ),
)

TECHNICAL_FORMAL_DEPTH_INTENTS = {
    "quality_schedule_commitment",
    "schedule_plan",
    "construction_organization",
    "safety_civil_fire",
    "quality_assurance",
    "temporary_power_plan",
    "material_procurement_plan",
    "key_difficulty_analysis",
    "site_facility_management",
    "waste_management_plan",
    "material_sample_plan",
    "competitive_enhancement",
}

TECHNICAL_REQUIREMENT_FACT_KEYS_BY_INTENT: dict[str, tuple[str, ...]] = {
    "quality_schedule_commitment": ("coordination", "material_procurement", "finished_product_protection", "safety_civilized"),
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

REQUIREMENT_COVERAGE_GAP_STATUSES = {"missing", "needs_manual_review"}

REQUIREMENT_REINFORCEMENT_TARGET_STATUSES = {"missing", "partially_covered"}
REQUIREMENT_REINFORCEMENT_SOURCE_TYPES = {"", "tender_document", "mixed", "tender_extracted_content"}
REQUIREMENT_REINFORCEMENT_SKIP_SOURCE_TYPES = {"manual_input", "enterprise_profile", "fixed_enterprise_material"}
REQUIREMENT_REINFORCEMENT_HARD_FACT_MARKERS = (
    "营业执照",
    "资质证明",
    "资格证明",
    "身份证明",
    "授权委托",
    "安全生产许可证",
    "注册证书",
    "证书编号",
    "获奖",
    "奖项",
    "业绩合同",
    "竣工验收证明",
)

REQUIREMENT_TERM_STOPWORDS = (
    "需要覆盖",
    "需覆盖",
    "应覆盖",
    "需要包含",
    "需包含",
    "应包含",
    "需要",
    "覆盖",
    "包含",
    "结合",
    "提供",
    "提交",
    "编制",
    "明确",
    "说明",
    "响应",
    "落实",
    "本项目",
    "本工程",
    "招标文件",
    "技术标",
    "投标",
    "要求",
    "相关",
    "内容",
    "章节",
    "方案",
    "措施",
    "计划",
)

REQUIREMENT_GENERIC_TERMS = {
    "项目",
    "工程",
    "施工",
    "管理",
    "资料",
    "文件",
    "要求",
    "内容",
    "相关",
    "方案",
    "措施",
    "计划",
}

PREVIOUS_PROJECT_LEAK_TERMS = (
    "东莞香港中心",
    "商业街区",
    "6#楼32F办公区",
    "6#楼32层办公区",
    "香港中心技术标提取",
)

FORMAL_FIXED_MATERIAL_SECTION_NOS = {
    "7.3.1",
    "7.3.2",
    "7.3.3",
    "7.3.4",
    "7.3.5",
    "7.3.7",
    "7.3.8",
    "7.3.18",
}

FORMAL_TABLE_REQUIRED_SECTION_NOS = {"7.3.8", "7.3.18"}

FORMAL_INTENT_DEPTH_MINIMUMS: dict[str, tuple[int, int]] = {
    "quality_schedule_commitment": (8, 750),
    "schedule_plan": (8, 700),
    "construction_organization": (12, 1200),
    "safety_civil_fire": (8, 700),
    "quality_assurance": (7, 650),
    "temporary_power_plan": (7, 650),
    "material_procurement_plan": (7, 650),
    "key_difficulty_analysis": (8, 750),
    "site_facility_management": (6, 520),
    "waste_management_plan": (6, 520),
    "material_sample_plan": (6, 520),
    "competitive_enhancement": (6, 520),
}

FORMAL_REQUIRED_TOPIC_GROUPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "quality_schedule_commitment": (
        ("质量承诺与验收标准", ("质量目标", "质量标准", "验收", "质量承诺")),
        ("工期承诺与节点控制", ("总工期", "工期", "节点", "进度")),
        ("组织资源与责任分工", ("项目经理", "组织", "资源", "责任")),
        ("检查纠偏与履约闭环", ("检查", "纠偏", "整改", "闭环")),
    ),
    "schedule_plan": (
        ("总工期与阶段安排", ("总工期", "合同工期", "阶段", "节点", "进度安排")),
        ("材料与设备进场", ("材料进场", "设备进场", "机具进场")),
        ("进度检查与纠偏", ("检查", "纠偏", "回补", "动态调整")),
        ("验收移交", ("验收", "移交", "竣工")),
    ),
    "construction_organization": (
        ("施工部署", ("施工部署", "总体部署", "施工区段", "施工顺序")),
        ("组织架构与职责", ("组织架构", "项目部", "职责", "管理人员")),
        ("施工工序与专业配合", ("工序", "交叉", "协调", "配合")),
        ("质量安全文明与成品保护", ("质量", "安全", "文明", "成品保护")),
        ("应急与沟通机制", ("应急", "沟通", "协调机制")),
    ),
    "temporary_power_plan": (
        ("配电系统", ("配电", "三级配电", "二级保护", "一机一闸")),
        ("线路与照明", ("线路", "照明", "机具用电")),
        ("巡检维护", ("巡检", "维护", "停送电", "检查")),
        ("应急处置", ("应急", "处置", "整改")),
    ),
    "material_procurement_plan": (
        ("采购计划", ("采购计划", "需求计划", "进场批次")),
        ("品牌规格样板", ("品牌", "规格", "样板", "报审")),
        ("验收与追溯", ("验收", "复核", "追溯", "台账")),
        ("替代与风险纠偏", ("替代", "风险", "纠偏", "审批")),
    ),
    "safety_civil_fire": (
        ("安全责任体系", ("安全责任", "安全管理", "责任体系")),
        ("教育交底", ("教育", "交底", "作业许可")),
        ("临电动火消防", ("临时用电", "动火", "消防", "防火")),
        ("检查整改应急", ("检查", "整改", "应急", "闭环")),
    ),
    "quality_assurance": (
        ("质量目标责任", ("质量目标", "质量管理", "责任")),
        ("样板与技术交底", ("样板", "技术交底")),
        ("材料报审与工序验收", ("材料", "报审", "工序", "隐蔽验收")),
        ("整改复验与资料闭环", ("整改", "复验", "资料", "闭环")),
    ),
    "key_difficulty_analysis": (
        ("重难点识别", ("重点", "难点", "风险", "识别")),
        ("针对性对策", ("对策", "措施", "控制")),
        ("检查纠偏", ("检查", "纠偏", "跟踪", "闭环")),
    ),
    "site_facility_management": (
        ("办公室工具间材料间", ("办公室", "工具间", "材料间")),
        ("消防临电文明", ("消防", "临电", "文明施工")),
        ("台账检查", ("台账", "检查", "责任")),
    ),
    "waste_management_plan": (
        ("分类清理堆放", ("分类", "清理", "堆放")),
        ("运输与路线", ("运输", "外运", "路线")),
        ("扬尘噪声安全文明", ("扬尘", "噪声", "安全", "文明")),
    ),
    "material_sample_plan": (
        ("样板清单规格", ("样板", "清单", "规格", "尺寸")),
        ("报审封样留存", ("报审", "封样", "留存")),
        ("采购进场联动", ("采购", "进场", "复核")),
    ),
    "competitive_enhancement": (
        ("进度组织优势", ("进度", "组织", "协同")),
        ("质量样板优势", ("质量", "样板", "精细化")),
        ("安全成品保护优势", ("安全", "文明", "成品保护")),
        ("服务与资料移交", ("服务", "资料", "移交", "持续改进")),
    ),
}

FORMAL_EXECUTION_LOOP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("责任主体", ("项目经理", "技术负责人", "责任岗位", "责任人", "质量安全管理人员", "资料员", "班组责任人")),
    ("实施流程", ("计划编制", "条件复核", "技术交底", "分区实施", "过程检查", "阶段验收", "移交复盘")),
    ("检查验收", ("日检查", "周复盘", "节点验收", "隐蔽验收", "工序验收", "验收记录", "检查验收")),
    ("整改纠偏", ("整改措施", "整改复查", "问题销项", "完成时限", "复查结论", "纠偏", "整改闭环")),
    ("资料闭环", ("资料闭环", "资料归档", "材料报审", "样板确认", "巡检记录", "会议纪要", "影像资料", "移交资料")),
)

FORMAL_FIXED_SECTION_SIGNALS: dict[str, tuple[str, ...]] = {
    "7.3.1": ("营业执照", "资质", "证书", "附件"),
    "7.3.2": ("法定代表人", "身份证明", "职务", "单位名称"),
    "7.3.3": ("授权委托", "委托代理人", "授权", "身份证"),
    "7.3.4": ("项目经理", "建造师", "注册证书", "资格证书"),
    "7.3.5": ("安全生产许可证", "其它投标资料", "附件"),
    "7.3.7": ("类似工程", "业绩", "合同", "竣工"),
    "7.3.8": ("项目经理", "技术负责人", "安全负责人", "简历", "资格证书"),
    "7.3.18": ("材料", "品牌", "拟采用", "推荐品牌"),
}


def validate_technical_bid_draft_for_formal_export(
    *,
    project_name: str | None,
    project_context: Mapping[str, Any] | None,
    project_facts: Mapping[str, Any] | None,
    drafts: Sequence[BidDraftSection],
    components_by_key: Mapping[str, Mapping[str, Any]],
    final_content_by_draft_id: Mapping[int, str],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    section_reports: list[dict[str, Any]] = []
    project_fact_section_reports: list[dict[str, Any]] = []
    allowed_project_text = _allowed_project_text(project_name, project_context, project_facts)
    expected_durations = _expected_schedule_durations(project_facts or project_context or {})
    quality_goal = _quality_goal(project_facts or project_context or {})
    work_zone_names = _work_zone_names(project_context, project_facts)
    technical_requirements = _technical_requirements(project_context, project_facts)
    text_hygiene_findings: list[dict[str, Any]] = []
    repetition_sections: list[dict[str, Any]] = []

    for draft in drafts:
        component = components_by_key.get(str(draft.section_key or ""), {}) or {}
        content = str(final_content_by_draft_id.get(draft.id) or draft.content_markdown or "")
        text = _plain_text(content)
        normalized_text = _normalize_for_contains(text)
        section = draft.section_title or draft.section_key or "-"
        intent = _section_intent(component, draft)
        section_no = _section_no(component, draft)
        paragraph_count = _paragraph_count(content)
        visible_length = len(re.sub(r"\s+", "", text))
        report = {
            "section": section,
            "section_no": section_no,
            "intent": intent,
            "formal_category": _formal_category(section_no, intent),
            "paragraph_count": paragraph_count,
            "visible_length": visible_length,
            "checks": [],
        }

        repetition_sections.append(
            {
                "draft_id": draft.id,
                "section": section,
                "section_no": section_no,
                "content": content,
            }
        )
        section_hygiene_findings = _text_hygiene_findings(content)
        for finding in section_hygiene_findings:
            item = _quality_item(
                str(finding["code"]),
                section,
                str(finding["issue"]),
                str(finding["suggestion"]),
                evidence={
                    "count": finding["count"],
                    "snippets": finding["snippets"],
                },
                severity="blocker",
            )
            blockers.append(item)
            text_hygiene_findings.append(item)
            report["checks"].append({"code": item["code"], "status": "blocked"})

        for term in PREVIOUS_PROJECT_LEAK_TERMS:
            normalized_term = _normalize_for_contains(term)
            if normalized_term in normalized_text and normalized_term not in allowed_project_text:
                item = _quality_item(
                    "previous_project_leakage",
                    section,
                    f"正文疑似包含非当前项目内容：{term}。",
                    "请重新生成该章节，或将正文中的项目名称、施工区域和来源痕迹改为当前项目事实。",
                    evidence=term,
                )
                blockers.append(item)
                report["checks"].append({"code": item["code"], "status": "blocked"})

        if intent in {"schedule_plan", "quality_schedule_commitment"}:
            observed_durations = _observed_schedule_durations(text)
            unexpected_durations = observed_durations - expected_durations
            if expected_durations and observed_durations and unexpected_durations:
                item = _quality_item(
                    "schedule_duration_conflict",
                    section,
                    f"招标文件工期为{_duration_text(expected_durations)}，正文却写为{_duration_text(unexpected_durations)}。",
                    "请以招标文件或项目事实抽取结果为准修正施工总进度计划中的工期表述。",
                    evidence={"expected": sorted(expected_durations), "observed": sorted(observed_durations), "unexpected": sorted(unexpected_durations)},
                )
                blockers.append(item)
                report["checks"].append({"code": item["code"], "status": "blocked"})
            elif expected_durations and not (expected_durations & observed_durations):
                warnings.append(
                    _quality_item(
                        "schedule_fact_not_used",
                        section,
                        f"已抽取到工期事实{_duration_text(expected_durations)}，但正文未明确响应。",
                        "建议在施工总进度计划中写明招标工期、阶段安排和纠偏机制。",
                    )
                )

        if intent == "quality_assurance" and quality_goal:
            if _quality_goal_conflicts(text, quality_goal):
                item = _quality_item(
                    "quality_goal_conflict",
                    section,
                    f"招标文件质量目标为“{quality_goal}”，正文质量目标存在冲突表述。",
                    "请按招标文件质量目标修正质量保证章节，避免写成另一个质量等级或奖项目标。",
                    evidence=quality_goal,
                )
                blockers.append(item)
                report["checks"].append({"code": item["code"], "status": "blocked"})
            elif quality_goal not in text:
                warnings.append(
                    _quality_item(
                        "quality_goal_not_used",
                        section,
                        f"已抽取到质量目标“{quality_goal}”，但正文未明确引用。",
                        "建议在质量保证章节中明确质量目标、验收标准、过程检查和整改闭环。",
                    )
                )

        if intent in {
            "construction_organization",
            "safety_civil_fire",
            "temporary_power_plan",
            "material_procurement_plan",
            "key_difficulty_analysis",
            "site_facility_management",
            "waste_management_plan",
            "material_sample_plan",
            "competitive_enhancement",
        } and work_zone_names:
            if not any(name and name in text for name in work_zone_names[:4]):
                warnings.append(
                    _quality_item(
                        "work_zone_fact_not_used",
                        section,
                        "已抽取到施工范围/区域，但正文未明显体现当前项目区域。",
                        "建议把施工部署、安全文明和交叉作业措施落到当前项目的具体施工区域。",
                        evidence=work_zone_names[:4],
                    )
                )

        for fact_key in TECHNICAL_REQUIREMENT_FACT_KEYS_BY_INTENT.get(intent, ()):
            fact = technical_requirements.get(fact_key)
            if not fact or _requirement_fact_reflected(normalized_text, fact):
                continue
            label = str(fact.get("label") or fact_key)
            warnings.append(
                _quality_item(
                    "technical_requirement_fact_not_used",
                    section,
                    f"已抽取到“{label}”专项要求，但正文未明显体现该项目事实。",
                    "建议将招标文件对应专项要求写入本章节的实施措施、责任分工、检查验收或闭环整改内容。",
                    evidence={
                        "fact_key": fact_key,
                        "label": label,
                        "summary": fact.get("summary"),
                        "keywords": fact.get("keywords") or [],
                    },
                )
            )
            report["checks"].append({"code": "technical_requirement_fact_not_used", "status": "warning"})

        project_fact_coverage = _project_fact_coverage_for_section(
            section=section,
            section_no=section_no,
            intent=intent,
            text=text,
            normalized_text=normalized_text,
            expected_durations=expected_durations,
            quality_goal=quality_goal,
            work_zone_names=work_zone_names,
            technical_requirements=technical_requirements,
        )
        report["project_fact_coverage"] = project_fact_coverage
        if project_fact_coverage["target_fact_count"]:
            project_fact_section_reports.append(project_fact_coverage)

        if intent in TECHNICAL_FORMAL_DEPTH_INTENTS and (paragraph_count < 5 or visible_length < 220):
            warnings.append(
                _quality_item(
                    "section_depth_weak",
                    section,
                    "章节正文偏短，正式技术标深度不足。",
                    "建议补足组织安排、流程措施、责任分工、检查验收和纠偏闭环。",
                    evidence={"paragraph_count": paragraph_count, "visible_length": visible_length},
                )
            )
            report["checks"].append({"code": "section_depth_weak", "status": "warning"})

        for item in _formal_profile_section_warnings(
            section=section,
            section_no=section_no,
            intent=intent,
            content=content,
            normalized_text=normalized_text,
            paragraph_count=paragraph_count,
            visible_length=visible_length,
        ):
            warnings.append(item)
            report["checks"].append({"code": item["code"], "status": "warning"})

        section_reports.append(report)

    template_repetition = _build_template_repetition_report(repetition_sections)
    for cluster in template_repetition["clusters"]:
        item = _quality_item(
            str(cluster["code"]),
            "全文",
            str(cluster["issue"]),
            str(cluster["suggestion"]),
            evidence={
                "occurrence_count": cluster["occurrence_count"],
                "section_count": cluster["section_count"],
                "sample": cluster["sample"],
                "sections": cluster["sections"],
            },
            severity=str(cluster["severity"]),
        )
        if cluster["severity"] == "blocker":
            blockers.append(item)
        else:
            warnings.append(item)

    requirement_coverage = _build_requirement_coverage_report(
        drafts=drafts,
        components_by_key=components_by_key,
        final_content_by_draft_id=final_content_by_draft_id,
    )
    coverage_warning = _requirement_coverage_quality_warning(requirement_coverage)
    if coverage_warning:
        warnings.append(coverage_warning)

    formal_profile = _build_formal_profile_report(
        drafts=drafts,
        components_by_key=components_by_key,
        section_reports=section_reports,
        blockers=blockers,
        warnings=warnings,
    )

    return {
        "version": BID_TECHNICAL_QUALITY_GATE_VERSION,
        "status": "blocked" if blockers else ("warning" if warnings else "pass"),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "section_reports": section_reports,
        "formal_profile": formal_profile,
        "text_hygiene": {
            "version": BID_TECHNICAL_TEXT_HYGIENE_VERSION,
            "status": "blocked" if text_hygiene_findings else "pass",
            "finding_count": len(text_hygiene_findings),
            "affected_section_count": len({item["section"] for item in text_hygiene_findings}),
            "findings": text_hygiene_findings[:50],
        },
        "template_repetition": template_repetition,
        "requirement_coverage": requirement_coverage,
        "project_fact_coverage": _build_project_fact_coverage_report(project_fact_section_reports),
    }


def quality_report_blocking_issue_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report.get("blockers") or []:
        if not isinstance(item, Mapping):
            continue
        issue = str(item.get("issue") or item.get("code") or "正式导出质量门禁未通过。")
        evidence_detail = _quality_issue_evidence_detail(item.get("evidence"))
        if evidence_detail:
            issue = f"{issue} {evidence_detail}"
        rows.append(
            {
                "code": str(item.get("code") or "quality_gate_blocker"),
                "section": str(item.get("section") or "-"),
                "issue": issue,
                "suggestion": str(item.get("suggestion") or "请根据质量门禁提示修正后重新导出。"),
            }
        )
    return rows


def _quality_issue_evidence_detail(evidence: Any) -> str:
    if not isinstance(evidence, Mapping):
        return ""
    sample = _clip_text(evidence.get("sample"), 120)
    sections = evidence.get("sections")
    if sample:
        section_labels = []
        if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)):
            for value in sections[:8]:
                label = str(value or "").strip().split(" ", 1)[0]
                if label and label not in section_labels:
                    section_labels.append(label)
        section_text = f"；涉及章节：{'、'.join(section_labels)}" if section_labels else ""
        return f"重复句：“{sample}”{section_text}。"
    snippets = evidence.get("snippets")
    if isinstance(snippets, Sequence) and not isinstance(snippets, (str, bytes)):
        snippet = next((_clip_text(value, 150) for value in snippets if str(value or "").strip()), "")
        if snippet:
            return f"命中片段：“{snippet}”。"
    return ""


def reinforce_technical_bid_section_requirement_coverage(
    *,
    draft: BidDraftSection,
    component: Mapping[str, Any],
    content: str,
    max_items: int = 6,
) -> dict[str, Any]:
    """Build export-time reinforcement text from P2 requirement coverage gaps.

    This helper only expands project/scheme requirements that came from the
    tender document. It deliberately skips manual-input and enterprise-profile
    requirements, because those are hard facts that must not be invented.
    """
    section_key = str(draft.section_key or "")
    if not section_key:
        empty_coverage = {
            "status": "pass",
            "requirement_count": 0,
            "covered_count": 0,
            "partially_covered_count": 0,
            "missing_count": 0,
            "needs_manual_review_count": 0,
            "coverage_rate": 1.0,
            "items": [],
        }
        return _requirement_reinforcement_result(
            content,
            [],
            skipped_count=0,
            skipped_items=[],
            changed=False,
            coverage_before=empty_coverage,
            coverage_after=empty_coverage,
        )

    coverage = _build_requirement_coverage_report(
        drafts=[draft],
        components_by_key={section_key: component},
        final_content_by_draft_id={draft.id: content},
    )
    section_no = _section_no(component, draft)
    supplement_items: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    skipped_count = 0
    for item in coverage.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        source_kind = str(item.get("source_kind") or "")
        source_type = str(item.get("source_type") or "")
        status = str(item.get("coverage_status") or "")
        is_hard_fact = _requirement_reinforcement_is_hard_fact(item)
        if source_type in REQUIREMENT_REINFORCEMENT_SKIP_SOURCE_TYPES or is_hard_fact:
            if status in REQUIREMENT_COVERAGE_GAP_STATUSES:
                skipped_count += 1
                skipped_items.append(_requirement_reinforcement_skipped_item(item, is_hard_fact=is_hard_fact))
            continue
        if source_kind != "information_need":
            continue
        if source_type not in REQUIREMENT_REINFORCEMENT_SOURCE_TYPES:
            continue
        if status not in REQUIREMENT_REINFORCEMENT_TARGET_STATUSES:
            continue
        terms = _requirement_reinforcement_terms(item)
        if not terms:
            continue
        supplement_items.append(
            {
                "requirement_key": item.get("requirement_key"),
                "section_no": section_no,
                "requirement_title": item.get("requirement_title") or "招标要求",
                "coverage_status": status,
                "terms": terms,
            }
        )
        if len(supplement_items) >= max_items:
            break

    if not supplement_items or "招标要求深化响应" in str(content or ""):
        return _requirement_reinforcement_result(
            content,
            supplement_items,
            skipped_count=skipped_count,
            skipped_items=skipped_items,
            changed=False,
            coverage_before=coverage,
            coverage_after=coverage,
        )

    reinforced_content = _append_requirement_reinforcement_section(content, supplement_items)
    coverage_after = _build_requirement_coverage_report(
        drafts=[draft],
        components_by_key={section_key: component},
        final_content_by_draft_id={draft.id: reinforced_content},
    )
    return _requirement_reinforcement_result(
        reinforced_content,
        supplement_items,
        skipped_count=skipped_count,
        skipped_items=skipped_items,
        changed=True,
        coverage_before=coverage,
        coverage_after=coverage_after,
    )


def _requirement_reinforcement_result(
    content: str,
    supplement_items: Sequence[Mapping[str, Any]],
    *,
    skipped_count: int,
    skipped_items: Sequence[Mapping[str, Any]],
    changed: bool,
    coverage_before: Mapping[str, Any],
    coverage_after: Mapping[str, Any],
) -> dict[str, Any]:
    transitions = _requirement_reinforcement_transitions(
        supplement_items=supplement_items,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
    )
    return {
        "version": BID_TECHNICAL_REQUIREMENT_REINFORCEMENT_VERSION,
        "status": "applied" if changed else ("manual_review_required" if skipped_count else "no_action"),
        "content": content,
        "changed": changed,
        "supplement_count": len(supplement_items),
        "skipped_manual_review_count": skipped_count,
        "coverage_before": _requirement_reinforcement_coverage_summary(coverage_before),
        "coverage_after": _requirement_reinforcement_coverage_summary(coverage_after),
        "coverage_transitions": transitions,
        "skipped_items": list(skipped_items)[:20],
        "supplement_items": [
            {
                "requirement_key": item.get("requirement_key"),
                "section_no": item.get("section_no"),
                "requirement_title": item.get("requirement_title"),
                "coverage_status": item.get("coverage_status"),
                "terms": list(item.get("terms") or [])[:12],
            }
            for item in supplement_items
        ],
    }


def _requirement_reinforcement_coverage_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status") or "pass",
        "requirement_count": report.get("requirement_count") or 0,
        "covered_count": report.get("covered_count") or 0,
        "partially_covered_count": report.get("partially_covered_count") or 0,
        "missing_count": report.get("missing_count") or 0,
        "needs_manual_review_count": report.get("needs_manual_review_count") or 0,
        "coverage_rate": report.get("coverage_rate", 1.0),
    }


def _requirement_reinforcement_transitions(
    *,
    supplement_items: Sequence[Mapping[str, Any]],
    coverage_before: Mapping[str, Any],
    coverage_after: Mapping[str, Any],
) -> list[dict[str, Any]]:
    before_by_key = _requirement_coverage_items_by_key(coverage_before)
    after_by_key = _requirement_coverage_items_by_key(coverage_after)
    transitions: list[dict[str, Any]] = []
    for item in supplement_items:
        key = str(item.get("requirement_key") or "")
        before = before_by_key.get(key, {})
        after = after_by_key.get(key, {})
        transitions.append(
            {
                "requirement_key": key,
                "section_no": item.get("section_no"),
                "requirement_title": item.get("requirement_title"),
                "before_status": before.get("coverage_status") or item.get("coverage_status"),
                "after_status": after.get("coverage_status") or item.get("coverage_status"),
                "matched_terms_after": after.get("matched_terms") or [],
                "missing_terms_after": after.get("missing_terms") or [],
                "terms": list(item.get("terms") or [])[:12],
            }
        )
    return transitions


def _requirement_coverage_items_by_key(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in report.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("requirement_key") or "")
        if key:
            result[key] = item
    return result


def _requirement_reinforcement_skipped_item(item: Mapping[str, Any], *, is_hard_fact: bool) -> dict[str, Any]:
    return {
        "requirement_key": item.get("requirement_key"),
        "section_no": item.get("section_no"),
        "section": item.get("section"),
        "requirement_title": item.get("requirement_title"),
        "coverage_status": item.get("coverage_status"),
        "source_type": item.get("source_type"),
        "reason": "hard_fact" if is_hard_fact else "manual_or_enterprise_profile",
        "missing_terms": item.get("missing_terms") or [],
    }


def _requirement_reinforcement_terms(item: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in list(item.get("missing_terms") or []) + list(item.get("matched_terms") or []):
        term = _clean_requirement_term(value)
        if not term:
            continue
        normalized = _normalize_for_contains(term)
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
        if len(terms) >= 6:
            break
    return terms


def _requirement_reinforcement_is_hard_fact(item: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            item.get("requirement_title"),
            item.get("requirement_text"),
            item.get("source_evidence"),
            " ".join(str(term) for term in item.get("missing_terms") or []),
        )
    )
    return any(marker in text for marker in REQUIREMENT_REINFORCEMENT_HARD_FACT_MARKERS)


def _append_requirement_reinforcement_section(content: str, supplement_items: Sequence[Mapping[str, Any]]) -> str:
    base = str(content or "").rstrip()
    lines = [base, "", "## 招标要求深化响应"]
    for index, item in enumerate(supplement_items, start=1):
        title = _clip_text(item.get("requirement_title") or "招标要求", 80)
        terms = [str(term).strip() for term in item.get("terms") or [] if str(term).strip()]
        paragraph = _requirement_reinforcement_paragraph(index, title, terms)
        if paragraph:
            lines.extend(["", paragraph])
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def _requirement_reinforcement_paragraph(index: int, title: str, terms: Sequence[str]) -> str:
    terms_text = "、".join(_clip_text(term, 30) for term in terms if str(term).strip())
    if not terms_text:
        return ""
    return (
        f"（{index}）{title}：针对招标文件关于{terms_text}的要求，"
        "本章从施工准备、过程实施、检查验收和资料闭环四个方面深化响应。"
        f"围绕{terms_text}，项目部在进场前组织技术交底和作业条件复核，"
        "施工过程中按责任分工进行专业协调、质量巡查、安全文明检查和问题整改，"
        "完成后形成验收记录、影像资料、整改闭环台账及移交资料，"
        "确保该项要求落实到现场管理和竣工移交全过程。"
    )


def _formal_profile_section_warnings(
    *,
    section: str,
    section_no: str,
    intent: str,
    content: str,
    normalized_text: str,
    paragraph_count: int,
    visible_length: int,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS and not _fixed_section_has_formal_signal(section_no, content):
        warnings.append(
            _quality_item(
                "formal_fixed_material_signal_weak",
                section,
                "固定资料章节缺少明显的正式表单、资料清单或附件说明特征。",
                "请核对营业执照、授权委托、证书、业绩、人员或品牌资料是否已从企业资料库绑定，并按正式投标资料顺序装配。",
                evidence={"section_no": section_no, "visible_length": visible_length},
            )
        )

    if section_no in FORMAL_TABLE_REQUIRED_SECTION_NOS and not _has_markdown_table(content):
        warnings.append(
            _quality_item(
                "formal_table_structure_missing",
                section,
                "正式标表格类章节未检测到可编辑表格结构。",
                "请核对人员配备/简历或材料品牌表是否以表格形式输出；如仅以图片附件呈现，需人工复核表格完整性和附件顺序。",
                evidence={"section_no": section_no},
            )
        )

    minimum = FORMAL_INTENT_DEPTH_MINIMUMS.get(intent)
    if minimum:
        min_paragraphs, min_visible_length = minimum
        if paragraph_count < min_paragraphs or visible_length < min_visible_length:
            warnings.append(
                _quality_item(
                    "formal_section_depth_weak",
                    section,
                    "章节未达到正式技术标画像建议的正文深度。",
                    "请补强措施、责任分工、实施流程、检查验收、问题纠偏和资料闭环，避免停留在原则性表述。",
                    evidence={
                        "intent": intent,
                        "paragraph_count": paragraph_count,
                        "min_paragraph_count": min_paragraphs,
                        "visible_length": visible_length,
                        "min_visible_length": min_visible_length,
                    },
                )
            )

    missing_topics = _missing_formal_topics(intent, normalized_text)
    if missing_topics:
        warnings.append(
            _quality_item(
                "formal_required_topic_missing",
                section,
                "章节缺少正式技术标画像建议覆盖的专项内容。",
                "请按缺失专项补充对应措施，并在正文中体现责任、流程、检查和闭环。",
                evidence={"intent": intent, "missing_topics": missing_topics[:8]},
            )
        )
    missing_execution_loop_groups = _missing_formal_execution_loop_groups(intent, normalized_text)
    if missing_execution_loop_groups:
        warnings.append(
            _quality_item(
                "formal_execution_loop_missing",
                section,
                "章节缺少正式技术标应具备的责任、流程、验收、整改或资料闭环骨架。",
                "请补强责任主体、实施流程、检查验收、整改纠偏和资料闭环，使章节措施从原则性描述落到可执行管理链条。",
                evidence={"intent": intent, "missing_execution_loop_groups": missing_execution_loop_groups[:8]},
            )
        )
    return warnings


def _build_formal_profile_report(
    *,
    drafts: Sequence[BidDraftSection],
    components_by_key: Mapping[str, Mapping[str, Any]],
    section_reports: Sequence[Mapping[str, Any]],
    blockers: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    component_keys = set(str(key) for key in components_by_key.keys())
    draft_keys = set(str(draft.section_key or "") for draft in drafts)
    missing_component_keys = sorted(key for key in component_keys if key and key not in draft_keys)
    section_nos = [str(item.get("section_no") or "") for item in section_reports if item.get("section_no")]
    fixed_sections = [item for item in section_nos if item in FORMAL_FIXED_MATERIAL_SECTION_NOS]
    scheme_sections = [
        str(item.get("section_no") or "")
        for item in section_reports
        if str(item.get("intent") or "") in TECHNICAL_FORMAL_DEPTH_INTENTS
    ]
    warning_code_counts = _code_counts(warnings)
    checks = [
        {
            "code": "composition_draft_coverage",
            "status": "blocked" if missing_component_keys else "pass",
            "message": "技术标组成项均已生成草稿。" if not missing_component_keys else "存在技术标组成项未生成草稿。",
            "evidence": {"missing_component_keys": missing_component_keys[:20]},
        },
        {
            "code": "fixed_material_sections",
            "status": "warning" if warning_code_counts.get("formal_fixed_material_signal_weak") else "pass",
            "message": "固定资料章节具备正式资料特征。" if not warning_code_counts.get("formal_fixed_material_signal_weak") else "部分固定资料章节正式资料特征偏弱。",
            "evidence": {"section_count": len(fixed_sections)},
        },
        {
            "code": "table_sections",
            "status": "warning" if warning_code_counts.get("formal_table_structure_missing") else "pass",
            "message": "表格类章节具备可检测表格结构。" if not warning_code_counts.get("formal_table_structure_missing") else "人员/品牌等表格类章节需要核对表格结构。",
        },
        {
            "code": "scheme_section_depth",
            "status": "warning"
            if warning_code_counts.get("formal_section_depth_weak") or warning_code_counts.get("formal_required_topic_missing")
            else "pass",
            "message": "方案类章节达到当前正式标画像深度检查。" if not (
                warning_code_counts.get("formal_section_depth_weak") or warning_code_counts.get("formal_required_topic_missing")
            ) else "部分方案类章节仍存在深度或专项覆盖缺口。",
            "evidence": {"section_count": len(scheme_sections)},
        },
        {
            "code": "scheme_execution_loop",
            "status": "warning" if warning_code_counts.get("formal_execution_loop_missing") else "pass",
            "message": "方案类章节具备责任、流程、验收、整改和资料闭环骨架。"
            if not warning_code_counts.get("formal_execution_loop_missing")
            else "部分方案类章节责任、流程、验收、整改或资料闭环骨架不足。",
            "evidence": {
                "formal_execution_loop_missing": warning_code_counts.get("formal_execution_loop_missing", 0),
            },
        },
        {
            "code": "project_fact_coverage",
            "status": "warning"
            if any(
                warning_code_counts.get(code)
                for code in ("schedule_fact_not_used", "quality_goal_not_used", "work_zone_fact_not_used", "technical_requirement_fact_not_used")
            )
            else "pass",
            "message": "当前项目工期、质量、施工区域和专项要求已进入对应方案章节。"
            if not any(
                warning_code_counts.get(code)
                for code in ("schedule_fact_not_used", "quality_goal_not_used", "work_zone_fact_not_used", "technical_requirement_fact_not_used")
            )
            else "部分项目事实尚未进入对应方案章节。",
            "evidence": {
                "schedule_fact_not_used": warning_code_counts.get("schedule_fact_not_used", 0),
                "quality_goal_not_used": warning_code_counts.get("quality_goal_not_used", 0),
                "work_zone_fact_not_used": warning_code_counts.get("work_zone_fact_not_used", 0),
                "technical_requirement_fact_not_used": warning_code_counts.get("technical_requirement_fact_not_used", 0),
            },
        },
    ]
    return {
        "version": BID_TECHNICAL_FORMAL_PROFILE_VERSION,
        "status": "blocked"
        if blockers or missing_component_keys
        else ("warning" if warnings else "pass"),
        "expected_component_count": len(component_keys),
        "generated_section_count": len(drafts),
        "recognized_section_numbers": sorted(set(section_nos), key=_section_no_sort_key),
        "fixed_material_section_count": len(fixed_sections),
        "scheme_section_count": len(scheme_sections),
        "missing_component_count": len(missing_component_keys),
        "missing_component_keys": missing_component_keys[:20],
        "warning_code_counts": warning_code_counts,
        "checks": checks,
    }


def _build_requirement_coverage_report(
    *,
    drafts: Sequence[BidDraftSection],
    components_by_key: Mapping[str, Mapping[str, Any]],
    final_content_by_draft_id: Mapping[int, str],
) -> dict[str, Any]:
    draft_by_key = {str(draft.section_key or ""): draft for draft in drafts}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def component_sort_key(item: tuple[str, Mapping[str, Any]]) -> tuple[tuple[int, int, str], str]:
        section_key, component = item
        return (_section_no_sort_key(_section_no_from_component_key(component, section_key)), section_key)

    for section_key, component in sorted(components_by_key.items(), key=component_sort_key):
        if not isinstance(component, Mapping):
            continue
        draft = draft_by_key.get(str(section_key))
        section_no = _section_no_from_component_key(component, section_key)
        section = _coverage_section_label(component, draft, section_key)
        content = ""
        if draft is not None:
            content = str(final_content_by_draft_id.get(draft.id) or draft.content_markdown or "")
        text = _plain_text(content)
        normalized_text = _normalize_for_contains(text)
        paragraph_count = _paragraph_count(content)
        visible_length = len(re.sub(r"\s+", "", text))

        for requirement_index, requirement in enumerate(_coverage_requirement_records(component), start=1):
            dedupe_key = _requirement_dedupe_key(section_key, requirement)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            terms = _requirement_terms(requirement)
            status, matched_terms, missing_terms = _requirement_coverage_status(
                requirement=requirement,
                section_no=section_no,
                content=content,
                normalized_text=normalized_text,
                paragraph_count=paragraph_count,
                visible_length=visible_length,
                terms=terms,
            )
            items.append(
                {
                    "requirement_key": _coverage_requirement_key(section_key, requirement, requirement_index),
                    "section_key": section_key,
                    "section_no": section_no,
                    "section": section,
                    "requirement_title": requirement.get("title") or section,
                    "requirement_text": _clip_text(requirement.get("text") or requirement.get("title") or "", 500),
                    "source_kind": requirement.get("source_kind") or "component",
                    "source_type": requirement.get("source_type") or "",
                    "classification": component.get("classification") or "",
                    "coverage_status": status,
                    "matched_terms": matched_terms[:12],
                    "missing_terms": missing_terms[:12],
                    "source_evidence": _clip_text(_first_evidence_text(requirement.get("source_evidence")), 300),
                    "evidence": {
                        "draft_id": draft.id if draft is not None else None,
                        "paragraph_count": paragraph_count,
                        "visible_length": visible_length,
                    },
                }
            )

    status_counts = _coverage_status_counts(items)
    gap_count = sum(status_counts.get(status, 0) for status in REQUIREMENT_COVERAGE_GAP_STATUSES)
    partial_count = status_counts.get("partially_covered", 0)
    requirement_count = len(items)
    covered_count = status_counts.get("covered", 0)
    problem_items = [
        _requirement_problem_summary(item)
        for item in items
        if item.get("coverage_status") in REQUIREMENT_COVERAGE_GAP_STATUSES
        or item.get("coverage_status") == "partially_covered"
    ]
    checks = [
        {
            "code": "requirement_item_extraction",
            "status": "pass" if requirement_count else "warning",
            "message": "已从技术标组成项提取逐条覆盖要求。" if requirement_count else "未提取到可检查的逐条覆盖要求。",
            "evidence": {"requirement_count": requirement_count},
        },
        {
            "code": "requirement_gap_detection",
            "status": "warning" if gap_count else "pass",
            "message": "逐条覆盖矩阵存在未覆盖或需人工复核的要求。" if gap_count else "未发现未覆盖或需人工复核的要求。",
            "evidence": {
                "missing_count": status_counts.get("missing", 0),
                "needs_manual_review_count": status_counts.get("needs_manual_review", 0),
            },
        },
        {
            "code": "requirement_partial_detection",
            "status": "warning" if partial_count else "pass",
            "message": "部分要求仅被正文局部响应。" if partial_count else "未发现仅局部响应的要求。",
            "evidence": {"partially_covered_count": partial_count},
        },
    ]
    return {
        "version": BID_TECHNICAL_REQUIREMENT_COVERAGE_VERSION,
        "status": "warning" if gap_count or partial_count else "pass",
        "requirement_count": requirement_count,
        "covered_count": covered_count,
        "partially_covered_count": partial_count,
        "missing_count": status_counts.get("missing", 0),
        "needs_manual_review_count": status_counts.get("needs_manual_review", 0),
        "coverage_rate": round(covered_count / requirement_count, 4) if requirement_count else 1.0,
        "status_counts": status_counts,
        "problem_items": problem_items[:30],
        "checks": checks,
        "items": items,
    }


def _requirement_coverage_quality_warning(report: Mapping[str, Any]) -> dict[str, Any] | None:
    gap_count = int(report.get("missing_count") or 0) + int(report.get("needs_manual_review_count") or 0)
    partial_count = int(report.get("partially_covered_count") or 0)
    if gap_count <= 0 and partial_count <= 0:
        return None
    if gap_count > 0:
        return _quality_item(
            "technical_requirement_coverage_gap",
            "技术标逐条要求覆盖矩阵",
            f"逐条覆盖矩阵发现 {gap_count} 项招标要求未覆盖或仍需人工复核。",
            "建议按 problem_items 定位对应章节，补齐招标要求的措施、责任、流程、检查或资料证据后再正式导出。",
            evidence={
                "version": report.get("version"),
                "missing_count": report.get("missing_count"),
                "needs_manual_review_count": report.get("needs_manual_review_count"),
                "sample_items": report.get("problem_items", [])[:10],
            },
        )
    return _quality_item(
        "technical_requirement_coverage_partial",
        "技术标逐条要求覆盖矩阵",
        f"逐条覆盖矩阵发现 {partial_count} 项招标要求仅被局部响应。",
        "建议补强相关章节正文，使招标要求不只出现在标题或零散关键词中，而是落实到实施安排和检查闭环。",
        evidence={
            "version": report.get("version"),
            "partially_covered_count": partial_count,
            "sample_items": report.get("problem_items", [])[:10],
        },
    )


def _coverage_requirement_records(component: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_item_no = str(component.get("source_item_no") or "").strip()
    component_title = str(component.get("component_title") or "").strip() or source_item_no or "技术标组成项"
    records: list[dict[str, Any]] = [
        {
            "source_kind": "component",
            "source_type": component.get("classification") or "",
            "source_item_no": source_item_no,
            "title": component_title,
            "text": _first_non_empty(
                _first_evidence_text(component.get("source_evidence")),
                component.get("classification_reason"),
                component_title,
            ),
            "query": component_title,
            "source_evidence": component.get("source_evidence") or [],
        }
    ]
    for index, need in enumerate(component.get("information_needs") or [], start=1):
        if not isinstance(need, Mapping):
            continue
        title = str(need.get("need_title") or need.get("title") or need.get("need_key") or f"信息需求{index}").strip()
        text = _first_non_empty(need.get("polished_text"), need.get("query"), need.get("reason"), title)
        records.append(
            {
                "source_kind": "information_need",
                "source_type": need.get("source_type") or component.get("classification") or "",
                "source_item_no": source_item_no,
                "title": title,
                "text": text,
                "query": need.get("query") or text,
                "source_evidence": need.get("source_evidence") or component.get("source_evidence") or [],
            }
        )
    return records


def _requirement_coverage_status(
    *,
    requirement: Mapping[str, Any],
    section_no: str,
    content: str,
    normalized_text: str,
    paragraph_count: int,
    visible_length: int,
    terms: Sequence[str],
) -> tuple[str, list[str], list[str]]:
    if visible_length < 12:
        return "missing", [], list(terms)

    source_kind = str(requirement.get("source_kind") or "")
    source_type = str(requirement.get("source_type") or "")
    if source_kind == "component":
        if visible_length >= 120 and paragraph_count >= 2:
            return "covered", [], []
        if visible_length >= 40:
            return "partially_covered", [], []
        return "missing", [], list(terms)

    if source_type == "manual_input" and _has_pending_marker(content):
        return "needs_manual_review", [], list(terms)

    matched_terms = [
        term for term in terms
        if _normalize_for_contains(term) and _normalize_for_contains(term) in normalized_text
    ]
    missing_terms = [term for term in terms if term not in matched_terms]

    if source_type in {"enterprise_profile", "fixed_enterprise_material"} and _fixed_section_has_formal_signal(section_no, content):
        if not terms or matched_terms or visible_length >= 160:
            return "covered", matched_terms, missing_terms

    if not terms:
        if source_type == "manual_input":
            return "needs_manual_review", [], []
        if visible_length >= 120:
            return "covered", [], []
        return "partially_covered", [], []

    required_match_count = 1 if len(terms) <= 2 else 2
    if len(matched_terms) >= required_match_count:
        return "covered", matched_terms, missing_terms
    if matched_terms:
        return "partially_covered", matched_terms, missing_terms
    if source_type == "manual_input":
        return "needs_manual_review", matched_terms, missing_terms
    return "missing", matched_terms, missing_terms


def _requirement_terms(requirement: Mapping[str, Any]) -> list[str]:
    raw_values = [
        requirement.get("title"),
        requirement.get("text"),
        requirement.get("query"),
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        text = str(value or "").strip()
        if not text:
            continue
        text = re.sub(r"\d+(?:\.\d+)+", " ", text)
        for piece in re.split(r"[\s,，、。；;：:（）()\[\]【】《》<>/|]+", text):
            if not piece:
                continue
            for sub_piece in re.split(r"(?<=[\u4e00-\u9fff])[及与和并或](?=[\u4e00-\u9fff])", piece):
                term = _clean_requirement_term(sub_piece)
                if not term:
                    continue
                normalized = _normalize_for_contains(term)
                if normalized in seen:
                    continue
                seen.add(normalized)
                terms.append(term)
                if len(terms) >= 16:
                    return terms
    return terms


def _clean_requirement_term(value: Any) -> str:
    term = re.sub(r"[^\w#\u4e00-\u9fff]+", "", str(value or "")).strip()
    if not term:
        return ""
    for stopword in REQUIREMENT_TERM_STOPWORDS:
        term = term.replace(stopword, "")
    term = term.strip()
    normalized = _normalize_for_contains(term)
    if len(normalized) < 2:
        return ""
    if term in REQUIREMENT_GENERIC_TERMS:
        return ""
    if re.fullmatch(r"[0-9._#-]+", term):
        return ""
    return term[:40]


def _section_no_from_component_key(component: Mapping[str, Any], section_key: str) -> str:
    for value in (
        section_key,
        component.get("source_item_no") if isinstance(component, Mapping) else None,
        component.get("component_title") if isinstance(component, Mapping) else None,
    ):
        match = re.search(r"7[_\.]3[_\.](\d+)", str(value or ""))
        if match:
            return f"7.3.{match.group(1)}"
    return ""


def _coverage_section_label(
    component: Mapping[str, Any],
    draft: BidDraftSection | None,
    section_key: str,
) -> str:
    if draft is not None and (draft.section_title or draft.section_key):
        return str(draft.section_title or draft.section_key)
    source_item_no = str(component.get("source_item_no") or "").strip()
    component_title = str(component.get("component_title") or "").strip()
    return " ".join(item for item in (source_item_no, component_title) if item).strip() or section_key or "-"


def _coverage_requirement_key(section_key: str, requirement: Mapping[str, Any], index: int) -> str:
    source_kind = str(requirement.get("source_kind") or "component")
    title_key = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(requirement.get("title") or "")).strip("_")
    return f"{section_key}:{source_kind}:{title_key or index}"[:180]


def _requirement_dedupe_key(section_key: str, requirement: Mapping[str, Any]) -> str:
    raw = "|".join(
        [
            section_key,
            str(requirement.get("source_kind") or ""),
            str(requirement.get("source_type") or ""),
            str(requirement.get("title") or ""),
            str(requirement.get("text") or ""),
        ]
    )
    return _normalize_for_contains(raw)[:260]


def _coverage_status_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        status = str(item.get("coverage_status") or "")
        if not status:
            continue
        result[status] = result.get(status, 0) + 1
    return result


def _requirement_problem_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requirement_key": item.get("requirement_key"),
        "section_no": item.get("section_no"),
        "section": item.get("section"),
        "requirement_title": item.get("requirement_title"),
        "coverage_status": item.get("coverage_status"),
        "missing_terms": item.get("missing_terms") or [],
        "matched_terms": item.get("matched_terms") or [],
    }


def _first_evidence_text(evidence: Any) -> str:
    if isinstance(evidence, Mapping):
        for key in ("original_text", "text", "content", "source_text", "summary"):
            value = str(evidence.get(key) or "").strip()
            if value:
                return value
        return ""
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
        for item in evidence:
            value = _first_evidence_text(item)
            if value:
                return value
    return str(evidence or "").strip() if evidence and not isinstance(evidence, (list, tuple, dict)) else ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _clip_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _text_hygiene_findings(content: str) -> list[dict[str, Any]]:
    text = _plain_text(content)
    findings: list[dict[str, Any]] = []
    for code, pattern, issue, suggestion in TEXT_HYGIENE_RULES:
        matches = list(re.finditer(pattern, text, flags=re.I | re.M))
        if not matches:
            continue
        findings.append(
            {
                "code": code,
                "count": len(matches),
                "issue": f"{issue}（{len(matches)}处）",
                "suggestion": suggestion,
                "snippets": _matching_text_snippets(text, matches),
            }
        )
    return findings


def _matching_text_snippets(text: str, matches: Sequence[re.Match[str]], *, limit: int = 5) -> list[str]:
    snippets: list[str] = []
    for match in matches[:limit]:
        start = max(0, match.start() - 32)
        end = min(len(text), match.end() + 32)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet)
    return snippets


def _build_template_repetition_report(sections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sentence_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    section_number_leads: list[dict[str, Any]] = []
    sentence_count = 0

    for section in sections:
        section_name = str(section.get("section") or section.get("section_no") or "-")
        for sentence in _repetition_sentences(str(section.get("content") or "")):
            sentence_count += 1
            occurrence = {
                "section": section_name,
                "section_no": str(section.get("section_no") or ""),
                "sentence": sentence,
            }
            canonical = _canonical_repetition_sentence(sentence)
            if len(canonical) >= 18:
                sentence_occurrences[canonical].append(occurrence)
            if re.search(r"围绕\s*[“\"']?\s*7[._]3[._]\d+", sentence):
                section_number_leads.append(occurrence)

    clusters: list[dict[str, Any]] = []
    for occurrences in sentence_occurrences.values():
        sections_used = _unique_text([item["section"] for item in occurrences])
        if len(occurrences) < 3 or len(sections_used) < 2:
            continue
        severity = "blocker" if len(occurrences) >= 8 or (len(occurrences) >= 5 and len(sections_used) >= 5) else "warning"
        clusters.append(
            {
                "code": "high_frequency_boilerplate" if severity == "blocker" else "repeated_bid_sentence",
                "severity": severity,
                "occurrence_count": len(occurrences),
                "section_count": len(sections_used),
                "sections": sections_used[:12],
                "sample": _clip_text(occurrences[0]["sentence"], 160),
                "issue": f"同一完整句在全文重复{len(occurrences)}次，涉及{len(sections_used)}个章节。",
                "suggestion": "保留最相关章节中的一次表述，其余位置删除、合并为表格，或改写为本章专属的执行参数和检查动作。",
            }
        )

    if len(section_number_leads) >= 3:
        sections_used = _unique_text([item["section"] for item in section_number_leads])
        severity = "blocker" if len(section_number_leads) >= 8 else "warning"
        clusters.append(
            {
                "code": "template_section_number_narration",
                "severity": severity,
                "occurrence_count": len(section_number_leads),
                "section_count": len(sections_used),
                "sections": sections_used[:12],
                "sample": _clip_text(section_number_leads[0]["sentence"], 160),
                "issue": f"全文出现{len(section_number_leads)}处“围绕7.3.x”式模板叙述。",
                "suggestion": "删除对招标目录编号的机械复述，直接写本章对象、责任人、实施步骤、检查频率和验收记录。",
            }
        )

    clusters.sort(
        key=lambda item: (
            0 if item["severity"] == "blocker" else 1,
            -int(item["occurrence_count"]),
            str(item["sample"]),
        )
    )
    blocker_count = sum(1 for item in clusters if item["severity"] == "blocker")
    warning_count = len(clusters) - blocker_count
    return {
        "version": BID_TECHNICAL_TEMPLATE_REPETITION_VERSION,
        "status": "blocked" if blocker_count else ("warning" if warning_count else "pass"),
        "sentence_count": sentence_count,
        "cluster_count": len(clusters),
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "clusters": clusters[:30],
    }


def _repetition_sentences(content: str) -> list[str]:
    sentences: list[str] = []
    for raw_line in str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        if line.startswith("|") and line.endswith("|"):
            continue
        line = re.sub(r"^(?:[-*]|\d+[.)、])\s+", "", line)
        line = re.sub(r"[*_`>]", "", line)
        for part in re.split(r"(?<=[。！？；])", line):
            sentence = re.sub(r"\s+", " ", part).strip(" -|\t")
            if len(re.sub(r"[\W_]", "", sentence, flags=re.UNICODE)) >= 12:
                sentences.append(sentence)
    return sentences


def _canonical_repetition_sentence(sentence: str) -> str:
    text = re.sub(r"7[._]3[._]\d+", "7.3.#", str(sentence or ""), flags=re.I)
    text = re.sub(r"第[一二三四五六七八九十百0-9]+章", "第#章", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()
    return text


def _has_pending_marker(content: str) -> bool:
    return any(marker in str(content or "") for marker in ("待确认", "待补充", "待人工完善", "待人工确认", "待复核", "【待"))


def _section_no(component: Mapping[str, Any], draft: BidDraftSection) -> str:
    for value in (
        draft.section_key,
        draft.section_title,
        component.get("source_item_no") if isinstance(component, Mapping) else None,
        component.get("component_title") if isinstance(component, Mapping) else None,
    ):
        match = re.search(r"7[_\.]3[_\.](\d+)", str(value or ""))
        if match:
            return f"7.3.{match.group(1)}"
    return ""


def _formal_category(section_no: str, intent: str) -> str:
    if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS:
        return "fixed_material"
    if intent in TECHNICAL_FORMAL_DEPTH_INTENTS:
        return "scheme"
    return "general"


def _fixed_section_has_formal_signal(section_no: str, content: str) -> bool:
    text = _plain_text(content)
    if _has_markdown_table(content):
        return True
    signals = FORMAL_FIXED_SECTION_SIGNALS.get(section_no, ())
    if not signals:
        return len(re.sub(r"\s+", "", text)) >= 120
    return any(signal in text for signal in signals) and len(re.sub(r"\s+", "", text)) >= 80


def _has_markdown_table(content: str) -> bool:
    lines = [line.strip() for line in str(content or "").splitlines()]
    for index, line in enumerate(lines):
        if "|" not in line:
            continue
        if index + 1 < len(lines) and re.fullmatch(r"\|?[\s:\-|]+\|?", lines[index + 1]):
            return True
        if line.count("|") >= 2 and re.search(r"\|[^|]{1,40}\|[^|]{1,40}\|", line):
            return True
    return False


def _missing_formal_topics(intent: str, normalized_text: str) -> list[str]:
    result: list[str] = []
    for label, keywords in FORMAL_REQUIRED_TOPIC_GROUPS.get(intent, ()):
        if not any(_normalize_for_contains(keyword) in normalized_text for keyword in keywords):
            result.append(label)
    return result


def _missing_formal_execution_loop_groups(intent: str, normalized_text: str) -> list[str]:
    if intent not in TECHNICAL_FORMAL_DEPTH_INTENTS:
        return []
    result: list[str] = []
    for label, keywords in FORMAL_EXECUTION_LOOP_GROUPS:
        if not any(_normalize_for_contains(keyword) in normalized_text for keyword in keywords):
            result.append(label)
    return result


def _code_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        code = str(item.get("code") or "")
        if not code:
            continue
        result[code] = result.get(code, 0) + 1
    return result


def _unique_text(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _section_no_sort_key(value: Any) -> tuple[int, int, str]:
    match = re.search(r"7\.3\.(\d+)", str(value or ""))
    if match:
        return (7, int(match.group(1)), str(value))
    return (999, 999, str(value or ""))


def _section_intent(component: Mapping[str, Any], draft: BidDraftSection) -> str:
    if isinstance(component, dict):
        intent = _technical_composition_intent(component)
        if intent:
            return intent
    text = f"{draft.section_key or ''} {draft.section_title or ''}".replace("_", ".")
    if "7.3.6" in text:
        return "quality_schedule_commitment"
    if "7.3.9" in text:
        return "schedule_plan"
    if "7.3.10" in text:
        return "construction_organization"
    if "7.3.11" in text:
        return "site_facility_management"
    if "7.3.12" in text:
        return "waste_management_plan"
    if "7.3.13" in text:
        return "temporary_power_plan"
    if "7.3.14" in text:
        return "material_procurement_plan"
    if "7.3.15" in text:
        return "safety_civil_fire"
    if "7.3.16" in text:
        return "quality_assurance"
    if "7.3.17" in text:
        return "material_sample_plan"
    if "7.3.19" in text:
        return "key_difficulty_analysis"
    if "7.3.20" in text:
        return "competitive_enhancement"
    if any(token in text for token in ("质量和工期", "质量工期", "工期承诺", "质量承诺", "承诺及保证措施")):
        return "quality_schedule_commitment"
    if any(token in text for token in ("进度", "工期", "schedule")):
        return "schedule_plan"
    if any(token in text for token in ("组织设计", "施工组织", "部署")):
        return "construction_organization"
    if any(token in text for token in ("办公室", "工具间", "材料间", "临时设施", "仓储管理")):
        return "site_facility_management"
    if any(token in text for token in ("垃圾清理", "垃圾清运", "垃圾堆放", "建筑垃圾", "装修垃圾", "工完场清")):
        return "waste_management_plan"
    if any(token in text for token in ("临时用电", "配电", "漏电保护", "temporary power")):
        return "temporary_power_plan"
    if any(token in text for token in ("材料采购", "采购计划", "甲指乙供", "材料报审")):
        return "material_procurement_plan"
    if any(token in text for token in ("材料样板", "样板提供", "封样", "样板报审")):
        return "material_sample_plan"
    if any(token in text for token in ("安全", "文明", "防火", "消防")):
        return "safety_civil_fire"
    if any(token in text for token in ("质量", "验收")):
        return "quality_assurance"
    if any(token in text for token in ("重难点", "重点难点", "施工难点", "难点分析")):
        return "key_difficulty_analysis"
    if any(token in text for token in ("投标竞争力", "竞争优势", "增值服务", "优化建议", "合理化建议")):
        return "competitive_enhancement"
    return ""


def _quality_item(
    code: str,
    section: str,
    issue: str,
    suggestion: str,
    *,
    evidence: Any | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    resolved_severity = severity or ("blocker" if code.endswith("conflict") or code.endswith("leakage") else "warning")
    item: dict[str, Any] = {
        "severity": resolved_severity,
        "code": code,
        "section": section,
        "issue": issue,
        "suggestion": suggestion,
    }
    if evidence is not None:
        item["evidence"] = evidence
    return item


def _allowed_project_text(
    project_name: str | None,
    project_context: Mapping[str, Any] | None,
    project_facts: Mapping[str, Any] | None,
) -> str:
    raw = {
        "project_name": project_name,
        "project_context": project_context or {},
        "project_facts": project_facts or {},
    }
    return _normalize_for_contains(json.dumps(raw, ensure_ascii=False, default=str))


def _plain_text(content: str) -> str:
    text = re.sub(r"```.*?```", " ", str(content or ""), flags=re.S)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`>|-]+", " ", text)
    text = re.sub(r"\|", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_for_contains(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "")).replace("６", "6").replace("＃", "#")
    text = re.sub(r"(?i)(\d+)F(?=办|办公|区|层|楼|$)", r"\1层", text)
    return text


def _paragraph_count(content: str) -> int:
    count = 0
    for line in str(content or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or set(text) <= {"-", "|", " "}:
            continue
        if re.fullmatch(r"[:\-|\s]+", text):
            continue
        count += 1
    return count


def _expected_schedule_durations(source: Mapping[str, Any]) -> set[int]:
    schedule = {}
    if isinstance(source.get("schedule"), Mapping):
        schedule = dict(source.get("schedule") or {})
    facts = source.get("project_facts") if isinstance(source.get("project_facts"), Mapping) else None
    if facts and isinstance(facts.get("schedule"), Mapping):
        schedule.update(dict(facts.get("schedule") or {}))
    durations: set[int] = set()
    for key in ("total_duration_days", "duration_days", "contract_duration_days"):
        value = schedule.get(key)
        if isinstance(value, int) and value > 0:
            durations.add(value)
    for zone in schedule.get("zones") or []:
        if not isinstance(zone, Mapping):
            continue
        value = zone.get("duration_days")
        if isinstance(value, int) and value > 0:
            durations.add(value)
    return durations


def _observed_schedule_durations(text: str) -> set[int]:
    durations: set[int] = set()
    for match in re.finditer(r"(?:总工期|合同工期|计划工期|工期)[^0-9一二三四五六七八九十百]{0,12}(\d{1,4})\s*(?:日历天|天)", text):
        value = int(match.group(1))
        if value > 0:
            durations.add(value)
    return durations


def _duration_text(durations: set[int]) -> str:
    return "、".join(f"{item}天" for item in sorted(durations)) or "-"


def _quality_goal(source: Mapping[str, Any]) -> str:
    quality = {}
    if isinstance(source.get("quality"), Mapping):
        quality = dict(source.get("quality") or {})
    facts = source.get("project_facts") if isinstance(source.get("project_facts"), Mapping) else None
    if facts and isinstance(facts.get("quality"), Mapping):
        quality.update(dict(facts.get("quality") or {}))
    return str(quality.get("goal") or "").strip()


def _technical_requirements(
    project_context: Mapping[str, Any] | None,
    project_facts: Mapping[str, Any] | None,
) -> dict[str, Mapping[str, Any]]:
    merged: dict[str, Mapping[str, Any]] = {}
    for source in (project_facts or {}, project_context or {}):
        raw = source.get("technical_requirements") if isinstance(source, Mapping) else None
        if not isinstance(raw, Mapping):
            continue
        for key, value in raw.items():
            if isinstance(value, Mapping) and value.get("summary"):
                merged[str(key)] = value
    return merged


def _project_fact_coverage_for_section(
    *,
    section: str,
    section_no: str,
    intent: str,
    text: str,
    normalized_text: str,
    expected_durations: set[int],
    quality_goal: str,
    work_zone_names: list[str],
    technical_requirements: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    def add_item(fact_type: str, label: str, covered: bool, evidence: Any) -> None:
        items.append(
            {
                "fact_type": fact_type,
                "label": label,
                "covered": bool(covered),
                "evidence": evidence,
            }
        )

    if intent in {"quality_schedule_commitment", "schedule_plan"} and expected_durations:
        add_item(
            "schedule",
            "工期事实",
            bool(expected_durations & _observed_schedule_durations(text)),
            sorted(expected_durations),
        )
    if intent in {"quality_schedule_commitment", "quality_assurance"} and quality_goal:
        add_item(
            "quality",
            "质量目标",
            _normalize_for_contains(quality_goal) in normalized_text,
            quality_goal,
        )
    if intent in {
        "construction_organization",
        "safety_civil_fire",
        "temporary_power_plan",
        "material_procurement_plan",
        "key_difficulty_analysis",
        "site_facility_management",
        "waste_management_plan",
        "material_sample_plan",
        "competitive_enhancement",
    } and work_zone_names:
        add_item(
            "work_zone",
            "施工区域",
            any(_normalize_for_contains(name) in normalized_text for name in work_zone_names[:4]),
            work_zone_names[:4],
        )
    for fact_key in TECHNICAL_REQUIREMENT_FACT_KEYS_BY_INTENT.get(intent, ()):
        fact = technical_requirements.get(fact_key)
        if not fact:
            continue
        add_item(
            f"technical_requirement:{fact_key}",
            str(fact.get("label") or fact_key),
            _requirement_fact_reflected(normalized_text, fact),
            {
                "summary": fact.get("summary"),
                "keywords": fact.get("keywords") or [],
            },
        )

    covered_count = sum(1 for item in items if item["covered"])
    missing_items = [item for item in items if not item["covered"]]
    return {
        "section": section,
        "section_no": section_no,
        "intent": intent,
        "status": "pass" if not missing_items else "warning",
        "target_fact_count": len(items),
        "covered_fact_count": covered_count,
        "missing_fact_count": len(missing_items),
        "coverage_rate": round(covered_count / len(items), 4) if items else 1.0,
        "missing_fact_types": [item["fact_type"] for item in missing_items],
        "items": items,
    }


def _build_project_fact_coverage_report(section_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    target_count = sum(int(item.get("target_fact_count") or 0) for item in section_reports)
    covered_count = sum(int(item.get("covered_fact_count") or 0) for item in section_reports)
    missing_count = sum(int(item.get("missing_fact_count") or 0) for item in section_reports)
    return {
        "version": "biz4c4_p9_project_fact_coverage_v1",
        "status": "pass" if not missing_count else "warning",
        "section_count": len(section_reports),
        "target_fact_count": target_count,
        "covered_fact_count": covered_count,
        "missing_fact_count": missing_count,
        "coverage_rate": round(covered_count / target_count, 4) if target_count else 1.0,
        "missing_fact_types": _unique_text(
            [
                str(fact_type)
                for item in section_reports
                for fact_type in (item.get("missing_fact_types") or [])
                if str(fact_type or "").strip()
            ]
        )[:40],
        "section_reports": [dict(item) for item in section_reports[:80]],
    }


def _requirement_fact_reflected(normalized_text: str, fact: Mapping[str, Any]) -> bool:
    keywords = [str(item).strip() for item in fact.get("keywords") or [] if str(item).strip()]
    if any(_normalize_for_contains(keyword) in normalized_text for keyword in keywords):
        return True

    summary = str(fact.get("summary") or "").strip()
    if not summary:
        return False
    normalized_summary = _normalize_for_contains(summary)
    if len(normalized_summary) >= 12 and normalized_summary[:24] in normalized_text:
        return True
    fragments = [
        _normalize_for_contains(item)
        for item in re.split(r"[，,。；;、\s]+", summary)
        if len(_normalize_for_contains(item)) >= 4
    ]
    return any(fragment in normalized_text for fragment in fragments[:6])


def _quality_goal_conflicts(text: str, quality_goal: str) -> bool:
    if not quality_goal:
        return False
    if "合格" in quality_goal:
        return bool(re.search(r"(?:质量目标|质量标准|工程质量)[^。\n；;]{0,20}(?:优良|省优|市优|鲁班奖)", text))
    if "优良" in quality_goal:
        return bool(re.search(r"(?:质量目标|质量标准|工程质量)[^。\n；;]{0,20}合格", text))
    return False


def _work_zone_names(
    project_context: Mapping[str, Any] | None,
    project_facts: Mapping[str, Any] | None,
) -> list[str]:
    names: list[str] = []
    for source in (project_context or {}, project_facts or {}):
        raw = source.get("work_zone_names")
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            names.extend(str(item).strip() for item in raw if str(item).strip())
        scope = source.get("scope")
        if isinstance(scope, Mapping):
            raw_zones = scope.get("work_zones")
            if isinstance(raw_zones, Sequence) and not isinstance(raw_zones, (str, bytes)):
                names.extend(str(item).strip() for item in raw_zones if str(item).strip())
        schedule = source.get("schedule")
        if isinstance(schedule, Mapping):
            for zone in schedule.get("zones") or []:
                if isinstance(zone, Mapping) and str(zone.get("name") or "").strip():
                    names.append(str(zone.get("name")).strip())
    result: list[str] = []
    for name in names:
        if name not in result:
            result.append(name)
    return result
