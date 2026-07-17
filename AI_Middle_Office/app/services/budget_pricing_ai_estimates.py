"""Manual AI unit-price estimation for mutable budget pricing drafts (P2-2C-1)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.budget_project import BudgetProjectProfile
from app.models.budget_pricing_draft import BudgetProjectPricingDraft, BudgetProjectPricingDraftLine
from app.models.user import User
from app.services.budget_pricing import (
    BudgetPricingError,
    _NUMERIC_20_6_MAX,
    _decimal,
    _decimal_text,
    _fits_numeric,
    _json_dump,
    _json_load,
    _q6,
    normalize_pricing_unit,
)
from app.services.budget_pricing_drafts import (
    _append_event,
    _apply_effective_price,
    _refresh_summary,
    get_budget_pricing_draft_line,
    get_current_budget_pricing_draft,
)
from app.services.model_gateway import post_json_via_gateway


AI_ESTIMATE_ENGINE_VERSION = "budget-pricing-ai-estimate-p2-2c1"


SYSTEM_PROMPT = """你是装饰装修工程成本预算助手。请根据预算清单行、单位、工程量、项目特征和当前模式，估算一个谨慎的税前成本单价。

红线：
1. 只输出 JSON，不要 Markdown。
2. 只能给单价建议，不能宣称这是正式定额或最终报价。
3. 若缺少依据，也要明确 confidence 和 risks，提醒人工确认。
4. 不要把估价写成企业定额或账户定额。

JSON 字段：
unit_price: number，大于 0，最多 6 位小数
confidence: number，0 到 1
basis: string，简短说明估价依据
risks: string array，列出需要人工确认的风险
"""


BATCH_SYSTEM_PROMPT = """你是装饰装修工程成本预算助手。请对一组预算清单行估算谨慎的税前成本单价。
硬性要求：
1. 只输出 JSON 对象，不要 Markdown，不要解释过程。
2. JSON 顶层必须是 {"items":[...]}。
3. 每个输入 row_id 必须返回一条 items 记录，不要漏行、不要合并行、不要新增行。
4. 只能给 mutable pricing draft 的建议单价；不要声称这是正式定额、最终报价、企业定额或账户定额。
5. 缺少依据时也要给保守估价，但降低 confidence 并写入 risks。
6. basis 控制在 60 字以内，risks 最多 3 条。
每条 items 字段：
row_id: string，必须原样返回输入 row_id
unit_price: number，大于 0，最多 6 位小数
confidence: number，0 到 1
basis: string
risks: string array
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _budget_pricing_ai_model() -> str:
    return (settings.budget_pricing_ai_model or settings.deepseek_model or "deepseek-v4-flash").strip()


def _line_input_snapshot(draft: BudgetProjectPricingDraft, line: BudgetProjectPricingDraftLine) -> dict[str, Any]:
    return {
        "draft_id": int(draft.id),
        "draft_uuid": draft.draft_uuid,
        "draft_revision": int(draft.revision),
        "pricing_mode": draft.pricing_mode,
        "line_id": int(line.id),
        "line_uuid": line.line_uuid,
        "line_revision": int(line.line_revision),
        "source_sheet": line.source_sheet,
        "source_raw_row_index": int(line.source_raw_row_index),
        "source_row_key": line.source_row_key,
        "item_name": line.item_name,
        "spec": line.spec,
        "unit": line.unit,
        "normalized_unit": normalize_pricing_unit(line.unit),
        "calculation_quantity": _decimal_text(line.calculation_quantity),
        "quantity_status": line.quantity_status,
        "match_status": line.match_status,
        "pricing_status": line.pricing_status,
        "match_evidence": _json_load(line.match_evidence_json, {}),
        "selected_source": _json_load(line.selected_source_snapshot_json, None),
    }


