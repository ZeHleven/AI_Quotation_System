from __future__ import annotations

import asyncio
import json
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models.registry  # noqa: F401  # Register local FK targets only.
from app.agents.bid_assessment_pure.action_runtime import DynamicActionLoopRuntime
from app.agents.bid_assessment_pure.capability_executors import (
    CapabilityExecutorFactories,
)
from app.agents.bid_assessment_pure.context_runtime import (
    ContextCounterUnavailable,
    ContextSourceUnavailable,
    ContextStoreUnavailable,
)
from app.agents.bid_assessment_pure.local_bootstrap import (
    LocalPureAgentRuntimeAdapters,
)
from app.agents.bid_assessment_pure.main_agent_boundary import (
    MainAgentTurn,
    PersistedMainAgentDecisionBoundaryProvider,
)
from app.agents.bid_assessment_pure.persisted_context_adapters import (
    PersistedContextAdapterFactories,
    PersistedContextCandidateSource,
    PersistedContextProjectionPolicy,
)
from app.agents.bid_assessment_pure.persisted_local_adapters import (
    LocalBoundaryInputPolicy,
    PersistedLocalBoundaryInputsProvider,
)
from app.agents.bid_assessment_pure.planner_runtime import PlannerRuntime
from app.agents.bid_assessment_pure.registry import (
    BID_DOCUMENT_SEARCH,
    ENTERPRISE_KNOWLEDGE_SEARCH,
    build_initial_registry,
)
from app.agents.bid_assessment_pure.repository import PureAgentRepository
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyRequest,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextProfile,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    RuntimeWakeReason,
    RuntimeWakeup,
)
from app.agents.bid_assessment_pure.slot_validation import SlotValidatorRegistry
from app.agents.bid_assessment_pure.state import AgentTaskState
from app.agents.bid_assessment_pure.tool_runtime import (
    RegistrySnapshot,
    canonical_hash,
    freeze_registry_snapshot,
)
from app.core.database import Base


def _registry() -> RegistrySnapshot:
    return freeze_registry_snapshot(
        build_initial_registry(),
        visible_names=(BID_DOCUMENT_SEARCH, ENTERPRISE_KNOWLEDGE_SEARCH),
    )


def _model_profile() -> ModelContextProfile:
    body = {
        "provider_ref": "provider:c03-2-static",
        "model_ref": "model:c03-2-static",
        "context_capacity_tokens": 32_000,
        "max_output_tokens": 4_000,
        "token_counter_ref": "token-counter:c03-2-conservative",
        "token_counter_mode": TokenCounterMode.CONSERVATIVE_ESTIMATOR.value,
        "framing_tokens": 16,
    }
    return ModelContextProfile(
        profile_ref="model-context-profile:c03-2",
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
        profile_ref="context-profile:c03-2",
        profile_hash=canonical_hash(body),
        **body,
    )


def _boundary_policy(registry: RegistrySnapshot) -> LocalBoundaryInputPolicy:
    return LocalBoundaryInputPolicy(
        policy_snapshot_ref="policy-snapshot:c03-2",
        prompt_template_ref="prompt-template:c03-2-main-agent",
        authorization_policy_ref="authorization-policy:c03-2-local",
        model_profile=_model_profile(),
        context_profile=_context_profile(),
        registry_snapshot=registry,
        information_need_refs=("information-need:c03-2-explicit",),
        required_resource_refs=("enterprise-baseline:c03-2-frozen",),
    )


def _projection_policy(registry: RegistrySnapshot) -> PersistedContextProjectionPolicy:
    return PersistedContextProjectionPolicy(
        policy_snapshot_ref="policy-snapshot:c03-2",
        prompt_template_ref="prompt-template:c03-2-main-agent",
        system_policy=(
            "你是投标机会研判主 Agent。只依据当前授权上下文自主选择下一动作，"
            "不得把资源引用或检索候选当作事实证据。"
        ),
        output_contract=(
            "返回一个符合主 Agent Decision Schema 的结构化决定；不输出思维链。"
        ),
        registry_snapshot=registry,
        max_interaction_messages=20,
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
    with_history: bool = True,
) -> tuple[AgentTaskState, Any]:
    conversation = repo.create_conversation(
        owner_id=1,
        tenant_ref="tenant:c03-2",
        assessment_id=f"assessment-{suffix}",
        conversation_id=f"conversation-c03-2-{suffix}",
    )
    if with_history:
        repo.append_message(
            conversation_id=conversation.id,
            role="assistant",
            message_type="answer.committed",
            content={
                "schema_name": "bid.answer.message.v1",
                "text": "上一轮回答仅作为未受信任的对话历史。",
            },
            created_by_ref="runtime:c03-2",
            idempotency_key=f"history-key:c03-2-{suffix}",
        )
    admission = repo.accept_user_message(
        conversation_id=conversation.id,
        owner_id=1,
        user_input={
            "text": "请判断当前项目的重要信息和投标风险。",
            "resources": [],
        },
        created_by_ref="user:1",
        idempotency_key=f"message-key:c03-2-{suffix}",
    )
    return admission.task, admission.message


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
        seed=f"c03-2:{task.task_id}:{task.state_version}",
    )


