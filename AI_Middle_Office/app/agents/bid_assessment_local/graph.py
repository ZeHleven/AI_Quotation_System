"""One-transition LangGraph for a single governed Task.

All nodes are pure. Persistence and I/O remain in Phase 3/4 service layers.
"""
from __future__ import annotations

from typing import Any, TypedDict

from app.agents.bid_assessment_local.contracts import (
    normalize_local_agent_state,
    normalize_task_action,
)


class BoundedGraphState(TypedDict, total=False):
    local_state: dict[str, Any]
    proposed_action: dict[str, Any] | None
    allowed_tools: list[str]
    operation: dict[str, Any]


def _hydrate_node(state: BoundedGraphState) -> BoundedGraphState:
    local_state = dict(state["local_state"])
    if local_state.get("outstanding_operation_ref") and state.get("proposed_action") is None:
        return {
            "operation": {
                "operation_type": "wait",
                "reason_code": "BID_LOCAL_AGENT_OUTSTANDING_OPERATION",
            }
        }
    return {}


def _propose_node(state: BoundedGraphState) -> BoundedGraphState:
    if state.get("operation"):
        return {}
    action = state.get("proposed_action")
    if action is None:
        return {"operation": {"operation_type": "request_model"}}
    normalized = normalize_task_action(
        dict(action), allowed_tools=set(state.get("allowed_tools") or [])
    )
    return {
        "operation": {
            "operation_type": str(normalized["action_type"]),
            "action": normalized,
        }
    }


def build_bounded_task_graph():
    # LangGraph is an Agent-only dependency and is imported lazily so the
    # default-disabled FastAPI process has no new import-time dependency.
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(BoundedGraphState)
    graph.add_node("hydrate_state", _hydrate_node)
    graph.add_node("propose_one_action", _propose_node)
    graph.add_edge(START, "hydrate_state")
    graph.add_edge("hydrate_state", "propose_one_action")
    graph.add_edge("propose_one_action", END)
    return graph.compile()


def run_bounded_transition(
    *,
    local_state: dict[str, Any],
    proposed_action: dict[str, Any] | None,
    allowed_tools: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    normalized_state = normalize_local_agent_state(dict(local_state))
    result = build_bounded_task_graph().invoke(
        {
            "local_state": normalized_state,
            "proposed_action": dict(proposed_action) if proposed_action is not None else None,
            "allowed_tools": list(allowed_tools),
        }
    )
    operation = dict(result.get("operation") or {})
    if not operation:
        raise RuntimeError("BID_LOCAL_AGENT_NO_PERSISTABLE_ACTION")
    return operation
