from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bidding import BidParseRun, TenderBusinessObject
from app.services.bidding_business_objects import (
    BUSINESS_ACTION_LABELS,
    BUSINESS_OBJECT_SUBTYPE_LABELS,
    BUSINESS_OBJECT_TYPE_LABELS,
)
from app.services.bidding_parser import dumps_json, loads_json
from app.services.model_gateway import post_json_via_gateway


BIDDING_LLM_PROMPT_VERSION = "biz4a_business_object_llm_review_v1"
UNCERTAIN_OBJECT_FLAGS = ("weak_split", "needs_llm_review", "needs_secondary_split")
LLM_REVIEW_DECISIONS = {"keep", "rename", "split", "ignore", "manual_review"}
LLM_BUSINESS_ACTIONS = set(BUSINESS_ACTION_LABELS)
MAX_EVIDENCE_SAMPLES = 6
MAX_TEXT_CHARS = 900
MAX_REASON_CHARS = 800
MAX_MANUAL_QUESTIONS = 5
MAX_SUGGESTED_SPLITS = 6


SYSTEM_PROMPT = """你是装饰工程投标中台的招标文件业务对象复核助手。

你的任务只处理系统已经标记为不确定的业务对象，包括 weak_split、needs_llm_review、needs_secondary_split。

必须遵守：
1. 只能基于输入的对象、证据样本和风险上下文判断，不得编造招标文件不存在的条款。
2. 不直接改写投标结果，不生成报价，不生成投标正文，只给人工复核建议。
3. 证据只能引用输入里的 evidence_id，例如 E1、E2。
4. business_action 只能从输入的 allowed_business_actions 中选择。
5. 如果证据不足，decision 使用 manual_review，并说明需要人工确认什么。
6. 如果建议拆分，decision 使用 split，但 suggested_splits 只是建议，不代表系统已创建新对象。
7. 必须输出严格 JSON，不要 Markdown，不要代码块。

返回 JSON：
{
  "object_review": {
    "decision": "keep|rename|split|ignore|manual_review",
    "confidence": 0.0,
    "suggested_object_type": "contract_clause",
    "suggested_object_subtype": "payment_document_condition",
    "suggested_title": "付款资料条件",
    "primary_business_action": "clarification",
    "secondary_business_actions": ["quote_allowance"],
    "selected_evidence_ids": ["E1"],
    "reason": "一句到三句话说明判断依据",
    "suggested_reviewer_note": "给人工复核人的处理建议",
    "manual_questions": ["需要向甲方确认的问题"],
    "suggested_splits": [
      {
        "object_subtype": "payment_document_condition",
        "title": "付款资料条件",
        "business_action": "document_response",
        "evidence_ids": ["E1"],
        "reason": "为什么应拆为该对象"
      }
    ]
  }
}
"""


def bidding_llm_model() -> str:
    return (settings.bidding_llm_model or settings.deepseek_model or "deepseek-v4-pro").strip()


def is_uncertain_business_object(item: TenderBusinessObject | dict[str, Any]) -> bool:
    normalized = _normalized_of(item)
    return any(bool(normalized.get(flag)) for flag in UNCERTAIN_OBJECT_FLAGS)


def select_uncertain_business_objects(
    items: Iterable[TenderBusinessObject],
    *,
    limit: int,
    force: bool = False,
    only_pending: bool = True,
    object_uuids: Iterable[str] | None = None,
) -> list[TenderBusinessObject]:
    selected: list[TenderBusinessObject] = []
    uuid_filter = {str(item).strip() for item in (object_uuids or []) if str(item).strip()}
    for item in items:
        if uuid_filter and item.object_uuid not in uuid_filter:
            continue
        if only_pending and item.review_status != "pending":
            continue
        normalized = _normalized_of(item)
        if not any(bool(normalized.get(flag)) for flag in UNCERTAIN_OBJECT_FLAGS):
            continue
        if not force and normalized.get("llm_review_status") in {
            "pending_manual_confirm",
            "accepted",
            "rejected",
            "modified",
            "error",
        }:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


