from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.bid_intake.execution_trace import (
    BidIntakeExecutionTrace,
    TRACE_SCHEMA_VERSION,
    restore_trace_position,
)


def _task_started(task_id: str, name: str, task_input: dict):
    return {
        "type": "tasks",
        "data": {
            "id": task_id,
            "name": name,
            "input": task_input,
            "triggers": (),
        },
    }


def _task_finished(
    task_id: str,
    name: str,
    result: dict,
    *,
    interrupts: list | None = None,
):
    return {
        "type": "tasks",
        "data": {
            "id": task_id,
            "name": name,
            "error": None,
            "result": result,
            "interrupts": interrupts or [],
        },
    }


def test_trace_projects_react_tool_observation_and_return_edge() -> None:
    emitted: list[tuple[str, str, dict]] = []
    trace = BidIntakeExecutionTrace(
        emit=lambda event_type, message, payload: emitted.append(
            (event_type, message, payload)
        )
    )
    model_message = AIMessage(
        content="private reasoning must not be persisted",
        additional_kwargs={
            "bid_adaptive_tool_phase": "initial_search",
            "bid_adaptive_tool_limit": 1,
            "bid_adaptive_tool_reason": (
                "尚无候选证据，先执行一次覆盖核心维度的主检索。"
            ),
            "bid_adaptive_preferred_tools": [
                "search_tender_evidence"
            ],
            "bid_requested_tool_count": 3,
            "bid_selected_tool_count": 1,
            "bid_tool_calls_trimmed": 2,
        },
        tool_calls=[
            {
                "id": "call-search-1",
                "name": "search_tender_evidence",
                "args": {
                    "query": "付款条件",
                    "top_k": 5,
                    "access_token": "must-redact",
                },
            }
        ],
    )

    trace.consume(_task_started("react-1", "react_model", {"messages": []}))
    trace.consume(
        _task_finished(
            "react-1",
            "react_model",
            {"messages": [model_message]},
        )
    )
    trace.consume(
        _task_started(
            "guard-1",
            "authorize_tools",
            {"messages": [model_message]},
        )
    )
    trace.consume(
        _task_finished(
            "guard-1",
            "authorize_tools",
            {"tool_call_count": 1},
        )
    )
    trace.consume(
        _task_started(
            "tools-1",
            "tool_executor",
            {"messages": [model_message]},
        )
    )
    tool_message = ToolMessage(
        name="search_tender_evidence",
        tool_call_id="call-search-1",
        content=json.dumps(
            {
                "status": "ok",
                "trace_id": "mcp-trace-1",
                "data": {
                    "query_plan": {
                        "schema_version": "tender-query-plan/v1",
                        "strategy": "topic_decomposition",
                        "queries": [
                            "付款条件和工期",
                            "项目付款、结算、审计和回款条件",
                            "项目工期、进度节点和延期违约责任",
                        ],
                        "atomic_queries": [
                            "项目付款、结算、审计和回款条件",
                            "项目工期、进度节点和延期违约责任",
                        ],
                        "topics": ["payment", "schedule"],
                        "query_count": 3,
                        "query_tasks": [
                            {
                                "query_id": "q1",
                                "requested_mode": "semantic",
                                "executed_mode": "semantic",
                            },
                            {
                                "query_id": "q2",
                                "requested_mode": "exact",
                                "executed_mode": "exact",
                            },
                            {
                                "query_id": "q3",
                                "requested_mode": "exact",
                                "executed_mode": "exact",
                            },
                        ],
                        "routing_summary": {
                            "requested": {
                                "exact": 2,
                                "semantic": 1,
                                "hybrid": 0,
                            },
                            "executed": {
                                "exact": 2,
                                "semantic": 1,
                                "hybrid": 0,
                            },
                            "fallback_count": 0,
                        },
                    },
                    "matches": [
                        {
                            "evidence_ref": {
                                "evidence_id": "EV-001",
                            },
                            "excerpt": "sensitive tender source text",
                        }
                    ]
                },
            }
        ),
    )
    trace.consume(
        _task_finished(
            "tools-1",
            "tool_executor",
            {"messages": [tool_message]},
        )
    )
    trace.consume(
        _task_started(
            "react-2",
            "react_model",
            {"messages": [model_message, tool_message]},
        )
    )

    payloads = [item[2] for item in emitted]
    latest = {}
    for payload in payloads:
        latest[payload["step_id"]] = payload

    tool = latest["tool:call-search-1"]
    observation = latest["observation:call-search-1:0"]
    first_input = latest["llm-input:react-1"]
    first_react = latest["node:react-1"]
    plan = latest["plan:node:react-1"]
    guard = latest["node:guard-1"]
    loop = latest["loop:tools-1"]
    second_input = latest["llm-input:react-2"]
    second_react = latest["node:react-2"]

    assert first_react["parent_step_ids"] == ["llm-input:react-1"]
    assert plan["parent_step_ids"] == ["node:react-1"]
    assert plan["details"]["decision"] == "call_tools"
    assert plan["details"]["adaptive_tool_phase"] == "initial_search"
    assert plan["details"]["adaptive_tool_limit"] == 1
    assert plan["details"]["trimmed_tool_count"] == 2
    assert guard["parent_step_ids"] == ["plan:node:react-1"]
    assert guard["details"]["adaptive_tool_phase_title"] == "首轮主检索"
    assert "本轮动态工具预算" in guard["details"]["checks"]
    assert tool["parent_step_ids"] == ["node:guard-1"]
    assert observation["parent_step_ids"] == ["tool:call-search-1"]
    assert observation["details"]["returns_to_llm"] is True
    assert loop["parent_step_ids"] == [
        "observation:call-search-1:0"
    ]
    assert loop["details"]["continue_react"] is True
    assert second_input["parent_step_ids"] == ["loop:tools-1"]
    assert second_input["details"]["llm_input"][
        "prior_observation_count"
    ] == 1
    assert second_react["parent_step_ids"] == ["llm-input:react-2"]
    assert len(first_input["details"]["available_tools"]) == 4
    assert observation["details"]["result_count"] == 1
    assert observation["details"]["evidence_ids"] == ["EV-001"]
    assert observation["details"]["query_count"] == 3
    assert observation["details"]["query_plan"]["topics"] == [
        "payment",
        "schedule",
    ]
    assert observation["details"]["routing_summary"]["executed"] == {
        "exact": 2,
        "semantic": 1,
        "hybrid": 0,
    }
    assert "词法2、语义1" in observation["summary"]
    assert "首轮主检索" in first_react["summary"]
    assert "本轮上限 1 个Tool" in plan["summary"]
    assert tool["details"]["input"]["access_token"] == "[REDACTED]"
    assert all(
        "private reasoning" not in json.dumps(payload, ensure_ascii=False)
        for payload in payloads
    )
    assert all(
        "sensitive tender source text"
        not in json.dumps(payload, ensure_ascii=False)
        for payload in payloads
    )


