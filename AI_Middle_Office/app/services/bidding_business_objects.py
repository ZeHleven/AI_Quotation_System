from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from app.services.bidding_risk_cards import cluster_tender_risks_to_cards


BUSINESS_OBJECT_TYPE_LABELS = {
    "bid_rule": "投标规则",
    "qualification": "资格审查",
    "contract_clause": "合同条款",
    "pricing_constraint": "报价约束",
    "document_checklist": "文件清单",
}

BUSINESS_OBJECT_TYPE_OWNER = {
    "bid_rule": "经营",
    "qualification": "经营",
    "contract_clause": "法务",
    "pricing_constraint": "预算",
    "document_checklist": "经营",
}

BUSINESS_OBJECT_SUBTYPE_LABELS = {
    "project_basic": "项目基本信息",
    "bid_deadline": "投标截止时间",
    "bid_open_time": "开标时间",
    "bid_validity": "投标有效期",
    "bid_bond": "投标保证金",
    "clarification_deadline": "答疑/澄清截止",
    "sealing_requirement": "密封要求",
    "anonymous_bid_requirement": "暗标要求",
    "signature_stamp": "签字盖章要求",
    "document_copies": "文件份数要求",
    "submission_method": "递交方式",
    "evaluation_method": "评标办法",
    "bid_rule_general": "其他投标规则",
    "enterprise_qualification": "企业资质要求",
    "safety_license": "安全生产许可证",
    "project_manager": "项目经理要求",
    "constructor_certificate": "建造师证书要求",
    "performance": "业绩要求",
    "financial_credit": "财务/信用要求",
    "joint_venture": "联合体要求",
    "subcontract_limit": "分包限制",
    "qualification_general": "其他资格要求",
    "payment_clause": "付款条款",
    "advance_payment": "预付款/垫资",
    "progress_payment": "进度款",
    "settlement_payment": "结算款",
    "retention_money": "质保金/保留金",
    "payment_condition": "付款前置条件",
    "payment_document_condition": "付款资料/审核条件",
    "invoice_payment_condition": "发票付款条件",
    "interim_payment_limitation": "中期付款限制",
    "preliminaries_payment_condition": "基本要求费用付款条件",
    "settlement_clause": "结算条款",
    "settlement_audit_review": "结算审计/评审",
    "settlement_document_requirement": "结算资料要求",
    "settlement_adjustment_boundary": "结算调整边界",
    "variation_claim": "变更签证索赔",
    "claim_notice_time_limit": "签证索赔时限",
    "claim_document_requirement": "签证资料要求",
    "counterclaim_deduction": "反索赔/扣款",
    "variation_procedure": "变更签证流程",
    "variation_approval_procedure": "变更审批流程",
    "variation_written_confirmation": "书面确认要求",
    "warranty_clause": "质保/保修条款",
    "warranty_period_responsibility": "保修期限责任",
    "defect_repair_obligation": "缺陷修补义务",
    "insurance_requirement": "保险要求",
    "liquidated_damages": "违约金条款",
    "schedule_delay_penalty": "工期逾期违约金",
    "quality_penalty": "质量/验收违约金",
    "material_quality_penalty": "材料质量违约金",
    "material_brand_standard": "材料品牌/样板标准",
    "material_inspection_acceptance": "材料进场验收",
    "material_replacement_responsibility": "不合格材料拆除重采",
    "material_penalty_deduction": "材料扣款/违约金",
    "personnel_penalty": "人员更换/到岗违约金",
    "termination_penalty": "解除/停工违约责任",
    "schedule_liability": "工期责任",
    "total_duration": "总工期/节点工期",
    "start_condition": "开工条件",
    "extension_condition": "工期顺延条件",
    "schedule_plan_submission": "进度计划报送",
    "rush_work_responsibility": "赶工责任",
    "acceptance_clause": "验收条件",
    "termination_clause": "解除合同条款",
    "scope_boundary": "范围界面",
    "site_condition_responsibility": "现场条件责任",
    "scope_change_boundary": "范围调整/界面迁移",
    "drawing_review": "图纸会审/深化设计",
    "drawing_deepening_design": "深化设计责任",
    "drawing_error_liability": "图纸错漏责任",
    "drawing_handover_review": "图纸会审/交底",
    "asbuilt_drawing_record": "竣工图/记录图纸",
    "permit_procedure": "报批报建/手续",
    "technical_requirement": "技术质量要求",
    "contract_general": "其他合同条款",
    "fixed_total_price": "固定总价/总价包干",
    "no_price_adjustment": "价格不调整",
    "unit_price_no_adjustment": "综合单价不调整",
    "measure_fee_no_adjustment": "措施费/开办费不调整",
    "market_price_no_adjustment": "人工材料价格波动不调整",
    "general_price_no_adjustment": "合同价款不调整",
    "omission_liability": "漏项责任",
    "preliminaries_included": "开办费/基本要求费用已包含",
    "measure_fee_lump_sum": "措施费/开办费包干",
    "temporary_quantity": "暂定量/暂定项目",
    "owner_supplied_material": "甲供材料",
    "brand_constraint": "指定/甲限品牌",
    "tax_or_quote_scope": "税率/报价口径",
    "pricing_general": "其他报价约束",
    "business_bid": "商务标文件",
    "technical_bid": "技术标文件",
    "price_bill": "报价清单",
    "authorization": "授权委托书",
    "commitment_letter": "承诺函",
    "qualification_file": "资格文件",
    "certificate_attachment": "证书附件",
    "performance_proof": "业绩证明",
    "deviation_table": "偏离表",
    "response_table": "响应表",
    "material_brand_table": "材料品牌表",
    "document_general": "其他文件清单",
}

BUSINESS_ACTION_LABELS = {
    "bid_compliance": "投标合规检查",
    "qualification_response": "资格响应",
    "document_response": "文件编制",
    "quote_allowance": "报价预留",
    "clarification": "转答疑",
    "legal_review": "法务复核",
    "delivery_planning": "履约策划",
    "reference": "信息参考",
}

BUSINESS_ACTION_OWNER = {
    "bid_compliance": "经营",
    "qualification_response": "经营",
    "document_response": "经营",
    "quote_allowance": "预算",
    "clarification": "经营",
    "legal_review": "法务",
    "delivery_planning": "技术",
    "reference": "经营",
}

ACTION_RESPONSE_REQUIRED = {
    "bid_compliance",
    "qualification_response",
    "document_response",
    "quote_allowance",
    "clarification",
}

VALUE_SENSITIVE_SUBTYPES = {
    "project_basic",
    "bid_deadline",
    "bid_open_time",
    "clarification_deadline",
    "bid_bond",
    "document_copies",
}

LOW_SIGNAL_REQUIREMENT_SUBTYPES = {
    "contract_general",
    "pricing_general",
    "bid_rule_general",
    "document_general",
}

QUOTE_ALLOWANCE_SUBTYPES = {
    "fixed_total_price",
    "no_price_adjustment",
    "unit_price_no_adjustment",
    "measure_fee_no_adjustment",
    "market_price_no_adjustment",
    "general_price_no_adjustment",
    "omission_liability",
    "preliminaries_included",
    "measure_fee_lump_sum",
    "temporary_quantity",
    "owner_supplied_material",
    "brand_constraint",
    "tax_or_quote_scope",
}

LEGAL_REVIEW_SUBTYPES = {
    "advance_payment",
    "progress_payment",
    "settlement_payment",
    "retention_money",
    "payment_condition",
    "payment_document_condition",
    "invoice_payment_condition",
    "interim_payment_limitation",
    "preliminaries_payment_condition",
    "payment_clause",
    "settlement_clause",
    "settlement_audit_review",
    "settlement_document_requirement",
    "settlement_adjustment_boundary",
    "variation_claim",
    "claim_notice_time_limit",
    "claim_document_requirement",
    "counterclaim_deduction",
    "variation_procedure",
    "variation_approval_procedure",
    "variation_written_confirmation",
    "liquidated_damages",
    "schedule_delay_penalty",
    "quality_penalty",
    "material_quality_penalty",
    "material_replacement_responsibility",
    "material_penalty_deduction",
    "personnel_penalty",
    "termination_penalty",
    "termination_clause",
    "warranty_period_responsibility",
    "defect_repair_obligation",
    "insurance_requirement",
}

DELIVERY_PLANNING_SUBTYPES = {
    "schedule_liability",
    "total_duration",
    "start_condition",
    "extension_condition",
    "schedule_plan_submission",
    "rush_work_responsibility",
    "acceptance_clause",
    "scope_boundary",
    "site_condition_responsibility",
    "scope_change_boundary",
    "drawing_review",
    "drawing_deepening_design",
    "drawing_error_liability",
    "drawing_handover_review",
    "asbuilt_drawing_record",
    "permit_procedure",
    "technical_requirement",
    "material_brand_standard",
    "material_inspection_acceptance",
    "warranty_clause",
}


