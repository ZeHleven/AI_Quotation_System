"""Industry-data AI estimates for the isolated pricing-agent workflow."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.core.config import settings
from app.models.user import User
from app.services.model_gateway import post_json_via_gateway


_Q6 = Decimal("0.000001")
_SYSTEM_PROMPT = """你是中国装饰装修工程造价估算助手。
请结合城市、项目业态、装修档次和清单行，给出谨慎的税前综合单价估算。
只能返回 JSON 对象，格式为 {"items":[...]}。每个输入 row_id 必须原样返回。
每项字段：row_id、unit_price（正数）、confidence（0到1）、basis（不超过80字）、risks（最多3条）。
这是行业数据的 AI 推算，不是企业定额、历史成交价或正式报价；不得虚构具体文件、定额编号或市场调查来源。"""


class PricingIndustryEstimateError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 502, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _decimal_text(value: Any) -> str | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return format(parsed.quantize(_Q6, rounding=ROUND_HALF_UP), "f")


def _json_object(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
                return payload if isinstance(payload, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def _clean_result(item: dict[str, Any]) -> dict[str, Any] | None:
    row_id = re.sub(r"\s+", " ", str(item.get("row_id") or "")).strip()[:128]
    unit_price = _decimal_text(item.get("unit_price"))
    if not row_id or unit_price is None:
        return None
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.35
    basis = re.sub(r"\s+", " ", str(item.get("basis") or "")).strip()[:500]
    risks = [
        re.sub(r"\s+", " ", str(value)).strip()[:300]
        for value in (item.get("risks") if isinstance(item.get("risks"), list) else [])
        if str(value or "").strip()
    ][:3]
    return {
        "row_id": row_id,
        "unit_price": unit_price,
        "confidence": confidence,
        "basis": basis or "根据地区、业态、装修档次和清单特征进行 AI 推算。",
        "risks": risks,
        "source_label": "行业数据·AI推算",
        "provider": "deepseek",
        "model": (settings.budget_pricing_ai_model or settings.deepseek_model or "deepseek-chat").strip(),
        "prompt_version": "pricing-agent-industry-v1",
    }


async def estimate_industry_prices(
    *,
    rows: list[dict[str, Any]],
    context: dict[str, str],
    current_user: User,
) -> dict[str, dict[str, Any]]:
    """Return estimates keyed by row id.

    No deterministic placeholder is emitted.  If a real model is not
    configured, the source remains unavailable instead of masquerading as
    industry evidence.
    """

    if not rows:
        return {}
    provider = (settings.budget_pricing_ai_provider or "").strip().lower()
    if provider != "deepseek" or not (settings.deepseek_api_key or "").strip():
        return {}
    model = (settings.budget_pricing_ai_model or settings.deepseek_model or "deepseek-chat").strip()
    payload = {
        "task": "pricing_agent_industry_estimate",
        "context": {
            "city": context.get("city"),
            "project_type": context.get("project_type"),
            "decoration_level": context.get("decoration_level"),
        },
        "rows": rows,
        "boundary": {
            "evidence_label": "行业数据·AI推算",
            "must_not_claim_archive_or_enterprise_evidence": True,
            "requires_human_review": True,
        },
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="pricing_agent_industry_estimate",
        url=settings.deepseek_chat_url,
        json_payload={
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        },
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=settings.budget_pricing_ai_timeout_seconds,
        username=current_user.username,
        trace_id=f"pricing-agent:{rows[0].get('row_id')}:{len(rows)}",
    )
    if not 200 <= response.status_code < 300:
        raise PricingIndustryEstimateError(
            "PRICING_AGENT_INDUSTRY_MODEL_ERROR",
            context={"http_status": response.status_code},
        )
    body = _json_object(response.json().get("choices", [{}])[0].get("message", {}).get("content", ""))
    items = body.get("items")
    if not isinstance(items, list):
        raise PricingIndustryEstimateError("PRICING_AGENT_INDUSTRY_INVALID_JSON")
    expected = {str(row.get("row_id") or "") for row in rows}
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        cleaned = _clean_result(item)
        if cleaned and cleaned["row_id"] in expected and cleaned["row_id"] not in results:
            results[cleaned["row_id"]] = cleaned
    return results
