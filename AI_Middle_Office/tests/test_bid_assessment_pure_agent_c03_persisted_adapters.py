from __future__ import annotations

import asyncio
from typing import Any, Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models.registry  # noqa: F401  # Register local FK targets only.
from app.agents.bid_assessment_pure.action_runtime import (
    ActionReservationIntent,
    AgentActionKind,
)
from app.agents.bid_assessment_pure.main_agent_boundary import MainAgentTurn
from app.agents.bid_assessment_pure.persisted_local_adapters import (
    LocalActionAdmissionRule,
    LocalAdmissionPolicy,
    LocalBoundaryInputPolicy,
    PersistedAdmissionContextRejected,
    PersistedLocalBoundaryInputsProvider,
    PersistedLocalBoundaryRejected,
    PersistedLocalRuntimeAdapterFactories,
    PersistedRuntimeAdmissionContextProvider,
)
from app.agents.bid_assessment_pure.repository import PureAgentRepository
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyStatus,
    ContextConsumer,
    ContextProfile,
    ContextSnapshot,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    GuardSuiteRuntimeActionGovernor,
    RuntimeActionPersistenceEnvelope,
    RuntimeWakeReason,
    RuntimeWakeup,
)
from app.agents.bid_assessment_pure.runtime_guards import (
    ActionExecutionRequirements,
    ActionRuntimeBinding,
    BudgetDemand,
    EffectFenceStatus,
    EffectReplayPolicy,
    RuntimeActionClass,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeResourceType,
)
from app.agents.bid_assessment_pure.state import (
    AgentTaskState,
    TaskEventType,
    TaskTransitionEvent,
)
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash
from app.core.database import Base


def _limits() -> RuntimeLimitSet:
    return RuntimeLimitSet(
        max_active_duration_ms=60_000,
        max_model_calls=10,
        max_tool_calls=10,
        max_total_input_tokens=20_000,
        max_total_output_tokens=10_000,
        max_cost_microunits=100_000,
        max_replans=3,
        max_answer_repairs=2,
        max_no_progress_actions=3,
        max_retry_attempts=1,
        max_parallel_read_calls=2,
        model_timeout_ms=30_000,
        tool_timeout_ms=10_000,
    )


def _model_profile() -> ModelContextProfile:
    body = {
        "provider_ref": "provider:c03-static",
        "model_ref": "model:c03-static",
        "context_capacity_tokens": 32_000,
        "max_output_tokens": 4_000,
        "token_counter_ref": "token-counter:c03-precounted",
        "token_counter_mode": TokenCounterMode.CONSERVATIVE_ESTIMATOR.value,
        "framing_tokens": 16,
    }
    return ModelContextProfile(
        profile_ref="model-context-profile:c03",
        profile_hash=canonical_hash(body),
        **body,
    )


def _context_profile() -> ContextProfile:
    body = {
        "runtime_max_input_tokens": 16_000,
        "reserved_output_tokens": 2_000,
        "safety_margin_tokens": 1_000,
        "soft_compression_threshold_tokens": 12_000,
        "max_entries": 128,
    }
    return ContextProfile(
        profile_ref="context-profile:c03",
        profile_hash=canonical_hash(body),
        **body,
    )


def _boundary_policy() -> LocalBoundaryInputPolicy:
    return LocalBoundaryInputPolicy(
        policy_snapshot_ref="policy-snapshot:c03",
        prompt_template_ref="prompt-template:c03-main-agent",
        authorization_policy_ref="authorization-policy:c03-local",
        model_profile=_model_profile(),
        context_profile=_context_profile(),
        registry_snapshot=None,
        information_need_refs=("information-need:c03-explicit",),
        required_resource_refs=("enterprise-baseline:c03-frozen",),
    )


def _binding(
    *,
    action_kind: AgentActionKind,
    action_class: RuntimeActionClass,
    resources: tuple[RuntimeResourceType, ...],
) -> ActionRuntimeBinding:
    return ActionRuntimeBinding.build(
        binding_ref=f"action-binding:c03-{action_kind.value}",
        action_class=action_class,
        effect_type=action_kind.value,
        replay_policy=EffectReplayPolicy.SAFE_IDEMPOTENT,
        reconciliation_supported=False,
        required_budget_resources=resources,
        requirements=ActionExecutionRequirements(expected_duration_ms=250),
    )


