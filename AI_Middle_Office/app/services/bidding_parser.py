from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pypdf import PdfReader


BIDDING_PARSER_VERSION = "biz4a-rule-v5-xlsx-safe-autotype"
DOCUMENT_STRUCTURE_VERSION = "biz4a-structure-v2"
MAX_SEGMENT_CHARS = 1200
MAX_EXTRACTED_TEXT_CHARS = 1_200_000
WORD_MAIN_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OLE_COMPOUND_FILE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_FILE_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
SPREADSHEET_MAIN_NS = (
    "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
)
OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_REL_NS = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
XLSX_MAX_ARCHIVE_ENTRIES = 10_000
XLSX_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
XLSX_MAX_SHEETS = 100
XLSX_MAX_NONEMPTY_ROWS_PER_SHEET = 5_000
XLSX_MAX_COLUMNS = 256
XLSX_MAX_CELL_CHARS = 800
XLSX_IDENTITY_ROW_SAMPLE = 12
XLSX_BLOATED_COLUMN_THRESHOLD = 512
XLSX_PHASE_PATTERN = re.compile(
    r"([一二三四五六七八九十百零〇两\d]+)\s*期"
)


class TenderParseError(ValueError):
    pass


@dataclass(frozen=True)
class TenderSegment:
    source_file: str
    source_location: str
    text: str
    page_number: int | None = None
    section_index: int | None = None
    document_section: str = "other"
    document_section_label: str = "其他"
    is_structural_noise: bool = False
    noise_reason: str | None = None
    structure_confidence: float = 0.35
    document_section_source: str = "direct"


@dataclass(frozen=True)
class RequirementRule:
    requirement_type: str
    label: str
    keywords: tuple[str, ...]
    owner_role: str
    output_section: str
    risk_level: str = "low"


@dataclass(frozen=True)
class RiskRule:
    risk_type: str
    risk_level: str
    keywords: tuple[str, ...]
    explanation: str
    impact_area: str
    suggested_action: str
    is_blocking: bool = False
    confidence: float = 0.72


@dataclass(frozen=True)
class DocumentSectionRule:
    section_type: str
    label: str
    keywords: tuple[str, ...]
    strong_keywords: tuple[str, ...] = ()


REQUIREMENT_RULES: tuple[RequirementRule, ...] = (
    RequirementRule("project_basic", "工程基本信息", ("工程名称", "建设地点", "工程地点", "工程规模", "承包方式"), "经营", "项目概况"),
    RequirementRule("bid_rule", "投标规则", ("投标截止", "递交投标文件", "开标时间", "投标保证金", "投标有效期", "密封", "电子投标"), "经营", "投标函/商务响应"),
    RequirementRule("qualification", "资格要求", ("资质", "安全生产许可证", "项目经理", "注册建造师", "业绩", "财务要求", "信用"), "经营", "资格审查文件", "medium"),
    RequirementRule("evaluation", "评标办法", ("评标", "评分", "综合评估", "报价分", "技术分", "商务分", "权重"), "经营", "投标策略", "medium"),
    RequirementRule("scope", "工程范围", ("工程范围", "承包范围", "施工范围", "界面划分", "图纸", "工程量清单"), "预算", "报价说明/技术方案", "medium"),
    RequirementRule("material", "材料品牌要求", ("材料", "品牌", "样品", "环保等级", "甲供", "甲指", "认质认价"), "预算", "材料品牌响应表", "medium"),
    RequirementRule("technical", "技术质量要求", ("质量标准", "验收标准", "施工工艺", "技术标准", "安全文明", "成品保护"), "技术", "技术标"),
    RequirementRule("contract", "合同商务条款", ("合同", "付款", "结算", "违约", "质保", "保修", "变更", "签证", "索赔", "总价包干"), "法务", "合同响应/偏离表", "medium"),
    RequirementRule("format", "格式与签章要求", ("格式", "签字", "盖章", "页码", "封面", "目录", "暗标", "文件命名"), "经营", "投标文件格式", "medium"),
    RequirementRule("bid_void", "废标/否决条款", ("废标", "否决投标", "无效投标", "实质性不响应", "重大偏差"), "经营", "废标风险检查", "high"),
)


RISK_RULES: tuple[RiskRule, ...] = (
    RiskRule(
        "fixed_total_price",
        "high",
        ("总价包干", "固定总价", "合同价款不予调整", "结算不予调整", "风险自行承担"),
        "存在总价包干或合同价款不可调整风险，清单漏项、现场条件变化或材料价格波动可能无法在结算阶段补偿。",
        "报价/利润",
        "预算负责人需复核清单完整性和风险预留，必要时在答疑阶段确认调价边界。",
    ),
    RiskRule(
        "omission_liability",
        "high",
        ("清单漏项", "漏项", "视为已包含", "不另行计取", "不予增加"),
        "条款可能将清单漏项、错项或措施遗漏风险转嫁给承包人，容易造成低价中标后亏损。",
        "报价/清单",
        "逐项复核工程量清单、图纸和项目特征，无法确认的范围应转答疑或报价风险预留。",
    ),
    RiskRule(
        "no_price_adjustment",
        "high",
        ("材料涨价", "价格波动", "市场价格", "不予调整", "自行承担"),
        "材料或人工价格波动可能不能调整，装饰主材价格变化会直接影响利润。",
        "材料/利润",
        "对主材、长周期材料和人工敏感项设置风险预留，并确认是否允许调差。",
    ),
    RiskRule(
        "advance_funding",
        "high",
        ("垫资", "无预付款", "不支付预付款", "预付款为0"),
        "项目可能需要承包人先行垫资，现金流压力较高。",
        "现金流",
        "经营负责人需测算垫资周期和资金占用，必要时提高报价风险系数或谨慎投标。",
    ),
    RiskRule(
        "delayed_payment",
        "medium",
        ("竣工结算后", "审计后", "财政评审后", "收到发票后", "付款至", "质保金"),
        "付款节点可能偏后或与审计、财政评审、质保金挂钩，会影响回款周期。",
        "现金流",
        "复核付款比例、质保金比例和回款周期，在投标决策中纳入资金成本。",
    ),
    RiskRule(
        "liquidated_damages",
        "high",
        ("工期违约金", "逾期", "每延误", "每日", "扣罚", "违约金"),
        "工期违约责任较重，若工期紧或现场交叉作业复杂，可能产生较大扣罚。",
        "工期/履约",
        "技术负责人需复核工期计划和关键路径，经营负责人确认违约金是否有上限。",
    ),
    RiskRule(
        "claim_time_limit",
        "high",
        ("签证", "索赔", "逾期视为放弃", "不予认可", "不得补办", "有效期"),
        "变更签证或索赔存在严格时限，现场未及时确认可能导致费用无法追索。",
        "变更/索赔",
        "项目执行前建立签证时限提醒，投标阶段确认变更签证流程和审批周期。",
    ),
    RiskRule(
        "site_condition",
        "medium",
        ("踏勘现场", "现场条件", "自行承担", "不予调整", "充分考虑"),
        "条款可能要求投标人自行承担现场条件差异，隐蔽障碍、运输限制或夜间施工约束会影响成本。",
        "现场/施工",
        "投标前组织现场踏勘，形成待答疑问题，报价中考虑现场条件风险。",
    ),
    RiskRule(
        "design_or_drawing_unclear",
        "medium",
        ("图纸不详", "图纸缺失", "以现场为准", "暂定", "另行确定", "甲方确认"),
        "图纸或范围存在不确定性，后续容易出现范围争议或漏项。",
        "范围/技术",
        "整理缺失图纸、暂定做法和边界问题，优先在答疑阶段确认。",
    ),
    RiskRule(
        "material_brand_constraint",
        "medium",
        ("指定品牌", "甲供", "甲指乙供", "认质认价", "品牌档次", "样品封样"),
        "材料品牌或供应方式受限，可能影响采购价格、供货周期和替代空间。",
        "材料/采购",
        "预算和采购需确认品牌档次、供货周期和替代规则，避免低估主材成本。",
    ),
    RiskRule(
        "bid_rejection",
        "high",
        ("废标", "否决投标", "无效投标", "实质性不响应", "重大偏差"),
        "存在废标或否决投标条款，投标文件如漏响应可能直接失去投标资格。",
        "合规/废标",
        "纳入交付前合规体检，逐条确认签章、格式、资格、报价和暗标要求。",
        True,
        0.86,
    ),
    RiskRule(
        "anonymous_bid",
        "high",
        ("暗标", "不得出现投标人名称", "不得出现单位名称", "不得出现标识", "技术标"),
        "技术标可能要求暗标匿名，如出现公司名称、人员姓名或企业标识，可能被否决。",
        "技术标/格式",
        "导出前执行暗标敏感词检查，技术标模板与附件需单独复核。",
        True,
        0.84,
    ),
)


QUESTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("图纸不完整", r"图纸(不详|缺失|另行|以现场为准)"),
    ("范围待确认", r"(暂定|另行确定|以甲方确认|以现场为准|施工范围.*不明)"),
    ("清单口径待确认", r"(暂估|暂列|按实|工程量.*暂定|清单.*不一致)"),
    ("材料品牌待确认", r"(同档次|不低于|甲方认可|封样|认质认价)"),
    ("答疑/补遗关注", r"(答疑|补遗|澄清|疑问|投标人须知前附表)"),
)


DOCUMENT_SECTION_RULES: tuple[DocumentSectionRule, ...] = (
    DocumentSectionRule(
        "bid_instructions",
        "投标须知",
        ("投标须知", "投标人须知", "投标文件", "投标截止", "开标", "投标保证金", "密封", "废标条件"),
        ("投标须知", "投标人须知", "废标条件"),
    ),
    DocumentSectionRule(
        "qualification",
        "资格要求",
        ("资质", "安全生产许可证", "项目经理", "注册建造师", "业绩", "财务要求", "信用"),
        ("投标人资质要求", "资格审查"),
    ),
    DocumentSectionRule(
        "evaluation",
        "评标办法",
        ("评标", "评分", "综合评估", "报价分", "技术分", "商务分", "权重"),
        ("评标办法", "评分标准"),
    ),
    DocumentSectionRule(
        "contract_terms",
        "合同条款",
        ("合同", "协议书", "发包人", "承包人", "分包人", "付款", "结算", "违约", "质保", "保修", "变更", "签证", "索赔"),
        ("合同条款", "合同文件", "协议书"),
    ),
    DocumentSectionRule(
        "technical_requirements",
        "技术要求",
        ("技术要求", "工料规范", "质量标准", "验收标准", "施工工艺", "安全文明", "成品保护"),
        ("标准、规范和技术要求", "技术要求", "工料规范"),
    ),
    DocumentSectionRule(
        "bill_of_quantities",
        "工程量清单",
        ("工程量清单", "报价汇总", "综合单价", "开办费", "工程量计算规则", "清单报价"),
        ("工程量清单", "工程量计算规则"),
    ),
    DocumentSectionRule(
        "bid_format",
        "投标文件格式",
        ("投标文件格式", "投标函", "授权委托书", "承诺书", "法定代表人", "签字", "盖章", "封面", "目录"),
        ("投标文件格式", "投标函", "授权委托书"),
    ),
    DocumentSectionRule(
        "material_brand",
        "材料品牌",
        ("材料", "品牌", "指定品牌", "甲供", "甲限", "样品", "封样", "认质认价"),
        ("材料品牌", "甲供材料", "甲限材料"),
    ),
    DocumentSectionRule(
        "scope_boundary",
        "范围界面",
        ("工程范围", "承包范围", "施工范围", "界面划分", "图纸", "现场踏勘", "暂定"),
        ("承包范围", "界面划分"),
    ),
    DocumentSectionRule(
        "clarification",
        "澄清答疑",
        ("答疑", "澄清", "补遗", "疑问", "回标疑问", "标前澄清"),
        ("标前澄清", "答疑", "补遗"),
    ),
)


STRUCTURAL_NOISE_SECTIONS = {"table_of_contents", "cover"}
STRUCTURE_INHERITABLE_SECTIONS = {
    "bid_instructions",
    "qualification",
    "evaluation",
    "contract_terms",
    "technical_requirements",
    "bill_of_quantities",
    "bid_format",
    "material_brand",
    "scope_boundary",
    "clarification",
}

REQUIREMENT_ALLOWED_SECTIONS: dict[str, set[str]] = {
    "project_basic": {"bid_instructions", "scope_boundary", "contract_terms", "other"},
    "bid_rule": {"bid_instructions", "bid_format", "clarification", "other"},
    "qualification": {"qualification", "bid_instructions", "bid_format", "other"},
    "evaluation": {"evaluation", "bid_instructions", "other"},
    "scope": {"scope_boundary", "bid_instructions", "contract_terms", "bill_of_quantities", "technical_requirements", "clarification", "other"},
    "material": {"material_brand", "technical_requirements", "bill_of_quantities", "contract_terms", "scope_boundary", "bid_instructions", "other"},
    "technical": {"technical_requirements", "scope_boundary", "bid_format", "bid_instructions", "other"},
    "contract": {"contract_terms", "bid_instructions", "clarification", "other"},
    "format": {"bid_format", "bid_instructions", "other"},
    "bid_void": {"bid_instructions", "bid_format", "contract_terms", "other"},
}

RISK_ALLOWED_SECTIONS: dict[str, set[str]] = {
    "fixed_total_price": {"contract_terms", "bill_of_quantities", "bid_instructions", "clarification", "other"},
    "omission_liability": {"contract_terms", "bill_of_quantities", "scope_boundary", "technical_requirements", "bid_instructions", "clarification", "other"},
    "no_price_adjustment": {"contract_terms", "bill_of_quantities", "bid_instructions", "clarification", "other"},
    "advance_funding": {"contract_terms", "bid_instructions", "other"},
    "delayed_payment": {"contract_terms", "bid_instructions", "other"},
    "liquidated_damages": {"contract_terms", "bid_instructions", "other"},
    "claim_time_limit": {"contract_terms", "clarification", "other"},
    "site_condition": {"scope_boundary", "contract_terms", "bid_instructions", "clarification", "other"},
    "design_or_drawing_unclear": {"scope_boundary", "contract_terms", "technical_requirements", "bill_of_quantities", "clarification", "other"},
    "material_brand_constraint": {"material_brand", "technical_requirements", "contract_terms", "bill_of_quantities", "other"},
    "bid_rejection": {"bid_instructions", "bid_format", "contract_terms", "other"},
    "anonymous_bid": {"bid_instructions", "bid_format", "technical_requirements", "other"},
}