def _text_of(item: Any) -> str:
    return " ".join(
        str(getattr(item, field, "") or "")
        for field in ("original_text", "parsed_requirement", "risk_explanation", "suggested_action")
    )


def _compact(value: str | None) -> str:
    return re.sub(r"[\s\u3000:：，。、；;,.!！?？\-_/\\()（）【】\[\]「」『』]+", "", value or "")


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    compact_text = _compact(text)
    return any(_compact(keyword) in compact_text for keyword in keywords)


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.I)
        if match:
            return match.group(0)[:120]
    return None


def _extract_datetime(text: str) -> str | None:
    return _first_match(
        text,
        (
            r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{0,2}\s*[时:：]?\s*\d{0,2}\s*分?",
            r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*\d{0,2}[:：]?\d{0,2}",
        ),
    )


def _extract_amount(text: str) -> str | None:
    return _first_match(text, (r"\d+(?:\.\d+)?\s*(?:万元|元|人民币)",))


def _extract_percent(text: str) -> str | None:
    return _first_match(text, (r"\d+(?:\.\d+)?\s*(?:%|％)",))


def _extract_days(text: str) -> str | None:
    return _first_match(text, (r"\d+\s*(?:日历天|天|日)",))


def _extract_copies(text: str) -> str | None:
    return _first_match(text, (r"\d+\s*(?:份|套|册)",))


def _business_subtype_title(subtype: str) -> str:
    return BUSINESS_OBJECT_SUBTYPE_LABELS.get(subtype, subtype)


def _normalized_value_for_subtype(subtype: str, text: str) -> str | None:
    if subtype in {"bid_deadline", "bid_open_time", "clarification_deadline"}:
        return _extract_datetime(text)
    if subtype in {"bid_bond"}:
        return _extract_amount(text) or _extract_percent(text)
    if subtype in {
        "bid_validity",
        "schedule_liability",
        "total_duration",
        "start_condition",
        "extension_condition",
        "schedule_plan_submission",
        "rush_work_responsibility",
        "warranty_clause",
        "claim_notice_time_limit",
        "schedule_delay_penalty",
        "quality_penalty",
        "material_quality_penalty",
        "personnel_penalty",
        "termination_penalty",
    }:
        return _extract_days(text)
    if subtype == "document_copies":
        return _extract_copies(text)
    if subtype in {
        "payment_clause",
        "advance_payment",
        "progress_payment",
        "settlement_payment",
        "retention_money",
        "payment_condition",
        "settlement_clause",
    }:
        return _extract_percent(text) or _extract_days(text)
    return None


def _bid_rule_subtype(text: str) -> str:
    if _has_any(text, ("答疑", "澄清", "补遗", "疑问")):
        return "clarification_deadline"
    if _has_any(text, ("开标时间", "开标")):
        return "bid_open_time"
    if _has_any(text, ("投标保证金", "投标担保", "投标保函", "保证金", "保函")):
        return "bid_bond"
    if _has_any(text, ("投标截止", "递交截止", "回标截止")):
        return "bid_deadline"
    if _has_any(text, ("投标有效期", "有效期")):
        return "bid_validity"
    if _has_any(text, ("密封", "封套", "封标")):
        return "sealing_requirement"
    if _has_any(text, ("暗标", "不得出现投标人名称", "单位名称", "企业标识")):
        return "anonymous_bid_requirement"
    if _has_any(text, ("签字", "盖章", "签章", "法定代表人")):
        return "signature_stamp"
    if _has_any(text, ("正本", "副本", "份", "套", "册")):
        return "document_copies"
    if _has_any(text, ("递交", "上传", "电子投标", "U盘", "光盘")):
        return "submission_method"
    if _has_any(text, ("评标", "评分", "综合评估", "权重")):
        return "evaluation_method"
    return "bid_rule_general"


def _qualification_subtype(text: str) -> str:
    if _has_any(text, ("安全生产许可证", "安全许可证")):
        return "safety_license"
    if _has_any(text, ("项目经理", "项目负责人")):
        return "project_manager"
    if _has_any(text, ("建造师", "注册证书", "执业资格")):
        return "constructor_certificate"
    if _has_any(text, ("业绩", "类似工程", "合同金额")):
        return "performance"
    if _has_any(text, ("财务", "信用", "征信", "失信")):
        return "financial_credit"
    if _has_any(text, ("联合体", "联合投标")):
        return "joint_venture"
    if _has_any(text, ("分包", "转包")):
        return "subcontract_limit"
    if _has_any(text, ("资质", "专业承包", "承包资质")):
        return "enterprise_qualification"
    return "qualification_general"


def _payment_subtype(text: str) -> str:
    if _has_any(text, ("无预付款", "不支付预付款", "预付款", "垫资")):
        return "advance_payment"
    if _has_any(text, ("进度款", "月进度", "每月支付", "工程款申请", "资金申请")):
        return "progress_payment"
    if _has_any(text, ("结算款", "竣工支付", "竣工结算", "支付至结算", "结算总价")):
        return "settlement_payment"
    if _has_any(text, ("质保金", "质量缺陷保证金", "保留金", "缺陷保修金")):
        return "retention_money"
    if _has_any(text, ("履约保函", "付款申请", "支付流程", "共管账户", "前置条件", "审核后支付")):
        return "payment_condition"
    return "payment_clause"


def _penalty_subtype(text: str) -> str:
    if _has_any(text, ("解除合同", "终止合同", "停止履行", "单方解除", "停工")):
        return "termination_penalty"
    if _has_any(text, ("工期", "延期", "逾期", "竣工", "节点")):
        return "schedule_delay_penalty"
    if _has_any(text, ("材料", "设备", "货款", "假冒伪劣", "不合格", "检测")):
        return "material_quality_penalty"
    if _has_any(text, ("项目经理", "技术负责人", "施工负责人", "人员", "调离", "更换", "到岗")):
        return "personnel_penalty"
    if _has_any(text, ("质量", "验收", "整改", "合格", "保修")):
        return "quality_penalty"
    return "liquidated_damages"


def _schedule_subtype(text: str) -> str:
    if _has_any(text, ("开工", "开工令", "进场")):
        return "start_condition"
    if _has_any(text, ("顺延", "延期申请", "工期延长", "不予顺延", "不被批准")):
        return "extension_condition"
    if _has_any(text, ("进度计划", "总计划", "月计划", "周计划", "季度计划", "施工组织设计", "报送", "提交")):
        return "schedule_plan_submission"
    if _has_any(text, ("赶工", "抢工", "必要的赶工")):
        return "rush_work_responsibility"
    if _has_any(text, ("总工期", "合同工期", "节点", "日历天", "竣工时间")):
        return "total_duration"
    return "schedule_liability"


def _scope_subtype(text: str) -> str:
    if _has_any(text, ("现场踏勘", "工地现场", "道路", "储存空间", "装卸限制", "现场条件")):
        return "site_condition_responsibility"
    if _has_any(text, ("迁移", "工作界面", "工程位置", "范围调整", "施工范围", "界面划分")):
        return "scope_change_boundary"
    if _has_any(text, ("图纸", "深化设计", "会审", "错漏", "设计资料", "设计变更")):
        return "drawing_review"
    if _has_any(text, ("手续", "批准文件", "报建", "许可证", "当地有关规定")):
        return "permit_procedure"
    return "scope_boundary"


def _variation_subtype(text: str) -> str:
    if _has_any(text, ("反索赔", "扣款", "扣除", "扣减")):
        return "counterclaim_deduction"
    if _has_any(text, ("资料", "依据", "签证单", "书面正式文件", "下料单", "排产单", "生产计划")):
        return "claim_document_requirement"
    if _has_any(text, ("天内", "时限", "期限", "逾期", "收到后", "通知后")):
        return "claim_notice_time_limit"
    return "variation_procedure"


def _contract_subtype(text: str) -> str:
    if _has_any(text, ("违约金", "扣罚", "罚款", "赔偿", "扣减", "扣除", "反索赔")):
        return _penalty_subtype(text)
    if _has_any(text, ("解除合同", "终止合同", "单方解除")):
        return "termination_clause"
    if _has_any(text, ("手续", "批准文件", "报建", "许可证", "当地有关规定")):
        return _scope_subtype(text)
    if _has_any(text, ("工期", "延期", "逾期", "顺延", "进度计划", "总计划", "月计划", "周计划", "季度计划", "施工组织设计")):
        return _schedule_subtype(text)
    if _has_any(text, ("质保金", "质量缺陷保证金", "保留金", "缺陷保修金")):
        return "retention_money"
    if _has_any(text, ("预付款", "付款", "支付", "进度款", "工程款", "共管账户")):
        return _payment_subtype(text)
    if _has_any(text, ("结算", "审计", "财政评审", "竣工结算")):
        return "settlement_clause"
    if _has_any(text, ("变更", "签证", "索赔", "洽商")):
        return _variation_subtype(text)
    if _has_any(text, ("质保", "保修", "缺陷责任")):
        return "warranty_clause"
    if _has_any(text, ("范围", "界面", "图纸", "现场")):
        return _scope_subtype(text)
    if _has_any(text, ("验收", "竣工", "移交")):
        return "acceptance_clause"
    if _has_any(text, ("质量", "技术要求", "施工工艺", "规范")):
        return "technical_requirement"
    return "contract_general"


