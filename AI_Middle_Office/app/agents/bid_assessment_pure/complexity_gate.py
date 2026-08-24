"""Minimal Direct/Planned gate for B04-1.

The LLM-facing intent contract recommends a mode.  This deterministic gate
enforces the one-way upgrade rule and a small number of structural safeguards;
it does not classify bid-assessment business scenarios or create a workflow.
"""

from __future__ import annotations

from dataclasses import dataclass

from .planning import (
    ComplexityDecision,
    ExecutionMode,
    InformationSourceHint,
    IntentUnderstanding,
)
from .state import AgentTaskState, AgentTaskStatus


class ComplexityGateRejected(ValueError):
    """The gate cannot make a legal decision for the supplied task."""


@dataclass(frozen=True, slots=True)
class ComplexityGateProfile:
    """Small runtime guardrails, not a business-rule classifier."""

    many_information_needs_threshold: int = 4
    cross_source_threshold: int = 2

    def __post_init__(self) -> None:
        if self.many_information_needs_threshold < 2:
            raise ValueError("many_information_needs_threshold must be at least 2")
        if self.cross_source_threshold < 2:
            raise ValueError("cross_source_threshold must be at least 2")


class DefaultComplexityGate:
    """Choose a mode without fixing the Agent's later action sequence."""

    _ASSESSMENT_SOURCES = frozenset(
        {
            InformationSourceHint.BID_DOCUMENTS,
            InformationSourceHint.ENTERPRISE_KNOWLEDGE,
        }
    )

    def __init__(self, profile: ComplexityGateProfile | None = None) -> None:
        self._profile = profile or ComplexityGateProfile()

    def decide(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
    ) -> ComplexityDecision:
        if task.status is not AgentTaskStatus.RUNNING:
            raise ComplexityGateRejected("complexity decision requires a running task")

        reasons: list[str] = []
        if task.execution_mode is ExecutionMode.PLANNED:
            mode = ExecutionMode.PLANNED
            reasons.append("planned_mode_is_not_downgraded")
        elif understanding.clarification_needed:
            mode = ExecutionMode.DIRECT
            reasons.append("blocking_clarification_precedes_planning")
        elif understanding.execution_mode is ExecutionMode.PLANNED:
            mode = ExecutionMode.PLANNED
            reasons.append("intent_understanding_recommended_planned")
        else:
            assessment_source_count = len(
                set(understanding.source_hints) & self._ASSESSMENT_SOURCES
            )
            if assessment_source_count >= self._profile.cross_source_threshold:
                mode = ExecutionMode.PLANNED
                reasons.append("cross_source_synthesis_requires_planning")
            elif (
                len(understanding.information_needs)
                >= self._profile.many_information_needs_threshold
            ):
                mode = ExecutionMode.PLANNED
                reasons.append("many_information_needs_require_planning")
            else:
                mode = ExecutionMode.DIRECT
                reasons.append("short_single_goal_path_remains_direct")

        if mode is ExecutionMode.PLANNED and task.execution_mode is ExecutionMode.DIRECT:
            reasons.append("direct_to_planned_upgrade_preserves_observations")

        return ComplexityDecision(
            execution_mode=mode,
            reasons=tuple(reasons),
            preserves_observation_refs=task.observation_refs,
        )
