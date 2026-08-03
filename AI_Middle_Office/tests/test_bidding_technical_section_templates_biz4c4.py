from __future__ import annotations

from app.models.bidding import BidDraftSection, BidParseRun
from app.services import bidding_technical_quality, bidding_technical_word_export
from app.services.bidding_technical_review_focus import (
    BID_TECHNICAL_SECTION_REVIEW_FOCUS_REINFORCEMENT_VERSION,
    SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING,
    reinforce_technical_bid_section_review_focus,
)
from app.services.bidding_technical_section_templates import (
    BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION,
    BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION,
    BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION,
    SECTION_EXECUTION_LOOP_HEADING,
    SECTION_PLAYBOOK_REINFORCEMENT_HEADING,
    SECTION_PROJECT_FACT_REINFORCEMENT_HEADING,
    SECTION_TEMPLATE_REINFORCEMENT_HEADING,
    reinforce_technical_bid_section_discipline_playbook,
    reinforce_technical_bid_section_project_facts,
    reinforce_technical_bid_section_template_depth,
)


def test_p6_section_template_reinforcement_satisfies_formal_depth_profile():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 针对本工程的施工组织设计",
        content_markdown="# 7.3.10 针对本工程的施工组织设计\n本工程按装饰装修施工要求组织实施。",
    )
    draft.id = 96010
    component = {
        "source_item_no": "7.3.10",
        "component_title": "针对本工程的施工组织设计",
        "classification": "mixed",
    }

    result = reinforce_technical_bid_section_template_depth(
        draft=draft,
        component=component,
        content=draft.content_markdown,
        project_context={
            "work_zone_phrase": "办公区、商业街区及相关配合区域",
            "affected_zone_phrase": "办公区、商业街区及周边受影响区域",
            "schedule": {"total_duration_days": 90},
            "quality": {"goal": "合格"},
        },
    )

    assert result["version"] == BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION
    assert result["changed"] is True
    assert result["intent"] == "construction_organization"
    assert SECTION_TEMPLATE_REINFORCEMENT_HEADING in result["content"]
    assert result["paragraph_count_after"] >= bidding_technical_quality.FORMAL_INTENT_DEPTH_MINIMUMS["construction_organization"][0]

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=[draft],
        components_by_key={"technical_composition:7_3_10": component},
        final_content_by_draft_id={draft.id: result["content"]},
    )
    warning_codes = {item["code"] for item in report["warnings"]}
    assert "formal_section_depth_weak" not in warning_codes
    assert "formal_required_topic_missing" not in warning_codes
    assert "formal_execution_loop_missing" not in warning_codes


def test_p2_template_reinforcement_backfills_execution_loop_for_legacy_reinforced_content():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
        content_markdown=(
            "# 7.3.10 施工组织设计\n"
            "## 专项深化措施\n"
            "本章已围绕施工部署、组织架构与职责、施工工序与专业配合、质量安全文明与成品保护、应急与沟通机制展开说明，"
            "但历史版本尚未形成责任主体、实施流程、检查验收、整改纠偏和资料闭环的完整骨架。"
        ),
    )
    draft.id = 96011
    component = {
        "source_item_no": "7.3.10",
        "component_title": "施工组织设计",
        "classification": "mixed",
    }

    result = reinforce_technical_bid_section_template_depth(
        draft=draft,
        component=component,
        content=draft.content_markdown,
        project_context={"work_zone_phrase": "商业街区及办公区施工区域", "affected_zone_phrase": "商业街区及办公区周边区域"},
    )

    assert result["changed"] is True
    assert result["reason"] == "execution_loop_reinforced"
    assert SECTION_EXECUTION_LOOP_HEADING in result["content"]
    assert result["added_execution_loop_group_count"] >= 1
    assert "责任分工方面，项目经理统筹" in result["content"]
    assert "实施流程方面，项目部按计划编制、条件复核" in result["content"]
    assert "检查验收方面" not in result["content"]
    assert "整改纠偏方面" not in result["content"]
    assert "资料闭环方面" not in result["content"]
    assert not result["missing_execution_loop_after"]


