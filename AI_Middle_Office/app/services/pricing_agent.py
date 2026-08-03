"""Controlled pricing-agent v1 orchestration.

The LLM is only an optional final industry-estimate tool.  Source retrieval,
priority, match-mode boundaries, and persistence are deterministic code.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time_utils import app_local_naive
from app.models.pricing_agent import (
    RUN_STATUS_PARTIAL,
    RUN_STATUS_PROCESSING,
    RUN_STATUS_SUCCEEDED,
    PricingAgentRun,
    PricingAgentRunLine,
)
from app.models.quote_job import QuoteJob
from app.models.quote_preview_draft import QuotePreviewDraft
from app.models.user import User
from app.schemas.pricing_agent import (
    PricingAgentCandidateSelectIn,
    PricingAgentManualPriceIn,
    PricingAgentRunCreateIn,
)
from app.services.account_tenancy import resolve_current_account
from app.services.pricing_agent_retrieval import (
    RetrievalSourceResult,
    retrieve_archive,
    retrieve_enterprise,
)
from app.services.pricing_industry_estimates import (
    PricingIndustryEstimateError,
    estimate_industry_prices,
)
from app.services.quote_job_readability import apply_job_request_summary, apply_job_result_summary
from app.services.quote_job_runner import append_job_event
from app.services.quote_preview_drafts import save_preview_draft


_Q6 = Decimal("0.000001")
_SOURCE_LABELS = {
    "archive": "存档数据",
    "enterprise": "企业数据",
    "industry": "行业数据·AI推算",
    "manual": "人工补价",
}


class PricingAgentError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite():
        return None
    return result.quantize(_Q6, rounding=ROUND_HALF_UP)


def _decimal_text(value: Any) -> str | None:
    parsed = _decimal(value)
    return format(parsed, "f") if parsed is not None else None


def _positive_decimal(value: Any) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed > 0 else None


def _json_number(value: Any) -> float | None:
    parsed = _decimal(value)
    return float(parsed) if parsed is not None else None


def _confirmation_data(run: PricingAgentRun) -> dict[str, Any]:
    stored = _json_load(run.confirmation_json, {})
    data = dict(stored) if isinstance(stored, dict) else {}
    data.update(
        {
            "confirmed": bool(run.confirmed_quote_job_id),
            "quote_job_id": run.confirmed_quote_job_id,
            "preview_draft_id": run.confirmed_preview_draft_id,
            "confirmed_by": run.confirmed_by,
            "confirmed_at": run.confirmed_at.isoformat() if run.confirmed_at else None,
            "confirmation_hash": run.confirmation_hash,
        }
    )
    if run.confirmed_quote_job_id:
        data["draft_url"] = (
            f"/index.html?quote_job_id={run.confirmed_quote_job_id}"
            "&from=pricing_agent"
        )
    return data


def _serialize_run_line(line: PricingAgentRunLine) -> dict[str, Any]:
    evidence = _json_load(line.evidence_json, {})
    if not isinstance(evidence, dict):
        evidence = {}
    candidates = _json_load(line.candidates_json, [])
    if not isinstance(candidates, list):
        candidates = []
    selected_candidate = _json_load(line.selected_candidate_json, None)
    selected_evidence = (
        selected_candidate
        if isinstance(selected_candidate, dict)
        else evidence.get("selected_evidence")
    )
    source_label = (
        selected_evidence.get("source_label")
        if isinstance(selected_evidence, dict)
        else None
    ) or _SOURCE_LABELS.get(str(line.selected_source or ""))
    return {
        "line_uuid": line.line_uuid,
        "row_key": line.row_key,
        "item_code": line.item_code,
        "item_name": line.item_name,
        "specification": line.specification,
        "quantity": _decimal_text(line.quantity),
        "unit": line.unit,
        "selected_source": line.selected_source,
        "source_label": source_label,
        "match_type": line.match_type,
        "unit_price": _decimal_text(line.unit_price),
        "total_price": _decimal_text(line.total_price),
        "confidence": _decimal_text(line.confidence),
        "requires_review": bool(line.requires_review),
        "status": "priced" if line.unit_price is not None else "unpriced",
        "query_plan": evidence.get("query_plan") or {},
        "source_evidence": evidence.get("source_evidence") or {},
        "selected_evidence": selected_evidence,
        "candidates": candidates,
        "selection_origin": line.selection_origin or "automatic",
        "manual_candidate_selected": (line.selection_origin or "automatic") in {
            "manual",
            "manual_candidate",
        },
        "manual_price_entered": (line.selection_origin or "automatic") == "manual_price",
        "manual_selected_by": line.manual_selected_by,
        "manual_selected_at": (
            line.manual_selected_at.isoformat()
            if line.manual_selected_at
            else None
        ),
        "decision_revision": int(line.decision_revision or 0),
    }


def _summary_from_lines(
    run: PricingAgentRun,
    lines: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _json_load(run.summary_json, {})
    if not isinstance(summary, dict):
        summary = {}
    priced_count = sum(line.get("unit_price") is not None for line in lines)
    summary.update(
        {
            "row_count": len(lines),
            "priced_count": priced_count,
            "unpriced_count": len(lines) - priced_count,
            "requires_review_count": sum(
                bool(line.get("requires_review"))
                for line in lines
            ),
            "source_counts": {
                source: sum(line.get("selected_source") == source for line in lines)
                for source in ("archive", "enterprise", "industry", "manual")
            },
            "mode": run.mode,
            "sources": _json_load(run.sources_json, []),
        }
    )
    return summary


def _sync_run_result(run: PricingAgentRun) -> dict[str, Any]:
    lines = [_serialize_run_line(line) for line in run.lines]
    summary = _summary_from_lines(run, lines)
    run.status = (
        RUN_STATUS_SUCCEEDED
        if summary["priced_count"] == summary["row_count"]
        else RUN_STATUS_PARTIAL
    )
    run.summary_json = _json_dump(summary)
    result = {"summary": summary, "lines": lines}
    run.result_json = _json_dump(result)
    return result


def _source_priority(source: str) -> int:
    return {"archive": 0, "enterprise": 1, "industry": 2}.get(source, 99)


def _query_plan(line: dict[str, Any], context: dict[str, str], *, expanded: bool) -> dict[str, Any]:
    base_parts = [
        str(line.get("item_code") or "").strip(),
        str(line.get("item_name") or "").strip(),
        str(line.get("specification") or "").strip(),
        str(line.get("unit") or "").strip(),
    ]
    base_query = " ".join(part for part in base_parts if part)
    context_parts = [
        context.get("city", ""),
        context.get("project_type", ""),
        context.get("decoration_level", ""),
        base_query,
    ]
    return {
        "exact_keys": {
            "item_code": line.get("item_code"),
            "item_name": line.get("item_name"),
            "specification": line.get("specification"),
            "unit": line.get("unit"),
        },
        "base_query": base_query,
        "context_query": " ".join(part for part in context_parts if part),
        "context_policy": "soft_rerank_only",
        "channels": ["exact"] if not expanded else ["exact", "keyword", "vector"],
        "vector_channel": (
            "configured"
            if expanded and settings.feature_pricing_agent_hybrid_search
            else ("disabled" if expanded else "not_used")
        ),
    }


def _choose_retrieval_result(
    source_results: list[RetrievalSourceResult],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates = [
        candidate
        for result in source_results
        for candidate in result.candidates
    ]
    candidates.sort(
        key=lambda item: (
            -Decimal(str(item.get("score") or "0")),
            _source_priority(str(item.get("source") or "")),
            str(item.get("source_record_id") or ""),
        )
    )
    selected_options = [result.selected for result in source_results if result.selected is not None]
    selected_options.sort(
        key=lambda item: (
            -Decimal(str(item.get("score") or "0")),
            _source_priority(str(item.get("source") or "")),
        )
    )
    return (selected_options[0] if selected_options else None), candidates[:10]


def _source_evidence(results: list[RetrievalSourceResult]) -> dict[str, Any]:
    return {
        result.source: {
            "channel_status": result.channel_status,
            "source_issue": result.source_issue,
            "candidate_count": len(result.candidates),
        }
        for result in results
    }


def _line_result(
    *,
    line: dict[str, Any],
    selected: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    source_results: list[RetrievalSourceResult],
    query_plan: dict[str, Any],
) -> dict[str, Any]:
    quantity = _decimal(line.get("quantity"))
    unit_price = _decimal(selected.get("unit_price")) if selected else None
    total_price = (
        (quantity * unit_price).quantize(_Q6, rounding=ROUND_HALF_UP)
        if quantity is not None and unit_price is not None
        else None
    )
    match_type = str(selected.get("match_type") or "") if selected else None
    exact = match_type in {"code_exact", "name_exact"}
    exact_prices = {
        str(candidate.get("unit_price"))
        for candidate in candidates
        if candidate.get("match_type") in {"code_exact", "name_exact"}
        and candidate.get("unit_compatible")
        and candidate.get("unit_price") is not None
    }
    return {
        "row_key": line["row_key"],
        "item_code": line.get("item_code"),
        "item_name": line["item_name"],
        "specification": line.get("specification"),
        "quantity": _decimal_text(quantity),
        "unit": line.get("unit"),
        "selected_source": selected.get("source") if selected else None,
        "source_label": selected.get("source_label") if selected else None,
        "match_type": match_type,
        "unit_price": _decimal_text(unit_price),
        "total_price": _decimal_text(total_price),
        "confidence": selected.get("score") if selected else None,
        "requires_review": bool(
            (selected and (not exact or len(exact_prices) > 1))
            or (selected is None and bool(candidates))
        ),
        "status": "priced" if unit_price is not None else "unpriced",
        "query_plan": query_plan,
        "source_evidence": _source_evidence(source_results),
        "selected_evidence": selected,
        "candidates": candidates,
    }


def _industry_input(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_id": line["row_key"],
        "item_code": line.get("item_code"),
        "item_name": line["item_name"],
        "specification": line.get("specification"),
        "quantity": line.get("quantity"),
        "unit": line.get("unit"),
    }


def _apply_industry_result(result: dict[str, Any], estimate: dict[str, Any]) -> None:
    quantity = _decimal(result.get("quantity"))
    unit_price = _decimal(estimate.get("unit_price"))
    result.update(
        {
            "selected_source": "industry",
            "source_label": "行业数据·AI推算",
            "match_type": "ai_estimate",
            "unit_price": _decimal_text(unit_price),
            "total_price": _decimal_text(quantity * unit_price) if quantity is not None and unit_price is not None else None,
            "confidence": estimate.get("confidence"),
            "requires_review": True,
            "status": "priced" if unit_price is not None else "unpriced",
            "selected_evidence": estimate,
        }
    )


async def create_pricing_agent_run(
    db: Session,
    *,
    current_user: User,
    payload: PricingAgentRunCreateIn,
) -> PricingAgentRun:
    account = resolve_current_account(db, current_user)
    request_data = payload.model_dump(mode="json")
    context = request_data["context"]
    expanded = payload.mode == "expanded"
    run = PricingAgentRun(
        run_uuid=str(uuid4()),
        account_id=account.id,
        mode=payload.mode,
        status=RUN_STATUS_PROCESSING,
        sources_json=_json_dump(payload.sources),
        context_json=_json_dump(context),
        request_json=_json_dump(request_data),
        created_by=current_user.id,
        started_at=app_local_naive(),
    )
    db.add(run)
    db.flush()

    results: list[dict[str, Any]] = []
    for line in request_data["lines"]:
        query_plan = _query_plan(line, context, expanded=expanded)
        source_results: list[RetrievalSourceResult] = []
        if "archive" in payload.sources:
            source_results.append(
                retrieve_archive(
                    db,
                    account_id=int(account.id),
                    query=line,
                    context=context,
                    expanded=expanded,
                )
            )
        if "enterprise" in payload.sources:
            source_results.append(
                retrieve_enterprise(
                    db,
                    query=line,
                    context=context,
                    expanded=expanded,
                )
            )
        selected, candidates = _choose_retrieval_result(source_results)
        results.append(
            _line_result(
                line=line,
                selected=selected,
                candidates=candidates,
                source_results=source_results,
                query_plan=query_plan,
            )
        )

    industry_issue: dict[str, Any] | None = None
    unresolved = [
        result
        for result in results
        if result["unit_price"] is None
        and not result["candidates"]
    ]
    if expanded and "industry" in payload.sources and unresolved:
        industry_rows = [
            _industry_input(next(line for line in request_data["lines"] if line["row_key"] == result["row_key"]))
            for result in unresolved
        ]
        try:
            estimates = await estimate_industry_prices(
                rows=industry_rows,
                context=context,
                current_user=current_user,
            )
        except PricingIndustryEstimateError as exc:
            estimates = {}
            industry_issue = exc.detail
        for result in unresolved:
            estimate = estimates.get(result["row_key"])
            if estimate:
                _apply_industry_result(result, estimate)
            else:
                result["source_evidence"]["industry"] = {
                    "channel_status": {"ai_estimate": "unavailable"},
                    "source_issue": industry_issue
                    or {
                        "code": "PRICING_AGENT_INDUSTRY_PROVIDER_NOT_CONFIGURED",
                        "message": "行业数据需要配置真实 AI 模型后才会返回估价。",
                    },
                    "candidate_count": 0,
                }

    priced_count = sum(result["unit_price"] is not None for result in results)
    review_count = sum(bool(result["requires_review"]) for result in results)
    summary = {
        "row_count": len(results),
        "priced_count": priced_count,
        "unpriced_count": len(results) - priced_count,
        "requires_review_count": review_count,
        "source_counts": {
            source: sum(result["selected_source"] == source for result in results)
            for source in ("archive", "enterprise", "industry")
        },
        "mode": payload.mode,
        "sources": list(payload.sources),
        "retrieval_contract": {
            "exact_mode": "exact_only_no_semantic_no_ai",
            "expanded_mode": (
                "exact_then_keyword_vector_rrf_manual_candidate"
                "_then_industry_ai_if_no_candidate"
            ),
            "vector_channel": (
                "configured"
                if settings.feature_pricing_agent_hybrid_search
                else "disabled_keyword_fallback"
            ),
            "approximate_candidate_policy": "manual_adoption_required",
            "context_policy": "query_and_soft_rerank_not_hard_filter",
        },
    }
    run.status = RUN_STATUS_SUCCEEDED if priced_count == len(results) else RUN_STATUS_PARTIAL
    run.summary_json = _json_dump(summary)
    run.result_json = _json_dump({"summary": summary, "lines": results})
    run.finished_at = app_local_naive()
    for sort_order, result in enumerate(results, start=1):
        db.add(
            PricingAgentRunLine(
                line_uuid=str(uuid4()),
                run_id=run.id,
                row_key=result["row_key"],
                sort_order=sort_order,
                item_code=result["item_code"],
                item_name=result["item_name"],
                specification=result["specification"],
                quantity=_decimal(result["quantity"]),
                unit=result["unit"],
                selected_source=result["selected_source"],
                match_type=result["match_type"],
                unit_price=_decimal(result["unit_price"]),
                total_price=_decimal(result["total_price"]),
                confidence=_decimal(result["confidence"]),
                requires_review=result["requires_review"],
                evidence_json=_json_dump(
                    {
                        "query_plan": result["query_plan"],
                        "source_evidence": result["source_evidence"],
                        "selected_evidence": result["selected_evidence"],
                    }
                ),
                candidates_json=_json_dump(result["candidates"]),
            )
        )
    db.flush()
    db.expire(run, ["lines"])
    _sync_run_result(run)
    db.flush()
    return run


def get_pricing_agent_run(
    db: Session,
    *,
    current_user: User,
    run_uuid: str,
    for_update: bool = False,
) -> PricingAgentRun:
    account = resolve_current_account(db, current_user)
    query = (
        db.query(PricingAgentRun)
        .filter(
            PricingAgentRun.account_id == account.id,
            PricingAgentRun.run_uuid == run_uuid,
        )
    )
    if for_update:
        query = query.with_for_update()
    run = query.one_or_none()
    if run is None:
        raise PricingAgentError("PRICING_AGENT_RUN_NOT_FOUND", status_code=404)
    return run


def select_pricing_agent_candidate(
    db: Session,
    *,
    current_user: User,
    run_uuid: str,
    line_uuid: str,
    payload: PricingAgentCandidateSelectIn,
) -> PricingAgentRun:
    run = get_pricing_agent_run(
        db,
        current_user=current_user,
        run_uuid=run_uuid,
        for_update=True,
    )
    if run.confirmed_quote_job_id:
        raise PricingAgentError(
            "PRICING_AGENT_RUN_ALREADY_CONFIRMED",
            context={"quote_job_id": run.confirmed_quote_job_id},
        )
    line = next(
        (item for item in run.lines if item.line_uuid == line_uuid),
        None,
    )
    if line is None:
        raise PricingAgentError("PRICING_AGENT_RUN_LINE_NOT_FOUND", status_code=404)

    candidates = _json_load(line.candidates_json, [])
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("source") or "") == payload.source
            and str(item.get("source_record_id") or "") == payload.source_record_id
        ),
        None,
    )
    if candidate is None:
        raise PricingAgentError(
            "PRICING_AGENT_CANDIDATE_NOT_FOUND",
            status_code=404,
        )
    unit_price = _positive_decimal(candidate.get("unit_price"))
    if unit_price is None:
        raise PricingAgentError(
            "PRICING_AGENT_CANDIDATE_PRICE_INVALID",
            status_code=422,
        )

    selected_at = app_local_naive()
    revision = int(line.decision_revision or 0) + 1
    evidence = _json_load(line.evidence_json, {})
    if not isinstance(evidence, dict):
        evidence = {}
    history = evidence.get("selection_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "revision": revision,
            "selected_by": current_user.id,
            "selected_at": selected_at.isoformat(),
            "previous_selected_evidence": evidence.get("selected_evidence"),
            "selected_candidate": candidate,
        }
    )
    evidence["selection_history"] = history[-20:]
    evidence["selected_evidence"] = candidate
    evidence["manual_selection"] = {
        "revision": revision,
        "selected_by": current_user.id,
        "selected_at": selected_at.isoformat(),
        "source": payload.source,
        "source_record_id": payload.source_record_id,
    }

    line.selected_source = payload.source
    line.match_type = str(candidate.get("match_type") or "")[:32] or None
    line.unit_price = unit_price
    line.total_price = (
        (line.quantity * unit_price).quantize(_Q6, rounding=ROUND_HALF_UP)
        if line.quantity is not None
        else None
    )
    line.confidence = _decimal(candidate.get("score"))
    line.requires_review = False
    line.evidence_json = _json_dump(evidence)
    line.selected_candidate_json = _json_dump(candidate)
    line.selection_origin = "manual"
    line.manual_selected_by = current_user.id
    line.manual_selected_at = selected_at
    line.decision_revision = revision
    _sync_run_result(run)
    db.flush()
    return run


def set_pricing_agent_manual_price(
    db: Session,
    *,
    current_user: User,
    run_uuid: str,
    line_uuid: str,
    payload: PricingAgentManualPriceIn,
) -> PricingAgentRun:
    run = get_pricing_agent_run(
        db,
        current_user=current_user,
        run_uuid=run_uuid,
        for_update=True,
    )
    if run.confirmed_quote_job_id:
        raise PricingAgentError(
            "PRICING_AGENT_RUN_ALREADY_CONFIRMED",
            context={"quote_job_id": run.confirmed_quote_job_id},
        )
    line = next(
        (item for item in run.lines if item.line_uuid == line_uuid),
        None,
    )
    if line is None:
        raise PricingAgentError("PRICING_AGENT_RUN_LINE_NOT_FOUND", status_code=404)
    if line.unit_price is not None and line.selected_source not in {None, "manual"}:
        raise PricingAgentError(
            "PRICING_AGENT_MANUAL_PRICE_ONLY_FOR_UNPRICED",
            status_code=409,
        )
    unit_price = _positive_decimal(payload.unit_price)
    if unit_price is None:
        raise PricingAgentError(
            "PRICING_AGENT_MANUAL_PRICE_INVALID",
            status_code=422,
        )

    selected_at = app_local_naive()
    revision = int(line.decision_revision or 0) + 1
    reason = payload.reason or "未匹配项目人工补价"
    evidence = _json_load(line.evidence_json, {})
    if not isinstance(evidence, dict):
        evidence = {}
    manual_evidence = {
        "source": "manual",
        "source_label": "人工补价",
        "source_record_id": f"manual:{line.line_uuid}:{revision}",
        "match_type": "manual_price",
        "unit_price": _decimal_text(unit_price),
        "reason": reason,
        "selected_by": current_user.id,
        "selected_at": selected_at.isoformat(),
        "revision": revision,
    }
    history = evidence.get("selection_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "revision": revision,
            "decision_type": "manual_price",
            "selected_by": current_user.id,
            "selected_at": selected_at.isoformat(),
            "previous_selected_evidence": evidence.get("selected_evidence"),
            "manual_price": manual_evidence,
        }
    )
    evidence["selection_history"] = history[-20:]
    evidence["selected_evidence"] = manual_evidence
    evidence["manual_selection"] = {
        "revision": revision,
        "decision_type": "manual_price",
        "selected_by": current_user.id,
        "selected_at": selected_at.isoformat(),
        "reason": reason,
    }

    line.selected_source = "manual"
    line.match_type = "manual_price"
    line.unit_price = unit_price
    line.total_price = (
        (line.quantity * unit_price).quantize(_Q6, rounding=ROUND_HALF_UP)
        if line.quantity is not None
        else None
    )
    line.confidence = None
    line.requires_review = False
    line.evidence_json = _json_dump(evidence)
    line.selected_candidate_json = None
    line.selection_origin = "manual_price"
    line.manual_selected_by = current_user.id
    line.manual_selected_at = selected_at
    line.decision_revision = revision
    _sync_run_result(run)
    db.flush()
    return run


def _quote_row_from_run_line(line: PricingAgentRunLine) -> dict[str, Any]:
    evidence = _json_load(line.evidence_json, {})
    if not isinstance(evidence, dict):
        evidence = {}
    selected = evidence.get("selected_evidence")
    if not isinstance(selected, dict):
        selected = {}
    unit_price = _positive_decimal(line.unit_price)
    total_price = _decimal(line.total_price)
    placeholder = (
        unit_price is None
        or total_price is None
        or total_price <= 0
    )
    source = str(line.selected_source or ("unpriced" if placeholder else ""))
    source_label = (
        selected.get("source_label")
        or _SOURCE_LABELS.get(source)
        or ("待补价" if placeholder else "智能组价")
    )
    pricing_tier = {
        "archive": "archive_data",
        "enterprise": "enterprise_quota",
        "industry": "ai_estimate",
        "manual": "manual_price",
        "unpriced": "unpriced_placeholder",
    }.get(source, "ai_estimate")
    cost_reference: dict[str, Any] = {
        "matched": source == "enterprise",
        "reference_price": _json_number(unit_price),
        "pricing_agent_source": source,
        "pricing_agent_source_label": source_label,
        "source_record_id": selected.get("source_record_id"),
        "message": (
            "智能组价准确模式未命中价格，已保留待补价占位行。"
            if placeholder
            else None
        ),
    }
    if source == "enterprise":
        try:
            enterprise_item_id = int(selected.get("source_record_id"))
        except (TypeError, ValueError):
            enterprise_item_id = None
        if enterprise_item_id:
            cost_reference["enterprise_quota_item_id"] = enterprise_item_id
        cost_reference["source_cost_item"] = {
            "id": enterprise_item_id,
            "item_name": selected.get("item_name"),
            "specification": selected.get("specification"),
            "unit": selected.get("unit"),
            "unit_price": _json_number(unit_price),
            "enterprise_quota_version_id": selected.get("enterprise_quota_version_id"),
        }

    row = {
        "requirement_row_key": line.row_key,
        "source_row_key": line.row_key,
        "project_name": line.item_name,
        "item_name": line.item_name,
        "item_code": line.item_code,
        "spec": line.specification,
        "project_feature": line.specification,
        "quantity": _json_number(line.quantity),
        "unit": line.unit,
        "unit_price": _json_number(unit_price) or 0,
        "manual_unit_price": _json_number(unit_price) or 0,
        "confirmed_unit_price": _json_number(unit_price) or 0,
        "total_price": _json_number(total_price) or 0,
        "confirmed_total_price": _json_number(total_price) or 0,
        "pricing_tier": pricing_tier,
        "price_source": source,
        "pricing_source_snapshot": selected,
        "cost_reference": cost_reference,
        "manual_price_source": (
            "pricing_agent_unpriced_placeholder"
            if placeholder
            else "pricing_agent_confirmed"
        ),
        "manual_price_action": "untouched" if placeholder else "manual_existing",
        "final_price_source": "unpriced" if placeholder else "manual",
        "price_confirmed_by_user": not placeholder,
        "price_confirmation_label": (
            "待补价"
            if placeholder
            else "智能组价 Agent 人工确认"
        ),
        "requirement_placeholder": placeholder,
        "pricing_agent_unpriced_placeholder": placeholder,
        "needs_manual_pricing": placeholder,
        "quote_source": (
            "pricing_agent_unpriced_placeholder"
            if placeholder
            else "pricing_agent_confirmed"
        ),
        "quote_explanation": {
            "ai_price_source": f"pricing_agent_{source or 'unknown'}",
            "ai_price_source_label": source_label,
            "ai_price_source_reason": (
                "智能组价准确模式未命中有效价格，当前行作为待补价占位写入报价草稿。"
                if placeholder
                else (
                    selected.get("reason")
                    or "由智能组价 Agent 检索候选并经人工整单确认后写入报价草稿。"
                )
            ),
        },
        "pricing_agent": {
            "line_uuid": line.line_uuid,
            "selected_source": source,
            "source_label": source_label,
            "match_type": line.match_type,
            "confidence": _decimal_text(line.confidence),
            "requires_review_before_confirmation": bool(line.requires_review),
            "selection_origin": line.selection_origin or "automatic",
            "decision_revision": int(line.decision_revision or 0),
            "unpriced_placeholder": placeholder,
        },
    }
    if source == "industry":
        row["ai_estimate"] = selected
        row["ai_suggested_unit_price"] = _json_number(unit_price)
    return row


def _build_confirmed_quote_payload(
    run: PricingAgentRun,
    *,
    quote_job_id: str,
    trace_id: str,
) -> dict[str, Any]:
    rows = [_quote_row_from_run_line(line) for line in run.lines]
    placeholder_count = sum(
        bool(row.get("pricing_agent_unpriced_placeholder"))
        for row in rows
    )
    total = sum(
        (_decimal(row.get("total_price")) or Decimal("0"))
        for row in rows
    ).quantize(_Q6, rounding=ROUND_HALF_UP)
    context = _json_load(run.context_json, {})
    return {
        "quote_id": f"PA-{run.run_uuid}",
        "quote_job_id": quote_job_id,
        "trace_id": trace_id,
        "source": "pricing_agent",
        "project_details": rows,
        "total_price": float(total),
        "total_amount": float(total),
        "customer_questions_answered": (
            f"智能组价条件：地区 {context.get('city') or '-'}；"
            f"行业/业态 {context.get('project_type') or '-'}；"
            f"装修程度 {context.get('decoration_level') or '-'}。"
        ),
        "pricing_agent_confirmation": {
            "run_uuid": run.run_uuid,
            "mode": run.mode,
            "sources": _json_load(run.sources_json, []),
            "context": context,
            "row_count": len(rows),
            "priced_row_count": len(rows) - placeholder_count,
            "unpriced_placeholder_count": placeholder_count,
            "confirmed_result_hash_scope": "project_details",
        },
    }


def _existing_confirmation(
    db: Session,
    run: PricingAgentRun,
) -> dict[str, Any] | None:
    if not run.confirmed_quote_job_id:
        return None
    job = db.query(QuoteJob).filter(
        QuoteJob.job_id == run.confirmed_quote_job_id
    ).one_or_none()
    draft = None
    if run.confirmed_preview_draft_id:
        draft = db.query(QuotePreviewDraft).filter(
            QuotePreviewDraft.id == run.confirmed_preview_draft_id
        ).one_or_none()
    if job is None or draft is None:
        raise PricingAgentError(
            "PRICING_AGENT_CONFIRMATION_TARGET_MISSING",
            context={
                "quote_job_id": run.confirmed_quote_job_id,
                "preview_draft_id": run.confirmed_preview_draft_id,
            },
        )
    return _confirmation_data(run)


def confirm_pricing_agent_run_to_quote_draft(
    db: Session,
    *,
    current_user: User,
    run_uuid: str,
) -> dict[str, Any]:
    run = get_pricing_agent_run(
        db,
        current_user=current_user,
        run_uuid=run_uuid,
        for_update=True,
    )
    existing = _existing_confirmation(db, run)
    if existing is not None:
        return existing

    job_id = str(uuid4())
    trace_id = f"pricing-agent-{run.run_uuid}"
    quote_payload = _build_confirmed_quote_payload(
        run,
        quote_job_id=job_id,
        trace_id=trace_id,
    )
    request_items = "；".join(
        line.item_name
        for line in run.lines[:5]
    )
    unpriced_count = sum(
        _positive_decimal(line.unit_price) is None
        or _positive_decimal(line.total_price) is None
        for line in run.lines
    )
    message = (
        f"智能组价 Agent 已确认 {len(run.lines)} 项"
        f"，其中待补价 {unpriced_count} 项"
        f"{f'：{request_items}' if request_items else ''}"
    )
    job = QuoteJob(
        job_id=job_id,
        username=current_user.username,
        status="succeeded",
        stage="completed",
        message=message,
        file_name=f"智能组价Agent-{run.run_uuid[:8]}.xlsx",
        file_mime_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        result_json=_json_dump(quote_payload),
        trace_id=trace_id,
        duration_ms=0,
        finished_at=app_local_naive(),
    )
    apply_job_request_summary(job)
    apply_job_result_summary(job, quote_payload)
    append_job_event(
        job,
        "success",
        "智能组价 Agent 结果已人工确认并写入报价草稿",
        trace_id=trace_id,
        stage="completed",
        source="pricing_agent",
        pricing_agent_run_uuid=run.run_uuid,
    )
    db.add(job)
    db.flush()

    draft = save_preview_draft(
        db,
        job=job,
        user=current_user,
        draft=quote_payload,
        quote_id=quote_payload["quote_id"],
        trace_id=trace_id,
    )
    confirmed_at = app_local_naive()
    canonical = json.dumps(
        quote_payload["project_details"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    confirmation_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    confirmation = {
        "confirmed": True,
        "quote_job_id": job_id,
        "preview_draft_id": draft["id"],
        "confirmed_by": current_user.id,
        "confirmed_at": confirmed_at.isoformat(),
        "confirmation_hash": confirmation_hash,
        "row_count": draft["row_count"],
        "priced_row_count": draft["priced_row_count"],
        "unpriced_row_count": draft["unpriced_row_count"],
        "contains_unpriced_placeholders": draft["unpriced_row_count"] > 0,
        "draft_status": draft["status"],
        "draft_url": f"/index.html?quote_job_id={job_id}&from=pricing_agent",
    }
    run.confirmed_quote_job_id = job_id
    run.confirmed_preview_draft_id = int(draft["id"])
    run.confirmed_by = current_user.id
    run.confirmed_at = confirmed_at
    run.confirmation_hash = confirmation_hash
    run.confirmation_json = _json_dump(confirmation)
    db.flush()
    return confirmation


def list_pricing_agent_runs(
    db: Session,
    *,
    current_user: User,
    page: int,
    page_size: int,
) -> tuple[list[PricingAgentRun], int]:
    account = resolve_current_account(db, current_user)
    query = db.query(PricingAgentRun).filter(PricingAgentRun.account_id == account.id)
    total = query.count()
    rows = (
        query.order_by(PricingAgentRun.created_at.desc(), PricingAgentRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def serialize_pricing_agent_run(run: PricingAgentRun, *, include_result: bool = True) -> dict[str, Any]:
    if include_result:
        lines = [_serialize_run_line(line) for line in run.lines]
        summary = _summary_from_lines(run, lines)
    else:
        lines = []
        summary = _json_load(run.summary_json, {})
        if not isinstance(summary, dict):
            summary = {}
    payload = {
        "run_uuid": run.run_uuid,
        "mode": run.mode,
        "status": run.status,
        "sources": _json_load(run.sources_json, []),
        "context": _json_load(run.context_json, {}),
        "summary": summary,
        "confirmation": _confirmation_data(run),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    if include_result:
        payload["result"] = {"summary": summary, "lines": lines}
        payload["error"] = _json_load(run.error_json, None)
    return payload
