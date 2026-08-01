from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


TRACE_SCHEMA_VERSION = "bid-intake-agent-trace/v1"

TraceEmitter = Callable[[str, str, dict[str, Any]], None]


NODE_PRESENTATION: dict[str, tuple[str, str]] = {
    "prepare": ("preparation", "准备研判上下文"),
    "react_model": ("react", "ReAct 核心研判"),
    "authorize_tools": ("guard", "工具调用授权门"),
    "update_fact_coverage": ("state", "更新事实覆盖状态"),
    "budget_finalize": ("control", "运行预算保护"),
    "finalize_draft": ("synthesis", "形成结构化研判草稿"),
    "repair_output": ("repair", "修复输出格式"),
    "evaluate_policy": ("policy", "总经办立项标准评估"),
    "evidence_gate": ("gate", "确定性证据门"),
    "repair_prompt": ("repair", "证据问题修复"),
    "human_review": ("human", "Human-in-the-loop"),
}

TOOL_TITLES = {
    "search_tender_evidence": "检索招标证据",
    "read_evidence_context": "读取证据上下文",
    "compare_document_versions": "比较资料版本",
    "get_bid_policy_rule": "读取立项规则",
}

ADAPTIVE_TOOL_PHASE_TITLES = {
    "initial_search": "首轮主检索",
    "evidence_read": "关键证据读取",
    "gap_check": "缺口检查",
    "targeted_followup": "定向核验",
    "finalize": "证据汇总",
}