def _pricing_subtype(text: str, risk_type: str | None = None) -> str:
    if risk_type == "fixed_total_price" or _has_any(text, ("固定总价", "总价包干", "合同总价")):
        return "fixed_total_price"
    if risk_type == "no_price_adjustment" or _has_any(text, ("不予调整", "不作调整", "价格波动", "综合单价")):
        if _has_any(text, ("措施费", "开办费")):
            return "measure_fee_no_adjustment"
        if _has_any(text, ("人工", "材料", "物价", "市场价格", "价格波动", "价差")):
            return "market_price_no_adjustment"
        if _has_any(text, ("综合单价", "单价")):
            return "unit_price_no_adjustment"
        if _has_any(text, ("合同价款", "合同价", "含税单价", "不含税")):
            return "general_price_no_adjustment"
        return "no_price_adjustment"
    if risk_type == "omission_liability" or _has_any(text, ("漏项", "错项", "视为已包含")):
        if _has_any(text, ("开办费", "基本要求")):
            return "preliminaries_included"
        return "omission_liability"
    if _has_any(text, ("措施费", "开办费")):
        return "measure_fee_lump_sum"
    if _has_any(text, ("暂定", "暂列", "暂估")):
        return "temporary_quantity"
    if _has_any(text, ("甲供", "独立供应")):
        return "owner_supplied_material"
    if risk_type == "material_brand_constraint" or _has_any(text, ("指定品牌", "甲限", "品牌范围", "认质认价")):
        return "brand_constraint"
    if _has_any(text, ("税率", "含税", "不含税", "报价口径")):
        return "tax_or_quote_scope"
    return "pricing_general"


def _document_subtype(text: str) -> str:
    if _has_any(text, ("商务标", "商务文件")):
        return "business_bid"
    if _has_any(text, ("技术标", "施工组织设计", "技术文件")):
        return "technical_bid"
    if _has_any(text, ("报价清单", "工程量清单", "清单报价")):
        return "price_bill"
    if _has_any(text, ("授权委托", "委托书")):
        return "authorization"
    if _has_any(text, ("承诺函", "承诺书")):
        return "commitment_letter"
    if _has_any(text, ("资格文件", "资格审查")):
        return "qualification_file"
    if _has_any(text, ("证书", "许可证", "建造师")):
        return "certificate_attachment"
    if _has_any(text, ("业绩证明", "业绩")):
        return "performance_proof"
    if _has_any(text, ("偏离表", "偏差表")):
        return "deviation_table"
    if _has_any(text, ("响应表", "响应矩阵")) and not _has_any(text, ("废标", "否决投标", "无效投标", "重大偏差", "实质性不响应")):
        return "response_table"
    if _has_any(text, ("材料品牌表", "拟采用的材料品牌", "品牌表")):
        return "material_brand_table"
    return "document_general"


def _object_type_and_subtype_from_requirement(requirement: Any) -> tuple[str, str] | None:
    requirement_type = getattr(requirement, "requirement_type", "") or ""
    text = _text_of(requirement)
    original_text = str(getattr(requirement, "original_text", None) or text)
    if requirement_type == "project_basic":
        return "bid_rule", "project_basic"
    if requirement_type == "bid_rule":
        return "bid_rule", _bid_rule_subtype(text)
    if requirement_type == "evaluation":
        return "bid_rule", "evaluation_method"
    if requirement_type == "qualification":
        return "qualification", _qualification_subtype(text)
    if requirement_type == "contract":
        return "contract_clause", _contract_subtype(text)
    if requirement_type in {"scope", "technical"}:
        return "contract_clause", _contract_subtype(text)
    if requirement_type == "material":
        if _has_any(original_text, ("材料品牌表", "拟采用的材料品牌", "品牌表", "材料样板", "样板报审")):
            return "document_checklist", _document_subtype(original_text)
        return "pricing_constraint", _pricing_subtype(text)
    if requirement_type in {"format", "bid_void"}:
        return "document_checklist", _document_subtype(original_text)
    return None


def _object_type_and_subtype_from_risk(risk: Any) -> tuple[str, str] | None:
    risk_type = getattr(risk, "risk_type", "") or ""
    text = _text_of(risk)
    if risk_type in {"fixed_total_price", "no_price_adjustment", "omission_liability", "material_brand_constraint"}:
        return "pricing_constraint", _pricing_subtype(text, risk_type)
    if risk_type == "design_or_drawing_unclear":
        if _has_any(text, ("暂定", "暂列", "暂估", "包干", "综合单价", "漏项", "报价", "合同价款")):
            return "pricing_constraint", _pricing_subtype(text, risk_type)
        return "contract_clause", _scope_subtype(text)
    if risk_type == "claim_time_limit":
        return "contract_clause", _variation_subtype(text)
    if risk_type in {"advance_funding", "delayed_payment", "liquidated_damages", "site_condition"}:
        return "contract_clause", _contract_subtype(text)
    if risk_type in {"bid_rejection", "anonymous_bid"}:
        return "bid_rule", "anonymous_bid_requirement" if risk_type == "anonymous_bid" else "bid_rule_general"
    return None


def _risk_identity(item: Any) -> tuple[int | None, str | None]:
    item_id = getattr(item, "id", None)
    try:
        numeric_id = int(item_id) if item_id is not None else None
    except (TypeError, ValueError):
        numeric_id = None
    risk_uuid = getattr(item, "risk_uuid", None)
    return numeric_id, str(risk_uuid) if risk_uuid else None


def _build_risk_context_index(risks: list[Any]) -> dict[tuple[str, Any], dict[str, Any]]:
    if not risks:
        return {}
    by_uuid: dict[str, Any] = {}
    by_id: dict[int, Any] = {}
    for risk in risks:
        risk_id, risk_uuid = _risk_identity(risk)
        if risk_uuid:
            by_uuid[risk_uuid] = risk
        if risk_id is not None:
            by_id[risk_id] = risk

    index: dict[tuple[str, Any], dict[str, Any]] = {}
    for card in cluster_tender_risks_to_cards(risks):
        context = {
            "risk_card_id": card.get("card_id"),
            "risk_card_title": card.get("title"),
            "risk_subtype": card.get("risk_subtype"),
            "risk_grade_v2": card.get("risk_grade_v2"),
            "risk_score": card.get("risk_score"),
            "primary_action": card.get("primary_action"),
            "secondary_action": card.get("secondary_action"),
            "review_roles": card.get("review_roles") or [],
            "suggested_action": card.get("suggested_action"),
        }
        for risk_uuid in card.get("member_risk_uuids") or []:
            if risk_uuid:
                index[("uuid", str(risk_uuid))] = context
                risk = by_uuid.get(str(risk_uuid))
                risk_id, _ = _risk_identity(risk) if risk else (None, None)
                if risk_id is not None:
                    index[("id", risk_id)] = context
    return index


def _risk_context_for(item: Any, index: dict[tuple[str, Any], dict[str, Any]]) -> dict[str, Any]:
    risk_id, risk_uuid = _risk_identity(item)
    if risk_uuid and ("uuid", risk_uuid) in index:
        return index[("uuid", risk_uuid)]
    if risk_id is not None and ("id", risk_id) in index:
        return index[("id", risk_id)]
    return {}


def _action_from_risk_context(risk_context: dict[str, Any]) -> str | None:
    primary = risk_context.get("primary_action")
    secondary = risk_context.get("secondary_action")
    if primary == "to_quote_allowance":
        return "quote_allowance"
    if primary == "to_clarify":
        return "clarification"
    if primary == "manual_blocking_review":
        return "bid_compliance"
    if primary == "bid_decision_review":
        return "legal_review"
    if secondary == "to_quote_allowance":
        return "quote_allowance"
    if secondary == "to_clarify":
        return "clarification"
    return None


def _suggested_review_status(action: str, risk_context: dict[str, Any] | None = None) -> str:
    primary = (risk_context or {}).get("primary_action")
    if primary in {"to_quote_allowance", "to_clarify"}:
        return str(primary)
    if action == "quote_allowance":
        return "to_quote_allowance"
    if action == "clarification":
        return "to_clarify"
    return "pending"


