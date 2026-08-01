from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from .contracts import (
    AgentVersions,
    AssessmentDraft,
    DocumentManifest,
    FactCoverageMode,
    FactCoverageState,
    GateResult,
    GateStatus,
    HumanAction,
    HumanDecision,
    PolicyEvaluation,
    REQUIRED_DIMENSIONS,
)
from .evidence_gate import evaluate_evidence_gate
from .fact_coverage import build_fact_coverage_state
from .fake_adapters import build_manual_review_draft
from .ports import AgentRuntime
from .prompts import build_system_prompt
from .state import AssessmentState
from .tools import build_react_tools


ALLOWED_TOOLS = {
    "search_tender_evidence",
    "read_evidence_context",
    "compare_document_versions",
    "get_bid_policy_rule",
}

EVIDENCE_APPROVAL_BLOCKERS = {
    "REQUIRED_DIMENSION_MISSING",
    "EVIDENCE_VALIDATION_UNAVAILABLE",
    "EVIDENCE_REF_INVALID",
    "HIGH_RISK_EVIDENCE_MISSING",
    "HIGH_RISK_CONTEXT_NOT_READ",
    "POLICY_FACTOR_EVIDENCE_MISSING",
    "POLICY_FACTOR_CONTEXT_NOT_READ",
    "POLICY_REQUIRES_MANUAL_REVIEW",
    "AGENT_TERMINATED_EARLY",
    "FACT_SLOT_EVIDENCE_INSUFFICIENT",
}


@dataclass
class BidIntakeAgent:
    graph: Any
    runtime: AgentRuntime

    def start(self, state: AssessmentState, *, thread_id: str) -> dict[str, Any]:
        return self.graph.invoke(
            state,
            config={"configurable": {"thread_id": thread_id}},
        )

    def resume(self, decision: HumanDecision | dict[str, Any], *, thread_id: str) -> dict[str, Any]:
        payload = decision.model_dump(mode="json") if isinstance(decision, HumanDecision) else decision
        return self.graph.invoke(
            Command(resume=payload),
            config={"configurable": {"thread_id": thread_id}},
        )

    def continue_run(self, *, thread_id: str) -> dict[str, Any]:
        """Continue an unfinished checkpoint after a worker/process restart."""

        return self.graph.invoke(
            None,
            config={"configurable": {"thread_id": thread_id}},
        )

    def stream_start(
        self,
        state: AssessmentState,
        *,
        thread_id: str,
    ):
        return self._stream(state, thread_id=thread_id)

    def stream_resume(
        self,
        decision: HumanDecision | dict[str, Any],
        *,
        thread_id: str,
    ):
        payload = (
            decision.model_dump(mode="json")
            if isinstance(decision, HumanDecision)
            else decision
        )
        return self._stream(
            Command(resume=payload),
            thread_id=thread_id,
        )

    def stream_continue(self, *, thread_id: str):
        """Continue an unfinished checkpoint while emitting task events."""

        return self._stream(None, thread_id=thread_id)

    def _stream(self, value: Any, *, thread_id: str):
        return self.graph.stream(
            value,
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="tasks",
            version="v2",
        )

    def snapshot(self, *, thread_id: str) -> dict[str, Any]:
        return dict(
            self.graph.get_state(
                {"configurable": {"thread_id": thread_id}}
            ).values
        )

    def graph_snapshot(self, *, thread_id: str):
        return self.graph.get_state(
            {"configurable": {"thread_id": thread_id}}
        )


@dataclass(frozen=True)
class AdaptiveToolBudget:
    phase: str
    limit: int
    reason: str
    preferred_tools: tuple[str, ...] = ()


