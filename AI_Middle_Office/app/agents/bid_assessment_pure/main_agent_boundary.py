"""Persisted Main Agent Decision Boundary Provider for C02-2.

The provider freezes the current logical user Turn, Context Snapshot, and Tool
Registry Snapshot. It never calls a model, classifies intent, creates a Plan,
routes a Tool, or chooses the next Action.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .context_runtime import ContextAssemblerRuntime
from .repository import PureAgentRepository
from .runtime import (
    ContextAssemblyRequest,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextProfile,
    ModelContextProfile,
)
from .runtime_controller import (
    MainAgentDecisionBoundary,
    RuntimeWakeReason,
    RuntimeWakeup,
)
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import RegistrySnapshot, canonical_hash


class MainAgentBoundaryError(RuntimeError):
    """Safe base error for Main Agent Boundary assembly failures."""


class MainAgentBoundaryRejected(MainAgentBoundaryError):
    """The persisted Turn or assembled Context lost an authoritative fence."""


class MainAgentTurn(StrictContract):
    turn_ref: Reference
    task_ref: Reference
    conversation_ref: Reference
    message_ref: Reference
    message_sequence: int = Field(ge=1)
    message_type: str = Field(
        pattern=r"^user\.(task_trigger|steering_candidate|slot_candidate)$"
    )
    message_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MainAgentBoundaryAssemblyInputs(StrictContract):
    """Authoritative inputs supplied by policy/authorization integration."""

    policy_snapshot_ref: Reference
    prompt_template_ref: Reference
    authorization_snapshot_ref: Reference
    model_profile: ModelContextProfile
    context_profile: ContextProfile
    registry_snapshot: RegistrySnapshot | None = None
    information_need_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    required_resource_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    checkpoint_snapshot_ref: Reference | None = None

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "MainAgentBoundaryAssemblyInputs":
        for field_name in ("information_need_refs", "required_resource_refs"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class MainAgentBoundaryInputsProvider(Protocol):
    """Resolve already-authorized profiles and visibility for one Turn."""

    async def prepare(
        self,
        *,
        task: AgentTaskState,
        turn: MainAgentTurn,
        wakeup: RuntimeWakeup,
    ) -> MainAgentBoundaryAssemblyInputs: ...


class PersistedMainAgentTurnResolver:
    """Map a wake reason to a persisted, Task-bound user message."""

    _MESSAGE_TYPES = {
        RuntimeWakeReason.USER_MESSAGE: ("user.task_trigger",),
        RuntimeWakeReason.STEERING_MESSAGE: ("user.steering_candidate",),
        RuntimeWakeReason.SLOT_RESUMED: ("user.slot_candidate",),
        RuntimeWakeReason.ACTION_CONTINUATION: (
            "user.steering_candidate",
            "user.slot_candidate",
            "user.task_trigger",
        ),
        RuntimeWakeReason.RECOVERY: (
            "user.steering_candidate",
            "user.slot_candidate",
            "user.task_trigger",
        ),
    }

    def __init__(self, repository: PureAgentRepository) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> MainAgentTurn:
        try:
            message_types = self._MESSAGE_TYPES[wakeup.reason]
        except KeyError as exc:
            raise MainAgentBoundaryRejected(
                "Runtime wake reason cannot resolve a Main Agent Turn"
            ) from exc
        row = self._repository.load_latest_task_user_turn(
            task_id=task.task_id,
            conversation_id=task.session_id,
            message_types=message_types,
        )
        content_hash = canonical_hash(row.content_json)
        return MainAgentTurn(
            turn_ref=f"user-turn:{row.id}",
            task_ref=task.task_id,
            conversation_ref=task.session_id,
            message_ref=row.id,
            message_sequence=int(row.sequence_no),
            message_type=row.message_type,
            message_content_hash=content_hash,
        )


class PersistedMainAgentDecisionBoundaryProvider:
    """Freeze one model-ready Boundary from persisted and authorized inputs."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        context_assembler: ContextAssemblerRuntime,
        inputs_provider: MainAgentBoundaryInputsProvider,
        turn_resolver: PersistedMainAgentTurnResolver | None = None,
    ) -> None:
        self._context_assembler = context_assembler
        self._inputs_provider = inputs_provider
        self._turn_resolver = turn_resolver or PersistedMainAgentTurnResolver(
            repository
        )

    async def prepare(
        self,
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> MainAgentDecisionBoundary:
        self._validate_boundary(task=task, wakeup=wakeup)
        turn = self._turn_resolver.resolve(task=task, wakeup=wakeup)
        inputs = await self._inputs_provider.prepare(
            task=task,
            turn=turn,
            wakeup=wakeup,
        )
        registry = inputs.registry_snapshot
        request = ContextAssemblyRequest(
            task_ref=task.task_id,
            state_version=task.state_version,
            consumer=ContextConsumer.MAIN_AGENT,
            user_message_ref=turn.message_ref,
            visible_tool_names=(
                () if registry is None else registry.visible_tool_names
            ),
            information_need_refs=inputs.information_need_refs,
            required_resource_refs=inputs.required_resource_refs,
            policy_snapshot_ref=inputs.policy_snapshot_ref,
            prompt_template_ref=inputs.prompt_template_ref,
            registry_snapshot_ref=(
                None if registry is None else registry.snapshot_ref
            ),
            model_profile_ref=inputs.model_profile.profile_ref,
            context_profile_ref=inputs.context_profile.profile_ref,
            checkpoint_snapshot_ref=inputs.checkpoint_snapshot_ref,
            authorization_snapshot_ref=inputs.authorization_snapshot_ref,
            # Task state is versioned after every accepted Action/Observation,
            # making this deterministic and idempotent for one safe boundary.
            snapshot_sequence=task.state_version,
        )
        context = await self._context_assembler.assemble(
            task=task,
            request=request,
            model_profile=inputs.model_profile,
            context_profile=inputs.context_profile,
            registry_snapshot=registry,
        )
        if context.snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            raise MainAgentBoundaryRejected(
                "Main Agent Context is not model-ready"
            )
        if (
            context.snapshot.task_ref != task.task_id
            or context.snapshot.state_version != task.state_version
            or context.snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or not any(
                entry.kind is ContextEntryKind.CURRENT_USER_MESSAGE
                and turn.message_ref in {entry.entry_ref, entry.source_ref}
                for entry in context.snapshot.included_entries
            )
        ):
            raise MainAgentBoundaryRejected(
                "Main Agent Context lost its Turn or Task fence"
            )
        return MainAgentDecisionBoundary(
            turn_ref=turn.turn_ref,
            context=context,
            registry_snapshot=registry,
        )

    @staticmethod
    def _validate_boundary(
        *,
        task: AgentTaskState,
        wakeup: RuntimeWakeup,
    ) -> None:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is not None
            or wakeup.task_ref != task.task_id
            or wakeup.conversation_ref != task.session_id
            or wakeup.observed_state_version > task.state_version
        ):
            raise MainAgentBoundaryRejected(
                "Main Agent Boundary is stale or outside the running Task"
            )


class DisabledMainAgentBoundaryInputsProvider:
    async def prepare(
        self,
        *,
        task: AgentTaskState,
        turn: MainAgentTurn,
        wakeup: RuntimeWakeup,
    ) -> MainAgentBoundaryAssemblyInputs:
        del task, turn, wakeup
        raise MainAgentBoundaryRejected(
            "Main Agent Boundary inputs are not configured"
        )
