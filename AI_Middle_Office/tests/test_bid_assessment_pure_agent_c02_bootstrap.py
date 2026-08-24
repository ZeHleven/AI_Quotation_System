from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.action_runtime import (
    AgentActionKind,
    DynamicActionLoopRuntime,
)
from app.agents.bid_assessment_pure.capability_executors import (
    CapabilityExecutorFactories,
)
from app.agents.bid_assessment_pure.context_runtime import ContextAssemblerRuntime
from app.agents.bid_assessment_pure.local_bootstrap import (
    LocalBootstrapStatus,
    LocalPureAgentRuntimeAdapters,
    LocalRuntimeBootstrapRequest,
    bootstrap_local_pure_agent_runtime,
)
from app.agents.bid_assessment_pure.planner_runtime import PlannerRuntime
from app.agents.bid_assessment_pure.repository import PureAgentRepository
from app.agents.bid_assessment_pure.runtime_composition import (
    CAPABILITY_ACTION_KINDS,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    DisabledRuntimeDispatcher,
    LocalRuntimePulseDispatcher,
)
from app.agents.bid_assessment_pure.slot_validation import SlotValidatorRegistry
from app.api.v1 import bid_assessment_pure_agent as conversation_api


SECRET = "c02-local-continuation-secret-at-least-32-bytes"


class _NeverInputsProvider:
    async def prepare(self, **kwargs):
        del kwargs
        raise AssertionError("Bootstrap must not assemble Main Agent inputs")


class _NeverAdmissionProvider:
    def for_action(self, **kwargs):
        del kwargs
        raise AssertionError("Bootstrap must not admit an Action")


class _NeverPlanBoundary:
    async def prepare(self, **kwargs):
        del kwargs
        raise AssertionError("Bootstrap must not invoke Planner boundary")


class _NeverToolBoundary:
    async def prepare(self, **kwargs):
        del kwargs
        raise AssertionError("Bootstrap must not invoke Tool boundary")


class _NeverAnswerBoundary:
    async def prepare(self, **kwargs):
        del kwargs
        raise AssertionError("Bootstrap must not invoke Answer boundary")


class _NeverToolGateway:
    async def execute(self, **kwargs):
        del kwargs
        raise AssertionError("Bootstrap must not execute a Tool")


class _NeverSessionFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("Bootstrap must not open a database Session")


def _request() -> LocalRuntimeBootstrapRequest:
    return LocalRuntimeBootstrapRequest(
        activation_ref="activation:c02-local",
        requested_by_ref="user:c02-local-developer",
        target_environment="isolated_local_development",
        install_requested=True,
    )


