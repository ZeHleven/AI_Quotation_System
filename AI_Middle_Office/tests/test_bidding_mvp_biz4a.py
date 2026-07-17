from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile
from xml.sax.saxutils import escape

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.bidding import (
    BidFileFormatPlan,
    BidFileFormatPlanEvent,
    BidDraftSection,
    BidDraftSectionVersion,
    BidParseRun,
    BidProject,
    BidProjectFile,
    TenderBusinessObject,
    TenderRequirement,
    TenderResponseItem,
    TenderRisk,
)
from app.models.user import User, UserRole
from app.services.bidding_business_objects import build_business_object_summary, build_tender_business_objects
from app.services.bidding_parser import analyze_tender_segments, dumps_json, extract_tender_text, loads_json
from app.services.bidding_tender_analysis import (
    TENDER_ANALYSIS_PREVIEW_VERSION,
    TENDER_ANALYSIS_SCHEMA_VERSION,
    build_tender_analysis_export_document,
    build_tender_analysis_preview,
    build_tender_analysis_preview_with_semantic_summary,
    get_tender_analysis_export_fields,
    get_tender_analysis_schema,
)


PASSWORD = "secret123"


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _docx_bytes_with_table(paragraphs: list[str], table_rows: list[list[str]]) -> bytes:
    paragraph_body = "".join(
        f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )

    def cell_xml(value: str) -> str:
        if value == "__CHECKED_FIXED_UNIT_PRICE__":
            return (
                '<w:tc><w:p>'
                '<w:r><w:sym w:font="Wingdings 2" w:char="0052"/></w:r>'
                "<w:r><w:t>固定单价包干 </w:t></w:r>"
                '<w:r><w:sym w:font="Wingdings 2" w:char="00A3"/></w:r>'
                "<w:r><w:t>固定总价包干</w:t></w:r>"
                "</w:p></w:tc>"
            )
        return f"<w:tc><w:p><w:r><w:t>{escape(value)}</w:t></w:r></w:p></w:tc>"

    table_body = "<w:tbl>" + "".join(
        "<w:tr>" + "".join(cell_xml(cell) for cell in row) + "</w:tr>"
        for row in table_rows
    ) + "</w:tbl>"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraph_body}{table_body}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _docx_document_xml(docx_bytes: bytes) -> str:
    with ZipFile(BytesIO(docx_bytes)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def test_bidding_parser_docx_extracts_ordered_table_rows_and_symbols():
    document_bytes = _docx_bytes_with_table(
        ["投标人须知前附表"],
        [
            ["项号", "条款名称", "说明与要求"],
            ["1", "招标工程名称", "深圳龙岗区丰隆深港科技园项目三期品质效果提升工程（一标段）"],
            ["2", "工程地点", "深圳市龙岗区"],
            ["3", "工程承包方式", "__CHECKED_FIXED_UNIT_PRICE__"],
            ["4", "投标保证金", "人民币50000元，2026年7月8日17:00前以银行转账形式缴纳。"],
            ["5", "投标文件送交截止日期", "2026年7月9日10:00，地点：招标人会议室。"],
            ["6", "合同价款的支付", "进度款支付至已完产值80%，结算后支付至97%，质保金3%。"],
        ],
    )

    extracted = extract_tender_text(
        document_bytes,
        "招标文件.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert "[DOCX第1段] 投标人须知前附表" in extracted["text"]
    assert "DOCX表1第4行" in extracted["text"]
    assert "☑固定单价包干" in extracted["text"]
    assert "☐固定总价包干" in extracted["text"]
    assert any(
        segment["source_location"] == "DOCX表1第5行" and "投标保证金" in segment["text"]
        for segment in extracted["segments"]
    )
    assert any("|" in segment["text"] for segment in extracted["segments"])


def test_tender_analysis_summary_falls_back_to_raw_docx_table_segments(client):
    document_bytes = _docx_bytes_with_table(
        ["投标人须知前附表"],
        [
            ["项号", "条款名称", "说明与要求"],
            ["1", "招标工程名称", "深圳龙岗区丰隆深港科技园项目三期品质效果提升工程（一标段）"],
            ["2", "工程地点", "深圳市龙岗区"],
            ["3", "招标范围", "三期园区展示区、公区及样板段品质效果提升工程。"],
            ["4", "工程承包方式", "__CHECKED_FIXED_UNIT_PRICE__"],
            ["5", "答疑截止时间", "2026年7月5日17:00，答疑联系人：李工 13900000000。"],
            ["6", "现场踏勘", "自行踏勘，地点为项目现场，踏勘现场联系人：赵工 13700000000。"],
            ["7", "投标保证金", "人民币50000元，2026年7月8日17:00前以银行转账形式缴纳。"],
            ["8", "投标文件要求", "商务标正本1份、副本2份；技术标正本1份、副本2份；均需签字盖章。"],
            ["9", "投标文件的密封和标记", "商务标、技术标分别密封，封口处加盖投标人公章并注明工程名称。"],
            ["10", "投标文件送交截止日期", "2026年7月9日10:00，地点：招标人会议室。"],
            ["11", "评标标准", "综合评定最优；废标条件包括未按要求密封、逾期送达；确定中标人为综合排名第一。"],
            ["12", "合同工期", "合同工期45日历天，实际开工日期以甲方开工令为准。"],
            ["13", "合同价款的支付", "进度款支付至已完产值80%，结算后支付至97%，质保金3%。"],
        ],
    )
    extracted = extract_tender_text(
        document_bytes,
        "招标文件.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    db = SessionLocal()
    user = _create_user("staff")
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="丰隆深港科技园品质提升工程",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        file_uuid = str(uuid.uuid4())
        db.add(
            BidProjectFile(
                file_uuid=file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=len(document_bytes),
                sha256=extracted["sha256"],
                parser_status="parsed",
                parser_version=extracted["parser_version"],
                extracted_text=extracted["text"],
                segments_json=dumps_json(extracted["segments"]),
                page_count=0,
                section_count=len(extracted["segments"]),
                uploaded_by=user.id,
            )
        )
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version=extracted["parser_version"],
            input_file_ids_json=dumps_json([file_uuid]),
            summary_json=dumps_json({"document_structure": {"segment_by_section": {"bid_instructions": 12}}}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.commit()

        preview = build_tender_analysis_preview(db, project, run)
    finally:
        db.close()

    summary_by_key = {item["item_key"]: item for item in preview["tables"]["summary"]["items"]}
    assert preview["source_counts"]["summary_segment_source_count"] >= 10
    assert "深圳龙岗区丰隆深港科技园" in summary_by_key["project_overview"]["extracted_value"]
    assert "深圳市龙岗区" in summary_by_key["project_overview"]["extracted_value"]
    assert "固定单价包干" in summary_by_key["pricing_method"]["extracted_value"]
    assert "50000元" in summary_by_key["bid_bond"]["extracted_value"]
    assert "李工" in summary_by_key["qa_deadline"]["extracted_value"]
    assert "赵工" in summary_by_key["site_visit"]["extracted_value"]
    assert "正本1份" in summary_by_key["bid_document_requirements"]["extracted_value"]
    assert "分别密封" in summary_by_key["sealing_requirements"]["extracted_value"]
    assert "2026年7月9日10:00" in summary_by_key["submission_deadline"]["normalized_value"]
    assert "废标条件" in summary_by_key["scoring_weight"]["extracted_value"]
    assert "45日历天" == summary_by_key["construction_period"]["normalized_value"]
    assert "支付至已完产值80%" in summary_by_key["payment_terms"]["extracted_value"]
    assert summary_by_key["pricing_method"]["source_kind"] == "file_segment"


def test_tender_analysis_summary_falls_back_when_semantic_marks_not_found(client):
    user = _create_user("staff")
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="语义兜底测试项目",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        file_uuid = str(uuid.uuid4())
        segments = [
            {
                "source_file": "招标文件.pdf",
                "source_location": "第30页 第1段",
                "document_section": "contract_terms",
                "text": "合同价款的支付：本工程无预付款；工程进度款每月支付一次，每月25日前申报，审核确认后15个工作日内按审核值的80%支付，结算后支付至97%，余3%为质保金。",
            }
        ]
        db.add(
            BidProjectFile(
                file_uuid=file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                sha256="2" * 64,
                parser_status="parsed",
                parser_version="biz4a-rule-v1",
                extracted_text=segments[0]["text"],
                segments_json=dumps_json(segments),
                page_count=30,
                section_count=1,
                uploaded_by=user.id,
            )
        )
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="biz4a-rule-v1",
            input_file_ids_json=dumps_json([file_uuid]),
            summary_json=dumps_json({"document_structure": {"segment_by_section": {"contract_terms": 1}}}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.commit()

        preview = build_tender_analysis_preview(
            db,
            project,
            run,
            summary_semantic_items={
                "payment_terms": {
                    "item_key": "payment_terms",
                    "status": "not_found",
                    "extracted_value": "",
                    "normalized_value": "",
                    "confidence": 0.2,
                    "reason": "候选片段不足，未找到付款方式。",
                }
            },
        )
    finally:
        db.close()

    payment = {item["item_key"]: item for item in preview["tables"]["summary"]["items"]}["payment_terms"]
    assert payment["extraction_method"] == "rule_after_semantic_not_found"
    assert "无预付款" in payment["extracted_value"]
    assert "支付至97%" in payment["extracted_value"]
    assert "语义摘要未找到明确结果" in payment["review_note"]


def test_tender_analysis_summary_rule_fallback_rejects_keyword_only_mismatches(client):
    user = _create_user("staff")
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="关键词误命中测试项目",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        file_uuid = str(uuid.uuid4())
        segments = [
            {
                "source_file": "招标文件.pdf",
                "source_location": "第10页",
                "document_section": "contract_terms",
                "text": "支持性表格包括付款申请报告、进度款封面、预算清单、资金计划等格式文件。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "第12页",
                "document_section": "bid_instructions",
                "text": "废标条件：未按要求密封或逾期送达的，视为无效标。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "第13页",
                "document_section": "clarification",
                "text": "招标人在投标截止日期前3日前以书面形式发出澄清纪要。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "第14页",
                "document_section": "bid_format",
                "text": "投标文件格式中联系人字段由投标人填写，不作为答疑联系人。",
            },
        ]
        db.add(
            BidProjectFile(
                file_uuid=file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.pdf",
                content_type="application/pdf",
                size_bytes=2048,
                sha256="3" * 64,
                parser_status="parsed",
                parser_version="biz4a-rule-v1",
                extracted_text="\n".join(item["text"] for item in segments),
                segments_json=dumps_json(segments),
                page_count=14,
                section_count=len(segments),
                uploaded_by=user.id,
            )
        )
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="biz4a-rule-v1",
            input_file_ids_json=dumps_json([file_uuid]),
            summary_json=dumps_json({"document_structure": {"segment_by_section": {"bid_instructions": 2}}}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.commit()

        preview = build_tender_analysis_preview(db, project, run)
    finally:
        db.close()

    summary_by_key = {item["item_key"]: item for item in preview["tables"]["summary"]["items"]}
    assert summary_by_key["payment_terms"]["extracted_value"] == ""
    assert summary_by_key["sealing_requirements"]["extracted_value"] == ""
    assert summary_by_key["submission_deadline"]["extracted_value"] == ""
    assert summary_by_key["contact_person"]["extracted_value"] == ""
    assert "废标条件" in summary_by_key["scoring_weight"]["extracted_value"]


def test_tender_analysis_project_overview_prefers_substantive_scope_over_placeholder(client):
    user = _create_user("staff")
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="东莞香港中心项目装修工程",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        file_uuid = str(uuid.uuid4())
        segments = [
            {
                "source_file": "招标文件.pdf",
                "source_location": "前附表第1页",
                "document_section": "bid_instructions",
                "text": "工程规模及特征：详见《协议书》，具体信息未提供。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "前附表第1页",
                "document_section": "bid_instructions",
                "text": "招标范围：详见《协议书》及《技术要求》，具体信息未提供。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "协议书第1页",
                "document_section": "contract_terms",
                "text": "招标单位：东莞市港心房地产开发有限公司；总承包单位：中建八局湾区建设发展有限公司。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "协议书第1页",
                "document_section": "contract_terms",
                "text": "工程规模及特征：商业街区约1000平方米；6#楼32层办公区约1700平方米。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "技术要求第2页",
                "document_section": "scope_boundary",
                "text": "招标范围：商业街区包含公共走道、外摆区、外摆商铺、卫生间、休息区、造型踏步等天花、地面、墙面装饰，机电及给排水、白蚁防治、精保洁、卫生间防水、环境治理等，不含空调工程及消防改造。",
            },
            {
                "source_file": "招标文件.pdf",
                "source_location": "技术要求第3页",
                "document_section": "scope_boundary",
                "text": "6#楼32F办公区包含电梯厅、办公区、公共走道、会议室、独立办公室、水吧区、咖啡吧、接待室、前台、多功能区、储藏间等装饰，墙体隔断、推拉门、卷式窗帘、机电及给排水、白蚁防治、精保洁、环境治理等，不含空调工程、消防改造、卫生间装饰、办公家具及软装。",
            },
        ]
        db.add(
            BidProjectFile(
                file_uuid=file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.pdf",
                content_type="application/pdf",
                size_bytes=4096,
                sha256="4" * 64,
                parser_status="parsed",
                parser_version="biz4a-rule-v1",
                extracted_text="\n".join(item["text"] for item in segments),
                segments_json=dumps_json(segments),
                page_count=3,
                section_count=len(segments),
                uploaded_by=user.id,
            )
        )
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="biz4a-rule-v1",
            input_file_ids_json=dumps_json([file_uuid]),
            summary_json=dumps_json({"document_structure": {"segment_by_section": {"scope_boundary": 2}}}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.commit()

        preview = build_tender_analysis_preview(db, project, run)
    finally:
        db.close()

    overview = {item["item_key"]: item for item in preview["tables"]["summary"]["items"]}["project_overview"]
    assert "东莞市港心房地产开发有限公司" in overview["extracted_value"]
    assert "中建八局湾区建设发展有限公司" in overview["extracted_value"]
    assert "约1000平方米" in overview["extracted_value"]
    assert "约1700平方米" in overview["extracted_value"]
    assert "公共走道" in overview["extracted_value"]
    assert "电梯厅" in overview["extracted_value"]
    assert "具体信息未提供" not in overview["extracted_value"]


def test_tender_analysis_docx_export_prefers_detailed_extracted_summary_values():
    preview = {
        "project_name": "东莞香港中心测试项目",
        "generated_at": "2026-07-02T13:29:06",
        "run_uuid": "run-test",
        "review_queue": [],
        "tables": {
            "summary": {
                "items": [
                    {
                        "item_key": "bid_bond",
                        "table_key": "summary",
                        "item_name": "保证金",
                        "extracted_value": "投标担保金额：RMB 200,000.00元；缴交时间：递交投标文件的同时提交；形式：现金、支票或银行投标保函；收款账户：东莞银行莞城支行，账号529000013326177；未提交后果：无效标处理。",
                        "normalized_value": "RMB 200,000.00元",
                        "source_file": "招标文件.pdf",
                        "source_location": "第8页",
                        "is_required": False,
                        "review_status": "pending",
                    },
                    {
                        "item_key": "payment_terms",
                        "table_key": "summary",
                        "item_name": "付款方式",
                        "extracted_value": "预付款：本工程无预付款；进度款：按审核值80%支付；结算款：支付至结算总价97%；余3%为质保金。",
                        "normalized_value": "",
                        "source_file": "招标文件.pdf",
                        "source_location": "第30页",
                        "is_required": False,
                        "review_status": "pending",
                    },
                ]
            },
            "scoring": {"items": []},
            "risk_clause": {"items": []},
        },
    }

    document_xml = _docx_document_xml(build_tender_analysis_export_document(preview))

    assert "RMB 200,000.00元" in document_xml
    assert "递交投标文件的同时提交" in document_xml
    assert "529000013326177" in document_xml
    assert "无效标处理" in document_xml
    assert "本工程无预付款" in document_xml
    assert "结算总价97%" in document_xml


def test_tender_analysis_docx_export_optimizes_readability_instead_of_dumping_raw_fragments():
    preview = {
        "project_name": "导出可读性测试项目",
        "generated_at": "2026-07-02T14:20:00",
        "run_uuid": "run-readable",
        "review_queue": [],
        "tables": {
            "summary": {
                "items": [
                    {
                        "item_key": "payment_terms",
                        "table_key": "summary",
                        "item_name": "付款方式",
                        "extracted_value": "三、术语和定义 工程进度款：指各承建商按照合同条款，按月或按进度形象节点对已完工程进行的工程进度申报款。工程结算款：指承建商承建的工程内容通过竣工验收后进行结算申报的工程剩余款项。资金计划：指承建商每月提交的资金申报资料。",
                        "normalized_value": "",
                        "source_file": "招标文件.pdf",
                        "source_location": "App4/1",
                        "is_required": False,
                        "review_status": "pending",
                    }
                ]
            },
            "scoring": {"items": []},
            "risk_clause": {
                "items": [
                    {
                        "row_key": "risk:advance",
                        "risk_title": "垫资/无预付款风险",
                        "risk_category": "advance_funding",
                        "risk_level": "high",
                        "clause_text": "三、术语和定义 工程进度款：指各承建商按照合同条款进行申报；1、本工程无预付款；",
                        "risk_description": "无预付款导致垫资压力。",
                        "suggested_response": "测算垫资周期和资金占用。",
                        "affects_pricing": True,
                        "need_clarification": False,
                        "review_status": "pending",
                    }
                ]
            },
        },
    }

    document_xml = _docx_document_xml(build_tender_analysis_export_document(preview))

    assert "未识别到明确付款比例" in document_xml
    assert "本工程无预付款" in document_xml
    assert "指各承建商按照合同条款" not in document_xml
    assert "资金申报资料" not in document_xml


def test_tender_analysis_docx_export_prefers_llm_important_info_sections():
    long_rejection_clause = "废标条件：" + "未按要求密封；" * 260 + "长条款尾部保留标记"
    preview = {
        "project_name": "东莞香港中心测试项目",
        "important_info": {
            "status": "completed",
            "metadata": {"status": "completed"},
            "sections": [
                {
                    "section_key": "project_overview",
                    "items": [
                        {
                            "field_key": "tender_project_name",
                            "field_name": "招标工程名称",
                            "status": "found",
                            "value": "东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程",
                            "source_evidence_ids": ["D001"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.96,
                        },
                        {
                            "field_key": "tenderer",
                            "field_name": "招标单位",
                            "status": "found",
                            "value": "东莞市港心房地产开发有限公司",
                            "source_evidence_ids": ["D002"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.96,
                        },
                    ],
                },
                {
                    "section_key": "payment_terms",
                    "items": [
                        {
                            "field_key": "advance_payment",
                            "field_name": "预付款",
                            "status": "found",
                            "value": "本工程无预付款。",
                            "source_evidence_ids": ["D030"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.95,
                        },
                        {
                            "field_key": "progress_payment",
                            "field_name": "进度款支付",
                            "status": "found",
                            "value": "甲方审核确认后15个工作日内，按审核值的80%支付。",
                            "source_evidence_ids": ["D030"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.95,
                        },
                        {
                            "field_key": "settlement_payment",
                            "field_name": "结算款",
                            "status": "found",
                            "value": "结算完成后支付至结算总价97%，余3%为质保金。",
                            "source_evidence_ids": ["D030"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.95,
                        },
                    ],
                },
                {
                    "section_key": "evaluation_rejection",
                    "items": [
                        {
                            "field_key": "bid_rejection_conditions",
                            "field_name": "废标条件",
                            "status": "found",
                            "value": long_rejection_clause,
                            "source_evidence_ids": ["D050"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.95,
                        }
                    ],
                },
                {
                    "section_key": "pre_bid_clarifications",
                    "items": [
                        {
                            "field_key": "priority_clarifications",
                            "field_name": "回标前优先澄清清单",
                            "status": "unclear",
                            "value": "截标时间前附表年月日时为空白，需以招标邀请函或补遗确认为准。",
                            "source_evidence_ids": ["D008"],
                            "source_file": "招标文件.pdf",
                            "confidence": 0.9,
                        }
                    ],
                },
            ],
            "priority_clarifications": [
                {
                    "item": "截标时间空白",
                    "reason": "前附表仅写2026年 月 日 时，具体月、日、时未填写。",
                    "source_evidence_ids": ["D008"],
                }
            ],
        },
        "tables": {
            "summary": {
                "items": [
                    {
                        "item_key": "payment_terms",
                        "extracted_value": "三、术语和定义 工程进度款：指各承建商按照合同条款进行申报；资金计划：指承建商每月提交的资金申报资料。",
                    }
                ]
            },
            "scoring": {"items": []},
            "risk_clause": {"items": []},
        },
    }

    document_xml = _docx_document_xml(build_tender_analysis_export_document(preview))

    assert "LLM依据招标文件原文证据整理" in document_xml
    assert "东莞市港心房地产开发有限公司" in document_xml
    assert "本工程无预付款" in document_xml
    assert "按审核值的80%支付" in document_xml
    assert "结算总价97%" in document_xml
    assert "长条款尾部保留标记" in document_xml
    assert "截标时间空白" in document_xml
    assert "指各承建商按照合同条款" not in document_xml
    assert "资金申报资料" not in document_xml


def test_tender_analysis_preview_builds_llm_important_info_from_document_chunks(client, monkeypatch):
    user = _create_user("staff")
    db = SessionLocal()
    captured_context: dict[str, Any] = {}
    old_llm = _set_flag("feature_bidding_llm_review", True)
    old_key = settings.deepseek_api_key
    object.__setattr__(settings, "deepseek_api_key", "test-key")
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="LLM重建流程测试项目",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        file_uuid = str(uuid.uuid4())
        segments = [
            {
                "source_file": "招标文件.pdf",
                "source_location": "第30页",
                "document_section": "contract_terms",
                "text": "合同价款的支付：本工程无预付款；工程进度款每月支付一次，每月25日前申报，审核确认后15个工作日内按审核值的80%支付。",
            }
        ]
        segments.extend(
            {
                "source_file": "招标文件.pdf",
                "source_location": f"第{index}页",
                "document_section": "bid_instructions",
                "text": f"普通条款片段{index}，用于确认系统不会只抽样前面片段。",
            }
            for index in range(2, 350)
        )
        segments.append(
            {
                "source_file": "招标文件.pdf",
                "source_location": "第350页",
                "document_section": "bid_instructions",
                "text": "投标担保金额为人民币200000元，须在投标截止时间前缴交，形式包括现金、支票、银行汇款、银行保函。",
            }
        )
        db.add(
            BidProjectFile(
                file_uuid=file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                sha256="5" * 64,
                parser_status="parsed",
                parser_version="biz4a-rule-v1",
                extracted_text="\n\n".join(segment["text"] for segment in segments),
                segments_json=dumps_json(segments),
                page_count=30,
                section_count=len(segments),
                uploaded_by=user.id,
            )
        )
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="biz4a-rule-v1",
            input_file_ids_json=dumps_json([file_uuid]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.commit()

        async def fake_post_json_via_gateway(**kwargs):
            nonlocal captured_context
            assert kwargs["endpoint_type"] == "bidding_tender_important_info_extract"
            user_content = kwargs["json_payload"]["messages"][1]["content"]
            captured_context = json.loads(user_content)
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "sections": [
                                            {
                                                "section_key": "payment_terms",
                                                "items": [
                                                    {
                                                        "field_key": "advance_payment",
                                                        "field_name": "预付款",
                                                        "status": "found",
                                                        "value": "预付款：本工程无预付款。",
                                                        "source_evidence_ids": ["D001"],
                                                        "confidence": 0.94,
                                                    },
                                                    {
                                                        "field_key": "progress_payment",
                                                        "field_name": "进度款支付",
                                                        "status": "found",
                                                        "value": "进度款每月支付一次，审核确认后15个工作日内按审核值的80%支付。",
                                                        "source_evidence_ids": ["D001"],
                                                        "confidence": 0.94,
                                                    },
                                                ],
                                            }
                                        ],
                                        "priority_clarifications": [],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )

        monkeypatch.setattr(
            "app.services.bidding_tender_analysis.post_json_via_gateway",
            fake_post_json_via_gateway,
        )

        preview = asyncio.run(
            build_tender_analysis_preview_with_semantic_summary(
                db,
                project,
                run,
                username=user.username,
                trace_id=run.run_uuid,
            )
        )
    finally:
        db.close()
        _set_flag("feature_bidding_llm_review", old_llm)
        object.__setattr__(settings, "deepseek_api_key", old_key)

    assert captured_context["task"] == "bidding_tender_important_information_extraction"
    assert "candidate_snippets" not in captured_context
    assert captured_context["context_truncated"] is False
    assert len(captured_context["document_chunks"]) == 350
    assert captured_context["document_chunks"][0]["evidence_id"] == "D001"
    assert "合同价款的支付" in captured_context["document_chunks"][0]["text"]
    assert captured_context["document_chunks"][-1]["evidence_id"] == "D350"
    assert "投标担保金额为人民币200000元" in captured_context["document_chunks"][-1]["text"]
    assert any(section["section_key"] == "payment_terms" for section in captured_context["output_schema"])
    assert preview["important_info"]["status"] == "completed"
    payment_section = next(section for section in preview["important_info"]["sections"] if section["section_key"] == "payment_terms")
    payment_by_key = {item["field_key"]: item for item in payment_section["items"]}
    assert payment_by_key["advance_payment"]["value"] == "预付款：本工程无预付款"
    assert "按审核值的80%支付" in payment_by_key["progress_payment"]["value"]


def _create_user(role: str = "staff") -> User:
    username = f"biz4a_{role}_{uuid.uuid4().hex[:10]}"
    legacy_role = "admin" if role in {"admin", "system_admin"} else "user"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(PASSWORD),
            role=legacy_role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz4a test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _headers(client, role: str = "staff") -> dict:
    user = _create_user(role)
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _headers_for_user(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_bidding_tender_analysis_schema_defines_manager_acceptance_tables():
    schema = get_tender_analysis_schema()
    assert schema["schema_version"] == TENDER_ANALYSIS_SCHEMA_VERSION
    assert schema["task_name"] == "招标文件分析"
    assert schema["business_object_policy"]["frontstage"].startswith("默认仅展示进入三张成果表")
    tables = {table["table_key"]: table for table in schema["tables"]}
    assert set(tables) == {"summary", "scoring", "risk_clause"}

    summary = tables["summary"]
    assert summary["sheet_name"] == "结构化信息摘要表"
    summary_item_names = {item["item_name"] for item in summary["item_catalog"]}
    assert {
        "项目概况",
        "答疑时间",
        "报价方式",
        "保证金",
        "联系人",
        "踏勘时间地点",
        "标书制作要求",
        "封标要求",
        "截标时间",
        "评标标准",
        "工期",
        "付款方式",
        "回标前优先澄清清单",
    }.issubset(summary_item_names)

    scoring_export_labels = {field["label"] for field in get_tender_analysis_export_fields("scoring")}
    assert {
        "评分项",
        "满分值",
        "评分标准说明",
        "关联标书章节",
        "我方预估得分",
        "差距分析",
        "建议动作",
        "原文依据",
    }.issubset(scoring_export_labels)

    risk_export_labels = {field["label"] for field in get_tender_analysis_export_fields("risk_clause")}
    assert {
        "条款原文",
        "所在章节",
        "风险等级",
        "风险说明",
        "建议应对方式",
        "是否需答疑",
        "是否影响报价",
        "原文依据",
    }.issubset(risk_export_labels)

    for table_key in tables:
        export_fields = get_tender_analysis_export_fields(table_key)
        export_keys = [field["key"] for field in export_fields]
        assert len(export_keys) == len(set(export_keys))
        assert {"evidence_text", "source_file", "source_location", "confidence", "review_status"}.issubset(export_keys)
        assert all(field["label"] for field in export_fields)


def test_bidding_tender_analysis_preview_generates_three_manager_tables(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_flag = _set_flag("feature_bidding_mvp", True)
    old_summary_llm = _set_flag("feature_bidding_llm_review", False)
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="香港中心装修工程",
            tenderer_name="某建设单位",
            tender_agency="某招标代理",
            project_location="东莞市",
            project_type="商业办公装修",
            status="parsed",
            tender_deadline_at=datetime(2026, 7, 10, 10, 0),
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        scoring_file_uuid = str(uuid.uuid4())
        scoring_segment_text = (
            "评分标准：技术标评分40分，其中施工组织设计15分；项目经理5分；"
            "企业业绩10分；质量安全措施10分。商务资信20分，其中企业资质10分；"
            "类似业绩10分。报价得分40分。"
        )
        db.add(
            BidProjectFile(
                file_uuid=scoring_file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=1024,
                sha256="0" * 64,
                parser_status="parsed",
                parser_version="biz4a-rule-v1",
                extracted_text=scoring_segment_text,
                segments_json=dumps_json(
                    [
                        {
                            "source_file": "招标文件.docx",
                            "source_location": "第12页 第4段",
                            "text": scoring_segment_text,
                            "document_section": "evaluation",
                            "document_section_label": "评标办法",
                            "is_structural_noise": False,
                        }
                    ]
                ),
                page_count=0,
                section_count=1,
                uploaded_by=user.id,
            )
        )
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="biz4a-rule-v1",
            input_file_ids_json=dumps_json([scoring_file_uuid]),
            summary_json=dumps_json(
                {
                    "document_structure": {
                        "segment_by_section": {"bid_rules": 3, "contract_terms": 2, "evaluation_method": 1},
                        "analyzed_by_section": {"bid_rules": 3, "contract_terms": 2, "evaluation_method": 1},
                    }
                }
            ),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.flush()
        requirements = [
            TenderRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                requirement_type="bid_rule",
                source_file="招标文件.docx",
                source_location="第3页",
                original_text="投标保证金人民币10万元；投标文件正本1份、副本2份，并按要求密封。",
                parsed_requirement="投标保证金人民币10万元；投标文件正本1份、副本2份，并按要求密封。",
                owner_role="经营",
                confidence=0.86,
                extraction_method="rule",
            ),
            TenderRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                requirement_type="evaluation",
                source_file="招标文件.docx",
                source_location="第12页",
                original_text="识别到评标办法，请在后续响应矩阵中确认是否满足并绑定证明材料。",
                parsed_requirement="识别到评标办法，请在后续响应矩阵中确认是否满足并绑定证明材料。",
                owner_role="经营",
                confidence=0.82,
                extraction_method="rule",
            ),
            TenderRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                requirement_type="contract",
                source_file="招标文件.docx",
                source_location="第28页",
                original_text="付款方式：进度款按月支付至已完产值80%，竣工结算后支付至97%。",
                parsed_requirement="付款方式：进度款按月支付至已完产值80%，竣工结算后支付至97%。",
                owner_role="法务",
                confidence=0.8,
                extraction_method="rule",
            ),
        ]
        db.add_all(requirements)
        db.flush()
        risks = [
            TenderRisk(
                risk_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                risk_type="fixed_total_price",
                risk_level="high",
                source_file="招标文件.docx",
                source_location="第30页",
                original_text="本工程采用固定总价合同，总价包干，除甲方变更外合同价款不予调整。",
                risk_explanation="固定总价会把清单漏项和市场波动风险转移给施工方。",
                impact_area="合同条款",
                suggested_action="转预算复核成本边界，并在报价中预留风险费用。",
                review_status="pending",
                confidence=0.88,
                extraction_method="rule",
            ),
            TenderRisk(
                risk_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                risk_type="liquidated_damages",
                risk_level="high",
                source_file="招标文件.docx",
                source_location="第33页",
                original_text="因承包人原因造成工期延误的，每延误一天支付40000元违约金。",
                risk_explanation="工期违约金金额较高，需要法务和项目履约共同复核。",
                impact_area="合同条款",
                suggested_action="转法务复核违约金上限，并结合工期计划评估风险。",
                review_status="pending",
                confidence=0.86,
                extraction_method="rule",
            ),
            TenderRisk(
                risk_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                risk_type="liquidated_damages",
                risk_level="high",
                source_file="招标文件.docx",
                source_location="第34页",
                original_text="关键节点工期每延误一天，承包人须按合同约定承担40000元/天违约责任。",
                risk_explanation="节点工期违约责任与总工期违约责任相近，应合并给法务/项目复核。",
                impact_area="合同条款",
                suggested_action="转法务复核违约金上限，并结合工期计划评估风险。",
                review_status="pending",
                confidence=0.82,
                extraction_method="rule",
            ),
        ]
        db.add_all(risks)
        db.flush()
        db.add(
            TenderBusinessObject(
                object_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                object_type="bid_rule",
                object_subtype="evaluation",
                title="评分权重",
                normalized_value="综合评标法",
                normalized_json=dumps_json({"analysis_item_key": "scoring_weights"}),
                source_file="招标文件.docx",
                source_location="第12页",
                original_text="评标原则：综合评标法。",
                source_count=1,
                evidence_json=dumps_json([]),
                related_requirement_ids_json=dumps_json([requirements[1].id]),
                related_risk_ids_json=dumps_json([]),
                document_section="evaluation_method",
                owner_role="经营",
                response_required=True,
                review_status="pending",
                confidence=0.84,
                extraction_method="rule_business_object_v1",
                status="active",
            )
        )
        db.commit()
        db.refresh(project)
        db.refresh(run)

        preview = build_tender_analysis_preview(db, project, run)
        project_uuid = project.project_uuid
    finally:
        db.close()

    try:
        assert preview["preview_version"] == TENDER_ANALYSIS_PREVIEW_VERSION
        assert preview["schema_version"] == TENDER_ANALYSIS_SCHEMA_VERSION
        assert preview["business_object_policy"]["hidden_by_default"] is True
        assert preview["source_counts"]["scoring_segment_source_count"] >= 1
        assert preview["tables"]["summary"]["schema"]["sheet_name"] == "结构化信息摘要表"
        assert preview["tables"]["scoring"]["schema"]["sheet_name"] == "评分细则表"
        assert preview["tables"]["risk_clause"]["schema"]["sheet_name"] == "风险条款清单"

        summary_by_key = {item["item_key"]: item for item in preview["tables"]["summary"]["items"]}
        assert len(summary_by_key) == 13
        assert "2026-07-10" in summary_by_key["submission_deadline"]["normalized_value"]
        assert "10万元" in summary_by_key["bid_bond"]["normalized_value"]
        assert "付款方式" in summary_by_key["payment_terms"]["extracted_value"]
        assert preview["quality_summary"]["summary_extracted_count"] >= 4

        scoring_items = preview["tables"]["scoring"]["items"]
        assert scoring_items
        assert any("技术标评分40分" in item["scoring_standard"] for item in scoring_items)
        assert scoring_items[0]["owner_role"] in {"经营", "预算", "技术"}
        scoring_groups = [item for item in scoring_items if item.get("is_scoring_group")]
        assert scoring_groups
        scoring_children = [child for item in scoring_groups for child in item.get("children") or []]
        assert len(scoring_children) >= 7
        scoring_child_titles = {item["scoring_item"] for item in scoring_children}
        assert {"施工组织设计", "项目经理", "企业资质", "报价"}.issubset(scoring_child_titles)
        assert len(scoring_child_titles) == len(scoring_children)
        assert preview["quality_summary"]["scoring_item_count"] >= len(scoring_children)

        risk_rows = preview["tables"]["risk_clause"]["items"]
        assert len(risk_rows) == 2
        fixed_price = next(item for item in risk_rows if item["risk_category"] == "fixed_total_price")
        penalty = next(item for item in risk_rows if item["risk_category"] == "liquidated_damages")
        assert fixed_price["affects_pricing"] is True
        assert fixed_price["owner_role"] == "预算"
        assert penalty["risk_level"] == "high"
        assert penalty["owner_role"] == "法务"
        assert penalty["risk_count"] == 2
        assert penalty["is_grouped"] is True
        assert len(penalty["children"]) == 2
        assert "工期延误" in penalty["risk_title"]
        assert preview["review_queue"]
        queue_titles = {item["title"] for item in preview["review_queue"]}
        assert any("工期延误" in title for title in queue_titles)
        assert not any("liquidated_damages" in title for title in queue_titles)
        penalty_queue = next(item for item in preview["review_queue"] if "工期延误" in item["title"])
        assert penalty_queue["item_count"] == 2
        assert len(penalty_queue["children"]) == 2
        document_bytes = build_tender_analysis_export_document(preview)
        document_xml = _docx_document_xml(document_bytes)
        assert "投标重要有效信息提取表" in document_xml
        assert "重点提醒" in document_xml
        assert "一、项目概况" in document_xml
        assert "七、评标标准与废标条件" in document_xml
        assert "十一、风险条款清单" in document_xml
        assert "施工组织设计" in document_xml
        assert "汇总" in document_xml
        assert "细分" in document_xml
        assert "关键节点工期" in document_xml
        assert "系统行编码" not in document_xml

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project_uuid}/tender-analysis/preview",
            headers=headers,
            params={"run_uuid": "latest"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["business_object_policy"]["hidden_by_default"] is True
        assert payload["tables"]["summary"]["items"][0]["table_key"] == "summary"
        assert payload["tables"]["risk_clause"]["items"][0]["table_key"] == "risk_clause"

        export_response = client.get(
            f"/api/v1/admin/bidding/projects/{project_uuid}/tender-analysis/export",
            headers=headers,
            params={"run_uuid": "latest"},
        )
        assert export_response.status_code == 200, export_response.text
        assert export_response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert export_response.content.startswith(b"PK")
        exported_document_xml = _docx_document_xml(export_response.content)
        assert "投标重要有效信息提取表" in exported_document_xml
        assert "LLM结构化提取未生成" in exported_document_xml
        assert "未使用旧规则兜底生成 Word" in exported_document_xml
    finally:
        _set_flag("feature_bidding_mvp", old_flag)
        _set_flag("feature_bidding_llm_review", old_summary_llm)


def test_bidding_tender_analysis_uses_deepseek_important_info_extract(client, monkeypatch):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_llm = _set_flag("feature_bidding_llm_review", True)
    old_provider = _set_flag("bidding_llm_provider", "deepseek")
    old_model = _set_flag("bidding_llm_model", "deepseek-v4-pro")
    old_key = _set_flag("deepseek_api_key", "test-deepseek-key")
    call_count = {"value": 0}

    def evidence_id_for(context: dict, keyword: str) -> str:
        for chunk in context["document_chunks"]:
            if keyword in chunk["text"]:
                return chunk["evidence_id"]
        raise AssertionError(f"missing document chunk: {keyword}")

    async def fake_important_info_llm(context, *, username=None, trace_id=None):
        call_count["value"] += 1
        assert context["task"] == "bidding_tender_important_information_extraction"
        assert "candidate_snippets" not in context
        assert any(section["section_key"] == "pricing_contracting" for section in context["output_schema"])
        return {
            "sections": [
                {
                    "section_key": "project_overview",
                    "items": [
                        {
                            "field_key": "tender_project_name",
                            "field_name": "招标工程名称",
                            "status": "found",
                            "value": "香港中心精装修工程",
                            "source_evidence_ids": [evidence_id_for(context, "招标工程名称")],
                            "confidence": 0.91,
                        },
                        {
                            "field_key": "project_location",
                            "field_name": "工程地点",
                            "status": "found",
                            "value": "东莞",
                            "source_evidence_ids": [evidence_id_for(context, "工程地点")],
                            "confidence": 0.91,
                        },
                        {
                            "field_key": "tender_scope",
                            "field_name": "招标范围",
                            "status": "found",
                            "value": "商业街区及6#楼32F办公区装修。",
                            "source_evidence_ids": [evidence_id_for(context, "招标范围")],
                            "confidence": 0.91,
                        },
                    ],
                },
                {
                    "section_key": "qa_site_deadline",
                    "items": [
                        {
                            "field_key": "qa_contact_email",
                            "field_name": "答疑联系人/邮箱",
                            "status": "found",
                            "value": "答疑联系人：李工 13900000000。",
                            "source_evidence_ids": [evidence_id_for(context, "答疑联系人")],
                            "confidence": 0.88,
                        },
                        {
                            "field_key": "site_visit_contact",
                            "field_name": "踏勘联系人",
                            "status": "found",
                            "value": "踏勘联系人：赵工 13700000000。",
                            "source_evidence_ids": [evidence_id_for(context, "踏勘联系人")],
                            "confidence": 0.88,
                        },
                    ],
                },
                {
                    "section_key": "pricing_contracting",
                    "items": [
                        {
                            "field_key": "contract_form",
                            "field_name": "合同形式",
                            "status": "found",
                            "value": "工程承包方式：固定总价包干。",
                            "source_evidence_ids": [evidence_id_for(context, "工程承包方式")],
                            "confidence": 0.9,
                        }
                    ],
                },
                {
                    "section_key": "bond_guarantee",
                    "items": [
                        {
                            "field_key": "bid_bond_amount",
                            "field_name": "投标担保金额",
                            "status": "found",
                            "value": "投标保证金金额为人民币100000元。",
                            "source_evidence_ids": [evidence_id_for(context, "投标保证金")],
                            "confidence": 0.92,
                        },
                        {
                            "field_key": "payment_time",
                            "field_name": "缴交时间",
                            "status": "found",
                            "value": "2026年7月9日17:00前提交。",
                            "source_evidence_ids": [evidence_id_for(context, "2026年7月9日17:00")],
                            "confidence": 0.92,
                        },
                    ],
                },
                {
                    "section_key": "payment_terms",
                    "items": [
                        {
                            "field_key": "progress_payment",
                            "field_name": "进度款支付",
                            "status": "found",
                            "value": "进度款按月支付至已完产值80%。",
                            "source_evidence_ids": [evidence_id_for(context, "合同价款的支付")],
                            "confidence": 0.9,
                        },
                        {
                            "field_key": "settlement_payment",
                            "field_name": "结算款",
                            "status": "found",
                            "value": "竣工结算后支付至97%。",
                            "source_evidence_ids": [evidence_id_for(context, "竣工结算后")],
                            "confidence": 0.9,
                        },
                    ],
                },
            ]
        }

    monkeypatch.setattr(
        "app.services.bidding_tender_analysis._call_deepseek_tender_important_info_extraction",
        fake_important_info_llm,
    )
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="语义摘要项目",
            tenderer_name="某建设单位",
            project_location="东莞",
            project_type="装修工程",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        file_uuid = str(uuid.uuid4())
        segments = [
            {
                "source_file": "招标文件.docx",
                "source_location": "第1页 第1段",
                "document_section": "overview",
                "text": "招标工程名称：香港中心精装修工程；工程地点：东莞；工程规模及特征：商业街区及办公区装修；招标范围：商业街区及6#楼32F办公区装修。",
            },
            {
                "source_file": "招标文件.docx",
                "source_location": "第2页 第1段",
                "document_section": "contact",
                "text": "联系人：前台电话，仅用于收发文件，不作为答疑联系人。",
            },
            {
                "source_file": "招标文件.docx",
                "source_location": "第3页 第2段",
                "document_section": "bid_rules",
                "text": "招投标联系人：王工 13800000000；答疑联系人：李工 13900000000；踏勘联系人：赵工 13700000000。",
            },
            {
                "source_file": "招标文件.docx",
                "source_location": "第4页 第1段",
                "document_section": "pricing",
                "text": "工程承包方式：固定总价包干。投标人应结合图纸、清单和现场条件综合报价。",
            },
            {
                "source_file": "招标文件.docx",
                "source_location": "第5页 第1段",
                "document_section": "bond",
                "text": "投标保证金金额为人民币100000元，须于2026年7月9日17:00前以银行转账或银行保函形式提交。",
            },
            {
                "source_file": "招标文件.docx",
                "source_location": "第30页 第1段",
                "document_section": "contract",
                "text": "合同价款的支付：进度款按月支付至已完产值80%，竣工结算后支付至97%。",
            },
        ]
        db.add(
            BidProjectFile(
                file_uuid=file_uuid,
                project_id=project.id,
                file_type="tender_document",
                original_filename="招标文件.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=2048,
                sha256="1" * 64,
                parser_status="parsed",
                parser_version="biz4a-rule-v1",
                extracted_text="\n".join(item["text"] for item in segments),
                segments_json=dumps_json(segments),
                page_count=30,
                section_count=len(segments),
                uploaded_by=user.id,
            )
        )
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="biz4a-rule-v1",
            input_file_ids_json=dumps_json([file_uuid]),
            summary_json=dumps_json({"document_structure": {"segment_by_section": {"bid_rules": 3}}}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 1, 10, 0),
        )
        db.add(run)
        db.commit()
        project_uuid = project.project_uuid
    finally:
        db.close()

    try:
        response = client.get(
            f"/api/v1/admin/bidding/projects/{project_uuid}/tender-analysis/preview",
            headers=headers,
            params={"run_uuid": "latest"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["important_info"]["status"] == "completed"
        assert call_count["value"] == 1
        section_by_key = {section["section_key"]: section for section in payload["important_info"]["sections"]}
        pricing_by_key = {item["field_key"]: item for item in section_by_key["pricing_contracting"]["items"]}
        bond_by_key = {item["field_key"]: item for item in section_by_key["bond_guarantee"]["items"]}
        qa_by_key = {item["field_key"]: item for item in section_by_key["qa_site_deadline"]["items"]}
        assert pricing_by_key["contract_form"]["value"] == "工程承包方式：固定总价包干"
        assert "100000元" in bond_by_key["bid_bond_amount"]["value"]
        assert "李工" in qa_by_key["qa_contact_email"]["value"]
        assert "前台电话" not in qa_by_key["qa_contact_email"]["value"]

        export_response = client.get(
            f"/api/v1/admin/bidding/projects/{project_uuid}/tender-analysis/export",
            headers=headers,
            params={"run_uuid": "latest"},
        )
        assert export_response.status_code == 200, export_response.text
        assert call_count["value"] == 1
        exported_document_xml = _docx_document_xml(export_response.content)
        assert "投标重要有效信息提取表" in exported_document_xml
        assert "固定总价包干" in exported_document_xml
        assert "100000元" in exported_document_xml
        assert "前台电话" not in exported_document_xml
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_bidding_llm_review", old_llm)
        _set_flag("bidding_llm_provider", old_provider)
        _set_flag("bidding_llm_model", old_model)
        _set_flag("deepseek_api_key", old_key)


def _business_requirement(
    item_id: int,
    requirement_type: str,
    text: str,
    *,
    document_section: str | None = None,
    is_structural_noise: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        file_id=1,
        requirement_type=requirement_type,
        original_text=text,
        parsed_requirement=text,
        source_file="tender.txt",
        source_location=f"P{item_id}",
        document_section=document_section,
        is_structural_noise=is_structural_noise,
        owner_role=None,
        confidence=0.8,
    )


def _business_risk(
    item_id: int,
    risk_type: str,
    text: str,
    *,
    document_section: str | None = None,
    is_structural_noise: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        risk_uuid=f"risk-{item_id}",
        file_id=1,
        risk_type=risk_type,
        risk_level="high",
        is_blocking=False,
        review_status="pending",
        original_text=text,
        risk_explanation=text,
        suggested_action="复核后处理",
        source_file="tender.txt",
        source_location=f"R{item_id}",
        document_section=document_section,
        is_structural_noise=is_structural_noise,
        owner_role=None,
        confidence=0.8,
    )


def test_bidding_parser_extracts_contract_and_void_risks_from_text():
    text = """
    第一章 投标须知
    投标截止时间为2026年7月20日，投标有效期90日历天。
    本工程采用固定总价合同，总价包干，合同价款不予调整。
    清单漏项视为已包含在综合单价内，不另行计取。
    技术标为暗标，不得出现投标人名称或单位标识，否则否决投标。
    图纸以现场为准，暂定工程量按实结算。
    """
    extracted = extract_tender_text(text.encode("utf-8"), "招标文件.txt", "text/plain")
    result = analyze_tender_segments(extracted["segments"])

    risk_types = {item["risk_type"] for item in result["risks"]}
    requirement_types = {item["requirement_type"] for item in result["requirements"]}

    assert "fixed_total_price" in risk_types
    assert "omission_liability" in risk_types
    assert "anonymous_bid" in risk_types
    assert "bid_rejection" in risk_types
    assert "bid_rule" in requirement_types
    assert "contract" in requirement_types
    assert result["summary"]["high_risk_count"] >= 3
    assert result["summary"]["blocking_risk_count"] >= 1
    assert result["questions"]


def test_bidding_business_objects_quality_v2_classifies_actionable_objects():
    requirements = [
        _business_requirement(1, "bid_rule", "招标人在投标截止日期前3日发出澄清答疑纪要，作为招标文件组成部分。"),
        _business_requirement(2, "bid_rule", "投标保函应在投标有效期满后28天内继续有效。"),
        _business_requirement(3, "contract", "工程进度款每月支付一次，审核后支付至当期已完工程量的80%。"),
        _business_requirement(4, "contract", "材料不合格时，甲方有权扣减该批材料货款50%作为违约金。"),
        _business_requirement(5, "contract", "合同承包内容为办公区装修工程。"),
        _business_requirement(6, "material", "投标单位须提交拟采用的材料品牌表。"),
    ]
    risks = [
        _business_risk(101, "no_price_adjustment", "综合单价包干，人工及材料价格波动不予调整。"),
        _business_risk(102, "design_or_drawing_unclear", "现场条件误解导致的索赔或工期顺延申请不被批准。"),
    ]

    objects = build_tender_business_objects(requirements, risks)
    object_pairs = {(item["object_type"], item["object_subtype"]) for item in objects}
    by_subtype = {item["object_subtype"]: item for item in objects}
    summary = build_business_object_summary(objects)

    assert ("bid_rule", "clarification_deadline") in object_pairs
    assert ("bid_rule", "bid_deadline") not in object_pairs
    assert ("bid_rule", "bid_bond") in object_pairs
    assert ("contract_clause", "progress_payment") in object_pairs
    assert ("contract_clause", "material_quality_penalty") in object_pairs
    assert ("contract_clause", "contract_general") not in object_pairs
    assert ("document_checklist", "material_brand_table") in object_pairs
    assert by_subtype["market_price_no_adjustment"]["normalized_json"]["business_action"] == "quote_allowance"
    assert by_subtype["site_condition_responsibility"]["normalized_json"]["business_action"] == "quote_allowance"
    assert "to_clarify" in by_subtype["site_condition_responsibility"]["normalized_json"]["risk_secondary_actions"]
    assert by_subtype["material_quality_penalty"]["response_required"] is False
    assert summary["object_by_action"]["quote_allowance"] >= 2
    assert summary["response_required_count"] < summary["object_count"]


def test_bidding_business_objects_v2_splits_large_objects_and_prefers_risk_evidence():
    requirements = [
        _business_requirement(1, "contract", "合同工期45日历天，承包人应按节点完成。"),
        _business_requirement(2, "contract", "承包人应每月25日前提交下月进度计划。"),
        _business_requirement(3, "contract", "乙方已充分踏勘现场道路、储存空间和装卸限制。"),
        _business_requirement(4, "contract", "开工前承包人须办妥当地规定的一切手续并取得批准文件。"),
    ]
    risks = [
        _business_risk(101, "liquidated_damages", "因承包人原因造成工期延误的，每延误一天支付40000元/日历天违约金。"),
        _business_risk(102, "liquidated_damages", "材料不合格时，甲方有权扣减该批材料货款50%作为违约金。"),
        _business_risk(103, "liquidated_damages", "项目经理未经同意更换，每人次支付100000元违约金。"),
        _business_risk(104, "claim_time_limit", "签证单必须对应合同条款、设计交底和有效书面正式文件，否则为无效签证。"),
        _business_risk(105, "claim_time_limit", "因承包人原因引发发包人反索赔事项时，发包人可从工程款中扣除。"),
    ]

    objects = build_tender_business_objects(requirements, risks)
    by_subtype = {item["object_subtype"]: item for item in objects}
    object_pairs = {(item["object_type"], item["object_subtype"]) for item in objects}
    summary = build_business_object_summary(objects)

    assert ("contract_clause", "schedule_delay_penalty") in object_pairs
    assert ("contract_clause", "material_quality_penalty") in object_pairs
    assert ("contract_clause", "personnel_penalty") in object_pairs
    assert ("contract_clause", "claim_document_requirement") in object_pairs
    assert ("contract_clause", "counterclaim_deduction") in object_pairs
    assert ("contract_clause", "total_duration") in object_pairs
    assert ("contract_clause", "schedule_plan_submission") in object_pairs
    assert ("contract_clause", "site_condition_responsibility") in object_pairs
    assert ("contract_clause", "permit_procedure") in object_pairs

    delay_penalty = by_subtype["schedule_delay_penalty"]
    assert delay_penalty["normalized_json"]["business_action"] == "quote_allowance"
    assert delay_penalty["normalized_json"]["suggested_review_status"] == "to_quote_allowance"
    assert delay_penalty["normalized_json"]["risk_cards"]
    assert delay_penalty["evidence"][0]["source_kind"] == "risk"
    assert "40000" in delay_penalty["original_text"]

    counterclaim = by_subtype["counterclaim_deduction"]
    assert counterclaim["normalized_json"]["business_action"] == "quote_allowance"
    assert "to_clarify" in counterclaim["normalized_json"]["risk_secondary_actions"]
    assert summary["quote_allowance_count"] >= 4
    assert summary["object_count"] >= 9


def test_bidding_business_objects_v3_denoises_representative_evidence_and_large_objects():
    requirements = [
        _business_requirement(1, "project_basic", "投标文件应密封并在封套处加盖单位公章。"),
        _business_requirement(2, "project_basic", "工程名称：东莞香港中心装修工程；建设地点：东莞市南城区；承包方式：专业分包。"),
        _business_requirement(3, "format", "投标文件格式包括响应表，需逐项填写招标要求响应情况。"),
        _business_requirement(4, "bid_void", "未按招标文件要求作出实质性响应或存在重大偏差的，将否决投标。"),
    ]
    risks = [
        _business_risk(101, "material_brand_constraint", "工程进度款按月支付至已完工程量的80%。"),
        _business_risk(102, "material_brand_constraint", "主要材料必须采用甲限品牌范围内产品，替代品牌须经甲方认质认价。"),
    ]
    for index in range(24):
        risks.append(
            _business_risk(
                200 + index,
                "liquidated_damages",
                f"材料设备进场验收不合格时，甲方有权扣减该批材料货款{index + 1}%作为违约金。",
            )
        )

    objects = build_tender_business_objects(requirements, risks)
    by_subtype = {item["object_subtype"]: item for item in objects}
    summary = build_business_object_summary(objects)

    project_basic = by_subtype["project_basic"]
    assert "工程名称" in project_basic["original_text"]
    assert project_basic["normalized_json"]["representative_evidence_quality"] in {"medium", "high"}
    assert project_basic["normalized_json"]["low_confidence_representative"] is False

    brand_constraint = by_subtype["brand_constraint"]
    assert "甲限品牌" in brand_constraint["original_text"]
    assert "进度款" not in brand_constraint["original_text"]
    assert "甲限" in brand_constraint["normalized_json"]["representative_matched_keywords"]
    assert brand_constraint["normalized_json"]["representative_evidence_quality"] == "high"

    response_table = by_subtype["response_table"]
    assert "响应表" in response_table["original_text"]
    assert "否决投标" not in response_table["original_text"]
    assert response_table["normalized_json"]["representative_negative_keywords"] == []

    material_penalty = by_subtype["material_penalty_deduction"]
    assert material_penalty["source_count"] >= 20
    assert material_penalty["normalized_json"]["split_applied"] is True
    assert material_penalty["normalized_json"]["split_parent_subtype"] == "material_quality_penalty"
    assert material_penalty["normalized_json"]["evidence_sample_count"] <= 8
    assert material_penalty["normalized_json"]["omitted_evidence_count"] > 0
    assert all(item["evidence_quality"] in {"medium", "high"} for item in material_penalty["evidence"])
    assert summary["large_object_count"] >= 1
    assert summary["secondary_split_count"] >= 1
    assert summary["low_confidence_evidence_count"] == 0


def test_bidding_business_objects_v4_splits_large_objects_and_demotes_structural_evidence():
    requirements = [
        _business_requirement(
            1,
            "contract",
            "十二、竣工验收和结算 ................................ 2/2/39 34 竣工验收 35 竣工结算审计",
            document_section="table_of_contents",
            is_structural_noise=True,
        ),
        _business_requirement(
            2,
            "contract",
            "竣工结算须经发包人审计审核后确认，承包人应按要求提交完整资料。",
            document_section="contract_terms",
        ),
    ]
    for index in range(24):
        case = index % 4
        if case == 0:
            text = "承包人负责图纸深化设计，深化设计标准不得低于招标图纸。"
        elif case == 1:
            text = "如发现图纸错漏、缺失或设计资料不详，承包人应及时复核并提出。"
        elif case == 2:
            text = "承包人应参加图纸会审和设计交底，并完成图纸审核记录。"
        else:
            text = "承包人须提交竣工图、记录图纸和使用说明书原稿。"
        requirements.append(_business_requirement(100 + index, "contract", text, document_section="contract_terms"))

    objects = build_tender_business_objects(requirements, [])
    by_subtype = {item["object_subtype"]: item for item in objects}
    object_pairs = {(item["object_type"], item["object_subtype"]) for item in objects}
    summary = build_business_object_summary(objects)

    settlement = by_subtype["settlement_clause"]
    assert "审计审核" in settlement["original_text"]
    assert "................................" not in settlement["original_text"]
    assert settlement["normalized_json"]["representative_evidence_context_quality"] == "body"

    assert ("contract_clause", "drawing_deepening_design") in object_pairs
    assert ("contract_clause", "drawing_error_liability") in object_pairs
    assert ("contract_clause", "drawing_handover_review") in object_pairs
    assert ("contract_clause", "asbuilt_drawing_record") in object_pairs
    assert by_subtype["drawing_deepening_design"]["normalized_json"]["split_parent_subtype"] == "drawing_review"
    assert by_subtype["drawing_deepening_design"]["normalized_json"]["secondary_business_actions"]
    assert summary["secondary_split_count"] >= 4
    assert summary["needs_secondary_split_count"] == 0


def test_bidding_parser_identifies_structure_and_skips_toc_noise():
    text = """
    目 录
    第一章 - 投标须知 页数 CT/1/1 - CT/1/2
    第二章 - 合同文件 协议书 C/1 - C/25
    第三章 - 标准、规范和技术要求 D/1 - D/5
    第五章 - 工程量清单 P/1 - P/49
    第六章 - 投标文件格式 6/1/1 - 6/2/16

    第二章 合同条款
    本工程采用综合单价包干形式，综合单价不会因人工费、物价、费率、汇率或者政策的变动而有所调整。
    清单漏项视为已包含在合同总价或综合单价中，不另行计取。
    """
    extracted = extract_tender_text(text.encode("utf-8"), "tender.txt", "text/plain")
    segments = extracted["segments"]
    toc_segments = [item for item in segments if item["document_section"] == "table_of_contents"]
    assert toc_segments
    assert all(item["is_structural_noise"] for item in toc_segments)

    result = analyze_tender_segments(segments)
    summary = result["summary"]
    assert summary["ignored_segment_count"] >= 1
    assert summary["analyzed_segment_count"] < summary["segment_count"]
    assert summary["document_structure"]["segment_by_section"]["table_of_contents"] >= 1
    assert summary["document_structure"]["analyzed_by_section"]["contract_terms"] >= 1
    assert not any("第一章 - 投标须知" in item["original_text"] for item in result["requirements"])
    assert any(item["requirement_type"] == "contract" for item in result["requirements"])
    assert {"no_price_adjustment", "omission_liability"} & {item["risk_type"] for item in result["risks"]}


def test_bidding_structure_v2_inherits_chapters_and_filters_common_false_positives():
    text = """
    第一章 投标须知

    7.1 投标文件由商务标部分和技术标部分组成。

    7.3 对技术标部分的要求：投标人须编制相应本工程需要的施工组织设计。

    8.2 投标文件的电子文档必须提供U盘，商务标部分1份，技术标部分1份，并在U盘上注明投标人名称、工程名称。

    技术标为暗标，不得出现投标人名称、单位名称或企业标识，否则否决投标。

    第二章 合同条款

    双方应按约定办理现场交接。

    本合同采用综合单价包干形式，综合单价不会因人工费、物价、费率、汇率或者政策的变动而有所调整。

    2.03 甲供材料（不适用）。
    """
    extracted = extract_tender_text(text.encode("utf-8"), "tender.txt", "text/plain")
    result = analyze_tender_segments(extracted["segments"])
    summary = result["summary"]
    risks = result["risks"]

    assert summary["document_structure"]["version"] == "biz4a-structure-v2"
    assert summary["inherited_segment_count"] >= 1
    assert summary["document_structure"]["analyzed_by_section"]["contract_terms"] >= 2

    anonymous_risks = [item for item in risks if item["risk_type"] == "anonymous_bid"]
    assert len(anonymous_risks) == 1
    assert "暗标" in anonymous_risks[0]["original_text"]
    assert not any("投标文件由商务标部分和技术标部分组成" in item["original_text"] for item in anonymous_risks)
    assert not any(item["risk_type"] == "material_brand_constraint" and "不适用" in item["original_text"] for item in risks)
    assert any(item["risk_type"] == "no_price_adjustment" for item in risks)


def test_bidding_feature_disabled_returns_not_found(client):
    headers = _headers(client, "staff")
    old_flag = _set_flag("feature_bidding_mvp", False)
    try:
        response = client.get("/api/v1/admin/bidding/projects", headers=headers)
    finally:
        _set_flag("feature_bidding_mvp", old_flag)

    assert response.status_code == 404
    assert response.json()["detail"] == "NOT_FOUND"


def test_bidding_llm_review_only_processes_uncertain_business_objects(client, monkeypatch):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_llm = _set_flag("feature_bidding_llm_review", True)
    old_provider = _set_flag("bidding_llm_provider", "deepseek")
    old_model = _set_flag("bidding_llm_model", "deepseek-v4-pro")
    old_key = _set_flag("deepseek_api_key", "test-deepseek-key")
    old_max = _set_flag("bidding_llm_max_objects", 25)
    calls = []

    async def fake_call(context, *, username=None, trace_id=None):
        calls.append(
            {
                "object_uuid": context["object_uuid"],
                "flags": context["uncertain_flags"],
                "model_context": context["object"]["object_subtype"],
                "username": username,
                "trace_id": trace_id,
            }
        )
        return {
            "object_review": {
                "decision": "manual_review",
                "confidence": 0.87,
                "suggested_object_type": context["object"]["object_type"],
                "suggested_object_subtype": context["object"]["object_subtype"],
                "suggested_title": context["object"]["title"],
                "primary_business_action": "clarification",
                "secondary_business_actions": ["quote_allowance"],
                "selected_evidence_ids": ["E1"],
                "reason": "Evidence is mixed and needs manual confirmation.",
                "suggested_reviewer_note": "Check with owner before bid response.",
                "manual_questions": ["Confirm contract boundary."],
            }
        }

    monkeypatch.setattr(
        "app.services.bidding_llm_review._call_deepseek_business_object_review",
        fake_call,
    )

    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="LLM review project",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4a"}),
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()

        rows = [
            (
                "weak-payment",
                "payment_clause",
                {"weak_split": True, "needs_llm_review": True, "business_action": "legal_review"},
            ),
            (
                "large-scope",
                "scope_boundary",
                {"needs_secondary_split": True, "large_object": True, "business_action": "quote_allowance"},
            ),
            (
                "stable-bid-rule",
                "bid_deadline",
                {"business_action": "bid_compliance"},
            ),
        ]
        object_uuids = {}
        for index, (title, subtype, normalized) in enumerate(rows, 1):
            object_uuid = str(uuid.uuid4())
            object_uuids[title] = object_uuid
            db.add(
                TenderBusinessObject(
                    object_uuid=object_uuid,
                    project_id=project.id,
                    parse_run_id=run.id,
                    object_type="contract_clause" if subtype != "bid_deadline" else "bid_rule",
                    object_subtype=subtype,
                    title=title,
                    normalized_value=None,
                    normalized_json=dumps_json(normalized),
                    source_file="tender.txt",
                    source_location=f"P{index}",
                    original_text=f"{title} original clause",
                    source_count=30 if normalized.get("large_object") else 2,
                    evidence_json=dumps_json(
                        [
                            {
                                "source_kind": "requirement",
                                "source_file": "tender.txt",
                                "source_location": f"P{index}",
                                "original_text": f"{title} evidence clause",
                                "document_section": "contract_terms",
                            }
                        ]
                    ),
                    related_requirement_ids_json=dumps_json([]),
                    related_risk_ids_json=dumps_json([]),
                    document_section="contract_terms",
                    owner_role="commercial",
                    response_required=True,
                    review_status="pending",
                    confidence=0.7,
                    extraction_method="test",
                    status="active",
                )
            )
        db.commit()
        project_uuid = project.project_uuid

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project_uuid}/business-objects/llm-review",
            headers=headers,
            json={"run_uuid": "latest", "limit": 25, "force": False, "only_pending": True},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["model"] == "deepseek-v4-pro"
        assert payload["candidate_count"] == 2
        assert payload["reviewed_count"] == 2
        assert len(calls) == 2
        assert {item["object_uuid"] for item in calls} == {object_uuids["weak-payment"], object_uuids["large-scope"]}
        assert all(any(item["flags"].values()) for item in calls)

        refreshed = (
            db.query(TenderBusinessObject)
            .filter(TenderBusinessObject.project_id == project.id)
            .order_by(TenderBusinessObject.id.asc())
            .all()
        )
        by_title = {item.title: loads_json(item.normalized_json, {}) for item in refreshed}
        assert by_title["weak-payment"]["llm_review_status"] == "pending_manual_confirm"
        assert by_title["weak-payment"]["llm_model"] == "deepseek-v4-pro"
        assert by_title["weak-payment"]["llm_review"]["decision"] == "manual_review"
        assert by_title["weak-payment"]["llm_review"]["selected_evidence_ids"] == ["E1"]
        assert "llm_review" not in by_title["stable-bid-rule"]

        calls.clear()
        skipped_response = client.post(
            f"/api/v1/admin/bidding/projects/{project_uuid}/business-objects/llm-review",
            headers=headers,
            json={
                "run_uuid": "latest",
                "limit": 1,
                "force": False,
                "only_pending": True,
                "object_uuids": [object_uuids["stable-bid-rule"]],
            },
        )
        assert skipped_response.status_code == 200, skipped_response.text
        assert skipped_response.json()["data"]["status"] == "no_candidates"
        assert calls == []

        already_reviewed_response = client.post(
            f"/api/v1/admin/bidding/projects/{project_uuid}/business-objects/llm-review",
            headers=headers,
            json={
                "run_uuid": "latest",
                "limit": 1,
                "force": False,
                "only_pending": True,
                "object_uuids": [object_uuids["weak-payment"]],
            },
        )
        assert already_reviewed_response.status_code == 200, already_reviewed_response.text
        assert already_reviewed_response.json()["data"]["status"] == "no_candidates"
        assert calls == []
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_bidding_llm_review", old_llm)
        _set_flag("bidding_llm_provider", old_provider)
        _set_flag("bidding_llm_model", old_model)
        _set_flag("deepseek_api_key", old_key)
        _set_flag("bidding_llm_max_objects", old_max)


def test_bidding_llm_review_decision_accept_reject_modify(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_llm = _set_flag("feature_bidding_llm_review", True)
    review = {
        "decision": "manual_review",
        "confidence": 0.83,
        "suggested_object_type": "contract_clause",
        "suggested_object_subtype": "payment_clause",
        "suggested_title": "付款条件复核",
        "primary_business_action": "legal_review",
        "secondary_business_actions": ["quote_allowance"],
        "selected_evidence_ids": ["E1"],
        "reason": "付款条款证据需要人工确认。",
        "suggested_reviewer_note": "请复核付款资料要求。",
        "manual_questions": ["是否需要向甲方确认付款节点？"],
        "suggested_splits": [],
        "validation_warnings": [],
        "read_only": True,
    }
    object_uuids: dict[str, str] = {}
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="LLM decision project",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()

        for title in ("accept-object", "reject-object", "modify-object"):
            object_uuid = str(uuid.uuid4())
            object_uuids[title] = object_uuid
            db.add(
                TenderBusinessObject(
                    object_uuid=object_uuid,
                    project_id=project.id,
                    parse_run_id=run.id,
                    object_type="contract_clause",
                    object_subtype="payment_clause",
                    title=title,
                    normalized_json=dumps_json(
                        {
                            "needs_llm_review": True,
                            "business_action": "legal_review",
                            "llm_review_status": "pending_manual_confirm",
                            "llm_review": {**review, "suggested_title": title},
                        }
                    ),
                    source_file="tender.txt",
                    source_location="P1",
                    original_text=f"{title} original clause",
                    source_count=1,
                    evidence_json=dumps_json(
                        [
                            {
                                "source_kind": "requirement",
                                "source_file": "tender.txt",
                                "source_location": "P1",
                                "original_text": f"{title} evidence clause",
                                "document_section": "contract_terms",
                            }
                        ]
                    ),
                    related_requirement_ids_json=dumps_json([]),
                    related_risk_ids_json=dumps_json([]),
                    document_section="contract_terms",
                    owner_role="commercial",
                    response_required=True,
                    review_status="pending",
                    confidence=0.7,
                    extraction_method="test",
                    status="active",
                )
            )
        db.commit()

        accept_response = client.patch(
            f"/api/v1/admin/bidding/business-objects/{object_uuids['accept-object']}/llm-review",
            headers=headers,
            json={"action": "accept"},
        )
        assert accept_response.status_code == 200, accept_response.text
        accept_object = accept_response.json()["data"]
        accept_normalized = accept_object["normalized"]
        assert accept_normalized["llm_review_status"] == "accepted"
        assert accept_normalized["llm_review_decision_action"] == "accept"
        assert accept_normalized["llm_review_effective"]["suggested_title"] == "accept-object"
        assert accept_object["review_status"] == "pending"

        reject_without_note = client.patch(
            f"/api/v1/admin/bidding/business-objects/{object_uuids['reject-object']}/llm-review",
            headers=headers,
            json={"action": "reject"},
        )
        assert reject_without_note.status_code == 422
        assert reject_without_note.json()["detail"] == "BIDDING_LLM_REVIEW_REJECT_NOTE_REQUIRED"

        reject_response = client.patch(
            f"/api/v1/admin/bidding/business-objects/{object_uuids['reject-object']}/llm-review",
            headers=headers,
            json={"action": "reject", "reviewer_note": "证据不足，驳回建议。"},
        )
        assert reject_response.status_code == 200, reject_response.text
        reject_object = reject_response.json()["data"]
        reject_normalized = reject_object["normalized"]
        assert reject_normalized["llm_review_status"] == "rejected"
        assert reject_normalized["llm_review_decision_note"] == "证据不足，驳回建议。"
        assert reject_normalized["llm_review_effective"] is None
        assert reject_object["review_status"] == "pending"

        modify_response = client.patch(
            f"/api/v1/admin/bidding/business-objects/{object_uuids['modify-object']}/llm-review",
            headers=headers,
            json={
                "action": "modify",
                "reviewer_note": "改为拆分建议，后续人工判断。",
                "modified_review": {
                    "decision": "split",
                    "suggested_title": "付款条件拆分建议",
                    "suggested_object_subtype": "payment_clause",
                    "primary_business_action": "clarification",
                    "reason": "付款资料条件与付款节点应拆开复核。",
                    "suggested_reviewer_note": "先答疑确认付款资料边界。",
                },
            },
        )
        assert modify_response.status_code == 200, modify_response.text
        modify_object = modify_response.json()["data"]
        modify_normalized = modify_object["normalized"]
        assert modify_normalized["llm_review_status"] == "modified"
        assert modify_normalized["llm_review_decision_action"] == "modify"
        assert modify_normalized["llm_review_manual_edit"]["decision"] == "split"
        assert modify_normalized["llm_review_effective"]["decision"] == "split"
        assert modify_normalized["llm_review_effective"]["primary_business_action"] == "clarification"
        assert modify_normalized["llm_review_effective"]["read_only"] is False
        assert modify_normalized["llm_review_effective"]["manual_modified"] is True
        assert modify_object["review_status"] == "pending"
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_bidding_llm_review", old_llm)


def test_bidding_response_matrix_generate_is_idempotent_and_uses_effective_llm_review(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="Response matrix project",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()
        uncovered_requirement = TenderRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            requirement_type="qualification",
            source_file="tender.txt",
            source_location="R1",
            original_text="投标人须具备建筑装修装饰工程专业承包资质。",
            parsed_requirement="准备企业资质证明",
            compliance_status="pending",
            risk_level="low",
            owner_role="经营",
            output_section="资格审查文件",
            confidence=0.8,
            extraction_method="test",
            status="active",
        )
        covered_requirement = TenderRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            requirement_type="submission",
            source_file="tender.txt",
            source_location="R2",
            original_text="投标文件需签字盖章。",
            parsed_requirement="检查签字盖章",
            compliance_status="pending",
            risk_level="low",
            owner_role="经营",
            output_section="投标文件",
            confidence=0.8,
            extraction_method="test",
            status="active",
        )
        uncovered_risk = TenderRisk(
            risk_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            requirement_id=None,
            risk_type="liquidated_damages",
            risk_level="high",
            source_file="tender.txt",
            source_location="P9",
            original_text="工期逾期每日按合同价千分之一支付违约金。",
            risk_explanation="高额工期违约金",
            impact_area="合同",
            suggested_action="法务复核违约金上限。",
            is_blocking=False,
            review_status="pending",
            confidence=0.8,
            extraction_method="test",
        )
        covered_risk = TenderRisk(
            risk_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            requirement_id=None,
            risk_type="fixed_total_price",
            risk_level="high",
            source_file="tender.txt",
            source_location="P10",
            original_text="本项目采用固定总价包干。",
            risk_explanation="总价包干",
            impact_area="报价",
            suggested_action="报价预留。",
            is_blocking=False,
            review_status="pending",
            confidence=0.8,
            extraction_method="test",
        )
        db.add_all([uncovered_requirement, covered_requirement, uncovered_risk, covered_risk])
        db.flush()

        rows = [
            (
                "accepted-scope",
                {
                    "business_action": "reference",
                    "llm_review_status": "accepted",
                    "llm_review": {"decision": "manual_review", "primary_business_action": "reference"},
                    "llm_review_effective": {
                        "decision": "manual_review",
                        "suggested_title": "答疑确认施工范围",
                        "primary_business_action": "clarification",
                        "suggested_object_type": "contract_clause",
                        "suggested_reviewer_note": "向甲方确认范围边界。",
                    },
                },
                None,
                None,
            ),
            (
                "rejected-payment",
                {
                    "business_action": "quote_allowance",
                    "llm_review_status": "rejected",
                    "llm_review": {
                        "decision": "rename",
                        "suggested_title": "错误的法务标题",
                        "primary_business_action": "legal_review",
                    },
                    "llm_review_effective": None,
                },
                None,
                covered_risk.id,
            ),
            (
                "modified-contract",
                {
                    "business_action": "reference",
                    "llm_review_status": "modified",
                    "llm_review_effective": {
                        "decision": "split",
                        "suggested_title": "合同付款条件法务复核",
                        "primary_business_action": "legal_review",
                    },
                },
                covered_requirement.id,
                None,
            ),
        ]
        for title, normalized, requirement_id, risk_id in rows:
            db.add(
                TenderBusinessObject(
                    object_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    requirement_id=requirement_id,
                    risk_id=risk_id,
                    object_type="contract_clause",
                    object_subtype="payment_clause",
                    title=title,
                    normalized_json=dumps_json(normalized),
                    source_file="tender.txt",
                    source_location="P1",
                    original_text=f"{title} original clause",
                    source_count=1,
                    evidence_json=dumps_json(
                        [
                            {
                                "source_kind": "requirement",
                                "source_file": "tender.txt",
                                "source_location": "P1",
                                "original_text": f"{title} evidence clause",
                                "risk_level": "high" if risk_id else "low",
                            }
                        ]
                    ),
                    related_requirement_ids_json=dumps_json([requirement_id] if requirement_id else []),
                    related_risk_ids_json=dumps_json([risk_id] if risk_id else []),
                    document_section="contract_terms",
                    owner_role="经营",
                    response_required=True,
                    review_status="pending",
                    confidence=0.7,
                    extraction_method="test",
                    status="active",
                )
            )
        db.commit()

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["candidate_count"] == 5
        assert payload["created_count"] == 5
        assert payload["total_count"] == 5
        assert payload["summary"]["to_clarify_count"] == 1
        assert payload["summary"]["to_quote_allowance_count"] == 1
        assert payload["summary"]["legal_review_count"] == 2

        list_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
        )
        assert list_response.status_code == 200, list_response.text
        rows_by_title = {item["response_title"]: item for item in list_response.json()["data"]}
        assert rows_by_title["答疑确认施工范围"]["response_action"] == "clarification"
        assert rows_by_title["rejected-payment"]["response_action"] == "quote_allowance"
        assert rows_by_title["rejected-payment"]["normalized"]["llm_review_rejected"] is True
        assert rows_by_title["合同付款条件法务复核"]["response_action"] == "legal_review"
        assert rows_by_title["高额工期违约金"]["created_from"] == "risk"
        assert rows_by_title["准备企业资质证明"]["response_action"] == "qualification_material"

        repeat_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert repeat_response.status_code == 200, repeat_response.text
        repeat_payload = repeat_response.json()["data"]
        assert repeat_payload["created_count"] == 0
        assert repeat_payload["skipped_existing_count"] == 5
        assert repeat_payload["total_count"] == 5

        edit_uuid = rows_by_title["答疑确认施工范围"]["response_item_uuid"]
        update_response = client.patch(
            f"/api/v1/admin/bidding/response-items/{edit_uuid}",
            headers=headers,
            json={
                "status": "done",
                "response_action": "direct_response",
                "response_note": "已在商务响应中说明。",
                "reviewer_note": "人工确认完成。",
            },
        )
        assert update_response.status_code == 200, update_response.text
        updated = update_response.json()["data"]
        assert updated["status"] == "done"
        assert updated["response_action"] == "direct_response"
        assert updated["response_note"] == "已在商务响应中说明。"
        assert updated["reviewer_note"] == "人工确认完成。"
        assert db.query(TenderResponseItem).filter(TenderResponseItem.parse_run_id == run.id).count() == 5
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)


def test_bidding_bid_file_format_plan_detects_and_confirms_project_format(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    db = SessionLocal()
    try:
        split_project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="商务技术分册测试项目",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({}),
        )
        db.add(split_project)
        db.flush()
        db.add(
            BidProjectFile(
                file_uuid=str(uuid.uuid4()),
                project_id=split_project.id,
                file_type="tender_document",
                original_filename="商务技术分册招标文件.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=1200,
                sha256="split-format",
                parser_status="parsed",
                parser_version="test",
                extracted_text=(
                    "第六章 投标文件格式\n"
                    "投标文件由商务标和技术标组成。\n"
                    "电子标书：商务标部分 1 份；技术标部分 1 份，并在U盘上注明投标人名称、工程名称。\n"
                    "7.2 商务标部分主要包括下列内容：投标报价汇总表及报价明细；投标函；投标保证金递交回执；商务条款偏离表；报价编制需参考施工组织设计。\n"
                    "7.3 对技术标部分的要求：营业执照及相关资质证明；施工组织设计及施工方案；施工总进度计划；成品保护方案；项目重难点分析。\n"
                    "投标文件技术标独立装订成册、投标文件商务标独立装订成册，商务标、技术标分别独立封装。"
                ),
                segments_json=dumps_json([]),
                page_count=1,
                section_count=1,
                uploaded_by=user.id,
            )
        )
        split_run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=split_project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(split_run)
        db.commit()

        preview_response = client.get(
            f"/api/v1/admin/bidding/projects/{split_project.project_uuid}/bid-draft/format-plan",
            headers=headers,
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]
        assert preview["review_status"] == "preview"
        assert preview["package_mode"] == "separate_business_technical"
        assert preview["format_source"] == "embedded_in_tender"
        assert {package["package_key"] for package in preview["structure"]["packages"]} == {"business", "technical"}
        business_items = preview["structure"]["packages"][0]["items"]
        technical_items = preview["structure"]["packages"][1]["items"]
        business_titles = {item["item_title"] for item in business_items}
        technical_titles = {item["item_title"] for item in technical_items}
        assert any(item["content_type"] == "pricing_table" for item in business_items)
        assert any(item["item_title"] == "施工组织设计/施工方案" for item in technical_items)
        assert "施工组织设计/施工方案" not in business_titles
        assert "投标函/投标书" not in technical_titles
        assert "投标保证金/投标保函" not in technical_titles
        construction_item = next(item for item in technical_items if item["item_title"] == "施工组织设计/施工方案")
        assert construction_item["conflict_status"] == "cross_package_duplicate"
        assert construction_item["conflict_packages"] == ["business", "technical"]
        conflict_titles = {
            item["item_title"]
            for package in preview["structure"]["packages"]
            for item in package["items"]
            if item.get("conflict_status") == "cross_package_duplicate"
        }
        assert conflict_titles == {"施工组织设计/施工方案"}
        assert any(warning["code"] == "CROSS_PACKAGE_DUPLICATE" for warning in preview["warnings"])
        assert preview["structure"]["packaging_requirements"]

        generate_response = client.post(
            f"/api/v1/admin/bidding/projects/{split_project.project_uuid}/bid-draft/format-plan/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert generate_response.status_code == 200, generate_response.text
        generated = generate_response.json()["data"]
        assert generated["plan_uuid"]
        assert generated["review_status"] == "draft"
        assert db.query(BidFileFormatPlan).filter(BidFileFormatPlan.parse_run_id == split_run.id).count() == 1

        structure = generated["structure"]
        structure["packages"][0]["items"][0]["owner_role"] = "经营"
        confirm_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/format-plan/{generated['plan_uuid']}/confirm",
            headers=headers,
            json={
                "structure": structure,
                "reviewer_note": "格式已确认",
                "edit_events": [
                    {
                        "event_type": "move_item",
                        "item_key": structure["packages"][0]["items"][0]["item_key"],
                        "item_title": structure["packages"][0]["items"][0]["item_title"],
                        "from_package_key": "technical",
                        "to_package_key": "business",
                        "detail": {"note": "人工移动目录项", "owner_role": "经营"},
                    },
                    {
                        "event_type": "remove_item",
                        "item_key": "technical:duplicate",
                        "item_title": "重复目录项",
                        "from_package_key": "technical",
                        "detail": {"note": "人工删除重复目录项"},
                    },
                    {"event_type": "invalid_event", "item_title": "非法事件会被忽略"},
                ],
            },
        )
        assert confirm_response.status_code == 200, confirm_response.text
        confirmed = confirm_response.json()["data"]
        assert confirmed["review_status"] == "confirmed"
        assert confirmed["reviewer_note"] == "格式已确认"
        assert confirmed["confirmed_at"]
        assert len(confirmed["edit_events"]) == 2
        assert {event["event_type"] for event in confirmed["edit_events"]} == {"move_item", "remove_item"}
        assert db.query(BidFileFormatPlanEvent).filter(BidFileFormatPlanEvent.plan_id == db.query(BidFileFormatPlan).filter(BidFileFormatPlan.parse_run_id == split_run.id).first().id).count() == 2

        unified_project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="统一投标文件测试项目",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({}),
        )
        db.add(unified_project)
        db.flush()
        db.add(
            BidProjectFile(
                file_uuid=str(uuid.uuid4()),
                project_id=unified_project.id,
                file_type="tender_document",
                original_filename="统一格式招标文件.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=1200,
                sha256="unified-format",
                parser_status="parsed",
                parser_version="test",
                extracted_text=(
                    "第三章 格式\n"
                    "9.投标书构成\n"
                    "投标单位编写的投标书应包括下列部分：投标函格式、投标报价一览表、资格证明文件、投标保证金、技术规格偏离表格式、商务条款偏离表格式。"
                ),
                segments_json=dumps_json([]),
                page_count=1,
                section_count=1,
                uploaded_by=user.id,
            )
        )
        unified_run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=unified_project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(unified_run)
        db.commit()

        unified_response = client.post(
            f"/api/v1/admin/bidding/projects/{unified_project.project_uuid}/bid-draft/format-plan/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert unified_response.status_code == 200, unified_response.text
        unified = unified_response.json()["data"]
        assert unified["package_mode"] == "unified_bid_file"
        assert unified["structure"]["packages"][0]["package_key"] == "unified"
        assert any(item["item_title"] == "商务条款偏离表" for item in unified["structure"]["packages"][0]["items"])
        assert any(warning["code"] == "UNIFIED_BID_FILE" for warning in unified["warnings"])
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)


def test_bidding_bid_draft_outline_generates_from_confirmed_file_format_plan(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="格式表驱动目录骨架测试",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({}),
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()
        technical_requirement = TenderRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            requirement_type="technical",
            source_file="招标文件.docx",
            source_location="7.3",
            original_text="技术标应包括施工组织设计、项目管理机构、质量安全和施工进度计划。",
            parsed_requirement="技术标需编制施工组织设计，并说明项目管理机构、质量安全和施工进度安排。",
            risk_level="medium",
            owner_role="技术",
            output_section="技术标",
            confidence=0.9,
            extraction_method="test",
        )
        technical_risk = TenderRisk(
            risk_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            requirement=technical_requirement,
            risk_type="technical_schedule_quality",
            risk_level="high",
            source_file="招标文件.docx",
            source_location="7.3",
            original_text="技术标施工组织设计需覆盖质量、安全和进度计划，缺失可能影响评分。",
            risk_explanation="施工组织设计缺失质量、安全或进度计划会影响技术标评分。",
            impact_area="技术标",
            suggested_action="技术负责人补齐施工组织、质量安全和进度计划内容。",
            is_blocking=False,
            confidence=0.88,
            extraction_method="test",
        )
        db.add(technical_requirement)
        db.add(technical_risk)
        db.flush()
        db.add(
            TenderResponseItem(
                response_item_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                requirement_id=technical_requirement.id,
                risk_id=technical_risk.id,
                source_key="format-outline:technical-plan",
                response_category="technical_requirement",
                response_action="document_preparation",
                response_title="技术标：施工组织设计与管理人员配置",
                source_text="施工组织设计应包含项目组织架构、质量安全措施和施工进度计划。",
                evidence_json=dumps_json(
                    [
                        {
                            "source_file": "招标文件.docx",
                            "source_location": "7.3",
                            "original_text": "技术标应包括施工组织设计、项目管理机构、质量安全和施工进度计划。",
                        }
                    ]
                ),
                owner_role="技术",
                risk_level="high",
                status="pending",
                response_note="技术负责人需编制施工组织设计正文。",
                created_from="test",
                normalized_json=dumps_json(
                    {
                        "review_action": "write_technical_document",
                        "review_priority": "P1",
                        "review_wave": "wave_1",
                        "coverage": {"requirement_ids": [technical_requirement.id], "risk_ids": [technical_risk.id], "evidence_count": 1},
                        "done_checklist": ["施工组织设计、项目管理机构、质量安全和进度计划均已覆盖"],
                    }
                ),
                created_by=user.id,
            )
        )
        db.flush()
        structure = {
            "format_version": "test",
            "format_source": "embedded_in_tender",
            "format_source_label": "招标文件内嵌格式",
            "package_mode": "separate_business_technical",
            "package_mode_label": "商务标/技术标分册",
            "packages": [
                {
                    "package_key": "business",
                    "package_title": "商务标",
                    "package_type": "business_bid",
                    "description": "商务报价、承诺、固定格式表单",
                    "items": [
                        {
                            "item_key": "business:bid_letter",
                            "base_item_key": "bid_letter",
                            "item_title": "投标函/投标书",
                            "package_key": "business",
                            "content_type": "fixed_form",
                            "content_type_label": "固定表单",
                            "owner_role": "经营",
                            "generation_strategy": "manual_fill",
                            "requires_signature": True,
                            "requires_attachment": False,
                            "order_index": 1,
                            "evidence": [
                                {
                                    "source_file": "招标文件.docx",
                                    "source_location": "7.2",
                                    "original_text": "商务标应包括投标函、投标报价表等格式。",
                                    "source_kind": "file_segment",
                                }
                            ],
                        },
                        {
                            "item_key": "business:boq",
                            "base_item_key": "boq",
                            "item_title": "工程量清单报价表",
                            "package_key": "business",
                            "content_type": "pricing_table",
                            "content_type_label": "报价表",
                            "owner_role": "预算",
                            "generation_strategy": "from_cost_quote",
                            "requires_signature": False,
                            "requires_attachment": False,
                            "order_index": 2,
                            "evidence": [
                                {
                                    "source_file": "招标文件.docx",
                                    "source_location": "7.2",
                                    "original_text": "商务标应包括工程量清单报价表。",
                                    "source_kind": "file_segment",
                                }
                            ],
                        },
                    ],
                },
                {
                    "package_key": "technical",
                    "package_title": "技术标",
                    "package_type": "technical_bid",
                    "description": "施工组织设计、进度质量安全和材料方案",
                    "items": [
                        {
                            "item_key": "technical:construction_plan",
                            "base_item_key": "construction_plan",
                            "item_title": "施工组织设计/施工方案",
                            "package_key": "technical",
                            "content_type": "draft_section",
                            "content_type_label": "正文章节",
                            "owner_role": "技术",
                            "generation_strategy": "generate_draft",
                            "requires_signature": False,
                            "requires_attachment": False,
                            "order_index": 1,
                            "evidence": [
                                {
                                    "source_file": "招标文件.docx",
                                    "source_location": "7.3",
                                    "original_text": "技术标应包括施工组织设计、质量、安全、进度计划。",
                                    "source_kind": "file_segment",
                                }
                            ],
                        }
                    ],
                },
            ],
            "packaging_requirements": [],
        }
        db.add(
            BidFileFormatPlan(
                plan_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                created_by=user.id,
                format_version="test",
                format_source="embedded_in_tender",
                package_mode="separate_business_technical",
                review_status="confirmed",
                structure_json=dumps_json(structure),
                summary_json=dumps_json(
                    {
                        "format_source": "embedded_in_tender",
                        "package_mode": "separate_business_technical",
                        "package_count": 2,
                        "item_count": 3,
                        "fixed_form_count": 1,
                        "draft_section_count": 1,
                        "pricing_table_count": 1,
                        "attachment_count": 0,
                    }
                ),
                warnings_json=dumps_json([]),
                confirmed_by=user.id,
                confirmed_at=datetime.now(),
            )
        )
        db.commit()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        outline = response.json()["data"]
        assert outline["source"]["source_type"] == "file_format_plan"
        assert outline["source"]["format_plan_review_status"] == "confirmed"
        assert outline["summary"]["outline_source"] == "file_format_plan"
        assert outline["summary"]["format_package_count"] == 2
        assert outline["summary"]["format_item_count"] == 3
        assert outline["summary"]["mapped_format_item_count"] == 1
        assert outline["summary"]["linked_response_item_count"] == 1
        assert outline["summary"]["linked_requirement_count"] == 1
        assert outline["summary"]["linked_risk_count"] == 1
        assert not any(warning["code"] == "NO_RESPONSE_MATRIX" for warning in outline["warnings"])
        assert any(warning["code"] == "FORMAT_ITEM_MAPPING_INCOMPLETE" for warning in outline["warnings"])

        sections = outline["sections"]
        leaf_sections = [section for section in sections if section["level"] == 2]
        assert {section["package_title"] for section in leaf_sections} == {"商务标", "技术标"}
        technical = next(section for section in leaf_sections if section["section_title"] == "施工组织设计/施工方案")
        assert technical["section_type"] == "technical"
        assert technical["draft_mode"] == "formal"
        assert technical["llm_eligible"] is True
        assert technical["generation_decision"]["code"] == "format_draft_section"
        assert technical["content_type"] == "draft_section"
        assert technical["response_item_count"] == 1
        assert technical["requirement_count"] == 1
        assert technical["risk_count"] == 1
        assert technical["source_mapping"]["status"] == "mapped"
        assert technical["source_mapping"]["confidence"] in {"medium", "high"}
        assert technical["source_mapping"]["response_item_matches"][0]["response_title"] == "技术标：施工组织设计与管理人员配置"
        pricing = next(section for section in leaf_sections if section["section_title"] == "工程量清单报价表")
        assert pricing["section_type"] == "pricing"
        assert pricing["draft_mode"] == "placeholder"
        assert pricing["generation_decision"]["code"] == "from_cost_quote"
        business_form = next(section for section in leaf_sections if section["section_title"] == "投标函/投标书")
        assert business_form["section_type"] == "business"
        assert business_form["draft_mode"] == "placeholder"
        assert "固定格式" in "；".join(business_form["missing_inputs"])

        draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": technical["section_key"]},
        )
        assert draft_response.status_code == 200, draft_response.text
        draft = draft_response.json()["data"]
        assert draft["section_title"] == "施工组织设计/施工方案"
        assert draft["draft_mode"] == "formal"
        assert draft["quality_profile"]["quality_status"] == "ready_for_draft"
        assert draft["quality_profile"]["llm_allowed"] is True
        assert draft["writing_plan"]["target_output"] == "formal_draft"
        assert draft["quality_result"]["status"] == "pass"
        assert draft["generation_decision"]["quality_profile"]["quality_status"] == "ready_for_draft"
        assert draft["generation_decision"]["writing_plan"]["target_output"] == "formal_draft"
        assert "## 章节质量画像" in draft["content_markdown"]
        assert "## 写作计划" in draft["content_markdown"]
        assert "## 规则质检结果" in draft["content_markdown"]
        assert "目录来源：投标文件格式确认表 / 技术标" in draft["content_markdown"]
        assert "映射结果：命中 1 个响应矩阵项" in draft["content_markdown"]
        assert "技术标：施工组织设计与管理人员配置" in draft["content_markdown"]
        assert "关联要求：1 条" in draft["content_markdown"]
        assert "关联风险：1 条" in draft["content_markdown"]
        assert "## 企业能力/施工经验参考模板" in draft["content_markdown"]
        assert any(item["source_location"] == "7.3" for item in draft["evidence"])
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)


def test_bidding_bid_draft_outline_generates_from_response_matrix(client, monkeypatch):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_deepseek_api_key = _set_flag("deepseek_api_key", "test-deepseek-key")
    db = SessionLocal()
    captured_llm_payload: dict[str, object] = {}
    llm_markdown = {
        "content": (
            "# 评标办法\n\n"
            "## 响应立场\n"
            "我方已充分理解招标文件关于评标办法、技术标评分、商务标评分和报价评分的要求，"
            "并将在投标文件中逐项响应。\n\n"
            "## 具体响应\n"
            "- 按招标文件要求组织商务、技术及报价文件内容。\n"
            "- 保留评标办法相关复核结论，提交前由经营负责人确认。\n\n"
            "## 来源依据\n"
            "- 评标办法包含技术标评分、商务标评分和报价评分。"
        )
    }

    class _FakeLlmResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "content_markdown": llm_markdown["content"]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    async def _fake_post_json_via_gateway(**kwargs):
        captured_llm_payload["payload"] = kwargs.get("json_payload")
        captured_llm_payload["provider"] = kwargs.get("provider")
        captured_llm_payload["model"] = kwargs.get("model")
        return _FakeLlmResponse()

    monkeypatch.setattr("app.services.bidding_draft_sections.post_json_via_gateway", _fake_post_json_via_gateway)
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="投标书目录骨架测试",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({}),
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()
        rows = [
            {
                "title": "评标办法",
                "category": "bid_rule",
                "action": "direct_response",
                "owner_role": "经营",
                "risk_level": "medium",
                "status": "done",
                "source_text": "评标办法包含技术标评分、商务标评分和报价评分。",
                "normalized": {
                    "review_action": "confirm_response",
                    "review_priority": "P0",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [11], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["评标办法已在商务标响应中确认"],
                },
            },
            {
                "title": "投标截止时间",
                "category": "bid_rule",
                "action": "direct_response",
                "owner_role": "经营",
                "risk_level": "high",
                "source_text": "投标截止时间为招标文件规定时间，逾期递交将不予受理。",
                "normalized": {
                    "review_action": "confirm_response",
                    "review_priority": "P0",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [12], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["投标截止时间已记录并同步到投标执行提醒"],
                },
            },
            {
                "title": "其他投标规则",
                "category": "bid_rule",
                "action": "direct_response",
                "owner_role": "经营",
                "risk_level": "high",
                "source_text": "投标文件正本、副本和电子文件应按招标文件要求分别密封并在封套处盖章。",
                "normalized": {
                    "review_action": "confirm_response",
                    "review_priority": "P0",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [13], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["密封方式和份数已记录到递交检查清单"],
                },
            },
            {
                "title": "综合响应事项",
                "category": "bid_rule",
                "action": "direct_response",
                "owner_role": "经营",
                "risk_level": "high",
                "source_text": "投标保证金可采用银行保函形式，保函有效期不得短于投标有效期。",
                "normalized": {
                    "review_action": "confirm_response",
                    "review_priority": "P0",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [14], "risk_ids": [132], "evidence_count": 1},
                    "done_checklist": ["保证金和保函形式需经营确认"],
                },
            },
            {
                "title": "其他商务要求",
                "category": "bid_rule",
                "action": "direct_response",
                "owner_role": "经营",
                "risk_level": "medium",
                "source_text": "投标人应充分理解招标文件中其他商务要求并按要求执行。",
                "normalized": {
                    "review_action": "confirm_response",
                    "review_priority": "P2",
                    "review_wave": "wave_2",
                    "coverage": {"requirement_ids": [15], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["其他商务要求需人工确认归属"],
                },
            },
            {
                "title": "技术标：施工组织设计与管理人员配置",
                "category": "technical_requirement",
                "action": "document_preparation",
                "owner_role": "技术",
                "risk_level": "low",
                "normalized": {
                    "review_action": "write_technical_document",
                    "review_priority": "P2",
                    "review_wave": "wave_2",
                    "coverage": {"requirement_ids": [101], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["施工组织设计已写入技术标", "项目班子材料已绑定"],
                },
            },
            {
                "title": "响应/偏离表一致性检查",
                "category": "document_checklist",
                "action": "document_preparation",
                "owner_role": "经营",
                "risk_level": "medium",
                "source_text": "技术标、商务标和报价文件的响应表、偏离表需保持一致。",
                "normalized": {
                    "review_action": "write_business_document",
                    "review_priority": "P2",
                    "review_wave": "wave_2",
                    "coverage": {"requirement_ids": [121], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["响应表和偏离表已完成一致性检查"],
                },
            },
            {
                "title": "投标保证金风险决策",
                "category": "bid_rule",
                "action": "direct_response",
                "owner_role": "经营",
                "risk_level": "high",
                "source_text": "投标保证金需按招标文件要求提交，形式和截止时间需经营负责人确认。",
                "normalized": {
                    "review_action": "confirm_response",
                    "review_priority": "P0",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [131], "risk_ids": [132], "evidence_count": 1},
                    "done_checklist": ["保证金形式、金额和截止时间需经营确认"],
                },
            },
            {
                "title": "固定总价与漏项责任报价预留",
                "category": "pricing_constraint",
                "action": "quote_allowance",
                "owner_role": "预算",
                "risk_level": "high",
                "normalized": {
                    "review_action": "budget_assessment",
                    "review_priority": "P1",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [201], "risk_ids": [301], "evidence_count": 1},
                    "done_checklist": ["报价口径和漏项责任已完成预算复核"],
                },
            },
            {
                "title": "合同违约责任法务复核",
                "category": "contract_clause",
                "action": "legal_review",
                "owner_role": "法务",
                "risk_level": "high",
                "normalized": {
                    "review_action": "legal_assessment",
                    "review_priority": "P0",
                    "review_wave": "wave_1",
                    "coverage": {"requirement_ids": [], "risk_ids": [401], "evidence_count": 1},
                    "done_checklist": ["违约责任边界已由法务确认"],
                },
            },
            {
                "title": "准备企业资质证明",
                "category": "qualification",
                "action": "qualification_material",
                "owner_role": "经营",
                "risk_level": "low",
                "normalized": {
                    "review_action": "prepare_qualification",
                    "review_priority": "P2",
                    "review_wave": "wave_2",
                    "coverage": {"requirement_ids": [501], "risk_ids": [], "evidence_count": 1},
                    "done_checklist": ["资质证书和人员证照已绑定"],
                },
            },
        ]
        for index, row in enumerate(rows, start=1):
            db.add(
                TenderResponseItem(
                    response_item_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    source_key=f"outline:{index}",
                    response_category=row["category"],
                    response_action=row["action"],
                    response_title=row["title"],
                    source_text=row.get("source_text") or f"{row['title']} 原文证据",
                    evidence_json=dumps_json(
                        [
                            {
                                "source_file": "tender.txt",
                                "source_location": f"P{index}",
                                "original_text": f"{row['title']} 原文证据",
                            }
                        ]
                    ),
                    owner_role=row["owner_role"],
                    risk_level=row["risk_level"],
                    status=row.get("status", "pending"),
                    response_note=f"{row['title']} 复核说明",
                    created_from="test",
                    normalized_json=dumps_json(row["normalized"]),
                    created_by=user.id,
                )
            )
        db.commit()

        preview_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline",
            headers=headers,
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()["data"]
        assert preview["outline_version"] == "biz4b_bid_draft_outline_v1.3"
        assert preview["summary"]["response_item_count"] == 11
        assert preview["summary"]["parent_section_count"] == 7

        generate_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert generate_response.status_code == 200, generate_response.text
        outline = generate_response.json()["data"]
        sections = outline["sections"]
        leaf_sections = [section for section in sections if section["level"] == 2]
        leaf_types = {section["section_type"] for section in leaf_sections}
        assert {"technical", "pricing", "legal", "qualification"}.issubset(leaf_types)
        assert outline["summary"]["response_item_count"] == 11
        assert outline["summary"]["generic_split_section_count"] >= 3
        assert outline["summary"]["secondary_split_needed_count"] >= 1
        assert outline["summary"]["by_split_family"]["submission_seal"] >= 1
        assert outline["summary"]["by_split_family"]["bid_guarantee"] >= 1
        bid_rule_sections = [section for section in leaf_sections if section["section_title"] == "评标办法"]
        assert bid_rule_sections
        assert bid_rule_sections[0]["section_type"] == "business"
        assert bid_rule_sections[0]["draft_status"] == "ready"
        assert bid_rule_sections[0]["draft_mode"] == "formal"
        assert bid_rule_sections[0]["generation_decision"]["code"] == "direct_response"
        assert bid_rule_sections[0]["llm_eligible"] is True
        assert bid_rule_sections[0]["can_generate_formal_draft"] is True
        deadline_sections = [section for section in leaf_sections if section["section_title"] == "投标截止时间"]
        assert deadline_sections
        assert deadline_sections[0]["section_type"] == "business"
        assert deadline_sections[0]["draft_status"] == "ready"
        assert deadline_sections[0]["draft_mode"] == "formal"
        assert deadline_sections[0]["generation_decision"]["code"] == "compliance_reminder"
        assert deadline_sections[0]["generation_decision"]["label"] == "硬性合规提醒"
        assert deadline_sections[0]["llm_eligible"] is True
        seal_split_sections = [section for section in leaf_sections if section["section_title"] == "递交方式与密封要求"]
        assert seal_split_sections
        assert seal_split_sections[0]["split_from_generic_title"] is True
        assert seal_split_sections[0]["original_group_title"] == "其他投标规则"
        assert seal_split_sections[0]["split_family"] == "submission_seal"
        assert seal_split_sections[0]["needs_secondary_split"] is False
        guarantee_split_sections = [section for section in leaf_sections if section["section_title"] == "投标保证金与保函"]
        assert guarantee_split_sections
        assert guarantee_split_sections[0]["split_from_generic_title"] is True
        assert guarantee_split_sections[0]["split_family"] == "bid_guarantee"
        assert guarantee_split_sections[0]["generation_decision"]["code"] == "risk_decision"
        unresolved_generic_sections = [section for section in leaf_sections if section["section_title"] == "其他商务要求（待二次拆分）"]
        assert unresolved_generic_sections
        assert unresolved_generic_sections[0]["needs_secondary_split"] is True
        assert unresolved_generic_sections[0]["split_family"] == "generic_unresolved"
        business_decision_sections = [section for section in leaf_sections if section["section_title"] == "投标保证金风险决策"]
        assert business_decision_sections
        assert business_decision_sections[0]["section_type"] == "business"
        assert business_decision_sections[0]["draft_status"] == "needs_input"
        assert business_decision_sections[0]["draft_mode"] == "review_note"
        assert business_decision_sections[0]["generation_decision"]["code"] == "risk_decision"
        assert business_decision_sections[0]["llm_eligible"] is False
        response_table_sections = [section for section in leaf_sections if section["section_title"] == "响应/偏离表一致性检查"]
        assert response_table_sections
        assert response_table_sections[0]["section_type"] == "business"
        assert response_table_sections[0]["generation_decision"]["code"] == "needs_input"
        assert response_table_sections[0]["draft_mode"] == "placeholder"
        assert any(section["owner_role"] == "技术" and section["section_type"] == "technical" for section in leaf_sections)
        assert any(section["owner_role"] == "预算" and section["section_type"] == "pricing" for section in leaf_sections)
        technical_sections = [section for section in leaf_sections if section["section_type"] == "technical"]
        assert any(section["draft_mode"] == "placeholder" for section in technical_sections)
        legal_sections = [section for section in leaf_sections if section["section_type"] == "legal"]
        assert legal_sections
        assert legal_sections[0]["draft_status"] == "blocked"
        assert legal_sections[0]["can_generate_draft"] is False
        assert legal_sections[0]["draft_mode"] == "blocked"
        assert legal_sections[0]["risk_count"] == 1
        assert legal_sections[0]["risk_warnings"]
        assert outline["summary"]["blocked_section_count"] >= 1
        assert outline["summary"]["by_section_type"]["technical"] >= 1
        assert outline["summary"]["formal_draft_ready_count"] >= 1
        assert outline["summary"]["placeholder_draft_count"] >= 1

        business_draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": bid_rule_sections[0]["section_key"]},
        )
        assert business_draft_response.status_code == 200, business_draft_response.text
        business_draft = business_draft_response.json()["data"]
        assert business_draft["section_title"] == "评标办法"
        assert business_draft["draft_mode"] == "formal"
        assert business_draft["review_status"] == "draft"
        assert business_draft["content_version"] == 1
        assert business_draft["versions"][0]["change_type"] == "generated"
        assert business_draft["generation_decision"]["code"] == "direct_response"
        assert business_draft["quality_profile"]["quality_status"] == "ready_for_draft"
        assert business_draft["writing_plan"]["target_output"] == "formal_draft"
        assert business_draft["quality_result"]["status"] in {"pass", "needs_review"}
        assert business_draft["llm_entry"]["eligible"] is True
        assert business_draft["llm_entry"]["prompt_version"] == "biz4b_single_section_draft_llm_v3"
        assert business_draft["llm_eligible"] is True
        assert business_draft["content_evidence"]["version"] == "biz4b_content_evidence_v1"
        assert business_draft["content_evidence"]["status"] in {"pass", "needs_review"}
        assert business_draft["content_evidence"]["coverage_summary"]["required_count"] >= 1
        assert business_draft["content_evidence"]["blocks"]
        assert business_draft["acceptance_check"]["status"] in {"pass", "needs_review"}
        assert business_draft["acceptance_check"]["can_accept"] is True
        assert "## 章节质量画像" in business_draft["content_markdown"]
        assert "## 写作计划" in business_draft["content_markdown"]
        assert "## 规则质检结果" in business_draft["content_markdown"]
        assert "## 响应立场" in business_draft["content_markdown"]
        assert business_draft["placeholders"] == []

        business_decision_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": business_decision_sections[0]["section_key"]},
        )
        assert business_decision_response.status_code == 200, business_decision_response.text
        business_decision_draft = business_decision_response.json()["data"]
        assert business_decision_draft["draft_mode"] == "review_note"
        assert business_decision_draft["llm_eligible"] is False
        assert business_decision_draft["llm_entry"]["eligible"] is False
        assert business_decision_draft["llm_entry"]["blocked_reasons"]
        assert "当前生成复核说明" in business_decision_draft["content_markdown"]

        technical_section = next(section for section in technical_sections if section["draft_mode"] == "placeholder")
        technical_draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": technical_section["section_key"]},
        )
        assert technical_draft_response.status_code == 200, technical_draft_response.text
        technical_draft = technical_draft_response.json()["data"]
        assert technical_draft["section_type"] == "technical"
        assert technical_draft["draft_mode"] == "placeholder"
        assert technical_draft["quality_profile"]["quality_status"] == "needs_material"
        assert technical_draft["writing_plan"]["target_output"] == "placeholder_draft"
        assert technical_draft["quality_result"]["status"] == "needs_material"
        assert technical_draft["llm_entry"]["eligible"] is False
        assert technical_draft["llm_entry"]["blocked_reasons"]
        assert technical_draft["placeholders"]
        assert "【待补充：" in technical_draft["content_markdown"]
        assert "## 企业能力/施工经验参考模板" in technical_draft["content_markdown"]
        assert "## 来源依据" in technical_draft["content_markdown"]

        legacy_technical_draft = db.query(BidDraftSection).filter(BidDraftSection.draft_uuid == technical_draft["draft_uuid"]).first()
        assert legacy_technical_draft
        legacy_technical_draft.generator_model = "rule_section_draft_v1"
        legacy_technical_draft.generation_decision_json = None
        legacy_technical_draft.content_markdown = "# 旧技术方案草稿\n\n旧模板正文。"
        db.commit()

        legal_draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": legal_sections[0]["section_key"]},
        )
        assert legal_draft_response.status_code == 200, legal_draft_response.text
        legal_draft = legal_draft_response.json()["data"]
        assert legal_draft["section_type"] == "legal"
        assert legal_draft["draft_mode"] == "blocked"
        assert "当前不生成可提交正文" in legal_draft["content_markdown"]
        assert legal_draft["warnings"]

        llm_blocked_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": legal_sections[0]["section_key"], "generator_type": "llm"},
        )
        assert llm_blocked_response.status_code == 422
        assert llm_blocked_response.json()["detail"] == "BID_DRAFT_SECTION_LLM_NOT_ALLOWED"

        edit_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/sections/{business_draft['draft_uuid']}/content",
            headers=headers,
            json={
                "content_markdown": business_draft["content_markdown"] + "\n\n人工补充：商务负责人已复核。",
                "editor_note": "补充复核说明",
            },
        )
        assert edit_response.status_code == 200, edit_response.text
        edited = edit_response.json()["data"]
        assert edited["content_version"] == 2
        assert edited["versions"][-1]["change_type"] == "manual_edit"
        assert "人工补充：商务负责人已复核。" in edited["content_markdown"]
        assert edited["content_evidence"]["review_source"] == "manual_edit"
        assert edited["content_evidence"]["status"] in {"pass", "needs_review"}
        assert edited["generation_decision"]["manual_edit_review"]["status"] == edited["acceptance_check"]["status"]
        assert edited["acceptance_check"]["can_accept"] is True

        list_drafts_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections",
            headers=headers,
        )
        assert list_drafts_response.status_code == 200, list_drafts_response.text
        list_drafts_payload = list_drafts_response.json()
        assert list_drafts_payload["total"] == 4
        legacy_technical = next(item for item in list_drafts_payload["data"] if item["draft_uuid"] == technical_draft["draft_uuid"])
        assert legacy_technical["needs_upgrade"] is True
        upgrade_codes = {item["code"] for item in legacy_technical["upgrade_hint"]["reasons"]}
        assert {"old_rule_template", "missing_generation_decision", "missing_technical_template"}.issubset(upgrade_codes)

        review_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/sections/{business_draft['draft_uuid']}/review",
            headers=headers,
            json={"review_status": "accepted", "reviewer_note": "商务规则章节草稿可用。"},
        )
        assert review_response.status_code == 200, review_response.text
        reviewed = review_response.json()["data"]
        assert reviewed["review_status"] == "accepted"
        assert reviewed["reviewer_note"] == "商务规则章节草稿可用。"

        llm_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": bid_rule_sections[0]["section_key"], "generator_type": "llm"},
        )
        assert llm_response.status_code == 200, llm_response.text
        llm_draft = llm_response.json()["data"]
        assert captured_llm_payload["provider"] == "deepseek"
        assert captured_llm_payload["model"] == settings.bidding_llm_model
        user_message = next(
            item
            for item in captured_llm_payload["payload"]["messages"]
            if item.get("role") == "user"
        )
        user_payload = json.loads(user_message["content"])
        assert user_payload["prompt_version"] == "biz4b_single_section_draft_llm_v3"
        assert "document_anti_repetition" in user_payload
        assert user_payload["quality_profile"]["quality_status"] == "ready_for_draft"
        assert user_payload["writing_plan"]["target_output"] == "formal_draft"
        assert user_payload["quality_result"]["status"] in {"pass", "needs_review"}
        assert llm_draft["generator_type"] == "llm"
        assert llm_draft["generator_model"] == settings.bidding_llm_model
        assert llm_draft["review_status"] == "draft"
        assert llm_draft["reviewer_note"] is None
        assert llm_draft["content_version"] == reviewed["content_version"] + 1
        assert llm_draft["versions"][-1]["change_type"] == "llm_generated"
        assert llm_draft["quality_result"]["generator_type"] == "llm"
        assert llm_draft["quality_result"]["status"] in {"pass", "needs_review"}
        assert llm_draft["generation_decision"]["llm_enhancement"]["prompt_version"] == "biz4b_single_section_draft_llm_v3"
        assert llm_draft["diff_summary"]["base_version_no"] == 1
        assert llm_draft["diff_summary"]["added_line_count"] >= 1
        assert llm_draft["diff_summary"]["risk_removed"] is False
        assert llm_draft["semantic_quality"]["status"] in {"pass", "needs_review"}
        assert llm_draft["content_evidence"]["review_source"] == "llm_generated"
        assert llm_draft["content_evidence"]["status"] in {"pass", "needs_review"}
        assert llm_draft["content_evidence"]["blocks"]
        assert llm_draft["acceptance_check"]["status"] in {"pass", "needs_review"}
        assert llm_draft["acceptance_check"]["can_accept"] is True
        assert "## 规则质检结果" in llm_draft["content_markdown"]

        llm_markdown["content"] = (
            "# 评标办法\n\n"
            "## 响应承诺\n"
            "我方保证中标，并承诺无条件接受全部风险。\n"
        )
        bad_llm_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={"run_uuid": "latest", "section_key": bid_rule_sections[0]["section_key"], "generator_type": "llm"},
        )
        assert bad_llm_response.status_code == 200, bad_llm_response.text
        bad_llm_draft = bad_llm_response.json()["data"]
        assert bad_llm_draft["semantic_quality"]["status"] == "blocked"
        assert bad_llm_draft["content_evidence"]["status"] == "blocked"
        assert bad_llm_draft["content_evidence"]["unsupported_blocks"]
        assert bad_llm_draft["acceptance_check"]["status"] == "blocked"
        assert bad_llm_draft["acceptance_check"]["can_accept"] is False
        accept_bad_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/sections/{bad_llm_draft['draft_uuid']}/review",
            headers=headers,
            json={"review_status": "accepted", "reviewer_note": "尝试接受坏稿。"},
        )
        assert accept_bad_response.status_code == 422
        assert accept_bad_response.json()["detail"] == "BID_DRAFT_SECTION_ACCEPTANCE_BLOCKED"
        fixed_content = (
            "# 评标办法\n\n"
            "## 响应立场\n"
            "我方已充分理解招标文件关于评标办法、技术标评分、商务标评分和报价评分的要求，并将在投标文件中逐项响应。\n\n"
            "## 具体响应\n"
            "- 按招标文件要求组织商务、技术及报价文件内容。\n"
            "- 提交前由经营负责人对评标办法响应内容进行复核确认。\n\n"
            "## 来源依据\n"
            "- 评标办法包含技术标评分、商务标评分和报价评分。"
        )
        fix_bad_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/sections/{bad_llm_draft['draft_uuid']}/content",
            headers=headers,
            json={"content_markdown": fixed_content, "editor_note": "人工修正无依据承诺。"},
        )
        assert fix_bad_response.status_code == 200, fix_bad_response.text
        fixed_bad_draft = fix_bad_response.json()["data"]
        assert fixed_bad_draft["versions"][-1]["change_type"] == "manual_edit"
        assert fixed_bad_draft["content_evidence"]["review_source"] == "manual_edit"
        assert fixed_bad_draft["content_evidence"]["status"] in {"pass", "needs_review"}
        assert fixed_bad_draft["acceptance_check"]["status"] in {"pass", "needs_review"}
        assert fixed_bad_draft["acceptance_check"]["can_accept"] is True
        accept_fixed_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/sections/{fixed_bad_draft['draft_uuid']}/review",
            headers=headers,
            json={"review_status": "accepted", "reviewer_note": "人工修正后接受。"},
        )
        assert accept_fixed_response.status_code == 200, accept_fixed_response.text
        assert db.query(BidDraftSection).filter(BidDraftSection.parse_run_id == run.id).count() == 4
        assert db.query(BidDraftSectionVersion).count() >= 8
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("deepseek_api_key", old_deepseek_api_key)