def _inputs(
    repo: PureAgentRepository,
    *,
    task: AgentTaskState,
    message: Any,
    registry: RegistrySnapshot,
):
    provider = PersistedLocalBoundaryInputsProvider(
        repo,
        policy=_boundary_policy(registry),
    )
    return asyncio.run(
        provider.prepare(
            task=task,
            turn=_turn(task, message),
            wakeup=_wakeup(task),
        )
    )


def _request(task: AgentTaskState, message: Any, inputs: Any) -> ContextAssemblyRequest:
    registry = inputs.registry_snapshot
    return ContextAssemblyRequest(
        task_ref=task.task_id,
        state_version=task.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        user_message_ref=message.id,
        visible_tool_names=registry.visible_tool_names,
        information_need_refs=inputs.information_need_refs,
        required_resource_refs=inputs.required_resource_refs,
        policy_snapshot_ref=inputs.policy_snapshot_ref,
        prompt_template_ref=inputs.prompt_template_ref,
        registry_snapshot_ref=registry.snapshot_ref,
        model_profile_ref=inputs.model_profile.profile_ref,
        context_profile_ref=inputs.context_profile.profile_ref,
        checkpoint_snapshot_ref=inputs.checkpoint_snapshot_ref,
        authorization_snapshot_ref=inputs.authorization_snapshot_ref,
        snapshot_sequence=task.state_version,
    )


def _assembly(
    repo: PureAgentRepository,
    *,
    task: AgentTaskState,
    message: Any,
    registry: RegistrySnapshot,
):
    inputs = _inputs(repo, task=task, message=message, registry=registry)
    request = _request(task, message, inputs)
    factories = PersistedContextAdapterFactories(
        projection_policy=_projection_policy(registry)
    )
    result = asyncio.run(
        factories.context_assembler(repo).assemble(
            task=task,
            request=request,
            model_profile=inputs.model_profile,
            context_profile=inputs.context_profile,
            registry_snapshot=registry,
        )
    )
    return inputs, request, factories, result


def test_candidate_source_builds_model_ready_persisted_context(repository) -> None:
    repo, _ = repository
    registry = _registry()
    task, message = _new_task(repo, suffix="ready")

    inputs, _, _, result = _assembly(
        repo,
        task=task,
        message=message,
        registry=registry,
    )

    assert result.snapshot.status is ContextAssemblyStatus.READY
    kinds = tuple(entry.kind for entry in result.projection_entries)
    assert ContextEntryKind.POLICY in kinds
    assert ContextEntryKind.OUTPUT_CONTRACT in kinds
    assert ContextEntryKind.TASK_STATE in kinds
    assert ContextEntryKind.CURRENT_USER_MESSAGE in kinds
    assert ContextEntryKind.CONVERSATION_MESSAGE in kinds
    tool_entries = tuple(
        entry
        for entry in result.projection_entries
        if entry.kind is ContextEntryKind.TOOL_CONTRACT
    )
    assert {entry.tool_name for entry in tool_entries} == {
        BID_DOCUMENT_SEARCH,
        ENTERPRISE_KNOWLEDGE_SEARCH,
    }
    assert not {
        ContextEntryKind.ACTIVE_TOOL_CALL,
        ContextEntryKind.ACTIVE_TOOL_RESULT,
    } & set(kinds)
    assert result.snapshot.authorization_snapshot_ref == inputs.authorization_snapshot_ref
    assert repo.load_context_snapshot(
        task_id=task.task_id,
        snapshot_ref=result.snapshot.snapshot_ref,
    ) == result.snapshot


def test_resource_and_observation_receipts_never_claim_loaded_evidence(
    repository,
) -> None:
    repo, _ = repository
    registry = _registry()
    task, message = _new_task(repo, suffix="receipts", with_history=False)
    inputs = _inputs(repo, task=task, message=message, registry=registry)
    request = _request(task, message, inputs)
    source = PersistedContextCandidateSource(
        repo,
        policy=_projection_policy(registry),
    )

    candidates = asyncio.run(source.collect(task=task, request=request))
    receipts = tuple(
        candidate
        for candidate in candidates
        if candidate.kind is ContextEntryKind.GROUNDING
    )

    assert {candidate.source_ref for candidate in receipts} == set(
        inputs.required_resource_refs
    )
    assert all(candidate.required for candidate in receipts)
    for candidate in receipts:
        payload = json.loads(candidate.content)
        assert payload["authorization_bound"] is True
        assert payload["evidence_loaded"] is False
        assert "not evidence" in payload["instruction"]