def test_p3_project_fact_reinforcement_injects_current_project_facts_by_intent():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
        content_markdown="# 7.3.10 施工组织设计\n本章按装饰装修施工要求组织实施，重点做好流程安排和现场协调。",
    )
    draft.id = 99010
    component = {
        "source_item_no": "7.3.10",
        "component_title": "施工组织设计",
        "classification": "mixed",
    }

    result = reinforce_technical_bid_section_project_facts(
        draft=draft,
        component=component,
        content=draft.content_markdown,
        project_context={
            "work_zone_names": ["后厨区", "就餐区"],
            "work_zone_phrase": "后厨区、就餐区及相关配合区域",
            "scope": {"scope_text": "后厨区、就餐区室内装饰装修及相关机电末端配合", "work_zones": ["后厨区", "就餐区"]},
            "technical_requirements": {
                "coordination": {
                    "label": "现场协调",
                    "summary": "需与总承包单位进行工作面移交和交叉作业协调",
                    "keywords": ["总承包单位", "工作面移交", "交叉作业"],
                },
                "finished_product_protection": {
                    "label": "成品保护",
                    "summary": "应对已完工程和既有设施采取覆盖保护、包裹保护措施",
                    "keywords": ["已完工程", "既有设施", "覆盖保护"],
                },
            },
        },
    )

    assert result["version"] == BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION
    assert result["changed"] is True
    assert result["reason"] == "project_fact_reinforced"
    assert SECTION_PROJECT_FACT_REINFORCEMENT_HEADING in result["content"]
    assert "后厨区、就餐区" in result["content"]
    assert "后厨区、就餐区室内装饰装修及相关机电末端配合" in result["content"]
    assert "需与总承包单位进行工作面移交和交叉作业协调" in result["content"]
    assert "已完工程和既有设施采取覆盖保护" in result["content"]
    assert {"work_zone", "scope", "technical_requirement:coordination", "technical_requirement:finished_product_protection"} <= set(result["fact_types"])


def test_p6_quality_schedule_commitment_intent_covers_7_3_6():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_6",
        section_title="7.3.6 工程质量和工期承诺及保证措施",
        content_markdown="# 7.3.6 工程质量和工期承诺及保证措施\n我方承诺满足招标文件要求。",
    )
    draft.id = 96006
    component = {
        "source_item_no": "7.3.6",
        "component_title": "工程质量和工期承诺及保证措施",
        "classification": "mixed",
    }

    result = reinforce_technical_bid_section_template_depth(
        draft=draft,
        component=component,
        content=draft.content_markdown,
        project_context={"schedule": {"total_duration_days": 60}, "quality": {"goal": "合格"}},
    )

    assert result["changed"] is True
    assert result["intent"] == "quality_schedule_commitment"
    assert "质量承诺" in result["content"]
    assert "工期承诺" in result["content"]
    assert "检查" in result["content"]
    assert "闭环" in result["content"]


def test_p7_section_playbook_reinforcement_adds_discipline_tables():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_13",
        section_title="7.3.13 施工临时用电施工方案",
        content_markdown="# 7.3.13 施工临时用电施工方案\n临时用电按现场管理要求执行。",
    )
    draft.id = 97013
    component = {
        "source_item_no": "7.3.13",
        "component_title": "施工临时用电施工方案",
        "classification": "mixed",
    }

    result = reinforce_technical_bid_section_discipline_playbook(
        draft=draft,
        component=component,
        content=draft.content_markdown,
        project_context={"work_zone_phrase": "办公区施工区域", "affected_zone_phrase": "办公区及周边区域"},
    )

    assert result["version"] == BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION
    assert result["changed"] is True
    assert result["intent"] == "temporary_power_plan"
    assert result["added_table_count"] == 2
    assert result["control_item_count"] >= 4
    assert result["process_node_count"] >= 6
    assert SECTION_PLAYBOOK_REINFORCEMENT_HEADING in result["content"]
    assert "| 控制对象 | 工法/做法 | 检查验收 | 资料记录 |" in result["content"]
    assert "三级配电" in result["content"]
    assert "一机一闸一漏一箱" in result["content"]
    assert "停送电" in result["content"]


def test_p8_review_focus_reinforcement_adds_review_keywords_and_table():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_9",
        section_title="7.3.9 施工总进度计划",
        content_markdown="# 7.3.9 施工总进度计划\n本工程按招标文件工期要求组织施工。",
    )
    draft.id = 98009
    component = {
        "source_item_no": "7.3.9",
        "component_title": "施工总进度计划",
        "classification": "mixed",
    }

    result = reinforce_technical_bid_section_review_focus(
        draft=draft,
        component=component,
        content=draft.content_markdown,
        project_context={"schedule": {"total_duration_days": 60}, "work_zone_phrase": "办公区及商业区施工区域"},
    )

    assert result["version"] == BID_TECHNICAL_SECTION_REVIEW_FOCUS_REINFORCEMENT_VERSION
    assert result["changed"] is True
    assert result["intent"] == "schedule_plan"
    assert result["added_focus_count"] >= 4
    assert result["added_keyword_count"] >= 4
    assert result["added_table_count"] == 1
    assert SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING in result["content"]
    assert "| 评审关注点 | 关键词 | 响应措施 | 复核资料 |" in result["content"]
    assert "施工机械设备" in result["content"]
    assert "机械投入" in result["content"]
    assert "交叉作业" in result["content"]
    assert "进度纠偏" in result["content"]
    assert "过程复核资料以" in result["content"]
    assert "复核资料包括" not in result["content"]
    assert "关键词响应" not in result["content"]


