"""Structured Provider bridges for replaceable Pure Agent capabilities."""

from __future__ import annotations

from .planning import IntentUnderstanding, TaskPlan
from .planner_runtime import PlannerGenerationRequest
from .provider_runtime import (
    ProviderRuntimeInput,
    ProviderStrictMode,
    ProviderToolChoice,
    StructuredModelCallBridge,
)
from .runtime import ContextAssemblyResult
from .state import AgentTaskState
from .tool_runtime import RegistrySnapshot


class ProviderIntentUnderstandingProvider:
    """Use the generic bridge without introducing a fixed intent classifier."""

    def __init__(self, bridge: StructuredModelCallBridge | None = None) -> None:
        self._bridge = bridge or StructuredModelCallBridge()

    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding:
        return await self._bridge.invoke(
            task=task,
            context=context,
            output_model=IntentUnderstanding,
            schema_name="intent_understanding",
            strict_mode=ProviderStrictMode.PREFERRED,
        )


class ProviderPlannerProvider:
    """Use one bounded structured model turn; it never executes a Tool."""

    def __init__(self, bridge: StructuredModelCallBridge | None = None) -> None:
        self._bridge = bridge or StructuredModelCallBridge()

    async def generate(
        self,
        request: PlannerGenerationRequest,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
    ) -> TaskPlan:
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=request.request_ref,
            input_kind="planner_generation_request",
            payload=request.model_dump(mode="json"),
        )
        return await self._bridge.invoke(
            task=task,
            context=context,
            output_model=TaskPlan,
            schema_name="task_plan",
            runtime_input=runtime_input,
            registry_snapshot=registry_snapshot,
            tool_choice=ProviderToolChoice.NONE,
            strict_mode=ProviderStrictMode.PREFERRED,
        )
