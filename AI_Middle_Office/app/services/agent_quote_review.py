from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.agent import AgentFinding, AgentRun, AgentSuggestion, AgentSuggestionEvent, AgentToolCall
from app.models.quote_job import QuoteJob
from app.services.agent_market_price_search import (
    MARKET_SEARCH_TOOL_NAME,
    build_market_search_explanation,
    market_search_context_for_record,
    query_market_price_web_search,
)
from app.services.quote_history import parse_amount
from app.services.quote_job_numbers import quote_job_number
from app.services.quote_review import build_quote_review_detail


QUOTE_REVIEW_AGENT_TYPE = "quote_review_assistant"
QUOTE_REVIEW_TARGET_TYPE = "quote_job"
AGENT_ENGINE = "rule_graph_v1"
MAX_ROW_FINDINGS = 120
MAX_ROW_SUGGESTIONS = 120
PRICE_ADJUSTMENT_THRESHOLD = 0.25


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(raw_value: str | None, fallback: Any = None) -> Any:
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except Exception:
        return fallback


def create_quote_review_agent_run(
    db: Session,
    *,
    job: QuoteJob,
    created_by: str,
    trace_id: str | None = None,
    trigger_source: str = "manual",
    trigger_ref_type: str | None = None,
    trigger_ref_id: str | None = None,
    audit_only: bool = True,
    audit_date: date | datetime | str | None = None,
) -> AgentRun:
    audit_only = True
    audit_day = _coerce_audit_date(audit_date)
    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_type=QUOTE_REVIEW_AGENT_TYPE,
        target_type=QUOTE_REVIEW_TARGET_TYPE,
        target_id=job.job_id,
        trigger_source=trigger_source,
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=trigger_ref_id,
        status="running",
        created_by=created_by,
        trace_id=trace_id or job.trace_id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.perf_counter()
    try:
        job_context = _run_tool(
            db,
            run.run_id,
            "get_quote_job_context",
            {"quote_job_id": job.job_id},
            lambda: _quote_job_context(job),
        )
        review_detail = _run_tool(
            db,
            run.run_id,
            "get_quote_review_detail",
            {"quote_job_id": job.job_id},
            lambda: build_quote_review_detail(db, job),
        )
        finding_payloads = _run_tool(
            db,
            run.run_id,
            "derive_quote_review_findings",
            {"quote_job_id": job.job_id},
            lambda: derive_quote_review_findings(job_context, review_detail),
        )
        audit_records = _run_tool(
            db,
            run.run_id,
            "build_confirmed_quote_audit_records",
            {"quote_job_id": job.job_id, "audit_only": audit_only},
            lambda: build_confirmed_quote_audit_records(job_context, review_detail),
        )
        market_search_context = _run_tool(
            db,
            run.run_id,
            "market_price_web_search",
            {
                "quote_job_id": job.job_id,
                "audit_date": audit_day.isoformat() if audit_day else None,
                "tool": MARKET_SEARCH_TOOL_NAME,
                "audit_record_count": len(audit_records),
            },
            lambda: query_market_price_web_search(
                audit_records,
                audit_date=audit_day,
                username=created_by,
                trace_id=trace_id or job.trace_id,
            ),
        )
        audit_records = _attach_market_search_context(audit_records, market_search_context)
        suggestion_payloads = []
        report = _run_tool(
            db,
            run.run_id,
            "generate_quote_review_report",
            {
                "quote_job_id": job.job_id,
                "finding_count": len(finding_payloads),
                "suggestion_count": len(suggestion_payloads),
                "audit_record_count": len(audit_records),
                "audit_only": audit_only,
                "market_search_result_count": (market_search_context.get("summary") or {}).get("result_count")
                if isinstance(market_search_context, dict)
                else 0,
            },
            lambda: build_quote_review_report(
                job_context,
                review_detail,
                finding_payloads,
                suggestion_payloads,
                audit_records=audit_records,
                audit_only=audit_only,
                market_search_context=market_search_context,
                trigger_source=trigger_source,
                trigger_ref_type=trigger_ref_type,
                trigger_ref_id=trigger_ref_id,
            ),
        )

        for payload in finding_payloads:
            db.add(
                AgentFinding(
                    run_id=run.run_id,
                    finding_type=payload["type"],
                    severity=payload["severity"],
                    target_ref=payload.get("target_ref"),
                    title=payload["title"][:255],
                    evidence_json=json_dumps(payload.get("evidence") or {}),
                    suggestion=payload.get("suggestion"),
                )
            )
        if suggestion_payloads:
            _persist_agent_suggestions(db, run, suggestion_payloads, created_by=created_by)

        run.status = "completed"
        run.risk_level = report["risk_level"]
        run.recommendation = report["recommendation"]
        run.summary = report["summary"]
        run.output_json = json_dumps(report)
        run.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        raise


def derive_quote_review_findings(job_context: dict[str, Any], review_detail: dict[str, Any]) -> list[dict[str, Any]]:
    job_id = job_context["job_id"]
    summary = review_detail.get("summary") or {}
    findings: list[dict[str, Any]] = []

    if job_context.get("status") not in {"succeeded", "completed"}:
        findings.append(
            _finding(
                "quote_job_not_completed",
                "high",
                f"quote_job:{job_id}",
                "报价任务尚未完成",
                {"status": job_context.get("status"), "stage": job_context.get("stage")},
                "请等待任务完成后再进行 Agent 复核。",
            )
        )

    if summary.get("missing_count", 0) > 0:
        findings.append(
            _finding(
                "missing_requirement_rows",
                "high",
                f"quote_job:{job_id}",
                "确认清单存在疑似未报价行",
                {"missing_count": summary.get("missing_count"), "matched_count": summary.get("matched_count")},
                "请逐行核对缺失行，必要时补价或打回重算。",
            )
        )
    if summary.get("placeholder_count", 0) > 0:
        findings.append(
            _finding(
                "requirement_placeholders",
                "high",
                f"quote_job:{job_id}",
                "存在 AI 未返回的占位报价行",
                {"placeholder_count": summary.get("placeholder_count")},
                "占位行必须人工补充有效单价和合计后才能下发。",
            )
        )
    if summary.get("extra_count", 0) > 0:
        findings.append(
            _finding(
                "extra_preview_rows",
                "medium",
                f"quote_job:{job_id}",
                "预审中存在未匹配确认清单的额外行",
                {"extra_count": summary.get("extra_count")},
                "请确认额外行是否为合理拆项，避免重复报价。",
            )
        )
    if summary.get("no_cost_reference_count", 0) > 0:
        findings.append(
            _finding(
                "no_cost_reference",
                "medium",
                f"quote_job:{job_id}",
                "存在无成本库底价参考的报价行",
                {"no_cost_reference_count": summary.get("no_cost_reference_count")},
                "请人工确认价格；确认下发后可按规则沉淀为 draft 待审核。",
            )
        )
    if summary.get("cost_fallback_count", 0) > 0:
        findings.append(
            _finding(
                "cost_fallback_used",
                "medium",
                f"quote_job:{job_id}",
                "存在成本库底价兜底行",
                {"cost_fallback_count": summary.get("cost_fallback_count")},
                "请确认兜底价格是否符合本单施工范围。",
            )
        )
    if summary.get("ai_rewrite_risk_count", 0) > 0:
        findings.append(
            _finding(
                "ai_rewrite_risk",
                "high",
                f"quote_job:{job_id}",
                "存在 AI 改写导致的成本依据不一致风险",
                {"ai_rewrite_risk_count": summary.get("ai_rewrite_risk_count")},
                "请确认当前采用的成本条目是否与原始需求一致。",
            )
        )
    if summary.get("ai_note_conflict_count", 0) > 0:
        findings.append(
            _finding(
                "ai_note_conflict",
                "high",
                f"quote_job:{job_id}",
                "存在 AI 备注与成本依据冲突",
                {"ai_note_conflict_count": summary.get("ai_note_conflict_count")},
                "请确认备注处理，避免对客户展示与成本依据冲突的说明。",
            )
        )

    findings.extend(_row_level_findings(job_id, review_detail))

    if not findings:
        findings.append(
            _finding(
                "no_blocking_risk",
                "low",
                f"quote_job:{job_id}",
                "未发现明显阻断风险",
                {"review_required_count": summary.get("review_required_count", 0)},
                "建议按常规抽查后下发。",
            )
        )
    return findings


