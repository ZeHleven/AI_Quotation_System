from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from .contracts import (
    EvidenceSufficiencyStatus,
    FactCoverageMode,
    FactCoverageState,
    FactSlotCoverage,
    FactSlotCoverageStatus,
)


FACT_COVERAGE_SCHEMA_VERSION = "bid_intake_fact_coverage_v1"


def build_fact_coverage_state(
    messages: Sequence[BaseMessage],
    *,
    mode: FactCoverageMode,
) -> FactCoverageState:
    """Build deterministic retrieval coverage state from Tool results.

    Only answer-signal coverage emitted by the retrieval service is treated
    as a candidate-covered fact. Ordinary Top K presence is intentionally not
    sufficient because a weakly related block must not become proof.
    """

    if mode == FactCoverageMode.OFF:
        return FactCoverageState(
            mode=mode,
            notes=["FACT_COVERAGE_DISABLED"],
        )

    read_evidence_ids = _context_read_evidence_ids(messages)
    slots: dict[str, dict[str, Any]] = {}
    observed_search_count = 0
    evaluated_search_count = 0
    notes: list[str] = []

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if str(message.name or "") != "search_tender_evidence":
            continue
        observed_search_count += 1
        payload = _message_payload(message)
        data = payload.get("data")
        if not isinstance(data, dict):
            _append_unique(notes, "SEARCH_RESULT_DATA_UNAVAILABLE")
            continue
        plan = data.get("query_plan")
        if not isinstance(plan, dict):
            _append_unique(notes, "SEARCH_QUERY_PLAN_UNAVAILABLE")
            continue
        uses_sufficiency_assessment = bool(
            plan.get("sufficiency_strategy")
        )
        labels = plan.get(
            (
                "sufficiency_need_queries"
                if uses_sufficiency_assessment
                else "coverage_need_queries"
            )
        )
        if not isinstance(labels, list) or not labels:
            _append_unique(
                notes,
                (
                    "SUFFICIENCY_RELATION_SHAPE_UNSUPPORTED"
                    if uses_sufficiency_assessment
                    else "SEARCH_HAS_NO_ASSESSABLE_FACT_SLOTS"
                ),
            )
            continue
        types = plan.get(
            (
                "sufficiency_need_types"
                if uses_sufficiency_assessment
                else "coverage_need_types"
            )
        )
        types = types if isinstance(types, list) else []
        evaluated_search_count += 1
        trace_id = str(payload.get("trace_id") or "").strip()
        index_to_slot_id: dict[int, str] = {}
        for index, raw_label in enumerate(labels):
            label = _normalize_label(raw_label)
            if not label:
                continue
            slot_type = _slot_type(types, index)
            slot_id = _slot_id(label=label, slot_type=slot_type)
            index_to_slot_id[index] = slot_id
            slot = slots.setdefault(
                slot_id,
                {
                    "slot_id": slot_id,
                    "label": label,
                    "slot_type": slot_type,
                    "candidate_evidence_ids": [],
                    "source_trace_ids": [],
                },
            )
            if trace_id:
                _append_unique(
                    slot["source_trace_ids"],
                    trace_id,
                )

        matches = data.get("matches")
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, dict):
                continue
            evidence_id = _evidence_id(match)
            if evidence_id:
                _apply_match_coverage(
                    slots=slots,
                    index_to_slot_id=index_to_slot_id,
                    indexes=match.get(
                        (
                            "sufficiency_need_indexes"
                            if uses_sufficiency_assessment
                            else "coverage_need_indexes"
                        )
                    ),
                    evidence_id=evidence_id,
                )
            group = match.get("context_evidence_group")
            if not isinstance(group, dict):
                continue
            members = group.get("members")
            if not isinstance(members, list):
                continue
            for member in members:
                if not isinstance(member, dict):
                    continue
                member_id = _evidence_id(member)
                if not member_id:
                    continue
                member_indexes: list[Any] = []
                member_fields = (
                    ("sufficiency_need_indexes",)
                    if uses_sufficiency_assessment
                    else (
                        "coverage_need_indexes",
                        "complementary_need_indexes",
                    )
                )
                for field in member_fields:
                    value = member.get(field)
                    if isinstance(value, list):
                        member_indexes.extend(value)
                _apply_match_coverage(
                    slots=slots,
                    index_to_slot_id=index_to_slot_id,
                    indexes=member_indexes,
                    evidence_id=member_id,
                )

    slot_models: list[FactSlotCoverage] = []
    for slot in slots.values():
        candidates = list(slot["candidate_evidence_ids"])
        verified = [
            evidence_id
            for evidence_id in candidates
            if evidence_id in read_evidence_ids
        ]
        if verified:
            status = FactSlotCoverageStatus.CONTEXT_VERIFIED
        elif candidates:
            status = FactSlotCoverageStatus.CANDIDATE_COVERED
        else:
            status = FactSlotCoverageStatus.UNCOVERED
        slot_models.append(
            FactSlotCoverage(
                slot_id=slot["slot_id"],
                label=slot["label"],
                slot_type=slot["slot_type"],
                status=status,
                candidate_evidence_ids=candidates,
                verified_evidence_ids=verified,
                source_trace_ids=list(slot["source_trace_ids"]),
            )
        )
    slot_models.sort(key=lambda item: item.slot_id)
    required_count = len(slot_models)
    covered_count = sum(
        item.status != FactSlotCoverageStatus.UNCOVERED
        for item in slot_models
    )
    verified_count = sum(
        item.status == FactSlotCoverageStatus.CONTEXT_VERIFIED
        for item in slot_models
    )
    if required_count == 0:
        sufficiency = EvidenceSufficiencyStatus.NOT_ASSESSED
        coverage_rate = None
    elif covered_count == required_count:
        sufficiency = (
            EvidenceSufficiencyStatus.CANDIDATE_SUFFICIENT
        )
        coverage_rate = 1.0
    else:
        sufficiency = EvidenceSufficiencyStatus.INSUFFICIENT
        coverage_rate = covered_count / required_count
    return FactCoverageState(
        mode=mode,
        sufficiency_status=sufficiency,
        required_slot_count=required_count,
        covered_slot_count=covered_count,
        verified_slot_count=verified_count,
        coverage_rate=coverage_rate,
        evaluated_search_count=evaluated_search_count,
        observed_search_count=observed_search_count,
        slots=slot_models,
        notes=notes,
    )


