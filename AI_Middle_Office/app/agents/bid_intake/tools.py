from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from .ports import AgentRuntime


def _json_result(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_react_tools(runtime: AgentRuntime) -> list[BaseTool]:
    def search_tender_evidence(query: str, top_k: int = 5) -> str:
        """Search evidence only inside the current tender case."""

        safe_top_k = max(1, min(int(top_k), 20))
        return _json_result(runtime.evidence.search(query=query, top_k=safe_top_k))

    def read_evidence_context(
        evidence_id: str,
        before_blocks: int = 0,
        after_blocks: int = 0,
    ) -> str:
        """Read the authoritative surrounding context for one evidence result."""

        return _json_result(
            runtime.evidence.read_context(
                evidence_id=evidence_id,
                before_blocks=max(0, min(int(before_blocks), 3)),
                after_blocks=max(0, min(int(after_blocks), 3)),
            )
        )

    def compare_document_versions(document_key: str) -> str:
        """Compare active and historical versions of one logical tender document."""

        return _json_result(runtime.evidence.compare_versions(document_key=document_key))

    def get_bid_policy_rule(topic: str) -> str:
        """Read the currently bound bid-decision policy for a specific topic."""

        return _json_result(runtime.policy.get_rule(topic=topic))

    return [
        StructuredTool.from_function(
            func=search_tender_evidence,
            name="search_tender_evidence",
            description=search_tender_evidence.__doc__ or "",
        ),
        StructuredTool.from_function(
            func=read_evidence_context,
            name="read_evidence_context",
            description=read_evidence_context.__doc__ or "",
        ),
        StructuredTool.from_function(
            func=compare_document_versions,
            name="compare_document_versions",
            description=compare_document_versions.__doc__ or "",
        ),
        StructuredTool.from_function(
            func=get_bid_policy_rule,
            name="get_bid_policy_rule",
            description=get_bid_policy_rule.__doc__ or "",
        ),
    ]
