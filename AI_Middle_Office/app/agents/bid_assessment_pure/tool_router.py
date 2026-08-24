"""Deterministic relevance-only Tool Router for B03.

The router narrows the model-visible set. It does not authorize execution,
choose a business path, call a model, or prescribe a tool sequence.
"""

from __future__ import annotations

from .planning import InformationSourceHint, IntentUnderstanding
from .registry import (
    BID_DOCUMENT_SEARCH,
    DOCUMENTS_OUTLINE,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    EVIDENCE_READ,
    CanonicalToolRegistry,
)
from .state import AgentTaskState, AgentTaskStatus


class RelevanceToolRouter:
    def visible_tool_names(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
        registry: CanonicalToolRegistry,
    ) -> tuple[str, ...]:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or understanding.clarification_needed
        ):
            return ()

        requested: list[str] = []
        source_hints = set(understanding.source_hints)
        if InformationSourceHint.BID_DOCUMENTS in source_hints:
            requested.extend((DOCUMENTS_OUTLINE, BID_DOCUMENT_SEARCH))
        if InformationSourceHint.ENTERPRISE_KNOWLEDGE in source_hints:
            requested.append(ENTERPRISE_KNOWLEDGE_SEARCH)
        if InformationSourceHint.EXISTING_EVIDENCE in source_hints:
            requested.append(EVIDENCE_READ)

        selected = tuple(dict.fromkeys(requested))
        for name in selected:
            registry.get(name)
        return selected