def build_initial_state(
    *,
    manifest: DocumentManifest,
    assessment_id: str,
    agent_run_id: str,
    analysis_goal: str = "判断该招标项目是否值得进入报价立项。",
    versions: AgentVersions | None = None,
) -> AssessmentState:
    bound_versions = versions or AgentVersions()
    return AssessmentState(
        case_id=manifest.case_id,
        assessment_id=assessment_id,
        agent_run_id=agent_run_id,
        phase="preparing",
        manifest=manifest.model_dump(mode="json"),
        analysis_goal=analysis_goal,
        required_dimensions=list(REQUIRED_DIMENSIONS),
        messages=[],
        assessment_draft=None,
        report_version=1,
        evidence_refs=[],
        policy_evaluation=None,
        gate_result=None,
        fact_coverage=FactCoverageState(
            mode=FactCoverageMode.SHADOW,
        ).model_dump(mode="json"),
        repair_count=0,
        human_decision=None,
        reasoning_loop_count=0,
        tool_call_count=0,
        tool_signature_counts={},
        output_repair_count=0,
        output_validation_error=None,
        termination_reason=None,
        versions=bound_versions.model_copy(
            update={
                "model_id": bound_versions.model_id,
            }
        ).model_dump(mode="json"),
        errors=[],
    )


