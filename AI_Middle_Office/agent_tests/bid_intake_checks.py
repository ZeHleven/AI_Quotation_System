from __future__ import annotations

from copy import deepcopy
import json

import pytest
from langchain_core.messages import AIMessage

from app.agents.bid_intake.contracts import (
    AssessmentDraft,
    DocumentManifest,
    GateStatus,
    HumanDecision,
)
from app.agents.bid_intake.evidence_gate import evaluate_evidence_gate
from app.agents.bid_intake.fake_adapters import (
    FakeTenderEvidenceClient,
    ScriptedBidAnalysisModel,
    build_demo_draft,
    build_demo_evidence,
    build_demo_manifest,
    build_demo_script,
)
from app.agents.bid_intake.graph import (
    _normalize_draft_payload,
    build_bid_intake_agent,
    build_initial_state,
)
from app.agents.bid_intake.policy import InMemoryBidPolicy
from app.agents.bid_intake.ports import AgentBudgets, AgentRuntime


def _runtime(
    *,
    model: ScriptedBidAnalysisModel | None = None,
    budgets: AgentBudgets | None = None,
) -> tuple[AgentRuntime, FakeTenderEvidenceClient]:
    manifest = build_demo_manifest()
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(case_id=manifest.case_id, records=records)
    runtime = AgentRuntime(
        model=model or build_demo_script(records),
        evidence=evidence,
        policy=InMemoryBidPolicy(),
        budgets=budgets or AgentBudgets(),
    )
    return runtime, evidence


def _initial_state():
    return build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-TEST-001",
        agent_run_id="RUN-TEST-001",
    )


def test_demo_graph_calls_tools_passes_gate_and_resumes_human_approval():
    runtime, evidence = _runtime()
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-TEST-001"

    first = agent.start(_initial_state(), thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)

    assert "__interrupt__" in first
    assert snapshot["tool_call_count"] == 5
    assert snapshot["reasoning_loop_count"] == 5
    assert snapshot["gate_result"]["status"] == "passed"
    assert evidence.read_evidence_ids == {"EV-001", "EV-002"}
    assert {call["tool"] for call in evidence.calls} >= {
        "search_tender_evidence",
        "read_evidence_context",
        "compare_document_versions",
        "validate_evidence_refs",
    }

    final = agent.resume(
        HumanDecision(
            decision_id="DECISION-TEST-001",
            action="approved",
            report_version=1,
            manifest_version=1,
            decided_by="general-manager",
        ),
        thread_id=thread_id,
    )

    assert final["phase"] == "approved"
    assert final["human_decision"]["decision_id"] == "DECISION-TEST-001"


def test_high_risk_claim_requires_a_traced_context_read():
    manifest = build_demo_manifest()
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(case_id=manifest.case_id, records=records)
    draft = build_demo_draft(records)
    policy = InMemoryBidPolicy().evaluate(draft=draft, manifest=manifest)

    result = evaluate_evidence_gate(
        draft=draft,
        manifest=manifest,
        policy=policy,
        evidence=evidence,
        repair_count=0,
        max_repairs=1,
        termination_reason="analysis_complete",
    )

    assert result.status == GateStatus.REPAIR_REQUIRED
    assert {issue.code for issue in result.issues} == {
        "HIGH_RISK_CONTEXT_NOT_READ",
        "POLICY_FACTOR_CONTEXT_NOT_READ",
    }


def test_missing_required_document_routes_to_supplement():
    manifest_data = build_demo_manifest().model_dump(mode="json")
    manifest_data["documents"][0]["document_type"] = "tender_notice"
    manifest = DocumentManifest.model_validate(manifest_data)
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(case_id=manifest.case_id, records=records)
    evidence.read_evidence_ids.update(records)
    draft = build_demo_draft(records)
    policy_engine = InMemoryBidPolicy()
    policy = policy_engine.evaluate(draft=draft, manifest=manifest)

    result = evaluate_evidence_gate(
        draft=draft,
        manifest=manifest,
        policy=policy,
        evidence=evidence,
        repair_count=0,
        max_repairs=1,
        termination_reason="analysis_complete",
    )

    assert result.status == GateStatus.SUPPLEMENT_REQUIRED
    assert "REQUIRED_MATERIAL_MISSING" in {issue.code for issue in result.issues}