def _rule_estimate(snapshot: dict[str, Any], *, fallback_reason: str | None = None) -> dict[str, Any]:
    name = str(snapshot.get("item_name") or "")
    spec = str(snapshot.get("spec") or "")
    unit = str(snapshot.get("normalized_unit") or snapshot.get("unit") or "").lower()
    text = f"{name} {spec}"

    price = Decimal("85")
    if unit in {"m2", "㎡", "m²"}:
        price = Decimal("120")
    elif unit in {"m", "米"}:
        price = Decimal("45")
    elif unit in {"item", "项"}:
        price = Decimal("800")
    elif unit in {"set", "套"}:
        price = Decimal("350")
    elif unit in {"piece", "个"}:
        price = Decimal("80")

    if any(token in text for token in ("拆除", "清运", "铲除")):
        price *= Decimal("0.55")
    if any(token in text for token in ("石材", "不锈钢", "玻璃", "定制")):
        price *= Decimal("1.80")
    if any(token in text for token in ("防水", "龙骨", "吊顶", "隔断")):
        price *= Decimal("1.25")
    if any(token in text for token in ("灯", "开关", "插座", "给水", "排水", "阀门")):
        price *= Decimal("0.80")

    return {
        "unit_price": _decimal_text(_q6(price)),
        "confidence": 0.35,
        "basis": "未连接真实模型时的保守规则估价，仅用于草稿占位和流程验证。",
        "risks": [
            "缺少真实模型推理依据，必须人工确认。",
            "未读取外部市场价或客户认可价格。",
        ],
        "mode": "rule_fallback",
        "fallback_reason": fallback_reason,
    }


def _extract_json_object(content: Any) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            result.append(text[:300])
    return result[:8]


def _clean_llm_estimate(payload: dict[str, Any]) -> dict[str, Any]:
    price = _q6(_decimal(payload.get("unit_price")))
    if price is None or price <= 0 or not _fits_numeric(price, _NUMERIC_20_6_MAX):
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_INVALID_PRICE", status_code=502)
    confidence = payload.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.5
    confidence_value = max(0.0, min(1.0, confidence_value))
    basis = re.sub(r"\s+", " ", str(payload.get("basis") or "")).strip()[:1000]
    return {
        "unit_price": _decimal_text(price),
        "confidence": confidence_value,
        "basis": basis or "模型返回了估算单价，未提供额外依据。",
        "risks": _clean_string_list(payload.get("risks")),
        "mode": "deepseek",
    }


async def _generate_estimate(snapshot: dict[str, Any], *, current_user: User) -> dict[str, Any]:
    provider = (settings.budget_pricing_ai_provider or "rule").strip().lower()
    prompt_version = settings.budget_pricing_ai_prompt_version
    if provider != "deepseek":
        estimate = _rule_estimate(snapshot, fallback_reason="provider_not_configured")
        estimate.update(
            {
                "provider": "rule",
                "model": None,
                "prompt_version": prompt_version,
                "engine_version": AI_ESTIMATE_ENGINE_VERSION,
            }
        )
        return estimate

    if not (settings.deepseek_api_key or "").strip():
        model = _budget_pricing_ai_model()
        estimate = _rule_estimate(snapshot, fallback_reason="deepseek_api_key_missing")
        estimate.update(
            {
                "provider": "rule",
                "model": None,
                "prompt_version": prompt_version,
                "engine_version": AI_ESTIMATE_ENGINE_VERSION,
                "requested_provider": "deepseek",
                "requested_model": model,
            }
        )
        return estimate

    user_payload = {
        "prompt_version": prompt_version,
        "task": "budget_pricing_unit_price_estimate",
        "line": snapshot,
        "boundary": {
            "result_scope": "mutable_pricing_draft_only",
            "must_not_write_enterprise_quota": True,
            "must_not_write_account_quota_active": True,
        },
    }
    model = _budget_pricing_ai_model()
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="budget_pricing_ai_estimate",
        url=settings.deepseek_chat_url,
        json_payload={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=settings.budget_pricing_ai_timeout_seconds,
        username=current_user.username,
        trace_id=str(snapshot.get("line_uuid") or ""),
    )
    if not 200 <= response.status_code < 300:
        raise BudgetPricingError(
            "BUDGET_PRICING_AI_ESTIMATE_MODEL_ERROR",
            status_code=502,
            context={"http_status": response.status_code},
        )
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    estimate = _clean_llm_estimate(_extract_json_object(content))
    estimate.update(
        {
            "provider": "deepseek",
            "model": model,
            "prompt_version": prompt_version,
            "engine_version": AI_ESTIMATE_ENGINE_VERSION,
        }
    )
    return estimate


def build_budget_pricing_ai_estimate_input(
    draft: BudgetProjectPricingDraft,
    line: BudgetProjectPricingDraftLine,
) -> dict[str, Any]:
    return _line_input_snapshot(draft, line)


