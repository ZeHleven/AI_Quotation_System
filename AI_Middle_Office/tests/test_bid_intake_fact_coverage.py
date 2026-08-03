from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from app.agents.bid_intake.contracts import (
    EvidenceSufficiencyStatus,
    FactCoverageMode,
    FactCoverageState,
    FactSlotCoverage,
    FactSlotCoverageStatus,
    GateStatus,
)
from app.agents.bid_intake.evidence_gate import evaluate_evidence_gate
from app.agents.bid_intake.fact_coverage import (
    build_fact_coverage_state,
)
from app.agents.bid_intake.fake_adapters import (
    FakeTenderEvidenceClient,
    build_demo_draft,
    build_demo_evidence,
    build_demo_manifest,
)
from app.agents.bid_intake.policy import InMemoryBidPolicy


def _tool_message(
    *,
    name: str,
    call_id: str,
    payload: dict,
) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=call_id,
        name=name,
    )


def _search_message(
    *,
    call_id: str,
    trace_id: str,
    covered_indexes: list[int],
    evidence_id: str,
) -> ToolMessage:
    return _tool_message(
        name="search_tender_evidence",
        call_id=call_id,
        payload={
            "status": "ok",
            "trace_id": trace_id,
            "data": {
                "query_plan": {
                    "coverage_need_queries": [
                        "投标保证金金额",
                        "投标有效期",
                    ],
                    "coverage_need_types": [
                        "amount",
                        "time",
                    ],
                },
                "matches": [
                    {
                        "coverage_need_indexes": covered_indexes,
                        "evidence_ref": {
                            "evidence_id": evidence_id,
                        },
                    }
                ],
            },
        },
    )


def _sufficiency_search_message(
    *,
    call_id: str,
    trace_id: str,
    covered_indexes: list[int],
    evidence_id: str,
) -> ToolMessage:
    return _tool_message(
        name="search_tender_evidence",
        call_id=call_id,
        payload={
            "status": "ok",
            "trace_id": trace_id,
            "data": {
                "query_plan": {
                    "sufficiency_strategy": (
                        "predicate_aware_relation_evidence_v1"
                    ),
                    "sufficiency_need_queries": [
                        "投标保证金 金额",
                        "投标保证金 退还条件",
                    ],
                    "sufficiency_need_types": [
                        "amount",
                        "condition",
                    ],
                    "coverage_need_queries": [
                        "误导性的旧表面槽",
                    ],
                    "coverage_need_types": ["entity_fact"],
                },
                "matches": [
                    {
                        "sufficiency_need_indexes": covered_indexes,
                        "coverage_need_indexes": [0],
                        "evidence_ref": {
                            "evidence_id": evidence_id,
                        },
                    }
                ],
            },
        },
    )


def test_fact_coverage_tracks_covered_and_uncovered_slots():
    state = build_fact_coverage_state(
        [
            _search_message(
                call_id="call-search-1",
                trace_id="trace-search-1",
                covered_indexes=[0],
                evidence_id="EV-BOND",
            )
        ],
        mode=FactCoverageMode.SHADOW,
    )

    assert state.sufficiency_status == (
        EvidenceSufficiencyStatus.INSUFFICIENT
    )
    assert state.required_slot_count == 2
    assert state.covered_slot_count == 1
    assert state.verified_slot_count == 0
    assert state.coverage_rate == 0.5
    assert {
        item.label: item.status for item in state.slots
    } == {
        "投标保证金金额": (
            FactSlotCoverageStatus.CANDIDATE_COVERED
        ),
        "投标有效期": FactSlotCoverageStatus.UNCOVERED,
    }