def build_bid_intake_agent(
    runtime: AgentRuntime,
    *,
    checkpointer: Any | None = None,
) -> BidIntakeAgent:
    tools = build_react_tools(runtime)
    tool_node = ToolNode(tools, handle_tool_errors=True)
    system_prompt = build_system_prompt(
        policy_version=runtime.policy.version,
        policy_context=runtime.policy.prompt_context,
    )

    def prepare_node(state: AssessmentState) -> dict[str, Any]:
        manifest = DocumentManifest.model_validate(state["manifest"])
        if manifest.case_id != state["case_id"]:
            raise ValueError("manifest case_id does not match Agent state")
        if not manifest.active_documents:
            raise ValueError("manifest has no active documents")
        initial_message = HumanMessage(
            content=(
                f"研判目标：{state['analysis_goal']}\n"
                f"资料清单版本：{manifest.manifest_version}\n"
                f"资料清单哈希：{manifest.manifest_hash}\n"
                "请先检索证据，再形成结构化研判结果。"
            )
        )
        versions = dict(state.get("versions") or {})
        versions["model_id"] = runtime.model.model_id
        versions["policy_version"] = runtime.policy.version
        return {
            "phase": "analyzing",
            "messages": [initial_message],
            "versions": versions,
        }

    def react_model_node(state: AssessmentState) -> dict[str, Any]:
        loops = int(state.get("reasoning_loop_count") or 0)
        used_tool_calls = int(state.get("tool_call_count") or 0)
        remaining_reasoning_loops = max(
            0,
            runtime.budgets.max_reasoning_loops - loops,
        )
        remaining_tool_calls = max(
            0,
            runtime.budgets.max_tool_calls - used_tool_calls,
        )
        adaptive_tool_budget = _adaptive_tool_budget(
            messages=state.get("messages") or [],
            used_tool_calls=used_tool_calls,
            remaining_tool_calls=remaining_tool_calls,
            hard_per_turn_limit=runtime.budgets.max_tool_calls_per_turn,
        )
        force_final_reason: str | None = None
        if remaining_tool_calls <= 0:
            force_final_reason = "tool_budget_forced_finalize"
        elif remaining_reasoning_loops <= 1:
            force_final_reason = "reasoning_budget_forced_finalize"
        try:
            response = runtime.model.invoke(
                state.get("messages") or [],
                system_prompt=system_prompt,
                state_view={
                    "case_id": state["case_id"],
                    "assessment_id": state["assessment_id"],
                    "manifest": state["manifest"],
                    "required_dimensions": state["required_dimensions"],
                    "gate_result": state.get("gate_result"),
                    "fact_coverage": (
                        state.get("fact_coverage")
                        if runtime.fact_coverage_mode
                        == FactCoverageMode.ENFORCED
                        else None
                    ),
                    "repair_count": state.get("repair_count", 0),
                    "force_final_response": bool(force_final_reason),
                    "force_final_reason": force_final_reason,
                    "runtime_budget": {
                        "remaining_reasoning_loops": (
                            remaining_reasoning_loops
                        ),
                        "remaining_tool_calls": remaining_tool_calls,
                        "max_tool_calls_per_turn": (
                            adaptive_tool_budget.limit
                        ),
                        "configured_max_tool_calls_per_turn": (
                            runtime.budgets.max_tool_calls_per_turn
                        ),
                        "max_same_tool_args": (
                            runtime.budgets.max_same_tool_args
                        ),
                        "adaptive_tool_phase": (
                            adaptive_tool_budget.phase
                        ),
                        "adaptive_tool_reason": (
                            adaptive_tool_budget.reason
                        ),
                        "preferred_tools": list(
                            adaptive_tool_budget.preferred_tools
                        ),
                    },
                },
            )
        except Exception as exc:
            fallback = build_manual_review_draft("model_invocation_failed")
            return {
                "messages": [
                    AIMessage(
                        content=json.dumps(fallback.model_dump(mode="json"), ensure_ascii=False),
                        additional_kwargs={"bid_model_turn": True},
                    )
                ],
                "reasoning_loop_count": loops + 1,
                "termination_reason": "model_invocation_failed",
                "errors": [
                    *list(state.get("errors") or []),
                    {"code": "MODEL_INVOCATION_FAILED", "message": str(exc)},
                ],
            }
        errors = list(state.get("errors") or [])
        requested_tool_call_count = len(response.tool_calls)
        if force_final_reason and response.tool_calls:
            fallback = build_manual_review_draft(
                "forced_finalization_returned_tool_calls"
            )
            response = AIMessage(
                content=json.dumps(
                    fallback.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                additional_kwargs={"bid_model_turn": True},
            )
            errors.append(
                {
                    "code": "FORCED_FINALIZATION_RETURNED_TOOL_CALLS",
                    "message": (
                        "Model requested tools during the forced finalization "
                        "turn."
                    ),
                }
            )
        elif response.tool_calls:
            selected_calls = _select_adaptive_tool_calls(
                calls=list(response.tool_calls),
                budget=adaptive_tool_budget,
            )
            if selected_calls != list(response.tool_calls):
                response = response.model_copy(
                    update={"tool_calls": selected_calls}
                )
        response = response.model_copy(
            update={
                "additional_kwargs": {
                    **dict(response.additional_kwargs or {}),
                    "bid_adaptive_tool_phase": (
                        adaptive_tool_budget.phase
                    ),
                    "bid_adaptive_tool_limit": (
                        adaptive_tool_budget.limit
                    ),
                    "bid_adaptive_tool_reason": (
                        adaptive_tool_budget.reason
                    ),
                    "bid_adaptive_preferred_tools": list(
                        adaptive_tool_budget.preferred_tools
                    ),
                    "bid_requested_tool_count": (
                        requested_tool_call_count
                    ),
                    "bid_selected_tool_count": len(
                        response.tool_calls
                    ),
                    "bid_tool_calls_trimmed": max(
                        0,
                        requested_tool_call_count
                        - len(response.tool_calls),
                    ),
                }
            }
        )
        update: dict[str, Any] = {
            "messages": [response],
            "reasoning_loop_count": loops + 1,
            "errors": errors,
        }
        if force_final_reason:
            update["termination_reason"] = force_final_reason
        return update

    def route_after_model(state: AssessmentState) -> str:
        message = _last_ai_message(state.get("messages") or [])
        if message is not None and message.tool_calls:
            return "authorize_tools"
        return "finalize_draft"

    def authorize_tools_node(state: AssessmentState) -> dict[str, Any]:
        message = _last_ai_message(state.get("messages") or [])
        if message is None or not message.tool_calls:
            return {"termination_reason": "tool_authorization_without_calls"}

        signatures = dict(state.get("tool_signature_counts") or {})
        existing_count = int(state.get("tool_call_count") or 0)
        errors = list(state.get("errors") or [])
        stop_reason: str | None = None

        if existing_count + len(message.tool_calls) > runtime.budgets.max_tool_calls:
            stop_reason = "tool_call_budget_exhausted"

        for call in message.tool_calls:
            name = str(call.get("name") or "")
            if name not in ALLOWED_TOOLS:
                stop_reason = "tool_not_allowed"
                errors.append(
                    {
                        "code": "TOOL_NOT_ALLOWED",
                        "message": f"Model requested a tool outside the allowlist: {name}",
                    }
                )
                continue
            signature = _tool_signature(name, call.get("args") or {})
            signatures[signature] = signatures.get(signature, 0) + 1
            if signatures[signature] > runtime.budgets.max_same_tool_args:
                stop_reason = "duplicate_tool_call_budget_exhausted"
                errors.append(
                    {
                        "code": "DUPLICATE_TOOL_CALL",
                        "message": f"Repeated tool call exceeded its budget: {name}",
                    }
                )

        authorized_call_count = (
            len(message.tool_calls)
            if stop_reason is None
            else 0
        )
        return {
            "tool_call_count": existing_count + authorized_call_count,
            "tool_signature_counts": signatures,
            "termination_reason": stop_reason,
            "errors": errors,
        }

    def route_after_authorization(state: AssessmentState) -> str:
        if state.get("termination_reason"):
            return "budget_finalize"
        return "tool_executor"

    def fact_coverage_node(
        state: AssessmentState,
    ) -> dict[str, Any]:
        result = build_fact_coverage_state(
            state.get("messages") or [],
            mode=runtime.fact_coverage_mode,
        )
        return {
            "phase": "fact_coverage_checked",
            "fact_coverage": result.model_dump(mode="json"),
        }

    def budget_finalize_node(state: AssessmentState) -> dict[str, Any]:
        reason = str(state.get("termination_reason") or "tool_execution_stopped")
        errors = list(state.get("errors") or [])
        messages = _messages_without_pending_tool_request(
            state.get("messages") or []
        )
        messages.append(
            HumanMessage(
                content=(
                    "运行预算或安全规则已停止后续工具调用。"
                    "请立即仅依据前面已经返回的Tool结果，输出完整的"
                    "AssessmentDraft JSON。必须保留已确认事实、判断依据、"
                    "风险和缺失资料；无法确认的内容标记为unknown或unresolved。"
                    "不得再次调用工具。"
                )
            )
        )
        try:
            response = runtime.model.invoke(
                messages,
                system_prompt=system_prompt,
                state_view={
                    "case_id": state["case_id"],
                    "assessment_id": state["assessment_id"],
                    "manifest": state["manifest"],
                    "required_dimensions": state["required_dimensions"],
                    "force_final_response": True,
                    "force_final_reason": reason,
                    "runtime_budget": {
                        "remaining_reasoning_loops": 0,
                        "remaining_tool_calls": 0,
                        "max_tool_calls_per_turn": 0,
                    },
                },
            )
            if response.tool_calls:
                raise ValueError(
                    "model requested tools during budget finalization"
                )
            parsed = _normalize_draft_payload(
                _parse_json_object(_message_text(response))
            )
            draft = AssessmentDraft.model_validate(parsed).model_copy(
                update={"termination_reason": reason}
            )
        except Exception as exc:
            fallback = build_manual_review_draft(reason)
            draft = fallback.model_copy(
                update={
                    "project_summary": (
                        "Agent已完成部分证据检索，但运行预算或安全规则"
                        "阻止了完整汇总。已产生的工具结果和运行轨迹均已保留，"
                        "请重新研判或人工复核。"
                    ),
                    "unresolved_questions": [
                        "请基于已保留的证据轨迹重新研判或进行人工复核。"
                    ],
                }
            )
            if "budget" in reason:
                errors.append(
                    {
                        "code": "FORCED_FINALIZATION_FAILED",
                        "message": str(exc)[:2000],
                    }
                )
        refs = [
            item.model_dump(mode="json")
            for item in draft.collect_evidence_refs()
        ]
        return {
            "phase": "analysis_stopped_partial",
            "assessment_draft": draft.model_dump(mode="json"),
            "evidence_refs": refs,
            "errors": errors,
        }

    def finalize_draft_node(state: AssessmentState) -> dict[str, Any]:
        message = _last_ai_message(state.get("messages") or [])
        try:
            raw = _message_text(message)
            parsed = _normalize_draft_payload(
                _parse_json_object(raw)
            )
            draft = AssessmentDraft.model_validate(parsed)
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            output_repair_count = int(
                state.get("output_repair_count") or 0
            )
            errors = [
                *list(state.get("errors") or []),
                {"code": "INVALID_MODEL_OUTPUT", "message": str(exc)},
            ]
            if output_repair_count < runtime.budgets.max_output_repairs:
                return {
                    "phase": "output_repair_required",
                    "assessment_draft": None,
                    "evidence_refs": [],
                    "output_validation_error": str(exc)[:2000],
                    "errors": errors,
                }
            draft = build_manual_review_draft("invalid_model_output")
            runtime_termination = "invalid_model_output"
        else:
            errors = list(state.get("errors") or [])
            runtime_termination = state.get("termination_reason")
            if (
                runtime_termination
                and runtime_termination != "analysis_complete"
            ):
                draft = draft.model_copy(
                    update={
                        "termination_reason": str(runtime_termination)
                    }
                )
        refs = [item.model_dump(mode="json") for item in draft.collect_evidence_refs()]
        return {
            "phase": "draft_ready",
            "assessment_draft": draft.model_dump(mode="json"),
            "evidence_refs": refs,
            "output_validation_error": None,
            "termination_reason": runtime_termination,
            "errors": errors,
        }

    def route_after_finalize(state: AssessmentState) -> str:
        if state.get("assessment_draft") is None:
            return "repair_output"
        return "evaluate_policy"

    def output_repair_node(state: AssessmentState) -> dict[str, Any]:
        feedback = {
            "instruction": (
                "上一次输出不符合AssessmentDraft。不要再调用任何工具；"
                "只依据已有Tool结果重新输出一个原始JSON对象，"
                "不得使用Markdown代码块或契约外字段。"
            ),
            "validation_error": state.get("output_validation_error"),
            "assessment_draft_json_schema": (
                AssessmentDraft.model_json_schema()
            ),
        }
        return {
            "phase": "repairing_output",
            "output_repair_count": int(
                state.get("output_repair_count") or 0
            )
            + 1,
            "messages": [
                HumanMessage(
                    content=json.dumps(feedback, ensure_ascii=False)
                )
            ],
        }

    def policy_node(state: AssessmentState) -> dict[str, Any]:
        draft = AssessmentDraft.model_validate(state["assessment_draft"])
        manifest = DocumentManifest.model_validate(state["manifest"])
        evaluation = runtime.policy.evaluate(draft=draft, manifest=manifest)
        return {
            "phase": "policy_evaluated",
            "policy_evaluation": evaluation.model_dump(mode="json"),
        }

    def gate_node(state: AssessmentState) -> dict[str, Any]:
        fact_coverage_payload = state.get("fact_coverage")
        fact_coverage = (
            FactCoverageState.model_validate(
                fact_coverage_payload
            )
            if isinstance(fact_coverage_payload, dict)
            else None
        )
        result = evaluate_evidence_gate(
            draft=AssessmentDraft.model_validate(state["assessment_draft"]),
            manifest=DocumentManifest.model_validate(state["manifest"]),
            policy=PolicyEvaluation.model_validate(state["policy_evaluation"]),
            evidence=runtime.evidence,
            repair_count=int(state.get("repair_count") or 0),
            max_repairs=runtime.budgets.max_gate_repairs,
            termination_reason=state.get("termination_reason"),
            fact_coverage=fact_coverage,
            fact_coverage_mode=runtime.fact_coverage_mode,
        )
        return {
            "phase": "gate_checked",
            "gate_result": result.model_dump(mode="json"),
        }

    def route_after_gate(state: AssessmentState) -> str:
        gate = GateResult.model_validate(state["gate_result"])
        if gate.status == GateStatus.REPAIR_REQUIRED:
            return "repair_prompt"
        return "human_review"

    def repair_prompt_node(state: AssessmentState) -> dict[str, Any]:
        gate = GateResult.model_validate(state["gate_result"])
        feedback = {
            "instruction": "证据门未通过。只修复列出的结构或证据问题，然后重新输出完整AssessmentDraft。",
            "issues": [item.model_dump(mode="json") for item in gate.issues],
        }
        return {
            "phase": "repairing",
            "repair_count": int(state.get("repair_count") or 0) + 1,
            "messages": [HumanMessage(content=json.dumps(feedback, ensure_ascii=False))],
        }

    def human_review_node(state: AssessmentState) -> dict[str, Any]:
        gate = GateResult.model_validate(state["gate_result"])
        manifest = DocumentManifest.model_validate(state["manifest"])
        review_payload = {
            "assessment_id": state["assessment_id"],
            "report_version": int(state.get("report_version") or 1),
            "manifest_version": manifest.manifest_version,
            "assessment_draft": state["assessment_draft"],
            "policy_evaluation": state["policy_evaluation"],
            "gate_result": gate.model_dump(mode="json"),
            "allowed_actions": [item.value for item in HumanAction],
        }
        raw_decision = interrupt(review_payload)
        decision = HumanDecision.model_validate(raw_decision)
        if decision.report_version != int(state.get("report_version") or 1):
            raise ValueError("STALE_REPORT")
        if decision.manifest_version != manifest.manifest_version:
            raise ValueError("STALE_MANIFEST")
        _validate_human_action(decision=decision, gate=gate)
        return {
            "phase": _phase_for_human_action(decision.action),
            "human_decision": decision.model_dump(mode="json"),
        }

    builder = StateGraph(AssessmentState)
    builder.add_node("prepare", prepare_node)
    builder.add_node("react_model", react_model_node)
    builder.add_node("authorize_tools", authorize_tools_node)
    builder.add_node("tool_executor", tool_node)
    builder.add_node("update_fact_coverage", fact_coverage_node)
    builder.add_node("budget_finalize", budget_finalize_node)
    builder.add_node("finalize_draft", finalize_draft_node)
    builder.add_node("repair_output", output_repair_node)
    builder.add_node("evaluate_policy", policy_node)
    builder.add_node("evidence_gate", gate_node)
    builder.add_node("repair_prompt", repair_prompt_node)
    builder.add_node("human_review", human_review_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "react_model")
    builder.add_conditional_edges(
        "react_model",
        route_after_model,
        {
            "authorize_tools": "authorize_tools",
            "finalize_draft": "finalize_draft",
        },
    )
    builder.add_conditional_edges(
        "authorize_tools",
        route_after_authorization,
        {
            "tool_executor": "tool_executor",
            "budget_finalize": "budget_finalize",
        },
    )
    builder.add_edge("tool_executor", "update_fact_coverage")
    builder.add_edge("update_fact_coverage", "react_model")
    builder.add_edge("budget_finalize", "evaluate_policy")
    builder.add_conditional_edges(
        "finalize_draft",
        route_after_finalize,
        {
            "repair_output": "repair_output",
            "evaluate_policy": "evaluate_policy",
        },
    )
    builder.add_edge("repair_output", "react_model")
    builder.add_edge("evaluate_policy", "evidence_gate")
    builder.add_conditional_edges(
        "evidence_gate",
        route_after_gate,
        {
            "repair_prompt": "repair_prompt",
            "human_review": "human_review",
        },
    )
    builder.add_edge("repair_prompt", "react_model")
    builder.add_edge("human_review", END)

    compiled = builder.compile(checkpointer=checkpointer or InMemorySaver())
    return BidIntakeAgent(graph=compiled, runtime=runtime)


def _last_ai_message(messages: list[BaseMessage]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def _adaptive_tool_budget(
    *,
    messages: list[BaseMessage],
    used_tool_calls: int,
    remaining_tool_calls: int,
    hard_per_turn_limit: int,
) -> AdaptiveToolBudget:
    hard_limit = max(1, int(hard_per_turn_limit))
    available = max(0, int(remaining_tool_calls))
    if available <= 0:
        return AdaptiveToolBudget(
            phase="finalize",
            limit=0,
            reason="Tool总预算已用完，应使用已有证据形成结论。",
        )

    if used_tool_calls <= 0:
        return AdaptiveToolBudget(
            phase="initial_search",
            limit=min(1, hard_limit, available),
            reason="尚无候选证据，先执行一次覆盖核心维度的主检索。",
            preferred_tools=("search_tender_evidence",),
        )

    latest_tool_names = _latest_tool_batch_names(messages)
    if "search_tender_evidence" in latest_tool_names:
        return AdaptiveToolBudget(
            phase="evidence_read",
            limit=min(2, hard_limit, available),
            reason="检索已返回候选，优先读取最多两条关键证据上下文。",
            preferred_tools=("read_evidence_context",),
        )
    if "read_evidence_context" in latest_tool_names:
        return AdaptiveToolBudget(
            phase="gap_check",
            limit=min(1, hard_limit, available),
            reason="关键上下文已读取，仅在存在明确证据缺口时补查一次。",
        )
    return AdaptiveToolBudget(
        phase="targeted_followup",
        limit=min(1, hard_limit, available),
        reason="已进入定向核验阶段，本轮只允许一个必要Tool动作。",
    )


def _latest_tool_batch_names(
    messages: list[BaseMessage],
) -> tuple[str, ...]:
    names: list[str] = []
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            names.append(str(message.name or ""))
            continue
        if names:
            break
    return tuple(reversed(names))


def _select_adaptive_tool_calls(
    *,
    calls: list[dict[str, Any]],
    budget: AdaptiveToolBudget,
) -> list[dict[str, Any]]:
    if budget.limit <= 0 or not calls:
        return []
    preferred = [
        call
        for call in calls
        if str(call.get("name") or "") in budget.preferred_tools
    ]
    candidates = preferred or calls
    return list(candidates[: budget.limit])


def _message_text(message: AIMessage | None) -> str:
    if message is None:
        raise ValueError("No AI message was produced")
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list):
        parts: list[str] = []
        for block in message.content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    raise TypeError("Unsupported AI message content")


def _messages_without_pending_tool_request(
    messages: list[BaseMessage],
) -> list[BaseMessage]:
    safe_messages = list(messages)
    if (
        safe_messages
        and isinstance(safe_messages[-1], AIMessage)
        and safe_messages[-1].tool_calls
    ):
        safe_messages.pop()
    return safe_messages


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("model output is empty")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output does not contain a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output JSON must be an object")
    return parsed


def _normalize_draft_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply only deterministic contract normalizations at the model edge."""

    normalized = dict(payload)
    raw_factors = normalized.get("policy_factors")
    if not isinstance(raw_factors, list):
        return normalized
    factors: list[Any] = []
    for raw_factor in raw_factors:
        if not isinstance(raw_factor, dict):
            factors.append(raw_factor)
            continue
        factor = dict(raw_factor)
        if factor.get("rating") == "unknown":
            factor["source_type"] = "unknown"
            factor["source_note"] = None
        factors.append(factor)
    normalized["policy_factors"] = factors
    return normalized


def _tool_signature(name: str, args: dict[str, Any]) -> str:
    return f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"


def _validate_human_action(*, decision: HumanDecision, gate: GateResult) -> None:
    if decision.action not in {HumanAction.APPROVED, HumanAction.APPROVED_WITH_CONDITIONS}:
        return
    blocking_codes = {issue.code for issue in gate.issues} & EVIDENCE_APPROVAL_BLOCKERS
    if blocking_codes:
        raise ValueError(f"APPROVAL_BLOCKED_BY_EVIDENCE_GATE:{','.join(sorted(blocking_codes))}")
    if gate.status == GateStatus.SUPPLEMENT_REQUIRED:
        raise ValueError("APPROVAL_BLOCKED_PENDING_SUPPLEMENT")


def _phase_for_human_action(action: HumanAction) -> str:
    return {
        HumanAction.APPROVED: "approved",
        HumanAction.APPROVED_WITH_CONDITIONS: "approved_with_conditions",
        HumanAction.REJECTED: "rejected",
        HumanAction.SUPPLEMENT_REQUESTED: "waiting_supplement",
        HumanAction.RESEARCH_REQUESTED: "research_requested",
    }[action]