def test_bidding_response_matrix_clusters_technical_requirements_and_explains_coverage(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="响应矩阵技术聚类测试",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({}),
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()
        technical_rows = [
            ("7.3.10 针对本工程的施工组织设计，主要包括组织架构、项目经理及主要管理人员简历。", "technical", "技术标"),
            ("所有材料进场须提供材料验收报告，环保方面检测资料齐全。", "technical", "技术标"),
            ("装饰施工单位须严格落实成品保护，并配合其他分包单位保护已完工程。", "technical", "技术标"),
            ("安全文明施工特别是防火安全和临时用电须编制专题方案。", "technical", "技术标"),
        ]
        requirements: list[TenderRequirement] = []
        for original_text, requirement_type, output_section in technical_rows:
            requirement = TenderRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                requirement_type=requirement_type,
                source_file="tender.txt",
                source_location="T1",
                original_text=original_text,
                parsed_requirement="识别到技术质量要求，请在后续响应矩阵中确认是否满足并绑定证明材料。",
                compliance_status="pending",
                risk_level="low",
                owner_role="技术",
                output_section=output_section,
                confidence=0.8,
                extraction_method="test",
                status="active",
            )
            db.add(requirement)
            requirements.append(requirement)
        db.flush()
        legacy_requirement = requirements[0]
        db.add(
            TenderResponseItem(
                response_item_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                requirement_id=legacy_requirement.id,
                source_key=f"req:{legacy_requirement.requirement_uuid}",
                response_category="requirement",
                response_action="document_preparation",
                response_title="识别到技术质量要求，请在后续响应矩阵中确认是否满足并绑定证明材料。",
                source_text=legacy_requirement.original_text,
                evidence_json=dumps_json([]),
                owner_role="技术",
                risk_level="low",
                status="pending",
                response_note="技术标",
                created_from="requirement",
                normalized_json=dumps_json(
                    {
                        "source": "requirement",
                        "requirement_uuid": legacy_requirement.requirement_uuid,
                        "requirement_type": "technical",
                    }
                ),
                created_by=user.id,
            )
        )
        db.commit()

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["created_count"] == 4
        assert payload["superseded_count"] == 1
        assert payload["total_count"] == 4
        assert payload["stored_total_count"] == 5
        assert payload["summary"]["clustered_requirement_count"] == 4
        assert payload["summary"]["covered_requirement_count"] == 4

        list_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
        )
        assert list_response.status_code == 200, list_response.text
        rows = list_response.json()["data"]
        titles = {item["response_title"] for item in rows}
        assert "技术标：施工组织设计与管理人员配置" in titles
        assert "技术标：材料品质、环保检测与进场验收" in titles
        assert "技术标：成品保护与交叉施工保护" in titles
        assert "技术标：安全文明施工与防火临电管理" in titles
        assert all(item["created_from"] == "requirement_cluster" for item in rows)
        assert all(item["coverage_explanation"] for item in rows)
        assert all(item["linked_actions"] for item in rows)
        assert all(item["primary_review_role"] == "技术" for item in rows)
        assert all("经营" in item["supporting_roles"] for item in rows)
        assert all(item["review_action"] == "write_technical_document" for item in rows)

        with_superseded = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
            params={"include_superseded": True, "page_size": 20},
        )
        assert with_superseded.status_code == 200, with_superseded.text
        assert len(with_superseded.json()["data"]) == 5
        assert any(item["superseded"] for item in with_superseded.json()["data"])
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)