@dataclass
class _TraceStep:
    step_id: str
    parent_step_ids: list[str]
    node_name: str
    kind: str
    title: str
    iteration: int | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BidIntakeExecutionTrace:
    """Projects LangGraph task events into a safe, append-only UI trace.

    The trace intentionally exposes operational decisions, tool inputs and
    bounded observations. It never persists model chain-of-thought.
    """

    emit: TraceEmitter
    sequence: int = 0
    frontier: list[str] = field(default_factory=list)
    react_iteration: int = 0
    _task_steps: dict[str, _TraceStep] = field(default_factory=dict)
    _tool_steps: dict[str, list[_TraceStep]] = field(default_factory=dict)
    _started_at: dict[str, float] = field(default_factory=dict)

    def consume(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict) or event.get("type") != "tasks":
            return
        data = event.get("data")
        if not isinstance(data, dict):
            return
        task_id = str(data.get("id") or "").strip()
        node_name = str(data.get("name") or "").strip()
        if not task_id or not node_name:
            return
        if "input" in data:
            self._start_task(
                task_id=task_id,
                node_name=node_name,
                task_input=data.get("input"),
            )
            return
        self._finish_task(
            task_id=task_id,
            node_name=node_name,
            result=data.get("result"),
            error=data.get("error"),
            interrupts=data.get("interrupts") or [],
        )

    def _start_task(
        self,
        *,
        task_id: str,
        node_name: str,
        task_input: Any,
    ) -> None:
        if node_name == "tool_executor":
            self._start_tools(
                task_id=task_id,
                task_input=task_input,
            )
            return

        kind, base_title = NODE_PRESENTATION.get(
            node_name,
            ("control", node_name),
        )
        iteration = None
        title = base_title
        step_details: dict[str, Any] = {}
        if node_name == "react_model":
            self.react_iteration += 1
            iteration = self.react_iteration
            input_details = _llm_input_details(task_input)
            input_step = _TraceStep(
                step_id=f"llm-input:{task_id}",
                parent_step_ids=list(self.frontier),
                node_name="llm_input",
                kind="llm_input",
                title=f"组装 LLM 输入 #{iteration}",
                iteration=iteration,
                details=input_details,
            )
            self._emit_step(
                "completed",
                input_step,
                summary=_llm_input_summary(input_details),
            )
            self.frontier = [input_step.step_id]
            title = f"{base_title} #{iteration}"
            step_details = {
                "reasoning_visibility": (
                    "仅展示可审计的决策摘要，不记录模型私有思维链。"
                ),
                "input_step_id": input_step.step_id,
            }
        elif node_name == "authorize_tools":
            calls = _tool_calls_from_state(task_input)
            adaptive_budget = _adaptive_tool_budget_from_messages(
                (
                    task_input.get("messages")
                    if isinstance(task_input, dict)
                    else None
                )
            )
            step_details = {
                "requested_tool_count": len(calls),
                "requested_tools": [
                    str(call.get("name") or "") for call in calls
                ],
                "tool_calls_used_before": int(
                    (
                        task_input.get("tool_call_count")
                        if isinstance(task_input, dict)
                        else 0
                    )
                    or 0
                ),
                "checks": [
                    "工具白名单",
                    "本轮动态工具预算",
                    "总调用预算",
                    "相同参数重复调用预算",
                ],
                **adaptive_budget,
            }
        step = _TraceStep(
            step_id=f"node:{task_id}",
            parent_step_ids=list(self.frontier),
            node_name=node_name,
            kind=kind,
            title=title,
            iteration=iteration,
            details=step_details,
        )
        self._task_steps[task_id] = step
        self._started_at[step.step_id] = time.monotonic()
        self.frontier = [step.step_id]
        self._emit_step(
            "started",
            step,
            summary=_node_started_summary(node_name),
        )

    def _finish_task(
        self,
        *,
        task_id: str,
        node_name: str,
        result: Any,
        error: Any,
        interrupts: list[Any],
    ) -> None:
        if node_name == "tool_executor":
            self._finish_tools(
                task_id=task_id,
                result=result,
                error=error,
            )
            return

        step = self._task_steps.get(task_id)
        if step is None:
            kind, title = NODE_PRESENTATION.get(
                node_name,
                ("control", node_name),
            )
            step = _TraceStep(
                step_id=f"node:{task_id}",
                parent_step_ids=list(self.frontier),
                node_name=node_name,
                kind=kind,
                title=title,
                iteration=None,
            )
            self._task_steps[task_id] = step

        state = "failed" if error else "waiting" if interrupts else "completed"
        summary, details = _node_finished_summary(
            node_name=node_name,
            result=result,
            error=error,
            interrupts=interrupts,
        )
        self.frontier = [step.step_id]
        self._emit_step(
            state,
            step,
            summary=summary,
            details=details,
        )
        if node_name == "react_model" and not error:
            self._emit_react_decision(step=step, result=result)

    def _emit_react_decision(
        self,
        *,
        step: _TraceStep,
        result: Any,
    ) -> None:
        data = result if isinstance(result, dict) else {}
        calls = _tool_calls_from_messages(data.get("messages"))
        adaptive_budget = _adaptive_tool_budget_from_messages(
            data.get("messages")
        )
        if calls:
            actions = [
                {
                    "tool_name": str(call.get("name") or "unknown_tool"),
                    "purpose": _tool_input_summary(
                        str(call.get("name") or "unknown_tool"),
                        (
                            call.get("args")
                            if isinstance(call.get("args"), dict)
                            else {}
                        ),
                    ),
                }
                for call in calls
            ]
            decision_step = _TraceStep(
                step_id=f"plan:{step.step_id}",
                parent_step_ids=[step.step_id],
                node_name="react_plan",
                kind="plan",
                title=f"行动计划 #{step.iteration or '-'}",
                iteration=step.iteration,
                details={
                    "decision": "call_tools",
                    "continue_react": True,
                    "tool_count": len(calls),
                    "tool_names": [
                        str(call.get("name") or "") for call in calls
                    ],
                    "planned_actions": actions,
                    **adaptive_budget,
                },
            )
            summary = "；".join(
                str(item["purpose"]).rstrip("。")
                for item in actions
            )
            phase_title = str(
                adaptive_budget.get("adaptive_tool_phase_title") or ""
            ).strip()
            limit = adaptive_budget.get("adaptive_tool_limit")
            budget_prefix = (
                f"动态预算为“{phase_title}”（本轮上限 {limit} 个Tool）；"
                if phase_title and limit is not None
                else ""
            )
            self._emit_step(
                "completed",
                decision_step,
                summary=f"{budget_prefix}LLM 本轮行动计划：{summary}。",
            )
        else:
            forced_reason = str(
                data.get("termination_reason") or ""
            ).strip()
            decision_step = _TraceStep(
                step_id=f"loop-decision:{step.step_id}",
                parent_step_ids=[step.step_id],
                node_name="react_loop_decision",
                kind="loop",
                title=f"循环判断 #{step.iteration or '-'}",
                iteration=step.iteration,
                details={
                    "decision": (
                        "force_finalize"
                        if forced_reason
                        else "finalize_assessment"
                    ),
                    "continue_react": False,
                    "next_action": "形成结构化研判草稿",
                    "termination_reason": forced_reason or None,
                },
            )
            self._emit_step(
                "completed",
                decision_step,
                summary=(
                    "运行预算已进入保留汇总阶段，LLM不再调用工具，"
                    "正在依据已有证据形成部分研判结果。"
                    if forced_reason
                    else
                    "LLM 未再请求工具，ReAct 循环停止，"
                    "进入结构化研判草稿整理。"
                ),
            )
        self.frontier = [decision_step.step_id]

    def _start_tools(self, *, task_id: str, task_input: Any) -> None:
        calls = _tool_calls_from_state(task_input)
        parent_step_ids = list(self.frontier)
        steps: list[_TraceStep] = []
        if not calls:
            calls = [
                {
                    "id": task_id,
                    "name": "unknown_tool",
                    "args": {},
                }
            ]
        for index, call in enumerate(calls):
            tool_call_id = str(call.get("id") or f"{task_id}:{index}")
            tool_name = str(call.get("name") or "unknown_tool")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            step = _TraceStep(
                step_id=f"tool:{tool_call_id}",
                parent_step_ids=parent_step_ids,
                node_name=tool_name,
                kind="tool",
                title=TOOL_TITLES.get(tool_name, tool_name),
                iteration=self.react_iteration or None,
                details={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "input": _sanitize_value(args),
                },
            )
            steps.append(step)
            self._started_at[step.step_id] = time.monotonic()
            self._emit_step(
                "started",
                step,
                summary=_tool_input_summary(tool_name, args),
            )
        self._tool_steps[task_id] = steps
        self.frontier = [item.step_id for item in steps]

    def _finish_tools(
        self,
        *,
        task_id: str,
        result: Any,
        error: Any,
    ) -> None:
        steps = self._tool_steps.get(task_id) or []
        messages = _tool_messages_from_result(result)
        messages_by_call_id = {
            str(message.tool_call_id): message
            for message in messages
            if getattr(message, "tool_call_id", None)
        }
        observation_steps: list[str] = []
        for index, step in enumerate(steps):
            call_id = str(step.details.get("tool_call_id") or "")
            message = messages_by_call_id.get(call_id)
            if message is None and index < len(messages):
                message = messages[index]
            observation = _tool_observation(
                tool_name=str(step.details.get("tool_name") or step.node_name),
                message=message,
                error=error,
            )
            tool_state = (
                "failed"
                if observation["state"] == "failed"
                else "completed"
            )
            self._emit_step(
                tool_state,
                step,
                summary=(
                    "工具调用失败"
                    if tool_state == "failed"
                    else "工具已返回结果"
                ),
                details=observation["details"],
            )
            observation_step = _TraceStep(
                step_id=f"observation:{call_id or task_id}:{index}",
                parent_step_ids=[step.step_id],
                node_name="observation",
                kind="observation",
                title=f"Observation 回传 · {step.title}",
                iteration=step.iteration,
                details={
                    **observation["details"],
                    "returns_to_llm": True,
                },
            )
            self._emit_step(
                observation["state"],
                observation_step,
                summary=(
                    f"{observation['summary']} "
                    "结果摘要已写回 LLM 消息上下文。"
                ),
            )
            observation_steps.append(observation_step.step_id)
        if observation_steps:
            loop_step = _TraceStep(
                step_id=f"loop:{task_id}",
                parent_step_ids=observation_steps,
                node_name="react_loop_decision",
                kind="loop",
                title=f"继续 ReAct #{self.react_iteration + 1}",
                iteration=self.react_iteration,
                details={
                    "decision": "continue_react",
                    "continue_react": True,
                    "next_iteration": self.react_iteration + 1,
                    "observation_count": len(observation_steps),
                },
            )
            self._emit_step(
                "completed",
                loop_step,
                summary=(
                    f"{len(observation_steps)} 条 Observation 已返回，"
                    f"进入第 {self.react_iteration + 1} 轮 ReAct，"
                    "由 LLM 判断是否补查或形成结论。"
                ),
            )
            self.frontier = [loop_step.step_id]

    def _emit_step(
        self,
        state: str,
        step: _TraceStep,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.sequence += 1
        duration_ms = None
        if state != "started" and step.step_id in self._started_at:
            duration_ms = max(
                0,
                round(
                    (time.monotonic() - self._started_at[step.step_id])
                    * 1000
                ),
            )
        merged_details = {
            **step.details,
            **(details or {}),
        }
        payload = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "sequence": self.sequence,
            "step_id": step.step_id,
            "parent_step_ids": step.parent_step_ids,
            "node_name": step.node_name,
            "kind": step.kind,
            "title": step.title,
            "state": state,
            "summary": str(summary or "")[:1000],
            "iteration": step.iteration,
            "duration_ms": duration_ms,
            "details": _sanitize_value(merged_details),
        }
        self.emit(f"trace_step_{state}", payload["summary"], payload)


