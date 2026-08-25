from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.registry  # noqa: F401  # Register FK targets without app startup.
from app.agents.bid_assessment_pure.action_runtime import (
    ActionLoopContractRejected,
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
    ActionReservationIntent,
    AgentActionKind,
)
from app.agents.bid_assessment_pure.conversation_contracts import (
    SubmitSlotInputRequest,
)
from app.agents.bid_assessment_pure.conversation_runtime import (
    ConversationApiRuntime,
    SlotSubmissionCommand,
)
from app.agents.bid_assessment_pure.persistence_models import (
    BidPureAgentAction,
    BidPureAgentBudgetAccount,
)
from app.agents.bid_assessment_pure.repository import (
    PureAgentRepository,
    hash_resume_token,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyStatus,
    ContextConsumer,
    ContextSnapshot,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    ContinuationTokenService,
    ContinuationTokenUnavailable,
    GuardSuiteRuntimeActionGovernor,
    LocalRuntimePulseDispatcher,
    PersistedRuntimeAction,
    PureAgentRuntimeController,
    RuntimeActionExecution,
    RuntimeAdmissionContext,
    RuntimePostAction,
    RuntimePulseDirective,
    RuntimePulseDisposition,
    RuntimeWakeReason,
    RuntimeWakeup,
    SlotSuspensionDirective,
)
from app.agents.bid_assessment_pure.runtime_guards import (
    ActionExecutionRequirements,
    ActionRuntimeBinding,
    BudgetBalance,
    BudgetDemand,
    CancellationSnapshot,
    EffectReplayPolicy,
    ProgressWindow,
    RuntimeActionClass,
    RuntimeBudgetSnapshot,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeResourceType,
)
from app.agents.bid_assessment_pure.slot_validation import SlotValidatorRegistry
from app.agents.bid_assessment_pure.slots import ContinuationCheckpoint
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash
from app.api.v1 import bid_assessment_pure_agent as conversation_api
from app.core.database import Base
from app.models.user import User


SECRET = "c01-local-continuation-secret-32-bytes-minimum"


def _limits() -> RuntimeLimitSet:
    return RuntimeLimitSet(
        max_active_duration_ms=60_000,
        max_model_calls=0,
        max_tool_calls=0,
        max_total_input_tokens=0,
        max_total_output_tokens=0,
        max_cost_microunits=0,
        max_replans=0,
        max_answer_repairs=0,
        max_no_progress_actions=2,
        max_retry_attempts=1,
        max_parallel_read_calls=1,
        model_timeout_ms=30_000,
        tool_timeout_ms=10_000,
    )