def test_fact_coverage_merges_later_search_without_triggering_it():
    state = build_fact_coverage_state(
        [
            _search_message(
                call_id="call-search-1",
                trace_id="trace-search-1",
                covered_indexes=[0],
                evidence_id="EV-BOND",
            ),
            _search_message(
                call_id="call-search-2",
                trace_id="trace-search-2",
                covered_indexes=[1],
                evidence_id="EV-VALIDITY",
            ),
            _tool_message(
                name="read_evidence_context",
                call_id="call-read-1",
                payload={
                    "status": "ok",
                    "trace_id": "trace-read-1",
                    "data": {
                        "evidence_ref": {
                            "evidence_id": "EV-VALIDITY",
                            "context_read": True,
                        }
                    },
                },
            ),
        ],
        mode=FactCoverageMode.SHADOW,
    )

    assert state.sufficiency_status == (
        EvidenceSufficiencyStatus.CANDIDATE_SUFFICIENT
    )
    assert state.covered_slot_count == 2
    assert state.verified_slot_count == 1
    assert {
        item.label: item.status for item in state.slots
    }["投标有效期"] == FactSlotCoverageStatus.CONTEXT_VERIFIED


def test_fact_coverage_does_not_treat_plain_top_k_as_sufficient():
    message = _tool_message(
        name="search_tender_evidence",
        call_id="call-search-plain",
        payload={
            "status": "ok",
            "trace_id": "trace-search-plain",
            "data": {
                "query_plan": {
                    "coverage_need_queries": [],
                    "coverage_need_types": [],
                },
                "matches": [
                    {
                        "evidence_ref": {
                            "evidence_id": "EV-WEAKLY-RELATED"
                        }
                    }
                ],
            },
        },
    )

    state = build_fact_coverage_state(
        [message],
        mode=FactCoverageMode.SHADOW,
    )

    assert state.sufficiency_status == (
        EvidenceSufficiencyStatus.NOT_ASSESSED
    )
    assert state.required_slot_count == 0
    assert "SEARCH_HAS_NO_ASSESSABLE_FACT_SLOTS" in state.notes


def test_fact_coverage_prefers_read_only_sufficiency_relations():
    state = build_fact_coverage_state(
        [
            _sufficiency_search_message(
                call_id="call-sufficiency-1",
                trace_id="trace-sufficiency-1",
                covered_indexes=[0],
                evidence_id="EV-BOND-AMOUNT",
            )
        ],
        mode=FactCoverageMode.SHADOW,
    )

    assert state.sufficiency_status == (
        EvidenceSufficiencyStatus.INSUFFICIENT
    )
    assert state.required_slot_count == 2
    assert state.covered_slot_count == 1
    assert state.coverage_rate == 0.5
    assert {item.slot_type for item in state.slots} == {
        "amount",
        "condition",
    }
    assert {
        item.label for item in state.slots
    } == {
        "投标保证金 金额",
        "投标保证金 退还条件",
    }


def test_fact_coverage_sufficiency_relations_upgrade_after_existing_search():
    state = build_fact_coverage_state(
        [
            _sufficiency_search_message(
                call_id="call-sufficiency-1",
                trace_id="trace-sufficiency-1",
                covered_indexes=[0],
                evidence_id="EV-BOND-AMOUNT",
            ),
            _sufficiency_search_message(
                call_id="call-sufficiency-2",
                trace_id="trace-sufficiency-2",
                covered_indexes=[1],
                evidence_id="EV-BOND-RETURN",
            ),
        ],
        mode=FactCoverageMode.SHADOW,
    )

    assert state.sufficiency_status == (
        EvidenceSufficiencyStatus.CANDIDATE_SUFFICIENT
    )
    assert state.required_slot_count == 2
    assert state.covered_slot_count == 2
    assert state.observed_search_count == 2
    assert state.evaluated_search_count == 2


