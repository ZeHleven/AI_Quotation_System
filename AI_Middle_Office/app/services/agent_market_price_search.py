from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services.quote_history import parse_amount


MARKET_SEARCH_TOOL_NAME = "market_price_web_search_v1"
MARKET_SEARCH_CITIES = ("东莞", "深圳")
MAX_MARKET_SEARCH_RECORDS = 20


def query_market_price_web_search(
    audit_records: list[dict[str, Any]],
    *,
    audit_date: date | datetime | str | None = None,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    query_date = _coerce_date(audit_date)
    searchable_records = [_record_search_input(record) for record in audit_records]
    searchable_records = [item for item in searchable_records if item][:MAX_MARKET_SEARCH_RECORDS]

    if not searchable_records:
        return _empty_result(query_date=query_date, status="no_audit_records")
    if not settings.feature_agent_market_web_search:
        return _empty_result(
            query_date=query_date,
            status="disabled",
            records=searchable_records,
            reason="FEATURE_AGENT_MARKET_WEB_SEARCH=false",
        )
    provider = (settings.market_search_provider or "tavily").strip().lower()
    if provider != "tavily":
        return _empty_result(
            query_date=query_date,
            status="unsupported_provider",
            records=searchable_records,
            reason=f"unsupported provider: {provider}",
        )
    if not (settings.market_search_api_key or "").strip():
        return _empty_result(
            query_date=query_date,
            status="missing_api_key",
            records=searchable_records,
            reason="MARKET_SEARCH_API_KEY missing",
        )

    items: list[dict[str, Any]] = []
    by_target_ref: dict[str, Any] = {}
    for record_input in searchable_records:
        city_results: dict[str, Any] = {}
        for city in MARKET_SEARCH_CITIES:
            search_payload = _tavily_search(record_input, city=city, query_date=query_date)
            city_results[city] = search_payload
        structured = _structure_with_deepseek(
            record_input,
            city_results=city_results,
            query_date=query_date,
            username=username,
            trace_id=trace_id,
        )
        item = {
            "target_ref": record_input["target_ref"],
            "target_label": record_input.get("target_label"),
            "item_name": record_input.get("item_name"),
            "spec": record_input.get("spec"),
            "unit": record_input.get("unit"),
            "confirmed_unit_price": record_input.get("confirmed_unit_price"),
            "query_date": query_date.isoformat(),
            "provider": provider,
            "cities": structured.get("cities") if isinstance(structured.get("cities"), dict) else {},
            "sources": structured.get("sources") if isinstance(structured.get("sources"), list) else [],
            "confidence": structured.get("confidence") or _fallback_confidence(city_results),
            "explanation": structured.get("explanation") or _fallback_explanation(city_results),
            "raw_search": city_results,
            "llm_mode": structured.get("llm_mode") or "rule_fallback",
        }
        items.append(item)
        by_target_ref[str(record_input["target_ref"])] = item

    covered = [item for item in items if _has_search_sources(item)]
    return {
        "tool": MARKET_SEARCH_TOOL_NAME,
        "scope": "quote_audit.live_web_market_reference",
        "provider": provider,
        "query_date": query_date.isoformat(),
        "cities": list(MARKET_SEARCH_CITIES),
        "summary": {
            "status": "ok",
            "searched_line_count": len(searchable_records),
            "covered_line_count": len(covered),
            "result_count": sum(len(item.get("sources") or []) for item in items),
            "max_results_per_city": settings.market_search_max_results,
            "deepseek_used": any(item.get("llm_mode") == "deepseek" for item in items),
            "snapshot_only": True,
        },
        "items": items,
        "by_target_ref": by_target_ref,
    }


def market_search_context_for_record(market_context: dict[str, Any], target_ref: str | None) -> dict[str, Any]:
    if not target_ref:
        return {}
    by_ref = market_context.get("by_target_ref") if isinstance(market_context, dict) else {}
    if not isinstance(by_ref, dict):
        return {}
    value = by_ref.get(str(target_ref))
    return value if isinstance(value, dict) else {}


def build_market_search_explanation(context: dict[str, Any]) -> str:
    if not context:
        return "联网市场价参考：本条未触发实时搜索或未生成可用搜索快照。"
    explanation = str(context.get("explanation") or "").strip()
    if explanation:
        return f"联网市场价参考：{explanation}"
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    if not sources:
        return "联网市场价参考：未检索到可信的深圳/东莞当天公开市场价结果。"
    return f"联网市场价参考：已检索到 {len(sources)} 条公开网页结果，需结合来源可信度人工理解。"


def _tavily_search(record_input: dict[str, Any], *, city: str, query_date: date) -> dict[str, Any]:
    query = _search_query(record_input, city=city, query_date=query_date)
    max_results = max(1, min(settings.market_search_max_results, 10))
    payload = {
        "api_key": settings.market_search_api_key.strip(),
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    try:
        with httpx.Client(timeout=settings.market_search_timeout_seconds, trust_env=False) as client:
            response = client.post(
                settings.market_search_endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            return {"city": city, "query": query, "status": "http_error", "http_status": response.status_code, "results": []}
        response_payload = response.json()
        values = (response_payload.get("results") or [])[:max_results]
        return {
            "city": city,
            "query": query,
            "status": "ok",
            "answer": response_payload.get("answer"),
            "response_time": response_payload.get("response_time"),
            "results": [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": item.get("content"),
                    "score": item.get("score"),
                    "date_last_crawled": None,
                }
                for item in values
                if isinstance(item, dict)
            ],
        }
    except Exception as exc:
        return {"city": city, "query": query, "status": "error", "error": str(exc)[:240], "results": []}


def _structure_with_deepseek(
    record_input: dict[str, Any],
    *,
    city_results: dict[str, Any],
    query_date: date,
    username: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    if not (settings.deepseek_api_key or "").strip():
        return _fallback_structure(city_results, reason="deepseek_api_key_missing")
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是装修报价后审计的市场价检索结果整理助手。只基于输入的搜索结果整理 JSON，"
                    "不得编造价格、来源或发布日期。若网页摘要没有明确价格，请返回空价格区间并降低 confidence。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "structure_market_price_search_results",
                        "query_date": query_date.isoformat(),
                        "record": record_input,
                        "search_results": city_results,
                        "required_schema": {
                            "cities": {
                                "深圳": {"price_range": {"min": None, "max": None}, "unit": None, "summary": ""},
                                "东莞": {"price_range": {"min": None, "max": None}, "unit": None, "summary": ""},
                            },
                            "sources": [{"city": "", "title": "", "url": "", "price_text": "", "date": ""}],
                            "confidence": "low|medium|high",
                            "explanation": "一句中文解释，只说明可查到什么和对下发价的参考意义",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=settings.agent_llm_timeout_seconds, trust_env=False) as client:
            response = client.post(
                settings.deepseek_chat_url,
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key.strip()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            return _fallback_structure(city_results, reason=f"deepseek_http_{response.status_code}")
        content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        result = _extract_json_object(content)
        result["llm_mode"] = "deepseek"
        return result
    except Exception as exc:
        return _fallback_structure(city_results, reason=f"deepseek_error:{str(exc)[:120]}")


def _fallback_structure(city_results: dict[str, Any], *, reason: str) -> dict[str, Any]:
    sources = _flatten_sources(city_results)
    return {
        "cities": {
            city: {"price_range": {"min": None, "max": None}, "unit": None, "summary": _city_source_summary(city_results.get(city))}
            for city in MARKET_SEARCH_CITIES
        },
        "sources": sources,
        "confidence": "low" if sources else "none",
        "explanation": _fallback_explanation(city_results),
        "llm_mode": "rule_fallback",
        "fallback_reason": reason,
    }


def _record_search_input(record: dict[str, Any]) -> dict[str, Any] | None:
    target_ref = record.get("target_ref")
    if not target_ref:
        return None
    preview = record.get("original_preview") if isinstance(record.get("original_preview"), dict) else {}
    reference = preview.get("cost_reference") if isinstance(preview.get("cost_reference"), dict) else {}
    item_name = record.get("project_name") or reference.get("item_name") or record.get("target_label")
    if not item_name:
        return None
    confirmed = record.get("confirmed_quote") if isinstance(record.get("confirmed_quote"), dict) else {}
    return {
        "target_ref": target_ref,
        "target_label": record.get("target_label"),
        "item_name": item_name,
        "spec": reference.get("spec") or preview.get("notes"),
        "unit": record.get("unit") or reference.get("unit"),
        "confirmed_unit_price": parse_amount(confirmed.get("unit_price")),
    }


def _search_query(record_input: dict[str, Any], *, city: str, query_date: date) -> str:
    parts = [
        city,
        query_date.isoformat(),
        "装修工程",
        str(record_input.get("item_name") or ""),
        str(record_input.get("spec") or ""),
        str(record_input.get("unit") or ""),
        "市场价 单价 报价",
    ]
    return " ".join(part for part in parts if part.strip())


def _empty_result(
    *,
    query_date: date,
    status: str,
    records: list[dict[str, Any]] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    records = records or []
    return {
        "tool": MARKET_SEARCH_TOOL_NAME,
        "scope": "quote_audit.live_web_market_reference",
        "provider": (settings.market_search_provider or "tavily").strip().lower(),
        "query_date": query_date.isoformat(),
        "cities": list(MARKET_SEARCH_CITIES),
        "summary": {
            "status": status,
            "reason": reason,
            "searched_line_count": len(records),
            "covered_line_count": 0,
            "result_count": 0,
            "max_results_per_city": settings.market_search_max_results,
            "deepseek_used": False,
            "snapshot_only": True,
        },
        "items": [],
        "by_target_ref": {},
    }


def _flatten_sources(city_results: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for city, payload in city_results.items():
        if not isinstance(payload, dict):
            continue
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            sources.append(
                {
                    "city": city,
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": item.get("snippet"),
                    "date": item.get("date_last_crawled"),
                }
            )
    return sources


def _fallback_confidence(city_results: dict[str, Any]) -> str:
    count = len(_flatten_sources(city_results))
    if count >= 4:
        return "medium"
    if count:
        return "low"
    return "none"


def _fallback_explanation(city_results: dict[str, Any]) -> str:
    sources = _flatten_sources(city_results)
    if not sources:
        return "未检索到可信的深圳/东莞当天公开市场价结果。"
    return f"检索到 {len(sources)} 条公开网页结果；价格区间需以 DeepSeek 结构化结果或人工查看来源为准。"


def _city_source_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "未检索"
    count = len(value.get("results") or [])
    if count:
        return f"检索到 {count} 条网页结果。"
    return "未检索到网页结果。"


def _has_search_sources(item: dict[str, Any]) -> bool:
    return bool(item.get("sources"))


def _coerce_date(value: date | datetime | str | None) -> date:
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return datetime.now().date()
    return date.fromisoformat(text[:10])


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("MARKET_SEARCH_JSON_NOT_OBJECT")
    return value
