from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.registry  # noqa: F401  # Register local FK targets only.
from app.agents.bid_assessment_pure.action_runtime import (
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
    ActionReservationIntent,
    AgentActionKind,
)
from app.agents.bid_assessment_pure.repository import (
    PersistedObservationArtifactRow,
    PureAgentRepository,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyStatus,
    ContextConsumer,
    ContextSnapshot,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    PersistedRuntimeAction,
    PureAgentRuntimeController,
    RunningActionRecoveryContext,
    RunningActionRecoveryController,
    RuntimeActionPersistenceEnvelope,
    RuntimeActionRecoveryBinding,
    RuntimePostAction,
    RuntimePulseDirective,
    RuntimePulseDisposition,
    RuntimeWakeReason,
    RuntimeWakeup,
)
from app.agents.bid_assessment_pure.runtime_guards import (
    CancellationSnapshot,
    EffectReplayPolicy,
    RecoveryDirective,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeResourceType,
)
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash
from app.core.database import Base


def _profile() -> RuntimeProfileSnapshot:
    limits = RuntimeLimitSet(
        max_active_duration_ms=60_000,
        max_model_calls=2,
        max_tool_calls=4,
        max_total_input_tokens=8_000,
        max_total_output_tokens=2_000,
        max_cost_microunits=10_000,
        max_replans=1,
        max_answer_repairs=1,
        max_no_progress_actions=2,
        max_retry_attempts=1,
        max_parallel_read_calls=2,
        model_timeout_ms=30_000,
        tool_timeout_ms=10_000,
    )
    policy = RuntimePolicyCeiling.build(
        policy_ref="policy:c03-4",
        limits=limits,
    )
    return RuntimeProfileSnapshot.build(
        profile_ref="profile:c03-4",
        policy=policy,
        limits=limits,
    )


def _task() -> AgentTaskState:
    return AgentTaskState(
        task_id="task:c03-4",
        session_id="conversation:c03-4",
        state_version=2,
        status=AgentTaskStatus.RUNNING,
        execution_mode="direct",
        goal_ref="goal:c03-4",
        plan_ref=None,
        pending_context=None,
        in_flight_action_ref="action:c03-4",
        observation_refs=(),
        last_error_ref=None,
    )


def _intent() -> ActionReservationIntent:
    arguments: dict[str, Any] = {
        "turn_ref": "turn:c03-4",
        "execution_mode": "direct",
        "plan_ref": None,
        "observation_refs": [],
    }
    body = {
        "task_ref": "task:c03-4",
        "state_version": 1,
        "decision_ref": None,
        "decision_hash": None,
        "decision_observation_ref": None,
        "action_kind": AgentActionKind.MAIN_AGENT_DECISION.value,
        "arguments": arguments,
        "arguments_hash": canonical_hash(arguments),
        "effect_identity_seed": "effect-seed:c03-4",
        "context_snapshot_ref": "context:c03-4",
        "context_snapshot_hash": canonical_hash({"context": "c03-4"}),
        "registry_snapshot_ref": "registry:c03-4",
        "registry_snapshot_hash": canonical_hash({"registry": "c03-4"}),
        "visible_tools_hash": canonical_hash({"tools": []}),
    }
    digest = canonical_hash(body)
    return ActionReservationIntent(
        **body,
        intent_ref=f"action-intent:{digest.removeprefix('sha256:')}",
        intent_hash=digest,
    )


def _binding() -> RuntimeActionRecoveryBinding:
    return RuntimeActionRecoveryBinding.build(
        profile=_profile(),
        authorization_policy_ref="authorization-policy:c03-4",
        scope_snapshot_hash=canonical_hash({"scope": "c03-4"}),
    )


