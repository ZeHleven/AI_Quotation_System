"""Explicit, local-only Bootstrap for the Pure Agent Runtime.

Importing this module never installs a dispatcher, opens a database session,
calls a model, or executes a Tool.  The caller must provide one explicit local
activation plus every transaction-scoped adapter factory, then invoke
``bootstrap_local_pure_agent_runtime`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Literal, Protocol

from pydantic import Field, model_validator
from sqlalchemy.engine import make_url

from .action_runtime import DynamicActionLoopRuntime
from .capability_executors import CapabilityExecutorFactories
from .common import Reference, StrictContract
from .context_runtime import ContextAssemblerRuntime
from .main_agent_boundary import (
    MainAgentBoundaryInputsProvider,
    PersistedMainAgentDecisionBoundaryProvider,
)
from .repository import PureAgentRepository
from .runtime_composition import (
    PureAgentRuntimeComponentFactories,
    PureAgentRuntimeCompositionRoot,
    RuntimeCompositionStatus,
    RuntimeDispatcherInstaller,
    build_capability_handler_registry,
)
from .runtime_config import ApplicationSettingsView, PureAgentFeatureConfig
from .runtime_controller import (
    DisabledRuntimeDispatcher,
    RuntimeAdmissionContextProvider,
)
from .slot_validation import SlotValidatorRegistry
from .tool_runtime import canonical_hash


class LocalBootstrapError(RuntimeError):
    """The explicit local Runtime Bootstrap contract is invalid."""


class LocalBootstrapStatus(str, Enum):
    REJECTED = "rejected"
    DISABLED = "disabled"
    INCOMPLETE = "incomplete"
    READY = "ready"


class LocalBootstrapSettingsView(ApplicationSettingsView, Protocol):
    app_env: str
    public_access_enabled: bool
    database_url: str


class LocalRuntimeBootstrapRequest(StrictContract):
    """One deliberate local installation request; no implicit default exists."""

    schema_name: Literal["bid.pure-agent.local-bootstrap.v1"] = (
        "bid.pure-agent.local-bootstrap.v1"
    )
    activation_ref: Reference
    requested_by_ref: Reference
    target_environment: Literal["isolated_local_development"]
    install_requested: Literal[True]


class LocalIsolationDecision(StrictContract):
    allowed: bool
    app_environment: str = Field(min_length=1, max_length=40)
    database_target_kind: Literal["sqlite", "loopback", "rejected"]
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)

    @model_validator(mode="after")
    def validate_decision(self) -> "LocalIsolationDecision":
        if self.allowed == bool(self.reason_codes):
            raise ValueError(
                "allowed local isolation decision must have no rejection reasons"
            )
        if self.allowed and self.database_target_kind == "rejected":
            raise ValueError("allowed local isolation requires a local database")
        if not self.allowed and self.database_target_kind != "rejected":
            raise ValueError("rejected local isolation must hide database details")
        return self


class LocalRuntimeBootstrapResult(StrictContract):
    schema_name: Literal["bid.pure-agent.local-bootstrap-result.v1"] = (
        "bid.pure-agent.local-bootstrap-result.v1"
    )
    bootstrap_ref: Reference
    bootstrap_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    activation_ref: Reference
    status: LocalBootstrapStatus
    dispatcher_installed: bool
    runtime_available: bool
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    registered_action_kinds: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=5,
    )
    isolation: LocalIsolationDecision

    @classmethod
    def build(
        cls,
        *,
        request: LocalRuntimeBootstrapRequest,
        status: LocalBootstrapStatus,
        dispatcher_installed: bool,
        runtime_available: bool,
        reason_codes: tuple[str, ...],
        registered_action_kinds: tuple[str, ...],
        isolation: LocalIsolationDecision,
    ) -> "LocalRuntimeBootstrapResult":
        body = {
            "activation_ref": request.activation_ref,
            "status": status.value,
            "dispatcher_installed": dispatcher_installed,
            "runtime_available": runtime_available,
            "reason_codes": list(reason_codes),
            "registered_action_kinds": list(registered_action_kinds),
            "isolation": isolation.model_dump(mode="json"),
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            bootstrap_ref=f"local-bootstrap:{digest.removeprefix('sha256:')}",
            bootstrap_hash=digest,
        )

    @model_validator(mode="after")
    def validate_result(self) -> "LocalRuntimeBootstrapResult":
        body = self.model_dump(
            mode="json",
            exclude={"schema_name", "bootstrap_ref", "bootstrap_hash"},
        )
        digest = canonical_hash(body)
        if self.bootstrap_hash != digest:
            raise ValueError("local Bootstrap result hash does not match")
        if self.bootstrap_ref != (
            f"local-bootstrap:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("local Bootstrap result ref does not match")
        if self.runtime_available != (self.status is LocalBootstrapStatus.READY):
            raise ValueError("only a ready local Bootstrap may expose Runtime authority")
        if not self.dispatcher_installed:
            raise ValueError("Bootstrap must install either ready or disabled dispatcher")
        return self


class LocalRuntimeIsolationGuard:
    """Reject public, production, ECS-style, and remote database targets."""

    _LOCAL_APP_ENVS = frozenset({"dev", "development", "local", "test"})
    _LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

    def evaluate(
        self,
        *,
        settings: LocalBootstrapSettingsView,
        request: LocalRuntimeBootstrapRequest,
    ) -> LocalIsolationDecision:
        del request  # The Literal contract already proves the requested target.
        app_environment = str(settings.app_env).strip().lower() or "unknown"
        reasons: list[str] = []
        if app_environment not in self._LOCAL_APP_ENVS:
            reasons.append("APP_ENV_NOT_LOCAL")
        if bool(settings.public_access_enabled):
            reasons.append("PUBLIC_ACCESS_FORBIDDEN")

        database_kind = "rejected"
        try:
            database_url = make_url(str(settings.database_url))
            backend = database_url.get_backend_name().lower()
            host = (database_url.host or "").strip().lower()
        except Exception:
            reasons.append("DATABASE_URL_INVALID")
        else:
            if backend == "sqlite":
                database_kind = "sqlite"
            elif host in self._LOOPBACK_HOSTS:
                database_kind = "loopback"
            else:
                reasons.append("REMOTE_DATABASE_FORBIDDEN")

        if reasons:
            return LocalIsolationDecision(
                allowed=False,
                app_environment=app_environment[:40],
                database_target_kind="rejected",
                reason_codes=tuple(dict.fromkeys(reasons)),
            )
        return LocalIsolationDecision(
            allowed=True,
            app_environment=app_environment[:40],
            database_target_kind=database_kind,
            reason_codes=(),
        )


@dataclass(frozen=True, slots=True)
class LocalPureAgentRuntimeAdapters:
    """Complete adapter factories required by the explicit local Bootstrap.

    Every callable constructs a transaction-scoped or invocation-scoped
    component.  Constructing this object performs no I/O.
    """

    context_assembler: Callable[[], ContextAssemblerRuntime] | None
    main_agent_inputs: Callable[
        [PureAgentRepository], MainAgentBoundaryInputsProvider
    ]
    admission_context: Callable[
        [PureAgentRepository], RuntimeAdmissionContextProvider
    ]
    action_loop: Callable[[], DynamicActionLoopRuntime]
    capability_executors: CapabilityExecutorFactories
    slot_validators: SlotValidatorRegistry
    context_assembler_for_repository: (
        Callable[[PureAgentRepository], ContextAssemblerRuntime] | None
    ) = None

    def __post_init__(self) -> None:
        assembler_factories = (
            self.context_assembler,
            self.context_assembler_for_repository,
        )
        if sum(factory is not None for factory in assembler_factories) != 1:
            raise TypeError(
                "exactly one Context Assembler factory must be configured"
            )
        if any(
            factory is not None and not callable(factory)
            for factory in assembler_factories
        ):
            raise TypeError("Context Assembler factory is not callable")
        for name in (
            "main_agent_inputs",
            "admission_context",
            "action_loop",
        ):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} local adapter factory is not callable")
        if not isinstance(self.capability_executors, CapabilityExecutorFactories):
            raise TypeError("capability_executors must use the C02-3 Factory contract")
        if not isinstance(self.slot_validators, SlotValidatorRegistry):
            raise TypeError("slot_validators must be an explicit SlotValidatorRegistry")

    def component_factories(self) -> PureAgentRuntimeComponentFactories:
        """Build C02-1 components without creating a workflow or doing I/O."""

        def boundary_provider(
            repository: PureAgentRepository,
        ) -> PersistedMainAgentDecisionBoundaryProvider:
            if self.context_assembler_for_repository is not None:
                assembler = self.context_assembler_for_repository(repository)
            else:
                factory = self.context_assembler
                if factory is None:
                    raise LocalBootstrapError(
                        "Context Assembler factory is not configured"
                    )
                assembler = factory()
            inputs = self.main_agent_inputs(repository)
            if not isinstance(assembler, ContextAssemblerRuntime):
                raise LocalBootstrapError(
                    "context_assembler factory returned an invalid component"
                )
            if not callable(getattr(inputs, "prepare", None)):
                raise LocalBootstrapError(
                    "main_agent_inputs factory returned an invalid provider"
                )
            return PersistedMainAgentDecisionBoundaryProvider(
                repository,
                context_assembler=assembler,
                inputs_provider=inputs,
            )

        return PureAgentRuntimeComponentFactories(
            boundary_provider=boundary_provider,
            admission_context_provider=self.admission_context,
            action_loop=self.action_loop,
            capability_handlers=build_capability_handler_registry(
                self.capability_executors.handler_factories()
            ),
        )


def bootstrap_local_pure_agent_runtime(
    *,
    request: LocalRuntimeBootstrapRequest,
    settings: LocalBootstrapSettingsView,
    session_factory: Callable[[], Any],
    continuation_secret: str | bytes | None,
    adapters: LocalPureAgentRuntimeAdapters,
    installer: RuntimeDispatcherInstaller,
    max_pulses_per_dispatch: int = 64,
    isolation_guard: LocalRuntimeIsolationGuard | None = None,
) -> LocalRuntimeBootstrapResult:
    """Install one ready or disabled dispatcher at an explicit local boundary.

    The disabled dispatcher is installed first.  Any later validation or
    composition failure therefore cannot leave stale execution authority.
    This function composes dependencies only; it does not open a Session or
    execute an Agent pulse.
    """

    if not callable(installer):
        raise LocalBootstrapError("Runtime dispatcher installer is invalid")
    installer(DisabledRuntimeDispatcher())
    isolation = (isolation_guard or LocalRuntimeIsolationGuard()).evaluate(
        settings=settings,
        request=request,
    )
    if not isolation.allowed:
        return LocalRuntimeBootstrapResult.build(
            request=request,
            status=LocalBootstrapStatus.REJECTED,
            dispatcher_installed=True,
            runtime_available=False,
            reason_codes=isolation.reason_codes,
            registered_action_kinds=(),
            isolation=isolation,
        )

    components = adapters.component_factories()
    composition = PureAgentRuntimeCompositionRoot(
        feature_config=PureAgentFeatureConfig.from_application_settings(settings),
        continuation_secret=continuation_secret,
        session_factory=session_factory,
        component_factories=components,
        max_pulses_per_dispatch=max_pulses_per_dispatch,
    ).compose_and_install(installer)
    status = {
        RuntimeCompositionStatus.READY: LocalBootstrapStatus.READY,
        RuntimeCompositionStatus.DISABLED: LocalBootstrapStatus.DISABLED,
        RuntimeCompositionStatus.INCOMPLETE: LocalBootstrapStatus.INCOMPLETE,
    }[composition.status]
    return LocalRuntimeBootstrapResult.build(
        request=request,
        status=status,
        dispatcher_installed=True,
        runtime_available=composition.available,
        reason_codes=composition.reason_codes,
        registered_action_kinds=tuple(
            kind.value for kind in composition.registered_action_kinds
        ),
        isolation=isolation,
    )
