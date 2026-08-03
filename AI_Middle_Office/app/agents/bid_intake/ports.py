from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from langchain_core.messages import AIMessage, BaseMessage

from .contracts import (
    AssessmentDraft,
    DocumentManifest,
    EvidenceRef,
    FactCoverageMode,
    PolicyEvaluation,
    ToolResult,
)


class TenderEvidencePort(Protocol):
    def search(self, *, query: str, top_k: int) -> ToolResult: ...

    def read_context(
        self,
        *,
        evidence_id: str,
        before_blocks: int = 0,
        after_blocks: int = 0,
    ) -> ToolResult: ...

    def compare_versions(self, *, document_key: str) -> ToolResult: ...

    def validate_refs(
        self,
        *,
        refs: Sequence[EvidenceRef],
        manifest: DocumentManifest,
    ) -> ToolResult: ...


class BidPolicyPort(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def prompt_context(self) -> dict[str, Any]: ...

    def get_rule(self, *, topic: str) -> ToolResult: ...

    def evaluate(
        self,
        *,
        draft: AssessmentDraft,
        manifest: DocumentManifest,
    ) -> PolicyEvaluation: ...


class BidAnalysisModelPort(Protocol):
    @property
    def model_id(self) -> str: ...

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        system_prompt: str,
        state_view: dict[str, Any],
    ) -> AIMessage: ...


@dataclass(frozen=True)
class AgentBudgets:
    max_reasoning_loops: int = 8
    max_tool_calls: int = 24
    max_tool_calls_per_turn: int = 3
    max_same_tool_args: int = 2
    max_output_repairs: int = 1
    max_gate_repairs: int = 1


@dataclass(frozen=True)
class AgentRuntime:
    model: BidAnalysisModelPort
    evidence: TenderEvidencePort
    policy: BidPolicyPort
    budgets: AgentBudgets = field(default_factory=AgentBudgets)
    fact_coverage_mode: FactCoverageMode = FactCoverageMode.SHADOW