def test_trace_explains_why_react_loop_stops() -> None:
    emitted: list[tuple[str, str, dict]] = []
    trace = BidIntakeExecutionTrace(
        emit=lambda event_type, message, payload: emitted.append(
            (event_type, message, payload)
        )
    )
    trace.consume(
        _task_started(
            "react-final",
            "react_model",
            {
                "analysis_goal": "判断是否报价",
                "manifest": {
                    "manifest_version": 2,
                    "active_documents": [{"id": "doc-1"}],
                },
                "messages": [],
            },
        )
    )
    trace.consume(
        _task_finished(
            "react-final",
            "react_model",
            {
                "messages": [
                    AIMessage(content="private final reasoning")
                ]
            },
        )
    )

    payloads = [item[2] for item in emitted]
    decision = next(
        payload
        for payload in payloads
        if payload["step_id"] == "loop-decision:node:react-final"
    )
    assert decision["kind"] == "loop"
    assert decision["details"]["continue_react"] is False
    assert decision["details"]["next_action"] == "形成结构化研判草稿"
    assert "停止" in decision["summary"]
    assert all(
        "private final reasoning"
        not in json.dumps(payload, ensure_ascii=False)
        for payload in payloads
    )


def test_trace_displays_model_failover_without_private_reasoning() -> None:
    emitted: list[tuple[str, str, dict]] = []
    trace = BidIntakeExecutionTrace(
        emit=lambda event_type, message, payload: emitted.append(
            (event_type, message, payload)
        )
    )
    model_message = AIMessage(
        content="private fallback reasoning",
        tool_calls=[
            {
                "id": "call-policy-1",
                "name": "get_bid_policy_rule",
                "args": {"topic": "立项硬门槛"},
            }
        ],
        response_metadata={
            "bid_model_route": "fallback",
            "bid_model_id": "glm-tool-model",
            "bid_primary_error": "http_402",
        },
    )

    trace.consume(_task_started("react-fallback", "react_model", {}))
    trace.consume(
        _task_finished(
            "react-fallback",
            "react_model",
            {"messages": [model_message]},
        )
    )

    payload = next(
        item[2]
        for item in emitted
        if item[2]["step_id"] == "node:react-fallback"
        and item[2]["state"] == "completed"
    )
    assert "备用模型接管" in payload["summary"]
    assert payload["details"]["model_route"] == "fallback"
    assert payload["details"]["model_id"] == "glm-tool-model"
    assert payload["details"]["primary_error"] == "http_402"
    assert "private fallback reasoning" not in json.dumps(
        payload,
        ensure_ascii=False,
    )


def test_trace_marks_human_interrupt_as_waiting() -> None:
    emitted: list[tuple[str, str, dict]] = []
    trace = BidIntakeExecutionTrace(
        emit=lambda event_type, message, payload: emitted.append(
            (event_type, message, payload)
        )
    )

    trace.consume(
        _task_started("human-1", "human_review", {"messages": []})
    )
    trace.consume(
        _task_finished(
            "human-1",
            "human_review",
            {},
            interrupts=[object()],
        )
    )

    event_type, _, payload = emitted[-1]
    assert event_type == "trace_step_waiting"
    assert payload["state"] == "waiting"
    assert payload["kind"] == "human"
    assert payload["details"]["interrupt_count"] == 1


def test_restore_trace_position_uses_latest_sequence() -> None:
    payloads = [
        {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "sequence": 2,
            "step_id": "node:react-1",
            "iteration": 1,
        },
        {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "sequence": 4,
            "step_id": "node:human-1",
            "iteration": None,
        },
    ]

    assert restore_trace_position(payloads) == (
        4,
        ["node:human-1"],
        1,
    )