def test_tool_budget_reserves_a_forced_final_turn_and_keeps_real_draft():
    records = build_demo_evidence()
    first_turn = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_tender_evidence",
                "args": {"query": "项目 资格 工期 付款 保证金", "top_k": 5},
                "id": "call-search-main",
                "type": "tool_call",
            },
            {
                "name": "search_tender_evidence",
                "args": {"query": "额外检索一", "top_k": 5},
                "id": "call-search-trimmed-1",
                "type": "tool_call",
            },
            {
                "name": "search_tender_evidence",
                "args": {"query": "额外检索二", "top_k": 5},
                "id": "call-search-trimmed-2",
                "type": "tool_call",
            },
        ],
    )
    second_turn = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_evidence_context",
                "args": {"evidence_id": "EV-001"},
                "id": "call-read-1",
                "type": "tool_call",
            },
            {
                "name": "read_evidence_context",
                "args": {"evidence_id": "EV-002"},
                "id": "call-read-trimmed",
                "type": "tool_call",
            },
        ],
    )
    forced_draft = build_demo_draft(records).model_copy(
        update={"termination_reason": "analysis_complete"}
    )
    model = ScriptedBidAnalysisModel(
        responses=[first_turn, second_turn],
        forced_response=AIMessage(
            content=json.dumps(
                forced_draft.model_dump(mode="json"),
                ensure_ascii=False,
            )
        ),
    )
    runtime, evidence = _runtime(
        model=model,
        budgets=AgentBudgets(
            max_reasoning_loops=8,
            max_tool_calls=2,
            max_tool_calls_per_turn=2,
        ),
    )
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-FORCED-FINAL"
    state = build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-FORCED-FINAL",
        agent_run_id="RUN-FORCED-FINAL",
    )

    first = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)

    assert "__interrupt__" in first
    assert snapshot["tool_call_count"] == 2
    assert snapshot["termination_reason"] == "tool_budget_forced_finalize"
    assert (
        snapshot["assessment_draft"]["termination_reason"]
        == "tool_budget_forced_finalize"
    )
    assert len(snapshot["assessment_draft"]["project_facts"]) == 2
    assert len(snapshot["assessment_draft"]["key_findings"]) == 1
    assert len(snapshot["assessment_draft"]["risks"]) == 1
    assert snapshot["gate_result"]["status"] == "manual_review_required"
    assert "AGENT_TERMINATED_EARLY" in {
        item["code"] for item in snapshot["gate_result"]["issues"]
    }
    assert len(evidence.calls) >= 2
    assert all(
        call["tool"] != "search_tender_evidence"
        or not str(call.get("query") or "").startswith("额外检索")
        for call in evidence.calls
    )
    assert any(
        item.get("force_final_response")
        for item in model.state_views
    )


def test_adaptive_tool_budget_uses_search_read_gap_cadence():
    records = build_demo_evidence()
    draft = build_demo_draft(records)
    model = ScriptedBidAnalysisModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_tender_evidence",
                        "args": {"query": "主检索", "top_k": 5},
                        "id": "call-main-search",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_tender_evidence",
                        "args": {"query": "首轮冗余一", "top_k": 5},
                        "id": "call-initial-trimmed-1",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_tender_evidence",
                        "args": {"query": "首轮冗余二", "top_k": 5},
                        "id": "call-initial-trimmed-2",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_evidence_context",
                        "args": {"evidence_id": "EV-001"},
                        "id": "call-read-1",
                        "type": "tool_call",
                    },
                    {
                        "name": "read_evidence_context",
                        "args": {"evidence_id": "EV-002"},
                        "id": "call-read-2",
                        "type": "tool_call",
                    },
                    {
                        "name": "read_evidence_context",
                        "args": {"evidence_id": "EV-001"},
                        "id": "call-read-trimmed",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_tender_evidence",
                        "args": {"query": "定向补查", "top_k": 5},
                        "id": "call-gap-search",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_tender_evidence",
                        "args": {"query": "补查冗余一", "top_k": 5},
                        "id": "call-gap-trimmed-1",
                        "type": "tool_call",
                    },
                    {
                        "name": "search_tender_evidence",
                        "args": {"query": "补查冗余二", "top_k": 5},
                        "id": "call-gap-trimmed-2",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(
                content=json.dumps(
                    draft.model_dump(mode="json"),
                    ensure_ascii=False,
                )
            ),
        ]
    )
    runtime, evidence = _runtime(model=model)
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-ADAPTIVE-BUDGET"
    state = build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-ADAPTIVE-BUDGET",
        agent_run_id="RUN-ADAPTIVE-BUDGET",
    )

    first = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)
    runtime_budgets = [
        item["runtime_budget"]
        for item in model.state_views[:4]
    ]

    assert "__interrupt__" in first
    assert snapshot["tool_call_count"] == 4
    assert [item["adaptive_tool_phase"] for item in runtime_budgets] == [
        "initial_search",
        "evidence_read",
        "gap_check",
        "evidence_read",
    ]
    assert [item["max_tool_calls_per_turn"] for item in runtime_budgets] == [
        1,
        2,
        1,
        2,
    ]
    assert [
        call.get("query")
        for call in evidence.calls
        if call["tool"] == "search_tender_evidence"
    ] == ["主检索", "定向补查"]
    assert evidence.read_evidence_ids == {"EV-001", "EV-002"}