def _business_action_for(
    object_type: str,
    object_subtype: str,
    *,
    has_risk: bool,
    text: str,
    risk_context: dict[str, Any] | None = None,
) -> str:
    risk_action = _action_from_risk_context(risk_context or {})
    if risk_action:
        return risk_action
    if object_subtype in QUOTE_ALLOWANCE_SUBTYPES:
        return "quote_allowance"
    if object_type == "qualification":
        return "qualification_response"
    if object_type == "document_checklist":
        return "document_response"
    if object_type == "bid_rule":
        if object_subtype == "project_basic":
            return "reference"
        return "bid_compliance"
    if object_subtype in LEGAL_REVIEW_SUBTYPES:
        return "legal_review"
    if object_subtype in DELIVERY_PLANNING_SUBTYPES:
        if has_risk and _has_any(text, ("不予索赔", "不予顺延", "不被批准", "不被认可", "风险", "自行承担", "不作调整", "不得索赔")):
            return "clarification"
        return "delivery_planning"
    if has_risk:
        return "legal_review"
    return "reference"


def _response_required_for(action: str) -> bool:
    return action in ACTION_RESPONSE_REQUIRED


def _should_keep_candidate(candidate: dict[str, Any], *, source_kind: str) -> bool:
    if source_kind != "requirement":
        return True
    if candidate["object_subtype"] in LOW_SIGNAL_REQUIREMENT_SUBTYPES:
        return bool(candidate.get("normalized_value"))
    return True


ACTION_PRIORITY = {
    "quote_allowance": 80,
    "clarification": 75,
    "bid_compliance": 70,
    "legal_review": 60,
    "qualification_response": 55,
    "document_response": 55,
    "delivery_planning": 45,
    "reference": 10,
}


SUBTYPE_EVIDENCE_KEYWORDS = {
    "project_basic": ("工程名称", "项目名称", "建设地点", "工程地点", "工程规模", "承包方式", "工程概况"),
    "bid_deadline": ("投标截止", "递交截止", "回标截止", "截止时间"),
    "bid_open_time": ("开标时间", "开标"),
    "bid_validity": ("投标有效期", "有效期"),
    "bid_bond": ("投标保证金", "投标担保", "投标保函", "保证金", "保函"),
    "clarification_deadline": ("答疑", "澄清", "补遗", "疑问", "截止"),
    "sealing_requirement": ("密封", "封套", "封标"),
    "anonymous_bid_requirement": ("暗标", "不得出现投标人名称", "单位名称", "企业标识"),
    "signature_stamp": ("签字", "盖章", "签章", "法定代表人"),
    "document_copies": ("正本", "副本", "份", "套", "册"),
    "submission_method": ("递交", "上传", "电子投标", "U盘", "光盘"),
    "evaluation_method": ("评标", "评分", "综合评估", "权重"),
    "advance_payment": ("预付款", "垫资", "不支付预付款", "无预付款"),
    "progress_payment": ("进度款", "月进度", "每月支付", "工程款申请"),
    "settlement_payment": ("结算款", "竣工支付", "竣工结算", "支付至结算"),
    "retention_money": ("质保金", "保留金", "缺陷保证金", "保修金"),
    "payment_condition": ("付款申请", "支付流程", "审核后支付", "共管账户", "前置条件"),
    "payment_document_condition": ("付款资料", "付款申请", "审核", "审批", "证明文件", "资金申请"),
    "invoice_payment_condition": ("发票", "增值税", "开票", "收据"),
    "interim_payment_limitation": ("中期付款", "暂不支付", "不会支付", "支付限制"),
    "preliminaries_payment_condition": ("基本要求", "开办费", "中期付款", "费用"),
    "payment_clause": ("付款", "支付", "工程款", "进度款", "结算款"),
    "settlement_clause": ("结算", "审计", "财政评审", "竣工结算"),
    "settlement_audit_review": ("结算审计", "审计", "财政评审", "评审", "审核"),
    "settlement_document_requirement": ("结算资料", "竣工资料", "结算文件", "资料清单"),
    "settlement_adjustment_boundary": ("结算调整", "不予调整", "包干", "单价", "价款调整"),
    "variation_claim": ("变更", "签证", "索赔", "洽商"),
    "warranty_clause": ("质保", "保修", "缺陷责任"),
    "warranty_period_responsibility": ("保修期", "质保期", "缺陷责任期", "期限"),
    "defect_repair_obligation": ("缺陷", "修补", "维修", "整改", "保修责任"),
    "insurance_requirement": ("保险", "工程一切险", "第三者责任险", "保险期限"),
    "liquidated_damages": ("违约金", "扣罚", "罚款", "赔偿"),
    "schedule_delay_penalty": ("逾期", "延期", "违约金", "工期"),
    "quality_penalty": ("质量", "验收", "整改", "违约金"),
    "material_quality_penalty": ("材料", "设备", "不合格", "扣减", "违约金"),
    "material_brand_standard": ("材料", "品牌", "样板", "规格", "型号", "质量等级"),
    "material_inspection_acceptance": ("材料", "设备", "进场", "验收", "检测", "检验"),
    "material_replacement_responsibility": ("不合格", "拆除", "重新采购", "更换", "返工"),
    "material_penalty_deduction": ("材料", "设备", "扣减", "货款", "违约金", "赔偿"),
    "personnel_penalty": ("项目经理", "人员", "更换", "调离", "违约金"),
    "termination_penalty": ("解除", "终止", "违约金", "赔偿"),
    "total_duration": ("总工期", "合同工期", "日历天", "节点"),
    "start_condition": ("开工", "开工令", "进场"),
    "extension_condition": ("顺延", "延期申请", "不被批准"),
    "schedule_plan_submission": ("进度计划", "月计划", "周计划", "提交"),
    "site_condition_responsibility": ("现场踏勘", "道路", "储存", "装卸", "现场条件"),
    "scope_change_boundary": ("迁移", "界面", "范围调整", "工程位置"),
    "scope_boundary": ("范围", "界面", "施工范围", "承包范围", "界面划分"),
    "drawing_review": ("图纸", "深化设计", "会审", "错漏"),
    "drawing_deepening_design": ("深化设计", "深化", "设计标准", "设计责任"),
    "drawing_error_liability": ("图纸", "错漏", "缺失", "不详", "设计资料"),
    "drawing_handover_review": ("图纸", "会审", "交底", "审核", "提供"),
    "asbuilt_drawing_record": ("竣工图", "记录图纸", "使用说明书", "原稿"),
    "permit_procedure": ("手续", "批准", "报建", "许可证"),
    "technical_requirement": ("质量标准", "验收标准", "施工工艺", "技术标准", "安全文明", "成品保护"),
    "claim_notice_time_limit": ("天内", "时限", "期限", "逾期"),
    "claim_document_requirement": ("资料", "依据", "签证单", "书面正式文件"),
    "counterclaim_deduction": ("反索赔", "扣款", "扣除", "扣减"),
    "variation_procedure": ("变更", "签证", "洽商", "审批", "流程", "书面确认"),
    "variation_approval_procedure": ("变更", "审批", "批准", "流程", "程序", "指令"),
    "variation_written_confirmation": ("书面", "确认", "指令", "签认", "正式文件"),
    "fixed_total_price": ("固定总价", "总价包干", "合同总价", "总价"),
    "no_price_adjustment": ("价格波动", "不予调整", "不作调整", "自行承担"),
    "unit_price_no_adjustment": ("综合单价", "单价", "不作调整"),
    "measure_fee_no_adjustment": ("措施费", "开办费", "不调整"),
    "market_price_no_adjustment": ("人工", "材料", "价格波动", "市场"),
    "general_price_no_adjustment": ("合同价款", "合同价", "不作调整"),
    "omission_liability": ("漏项", "错项", "视为已包含", "不另行计取", "项目特征"),
    "preliminaries_included": ("开办费", "基本要求", "已包含"),
    "measure_fee_lump_sum": ("措施费", "开办费", "包干"),
    "temporary_quantity": ("暂定", "暂列", "暂估", "按实"),
    "owner_supplied_material": ("甲供", "甲方供应", "独立供应", "到货", "供应边界"),
    "brand_constraint": ("指定品牌", "甲限", "甲指", "品牌范围", "认质认价", "同档次"),
    "tax_or_quote_scope": ("税率", "含税", "不含税", "报价口径"),
    "business_bid": ("商务标", "商务文件"),
    "technical_bid": ("技术标", "施工组织设计", "技术文件"),
    "price_bill": ("报价清单", "工程量清单", "清单报价"),
    "authorization": ("授权委托", "委托书"),
    "commitment_letter": ("承诺函", "承诺书"),
    "qualification_file": ("资格文件", "资格审查"),
    "certificate_attachment": ("证书", "许可证", "建造师"),
    "performance_proof": ("业绩证明", "业绩"),
    "deviation_table": ("偏离表", "偏差表"),
    "response_table": ("响应表", "响应矩阵", "响应文件"),
    "material_brand_table": ("材料品牌表", "拟采用的材料品牌", "品牌表"),
}


