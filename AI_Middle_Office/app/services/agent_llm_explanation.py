from __future__ import annotations

import json
import re
from typing import Any, Iterable

from app.core.config import settings
from app.models.agent import AgentFinding, AgentRun, AgentSuggestion
from app.services.agent_quote_review import (
    json_loads,
    serialize_agent_finding,
    serialize_agent_suggestion,
)
from app.services.model_gateway import post_json_via_gateway
from app.services.quote_history import parse_amount


MAX_EXPLAINED_FINDINGS = 8
MAX_PRIORITIZED_SUGGESTIONS = 8
MAX_AUDIT_EXPLANATIONS = 10
LLM_CONTEXT_FINDINGS = 20
LLM_CONTEXT_SUGGESTIONS = 20


SYSTEM_PROMPT = """你是企业装修报价后审计 Agent 的业务解释助手。
你不是把规则解释改写一遍，而是要把 Agent 后审计结果转成更有业务判断力的审计说明，帮助管理员理解已下发报价的风险留痕。
你只能基于输入的 Agent 后审计结果、风险发现、历史建议和指标做解释增强，不能重新计算金额，不能新增风险事实，不能输出执行动作或二次人工确认要求。
请输出严格 JSON，不要 Markdown，不要代码块。
JSON 字段：
headline: string
business_summary: string，说明这张报价单当前最核心的业务判断，避免复述规则标题
review_focus: string array，列出 2-5 个最应该先看的复核重点
risk_explanations: array，元素含 severity/title/target_label/explanation/evidence_ref/handling_advice
suggestion_priorities: array，元素含 suggestion_id/priority/title/target_label/reason/handling_advice/estimated_saving_amount
saving_explanation: object，含 text
saving_opportunities: array，元素含 suggestion_id/title/why_it_saves_money/check_before_adoption/estimated_saving_amount
decision_checklist: string array，首选返回空数组；不要要求二次人工确认
manual_handling: string array
uncertainties: string array
要求：
1. DeepSeek 输出必须明显区别于规则解释：多解释为什么、先看哪里、确认什么，而不是照搬 fallback_explanation。
2. 省钱建议必须明确说明预计节省多少钱；金额必须照抄输入，不得自行推算。
3. 如果替代建议涉及规格、品牌、施工范围或质量要求不一致，必须提示不得直接替换。
4. 不要承诺已经执行修改；所有内容都必须表述为后审计解释或证据追溯，不要生成待办。"""


SYSTEM_PROMPT += """
5. 如果 agent_output.audit_records 非空，必须输出 before_after_explanations，逐条解释“原预审风险”和“最终确认下发状态”的差异。
before_after_explanations: array，元素含 target_label/original_risk/confirmed_state/explanation/manual_modified。
"""


def build_agent_llm_explanation(
    run: AgentRun,
    *,
    findings: Iterable[AgentFinding | dict[str, Any]],
    suggestions: Iterable[AgentSuggestion | dict[str, Any]],
) -> dict[str, Any]:
    finding_payloads = [_finding_payload(item) for item in findings]
    suggestion_payloads = [_suggestion_payload(item) for item in suggestions]
    output = json_loads(run.output_json, {}) if run.output_json else {}

    saving_explanation = _saving_explanation(output, suggestion_payloads)
    risk_explanations = _risk_explanations(finding_payloads)
    audit_explanations = _audit_explanations(output.get("audit_records") if isinstance(output, dict) else [])
    suggestion_priorities = _suggestion_priorities(suggestion_payloads)
    uncertainties = _uncertainties(output, finding_payloads, suggestion_payloads)

    return {
        "mode": "rule_based_fallback",
        "llm_provider": "not_connected",
        "llm_model": None,
        "read_only": True,
        "agent_type": run.agent_type,
        "run_id": run.run_id,
        "target_type": run.target_type,
        "target_id": run.target_id,
        "target_number": output.get("target_number") if isinstance(output, dict) else None,
        "risk_level": run.risk_level,
        "recommendation": run.recommendation,
        "headline": _headline(run, output, finding_payloads, saving_explanation),
        "business_summary": _audit_business_summary(output, audit_explanations),
        "review_focus": [],
        "before_after_explanations": audit_explanations,
        "risk_explanations": risk_explanations,
        "suggestion_priorities": suggestion_priorities,
        "saving_explanation": saving_explanation,
        "saving_opportunities": [],
        "decision_checklist": [],
        "manual_handling": _manual_handling(run, finding_payloads, suggestion_payloads),
        "uncertainties": uncertainties,
        "source": {
            "run_output": True,
            "finding_count": len(finding_payloads),
            "suggestion_count": len(suggestion_payloads),
        },
    }