def test_shadow_gate_records_insufficiency_without_blocking():
    manifest = build_demo_manifest()
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(
        case_id=manifest.case_id,
        records=records,
    )
    evidence.read_evidence_ids.update(records)
    draft = build_demo_draft(records)
    policy = InMemoryBidPolicy().evaluate(
        draft=draft,
        manifest=manifest,
    )
    fact_coverage = _insufficient_fact_coverage(
        mode=FactCoverageMode.SHADOW
    )

    result = evaluate_evidence_gate(
        draft=draft,
        manifest=manifest,
        policy=policy,
        evidence=evidence,
        repair_count=0,
        max_repairs=1,
        termination_reason="analysis_complete",
        fact_coverage=fact_coverage,
        fact_coverage_mode=FactCoverageMode.SHADOW,
    )

    assert result.status == GateStatus.PASSED
    assert result.fact_coverage_status == (
        EvidenceSufficiencyStatus.INSUFFICIENT
    )
    assert result.fact_coverage_rate == 0.5
    assert "FACT_SLOT_EVIDENCE_INSUFFICIENT" not in {
        item.code for item in result.issues
    }


def test_enforced_gate_blocks_uncovered_fact_slots():
    manifest = build_demo_manifest()
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(
        case_id=manifest.case_id,
        records=records,
    )
    evidence.read_evidence_ids.update(records)
    draft = build_demo_draft(records)
    policy = InMemoryBidPolicy().evaluate(
        draft=draft,
        manifest=manifest,
    )
    fact_coverage = _insufficient_fact_coverage(
        mode=FactCoverageMode.ENFORCED
    )

    result = evaluate_evidence_gate(
        draft=draft,
        manifest=manifest,
        policy=policy,
        evidence=evidence,
        repair_count=0,
        max_repairs=1,
        termination_reason="analysis_complete",
        fact_coverage=fact_coverage,
        fact_coverage_mode=FactCoverageMode.ENFORCED,
    )

    assert result.status == GateStatus.MANUAL_REVIEW_REQUIRED
    assert "FACT_SLOT_EVIDENCE_INSUFFICIENT" in {
        item.code for item in result.issues
    }


def test_enforced_gate_fails_closed_when_sufficiency_not_assessed():
    manifest = build_demo_manifest()
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(
        case_id=manifest.case_id,
        records=records,
    )
    evidence.read_evidence_ids.update(records)
    draft = build_demo_draft(records)
    policy = InMemoryBidPolicy().evaluate(
        draft=draft,
        manifest=manifest,
    )
    fact_coverage = FactCoverageState(
        mode=FactCoverageMode.ENFORCED,
        sufficiency_status=(
            EvidenceSufficiencyStatus.NOT_ASSESSED
        ),
        notes=["SUFFICIENCY_RELATION_SHAPE_UNSUPPORTED"],
    )

    result = evaluate_evidence_gate(
        draft=draft,
        manifest=manifest,
        policy=policy,
        evidence=evidence,
        repair_count=0,
        max_repairs=1,
        termination_reason="analysis_complete",
        fact_coverage=fact_coverage,
        fact_coverage_mode=FactCoverageMode.ENFORCED,
    )

    assert result.status == GateStatus.MANUAL_REVIEW_REQUIRED
    assert "FACT_SLOT_EVIDENCE_NOT_ASSESSED" in {
        item.code for item in result.issues
    }


def _insufficient_fact_coverage(
    *,
    mode: FactCoverageMode,
) -> FactCoverageState:
    return FactCoverageState(
        mode=mode,
        sufficiency_status=EvidenceSufficiencyStatus.INSUFFICIENT,
        required_slot_count=2,
        covered_slot_count=1,
        verified_slot_count=1,
        coverage_rate=0.5,
        evaluated_search_count=1,
        observed_search_count=1,
        slots=[
            FactSlotCoverage(
                slot_id="fact-covered",
                label="投标保证金金额",
                slot_type="amount",
                status=FactSlotCoverageStatus.CONTEXT_VERIFIED,
                candidate_evidence_ids=["EV-001"],
                verified_evidence_ids=["EV-001"],
            ),
            FactSlotCoverage(
                slot_id="fact-uncovered",
                label="投标有效期",
                slot_type="time",
                status=FactSlotCoverageStatus.UNCOVERED,
            ),
        ],
    )