def _artifact() -> PersistedObservationArtifactRow:
    result = {"decision": "continue", "basis": ["fact:c03-4"]}
    body = {
        "task_ref": "task:c03-4",
        "source_action_ref": "action:c03-4",
        "action_sequence": 1,
        "state_version": 2,
        "kind": ActionObservationKind.CONTROL_DECISION.value,
        "status": ActionObservationStatus.SUCCEEDED.value,
        "artifact_ref": "artifact:c03-4",
        "artifact_hash": canonical_hash(result),
        "summary": "已取得持久化决定。",
        "material_progress": True,
        "progress_signal_refs": ["fact:c03-4"],
        "limitation_codes": [],
    }
    digest = canonical_hash(body)
    observation = ActionObservation(
        **body,
        observation_ref=f"observation:{digest.removeprefix('sha256:')}",
        observation_hash=digest,
    )
    return PersistedObservationArtifactRow(
        observation=observation,
        artifact=result,
        context_snapshot_ref="context:c03-4",
    )


def _action(
    *,
    status: str = "succeeded",
    effect_status: str = "succeeded",
    artifact: PersistedObservationArtifactRow | None = None,
) -> PersistedRuntimeAction:
    persisted_artifact = artifact or _artifact()
    has_result = effect_status in {"succeeded", "failed"}
    intent = _intent()
    return PersistedRuntimeAction(
        action_ref="action:c03-4",
        sequence=1,
        action_kind=AgentActionKind.MAIN_AGENT_DECISION,
        status=status,
        effect_fence_ref="effect:c03-4",
        effect_status=effect_status,
        fencing_token=1,
        effect_key="effect-key:c03-4",
        effect_request_hash=intent.arguments_hash,
        effect_replay_policy=EffectReplayPolicy.SAFE_IDEMPOTENT,
        effect_result_ref=(
            persisted_artifact.observation.artifact_ref if has_result else None
        ),
        effect_result_hash=(
            persisted_artifact.observation.artifact_hash if has_result else None
        ),
        effect_error_code=None,
        envelope=RuntimeActionPersistenceEnvelope.build(
            intent=intent,
            recovery_binding=_binding(),
        ),
    )


@dataclass
class _Repository:
    artifact: PersistedObservationArtifactRow | None
    loads: int = 0
    budget_checks: int = 0

    def load_running_action_observation_artifact(
        self,
        *,
        task_id: str,
        action_id: str,
    ) -> PersistedObservationArtifactRow | None:
        assert task_id == "task:c03-4"
        assert action_id == "action:c03-4"
        self.loads += 1
        return self.artifact

    def assert_action_budget_settled(
        self,
        *,
        task_id: str,
        action_id: str,
    ) -> None:
        assert task_id == "task:c03-4"
        assert action_id == "action:c03-4"
        self.budget_checks += 1


@dataclass
class _ContextProvider:
    registry_hash: str

    def for_recovery(self, *, task, action, binding):
        assert action.envelope.recovery_binding == binding
        return RunningActionRecoveryContext(
            profile=_profile(),
            current_registry_snapshot_hash=self.registry_hash,
            cancellation=CancellationSnapshot(
                task_ref=task.task_id,
                state_version=task.state_version,
                cancellation_fence_ref=None,
            ),
            lease_expired=True,
            authorization_valid=True,
            source_heads_valid=True,
            retry_attempt_count=0,
        )


def _controller(
    repository: _Repository,
    *,
    registry_hash: str | None = None,
) -> RunningActionRecoveryController:
    return RunningActionRecoveryController(
        repository,  # type: ignore[arg-type]
        context_provider=_ContextProvider(
            registry_hash=registry_hash or _intent().registry_snapshot_hash
        ),
    )


def test_persisted_terminal_result_is_consumed_without_effect_replay() -> None:
    artifact = _artifact()
    repository = _Repository(artifact=artifact)

    plan = _controller(repository).assess(
        task=_task(),
        action=_action(artifact=artifact),
    )

    assert plan.directive is RecoveryDirective.CONSUME_PERSISTED_RESULT
    assert plan.execution is not None
    assert plan.execution.result_payload == artifact.artifact
    assert repository.loads == 1
    assert repository.budget_checks == 1


