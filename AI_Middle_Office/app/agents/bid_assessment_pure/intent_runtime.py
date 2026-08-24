"""Provider-neutral open intent understanding for B04-1.

The runtime validates context ownership and structured provider output.  It does
not classify the request into fixed business labels and does not start a model.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Protocol

from pydantic import ValidationError

from .planning import IntentUnderstanding
from .runtime import ContextAssemblyResult, ContextAssemblyStatus, ContextConsumer
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import canonical_json


class IntentRuntimeError(RuntimeError):
    """Safe base error for intent-runtime boundary failures."""


class IntentProviderUnavailable(IntentRuntimeError):
    """No authorized intent provider is configured."""


class IntentContextRejected(IntentRuntimeError):
    """The supplied context is not valid for intent understanding."""


class IntentContractRejected(IntentRuntimeError):
    """Provider output failed the authoritative runtime contract."""


class IntentUnderstandingProvider(Protocol):
    """Replaceable structured-output provider bridged by B04-3."""

    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding | Mapping[str, Any]: ...


class DisabledIntentUnderstandingProvider:
    """Fail-closed default that can never initiate a model call."""

    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding:
        del task, context
        raise IntentProviderUnavailable("intent understanding provider is disabled")


class StaticIntentUnderstandingProvider:
    """In-memory fixture provider for later authorized contract tests only."""

    def __init__(self, responses: Mapping[str, IntentUnderstanding | Mapping[str, Any]]):
        self._responses = MappingProxyType(dict(responses))

    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding | Mapping[str, Any]:
        del task
        try:
            return self._responses[context.snapshot.snapshot_ref]
        except KeyError as exc:
            raise IntentProviderUnavailable(
                "no static intent response is configured for this context"
            ) from exc


class IntentUnderstandingRuntime:
    """Validate one open intent-understanding capability invocation."""

    def __init__(self, provider: IntentUnderstandingProvider | None = None) -> None:
        self._provider = provider or DisabledIntentUnderstandingProvider()

    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding:
        self._validate_context(task=task, context=context)
        raw = await self._provider.understand(task=task, context=context)
        if isinstance(raw, IntentUnderstanding):
            return raw
        try:
            return IntentUnderstanding.model_validate_json(canonical_json(raw))
        except (TypeError, ValueError, ValidationError) as exc:
            raise IntentContractRejected(
                "intent provider output does not satisfy IntentUnderstanding"
            ) from exc

    @staticmethod
    def _validate_context(
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> None:
        snapshot = context.snapshot
        if task.status is not AgentTaskStatus.RUNNING:
            raise IntentContextRejected("intent understanding requires a running task")
        if snapshot.task_ref != task.task_id:
            raise IntentContextRejected("intent context belongs to another task")
        if snapshot.state_version != task.state_version:
            raise IntentContextRejected("intent context state version is stale")
        if snapshot.consumer is not ContextConsumer.INTENT:
            raise IntentContextRejected("context was not assembled for intent understanding")
        if snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            raise IntentContextRejected("intent context is not ready for consumption")