async def review_uncertain_business_objects_with_deepseek(
    db: Session,
    run: BidParseRun,
    *,
    username: str | None = None,
    trace_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
    only_pending: bool = True,
    object_uuids: Iterable[str] | None = None,
) -> dict[str, Any]:
    configured_limit = max(int(settings.bidding_llm_max_objects or 25), 1)
    requested_limit = max(int(limit or configured_limit), 1)
    effective_limit = min(requested_limit, configured_limit)
    provider = (settings.bidding_llm_provider or "deepseek").strip().lower()
    model = bidding_llm_model()

    all_items = (
        db.query(TenderBusinessObject)
        .filter(TenderBusinessObject.parse_run_id == run.id, TenderBusinessObject.status == "active")
        .order_by(TenderBusinessObject.id.asc())
        .all()
    )
    uncertain_count = sum(1 for item in all_items if is_uncertain_business_object(item))
    candidates = select_uncertain_business_objects(
        all_items,
        limit=effective_limit,
        force=force,
        only_pending=only_pending,
        object_uuids=object_uuids,
    )

    result: dict[str, Any] = {
        "status": "ready",
        "run_uuid": run.run_uuid,
        "provider": provider,
        "model": model,
        "prompt_version": BIDDING_LLM_PROMPT_VERSION,
        "uncertain_count": uncertain_count,
        "candidate_count": len(candidates),
        "reviewed_count": 0,
        "skipped_count": max(uncertain_count - len(candidates), 0),
        "error_count": 0,
        "limit": effective_limit,
        "filters": {
            "flags": list(UNCERTAIN_OBJECT_FLAGS),
            "only_pending": only_pending,
            "force": force,
            "object_uuid_count": len([item for item in (object_uuids or []) if str(item).strip()]),
        },
        "items": [],
        "errors": [],
    }

    if provider != "deepseek":
        result["status"] = "skipped"
        result["skip_reason"] = "bidding_llm_provider_not_deepseek"
        result["skipped_count"] = uncertain_count
        return result
    if not (settings.deepseek_api_key or "").strip():
        result["status"] = "skipped"
        result["skip_reason"] = "deepseek_api_key_missing"
        result["skipped_count"] = uncertain_count
        return result
    if not candidates:
        result["status"] = "no_candidates"
        return result

    for item in candidates:
        context = build_business_object_review_context(item)
        try:
            raw_payload = await _call_deepseek_business_object_review(
                context,
                username=username,
                trace_id=trace_id or run.run_uuid,
            )
            review = clean_llm_review_payload(raw_payload, context)
            _store_llm_review(item, review, raw_payload=raw_payload, model=model)
            result["reviewed_count"] += 1
            result["items"].append(
                {
                    "object_uuid": item.object_uuid,
                    "title": item.title,
                    "object_type": item.object_type,
                    "object_subtype": item.object_subtype,
                    "decision": review.get("decision"),
                    "confidence": review.get("confidence"),
                    "selected_evidence_ids": review.get("selected_evidence_ids") or [],
                    "status": "pending_manual_confirm",
                }
            )
        except Exception as exc:
            result["error_count"] += 1
            error_message = str(exc) or exc.__class__.__name__
            error = {
                "object_uuid": item.object_uuid,
                "title": item.title,
                "object_type": item.object_type,
                "object_subtype": item.object_subtype,
                "error": error_message[:300],
            }
            result["errors"].append(error)
            _store_llm_review_error(item, error, model=model)
    db.commit()
    result["status"] = "completed" if result["reviewed_count"] else "failed"
    return result