def test_terminal_receipt_without_result_body_fails_closed() -> None:
    repository = _Repository(artifact=None)

    plan = _controller(repository).assess(task=_task(), action=_action())

    assert plan.directive is RecoveryDirective.BLOCKED
    assert plan.reason_codes == ("PERSISTED_RESULT_BODY_UNAVAILABLE",)
    assert plan.execution is None
    assert repository.budget_checks == 0


def test_safe_retry_is_reported_but_never_executed_by_recovery_controller() -> None:
    repository = _Repository(artifact=None)

    plan = _controller(repository).assess(
        task=_task(),
        action=_action(status="running", effect_status="running"),
    )

    assert plan.directive is RecoveryDirective.RETRY_SAFE
    assert plan.execution is None
    assert repository.loads == 1


def test_registry_drift_blocks_persisted_result_consumption() -> None:
    artifact = _artifact()
    repository = _Repository(artifact=artifact)

    plan = _controller(
        repository,
        registry_hash=canonical_hash({"registry": "changed"}),
    ).assess(task=_task(), action=_action(artifact=artifact))

    assert plan.directive is RecoveryDirective.BLOCKED
    assert plan.reason_codes == ("REGISTRY_SNAPSHOT_CHANGED",)
    assert plan.execution is None


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _persist_terminal_action(
    repo: PureAgentRepository,
    *,
    suffix: str,
    settle_budget: bool,
) -> tuple[str, AgentTaskState, RuntimeProfileSnapshot]:
    conversation = repo.create_conversation(
        owner_id=1,
        tenant_ref="tenant:c03-4-integration",
        conversation_id=f"conversation:c03-4-{suffix}",
    )
    message = repo.append_message(
        conversation_id=conversation.id,
        role="user",
        message_type="user.task_trigger",
        content={"text": "请研判投标风险"},
        created_by_ref="user:1",
        idempotency_key=f"message:c03-4-{suffix}",
    )
    task = repo.create_task(
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        owner_id=1,
        goal_ref=f"goal:c03-4-{suffix}",
    )
    context_hash = canonical_hash({"context": suffix})
    context_ref = f"context:c03-4-{suffix}"
    repo.store_context_snapshot(
        ContextSnapshot(
            snapshot_ref=context_ref,
            snapshot_sequence=task.state_version,
            task_ref=task.task_id,
            state_version=task.state_version,
            consumer=ContextConsumer.MAIN_AGENT,
            status=ContextAssemblyStatus.READY,
            request_hash=canonical_hash({"request": suffix}),
            policy_snapshot_ref="policy-snapshot:c03-4",
            prompt_template_ref="prompt-template:c03-4",
            model_profile_ref="model-context-profile:c03-4",
            model_profile_hash=canonical_hash({"model": "c03-4"}),
            context_profile_ref="context-profile:c03-4",
            context_profile_hash=canonical_hash({"context-profile": "c03-4"}),
            registry_snapshot_ref=None,
            registry_snapshot_hash=None,
            authorization_snapshot_ref="authorization:c03-4",
            dependency_refs=(),
            included_entries=(),
            excluded_entries=(),
            compression_receipts=(),
            included_refs=(),
            excluded_refs=(),
            limitation_messages=(),
            estimated_input_tokens=1,
            effective_input_budget=1_000,
            reserved_output_tokens=100,
            safety_margin_tokens=10,
            projection_hash=canonical_hash({"projection": suffix}),
            snapshot_hash=context_hash,
        )
    )
    arguments = {
        "turn_ref": f"turn:c03-4-{suffix}",
        "execution_mode": "direct",
        "plan_ref": None,
        "observation_refs": [],
    }
    intent_body = {
        "task_ref": task.task_id,
        "state_version": task.state_version,
        "decision_ref": None,
        "decision_hash": None,
        "decision_observation_ref": None,
        "action_kind": AgentActionKind.MAIN_AGENT_DECISION.value,
        "arguments": arguments,
        "arguments_hash": canonical_hash(arguments),
        "effect_identity_seed": f"effect-seed:c03-4-{suffix}",
        "context_snapshot_ref": context_ref,
        "context_snapshot_hash": context_hash,
        "registry_snapshot_ref": None,
        "registry_snapshot_hash": None,
        "visible_tools_hash": None,
    }
    intent_hash = canonical_hash(intent_body)
    intent = ActionReservationIntent(
        **intent_body,
        intent_ref=f"action-intent:{intent_hash.removeprefix('sha256:')}",
        intent_hash=intent_hash,
    )
    profile = _profile()
    envelope = RuntimeActionPersistenceEnvelope.build(
        intent=intent,
        recovery_binding=RuntimeActionRecoveryBinding.build(
            profile=profile,
            authorization_policy_ref="authorization-policy:c03-4",
            scope_snapshot_hash=canonical_hash({"scope": suffix}),
        ),
    )
    repo.create_budget_account(
        task_id=task.task_id,
        resource_type=RuntimeResourceType.ACTIVE_DURATION_MS.value,
        unit="millisecond",
        limit_amount=profile.limits.max_active_duration_ms,
    )
    reservation = repo.reserve_action(
        task_id=task.task_id,
        event_id=f"runtime-action-reserve:c03-4-{suffix}",
        action_type=AgentActionKind.MAIN_AGENT_DECISION.value,
        execution_kind="direct",
        arguments=envelope.model_dump(mode="json"),
        effect_key=f"effect-key:c03-4-{suffix}",
        effect_type="main_agent_decision",
        replay_policy=EffectReplayPolicy.SAFE_IDEMPOTENT.value,
        fencing_token=1,
        effect_request_hash=intent.arguments_hash,
    )
    budget_reservation = repo.reserve_budget(
        task_id=task.task_id,
        resource_type=RuntimeResourceType.ACTIVE_DURATION_MS.value,
        amount=500,
        idempotency_key=f"budget-reserve:c03-4-{suffix}",
        action_id=reservation.action_id,
    )
    repo.mark_effect_running(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=reservation.fencing_token,
        expected_state_version=reservation.state.state_version,
    )
    result = {"decision": "continue", "suffix": suffix}
    observation_body = {
        "task_ref": task.task_id,
        "source_action_ref": reservation.action_id,
        "action_sequence": 1,
        "state_version": reservation.state.state_version,
        "kind": ActionObservationKind.CONTROL_DECISION.value,
        "status": ActionObservationStatus.SUCCEEDED.value,
        "artifact_ref": f"artifact:c03-4-{suffix}",
        "artifact_hash": canonical_hash(result),
        "summary": "持久化结果等待恢复消费。",
        "material_progress": True,
        "progress_signal_refs": [f"fact:c03-4-{suffix}"],
        "limitation_codes": [],
    }
    observation_hash = canonical_hash(observation_body)
    observation = ActionObservation(
        **observation_body,
        observation_ref=(
            f"observation:{observation_hash.removeprefix('sha256:')}"
        ),
        observation_hash=observation_hash,
    )
    repo.settle_effect(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=reservation.fencing_token,
        expected_state_version=reservation.state.state_version,
        status="succeeded",
        result_ref=observation.artifact_ref,
        result=result,
    )
    if settle_budget:
        repo.settle_budget(
            task_id=task.task_id,
            resource_type=RuntimeResourceType.ACTIVE_DURATION_MS.value,
            reservation_entry_id=budget_reservation.entry_id,
            actual_amount=250,
            idempotency_key=f"budget-settle:c03-4-{suffix}",
            action_id=reservation.action_id,
        )
    repo.store_observation_artifact(
        observation,
        artifact=result,
        context_snapshot_ref=context_ref,
    )
    return conversation.id, reservation.state, profile


