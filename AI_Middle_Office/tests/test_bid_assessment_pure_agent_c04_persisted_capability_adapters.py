from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.action_runtime import (
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
    ActionReservationIntent,
    AgentActionKind,
    PlanActionRequest,
)
from app.agents.bid_assessment_pure.answer_contracts import (
    AnswerDraft,
    AnswerLimitationCode,
    LimitationBlock,
)
from app.agents.bid_assessment_pure.capability_executors import (
    CapabilityExecutionRejected,
    ToolCallBatchCapabilityExecutor,
)
from app.agents.bid_assessment_pure.persisted_capability_adapters import (
    PersistedAnswerAuthorityRejected,
    PersistedAnswerCapabilityBoundaryProvider,
    PersistedCapabilityBoundaryRejected,
    PersistedPlanCapabilityBoundaryProvider,
    PersistedToolBoundaryPolicy,
    PersistedToolCallBatchBoundaryProvider,
    ReceiptOnlyAnswerAuthorityProjector,
)
from app.agents.bid_assessment_pure.persisted_local_adapters import (
    LocalBoundaryInputPolicy,
)
from app.agents.bid_assessment_pure.planning import IntentUnderstanding
from app.agents.bid_assessment_pure.registry import (
    BID_DOCUMENT_SEARCH,
    build_initial_registry,
)
from app.agents.bid_assessment_pure.repository import (
    ContextMessageRow,
    LocalTaskScopeSnapshot,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextIncludedEntry,
    ContextLane,
    ContextProfile,
    ContextProjectionEntry,
    ContextProtectionClass,
    ContextRepresentation,
    ContextSnapshot,
    ContextTrustClass,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    PersistedRuntimeAction,
    RuntimeActionExecution,
    RuntimeActionPersistenceEnvelope,
    RuntimeActionRecoveryBinding,
)
from app.agents.bid_assessment_pure.runtime_guards import (
    EffectReplayPolicy,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
)
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
from app.agents.bid_assessment_pure.tool_runtime import (
    RegistrySnapshot,
    canonical_hash,
    freeze_registry_snapshot,
)


def _registry() -> RegistrySnapshot:
    return freeze_registry_snapshot(
        build_initial_registry(),
        visible_names=(BID_DOCUMENT_SEARCH,),
    )


def _model_profile() -> ModelContextProfile:
    body = {
        "provider_ref": "provider:c04-static",
        "model_ref": "model:c04-static",
        "context_capacity_tokens": 16_000,
        "max_output_tokens": 2_000,
        "token_counter_ref": "counter:c04-static",
        "token_counter_mode": TokenCounterMode.CONSERVATIVE_ESTIMATOR.value,
        "framing_tokens": 16,
    }
    return ModelContextProfile(
        profile_ref="model-profile:c04",
        profile_hash=canonical_hash(body),
        **body,
    )


def _context_profile() -> ContextProfile:
    body = {
        "runtime_max_input_tokens": 8_000,
        "reserved_output_tokens": 1_000,
        "safety_margin_tokens": 500,
        "soft_compression_threshold_tokens": 6_000,
        "max_entries": 64,
    }
    return ContextProfile(
        profile_ref="context-profile:c04",
        profile_hash=canonical_hash(body),
        **body,
    )


def _boundary_policy(registry: RegistrySnapshot) -> LocalBoundaryInputPolicy:
    return LocalBoundaryInputPolicy(
        policy_snapshot_ref="policy:c04",
        prompt_template_ref="prompt:c04",
        authorization_policy_ref="authorization-policy:c04",
        model_profile=_model_profile(),
        context_profile=_context_profile(),
        registry_snapshot=registry,
        information_need_refs=("need:c04",),
        required_resource_refs=("enterprise-baseline:c04",),
    )


def _task(*, action_ref: str = "action:c04") -> AgentTaskState:
    return AgentTaskState(
        task_id="task:c04",
        session_id="conversation:c04",
        state_version=4,
        status=AgentTaskStatus.RUNNING,
        execution_mode="direct",
        goal_ref="goal:c04",
        plan_ref=None,
        pending_context=None,
        in_flight_action_ref=action_ref,
        observation_refs=(),
        last_error_ref=None,
    )