def _compact_for_structure(value: str) -> str:
    return re.sub(r"[\s\u3000:：·,，、。.\-_/\\()（）【】\[\]「」《》]+", "", value or "")


def _keyword_hit(keyword: str, *, compact_text: str) -> bool:
    compact_keyword = _compact_for_structure(keyword)
    return bool(compact_keyword and compact_keyword in compact_text)


def _document_section_label(section_type: str | None) -> str:
    labels = {
        "empty": "空白",
        "table_of_contents": "目录",
        "cover": "封面",
        "other": "其他",
    }
    for rule in DOCUMENT_SECTION_RULES:
        labels[rule.section_type] = rule.label
    return labels.get(section_type or "other", section_type or "其他")


def _heading_section_type(text: str) -> str | None:
    compact_text = _compact_for_structure(text)
    if not compact_text:
        return None
    has_chapter_marker = bool(re.search(r"第[一二三四五六七八九十\d]+[章节篇部分]", compact_text))
    has_sentence_body = any(mark in text for mark in ("。", "；", ";"))
    heading_like = has_chapter_marker or (len(compact_text) <= 70 and not has_sentence_body)
    if not heading_like:
        return None
    heading_rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("bid_instructions", ("投标须知", "投标人须知")),
        ("contract_terms", ("合同文件", "合同条款", "协议书", "合同条件")),
        ("technical_requirements", ("标准规范和技术要求", "技术要求", "工料规范", "技术规范")),
        ("bill_of_quantities", ("工程量清单", "清单报价", "工程量计算规则")),
        ("bid_format", ("投标文件格式", "商务标部分", "技术标部分", "投标函", "授权委托书")),
        ("material_brand", ("材料品牌", "甲供材料", "甲限材料", "认质认价")),
        ("evaluation", ("评标办法", "评分标准")),
        ("qualification", ("资格审查", "资质要求", "投标人资质要求")),
        ("scope_boundary", ("承包范围", "工程范围", "界面划分", "施工范围")),
        ("clarification", ("标前澄清", "答疑", "补遗", "回标疑问")),
    )
    for section_type, keywords in heading_rules:
        if any(_keyword_hit(keyword, compact_text=compact_text) for keyword in keywords):
            return section_type
    return None


def _is_pure_section_heading(text: str, document_section: str) -> bool:
    compact_text = _compact_for_structure(text)
    if len(compact_text) > 42:
        return False
    if any(mark in text for mark in ("。", "；", ";")):
        return False
    if document_section not in STRUCTURE_INHERITABLE_SECTIONS and not _heading_section_type(text):
        return False
    body_signals = ("应", "须", "不得", "必须", "不予", "视为", "支付", "承担", "提交", "报价", "结算")
    return not any(signal in compact_text for signal in body_signals)


def _low_value_ignore_reason(text: str, document_section: str) -> str | None:
    compact_text = _compact_for_structure(text)
    if not compact_text:
        return "empty_text"
    if _is_pure_section_heading(text, document_section):
        return "section_heading"
    if "不适用" in compact_text and len(compact_text) <= 80:
        return "not_applicable"
    if "如有" in compact_text and len(compact_text) <= 42:
        return "conditional_short_note"
    return None


