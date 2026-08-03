from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.bidding import BidDraftSection, BidMaterialRequirement, BidParseRun, BidProject
from app.models.enterprise_profile import ENTERPRISE_PROFILE_STATUS_ACTIVE, EnterpriseProfileFile, EnterpriseProfileItem
from app.models.file_object import FileObject
from app.services.bidding_draft_sections import (
    _formalize_scheme_pending_markers,
    _has_usable_personnel_bundle_material,
    _technical_brand_table_content_from_materials,
    _technical_project_context,
    _technical_project_facts,
    list_bid_draft_sections,
)
from app.services.bidding_parser import loads_json
from app.services.bidding_technical_quality import (
    quality_report_blocking_issue_rows,
    reinforce_technical_bid_section_requirement_coverage,
    validate_technical_bid_draft_for_formal_export,
)
from app.services.bidding_technical_review_focus import (
    BID_TECHNICAL_SECTION_REVIEW_FOCUS_REINFORCEMENT_VERSION,
    reinforce_technical_bid_section_review_focus,
)
from app.services.bidding_technical_section_templates import (
    BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION,
    BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION,
    BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION,
    reinforce_technical_bid_section_discipline_playbook,
    reinforce_technical_bid_section_project_facts,
    reinforce_technical_bid_section_template_depth,
)
from app.services.bidding_tender_analysis import _TenderAnalysisDocxBuilder
from app.services.file_storage import get_object_bytes


BID_TECHNICAL_WORD_EXPORT_VERSION = "biz4c3_1_technical_word_attachment_embed_mvp_v1"
BID_TECHNICAL_FINAL_WORD_EXPORT_VERSION = "biz4c4_technical_word_formal_quality_gate_v4"
BID_TECHNICAL_FINAL_QUALITY_VISIBILITY_VERSION = "biz4c4_p4_final_quality_visibility_v3"
BID_TECHNICAL_REINFORCEMENT_POLICY_VERSION = "biz4c4_p12_compact_reinforcement_policy_v2"

FORMAL_EXPORT_INTERNAL_TERMS = (
    "技术标组成识别",
    "企业资料库",
    "投标资料补齐清单附件",
    "已上传补齐附件",
    "技术标草案",
    "投标响应草稿",
    "草稿正文",
    "章节草稿",
    "采用企业资料",
    "上述资料将作为",
    "招标文件.pdf",
    "已生成正文",
    "已绑定资料",
    "正式投标前应核对",
    "按企业资料库信息",
    "智能投标系统",
    "系统工作流",
    "工作流",
    "截图素材包",
    "素材包",
    "源序",
    "关键词响应",
    "复核资料包括",
)

FORMAL_FIXED_MATERIAL_SECTION_LABELS = {
    "7.3.1": "投标人营业执照及资质证明复印件",
    "7.3.4": "项目经理一级建造师注册证书复印件",
    "7.3.5": "安全生产许可证及招标文件要求的其他投标资料",
    "7.3.7": "近三年已完成的类似工程业绩资料",
}

FORMAL_TECHNICAL_SECTION_TITLES = {
    "7.3.1": "投标人营业执照及资质证明(复印加盖公章)",
    "7.3.2": "法定代表人身份证明书",
    "7.3.3": "投标文件签署授权委托书，委托书要求总公司授权",
    "7.3.4": "投标人拟派出的项目经理的《中华人民共和国一级建造师注册证书》复印件加盖投标人公章",
    "7.3.5": "招标文件要求投标人提交的其它投标资料",
    "7.3.6": "投标人对本工程的工程质量和工期(请注明天数)的承诺及保证措施（此项作为重点技术评审项）",
    "7.3.7": "投标人近三年已完成的类似工程经验",
    "7.3.8": "投标人拟派驻本项目的项目经理、技术负责人、安全负责人以及其它主要管理人员和技术人员的简历和资格证书",
    "7.3.9": "施工总进度计划(包括总工期、主要材料与详细设备进场时间等)",
    "7.3.10": "针对本工程的施工组织设计",
    "7.3.11": "办公室、工具间、材料间的管理方案",
    "7.3.12": "垃圾的清理、堆置、运输、垃圾堆场管理方案",
    "7.3.13": "施工临时用电的施工方案",
    "7.3.14": "主要材料的采购计划（含甲指乙供材料）",
    "7.3.15": "提供详细的安全生产、文明施工、防火施工方案和保证措施",
    "7.3.16": "重要的施工质量保障措施",
    "7.3.17": "投标单位按需于回标前提供主要材料样板，规格尺寸按发包人要求",
    "7.3.18": "投标单位拟采用的材料品牌表",
    "7.3.19": "项目重难点分析",
    "7.3.20": "投标单位认为能提升投标竞争力的内容",
}

FORMAL_SECTION_FALLBACK_PROFILE_SPECS: dict[str, tuple[dict[str, tuple[str, ...]], ...]] = {
    "7.3.1": (
        {"category": ("basic_info", "certificate", "qualification"), "title": ("营业执照", "资质证书", "建筑业企业资质证书")},
    ),
    "7.3.2": (
        {"category": ("basic_info",), "title": ("法定代表人身份证明书",)},
    ),
    "7.3.3": (
        {"category": ("basic_info", "commitment_template"), "title": ("投标文件签署授权委托书", "授权委托书")},
        {"category": ("basic_info",), "title": ("被委托人身份证复印件",)},
    ),
    "7.3.7": (
        {"title": ("类似工程业绩清单", "投标人近三年已完成的类似工程经验")},
        {"category": ("project_performance",), "subcategory": ("similar_project_contract", "similar_project_case_images")},
    ),
    "7.3.8": (
        {"category": ("basic_info",), "subcategory": ("personnel_summary",)},
        {"category": ("personnel",)},
        {"subcategory": ("project_management_performance_tables",)},
    ),
    "7.3.9": (
        {"subcategory": ("construction_schedule_asset",)},
    ),
    "7.3.10": (
        {"subcategory": ("construction_method_asset", "site_layout_asset")},
    ),
}

MATERIAL_BRAND_ROW_NAMES = (
    "水泥",
    "防水涂料及卷材",
    "瓷砖 岩板",
    "内墙及天花涂料",
    "外墙涂料 腻子",
    "普通腻子 防霉腻子",
    "矿棉吸音板",
    "热镀锌电线钢管",
    "PVC电线管",
    "筒灯 射灯 灯带 带物联网功能",
    "轨道灯",
    "可调光灯具",
    "开关插座面板",
    "角阀",
    "地漏",
    "石膏板",
    "木夹板",
    "电线电缆",
    "网线",
    "空气 漏电开关",
    "天花龙骨",
    "排气扇",
    "密封胶",
    "钢塑复合管",
    "PPR给水管 排水管",
    "地弹簧",
    "铝型材",
    "铜芯阀门",
    "淋浴隔断",
    "木地板",
    "空调铜管",
    "瓷砖胶",
    "浴霸",
    "展示样板房燃气热水器",
    "展示样板房洁具",
    "展示样板房五金",
)

FORMAL_SCHEME_SECTION_NOS = {
    "7.3.6",
    "7.3.9",
    "7.3.10",
    "7.3.11",
    "7.3.12",
    "7.3.13",
    "7.3.14",
    "7.3.15",
    "7.3.16",
    "7.3.17",
    "7.3.19",
    "7.3.20",
}

FORMAL_REVIEW_FOCUS_SECTION_NOS = {"7.3.19", "7.3.20"}


class BidTechnicalWordExportError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def build_technical_bid_draft_export_document(db: Session, project: BidProject, run: BidParseRun) -> bytes:
    context = _technical_export_context(db, project, run)
    components = context["components"]
    ordered_drafts = context["ordered_drafts"]
    missing_draft_sections = context["missing_draft_sections"]
    issue_rows = context["issue_rows"]
    attachments_by_draft = context["attachments_by_draft"]
    embedded_image_candidate_count = sum(1 for assets in attachments_by_draft.values() for item in assets if item.get("can_embed_image"))
    status_counts = _draft_status_counts(ordered_drafts)
    ready_count = status_counts.get("ready", 0)
    blocked_count = len(ordered_drafts) - ready_count

    doc = _TenderAnalysisDocxBuilder()
    doc.add_title(project.project_name or "技术标投标文件")
    doc.add_subtitle("技术标投标文件草稿")
    doc.add_paragraph(
        f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}；"
        f"解析版本：{run.run_uuid}；导出版本：{BID_TECHNICAL_WORD_EXPORT_VERSION}",
        style="Meta",
    )
    doc.add_callout(
        "草稿状态",
        f"本文件由当前“投标文件组成识别”结果组装生成，共识别 {len(components)} 个组成项，"
        f"已生成草稿 {len(ordered_drafts)} 章，其中可复核 {ready_count} 章，仍需补齐或复核 {blocked_count + len(missing_draft_sections)} 章。"
        f"本次可尝试嵌入图片附件 {embedded_image_candidate_count} 个；PDF/Word 等非图片附件暂以清单引用。"
        "本文件为内部草稿，请在资料、格式、签章和附件校验完成后再作为正式投标文件使用。",
    )

    doc.add_heading("一、导出范围")
    doc.add_kv_table(
        [
            ("项目名称", project.project_name or "-"),
            ("招标人", project.tenderer_name or "-"),
            ("技术标组成项", f"{len(components)} 项"),
            ("已生成章节", f"{len(ordered_drafts)} 章"),
            ("可复核章节", f"{ready_count} 章"),
            ("待补齐/待复核章节", f"{blocked_count + len(missing_draft_sections)} 章"),
            ("图片附件嵌入候选", f"{embedded_image_candidate_count} 个"),
        ]
    )

    doc.add_heading("二、草稿目录")
    doc.add_table(
        [["序号", "章节", "生成状态", "复核状态"]]
        + [
            [
                str(index),
                draft.section_title or "-",
                _draft_status_label(draft.draft_status),
                _review_status_label(draft.review_status),
            ]
            for index, draft in enumerate(ordered_drafts, start=1)
        ]
        + [
            [
                str(len(ordered_drafts) + index),
                item.get("title") or item.get("section_key") or "-",
                "未生成",
                "待生成",
            ]
            for index, item in enumerate(missing_draft_sections, start=1)
        ],
        widths=(700, 5100, 1700, 1860),
        header=True,
    )

    if issue_rows:
        doc.add_heading("三、导出前待处理事项")
        doc.add_table(
            [["章节", "问题", "处理建议"]]
            + [[row["section"], row["issue"], row["suggestion"]] for row in issue_rows],
            widths=(3000, 3500, 2860),
            header=True,
        )
    else:
        doc.add_heading("三、导出前待处理事项")
        doc.add_paragraph("当前未发现系统层面的阻断项，仍需人工复核正文、格式、附件和签章。")

    doc.add_heading("四、技术标正文草稿")
    for index, draft in enumerate(ordered_drafts, start=1):
        doc.add_heading(f"{index}. {draft.section_title or '技术标章节'}")
        if draft.draft_status != "ready":
            doc.add_callout("章节提示", f"当前章节状态为“{_draft_status_label(draft.draft_status)}”，导出内容仍需补齐或复核。")
        _append_markdown_to_doc(doc, draft.content_markdown or "", section_title=draft.section_title or "")
        _append_attachment_assets_to_doc(doc, attachments_by_draft.get(draft.id) or [])

    if missing_draft_sections:
        for item in missing_draft_sections:
            doc.add_heading(str(item.get("title") or item.get("section_key") or "未生成章节"))
            doc.add_callout("未生成", "当前投标文件组成识别包含该章节，但系统尚未生成对应技术标草稿。请先点击“一键生成技术标草案”或单章生成。")

    return doc.to_bytes()


def build_technical_bid_final_export_document(db: Session, project: BidProject, run: BidParseRun) -> bytes:
    context = _technical_final_export_quality_context(db, project, run)
    ordered_drafts = context["ordered_drafts"]
    components_by_key = context["components_by_key"]
    final_content_by_draft_id = context["final_content_by_draft_id"]
    quality_report = context["quality_report"]
    issue_rows = context["issue_rows"]
    if issue_rows:
        raise BidTechnicalWordExportError(
            "BID_TECHNICAL_FINAL_EXPORT_BLOCKED",
            {
                "code": "BID_TECHNICAL_FINAL_EXPORT_BLOCKED",
                "message": f"正式技术标导出被阻断，仍有 {len(issue_rows)} 项需要处理。",
                "issue_count": len(issue_rows),
                "issues": issue_rows[:20],
                "quality_report": quality_report,
            },
        )

    doc = _TenderAnalysisDocxBuilder()
    doc.set_page_header_footer(
        header_text=_formal_project_name(project.project_name) or "技术标投标文件",
        footer_text="技术标部分",
    )
    _append_formal_cover(doc, db, project, run, ordered_drafts)
    doc.add_page_break()
    _append_formal_table_of_contents(doc, ordered_drafts, context["components"])
    doc.add_page_break()

    for index, draft in enumerate(ordered_drafts, start=1):
        component = components_by_key.get(str(draft.section_key or "")) or {}
        doc.add_heading(_formal_chapter_title(draft, component, index), page_break_before=index > 1)
        _append_markdown_to_doc(doc, final_content_by_draft_id.get(draft.id) or draft.content_markdown or "", section_title=draft.section_title or "")
        _append_formal_attachment_assets_to_doc(doc, context["attachments_by_draft"].get(draft.id) or [])

    return doc.to_bytes()