def _entry(*, with_projection: bool) -> ContextIncludedEntry | ContextProjectionEntry:
    body = {
        "entry_ref": "context-entry:user-c04",
        "stable_key": "control:current-user-message",
        "source_ref": "message:c04",
        "source_version_ref": "message-version:c04",
        "lane": ContextLane.ACTIVE_CONTROL,
        "kind": ContextEntryKind.CURRENT_USER_MESSAGE,
        "representation": ContextRepresentation.EXACT,
        "authority_label": "persisted-user-turn",
        "protection_class": ContextProtectionClass.MANDATORY_EXACT,
        "trust_class": ContextTrustClass.UNTRUSTED_DATA,
        "source_content_hash": canonical_hash({"text": "请研判风险"}),
        "projection_hash": canonical_hash("请研判风险"),
        "token_count": 16,
        "tool_name": None,
        "protocol_pair_ref": None,
    }
    if with_projection:
        return ContextProjectionEntry(
            **body,
            content='{"text":"请研判风险"}',
            untrusted_data=True,
        )
    return ContextIncludedEntry(**body)


def _context(
    *,
    state_version: int,
    consumer: ContextConsumer,
    registry: RegistrySnapshot,
    suffix: str,
) -> ContextAssemblyResult:
    included = _entry(with_projection=False)
    projection = _entry(with_projection=True)
    projection_hash = canonical_hash([projection.model_dump(mode="json")])
    snapshot = ContextSnapshot(
        snapshot_ref=f"context:c04-{suffix}",
        snapshot_sequence=state_version,
        task_ref="task:c04",
        state_version=state_version,
        consumer=consumer,
        status=ContextAssemblyStatus.READY,
        request_hash=canonical_hash({"request": suffix}),
        policy_snapshot_ref="policy:c04",
        prompt_template_ref="prompt:c04",
        model_profile_ref=_model_profile().profile_ref,
        model_profile_hash=_model_profile().profile_hash,
        context_profile_ref=_context_profile().profile_ref,
        context_profile_hash=_context_profile().profile_hash,
        registry_snapshot_ref=registry.snapshot_ref,
        registry_snapshot_hash=registry.snapshot_hash,
        authorization_snapshot_ref="authorization-snapshot:c04",
        dependency_refs=("message:c04",),
        included_entries=(included,),
        excluded_entries=(),
        compression_receipts=(),
        included_refs=(included.entry_ref,),
        excluded_refs=(),
        limitation_messages=(),
        estimated_input_tokens=32,
        effective_input_budget=8_000,
        reserved_output_tokens=1_000,
        safety_margin_tokens=500,
        projection_hash=projection_hash,
        snapshot_hash=canonical_hash({"snapshot": suffix}),
    )
    return ContextAssemblyResult(
        snapshot=snapshot,
        projection_entries=(projection,),
    )


def _scope() -> LocalTaskScopeSnapshot:
    return LocalTaskScopeSnapshot(
        task_id="task:c04",
        conversation_id="conversation:c04",
        task_state_version=4,
        task_row_version=4,
        conversation_row_version=1,
        conversation_status="active",
        owner_id=7,
        tenant_ref="tenant:c04",
        assessment_id="assessment-c04",
        trigger_message_id="message:c04",
        goal_ref="goal:c04",
        plan_ref=None,
        cancellation_fence_ref=None,
        latest_checkpoint_ref=None,
    )


def _runtime_profile() -> RuntimeProfileSnapshot:
    limits = RuntimeLimitSet(
        max_active_duration_ms=60_000,
        max_model_calls=4,
        max_tool_calls=4,
        max_total_input_tokens=8_000,
        max_total_output_tokens=2_000,
        max_cost_microunits=10_000,
        max_replans=2,
        max_answer_repairs=1,
        max_no_progress_actions=2,
        max_retry_attempts=1,
        max_parallel_read_calls=2,
        model_timeout_ms=30_000,
        tool_timeout_ms=10_000,
    )
    ceiling = RuntimePolicyCeiling.build(policy_ref="runtime-policy:c04", limits=limits)
    return RuntimeProfileSnapshot.build(
        profile_ref="runtime-profile:c04",
        policy=ceiling,
        limits=limits,
    )


def _scope_hash(
    *,
    context: ContextSnapshot,
    registry: RegistrySnapshot,
) -> str:
    scope = _scope()
    return canonical_hash(
        {
            "authorization_snapshot_ref": context.authorization_snapshot_ref,
            "task_ref": scope.task_id,
            "conversation_ref": scope.conversation_id,
            "owner_ref": f"user:{scope.owner_id}",
            "tenant_ref": scope.tenant_ref,
            "assessment_ref": f"assessment:{scope.assessment_id}",
            "context_snapshot_ref": context.snapshot_ref,
            "context_snapshot_hash": context.snapshot_hash,
            "registry_snapshot_ref": registry.snapshot_ref,
            "registry_snapshot_hash": registry.snapshot_hash,
            "visible_tools_hash": registry.visible_tools_hash,
        }
    )


