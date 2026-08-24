"""Fail-closed composition root for the local Pure Agent Runtime.

This module owns dependency assembly only.  It does not choose an Action,
declare an Action order, or encode a business stage graph.  The Main Agent
continues to choose one Action at a time; the capability registry merely binds
an already accepted Action kind to its explicit runtime handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol

from .action_runtime import AgentActionKind, DynamicActionLoopRuntime
from .repository import PureAgentRepository
from .runtime_config import PureAgentFeatureConfig
from .runtime_controller import (
    CapabilityActionExecutorPort,
    ContinuationTokenService,
    DisabledRuntimeDispatcher,
    DynamicActionControllerDriver,
    GuardSuiteRuntimeActionGovernor,
    LocalRuntimePulseDispatcher,
    MainAgentDecisionBoundaryProvider,
    PureAgentRuntimeController,
    RegisteredCapabilityActionExecutor,
    RuntimeAdmissionContextProvider,
    RuntimeDispatchPort,
)


CAPABILITY_ACTION_KINDS = frozenset(
    {
        AgentActionKind.PLAN,
        AgentActionKind.REPLAN,
        AgentActionKind.TOOL_CALL_BATCH,
        AgentActionKind.REQUEST_INFORMATION,
        AgentActionKind.ANSWER,
    }
)


class RuntimeCompositionError(RuntimeError):
    """A local Runtime dependency violates the explicit composition contract."""


class CapabilityHandlerRegistrationError(RuntimeCompositionError):
    """A capability handler registration is duplicate, incomplete, or invalid."""


class RuntimeCompositionStatus(str, Enum):
    DISABLED = "disabled"
    INCOMPLETE = "incomplete"
    READY = "ready"


class CapabilityHandlerFactory(Protocol):
    """Build one transaction-scoped handler without starting any work."""

    def __call__(
        self,
        repository: PureAgentRepository,
    ) -> CapabilityActionExecutorPort: ...


class MainAgentBoundaryProviderFactory(Protocol):
    def __call__(
        self,
        repository: PureAgentRepository,
    ) -> MainAgentDecisionBoundaryProvider: ...


class AdmissionContextProviderFactory(Protocol):
    def __call__(
        self,
        repository: PureAgentRepository,
    ) -> RuntimeAdmissionContextProvider: ...


class ActionLoopFactory(Protocol):
    def __call__(self) -> DynamicActionLoopRuntime: ...


class RuntimeDispatcherInstaller(Protocol):
    """Application boundary that explicitly installs one composed dispatcher."""

    def __call__(self, dispatcher: RuntimeDispatchPort) -> None: ...


def _normalize_capability_kind(
    action_kind: AgentActionKind | str,
) -> AgentActionKind:
    try:
        normalized = AgentActionKind(action_kind)
    except ValueError as exc:
        raise CapabilityHandlerRegistrationError(
            f"unsupported capability Action kind: {action_kind}"
        ) from exc
    if normalized not in CAPABILITY_ACTION_KINDS:
        raise CapabilityHandlerRegistrationError(
            "Main Agent decision cannot be registered as a capability handler"
        )
    return normalized


def _assert_handler_contract(
    handler: CapabilityActionExecutorPort,
    *,
    action_kind: AgentActionKind,
) -> None:
    if not callable(getattr(handler, "execute", None)) or not callable(
        getattr(handler, "after_observation", None)
    ):
        raise CapabilityHandlerRegistrationError(
            f"capability handler for {action_kind.value} has an invalid contract"
        )


@dataclass(frozen=True, slots=True)
class FrozenCapabilityHandlerRegistry:
    """Immutable handler-factory bindings shared by controller transactions."""

    _factories: Mapping[AgentActionKind, CapabilityHandlerFactory]
    registered_action_kinds: tuple[AgentActionKind, ...]

    @property
    def complete(self) -> bool:
        return set(self.registered_action_kinds) == CAPABILITY_ACTION_KINDS

    def build_executor(
        self,
        repository: PureAgentRepository,
    ) -> RegisteredCapabilityActionExecutor:
        if not self.complete:
            missing = sorted(
                kind.value
                for kind in CAPABILITY_ACTION_KINDS - set(self.registered_action_kinds)
            )
            raise CapabilityHandlerRegistrationError(
                "capability registry is incomplete: " + ", ".join(missing)
            )
        handlers: dict[AgentActionKind, CapabilityActionExecutorPort] = {}
        for action_kind in self.registered_action_kinds:
            handler = self._factories[action_kind](repository)
            _assert_handler_contract(handler, action_kind=action_kind)
            handlers[action_kind] = handler
        return RegisteredCapabilityActionExecutor(handlers)


class CapabilityHandlerRegistry:
    """Mutable only during bootstrap; frozen before a dispatcher is exposed."""

    def __init__(self) -> None:
        self._factories: dict[AgentActionKind, CapabilityHandlerFactory] = {}
        self._frozen = False

    @property
    def registered_action_kinds(self) -> tuple[AgentActionKind, ...]:
        return tuple(sorted(self._factories, key=lambda item: item.value))

    def register(
        self,
        action_kind: AgentActionKind | str,
        factory: CapabilityHandlerFactory,
    ) -> "CapabilityHandlerRegistry":
        if self._frozen:
            raise CapabilityHandlerRegistrationError(
                "capability registry is already frozen"
            )
        normalized = _normalize_capability_kind(action_kind)
        if normalized in self._factories:
            raise CapabilityHandlerRegistrationError(
                f"duplicate capability handler: {normalized.value}"
            )
        if not callable(factory):
            raise CapabilityHandlerRegistrationError(
                f"capability handler factory is not callable: {normalized.value}"
            )
        self._factories[normalized] = factory
        return self

    def register_many(
        self,
        factories: Mapping[
            AgentActionKind | str,
            CapabilityHandlerFactory,
        ],
    ) -> "CapabilityHandlerRegistry":
        if self._frozen:
            raise CapabilityHandlerRegistrationError(
                "capability registry is already frozen"
            )
        normalized: list[tuple[AgentActionKind, CapabilityHandlerFactory]] = []
        seen: set[AgentActionKind] = set()
        for raw_kind, factory in factories.items():
            action_kind = _normalize_capability_kind(raw_kind)
            if (
                action_kind in seen
                or action_kind in self._factories
                or not callable(factory)
            ):
                raise CapabilityHandlerRegistrationError(
                    f"invalid or duplicate capability handler: {action_kind.value}"
                )
            seen.add(action_kind)
            normalized.append((action_kind, factory))
        for action_kind, factory in normalized:
            self._factories[action_kind] = factory
        return self

    def freeze(
        self,
        *,
        required_action_kinds: Iterable[AgentActionKind | str] = (
            CAPABILITY_ACTION_KINDS
        ),
    ) -> FrozenCapabilityHandlerRegistry:
        if self._frozen:
            raise CapabilityHandlerRegistrationError(
                "capability registry is already frozen"
            )
        required = {
            _normalize_capability_kind(action_kind)
            for action_kind in required_action_kinds
        }
        missing = sorted(
            action_kind.value for action_kind in required - set(self._factories)
        )
        if missing:
            raise CapabilityHandlerRegistrationError(
                "capability registry is incomplete: " + ", ".join(missing)
            )
        self._frozen = True
        ordered = self.registered_action_kinds
        return FrozenCapabilityHandlerRegistry(
            _factories=MappingProxyType(dict(self._factories)),
            registered_action_kinds=ordered,
        )


@dataclass(frozen=True, slots=True)
class PureAgentRuntimeComponentFactories:
    """Factories required for a transaction-scoped dynamic Runtime."""

    boundary_provider: MainAgentBoundaryProviderFactory
    admission_context_provider: AdmissionContextProviderFactory
    action_loop: ActionLoopFactory
    capability_handlers: FrozenCapabilityHandlerRegistry

    def validate(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not callable(self.boundary_provider):
            reasons.append("BOUNDARY_PROVIDER_FACTORY_MISSING")
        if not callable(self.admission_context_provider):
            reasons.append("ADMISSION_CONTEXT_PROVIDER_FACTORY_MISSING")
        if not callable(self.action_loop):
            reasons.append("ACTION_LOOP_FACTORY_MISSING")
        if not self.capability_handlers.complete:
            reasons.append("CAPABILITY_HANDLER_REGISTRY_INCOMPLETE")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class PureAgentRuntimeComposition:
    """Composition result safe to hand to the Conversation API bootstrap."""

    status: RuntimeCompositionStatus
    dispatcher: RuntimeDispatchPort
    reason_codes: tuple[str, ...]
    registered_action_kinds: tuple[AgentActionKind, ...] = ()

    @property
    def available(self) -> bool:
        return (
            self.status is RuntimeCompositionStatus.READY
            and self.dispatcher.available
        )


class PureAgentRuntimeCompositionRoot:
    """Build a local dispatcher only when every execution authority is present."""

    def __init__(
        self,
        *,
        feature_config: PureAgentFeatureConfig,
        continuation_secret: str | bytes | None,
        session_factory: Callable[[], Any] | None = None,
        component_factories: PureAgentRuntimeComponentFactories | None = None,
        max_pulses_per_dispatch: int = 64,
    ) -> None:
        self._feature_config = feature_config
        self._tokens = ContinuationTokenService(continuation_secret)
        self._session_factory = session_factory
        self._components = component_factories
        self._max_pulses = int(max_pulses_per_dispatch)

    def compose(self) -> PureAgentRuntimeComposition:
        disabled_reasons = self._disabled_reasons()
        if disabled_reasons:
            return self._disabled(
                status=RuntimeCompositionStatus.DISABLED,
                reasons=disabled_reasons,
            )
        incomplete_reasons = self._incomplete_reasons()
        if incomplete_reasons:
            return self._disabled(
                status=RuntimeCompositionStatus.INCOMPLETE,
                reasons=incomplete_reasons,
            )
        assert self._session_factory is not None
        assert self._components is not None
        dispatcher = LocalRuntimePulseDispatcher(
            session_factory=self._session_factory,
            controller_factory=self._build_controller,
            max_pulses_per_dispatch=self._max_pulses,
        )
        return PureAgentRuntimeComposition(
            status=RuntimeCompositionStatus.READY,
            dispatcher=dispatcher,
            reason_codes=("LOCAL_RUNTIME_COMPOSITION_READY",),
            registered_action_kinds=(
                self._components.capability_handlers.registered_action_kinds
            ),
        )

    def compose_and_install(
        self,
        installer: RuntimeDispatcherInstaller,
    ) -> PureAgentRuntimeComposition:
        """Install only when application bootstrap explicitly invokes this method.

        Disabled and incomplete compositions install a disabled dispatcher, so
        a restart or reconfiguration cannot accidentally retain old authority.
        """

        if not callable(installer):
            raise RuntimeCompositionError("Runtime dispatcher installer is invalid")
        composition = self.compose()
        installer(composition.dispatcher)
        return composition

    def _disabled_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self._feature_config.enabled:
            reasons.append("PURE_AGENT_API_DISABLED")
        if not self._feature_config.runtime_enabled:
            reasons.append("PURE_AGENT_RUNTIME_DISABLED")
        if not self._tokens.available:
            reasons.append("CONTINUATION_SECRET_UNAVAILABLE")
        return tuple(reasons)

    def _incomplete_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self._session_factory is None or not callable(self._session_factory):
            reasons.append("SESSION_FACTORY_MISSING")
        if self._components is None:
            reasons.append("RUNTIME_COMPONENT_FACTORIES_MISSING")
        else:
            reasons.extend(self._components.validate())
        if not 1 <= self._max_pulses <= 256:
            reasons.append("MAX_PULSES_OUT_OF_RANGE")
        return tuple(reasons)

    def _build_controller(self, session: Any) -> PureAgentRuntimeController:
        if self._components is None:
            raise RuntimeCompositionError("Runtime components are unavailable")
        repository = PureAgentRepository(session)
        boundary_provider = self._components.boundary_provider(repository)
        admission_provider = self._components.admission_context_provider(repository)
        action_loop = self._components.action_loop()
        if not callable(getattr(boundary_provider, "prepare", None)):
            raise RuntimeCompositionError(
                "Main Agent boundary provider contract is invalid"
            )
        if not callable(getattr(admission_provider, "for_action", None)):
            raise RuntimeCompositionError(
                "Runtime admission context provider contract is invalid"
            )
        if not isinstance(action_loop, DynamicActionLoopRuntime):
            raise RuntimeCompositionError("Action Loop factory contract is invalid")
        capability_executor = self._components.capability_handlers.build_executor(
            repository
        )
        driver = DynamicActionControllerDriver(
            repository,
            boundary_provider=boundary_provider,
            governor=GuardSuiteRuntimeActionGovernor(
                context_provider=admission_provider
            ),
            action_loop=action_loop,
            capability_executor=capability_executor,
        )
        return PureAgentRuntimeController(
            repository,
            driver=driver,
            continuation_tokens=self._tokens,
            recovery_context_provider=(
                admission_provider
                if callable(getattr(admission_provider, "for_recovery", None))
                else None
            ),
        )

    @staticmethod
    def _disabled(
        *,
        status: RuntimeCompositionStatus,
        reasons: tuple[str, ...],
    ) -> PureAgentRuntimeComposition:
        return PureAgentRuntimeComposition(
            status=status,
            dispatcher=DisabledRuntimeDispatcher(),
            reason_codes=reasons,
        )


def build_capability_handler_registry(
    factories: Mapping[AgentActionKind | str, CapabilityHandlerFactory],
) -> FrozenCapabilityHandlerRegistry:
    """Build the complete v0.1 capability registry with no runtime side effects."""

    return CapabilityHandlerRegistry().register_many(factories).freeze()