@dataclass
class _IntegrationRecoveryProvider:
    profile: RuntimeProfileSnapshot

    def for_recovery(self, *, task, action, binding):
        assert binding.profile == self.profile
        return RunningActionRecoveryContext(
            profile=self.profile,
            current_registry_snapshot_hash=None,
            cancellation=CancellationSnapshot(
                task_ref=task.task_id,
                state_version=task.state_version,
                cancellation_fence_ref=None,
            ),
            lease_expired=True,
            authorization_valid=True,
            source_heads_valid=True,
            retry_attempt_count=0,
        )


class _RecoveryOnlyDriver:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.after_calls = 0

    async def prepare_next_action(self, *, task, wakeup):
        raise AssertionError("recovery must not prepare a replacement Action")

    async def execute_active_action(self, *, task, action):
        self.execute_calls += 1
        raise AssertionError("recovery must not execute the persisted Effect")

    async def after_observation(self, *, task, action, execution):
        self.after_calls += 1
        assert execution.observation.observation_ref in task.observation_refs
        return RuntimePostAction(directive=RuntimePulseDirective.CONTINUE)


def test_runtime_controller_consumes_persisted_result_without_reexecution(
    sqlite_engine,
) -> None:
    SessionFactory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    setup_session: Session = SessionFactory()
    conversation_ref, state, profile = _persist_terminal_action(
        PureAgentRepository(setup_session),
        suffix="consume",
        settle_budget=True,
    )
    setup_session.commit()
    setup_session.close()

    session: Session = SessionFactory()
    repo = PureAgentRepository(session)
    driver = _RecoveryOnlyDriver()
    controller = PureAgentRuntimeController(
        repo,
        driver=driver,
        recovery_context_provider=_IntegrationRecoveryProvider(profile),
    )
    outcome = asyncio.run(
        controller.advance_once(
            RuntimeWakeup.build(
                task_ref=state.task_id,
                conversation_ref=conversation_ref,
                observed_state_version=state.state_version,
                reason=RuntimeWakeReason.RECOVERY,
                seed="c03-4-consume",
            )
        )
    )

    recovered = repo.load_task_state(state.task_id)
    assert outcome.disposition is RuntimePulseDisposition.ACTION_COMPLETED
    assert driver.execute_calls == 0
    assert driver.after_calls == 1
    assert recovered.in_flight_action_ref is None
    assert len(recovered.observation_refs) == 1
    session.close()