def _action(
    *,
    kind: AgentActionKind,
    registry: RegistrySnapshot,
    decision_context: ContextSnapshot,
) -> PersistedRuntimeAction:
    arguments: dict[str, Any] = {"selected": kind.value}
    body = {
        "task_ref": "task:c04",
        "state_version": 3,
        "decision_ref": "decision:c04",
        "decision_hash": canonical_hash({"decision": kind.value}),
        "decision_observation_ref": "observation:decision-c04",
        "action_kind": kind.value,
        "arguments": arguments,
        "arguments_hash": canonical_hash(arguments),
        "effect_identity_seed": f"effect-seed:c04-{kind.value}",
        "context_snapshot_ref": decision_context.snapshot_ref,
        "context_snapshot_hash": decision_context.snapshot_hash,
        "registry_snapshot_ref": registry.snapshot_ref,
        "registry_snapshot_hash": registry.snapshot_hash,
        "visible_tools_hash": registry.visible_tools_hash,
    }
    digest = canonical_hash(body)
    intent = ActionReservationIntent(
        **body,
        intent_ref=f"action-intent:{digest.removeprefix('sha256:')}",
        intent_hash=digest,
    )
    recovery = RuntimeActionRecoveryBinding.build(
        profile=_runtime_profile(),
        authorization_policy_ref="authorization-policy:c04",
        scope_snapshot_hash=_scope_hash(
            context=decision_context,
            registry=registry,
        ),
    )
    return PersistedRuntimeAction(
        action_ref="action:c04",
        sequence=1,
        action_kind=kind,
        status="accepted",
        effect_fence_ref="effect:c04",
        effect_status="reserved",
        fencing_token=1,
        effect_key="effect-key:c04",
        effect_request_hash=intent.arguments_hash,
        effect_replay_policy=EffectReplayPolicy.SAFE_IDEMPOTENT,
        effect_result_ref=None,
        effect_result_hash=None,
        effect_error_code=None,
        envelope=RuntimeActionPersistenceEnvelope.build(
            intent=intent,
            recovery_binding=recovery,
        ),
    )


