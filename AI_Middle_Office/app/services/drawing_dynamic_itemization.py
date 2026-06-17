from __future__ import annotations

import csv
import asyncio
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.core.config import settings
from app.services.drawing_quantity_confirmation import build_drawing_confirmation_pack, write_confirmation_outputs
from app.services.model_gateway import post_json_via_gateway
from app.services.quantity_standard_index import (
    LoadedStandardLibraryIndex,
    find_standard_item,
    infer_standard_routes,
    load_standard_library_index,
    search_pricing_rules,
    search_standard_index,
    standard_index_summary,
)


PHASE = "BIZ-2x-R0-R9-standard-llm-dynamic-itemization"

STAGE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("R0", "standard_library_baseline"),
    ("R1", "drawing_evidence_unification"),
    ("R2", "drawing_profession_and_scenario_route"),
    ("R3", "standard_candidate_retrieval"),
    ("R4", "llm_dynamic_itemization_contract"),
    ("R5", "programmatic_hard_validation"),
    ("R6", "classified_quantity_execution"),
    ("R7", "manual_confirmation"),
    ("R8", "four_field_excel_export"),
    ("R9", "feedback_loop"),
)

LLM_DECISION_SCHEMA = {
    "type": "object",
    "required": [
        "signal_id",
        "standard_code",
        "item_code",
        "display_item_name",
        "selected_unit",
        "feature_values",
        "quantity",
        "quantity_source",
        "confidence",
        "reasoning_summary",
    ],
    "properties": {
        "signal_id": {"type": "string"},
        "standard_code": {"type": "string", "enum": ["GBT50854-2024", "GBT50856-2024"]},
        "item_code": {"type": "string"},
        "display_item_name": {"type": "string"},
        "selected_unit": {"type": "string"},
        "feature_values": {"type": "object"},
        "quantity": {"type": ["number", "string", "null"]},
        "quantity_source": {
            "type": "string",
            "enum": ["dxf", "pdf_visual", "manual", "none"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning_summary": {"type": "string"},
    },
}

LLM_SYSTEM_PROMPT = """You are a construction quantity itemization assistant.
You must select a bill item only from the supplied GB/T candidates.
Return strict JSON only, without Markdown.
Do not invent GB/T codes, units, feature field names, or quantities.
If a fine-grained business item is needed, keep it in display_item_name while
attaching it to one supplied GB/T parent candidate.
Quantity may only come from DXF/PDF evidence or manual input; never estimate it.
"""


def build_dynamic_itemization_report(
    evidence_source: Mapping[str, Any] | list[Mapping[str, Any]],
    *,
    standard_index: LoadedStandardLibraryIndex | None = None,
    llm_decisions: Mapping[str, Any] | list[Mapping[str, Any]] | None = None,
    limit_per_signal: int = 6,
    min_decision_confidence: float = 0.55,
) -> dict[str, Any]:
    index = standard_index or load_standard_library_index()
    evidence_signals = normalize_evidence_signals(evidence_source)
    llm_decision_lookup = _normalize_llm_decisions(llm_decisions)
    decisions: list[dict[str, Any]] = []
    candidate_groups: list[dict[str, Any]] = []

    for signal in evidence_signals:
        route = route_evidence_signal(signal)
        candidates = search_standard_index(
            signal["evidence_text"],
            index=index,
            standard_codes=route["standard_codes"],
            include_draft=False,
            limit=limit_per_signal,
        )
        llm_payload = build_llm_prompt_payload(signal, route, candidates)
        decision = _build_itemization_decision(
            signal=signal,
            route=route,
            candidates=candidates,
            index=index,
            llm_decision=llm_decision_lookup.get(signal["signal_id"]),
            min_decision_confidence=min_decision_confidence,
        )
        decisions.append(decision)
        candidate_groups.append(
            {
                "signal_id": signal["signal_id"],
                "source_signal": signal,
                "route": route,
                "candidate_count": len(candidates),
                "standard_candidates": candidates,
                "llm_prompt_payload": llm_payload,
            }
        )

    standard_match_report = build_standard_match_report_from_itemization(decisions)
    quantity_evidence_report = build_quantity_evidence_report_from_itemization(decisions)
    stage_results = _build_stage_results(index, evidence_signals, candidate_groups, decisions)
    validation_summary = _validation_summary(decisions)
    quantity_summary = _quantity_summary(decisions)

    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "evidence_signal_count": len(evidence_signals),
            "candidate_group_count": len(candidate_groups),
            "itemization_decision_count": len(decisions),
            **validation_summary,
            **quantity_summary,
            "final_generation_status": "blocked_until_program_validation_and_manual_confirmation",
        },
        "standard_index_summary": standard_index_summary(index),
        "llm_decision_schema": LLM_DECISION_SCHEMA,
        "stage_results": stage_results,
        "evidence_signals": evidence_signals,
        "candidate_groups": candidate_groups,
        "itemization_decisions": decisions,
        "standard_match_report": standard_match_report,
        "quantity_evidence_report": quantity_evidence_report,
        "manual_confirmation_pack": build_drawing_confirmation_pack(standard_match_report, quantity_evidence_report),
        "feedback_hooks": _feedback_hooks(decisions),
    }


