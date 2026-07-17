from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from app.models.bidding import BidDraftSection
from app.services import bidding_draft_sections, bidding_technical_quality, bidding_technical_word_export
from app.services.bidding_technical_section_templates import (
    reinforce_technical_bid_section_discipline_playbook,
    reinforce_technical_bid_section_template_depth,
)
from app.services.bidding_tender_analysis import _TenderAnalysisDocxBuilder


def _draft(index: int, content: str) -> BidDraftSection:
    row = BidDraftSection(
        section_key=f"technical_composition:custom_{index}",
        section_title=f"测试章节{index}",
        content_markdown=content,
    )
    row.id = index
    return row


def test_formal_quality_gate_blocks_text_residue_and_returns_snippets():
    draft = _draft(
        1,
        "# 测试章节\n本应原文直接用于投标。层办公区按计划施工，并满足招标文件的的要求。变量{{project.zone}}尚未解析。",
    )

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=[draft],
        components_by_key={},
        final_content_by_draft_id={draft.id: draft.content_markdown},
    )

    blocker_codes = {item["code"] for item in report["blockers"]}
    assert report["status"] == "blocked"
    assert report["text_hygiene"]["status"] == "blocked"
    assert {
        "editorial_residue",
        "dangling_project_scope",
        "duplicated_function_word",
        "unresolved_template_token",
    } <= blocker_codes
    assert all(item.get("evidence", {}).get("snippets") for item in report["text_hygiene"]["findings"])


def test_formal_quality_gate_blocks_high_frequency_boilerplate_across_sections():
    repeated = "所产生的各项费用均已包括于投标金额中。"
    drafts = [
        _draft(index, f"# 测试章节{index}\n本章执行专业施工控制。{repeated}")
        for index in range(1, 9)
    ]

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=drafts,
        components_by_key={},
        final_content_by_draft_id={row.id: row.content_markdown for row in drafts},
    )

    repetition = report["template_repetition"]
    assert repetition["status"] == "blocked"
    cluster = next(item for item in repetition["clusters"] if item["code"] == "high_frequency_boilerplate")
    assert cluster["occurrence_count"] == 8
    assert cluster["section_count"] == 8
    assert "high_frequency_boilerplate" in {item["code"] for item in report["blockers"]}


def test_formal_quality_gate_warns_for_moderate_cross_section_repetition():
    repeated = "项目部按周检查施工记录并形成整改闭环。"
    drafts = [_draft(index, f"# 测试章节{index}\n{repeated}") for index in range(1, 4)]

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=drafts,
        components_by_key={},
        final_content_by_draft_id={row.id: row.content_markdown for row in drafts},
    )

    assert report["template_repetition"]["status"] == "warning"
    assert "repeated_bid_sentence" in {item["code"] for item in report["warnings"]}


def test_composition_prompt_context_carries_previous_section_signatures():
    drafts = [
        _draft(
            1,
            "# 施工组织设计\n## 施工部署\n项目部按区域组织流水施工。\n## 检查机制\n每周复盘问题并完成销项。",
        )
    ]

    context = bidding_draft_sections._technical_composition_anti_repetition_context(drafts)

    assert context["previous_section_count"] == 1
    assert context["section_signatures"][0]["section_title"] == "测试章节1"
    assert "所产生的各项费用均已包括于投标金额中" in context["avoid_exact_phrases"]
    assert any("项目部按区域组织流水施工" in item for item in context["avoid_exact_phrases"])


def test_work_zone_extraction_rejects_contract_clauses_and_recovers_project_name():
    scope_zones = bidding_draft_sections._extract_work_zones_from_scope_text(
        "商业街区、6#楼32F办公区、分段不连续开工、所产生的各项费用均已包括于投标金额中"
    )
    project_zones = bidding_draft_sections._work_zone_names_from_project_name(
        "东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程"
    )

    assert scope_zones == ["商业街区", "6#楼32F办公区"]
    assert project_zones == ["商业街区", "6#楼32F办公区"]
    assert bidding_draft_sections._normalize_work_zone_name("层办公区") == ""