@dataclass
class _Repository:
    task: AgentTaskState
    decision_context: ContextSnapshot

    def load_task_state(self, task_id: str) -> AgentTaskState:
        assert task_id == self.task.task_id
        return self.task

    def load_local_task_scope(self, *, task_id: str, conversation_id: str):
        assert task_id == self.task.task_id
        assert conversation_id == self.task.session_id
        return _scope()

    def load_context_snapshot(self, *, task_id: str, snapshot_ref: str):
        assert task_id == self.task.task_id
        assert snapshot_ref == self.decision_context.snapshot_ref
        return self.decision_context

    def load_task_context_message(
        self,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ContextMessageRow:
        assert (task_id, conversation_id, message_id) == (
            "task:c04",
            "conversation:c04",
            "message:c04",
        )
        return ContextMessageRow(
            message_ref="message:c04",
            conversation_ref="conversation:c04",
            sequence_no=1,
            role="user",
            message_type="user.task_trigger",
            content={"text": "请研判风险"},
            content_hash=canonical_hash({"text": "请研判风险"}).removeprefix(
                "sha256:"
            ),
            reply_to_message_ref=None,
        )


@dataclass
class _Assembler:
    result: ContextAssemblyResult
    consumer: ContextConsumer | None = None

    async def assemble(self, *, task, request, **kwargs):
        del task, kwargs
        self.consumer = request.consumer
        return self.result


def test_tool_policy_is_fail_closed_by_default() -> None:
    assert PersistedToolBoundaryPolicy().runtime_enabled is False
    with pytest.raises(ValidationError, match="cannot grant execution authority"):
        PersistedToolBoundaryPolicy(runtime_enabled=False, allow_local=True)


def test_tool_boundary_rebinds_frozen_registry_and_current_scope() -> None:
    registry = _registry()
    task = _task()
    decision = _context(
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix="decision",
    ).snapshot
    provider = PersistedToolCallBatchBoundaryProvider(
        _Repository(task=task, decision_context=decision),  # type: ignore[arg-type]
        policy=_boundary_policy(registry),
        tool_policy=PersistedToolBoundaryPolicy(
            runtime_enabled=True,
            allowed_tool_names=(BID_DOCUMENT_SEARCH,),
            allow_local=True,
            authorized_document_refs=("document:c04",),
            timeout_seconds=15,
        ),
        clock=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
    )

    boundary = asyncio.run(
        provider.prepare(
            task=task,
            action=_action(
                kind=AgentActionKind.TOOL_CALL_BATCH,
                registry=registry,
                decision_context=decision,
            ),
        )
    )

    assert boundary.registry_snapshot == registry
    assert boundary.execution_context.user_ref == "user:7"
    assert boundary.execution_context.state_version == 4
    assert boundary.guard_policy.runtime_enabled is True
    assert boundary.deadline.remaining_seconds(
        now=datetime(2026, 8, 21, tzinfo=timezone.utc)
    ) == 15


def test_plan_boundary_uses_fresh_planner_context_and_complexity_gate() -> None:
    registry = _registry()
    task = _task()
    decision = _context(
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix="plan-decision",
    ).snapshot
    fresh = _context(
        state_version=4,
        consumer=ContextConsumer.PLANNER,
        registry=registry,
        suffix="planner-fresh",
    )
    assembler = _Assembler(fresh)
    provider = PersistedPlanCapabilityBoundaryProvider(
        _Repository(task=task, decision_context=decision),  # type: ignore[arg-type]
        policy=_boundary_policy(registry),
        context_assembler=assembler,  # type: ignore[arg-type]
    )
    request = PlanActionRequest(
        understanding=IntentUnderstanding(
            goal_summary="研判当前投标机会",
            information_needs=("招标要求", "企业匹配度"),
            source_hints=("bid_documents", "enterprise_knowledge"),
            clarification_needed=False,
            blocking_slot_name=None,
            execution_mode="planned",
            rationale="需要跨来源综合判断",
        ),
        reason="当前问题需要生成有限滚动计划",
    )

    boundary = asyncio.run(
        provider.prepare(
            task=task,
            action=_action(
                kind=AgentActionKind.PLAN,
                registry=registry,
                decision_context=decision,
            ),
            request=request,
        )
    )

    assert assembler.consumer is ContextConsumer.PLANNER
    assert boundary.context == fresh
    assert boundary.complexity.execution_mode.value == "planned"
    assert boundary.previous_plan is None


def test_answer_boundary_only_projects_non_citable_context_receipts() -> None:
    registry = _registry()
    task = _task()
    decision = _context(
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix="answer-decision",
    ).snapshot
    fresh = _context(
        state_version=4,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix="answer-fresh",
    )
    draft = AnswerDraft(
        response_language="zh-CN",
        blocks=(
            LimitationBlock(
                block_id="limitation:c04",
                code=AnswerLimitationCode.EVIDENCE_INSUFFICIENT,
                text="当前信息只能作为用户输入收据，尚不能形成可引用事实。",
                grounding_refs=("context-entry:user-c04",),
                applies_to_statement_refs=(),
            ),
        ),
        context_snapshot_ref=decision.snapshot_ref,
        state_version=1,
    )
    provider = PersistedAnswerCapabilityBoundaryProvider(
        _Repository(task=task, decision_context=decision),  # type: ignore[arg-type]
        policy=_boundary_policy(registry),
        context_assembler=_Assembler(fresh),  # type: ignore[arg-type]
    )

    boundary = asyncio.run(
        provider.prepare(
            task=task,
            action=_action(
                kind=AgentActionKind.ANSWER,
                registry=registry,
                decision_context=decision,
            ),
            draft=draft,
        )
    )

    assert len(boundary.grounding_snapshot.records) == 1
    assert boundary.grounding_snapshot.records[0].citable is False
    assert boundary.citation_authority_snapshot.records == ()
    assert boundary.previous_response is None


def test_receipt_authority_rejects_grounding_outside_fresh_context() -> None:
    registry = _registry()
    task = _task()
    fresh = _context(
        state_version=4,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix="missing-grounding",
    )
    draft = AnswerDraft(
        response_language="zh-CN",
        blocks=(
            LimitationBlock(
                block_id="limitation:missing",
                code=AnswerLimitationCode.EVIDENCE_INSUFFICIENT,
                text="证据不足。",
                grounding_refs=("context-entry:not-present",),
                applies_to_statement_refs=(),
            ),
        ),
        context_snapshot_ref="context:old",
        state_version=1,
    )

    with pytest.raises(PersistedAnswerAuthorityRejected, match="outside"):
        ReceiptOnlyAnswerAuthorityProjector().project(
            task=task,
            context=fresh,
            draft=draft,
        )


def test_missing_recovery_binding_rejects_capability_boundary() -> None:
    registry = _registry()
    task = _task()
    decision = _context(
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix="no-recovery",
    ).snapshot
    action = _action(
        kind=AgentActionKind.TOOL_CALL_BATCH,
        registry=registry,
        decision_context=decision,
    )
    action = action.model_copy(
        update={
            "envelope": RuntimeActionPersistenceEnvelope.build(
                intent=action.envelope.intent,
                recovery_binding=None,
            )
        }
    )
    provider = PersistedToolCallBatchBoundaryProvider(
        _Repository(task=task, decision_context=decision),  # type: ignore[arg-type]
        policy=_boundary_policy(registry),
        tool_policy=PersistedToolBoundaryPolicy(),
    )

    with pytest.raises(PersistedCapabilityBoundaryRejected, match="recoverable"):
        asyncio.run(provider.prepare(task=task, action=action))


def _persisted_tool_error_execution(
    *,
    error_code: str,
) -> tuple[AgentTaskState, PersistedRuntimeAction, RuntimeActionExecution]:
    registry = _registry()
    decision = _context(
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        registry=registry,
        suffix=f"tool-error-{error_code}",
    ).snapshot
    action = _action(
        kind=AgentActionKind.TOOL_CALL_BATCH,
        registry=registry,
        decision_context=decision,
    )
    call_ref = "tool-call:c04-persisted-error"
    payload = {
        "schema_name": "bid.pure-agent.capability.tool-batch-result.v1",
        "calls": [
            {
                "call_ref": call_ref,
                "tool_name": BID_DOCUMENT_SEARCH,
                "result": {
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": error_code,
                        "message": "No matching local result.",
                        "retryable": False,
                    },
                },
                "tool_message": None,
                "ledger_call_id": "ledger:c04-persisted-error",
                "accepted_for_context": True,
                "guard_decisions": [],
                "replayed": False,
                "provenance": [],
            }
        ],
    }
    result_hash = canonical_hash(payload)
    result_ref = f"tool-batch-result:{result_hash.removeprefix('sha256:')}"
    observation_body = {
        "task_ref": "task:c04",
        "source_action_ref": action.action_ref,
        "action_sequence": action.sequence,
        "state_version": 4,
        "kind": ActionObservationKind.TOOL_RESULT.value,
        "status": ActionObservationStatus.DEGRADED.value,
        "artifact_ref": result_ref,
        "artifact_hash": result_hash,
        "summary": "Tool batch preserved one accepted error result.",
        "material_progress": True,
        "progress_signal_refs": [call_ref],
        "limitation_codes": [error_code],
    }
    observation_hash = canonical_hash(observation_body)
    observation = ActionObservation(
        **observation_body,
        observation_ref=(
            f"observation:{observation_hash.removeprefix('sha256:')}"
        ),
        observation_hash=observation_hash,
    )
    task = _task(action_ref=None).model_copy(
        update={
            "state_version": 5,
            "observation_refs": (observation.observation_ref,),
        }
    )
    execution = RuntimeActionExecution(
        observation=observation,
        effect_status="succeeded",
        result_ref=result_ref,
        result_payload=payload,
    )
    return task, action, execution


def test_tool_error_result_round_trips_through_strict_json_contract() -> None:
    task, action, execution = _persisted_tool_error_execution(
        error_code="not_found"
    )
    executor = ToolCallBatchCapabilityExecutor(
        boundary_provider=object(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
    )

    post_action = asyncio.run(
        executor.after_observation(
            task=task,
            action=action,
            execution=execution,
        )
    )

    assert post_action.directive.value == "continue"


def test_tool_error_result_still_rejects_unknown_json_error_code() -> None:
    task, action, execution = _persisted_tool_error_execution(
        error_code="not_a_tool_error_code"
    )
    executor = ToolCallBatchCapabilityExecutor(
        boundary_provider=object(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(CapabilityExecutionRejected, match="persisted contract"):
        asyncio.run(
            executor.after_observation(
                task=task,
                action=action,
                execution=execution,
            )
        )