def build_dynamic_itemization_report_runtime(
    evidence_source: Mapping[str, Any] | list[Mapping[str, Any]],
    *,
    standard_index: LoadedStandardLibraryIndex | None = None,
    limit_per_signal: int = 6,
    min_decision_confidence: float = 0.55,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Build the report and use the configured LLM when this sync caller can run one."""
    provider = (settings.agent_llm_provider or "rule").strip().lower()
    if provider == "deepseek" and (settings.deepseek_api_key or "").strip():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                build_dynamic_itemization_report_with_llm(
                    evidence_source,
                    standard_index=standard_index,
                    limit_per_signal=limit_per_signal,
                    min_decision_confidence=min_decision_confidence,
                    username=username,
                    trace_id=trace_id,
                )
            )
        report = build_dynamic_itemization_report(
            evidence_source,
            standard_index=standard_index,
            limit_per_signal=limit_per_signal,
            min_decision_confidence=min_decision_confidence,
        )
        _attach_llm_runtime(
            report,
            provider="deepseek",
            model=settings.deepseek_model,
            statuses=[
                {
                    "signal_id": item.get("signal_id", ""),
                    "status": "skipped",
                    "reason": "running_event_loop_in_sync_caller",
                }
                for item in report.get("evidence_signals") or []
            ],
        )
        return report

    report = build_dynamic_itemization_report(
        evidence_source,
        standard_index=standard_index,
        limit_per_signal=limit_per_signal,
        min_decision_confidence=min_decision_confidence,
    )
    _attach_llm_runtime(
        report,
        provider=provider or "rule",
        model=None,
        statuses=[
            {
                "signal_id": item.get("signal_id", ""),
                "status": "fallback",
                "reason": "provider_not_configured" if provider != "deepseek" else "deepseek_api_key_missing",
            }
            for item in report.get("evidence_signals") or []
        ],
    )
    return report


async def build_dynamic_itemization_report_with_llm(
    evidence_source: Mapping[str, Any] | list[Mapping[str, Any]],
    *,
    standard_index: LoadedStandardLibraryIndex | None = None,
    limit_per_signal: int = 6,
    min_decision_confidence: float = 0.55,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    index = standard_index or load_standard_library_index()
    evidence_signals = normalize_evidence_signals(evidence_source)
    provider = (settings.agent_llm_provider or "rule").strip().lower()
    statuses: list[dict[str, Any]] = []
    llm_decisions: list[dict[str, Any]] = []

    if provider != "deepseek" or not (settings.deepseek_api_key or "").strip():
        report = build_dynamic_itemization_report(
            evidence_signals,
            standard_index=index,
            limit_per_signal=limit_per_signal,
            min_decision_confidence=min_decision_confidence,
        )
        _attach_llm_runtime(
            report,
            provider=provider or "rule",
            model=None,
            statuses=[
                {
                    "signal_id": item.get("signal_id", ""),
                    "status": "fallback",
                    "reason": "provider_not_configured" if provider != "deepseek" else "deepseek_api_key_missing",
                }
                for item in evidence_signals
            ],
        )
        return report

    for signal in evidence_signals:
        route = route_evidence_signal(signal)
        candidates = search_standard_index(
            signal["evidence_text"],
            index=index,
            standard_codes=route["standard_codes"],
            include_draft=False,
            limit=limit_per_signal,
        )
        prompt_payload = build_llm_prompt_payload(signal, route, candidates)
        if not candidates:
            statuses.append(
                {
                    "signal_id": signal["signal_id"],
                    "status": "fallback",
                    "reason": "no_standard_candidates",
                }
            )
            continue
        try:
            decision = await _call_deepseek_itemization(
                prompt_payload,
                username=username,
                trace_id=trace_id,
            )
            if decision:
                decision.setdefault("signal_id", signal["signal_id"])
                llm_decisions.append(decision)
                statuses.append(
                    {
                        "signal_id": signal["signal_id"],
                        "status": "success",
                        "reason": "deepseek_json_decision",
                    }
                )
            else:
                statuses.append(
                    {
                        "signal_id": signal["signal_id"],
                        "status": "fallback",
                        "reason": "empty_llm_decision",
                    }
                )
        except Exception as exc:
            statuses.append(
                {
                    "signal_id": signal["signal_id"],
                    "status": "fallback",
                    "reason": f"deepseek_error:{str(exc)[:160]}",
                }
            )

    report = build_dynamic_itemization_report(
        evidence_signals,
        standard_index=index,
        llm_decisions=llm_decisions,
        limit_per_signal=limit_per_signal,
        min_decision_confidence=min_decision_confidence,
    )
    _attach_llm_runtime(report, provider="deepseek", model=settings.deepseek_model, statuses=statuses)
    return report


def normalize_evidence_signals(
    evidence_source: Mapping[str, Any] | list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(evidence_source, list):
        rows: Iterable[Mapping[str, Any]] = evidence_source
    elif isinstance(evidence_source, Mapping):
        if isinstance(evidence_source.get("evidence_signals"), list):
            rows = evidence_source["evidence_signals"]
        else:
            rows = _iter_field_report_rows(evidence_source)
    else:
        raise ValueError("evidence_source must be a mapping or a list")

    signals: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            continue
        source_name = _first_text(
            raw.get("source_name"),
            raw.get("material_or_method_name"),
            raw.get("item_name"),
            raw.get("name"),
        )
        source_spec = _first_text(raw.get("source_spec_or_method"), raw.get("spec_or_method"), raw.get("feature"))
        raw_text = _first_text(raw.get("raw_row_text"), raw.get("evidence_text"), raw.get("text"))
        evidence_text = _join_text(source_name, source_spec, raw_text)
        if not evidence_text:
            continue
        signal_id = _clean_text(raw.get("signal_id")) or _clean_text(raw.get("candidate_key")) or f"SIG-{index:04d}"
        signals.append(
            {
                "signal_id": signal_id,
                "source_kind": _clean_text(raw.get("source_kind")) or _clean_text(raw.get("row_type")) or "manual_signal",
                "source_kind_label": _clean_text(raw.get("source_kind_label")) or _clean_text(raw.get("row_type_label")),
                "source_file": _clean_text(raw.get("source_file")),
                "source_row_number": raw.get("source_row_number") or raw.get("row_no") or raw.get("line_number") or "",
                "source_name": source_name or evidence_text,
                "source_spec_or_method": source_spec,
                "raw_row_text": raw_text,
                "evidence_text": evidence_text,
                "evidence_source": _clean_text(raw.get("evidence_source")) or "dxf_pdf_unified_evidence",
                "quantity": raw.get("quantity"),
                "quantity_unit": _clean_text(raw.get("quantity_unit") or raw.get("unit")),
                "quantity_source": _clean_text(raw.get("quantity_source")),
                "confidence": _coerce_float(raw.get("confidence"), default=0.0),
            }
        )
    return signals


def route_evidence_signal(signal: Mapping[str, Any]) -> dict[str, Any]:
    text = _clean_text(signal.get("evidence_text"))
    standard_codes = infer_standard_routes(text)
    scenario = _route_scenario(text, standard_codes)
    return {
        "standard_codes": standard_codes,
        "scenario": scenario,
        "drawing_route": _drawing_route_for_scenario(scenario),
        "route_reason": _route_reason(text, standard_codes, scenario),
    }


def build_llm_prompt_payload(
    signal: Mapping[str, Any],
    route: Mapping[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "task": "select_standard_item_and_prepare_four_field_candidate",
        "rules": [
            "Only select an item_code from the provided candidates or leave it empty.",
            "Do not invent GB/T item codes or units.",
            "Quantity must come from DXF/PDF evidence or manual input, never from LLM estimation.",
            "Fine business subitems may be displayed, but must attach to a GB/T parent item.",
        ],
        "schema": LLM_DECISION_SCHEMA,
        "source_signal": dict(signal),
        "route": dict(route),
        "standard_candidates": [
            {
                "standard_code": item["standard_code"],
                "item_code": item["item_code"],
                "official_item_code": item["official_item_code"],
                "item_name": item["item_name"],
                "unit_options": item["unit_options"],
                "feature_field_names": [
                    _clean_text(field.get("name")) for field in item.get("feature_fields") or []
                ],
                "quantity_rule": item["quantity_rule"],
                "score": item["score"],
                "match_reason": item["match_reason"],
            }
            for item in candidates
        ],
    }


def validate_itemization_decision(
    decision: Mapping[str, Any],
    *,
    standard_index: LoadedStandardLibraryIndex | None = None,
) -> dict[str, Any]:
    index = standard_index or load_standard_library_index()
    issues: list[str] = []
    standard_code = _clean_text(decision.get("standard_code"))
    item_code = _clean_text(decision.get("item_code"))
    item = find_standard_item(index, standard_code, item_code) if standard_code and item_code else None
    if not standard_code:
        issues.append("standard_code_missing")
    elif standard_code not in index.quantity_libraries:
        issues.append("standard_code_not_loaded")
    if not item_code:
        issues.append("item_code_missing")
    elif standard_code and item is None:
        issues.append("item_code_not_found_in_standard_library")
    if item:
        selected_unit = _clean_text(decision.get("selected_unit"))
        if selected_unit and selected_unit not in item.unit_options:
            issues.append("selected_unit_not_allowed_by_standard")
        feature_values = decision.get("feature_values") or {}
        if not isinstance(feature_values, Mapping):
            issues.append("feature_values_must_be_object")
        else:
            allowed_feature_names = set(item.feature_names)
            for name in feature_values:
                if _clean_text(name) and _clean_text(name) not in allowed_feature_names:
                    issues.append("feature_field_not_defined_by_standard")
                    break
    if _clean_text(decision.get("quantity_source")).lower() == "llm":
        issues.append("quantity_source_must_not_be_llm")
    if decision.get("quantity") not in (None, "") and not _clean_text(decision.get("quantity_source")):
        issues.append("quantity_source_missing")
    return {
        "status": "passed" if not issues else "failed",
        "issues": issues,
    }


def build_standard_match_report_from_itemization(report_or_decisions: Mapping[str, Any] | list[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = _decisions_from_report_or_list(report_or_decisions)
    candidate_groups: list[dict[str, Any]] = []
    flattened: list[dict[str, Any]] = []
    for decision in decisions:
        standard_candidate = {
            "rank": 1,
            "item_code": decision.get("item_code", ""),
            "official_item_code": decision.get("official_item_code", ""),
            "item_name": decision.get("standard_item_name", ""),
            "chapter_name": decision.get("chapter_name", ""),
            "unit_options": list(decision.get("unit_options") or []),
            "quantity_rule_text": decision.get("quantity_rule_text", ""),
            "quantity_formula_type": decision.get("quantity_formula_type", ""),
            "quantity_required_evidence": list(decision.get("quantity_required_evidence") or []),
            "quantity_evidence_status": decision.get("quantity_status", ""),
            "feature_fields": list(decision.get("feature_field_names") or []),
            "feature_fill_candidates": list(decision.get("feature_fill_candidates") or []),
            "no_feature_fields_in_standard": decision.get("no_feature_fields_in_standard", False),
            "match_score": decision.get("candidate_score", 0),
            "match_confidence": decision.get("confidence", 0),
            "match_reasons": decision.get("match_reasons", []),
            "matched_fields": decision.get("matched_fields", []),
            "source_note": decision.get("source_note", ""),
        }
        candidate_groups.append(
            {
                "candidate_key": decision.get("candidate_key", ""),
                "source_signal": decision.get("source_signal", {}),
                "standard_candidates": [standard_candidate],
            }
        )
        flattened.append(
            {
                "candidate_key": decision.get("candidate_key", ""),
                "source_kind_label": decision.get("source_signal", {}).get("source_kind_label", ""),
                "source_file": decision.get("source_signal", {}).get("source_file", ""),
                "source_row_number": decision.get("source_signal", {}).get("source_row_number", ""),
                "source_name": decision.get("source_signal", {}).get("source_name", ""),
                "source_spec_or_method": decision.get("source_signal", {}).get("source_spec_or_method", ""),
                "standard_code": decision.get("standard_code", ""),
                "standard_item_code": decision.get("item_code", ""),
                "official_item_code": decision.get("official_item_code", ""),
                "standard_item_name": decision.get("standard_item_name", ""),
                "display_item_name": decision.get("display_item_name", ""),
                "chapter_name": decision.get("chapter_name", ""),
                "unit_options": list(decision.get("unit_options") or []),
                "quantity_rule_text": decision.get("quantity_rule_text", ""),
                "quantity_formula_type": decision.get("quantity_formula_type", ""),
                "quantity_required_evidence": list(decision.get("quantity_required_evidence") or []),
                "quantity_evidence_status": decision.get("quantity_status", ""),
                "match_confidence": decision.get("confidence", 0),
                "match_reasons": decision.get("match_reasons", []),
                "matched_fields": decision.get("matched_fields", []),
            }
        )
    return {
        "ok": True,
        "phase": "BIZ-2x-R0-R9-standard-match-from-dynamic-itemization",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "source_signal_count": len(decisions),
            "matched_signal_count": sum(1 for item in decisions if item.get("hard_validation", {}).get("status") == "passed"),
            "standard_candidate_count": len(flattened),
            "quantity_ready_count": 0,
            "quantity_pending_count": len(flattened),
            "final_generation_status": "blocked_until_quantity_evidence_and_manual_review",
        },
        "candidate_groups": candidate_groups,
        "standard_item_candidates": flattened,
    }


def build_quantity_evidence_report_from_itemization(report_or_decisions: Mapping[str, Any] | list[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = _decisions_from_report_or_list(report_or_decisions)
    quantity_candidates: list[dict[str, Any]] = []
    for decision in decisions:
        signal = decision.get("source_signal", {})
        quantity_candidates.append(
            {
                "candidate_key": decision.get("candidate_key", ""),
                "source_file": signal.get("source_file", ""),
                "source_row_number": signal.get("source_row_number", ""),
                "source_name": decision.get("display_item_name") or signal.get("source_name", ""),
                "source_spec_or_method": signal.get("source_spec_or_method", ""),
                "standard_code": decision.get("standard_code", ""),
                "standard_item_code": decision.get("item_code", ""),
                "official_item_code": decision.get("official_item_code", ""),
                "standard_item_name": decision.get("display_item_name") or decision.get("standard_item_name", ""),
                "standard_parent_item_name": decision.get("standard_item_name", ""),
                "chapter_name": decision.get("chapter_name", ""),
                "unit_options": list(decision.get("unit_options") or []),
                "quantity_rule_text": decision.get("quantity_rule_text", ""),
                "quantity_formula_type": decision.get("quantity_formula_type", ""),
                "quantity_required_evidence": list(decision.get("quantity_required_evidence") or []),
                "quantity_status": decision.get("quantity_status", ""),
                "suggested_quantity": decision.get("suggested_quantity", ""),
                "suggested_unit": decision.get("selected_unit", ""),
                "quantity_can_be_final_without_manual_review": False,
                "quantity_block_reason": decision.get("quantity_block_reason", ""),
                "evidence_count": 1 if decision.get("suggested_quantity") else 0,
                "direct_evidence_count": 1 if decision.get("suggested_quantity") else 0,
                "evidence_summary": decision.get("quantity_evidence_summary", ""),
                "quantity_evidence": [],
            }
        )
    return {
        "ok": True,
        "phase": "BIZ-2x-R0-R9-quantity-evidence-from-dynamic-itemization",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "standard_candidate_count": len(quantity_candidates),
            "quantity_direct_candidate_count": sum(1 for item in quantity_candidates if item["suggested_quantity"]),
            "quantity_missing_evidence_count": sum(1 for item in quantity_candidates if not item["suggested_quantity"]),
            "quantity_ready_without_manual_review_count": 0,
            "final_generation_status": "blocked_until_manual_confirmation",
        },
        "quantity_candidates": quantity_candidates,
        "quantity_evidence_rows": [],
    }


def write_dynamic_itemization_outputs(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
    include_confirmation_pack: bool = True,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R0_R9_dynamic_itemization_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    csv_path = directory / f"{file_stem}_itemization_decisions.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_dynamic_itemization_markdown(report), encoding="utf-8")
    _write_csv(csv_path, _decision_csv_rows(report.get("itemization_decisions") or []))
    outputs = {
        "json": str(json_path),
        "markdown": str(md_path),
        "itemization_decision_csv": str(csv_path),
    }
    if include_confirmation_pack:
        confirmation_outputs = write_confirmation_outputs(
            report.get("manual_confirmation_pack") or {},
            directory,
            stem=f"{file_stem}_manual_confirmation",
        )
        outputs.update({f"confirmation_{key}": value for key, value in confirmation_outputs.items()})
    return outputs


def build_dynamic_itemization_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x R0-R9 标准库约束型动态列项报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 证据线索数：{summary.get('evidence_signal_count', 0)}",
        f"- 列项决策数：{summary.get('itemization_decision_count', 0)}",
        f"- 硬校验失败数：{summary.get('hard_validation_failed_count', 0)}",
        f"- 需人工确认数：{summary.get('needs_human_review_count', 0)}",
        f"- 最终状态：{summary.get('final_generation_status', '-')}",
        "",
        "## R0-R9 状态",
        "",
        "| 阶段 | 状态 | 说明 |",
        "| --- | --- | --- |",
    ]
    for stage in report.get("stage_results") or []:
        lines.append(f"| {stage.get('stage')} | {stage.get('status')} | {stage.get('message')} |")
    lines.extend(
        [
            "",
            "## 列项决策",
            "",
            "| 候选 | 图纸线索 | 国标 | 推荐项目 | 显示项目 | 单位 | 数量状态 | 校验 | 人工确认 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for decision in report.get("itemization_decisions") or []:
        hard = decision.get("hard_validation") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(decision.get("candidate_key")),
                    _md((decision.get("source_signal") or {}).get("source_name")),
                    _md(f"{decision.get('standard_code', '')} {decision.get('item_code', '')}"),
                    _md(decision.get("standard_item_name")),
                    _md(decision.get("display_item_name")),
                    _md(decision.get("selected_unit")),
                    _md(decision.get("quantity_status")),
                    _md(hard.get("status")),
                    _md("是" if decision.get("needs_human_review") else "否"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告不允许 LLM 编造国标编码、单位或工程量。",
            "- 工程量必须来自 DXF/PDF 证据或人工补量；无可靠证据时只列项，不进入最终四字段 Excel。",
            "- 细支分项可以作为显示项目，但必须挂接到标准库上位项目并进入人工确认。",
        ]
    )
    return "\n".join(lines) + "\n"


async def _call_deepseek_itemization(
    prompt_payload: Mapping[str, Any],
    *,
    username: str | None,
    trace_id: str | None,
) -> dict[str, Any]:
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=settings.deepseek_model,
        endpoint_type="drawing_dynamic_itemization",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=settings.agent_llm_timeout_seconds,
        username=username,
        trace_id=trace_id,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}")
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    return _extract_llm_decision(content)


def _extract_llm_decision(content: Any) -> dict[str, Any]:
    payload = _extract_json_object(content)
    if isinstance(payload.get("decision"), Mapping):
        return dict(payload["decision"])
    if isinstance(payload.get("itemization_decision"), Mapping):
        return dict(payload["itemization_decision"])
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        for item in decisions:
            if isinstance(item, Mapping):
                return dict(item)
    return dict(payload)


def _extract_json_object(content: Any) -> dict[str, Any]:
    text = _clean_text(content)
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            value = json.loads(fenced.group(1))
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


def _attach_llm_runtime(
    report: dict[str, Any],
    *,
    provider: str,
    model: str | None,
    statuses: list[dict[str, Any]],
) -> None:
    success_count = sum(1 for item in statuses if item.get("status") == "success")
    fallback_count = sum(1 for item in statuses if item.get("status") in {"fallback", "skipped"})
    report["llm_runtime"] = {
        "provider": provider,
        "model": model,
        "status_summary": {
            "signal_count": len(statuses),
            "success_count": success_count,
            "fallback_count": fallback_count,
        },
        "signal_statuses": statuses,
    }
    summary = report.setdefault("summary", {})
    summary["llm_success_count"] = success_count
    summary["llm_fallback_count"] = fallback_count
    summary["llm_provider"] = provider
    for stage in report.get("stage_results") or []:
        if stage.get("stage") == "R4":
            if success_count:
                stage["status"] = "completed"
                stage["message"] = f"LLM dynamic itemization used for {success_count} signals; all results remain hard-validated"
            else:
                stage["status"] = "completed"
                stage["message"] = "LLM runtime unavailable or skipped; deterministic candidate fallback remains hard-validated"
            break


def _build_itemization_decision(
    *,
    signal: dict[str, Any],
    route: dict[str, Any],
    candidates: list[dict[str, Any]],
    index: LoadedStandardLibraryIndex,
    llm_decision: Mapping[str, Any] | None,
    min_decision_confidence: float,
) -> dict[str, Any]:
    candidate_key = f"R0R9-{int(signal['signal_id'].split('-')[-1]) if signal['signal_id'].split('-')[-1].isdigit() else len(signal['signal_id']):04d}"
    selected = _candidate_from_llm_decision(llm_decision, candidates, index) if llm_decision else (candidates[0] if candidates else {})
    if not selected:
        decision = _empty_decision(signal=signal, route=route, candidate_key=candidate_key)
        decision["hard_validation"] = validate_itemization_decision(decision, standard_index=index)
        return decision

    item = find_standard_item(index, selected["standard_code"], selected["item_code"])
    feature_fill_candidates = _feature_fill_candidates(signal, item) if item else []
    feature_values = _feature_values_from_candidates(feature_fill_candidates)
    override = dict(llm_decision or {})
    selected_unit = _select_unit(signal, selected, override)
    quantity = override.get("quantity", signal.get("quantity"))
    quantity_source = _clean_text(override.get("quantity_source") or signal.get("quantity_source"))
    quantity_status = "direct_quantity_candidate_needs_manual_review" if _parse_decimal(quantity) is not None else "missing_quantity_measurement_needs_manual_review"
    confidence = _decision_confidence(selected, override)
    split_required = _split_required(signal, selected)
    display_item_name = _display_item_name(signal, selected, override, split_required)
    match_reasons = [selected.get("match_reason", "standard_candidate_retrieval")]
    if split_required:
        match_reasons.append("fine_business_subitem_attached_to_standard_parent")

    decision = {
        "candidate_key": candidate_key,
        "signal_id": signal["signal_id"],
        "source_signal": signal,
        "route": route,
        "decision_source": "llm" if llm_decision else "deterministic_fallback",
        "llm_reasoning_summary": _clean_text(override.get("reasoning_summary")),
        "standard_code": selected.get("standard_code", ""),
        "standard_name": selected.get("standard_name", ""),
        "item_code": selected.get("item_code", ""),
        "official_item_code": selected.get("official_item_code", ""),
        "standard_item_name": selected.get("item_name", ""),
        "display_item_name": display_item_name,
        "chapter_name": selected.get("chapter_name", ""),
        "unit_options": list(selected.get("unit_options") or []),
        "selected_unit": selected_unit,
        "feature_field_names": [field.get("name", "") for field in selected.get("feature_fields") or []],
        "feature_values": dict(override.get("feature_values") or feature_values),
        "feature_fill_candidates": feature_fill_candidates,
        "no_feature_fields_in_standard": bool(item.no_feature_fields_in_standard) if item else False,
        "quantity_rule_text": _clean_text((selected.get("quantity_rule") or {}).get("rule_text")),
        "quantity_formula_type": _clean_text((selected.get("quantity_rule") or {}).get("formula_type")),
        "quantity_required_evidence": list((selected.get("quantity_rule") or {}).get("required_evidence") or []),
        "quantity_status": quantity_status,
        "suggested_quantity": _format_quantity(quantity),
        "quantity_source": quantity_source or "none",
        "quantity_block_reason": "" if _parse_decimal(quantity) is not None else "missing_reliable_quantity_evidence",
        "quantity_evidence_summary": _quantity_evidence_summary(quantity, quantity_source, signal),
        "split_required": split_required,
        "split_basis": "business_subitem_under_standard_parent" if split_required else "",
        "candidate_score": selected.get("score", 0),
        "confidence": confidence,
        "matched_fields": selected.get("matched_fields", []),
        "match_reasons": match_reasons,
        "source_note": selected.get("source_note", ""),
        "pricing_rule_refs": search_pricing_rules(signal["evidence_text"], index=index, limit=3),
    }
    hard_validation = validate_itemization_decision(decision, standard_index=index)
    feature_missing = any(item.get("status") == "missing_needs_manual_review" for item in feature_fill_candidates)
    needs_review = (
        hard_validation["status"] != "passed"
        or feature_missing
        or split_required
        or quantity_status != "direct_quantity_candidate_needs_manual_review"
        or confidence < min_decision_confidence
    )
    decision["hard_validation"] = hard_validation
    decision["needs_human_review"] = needs_review
    decision["manual_review_reasons"] = _manual_review_reasons(decision, feature_missing)
    return decision


def _candidate_from_llm_decision(
    llm_decision: Mapping[str, Any] | None,
    candidates: list[dict[str, Any]],
    index: LoadedStandardLibraryIndex,
) -> dict[str, Any]:
    if not llm_decision:
        return {}
    standard_code = _clean_text(llm_decision.get("standard_code"))
    item_code = _clean_text(llm_decision.get("item_code"))
    if not standard_code or not item_code:
        return {}
    for candidate in candidates:
        if candidate["standard_code"] == standard_code and candidate["item_code"] == item_code:
            return candidate
    item = find_standard_item(index, standard_code, item_code)
    if item is None:
        return {
            "standard_code": standard_code,
            "item_code": item_code,
            "official_item_code": item_code,
            "item_name": "",
            "unit_options": [],
            "feature_fields": [],
            "quantity_rule": {},
            "score": 0,
            "matched_fields": [],
            "match_reason": "llm_selected_code_not_found",
        }
    return {
        "standard_code": standard_code,
        "standard_name": index.quantity_libraries[standard_code].standard.get("name", ""),
        "item_code": item.item_code,
        "official_item_code": item.official_item_code,
        "item_name": item.item_name,
        "chapter_name": item.chapter_name,
        "unit_options": list(item.unit_options),
        "feature_fields": list(item.feature_fields),
        "quantity_rule": dict(item.quantity_rule),
        "drawing_evidence_requirements": list(item.drawing_evidence_requirements),
        "source_note": item.source_note,
        "score": 0,
        "matched_fields": ["llm_decision"],
        "match_reason": "llm_selected_existing_standard_item",
    }


def _empty_decision(*, signal: dict[str, Any], route: dict[str, Any], candidate_key: str) -> dict[str, Any]:
    return {
        "candidate_key": candidate_key,
        "signal_id": signal["signal_id"],
        "source_signal": signal,
        "route": route,
        "standard_code": "",
        "standard_name": "",
        "item_code": "",
        "official_item_code": "",
        "standard_item_name": "",
        "display_item_name": signal.get("source_name", ""),
        "selected_unit": "",
        "feature_values": {},
        "feature_fill_candidates": [],
        "quantity_status": "blocked_no_standard_candidate",
        "suggested_quantity": "",
        "quantity_source": "none",
        "quantity_block_reason": "no_standard_candidate_retrieved",
        "split_required": False,
        "split_basis": "",
        "candidate_score": 0,
        "confidence": 0,
        "matched_fields": [],
        "match_reasons": [],
        "pricing_rule_refs": [],
        "needs_human_review": True,
        "manual_review_reasons": ["no_standard_candidate_retrieved"],
    }


def _iter_field_report_rows(field_report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for key in (
        "material_method_rows",
        "drawing_annotation_rows",
        "table_rows",
        "quantity_candidates",
        "standard_item_candidates",
    ):
        value = field_report.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, Mapping))
    return rows


def _route_scenario(text: str, standard_codes: list[str]) -> str:
    if "GBT50856-2024" in standard_codes:
        if any(keyword in text for keyword in ("配电", "配管", "配线", "插座", "灯具", "开关", "电缆")):
            return "installation_electrical"
        if any(keyword in text for keyword in ("给水", "排水", "地漏", "水表", "阀门", "洁具", "管道")):
            return "installation_plumbing"
        return "installation_general"
    if any(keyword in text for keyword in ("地面", "地砖", "瓷砖", "楼地面", "块料")):
        return "decoration_floor"
    if any(keyword in text for keyword in ("墙", "墙面", "墙砖")):
        return "decoration_wall"
    if any(keyword in text for keyword in ("吊顶", "天棚")):
        return "decoration_ceiling"
    return "building_decoration_general"


def _drawing_route_for_scenario(scenario: str) -> dict[str, Any]:
    routes = {
        "decoration_floor": {
            "primary_drawings": ["建筑平面图", "地面铺装图"],
            "core_evidence": ["房间/区域闭合边界", "地面材料文字", "材料编号", "柱墙占用范围"],
        },
        "decoration_wall": {
            "primary_drawings": ["建筑平面图", "墙面装饰图", "立面图"],
            "core_evidence": ["净周长", "墙高", "门窗洞口", "墙面材料分区"],
        },
        "decoration_ceiling": {
            "primary_drawings": ["吊顶平面图", "节点/大样图"],
            "core_evidence": ["吊顶闭合区域", "灯槽/跌级边界", "展开面积节点"],
        },
        "installation_electrical": {
            "primary_drawings": ["电气平面图", "系统图", "图例/设备表"],
            "core_evidence": ["设备符号", "回路/管线", "图例", "设备编号和数量"],
        },
        "installation_plumbing": {
            "primary_drawings": ["给排水平面图", "系统图", "设备表/图例"],
            "core_evidence": ["管线", "洁具/附件符号", "设备表", "数量或长度证据"],
        },
    }
    return routes.get(
        scenario,
        {
            "primary_drawings": ["相关专业平面图", "图例/说明", "大样图"],
            "core_evidence": ["标准库候选", "图纸文字", "尺寸/数量证据"],
        },
    )


def _route_reason(text: str, standard_codes: list[str], scenario: str) -> str:
    if standard_codes == ["GBT50856-2024"]:
        return f"安装工程关键词命中，进入 {scenario}，优先使用 GB/T 50856。"
    if standard_codes == ["GBT50854-2024"]:
        return f"建筑装饰关键词命中，进入 {scenario}，优先使用 GB/T 50854。"
    return f"存在跨专业或弱信号，按 {scenario} 同时召回相关标准库。"


def _feature_fill_candidates(signal: Mapping[str, Any], item: Any) -> list[dict[str, Any]]:
    if item is None:
        return []
    source_text = _clean_text(signal.get("evidence_text"))
    spec_text = _clean_text(signal.get("source_spec_or_method"))
    features: list[dict[str, Any]] = []
    for field in item.feature_fields:
        name = _clean_text(field.get("name"))
        value = _feature_value_for_name(name, source_text, spec_text)
        features.append(
            {
                "field_name": name,
                "candidate_value": value,
                "status": "candidate_from_drawing_text" if value else "missing_needs_manual_review",
                "confidence": 0.72 if value else 0.0,
                "evidence_text": source_text if value else "",
            }
        )
    return features


def _feature_value_for_name(name: str, source_text: str, spec_text: str) -> str:
    if not name:
        return ""
    if any(keyword in name for keyword in ("品种", "规格", "材质", "材料", "型号")):
        return spec_text or source_text
    if "厚" in name and spec_text:
        return spec_text
    return ""


def _feature_values_from_candidates(candidates: list[dict[str, Any]]) -> dict[str, str]:
    return {
        item["field_name"]: item["candidate_value"]
        for item in candidates
        if item.get("candidate_value")
    }


def _select_unit(signal: Mapping[str, Any], selected: Mapping[str, Any], override: Mapping[str, Any]) -> str:
    override_unit = _clean_text(override.get("selected_unit"))
    if override_unit:
        return override_unit
    signal_unit = _clean_text(signal.get("quantity_unit"))
    unit_options = list(selected.get("unit_options") or [])
    if signal_unit and signal_unit in unit_options:
        return signal_unit
    return _clean_text(unit_options[0] if unit_options else "")


def _split_required(signal: Mapping[str, Any], selected: Mapping[str, Any]) -> bool:
    text = _clean_text(signal.get("evidence_text"))
    standard_name = _clean_text(selected.get("item_name"))
    if any(keyword in text for keyword in ("地漏", "洁具", "供货", "安装", "拆除")) and standard_name not in text:
        return True
    return "附件" in standard_name and standard_name not in text


def _display_item_name(
    signal: Mapping[str, Any],
    selected: Mapping[str, Any],
    override: Mapping[str, Any],
    split_required: bool,
) -> str:
    override_name = _clean_text(override.get("display_item_name"))
    if override_name:
        return override_name
    source_name = _clean_text(signal.get("source_name"))
    if split_required and source_name:
        return source_name
    return _clean_text(selected.get("item_name"))


def _decision_confidence(selected: Mapping[str, Any], override: Mapping[str, Any]) -> float:
    override_confidence = _coerce_float(override.get("confidence"), default=-1)
    if override_confidence >= 0:
        return max(0.0, min(1.0, override_confidence))
    score = _coerce_float(selected.get("score"), default=0.0)
    return round(max(0.0, min(0.99, score / 20.0)), 3)


def _quantity_evidence_summary(quantity: Any, quantity_source: str, signal: Mapping[str, Any]) -> str:
    if _parse_decimal(quantity) is None:
        return "no_direct_quantity_evidence"
    source = quantity_source or signal.get("evidence_source") or "unknown"
    return f"{source}:{_format_quantity(quantity)}"


def _manual_review_reasons(decision: Mapping[str, Any], feature_missing: bool) -> list[str]:
    reasons: list[str] = []
    hard = decision.get("hard_validation") or {}
    if hard.get("status") != "passed":
        reasons.extend(hard.get("issues") or [])
    if feature_missing:
        reasons.append("feature_fields_need_manual_completion")
    if decision.get("split_required"):
        reasons.append("fine_subitem_needs_business_confirmation")
    if decision.get("quantity_status") != "direct_quantity_candidate_needs_manual_review":
        reasons.append("quantity_needs_manual_measurement")
    if decision.get("confidence", 0) < 0.55:
        reasons.append("low_confidence")
    return reasons


def _build_stage_results(
    index: LoadedStandardLibraryIndex,
    signals: list[dict[str, Any]],
    candidate_groups: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    candidate_count = sum(group.get("candidate_count", 0) for group in candidate_groups)
    hard_failed = sum(1 for item in decisions if (item.get("hard_validation") or {}).get("status") != "passed")
    quantity_ready = sum(1 for item in decisions if item.get("suggested_quantity"))
    stage_status = {
        "R0": ("completed", f"loaded {len(index.quantity_libraries)} quantity standards and {len(index.pricing_rule_libraries)} pricing rule standards"),
        "R1": ("completed" if signals else "blocked", f"normalized {len(signals)} evidence signals"),
        "R2": ("completed" if signals else "blocked", "routed each signal to drawing scenario and standard scope"),
        "R3": ("completed" if candidate_count else "blocked", f"retrieved {candidate_count} standard candidates"),
        "R4": ("completed", "LLM JSON contract prepared; deterministic fallback decision generated when no LLM response is supplied"),
        "R5": ("completed" if hard_failed == 0 else "blocked", f"hard validation failed rows: {hard_failed}"),
        "R6": ("partial" if quantity_ready else "blocked", f"quantity evidence ready rows: {quantity_ready}; missing rows stay pending"),
        "R7": ("pending_manual", "manual confirmation pack generated"),
        "R8": ("blocked_until_manual_confirmation", "four-field Excel export requires adopted and validated confirmation rows"),
        "R9": ("ready", "feedback hooks prepared for human corrections and rejected candidates"),
    }
    return [
        {"stage": stage, "name": name, "status": stage_status[stage][0], "message": stage_status[stage][1]}
        for stage, name in STAGE_DEFINITIONS
    ]


def _feedback_hooks(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "hook": "manual_correction_capture",
            "signal_id": item["signal_id"],
            "candidate_key": item["candidate_key"],
            "standard_code": item.get("standard_code", ""),
            "item_code": item.get("item_code", ""),
            "expected_feedback": ["accepted", "changed_standard_item", "changed_feature", "changed_quantity", "rejected"],
        }
        for item in decisions
    ]


def _validation_summary(decisions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "hard_validation_passed_count": sum(1 for item in decisions if (item.get("hard_validation") or {}).get("status") == "passed"),
        "hard_validation_failed_count": sum(1 for item in decisions if (item.get("hard_validation") or {}).get("status") != "passed"),
        "needs_human_review_count": sum(1 for item in decisions if item.get("needs_human_review")),
    }


def _quantity_summary(decisions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "quantity_direct_candidate_count": sum(1 for item in decisions if item.get("suggested_quantity")),
        "quantity_missing_count": sum(1 for item in decisions if not item.get("suggested_quantity")),
    }


def _normalize_llm_decisions(
    decisions: Mapping[str, Any] | list[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if not decisions:
        return {}
    if isinstance(decisions, Mapping):
        if "signal_id" in decisions:
            return {_clean_text(decisions.get("signal_id")): decisions}
        return {
            _clean_text(key): value
            for key, value in decisions.items()
            if isinstance(value, Mapping) and _clean_text(key)
        }
    result: dict[str, Mapping[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, Mapping):
            continue
        signal_id = _clean_text(item.get("signal_id"))
        if signal_id:
            result[signal_id] = item
    return result


def _decisions_from_report_or_list(report_or_decisions: Mapping[str, Any] | list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(report_or_decisions, Mapping):
        return list(report_or_decisions.get("itemization_decisions") or [])
    return list(report_or_decisions)


def _decision_csv_rows(decisions: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in decisions:
        hard = item.get("hard_validation") or {}
        signal = item.get("source_signal") or {}
        rows.append(
            {
                "candidate_key": item.get("candidate_key", ""),
                "signal_id": item.get("signal_id", ""),
                "source_name": signal.get("source_name", ""),
                "standard_code": item.get("standard_code", ""),
                "item_code": item.get("item_code", ""),
                "official_item_code": item.get("official_item_code", ""),
                "standard_item_name": item.get("standard_item_name", ""),
                "display_item_name": item.get("display_item_name", ""),
                "selected_unit": item.get("selected_unit", ""),
                "quantity_status": item.get("quantity_status", ""),
                "suggested_quantity": item.get("suggested_quantity", ""),
                "confidence": item.get("confidence", ""),
                "hard_validation_status": hard.get("status", ""),
                "hard_validation_issues": ";".join(hard.get("issues") or []),
                "needs_human_review": item.get("needs_human_review", ""),
                "manual_review_reasons": ";".join(item.get("manual_review_reasons") or []),
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _join_text(*values: Any) -> str:
    return " ".join(text for text in (_clean_text(value) for value in values) if text)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_decimal(value: Any) -> Decimal | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _format_quantity(value: Any) -> str:
    parsed = _parse_decimal(value)
    if parsed is None:
        return ""
    return format(parsed.normalize(), "f")


def _md(value: Any) -> str:
    text = _clean_text(value).replace("|", "\\|").replace("\n", " ")
    return text or "-"
