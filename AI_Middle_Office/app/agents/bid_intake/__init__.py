"""Bid document assessment Agent.

The package stays side-effect free on import. FastAPI owns the durable control
plane, while the separately installed Agent worker owns LangGraph, model, MCP,
and SQL checkpoint execution.
"""

from .contracts import AssessmentDraft, HumanDecision

__all__ = [
    "AssessmentDraft",
    "BidIntakeAgent",
    "HumanDecision",
    "build_bid_intake_agent",
]


def __getattr__(name: str):
    if name in {"BidIntakeAgent", "build_bid_intake_agent"}:
        from .graph import BidIntakeAgent, build_bid_intake_agent

        return {
            "BidIntakeAgent": BidIntakeAgent,
            "build_bid_intake_agent": build_bid_intake_agent,
        }[name]
    raise AttributeError(name)