def _apply_document_structure_context_to_dicts(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    structured: list[dict[str, Any]] = []
    current_section: str | None = None
    current_label: str | None = None
    for raw_segment in raw_segments:
        text = _clean_text(str(raw_segment.get("text") or ""))
        structure = _structure_from_raw_segment(raw_segment, text) if text else {
            "document_section": "empty",
            "document_section_label": "空白",
            "is_structural_noise": True,
            "noise_reason": "empty_text",
            "structure_confidence": 0.9,
        }
        section = str(structure["document_section"])
        label = str(structure["document_section_label"])
        confidence = float(structure.get("structure_confidence") or 0.35)
        source = str(raw_segment.get("document_section_source") or "direct")

        heading_section = None if structure.get("is_structural_noise") else _heading_section_type(text)
        if heading_section in STRUCTURE_INHERITABLE_SECTIONS:
            current_section = heading_section
            current_label = _document_section_label(heading_section)
            section = heading_section
            label = current_label
            confidence = max(confidence, 0.88)
            source = "heading"
        elif current_section and not structure.get("is_structural_noise"):
            if section == "other" or confidence < 0.62:
                section = current_section
                label = current_label or _document_section_label(current_section)
                confidence = max(confidence, 0.64)
                source = "inherited"

        enriched = dict(raw_segment)
        enriched.update(
            {
                "document_section": section,
                "document_section_label": label,
                "is_structural_noise": bool(structure.get("is_structural_noise")),
                "noise_reason": structure.get("noise_reason"),
                "structure_confidence": round(confidence, 4),
                "document_section_source": source,
            }
        )
        structured.append(enriched)
    return structured


def _is_table_of_contents(text: str, compact_text: str) -> bool:
    if "目录" not in compact_text:
        return False
    signals = (
        "第一章",
        "第二章",
        "第三章",
        "第四章",
        "第五章",
        "页数",
        "投标须知",
        "合同文件",
        "技术要求",
        "工程量清单",
        "投标文件格式",
    )
    signal_count = sum(1 for signal in signals if _keyword_hit(signal, compact_text=compact_text))
    chapter_count = len(re.findall(r"第[一二三四五六七八九十]+章", compact_text))
    return signal_count >= 3 or chapter_count >= 3


def _is_cover(text: str, compact_text: str, page_number: int | None) -> bool:
    if page_number not in {None, 0, 1}:
        return False
    if len(compact_text) > 120 or text.count("\n") >= 2:
        return False
    has_title = "招标文件" in compact_text or "投标邀请" in compact_text
    has_project = "工程" in compact_text or "项目" in compact_text
    has_business_body = any(
        _keyword_hit(keyword, compact_text=compact_text)
        for keyword in (
            "递交截止",
            "投标有效期",
            "资质",
            "工期",
            "总价",
            "包干",
            "付款",
            "结算",
            "废标",
            "否决投标",
            "暗标",
            "工程量清单",
            "合同条款",
        )
    )
    return has_title and has_project and not has_business_body


def classify_document_structure(
    text: str,
    *,
    page_number: int | None = None,
    source_location: str | None = None,
) -> dict[str, Any]:
    cleaned = _clean_text(text)
    compact_text = _compact_for_structure(cleaned)
    if not compact_text:
        return {
            "document_section": "empty",
            "document_section_label": "空白",
            "is_structural_noise": True,
            "noise_reason": "empty_text",
            "structure_confidence": 0.9,
        }
    if _is_table_of_contents(cleaned, compact_text):
        return {
            "document_section": "table_of_contents",
            "document_section_label": "目录",
            "is_structural_noise": True,
            "noise_reason": "table_of_contents",
            "structure_confidence": 0.92,
        }
    if _is_cover(cleaned, compact_text, page_number):
        return {
            "document_section": "cover",
            "document_section_label": "封面",
            "is_structural_noise": True,
            "noise_reason": "cover_page",
            "structure_confidence": 0.82,
        }

    best_rule: DocumentSectionRule | None = None
    best_score = 0
    for rule in DOCUMENT_SECTION_RULES:
        score = sum(1 for keyword in rule.keywords if _keyword_hit(keyword, compact_text=compact_text))
        score += 2 * sum(1 for keyword in rule.strong_keywords if _keyword_hit(keyword, compact_text=compact_text))
        if score > best_score:
            best_score = score
            best_rule = rule

    if not best_rule:
        return {
            "document_section": "other",
            "document_section_label": "其他",
            "is_structural_noise": False,
            "noise_reason": None,
            "structure_confidence": 0.35,
        }

    return {
        "document_section": best_rule.section_type,
        "document_section_label": best_rule.label,
        "is_structural_noise": best_rule.section_type in STRUCTURAL_NOISE_SECTIONS,
        "noise_reason": best_rule.section_type if best_rule.section_type in STRUCTURAL_NOISE_SECTIONS else None,
        "structure_confidence": min(0.95, 0.48 + best_score * 0.08),
    }


def _segment_with_structure(
    *,
    source_file: str,
    source_location: str,
    text: str,
    page_number: int | None = None,
    section_index: int | None = None,
) -> TenderSegment:
    structure = classify_document_structure(text, page_number=page_number, source_location=source_location)
    return TenderSegment(
        source_file=source_file,
        source_location=source_location,
        text=text,
        page_number=page_number,
        section_index=section_index,
        document_section=structure["document_section"],
        document_section_label=structure["document_section_label"],
        is_structural_noise=bool(structure["is_structural_noise"]),
        noise_reason=structure.get("noise_reason"),
        structure_confidence=float(structure["structure_confidence"]),
        document_section_source="direct",
    )


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _clean_text(value: str) -> str:
    value = re.sub(r"\r\n?", "\n", value or "")
    value = re.sub(r"[ \t\u3000]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _segment_long_text(text: str, *, source_file: str, source_location: str, page_number: int | None = None) -> list[TenderSegment]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n|\n(?=[一二三四五六七八九十\d]+[、.．])", cleaned) if item.strip()]
    if not paragraphs:
        paragraphs = [cleaned]
    segments: list[TenderSegment] = []
    section_index = 0
    for paragraph in paragraphs:
        if len(paragraph) <= MAX_SEGMENT_CHARS:
            section_index += 1
            segments.append(
                _segment_with_structure(
                    source_file=source_file,
                    source_location=f"{source_location} 第{section_index}段",
                    text=paragraph,
                    page_number=page_number,
                    section_index=section_index,
                )
            )
            continue
        for start in range(0, len(paragraph), MAX_SEGMENT_CHARS):
            chunk = paragraph[start : start + MAX_SEGMENT_CHARS].strip()
            if chunk:
                section_index += 1
                segments.append(
                    _segment_with_structure(
                        source_file=source_file,
                        source_location=f"{source_location} 第{section_index}段",
                        text=chunk,
                        page_number=page_number,
                        section_index=section_index,
                    )
                )
    return segments


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _word_attr(name: str) -> str:
    return f"{{{WORD_MAIN_NS}}}{name}"


def _docx_symbol_text(node: ElementTree.Element) -> str:
    font = str(node.attrib.get(_word_attr("font")) or "").upper()
    char = str(node.attrib.get(_word_attr("char")) or "").upper()
    if "WINGDINGS" not in font:
        return ""
    if char in {"0052", "F052", "00FE", "F0FE"}:
        return "☑"
    if char in {"00A3", "F0A3", "006F", "F06F", "00A8", "F0A8"}:
        return "☐"
    return ""


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        local_name = _xml_local_name(node.tag)
        if local_name == "t":
            parts.append(node.text or "")
        elif local_name == "tab":
            parts.append(" ")
        elif local_name == "br":
            parts.append("\n")
        elif local_name == "sym":
            parts.append(_docx_symbol_text(node))
    return _clean_text("".join(parts))