def build_technical_bid_final_export_quality_report(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    context = _technical_final_export_quality_context(db, project, run)
    ordered_drafts = context["ordered_drafts"]
    issue_rows = context["issue_rows"]
    quality_report = context["quality_report"]
    return {
        "version": BID_TECHNICAL_FINAL_WORD_EXPORT_VERSION,
        "status": "blocked" if issue_rows else quality_report.get("status", "pass"),
        "issue_count": len(issue_rows),
        "issues": issue_rows[:50],
        "component_count": len(context["components"]),
        "draft_count": len(ordered_drafts),
        "missing_draft_section_count": len(context["missing_draft_sections"]),
        "quality_report": quality_report,
    }


def _technical_final_export_quality_context(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    context = _technical_export_context(db, project, run)
    ordered_drafts = context["ordered_drafts"]
    components_by_key = {
        _technical_section_key(component, index): component
        for index, component in enumerate(context["components"], start=1)
    }
    project_context = _technical_project_context(db, project, run)
    project_facts = _technical_project_facts(db, project, run)
    final_content_by_draft_id: dict[int, str] = {}
    reinforcement_traces: list[dict[str, Any]] = []
    template_reinforcement_traces: list[dict[str, Any]] = []
    project_fact_reinforcement_traces: list[dict[str, Any]] = []
    playbook_reinforcement_traces: list[dict[str, Any]] = []
    review_focus_reinforcement_traces: list[dict[str, Any]] = []
    for draft in ordered_drafts:
        component = components_by_key.get(str(draft.section_key or ""), {})
        final_content, reinforcement, template_reinforcement, project_fact_reinforcement, playbook_reinforcement, review_focus_reinforcement = _final_export_content_and_reinforcements_for_draft(
            db,
            run,
            draft,
            component,
            project_context=project_context,
        )
        final_content_by_draft_id[draft.id] = final_content
        if reinforcement:
            reinforcement_traces.append(
                {
                    **reinforcement,
                    "draft_id": draft.id,
                    "draft_uuid": draft.draft_uuid,
                    "section_key": draft.section_key,
                    "section_title": draft.section_title,
                    "section_no": _section_no_from_draft_or_component(draft, component),
                }
            )
        if template_reinforcement:
            template_reinforcement_traces.append(
                {
                    **template_reinforcement,
                    "draft_id": draft.id,
                    "draft_uuid": draft.draft_uuid,
                    "section_key": draft.section_key,
                    "section_title": draft.section_title,
                    "section_no": _section_no_from_draft_or_component(draft, component),
                }
            )
        if project_fact_reinforcement:
            project_fact_reinforcement_traces.append(
                {
                    **project_fact_reinforcement,
                    "draft_id": draft.id,
                    "draft_uuid": draft.draft_uuid,
                    "section_key": draft.section_key,
                    "section_title": draft.section_title,
                    "section_no": _section_no_from_draft_or_component(draft, component),
                }
            )
        if playbook_reinforcement:
            playbook_reinforcement_traces.append(
                {
                    **playbook_reinforcement,
                    "draft_id": draft.id,
                    "draft_uuid": draft.draft_uuid,
                    "section_key": draft.section_key,
                    "section_title": draft.section_title,
                    "section_no": _section_no_from_draft_or_component(draft, component),
                }
            )
        if review_focus_reinforcement:
            review_focus_reinforcement_traces.append(
                {
                    **review_focus_reinforcement,
                    "draft_id": draft.id,
                    "draft_uuid": draft.draft_uuid,
                    "section_key": draft.section_key,
                    "section_title": draft.section_title,
                    "section_no": _section_no_from_draft_or_component(draft, component),
                }
            )

    quality_report = validate_technical_bid_draft_for_formal_export(
        project_name=project.project_name,
        project_context=project_context,
        project_facts=project_facts,
        drafts=ordered_drafts,
        components_by_key=components_by_key,
        final_content_by_draft_id=final_content_by_draft_id,
    )
    quality_report["requirement_reinforcement"] = _build_requirement_reinforcement_audit_report(reinforcement_traces)
    quality_report["section_template_reinforcement"] = _build_section_template_reinforcement_audit_report(template_reinforcement_traces)
    quality_report["section_project_fact_reinforcement"] = _build_section_project_fact_reinforcement_audit_report(project_fact_reinforcement_traces)
    quality_report["section_playbook_reinforcement"] = _build_section_playbook_reinforcement_audit_report(playbook_reinforcement_traces)
    quality_report["section_review_focus_reinforcement"] = _build_section_review_focus_reinforcement_audit_report(review_focus_reinforcement_traces)
    quality_report["reinforcement_policy"] = {
        "version": BID_TECHNICAL_REINFORCEMENT_POLICY_VERSION,
        "strategy": "gap_only_with_single_detail_layer",
        "max_structured_detail_layer_per_section": 1,
        "review_focus_section_nos": sorted(FORMAL_REVIEW_FOCUS_SECTION_NOS),
    }
    issue_rows = _technical_final_export_issue_rows(
        db,
        run,
        drafts=ordered_drafts,
        missing_draft_sections=context["missing_draft_sections"],
        attachments_by_draft=context["attachments_by_draft"],
        final_content_by_draft_id=final_content_by_draft_id,
    )
    issue_rows = _dedupe_issue_rows(quality_report_blocking_issue_rows(quality_report) + issue_rows)
    return {
        **context,
        "components_by_key": components_by_key,
        "final_content_by_draft_id": final_content_by_draft_id,
        "reinforcement_traces": reinforcement_traces,
        "template_reinforcement_traces": template_reinforcement_traces,
        "project_fact_reinforcement_traces": project_fact_reinforcement_traces,
        "playbook_reinforcement_traces": playbook_reinforcement_traces,
        "review_focus_reinforcement_traces": review_focus_reinforcement_traces,
        "quality_report": quality_report,
        "issue_rows": issue_rows,
    }


def _build_requirement_reinforcement_audit_report(traces: list[dict[str, Any]]) -> dict[str, Any]:
    changed_traces = [item for item in traces if item.get("changed")]
    transitions = [
        {
            **transition,
            "section_no": trace.get("section_no") or transition.get("section_no"),
            "section_title": trace.get("section_title"),
            "section_key": trace.get("section_key"),
        }
        for trace in traces
        for transition in (trace.get("coverage_transitions") or [])
    ]
    skipped_items = [
        {
            **item,
            "section_no": trace.get("section_no") or item.get("section_no"),
            "section_title": trace.get("section_title"),
            "section_key": trace.get("section_key"),
        }
        for trace in traces
        for item in (trace.get("skipped_items") or [])
    ]
    return {
        "version": BID_TECHNICAL_FINAL_QUALITY_VISIBILITY_VERSION,
        "status": "applied" if changed_traces else ("manual_review_required" if skipped_items else "no_action"),
        "section_count": len(traces),
        "reinforced_section_count": len(changed_traces),
        "auto_reinforced_count": sum(int(item.get("supplement_count") or 0) for item in traces),
        "manual_review_count": len(skipped_items),
        "transitions": transitions[:80],
        "skipped_items": skipped_items[:80],
        "section_reports": [
            _requirement_reinforcement_section_report(item)
            for item in traces
            if item.get("changed") or item.get("skipped_items") or item.get("supplement_items")
        ],
    }


def _requirement_reinforcement_section_report(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_no": trace.get("section_no"),
        "section_key": trace.get("section_key"),
        "section_title": trace.get("section_title"),
        "status": trace.get("status"),
        "changed": bool(trace.get("changed")),
        "supplement_count": trace.get("supplement_count") or 0,
        "skipped_manual_review_count": trace.get("skipped_manual_review_count") or 0,
        "coverage_before": trace.get("coverage_before") or {},
        "coverage_after": trace.get("coverage_after") or {},
        "coverage_transitions": (trace.get("coverage_transitions") or [])[:20],
        "skipped_items": (trace.get("skipped_items") or [])[:20],
    }


def _build_section_template_reinforcement_audit_report(traces: list[dict[str, Any]]) -> dict[str, Any]:
    changed_traces = [item for item in traces if item.get("changed")]
    return {
        "version": BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION,
        "status": "applied" if changed_traces else "no_action",
        "section_count": len(traces),
        "reinforced_section_count": len(changed_traces),
        "added_topic_count": sum(int(item.get("added_topic_count") or 0) for item in changed_traces),
        "added_execution_loop_group_count": sum(int(item.get("added_execution_loop_group_count") or 0) for item in changed_traces),
        "section_reports": [_section_template_reinforcement_section_report(item) for item in changed_traces[:80]],
    }


def _section_template_reinforcement_section_report(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_no": trace.get("section_no"),
        "section_key": trace.get("section_key"),
        "section_title": trace.get("section_title"),
        "status": trace.get("status"),
        "changed": bool(trace.get("changed")),
        "intent": trace.get("intent"),
        "reason": trace.get("reason"),
        "paragraph_count_before": trace.get("paragraph_count_before"),
        "paragraph_count_after": trace.get("paragraph_count_after"),
        "visible_length_before": trace.get("visible_length_before"),
        "visible_length_after": trace.get("visible_length_after"),
        "missing_topics_before": trace.get("missing_topics_before") or [],
        "missing_topics_after": trace.get("missing_topics_after") or [],
        "missing_execution_loop_before": trace.get("missing_execution_loop_before") or [],
        "missing_execution_loop_after": trace.get("missing_execution_loop_after") or [],
        "added_topics": trace.get("added_topics") or [],
        "added_execution_loop_groups": trace.get("added_execution_loop_groups") or [],
        "added_blocks": trace.get("added_blocks") or [],
    }


def _build_section_project_fact_reinforcement_audit_report(traces: list[dict[str, Any]]) -> dict[str, Any]:
    changed_traces = [item for item in traces if item.get("changed")]
    return {
        "version": BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION,
        "status": "applied" if changed_traces else "no_action",
        "section_count": len(traces),
        "reinforced_section_count": len(changed_traces),
        "fact_count": sum(int(item.get("fact_count") or 0) for item in changed_traces),
        "fact_types": _unique_text(
            str(fact_type)
            for item in changed_traces
            for fact_type in (item.get("fact_types") or [])
            if str(fact_type or "").strip()
        )[:40],
        "skipped_fact_types": _unique_text(
            str(fact_type)
            for item in traces
            for fact_type in (item.get("skipped_fact_types") or [])
            if str(fact_type or "").strip()
        )[:40],
        "section_reports": [_section_project_fact_reinforcement_section_report(item) for item in changed_traces[:80]],
    }


def _section_project_fact_reinforcement_section_report(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_no": trace.get("section_no"),
        "section_key": trace.get("section_key"),
        "section_title": trace.get("section_title"),
        "status": trace.get("status"),
        "changed": bool(trace.get("changed")),
        "intent": trace.get("intent"),
        "reason": trace.get("reason"),
        "visible_length_before": trace.get("visible_length_before"),
        "visible_length_after": trace.get("visible_length_after"),
        "fact_count": trace.get("fact_count") or 0,
        "fact_types": trace.get("fact_types") or [],
        "fact_labels": trace.get("fact_labels") or [],
        "skipped_fact_types": trace.get("skipped_fact_types") or [],
        "added_blocks": trace.get("added_blocks") or [],
    }


def _build_section_playbook_reinforcement_audit_report(traces: list[dict[str, Any]]) -> dict[str, Any]:
    changed_traces = [item for item in traces if item.get("changed")]
    return {
        "version": BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION,
        "status": "applied" if changed_traces else "no_action",
        "section_count": len(traces),
        "reinforced_section_count": len(changed_traces),
        "added_table_count": sum(int(item.get("added_table_count") or 0) for item in changed_traces),
        "control_item_count": sum(int(item.get("control_item_count") or 0) for item in changed_traces),
        "process_node_count": sum(int(item.get("process_node_count") or 0) for item in changed_traces),
        "section_reports": [_section_playbook_reinforcement_section_report(item) for item in changed_traces[:80]],
    }


def _section_playbook_reinforcement_section_report(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_no": trace.get("section_no"),
        "section_key": trace.get("section_key"),
        "section_title": trace.get("section_title"),
        "status": trace.get("status"),
        "changed": bool(trace.get("changed")),
        "intent": trace.get("intent"),
        "reason": trace.get("reason"),
        "paragraph_count_before": trace.get("paragraph_count_before"),
        "paragraph_count_after": trace.get("paragraph_count_after"),
        "visible_length_before": trace.get("visible_length_before"),
        "visible_length_after": trace.get("visible_length_after"),
        "added_table_count": trace.get("added_table_count") or 0,
        "control_item_count": trace.get("control_item_count") or 0,
        "process_node_count": trace.get("process_node_count") or 0,
        "added_blocks": trace.get("added_blocks") or [],
    }


def _build_section_review_focus_reinforcement_audit_report(traces: list[dict[str, Any]]) -> dict[str, Any]:
    changed_traces = [item for item in traces if item.get("changed")]
    return {
        "version": BID_TECHNICAL_SECTION_REVIEW_FOCUS_REINFORCEMENT_VERSION,
        "status": "applied" if changed_traces else "no_action",
        "section_count": len(traces),
        "reinforced_section_count": len(changed_traces),
        "added_focus_count": sum(int(item.get("added_focus_count") or 0) for item in changed_traces),
        "added_keyword_count": sum(int(item.get("added_keyword_count") or 0) for item in changed_traces),
        "added_table_count": sum(int(item.get("added_table_count") or 0) for item in changed_traces),
        "section_reports": [_section_review_focus_reinforcement_section_report(item) for item in changed_traces[:80]],
    }


def _section_review_focus_reinforcement_section_report(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_no": trace.get("section_no"),
        "section_key": trace.get("section_key"),
        "section_title": trace.get("section_title"),
        "status": trace.get("status"),
        "changed": bool(trace.get("changed")),
        "intent": trace.get("intent"),
        "reason": trace.get("reason"),
        "paragraph_count_before": trace.get("paragraph_count_before"),
        "paragraph_count_after": trace.get("paragraph_count_after"),
        "visible_length_before": trace.get("visible_length_before"),
        "visible_length_after": trace.get("visible_length_after"),
        "matched_keyword_count_before": trace.get("matched_keyword_count_before") or 0,
        "matched_keyword_count_after": trace.get("matched_keyword_count_after") or 0,
        "added_keyword_count": trace.get("added_keyword_count") or 0,
        "added_focus_count": trace.get("added_focus_count") or 0,
        "added_table_count": trace.get("added_table_count") or 0,
        "missing_keywords_before": trace.get("missing_keywords_before") or [],
        "missing_keywords_after": trace.get("missing_keywords_after") or [],
        "added_blocks": trace.get("added_blocks") or [],
    }


def _technical_export_context(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    plan = _technical_composition_plan(run)
    components = [item for item in plan.get("components") or [] if isinstance(item, dict)]
    if not components:
        raise BidTechnicalWordExportError("BID_TECHNICAL_COMPOSITION_NOT_GENERATED")

    current_keys = _current_technical_section_keys(components)
    drafts = [
        row
        for row in list_bid_draft_sections(db, run, package_key="technical")
        if _is_current_technical_composition_draft(row, current_keys)
    ]
    if not drafts:
        raise BidTechnicalWordExportError("BID_TECHNICAL_DRAFT_NOT_GENERATED")

    ordered_drafts = _order_drafts_by_composition(drafts, components)
    missing_draft_sections = _missing_draft_sections(ordered_drafts, components)
    issue_rows = _technical_export_issue_rows(db, run, ordered_drafts, missing_draft_sections)
    attachments_by_draft = {
        draft.id: _collect_draft_attachment_assets(db, run, draft)
        for draft in ordered_drafts
    }
    return {
        "components": components,
        "ordered_drafts": ordered_drafts,
        "missing_draft_sections": missing_draft_sections,
        "issue_rows": issue_rows,
        "attachments_by_draft": attachments_by_draft,
    }


def _final_export_content_for_draft(
    db: Session,
    run: BidParseRun,
    draft: BidDraftSection,
    component: dict[str, Any],
) -> str:
    content, _reinforcement, _template_reinforcement, _project_fact_reinforcement, _playbook_reinforcement, _review_focus_reinforcement = _final_export_content_and_reinforcements_for_draft(
        db,
        run,
        draft,
        component,
    )
    return content


def _final_export_content_and_reinforcement_for_draft(
    db: Session,
    run: BidParseRun,
    draft: BidDraftSection,
    component: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    content, reinforcement, _template_reinforcement, _project_fact_reinforcement, _playbook_reinforcement, _review_focus_reinforcement = _final_export_content_and_reinforcements_for_draft(
        db,
        run,
        draft,
        component,
    )
    return content, reinforcement


def _final_export_content_and_reinforcements_for_draft(
    db: Session,
    run: BidParseRun,
    draft: BidDraftSection,
    component: dict[str, Any],
    *,
    project_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    section_no = _section_no_from_draft_or_component(draft, component)
    content = str(draft.content_markdown or "")
    material_rows = _requirements_for_draft_attachments(db, run, draft)
    if section_no in {"7.3.1", "7.3.2", "7.3.3", "7.3.4", "7.3.5", "7.3.7"}:
        fixed_content = _formal_fixed_material_content_from_materials(
            db,
            draft,
            material_rows,
            section_no=section_no,
        )
        if fixed_content:
            return _formalize_final_export_text(fixed_content, section_no=section_no), None, None, None, None, None
    if section_no == "7.3.18":
        brand_content, _brand_evidence, _brand_count = _technical_brand_table_content_from_materials(
            db,
            component or {"component_title": draft.section_title, "source_item_no": section_no},
            material_rows,
        )
        if brand_content:
            return _formalize_final_export_text(_formalize_brand_table_for_export(brand_content), section_no=section_no), None, None, None, None, None
        return _formalize_final_export_text(_formalize_brand_table_for_export(content), section_no=section_no), None, None, None, None, None
    if section_no == "7.3.8":
        personnel_content = _formal_personnel_section_content_from_materials(db, material_rows, fallback_content=content)
        if personnel_content:
            return _formalize_final_export_text(personnel_content, section_no=section_no), None, None, None, None, None
        if _has_usable_personnel_bundle_material(db, material_rows):
            content, _count = _formalize_scheme_pending_markers(content, section_no=section_no)
        content = _formalize_personnel_requirement_refs_for_export(db, content, material_rows)
        return _formalize_final_export_text(content, section_no=section_no), None, None, None, None, None
    if section_no in FORMAL_SCHEME_SECTION_NOS:
        content, _count = _formalize_scheme_pending_markers(content, section_no=section_no)
        content = _formalize_weak_commitment_text(content, section_no=section_no)
    template_reinforcement_trace: dict[str, Any] | None = None
    if _should_apply_section_template_reinforcement(section_no, component):
        template_reinforcement = reinforce_technical_bid_section_template_depth(
            draft=draft,
            component=component or {},
            content=content,
            project_context=project_context,
        )
        content = str(template_reinforcement.get("content") or content)
        template_reinforcement_trace = {key: value for key, value in template_reinforcement.items() if key != "content"}
    project_fact_reinforcement_trace: dict[str, Any] | None = None
    if _should_apply_section_template_reinforcement(section_no, component):
        project_fact_reinforcement = reinforce_technical_bid_section_project_facts(
            draft=draft,
            component=component or {},
            content=content,
            project_context=project_context,
        )
        content = str(project_fact_reinforcement.get("content") or content)
        project_fact_reinforcement_trace = {key: value for key, value in project_fact_reinforcement.items() if key != "content"}
    playbook_reinforcement_trace: dict[str, Any] | None = None
    if _should_apply_section_template_reinforcement(section_no, component) and section_no not in FORMAL_REVIEW_FOCUS_SECTION_NOS:
        playbook_reinforcement = reinforce_technical_bid_section_discipline_playbook(
            draft=draft,
            component=component or {},
            content=content,
            project_context=project_context,
        )
        content = str(playbook_reinforcement.get("content") or content)
        playbook_reinforcement_trace = {key: value for key, value in playbook_reinforcement.items() if key != "content"}
    review_focus_reinforcement_trace: dict[str, Any] | None = None
    if _should_apply_section_template_reinforcement(section_no, component) and section_no in FORMAL_REVIEW_FOCUS_SECTION_NOS:
        review_focus_reinforcement = reinforce_technical_bid_section_review_focus(
            draft=draft,
            component=component or {},
            content=content,
            project_context=project_context,
        )
        content = str(review_focus_reinforcement.get("content") or content)
        review_focus_reinforcement_trace = {key: value for key, value in review_focus_reinforcement.items() if key != "content"}
    reinforcement_trace: dict[str, Any] | None = None
    if _should_apply_requirement_coverage_reinforcement(section_no, component):
        reinforcement = reinforce_technical_bid_section_requirement_coverage(
            draft=draft,
            component=component or {},
            content=content,
        )
        content = str(reinforcement.get("content") or content)
        reinforcement_trace = {key: value for key, value in reinforcement.items() if key != "content"}
    return (
        _formalize_final_export_text(content, section_no=section_no, project_context=project_context),
        reinforcement_trace,
        template_reinforcement_trace,
        project_fact_reinforcement_trace,
        playbook_reinforcement_trace,
        review_focus_reinforcement_trace,
    )


def _should_apply_section_template_reinforcement(section_no: str, component: dict[str, Any]) -> bool:
    if section_no not in FORMAL_SCHEME_SECTION_NOS:
        return False
    if not isinstance(component, dict):
        return bool(section_no)
    classification = str(component.get("classification") or "")
    if classification in {"fixed_enterprise_material"}:
        return False
    return True


def _should_apply_requirement_coverage_reinforcement(section_no: str, component: dict[str, Any]) -> bool:
    if section_no in {"7.3.1", "7.3.2", "7.3.3", "7.3.4", "7.3.5", "7.3.7", "7.3.8", "7.3.18"}:
        return False
    if not isinstance(component, dict) or not component.get("information_needs"):
        return False
    classification = str(component.get("classification") or "")
    if classification in {"fixed_enterprise_material", "manual_input"}:
        return False
    return True


def _formalize_final_export_text(
    content: str,
    *,
    section_no: str = "",
    project_context: Mapping[str, Any] | None = None,
) -> str:
    text = str(content or "")
    replacements = {
        "投标响应草稿": "投标响应",
        "章节草稿": "章节",
        "草稿正文": "正文",
        "技术标草案": "技术标",
        "点击“填写”": "按投标人资料",
        "点击“填写”时": "按投标人资料",
        "“填写”时": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = _formalize_editorial_list_tokens(text)
    text = _clean_formal_public_text(text)
    text = _formalize_formal_self_references(text)
    text = _formalize_residual_weak_wording(text)
    text = _formalize_public_bid_wording(text)
    text = _formalize_internal_workflow_wording(text)
    text = _formalize_p1_internal_source_trace_wording(text)
    text = _formalize_internal_file_trace_wording(text)
    text = _formalize_p0_hard_final_wording(text, project_context=project_context)
    if section_no in FORMAL_SCHEME_SECTION_NOS:
        text = _remove_formal_pending_action_sections(text)
        text = _formalize_scheme_residual_pending_confirmation(text, section_no=section_no)
    text = re.sub(r"可在技术标[“\"]?填写[”\"]?时作为企业资料库资料引用。?", "", text)
    text = re.sub(r"可在技术标按企业资料库信息作为企业资料库资料引用。?", "", text)
    text = re.sub(r"可按企业资料库信息作为企业资料库资料引用。?", "", text)
    text = _remove_formal_internal_evidence_lines(text)
    text = _drop_empty_markdown_sections(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _formalize_editorial_list_tokens(content: str) -> str:
    text = str(content or "")
    text = re.sub(r"(?m)^\s*本应原文\s*[、，；：]?", "", text)
    text = re.sub(r"([、，；：])\s*本应原文\s*(?:[、，；：])?", r"\1", text)
    text = re.sub(r"[（(]\s*本应原文\s*[）)]", "", text)
    text = re.sub(r"、{2,}", "、", text)
    text = re.sub(r"，{2,}", "，", text)
    return text


def _formalize_internal_workflow_wording(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    replacements = [
        ("企业资料库", "投标人资料"),
        ("采用企业资料", "采用投标人资料"),
        ("上述资料将作为", "上述资料作为"),
        ("已生成正文", "本章正文"),
        ("已绑定资料", "已提交资料"),
        ("正式投标前应核对", "投标文件定稿时复核"),
        ("技术标组成识别", "招标文件技术标要求"),
        ("投标资料补齐清单附件", "本章附件资料"),
        ("已上传补齐附件", "已提交附件资料"),
        ("按企业资料库信息", "按投标人资料"),
        ("智能投标系统", "投标文件编制"),
        ("系统工作流", "投标文件编制"),
        ("招标文件.pdf", "招标文件"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"(?:本系统|平台)(?:自动)?(?:生成|识别|检索|匹配)[^。；;\n]{0,60}[。；;]?", "", text)
    text = re.sub(r"(?:Dify|N8N|workflow|trace_id)[^。；;\n]{0,60}[。；;]?", "", text, flags=re.I)
    text = re.sub(r"工作流(?:已)?(?:生成|识别|检索|匹配)[^。；;\n]{0,60}[。；;]?", "", text)
    return text


def _formalize_p0_hard_final_wording(
    content: str,
    *,
    project_context: Mapping[str, Any] | None = None,
) -> str:
    text = str(content or "")
    if not text:
        return ""
    replacements = [
        ("商业街、层办公", "商业街区、6#楼32F办公区"),
        ("商业街，合同工期 45天；层办公，合同工期 60天", "商业街区，合同工期45天；6#楼32F办公区，合同工期60天"),
        ("商业街，合同工期45天；层办公，合同工期60天", "商业街区，合同工期45天；6#楼32F办公区，合同工期60天"),
        ("商业街，合同工期 45 天；层办公，合同工期 60 天", "商业街区，合同工期45天；6#楼32F办公区，合同工期60天"),
        ("合同约定约定", "合同约定"),
        ("招标文件招标文件", "招标文件"),
    ]
    context_text = str(dict(project_context or {}))
    allow_hk_center_scope_repair = (
        "商业街" in context_text
        and "6#楼" in context_text
        and ("32F办公区" in context_text or "32层办公区" in context_text)
    )
    project_specific_sources = {
        "商业街、层办公",
        "商业街，合同工期 45天；层办公，合同工期 60天",
        "商业街，合同工期45天；层办公，合同工期60天",
        "商业街，合同工期 45 天；层办公，合同工期 60 天",
    }
    for old, new in replacements:
        if old in project_specific_sources and not allow_hk_center_scope_repair:
            continue
        text = text.replace(old, new)
    text = re.sub(r"6#楼\s*32\s*层\s*办公区", "6#楼32F办公区", text)
    text = re.sub(r"6#楼\s*32F\s*办公区", "6#楼32F办公区", text, flags=re.I)
    text = re.sub(
        r"高峰期计划投入装饰技工约\s*(?:XX|X{2,})\s*人",
        "高峰期按施工段及作业面配置充足装饰技工",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"约\s*(?:XX|X{2,})\s*人",
        "按施工段及作业面动态配置满足进度要求的人员",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"高峰期(?:计划)?(?:配备|配置|配)\s*[^。；;\n]{0,160}?[×＊*]\s*人(?:\s*[、，,及和]\s*[^。；;\n]{0,80}?[×＊*]\s*人)*",
        "高峰期根据施工段、作业面和进度计划动态配置专业工种及辅助人员，满足施工进度要求",
        text,
    )
    text = re.sub(
        r"项目组织架构图具体见附图[（(]略，?实际标书中将附清晰的架构框图[）)]。?",
        "项目组织架构详见本章项目管理组织机构及岗位职责表。",
        text,
    )
    text = re.sub(
        r"附图[（(]略，?实际标书中将附清晰的架构框图[）)]",
        "本章项目管理组织机构及岗位职责表",
        text,
    )
    return text


def _formalize_p1_internal_source_trace_wording(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    text = text.replace("截图素材包", "附件资料")
    text = text.replace("素材包", "附件资料")

    def _review_trace_replacement(match: re.Match[str]) -> str:
        record = re.sub(r"\s+", "", match.group("record")).strip("，,；;。 ")
        keywords = re.sub(r"\s+", "", match.group("keywords")).strip("，,；;。 ")
        if record and keywords:
            return f"过程复核资料以{record}为主，并围绕{keywords}等评审要点形成检查、验收和整改闭环。"
        if record:
            return f"过程复核资料以{record}为主，作为检查、验收和整改闭环依据。"
        if keywords:
            return f"围绕{keywords}等评审要点形成检查、验收和整改闭环。"
        return ""

    text = re.sub(
        r"复核资料包括[:：]\s*(?P<record>[^。\n；;]{1,160})[。；;]\s*关键词响应[:：]\s*(?P<keywords>[^。\n；;]{1,160})[。；;]?",
        _review_trace_replacement,
        text,
    )

    def _record_only_replacement(match: re.Match[str]) -> str:
        record = re.sub(r"\s+", "", match.group("record")).strip("，,；;。 ")
        return f"过程复核资料以{record}为主，作为检查、验收和整改闭环依据。" if record else ""

    def _keyword_only_replacement(match: re.Match[str]) -> str:
        keywords = re.sub(r"\s+", "", match.group("keywords")).strip("，,；;。 ")
        return f"围绕{keywords}等评审要点形成检查、验收和整改闭环。" if keywords else ""

    text = re.sub(r"复核资料包括[:：]\s*(?P<record>[^。\n；;]{1,160})[。；;]?", _record_only_replacement, text)
    text = re.sub(r"关键词响应[:：]\s*(?P<keywords>[^。\n；;]{1,160})[。；;]?", _keyword_only_replacement, text)
    text = re.sub(r"[（(【\[]\s*源序\s*[:：]?\s*[^）)\]】\n]{0,80}[）)】\]]", "", text)
    text = re.sub(
        r"\s*源序\s*[:：]?\s*[A-Za-z0-9_\-./第页码一二三四五六七八九十百零、,， ]{1,80}(?=[。；;，,\n]|$)",
        "",
        text,
    )
    text = re.sub(r"\s+([，。、；：）])", r"\1", text)
    text = re.sub(r"（\s*）", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"([，,；;：:])。", "。", text)
    text = re.sub(r"。{2,}", "。", text)
    return text


def _formalize_internal_file_trace_wording(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    source_coordinate = r"(?:DOCX|PDF|WORD)\s*第\s*\d+(?:\s*[-—‑–~至]\s*\d+)?\s*(?:段|页)"
    source_filename = r"[^（）()\n]{0,80}\.(?:docx?|pdf|xlsx?)"
    text = re.sub(
        rf"[（(]\s*(?:(?:{source_filename})\s*(?:[/／]\s*)?)?(?:{source_coordinate})?\s*[）)]",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(rf"\s*(?:[/／]\s*)?{source_coordinate}", "", text, flags=re.I)
    text = re.sub(r"(?:技术要求|招标文件)\.docx?", "招标文件技术要求", text, flags=re.I)
    text = re.sub(r"招标文件\.pdf", "招标文件", text, flags=re.I)
    text = re.sub(r"\s+([，。、；：）])", r"\1", text)
    text = re.sub(r"[（(]\s*[/／]?\s*[）)]", "", text)
    text = re.sub(r"\s*[/／]\s*(?=[，。、；：）]|$)", "", text)
    return text


def _clean_formal_public_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    # Internal source labels are useful in the enterprise profile library, but
    # must not leak into a formal bid document.
    text = re.sub(r"[（(]\s*[^（）()\n]{0,40}技术标提取\s*[）)]", "", text)
    text = text.replace("香港中心技术标提取", "")
    text = text.replace("技术标提取", "")
    text = re.sub(r"\s+([，。、；：）])", r"\1", text)
    text = re.sub(r"（\s*）", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip()


def _formalize_formal_self_references(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    text = re.sub(
        r"详见本技术标[“\"]?拟派驻主要管理人员简历和资格证书[”\"]?章节",
        "随本章后附人员简历及资格证书资料一并提交",
        text,
    )
    text = re.sub(
        r"详见本章后附人员简历及资格证书资料",
        "随本章后附人员简历及资格证书资料一并提交",
        text,
    )
    return text


def _formalize_residual_weak_wording(content: str) -> str:
    text = str(content or "")
    text = text.replace("（如有）", "")
    text = text.replace("（如需）", "")
    text = text.replace("如有", "按招标文件要求")
    text = text.replace("如需", "按发包人、总包及监理审批要求")
    text = text.replace("可根据现场实际进度动态调整", "根据现场实际进度动态优化调整")
    text = text.replace("需结合现场实际同时系数计算", "按现场实际同时系数复核计算")
    return text


def _formalize_public_bid_wording(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    replacements = [
        ("随本章后附人员简历及资格证书资料一并提交", "见本章人员简历及资格证书附件资料"),
        ("详见本章后附人员简历及资格证书资料", "见本章人员简历及资格证书附件资料"),
        ("相关复印件、证照或证明文件随本章后附。", "相关复印件、证照或证明文件见本章附件资料。"),
        ("相关复印件、证照或证明文件随本章后附", "相关复印件、证照或证明文件见本章附件资料"),
        ("随本章后附。", "见本章附件资料。"),
        ("随本章后附", "见本章附件资料"),
        ("按合同附件另外处罚", "按照招标文件合同附件另行处罚"),
        ("每延误一天按合同约定", "每延误一天按照招标文件及合同约定"),
        ("不合格按合同处罚", "不合格按照招标文件及合同约定处罚"),
        ("按合同约定配备", "按照招标文件及合同约定配备"),
        ("品牌按合同", "品牌按招标文件品牌要求及发包人确认结果执行"),
        ("按合同", "按照招标文件及合同约定"),
        ("按清单", "按招标工程量清单及经确认的设计图纸执行"),
        ("总包单位", "总承包单位"),
        ("总包", "总承包单位"),
        ("甲方", "发包人"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("总承包单位单位", "总承包单位")
    return text


def _remove_formal_pending_action_sections(content: str) -> str:
    lines = str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    skip_until_heading_level = 0
    for raw_line in lines:
        line = raw_line.strip()
        heading_match = re.match(r"^(#{1,6})\s*(.+?)\s*$", line)
        if skip_until_heading_level:
            if heading_match and len(heading_match.group(1)) <= skip_until_heading_level:
                skip_until_heading_level = 0
            else:
                continue
        plain_heading_match = re.match(r"^(?:第?[一二三四五六七八九十0-9]+[、.．]\s*)?(.+?)\s*[:：]?$", line)
        plain_heading_key = re.sub(r"\s+", "", plain_heading_match.group(1) if plain_heading_match else line)
        if heading_match:
            heading_level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_key = re.sub(r"\s+", "", heading_text)
            if _is_formal_pending_action_heading(heading_key):
                skip_until_heading_level = heading_level
                continue
        elif _is_formal_pending_action_heading(plain_heading_key):
            continue
        if re.match(r"^[-*]\s*【?待补充[:：】]", line):
            continue
        if re.match(r"^[-*]\s*【?待确认[:：】]", line):
            continue
        if re.match(r"^(?:[-*]\s*)?待补充[:：]", line):
            continue
        if re.match(r"^(?:[-*]\s*)?待人工完善[:：]", line):
            continue
        result.append(raw_line)
    return "\n".join(result)


def _is_formal_pending_action_heading(value: str) -> bool:
    key = re.sub(r"\s+", "", str(value or ""))
    if not key:
        return False
    return any(
        token in key
        for token in (
            "待人工完善",
            "待补充",
            "待补充/待复核",
            "待复核",
            "需项目负责人补实的信息",
            "待确认事项",
        )
    )


def _formalize_scheme_residual_pending_confirmation(content: str, *, section_no: str) -> str:
    text = str(content or "")
    if "待确认" not in text:
        return text
    if section_no == "7.3.13":
        replacement = "经现场复核并报发包人、监理及总承包单位审批后确定"
    else:
        replacement = "经深化设计、现场复核及发包人审批后确定"
    text = re.sub(r"仍?待确认后", f"{replacement}后", text)
    text = re.sub(r"仍?待确认", replacement, text)
    text = text.replace("经现场复核并报发包人、监理及总承包单位审批后确定后", "经现场复核并报发包人、监理及总承包单位审批后")
    text = text.replace("经深化设计、现场复核及发包人审批后确定后", "经深化设计、现场复核及发包人审批后")
    return text


def _remove_formal_internal_evidence_lines(content: str) -> str:
    lines: list[str] = []
    for raw_line in str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line in {"```", "```text", "```json"} or re.fullmatch(r"```[A-Za-z0-9_-]*", line):
            continue
        if re.fullmatch(r"#{1,4}\s*企业资料引用", line):
            continue
        if any(term in line for term in ("技术标组成识别", "投标资料补齐清单附件", "已上传补齐附件")):
            continue
        if re.search(r"\b(?:Dify|N8N|workflow|trace_id)\b", line, flags=re.I):
            continue
        if any(term in line for term in ("智能投标系统", "系统工作流")):
            continue
        if re.search(r"(?:本系统|平台)(?:自动)?(?:生成|识别|检索|匹配)", line):
            continue
        if line.startswith(("- 企业资料库：", "企业资料库：")):
            continue
        if "可在技术标" in line and "企业资料库" in line:
            continue
        lines.append(raw_line)
    return "\n".join(lines)


def _drop_empty_markdown_sections(content: str) -> str:
    lines = str(content or "").split("\n")
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^#{2,4}\s+\S+", line.strip()):
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index >= len(lines) or re.match(r"^#{1,4}\s+\S+", lines[next_index].strip()):
                index += 1
                continue
        result.append(line)
        index += 1
    return "\n".join(result)


def _formal_fixed_material_content_from_materials(
    db: Session,
    draft: BidDraftSection,
    material_rows: list[BidMaterialRequirement],
    *,
    section_no: str,
) -> str:
    profiles = _profile_items_for_section_materials(db, section_no, material_rows, include_fallback=True)
    if section_no in {"7.3.2", "7.3.3", "7.3.7"}:
        for profile in _rank_form_profile_text_items(profiles, section_no=section_no):
            content = _profile_content_text_for_formal(profile)
            if content:
                return f"## {_formal_profile_document_title(profile, draft.section_title)}\n{content}"

    label = FORMAL_FIXED_MATERIAL_SECTION_LABELS.get(section_no)
    if not label:
        return ""
    names = [
        _clean_formal_public_text(profile.title)
        for profile in profiles
        if _clean_formal_public_text(profile.title)
    ]
    material_text = f"包括{'、'.join(_unique_text(names)[:8])}。" if names else ""
    material_table = _formal_fixed_material_summary_table(label, names)
    return (
        "## 投标响应\n"
        f"我方按招标文件要求提交{label}。{material_text}"
        "相关复印件、证照或证明文件随本章后附。"
        f"{material_table}"
    )


def _formal_fixed_material_summary_table(label: str, names: list[str]) -> str:
    clean_names = _unique_text([name for name in names if name])[:12]
    if not clean_names:
        return ""
    lines = [
        "",
        "",
        "## 资料清单",
        "| 序号 | 资料名称 | 响应说明 |",
        "| --- | --- | --- |",
    ]
    for index, name in enumerate(clean_names, start=1):
        lines.append(f"| {index} | {name} | 作为{label}随本章后附。 |")
    return "\n".join(lines)


def _rank_form_profile_text_items(
    profiles: list[EnterpriseProfileItem],
    *,
    section_no: str,
) -> list[EnterpriseProfileItem]:
    def score(item: EnterpriseProfileItem) -> tuple[int, int]:
        title = str(item.title or "")
        content = _profile_content_text_for_formal(item)
        value = 100
        if section_no == "7.3.2" and "法定代表人身份证明书" in title:
            value -= 60
        if section_no == "7.3.3" and "投标文件签署授权委托书" in title:
            value -= 60
        if "填写依据" in title or "依据" in title:
            value += 30
        if content:
            value -= min(20, len(content) // 40)
        return (value, item.id or 0)

    return sorted([item for item in profiles if _profile_content_text_for_formal(item)], key=score)


def _formal_profile_document_title(profile: EnterpriseProfileItem, fallback: Any) -> str:
    title = _clean_formal_public_text(profile.title)
    title = re.sub(r"填写依据$", "", title).strip(" -_")
    return title or str(fallback or "投标资料").strip() or "投标资料"


def _profile_content_text_for_formal(profile: EnterpriseProfileItem) -> str:
    text = str(profile.content_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""
    cleaned_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if re.match(r"^#{1,3}\s+", line):
            continue
        if re.match(r"^(资料名称|导入形式|来源|用途)\s*[:：]", line):
            continue
        if re.match(r"^(复核说明|导入说明)\s*[:：]", line):
            continue
        cleaned_lines.append(raw_line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()


def _formal_personnel_section_content_from_materials(
    db: Session,
    material_rows: list[BidMaterialRequirement],
    *,
    fallback_content: str = "",
) -> str:
    profiles = _profile_items_for_section_materials(db, "7.3.8", material_rows, include_fallback=True)
    profile_text = "\n".join(_profile_content_text_for_formal(profile) for profile in profiles)
    personnel_rows = _personnel_table_rows_from_profiles(profiles, profile_text)
    manual_lines = [
        re.sub(r"\s+", " ", str(row.submitted_value or "").strip())
        for row in material_rows
        if str(row.submitted_value or "").strip()
    ]
    if _has_unresolved_personnel_requirement_reference(fallback_content) and not profiles and not manual_lines:
        return ""
    if manual_lines:
        for line in manual_lines:
            parsed = _personnel_row_from_text_line(line)
            if parsed:
                personnel_rows.append(parsed)

    fallback_lines = _personnel_detail_lines_from_text(fallback_content)
    for line in fallback_lines:
        parsed = _personnel_row_from_text_line(line)
        if parsed:
            personnel_rows.append(parsed)

    personnel_rows = _dedupe_personnel_rows(personnel_rows)
    if not personnel_rows and not manual_lines:
        return ""

    lines = [
        "## 拟派驻主要管理人员表",
        "| 岗位 | 姓名 | 主要资格或资料 |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {row[0]} | {row[1]} | {row[2]} |" for row in personnel_rows)

    if manual_lines:
        lines.extend(["", "## 人员简历及补充说明"])
        lines.extend(manual_lines)
    elif fallback_lines:
        lines.extend(["", "## 人员资料说明"])
        lines.append("本章人员安排以投标人拟派项目管理班子资料为依据，人员简历、岗位资格及证书附件见本章附件资料。")

    cert_names = _personnel_certificate_names(profiles)
    if cert_names:
        lines.extend(["", "## 资格证书附件"])
        lines.append("本章后附以下人员简历、岗位资格及证书复印件：")
        lines.extend(f"- {name}" for name in cert_names)
    return "\n".join(lines).strip()


def _has_unresolved_personnel_requirement_reference(content: str) -> bool:
    return "资料需求清单" in str(content or "")


def _personnel_table_rows_from_profiles(
    profiles: list[EnterpriseProfileItem],
    profile_text: str,
) -> list[list[str]]:
    rows: list[list[str]] = []
    manager_name = _text_value_for_label(profile_text, "项目经理")
    manager_cert = _text_value_for_label(profile_text, "一级建造师注册证书号")
    manager_major = _text_value_for_label(profile_text, "注册专业")
    if manager_name:
        detail = _join_nonempty(["一级建造师注册证书号：" + manager_cert if manager_cert else "", "注册专业：" + manager_major if manager_major else ""])
        rows.append(["项目经理", manager_name, detail or "一级建造师及安全生产考核合格证书复印件附后"])

    for profile in profiles:
        title = _clean_formal_public_text(profile.title)
        parsed = _personnel_row_from_certificate_title(title)
        if parsed:
            rows.append(parsed)
    return rows


def _personnel_row_from_certificate_title(title: str) -> list[str] | None:
    parts = [part.strip() for part in str(title or "").split("_") if part.strip()]
    if len(parts) < 2:
        return None
    name = _clean_person_name(parts[0])
    if not name:
        return None
    qualification = "、".join(parts[1:])
    role = _personnel_role_from_text(title, name)
    return [role, name, qualification]


def _personnel_detail_lines_from_text(content: str) -> list[str]:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = re.sub(r"^[#>\-\s|]+", "", raw_line).strip()
        if not line or len(line) > 180:
            continue
        if any(token in line for token in ("资料需求清单", "待补充", "待确认", "待人工")):
            continue
        if any(token in line for token in ("项目经理", "技术负责人", "安全负责人", "施工员", "质量员", "材料员", "资料员")):
            lines.append(line)
    if lines:
        return _unique_text(lines)[:12]

    compact = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(项目经理|技术负责人|安全负责人|施工员|质量员|材料员|资料员)\s*[:：]\s*([\u4e00-\u9fff]{2,4})([^。；;\n]{0,80})"
    )
    result: list[str] = []
    for match in pattern.finditer(compact):
        line = f"{match.group(1)}：{match.group(2)}{match.group(3)}"
        if any(token in line for token in ("资料需求清单", "待补充", "待确认", "待人工")):
            continue
        result.append(line)
    return _unique_text(result)[:12]


def _personnel_row_from_text_line(line: str) -> list[str] | None:
    name_match = re.search(r"姓名\s*[:：]\s*([\u4e00-\u9fff]{2,4})", line)
    role_match = re.search(r"(?:职务|岗位)\s*[:：]\s*([^，,；;。]{2,16})", line)
    if not name_match:
        lead_match = re.match(r"([^：:]{2,16})[:：]\s*([\u4e00-\u9fff]{2,4})", line)
        if lead_match:
            role = lead_match.group(1).strip()
            name = _clean_person_name(lead_match.group(2))
            if name:
                return [role, name, line]
        return None
    name = _clean_person_name(name_match.group(1))
    if not name:
        return None
    role = role_match.group(1).strip() if role_match else _personnel_role_from_text(line, name)
    return [role, name, line]


def _personnel_role_from_text(text: str, name: str) -> str:
    value = str(text or "")
    role_keywords = (
        ("项目经理", ("项目经理", "一级建造师", "建造师", "B证")),
        ("技术负责人", ("技术负责人", "技术")),
        ("安全负责人", ("安全负责人", "安全生产考核合格证书_C", "C3证", "专职安全")),
        ("施工员", ("施工员",)),
        ("材料员", ("材料员",)),
        ("资料员", ("资料员",)),
        ("质量员", ("质量员",)),
        ("企业主要负责人", ("A证", "主要负责人")),
    )
    for role, keywords in role_keywords:
        if any(keyword in value for keyword in keywords):
            return role
    return "主要管理人员" if name else "人员"


def _personnel_certificate_names(profiles: list[EnterpriseProfileItem]) -> list[str]:
    names = [
        _clean_formal_public_text(profile.title)
        for profile in profiles
        if profile.category == "personnel" or "证" in str(profile.title or "")
    ]
    return _unique_text([name for name in names if name])


def _dedupe_personnel_rows(rows: list[list[str]]) -> list[list[str]]:
    result: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if len(row) < 3:
            continue
        key = (row[0], row[1])
        if key in seen:
            continue
        seen.add(key)
        result.append(row[:3])
    return result


def _text_value_for_label(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n\r；;，,]+)", str(text or ""))
    return match.group(1).strip() if match else ""


def _join_nonempty(values: list[str]) -> str:
    return "；".join(value for value in values if value)


def _formalize_brand_table_for_export(content: str) -> str:
    text = str(content or "")
    pattern = re.compile(r"(?s)(##\s*投标单位拟采用的材料品牌表\s*\n)(.*?)(?=\n##\s+|\Z)")

    def replace(match: re.Match[str]) -> str:
        body = match.group(2).strip()
        if _looks_like_markdown_table(body):
            return match.group(0)
        table = _brand_plain_text_to_markdown_table(body)
        return f"{match.group(1)}{table}\n" if table else match.group(0)

    updated = pattern.sub(replace, text)
    if updated != text or _looks_like_markdown_table(updated):
        return updated
    table = _brand_plain_text_to_markdown_table(text)
    if not table:
        return updated
    return f"{updated.rstrip()}\n\n## 投标单位拟采用的材料品牌表\n{table}\n"


def _looks_like_markdown_table(text: str) -> bool:
    return any(_is_markdown_table_line(line.strip()) for line in str(text or "").splitlines())


def _brand_plain_text_to_markdown_table(text: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    lines = [
        line
        for line in lines
        if line and "投标单位拟采用的材料品牌表" not in line and not line.startswith("#")
    ]
    if not lines:
        return ""
    header_index = -1
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if "材料名称" in compact and "招标品牌" in compact and "投标单位选定品牌" in compact:
            header_index = index
            break
    data_lines = lines[header_index + 1 :] if header_index >= 0 else lines
    rows: list[list[str]] = []
    for line in data_lines:
        if line.startswith("##"):
            break
        parts = [part for part in re.split(r"\s+", line.strip()) if part]
        if len(parts) < 3:
            continue
        material_name = parts[0]
        tender_brands = "、".join(parts[1:-1])
        selected_brand = parts[-1]
        rows.append([material_name, tender_brands, selected_brand])
    if not rows:
        rows = _brand_compact_text_to_rows(text)
    if not rows:
        return ""
    table_lines = [
        "| 材料名称 | 招标品牌 | 投标单位选定品牌 |",
        "| --- | --- | --- |",
    ]
    table_lines.extend(f"| {row[0]} | {row[1]} | {row[2]} |" for row in rows)
    return "\n".join(table_lines)


def _brand_compact_text_to_rows(text: str) -> list[list[str]]:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    header = "材料名称 招标品牌 投标单位选定品牌"
    header_index = compact.find(header)
    if header_index >= 0:
        compact = compact[header_index + len(header) :].strip()
    positions: list[tuple[int, str]] = []
    for row_name in MATERIAL_BRAND_ROW_NAMES:
        index = compact.find(row_name)
        if index >= 0:
            positions.append((index, row_name))
    if not positions:
        return []
    positions.sort(key=lambda item: item[0])
    rows: list[list[str]] = []
    for index, (start, row_name) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(compact)
        if end <= start:
            continue
        segment = compact[start + len(row_name) : end].strip()
        brand_parts = [part for part in re.split(r"\s+", segment) if part]
        if not brand_parts:
            continue
        selected_brand = brand_parts[-1]
        tender_brands = "、".join(brand_parts[:-1]) or selected_brand
        rows.append([row_name, tender_brands, selected_brand])
    return rows


def _formalize_personnel_requirement_refs_for_export(
    db: Session,
    content: str,
    material_rows: list[BidMaterialRequirement],
) -> str:
    usable_rows = [row for row in material_rows if _requirement_submission_is_usable(db, row)]
    if not usable_rows:
        return content
    title_to_reference = _personnel_requirement_reference_map(db, usable_rows)
    if not title_to_reference:
        return content

    def replace(match: re.Match[str]) -> str:
        title = re.sub(r"\s+", " ", match.group(1) or "").strip()
        return title_to_reference.get(title) or "详见本章后附人员简历及资格证书资料"

    updated = re.sub(r"见资料需求清单《([^》]+)》", replace, str(content or ""))
    updated = re.sub(r"见资料需求清单", "详见本章后附人员简历及资格证书资料", updated)
    table = _personnel_material_summary_table(db, usable_rows)
    if table and "## 人员资料及资格证书清单" not in updated:
        updated = f"{updated.rstrip()}\n\n## 人员资料及资格证书清单\n{table}\n"
    return updated


def _personnel_requirement_reference_map(
    db: Session,
    rows: list[BidMaterialRequirement],
) -> dict[str, str]:
    result: dict[str, str] = {}
    fallback_refs: list[str] = []
    for row in rows:
        names = _formal_material_names_for_requirement(db, row)
        if not names:
            continue
        reference = f"详见本章后附《{'、'.join(names[:3])}》"
        result[str(row.title or "").strip()] = reference
        fallback_refs.append(reference)
    if not result and fallback_refs:
        result["资料需求清单"] = fallback_refs[0]
    return result


def _formal_material_names_for_requirement(db: Session, row: BidMaterialRequirement) -> list[str]:
    names: list[str] = []
    for profile in _profile_items_for_requirement(db, row):
        if profile.title:
            names.append(_clean_formal_public_text(profile.title))
    for file_obj in _direct_file_objects_for_requirement(db, row):
        if file_obj.original_filename:
            names.append(_clean_formal_public_text(file_obj.original_filename))
    if str(row.submitted_value or "").strip():
        names.append(row.title or "人员资料")
    return _unique_text(names)


def _personnel_material_summary_table(
    db: Session,
    rows: list[BidMaterialRequirement],
) -> str:
    table_rows: list[list[str]] = []
    for row in rows:
        names = _formal_material_names_for_requirement(db, row)
        if not names:
            continue
        table_rows.append(
            [
                str(row.title or row.item_title or "人员资料"),
                "、".join(names[:5]),
                "已作为本章人员简历、岗位资格及证书附件资料纳入正式技术标。",
            ]
        )
    if not table_rows:
        return ""
    lines = [
        "| 资料项 | 已采用资料 | 响应说明 |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {row[0]} | {row[1]} | {row[2]} |" for row in table_rows)
    return "\n".join(lines)


def _formalize_weak_commitment_text(content: str, *, section_no: str) -> str:
    text = str(content or "")
    formal_commitment = (
        "进场后我方将结合施工图、深化设计、合同协议书、现场复核情况及发包人审批意见完成细化报审，"
        "并按批准方案组织实施"
    )
    replacements = [
        (
            r"（?注[:：]?[^\n。；;]*待图纸及协议书进一步明确后细化[^\n。；;]*[。；;]?）?",
            "本章内容将作为投标阶段施工策划和实施承诺，进场后我方将结合施工图、深化设计、合同协议书及发包人审批意见完成细化报审，并按批准方案组织实施。",
        ),
        (
            r"待图纸及协议书进一步明确后细化",
            "进场后结合施工图、深化设计、合同协议书及发包人审批意见完成细化报审，并按批准方案组织实施",
        ),
        (
            r"待图纸[、及和协议书]*进一步明确后细化",
            "进场后结合施工图、深化设计、合同协议书及发包人审批意见完成细化报审，并按批准方案组织实施",
        ),
        (
            r"进一步明确后细化",
            "经深化设计、现场复核及发包人审批后细化实施",
        ),
        (
            r"待.{0,40}进一步.{0,40}细化",
            formal_commitment,
        ),
        (
            r"待图纸",
            "依据施工图、深化设计文件和发包人审批意见",
        ),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    if section_no == "7.3.14":
        text = text.replace(
            "材料采购计划以施工图预算、深化设计和经审批的材料采购计划确定",
            "材料采购计划以施工图、深化设计、招标文件品牌要求、甲指乙供材料计划及经审批的材料采购计划为依据确定",
        )
    if section_no == "7.3.19":
        text = re.sub(
            r"具体工期、特殊技术要求等细节[^。\n]*",
            "具体工期、技术要求、交叉施工及现场界面条件按招标文件、合同协议书、施工图纸及发包人审批意见执行",
            text,
        )
    return text


def _section_no_from_draft_or_component(draft: BidDraftSection, component: dict[str, Any]) -> str:
    for value in (
        draft.section_key,
        draft.section_title,
        component.get("source_item_no") if isinstance(component, dict) else None,
        component.get("component_title") if isinstance(component, dict) else None,
    ):
        match = re.search(r"7[_\.]3[_\.](\d+)", str(value or ""))
        if match:
            return f"7.3.{match.group(1)}"
    return ""


def _technical_final_export_issue_rows(
    db: Session,
    run: BidParseRun,
    *,
    drafts: list[BidDraftSection],
    missing_draft_sections: list[dict[str, Any]],
    attachments_by_draft: dict[int, list[dict[str, Any]]],
    final_content_by_draft_id: dict[int, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_requirement_ids = _current_draft_source_requirement_ids(drafts)
    material_rows: list[BidMaterialRequirement] = []
    blocking_material_rows: list[BidMaterialRequirement] = []
    if current_requirement_ids:
        material_rows = (
            db.query(BidMaterialRequirement)
            .filter(
                BidMaterialRequirement.parse_run_id == run.id,
                BidMaterialRequirement.id.in_(current_requirement_ids),
                BidMaterialRequirement.package_key == "technical",
                BidMaterialRequirement.section_key.like("technical_composition:%"),
                BidMaterialRequirement.status.in_(["missing", "submitted"]),
            )
            .order_by(BidMaterialRequirement.id.asc())
            .all()
        )
        material_by_section: dict[str, list[BidMaterialRequirement]] = {}
        for material_row in material_rows:
            material_by_section.setdefault(str(material_row.section_key or ""), []).append(material_row)
        for material_row in material_rows:
            section_rows = material_by_section.get(str(material_row.section_key or ""), [])
            if material_row.status == "submitted" and _requirement_submission_is_usable(db, material_row):
                continue
            if _requirement_missing_covered_by_section_material(db, material_row, section_rows):
                continue
            blocking_material_rows.append(material_row)
    blocking_material_section_keys = {str(row.section_key or "") for row in blocking_material_rows}

    for item in missing_draft_sections:
        rows.append(
            {
                "section": str(item.get("title") or item.get("section_key") or "-"),
                "issue": "当前组成项尚未生成技术标草稿。",
                "suggestion": "先点击“一键生成技术标草案”，或定位该章节单独生成。",
            }
        )
    for draft in drafts:
        final_content = final_content_by_draft_id.get(draft.id) or draft.content_markdown or ""
        final_content = _formal_final_content_for_issue_check(draft, final_content, final_content_by_draft_id)
        for finding in _formal_export_blocking_findings(final_content):
            if finding["code"] in {"资料需求清单", "待补充"} and str(draft.section_key or "") in blocking_material_section_keys:
                continue
            rows.append(
                {
                    "code": finding["code"],
                    "section": draft.section_title or draft.section_key or "-",
                    "issue": finding["issue"],
                    "suggestion": finding["suggestion"],
                }
            )
        if not _formal_export_visible_text(final_content) and not attachments_by_draft.get(draft.id):
            rows.append(
                {
                    "section": draft.section_title or draft.section_key or "-",
                    "issue": "正式正文清洗后为空，且本章没有可嵌入附件。",
                    "suggestion": "请重新生成该章正文，或补充可用的企业资料附件后再导出正式稿。",
                }
            )
        for item in attachments_by_draft.get(draft.id) or []:
            if item.get("can_embed_image"):
                continue
            rows.append(
                {
                    "section": draft.section_title or draft.section_key or "-",
                    "issue": f"附件“{item.get('filename') or '-'}”暂不能嵌入正式 Word。",
                    "suggestion": "请先转换为 PNG/JPG 图片后重新上传，或人工装订到正式投标文件。",
                }
            )
    for row in blocking_material_rows:
        required_information = _material_requirement_required_information(row)
        status_label = _requirement_status_label(row.status)
        rows.append(
            {
                "code": "material_requirement_missing",
                "section": row.item_title or row.section_key or "-",
                "issue": f"需补充：{required_information}（当前状态：{status_label}）。",
                "suggestion": (
                    f"处理入口：本页面“技术标资料需求与补齐清单”→“{row.title}”→“填写”；"
                    "录入明确内容或资料位置，保存后点击“确认可用”，再重新生成对应章节。"
                ),
                "required_information": required_information,
                "requirement_uuid": row.requirement_uuid,
                "section_key": row.section_key,
                "requirement_status": row.status,
                "action": "fill_confirm_and_regenerate",
            }
        )
    return _dedupe_issue_rows(rows)


def _material_requirement_required_information(row: BidMaterialRequirement) -> str:
    description = re.sub(r"\s+", " ", str(row.description or "")).strip()
    if "需补充：" in description:
        description = description.split("需补充：", 1)[1].strip()
    description = description.rstrip("。；; ")
    return description or str(row.title or row.item_title or "本章节所需资料").strip()


def _formal_final_content_for_issue_check(
    draft: BidDraftSection,
    final_content: str,
    final_content_by_draft_id: dict[int, str],
) -> str:
    section_no = _section_no_from_draft_or_component(draft, {})
    if section_no not in FORMAL_SCHEME_SECTION_NOS:
        return final_content
    updated = _formalize_final_export_text(final_content, section_no=section_no)
    final_content_by_draft_id[draft.id] = updated
    return updated


def _formal_export_blocking_findings(content: str) -> list[dict[str, str]]:
    text = str(content or "")
    specs = [
        (
            "editorial_residue",
            r"本应原文|原文应为|以下为重写后的|请替换为正式内容",
            "正文仍包含编辑过程或改写提示。",
            "按招标文件事实重写命中句，删除编辑说明后再导出正式稿。",
        ),
        (
            "duplicated_function_word",
            r"的的(?!确)|了了|和和|及及|与与|为为|在在|对对|按按|应应|将将|需需|并并|或或|由由",
            "正文仍包含连续重复虚词。",
            "修正命中句的拼接或模型输出残留，并复核相邻句语义。",
        ),
        (
            "unresolved_template_token",
            r"\{\{[^{}\n]{1,80}\}\}|\$\{[^{}\n]{1,80}\}|\[\[(?:TODO|TBD|PLACEHOLDER)[^\]\n]*\]\]",
            "正文仍包含未解析的模板变量。",
            "从项目事实表解析变量；缺失字段应转为人工填写位并阻止正式稿输出。",
        ),
        (
            "待确认",
            r"待确认",
            "正文仍包含“待确认”。",
            "转成明确资料需求、从招标文件抽取，或人工修正文案后再定稿。",
        ),
        (
            "待补充",
            r"【?待补充】?|待人工完善",
            "正文仍包含待补充/待人工完善内容。",
            "先在资料需求中补齐并重新生成该章，不能把待补充提示带入正式投标文件。",
        ),
        (
            "资料需求清单",
            r"见资料需求清单|资料需求清单《[^》]+》?",
            "正文仍引用“资料需求清单”，说明资料没有真正落入正式正文。",
            "将对应资料填入正文或作为本章正式附件引用，不能继续引用系统补齐清单。",
        ),
        (
            "内部工作流痕迹",
            "|".join(re.escape(term) for term in FORMAL_EXPORT_INTERNAL_TERMS),
            "正文仍包含系统工作流痕迹。",
            "删除系统来源、企业资料库检索说明和草稿提示，改成正式投标文件表达。",
        ),
        (
            "Markdown代码块",
            r"```",
            "正文仍包含 Markdown 代码块标记。",
            "将代码块内容改为 Word 表格、层级文字或正式示意图。",
        ),
        (
            "项目事实截断",
            r"商业街[、，]\s*层办公|(?<![0-9#楼F])层办公(?=[，,、；;\s])",
            "正文疑似包含项目范围截断表达。",
            "改为完整项目范围，如“商业街区、6#楼32F办公区”，不能带入被截断的区域名称。",
        ),
        (
            "显性占位",
            r"(?<![A-Za-z])[XxＸｘ]{2,}(?![A-Za-z])|[XxＸｘ]{2,}\s*年|[XxＸｘ]{2,}\s*月|[XxＸｘ]{2,}\s*日|约\s*[XxＸｘ]{2,}\s*人|[×＊*]\s*人",
            "正文仍包含显性占位符。",
            "用已抽取的招标事实、保守资源配置口径或正式承诺表达替换，不能把 XX/XXXX 带入正式稿。",
        ),
        (
            "内部文件溯源",
            r"(?i:(?:DOCX|PDF|WORD)\s*第\s*\d+(?:\s*[-—‑–~至]\s*\d+)?\s*(?:段|页)|(?:技术要求|招标文件)[^，。；：\n]{0,50}\.(?:docx?|pdf|xlsx?))",
            "正文仍包含源文件名或内部段页坐标。",
            "删除 DOCX/PDF 段页坐标和源文件名，仅保留正式的招标文件依据表述。",
        ),
        (
            "未定稿图示",
            r"附图[（(]?略|实际标书|将附清晰|图[（(]略",
            "正文仍包含未定稿图示表达。",
            "改为正式表格、明确附件引用或可提交的文字说明，不能写“略”或“实际标书中将附”。",
        ),
        (
            "未定稿弱承诺",
            r"待图纸|进一步明确后细化|待.{0,20}进一步.{0,20}细化|如有|如需",
            "正文仍包含未定稿式弱承诺。",
            "改成投标承诺式表达，或在招标文件允许的前提下写明进场深化报审机制。",
        ),
        (
            "非正式投标口径",
            r"随本章后附|按合同|按清单|甲方|总包",
            "正文仍包含草稿化或口语化投标表述。",
            "改为发包人、总承包单位、招标文件及合同约定、招标工程量清单等正式投标文件表达。",
        ),
        (
            "疑似截断表单",
            r"(?:职务|身份证号码|投标人|法定代表人|授权委托日期)\s*[:：][^\n\r]{0,40}\s[投法授委]$",
            "正文疑似包含被截断的表单字段。",
            "请从企业资料库读取完整文本或重新生成该章，不能带入尾部残缺字段。",
        ),
    ]
    findings: list[dict[str, str]] = []
    for code, pattern, issue, suggestion in specs:
        matches = re.findall(pattern, text)
        if not matches:
            continue
        count = len(matches)
        findings.append(
            {
                "code": code,
                "issue": f"{issue}（{count} 处）",
                "suggestion": suggestion,
            }
        )
    return findings


def _formal_export_visible_text(content: str) -> str:
    text = re.sub(r"^#{1,6}\s+.*$", "", str(content or ""), flags=re.M)
    text = re.sub(r"[-|:\s]+", "", text)
    return text.strip()


def _append_formal_cover(
    doc: _TenderAnalysisDocxBuilder,
    db: Session,
    project: BidProject,
    run: BidParseRun,
    drafts: list[BidDraftSection],
) -> None:
    bidder_name = _enterprise_bidder_name(db)
    project_manager_name = _extract_project_manager_name(drafts)
    project_manager_cert = _extract_project_manager_cert_no(drafts)
    legal_representative = _extract_legal_representative_name(db, drafts)
    bid_date = _extract_bid_document_date(project, run, drafts)
    tender_project_no = _extract_tender_project_no(project, run)
    if tender_project_no:
        doc.add_paragraph(f"项目招标编号：{tender_project_no}", style="Body")
    doc.add_paragraph("正本", style="DocSubtitle")
    doc.add_title("建设工程施工招标")
    doc.add_title("投 标 文 件")
    cover_rows = [
        ("工程名称", _formal_project_name(project.project_name)),
        ("投标文件内容", "技术标部分"),
        ("投标人(盖章)", bidder_name),
        ("法定代表人或委托代理人(签字或盖章)", legal_representative),
        ("项目经理签字", project_manager_name),
        ("一级建造师注册证书号", project_manager_cert),
        ("日期", bid_date),
    ]
    doc.add_kv_table([(label, value) for label, value in cover_rows if str(value or "").strip()])


def _extract_tender_project_no(project: BidProject, run: BidParseRun) -> str:
    candidate_keys = (
        "项目招标编号",
        "招标编号",
        "项目编号",
        "工程编号",
        "标段编号",
        "tender_no",
        "tender_number",
        "project_no",
        "project_code",
        "bid_no",
        "bid_number",
    )
    payloads = [
        loads_json(project.summary_json, {}) if project.summary_json else {},
        loads_json(run.summary_json, {}) if run.summary_json else {},
    ]
    for payload in payloads:
        for value in _iter_tender_no_candidates(payload, candidate_keys):
            text = re.sub(r"\s+", " ", str(value or "").strip())
            if not text or text in {"-", "/", "无", "暂无", "未提供"}:
                continue
            if len(text) > 80:
                continue
            return text
    return ""


def _iter_tender_no_candidates(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    result: list[Any] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key or "").strip()
            if any(token in key_text for token in keys):
                if not isinstance(value, (dict, list)):
                    result.append(value)
            result.extend(_iter_tender_no_candidates(value, keys))
    elif isinstance(payload, list):
        for item in payload:
            result.extend(_iter_tender_no_candidates(item, keys))
    return result


def _append_formal_table_of_contents(
    doc: _TenderAnalysisDocxBuilder,
    drafts: list[BidDraftSection],
    components: list[dict[str, Any]],
) -> None:
    doc.add_paragraph("目 录", style="DocSubtitle")
    components_by_key = {
        _technical_section_key(component, index): component
        for index, component in enumerate(components, start=1)
    }
    fallback_lines = [
        _formal_chapter_title(draft, components_by_key.get(str(draft.section_key or ""), {}), index)
        for index, draft in enumerate(drafts, start=1)
    ]
    doc.add_toc_field(levels="1-2", fallback_lines=fallback_lines)


def _append_formal_attachment_assets_to_doc(doc: _TenderAnalysisDocxBuilder, assets: list[dict[str, Any]]) -> None:
    if not assets:
        return
    doc.add_paragraph("附件资料", style="Heading2")
    for index, item in enumerate(assets, start=1):
        filename = _clean_formal_public_text(item.get("filename") or f"附件{index}") or f"附件{index}"
        title = f"附件{index}：{filename}"
        if item.get("profile_title"):
            profile_title = _clean_formal_public_text(item["profile_title"])
            if profile_title:
                title += f"（{profile_title}）"
        if not item.get("can_embed_image"):
            raise BidTechnicalWordExportError(
                "BID_TECHNICAL_ATTACHMENT_NOT_EMBEDDABLE",
                {"message": f"附件“{filename}”暂不能嵌入正式 Word，请先转换为 PNG/JPG。"},
            )
        try:
            image_bytes = get_object_bytes(str(item.get("object_name") or ""), str(item.get("bucket") or "") or None)
            width_px, height_px = _image_dimensions(image_bytes, str(item.get("extension") or ""), str(item.get("content_type") or ""))
            doc.add_paragraph(title, style="Body")
            if not doc.add_image(
                image_bytes,
                extension=str(item.get("extension") or ""),
                filename=filename,
                width_px=width_px,
                height_px=height_px,
            ):
                raise BidTechnicalWordExportError(
                    "BID_TECHNICAL_ATTACHMENT_EMBED_FAILED",
                    {"message": f"附件“{filename}”图片格式暂不支持嵌入正式 Word。"},
                )
        except BidTechnicalWordExportError:
            raise
        except Exception as exc:
            raise BidTechnicalWordExportError(
                "BID_TECHNICAL_ATTACHMENT_EMBED_FAILED",
                {"message": f"附件“{filename}”读取失败，未能嵌入正式 Word。原因：{str(exc)[:180]}"},
            ) from exc


def _collect_draft_attachment_assets(db: Session, run: BidParseRun, draft: BidDraftSection) -> list[dict[str, Any]]:
    requirements = _requirements_for_draft_attachments(db, run, draft)
    assets: list[dict[str, Any]] = []
    seen_file_ids: set[str] = set()
    seen_profile_uuids: set[str] = set()
    for requirement in requirements:
        for file_obj in _direct_file_objects_for_requirement(db, requirement):
            _append_file_asset(
                assets,
                seen_file_ids,
                requirement=requirement,
                file_obj=file_obj,
                source_label="人工上传附件",
                profile_title=None,
            )
        for profile in _profile_items_for_requirement(db, requirement):
            seen_profile_uuids.add(str(profile.item_uuid or ""))
            _append_profile_attachment_assets(
                db,
                assets,
                seen_file_ids,
                requirement=requirement,
                profile=profile,
                source_label="企业资料库附件",
            )
    section_no = _section_no_from_draft_or_component(draft, {})
    for profile in _fallback_profile_items_for_section(db, section_no, exclude_uuids=seen_profile_uuids):
        _append_profile_attachment_assets(
            db,
            assets,
            seen_file_ids,
            requirement=None,
            profile=profile,
            source_label="企业资料库附件（章节兜底）",
        )
    return assets


def _append_profile_attachment_assets(
    db: Session,
    assets: list[dict[str, Any]],
    seen_file_ids: set[str],
    *,
    requirement: BidMaterialRequirement | None,
    profile: EnterpriseProfileItem,
    source_label: str,
) -> None:
    attachments = sorted(profile.attachments or [], key=lambda item: (not bool(item.is_primary), item.created_at or datetime.min))
    for attachment in attachments:
        file_obj = attachment.file_object
        if not file_obj and attachment.file_id:
            file_obj = db.query(FileObject).filter(FileObject.file_id == attachment.file_id).first()
        if not file_obj:
            continue
        _append_file_asset(
            assets,
            seen_file_ids,
            requirement=requirement,
            file_obj=file_obj,
            source_label=source_label,
            profile_title=profile.title,
            attachment=attachment,
        )


def _requirements_for_draft_attachments(db: Session, run: BidParseRun, draft: BidDraftSection) -> list[BidMaterialRequirement]:
    source_ids = _clean_int_list(loads_json(draft.source_requirement_ids_json, []) if draft.source_requirement_ids_json else [])
    conditions = [BidMaterialRequirement.section_key == draft.section_key]
    if source_ids:
        conditions.append(BidMaterialRequirement.id.in_(source_ids))
    return (
        db.query(BidMaterialRequirement)
        .filter(
            BidMaterialRequirement.parse_run_id == run.id,
            BidMaterialRequirement.package_key == "technical",
            BidMaterialRequirement.status.in_(["submitted", "approved", "applied"]),
            or_(*conditions),
        )
        .order_by(BidMaterialRequirement.id.asc())
        .all()
    )


def _direct_file_objects_for_requirement(db: Session, requirement: BidMaterialRequirement) -> list[FileObject]:
    file_ids = _submitted_file_ids(requirement)
    if not file_ids:
        return []
    rows = db.query(FileObject).filter(FileObject.file_id.in_(file_ids)).all()
    by_id = {row.file_id: row for row in rows}
    return [by_id[file_id] for file_id in file_ids if file_id in by_id]


def _profile_items_for_requirement(db: Session, requirement: BidMaterialRequirement) -> list[EnterpriseProfileItem]:
    item_uuids = _submitted_profile_item_uuids(requirement)
    if not item_uuids:
        return []
    rows = (
        db.query(EnterpriseProfileItem)
        .filter(
            EnterpriseProfileItem.item_uuid.in_(item_uuids),
            EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE,
        )
        .all()
    )
    by_uuid = {row.item_uuid: row for row in rows}
    return [by_uuid[item_uuid] for item_uuid in item_uuids if item_uuid in by_uuid]


def _profile_items_for_section_materials(
    db: Session,
    section_no: str,
    material_rows: list[BidMaterialRequirement],
    *,
    include_fallback: bool,
) -> list[EnterpriseProfileItem]:
    items: list[EnterpriseProfileItem] = []
    seen_uuids: set[str] = set()
    for row in material_rows:
        for item in _profile_items_for_requirement(db, row):
            uuid_value = str(item.item_uuid or "")
            if uuid_value in seen_uuids:
                continue
            seen_uuids.add(uuid_value)
            items.append(item)
    if include_fallback:
        for item in _fallback_profile_items_for_section(db, section_no, exclude_uuids=seen_uuids):
            uuid_value = str(item.item_uuid or "")
            if uuid_value in seen_uuids:
                continue
            seen_uuids.add(uuid_value)
            items.append(item)
    return items


def _fallback_profile_items_for_section(
    db: Session,
    section_no: str,
    *,
    exclude_uuids: set[str] | None = None,
) -> list[EnterpriseProfileItem]:
    specs = FORMAL_SECTION_FALLBACK_PROFILE_SPECS.get(str(section_no or ""))
    if not specs:
        return []
    excluded = exclude_uuids or set()
    rows = (
        db.query(EnterpriseProfileItem)
        .filter(EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE)
        .order_by(EnterpriseProfileItem.id.asc())
        .all()
    )
    result: list[EnterpriseProfileItem] = []
    for row in rows:
        if str(row.item_uuid or "") in excluded:
            continue
        if any(_profile_item_matches_fallback_spec(row, spec) for spec in specs):
            result.append(row)
    return result


def _profile_item_matches_fallback_spec(item: EnterpriseProfileItem, spec: dict[str, tuple[str, ...]]) -> bool:
    category_values = spec.get("category")
    if category_values and str(item.category or "") not in category_values:
        return False
    subcategory_values = spec.get("subcategory")
    if subcategory_values and str(item.subcategory or "") not in subcategory_values:
        return False
    title_values = spec.get("title")
    if title_values:
        title = str(item.title or "")
        return any(value in title for value in title_values)
    return True


def _append_file_asset(
    assets: list[dict[str, Any]],
    seen_file_ids: set[str],
    *,
    requirement: BidMaterialRequirement | None,
    file_obj: FileObject,
    source_label: str,
    profile_title: str | None,
    attachment: EnterpriseProfileFile | None = None,
) -> None:
    if file_obj.file_id in seen_file_ids:
        return
    seen_file_ids.add(file_obj.file_id)
    filename = (
        (attachment.original_filename if attachment else None)
        or file_obj.original_filename
        or file_obj.file_id
    )
    extension = _file_extension(filename, file_obj.content_type)
    content_type = str(file_obj.content_type or "").lower()
    can_embed_image = _is_supported_docx_image(extension, content_type)
    assets.append(
        {
            "requirement_id": requirement.id if requirement else None,
            "requirement_title": requirement.title if requirement else profile_title,
            "section_title": requirement.item_title if requirement else None,
            "source_label": source_label,
            "profile_title": profile_title,
            "file_id": file_obj.file_id,
            "filename": filename,
            "content_type": file_obj.content_type,
            "size_bytes": file_obj.size_bytes,
            "bucket": file_obj.bucket,
            "object_name": file_obj.object_name,
            "extension": extension,
            "can_embed_image": can_embed_image,
        }
    )


def _append_attachment_assets_to_doc(doc: _TenderAnalysisDocxBuilder, assets: list[dict[str, Any]]) -> None:
    if not assets:
        return
    doc.add_paragraph("附件资料", style="Heading2")
    doc.add_table(
        [["资料需求", "附件名称", "来源", "嵌入状态"]]
        + [
            [
                item.get("requirement_title") or "-",
                item.get("filename") or "-",
                _attachment_source_text(item),
                "图片已嵌入" if item.get("can_embed_image") else "非图片，清单引用",
            ]
            for item in assets
        ],
        widths=(2600, 3400, 1800, 1560),
        header=True,
    )
    for index, item in enumerate(assets, start=1):
        filename = str(item.get("filename") or f"附件{index}")
        title = f"附件{index}：{filename}"
        if item.get("profile_title"):
            title += f"（{item['profile_title']}）"
        if not item.get("can_embed_image"):
            doc.add_paragraph(f"{title}：该附件为 {item.get('content_type') or item.get('extension') or '非图片文件'}，MVP 暂不嵌入正文，仅在清单中引用。")
            continue
        try:
            image_bytes = get_object_bytes(str(item.get("object_name") or ""), str(item.get("bucket") or "") or None)
            width_px, height_px = _image_dimensions(image_bytes, str(item.get("extension") or ""), str(item.get("content_type") or ""))
            doc.add_paragraph(title, style="Body")
            if not doc.add_image(
                image_bytes,
                extension=str(item.get("extension") or ""),
                filename=filename,
                width_px=width_px,
                height_px=height_px,
            ):
                doc.add_paragraph(f"{title}：当前图片格式暂不支持嵌入，请人工检查附件。")
        except Exception as exc:
            doc.add_paragraph(f"{title}：附件读取失败，未能嵌入图片。原因：{str(exc)[:180]}")


def _attachment_source_text(item: dict[str, Any]) -> str:
    profile_title = str(item.get("profile_title") or "").strip()
    source = str(item.get("source_label") or "").strip()
    return f"{source} / {profile_title}" if profile_title else source or "-"


def _manual_submission(row: BidMaterialRequirement) -> dict[str, Any]:
    normalized = loads_json(row.normalized_json, {}) if row.normalized_json else {}
    manual = normalized.get("manual_submission") if isinstance(normalized, dict) else {}
    return dict(manual) if isinstance(manual, dict) else {}


def _submitted_profile_item_uuids(row: BidMaterialRequirement) -> list[str]:
    manual = _manual_submission(row)
    values = _clean_string_list(manual.get("profile_item_uuids"))
    if values:
        return values
    return _clean_string_list([row.submitted_profile_item_uuid])


def _submitted_file_ids(row: BidMaterialRequirement) -> list[str]:
    manual = _manual_submission(row)
    values = _clean_string_list(manual.get("file_ids"))
    if values:
        return values
    return _clean_string_list([row.submitted_file_id])


def _clean_string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _clean_int_list(values: Any) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        result.append(number)
    return result


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


def _file_extension(filename: Any, content_type: Any = None) -> str:
    name = str(filename or "").lower()
    if "." in name:
        ext = re.sub(r"[^a-z0-9]+", "", name.rsplit(".", 1)[-1])
        if ext == "jpeg":
            return "jpg"
        if ext:
            return ext
    ctype = str(content_type or "").lower()
    if ctype == "image/png":
        return "png"
    if ctype in {"image/jpeg", "image/jpg"}:
        return "jpg"
    return ""


def _is_supported_docx_image(extension: str, content_type: str) -> bool:
    return _file_extension(f"file.{extension}", content_type) in {"png", "jpg", "jpeg"} or content_type in {"image/png", "image/jpeg", "image/jpg"}


def _image_dimensions(content: bytes, extension: str, content_type: str) -> tuple[int | None, int | None]:
    ext = _file_extension(f"file.{extension}", content_type)
    if ext == "png" and len(content) >= 24 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    if ext in {"jpg", "jpeg"}:
        return _jpeg_dimensions(content)
    return None, None


def _jpeg_dimensions(content: bytes) -> tuple[int | None, int | None]:
    if len(content) < 4 or content[:2] != b"\xff\xd8":
        return None, None
    index = 2
    while index + 9 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        marker = content[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(content):
            break
        segment_length = int.from_bytes(content[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(content):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(content[index + 3:index + 5], "big")
            width = int.from_bytes(content[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    return None, None


def _technical_composition_plan(run: BidParseRun) -> dict[str, Any]:
    summary = loads_json(run.summary_json, {}) if run.summary_json else {}
    plan = summary.get("technical_composition_plan") if isinstance(summary, dict) else None
    if isinstance(plan, dict) and plan.get("status") == "generated":
        return plan
    return {}


def _current_technical_section_keys(components: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for index, component in enumerate(components, start=1):
        keys.add(_technical_section_key(component, index))
    return keys


def _is_current_technical_composition_draft(row: BidDraftSection, current_keys: set[str]) -> bool:
    section_key = str(row.section_key or "")
    if not section_key.startswith("technical_composition:"):
        return False
    return not current_keys or section_key in current_keys


def _order_drafts_by_composition(
    drafts: list[BidDraftSection],
    components: list[dict[str, Any]],
) -> list[BidDraftSection]:
    order = {_technical_section_key(component, index): index for index, component in enumerate(components, start=1)}
    return sorted(drafts, key=lambda row: (order.get(str(row.section_key or ""), 100000), row.id or 0))


def _missing_draft_sections(
    drafts: list[BidDraftSection],
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = {str(row.section_key or "") for row in drafts}
    missing: list[dict[str, Any]] = []
    for index, component in enumerate(components, start=1):
        section_key = _technical_section_key(component, index)
        if section_key in existing:
            continue
        missing.append(
            {
                "section_key": section_key,
                "title": _technical_component_title(component, index),
            }
        )
    return missing


def _technical_section_key(component: dict[str, Any], index: int) -> str:
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


def _technical_component_key(component: dict[str, Any], index: int) -> str:
    raw = component.get("component_key") or component.get("source_item_no") or component.get("component_title") or f"component_{index}"
    key = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(raw)).strip("_").lower()
    return (key or f"component_{index}")[:120]


def _technical_component_title(component: dict[str, Any], index: int) -> str:
    title = str(component.get("component_title") or "").strip()
    source_item_no = str(component.get("source_item_no") or "").strip()
    if source_item_no and title and not title.startswith(source_item_no):
        return f"{source_item_no} {title}"[:255]
    return (title or f"技术标组成项{index}")[:255]


def _formal_chapter_title(draft: BidDraftSection, component: dict[str, Any], index: int) -> str:
    section_no = _section_no_from_draft_or_component(draft, component if isinstance(component, dict) else {})
    raw_title = str(FORMAL_TECHNICAL_SECTION_TITLES.get(section_no) or component.get("component_title") or draft.section_title or f"技术标章节{index}").strip()
    title = _strip_source_item_no(raw_title) or f"技术标章节{index}"
    return f"第{_chapter_number_cn(index)}章、 {title}"


def _strip_source_item_no(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*\d+(?:\.\d+)+\s*[、.．]?\s*", "", text)
    text = re.sub(r"^第[一二三四五六七八九十]{1,3}章[、.．]?\s*", "", text)
    return text.strip()


def _chapter_number_cn(index: int) -> str:
    numerals = "零一二三四五六七八九"
    if index <= 0:
        return str(index)
    if index < 10:
        return numerals[index]
    if index == 10:
        return "十"
    if index < 20:
        return f"十{numerals[index - 10]}"
    if index == 20:
        return "二十"
    if index < 30:
        return f"二十{numerals[index - 20]}"
    return str(index)


def _formal_project_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[A-Za-z][A-Za-z0-9_-]{1,24}--\s*", "", text)
    text = re.sub(r"\.(?:pdf|docx?|xlsx?)$", "", text, flags=re.I)
    text = re.sub(r"(?:招标文件|技术标草稿|技术标正式稿)(?:_\d{8}_\d{6})?$", "", text).strip("_ -")
    text = re.sub(r"\s+", " ", text)
    return text


def _enterprise_bidder_name(db: Session) -> str:
    rows = (
        db.query(EnterpriseProfileItem)
        .filter(
            EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE,
            EnterpriseProfileItem.category == "basic_info",
        )
        .order_by(EnterpriseProfileItem.id.asc())
        .limit(20)
        .all()
    )
    for row in rows:
        structured = loads_json(row.structured_json, {}) if row.structured_json else {}
        name = _enterprise_name_from_structured(structured)
        if name:
            return name
        text = "\n".join(part for part in [row.title, row.summary, row.content_text] if part)
        name = _enterprise_name_from_text(text)
        if name:
            return name
    return ""


def _enterprise_name_from_structured(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("企业名称", "公司名称", "投标人名称", "单位名称", "name", "company_name", "enterprise_name"):
            candidate = str(value.get(key) or "").strip()
            if _looks_like_enterprise_name(candidate):
                return candidate[:120]
        for item in value.values():
            candidate = _enterprise_name_from_structured(item)
            if candidate:
                return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _enterprise_name_from_structured(item)
            if candidate:
                return candidate
    return ""


def _enterprise_name_from_text(text: Any) -> str:
    value = str(text or "")
    patterns = [
        r"(?:企业名称|公司名称|投标人名称|单位名称)\s*[:：]\s*([^\n\r；;，,]{2,80})",
        r"投标人\s*[:：]\s*([^\n\r；;，,]{2,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match and _looks_like_enterprise_name(match.group(1)):
            return match.group(1).strip()[:120]
    return ""


def _looks_like_enterprise_name(value: str) -> bool:
    text = str(value or "").strip()
    if len(text) < 4:
        return False
    return any(marker in text for marker in ("公司", "集团", "企业", "事务所", "设计院", "工程局"))


def _extract_legal_representative_name(db: Session, drafts: list[BidDraftSection]) -> str:
    rows = _active_profile_rows_for_cover(db)
    for row in _prioritized_legal_representative_rows(rows):
        structured = loads_json(row.structured_json, {}) if row.structured_json else {}
        name = _legal_representative_from_structured(structured)
        if name:
            return name
        text = "\n".join(part for part in [row.title, row.summary, row.content_text] if part)
        name = _legal_representative_from_text(text)
        if name:
            return name
    return _legal_representative_from_text(_joined_draft_content(drafts))


def _prioritized_legal_representative_rows(rows: list[EnterpriseProfileItem]) -> list[EnterpriseProfileItem]:
    def score(row: EnterpriseProfileItem) -> tuple[int, int]:
        text = " ".join(str(part or "") for part in (row.title, row.summary, row.content_text))
        value = 100
        if "法定代表人身份证明" in text or "法定代表人证明" in text:
            value -= 80
        if "营业执照" in text or str(row.subcategory or "") in {"company_basic_info", "legal_representative_proof_basis"}:
            value -= 40
        if "填写依据" in str(row.title or ""):
            value += 10
        if "授权委托" in text or "委托代理人" in text:
            value += 50
        return (value, row.id or 0)

    return sorted(rows, key=score)


def _active_profile_rows_for_cover(db: Session) -> list[EnterpriseProfileItem]:
    return (
        db.query(EnterpriseProfileItem)
        .filter(
            EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE,
            EnterpriseProfileItem.category.in_(["basic_info", "personnel", "commitment_template", "other"]),
        )
        .order_by(EnterpriseProfileItem.id.asc())
        .limit(80)
        .all()
    )


def _legal_representative_from_structured(value: Any) -> str:
    keys = ("法定代表人", "法定代表人姓名", "法人", "法人代表", "legal_representative")
    if isinstance(value, dict):
        for key in keys:
            candidate = _clean_person_name(value.get(key))
            if candidate:
                return candidate
        for item in value.values():
            candidate = _legal_representative_from_structured(item)
            if candidate:
                return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _legal_representative_from_structured(item)
            if candidate:
                return candidate
    return ""


def _legal_representative_from_text(text: Any) -> str:
    value = str(text or "")
    patterns = [
        r"(?:法定代表人|法人代表|法人)(?:姓名)?\s*[:：]\s*([\u4e00-\u9fff]{2,6})",
        r"法定代表人或委托代理人(?:\(签字或盖章\))?\s*[:：]\s*([\u4e00-\u9fff]{2,6})",
        r"姓名\s*[:：]\s*([\u4e00-\u9fff]{2,6})[^\n\r]{0,80}?的法定代表人",
        r"我\s*([\u4e00-\u9fff]{2,6})\s*(?:\(姓名\))?系[^\n\r]{0,80}?的法定代表人",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            candidate = _clean_person_name(match.group(1))
            if candidate:
                return candidate
    return ""


def _extract_project_manager_name(drafts: list[BidDraftSection]) -> str:
    text = _joined_draft_content(drafts)
    plain_text = re.sub(r"[*_`#]+", "", text)
    line_patterns = [
        r"项目经理(?:签字|姓名)?\s*[:：]\s*([^\s；;，,。]{2,8})",
        r"项目经理[^\n\r；;，,。]{0,12}(?:为|由)\s*([^\s；;，,。]{2,8})",
        r"姓名\s*[:：]\s*([\u4e00-\u9fff]{2,6})[^\n\r]{0,40}?职务\s*[:：]\s*项目经理",
    ]
    for line in plain_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if "项目经理" not in line:
            continue
        for pattern in line_patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            candidate = _clean_person_name(match.group(1))
            if candidate:
                return candidate
    fallback_patterns = [
        r"项目经理[^\n\r]{0,20}(?:姓名|为|由)\s*[:：]?\s*([\u4e00-\u9fff]{2,6})",
        r"项目经理[^\n\r]{0,120}?姓名\s*[:：]\s*([\u4e00-\u9fff]{2,6})",
        r"项目经理[\s\S]{0,240}?姓名\s*[:：]\s*([\u4e00-\u9fff]{2,6})",
        r"([\u4e00-\u9fff]{2,6})[^\n\r]{0,40}?一级建造师注册证书编号",
    ]
    for pattern in fallback_patterns:
        for match in re.finditer(pattern, plain_text):
            candidate = _clean_person_name(match.group(1))
            if candidate:
                return candidate
    return ""


def _extract_project_manager_cert_no(drafts: list[BidDraftSection]) -> str:
    text = _joined_draft_content(drafts)
    patterns = [
        r"(?:一级建造师注册证书号|注册编号|注册证书号)\s*[:：]?\s*([A-Za-z\u4e00-\u9fff]{0,4}\d{8,20})",
        r"(粤\d{10,20})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _clean_person_name(value: Any) -> str:
    text = re.sub(r"[^\u4e00-\u9fff]", "", str(value or "").strip())
    if not 2 <= len(text) <= 4:
        return ""
    if any(token in text for token in ("项目", "经理", "签字", "姓名", "一级", "建造", "注册", "证书", "法定", "代表", "委托", "代理")):
        return ""
    return text


def _extract_bid_document_date(project: BidProject, run: BidParseRun, drafts: list[BidDraftSection]) -> str:
    text = _joined_draft_content(drafts)
    patterns = [
        r"日期\s*[:：]\s*((?:20\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        r"((?:20\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        r"((?:20\d{2})[-/.]\d{1,2}[-/.]\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            parsed = _parse_date_text(match.group(1))
            if parsed:
                return _format_cn_date(parsed)
    for candidate in (project.tender_deadline_at, project.bid_open_at, run.finished_at):
        if isinstance(candidate, datetime):
            return _format_cn_date(candidate.date())
        if isinstance(candidate, date):
            return _format_cn_date(candidate)
    return ""


def _parse_date_text(value: Any) -> date | None:
    text = re.sub(r"\s+", "", str(value or ""))
    match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    if not match:
        match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _format_cn_date(value: date) -> str:
    return f"{value.year} 年 {value.month:02d} 月 {value.day:02d} 日"


def _joined_draft_content(drafts: list[BidDraftSection]) -> str:
    return "\n".join(str(row.content_markdown or "") for row in drafts)


def _draft_status_counts(drafts: list[BidDraftSection]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in drafts:
        key = str(row.draft_status or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _technical_export_issue_rows(
    db: Session,
    run: BidParseRun,
    drafts: list[BidDraftSection],
    missing_draft_sections: list[dict[str, Any]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for draft in drafts:
        placeholders = [item for item in loads_json(draft.placeholders_json, []) or [] if isinstance(item, dict)]
        if draft.draft_status != "ready":
            rows.append(
                {
                    "section": draft.section_title or draft.section_key or "-",
                    "issue": f"章节状态为“{_draft_status_label(draft.draft_status)}”。",
                    "suggestion": "补齐资料需求后重新生成或人工编辑章节正文。",
                }
            )
        for item in placeholders[:5]:
            rows.append(
                {
                    "section": draft.section_title or draft.section_key or "-",
                    "issue": str(item.get("label") or item.get("detail") or "待补齐资料")[:180],
                    "suggestion": str(item.get("detail") or "在技术标资料需求与补齐清单中补充后重新生成。")[:220],
                }
            )
        pending_count = (draft.content_markdown or "").count("待确认")
        if pending_count:
            rows.append(
                {
                    "section": draft.section_title or draft.section_key or "-",
                    "issue": f"正文仍包含 {pending_count} 处“待确认”。",
                    "suggestion": "转成明确资料需求、从招标文件抽取，或人工修正文案后再定稿。",
                }
            )

    for item in missing_draft_sections:
        rows.append(
            {
                "section": str(item.get("title") or item.get("section_key") or "-"),
                "issue": "当前组成项尚未生成技术标草稿。",
                "suggestion": "先点击“一键生成技术标草案”，或定位该章节单独生成。",
            }
        )

    material_rows = (
        db.query(BidMaterialRequirement)
        .filter(
            BidMaterialRequirement.parse_run_id == run.id,
            BidMaterialRequirement.package_key == "technical",
            BidMaterialRequirement.section_key.like("technical_composition:%"),
            BidMaterialRequirement.status.in_(["missing", "submitted"]),
        )
        .order_by(BidMaterialRequirement.id.asc())
        .all()
    )
    current_requirement_ids = _current_draft_source_requirement_ids(drafts)
    if current_requirement_ids:
        material_rows = [row for row in material_rows if row.id in current_requirement_ids]
    for item in material_rows[:20]:
        if item.status == "submitted" and _requirement_submission_is_usable(db, item):
            continue
        rows.append(
            {
                "section": item.item_title or item.section_key or "-",
                "issue": f"资料需求“{item.title}”状态为“{_requirement_status_label(item.status)}”。",
                "suggestion": "上传或选择企业资料后，点击确认可用；必要时重新生成对应章节。",
            }
        )
    return _dedupe_issue_rows(rows)[:40]


def _current_draft_source_requirement_ids(drafts: list[BidDraftSection]) -> set[int]:
    ids: set[int] = set()
    for draft in drafts:
        values = loads_json(draft.source_requirement_ids_json, []) if draft.source_requirement_ids_json else []
        for value in values or []:
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return ids


def _requirement_submission_is_usable(db: Session, row: BidMaterialRequirement) -> bool:
    if str(row.submitted_value or "").strip():
        return True
    if _submitted_file_ids(row):
        return True
    item_uuids = _submitted_profile_item_uuids(row)
    if not item_uuids:
        return False
    return (
        db.query(EnterpriseProfileItem.id)
        .filter(
            EnterpriseProfileItem.item_uuid.in_(item_uuids),
            EnterpriseProfileItem.status == ENTERPRISE_PROFILE_STATUS_ACTIVE,
        )
        .first()
        is not None
    )


def _requirement_missing_covered_by_section_material(
    db: Session,
    row: BidMaterialRequirement,
    section_rows: list[BidMaterialRequirement],
) -> bool:
    section_key = str(row.section_key or "")
    title = str(row.title or "")
    if section_key.endswith("7_3_8") and any(
        token in title
        for token in ("项目经理完整简历", "技术负责人完整简历", "安全负责人完整简历", "其他主要管理人员")
    ):
        return _has_usable_personnel_bundle_material(db, section_rows)
    if section_key.endswith("7_3_18") and any(token in title for token in ("品牌", "材料")):
        return any(
            item.status in {"submitted", "approved", "applied"}
            and _requirement_submission_is_usable(db, item)
            and any(token in str(item.title or "") for token in ("品牌", "材料"))
            for item in section_rows
        )
    return False


def _dedupe_issue_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    overlap_codes = {"editorial_residue", "duplicated_function_word", "unresolved_template_token"}
    for row in rows:
        section = str(row.get("section") or "")
        code = str(row.get("code") or "")
        issue = str(row.get("issue") or "")
        key = (section, f"code:{code}" if code in overlap_codes else f"issue:{issue}")
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _append_markdown_to_doc(doc: _TenderAnalysisDocxBuilder, markdown: str, *, section_title: str) -> None:
    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    buffer: list[str] = []
    emitted = False
    first_heading_seen = False
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            _flush_paragraph_buffer(doc, buffer)
            buffer = []
            index += 1
            continue
        if line.startswith("```"):
            _flush_paragraph_buffer(doc, buffer)
            buffer = []
            index += 1
            continue
        if _is_markdown_table_line(line):
            _flush_paragraph_buffer(doc, buffer)
            buffer = []
            table_lines: list[str] = []
            while index < len(lines) and _is_markdown_table_line(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            if _append_markdown_table(doc, table_lines):
                emitted = True
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            _flush_paragraph_buffer(doc, buffer)
            buffer = []
            level = len(heading.group(1))
            title = _clean_markdown_inline(heading.group(2))
            if level == 1 and not first_heading_seen and _same_heading(title, section_title):
                first_heading_seen = True
                index += 1
                continue
            doc.add_heading(title, level=2)
            first_heading_seen = True
            emitted = True
            index += 1
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        numbered = re.match(r"^\d+[.)、]\s+(.+)$", line)
        if bullet or numbered:
            _flush_paragraph_buffer(doc, buffer)
            buffer = []
            text = _clean_markdown_inline((bullet or numbered).group(1))
            prefix = "-" if bullet else re.match(r"^(\d+[.)、])", line).group(1)
            doc.add_paragraph(f"{prefix} {text}", style="Body")
            emitted = True
            index += 1
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            _flush_paragraph_buffer(doc, buffer)
            buffer = []
            index += 1
            continue
        buffer.append(_clean_markdown_inline(line))
        index += 1

    _flush_paragraph_buffer(doc, buffer)
    if not emitted and not any(part.strip() for part in lines):
        doc.add_paragraph("本章节暂无草稿正文，需先生成或人工补充。")


def _flush_paragraph_buffer(doc: _TenderAnalysisDocxBuilder, buffer: list[str]) -> None:
    text = " ".join(part.strip() for part in buffer if part.strip()).strip()
    if text:
        doc.add_paragraph(text, style="Body")


def _is_markdown_table_line(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2


def _append_markdown_table(doc: _TenderAnalysisDocxBuilder, lines: list[str]) -> bool:
    rows = [_split_markdown_table_row(line) for line in lines]
    rows = [row for row in rows if row and not _is_table_separator_row(row)]
    if not rows:
        return False
    width_tuple = _table_widths(max(len(row) for row in rows))
    normalized_rows = [
        [_clean_markdown_inline(row[index]) if index < len(row) else "" for index in range(len(width_tuple))]
        for row in rows
    ]
    doc.add_table(normalized_rows, widths=width_tuple, header=len(normalized_rows) > 1)
    return True


def _split_markdown_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator_row(row: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in row if cell.strip())


def _table_widths(column_count: int) -> tuple[int, ...]:
    count = max(1, min(int(column_count or 1), 6))
    if count == 1:
        return (9360,)
    if count == 2:
        return (2600, 6760)
    if count == 3:
        return (900, 2500, 5960)
    if count == 4:
        return (800, 1900, 3100, 3560)
    if count == 5:
        return (700, 1500, 2200, 2500, 2460)
    return (700, 1300, 1700, 1900, 1900, 1860)


def _clean_markdown_inline(text: Any) -> str:
    value = str(text or "").strip()
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_]+)__", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    value = re.sub(r"_([^_]+)_", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", value)
    return re.sub(r"\s+", " ", value).strip()


def _same_heading(left: str, right: str) -> bool:
    left_clean = re.sub(r"\s+", "", left or "")
    right_clean = re.sub(r"\s+", "", right or "")
    return bool(left_clean and right_clean and (left_clean == right_clean or left_clean in right_clean or right_clean in left_clean))


def _draft_status_label(value: Any) -> str:
    return {
        "ready": "可复核",
        "needs_input": "需补资料",
        "blocked": "阻断",
        "draft": "草稿",
        "review_note": "复核说明",
    }.get(str(value or ""), str(value or "-"))


def _review_status_label(value: Any) -> str:
    return {
        "draft": "待复核",
        "reviewed": "已复核",
        "needs_revision": "需修改",
        "accepted": "已接受",
    }.get(str(value or ""), str(value or "-"))


def _requirement_status_label(value: Any) -> str:
    return {
        "missing": "缺失",
        "submitted": "已提交待确认",
        "approved": "确认可用",
        "applied": "已应用",
        "not_applicable": "不适用",
    }.get(str(value or ""), str(value or "-"))