async def generate_budget_pricing_ai_estimate(
    snapshot: dict[str, Any],
    *,
    current_user: User,
) -> dict[str, Any]:
    return await _generate_estimate(snapshot, current_user=current_user)


def _batch_row_id(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("line_uuid") or snapshot.get("line_id") or snapshot.get("source_row_key") or "").strip()


def _batch_line_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_id": _batch_row_id(snapshot),
        "source_sheet": snapshot.get("source_sheet"),
        "source_raw_row_index": snapshot.get("source_raw_row_index"),
        "item_name": snapshot.get("item_name"),
        "spec": snapshot.get("spec"),
        "unit": snapshot.get("unit"),
        "normalized_unit": snapshot.get("normalized_unit"),
        "calculation_quantity": snapshot.get("calculation_quantity"),
        "quantity_status": snapshot.get("quantity_status"),
        "pricing_mode": snapshot.get("pricing_mode"),
    }


async def generate_budget_pricing_ai_estimate_batch(
    snapshots: list[dict[str, Any]],
    *,
    current_user: User,
) -> dict[str, dict[str, Any]]:
    rows = [snapshot for snapshot in snapshots if _batch_row_id(snapshot)]
    if not rows:
        return {}
    provider = (settings.budget_pricing_ai_provider or "rule").strip().lower()
    prompt_version = settings.budget_pricing_ai_prompt_version
    if provider != "deepseek" or not (settings.deepseek_api_key or "").strip():
        reason = "provider_not_configured" if provider != "deepseek" else "deepseek_api_key_missing"
        results: dict[str, dict[str, Any]] = {}
        for snapshot in rows:
            estimate = _rule_estimate(snapshot, fallback_reason=reason)
            estimate.update(
                {
                    "provider": "rule",
                    "model": None,
                    "prompt_version": prompt_version,
                    "engine_version": AI_ESTIMATE_ENGINE_VERSION,
                    "batch_mode": True,
                }
            )
            results[_batch_row_id(snapshot)] = estimate
        return results

    user_payload = {
        "prompt_version": prompt_version,
        "task": "budget_pricing_batch_unit_price_estimate",
        "rows": [_batch_line_payload(snapshot) for snapshot in rows],
        "boundary": {
            "result_scope": "mutable_pricing_draft_only",
            "must_not_write_enterprise_quota": True,
            "must_not_write_account_quota_active": True,
            "return_every_row_id": True,
        },
    }
    trace_id = f"batch:{rows[0].get('line_uuid') or rows[0].get('line_id')}:{len(rows)}"
    model = _budget_pricing_ai_model()
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="budget_pricing_ai_estimate_batch",
        url=settings.deepseek_chat_url,
        json_payload={
            "model": model,
            "messages": [
                {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
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
        trace_id=trace_id,
    )
    if not 200 <= response.status_code < 300:
        raise BudgetPricingError(
            "BUDGET_PRICING_AI_ESTIMATE_MODEL_ERROR",
            status_code=502,
            context={"http_status": response.status_code},
        )
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    payload = _extract_json_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_BATCH_INVALID_JSON", status_code=502)
    expected_ids = {_batch_row_id(snapshot) for snapshot in rows}
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("row_id") or "").strip()
        if not row_id or row_id not in expected_ids or row_id in results:
            continue
        estimate = _clean_llm_estimate(item)
        estimate.update(
            {
                "provider": "deepseek",
                "model": model,
                "prompt_version": prompt_version,
                "engine_version": AI_ESTIMATE_ENGINE_VERSION,
                "batch_mode": True,
                "batch_size": len(rows),
            }
        )
        results[row_id] = estimate
    return results


def apply_budget_pricing_ai_estimate_to_line(
    db: Session,
    *,
    draft_id: int,
    line_id: int,
    current_user: User,
    estimate: dict[str, Any],
    input_snapshot: dict[str, Any],
    reason: str | None = None,
    expected_revision: int | None = None,
    expected_line_revision: int | None = None,
    event_type: str = "ai_estimate_updated",
) -> tuple[BudgetProjectPricingDraft, BudgetProjectPricingDraftLine]:
    draft = (
        db.query(BudgetProjectPricingDraft)
        .filter(BudgetProjectPricingDraft.id == int(draft_id))
        .with_for_update()
        .one_or_none()
    )
    if draft is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
    if expected_revision is not None and int(draft.revision) != int(expected_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_REVISION_CONFLICT",
            context={"expected_revision": expected_revision, "current_revision": draft.revision},
        )
    line = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(
            BudgetProjectPricingDraftLine.id == int(line_id),
            BudgetProjectPricingDraftLine.draft_id == draft.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if line is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_LINE_NOT_FOUND", status_code=404)
    if expected_line_revision is not None and int(line.line_revision) != int(expected_line_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_LINE_REVISION_CONFLICT",
            context={
                "expected_line_revision": expected_line_revision,
                "current_line_revision": line.line_revision,
            },
        )
    if line.manual_unit_price is not None:
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_MANUAL_PRICE_EXISTS", status_code=409)
    if line.base_unit_price is not None:
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_BASE_PRICE_EXISTS", status_code=409)

    price = _q6(_decimal(estimate.get("unit_price")))
    if price is None or price <= 0 or not _fits_numeric(price, _NUMERIC_20_6_MAX):
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_INVALID_PRICE", status_code=502)

    previous_revision = int(draft.revision)
    previous_ai_price = _decimal_text(line.ai_estimated_unit_price)
    line.ai_estimated_unit_price = price
    snapshot_payload = {
        "generated_at": _utcnow_iso(),
        "estimate": estimate,
        "input": input_snapshot,
        "reason": (reason or "").strip()[:2000] or None,
    }
    line.ai_estimate_snapshot_json = _json_dump(snapshot_payload)
    warnings = _json_load(line.warnings_json, [])
    if isinstance(warnings, list):
        warnings = [warning for warning in warnings if warning != "BUDGET_PRICING_DRAFT_LLM_NOT_CONNECTED"]
        line.warnings_json = _json_dump(warnings)
    _apply_effective_price(line, manual_unit_price=line.manual_unit_price)
    line.line_revision = int(line.line_revision) + 1
    line.updated_by = current_user.id
    draft.revision = int(draft.revision) + 1
    draft.updated_by = current_user.id
    summary = _refresh_summary(db, draft)
    _append_event(
        db,
        draft=draft,
        current_user=current_user,
        event_type=event_type,
        from_mode=draft.pricing_mode,
        from_revision=previous_revision,
        event={
            "line_id": line.id,
            "line_uuid": line.line_uuid,
            "previous_ai_estimated_unit_price": previous_ai_price,
            "ai_estimated_unit_price": _decimal_text(price),
            "provider": estimate.get("provider"),
            "model": estimate.get("model"),
            "mode": estimate.get("mode"),
            "confidence": estimate.get("confidence"),
            "reason": (reason or "").strip()[:2000] or None,
            "summary": summary,
        },
    )
    db.flush()
    return draft, line


async def estimate_budget_pricing_draft_line(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    *,
    line_identifier: str | int,
    expected_revision: int,
    expected_line_revision: int,
    reason: str | None = None,
) -> tuple[BudgetProjectPricingDraft, BudgetProjectPricingDraftLine]:
    draft = get_current_budget_pricing_draft(db, profile, current_user, for_update=False)
    if draft is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_NOT_FOUND", status_code=404)
    if int(draft.revision) != int(expected_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_REVISION_CONFLICT",
            context={"expected_revision": expected_revision, "current_revision": draft.revision},
        )
    line = get_budget_pricing_draft_line(db, draft, line_identifier, for_update=False)
    if int(line.line_revision) != int(expected_line_revision):
        raise BudgetPricingError(
            "BUDGET_PRICING_DRAFT_LINE_REVISION_CONFLICT",
            context={
                "expected_line_revision": expected_line_revision,
                "current_line_revision": line.line_revision,
            },
        )
    if line.manual_unit_price is not None:
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_MANUAL_PRICE_EXISTS", status_code=409)
    if line.base_unit_price is not None:
        raise BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_BASE_PRICE_EXISTS", status_code=409)

    draft_id = int(draft.id)
    line_id = int(line.id)
    snapshot = build_budget_pricing_ai_estimate_input(draft, line)
    estimate = await generate_budget_pricing_ai_estimate(snapshot, current_user=current_user)
    return apply_budget_pricing_ai_estimate_to_line(
        db,
        draft_id=draft_id,
        line_id=line_id,
        current_user=current_user,
        estimate=estimate,
        input_snapshot=snapshot,
        reason=reason,
        expected_revision=expected_revision,
        expected_line_revision=expected_line_revision,
    )