def derive_quote_review_suggestions(job_context: dict[str, Any], review_detail: dict[str, Any]) -> list[dict[str, Any]]:
    job_id = job_context["job_id"]
    suggestions: list[dict[str, Any]] = []

    for row in review_detail.get("preview_rows") or []:
        if not isinstance(row, dict):
            continue
        suggestions.extend(_price_suggestions_for_row(job_id, row))
        suggestions.extend(_risk_suggestions_for_row(job_id, row))
        if len(suggestions) >= MAX_ROW_SUGGESTIONS:
            return suggestions[:MAX_ROW_SUGGESTIONS]

    for item in review_detail.get("missing_requirement_rows") or []:
        row = item.get("requirement_row") or {}
        row_label = _row_label(row)
        suggestions.append(
            _suggestion(
                "risk_mitigation",
                "high",
                _row_ref(job_id, row, fallback=f"requirement:{item.get('requirement_index')}"),
                None,
                f"补齐疑似未报价行：{_row_display_name(row)}",
                "确认清单存在未匹配预审报价的行，直接下发会造成漏报风险。",
                "建议先补价或打回重算；若业务确认不报价，应在备注中记录原因。",
                {
                    "target_label": row_label,
                    "requirement_row": row,
                    "match_status": item.get("status"),
                    "score": item.get("score"),
                },
                {"action": "manual_complete_or_recalculate", "requires_quote_line_patch": True},
                None,
                None,
                0.9,
            )
        )
        if len(suggestions) >= MAX_ROW_SUGGESTIONS:
            break

    if not suggestions:
        suggestions.append(
            _suggestion(
                "risk_mitigation",
                "low",
                f"quote_job:{job_id}",
                None,
                "常规抽查后再下发",
                "本次 Agent 未发现明确阻断风险。",
                "建议按试运行规则抽查关键金额行和无成本证据行。",
                {"summary": review_detail.get("summary") or {}},
                {"action": "spot_check_before_push"},
                None,
                None,
                0.75,
            )
        )
    return suggestions[:MAX_ROW_SUGGESTIONS]


def build_quote_review_report(
    job_context: dict[str, Any],
    review_detail: dict[str, Any],
    findings: list[dict[str, Any]],
    suggestions: list[dict[str, Any]] | None = None,
    *,
    audit_records: list[dict[str, Any]] | None = None,
    audit_only: bool = False,
    market_search_context: dict[str, Any] | None = None,
    trigger_source: str | None = None,
    trigger_ref_type: str | None = None,
    trigger_ref_id: str | None = None,
) -> dict[str, Any]:
    summary = review_detail.get("summary") or {}
    suggestions = suggestions or []
    audit_records = audit_records or []
    severity_counts = _severity_counts(findings)
    saving_summary = _saving_summary(suggestions)
    audit_summary = _audit_summary(audit_records, review_detail)
    risk_level = _risk_level(severity_counts, job_context, summary)
    recommendation = _recommendation(risk_level, summary)
    next_actions = _next_actions(findings, recommendation)
    if audit_only:
        recommendation = "post_audit_recorded"
        next_actions = _audit_next_actions(audit_summary)
    headline = (
        _audit_summary_text(job_context, audit_summary, risk_level)
        if audit_only
        else _summary_text(job_context, summary, risk_level, recommendation)
    )
    market_search_context = market_search_context or {}

    report = {
        "agent_type": QUOTE_REVIEW_AGENT_TYPE,
        "agent_engine": AGENT_ENGINE,
        "audit_mode": "confirmed_quote_risk_audit" if audit_only else "interactive_review",
        "llm_mode": "disabled_by_default",
        "target_type": QUOTE_REVIEW_TARGET_TYPE,
        "target_id": job_context["job_id"],
        "target_number": job_context.get("job_number"),
        "trigger_source": trigger_source,
        "trigger_ref_type": trigger_ref_type,
        "trigger_ref_id": trigger_ref_id,
        "risk_level": risk_level,
        "recommendation": recommendation,
        "summary": headline,
        "metrics": {
            "requirement_row_count": summary.get("requirement_row_count", 0),
            "preview_row_count": summary.get("preview_row_count", 0),
            "matched_count": summary.get("matched_count", 0),
            "missing_count": summary.get("missing_count", 0),
            "extra_count": summary.get("extra_count", 0),
            "placeholder_count": summary.get("placeholder_count", 0),
            "no_cost_reference_count": summary.get("no_cost_reference_count", 0),
            "high_risk_count": summary.get("high_risk_count", 0),
            "review_required_count": summary.get("review_required_count", 0),
        },
        "severity_counts": severity_counts,
        "findings": findings,
        "audit_records": audit_records,
        "audit_summary": audit_summary,
        "market_search_context": market_search_context,
        "market_search_summary": market_search_context.get("summary") if isinstance(market_search_context, dict) else {},
        "knowledge_sources": {
            "rag": "not_used",
            "memory": "not_used",
            "market_search_tool": MARKET_SEARCH_TOOL_NAME,
        },
        "suggestions": suggestions,
        "saving_summary": saving_summary,
        "next_actions": next_actions,
        "review_detail_summary": summary,
    }
    report["markdown"] = _markdown_report(report, job_context)
    return report