def _docx_table_rows(table: ElementTree.Element, ns: dict[str, str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("./w:tr", ns):
        cells: list[str] = []
        for cell in row.findall("./w:tc", ns):
            paragraphs = [
                value
                for paragraph in cell.findall(".//w:p", ns)
                if (value := _docx_paragraph_text(paragraph))
            ]
            cell_text = _clean_text(" ".join(paragraphs))
            cells.append(cell_text)
        if any(cells):
            rows.append(cells)
    return rows


def _extract_pdf(content: bytes, filename: str) -> tuple[str, list[TenderSegment], int]:
    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise TenderParseError(f"PDF 读取失败: {exc}") from exc
    segments: list[TenderSegment] = []
    page_texts: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            page_texts.append(f"[第{page_index}页]\n{text}")
            segments.extend(
                _segment_long_text(
                    text,
                    source_file=filename,
                    source_location=f"第{page_index}页",
                    page_number=page_index,
                )
            )
    return _clean_text("\n\n".join(page_texts)), segments, len(reader.pages)


def _extract_docx(content: bytes, filename: str) -> tuple[str, list[TenderSegment], int]:
    display_name = Path(filename or "").name
    if display_name.startswith("~$"):
        raise TenderParseError(
            "无法读取 Word 临时锁定文件（文件名以“~$”开头）。"
            "请关闭正在编辑的文档，并选择原始 .docx 文件重新上传"
        )

    leading_content = content[:1024].lstrip()
    if content.startswith(OLE_COMPOUND_FILE_SIGNATURE):
        raise TenderParseError(
            "DOCX 格式校验失败：文件内容是旧版 Word .doc（或 Word 临时锁定文件），并非标准 .docx。"
            "请用 Word 打开原文件并另存为“Word 文档（.docx）”后重新上传"
        )
    if leading_content.startswith(b"%PDF-"):
        raise TenderParseError(
            "DOCX 格式校验失败：文件内容实际为 PDF。"
            "请将文件扩展名改回 .pdf，或重新导出为 .docx 后上传"
        )
    if leading_content.lower().startswith(b"{\\rtf"):
        raise TenderParseError(
            "DOCX 格式校验失败：文件内容实际为 RTF。"
            "请用 Word 打开后另存为“Word 文档（.docx）”再上传"
        )
    if leading_content.lower().startswith((b"<!doctype html", b"<html")):
        raise TenderParseError(
            "DOCX 格式校验失败：文件内容实际为网页 HTML。"
            "请用 Word 打开后另存为“Word 文档（.docx）”再上传"
        )
    if not content.startswith(ZIP_FILE_SIGNATURES):
        raise TenderParseError(
            "DOCX 格式校验失败：扩展名是 .docx，但文件内容不是有效的 Word DOCX。"
            "文件可能已损坏或仅修改了扩展名，请用 Word 打开后另存为 .docx 再上传"
        )

    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml_bytes = archive.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise TenderParseError(
            "DOCX 文件已损坏或上传不完整，无法解压。"
            "请重新获取文件，或用 Word 打开后另存为 .docx 再上传"
        ) from exc
    except KeyError as exc:
        raise TenderParseError(
            "该文件不是标准 Word DOCX（缺少 word/document.xml）。"
            "请确认文件来源，并用 Word 另存为 .docx 后重新上传"
        ) from exc
    except RuntimeError as exc:
        raise TenderParseError(
            "DOCX 文件可能受密码保护或使用了不受支持的压缩结构。"
            "请取消文档密码保护，并用 Word 另存为 .docx 后重新上传"
        ) from exc
    except OSError as exc:
        raise TenderParseError("DOCX 文件读取失败，请重新选择文件后上传") from exc
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise TenderParseError(
            "DOCX 主文档结构已损坏，无法读取正文。"
            "请用 Word 打开并修复后，另存为 .docx 再上传"
        ) from exc
    ns = {"w": WORD_MAIN_NS}
    body = root.find("w:body", ns)
    block_parent = body if body is not None else root
    lines: list[str] = []
    segments: list[TenderSegment] = []
    section_index = 0
    table_index = 0

    for child in list(block_parent):
        local_name = _xml_local_name(child.tag)
        if local_name == "p":
            paragraph_text = _docx_paragraph_text(child)
            if not paragraph_text:
                continue
            section_index += 1
            source_location = f"DOCX第{section_index}段"
            lines.append(f"[{source_location}] {paragraph_text}")
            segments.append(
                _segment_with_structure(
                    source_file=filename,
                    source_location=source_location,
                    text=paragraph_text[:MAX_SEGMENT_CHARS],
                    section_index=section_index,
                )
            )
            continue
        if local_name != "tbl":
            continue
        table_index += 1
        for row_index, cells in enumerate(_docx_table_rows(child, ns), start=1):
            row_text = _clean_text(" | ".join(cell for cell in cells if cell))
            if not row_text:
                continue
            section_index += 1
            source_location = f"DOCX表{table_index}第{row_index}行"
            lines.append(f"[{source_location}] {row_text}")
            segments.append(
                _segment_with_structure(
                    source_file=filename,
                    source_location=source_location,
                    text=row_text[:MAX_SEGMENT_CHARS],
                    section_index=section_index,
                )
            )
    text = "\n".join(lines)
    return _clean_text(text), segments, 0


def _xlsx_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xlsx_column_number(cell_reference: str) -> int:
    match = re.match(r"\$?([A-Z]+)", (cell_reference or "").upper())
    if match is None:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    return value


def _xlsx_row_number(cell_reference: str) -> int:
    match = re.search(r"(\d+)$", cell_reference or "")
    return int(match.group(1)) if match else 0


def _xlsx_dimension_bounds(
    dimension_reference: str | None,
) -> tuple[int, int] | None:
    if not dimension_reference:
        return None
    last_reference = dimension_reference.split(":", 1)[-1]
    column = _xlsx_column_number(last_reference)
    row = _xlsx_row_number(last_reference)
    if column <= 0 or row <= 0:
        return None
    return row, column


def _xlsx_part_path(base_part: str, target: str) -> str:
    normalized_target = (target or "").replace("\\", "/")
    if normalized_target.startswith("/"):
        return normalized_target.lstrip("/")
    return posixpath.normpath(
        posixpath.join(posixpath.dirname(base_part), normalized_target)
    )


def _xlsx_node_text(node: ElementTree.Element | None) -> str:
    if node is None:
        return ""
    return "".join(
        child.text or ""
        for child in node.iter()
        if _xlsx_local_name(child.tag) == "t"
    )


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    part_name = "xl/sharedStrings.xml"
    if part_name not in archive.namelist():
        return []
    values: list[str] = []
    try:
        with archive.open(part_name) as source:
            for event, element in ElementTree.iterparse(
                source,
                events=("end",),
            ):
                if _xlsx_local_name(element.tag) != "si":
                    continue
                values.append(_clean_text(_xlsx_node_text(element)))
                element.clear()
    except ElementTree.ParseError as exc:
        raise TenderParseError(
            f"Excel 共享字符串读取失败: {exc}"
        ) from exc
    return values


def _xlsx_sheet_parts(
    archive: zipfile.ZipFile,
) -> list[tuple[str, str]]:
    try:
        workbook_root = ElementTree.fromstring(
            archive.read("xl/workbook.xml")
        )
        relationship_root = ElementTree.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
    except KeyError as exc:
        raise TenderParseError(
            "Excel 结构不完整，缺少 workbook.xml 或关系文件"
        ) from exc
    except ElementTree.ParseError as exc:
        raise TenderParseError(
            f"Excel 工作簿结构读取失败: {exc}"
        ) from exc

    relationships = {
        item.attrib.get("Id", ""): item.attrib.get("Target", "")
        for item in relationship_root.iter()
        if _xlsx_local_name(item.tag) == "Relationship"
    }
    sheets: list[tuple[str, str]] = []
    relationship_attribute = f"{{{OFFICE_REL_NS}}}id"
    for item in workbook_root.iter():
        if _xlsx_local_name(item.tag) != "sheet":
            continue
        sheet_name = _clean_text(item.attrib.get("name")) or "未命名Sheet"
        relationship_id = item.attrib.get(relationship_attribute, "")
        target = relationships.get(relationship_id, "")
        if not target:
            continue
        sheets.append(
            (
                sheet_name,
                _xlsx_part_path("xl/workbook.xml", target),
            )
        )
    return sheets


def _xlsx_cell_value(
    cell: ElementTree.Element,
    shared_strings: list[str],
) -> str:
    cell_type = cell.attrib.get("t", "")
    value_node = next(
        (
            child
            for child in cell
            if _xlsx_local_name(child.tag) == "v"
        ),
        None,
    )
    inline_node = next(
        (
            child
            for child in cell
            if _xlsx_local_name(child.tag) == "is"
        ),
        None,
    )
    raw_value = (
        value_node.text
        if value_node is not None and value_node.text is not None
        else ""
    )
    if cell_type == "s":
        try:
            index = int(raw_value)
        except (TypeError, ValueError):
            return ""
        if 0 <= index < len(shared_strings):
            return shared_strings[index]
        return ""
    if cell_type == "inlineStr":
        return _xlsx_node_text(inline_node)
    if cell_type == "b":
        return "TRUE" if raw_value == "1" else "FALSE"
    return raw_value


def _xlsx_phase_markers(value: str) -> set[str]:
    return {
        _clean_text(match.group(1))
        for match in XLSX_PHASE_PATTERN.finditer(value or "")
        if _clean_text(match.group(1))
    }


def _scan_xlsx_sheet(
    archive: zipfile.ZipFile,
    *,
    sheet_name: str,
    part_name: str,
    shared_strings: list[str],
) -> tuple[list[tuple[int, str]], dict[str, Any]]:
    rows: list[tuple[int, str]] = []
    declared_dimension: str | None = None
    physical_row_count = 0
    meaningful_cell_count = 0
    ignored_value_cell_count = 0
    formula_cell_count = 0
    first_row = 0
    last_row = 0
    first_column = 0
    last_column = 0
    row_limit_reached = False
    try:
        source = archive.open(part_name)
    except KeyError:
        return [], {
            "sheet_name": sheet_name,
            "status": "skipped",
            "warning_codes": ["worksheet_part_missing"],
            "part_name": part_name,
        }

    try:
        with source:
            for event, element in ElementTree.iterparse(
                source,
                events=("start", "end"),
            ):
                local_name = _xlsx_local_name(element.tag)
                if event == "start" and local_name == "dimension":
                    declared_dimension = element.attrib.get("ref")
                    continue
                if event != "end" or local_name != "row":
                    continue
                physical_row_count += 1
                row_number = int(
                    element.attrib.get("r")
                    or physical_row_count
                )
                row_values: list[tuple[int, str]] = []
                for cell in element:
                    if _xlsx_local_name(cell.tag) != "c":
                        continue
                    if any(
                        _xlsx_local_name(child.tag) == "f"
                        for child in cell
                    ):
                        formula_cell_count += 1
                    value = _clean_text(
                        _xlsx_cell_value(cell, shared_strings)
                    )
                    if not value:
                        continue
                    column = _xlsx_column_number(
                        cell.attrib.get("r", "")
                    )
                    if column <= 0:
                        continue
                    meaningful_cell_count += 1
                    first_row = (
                        row_number
                        if first_row == 0
                        else min(first_row, row_number)
                    )
                    last_row = max(last_row, row_number)
                    first_column = (
                        column
                        if first_column == 0
                        else min(first_column, column)
                    )
                    last_column = max(last_column, column)
                    if column > XLSX_MAX_COLUMNS:
                        ignored_value_cell_count += 1
                        continue
                    row_values.append(
                        (column, value[:XLSX_MAX_CELL_CHARS])
                    )

                if row_values:
                    row_values.sort(key=lambda item: item[0])
                    row_text = " | ".join(
                        value for _, value in row_values
                    )[:MAX_SEGMENT_CHARS]
                    rows.append((row_number, row_text))
                    if (
                        len(rows)
                        >= XLSX_MAX_NONEMPTY_ROWS_PER_SHEET
                    ):
                        row_limit_reached = True
                        element.clear()
                        break
                element.clear()
    except ElementTree.ParseError as exc:
        raise TenderParseError(
            f"Excel Sheet“{sheet_name}”读取失败: {exc}"
        ) from exc

    warning_codes: list[str] = []
    declared_bounds = _xlsx_dimension_bounds(declared_dimension)
    if declared_bounds is not None:
        declared_row, declared_column = declared_bounds
        effective_column = max(last_column, 1)
        if (
            declared_column > XLSX_BLOATED_COLUMN_THRESHOLD
            and declared_column > effective_column * 4
        ):
            warning_codes.append("bloated_declared_dimension")
    if ignored_value_cell_count:
        warning_codes.append("column_limit_applied")
    if row_limit_reached:
        warning_codes.append("row_limit_applied")
    if not rows:
        warning_codes.append("no_meaningful_rows")

    effective_range = None
    if first_row and first_column:
        effective_range = {
            "min_row": first_row,
            "max_row": last_row,
            "min_column": first_column,
            "max_column": last_column,
        }
    return rows, {
        "sheet_name": sheet_name,
        "status": "parsed",
        "warning_codes": warning_codes,
        "part_name": part_name,
        "declared_dimension": declared_dimension,
        "effective_range": effective_range,
        "physical_row_count": physical_row_count,
        "extracted_row_count": len(rows),
        "meaningful_cell_count": meaningful_cell_count,
        "formula_cell_count": formula_cell_count,
        "ignored_value_cell_count": ignored_value_cell_count,
    }


def _extract_xlsx(
    content: bytes,
    filename: str,
) -> tuple[str, list[TenderSegment], int, dict[str, Any]]:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".xls":
        raise TenderParseError("暂不支持旧版 .xls，请另存为 .xlsx 后上传")
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise TenderParseError(f"Excel 读取失败: {exc}") from exc

    segments: list[TenderSegment] = []
    lines: list[str] = []
    sheet_diagnostics: list[dict[str, Any]] = []
    seen_sheet_content: dict[str, str] = {}
    extracted_character_count = 0
    text_limit_reached = False
    source_phase_markers = _xlsx_phase_markers(Path(filename).stem)
    try:
        entries = archive.infolist()
        if len(entries) > XLSX_MAX_ARCHIVE_ENTRIES:
            raise TenderParseError(
                "Excel 压缩包条目过多，已拒绝解析"
            )
        total_uncompressed_bytes = sum(
            max(int(item.file_size), 0) for item in entries
        )
        if total_uncompressed_bytes > XLSX_MAX_UNCOMPRESSED_BYTES:
            raise TenderParseError(
                "Excel 解压后体积超过安全上限，已拒绝解析"
            )
        shared_strings = _xlsx_shared_strings(archive)
        sheet_parts = _xlsx_sheet_parts(archive)
        for sheet_index, (sheet_name, part_name) in enumerate(
            sheet_parts,
            start=1,
        ):
            if sheet_index > XLSX_MAX_SHEETS:
                sheet_diagnostics.append(
                    {
                        "sheet_name": sheet_name,
                        "status": "skipped",
                        "warning_codes": ["sheet_limit_applied"],
                    }
                )
                continue
            rows, diagnostic = _scan_xlsx_sheet(
                archive,
                sheet_name=sheet_name,
                part_name=part_name,
                shared_strings=shared_strings,
            )
            sheet_phase_markers = _xlsx_phase_markers(
                sheet_name
            )
            if not sheet_phase_markers:
                for _, identity_row_text in rows[
                    :XLSX_IDENTITY_ROW_SAMPLE
                ]:
                    sheet_phase_markers = _xlsx_phase_markers(
                        identity_row_text
                    )
                    if sheet_phase_markers:
                        break
            content_digest = hashlib.sha256(
                "\n".join(
                    f"{row_number}:{row_text}"
                    for row_number, row_text in rows
                ).encode("utf-8")
            ).hexdigest()
            warning_codes = list(diagnostic["warning_codes"])
            quarantine_reason: str | None = None
            if (
                source_phase_markers
                and sheet_phase_markers
                and source_phase_markers.isdisjoint(
                    sheet_phase_markers
                )
            ):
                quarantine_reason = "project_phase_mismatch"
            elif rows and content_digest in seen_sheet_content:
                quarantine_reason = "duplicate_sheet_content"

            if quarantine_reason:
                diagnostic["status"] = "quarantined"
                warning_codes.append(quarantine_reason)
                diagnostic["quarantine_reason"] = (
                    quarantine_reason
                )
            elif rows:
                seen_sheet_content[content_digest] = sheet_name
            diagnostic["warning_codes"] = sorted(
                set(warning_codes)
            )
            diagnostic["source_phase_markers"] = sorted(
                source_phase_markers
            )
            diagnostic["sheet_phase_markers"] = sorted(
                sheet_phase_markers
            )
            sheet_diagnostics.append(diagnostic)
            if quarantine_reason or not rows:
                continue

            for row_number, row_text in rows:
                source_location = (
                    f"{sheet_name} 第{row_number}行"
                )
                line = f"[{source_location}] {row_text}"
                projected_size = (
                    extracted_character_count + len(line) + 1
                )
                if projected_size > MAX_EXTRACTED_TEXT_CHARS:
                    text_limit_reached = True
                    break
                extracted_character_count = projected_size
                lines.append(line)
                section_index = len(segments) + 1
                segments.append(
                    _segment_with_structure(
                        source_file=filename,
                        source_location=source_location,
                        text=row_text,
                        section_index=section_index,
                    )
                )
            if text_limit_reached:
                break
    finally:
        archive.close()

    warning_codes = sorted(
        {
            code
            for item in sheet_diagnostics
            for code in item.get("warning_codes", [])
        }
    )
    diagnostics = {
        "schema_version": "tender_xlsx_scan_v1",
        "sheet_count": len(sheet_parts),
        "parsed_sheet_count": sum(
            item.get("status") == "parsed"
            for item in sheet_diagnostics
        ),
        "quarantined_sheet_count": sum(
            item.get("status") == "quarantined"
            for item in sheet_diagnostics
        ),
        "skipped_sheet_count": sum(
            item.get("status") == "skipped"
            for item in sheet_diagnostics
        ),
        "extracted_segment_count": len(segments),
        "column_limit": XLSX_MAX_COLUMNS,
        "row_limit_per_sheet": (
            XLSX_MAX_NONEMPTY_ROWS_PER_SHEET
        ),
        "text_limit_reached": text_limit_reached,
        "warning_codes": warning_codes,
        "sheets": sheet_diagnostics,
    }
    return (
        _clean_text("\n".join(lines)),
        segments,
        len(sheet_parts),
        diagnostics,
    )


def extract_tender_text(content: bytes, filename: str | None, content_type: str | None = None) -> dict[str, Any]:
    filename = filename or "tender-file"
    suffix = Path(filename).suffix.lower()
    if not content:
        raise TenderParseError("文件为空")
    if len(content) > 80 * 1024 * 1024:
        raise TenderParseError("文件超过 80MB，BIZ-4a MVP 暂不支持")

    normalized_content_type = (content_type or "").lower()
    parse_diagnostics: dict[str, Any] | None = None
    if suffix == ".pdf":
        text, segments, page_count = _extract_pdf(content, filename)
    elif suffix == ".docx":
        text, segments, page_count = _extract_docx(content, filename)
    elif suffix == ".doc":
        raise TenderParseError("暂不支持旧版 Word .doc，请用 Word 另存为 .docx 后上传")
    elif suffix in {".xlsx", ".xlsm", ".xls"}:
        (
            text,
            segments,
            page_count,
            parse_diagnostics,
        ) = _extract_xlsx(content, filename)
    elif normalized_content_type == "application/pdf":
        text, segments, page_count = _extract_pdf(content, filename)
    elif suffix in {".txt", ".md", ".csv", ""} or normalized_content_type.startswith("text/"):
        text = _clean_text(_decode_text(content))
        segments = _segment_long_text(text, source_file=filename, source_location="文本")
        page_count = 0
    else:
        raise TenderParseError("暂只支持 .pdf/.docx/.xlsx/.xlsm/.txt/.md 文件")

    if not text.strip():
        raise TenderParseError("未能从文件中提取可分析文本，扫描版文件需先 OCR")
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        text = text[:MAX_EXTRACTED_TEXT_CHARS]
    segment_dicts = _apply_document_structure_context_to_dicts([segment.__dict__ for segment in segments])
    result = {
        "filename": filename,
        "parser_version": BIDDING_PARSER_VERSION,
        "text": text,
        "segments": segment_dicts,
        "page_count": page_count,
        "section_count": len(segment_dicts),
        "sha256": sha256_bytes(content),
    }
    if parse_diagnostics is not None:
        result["parse_diagnostics"] = parse_diagnostics
    return result


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _snippet(text: str, limit: int = 700) -> str:
    text = _clean_text(text)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _dedupe_key(*parts: str) -> str:
    raw = "|".join(re.sub(r"\s+", "", part or "")[:220] for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _structure_from_raw_segment(raw_segment: dict[str, Any], text: str) -> dict[str, Any]:
    document_section = raw_segment.get("document_section")
    if document_section:
        return {
            "document_section": str(document_section),
            "document_section_label": str(raw_segment.get("document_section_label") or "其他"),
            "is_structural_noise": bool(raw_segment.get("is_structural_noise")),
            "noise_reason": raw_segment.get("noise_reason"),
            "structure_confidence": float(raw_segment.get("structure_confidence") or 0.35),
        }
    return classify_document_structure(
        text,
        page_number=raw_segment.get("page_number"),
        source_location=str(raw_segment.get("source_location") or ""),
    )


def _count_map_increment(counter: dict[str, int], key: str | None) -> None:
    normalized = key or "unknown"
    counter[normalized] = counter.get(normalized, 0) + 1


def _requirement_text(rule: RequirementRule, text: str) -> str:
    return f"识别到{rule.label}，请在后续响应矩阵中确认是否满足并绑定证明材料。"


def _requirement_rule_allowed(requirement_type: str, document_section: str) -> bool:
    allowed = REQUIREMENT_ALLOWED_SECTIONS.get(requirement_type)
    return not allowed or document_section in allowed


def _risk_rule_allowed(risk_type: str, document_section: str) -> bool:
    allowed = RISK_ALLOWED_SECTIONS.get(risk_type)
    return not allowed or document_section in allowed


def _risk_matches(rule: RiskRule, text: str, *, document_section: str) -> bool:
    compact_text = _compact_for_structure(text)
    if rule.risk_type == "anonymous_bid":
        if "暗标" in compact_text:
            return True
        identity_terms = ("不得出现投标人名称", "不得出现单位名称", "不得出现标识", "投标人名称", "企业标识", "单位名称")
        has_identity_limit = any(_keyword_hit(term, compact_text=compact_text) for term in identity_terms)
        has_technical_context = "技术标" in compact_text or document_section in {"bid_format", "technical_requirements"}
        return has_technical_context and has_identity_limit and ("不得" in compact_text or "否决投标" in compact_text)
    if rule.risk_type == "no_price_adjustment":
        price_terms = ("价格", "价款", "单价", "总价", "综合单价", "措施项目费", "人工费", "材料费", "物价", "费率", "汇率")
        adjustment_terms = ("不予调整", "不会调整", "不作调整", "不做调整", "有所调整", "自行承担", "市场价格涨幅", "价格波动")
        has_adjustment_limit = any(term in compact_text for term in adjustment_terms) or ("不会" in compact_text and "调整" in compact_text)
        return any(term in compact_text for term in price_terms) and has_adjustment_limit
    if rule.risk_type == "material_brand_constraint":
        if ("不适用" in compact_text or "如有" in compact_text) and len(compact_text) <= 80:
            return False
    hit_count = sum(1 for keyword in rule.keywords if keyword in text)
    if rule.risk_type in {"delayed_payment", "liquidated_damages", "claim_time_limit", "site_condition"}:
        return hit_count >= 2
    return hit_count >= 1


def analyze_tender_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    segments = _apply_document_structure_context_to_dicts(segments)
    requirements: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    requirement_seen: set[str] = set()
    risk_seen: set[str] = set()
    question_seen: set[str] = set()
    segment_by_document_section: dict[str, int] = {}
    analyzed_by_document_section: dict[str, int] = {}
    ignored_by_reason: dict[str, int] = {}
    ignored_segment_count = 0
    short_segment_count = 0
    inherited_segment_count = 0

    for raw_segment in segments:
        text = _clean_text(str(raw_segment.get("text") or ""))
        if len(text) < 8:
            short_segment_count += 1
            continue
        source_file = str(raw_segment.get("source_file") or "")
        source_location = str(raw_segment.get("source_location") or "")
        structure = _structure_from_raw_segment(raw_segment, text)
        document_section = str(structure["document_section"])
        _count_map_increment(segment_by_document_section, document_section)
        if raw_segment.get("document_section_source") == "inherited":
            inherited_segment_count += 1
        if structure.get("is_structural_noise"):
            ignored_segment_count += 1
            _count_map_increment(ignored_by_reason, str(structure.get("noise_reason") or document_section))
            continue
        low_value_reason = _low_value_ignore_reason(text, document_section)
        if low_value_reason:
            ignored_segment_count += 1
            _count_map_increment(ignored_by_reason, low_value_reason)
            continue
        _count_map_increment(analyzed_by_document_section, document_section)

        for rule in REQUIREMENT_RULES:
            if not _requirement_rule_allowed(rule.requirement_type, document_section):
                continue
            if not _contains_any(text, rule.keywords):
                continue
            key = _dedupe_key(rule.requirement_type, text)
            if key in requirement_seen:
                continue
            requirement_seen.add(key)
            requirements.append(
                {
                    "requirement_type": rule.requirement_type,
                    "source_file": source_file,
                    "source_location": source_location,
                    "document_section": document_section,
                    "original_text": _snippet(text),
                    "parsed_requirement": _requirement_text(rule, text),
                    "compliance_status": "pending",
                    "risk_level": rule.risk_level,
                    "owner_role": rule.owner_role,
                    "output_section": rule.output_section,
                    "confidence": 0.68 if rule.requirement_type != "bid_void" else 0.82,
                    "extraction_method": "rule",
                }
            )

        for rule in RISK_RULES:
            if not _risk_rule_allowed(rule.risk_type, document_section):
                continue
            if not _risk_matches(rule, text, document_section=document_section):
                continue
            key = _dedupe_key(rule.risk_type, text)
            if key in risk_seen:
                continue
            risk_seen.add(key)
            risks.append(
                {
                    "risk_type": rule.risk_type,
                    "risk_level": rule.risk_level,
                    "source_file": source_file,
                    "source_location": source_location,
                    "document_section": document_section,
                    "original_text": _snippet(text),
                    "risk_explanation": rule.explanation,
                    "impact_area": rule.impact_area,
                    "suggested_action": rule.suggested_action,
                    "is_blocking": rule.is_blocking,
                    "review_status": "pending",
                    "confidence": rule.confidence,
                    "extraction_method": "rule",
                }
            )

        for question_type, pattern in QUESTION_PATTERNS:
            if not re.search(pattern, text):
                continue
            key = _dedupe_key(question_type, text)
            if key in question_seen:
                continue
            question_seen.add(key)
            questions.append(
                {
                    "question_type": question_type,
                    "source_file": source_file,
                    "source_location": source_location,
                    "document_section": document_section,
                    "original_text": _snippet(text, 500),
                    "suggested_question": f"{question_type}：建议在答疑阶段确认该条款的适用范围、计价口径和责任边界。",
                    "review_status": "pending",
                }
            )

    summary = build_analysis_summary(
        requirements,
        risks,
        questions,
        segment_count=len(segments),
        analyzed_segment_count=sum(analyzed_by_document_section.values()),
        ignored_segment_count=ignored_segment_count,
        short_segment_count=short_segment_count,
        inherited_segment_count=inherited_segment_count,
        segment_by_document_section=segment_by_document_section,
        analyzed_by_document_section=analyzed_by_document_section,
        ignored_by_reason=ignored_by_reason,
    )
    return {
        "parser_version": BIDDING_PARSER_VERSION,
        "requirements": requirements,
        "risks": risks,
        "questions": questions,
        "summary": summary,
    }


def build_analysis_summary(
    requirements: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    *,
    segment_count: int,
    analyzed_segment_count: int | None = None,
    ignored_segment_count: int = 0,
    short_segment_count: int = 0,
    inherited_segment_count: int = 0,
    segment_by_document_section: dict[str, int] | None = None,
    analyzed_by_document_section: dict[str, int] | None = None,
    ignored_by_reason: dict[str, int] | None = None,
) -> dict[str, Any]:
    requirement_by_type: dict[str, int] = {}
    risk_by_level: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    risk_by_type: dict[str, int] = {}
    for item in requirements:
        requirement_by_type[item["requirement_type"]] = requirement_by_type.get(item["requirement_type"], 0) + 1
    for item in risks:
        level = item.get("risk_level") or "medium"
        risk_by_level[level] = risk_by_level.get(level, 0) + 1
        risk_type = item.get("risk_type") or "unknown"
        risk_by_type[risk_type] = risk_by_type.get(risk_type, 0) + 1
    return {
        "segment_count": segment_count,
        "analyzed_segment_count": analyzed_segment_count if analyzed_segment_count is not None else segment_count,
        "ignored_segment_count": ignored_segment_count,
        "short_segment_count": short_segment_count,
        "inherited_segment_count": inherited_segment_count,
        "requirement_count": len(requirements),
        "risk_count": len(risks),
        "question_count": len(questions),
        "high_risk_count": risk_by_level.get("high", 0),
        "blocking_risk_count": sum(1 for item in risks if item.get("is_blocking")),
        "requirement_by_type": requirement_by_type,
        "risk_by_level": risk_by_level,
        "risk_by_type": risk_by_type,
        "document_structure": {
            "enabled": True,
            "version": DOCUMENT_STRUCTURE_VERSION,
            "segment_by_section": segment_by_document_section or {},
            "analyzed_by_section": analyzed_by_document_section or {},
            "ignored_by_reason": ignored_by_reason or {},
        },
        "llm_used": False,
        "analysis_mode": "rule_first_llm_ready",
    }


def loads_segments_from_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for file_info in files:
        extracted_text = file_info.get("extracted_text") or ""
        if not extracted_text:
            continue
        file_segments = _segment_long_text(
            extracted_text,
            source_file=str(file_info.get("source_file") or file_info.get("original_filename") or ""),
            source_location="已抽取文本",
        )
        segments.extend(segment.__dict__ for segment in file_segments)
    return _apply_document_structure_context_to_dicts(segments)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default