def test_duplicate_tool_calls_stop_before_third_execution():
    repeated_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_tender_evidence",
                "args": {"query": "资格", "top_k": 5},
                "id": "call-search",
                "type": "tool_call",
            }
        ],
    )
    model = ScriptedBidAnalysisModel(
        responses=[deepcopy(repeated_call), deepcopy(repeated_call), deepcopy(repeated_call)]
    )
    runtime, evidence = _runtime(
        model=model,
        budgets=AgentBudgets(max_same_tool_args=2),
    )
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-DUPLICATE"
    state = build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-DUPLICATE",
        agent_run_id="RUN-DUPLICATE",
    )

    first = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)

    assert "__interrupt__" in first
    assert snapshot["termination_reason"] == "duplicate_tool_call_budget_exhausted"
    assert snapshot["gate_result"]["status"] == "manual_review_required"
    assert len([call for call in evidence.calls if call["tool"] == "search_tender_evidence"]) == 2


def test_evidence_gate_repairs_only_once_before_manual_review():
    records = build_demo_evidence()
    draft_json = json.dumps(build_demo_draft(records).model_dump(mode="json"), ensure_ascii=False)
    model = ScriptedBidAnalysisModel(
        responses=[
            AIMessage(content=draft_json),
            AIMessage(content=draft_json),
        ]
    )
    runtime, _ = _runtime(model=model)
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-REPAIR"
    state = build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-REPAIR",
        agent_run_id="RUN-REPAIR",
    )

    first = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)

    assert "__interrupt__" in first
    assert snapshot["repair_count"] == 1
    assert snapshot["gate_result"]["status"] == "manual_review_required"
    assert {issue["code"] for issue in snapshot["gate_result"]["issues"]} == {
        "HIGH_RISK_CONTEXT_NOT_READ",
        "POLICY_FACTOR_CONTEXT_NOT_READ",
    }


def test_invalid_structured_output_is_repaired_once():
    records = build_demo_evidence()
    valid_json = json.dumps(
        build_demo_draft(records).model_copy(
            update={
                "termination_reason": (
                    "模型自述：已经完成业务分析。"
                )
            }
        ).model_dump(mode="json"),
        ensure_ascii=False,
    )
    model = ScriptedBidAnalysisModel(
        responses=[
            AIMessage(content="```json\n{\"wrong\":true}\n```"),
            AIMessage(content=f"修复结果：\n```json\n{valid_json}\n```"),
        ]
    )
    runtime, evidence = _runtime(model=model)
    evidence.read_evidence_ids.update(records)
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-OUTPUT-REPAIR"
    state = build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-OUTPUT-REPAIR",
        agent_run_id="RUN-OUTPUT-REPAIR",
    )

    first = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)

    assert "__interrupt__" in first
    assert snapshot["output_repair_count"] == 1
    assert snapshot["assessment_draft"]["project_summary"]
    assert snapshot["termination_reason"] is None
    assert snapshot["gate_result"]["status"] == "passed"
    assert "AGENT_TERMINATED_EARLY" not in {
        item["code"] for item in snapshot["gate_result"]["issues"]
    }


def test_unknown_policy_factor_source_is_normalized_at_model_edge():
    payload = {
        "policy_factors": [
            {
                "factor_id": "qualification_fit",
                "rating": "unknown",
                "source_type": "tender_evidence",
                "source_note": "模型错误地保留了证据来源。",
            },
            {
                "factor_id": "payment_cashflow",
                "rating": "critical",
                "source_type": "tender_evidence",
            },
        ]
    }

    normalized = _normalize_draft_payload(payload)

    assert normalized["policy_factors"][0]["source_type"] == "unknown"
    assert normalized["policy_factors"][0]["source_note"] is None
    assert (
        normalized["policy_factors"][1]["source_type"]
        == "tender_evidence"
    )


def test_unknown_tool_is_rejected_without_execution():
    model = ScriptedBidAnalysisModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_shell",
                        "args": {"command": "whoami"},
                        "id": "call-unsafe",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    runtime, evidence = _runtime(model=model)
    agent = build_bid_intake_agent(runtime)
    thread_id = "bid-assessment:ASSESSMENT-UNSAFE"
    state = build_initial_state(
        manifest=build_demo_manifest(),
        assessment_id="ASSESSMENT-UNSAFE",
        agent_run_id="RUN-UNSAFE",
    )

    first = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)

    assert "__interrupt__" in first
    assert snapshot["termination_reason"] == "tool_not_allowed"
    assert {error["code"] for error in snapshot["errors"]} == {"TOOL_NOT_ALLOWED"}
    assert all(call["tool"] != "run_shell" for call in evidence.calls)


def test_assessment_contract_rejects_duplicate_dimensions():
    draft = build_demo_draft(build_demo_evidence()).model_dump(mode="json")
    draft["dimension_reviews"].append(deepcopy(draft["dimension_reviews"][0]))

    with pytest.raises(ValueError, match="duplicate dimensions"):
        AssessmentDraft.model_validate(draft)