def build_confirmed_quote_audit_records(
    job_context: dict[str, Any],
    review_detail: dict[str, Any],
) -> list[dict[str, Any]]:
    job_id = job_context["job_id"]
    records: list[dict[str, Any]] = []

    for row in review_detail.get("preview_rows") or []:
        if not isinstance(row, dict):
            continue
        failed_checks = _failed_checks(row)
        risk = row.get("risk") if isinstance(row.get("risk"), dict) else {}
        manual_modified = bool(row.get("manual_modified"))
        if risk.get("type") not in {"danger", "warning"} and not failed_checks and not manual_modified:
            continue

        reference = row.get("cost_reference") if isinstance(row.get("cost_reference"), dict) else {}
        before_quantity = _round_money(row.get("quantity"))
        after_quantity = _round_money(row.get("final_quantity"))
        if after_quantity is None:
            after_quantity = before_quantity
        before_unit = _round_money(row.get("ai_unit_price"))
        before_total = _round_money(row.get("system_total_price"))
        after_unit = _round_money(row.get("final_unit_price"))
        after_total = _round_money(row.get("final_total_price"))
        if after_unit is None:
            after_unit = _round_money(row.get("display_unit_price"))
        if after_total is None:
            after_total = _round_money(row.get("display_total_price"))

        risk_reasons = _audit_risk_reasons(failed_checks, risk)
        records.append(
            {
                "record_type": "confirmed_quote_line_audit",
                "target_ref": _row_ref(job_id, row, fallback=f"preview:{row.get('line_no') or row.get('index')}"),
                "target_label": _row_label(row),
                "line_no": row.get("line_no"),
                "project_name": row.get("project_name"),
                "quantity": row.get("quantity"),
                "unit": row.get("unit"),
                "risk_level": "high" if risk.get("type") == "danger" else ("medium" if risk.get("type") == "warning" else "low"),
                "risk_reasons": risk_reasons,
                "original_preview": {
                    "quantity": before_quantity,
                    "unit_price": before_unit,
                    "total_price": before_total,
                    "notes": row.get("notes"),
                    "risk": risk,
                    "failed_checks": failed_checks,
                    "cost_reference": _compact_cost_reference(reference),
                },
                "confirmed_quote": {
                    "quantity": after_quantity,
                    "unit_price": after_unit,
                    "total_price": after_total,
                    "price_source": row.get("display_price_source"),
                    "manual_modified": manual_modified,
                },
                "price_change": _audit_price_change(
                    before_unit,
                    before_total,
                    after_unit,
                    after_total,
                    before_quantity,
                    after_quantity,
                ),
                "before_after_summary": _audit_before_after_summary(
                    row,
                    risk_reasons=risk_reasons,
                    before_quantity=before_quantity,
                    after_quantity=after_quantity,
                    before_unit=before_unit,
                    before_total=before_total,
                    after_unit=after_unit,
                    after_total=after_total,
                    manual_modified=manual_modified,
                ),
            }
        )
        if len(records) >= MAX_ROW_FINDINGS:
            return records

    for item in review_detail.get("missing_requirement_rows") or []:
        row = item.get("requirement_row") or {}
        records.append(
            {
                "record_type": "missing_requirement_row_audit",
                "target_ref": _row_ref(job_id, row, fallback=f"requirement:{item.get('requirement_index')}"),
                "target_label": _row_label(row),
                "line_no": row.get("line_no") or row.get("raw_row_index"),
                "project_name": _row_display_name(row),
                "quantity": row.get("quantity"),
                "unit": row.get("unit"),
                "risk_level": "high",
                "risk_reasons": [
                    {
                        "type": "missing_requirement_row",
                        "label": "确认清单行未匹配到预审报价",
                        "severity": "high",
                    }
                ],
                "original_preview": {
                    "unit_price": None,
                    "total_price": None,
                    "notes": None,
                    "risk": {"type": "danger", "label": "疑似漏报价"},
                    "failed_checks": [],
                    "cost_reference": {},
                },
                "confirmed_quote": {
                    "unit_price": None,
                    "total_price": None,
                    "price_source": "not_found_in_preview",
                    "manual_modified": False,
                },
                "price_change": {},
                "before_after_summary": "原预审风险：确认清单行未进入预审报价；下发记录中未找到对应报价行。",
            }
        )
        if len(records) >= MAX_ROW_FINDINGS:
            break
    return records


def _audit_summary(audit_records: list[dict[str, Any]], review_detail: dict[str, Any]) -> dict[str, Any]:
    risk_type_counts: dict[str, int] = {}
    manual_modified_count = 0
    market_search_result_count = 0
    market_search_covered_line_count = 0
    for record in audit_records:
        confirmed = record.get("confirmed_quote") if isinstance(record.get("confirmed_quote"), dict) else {}
        if confirmed.get("manual_modified"):
            manual_modified_count += 1
        market_context = record.get("market_search_context") if isinstance(record.get("market_search_context"), dict) else {}
        source_count = len(market_context.get("sources") or []) if isinstance(market_context.get("sources"), list) else 0
        market_search_result_count += source_count
        if source_count:
            market_search_covered_line_count += 1
        for reason in record.get("risk_reasons") or []:
            if not isinstance(reason, dict):
                continue
            key = reason.get("type") or "unknown"
            risk_type_counts[key] = risk_type_counts.get(key, 0) + 1
    summary = review_detail.get("summary") if isinstance(review_detail.get("summary"), dict) else {}
    return {
        "audit_record_count": len(audit_records),
        "manual_modified_count": manual_modified_count,
        "risk_type_counts": risk_type_counts,
        "confirmed_line_count": summary.get("preview_row_count", 0),
        "high_risk_count": sum(1 for item in audit_records if item.get("risk_level") == "high"),
        "medium_risk_count": sum(1 for item in audit_records if item.get("risk_level") == "medium"),
        "market_search_result_count": market_search_result_count,
        "market_search_covered_line_count": market_search_covered_line_count,
    }


def _audit_risk_reasons(failed_checks: list[dict[str, Any]], risk: dict[str, Any]) -> list[dict[str, Any]]:
    risk_type_by_check = {
        "has_cost_reference": "no_cost_reference",
        "cost_candidate_confirmed": "multiple_cost_candidates",
        "cost_delta_in_range": "cost_price_delta",
        "manual_quantity_change_not_large": "manual_quantity_deviation",
        "manual_unit_price_change_not_large": "manual_unit_price_deviation",
        "manual_change_not_large": "manual_price_deviation",
        "ai_rewrite_confirmed": "ai_rewrite_conflict",
        "ai_note_confirmed": "ai_note_conflict",
        "unit_price_positive": "invalid_unit_price",
        "total_price_positive": "invalid_total_price",
        "matched_requirement_row": "requirement_match_risk",
        "cost_fallback_not_used": "cost_fallback_used",
        "notes_present": "missing_note",
    }
    result: list[dict[str, Any]] = []
    for item in failed_checks:
        key = item.get("key")
        result.append(
            {
                "type": risk_type_by_check.get(key, key or "unknown"),
                "check_key": key,
                "label": item.get("label") or key or "预审风险",
                "severity": "high" if item.get("severity") == "danger" else "medium",
            }
        )
    if not result and risk:
        for reason in risk.get("reasons") or []:
            result.append({"type": "preview_risk", "label": str(reason), "severity": "medium"})
    return result


def _compact_cost_reference(reference: dict[str, Any]) -> dict[str, Any]:
    alternatives = reference.get("alternative_cost_items") or []
    candidate_count = reference.get("candidate_count")
    if candidate_count is None:
        candidate_count = len(alternatives) + (1 if reference.get("matched") else 0)
    return {
        "matched": bool(reference.get("matched")),
        "match_type": reference.get("match_type"),
        "cost_item_id": reference.get("cost_item_id"),
        "item_name": reference.get("item_name"),
        "spec": reference.get("spec"),
        "unit": reference.get("unit"),
        "reference_price": _round_money(reference.get("reference_price")),
        "price_delta": _round_money(reference.get("price_delta")),
        "price_delta_rate": reference.get("price_delta_rate"),
        "candidate_count": candidate_count,
        "requires_manual_cost_candidate_confirmation": bool(
            reference.get("requires_manual_cost_candidate_confirmation")
        ),
        "manual_cost_candidate_confirmed": bool(reference.get("manual_cost_candidate_confirmed")),
    }


def _audit_price_change(
    before_unit: float | None,
    before_total: float | None,
    after_unit: float | None,
    after_total: float | None,
    before_quantity: float | None = None,
    after_quantity: float | None = None,
) -> dict[str, Any]:
    quantity_delta = _money_delta(before_quantity, after_quantity)
    unit_delta = _money_delta(before_unit, after_unit)
    total_delta = _money_delta(before_total, after_total)
    return {
        "quantity_before": before_quantity,
        "quantity_after": after_quantity,
        "quantity_delta": quantity_delta,
        "quantity_delta_rate": _delta_rate(before_quantity, quantity_delta),
        "unit_price_before": before_unit,
        "unit_price_after": after_unit,
        "unit_price_delta": unit_delta,
        "unit_price_delta_rate": _delta_rate(before_unit, unit_delta),
        "total_price_before": before_total,
        "total_price_after": after_total,
        "total_price_delta": total_delta,
        "total_price_delta_rate": _delta_rate(before_total, total_delta),
    }


