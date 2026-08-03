"""Business-bid draft field responsibility plan.

V1.4.1 keeps the bid template generic while making every draft field explicit:
system-filled, LLM-draftable, manually filled, manually attached, or manually signed.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any


FIELD_PLAN_VERSION = "business_bid_draft_field_plan_v1.4.1"

LLM_SOURCE_POLICY = {
    "allowed_sources": ["本项目招标文件", "系统已确认的企业资料", "人工填写信息"],
    "disallowed_sources": ["互联网企业事实补充", "未确认的企业资料", "通用想象性业绩或资质"],
    "rule": "LLM 只能基于封闭资料源生成商务文字草稿；缺少依据时必须转为人工填写或人工确认。",
}


def build_business_bid_draft_field_plan(
    *,
    project: Any,
    quote_import: Any | None,
    template_plan: dict[str, Any],
    requirements: list[dict[str, Any]] | None = None,
    v12_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_keys = {
        str(item.get("section_key"))
        for item in template_plan.get("generated_sections") or []
        if isinstance(item, dict) and item.get("section_key")
    }
    fields: list[dict[str, Any]] = []

    def add(
        section_key: str,
        field_key: str,
        label: str,
        mode: str,
        *,
        status: str,
        required: bool = True,
        value: Any | None = None,
        instruction: str = "",
        source_hint: str = "",
        pdf_location: str = "",
    ) -> None:
        if section_key not in generated_keys:
            return
        fields.append(
            {
                "section_key": section_key,
                "field_key": field_key,
                "label": label,
                "mode": mode,
                "status": status,
                "required": required,
                "value": _safe_value(value),
                "instruction": instruction,
                "source_hint": source_hint,
                "pdf_location": pdf_location,
                "placeholder": _placeholder(label, mode, source_hint),
                "highlight": "yellow" if mode.startswith("manual") or status.startswith("needs_") else None,
            }
        )

    project_name = getattr(project, "project_name", None)
    tenderer_name = getattr(project, "tenderer_name", None)
    tender_agency = getattr(project, "tender_agency", None)
    tender_number = _summary_value(project, "tender_number", "bid_number", "project_code")
    quote_total = getattr(quote_import, "total_amount", None) if quote_import else None

    add("bid_letter", "project_name", "项目名称", "system", status="filled" if project_name else "needs_manual", value=project_name, source_hint="投标项目基础信息", pdf_location="投标函正文")
    add("bid_letter", "tender_agency", "招标人/招标代理", "manual_text", status="needs_manual" if not tender_agency else "filled", value=tender_agency, source_hint="本项目招标文件", pdf_location="投标函抬头")
    add("bid_letter", "bid_letter_text", "投标函承诺正文", "llm_draft", status="needs_review", source_hint="本项目招标文件 + 人工填写信息", pdf_location="投标函正文")
    add("bid_letter", "bidder_signature", "投标人签章", "manual_signature", status="needs_manual", source_hint="企业印章和授权签署人", pdf_location="投标函签章栏")

    add("pricing_summary", "project_name", "项目名称", "system", status="filled" if project_name else "needs_manual", value=project_name, source_hint="投标项目基础信息", pdf_location="投标报价汇总表")
    add("pricing_summary", "tender_number", "招标编号", "manual_text", status="needs_manual" if not tender_number else "filled", value=tender_number, source_hint="本项目招标文件", pdf_location="投标报价汇总表")
    add("pricing_summary", "tenderer_name", "投标人名称", "manual_text", status="needs_manual" if not tenderer_name else "filled", value=tenderer_name, source_hint="系统已确认的企业资料", pdf_location="投标报价汇总表")
    add("pricing_summary", "quote_total", "报价合计", "system", status="filled" if quote_total is not None else "needs_quote_import", value=quote_total, source_hint="系统已确认报价快照", pdf_location="投标报价汇总表")
    add("pricing_summary", "pricing_review_note", "报价说明复核", "manual_review", status="needs_manual", source_hint="本项目招标文件", pdf_location="投标报价汇总表说明")

    add("legal_representative", "legal_representative_identity", "法定代表人身份信息", "manual_text", status="needs_manual", source_hint="系统已确认的企业资料", pdf_location="法定代表人身份证明")
    add("legal_representative", "legal_representative_scan", "法定代表人证明扫描件", "manual_attachment", status="needs_manual", source_hint="系统已确认的企业资料库", pdf_location="法定代表人身份证明附件")
    add("legal_representative", "legal_representative_signature", "法定代表人签章", "manual_signature", status="needs_manual", source_hint="企业印章和法定代表人签署", pdf_location="法定代表人身份证明签章栏")

    add("authorization", "authorized_agent_identity", "授权代理人身份信息", "manual_text", status="needs_manual", source_hint="人工填写信息 + 企业授权资料", pdf_location="授权委托书")
    add("authorization", "authorization_scope", "授权范围和期限", "manual_text", status="needs_manual", source_hint="本项目招标文件 + 人工填写信息", pdf_location="授权委托书")
    add("authorization", "authorization_scan", "授权委托扫描件", "manual_attachment", status="needs_manual", source_hint="签署盖章后的授权文件", pdf_location="授权委托书附件")
    add("authorization", "authorization_signature", "授权委托签章", "manual_signature", status="needs_manual", source_hint="企业印章和授权签署人", pdf_location="授权委托书签章栏")

    add("commitment", "commitment_text", "商务承诺正文", "llm_draft", status="needs_review", source_hint="本项目招标文件 + 已确认报价快照 + 人工填写信息", pdf_location="商务承诺书正文")
    add("commitment", "commitment_signature", "承诺书签章", "manual_signature", status="needs_manual", source_hint="企业印章和授权签署人", pdf_location="商务承诺书签章栏")

    response_items = (((v12_report or {}).get("business_responses") or {}).get("items") or [])
    response_status = "needs_review" if response_items else "needs_llm_draft"
    add("business_deviation", "business_response_rows", "商务条款响应行", "llm_draft", status=response_status, source_hint="本项目招标文件 + 响应矩阵", pdf_location="商务条款偏离表")
    add("business_deviation", "business_deviation_review", "偏离情况人工确认", "manual_review", status="needs_manual", source_hint="本项目招标文件", pdf_location="商务条款偏离表")

    add("boq", "boq_rows", "工程量清单报价行", "system", status="filled" if quote_import else "needs_quote_import", value=getattr(quote_import, "line_count", None) if quote_import else None, source_hint="系统已确认报价快照", pdf_location="工程量清单报价表")
    add("boq", "boq_manual_review", "清单税费/暂估价/招标约定复核", "manual_review", status="needs_manual", source_hint="本项目招标文件", pdf_location="工程量清单报价表说明")

    unresolved_requirements = [row for row in (requirements or []) if not row.get("resolved")]
    add("attachment_index", "enterprise_material_index", "企业资料附件索引", "system", status="filled" if requirements else "needs_manual", value=len(requirements or []), source_hint="系统资料需求清单", pdf_location="企业资料附件目录")
    add("attachment_index", "enterprise_material_files", "企业资料扫描件/截图/PDF", "manual_attachment", status="needs_manual" if unresolved_requirements else "filled", value=len(requirements or []), source_hint="系统已确认的企业资料库 + 人工导入附件", pdf_location="企业资料附件目录")
    add("attachment_index", "unmapped_directory_items", "项目专属目录项", "manual_attachment", status="needs_manual" if template_plan.get("manual_directory_items") else "filled", value=len(template_plan.get("manual_directory_items") or []), source_hint="本项目招标文件", pdf_location="项目专属人工处理目录项")

    by_mode = Counter(str(item["mode"]) for item in fields)
    by_status = Counter(str(item["status"]) for item in fields)
    manual_required = [
        item
        for item in fields
        if item.get("required") and (str(item.get("mode")).startswith("manual") or str(item.get("status")).startswith("needs_"))
    ]
    by_section = _section_summary(fields)
    return {
        "version": FIELD_PLAN_VERSION,
        "llm_source_policy": LLM_SOURCE_POLICY,
        "fields": fields,
        "summary": {
            "field_count": len(fields),
            "by_mode": dict(by_mode),
            "by_status": dict(by_status),
            "manual_required_count": len(manual_required),
            "llm_review_count": sum(1 for item in fields if item.get("mode") == "llm_draft"),
            "yellow_placeholder_count": sum(1 for item in fields if item.get("highlight") == "yellow"),
            "by_section": by_section,
        },
    }


def field_lookup(field_plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in (field_plan or {}).get("fields") or []:
        if isinstance(item, dict) and item.get("field_key"):
            result[str(item["field_key"])] = item
    return result


def _section_summary(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in fields:
        grouped.setdefault(str(item.get("section_key") or ""), []).append(item)
    result = []
    for section_key, rows in grouped.items():
        result.append(
            {
                "section_key": section_key,
                "field_count": len(rows),
                "manual_required_count": sum(1 for row in rows if str(row.get("mode", "")).startswith("manual")),
                "llm_review_count": sum(1 for row in rows if row.get("mode") == "llm_draft"),
                "yellow_placeholder_count": sum(1 for row in rows if row.get("highlight") == "yellow"),
            }
        )
    return result


def _placeholder(label: str, mode: str, source_hint: str) -> str:
    source = f"（依据：{source_hint}）" if source_hint else ""
    if mode == "llm_draft":
        return f"【待确认 LLM 草稿：{label}{source}】"
    if mode == "manual_attachment":
        return f"【人工导入：{label}{source}】"
    if mode == "manual_signature":
        return f"【人工签章：{label}{source}】"
    if mode == "manual_review":
        return f"【人工复核：{label}{source}】"
    if mode == "manual_text":
        return f"【人工填写：{label}{source}】"
    return f"【系统生成：{label}{source}】"


def _summary_value(project: Any, *keys: str) -> str | None:
    source = getattr(project, "summary_json", None)
    if not isinstance(source, dict):
        try:
            import json

            source = json.loads(source or "{}")
        except (TypeError, ValueError):
            source = {}
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else None
        if value:
            return str(value).strip()
    return None


def _safe_value(value: Any) -> str | int | float | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