def _settings(**overrides):
    values = {
        "app_env": "development",
        "public_access_enabled": False,
        "database_url": "sqlite:///./tests/.c02-isolated.db",
        "feature_bid_assessment_pure_agent": True,
        "feature_bid_assessment_pure_agent_runtime": True,
        "bid_assessment_pure_agent_continuation_secret": SECRET,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _adapters(
    *,
    slot_validators: SlotValidatorRegistry | None = None,
) -> LocalPureAgentRuntimeAdapters:
    capabilities = CapabilityExecutorFactories(
        planner=lambda: PlannerRuntime(),
        plan_boundary=lambda repository: _NeverPlanBoundary(),
        tool_boundary=lambda repository: _NeverToolBoundary(),
        tool_gateway=lambda repository: _NeverToolGateway(),
        answer_boundary=lambda repository: _NeverAnswerBoundary(),
    )
    return LocalPureAgentRuntimeAdapters(
        context_assembler=lambda: ContextAssemblerRuntime(),
        main_agent_inputs=lambda repository: _NeverInputsProvider(),
        admission_context=lambda repository: _NeverAdmissionProvider(),
        action_loop=lambda: DynamicActionLoopRuntime(),
        capability_executors=capabilities,
        slot_validators=slot_validators or SlotValidatorRegistry(),
    )


@pytest.mark.parametrize(
    ("settings", "expected_codes"),
    (
        (
            _settings(
                app_env="production",
                public_access_enabled=True,
                database_url="mysql+pymysql://user:secret@ai-mysql/pure_agent",
            ),
            {
                "APP_ENV_NOT_LOCAL",
                "PUBLIC_ACCESS_FORBIDDEN",
                "REMOTE_DATABASE_FORBIDDEN",
            },
        ),
        (
            _settings(
                database_url="mysql+pymysql://user:secret@192.168.88.128/pure_agent",
            ),
            {"REMOTE_DATABASE_FORBIDDEN"},
        ),
    ),
)
def test_local_bootstrap_rejects_non_local_targets_before_adapter_or_session_use(
    settings,
    expected_codes,
) -> None:
    session_factory = _NeverSessionFactory()
    installed = []

    result = bootstrap_local_pure_agent_runtime(
        request=_request(),
        settings=settings,
        session_factory=session_factory,
        continuation_secret=SECRET,
        adapters=_adapters(),
        installer=installed.append,
    )

    assert result.status is LocalBootstrapStatus.REJECTED
    assert result.runtime_available is False
    assert set(result.reason_codes) == expected_codes
    assert result.registered_action_kinds == ()
    assert len(installed) == 1
    assert isinstance(installed[0], DisabledRuntimeDispatcher)
    assert session_factory.calls == 0


def test_local_bootstrap_request_requires_explicit_local_install_activation() -> None:
    with pytest.raises(ValidationError):
        LocalRuntimeBootstrapRequest(
            activation_ref="activation:c02-invalid",
            requested_by_ref="user:c02-local-developer",
            target_environment="isolated_local_development",
            install_requested=False,
        )
    with pytest.raises(ValidationError):
        LocalRuntimeBootstrapRequest(
            activation_ref="activation:c02-invalid-target",
            requested_by_ref="user:c02-local-developer",
            target_environment="production",
            install_requested=True,
        )


def test_local_bootstrap_preserves_default_disabled_authority() -> None:
    session_factory = _NeverSessionFactory()
    installed = []

    result = bootstrap_local_pure_agent_runtime(
        request=_request(),
        settings=_settings(
            feature_bid_assessment_pure_agent=False,
            feature_bid_assessment_pure_agent_runtime=False,
        ),
        session_factory=session_factory,
        continuation_secret=SECRET,
        adapters=_adapters(),
        installer=installed.append,
    )

    assert result.status is LocalBootstrapStatus.DISABLED
    assert result.runtime_available is False
    assert set(result.reason_codes) == {
        "PURE_AGENT_API_DISABLED",
        "PURE_AGENT_RUNTIME_DISABLED",
    }
    assert len(installed) == 2
    assert all(isinstance(item, DisabledRuntimeDispatcher) for item in installed)
    assert session_factory.calls == 0


def test_local_bootstrap_reports_incomplete_without_opening_a_session() -> None:
    installed = []

    result = bootstrap_local_pure_agent_runtime(
        request=_request(),
        settings=_settings(),
        session_factory=None,  # type: ignore[arg-type]
        continuation_secret=SECRET,
        adapters=_adapters(),
        installer=installed.append,
    )

    assert result.status is LocalBootstrapStatus.INCOMPLETE
    assert result.runtime_available is False
    assert result.reason_codes == ("SESSION_FACTORY_MISSING",)
    assert len(installed) == 2
    assert all(isinstance(item, DisabledRuntimeDispatcher) for item in installed)


def test_local_bootstrap_ready_freezes_all_five_handlers_without_running_them() -> None:
    session_factory = _NeverSessionFactory()
    adapters = _adapters()
    components = adapters.component_factories()
    installed = []

    assert components.capability_handlers.complete is True
    assert set(components.capability_handlers.registered_action_kinds) == set(
        CAPABILITY_ACTION_KINDS
    )
    components.capability_handlers.build_executor(PureAgentRepository(object()))

    result = bootstrap_local_pure_agent_runtime(
        request=_request(),
        settings=_settings(),
        session_factory=session_factory,
        continuation_secret=SECRET,
        adapters=adapters,
        installer=installed.append,
    )

    assert result.status is LocalBootstrapStatus.READY
    assert result.runtime_available is True
    assert result.reason_codes == ("LOCAL_RUNTIME_COMPOSITION_READY",)
    assert set(result.registered_action_kinds) == {
        kind.value for kind in CAPABILITY_ACTION_KINDS
    }
    assert len(installed) == 2
    assert isinstance(installed[0], DisabledRuntimeDispatcher)
    assert isinstance(installed[1], LocalRuntimePulseDispatcher)
    assert session_factory.calls == 0


def test_application_bootstrap_installs_dispatcher_and_slot_registry_atomically(
    monkeypatch,
) -> None:
    session_factory = _NeverSessionFactory()
    slot_validators = SlotValidatorRegistry()
    adapters = _adapters(slot_validators=slot_validators)
    monkeypatch.setattr(conversation_api, "settings", _settings())
    monkeypatch.setattr(conversation_api, "SessionLocal", session_factory)
    conversation_api.configure_pure_agent_runtime_dispatcher(
        DisabledRuntimeDispatcher()
    )
    conversation_api.configure_pure_agent_slot_validator_registry(
        SlotValidatorRegistry()
    )

    try:
        result = conversation_api.bootstrap_pure_agent_local_runtime(
            request=_request(),
            adapters=adapters,
        )

        assert result.status is LocalBootstrapStatus.READY
        assert conversation_api.get_pure_agent_local_bootstrap_result() == result
        assert isinstance(
            conversation_api.get_pure_agent_runtime_dispatcher(),
            LocalRuntimePulseDispatcher,
        )
        assert (
            conversation_api.get_pure_agent_slot_validator_registry()
            is slot_validators
        )
        assert session_factory.calls == 0

        with pytest.raises(RuntimeError, match="requires bootstrap"):
            conversation_api.configure_pure_agent_runtime_dispatcher(
                LocalRuntimePulseDispatcher(
                    session_factory=session_factory,
                    controller_factory=lambda session: None,
                )
            )

        monkeypatch.setattr(
            conversation_api,
            "settings",
            _settings(app_env="production"),
        )
        rejected = conversation_api.bootstrap_pure_agent_local_runtime(
            request=_request(),
            adapters=adapters,
        )
        assert rejected.status is LocalBootstrapStatus.REJECTED
        assert isinstance(
            conversation_api.get_pure_agent_runtime_dispatcher(),
            DisabledRuntimeDispatcher,
        )
        assert (
            conversation_api.get_pure_agent_slot_validator_registry()
            is not slot_validators
        )
    finally:
        conversation_api.configure_pure_agent_runtime_dispatcher(
            DisabledRuntimeDispatcher()
        )
        conversation_api.configure_pure_agent_slot_validator_registry(
            SlotValidatorRegistry()
        )


def test_capability_registry_excludes_main_agent_decision_handler() -> None:
    registered = set(_adapters().component_factories().capability_handlers.registered_action_kinds)

    assert registered == {
        AgentActionKind.PLAN,
        AgentActionKind.REPLAN,
        AgentActionKind.TOOL_CALL_BATCH,
        AgentActionKind.REQUEST_INFORMATION,
        AgentActionKind.ANSWER,
    }
    assert AgentActionKind.MAIN_AGENT_DECISION not in registered