def _audit_before_after_summary(
    row: dict[str, Any],
    *,
    risk_reasons: list[dict[str, Any]],
    before_quantity: float | None,
    after_quantity: float | None,
    before_unit: float | None,
    before_total: float | None,
    after_unit: float | None,
    after_total: float | None,
    manual_modified: bool,
) -> str:
    reason_text = "；".join(str(item.get("label") or item.get("type")) for item in risk_reasons[:3]) or "未发现明确预审风险"
    action_text = "人工已修改价格" if manual_modified else "下发价沿用预审价或未记录人工改价"
    return (
        f"原预审风险：{reason_text}；"
        f"预审：工程量 {_money_text(before_quantity)}，单价 {_money_text(before_unit)} 元，合计 {_money_text(before_total)} 元；"
        f"确认下发：工程量 {_money_text(after_quantity)}，单价 {_money_text(after_unit)} 元，合计 {_money_text(after_total)} 元；"
        f"{action_text}。"
    )


def _attach_market_search_context(
    audit_records: list[dict[str, Any]],
    market_search_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if not audit_records:
        return audit_records
    for record in audit_records:
        item_context = market_search_context_for_record(market_search_context, record.get("target_ref"))
        record["market_search_context"] = item_context
        explanation = build_market_search_explanation(item_context)
        record["market_search_explanation"] = explanation
        record["before_after_summary"] = f"{record.get('before_after_summary') or ''}{explanation}"
    return audit_records


def _coerce_audit_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _money_delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return _round_money(float(after) - float(before))


def _delta_rate(before: float | None, delta: float | None) -> float | None:
    if before in (None, 0) or delta is None:
        return None
    return round(float(delta) / float(before), 4)


def serialize_agent_run(run: AgentRun, *, include_output: bool = True) -> dict[str, Any]:
    output = json_loads(run.output_json, {}) if run.output_json else {}
    data = {
        "id": run.id,
        "run_id": run.run_id,
        "agent_type": run.agent_type,
        "target_type": run.target_type,
        "target_id": run.target_id,
        "target_number": output.get("target_number") if isinstance(output, dict) else None,
        "trigger_source": run.trigger_source,
        "trigger_ref_type": run.trigger_ref_type,
        "trigger_ref_id": run.trigger_ref_id,
        "status": run.status,
        "risk_level": run.risk_level,
        "recommendation": run.recommendation,
        "summary": run.summary,
        "created_by": run.created_by,
        "trace_id": run.trace_id,
        "duration_ms": run.duration_ms,
        "error_message": run.error_message,
        "created_at": _format_dt(run.created_at),
        "started_at": _format_dt(run.started_at),
        "finished_at": _format_dt(run.finished_at),
    }
    if include_output:
        data["output"] = output or None
    return data


def serialize_agent_tool_call(row: AgentToolCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "tool_name": row.tool_name,
        "input": json_loads(row.input_json, {}),
        "output_summary": row.output_summary,
        "output": json_loads(row.output_json, None),
        "status": row.status,
        "duration_ms": row.duration_ms,
        "created_at": _format_dt(row.created_at),
    }


def serialize_agent_finding(row: AgentFinding) -> dict[str, Any]:
    evidence = json_loads(row.evidence_json, {})
    return {
        "id": row.id,
        "run_id": row.run_id,
        "finding_type": row.finding_type,
        "severity": row.severity,
        "target_ref": row.target_ref,
        "target_label": _target_label_from_evidence(evidence, row.target_ref),
        "title": row.title,
        "evidence": evidence,
        "suggestion": row.suggestion,
        "created_at": _format_dt(row.created_at),
    }


def serialize_agent_suggestion(row: AgentSuggestion) -> dict[str, Any]:
    current_snapshot = json_loads(row.current_snapshot_json, {})
    return {
        "id": row.id,
        "suggestion_id": row.suggestion_id,
        "run_id": row.run_id,
        "agent_type": row.agent_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "suggestion_type": row.suggestion_type,
        "status": row.status,
        "priority": row.priority,
        "target_ref": row.target_ref,
        "target_line_no": row.target_line_no,
        "target_label": _target_label_from_snapshot(current_snapshot, row.target_ref),
        "title": row.title,
        "rationale": row.rationale,
        "risk_note": row.risk_note,
        "current_snapshot": current_snapshot,
        "proposed_snapshot": json_loads(row.proposed_snapshot_json, {}),
        "execution_result": json_loads(row.execution_result_json, None),
        "final_result": json_loads(row.final_result_json, None),
        "estimated_saving_amount": row.estimated_saving_amount,
        "estimated_saving_rate": row.estimated_saving_rate,
        "confidence": row.confidence,
        "requires_approval": row.requires_approval,
        "created_by": row.created_by,
        "decided_by": row.decided_by,
        "decision_note": row.decision_note,
        "executed_by": row.executed_by,
        "final_confirmed_by": row.final_confirmed_by,
        "final_note": row.final_note,
        "created_at": _format_dt(row.created_at),
        "updated_at": _format_dt(row.updated_at),
        "decided_at": _format_dt(row.decided_at),
        "executed_at": _format_dt(row.executed_at),
        "final_confirmed_at": _format_dt(row.final_confirmed_at),
    }


def serialize_agent_suggestion_event(row: AgentSuggestionEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "suggestion_id": row.suggestion_id,
        "run_id": row.run_id,
        "event_type": row.event_type,
        "actor": row.actor,
        "note": row.note,
        "payload": json_loads(row.payload_json, {}),
        "created_at": _format_dt(row.created_at),
    }


def decide_agent_suggestion(
    db: Session,
    suggestion: AgentSuggestion,
    *,
    decision: str,
    actor: str,
    note: str | None = None,
) -> AgentSuggestion:
    if suggestion.status not in {"pending_review", "approved"}:
        raise ValueError("SUGGESTION_STATUS_NOT_DECIDABLE")
    if decision == "approve":
        suggestion.status = "approved"
        event_type = "approved"
    elif decision == "reject":
        suggestion.status = "rejected"
        event_type = "rejected"
    else:
        raise ValueError("INVALID_SUGGESTION_DECISION")
    suggestion.decided_by = actor
    suggestion.decision_note = note
    suggestion.decided_at = _utcnow()
    _add_suggestion_event(
        db,
        suggestion,
        event_type,
        actor=actor,
        note=note,
        payload={"decision": decision, "status": suggestion.status},
    )
    db.commit()
    db.refresh(suggestion)
    return suggestion


def execute_agent_suggestion(
    db: Session,
    suggestion: AgentSuggestion,
    *,
    actor: str,
    note: str | None = None,
) -> AgentSuggestion:
    if suggestion.status != "approved":
        raise ValueError("SUGGESTION_MUST_BE_APPROVED_BEFORE_EXECUTION")
    result = _build_suggestion_execution_result(suggestion)
    suggestion.status = "draft_generated"
    suggestion.executed_by = actor
    suggestion.executed_at = _utcnow()
    suggestion.execution_result_json = json_dumps(result)
    _add_suggestion_event(db, suggestion, "draft_generated", actor=actor, note=note, payload=result)
    db.commit()
    db.refresh(suggestion)
    return suggestion


def final_confirm_agent_suggestion(
    db: Session,
    suggestion: AgentSuggestion,
    *,
    accepted_agent_result: bool,
    actor: str,
    final_result: dict[str, Any] | None = None,
    note: str | None = None,
) -> AgentSuggestion:
    if suggestion.status != "draft_generated":
        raise ValueError("SUGGESTION_DRAFT_MUST_BE_GENERATED_BEFORE_FINAL_CONFIRM")
    payload = {
        "accepted_agent_result": bool(accepted_agent_result),
        "final_result": final_result or {},
        "execution_result": json_loads(suggestion.execution_result_json, {}),
    }
    suggestion.status = "agent_result_confirmed" if accepted_agent_result else "human_modified"
    suggestion.final_confirmed_by = actor
    suggestion.final_confirmed_at = _utcnow()
    suggestion.final_note = note
    suggestion.final_result_json = json_dumps(payload)
    _add_suggestion_event(
        db,
        suggestion,
        "final_confirmed" if accepted_agent_result else "human_modified",
        actor=actor,
        note=note,
        payload=payload,
    )
    db.commit()
    db.refresh(suggestion)
    return suggestion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _persist_agent_suggestions(
    db: Session,
    run: AgentRun,
    payloads: list[dict[str, Any]],
    *,
    created_by: str,
) -> list[AgentSuggestion]:
    rows: list[AgentSuggestion] = []
    for payload in payloads:
        suggestion = AgentSuggestion(
            suggestion_id=str(uuid.uuid4()),
            run_id=run.run_id,
            agent_type=run.agent_type,
            target_type=run.target_type,
            target_id=run.target_id,
            suggestion_type=payload["suggestion_type"],
            status="pending_review",
            priority=payload.get("priority") or "medium",
            target_ref=payload.get("target_ref"),
            target_line_no=payload.get("target_line_no"),
            title=(payload.get("title") or "Agent 建议")[:255],
            rationale=payload.get("rationale"),
            risk_note=payload.get("risk_note"),
            current_snapshot_json=json_dumps(payload.get("current_snapshot") or {}),
            proposed_snapshot_json=json_dumps(payload.get("proposed_snapshot") or {}),
            estimated_saving_amount=parse_amount(payload.get("estimated_saving_amount")),
            estimated_saving_rate=parse_amount(payload.get("estimated_saving_rate")),
            confidence=parse_amount(payload.get("confidence")),
            requires_approval=bool(payload.get("requires_approval", True)),
            created_by=created_by,
        )
        db.add(suggestion)
        db.flush()
        _add_suggestion_event(
            db,
            suggestion,
            "created",
            actor=created_by,
            payload={
                "suggestion_type": suggestion.suggestion_type,
                "priority": suggestion.priority,
                "estimated_saving_amount": suggestion.estimated_saving_amount,
                "estimated_saving_rate": suggestion.estimated_saving_rate,
            },
        )
        rows.append(suggestion)
    return rows


def _add_suggestion_event(
    db: Session,
    suggestion: AgentSuggestion,
    event_type: str,
    *,
    actor: str | None = None,
    note: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AgentSuggestionEvent:
    event = AgentSuggestionEvent(
        event_id=str(uuid.uuid4()),
        suggestion_id=suggestion.suggestion_id,
        run_id=suggestion.run_id,
        event_type=event_type,
        actor=actor,
        note=note,
        payload_json=json_dumps(payload or {}),
    )
    db.add(event)
    return event


def _build_suggestion_execution_result(suggestion: AgentSuggestion) -> dict[str, Any]:
    current = json_loads(suggestion.current_snapshot_json, {})
    proposed = json_loads(suggestion.proposed_snapshot_json, {})
    result: dict[str, Any] = {
        "mode": "draft_only",
        "applied_to_business_record": False,
        "message": "Agent 已生成调整草案，尚未修改报价单；需人工最终确认后再进入业务流程。",
        "suggestion_id": suggestion.suggestion_id,
        "suggestion_type": suggestion.suggestion_type,
        "target_ref": suggestion.target_ref,
        "target_line_no": suggestion.target_line_no,
        "current_snapshot": current,
        "proposed_snapshot": proposed,
        "estimated_saving_amount": suggestion.estimated_saving_amount,
        "estimated_saving_rate": suggestion.estimated_saving_rate,
    }
    action = proposed.get("action") if isinstance(proposed, dict) else None
    if action in {"adjust_unit_price", "replace_cost_item"}:
        result["quote_line_patch"] = {
            "line_no": suggestion.target_line_no,
            "project_name": current.get("project_name") if isinstance(current, dict) else None,
            "unit_price_before": current.get("display_unit_price") if isinstance(current, dict) else None,
            "total_price_before": current.get("display_total_price") if isinstance(current, dict) else None,
            "unit_price_after": proposed.get("suggested_unit_price"),
            "total_price_after": proposed.get("suggested_total_price"),
            "action": action,
        }
    else:
        result["manual_checklist"] = {
            "action": action or "manual_review",
            "risk_note": suggestion.risk_note,
            "rationale": suggestion.rationale,
        }
    return result


def _run_tool(
    db: Session,
    run_id: str,
    tool_name: str,
    input_payload: dict[str, Any],
    fn: Callable[[], Any],
) -> Any:
    started = time.perf_counter()
    try:
        output = fn()
        status = "success"
        output_summary = _tool_output_summary(tool_name, output)
        return output
    except Exception as exc:
        output = {"error": str(exc)}
        status = "failed"
        output_summary = str(exc)
        raise
    finally:
        db.add(
            AgentToolCall(
                run_id=run_id,
                tool_name=tool_name,
                input_json=json_dumps(input_payload),
                output_summary=output_summary,
                output_json=json_dumps(output),
                status=status,
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )
        )
        db.commit()


def _quote_job_context(job: QuoteJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_number": quote_job_number(job),
        "username": job.username,
        "status": job.status,
        "stage": job.stage,
        "request_summary": job.request_summary or (job.message or "")[:180],
        "result_total_amount": job.result_total_amount,
        "result_item_count": job.result_item_count,
        "created_at": _format_dt(job.created_at),
        "finished_at": _format_dt(job.finished_at),
        "trace_id": job.trace_id,
    }


def _row_level_findings(job_id: str, review_detail: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in review_detail.get("missing_requirement_rows") or []:
        row = item.get("requirement_row") or {}
        row_label = _row_label(row)
        findings.append(
            _finding(
                "missing_requirement_row",
                "high",
                _row_ref(job_id, row, fallback=f"requirement:{item.get('requirement_index')}"),
                f"疑似未报价：{_row_display_name(row)}",
                {
                    "target_label": row_label,
                    "requirement_row": row,
                    "match_status": item.get("status"),
                    "score": item.get("score"),
                },
                "请确认该行是否需要补充报价。",
            )
        )

    for row in review_detail.get("preview_rows") or []:
        risk = row.get("risk") or {}
        if risk.get("type") not in {"danger", "warning"}:
            continue
        severity = "high" if risk.get("type") == "danger" else "medium"
        failed_checks = _failed_checks(row)
        row_label = _row_label(row)
        findings.append(
            _finding(
                "preview_row_risk",
                severity,
                _row_ref(job_id, row, fallback=f"preview:{row.get('line_no')}"),
                f"{_row_short_label(row)}需复核",
                {
                    "target_label": row_label,
                    "line_no": row.get("line_no"),
                    "project_name": row.get("project_name"),
                    "requirement_row_key": row.get("requirement_row_key"),
                    "risk": risk,
                    "failed_checks": failed_checks,
                    "unit_price": row.get("display_unit_price"),
                    "total_price": row.get("display_total_price"),
                },
                "请按失败检查项逐项确认，必要时补价、切换成本条目或打回重算。",
            )
        )
        if len(findings) >= MAX_ROW_FINDINGS:
            break
    return findings


def _price_suggestions_for_row(job_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    reference = row.get("cost_reference") or {}
    if not isinstance(reference, dict) or not reference.get("matched"):
        return []

    line_no = _int_or_none(row.get("line_no"))
    target_ref = _row_ref(job_id, row, fallback=f"preview:{line_no or row.get('index')}")
    quantity = parse_amount(row.get("quantity"))
    current_unit = parse_amount(row.get("display_unit_price"))
    if current_unit is None:
        current_unit = parse_amount(row.get("ai_unit_price"))
    current_total = _line_total(row, unit_price=current_unit, quantity=quantity)
    reference_price = parse_amount(reference.get("reference_price"))
    if reference_price is None or reference_price <= 0:
        return []

    suggestions: list[dict[str, Any]] = []
    delta_rate = parse_amount(reference.get("price_delta_rate"))
    if current_unit is not None and current_unit > reference_price and (
        delta_rate is None or delta_rate >= PRICE_ADJUSTMENT_THRESHOLD
    ):
        proposed_total = _total_from_unit(reference_price, quantity, current_total=current_total, current_unit=current_unit)
        saving_amount = _saving_amount(current_total, proposed_total)
        saving_rate = _saving_rate(saving_amount, current_total)
        if saving_amount is not None and saving_amount > 0:
            title = f"{_row_short_label(row)}建议按成本库参考价调价，预计节省 {_money_text(saving_amount)} 元"
        else:
            title = f"{_row_short_label(row)}建议按成本库参考价复核单价"
        suggestions.append(
            _suggestion(
                "price_adjustment",
                "high" if (delta_rate or 0) >= 0.35 else "medium",
                target_ref,
                line_no,
                title,
                (
                    f"当前单价 {_money_text(current_unit)} 元，高于成本库参考价 "
                    f"{_money_text(reference_price)} 元。"
                ),
                "价格下调前需确认施工范围、规格、品牌和现场条件与成本库条目一致。",
                _line_snapshot(row),
                {
                    "action": "adjust_unit_price",
                    "suggested_unit_price": _round_money(reference_price),
                    "suggested_total_price": proposed_total,
                    "cost_reference": reference,
                    "draft_only": True,
                },
                saving_amount,
                saving_rate,
                _confidence_for_reference(reference),
            )
        )

    alternative = _best_saving_alternative(reference, current_unit)
    if alternative:
        alt_price = parse_amount(alternative.get("reference_price"))
        proposed_total = _total_from_unit(alt_price, quantity, current_total=current_total, current_unit=current_unit)
        saving_amount = _saving_amount(current_total, proposed_total)
        saving_rate = _saving_rate(saving_amount, current_total)
        if saving_amount is not None and saving_amount > 0:
            suggestions.append(
                _suggestion(
                    "cost_saving_replacement",
                    "medium",
                    target_ref,
                    line_no,
                    (
                        f"{_row_short_label(row)}可切换省钱条目，预计节省 "
                        f"{_money_text(saving_amount)} 元"
                    ),
                    (
                        f"当前单价 {_money_text(current_unit)} 元；候选成本条目“"
                        f"{alternative.get('item_name') or '-'}”参考价 {_money_text(alt_price)} 元。"
                    ),
                    "替代前必须确认规格、品牌、施工范围和质量要求一致；不一致时不得直接替换。",
                    _line_snapshot(row),
                    {
                        "action": "replace_cost_item",
                        "replacement_cost_item": alternative,
                        "suggested_unit_price": _round_money(alt_price),
                        "suggested_total_price": proposed_total,
                        "draft_only": True,
                    },
                    saving_amount,
                    saving_rate,
                    0.68,
                )
            )
    return suggestions


def _risk_suggestions_for_row(job_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    checks = row.get("checks") or {}
    if not isinstance(checks, dict):
        checks = {}
    reference = row.get("cost_reference") or {}
    if not isinstance(reference, dict):
        reference = {}
    line_no = _int_or_none(row.get("line_no"))
    target_ref = _row_ref(job_id, row, fallback=f"preview:{line_no or row.get('index')}")
    suggestions: list[dict[str, Any]] = []

    if row.get("requirement_placeholder"):
        suggestions.append(
            _suggestion(
                "manual_price_completion",
                "high",
                target_ref,
                line_no,
                f"{_row_short_label(row)}为 AI 未返回占位行，需人工补价",
                "占位行不允许直接下发，必须补充有效单价和合计。",
                "建议按成本部门核价或打回重算，补价后再执行最终确认。",
                _line_snapshot(row),
                {"action": "manual_complete_price", "draft_only": True},
                None,
                None,
                0.92,
            )
        )

    if _check_failed(checks, "has_cost_reference"):
        suggestions.append(
            _suggestion(
                "risk_mitigation",
                "medium",
                target_ref,
                line_no,
                f"{_row_short_label(row)}无成本库参考，需人工核价",
                "本行未命中 active 成本库底价，AI 估价缺少内部成本证据。",
                "建议人工确认价格；确认下发后按规则沉淀为成本库 draft，待成本审核后再启用 active。",
                _line_snapshot(row),
                {"action": "manual_verify_no_cost_reference", "draft_only": True},
                None,
                None,
                0.82,
            )
        )

    for key, title, note in (
        (
            "cost_candidate_confirmed",
            f"{_row_short_label(row)}存在多条成本候选，需确认采用依据",
            "建议在候选条目中选择规格和施工范围最匹配的一项，再重新计算差异。",
        ),
        (
            "ai_rewrite_confirmed",
            f"{_row_short_label(row)}AI 改写与成本依据不一致",
            "建议优先采用原始需求命中的成本依据，或人工说明改写原因后再确认。",
        ),
        (
            "ai_note_confirmed",
            f"{_row_short_label(row)}AI 备注与成本依据冲突",
            "建议修正对客户展示备注，避免出现“无数据/无法报价”等与成本证据冲突的说明。",
        ),
        (
            "manual_quantity_change_not_large",
            f"{_row_short_label(row)}人工工程量改动幅度较大",
            "建议补充工程量调整原因，复核图纸/现场计量/客户确认口径是否一致。",
        ),
        (
            "manual_unit_price_change_not_large",
            f"{_row_short_label(row)}人工单价改动幅度较大",
            "建议补充人工改价原因，并复核最终报价是否仍满足毛利和客户口径。",
        ),
        (
            "manual_change_not_large",
            f"{_row_short_label(row)}人工改动幅度较大",
            "建议补充人工改价原因，并复核最终报价是否仍满足毛利和客户口径。",
        ),
    ):
        if _check_failed(checks, key):
            suggestions.append(
                _suggestion(
                    "risk_mitigation",
                    "high" if key in {"cost_candidate_confirmed", "ai_rewrite_confirmed", "ai_note_confirmed"} else "medium",
                    target_ref,
                    line_no,
                    title,
                    checks.get(key, {}).get("label") or "预审检查未通过。",
                    note,
                    _line_snapshot(row),
                    {"action": key, "cost_reference": reference, "draft_only": True},
                    None,
                    None,
                    0.84,
                )
            )
    return suggestions


def _suggestion(
    suggestion_type: str,
    priority: str,
    target_ref: str,
    target_line_no: int | None,
    title: str,
    rationale: str,
    risk_note: str,
    current_snapshot: dict[str, Any],
    proposed_snapshot: dict[str, Any],
    estimated_saving_amount: float | None,
    estimated_saving_rate: float | None,
    confidence: float,
) -> dict[str, Any]:
    return {
        "suggestion_type": suggestion_type,
        "priority": priority,
        "target_ref": target_ref,
        "target_line_no": target_line_no,
        "title": title,
        "rationale": rationale,
        "risk_note": risk_note,
        "current_snapshot": current_snapshot,
        "proposed_snapshot": proposed_snapshot,
        "estimated_saving_amount": _round_money(estimated_saving_amount),
        "estimated_saving_rate": estimated_saving_rate,
        "confidence": round(float(confidence), 4),
        "requires_approval": True,
    }


def _line_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_label": _row_label(row),
        "line_no": row.get("line_no"),
        "project_name": row.get("project_name"),
        "item_name": row.get("item_name"),
        "spec": _row_spec(row),
        "quantity": row.get("quantity"),
        "unit": row.get("unit"),
        "display_unit_price": row.get("display_unit_price"),
        "display_total_price": row.get("display_total_price"),
        "ai_unit_price": row.get("ai_unit_price"),
        "system_total_price": row.get("system_total_price"),
        "final_unit_price": row.get("final_unit_price"),
        "final_total_price": row.get("final_total_price"),
        "requirement_row_key": row.get("requirement_row_key"),
        "source_sheet": row.get("source_sheet"),
        "raw_row_index": row.get("raw_row_index"),
        "notes": row.get("notes"),
        "cost_reference": row.get("cost_reference"),
        "risk": row.get("risk"),
    }


def _target_label_from_evidence(evidence: dict[str, Any], fallback: str | None = None) -> str | None:
    if not isinstance(evidence, dict):
        return fallback
    label = evidence.get("target_label")
    if label:
        return str(label)
    row = evidence.get("requirement_row")
    if isinstance(row, dict):
        return _row_label(row)
    if evidence.get("project_name") or evidence.get("line_no"):
        return _row_label(evidence)
    return fallback


def _target_label_from_snapshot(snapshot: dict[str, Any], fallback: str | None = None) -> str | None:
    if not isinstance(snapshot, dict):
        return fallback
    label = snapshot.get("row_label") or snapshot.get("target_label")
    if label:
        return str(label)
    if snapshot.get("project_name") or snapshot.get("line_no"):
        return _row_label(snapshot)
    return fallback


def _row_short_label(row: dict[str, Any]) -> str:
    line_no = _row_position(row)
    name = _row_display_name(row)
    if line_no:
        return f"{line_no}「{name}」"
    return f"「{name}」"


def _row_label(row: dict[str, Any]) -> str:
    parts: list[str] = []
    position = _row_position(row)
    if position:
        parts.append(position)
    source_sheet = _clean_row_text(row.get("source_sheet"))
    if source_sheet:
        parts.append(f"Sheet：{source_sheet}")
    name = _row_display_name(row)
    if name:
        parts.append(f"项目：{name}")
    spec = _row_spec(row)
    if spec:
        parts.append(f"规格：{spec}")
    quantity = row.get("quantity")
    unit = _clean_row_text(row.get("unit"))
    if quantity not in (None, "") or unit:
        parts.append(f"工程量：{quantity if quantity not in (None, '') else '-'}{unit or ''}")
    unit_price = row.get("display_unit_price")
    if unit_price is None:
        unit_price = row.get("unit_price") or row.get("ai_unit_price")
    total_price = row.get("display_total_price")
    if total_price is None:
        total_price = row.get("total_price") or row.get("system_total_price")
    if unit_price not in (None, ""):
        parts.append(f"单价：{_money_text(unit_price)}元")
    if total_price not in (None, ""):
        parts.append(f"合计：{_money_text(total_price)}元")
    reference = row.get("cost_reference")
    if isinstance(reference, dict) and reference.get("matched"):
        ref_name = _clean_row_text(reference.get("item_name"))
        ref_spec = _clean_row_text(reference.get("spec"))
        ref_price = reference.get("reference_price")
        ref_parts = [value for value in (ref_name, ref_spec) if value]
        ref_text = " / ".join(ref_parts)
        if ref_price not in (None, ""):
            ref_text = f"{ref_text}，参考价 {_money_text(ref_price)}元" if ref_text else f"参考价 {_money_text(ref_price)}元"
        if ref_text:
            parts.append(f"成本参考：{ref_text}")
    return "｜".join(parts) or "未定位到具体报价行"


def _row_position(row: dict[str, Any]) -> str:
    line_no = row.get("line_no")
    if line_no not in (None, ""):
        return f"第 {line_no} 行"
    raw_row_index = row.get("raw_row_index")
    if raw_row_index not in (None, ""):
        return f"原始第 {raw_row_index} 行"
    index = row.get("index")
    if index not in (None, ""):
        return f"第 {index} 行"
    return ""


def _row_display_name(row: dict[str, Any]) -> str:
    for key in ("project_name", "item_name", "name", "raw_text"):
        text = _clean_row_text(row.get(key))
        if text:
            return text
    return "未命名报价条目"


def _row_spec(row: dict[str, Any]) -> str:
    for key in ("spec", "specification", "feature", "project_feature", "features"):
        text = _clean_row_text(row.get(key), max_length=80)
        if text:
            return text
    notes = _clean_row_text(row.get("notes"), max_length=80)
    return notes


def _clean_row_text(value: Any, *, max_length: int = 60) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > max_length:
        return text[: max_length - 1] + "…"
    return text


def _line_total(row: dict[str, Any], *, unit_price: float | None, quantity: float | None) -> float | None:
    total = parse_amount(row.get("display_total_price"))
    if total is None:
        total = parse_amount(row.get("system_total_price"))
    if total is not None:
        return _round_money(total)
    if unit_price is not None and quantity is not None:
        return _round_money(float(unit_price) * float(quantity))
    return None


def _total_from_unit(
    unit_price: float | None,
    quantity: float | None,
    *,
    current_total: float | None,
    current_unit: float | None,
) -> float | None:
    if unit_price is None:
        return None
    if quantity is not None:
        return _round_money(float(unit_price) * float(quantity))
    if current_total is not None and current_unit not in (None, 0):
        return _round_money(float(current_total) * float(unit_price) / float(current_unit))
    return None


def _saving_amount(current_total: float | None, proposed_total: float | None) -> float | None:
    if current_total is None or proposed_total is None:
        return None
    return _round_money(max(0.0, float(current_total) - float(proposed_total)))


def _saving_rate(saving_amount: float | None, current_total: float | None) -> float | None:
    if saving_amount is None or current_total in (None, 0):
        return None
    return round(float(saving_amount) / float(current_total), 4)


def _best_saving_alternative(reference: dict[str, Any], current_unit: float | None) -> dict[str, Any] | None:
    if current_unit is None or current_unit <= 0:
        return None
    current_cost_item_id = reference.get("cost_item_id")
    alternatives = reference.get("alternative_cost_items") or []
    if not isinstance(alternatives, list):
        return None
    candidates: list[dict[str, Any]] = []
    for item in alternatives:
        if not isinstance(item, dict):
            continue
        if current_cost_item_id is not None and item.get("id") == current_cost_item_id:
            continue
        price = parse_amount(item.get("reference_price"))
        if price is None or price <= 0 or price >= current_unit:
            continue
        candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda item: parse_amount(item.get("reference_price")) or current_unit)
    return candidates[0]


def _check_failed(checks: dict[str, Any], key: str) -> bool:
    check = checks.get(key) or {}
    return isinstance(check, dict) and not check.get("skipped") and not check.get("passed")


def _confidence_for_reference(reference: dict[str, Any]) -> float:
    match_type = reference.get("match_type")
    if match_type == "exact_item_spec":
        return 0.88
    if match_type == "manual_selected":
        return 0.9
    if match_type == "fuzzy_item_name":
        return 0.72
    return 0.78


def _saving_summary(suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    saving_suggestions = [
        item
        for item in suggestions
        if parse_amount(item.get("estimated_saving_amount")) is not None
        and (parse_amount(item.get("estimated_saving_amount")) or 0) > 0
    ]
    total = _round_money(sum(parse_amount(item.get("estimated_saving_amount")) or 0 for item in saving_suggestions))
    return {
        "estimated_total_saving_amount": total or 0.0,
        "saving_suggestion_count": len(saving_suggestions),
        "high_confidence_saving_count": sum(1 for item in saving_suggestions if (parse_amount(item.get("confidence")) or 0) >= 0.8),
        "max_single_saving_amount": _round_money(
            max((parse_amount(item.get("estimated_saving_amount")) or 0 for item in saving_suggestions), default=0)
        ),
    }


def _round_money(value: Any) -> float | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    return round(float(amount), 2)


def _money_text(value: Any) -> str:
    amount = parse_amount(value)
    if amount is None:
        return "-"
    return f"{float(amount):.2f}"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _failed_checks(row: dict[str, Any]) -> list[dict[str, Any]]:
    checks = row.get("checks") or {}
    result = []
    for key, check in checks.items():
        if not isinstance(check, dict) or check.get("skipped") or check.get("passed"):
            continue
        result.append({"key": key, "severity": check.get("severity"), "label": check.get("label")})
    return result


def _finding(
    finding_type: str,
    severity: str,
    target_ref: str,
    title: str,
    evidence: dict[str, Any],
    suggestion: str,
) -> dict[str, Any]:
    return {
        "type": finding_type,
        "severity": severity,
        "target_ref": target_ref,
        "title": title,
        "evidence": evidence,
        "suggestion": suggestion,
    }


def _row_ref(job_id: str, row: dict[str, Any], *, fallback: str) -> str:
    key = row.get("requirement_row_key") or row.get("blocked_row_key")
    if key:
        return f"quote_job:{job_id}:row_key:{key}"
    line_no = row.get("line_no")
    if line_no:
        return f"quote_job:{job_id}:line:{line_no}"
    return f"quote_job:{job_id}:{fallback}"


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for item in findings:
        severity = item.get("severity") or "low"
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _risk_level(severity_counts: dict[str, int], job_context: dict[str, Any], summary: dict[str, Any]) -> str:
    if job_context.get("status") not in {"succeeded", "completed"}:
        return "high"
    if severity_counts.get("high", 0) or summary.get("high_risk_count", 0):
        return "high"
    if severity_counts.get("medium", 0) or summary.get("review_required_count", 0):
        return "medium"
    return "low"


def _recommendation(risk_level: str, summary: dict[str, Any]) -> str:
    if risk_level == "high":
        return "manual_review_required"
    if risk_level == "medium":
        return "review_before_push"
    if summary.get("review_required_count", 0):
        return "spot_check_before_push"
    return "can_push_after_spot_check"


def _summary_text(job_context: dict[str, Any], summary: dict[str, Any], risk_level: str, recommendation: str) -> str:
    risk_label = {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(risk_level, risk_level)
    recommendation_label = {
        "manual_review_required": "建议人工复核后再下发",
        "review_before_push": "建议复核重点问题后再下发",
        "spot_check_before_push": "建议抽查后下发",
        "can_push_after_spot_check": "可常规抽查后下发",
    }.get(recommendation, recommendation)
    return (
        f"报价任务 {job_context.get('job_number') or job_context['job_id']} Agent 复核完成：{risk_label}，{recommendation_label}。"
        f"确认清单 {summary.get('requirement_row_count', 0)} 行，预审 {summary.get('preview_row_count', 0)} 行，"
        f"疑似缺失 {summary.get('missing_count', 0)} 行，无底价 {summary.get('no_cost_reference_count', 0)} 行，"
        f"高风险 {summary.get('high_risk_count', 0)} 行。"
    )


def _audit_summary_text(job_context: dict[str, Any], audit_summary: dict[str, Any], risk_level: str) -> str:
    risk_label = {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(risk_level, risk_level)
    return (
        f"报价任务 {job_context.get('job_number') or job_context['job_id']} 后审计完成：{risk_label}；"
        f"已记录 {audit_summary.get('audit_record_count', 0)} 条预审风险/人工改动审计，"
        f"其中高风险 {audit_summary.get('high_risk_count', 0)} 条、人工改价 "
        f"{audit_summary.get('manual_modified_count', 0)} 条；"
        f"联网市场价来源 {audit_summary.get('market_search_result_count', 0)} 条，RAG 与 Memory 未参与。"
    )


def _next_actions(findings: list[dict[str, Any]], recommendation: str) -> list[str]:
    actions = []
    types = {item.get("type") for item in findings}
    if {"missing_requirement_rows", "missing_requirement_row", "requirement_placeholders"} & types:
        actions.append("先处理疑似未报价和占位未补价行。")
    if "no_cost_reference" in types:
        actions.append("复核无成本库参考行的人工价格，并按规则沉淀 draft。")
    if {"ai_rewrite_risk", "ai_note_conflict"} & types:
        actions.append("确认 AI 改写项目名和备注是否与成本依据一致。")
    if "extra_preview_rows" in types:
        actions.append("检查额外预审行是否为合理拆项，避免重复报价。")
    if not actions and recommendation == "can_push_after_spot_check":
        actions.append("按常规抽查流程确认后下发。")
    return actions


def _audit_next_actions(audit_summary: dict[str, Any]) -> list[str]:
    record_count = int(audit_summary.get("audit_record_count") or 0)
    manual_count = int(audit_summary.get("manual_modified_count") or 0)
    if not record_count:
        return ["本次后审计未发现需要留痕的预审风险或人工改价记录，不生成待办。"]
    actions = [f"已留痕 {record_count} 条修改前后审计记录，可按需查看原预审风险与最终下发报价状态。"]
    if manual_count:
        actions.append(f"其中 {manual_count} 条存在人工改价，已记录工程量/单价修改前后解释。")
    actions.append("该 Agent 不生成每日待办，不要求二次人工确认，仅保留后审计证据。")
    return actions


def _markdown_report(report: dict[str, Any], job_context: dict[str, Any]) -> str:
    lines = [
        f"# 报价复核 Agent 报告",
        "",
        f"- 任务号: `{job_context.get('job_number') or job_context['job_id']}`",
        f"- 内部ID: `{job_context['job_id']}`",
        f"- 风险等级: {report['risk_level']}",
        f"- 建议动作: {report['recommendation']}",
        f"- 摘要: {report['summary']}",
        "",
        "## 关键指标",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"- {key}: {value}")
    saving = report.get("saving_summary") or {}
    lines.append(f"- estimated_total_saving_amount: {saving.get('estimated_total_saving_amount', 0)}")
    lines.append(f"- saving_suggestion_count: {saving.get('saving_suggestion_count', 0)}")
    lines.append("")
    lines.append("## 下一步")
    for action in report["next_actions"]:
        lines.append(f"- {action}")
    lines.append("")
    lines.append("## 优化建议")
    for item in report.get("suggestions", [])[:20]:
        saving_text = ""
        if item.get("estimated_saving_amount"):
            saving_text = f"，预计节省 {item['estimated_saving_amount']} 元"
        lines.append(f"- [{item['priority']}] {item['title']}{saving_text}")
    lines.append("")
    lines.append("## 风险发现")
    for item in report["findings"][:20]:
        lines.append(f"- [{item['severity']}] {item['title']} - {item['suggestion']}")
    return "\n".join(lines)


def _tool_output_summary(tool_name: str, output: Any) -> str:
    if tool_name == "get_quote_job_context" and isinstance(output, dict):
        return f"job={output.get('job_id')} status={output.get('status')}"
    if tool_name == "get_quote_review_detail" and isinstance(output, dict):
        summary = output.get("summary") or {}
        return (
            f"requirement={summary.get('requirement_row_count', 0)} "
            f"preview={summary.get('preview_row_count', 0)} "
            f"review_required={summary.get('review_required_count', 0)}"
        )
    if tool_name == "derive_quote_review_findings" and isinstance(output, list):
        return f"findings={len(output)}"
    if tool_name == "build_confirmed_quote_audit_records" and isinstance(output, list):
        return f"audit_records={len(output)}"
    if tool_name == "market_price_web_search" and isinstance(output, dict):
        summary = output.get("summary") or {}
        return (
            f"market_search_results={summary.get('result_count', 0)} "
            f"status={summary.get('status')}"
        )
    if tool_name == "derive_quote_review_suggestions" and isinstance(output, list):
        saving = _saving_summary(output)
        return (
            f"suggestions={len(output)} "
            f"saving={saving.get('estimated_total_saving_amount', 0)}"
        )
    if tool_name == "generate_quote_review_report" and isinstance(output, dict):
        return f"risk={output.get('risk_level')} recommendation={output.get('recommendation')}"
    return f"type={type(output).__name__}"


def _format_dt(value: Any) -> str | None:
    if not value:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