def restore_trace_position(
    payloads: Iterable[dict[str, Any]],
) -> tuple[int, list[str], int]:
    sequence = 0
    frontier: list[str] = []
    react_iteration = 0
    for payload in payloads:
        if (
            not isinstance(payload, dict)
            or payload.get("trace_schema_version") != TRACE_SCHEMA_VERSION
        ):
            continue
        current_sequence = int(payload.get("sequence") or 0)
        if current_sequence >= sequence:
            sequence = current_sequence
            step_id = str(payload.get("step_id") or "").strip()
            if step_id:
                frontier = [step_id]
        react_iteration = max(
            react_iteration,
            int(payload.get("iteration") or 0),
        )
    return sequence, frontier, react_iteration


def _llm_input_details(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    messages = (
        state.get("messages")
        if isinstance(state.get("messages"), list)
        else []
    )
    manifest = (
        state.get("manifest")
        if isinstance(state.get("manifest"), dict)
        else {}
    )
    versions = (
        state.get("versions")
        if isinstance(state.get("versions"), dict)
        else {}
    )
    gate_result = (
        state.get("gate_result")
        if isinstance(state.get("gate_result"), dict)
        else {}
    )
    observation_count = sum(
        isinstance(message, ToolMessage)
        for message in messages
    )
    llm_input = {
        "analysis_goal": _text_preview(state.get("analysis_goal"), 240),
        "manifest_version": manifest.get("manifest_version"),
        "active_document_count": len(
            manifest.get("active_documents") or []
        ),
        "required_dimensions": list(
            state.get("required_dimensions") or []
        )[:20],
        "message_count": len(messages),
        "prior_observation_count": observation_count,
        "gate_status": gate_result.get("status"),
        "reasoning_loops_completed": int(
            state.get("reasoning_loop_count") or 0
        ),
        "tool_calls_used": int(state.get("tool_call_count") or 0),
        "model_id": versions.get("model_id"),
        "policy_version": versions.get("policy_version"),
        "available_tools": list(TOOL_TITLES),
        "instruction_scope": [
            "按总经办立项标准研判",
            "先检索和读取证据，再形成结论",
            "只允许调用白名单工具",
            "最终输出结构化 AssessmentDraft",
        ],
    }
    return {
        "llm_input": llm_input,
        "message_count": llm_input["message_count"],
        "observation_count": observation_count,
        "available_tools": llm_input["available_tools"],
        "model_id": llm_input["model_id"],
    }


def _llm_input_summary(details: dict[str, Any]) -> str:
    payload = (
        details.get("llm_input")
        if isinstance(details.get("llm_input"), dict)
        else {}
    )
    return (
        f"向 LLM 提交研判目标、资料清单 v"
        f"{payload.get('manifest_version') or '-'}、"
        f"{payload.get('message_count') or 0} 条历史消息"
        f"（含 {payload.get('prior_observation_count') or 0} 条 Observation）"
        f"及 {len(payload.get('available_tools') or [])} 个可用工具。"
    )


def _node_started_summary(node_name: str) -> str:
    return {
        "prepare": "正在绑定资料清单、研判目标与运行版本。",
        "react_model": "正在根据已有证据决定下一步动作。",
        "authorize_tools": "正在检查工具白名单与调用预算。",
        "update_fact_coverage": (
            "正在记录已覆盖、未覆盖和已读取上下文的事实槽位。"
        ),
        "budget_finalize": "工具或推理预算已触发保护，正在安全收口。",
        "finalize_draft": "正在把已验证信息整理为结构化研判草稿。",
        "repair_output": "正在按结构化输出契约修复结果。",
        "evaluate_policy": "正在应用总经办立项标准。",
        "evidence_gate": "正在验证关键结论、证据引用和上下文读取记录。",
        "repair_prompt": "证据门要求修复，正在生成定向补查任务。",
        "human_review": "正在进入人工审核节点。",
    }.get(node_name, f"正在执行 {node_name}。")


def _node_finished_summary(
    *,
    node_name: str,
    result: Any,
    error: Any,
    interrupts: list[Any],
) -> tuple[str, dict[str, Any]]:
    if error:
        return (
            f"{NODE_PRESENTATION.get(node_name, ('', node_name))[1]}执行失败。",
            {"error": str(error)[:500]},
        )
    data = result if isinstance(result, dict) else {}
    if node_name == "prepare":
        return "运行上下文已准备完成，进入 ReAct 研判。", {}
    if node_name == "react_model":
        calls = _tool_calls_from_messages(data.get("messages"))
        model_route = _model_route_from_messages(data.get("messages"))
        adaptive_budget = _adaptive_tool_budget_from_messages(
            data.get("messages")
        )
        route_prefix = (
            "主模型不可用，已由备用模型接管；"
            if model_route.get("model_route") == "fallback"
            else ""
        )
        if calls:
            names = [
                TOOL_TITLES.get(
                    str(call.get("name") or ""),
                    str(call.get("name") or "未知工具"),
                )
                for call in calls
            ]
            phase_title = str(
                adaptive_budget.get("adaptive_tool_phase_title") or ""
            ).strip()
            limit = adaptive_budget.get("adaptive_tool_limit")
            budget_prefix = (
                f"动态预算“{phase_title}”上限 {limit} 个Tool；"
                if phase_title and limit is not None
                else ""
            )
            return (
                f"{route_prefix}{budget_prefix}"
                f"本轮决定调用 {len(calls)} 个工具："
                f"{'、'.join(names)}。",
                {
                    "tool_count": len(calls),
                    "tool_names": [
                        str(call.get("name") or "") for call in calls
                    ],
                    **model_route,
                    **adaptive_budget,
                },
            )
        termination = str(data.get("termination_reason") or "").strip()
        if termination:
            return (
                "工具或推理预算已到达汇总边界，"
                "正在使用已有证据生成部分研判结果。",
                {"termination_reason": termination},
            )
        return "证据收集阶段结束，转入结构化结果整理。", {}
    if node_name == "authorize_tools":
        termination = str(data.get("termination_reason") or "").strip()
        if termination:
            return (
                f"工具调用被运行预算或安全规则阻断：{termination}。",
                {"termination_reason": termination},
            )
        return "工具白名单、重复调用和总调用预算检查通过。", {}
    if node_name == "update_fact_coverage":
        coverage = data.get("fact_coverage")
        if isinstance(coverage, dict):
            return (
                "事实覆盖状态已更新："
                f"{coverage.get('covered_slot_count') or 0}/"
                f"{coverage.get('required_slot_count') or 0} "
                "个可评估事实已有候选证据。",
                {
                    "mode": coverage.get("mode"),
                    "sufficiency_status": coverage.get(
                        "sufficiency_status"
                    ),
                    "required_slot_count": coverage.get(
                        "required_slot_count"
                    ),
                    "covered_slot_count": coverage.get(
                        "covered_slot_count"
                    ),
                    "verified_slot_count": coverage.get(
                        "verified_slot_count"
                    ),
                    "coverage_rate": coverage.get("coverage_rate"),
                },
            )
        return "本轮没有形成可评估的事实覆盖状态。", {}
    if node_name == "finalize_draft":
        draft = data.get("assessment_draft")
        evidence_refs = data.get("evidence_refs") or []
        if isinstance(draft, dict):
            return (
                f"结构化研判草稿已形成，引用 {len(evidence_refs)} 条证据。",
                {
                    "recommendation": draft.get("recommendation"),
                    "confidence": draft.get("confidence"),
                    "evidence_ref_count": len(evidence_refs),
                },
            )
        return "模型输出需要按研判契约修复。", {}
    if node_name == "repair_output":
        return "输出修复指令已生成，返回 ReAct 重新整理。", {}
    if node_name == "evaluate_policy":
        evaluation = data.get("policy_evaluation")
        if isinstance(evaluation, dict):
            return (
                "总经办立项标准评估完成。",
                {
                    "decision": evaluation.get("decision"),
                    "score": evaluation.get("score"),
                    "coverage": evaluation.get("coverage"),
                    "hard_rule_hit_count": len(
                        evaluation.get("hard_rule_hits") or []
                    ),
                },
            )
        return "总经办立项标准评估完成。", {}
    if node_name == "evidence_gate":
        gate = data.get("gate_result")
        if isinstance(gate, dict):
            issues = gate.get("issues") or []
            return (
                f"证据门检查完成：{gate.get('status') or 'unknown'}，"
                f"发现 {len(issues)} 项问题。",
                {
                    "gate_status": gate.get("status"),
                    "fact_coverage_mode": gate.get(
                        "fact_coverage_mode"
                    ),
                    "fact_coverage_status": gate.get(
                        "fact_coverage_status"
                    ),
                    "fact_coverage_rate": gate.get(
                        "fact_coverage_rate"
                    ),
                    "issue_count": len(issues),
                    "issue_codes": [
                        item.get("code")
                        for item in issues
                        if isinstance(item, dict) and item.get("code")
                    ][:20],
                },
            )
        return "证据门检查完成。", {}
    if node_name == "repair_prompt":
        return "定向补查任务已生成，返回 ReAct 继续研判。", {}
    if node_name == "budget_finalize":
        draft = data.get("assessment_draft")
        refs = data.get("evidence_refs") or []
        return (
            "运行预算保护已基于既有Tool结果生成部分研判结果。",
            {
                "recommendation": (
                    draft.get("recommendation")
                    if isinstance(draft, dict)
                    else None
                ),
                "evidence_ref_count": len(refs),
            },
        )
    if node_name == "human_review":
        if interrupts:
            return (
                "Agent 已暂停，等待人工审核后从 Checkpoint 恢复。",
                {"interrupt_count": len(interrupts)},
            )
        decision = data.get("human_decision")
        return (
            "人工决定已应用，研判运行完成。",
            {
                "action": (
                    decision.get("action")
                    if isinstance(decision, dict)
                    else None
                )
            },
        )
    return f"{node_name} 执行完成。", {}


def _tool_calls_from_state(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return _tool_calls_from_messages(value.get("messages"))


def _tool_calls_from_messages(value: Any) -> list[dict[str, Any]]:
    messages = value if isinstance(value, list) else []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return [
                dict(call)
                for call in message.tool_calls
                if isinstance(call, dict)
            ]
    return []


def _model_route_from_messages(value: Any) -> dict[str, Any]:
    messages = value if isinstance(value, list) else []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        metadata = dict(message.response_metadata or {})
        route = str(metadata.get("bid_model_route") or "").strip()
        if not route:
            return {}
        result = {
            "model_route": route,
            "model_id": str(
                metadata.get("bid_model_id") or ""
            ).strip() or None,
        }
        if route == "fallback":
            result["primary_error"] = str(
                metadata.get("bid_primary_error") or "unknown"
            )[:80]
        return result
    return {}


def _adaptive_tool_budget_from_messages(
    value: Any,
) -> dict[str, Any]:
    messages = value if isinstance(value, list) else []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        metadata = dict(message.additional_kwargs or {})
        phase = str(
            metadata.get("bid_adaptive_tool_phase") or ""
        ).strip()
        if not phase:
            return {}
        limit = int(metadata.get("bid_adaptive_tool_limit") or 0)
        requested = int(
            metadata.get("bid_requested_tool_count") or 0
        )
        selected = int(
            metadata.get("bid_selected_tool_count") or 0
        )
        return {
            "adaptive_tool_phase": phase,
            "adaptive_tool_phase_title": (
                ADAPTIVE_TOOL_PHASE_TITLES.get(phase, phase)
            ),
            "adaptive_tool_limit": limit,
            "adaptive_tool_reason": str(
                metadata.get("bid_adaptive_tool_reason") or ""
            )[:300],
            "adaptive_preferred_tools": list(
                metadata.get("bid_adaptive_preferred_tools") or []
            )[:4],
            "model_requested_tool_count": requested,
            "selected_tool_count": selected,
            "trimmed_tool_count": int(
                metadata.get("bid_tool_calls_trimmed") or 0
            ),
        }
    return {}


def _tool_messages_from_result(value: Any) -> list[ToolMessage]:
    if not isinstance(value, dict):
        return []
    messages = value.get("messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, ToolMessage)]


def _tool_input_summary(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "search_tender_evidence":
        query = _text_preview(args.get("query"), 120)
        return f"检索“{query}”，最多返回 {args.get('top_k', 5)} 条证据。"
    if tool_name == "read_evidence_context":
        return (
            f"读取证据 {args.get('evidence_id') or '-'} 的权威上下文，"
            f"前后各 {args.get('before_blocks', 0)}/"
            f"{args.get('after_blocks', 0)} 个区块。"
        )
    if tool_name == "compare_document_versions":
        return f"比较文档 {args.get('document_key') or '-'} 的有效版本。"
    if tool_name == "get_bid_policy_rule":
        return f"读取“{_text_preview(args.get('topic'), 120)}”相关立项规则。"
    return f"调用工具 {tool_name}。"


def _tool_observation(
    *,
    tool_name: str,
    message: ToolMessage | None,
    error: Any,
) -> dict[str, Any]:
    if error:
        return {
            "state": "failed",
            "summary": f"工具执行异常：{_text_preview(error, 300)}",
            "details": {"error": _text_preview(error, 500)},
        }
    if message is None:
        return {
            "state": "failed",
            "summary": "工具没有返回可识别的 Observation。",
            "details": {"result_status": "missing"},
        }

    payload = _parse_json(message.content)
    result_status = str(
        payload.get("status")
        if isinstance(payload, dict)
        else getattr(message, "status", None)
        or "ok"
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    details: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_call_id": getattr(message, "tool_call_id", None),
        "result_status": result_status,
    }
    if isinstance(payload, dict):
        details.update(
            {
                "trace_id": payload.get("trace_id"),
                "error_code": payload.get("error_code"),
            }
        )

    if tool_name == "search_tender_evidence":
        rows = []
        query_plan = {}
        routing_summary = {}
        query_tasks = []
        if isinstance(data, dict):
            rows = data.get("matches") or data.get("hits") or []
            query_plan = (
                data.get("query_plan")
                if isinstance(data.get("query_plan"), dict)
                else {}
            )
            routing_summary = (
                query_plan.get("routing_summary")
                if isinstance(query_plan.get("routing_summary"), dict)
                else {}
            )
            query_tasks = (
                query_plan.get("query_tasks")
                if isinstance(query_plan.get("query_tasks"), list)
                else []
            )
        evidence_ids = _evidence_ids(rows)
        details.update(
            {
                "result_count": len(rows),
                "evidence_ids": evidence_ids,
                "query_plan": query_plan,
                "query_count": int(
                    query_plan.get("query_count") or 1
                ),
                "query_tasks": query_tasks,
                "routing_summary": routing_summary,
            }
        )
        executed_routes = (
            routing_summary.get("executed")
            if isinstance(routing_summary.get("executed"), dict)
            else {}
        )
        route_summary = "、".join(
            f"{label}{int(executed_routes.get(mode) or 0)}"
            for mode, label in (
                ("exact", "词法"),
                ("semantic", "语义"),
                ("hybrid", "混合"),
            )
            if int(executed_routes.get(mode) or 0) > 0
        )
        summary = (
            (
                f"Query已拆分为 {details['query_count']} 个检索任务，"
                if details["query_count"] > 1
                else ""
            )
            + (f"（{route_summary}）" if route_summary else "")
            + f"检索返回 {len(rows)} 条候选证据"
            + (
                f"，包括 {'、'.join(evidence_ids[:4])}"
                if evidence_ids
                else ""
            )
            + "。"
        )
    elif tool_name == "read_evidence_context":
        blocks = []
        selected_id = None
        if isinstance(data, dict):
            blocks = data.get("blocks") or []
            selected_id = (
                data.get("selected_evidence_id")
                or (data.get("evidence_ref") or {}).get("evidence_id")
            )
        details.update(
            {
                "result_count": len(blocks) if blocks else int(data is not None),
                "evidence_ids": [selected_id] if selected_id else [],
            }
        )
        summary = (
            f"已读取证据 {selected_id or '-'} 的上下文，"
            f"获得 {len(blocks) if blocks else 1 if data else 0} 个有效区块。"
        )
    elif tool_name == "compare_document_versions":
        versions = data.get("versions") if isinstance(data, dict) else []
        conflicts = data.get("conflicts") if isinstance(data, dict) else []
        details.update(
            {
                "version_count": len(versions or []),
                "conflict_count": len(conflicts or []),
            }
        )
        summary = (
            f"版本比较完成：{len(versions or [])} 个版本，"
            f"{len(conflicts or [])} 项冲突。"
        )
    elif tool_name == "get_bid_policy_rule":
        rule_count = (
            len(data)
            if isinstance(data, (list, dict))
            else int(data is not None)
        )
        details["result_count"] = rule_count
        summary = f"已读取当前绑定的立项规则，返回 {rule_count} 项内容。"
    else:
        summary = f"工具返回状态：{result_status}。"

    failed = (
        result_status == "failed"
        or str(getattr(message, "status", "") or "") == "error"
    )
    if failed and isinstance(payload, dict) and payload.get("message"):
        summary = f"工具返回失败：{_text_preview(payload.get('message'), 300)}"
    return {
        "state": "failed" if failed else "completed",
        "summary": summary,
        "details": details,
    }


def _parse_json(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        value = "".join(
            str(item.get("text", ""))
            if isinstance(item, dict)
            else str(item)
            for item in value
        )
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return {}


def _evidence_ids(rows: Any) -> list[str]:
    result: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        ref = row.get("evidence_ref")
        evidence_id = (
            ref.get("evidence_id")
            if isinstance(ref, dict)
            else row.get("evidence_id")
        )
        if evidence_id and str(evidence_id) not in result:
            result.append(str(evidence_id))
    return result[:20]


def _sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _text_preview(value, 160)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:40]:
            key = str(raw_key)[:80]
            lowered = key.casefold()
            if any(
                token in lowered
                for token in ("secret", "token", "password", "authorization")
            ):
                clean[key] = "[REDACTED]"
            else:
                clean[key] = _sanitize_value(item, depth=depth + 1)
        return clean
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(item, depth=depth + 1)
            for item in list(value)[:40]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text_preview(value, 500)


def _text_preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"