def _demands(
    resources: tuple[RuntimeResourceType, ...],
) -> tuple[BudgetDemand, ...]:
    amounts = {
        RuntimeResourceType.ACTIVE_DURATION_MS: 250,
        RuntimeResourceType.MODEL_CALLS: 1,
        RuntimeResourceType.TOOL_CALLS: 1,
        RuntimeResourceType.INPUT_TOKENS: 500,
        RuntimeResourceType.OUTPUT_TOKENS: 250,
        RuntimeResourceType.COST_MICROUNITS: 1_000,
    }
    return tuple(
        BudgetDemand(resource_type=resource, amount=amounts[resource])
        for resource in resources
    )


def _admission_policy() -> LocalAdmissionPolicy:
    limits = _limits()
    policy = RuntimePolicyCeiling.build(
        policy_ref="runtime-policy:c03",
        limits=limits,
    )
    profile = RuntimeProfileSnapshot.build(
        profile_ref="runtime-profile:c03",
        policy=policy,
        limits=limits,
    )
    model_resources = (
        RuntimeResourceType.ACTIVE_DURATION_MS,
        RuntimeResourceType.MODEL_CALLS,
        RuntimeResourceType.INPUT_TOKENS,
        RuntimeResourceType.OUTPUT_TOKENS,
        RuntimeResourceType.COST_MICROUNITS,
    )
    tool_resources = (
        RuntimeResourceType.ACTIVE_DURATION_MS,
        RuntimeResourceType.TOOL_CALLS,
    )
    local_resources = (RuntimeResourceType.ACTIVE_DURATION_MS,)
    action_classes = {
        AgentActionKind.MAIN_AGENT_DECISION: (
            RuntimeActionClass.MODEL,
            model_resources,
        ),
        AgentActionKind.PLAN: (RuntimeActionClass.MODEL, model_resources),
        AgentActionKind.REPLAN: (RuntimeActionClass.MODEL, model_resources),
        AgentActionKind.TOOL_CALL_BATCH: (
            RuntimeActionClass.TOOL,
            tool_resources,
        ),
        AgentActionKind.REQUEST_INFORMATION: (
            RuntimeActionClass.LOCAL,
            local_resources,
        ),
        AgentActionKind.ANSWER: (RuntimeActionClass.LOCAL, local_resources),
    }
    return LocalAdmissionPolicy(
        policy=policy,
        profile=profile,
        rules=tuple(
            LocalActionAdmissionRule(
                action_kind=kind,
                binding=_binding(
                    action_kind=kind,
                    action_class=action_class,
                    resources=resources,
                ),
                budget_demands=_demands(resources),
                expected_output_contract_ref=f"output-contract:c03-{kind.value}",
            )
            for kind, (action_class, resources) in action_classes.items()
        ),
    )


@pytest.fixture(scope="module")
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


@pytest.fixture
def repository(sqlite_engine) -> Iterator[tuple[PureAgentRepository, Session]]:
    connection = sqlite_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield PureAgentRepository(session), session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _new_task(
    repo: PureAgentRepository,
    *,
    suffix: str,
) -> tuple[AgentTaskState, Any]:
    conversation = repo.create_conversation(
        owner_id=1,
        tenant_ref="tenant:c03",
        assessment_id=f"assessment-{suffix}",
        conversation_id=f"conversation-c03-{suffix}",
    )
    message = repo.append_message(
        conversation_id=conversation.id,
        role="user",
        message_type="user.task_trigger",
        content={"text": "请判断本项目的投标风险"},
        created_by_ref="user:1",
        idempotency_key=f"message-key:c03-{suffix}",
    )
    task = repo.create_task(
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        owner_id=1,
        goal_ref=f"goal:c03-{suffix}",
    )
    return task, message