def _context_read_evidence_ids(
    messages: Sequence[BaseMessage],
) -> set[str]:
    evidence_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if str(message.name or "") != "read_evidence_context":
            continue
        payload = _message_payload(message)
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        evidence_id = _evidence_id(data)
        if evidence_id:
            evidence_ids.add(evidence_id)
    return evidence_ids


def _message_payload(message: ToolMessage) -> dict[str, Any]:
    content = message.content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _apply_match_coverage(
    *,
    slots: dict[str, dict[str, Any]],
    index_to_slot_id: dict[int, str],
    indexes: Any,
    evidence_id: str,
) -> None:
    if not isinstance(indexes, list):
        return
    for raw_index in indexes:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        slot_id = index_to_slot_id.get(index)
        if not slot_id or slot_id not in slots:
            continue
        _append_unique(
            slots[slot_id]["candidate_evidence_ids"],
            evidence_id,
        )


def _evidence_id(payload: dict[str, Any]) -> str | None:
    direct = str(payload.get("evidence_id") or "").strip()
    if direct:
        return direct
    ref = payload.get("evidence_ref")
    if not isinstance(ref, dict):
        return None
    value = str(ref.get("evidence_id") or "").strip()
    return value or None


def _normalize_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slot_type(types: list[Any], index: int) -> str:
    value = (
        str(types[index] or "").strip()
        if index < len(types)
        else ""
    )
    return value or "entity_fact"


def _slot_id(*, label: str, slot_type: str) -> str:
    fingerprint = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff]+",
        "",
        f"{slot_type}:{label}",
    ).casefold()
    digest = hashlib.sha256(
        fingerprint.encode("utf-8")
    ).hexdigest()
    return f"fact-{digest[:24]}"


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