def build_business_object_review_context(item: TenderBusinessObject) -> dict[str, Any]:
    normalized = _normalized_of(item)
    evidence_samples = _evidence_samples(item, normalized)
    candidate_subtypes = _candidate_subtypes(item, normalized)
    return {
        "prompt_version": BIDDING_LLM_PROMPT_VERSION,
        "task": "bidding_uncertain_business_object_review",
        "object_uuid": item.object_uuid,
        "object": {
            "object_type": item.object_type,
            "object_type_label": BUSINESS_OBJECT_TYPE_LABELS.get(item.object_type, item.object_type),
            "object_subtype": item.object_subtype,
            "object_subtype_label": BUSINESS_OBJECT_SUBTYPE_LABELS.get(item.object_subtype, item.object_subtype),
            "title": item.title,
            "normalized_value": item.normalized_value,
            "source_file": item.source_file,
            "source_location": item.source_location,
            "source_count": item.source_count,
            "document_section": item.document_section,
            "response_required": bool(item.response_required),
            "review_status": item.review_status,
            "confidence": item.confidence,
            "original_text": _truncate(item.original_text, MAX_TEXT_CHARS),
        },
        "uncertain_flags": {
            flag: bool(normalized.get(flag))
            for flag in UNCERTAIN_OBJECT_FLAGS
        },
        "quality_signals": {
            "large_object": bool(normalized.get("large_object")),
            "split_applied": bool(normalized.get("split_applied")),
            "split_parent_subtype": normalized.get("split_parent_subtype"),
            "split_confidence": normalized.get("split_confidence"),
            "representative_evidence_quality": normalized.get("representative_evidence_quality"),
            "representative_evidence_context_quality": normalized.get("representative_evidence_context_quality"),
            "representative_evidence_relevance": normalized.get("representative_evidence_relevance"),
            "low_confidence_representative": bool(normalized.get("low_confidence_representative")),
        },
        "current_actions": {
            "business_action": normalized.get("business_action"),
            "primary_business_action": normalized.get("primary_business_action"),
            "secondary_business_actions": _clean_string_list(normalized.get("secondary_business_actions")),
            "risk_secondary_actions": _clean_string_list(normalized.get("risk_secondary_actions")),
        },
        "risk_context": {
            "risk_cards": _compact_risk_cards(normalized.get("risk_cards")),
            "risk_grades": _clean_string_list(normalized.get("risk_grades")),
            "review_roles": _clean_string_list(normalized.get("review_roles")),
        },
        "candidate_subtypes": candidate_subtypes,
        "allowed_business_actions": sorted(LLM_BUSINESS_ACTIONS),
        "evidence_samples": evidence_samples,
    }


async def _call_deepseek_business_object_review(
    context: dict[str, Any],
    *,
    username: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    model = bidding_llm_model()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="bidding_business_object_llm_review",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=settings.bidding_llm_timeout_seconds,
        username=username,
        trace_id=trace_id,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}")
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    return _extract_json_object(content)