def _intent(task: AgentTaskState) -> ActionReservationIntent:
    arguments = {
        "turn_ref": "turn:c01",
        "execution_mode": "direct",
        "plan_ref": None,
        "observation_refs": [],
    }
    body: dict[str, Any] = {
        "task_ref": task.task_id,
        "state_version": task.state_version,
        "decision_ref": None,
        "decision_hash": None,
        "decision_observation_ref": None,
        "action_kind": AgentActionKind.MAIN_AGENT_DECISION.value,
        "arguments": arguments,
        "arguments_hash": canonical_hash(arguments),
        "effect_identity_seed": "effect-seed:c01",
        "context_snapshot_ref": "context:c01",
        "context_snapshot_hash": canonical_hash({"context": "c01"}),
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


class _AdmissionContextProvider:
    def __init__(self, context: RuntimeAdmissionContext) -> None:
        self.context = context

    def for_action(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
    ) -> RuntimeAdmissionContext:
        assert task.task_id == intent.task_ref
        return self.context


class _SlotAfterActionDriver:
    def __init__(self, prepared) -> None:
        self.prepared = prepared
        self.prepare_calls = 0
        self.execute_calls = 0

    async def prepare_next_action(self, *, task, wakeup):
        assert wakeup.task_ref == task.task_id
        self.prepare_calls += 1
        return self.prepared

    async def execute_active_action(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> RuntimeActionExecution:
        self.execute_calls += 1
        artifact = {"decision": "request_information", "missing": "deadline_days"}
        body = {
            "task_ref": task.task_id,
            "source_action_ref": action.action_ref,
            "action_sequence": action.sequence,
            "state_version": task.state_version,
            "kind": ActionObservationKind.CONTROL_DECISION.value,
            "status": ActionObservationStatus.SUCCEEDED.value,
            "artifact_ref": "artifact:c01-control-decision",
            "artifact_hash": canonical_hash(artifact),
            "summary": "需要用户补充工期天数。",
            "material_progress": False,
            "progress_signal_refs": [],
            "limitation_codes": ["MISSING_DEADLINE_DAYS"],
        }
        digest = canonical_hash(body)
        observation = ActionObservation(
            **body,
            observation_ref=f"observation:{digest.removeprefix('sha256:')}",
            observation_hash=digest,
        )
        return RuntimeActionExecution(
            observation=observation,
            effect_status="succeeded",
            result_ref=observation.artifact_ref,
            result_payload=artifact,
            # Empty usage is intentionally conservative: the Controller must
            # settle the complete reservation instead of releasing it.
            budget_usage=(),
        )

    async def after_observation(self, *, task, action, execution):
        assert execution.observation.observation_ref in task.observation_refs
        return RuntimePostAction(
            directive=RuntimePulseDirective.WAIT,
            slot=SlotSuspensionDirective(
                name="bid.deadline",
                request_message="请补充允许的工期天数。",
                input_model_ref="slot-model:c01-deadline-v1",
                business_validator_refs=(),
                context_snapshot_ref="context:c01",
                slot_ref="slot:c01-deadline",
                checkpoint_ref="checkpoint:c01-deadline",
            ),
        )


class _FailingActionDriver:
    def __init__(self, prepared) -> None:
        self.prepared = prepared

    async def prepare_next_action(self, *, task, wakeup):
        del task, wakeup
        return self.prepared

    async def execute_active_action(self, *, task, action):
        del task, action
        raise ActionLoopContractRejected("fixture Provider input exceeded its budget")

    async def after_observation(self, *, task, action, execution):
        del task, action, execution
        raise AssertionError("failed execution must not enter normal continuation")


class _ContinueAfterActionDriver(_SlotAfterActionDriver):
    async def after_observation(self, *, task, action, execution):
        del action
        assert execution.observation.observation_ref in task.observation_refs
        return RuntimePostAction(directive=RuntimePulseDirective.CONTINUE)


class DeadlineInput(BaseModel):
    deadline_days: int = Field(ge=7, le=365)


class _RecordingDispatcher:
    available = True

    def __init__(self) -> None:
        self.wakeups: list[RuntimeWakeup] = []

    async def dispatch(self, wakeup: RuntimeWakeup):
        self.wakeups.append(wakeup)
        return None


class _CommitCheckingDispatcher(_RecordingDispatcher):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self._session_factory = session_factory
        self.observed_committed_task = False

    async def dispatch(self, wakeup: RuntimeWakeup):
        session = self._session_factory()
        try:
            state = PureAgentRepository(session).load_task_state(wakeup.task_ref)
            self.observed_committed_task = (
                state.session_id == wakeup.conversation_ref
                and state.state_version == wakeup.observed_state_version
            )
        finally:
            session.close()
        return await super().dispatch(wakeup)


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


def _new_task(repo: PureAgentRepository) -> tuple[str, AgentTaskState]:
    conversation = repo.create_conversation(
        owner_id=1,
        tenant_ref="tenant:c01",
        conversation_id="conversation:c01",
    )
    message = repo.append_message(
        conversation_id=conversation.id,
        role="user",
        message_type="user.task_trigger",
        content={"text": "请研判投标风险"},
        created_by_ref="user:1",
        idempotency_key="message:c01-task-trigger",
    )
    task = repo.create_task(
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        owner_id=1,
        goal_ref="goal:c01",
    )
    return conversation.id, task


def _guarded_action(repo: PureAgentRepository, task: AgentTaskState):
    limits = _limits()
    policy = RuntimePolicyCeiling.build(policy_ref="policy:c01", limits=limits)
    profile = RuntimeProfileSnapshot.build(
        profile_ref="profile:c01",
        policy=policy,
        limits=limits,
    )
    account = repo.create_budget_account(
        task_id=task.task_id,
        resource_type=RuntimeResourceType.ACTIVE_DURATION_MS.value,
        unit="millisecond",
        limit_amount=limits.max_active_duration_ms,
    )
    snapshot = RuntimeBudgetSnapshot.build(
        snapshot_ref="budget-snapshot:c01",
        task_ref=task.task_id,
        profile=profile,
        balances=(
            BudgetBalance(
                resource_type=RuntimeResourceType.ACTIVE_DURATION_MS,
                unit=account.unit,
                limit_amount=int(account.limit_amount),
                reserved_amount=int(account.reserved_amount),
                spent_amount=int(account.actual_amount),
                row_version=int(account.row_version),
            ),
        ),
    )
    binding = ActionRuntimeBinding.build(
        binding_ref="binding:c01-local",
        action_class=RuntimeActionClass.LOCAL,
        effect_type="main_agent_decision",
        replay_policy=EffectReplayPolicy.SAFE_IDEMPOTENT,
        reconciliation_supported=False,
        required_budget_resources=(RuntimeResourceType.ACTIVE_DURATION_MS,),
        requirements=ActionExecutionRequirements(expected_duration_ms=500),
    )
    intent = _intent(task)
    repo.store_context_snapshot(
        ContextSnapshot(
            snapshot_ref=intent.context_snapshot_ref,
            snapshot_sequence=task.state_version,
            task_ref=task.task_id,
            state_version=task.state_version,
            consumer=ContextConsumer.MAIN_AGENT,
            status=ContextAssemblyStatus.READY,
            request_hash=canonical_hash({"request": "c01"}),
            policy_snapshot_ref="policy-snapshot:c01",
            prompt_template_ref="prompt-template:c01",
            model_profile_ref="model-profile:c01",
            model_profile_hash=canonical_hash({"model": "c01"}),
            context_profile_ref="context-profile:c01",
            context_profile_hash=canonical_hash({"context-profile": "c01"}),
            registry_snapshot_ref=None,
            registry_snapshot_hash=None,
            authorization_snapshot_ref="authorization:c01",
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
            projection_hash=canonical_hash({"projection": "c01"}),
            snapshot_hash=intent.context_snapshot_hash,
        )
    )
    context = RuntimeAdmissionContext(
        binding=binding,
        profile=profile,
        policy=policy,
        budget_snapshot=snapshot,
        budget_demands=(
            BudgetDemand(
                resource_type=RuntimeResourceType.ACTIVE_DURATION_MS,
                amount=500,
            ),
        ),
        progress_window=ProgressWindow.build(
            window_ref="progress-window:c01",
            task_ref=task.task_id,
        ),
        cancellation=CancellationSnapshot(
            task_ref=task.task_id,
            state_version=task.state_version,
            cancellation_fence_ref=None,
        ),
        semantic_basis_hash=canonical_hash({"intent": "collect deadline"}),
    )
    governor = GuardSuiteRuntimeActionGovernor(
        context_provider=_AdmissionContextProvider(context)
    )
    return governor.govern(
        task=task,
        intent=intent,
        driver_payload={"test_boundary": "c01-local-only"},
    )


def test_continuation_token_is_server_bound_and_not_available_without_secret() -> None:
    checkpoint = ContinuationCheckpoint(
        checkpoint_id="checkpoint:c01",
        task_id="task:c01",
        slot_ref="slot:c01",
        suspended_state_version=3,
        execution_mode="direct",
        context_snapshot_ref="context:c01",
        suspended_action_ref="action:c01",
        effect_fence_ref="effect-fence:c01",
        resume_token_hash="sha256:" + "0" * 64,
        status="open",
    )
    service = ContinuationTokenService(SECRET)
    token = service.issue(checkpoint)

    assert token == service.issue(checkpoint)
    assert service.matches(checkpoint, token)
    assert not service.matches(
        checkpoint.model_copy(update={"checkpoint_id": "checkpoint:c01-other"}),
        token,
    )
    with pytest.raises(ContinuationTokenUnavailable):
        ContinuationTokenService().issue(checkpoint)


def test_slot_request_contract_no_longer_requires_browser_resume_token() -> None:
    request = SubmitSlotInputRequest(
        expected_state_version=4,
        candidate={"deadline_days": 30},
    )

    assert request.resume_token is None


def test_local_controller_dispatcher_persists_one_action_and_resumes_slot(
    sqlite_engine,
) -> None:
    SessionFactory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    setup_session: Session = SessionFactory()
    setup_repo = PureAgentRepository(setup_session)
    conversation_ref, task = _new_task(setup_repo)
    prepared = _guarded_action(setup_repo, task)
    setup_session.commit()
    setup_session.close()

    driver = _SlotAfterActionDriver(prepared)
    token_service = ContinuationTokenService(SECRET)
    dispatcher = LocalRuntimePulseDispatcher(
        session_factory=SessionFactory,
        controller_factory=lambda db: PureAgentRuntimeController(
            PureAgentRepository(db),
            driver=driver,
            continuation_tokens=token_service,
        ),
        max_pulses_per_dispatch=4,
    )
    wakeup = RuntimeWakeup.build(
        task_ref=task.task_id,
        conversation_ref=conversation_ref,
        observed_state_version=task.state_version,
        reason=RuntimeWakeReason.USER_MESSAGE,
        seed="c01-integration",
    )

    outcome = asyncio.run(dispatcher.dispatch(wakeup))

    assert outcome.disposition is RuntimePulseDisposition.PENDING
    assert outcome.directive is RuntimePulseDirective.WAIT
    assert outcome.task_status is AgentTaskStatus.PENDING
    assert driver.prepare_calls == 1
    assert driver.execute_calls == 1

    verify_session: Session = SessionFactory()
    repo = PureAgentRepository(verify_session)
    pending = repo.load_task_state(task.task_id)
    assert pending.status is AgentTaskStatus.PENDING
    assert pending.pending_context is not None
    assert pending.pending_context.slot_ref == "slot:c01-deadline"
    persisted_observation = repo.load_context_observation_artifact(
        task_id=task.task_id,
        observation_ref=pending.observation_refs[-1],
    )
    assert persisted_observation.artifact == {
        "decision": "request_information",
        "missing": "deadline_days",
    }
    assert persisted_observation.context_snapshot_ref == "context:c01"
    action = verify_session.query(BidPureAgentAction).one()
    assert action.status == "succeeded"
    assert action.arguments_json["schema_name"] == "bid.pure-agent.action-envelope.v1"
    account = verify_session.query(BidPureAgentBudgetAccount).one()
    assert int(account.reserved_amount) == 0
    assert int(account.actual_amount) == 500

    checkpoint = repo.load_task_checkpoint_for_slot(
        task_id=task.task_id,
        slot_id="slot:c01-deadline",
    )
    server_token = token_service.issue(checkpoint)
    assert checkpoint.resume_token_hash == hash_resume_token(server_token)

    validators = SlotValidatorRegistry()
    validators.register_input_model(
        "slot-model:c01-deadline-v1",
        DeadlineInput,
        format_guidance="请输入 7 到 365 之间的整数天数。",
    )
    runtime = ConversationApiRuntime(
        repo,
        slot_validators=validators,
        runtime_available=True,
        continuation_tokens=token_service,
    )
    resumed = runtime.submit_slot_input(
        SlotSubmissionCommand(
            conversation_ref=conversation_ref,
            task_ref=task.task_id,
            slot_ref="slot:c01-deadline",
            owner_id=1,
            created_by_ref="user:1",
            tenant_ref="tenant:c01",
            authorization_snapshot_ref="user-auth:1:v1",
            expected_state_version=pending.state_version,
            resume_token=None,
            candidate={"deadline_days": 30},
            idempotency_key="slot-submit:c01-deadline",
        )
    )
    verify_session.commit()

    assert resumed.accepted
    assert resumed.task.status is AgentTaskStatus.RUNNING
    assert resumed.task.dispatch_status == "ready"
    verify_session.close()


def test_local_dispatcher_terminalizes_an_unhandled_active_action_failure(
    sqlite_engine,
) -> None:
    SessionFactory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    setup_session: Session = SessionFactory()
    setup_repo = PureAgentRepository(setup_session)
    conversation_ref, task = _new_task(setup_repo)
    prepared = _guarded_action(setup_repo, task)
    setup_session.commit()
    setup_session.close()

    dispatcher = LocalRuntimePulseDispatcher(
        session_factory=SessionFactory,
        controller_factory=lambda db: PureAgentRuntimeController(
            PureAgentRepository(db),
            driver=_FailingActionDriver(prepared),
        ),
        max_pulses_per_dispatch=4,
    )
    wakeup = RuntimeWakeup.build(
        task_ref=task.task_id,
        conversation_ref=conversation_ref,
        observed_state_version=task.state_version,
        reason=RuntimeWakeReason.USER_MESSAGE,
        seed="c01-failure-terminalization",
    )

    outcome = asyncio.run(dispatcher.dispatch(wakeup))

    assert outcome.disposition is RuntimePulseDisposition.FAILED
    assert outcome.directive is RuntimePulseDirective.STOP
    assert outcome.task_status is AgentTaskStatus.FAILED
    assert outcome.reason_codes == ("runtime_contract_rejected",)

    verify_session: Session = SessionFactory()
    repo = PureAgentRepository(verify_session)
    failed = repo.load_task_state(task.task_id)
    assert failed.status is AgentTaskStatus.FAILED
    assert failed.in_flight_action_ref is None
    assert failed.last_error_ref is not None
    assert failed.last_error_ref.startswith("runtime-error:")
    action = verify_session.query(BidPureAgentAction).one()
    assert action.status == "failed"
    assert action.error_code == "runtime_contract_rejected"
    account = verify_session.query(BidPureAgentBudgetAccount).one()
    assert int(account.reserved_amount) == 0
    assert int(account.actual_amount) == 500
    verify_session.close()


def test_local_dispatcher_terminalizes_when_its_pulse_limit_is_exhausted(
    sqlite_engine,
) -> None:
    SessionFactory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    setup_session: Session = SessionFactory()
    setup_repo = PureAgentRepository(setup_session)
    conversation_ref, task = _new_task(setup_repo)
    prepared = _guarded_action(setup_repo, task)
    setup_session.commit()
    setup_session.close()

    driver = _ContinueAfterActionDriver(prepared)
    dispatcher = LocalRuntimePulseDispatcher(
        session_factory=SessionFactory,
        controller_factory=lambda db: PureAgentRuntimeController(
            PureAgentRepository(db),
            driver=driver,
        ),
        max_pulses_per_dispatch=2,
    )
    wakeup = RuntimeWakeup.build(
        task_ref=task.task_id,
        conversation_ref=conversation_ref,
        observed_state_version=task.state_version,
        reason=RuntimeWakeReason.USER_MESSAGE,
        seed="c01-pulse-limit-terminalization",
    )

    outcome = asyncio.run(dispatcher.dispatch(wakeup))

    assert outcome.disposition is RuntimePulseDisposition.FAILED
    assert outcome.directive is RuntimePulseDirective.STOP
    assert outcome.task_status is AgentTaskStatus.FAILED
    assert outcome.reason_codes == ("runtime_pulse_limit_exceeded",)
    assert driver.prepare_calls == 1
    assert driver.execute_calls == 1

    verify_session: Session = SessionFactory()
    repo = PureAgentRepository(verify_session)
    failed = repo.load_task_state(task.task_id)
    assert failed.status is AgentTaskStatus.FAILED
    assert failed.in_flight_action_ref is None
    assert failed.last_error_ref is not None
    action = verify_session.query(BidPureAgentAction).one()
    assert action.status == "succeeded"
    account = verify_session.query(BidPureAgentBudgetAccount).one()
    assert int(account.reserved_amount) == 0
    assert int(account.actual_amount) == 500
    verify_session.close()


def test_conversation_dispatch_status_is_runtime_derived() -> None:
    enabled = ConversationApiRuntime(
        None,
        slot_validators=SlotValidatorRegistry(),
        runtime_available=True,
    )
    disabled = ConversationApiRuntime(
        None,
        slot_validators=SlotValidatorRegistry(),
        runtime_available=False,
    )
    running = create_running_task(
        task_id="task:c01-status",
        session_id="conversation:c01-status",
        goal_ref="goal:c01-status",
    )

    assert disabled._dispatch_status(running) == "disabled"
    assert enabled._dispatch_status(running) == "ready"
    assert enabled._dispatch_status(
        running.model_copy(update={"in_flight_action_ref": "action:c01-status"})
    ) == "active"
    assert enabled._dispatch_status(
        running.model_copy(update={"status": AgentTaskStatus.PENDING})
    ) == "waiting_input"
    assert enabled._dispatch_status(
        running.model_copy(update={"status": AgentTaskStatus.COMPLETED})
    ) == "finished"


def test_api_schedules_only_when_all_fail_closed_runtime_gates_pass(
    monkeypatch,
) -> None:
    dispatcher = _RecordingDispatcher()
    background = BackgroundTasks()
    local_settings = SimpleNamespace(
        feature_bid_assessment_pure_agent=True,
        feature_bid_assessment_pure_agent_runtime=True,
        bid_assessment_pure_agent_continuation_secret=SECRET,
    )
    monkeypatch.setattr(conversation_api, "settings", local_settings)
    conversation_api._schedule_runtime(
        background_tasks=background,
        dispatcher=dispatcher,
        task_ref="task:c01-api",
        conversation_ref="conversation:c01-api",
        state_version=1,
        reason=RuntimeWakeReason.USER_MESSAGE,
        seed="message:c01-api",
    )

    assert len(background.tasks) == 1
    asyncio.run(background())
    assert len(dispatcher.wakeups) == 1
    assert dispatcher.wakeups[0].reason is RuntimeWakeReason.USER_MESSAGE

    local_settings.bid_assessment_pure_agent_continuation_secret = ""
    blocked = BackgroundTasks()
    conversation_api._schedule_runtime(
        background_tasks=blocked,
        dispatcher=dispatcher,
        task_ref="task:c01-api-blocked",
        conversation_ref="conversation:c01-api",
        state_version=1,
        reason=RuntimeWakeReason.SLOT_RESUMED,
        seed="slot:c01-api",
    )
    assert blocked.tasks == []


def test_conversation_http_api_commits_before_background_runtime_wakeup(
    sqlite_engine,
    monkeypatch,
) -> None:
    SessionFactory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    dispatcher = _CommitCheckingDispatcher(SessionFactory)
    local_settings = SimpleNamespace(
        feature_bid_assessment_pure_agent=True,
        feature_bid_assessment_pure_agent_runtime=True,
        bid_assessment_pure_agent_continuation_secret=SECRET,
    )
    monkeypatch.setattr(conversation_api, "settings", local_settings)

    app = FastAPI()
    app.include_router(conversation_api.router)

    def override_db():
        session = SessionFactory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[conversation_api.get_db] = override_db
    app.dependency_overrides[conversation_api.get_current_user] = lambda: User(
        id=1,
        username="c01-local-user",
        role="user",
        role_version=1,
        is_active=True,
    )
    app.dependency_overrides[
        conversation_api.get_pure_agent_runtime_dispatcher
    ] = lambda: dispatcher

    with TestClient(app) as client:
        created = client.post(
            "/bid-assessment-pure-agent/conversations",
            headers={"Idempotency-Key": "c01-create-conversation"},
            json={"title": "C01 local integration"},
        )
        assert created.status_code == 200
        conversation_ref = created.json()["data"]["conversation_ref"]

        submitted = client.post(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/messages",
            headers={"Idempotency-Key": "c01-submit-message"},
            json={"text": "请判断这份招标资料有哪些风险。"},
        )

    assert submitted.status_code == 200
    payload = submitted.json()["data"]
    assert payload["admission"] == "task_trigger"
    assert payload["task"]["dispatch_status"] == "ready"
    assert dispatcher.observed_committed_task
    assert len(dispatcher.wakeups) == 1
    assert dispatcher.wakeups[0].reason is RuntimeWakeReason.USER_MESSAGE