def test_final_word_export_returns_p6_template_reinforcement_audit(monkeypatch):
    monkeypatch.setattr(
        bidding_technical_word_export,
        "_requirements_for_draft_attachments",
        lambda db, run, draft: [],
    )
    draft = BidDraftSection(
        section_key="technical_composition:7_3_13",
        section_title="7.3.13 施工临时用电施工方案",
        content_markdown="# 7.3.13 施工临时用电施工方案\n临时用电按现场管理要求执行。",
    )
    draft.id = 96013
    component = {
        "source_item_no": "7.3.13",
        "component_title": "施工临时用电施工方案",
        "classification": "mixed",
    }

    content, requirement_trace, template_trace, project_fact_trace, playbook_trace, review_focus_trace = bidding_technical_word_export._final_export_content_and_reinforcements_for_draft(
        None,
        BidParseRun(id=1),
        draft,
        component,
        project_context={
            "work_zone_names": ["办公区"],
            "work_zone_phrase": "办公区施工区域",
            "affected_zone_phrase": "办公区及周边区域",
            "technical_requirements": {
                "temporary_power": {
                    "label": "临时用电",
                    "summary": "临时用电应执行三级配电、漏电保护和配电箱巡检",
                    "keywords": ["三级配电", "漏电保护", "配电箱巡检"],
                },
                "coordination": {
                    "label": "现场协调",
                    "summary": "需配合总承包单位办理夜间错峰移交窗口和停送电审批",
                    "keywords": ["夜间错峰移交窗口", "停送电审批"],
                },
            },
        },
    )

    assert requirement_trace is None
    assert template_trace
    assert template_trace["version"] == BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION
    assert template_trace["changed"] is True
    assert SECTION_TEMPLATE_REINFORCEMENT_HEADING in content
    assert project_fact_trace
    assert project_fact_trace["version"] == BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION
    assert project_fact_trace["changed"] is True
    assert SECTION_PROJECT_FACT_REINFORCEMENT_HEADING in content
    assert "办公区施工区域" in content
    assert "需配合总承包单位办理夜间错峰移交窗口和停送电审批" in content
    assert playbook_trace
    assert playbook_trace["version"] == BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION
    assert playbook_trace["changed"] is True
    assert playbook_trace["added_table_count"] == 2
    assert SECTION_PLAYBOOK_REINFORCEMENT_HEADING in content
    assert review_focus_trace is None
    assert SECTION_REVIEW_FOCUS_REINFORCEMENT_HEADING not in content
    assert "三级配电" in content
    assert "一机一闸一漏一箱" in content
    assert "巡检" in content

    quality_report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="办公区装修项目",
        project_context={
            "work_zone_names": ["办公区"],
            "technical_requirements": {
                "temporary_power": {
                    "label": "临时用电",
                    "summary": "临时用电应执行三级配电、漏电保护和配电箱巡检",
                    "keywords": ["三级配电", "漏电保护", "配电箱巡检"],
                },
                "coordination": {
                    "label": "现场协调",
                    "summary": "需配合总承包单位办理夜间错峰移交窗口和停送电审批",
                    "keywords": ["夜间错峰移交窗口", "停送电审批"],
                },
            },
        },
        project_facts={},
        drafts=[draft],
        components_by_key={"technical_composition:7_3_13": component},
        final_content_by_draft_id={draft.id: content},
    )
    assert quality_report["project_fact_coverage"]["status"] == "pass"
    warning_codes = {item["code"] for item in quality_report["warnings"]}
    assert "work_zone_fact_not_used" not in warning_codes
    assert "technical_requirement_fact_not_used" not in warning_codes
    profile_check_by_code = {item["code"]: item for item in quality_report["formal_profile"]["checks"]}
    assert profile_check_by_code["project_fact_coverage"]["status"] == "pass"

    template_audit = bidding_technical_word_export._build_section_template_reinforcement_audit_report(
        [{**template_trace, "section_no": "7.3.13", "section_title": draft.section_title, "section_key": draft.section_key}]
    )
    assert template_audit["version"] == BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION
    assert template_audit["status"] == "applied"
    assert template_audit["reinforced_section_count"] == 1
    assert template_audit["section_reports"][0]["section_no"] == "7.3.13"

    project_fact_audit = bidding_technical_word_export._build_section_project_fact_reinforcement_audit_report(
        [{**project_fact_trace, "section_no": "7.3.13", "section_title": draft.section_title, "section_key": draft.section_key}]
    )
    assert project_fact_audit["version"] == BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION
    assert project_fact_audit["status"] == "applied"
    assert project_fact_audit["reinforced_section_count"] == 1
    assert "technical_requirement:coordination" in project_fact_audit["fact_types"]

    playbook_audit = bidding_technical_word_export._build_section_playbook_reinforcement_audit_report(
        [{**playbook_trace, "section_no": "7.3.13", "section_title": draft.section_title, "section_key": draft.section_key}]
    )
    assert playbook_audit["version"] == BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION
    assert playbook_audit["status"] == "applied"
    assert playbook_audit["reinforced_section_count"] == 1
    assert playbook_audit["added_table_count"] == 2
    assert playbook_audit["section_reports"][0]["control_item_count"] >= 4