def test_snapshot_store_is_idempotent_and_task_scoped(repository) -> None:
    repo, _ = repository
    registry = _registry()
    task, message = _new_task(repo, suffix="snapshot")
    _, _, factories, result = _assembly(
        repo,
        task=task,
        message=message,
        registry=registry,
    )
    store = factories.snapshot_store(repo)

    asyncio.run(store.save(result.snapshot))
    loaded = asyncio.run(
        store.load(result.snapshot.snapshot_ref, task_ref=task.task_id)
    )
    other_task, _ = _new_task(repo, suffix="snapshot-other", with_history=False)

    assert loaded == result.snapshot
    with pytest.raises(ContextStoreUnavailable, match="does not exist"):
        asyncio.run(
            store.load(
                result.snapshot.snapshot_ref,
                task_ref=other_task.task_id,
            )
        )


def test_candidate_source_rejects_registry_and_turn_scope_drift(repository) -> None:
    repo, _ = repository
    registry = _registry()
    task, message = _new_task(repo, suffix="drift", with_history=False)
    inputs = _inputs(repo, task=task, message=message, registry=registry)
    request = _request(task, message, inputs)
    source = PersistedContextCandidateSource(
        repo,
        policy=_projection_policy(registry),
    )

    registry_drift = request.model_copy(
        update={"visible_tool_names": (BID_DOCUMENT_SEARCH,)}
    )
    with pytest.raises(ContextSourceUnavailable, match="Registry projection drifted"):
        asyncio.run(source.collect(task=task, request=registry_drift))

    _, foreign_message = _new_task(repo, suffix="foreign-turn", with_history=False)
    foreign_turn = request.model_copy(
        update={"user_message_ref": foreign_message.id}
    )
    with pytest.raises(ContextSourceUnavailable, match="candidates are unavailable"):
        asyncio.run(source.collect(task=task, request=foreign_turn))


def test_candidate_source_and_counter_reject_stale_or_mismatched_profiles(
    repository,
) -> None:
    repo, _ = repository
    registry = _registry()
    task, message = _new_task(repo, suffix="stale", with_history=False)
    inputs = _inputs(repo, task=task, message=message, registry=registry)
    request = _request(task, message, inputs)
    factories = PersistedContextAdapterFactories(
        projection_policy=_projection_policy(registry)
    )

    stale = task.model_copy(update={"state_version": task.state_version + 1})
    stale_request = request.model_copy(update={"state_version": stale.state_version})
    with pytest.raises(ContextSourceUnavailable, match="no longer matches"):
        asyncio.run(
            factories.candidate_source(repo).collect(
                task=stale,
                request=stale_request,
            )
        )

    matched_profile = inputs.model_profile.model_copy(
        update={"token_counter_mode": TokenCounterMode.MATCHED_TOKENIZER}
    )
    with pytest.raises(ContextCounterUnavailable, match="conservative-estimator"):
        asyncio.run(
            factories.context_assembler(repo).assemble(
                task=task,
                request=request,
                model_profile=matched_profile,
                context_profile=inputs.context_profile,
                registry_snapshot=registry,
            )
        )


def test_repository_scoped_context_factory_wires_into_local_bootstrap(
    repository,
) -> None:
    repo, _ = repository
    registry = _registry()
    context_factories = PersistedContextAdapterFactories(
        projection_policy=_projection_policy(registry)
    )
    capability_factories = CapabilityExecutorFactories(
        planner=lambda: PlannerRuntime(),
        plan_boundary=lambda repository: object(),
        tool_boundary=lambda repository: object(),
        tool_gateway=lambda repository: object(),
        answer_boundary=lambda repository: object(),
    )
    adapters = LocalPureAgentRuntimeAdapters(
        context_assembler=None,
        context_assembler_for_repository=context_factories.context_assembler,
        main_agent_inputs=lambda repository: PersistedLocalBoundaryInputsProvider(
            repository,
            policy=_boundary_policy(registry),
        ),
        admission_context=lambda repository: object(),
        action_loop=lambda: DynamicActionLoopRuntime(),
        capability_executors=capability_factories,
        slot_validators=SlotValidatorRegistry(),
    )

    boundary = adapters.component_factories().boundary_provider(repo)

    assert isinstance(boundary, PersistedMainAgentDecisionBoundaryProvider)
    with pytest.raises(TypeError, match="exactly one Context Assembler"):
        LocalPureAgentRuntimeAdapters(
            context_assembler=context_factories.context_assembler(repo).__class__,
            context_assembler_for_repository=context_factories.context_assembler,
            main_agent_inputs=lambda repository: object(),
            admission_context=lambda repository: object(),
            action_loop=lambda: DynamicActionLoopRuntime(),
            capability_executors=capability_factories,
            slot_validators=SlotValidatorRegistry(),
        )