def test_docx_builder_applies_pagination_table_and_toc_controls():
    doc = _TenderAnalysisDocxBuilder()
    doc.add_toc_field(levels="1-2", fallback_lines=["第一章 测试"])
    doc.add_heading("第一章 测试")
    doc.add_paragraph("正文内容。")
    doc.add_heading("第二章 测试", page_break_before=True)
    doc.add_table(
        [["序号", "内容"], ["1", "跨页表格内容"]],
        widths=(1200, 8160),
        header=True,
    )

    with ZipFile(BytesIO(doc.to_bytes())) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        settings_xml = archive.read("word/settings.xml").decode("utf-8")
        rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8")

    assert '<w:updateFields w:val="true"/>' in settings_xml
    assert "relationships/styles" in rels_xml
    assert '<w:fldChar w:fldCharType="begin" w:dirty="true"/>' in document_xml
    assert "<w:keepNext/><w:keepLines/>" in document_xml
    assert "<w:pageBreakBefore/>" in document_xml
    assert "<w:tblHeader/>" in document_xml
    assert document_xml.count("<w:cantSplit/>") == 2


def test_blocking_issue_rows_include_evidence_and_dedupe_overlapping_scanners():
    report = {
        "blockers": [
            {
                "code": "editorial_residue",
                "section": "7.3.14 主要材料采购计划",
                "issue": "正文包含编辑提示残留。（2处）",
                "suggestion": "删除编辑提示。",
                "evidence": {"snippets": ["供应保障、本应原文、依据施工进度确定订货时间"]},
            },
            {
                "code": "high_frequency_boilerplate",
                "section": "全文",
                "issue": "同一完整句在全文重复12次。",
                "suggestion": "按章节改写。",
                "evidence": {
                    "sample": "工期节点按招标文件要求倒排。",
                    "sections": ["7.3.6 工期承诺", "7.3.9 进度计划"],
                },
            },
            {
                "code": "high_frequency_boilerplate",
                "section": "全文",
                "issue": "同一完整句在全文重复12次。",
                "suggestion": "按章节改写。",
                "evidence": {
                    "sample": "质量目标按招标文件要求落实。",
                    "sections": ["7.3.6 工期承诺", "7.3.16 质量保障"],
                },
            },
        ]
    }

    quality_rows = bidding_technical_quality.quality_report_blocking_issue_rows(report)
    rows = bidding_technical_word_export._dedupe_issue_rows(
        quality_rows
        + [
            {
                "code": "editorial_residue",
                "section": "7.3.14 主要材料采购计划",
                "issue": "正文仍包含编辑过程或改写提示。（2 处）",
                "suggestion": "删除编辑说明。",
            }
        ]
    )

    assert len(rows) == 3
    assert "命中片段" in rows[0]["issue"]
    assert "工期节点按招标文件要求倒排" in rows[1]["issue"]
    assert "质量目标按招标文件要求落实" in rows[2]["issue"]
    assert "7.3.6、7.3.9" in rows[1]["issue"]


def test_formal_export_safely_removes_editorial_marker_used_as_list_token():
    updated = bidding_technical_word_export._formalize_final_export_text(
        "采购时间表、供应保障、本应原文、依据施工进度确定订货时间。",
        section_no="7.3.14",
    )

    assert "本应原文" not in updated
    assert "供应保障、依据施工进度" in updated
    assert not bidding_technical_word_export._formal_export_blocking_findings(updated)
    assert bidding_technical_word_export._formal_export_blocking_findings("原文应为正式版本。")


def test_section_reinforcement_keeps_project_facts_in_intent_specific_layer_and_avoids_exact_repeats():
    context = {
        "work_zone_phrase": "商业街区、6#楼32F办公区及相关配合区域",
        "affected_zone_phrase": "商业街区、6#楼32F办公区及周边受影响区域",
        "schedule": {"sentence": "商业街区合同工期45天，办公区合同工期60天"},
        "quality": {"goal": "广东省优质工程奖项目"},
    }
    outputs = []
    for index, title in enumerate(("临时用电方案甲", "临时用电方案乙"), start=1):
        draft = BidDraftSection(
            section_key="technical_composition:7_3_13",
            section_title=f"7.3.13 {title}",
            content_markdown=f"# {title}\n临时用电按审批方案实施。",
        )
        draft.id = 2000 + index
        component = {
            "source_item_no": "7.3.13",
            "component_title": title,
            "classification": "mixed",
        }
        template = reinforce_technical_bid_section_template_depth(
            draft=draft,
            component=component,
            content=draft.content_markdown,
            project_context=context,
        )
        assert "工期组织方面" not in template["content"]
        assert "质量控制方面" not in template["content"]
        playbook = reinforce_technical_bid_section_discipline_playbook(
            draft=draft,
            component=component,
            content=template["content"],
            project_context=context,
        )
        outputs.append(
            {
                "section": title,
                "section_no": "7.3.13",
                "content": playbook["content"],
            }
        )

    repetition = bidding_technical_quality._build_template_repetition_report(outputs)
    assert repetition["status"] == "pass"
    assert repetition["clusters"] == []