def _turn(task: AgentTaskState, message: Any) -> MainAgentTurn:
    return MainAgentTurn(
        turn_ref=f"user-turn:{message.id}",
        task_ref=task.task_id,
        conversation_ref=task.session_id,
        message_ref=message.id,
        message_sequence=int(message.sequence_no),
        message_type=message.message_type,
        message_content_hash=canonical_hash(message.content_json),
    )


def _wakeup(task: AgentTaskState) -> RuntimeWakeup:
    return RuntimeWakeup.build(
        task_ref=task.task_id,
        conversation_ref=task.session_id,
        observed_state_version=task.state_version,
        reason=RuntimeWakeReason.USER_MESSAGE,
        seed=f"c03:{task.task_id}:{task.state_version}",
    )


def _context_snapshot(
    task: AgentTaskState,
    *,
    authorization_snapshot_ref: str,
    suffix: str,
) -> ContextSnapshot:
    model_profile = _model_profile()
    context_profile = _context_profile()
    request_hash = canonical_hash(
        {"task_ref": task.task_id, "state_version": task.state_version}
    )
    projection_hash = canonical_hash({"projection": suffix})
    snapshot_hash = canonical_hash(
        {
            "request_hash": request_hash,
            "projection_hash": projection_hash,
            "authorization_snapshot_ref": authorization_snapshot_ref,
        }
    )
    return ContextSnapshot(
        snapshot_ref=f"context-snapshot:c03-{suffix}",
        snapshot_sequence=task.state_version,
        task_ref=task.task_id,
        state_version=task.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        status=ContextAssemblyStatus.READY,
        request_hash=request_hash,
        policy_snapshot_ref="policy-snapshot:c03",
        prompt_template_ref="prompt-template:c03-main-agent",
        model_profile_ref=model_profile.profile_ref,
        model_profile_hash=model_profile.profile_hash,
        context_profile_ref=context_profile.profile_ref,
        context_profile_hash=context_profile.profile_hash,
        registry_snapshot_ref=None,
        registry_snapshot_hash=None,
        authorization_snapshot_ref=authorization_snapshot_ref,
        dependency_refs=(),
        included_entries=(),
        excluded_entries=(),
        compression_receipts=(),
        included_refs=(),
        excluded_refs=(),
        limitation_messages=(),
        estimated_input_tokens=32,
        effective_input_budget=16_000,
        reserved_output_tokens=2_000,
        safety_margin_tokens=1_000,
        projection_hash=projection_hash,
        snapshot_hash=snapshot_hash,
    )


def _intent(
    task: AgentTaskState,
    context: ContextSnapshot,
    *,
    arguments: dict[str, Any] | None = None,
) -> ActionReservationIntent:
    action_arguments = arguments or {
        "turn_ref": "user-turn:c03",
        "execution_mode": "direct",
        "plan_ref": None,
        "observation_refs": [],
    }
    body = {
        "task_ref": task.task_id,
        "state_version": task.state_version,
        "decision_ref": None,
        "decision_hash": None,
        "decision_observation_ref": None,
        "action_kind": AgentActionKind.MAIN_AGENT_DECISION.value,
        "arguments": action_arguments,
        "arguments_hash": canonical_hash(action_arguments),
        "effect_identity_seed": f"effect-seed:c03-{task.state_version}",
        "context_snapshot_ref": context.snapshot_ref,
        "context_snapshot_hash": context.snapshot_hash,
        "registry_snapshot_ref": None,
        "registry_snapshot_hash": None,
        "visible_tools_hash": None,
    }
    digest = canonical_hash(body)
    return ActionReservationIntent(
        **body,
        intent_ref=f"action-intent:{digest.removeprefix('sha256:')}",
        intent_hash=digest,
    )