def test_bidding_response_matrix_quality_rules_merge_split_and_role_views(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="响应矩阵质量规则测试",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({}),
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()

        duplicate_normalized = {
            "source": "business_object",
            "workflow_actions": [{"action": "direct_response", "owner_role": "经营", "reason": "主响应动作"}],
            "coverage": {"requirement_ids": [1], "risk_ids": [], "source_count": 1, "evidence_count": 0, "explanation": "测试覆盖。"},
            "coverage_explanation": "测试覆盖。",
        }
        for index in range(2):
            db.add(
                TenderResponseItem(
                    response_item_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    source_key=f"test:clarify_deadline:{index}",
                    response_category="bid_rule",
                    response_action="direct_response",
                    response_title="答疑/澄清截止",
                    source_text="投标人应在规定时间前提交答疑。",
                    evidence_json=dumps_json([]),
                    owner_role="经营",
                    risk_level="low",
                    status="pending",
                    response_note="确认是否需要提交答疑。",
                    created_from="business_object",
                    normalized_json=dumps_json(duplicate_normalized),
                    created_by=user.id,
                )
            )

        db.add(
            TenderResponseItem(
                response_item_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                source_key="test:overloaded_omission",
                response_category="pricing_constraint",
                response_action="document_preparation",
                response_title="表格漏项填报要求",
                source_text="报价清单、响应表和漏项责任相关条款需投标人完整响应。",
                evidence_json=dumps_json([]),
                owner_role="经营",
                risk_level="high",
                status="pending",
                response_note="同时涉及填表、报价预留、答疑和法务复核。",
                created_from="business_object",
                normalized_json=dumps_json(
                    {
                        "source": "business_object",
                        "object_subtype": "omission_liability",
                        "risk_types": ["omission_liability"],
                        "workflow_actions": [
                            {"action": "document_preparation", "owner_role": "经营", "reason": "文件编制"},
                            {"action": "direct_response", "owner_role": "经营", "reason": "直接响应"},
                            {"action": "clarification", "owner_role": "经营", "reason": "答疑判断"},
                            {"action": "quote_allowance", "owner_role": "预算", "reason": "报价预留"},
                            {"action": "legal_review", "owner_role": "法务", "reason": "合同责任"},
                        ],
                        "coverage": {
                            "requirement_ids": [10, 11, 12],
                            "risk_ids": list(range(100, 122)),
                            "source_count": 22,
                            "evidence_count": 0,
                            "explanation": "测试过载覆盖。",
                        },
                        "coverage_explanation": "测试过载覆盖。",
                    }
                ),
                created_by=user.id,
            )
        )
        db.commit()

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["quality_merged_count"] == 1
        assert payload["quality_split_parent_count"] >= 1
        assert payload["quality_split_child_count"] >= 3
        assert payload["quality_metadata_updated_count"] >= 1

        list_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
            params={"page_size": 20},
        )
        assert list_response.status_code == 200, list_response.text
        rows = list_response.json()["data"]
        titles = [item["response_title"] for item in rows]
        assert titles.count("答疑/澄清截止") == 1
        assert "表格漏项填报要求" not in titles
        assert any("漏项" in title or "表格" in title for title in titles)
        split_rows = [item for item in rows if "quality_split_child" in item["quality_flags"]]
        assert len(split_rows) >= 3
        assert all(item["quality_explanation"] for item in split_rows)
        assert all(item["primary_review_role"] for item in rows)
        assert all(item["review_action_label"] and item["done_criteria"] for item in rows)
        assert all(item["task_display_type"] and item["task_display_label"] for item in rows)
        assert all(item["review_priority"] and item["review_wave"] for item in rows)
        assert all(item["done_checklist"] for item in rows)
        assert list_response.json()["summary"]["split_item_count"] >= 3
        assert list_response.json()["summary"]["by_primary_review_role"]["预算"] >= 1
        assert list_response.json()["summary"]["by_primary_review_role"]["法务"] >= 1
        assert list_response.json()["summary"]["by_review_wave"]["wave_1"] >= 1
        assert list_response.json()["summary"]["by_task_display_type"]

        with_superseded = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
            params={"include_superseded": True, "page_size": 20},
        )
        assert with_superseded.status_code == 200, with_superseded.text
        superseded_titles = [item["response_title"] for item in with_superseded.json()["data"] if item["superseded"]]
        assert "表格漏项填报要求" in superseded_titles
        assert superseded_titles.count("答疑/澄清截止") == 1

        legal_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
            params={"review_role": "legal", "page_size": 20},
        )
        assert legal_response.status_code == 200, legal_response.text
        legal_titles = {item["response_title"] for item in legal_response.json()["data"]}
        assert any("法务" in title or "责任" in title for title in legal_titles)
        assert all(item["primary_review_role"] == "法务" for item in legal_response.json()["data"])

        budget_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/response-matrix",
            headers=headers,
            params={"review_role": "budget", "page_size": 20},
        )
        assert budget_response.status_code == 200, budget_response.text
        budget_titles = {item["response_title"] for item in budget_response.json()["data"]}
        assert any("报价" in title or "预算" in title for title in budget_titles)
        assert all(item["primary_review_role"] == "预算" for item in budget_response.json()["data"])

    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)


