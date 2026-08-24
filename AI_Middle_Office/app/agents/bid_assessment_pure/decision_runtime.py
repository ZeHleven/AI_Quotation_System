"""B04-1 capability composition without a fixed Agent execution chain."""

from __future__ import annotations

from .complexity_gate import DefaultComplexityGate
from .intent_runtime import IntentUnderstandingRuntime
from .planning import ComplexityDecision, IntentUnderstanding, PlanRevision
from .planner_runtime import PlanRevisionReason, PlannerRuntime
from .runtime import ContextAssemblyResult
from .state import AgentTaskState
from .tool_runtime import RegistrySnapshot


class MainAgentDecisionRuntime:
    """Expose decision capabilities individually to the future dynamic loop.

    Deliberately no ``run`` or pipeline method exists here.  The Main Agent may
    invoke these capabilities when current state calls for them, rather than
    following an Intent -> Plan -> Tool workflow.
    """

    def __init__(
        self,
        *,
        intent: IntentUnderstandingRuntime | None = None,
        complexity_gate: DefaultComplexityGate | None = None,
        planner: PlannerRuntime | None = None,
    ) -> None:
        self.intent = intent or IntentUnderstandingRuntime()
        self.complexity_gate = complexity_gate or DefaultComplexityGate()
        self.planner = planner or PlannerRuntime()

    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding:
        return await self.intent.understand(task=task, context=context)

    def decide_complexity(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
    ) -> ComplexityDecision:
        return self.complexity_gate.decide(
            task=task,
            understanding=understanding,
        )

    async def create_or_revise_plan(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
        complexity: ComplexityDecision,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
        previous_plan: PlanRevision | None = None,
        revision_reasons: tuple[PlanRevisionReason, ...] = (),
    ) -> PlanRevision:
        return await self.planner.create_or_revise(
            task=task,
            understanding=understanding,
            complexity=complexity,
            context=context,
            registry_snapshot=registry_snapshot,
            previous_plan=previous_plan,
            revision_reasons=revision_reasons,
        )