def _create_budget_accounts(
    repo: PureAgentRepository,
    task: AgentTaskState,
    *,
    omit: RuntimeResourceType | None = None,
) -> None:
    units = {
        RuntimeResourceType.ACTIVE_DURATION_MS: "millisecond",
        RuntimeResourceType.MODEL_CALLS: "call",
        RuntimeResourceType.INPUT_TOKENS: "token",
        RuntimeResourceType.OUTPUT_TOKENS: "token",
        RuntimeResourceType.COST_MICROUNITS: "micro-unit",
    }
    limits = _limits()
    for resource, unit in units.items():
        if resource is omit:
            continue
        repo.create_budget_account(
            task_id=task.task_id,
            resource_type=resource.value,
            unit=unit,
            limit_amount=limits.resource_limit(resource),
        )


def _boundary_inputs(
    repo: PureAgentRepository,
    task: AgentTaskState,
    message: Any,
):
    provider = PersistedLocalBoundaryInputsProvider(
        repo,
        policy=_boundary_policy(),
    )
    return asyncio.run(
        provider.prepare(
            task=task,
            turn=_turn(task, message),
            wakeup=_wakeup(task),
        )
    )


def test_boundary_inputs_freeze_persisted_scope_without_deciding_an_action(
    repository,
) -> None:
    repo, _ = repository
    task, message = _new_task(repo, suffix="boundary")

    first = _boundary_inputs(repo, task, message)
    second = _boundary_inputs(repo, task, message)

    assert first == second
    assert first.authorization_snapshot_ref.startswith("authorization-snapshot:")
    assert first.information_need_refs == (
        task.goal_ref,
        "information-need:c03-explicit",
    )
    assert first.required_resource_refs == (
        "assessment:assessment-boundary",
        "enterprise-baseline:c03-frozen",
    )
    assert first.checkpoint_snapshot_ref is None
    assert first.registry_snapshot is None


def test_boundary_inputs_reject_stale_task_version(repository) -> None:
    repo, _ = repository
    task, message = _new_task(repo, suffix="stale-boundary")
    stale = task.model_copy(update={"state_version": task.state_version + 1})
    provider = PersistedLocalBoundaryInputsProvider(
        repo,
        policy=_boundary_policy(),
    )

    with pytest.raises(
        PersistedLocalBoundaryRejected,
        match="no longer matches",
    ):
        asyncio.run(
            provider.prepare(
                task=stale,
                turn=_turn(stale, message),
                wakeup=_wakeup(stale),
            )
        )


def test_admission_policy_requires_all_six_action_kinds() -> None:
    policy = _admission_policy()
    incomplete = policy.model_dump(mode="python")
    incomplete["rules"] = incomplete["rules"][:-1]

    with pytest.raises(ValidationError, match="policy is incomplete"):
        LocalAdmissionPolicy.model_validate(incomplete)


def test_admission_context_projects_context_budget_scope_and_factory(repository) -> None:
    repo, _ = repository
    task, message = _new_task(repo, suffix="admission")
    boundary = _boundary_inputs(repo, task, message)
    context = _context_snapshot(
        task,
        authorization_snapshot_ref=boundary.authorization_snapshot_ref,
        suffix="admission",
    )
    repo.store_context_snapshot(context)
    _create_budget_accounts(repo, task)
    policy = _admission_policy()
    factories = PersistedLocalRuntimeAdapterFactories(
        boundary_policy=_boundary_policy(),
        admission_policy=policy,
    )

    boundary_provider = factories.main_agent_inputs(repo)
    admission_provider = factories.admission_context(repo)
    projected = admission_provider.for_action(
        task=task,
        intent=_intent(task, context),
    )

    assert isinstance(boundary_provider, PersistedLocalBoundaryInputsProvider)
    assert isinstance(admission_provider, PersistedRuntimeAdmissionContextProvider)
    assert projected.binding == policy.rule_for(
        AgentActionKind.MAIN_AGENT_DECISION
    ).binding
    assert projected.budget_snapshot.task_ref == task.task_id
    assert len(projected.budget_snapshot.balances) == 5
    assert projected.cancellation.cancellation_fence_ref is None
    assert projected.existing_effect is None
    assert projected.scope_snapshot_hash is not None
    assert projected.expected_output_hash is not None
    assert projected.progress_window.records == ()
    assert projected.progress_window.window_ref.startswith("progress-window:")