def test_bidding_llm_review_skips_without_deepseek_key(client):
    user = _create_user("staff")
    headers = _headers_for_user(client, user)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_llm = _set_flag("feature_bidding_llm_review", True)
    old_key = _set_flag("deepseek_api_key", "")
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="LLM review no key",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime.now(),
        )
        db.add(run)
        db.flush()
        db.add(
            TenderBusinessObject(
                object_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                object_type="contract_clause",
                object_subtype="payment_clause",
                title="payment",
                normalized_json=dumps_json({"needs_llm_review": True, "business_action": "legal_review"}),
                source_file="tender.txt",
                source_location="P1",
                original_text="payment clause",
                source_count=1,
                evidence_json=dumps_json([]),
                related_requirement_ids_json=dumps_json([]),
                related_risk_ids_json=dumps_json([]),
                response_required=True,
                review_status="pending",
                confidence=0.7,
                extraction_method="test",
                status="active",
            )
        )
        db.commit()

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/business-objects/llm-review",
            headers=headers,
            json={"run_uuid": "latest", "limit": 25},
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["status"] == "skipped"
        assert payload["skip_reason"] == "deepseek_api_key_missing"
        assert payload["candidate_count"] == 1
        assert payload["reviewed_count"] == 0
    finally:
        db.close()
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_bidding_llm_review", old_llm)
        _set_flag("deepseek_api_key", old_key)


