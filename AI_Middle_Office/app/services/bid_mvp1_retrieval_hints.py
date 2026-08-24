"""Frozen, project-agnostic retrieval hints for MVP-1 tender fact tasks."""
from __future__ import annotations

from typing import Any


MVP1_RETRIEVAL_HINTS: dict[str, dict[str, Any]] = {
    "extract_tender_overview": {
        "primary_query": "项目概况 招标范围",
        "field_aliases": ["项目概况", "工程概况", "招标范围", "承包范围"],
    },
    "extract_critical_dates": {
        "primary_query": "投标截止时间 开标时间",
        "field_aliases": ["投标截止", "递交截止", "开标时间", "截止日期"],
    },
    "extract_qualification_requirements": {
        "primary_query": "资格要求 资质",
        "field_aliases": ["资格要求", "资质要求", "投标人资格", "项目负责人资格"],
    },
    "extract_rejection_clauses": {
        "primary_query": "否决投标 废标 条款",
        "field_aliases": ["否决投标", "废标", "无效投标", "不予受理"],
    },
    "extract_guarantees_and_fees": {
        "primary_query": "投标保证金 费用",
        "field_aliases": ["投标保证金", "履约保证金", "招标文件费", "投标费用"],
    },
    "extract_evaluation_method": {
        "primary_query": "评标办法 评分标准",
        "field_aliases": ["评标办法", "评审方法", "评分标准", "综合评分"],
    },
    "extract_scope_and_quantities": {
        "primary_query": "招标范围 工程量",
        "field_aliases": ["招标范围", "承包范围", "工程量", "清单范围"],
    },
    "extract_deliverables_and_samples": {
        "primary_query": "样品 成果 交付",
        "field_aliases": ["样品", "送样", "交付成果", "提交成果"],
    },
    "extract_contract_terms": {
        "primary_query": "合同条款 付款",
        "field_aliases": ["合同条款", "付款条件", "支付方式", "结算方式"],
    },
    "extract_schedule_and_site_constraints": {
        "primary_query": "工期 现场 条件",
        "field_aliases": ["计划工期", "现场条件", "施工限制", "物业管理"],
    },
}


def retrieval_guidance_for_task(task_type: str) -> dict[str, Any] | None:
    hint = MVP1_RETRIEVAL_HINTS.get(str(task_type))
    if hint is None:
        return None
    return {
        **hint,
        "query_language_policy": "prefer_document_language_and_exact_field_terms",
        "no_result_policy": "rewrite_with_field_aliases_or_finish_without_fabrication",
    }