async def build_agent_llm_explanation_with_llm(
    run: AgentRun,
    *,
    findings: Iterable[AgentFinding | dict[str, Any]],
    suggestions: Iterable[AgentSuggestion | dict[str, Any]],
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    finding_payloads = [_finding_payload(item) for item in findings]
    suggestion_payloads = [_suggestion_payload(item) for item in suggestions]
    fallback = build_agent_llm_explanation(run, findings=finding_payloads, suggestions=suggestion_payloads)
    provider = (settings.agent_llm_provider or "rule").strip().lower()
    fallback["prompt_version"] = settings.agent_llm_prompt_version

    if provider != "deepseek":
        fallback["llm_provider"] = "not_connected"
        fallback["fallback_reason"] = "provider_not_configured"
        return fallback

    fallback["llm_provider"] = "deepseek"
    fallback["llm_model"] = settings.deepseek_model
    if not (settings.deepseek_api_key or "").strip():
        fallback["fallback_reason"] = "deepseek_api_key_missing"
        return fallback

    try:
        llm_payload = await _call_deepseek_explanation(
            run,
            fallback=fallback,
            findings=finding_payloads,
            suggestions=suggestion_payloads,
            username=username,
            trace_id=trace_id,
        )
        return _merge_llm_payload(fallback, llm_payload)
    except Exception as exc:
        fallback["fallback_reason"] = f"deepseek_error: {str(exc)[:200]}"
        return fallback


def _finding_payload(item: AgentFinding | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return serialize_agent_finding(item)


def _suggestion_payload(item: AgentSuggestion | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return serialize_agent_suggestion(item)


def _audit_business_summary(output: dict[str, Any], audit_explanations: list[dict[str, Any]]) -> str:
    if not isinstance(output, dict):
        return ""
    audit_summary = output.get("audit_summary") if isinstance(output.get("audit_summary"), dict) else {}
    record_count = int(audit_summary.get("audit_record_count") or len(audit_explanations))
    manual_count = int(audit_summary.get("manual_modified_count") or 0)
    if record_count:
        return (
            f"本次为已下发报价的事后审计，共记录 {record_count} 条预审风险或人工改价痕迹，"
            f"其中 {manual_count} 条在确认下发前发生人工改价；本说明仅用于后审计追溯，不生成每日待办或二次确认。"
        )
    if output.get("audit_mode") == "confirmed_quote_risk_audit":
        return "本次为已下发报价的事后审计，未记录到需要重点追溯的预审风险或人工改价痕迹。"
    return ""


def _audit_explanations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:MAX_AUDIT_EXPLANATIONS]:
        if not isinstance(item, dict):
            continue
        original_preview = item.get("original_preview") if isinstance(item.get("original_preview"), dict) else {}
        confirmed_quote = item.get("confirmed_quote") if isinstance(item.get("confirmed_quote"), dict) else {}
        risk_reasons = item.get("risk_reasons") if isinstance(item.get("risk_reasons"), list) else []
        risk_text = "；".join(
            str(reason.get("label") or reason.get("type"))
            for reason in risk_reasons
            if isinstance(reason, dict) and (reason.get("label") or reason.get("type"))
        )
        if not risk_text:
            risk_text = "未记录明确预审风险"
        confirmed_text = (
            f"下发单价 {_money_text(confirmed_quote.get('unit_price'))} 元，"
            f"下发合计 {_money_text(confirmed_quote.get('total_price'))} 元"
        )
        confirmed_text = (
            f"下发工程量 {_money_text(confirmed_quote.get('quantity'))}，"
            f"下发单价 {_money_text(confirmed_quote.get('unit_price'))} 元，"
            f"下发合计 {_money_text(confirmed_quote.get('total_price'))} 元"
        )
        fallback_explanation = (
            f"预审工程量 {_money_text(original_preview.get('quantity'))}，"
            f"预审单价 {_money_text(original_preview.get('unit_price'))} 元，"
            f"预审合计 {_money_text(original_preview.get('total_price'))} 元；"
            f"{confirmed_text}。"
        )
        item_summary = item.get("before_after_summary") or fallback_explanation
        result.append(
            {
                "target_label": item.get("target_label"),
                "original_risk": risk_text,
                "confirmed_state": confirmed_text,
                "manual_modified": bool(confirmed_quote.get("manual_modified")),
                "explanation": item_summary
                or (
                    f"预审单价 {_money_text(original_preview.get('unit_price'))} 元，"
                    f"预审合计 {_money_text(original_preview.get('total_price'))} 元；{confirmed_text}。"
                ),
            }
        )
    return result


async def _call_deepseek_explanation(
    run: AgentRun,
    *,
    fallback: dict[str, Any],
    findings: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
    username: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    user_payload = {
        "prompt_version": settings.agent_llm_prompt_version,
        "task": "quote_review_explanation",
        "run": {
            "run_id": run.run_id,
            "target_id": run.target_id,
            "target_number": fallback.get("target_number"),
            "risk_level": fallback.get("risk_level"),
            "recommendation": fallback.get("recommendation"),
        },
        "agent_output": _llm_agent_output(json_loads(run.output_json, {}) if run.output_json else {}),
        "fallback_explanation": _llm_safe_fallback(fallback),
        "findings": [_llm_finding(item) for item in findings[:LLM_CONTEXT_FINDINGS]],
        "suggestions": [_llm_suggestion(item) for item in suggestions[:LLM_CONTEXT_SUGGESTIONS]],
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=settings.deepseek_model,
        endpoint_type="agent_quote_review_explanation",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=settings.agent_llm_timeout_seconds,
        username=username,
        trace_id=trace_id or run.trace_id,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}")
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    return _extract_json_object(content)


def _merge_llm_payload(fallback: dict[str, Any], llm_payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(fallback)
    result["mode"] = "deepseek"
    result["llm_provider"] = "deepseek"
    result["llm_model"] = settings.deepseek_model
    result["prompt_version"] = settings.agent_llm_prompt_version

    headline = _string_or_none(llm_payload.get("headline"))
    if headline:
        result["headline"] = headline

    business_summary = _string_or_none(llm_payload.get("business_summary"))
    if business_summary:
        result["business_summary"] = business_summary

    for key in ("review_focus", "decision_checklist"):
        values = _clean_string_list(llm_payload.get(key))
        if values:
            result[key] = values[:6]

    before_after = _clean_before_after_explanations(llm_payload.get("before_after_explanations"))
    if before_after:
        fallback_items = result.get("before_after_explanations") or []
        for index, item in enumerate(before_after):
            if not item.get("target_label") and index < len(fallback_items):
                item["target_label"] = fallback_items[index].get("target_label")
            if "manual_modified" not in item and index < len(fallback_items):
                item["manual_modified"] = fallback_items[index].get("manual_modified")
        result["before_after_explanations"] = before_after

    risks = llm_payload.get("risk_explanations")
    if isinstance(risks, list) and risks:
        cleaned_risks = [_clean_llm_risk(item) for item in risks if isinstance(item, dict)][
            :MAX_EXPLAINED_FINDINGS
        ]
        fallback_risks = result.get("risk_explanations") or []
        for index, item in enumerate(cleaned_risks):
            if not item.get("target_label") and index < len(fallback_risks):
                item["target_label"] = fallback_risks[index].get("target_label")
            if not item.get("target_ref") and index < len(fallback_risks):
                item["target_ref"] = fallback_risks[index].get("target_ref")
        result["risk_explanations"] = cleaned_risks or result["risk_explanations"]

    result["suggestion_priorities"] = _merge_llm_suggestions(
        result.get("suggestion_priorities") or [],
        llm_payload.get("suggestion_priorities"),
    )
    result["saving_explanation"] = _merge_llm_saving(
        result.get("saving_explanation") or {},
        llm_payload.get("saving_explanation"),
    )
    saving_opportunities = _clean_saving_opportunities(
        result.get("suggestion_priorities") or [],
        llm_payload.get("saving_opportunities"),
    )
    if saving_opportunities:
        result["saving_opportunities"] = saving_opportunities

    for key in ("manual_handling", "uncertainties"):
        values = _clean_string_list(llm_payload.get(key))
        if values:
            result[key] = values[:6]
    return result


def _merge_llm_suggestions(
    fallback_items: list[dict[str, Any]],
    llm_items: Any,
) -> list[dict[str, Any]]:
    if not isinstance(llm_items, list) or not llm_items:
        return fallback_items
    by_id = {
        str(item.get("suggestion_id")): item
        for item in llm_items
        if isinstance(item, dict) and item.get("suggestion_id")
    }
    merged: list[dict[str, Any]] = []
    for fallback_item in fallback_items:
        item = dict(fallback_item)
        llm_item = by_id.get(str(fallback_item.get("suggestion_id")))
        if isinstance(llm_item, dict):
            for key in ("title", "reason", "handling_advice"):
                value = _string_or_none(llm_item.get(key))
                if value:
                    item[key] = value
        item["estimated_saving_amount"] = fallback_item.get("estimated_saving_amount")
        item["estimated_saving_rate"] = fallback_item.get("estimated_saving_rate")
        merged.append(item)
    return merged


def _merge_llm_saving(fallback_saving: dict[str, Any], llm_saving: Any) -> dict[str, Any]:
    result = dict(fallback_saving)
    if isinstance(llm_saving, dict):
        text = _string_or_none(llm_saving.get("text"))
        if text:
            result["text"] = text
    return result


def _clean_saving_opportunities(
    fallback_suggestions: list[dict[str, Any]],
    llm_items: Any,
) -> list[dict[str, Any]]:
    if not isinstance(llm_items, list) or not llm_items:
        return []
    by_id = {
        str(item.get("suggestion_id")): item
        for item in fallback_suggestions
        if item.get("suggestion_id")
    }
    result: list[dict[str, Any]] = []
    for llm_item in llm_items:
        if not isinstance(llm_item, dict):
            continue
        suggestion_id = str(llm_item.get("suggestion_id") or "")
        fallback_item = by_id.get(suggestion_id)
        if not fallback_item:
            continue
        saving_amount = parse_amount(fallback_item.get("estimated_saving_amount")) or 0
        if saving_amount <= 0:
            continue
        result.append(
            {
                "suggestion_id": fallback_item.get("suggestion_id"),
                "title": _string_or_none(llm_item.get("title")) or fallback_item.get("title"),
                "why_it_saves_money": _string_or_none(llm_item.get("why_it_saves_money")) or fallback_item.get("reason"),
                "check_before_adoption": _string_or_none(llm_item.get("check_before_adoption"))
                or fallback_item.get("handling_advice"),
                "estimated_saving_amount": fallback_item.get("estimated_saving_amount"),
            }
        )
    return result[:5]


def _clean_before_after_explanations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        explanation = _string_or_none(item.get("explanation"))
        original_risk = _string_or_none(item.get("original_risk"))
        confirmed_state = _string_or_none(item.get("confirmed_state"))
        if not (explanation or original_risk or confirmed_state):
            continue
        result.append(
            {
                "target_label": _string_or_none(item.get("target_label")),
                "original_risk": original_risk or "",
                "confirmed_state": confirmed_state or "",
                "explanation": explanation or "",
                "manual_modified": bool(item.get("manual_modified")),
            }
        )
    return result[:MAX_AUDIT_EXPLANATIONS]


def _clean_llm_risk(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": _string_or_none(item.get("severity")) or "medium",
        "severity_label": _severity_label(item.get("severity")),
        "finding_type": _string_or_none(item.get("finding_type")) or "",
        "target_ref": _string_or_none(item.get("target_ref")),
        "target_label": _string_or_none(item.get("target_label")),
        "title": _string_or_none(item.get("title")) or "风险项",
        "explanation": _string_or_none(item.get("explanation")) or "",
        "evidence_ref": _string_or_none(item.get("evidence_ref")) or "-",
        "handling_advice": _string_or_none(item.get("handling_advice")) or "请人工确认。",
    }


def _extract_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("EMPTY_DEEPSEEK_CONTENT")
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("DEEPSEEK_JSON_NOT_OBJECT")
    return value


def _llm_safe_fallback(fallback: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": fallback.get("headline"),
        "risk_level": fallback.get("risk_level"),
        "recommendation": fallback.get("recommendation"),
        "business_summary": fallback.get("business_summary"),
        "before_after_explanations": fallback.get("before_after_explanations"),
        "saving_explanation": fallback.get("saving_explanation"),
        "manual_handling": fallback.get("manual_handling"),
        "uncertainties": fallback.get("uncertainties"),
    }


def _llm_agent_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    return {
        "summary": output.get("summary"),
        "risk_level": output.get("risk_level"),
        "recommendation": output.get("recommendation"),
        "metrics": output.get("metrics") if isinstance(output.get("metrics"), dict) else {},
        "saving_summary": output.get("saving_summary") if isinstance(output.get("saving_summary"), dict) else {},
        "audit_mode": output.get("audit_mode"),
        "audit_summary": output.get("audit_summary") if isinstance(output.get("audit_summary"), dict) else {},
        "market_search_summary": output.get("market_search_summary")
        if isinstance(output.get("market_search_summary"), dict)
        else {},
        "knowledge_sources": output.get("knowledge_sources") if isinstance(output.get("knowledge_sources"), dict) else {},
        "audit_records": _llm_audit_records(output.get("audit_records")),
        "review_detail_summary": output.get("review_detail_summary")
        if isinstance(output.get("review_detail_summary"), dict)
        else {},
        "next_actions": output.get("next_actions") if isinstance(output.get("next_actions"), list) else [],
    }


def _llm_audit_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    for item in value[:LLM_CONTEXT_FINDINGS]:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "target_label": item.get("target_label"),
                "project_name": item.get("project_name"),
                "risk_level": item.get("risk_level"),
                "risk_reasons": item.get("risk_reasons") if isinstance(item.get("risk_reasons"), list) else [],
                "original_preview": item.get("original_preview") if isinstance(item.get("original_preview"), dict) else {},
                "confirmed_quote": item.get("confirmed_quote") if isinstance(item.get("confirmed_quote"), dict) else {},
                "price_change": item.get("price_change") if isinstance(item.get("price_change"), dict) else {},
                "before_after_summary": item.get("before_after_summary"),
                "market_search_explanation": item.get("market_search_explanation"),
                "market_search_context": item.get("market_search_context")
                if isinstance(item.get("market_search_context"), dict)
                else {},
            }
        )
    return records


def _llm_finding(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    return {
        "finding_type": item.get("finding_type") or item.get("type"),
        "severity": item.get("severity"),
        "target_ref": item.get("target_ref"),
        "target_label": item.get("target_label") or _evidence_target_label(evidence),
        "title": item.get("title"),
        "suggestion": item.get("suggestion"),
        "evidence_ref": _evidence_ref(evidence, item),
        "evidence": _compact_evidence(evidence),
    }


def _llm_suggestion(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "suggestion_id": item.get("suggestion_id"),
        "suggestion_type": item.get("suggestion_type"),
        "priority": item.get("priority"),
        "target_ref": item.get("target_ref"),
        "target_label": item.get("target_label") or _snapshot_target_label(item.get("current_snapshot")),
        "target_line_no": item.get("target_line_no"),
        "title": item.get("title"),
        "rationale": item.get("rationale"),
        "risk_note": item.get("risk_note"),
        "estimated_saving_amount": item.get("estimated_saving_amount"),
        "estimated_saving_rate": item.get("estimated_saving_rate"),
        "proposed_action": _proposed_action(item),
    }


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "target_label",
        "missing_count",
        "placeholder_count",
        "no_cost_reference_count",
        "line_no",
        "project_name",
        "unit_price",
        "total_price",
        "failed_checks",
        "match_status",
        "score",
    ):
        if key in evidence:
            compact[key] = evidence.get(key)
    row = evidence.get("requirement_row")
    if isinstance(row, dict):
        compact["requirement_row"] = {
            "source_sheet": row.get("source_sheet"),
            "raw_row_index": row.get("raw_row_index"),
            "item_name": row.get("item_name"),
            "quantity": row.get("quantity"),
            "unit": row.get("unit"),
        }
    return compact


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _string_or_none(item)
        if text:
            result.append(text)
    return result


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _headline(
    run: AgentRun,
    output: dict[str, Any],
    findings: list[dict[str, Any]],
    saving_explanation: dict[str, Any],
) -> str:
    if isinstance(output, dict) and output.get("audit_mode") == "confirmed_quote_risk_audit":
        audit_summary = output.get("audit_summary") if isinstance(output.get("audit_summary"), dict) else {}
        record_count = int(audit_summary.get("audit_record_count") or 0)
        manual_count = int(audit_summary.get("manual_modified_count") or 0)
        high_count = int(audit_summary.get("high_risk_count") or 0)
        return (
            f"本次已下发报价审计完成：记录 {record_count} 条预审风险或人工改价痕迹，"
            f"其中高风险 {high_count} 条、人工改价 {manual_count} 条；已保留修改前后和市场价辅助解释。"
        )

    risk_label = _risk_label(run.risk_level)
    recommendation_label = _recommendation_label(run.recommendation)
    metrics = output.get("metrics") if isinstance(output, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    missing_count = int(metrics.get("missing_count") or 0)
    placeholder_count = int(metrics.get("placeholder_count") or 0)
    no_cost_count = int(metrics.get("no_cost_reference_count") or 0)
    saving_amount = parse_amount(saving_explanation.get("estimated_total_saving_amount")) or 0

    risk_parts: list[str] = []
    if missing_count:
        risk_parts.append(f"疑似未报价 {missing_count} 行")
    if placeholder_count:
        risk_parts.append(f"占位未补价 {placeholder_count} 行")
    if no_cost_count:
        risk_parts.append(f"无成本库参考 {no_cost_count} 行")
    if not risk_parts and findings:
        risk_parts.append(f"风险发现 {len(findings)} 项")
    if not risk_parts:
        risk_parts.append("未发现明显阻断风险")

    return (
        f"本次复核判定为{risk_label}，建议{recommendation_label}。"
        f"重点关注：{'、'.join(risk_parts)}。"
        f"已量化预计可节省 {_money_text(saving_amount)} 元，采纳前仍需人工确认。"
    )


def _risk_explanations(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(findings, key=lambda item: (_severity_rank(item.get("severity")), item.get("id") or 0))
    explanations: list[dict[str, Any]] = []
    for item in ordered[:MAX_EXPLAINED_FINDINGS]:
        finding_type = item.get("finding_type") or item.get("type") or ""
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        explanations.append(
            {
                "severity": item.get("severity") or "medium",
                "severity_label": _severity_label(item.get("severity")),
                "finding_type": finding_type,
                "target_ref": item.get("target_ref"),
                "target_label": item.get("target_label") or _evidence_target_label(evidence),
                "title": item.get("title") or "风险项",
                "explanation": _finding_explanation(finding_type, evidence, item),
                "evidence_ref": _evidence_ref(evidence, item),
                "handling_advice": item.get("suggestion") or _default_handling_advice(finding_type),
            }
        )
    return explanations


def _suggestion_priorities(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        suggestions,
        key=lambda item: (
            _priority_rank(item.get("priority")),
            -(parse_amount(item.get("estimated_saving_amount")) or 0),
            item.get("id") or 0,
        ),
    )
    priorities: list[dict[str, Any]] = []
    for item in ordered[:MAX_PRIORITIZED_SUGGESTIONS]:
        saving_amount = parse_amount(item.get("estimated_saving_amount"))
        action = _proposed_action(item)
        priorities.append(
            {
                "suggestion_id": item.get("suggestion_id"),
                "priority": item.get("priority") or "medium",
                "priority_label": _priority_label(item.get("priority")),
                "suggestion_type": item.get("suggestion_type"),
                "status": item.get("status"),
                "target_ref": item.get("target_ref"),
                "target_label": item.get("target_label") or _snapshot_target_label(item.get("current_snapshot")),
                "target_line_no": item.get("target_line_no"),
                "title": item.get("title") or "Agent 建议",
                "reason": _suggestion_reason(item, saving_amount, action),
                "handling_advice": item.get("risk_note") or "请人工确认后再采纳。",
                "estimated_saving_amount": _round_money(saving_amount),
                "estimated_saving_rate": item.get("estimated_saving_rate"),
                "proposed_action": action,
            }
        )
    return priorities


def _saving_explanation(output: dict[str, Any], suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    raw_saving = output.get("saving_summary") if isinstance(output, dict) else {}
    raw_saving = raw_saving if isinstance(raw_saving, dict) else {}
    saving_items = [
        item
        for item in suggestions
        if (parse_amount(item.get("estimated_saving_amount")) or 0) > 0
    ]
    total = parse_amount(raw_saving.get("estimated_total_saving_amount"))
    if total is None:
        total = sum(parse_amount(item.get("estimated_saving_amount")) or 0 for item in saving_items)
    total = _round_money(total) or 0.0
    top_item = max(saving_items, key=lambda item: parse_amount(item.get("estimated_saving_amount")) or 0, default=None)
    top_amount = _round_money(top_item.get("estimated_saving_amount")) if top_item else 0.0
    top_title = top_item.get("title") if top_item else None

    if total > 0:
        text = (
            f"本次可量化省钱建议预计合计节省 {_money_text(total)} 元。"
            f"最高单条建议预计节省 {_money_text(top_amount)} 元；采纳前需核对规格、品牌、施工范围和客户口径。"
        )
    else:
        text = "当前后审计口径不生成可量化省钱建议。"

    return {
        "estimated_total_saving_amount": total,
        "saving_suggestion_count": len(saving_items),
        "max_single_saving_amount": top_amount or 0.0,
        "top_saving_suggestion_title": top_title,
        "text": text,
    }


def _manual_handling(
    run: AgentRun,
    findings: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> list[str]:
    if run.trigger_source == "scheduled_daily" or run.recommendation == "post_audit_recorded":
        return [
            "本次为已下发报价的事后审计，不需要再次采纳、生成草案或终确认。",
            "重点查看预审风险、人工改价原因和最终下发报价是否已经形成一致留痕。",
            "如发现下发价与人工确认口径不一致，再回到原报价业务记录核查。",
        ]

    actions: list[str] = []
    finding_types = {item.get("finding_type") or item.get("type") for item in findings}
    if {"missing_requirement_rows", "missing_requirement_row", "requirement_placeholders"} & finding_types:
        actions.append("先补齐疑似未报价行和占位未补价行，再考虑下发。")
    if "no_cost_reference" in finding_types:
        actions.append("无成本库参考行必须人工核价；确认下发后按规则沉淀 draft，待成本审核。")
    if {"ai_rewrite_risk", "ai_note_conflict", "preview_row_risk"} & finding_types:
        actions.append("逐行核对 AI 改写、备注和成本条目是否与原始需求一致。")
    if any((parse_amount(item.get("estimated_saving_amount")) or 0) > 0 for item in suggestions):
        actions.append("优先处理预计节省金额高的调价/替代建议，并记录采纳或拒绝原因。")
    if not actions:
        actions.append("按常规抽查流程复核关键金额行后再确认。")
    if run.recommendation == "manual_review_required":
        actions.insert(0, "当前建议人工复核，不建议直接下发。")
    return actions[:5]


def _uncertainties(
    output: dict[str, Any],
    findings: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> list[str]:
    metrics = output.get("metrics") if isinstance(output, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    uncertainties: list[str] = []
    if int(metrics.get("no_cost_reference_count") or 0) > 0:
        uncertainties.append("存在无 active 成本库参考的报价行，AI 估价缺少内部成本证据。")
    if int(metrics.get("placeholder_count") or 0) > 0:
        uncertainties.append("存在 AI 未返回的占位行，必须人工补价后才能进入最终确认。")
    if any(item.get("suggestion_type") == "cost_saving_replacement" for item in suggestions):
        uncertainties.append("省钱替代建议依赖规格和施工范围一致性，系统无法替代现场/成本人工判断。")
    if not findings:
        uncertainties.append("未读取到风险发现明细，本解释仅能基于运行摘要生成。")
    return uncertainties


def _finding_explanation(finding_type: str, evidence: dict[str, Any], item: dict[str, Any]) -> str:
    if finding_type in {"missing_requirement_rows", "missing_requirement_row"}:
        count = evidence.get("missing_count")
        if count:
            return f"确认清单与预审报价未完全对齐，至少 {count} 行可能未被报价覆盖，直接下发会造成漏报。"
        return "确认清单中有行未匹配到预审报价，需确认是否补价或记录不报价原因。"
    if finding_type == "requirement_placeholders":
        count = evidence.get("placeholder_count")
        return f"存在 {count or ''} 行 AI 未返回真实价格的占位报价，未补有效单价和合计前不能下发。".strip()
    if finding_type == "extra_preview_rows":
        return "预审报价里出现确认清单之外的额外行，需要确认是合理拆项还是重复报价。"
    if finding_type == "no_cost_reference":
        return "报价行未命中 active 成本库底价，当前价格缺少内部成本证据，需人工核价。"
    if finding_type == "cost_fallback_used":
        return "系统曾用成本库底价进行兜底，仍需人工确认施工范围、数量和客户口径。"
    if finding_type == "ai_rewrite_risk":
        return "AI 改写后的项目名或条目可能与原始需求/成本依据不一致，需要逐行确认。"
    if finding_type == "ai_note_conflict":
        return "AI 备注与成本依据存在冲突，可能影响对客户展示口径。"
    if finding_type == "preview_row_risk":
        failed = evidence.get("failed_checks") or []
        if failed:
            labels = [str(check.get("label") or check.get("key")) for check in failed[:3] if isinstance(check, dict)]
            return f"该预审行有检查项未通过：{'、'.join(labels)}。"
        return "该预审行被规则标记为需复核，建议按失败检查项逐项确认。"
    if finding_type == "quote_job_not_completed":
        return "报价任务尚未完成，当前复核结果可能不完整。"
    return item.get("title") or "该风险项需要人工结合报价明细复核。"


def _suggestion_reason(item: dict[str, Any], saving_amount: float | None, action: str | None) -> str:
    base = item.get("rationale") or "该建议来自 Agent 复核规则。"
    if saving_amount and saving_amount > 0:
        if action == "replace_cost_item" or item.get("suggestion_type") == "cost_saving_replacement":
            return f"{base} 这是一条省钱替代建议，预计可省 {_money_text(saving_amount)} 元。"
        return f"{base} 预计可省 {_money_text(saving_amount)} 元。"
    return base


def _evidence_ref(evidence: dict[str, Any], item: dict[str, Any]) -> str:
    row = evidence.get("requirement_row") if isinstance(evidence, dict) else None
    if isinstance(row, dict):
        sheet = row.get("source_sheet") or row.get("sheet_name")
        raw_index = row.get("raw_row_index") or row.get("row_index")
        if sheet or raw_index:
            return f"{sheet or '-'} 第 {raw_index or '-'} 行"
    if evidence.get("line_no"):
        return f"预审第 {evidence.get('line_no')} 行"
    return item.get("target_ref") or "-"


def _evidence_target_label(evidence: dict[str, Any]) -> str | None:
    if not isinstance(evidence, dict):
        return None
    label = _string_or_none(evidence.get("target_label"))
    if label:
        return label
    row = evidence.get("requirement_row")
    if isinstance(row, dict):
        return _row_label_from_mapping(row)
    if evidence.get("project_name") or evidence.get("line_no"):
        return _row_label_from_mapping(evidence)
    return None


def _snapshot_target_label(snapshot: Any) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    label = _string_or_none(snapshot.get("row_label") or snapshot.get("target_label"))
    if label:
        return label
    if snapshot.get("project_name") or snapshot.get("line_no"):
        return _row_label_from_mapping(snapshot)
    return None


def _row_label_from_mapping(row: dict[str, Any]) -> str:
    parts: list[str] = []
    line_no = row.get("line_no")
    if line_no not in (None, ""):
        parts.append(f"第 {line_no} 行")
    elif row.get("raw_row_index") not in (None, ""):
        parts.append(f"原始第 {row.get('raw_row_index')} 行")
    source_sheet = _short_text(row.get("source_sheet"))
    if source_sheet:
        parts.append(f"Sheet：{source_sheet}")
    name = _short_text(row.get("project_name") or row.get("item_name") or row.get("raw_text"))
    if name:
        parts.append(f"项目：{name}")
    spec = _short_text(row.get("spec") or row.get("specification") or row.get("feature") or row.get("notes"), 80)
    if spec:
        parts.append(f"规格：{spec}")
    quantity = row.get("quantity")
    unit = _short_text(row.get("unit"), 20)
    if quantity not in (None, "") or unit:
        parts.append(f"工程量：{quantity if quantity not in (None, '') else '-'}{unit or ''}")
    return "｜".join(parts) or "-"


def _short_text(value: Any, max_length: int = 60) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > max_length:
        return text[: max_length - 1] + "…"
    return text


def _default_handling_advice(finding_type: str) -> str:
    if finding_type in {"missing_requirement_rows", "missing_requirement_row", "requirement_placeholders"}:
        return "请先补价或打回重算。"
    if finding_type == "no_cost_reference":
        return "请人工核价并记录依据。"
    return "请人工确认后再进入下一步。"


def _proposed_action(item: dict[str, Any]) -> str | None:
    proposed = item.get("proposed_snapshot") or {}
    if not isinstance(proposed, dict):
        return None
    action = proposed.get("action")
    return str(action) if action else None


def _risk_label(value: str | None) -> str:
    return {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(value or "", "未评估")


def _recommendation_label(value: str | None) -> str:
    return {
        "manual_review_required": "人工复核后处理",
        "can_push_after_spot_check": "抽查后可下发",
        "can_push": "可下发",
    }.get(value or "", "按复核建议处理")


def _severity_label(value: str | None) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value or "", "中")


def _priority_label(value: str | None) -> str:
    return {"high": "高优先级", "medium": "中优先级", "low": "低优先级"}.get(value or "", "中优先级")


def _severity_rank(value: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value or "", 1)


def _priority_rank(value: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value or "", 1)


def _round_money(value: Any) -> float | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    return round(float(amount), 2)


def _money_text(value: Any) -> str:
    amount = parse_amount(value)
    if amount is None:
        amount = 0
    return f"{float(amount):.2f}"