def clean_llm_review_payload(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    review = payload.get("object_review") if isinstance(payload.get("object_review"), dict) else payload
    if not isinstance(review, dict):
        review = {}

    validation_warnings: list[str] = []
    decision = str(review.get("decision") or "manual_review").strip().lower()
    if decision not in LLM_REVIEW_DECISIONS:
        validation_warnings.append(f"invalid_decision:{decision}")
        decision = "manual_review"

    allowed_evidence_ids = {
        str(item.get("evidence_id"))
        for item in context.get("evidence_samples", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    selected_evidence_ids = [
        item
        for item in _clean_string_list(review.get("selected_evidence_ids"))
        if item in allowed_evidence_ids
    ]
    if review.get("selected_evidence_ids") and not selected_evidence_ids:
        validation_warnings.append("selected_evidence_ids_not_in_context")

    allowed_subtypes = set(_clean_string_list(context.get("candidate_subtypes")))
    suggested_subtype = _string_or_none(review.get("suggested_object_subtype"))
    if suggested_subtype and allowed_subtypes and suggested_subtype not in allowed_subtypes:
        validation_warnings.append(f"suggested_subtype_out_of_candidates:{suggested_subtype}")
        suggested_subtype = None

    object_info = context.get("object") if isinstance(context.get("object"), dict) else {}
    suggested_type = _string_or_none(review.get("suggested_object_type"))
    if suggested_type and suggested_type != object_info.get("object_type"):
        validation_warnings.append(f"suggested_type_changed:{suggested_type}")
        suggested_type = object_info.get("object_type")

    primary_action = _clean_business_action(review.get("primary_business_action"), validation_warnings)
    secondary_actions = [
        action
        for action in (_clean_business_action(item, validation_warnings) for item in _clean_string_list(review.get("secondary_business_actions")))
        if action
    ]

    suggested_splits = _clean_suggested_splits(
        review.get("suggested_splits"),
        allowed_evidence_ids=allowed_evidence_ids,
        validation_warnings=validation_warnings,
    )

    return {
        "decision": decision,
        "confidence": _confidence(review.get("confidence")),
        "suggested_object_type": suggested_type or object_info.get("object_type"),
        "suggested_object_subtype": suggested_subtype or object_info.get("object_subtype"),
        "suggested_title": _string_or_none(review.get("suggested_title")) or object_info.get("title"),
        "primary_business_action": primary_action,
        "secondary_business_actions": _dedupe(secondary_actions),
        "selected_evidence_ids": selected_evidence_ids,
        "reason": _truncate(_string_or_none(review.get("reason")) or "", MAX_REASON_CHARS),
        "suggested_reviewer_note": _truncate(_string_or_none(review.get("suggested_reviewer_note")) or "", MAX_REASON_CHARS),
        "manual_questions": _clean_string_list(review.get("manual_questions"))[:MAX_MANUAL_QUESTIONS],
        "suggested_splits": suggested_splits,
        "validation_warnings": validation_warnings,
        "read_only": True,
    }


def _store_llm_review(
    item: TenderBusinessObject,
    review: dict[str, Any],
    *,
    raw_payload: dict[str, Any],
    model: str,
) -> None:
    normalized = _normalized_of(item)
    now = datetime.now(timezone.utc).isoformat()
    normalized["llm_review"] = review
    normalized["llm_review_status"] = "pending_manual_confirm"
    normalized["llm_provider"] = "deepseek"
    normalized["llm_model"] = model
    normalized["llm_prompt_version"] = BIDDING_LLM_PROMPT_VERSION
    normalized["llm_reviewed_at"] = now
    normalized["llm_decision"] = review.get("decision")
    normalized["llm_confidence"] = review.get("confidence")
    normalized["llm_suggested_subtype"] = review.get("suggested_object_subtype")
    normalized["llm_selected_evidence_ids"] = review.get("selected_evidence_ids") or []
    normalized["llm_raw_response_preview"] = _truncate(json.dumps(raw_payload, ensure_ascii=False), 1600)
    item.normalized_json = dumps_json(normalized)


def _store_llm_review_error(item: TenderBusinessObject, error: dict[str, Any], *, model: str) -> None:
    normalized = _normalized_of(item)
    normalized["llm_review_status"] = "error"
    normalized["llm_provider"] = "deepseek"
    normalized["llm_model"] = model
    normalized["llm_prompt_version"] = BIDDING_LLM_PROMPT_VERSION
    normalized["llm_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    normalized["llm_review_error"] = error
    item.normalized_json = dumps_json(normalized)


def _normalized_of(item: TenderBusinessObject | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        normalized = item.get("normalized") or item.get("normalized_json") or {}
        return normalized if isinstance(normalized, dict) else {}
    return loads_json(item.normalized_json, {}) if item.normalized_json else {}


def _evidence_samples(item: TenderBusinessObject, normalized: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = loads_json(item.evidence_json, []) if item.evidence_json else []
    if not isinstance(evidence, list):
        evidence = []
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence_item in evidence:
        if not isinstance(evidence_item, dict):
            continue
        text = _truncate(str(evidence_item.get("original_text") or evidence_item.get("text") or ""), MAX_TEXT_CHARS)
        if not text or text in seen:
            continue
        seen.add(text)
        samples.append(
            {
                "evidence_id": f"E{len(samples) + 1}",
                "source_kind": evidence_item.get("source_kind"),
                "source_file": evidence_item.get("source_file") or item.source_file,
                "source_location": evidence_item.get("source_location") or item.source_location,
                "document_section": evidence_item.get("document_section") or item.document_section,
                "risk_type": evidence_item.get("risk_type"),
                "risk_level": evidence_item.get("risk_level"),
                "risk_card_title": evidence_item.get("risk_card_title"),
                "evidence_quality": evidence_item.get("evidence_quality"),
                "evidence_context_quality": evidence_item.get("evidence_context_quality")
                or normalized.get("representative_evidence_context_quality"),
                "text": text,
            }
        )
        if len(samples) >= MAX_EVIDENCE_SAMPLES:
            break
    if not samples and item.original_text:
        samples.append(
            {
                "evidence_id": "E1",
                "source_kind": "business_object",
                "source_file": item.source_file,
                "source_location": item.source_location,
                "document_section": item.document_section,
                "text": _truncate(item.original_text, MAX_TEXT_CHARS),
            }
        )
    return samples


def _candidate_subtypes(item: TenderBusinessObject, normalized: dict[str, Any]) -> list[str]:
    subtypes = [
        item.object_subtype,
        normalized.get("split_parent_subtype"),
        normalized.get("suggested_object_subtype"),
        normalized.get("llm_suggested_subtype"),
    ]
    for risk_card in normalized.get("risk_cards") or []:
        if isinstance(risk_card, dict):
            subtypes.append(risk_card.get("risk_subtype"))
    known_subtypes = set(BUSINESS_OBJECT_SUBTYPE_LABELS)
    focused = [
        item
        for item in _dedupe(_clean_string_list(subtypes))
        if not known_subtypes or item in known_subtypes
    ]
    return _dedupe(focused + list(BUSINESS_OBJECT_SUBTYPE_LABELS))


def _compact_risk_cards(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cards: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        cards.append(
            {
                "card_id": item.get("card_id"),
                "title": item.get("title"),
                "risk_grade_v2": item.get("risk_grade_v2"),
                "primary_action": item.get("primary_action"),
                "secondary_actions": item.get("secondary_actions"),
                "review_roles": item.get("review_roles"),
            }
        )
    return cards


def _clean_suggested_splits(
    value: Any,
    *,
    allowed_evidence_ids: set[str],
    validation_warnings: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_ids = [
            evidence_id
            for evidence_id in _clean_string_list(item.get("evidence_ids"))
            if evidence_id in allowed_evidence_ids
        ]
        action = _clean_business_action(item.get("business_action"), validation_warnings)
        result.append(
            {
                "object_subtype": _string_or_none(item.get("object_subtype")),
                "title": _string_or_none(item.get("title")),
                "business_action": action,
                "evidence_ids": evidence_ids,
                "reason": _truncate(_string_or_none(item.get("reason")) or "", MAX_REASON_CHARS),
            }
        )
        if len(result) >= MAX_SUGGESTED_SPLITS:
            break
    return result


def _clean_business_action(value: Any, validation_warnings: list[str]) -> str | None:
    action = _string_or_none(value)
    if not action:
        return None
    if action not in LLM_BUSINESS_ACTIONS:
        validation_warnings.append(f"invalid_business_action:{action}")
        return None
    return action


def _extract_json_object(content: Any) -> dict[str, Any]:
    text = str(content or "").strip()
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


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    return [
        item
        for item in (_string_or_none(raw) for raw in raw_items)
        if item
    ]


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, round(score, 4)))


def _dedupe(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _string_or_none(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