SUBTYPE_NEGATIVE_EVIDENCE_KEYWORDS = {
    "project_basic": ("密封", "封套", "盖章", "签字", "正本", "副本", "废标", "否决投标"),
    "brand_constraint": ("进度款", "工程款", "付款", "支付", "结算", "质保金"),
    "owner_supplied_material": ("进度款", "工程款", "付款", "支付", "施工质量", "质量控制"),
    "response_table": ("废标", "否决投标", "无效投标", "重大偏差", "实质性不响应"),
    "scope_boundary": ("打印", "复印", "副本", "页码", "密封", "封套"),
    "material_brand_table": ("进度款", "付款", "支付", "结算"),
    "payment_clause": ("目录", "页数", "合同协议书", "建设单位", "总承包单位"),
    "settlement_clause": ("目录", "页数", "合同协议书", "建设单位", "总承包单位"),
    "settlement_audit_review": ("目录", "页数", "合同协议书", "建设单位", "总承包单位"),
    "scope_change_boundary": ("目录", "页数", "封面", "合同协议书"),
    "drawing_review": ("目录", "页数", "封面"),
}


RISK_SUBTYPE_TO_OBJECT_SUBTYPE = {
    "fixed_total_contract": "fixed_total_price",
    "unit_price_no_adjustment": "unit_price_no_adjustment",
    "measure_fee_no_adjustment": "measure_fee_no_adjustment",
    "market_price_no_adjustment": "market_price_no_adjustment",
    "general_price_no_adjustment": "general_price_no_adjustment",
    "missing_item_included": "omission_liability",
    "preliminaries_included": "preliminaries_included",
    "quantity_or_item_tentative": "temporary_quantity",
    "drawing_or_scope_unclear": "drawing_review",
    "site_condition_claim_rejected": "site_condition_responsibility",
    "advance_payment_none": "advance_payment",
    "claim_evidence_strict": "claim_document_requirement",
    "counter_claim_or_deduction": "counterclaim_deduction",
    "schedule_delay_penalty": "schedule_delay_penalty",
    "quality_material_penalty": "material_quality_penalty",
    "termination_penalty": "termination_penalty",
    "owner_supplied_material": "owner_supplied_material",
    "owner_limited_brand": "brand_constraint",
    "sample_or_approval": "brand_constraint",
    "bid_rejection_clause": "bid_rule_general",
    "anonymous_bid_identity": "anonymous_bid_requirement",
}


STRICT_REPRESENTATIVE_SUBTYPES = set(SUBTYPE_EVIDENCE_KEYWORDS)
NON_RISK_PREFERRED_SUBTYPES = {"project_basic", "response_table", "material_brand_table", "deviation_table"}
LARGE_OBJECT_SOURCE_COUNT_THRESHOLD = 20
DEFAULT_EVIDENCE_SAMPLE_LIMIT = 10
LARGE_OBJECT_EVIDENCE_SAMPLE_LIMIT = 8
STRUCTURAL_EVIDENCE_SECTIONS = {"table_of_contents", "cover"}
WEAK_EVIDENCE_SECTIONS = {"other"}
BODY_EVIDENCE_SECTIONS = {
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


SECONDARY_SPLIT_PARENT_SUBTYPES = {
    "material_quality_penalty",
    "drawing_review",
    "variation_procedure",
    "payment_clause",
    "settlement_clause",
    "warranty_clause",
    "scope_boundary",
}


OBJECT_SUBTYPE_SECONDARY_ACTIONS = {
    "material_brand_standard": ("quote_allowance", "delivery_planning"),
    "material_inspection_acceptance": ("delivery_planning",),
    "material_replacement_responsibility": ("legal_review", "delivery_planning"),
    "material_penalty_deduction": ("quote_allowance", "delivery_planning"),
    "drawing_deepening_design": ("clarification", "quote_allowance"),
    "drawing_error_liability": ("clarification", "quote_allowance"),
    "drawing_handover_review": ("delivery_planning",),
    "asbuilt_drawing_record": ("delivery_planning",),
    "variation_approval_procedure": ("legal_review", "delivery_planning"),
    "variation_written_confirmation": ("legal_review", "delivery_planning"),
    "payment_document_condition": ("legal_review",),
    "invoice_payment_condition": ("legal_review",),
    "interim_payment_limitation": ("quote_allowance", "legal_review"),
    "preliminaries_payment_condition": ("quote_allowance", "legal_review"),
    "settlement_audit_review": ("quote_allowance", "legal_review"),
    "settlement_document_requirement": ("legal_review", "delivery_planning"),
    "settlement_adjustment_boundary": ("quote_allowance", "clarification"),
    "warranty_period_responsibility": ("legal_review", "delivery_planning"),
    "defect_repair_obligation": ("legal_review", "delivery_planning"),
    "insurance_requirement": ("legal_review",),
}


def _business_action_from_review_action(action: str | None) -> str | None:
    if action == "to_quote_allowance":
        return "quote_allowance"
    if action == "to_clarify":
        return "clarification"
    if action == "manual_blocking_review":
        return "bid_compliance"
    if action == "bid_decision_review":
        return "legal_review"
    if action == "confirmed":
        return "delivery_planning"
    return None


def _material_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("扣减", "扣除", "货款", "违约金", "赔偿")):
        return "material_penalty_deduction"
    if _has_any(text, ("拆除", "重新采购", "更换", "返工", "无条件")):
        return "material_replacement_responsibility"
    if _has_any(text, ("进场", "验收", "检测", "检验", "报验")):
        return "material_inspection_acceptance"
    if _has_any(text, ("品牌", "样板", "封样", "规格", "型号", "质量等级", "认质认价")):
        return "material_brand_standard"
    return None


def _drawing_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("竣工图", "记录图纸", "使用说明书", "原稿")):
        return "asbuilt_drawing_record"
    if _has_any(text, ("深化设计", "深化", "设计标准")):
        return "drawing_deepening_design"
    if _has_any(text, ("错漏", "缺失", "不详", "设计资料", "错误")):
        return "drawing_error_liability"
    if _has_any(text, ("会审", "交底", "审核", "提供图纸", "图纸提供")):
        return "drawing_handover_review"
    return None


def _variation_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("资料", "依据", "签证单", "书面正式文件", "附件")):
        return "claim_document_requirement"
    if _has_any(text, ("天内", "时限", "期限", "逾期", "收到后", "通知后")):
        return "claim_notice_time_limit"
    if _has_any(text, ("反索赔", "扣款", "扣除", "扣减")):
        return "counterclaim_deduction"
    if _has_any(text, ("书面", "确认", "签认", "正式文件", "指令")):
        return "variation_written_confirmation"
    if _has_any(text, ("审批", "批准", "流程", "程序", "申请")):
        return "variation_approval_procedure"
    return None


def _payment_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("基本要求", "开办费")) and _has_any(text, ("中期付款", "支付", "付款")):
        return "preliminaries_payment_condition"
    if _has_any(text, ("发票", "增值税", "开票", "收据")):
        return "invoice_payment_condition"
    if _has_any(text, ("中期付款", "暂不支付", "不会支付", "支付限制")):
        return "interim_payment_limitation"
    if _has_any(text, ("付款资料", "付款申请", "审核", "审批", "证明文件", "资金申请")):
        return "payment_document_condition"
    return None


def _settlement_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("结算资料", "竣工资料", "结算文件", "资料清单")):
        return "settlement_document_requirement"
    if _has_any(text, ("审计", "财政评审", "评审", "审核")):
        return "settlement_audit_review"
    if _has_any(text, ("调整", "不予调整", "包干", "单价", "价款调整")):
        return "settlement_adjustment_boundary"
    return None


def _warranty_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("保险", "工程一切险", "第三者责任险")):
        return "insurance_requirement"
    if _has_any(text, ("缺陷", "修补", "维修", "整改")):
        return "defect_repair_obligation"
    if _has_any(text, ("保修期", "质保期", "缺陷责任期", "期限")):
        return "warranty_period_responsibility"
    return None


def _scope_secondary_subtype(text: str) -> str | None:
    if _has_any(text, ("现场踏勘", "现场条件", "道路", "储存", "装卸")):
        return "site_condition_responsibility"
    if _has_any(text, ("迁移", "工作界面", "界面划分", "范围调整", "施工范围")):
        return "scope_change_boundary"
    return None