def test_admission_context_fails_closed_when_budget_account_is_missing(
    repository,
) -> None:
    repo, _ = repository
    task, message = _new_task(repo, suffix="missing-budget")
    boundary = _boundary_inputs(repo, task, message)
    context = _context_snapshot(
        task,
        authorization_snapshot_ref=boundary.authorization_snapshot_ref,
        suffix="missing-budget",
    )
    repo.store_context_snapshot(context)
    _create_budget_accounts(
        repo,
        task,
        omit=RuntimeResourceType.COST_MICROUNITS,
    )
    provider = PersistedRuntimeAdmissionContextProvider(
        repo,
        policy=_admission_policy(),
    )

    with pytest.raises(
        PersistedAdmissionContextRejected,
        match="Budget accounts are incomplete: cost_microunits",
    ):
        provider.for_action(task=task, intent=_intent(task, context))


def test_governed_effect_hash_uses_action_arguments_and_is_projected_on_retry(
    repository,
) -> None:
    repo, _ = repository
    task, message = _new_task(repo, suffix="effect")
    boundary = _boundary_inputs(repo, task, message)
    context = _context_snapshot(
        task,
        authorization_snapshot_ref=boundary.authorization_snapshot_ref,
        suffix="effect",
    )
    repo.store_context_snapshot(context)
    _create_budget_accounts(repo, task)
    provider = PersistedRuntimeAdmissionContextProvider(
        repo,
        policy=_admission_policy(),
    )
    intent = _intent(task, context)
    guarded = GuardSuiteRuntimeActionGovernor(
        context_provider=provider
    ).govern(task=task, intent=intent, driver_payload={"receipt": "c03"})
    envelope = RuntimeActionPersistenceEnvelope.build(
        intent=intent,
        driver_payload={"receipt": "c03"},
    )

    reservation = repo.reserve_governed_action(
        event_id=f"runtime-action-reserve:{intent.intent_hash.removeprefix('sha256:')}",
        intent=intent,
        binding=guarded.binding,
        admission=guarded.admission,
        fencing_token=guarded.fencing_token,
        persisted_action_payload=envelope.model_dump(mode="json"),
    )
    effect_row = repo.load_runtime_effect_fence_by_key(
        task_id=task.task_id,
        effect_key=guarded.admission.candidate.effect_key,
    )
    action_row = repo.load_task_action(
        task_id=task.task_id,
        action_id=reservation.action.action_id,
    )
    assert effect_row is not None
    assert effect_row.request_hash == intent.arguments_hash.removeprefix("sha256:")
    assert action_row.arguments_hash != effect_row.request_hash

    repo.mark_effect_running(
        effect_fence_id=reservation.action.effect_fence_id,
        fencing_token=guarded.fencing_token,
        expected_state_version=reservation.action.state.state_version,
    )
    repo.settle_effect(
        effect_fence_id=reservation.action.effect_fence_id,
        fencing_token=guarded.fencing_token,
        expected_state_version=reservation.action.state.state_version,
        status="succeeded",
        result_ref="result:c03-effect",
        result={"accepted": True},
    )
    current = reservation.action.state
    observed = repo.commit_transition(
        TaskTransitionEvent(
            event_id="event:c03-effect-observation",
            task_id=current.task_id,
            expected_state_version=current.state_version,
            event_type=TaskEventType.OBSERVATION_ACCEPTED,
            effect_idempotency_key=None,
            action_ref=reservation.action.action_id,
            pending_context=None,
            resume_proof=None,
            execution_mode=None,
            plan_ref=None,
            observation_ref="observation:c03-effect",
            result_committed=False,
            error_ref=None,
            cancellation_fence_ref=None,
        )
    ).state
    retry_intent = _intent(observed, context, arguments=intent.arguments)

    retry_context = provider.for_action(task=observed, intent=retry_intent)

    assert retry_context.existing_effect is not None
    assert retry_context.existing_effect.status is EffectFenceStatus.SUCCEEDED
    assert retry_context.existing_effect.request_hash == intent.arguments_hash
    assert retry_context.existing_effect.result_ref == "result:c03-effect"