def test_bidding_mvp_upload_tender_file_creates_project(client):
    headers = _headers(client, "staff")
    old_flag = _set_flag("feature_bidding_mvp", True)
    try:
        invalid_response = client.post(
            "/api/v1/admin/bidding/projects/from-tender-file",
            headers=headers,
            files={"file": ("tender.txt", "招标文件".encode("utf-8"), "text/plain")},
        )
        assert invalid_response.status_code == 422
        assert invalid_response.json()["detail"] == "INVALID_PRIMARY_TENDER_FILE_TYPE"

        docx = _docx_bytes(
            [
                "工程名称：甲方办公楼装饰工程",
                "投标截止时间为2026年7月20日，投标文件需签字盖章。",
                "本工程采用固定总价，总价包干，合同价款不予调整。",
                "清单漏项视为已包含，不另行计取。",
                "技术标为暗标，不得出现投标人名称，否则否决投标。",
            ]
        )
        upload_response = client.post(
            "/api/v1/admin/bidding/projects/from-tender-file",
            headers=headers,
            files={
                "file": (
                    "甲方招标文件.docx",
                    docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert upload_response.status_code == 200, upload_response.text
        payload = upload_response.json()["data"]
        project = payload["project"]
        file_data = payload["file"]
        assert project["project_name"] == "甲方招标文件"
        assert project["status"] == "files_uploaded"
        assert project["summary"]["input_mode"] == "primary_tender_file"
        assert file_data["file_type"] == "tender_document"
        assert file_data["parser_status"] == "parsed"
        assert file_data["section_count"] > 0

        parse_response = client.post(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/parse",
            headers=headers,
            json={},
        )
        assert parse_response.status_code == 200, parse_response.text
        assert parse_response.json()["data"]["summary"]["risk_count"] >= 3
    finally:
        _set_flag("feature_bidding_mvp", old_flag)


def test_bidding_mvp_project_upload_parse_and_review_risk(client):
    headers = _headers(client, "staff")
    old_flag = _set_flag("feature_bidding_mvp", True)
    try:
        create_response = client.post(
            "/api/v1/admin/bidding/projects",
            headers=headers,
            json={
                "project_name": "办公楼装饰工程投标",
                "tenderer_name": "某建设单位",
                "project_type": "办公楼装修",
                "tender_deadline_at": "2026-07-20 10:00",
            },
        )
        assert create_response.status_code == 200, create_response.text
        project = create_response.json()["data"]

        tender_text = """
        招标文件

        投标文件递交截止时间：2026年7月20日10时，投标有效期90日历天。

        投标人须具备建筑装修装饰工程专业承包资质，项目经理须具备建筑工程二级建造师。

        本工程质量标准为合格，工期45日历天。

        合同采用固定总价，总价包干，除发包人原因外合同价款不予调整。

        措施项目费、综合单价及合同总价均为包干价，市场价格波动不予调整。

        清单漏项视为已包含在综合单价内，不再另行增加费用。

        工程量清单错项、漏项或项目特征描述不完整，投标人应综合考虑在报价中，结算不另行增加。

        技术标为暗标，不得出现投标人名称、人员姓名或企业标识，否则否决投标。
        """
        upload_response = client.post(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/files",
            headers=headers,
            data={"file_type": "tender_document"},
            files={"file": ("tender.txt", tender_text.encode("utf-8"), "text/plain")},
        )
        assert upload_response.status_code == 200, upload_response.text
        uploaded = upload_response.json()["data"]
        assert uploaded["section_count"] > 0
        assert uploaded["parser_version"] == "biz4a-rule-v1"

        parse_response = client.post(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/parse",
            headers=headers,
            json={},
        )
        assert parse_response.status_code == 200, parse_response.text
        parse_run = parse_response.json()["data"]
        assert parse_run["status"] == "completed"
        assert parse_run["summary"]["requirement_count"] >= 4
        assert parse_run["summary"]["high_risk_count"] >= 2
        assert parse_run["summary"]["risk_card_summary"]["card_count"] >= 1
        assert parse_run["summary"]["risk_card_summary"]["blocking_v2_card_count"] >= 1
        assert parse_run["summary"]["risk_card_summary"]["critical_card_count"] >= 1
        assert parse_run["summary"]["business_object_summary"]["object_count"] >= 5
        assert parse_run["summary"]["business_object_summary"]["object_by_type"]
        assert parse_run["summary"]["business_object_summary"]["object_by_action"]

        requirements_response = client.get(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/requirements",
            headers=headers,
        )
        assert requirements_response.status_code == 200, requirements_response.text
        requirement_types = {item["requirement_type"] for item in requirements_response.json()["data"]}
        assert {"bid_rule", "qualification", "contract", "bid_void"} & requirement_types

        risks_response = client.get(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/risks",
            headers=headers,
            params={"risk_level": "high"},
        )
        assert risks_response.status_code == 200, risks_response.text
        risks = risks_response.json()["data"]
        assert risks
        assert any(item["risk_type"] == "fixed_total_price" for item in risks)

        business_objects_response = client.get(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/business-objects",
            headers=headers,
            params={"page_size": 200},
        )
        assert business_objects_response.status_code == 200, business_objects_response.text
        business_objects = business_objects_response.json()["data"]
        business_summary = business_objects_response.json()["summary"]
        object_types = {item["object_type"] for item in business_objects}
        assert {"bid_rule", "qualification", "contract_clause", "pricing_constraint"} <= object_types
        assert business_summary["object_count"] == len(business_objects)
        assert business_summary["response_required_count"] >= 5
        assert business_summary["object_by_action"]
        assert business_summary["quote_allowance_count"] >= 1
        assert any(item["evidence"] and item["related_requirement_ids"] for item in business_objects)
        assert any(item["related_risk_ids"] for item in business_objects)
        assert any(item["normalized"].get("business_action") for item in business_objects)

        object_to_review = next(item for item in business_objects if item["review_status"] == "pending")
        object_review_response = client.patch(
            f"/api/v1/admin/bidding/business-objects/{object_to_review['object_uuid']}/review",
            headers=headers,
            json={"review_status": "confirmed", "reviewer_note": "业务对象已确认"},
        )
        assert object_review_response.status_code == 200, object_review_response.text
        reviewed_object = object_review_response.json()["data"]
        assert reviewed_object["review_status"] == "confirmed"
        assert reviewed_object["reviewed_by"] is not None

        cards_response = client.get(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/risk-cards",
            headers=headers,
        )
        assert cards_response.status_code == 200, cards_response.text
        cards_payload = cards_response.json()["data"]
        cards = cards_payload["cards"]
        assert cards
        assert cards_payload["summary"]["card_count"] == len(cards)
        assert cards_payload["summary"]["risk_count"] >= len(cards)
        assert len(cards) < parse_run["summary"]["risk_count"]
        assert any(card["risk_count"] >= 1 and card["evidence"] for card in cards)
        assert cards_payload["summary"]["card_by_grade_v2"]
        assert cards_payload["summary"]["blocking_v2_card_count"] >= 1
        assert cards_payload["summary"]["critical_card_count"] >= 1
        assert all(card["risk_grade_v2"] and card["risk_score"] >= 0 for card in cards)
        assert any(card["risk_grade_v2"] == "blocking" for card in cards)
        assert any(card["risk_grade_v2"] == "critical" for card in cards)
        assert any(card["grade_reason"] and card["drivers"] and card["review_roles"] for card in cards)

        card_to_review = next(card for card in cards if card["review_status"] == "pending")
        card_review_response = client.patch(
            f"/api/v1/admin/bidding/projects/{project['project_uuid']}/risk-cards/{card_to_review['card_id']}/review",
            headers=headers,
            json={"review_status": "to_quote_allowance", "reviewer_note": "报价阶段统一预留风险费"},
        )
        assert card_review_response.status_code == 200, card_review_response.text
        reviewed_card_payload = card_review_response.json()["data"]
        assert reviewed_card_payload["updated_risk_count"] == card_to_review["risk_count"]
        assert reviewed_card_payload["card"]["review_status"] == "to_quote_allowance"

        review_response = client.patch(
            f"/api/v1/admin/bidding/risks/{risks[0]['risk_uuid']}/review",
            headers=headers,
            json={"review_status": "to_clarify", "reviewer_note": "转答疑确认合同价款调整边界"},
        )
        assert review_response.status_code == 200, review_response.text
        assert review_response.json()["data"]["review_status"] == "to_clarify"
        assert review_response.json()["data"]["reviewed_by"] is not None
    finally:
        _set_flag("feature_bidding_mvp", old_flag)