def _secondary_split_subtype(parent_subtype: str, text: str) -> str | None:
    if parent_subtype == "material_quality_penalty":
        return _material_secondary_subtype(text)
    if parent_subtype == "drawing_review":
        return _drawing_secondary_subtype(text)
    if parent_subtype == "variation_procedure":
        return _variation_secondary_subtype(text)
    if parent_subtype == "payment_clause":
        return _payment_secondary_subtype(text)
    if parent_subtype == "settlement_clause":
        return _settlement_secondary_subtype(text)
    if parent_subtype == "warranty_clause":
        return _warranty_secondary_subtype(text)
    if parent_subtype == "scope_boundary":
        return _scope_secondary_subtype(text)
    return None


def _merge_business_action(actions: list[str]) -> str:
    clean_actions = [action for action in actions if action]
    if not clean_actions:
        return "reference"
    return max(clean_actions, key=lambda action: ACTION_PRIORITY.get(action, 0))


def _subtype_keywords(object_subtype: str) -> tuple[str, ...]:
    return SUBTYPE_EVIDENCE_KEYWORDS.get(
        object_subtype,
        (BUSINESS_OBJECT_SUBTYPE_LABELS.get(object_subtype, object_subtype),),
    )


def _matched_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword and _has_any(text, (keyword,))]


def _evidence_negative_hits(evidence: dict[str, Any], object_subtype: str) -> list[str]:
    text = str(evidence.get("original_text") or "")
    return _matched_keywords(text, SUBTYPE_NEGATIVE_EVIDENCE_KEYWORDS.get(object_subtype, ()))


def _looks_like_toc_or_page_index(text: str) -> bool:
    if not text:
        return False
    compact_text = re.sub(r"\s+", " ", text)
    dotted = bool(re.search(r"\.{6,}\s*\d+", compact_text))
    page_ref = bool(re.search(r"\b(?:[A-Z]+/)?\d+/\d+/\d+\b", compact_text))
    section_list = len(re.findall(r"(?:第[一二三四五六七八九十]+[章节]|^\s*\d+(?:\.\d+)*)", compact_text)) >= 3
    return dotted or (page_ref and section_list)


def _looks_like_agreement_heading(text: str) -> bool:
    return _has_any(text, ("合同协议书", "建设单位", "总承包单位", "专业分包单位")) and not _has_any(
        text,
        (
            "承包范围",
            "施工范围",
            "工作界面",
            "付款",
            "结算",
            "违约",
            "变更",
            "签证",
            "保修",
        ),
    )


def _evidence_context_penalty(evidence: dict[str, Any]) -> int:
    text = str(evidence.get("original_text") or "")
    section = str(evidence.get("document_section") or "")
    penalty = 0
    if evidence.get("is_structural_noise"):
        penalty += 180
    if section in STRUCTURAL_EVIDENCE_SECTIONS:
        penalty += 160
    elif section in WEAK_EVIDENCE_SECTIONS:
        penalty += 20
    elif section in BODY_EVIDENCE_SECTIONS:
        penalty -= 10
    if _looks_like_toc_or_page_index(text):
        penalty += 120
    if _looks_like_agreement_heading(text):
        penalty += 80
    return max(penalty, 0)


def _evidence_context_quality(penalty: int) -> str:
    if penalty >= 140:
        return "structural_noise"
    if penalty >= 70:
        return "weak_context"
    return "body"


def _evidence_relevance_score(evidence: dict[str, Any], object_subtype: str) -> int:
    text = str(evidence.get("original_text") or "")
    matched = _matched_keywords(text, _subtype_keywords(object_subtype))
    negative = _evidence_negative_hits(evidence, object_subtype)
    score = min(len(matched), 5) * 20

    risk_subtype = str(evidence.get("risk_subtype") or "")
    mapped_subtype = RISK_SUBTYPE_TO_OBJECT_SUBTYPE.get(risk_subtype, risk_subtype)
    if mapped_subtype == object_subtype:
        score += 60
    elif risk_subtype and object_subtype in {"scope_boundary", "drawing_review"} and mapped_subtype in {
        "scope_boundary",
        "drawing_review",
    }:
        score += 25

    if evidence.get("source_kind") == "requirement" and object_subtype in NON_RISK_PREFERRED_SUBTYPES and matched:
        score += 20
    if evidence.get("source_kind") == "risk" and (matched or mapped_subtype == object_subtype):
        score += 10
    score -= min(len(negative), 4) * 25
    return max(score, 0)


def _min_representative_relevance(object_subtype: str) -> int:
    return 20 if object_subtype in STRICT_REPRESENTATIVE_SUBTYPES else 0


def _evidence_quality_label(relevance_score: int, object_subtype: str, context_penalty: int = 0) -> str:
    if context_penalty >= 140:
        return "low"
    if context_penalty >= 70 and relevance_score < 80:
        return "low"
    if relevance_score >= 60:
        return "high"
    if relevance_score >= _min_representative_relevance(object_subtype):
        return "medium"
    return "low"


def _enrich_evidence(evidence: dict[str, Any], object_subtype: str, business_action: str) -> dict[str, Any]:
    text = str(evidence.get("original_text") or "")
    relevance = _evidence_relevance_score(evidence, object_subtype)
    context_penalty = _evidence_context_penalty(evidence)
    return {
        **evidence,
        "evidence_relevance": relevance,
        "evidence_quality": _evidence_quality_label(relevance, object_subtype, context_penalty),
        "evidence_context_penalty": context_penalty,
        "evidence_context_quality": _evidence_context_quality(context_penalty),
        "matched_keywords": _matched_keywords(text, _subtype_keywords(object_subtype))[:8],
        "negative_keywords": _evidence_negative_hits(evidence, object_subtype)[:8],
        "ranking_score": _evidence_score(evidence, object_subtype, business_action),
    }


def _evidence_score(evidence: dict[str, Any], object_subtype: str, business_action: str) -> int:
    relevance = _evidence_relevance_score(evidence, object_subtype)
    min_relevance = _min_representative_relevance(object_subtype)
    is_relevant = relevance >= min_relevance
    score = relevance * 4
    if evidence.get("source_kind") == "risk":
        score += 100 if is_relevant else 5
    if evidence.get("is_blocking") and is_relevant:
        score += 70
    if evidence.get("risk_level") == "high" and is_relevant:
        score += 35
    if evidence.get("business_action") == business_action:
        score += 15
    if evidence.get("risk_card_title") and is_relevant:
        score += 15
    if evidence.get("source_kind") == "requirement" and object_subtype in NON_RISK_PREFERRED_SUBTYPES and is_relevant:
        score += 20
    if _evidence_negative_hits(evidence, object_subtype):
        score -= 80
    score -= _evidence_context_penalty(evidence)
    return score


def _ranked_evidence(evidence: list[dict[str, Any]], object_subtype: str, business_action: str) -> list[dict[str, Any]]:
    return sorted(
        evidence,
        key=lambda item: (-_evidence_score(item, object_subtype, business_action), str(item.get("source_location") or "")),
    )


def _relevant_evidence(
    evidence: list[dict[str, Any]],
    object_subtype: str,
    business_action: str,
) -> list[dict[str, Any]]:
    ranked = _ranked_evidence(evidence, object_subtype, business_action)
    threshold = _min_representative_relevance(object_subtype)
    relevant = [item for item in ranked if _evidence_relevance_score(item, object_subtype) >= threshold]
    return relevant or ranked


def _representative_evidence(evidence: list[dict[str, Any]], object_subtype: str, business_action: str) -> dict[str, Any] | None:
    if not evidence:
        return None
    return _relevant_evidence(evidence, object_subtype, business_action)[0]


def _evidence_sample(
    evidence: list[dict[str, Any]],
    object_subtype: str,
    business_action: str,
    *,
    source_count: int,
) -> list[dict[str, Any]]:
    limit = LARGE_OBJECT_EVIDENCE_SAMPLE_LIMIT if source_count >= LARGE_OBJECT_SOURCE_COUNT_THRESHOLD else DEFAULT_EVIDENCE_SAMPLE_LIMIT
    sample: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for item in _relevant_evidence(evidence, object_subtype, business_action):
        compact_text = _compact(str(item.get("original_text") or ""))[:180]
        if compact_text and compact_text in seen_text:
            continue
        if compact_text:
            seen_text.add(compact_text)
        sample.append(_enrich_evidence(item, object_subtype, business_action))
        if len(sample) >= limit:
            break
    return sample