def test_runtime_controller_blocks_result_with_unsettled_budget(
    sqlite_engine,
) -> None:
    SessionFactory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    setup_session: Session = SessionFactory()
    conversation_ref, state, profile = _persist_terminal_action(
        PureAgentRepository(setup_session),
        suffix="budget-block",
        settle_budget=False,
    )
    setup_session.commit()
    setup_session.close()

    session: Session = SessionFactory()
    repo = PureAgentRepository(session)
    driver = _RecoveryOnlyDriver()
    outcome = asyncio.run(
        PureAgentRuntimeController(
            repo,
            driver=driver,
            recovery_context_provider=_IntegrationRecoveryProvider(profile),
        ).advance_once(
            RuntimeWakeup.build(
                task_ref=state.task_id,
                conversation_ref=conversation_ref,
                observed_state_version=state.state_version,
                reason=RuntimeWakeReason.RECOVERY,
                seed="c03-4-budget-block",
            )
        )
    )

    blocked = repo.load_task_state(state.task_id)
    assert outcome.disposition is RuntimePulseDisposition.RECOVERY_REQUIRED
    assert outcome.recovery_directive is RecoveryDirective.BLOCKED
    assert outcome.reason_codes == ("PERSISTED_RESULT_OR_BUDGET_INVALID",)
    assert driver.execute_calls == 0
    assert driver.after_calls == 0
    assert blocked.in_flight_action_ref is not None
    assert blocked.observation_refs == ()
    session.close()