def _candidate_from_item(
    item: Any,
    *,
    source_kind: str,
    risk_context_index: dict[tuple[str, Any], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if source_kind == "requirement":
        type_pair = _object_type_and_subtype_from_requirement(item)
    else:
        type_pair = _object_type_and_subtype_from_risk(item)
    if not type_pair:
        return None
    object_type, object_subtype = type_pair
    text = _text_of(item)
    normalized_value = _normalized_value_for_subtype(object_subtype, text)
    title = _business_subtype_title(object_subtype)
    has_risk = source_kind == "risk"
    risk_context = _risk_context_for(item, risk_context_index or {}) if has_risk else {}
    business_action = _business_action_for(
        object_type,
        object_subtype,
        has_risk=has_risk,
        text=text,
        risk_context=risk_context,
    )
    source_location = getattr(item, "source_location", None)
    evidence = {
        "source_kind": source_kind,
        "source_file": getattr(item, "source_file", None),
        "source_location": source_location,
        "original_text": getattr(item, "original_text", None),
        "risk_type": getattr(item, "risk_type", None),
        "risk_level": getattr(item, "risk_level", None),
        "is_blocking": bool(getattr(item, "is_blocking", False)),
        "business_action": business_action,
        "risk_card_title": risk_context.get("risk_card_title"),
        "risk_subtype": risk_context.get("risk_subtype"),
        "document_section": getattr(item, "document_section", None),
        "is_structural_noise": bool(getattr(item, "is_structural_noise", False)),
    }
    requirement_id = getattr(item, "id", None) if source_kind == "requirement" else None
    risk_id = getattr(item, "id", None) if source_kind == "risk" else None
    candidate = {
        "object_type": object_type,
        "object_subtype": object_subtype,
        "title": title,
        "normalized_value": normalized_value,
        "normalized_json": {
            "source_kind": source_kind,
            "label": title,
            "extracted_value": normalized_value,
            "business_action": business_action,
            "business_action_label": BUSINESS_ACTION_LABELS.get(business_action, business_action),
            "suggested_review_status": _suggested_review_status(business_action, risk_context),
            "risk_card": risk_context,
        },
        "file_id": getattr(item, "file_id", None),
        "requirement_id": requirement_id,
        "risk_id": risk_id,
        "source_file": getattr(item, "source_file", None),
        "source_location": source_location,
        "original_text": getattr(item, "original_text", None) or text or title,
        "evidence": [evidence],
        "related_requirement_ids": [requirement_id] if requirement_id else [],
        "related_risk_ids": [risk_id] if risk_id else [],
        "document_section": getattr(item, "document_section", None),
        "owner_role": BUSINESS_ACTION_OWNER.get(business_action) or getattr(item, "owner_role", None) or BUSINESS_OBJECT_TYPE_OWNER.get(object_type),
        "response_required": _response_required_for(business_action),
        "confidence": float(getattr(item, "confidence", 0.6) or 0.6),
        "extraction_method": "rule_business_object_v2",
    }
    if not _should_keep_candidate(candidate, source_kind=source_kind):
        return None
    return candidate


def _dedupe_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    subtype = str(candidate.get("object_subtype") or "")
    normalized = ""
    if subtype in VALUE_SENSITIVE_SUBTYPES:
        normalized = _compact(str(candidate.get("normalized_value") or ""))
    if not normalized:
        normalized = _compact(str(candidate.get("title") or subtype or ""))
    return (str(candidate.get("object_type")), str(candidate.get("object_subtype")), normalized)


def _candidate_source_count(candidates: list[dict[str, Any]]) -> int:
    locations: set[str] = set()
    evidence_count = 0
    for candidate in candidates:
        for item in candidate.get("evidence") or []:
            evidence_count += 1
            location = str(item.get("source_location") or "")
            if location:
                locations.add(location)
    return len(locations) or evidence_count or len(candidates)


def _candidate_text(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("evidence") or []
    evidence_text = " ".join(str(item.get("original_text") or "") for item in evidence)
    return " ".join(
        [
            str(candidate.get("original_text") or ""),
            str(candidate.get("normalized_value") or ""),
            evidence_text,
        ]
    )


def _split_candidate_groups(candidates: list[dict[str, Any]]) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    if not candidates:
        return []
    parent_subtype = str(candidates[0].get("object_subtype") or "")
    source_count = _candidate_source_count(candidates)
    if parent_subtype not in SECONDARY_SPLIT_PARENT_SUBTYPES or source_count < LARGE_OBJECT_SOURCE_COUNT_THRESHOLD:
        return [(candidates, {})]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        split_subtype = _secondary_split_subtype(parent_subtype, _candidate_text(candidate)) or parent_subtype
        buckets[split_subtype].append(candidate)

    if len(buckets) <= 1:
        only_subtype, only_bucket = next(iter(buckets.items()))
        if only_subtype != parent_subtype:
            return [
                (
                    only_bucket,
                    {
                        "split_parent_subtype": parent_subtype,
                        "split_subtype": only_subtype,
                        "split_attempted": True,
                        "split_rule": "large_object_secondary_subtype_v1",
                    },
                )
            ]
        return [(candidates, {"split_parent_subtype": parent_subtype, "split_attempted": True})]

    groups: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for subtype, bucket in buckets.items():
        groups.append(
            (
                bucket,
                {
                    "split_parent_subtype": parent_subtype,
                    "split_subtype": subtype,
                    "split_attempted": True,
                    "split_rule": "large_object_secondary_subtype_v1",
                },
            )
        )
    return groups


def _secondary_business_actions_for(
    object_subtype: str,
    merged_action: str,
    *,
    risk_secondary_actions: list[str],
    business_actions: list[str],
) -> list[str]:
    actions: list[str] = []
    for action in OBJECT_SUBTYPE_SECONDARY_ACTIONS.get(object_subtype, ()):
        if action != merged_action and action not in actions:
            actions.append(action)
    for review_action in risk_secondary_actions:
        action = _business_action_from_review_action(review_action)
        if action and action != merged_action and action not in actions:
            actions.append(action)
    for action in business_actions:
        if action and action != merged_action and action not in actions:
            actions.append(action)
    return actions[:4]


def _build_object_from_candidates(candidates: list[dict[str, Any]], split_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    split_meta = split_meta or {}
    first = candidates[0]
    object_subtype = str(split_meta.get("split_subtype") or first.get("object_subtype") or "")
    object_type = str(first.get("object_type") or "")
    title = _business_subtype_title(object_subtype)
    evidence: list[dict[str, Any]] = []
    requirement_ids: list[int] = []
    risk_ids: list[int] = []
    confidence_values: list[float] = []
    source_locations: list[str] = []
    extracted_values: list[str] = []
    business_actions: list[str] = []
    risk_cards: list[dict[str, Any]] = []
    risk_primary_actions: list[str] = []
    risk_secondary_actions: list[str] = []
    review_roles: list[str] = []
    risk_grades: list[str] = []
    for candidate in candidates:
        confidence_values.append(float(candidate.get("confidence") or 0.6))
        value = candidate.get("normalized_value")
        if value and value not in extracted_values:
            extracted_values.append(str(value))
        normalized_json = candidate.get("normalized_json") or {}
        action = normalized_json.get("business_action")
        if action and action not in business_actions:
            business_actions.append(str(action))
        risk_card = normalized_json.get("risk_card") or {}
        if risk_card.get("risk_card_id") and risk_card not in risk_cards:
            risk_cards.append(risk_card)
        primary_action = risk_card.get("primary_action")
        if primary_action and primary_action not in risk_primary_actions:
            risk_primary_actions.append(str(primary_action))
        secondary_action = risk_card.get("secondary_action")
        if secondary_action and secondary_action not in risk_secondary_actions:
            risk_secondary_actions.append(str(secondary_action))
        grade = risk_card.get("risk_grade_v2")
        if grade and grade not in risk_grades:
            risk_grades.append(str(grade))
        for role in risk_card.get("review_roles") or []:
            if role and role not in review_roles:
                review_roles.append(str(role))
        for item in candidate.get("evidence") or []:
            location = str(item.get("source_location") or "")
            if location and location not in source_locations:
                source_locations.append(location)
            evidence.append(item)
        for item_id in candidate.get("related_requirement_ids") or []:
            if item_id and item_id not in requirement_ids:
                requirement_ids.append(item_id)
        for item_id in candidate.get("related_risk_ids") or []:
            if item_id and item_id not in risk_ids:
                risk_ids.append(item_id)

    merged_action = _merge_business_action(business_actions)
    source_count = len(source_locations) or len(evidence) or 1
    representative = _representative_evidence(evidence, object_subtype, merged_action)
    representative_relevance = _evidence_relevance_score(representative or {}, object_subtype)
    representative_context_penalty = _evidence_context_penalty(representative or {})
    representative_score = _evidence_score(representative or {}, object_subtype, merged_action)
    representative_quality = _evidence_quality_label(
        representative_relevance,
        object_subtype,
        representative_context_penalty,
    )
    evidence_sample = _evidence_sample(evidence, object_subtype, merged_action, source_count=source_count)
    evidence_by_source_kind = Counter(str(item.get("source_kind") or "unknown") for item in evidence)
    large_object = source_count >= LARGE_OBJECT_SOURCE_COUNT_THRESHOLD
    secondary_business_actions = _secondary_business_actions_for(
        object_subtype,
        merged_action,
        risk_secondary_actions=risk_secondary_actions,
        business_actions=business_actions,
    )
    split_parent = split_meta.get("split_parent_subtype")
    split_applied = bool(split_parent and split_parent != object_subtype)
    low_confidence_representative = representative_quality == "low"
    needs_secondary_split = large_object and not split_applied
    split_confidence = None
    if split_applied:
        if source_count >= 3 and representative_quality != "low" and representative_context_penalty < 70:
            split_confidence = 0.82
        elif source_count >= 2 and representative_quality != "low":
            split_confidence = 0.62
        else:
            split_confidence = 0.48
    elif large_object and split_meta.get("split_attempted"):
        split_confidence = 0.45
    weak_split = bool(split_applied and (split_confidence or 0) < 0.65)

    merged_normalized = {
        **(first.get("normalized_json") or {}),
        "label": title,
        "business_action": merged_action,
        "primary_business_action": merged_action,
        "business_action_label": BUSINESS_ACTION_LABELS.get(merged_action, merged_action),
        "secondary_business_actions": secondary_business_actions,
        "secondary_business_action_labels": [
            BUSINESS_ACTION_LABELS.get(action, action) for action in secondary_business_actions
        ],
        "suggested_review_status": _suggested_review_status(merged_action, risk_cards[0] if risk_cards else None),
        "extracted_values": extracted_values,
        "business_actions": business_actions or [first.get("normalized_json", {}).get("business_action")],
        "risk_cards": risk_cards,
        "risk_primary_actions": risk_primary_actions,
        "risk_secondary_actions": risk_secondary_actions,
        "risk_grades": risk_grades,
        "review_roles": review_roles,
        "representative_evidence_score": representative_score,
        "representative_evidence_relevance": representative_relevance,
        "representative_evidence_quality": representative_quality,
        "representative_evidence_context_penalty": representative_context_penalty,
        "representative_evidence_context_quality": _evidence_context_quality(representative_context_penalty),
        "representative_matched_keywords": _matched_keywords(
            str((representative or {}).get("original_text") or ""),
            _subtype_keywords(object_subtype),
        )[:8],
        "representative_negative_keywords": _evidence_negative_hits(representative or {}, object_subtype)[:8],
        "low_confidence_representative": low_confidence_representative,
        "evidence_sample_count": len(evidence_sample),
        "evidence_total_count": len(evidence),
        "omitted_evidence_count": max(0, len(evidence) - len(evidence_sample)),
        "evidence_by_source_kind": dict(evidence_by_source_kind),
        "large_object": large_object,
        "needs_secondary_split": needs_secondary_split,
        "needs_llm_review": bool(needs_secondary_split or low_confidence_representative or weak_split),
        "split_parent_subtype": split_parent,
        "split_applied": split_applied,
        "split_confidence": split_confidence,
        "weak_split": weak_split,
        "split_rule": split_meta.get("split_rule"),
        "split_reason": "large object split by subtype keyword bucket"
        if split_applied and not weak_split
        else ("weak split bucket; needs LLM/manual confirmation" if weak_split else None),
        "large_object_warning": "source_count>=20; evidence sample trimmed to the most relevant items"
        if large_object
        else None,
    }

    return {
        **first,
        "object_type": object_type,
        "object_subtype": object_subtype,
        "title": title,
        "normalized_json": merged_normalized,
        "source_file": (representative or {}).get("source_file") or first.get("source_file"),
        "source_location": (representative or {}).get("source_location") or first.get("source_location"),
        "original_text": (representative or {}).get("original_text") or first.get("original_text"),
        "source_count": source_count,
        "evidence": evidence_sample,
        "related_requirement_ids": requirement_ids,
        "related_risk_ids": risk_ids,
        "owner_role": BUSINESS_ACTION_OWNER.get(merged_action) or first.get("owner_role"),
        "response_required": _response_required_for(merged_action),
        "confidence": round(sum(confidence_values) / max(len(confidence_values), 1), 4),
        "extraction_method": "rule_business_object_v4",
    }


def build_tender_business_objects(requirements: list[Any], risks: list[Any]) -> list[dict[str, Any]]:
    risk_context_index = _build_risk_context_index(risks)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for requirement in requirements:
        candidate = _candidate_from_item(requirement, source_kind="requirement", risk_context_index=risk_context_index)
        if candidate:
            grouped[_dedupe_key(candidate)].append(candidate)
    for risk in risks:
        candidate = _candidate_from_item(risk, source_kind="risk", risk_context_index=risk_context_index)
        if candidate:
            grouped[_dedupe_key(candidate)].append(candidate)

    objects: list[dict[str, Any]] = []
    for _, candidates in grouped.items():
        for bucket, split_meta in _split_candidate_groups(candidates):
            objects.append(_build_object_from_candidates(bucket, split_meta))
    return sorted(
        objects,
        key=lambda item: (
            str(item.get("object_type") or ""),
            str(item.get("object_subtype") or ""),
            str(item.get("title") or ""),
        ),
    )


def build_business_object_summary(objects: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(item.get("object_type") or "unknown" for item in objects)
    by_review_status = Counter(item.get("review_status") or "pending" for item in objects)
    by_action: Counter[str] = Counter()
    low_confidence_evidence_count = 0
    large_object_count = 0
    secondary_split_count = 0
    weak_split_count = 0
    needs_secondary_split_count = 0
    needs_llm_review_count = 0
    structural_evidence_penalty_count = 0
    llm_reviewed_count = 0
    llm_pending_manual_count = 0
    llm_accepted_count = 0
    llm_rejected_count = 0
    llm_modified_count = 0
    llm_error_count = 0
    llm_suggested_split_count = 0
    for item in objects:
        normalized = item.get("normalized") or item.get("normalized_json") or {}
        action = normalized.get("business_action") or "reference"
        by_action[str(action)] += 1
        if normalized.get("low_confidence_representative"):
            low_confidence_evidence_count += 1
        if normalized.get("large_object") or int(item.get("source_count") or 0) >= LARGE_OBJECT_SOURCE_COUNT_THRESHOLD:
            large_object_count += 1
        if normalized.get("split_applied"):
            secondary_split_count += 1
        if normalized.get("weak_split"):
            weak_split_count += 1
        if normalized.get("needs_secondary_split"):
            needs_secondary_split_count += 1
        if normalized.get("needs_llm_review"):
            needs_llm_review_count += 1
        if int(normalized.get("representative_evidence_context_penalty") or 0) >= 70:
            structural_evidence_penalty_count += 1
        if normalized.get("llm_review"):
            llm_reviewed_count += 1
        llm_status = normalized.get("llm_review_status")
        if llm_status == "pending_manual_confirm":
            llm_pending_manual_count += 1
        if llm_status == "accepted":
            llm_accepted_count += 1
        if llm_status == "rejected":
            llm_rejected_count += 1
        if llm_status == "modified":
            llm_modified_count += 1
        if llm_status == "error":
            llm_error_count += 1
        llm_review = normalized.get("llm_review") if isinstance(normalized.get("llm_review"), dict) else {}
        if llm_review.get("decision") == "split":
            llm_suggested_split_count += 1
    return {
        "object_count": len(objects),
        "response_required_count": sum(1 for item in objects if item.get("response_required", True)),
        "pending_count": by_review_status.get("pending", len(objects)),
        "object_by_type": dict(by_type),
        "object_by_action": dict(by_action),
        "action_labels": BUSINESS_ACTION_LABELS,
        "bid_compliance_count": by_action.get("bid_compliance", 0),
        "quote_allowance_count": by_action.get("quote_allowance", 0),
        "clarification_count": by_action.get("clarification", 0),
        "legal_review_count": by_action.get("legal_review", 0),
        "delivery_planning_count": by_action.get("delivery_planning", 0),
        "document_response_count": by_action.get("document_response", 0),
        "qualification_response_count": by_action.get("qualification_response", 0),
        "large_object_count": large_object_count,
        "low_confidence_evidence_count": low_confidence_evidence_count,
        "secondary_split_count": secondary_split_count,
        "weak_split_count": weak_split_count,
        "needs_secondary_split_count": needs_secondary_split_count,
        "needs_llm_review_count": needs_llm_review_count,
        "structural_evidence_penalty_count": structural_evidence_penalty_count,
        "llm_reviewed_count": llm_reviewed_count,
        "llm_pending_manual_count": llm_pending_manual_count,
        "llm_accepted_count": llm_accepted_count,
        "llm_rejected_count": llm_rejected_count,
        "llm_modified_count": llm_modified_count,
        "llm_error_count": llm_error_count,
        "llm_suggested_split_count": llm_suggested_split_count,
        "review_by_status": dict(by_review_status),
        "type_labels": BUSINESS_OBJECT_TYPE_LABELS,
    }
