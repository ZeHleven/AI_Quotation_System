from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.runtime_guards import (
    ActionExecutionKind,
    ActionExecutionRequirements,
    ActionProgressRecord,
    ActionRuntimeBinding,
    BudgetBalance,
    BudgetDemand,
    BudgetUsage,
    CancellationLateResultGuard,
    CancellationSnapshot,
    DirectDurableGuard,
    EffectDirective,
    EffectFenceSnapshot,
    EffectFenceStatus,
    EffectReplayPolicy,
    LateResultDisposition,
    LoopDisposition,
    ProgressLoopGuard,
    ProgressWindow,
    RecoveryDirective,
    ResultAcceptanceCandidate,
    RuntimeActionCandidate,
    RuntimeActionClass,
    RuntimeBudgetGuard,
    RuntimeBudgetSnapshot,
    RuntimeCheckpointSnapshot,
    RuntimeEffectGuard,
    RuntimeGuardRejected,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeRecoveryGuard,
    RuntimeResourceType,
)
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash


def _limits(**overrides: Any) -> RuntimeLimitSet:
    values: dict[str, Any] = {
        "max_active_duration_ms": 60_000,
        "max_model_calls": 4,
        "max_tool_calls": 6,
        "max_total_input_tokens": 20_000,
        "max_total_output_tokens": 5_000,
        "max_cost_microunits": 1_000_000,
        "max_replans": 2,
        "max_answer_repairs": 2,
        "max_no_progress_actions": 2,
        "max_retry_attempts": 2,
        "max_parallel_read_calls": 1,
        "model_timeout_ms": 30_000,
        "tool_timeout_ms": 10_000,
    }
    values.update(overrides)
    return RuntimeLimitSet.model_validate(values)


def _profile(**limit_overrides: Any) -> RuntimeProfileSnapshot:
    limits = _limits(**limit_overrides)
    policy = RuntimePolicyCeiling.build(policy_ref="policy:v601", limits=limits)
    return RuntimeProfileSnapshot.build(
        profile_ref="profile:v601",
        policy=policy,
        limits=limits,
    )


def _task(
    *,
    state_version: int = 1,
    status: AgentTaskStatus = AgentTaskStatus.RUNNING,
    in_flight_action_ref: str | None = None,
) -> AgentTaskState:
    values = create_running_task(
        task_id="task:v601",
        session_id="conversation:v601",
        goal_ref="goal:v601",
    ).model_dump(mode="python")
    values.update(
        state_version=state_version,
        status=status,
        in_flight_action_ref=in_flight_action_ref,
    )
    return AgentTaskState.model_validate(values)


def _binding(
    *,
    action_class: RuntimeActionClass = RuntimeActionClass.LOCAL,
    replay_policy: EffectReplayPolicy = EffectReplayPolicy.SAFE_IDEMPOTENT,
    requirements: ActionExecutionRequirements | None = None,
) -> ActionRuntimeBinding:
    resources = [RuntimeResourceType.ACTIVE_DURATION_MS]
    if action_class is RuntimeActionClass.TOOL:
        resources.append(RuntimeResourceType.TOOL_CALLS)
    elif action_class is RuntimeActionClass.MODEL:
        resources.extend(
            [
                RuntimeResourceType.MODEL_CALLS,
                RuntimeResourceType.INPUT_TOKENS,
                RuntimeResourceType.OUTPUT_TOKENS,
                RuntimeResourceType.COST_MICROUNITS,
            ]
        )
    return ActionRuntimeBinding.build(
        binding_ref=f"binding:{action_class.value}",
        action_class=action_class,
        effect_type="agent_action",
        replay_policy=replay_policy,
        reconciliation_supported=(
            replay_policy is EffectReplayPolicy.RECONCILE_REQUIRED
        ),
        required_budget_resources=tuple(resources),
        requirements=requirements or ActionExecutionRequirements(expected_duration_ms=500),
    )


def _candidate(**overrides: Any) -> RuntimeActionCandidate:
    body: dict[str, Any] = {
        "task_ref": "task:v601",
        "state_version": 3,
        "action_type": "evidence_read",
        "action_intent_ref": "intent:v601",
        "arguments_hash": canonical_hash({"evidence_ref": "evidence:1"}),
        "effect_key": "effect-key:v601",
        "context_snapshot_ref": "context:v601",
        "context_snapshot_hash": canonical_hash({"context": 1}),
        "registry_snapshot_ref": "registry:v601",
        "registry_snapshot_hash": canonical_hash({"registry": 1}),
        "visible_tools_hash": canonical_hash(["evidence_read"]),
        "binding_ref": "binding:local",
        "binding_hash": canonical_hash({"binding": 1}),
        "profile_ref": "profile:v601",
        "profile_hash": canonical_hash({"profile": 1}),
        "policy_ref": "policy:v601",
        "policy_hash": canonical_hash({"policy": 1}),
        "budget_snapshot_ref": "budget:v601",
        "budget_snapshot_hash": canonical_hash({"budget": 1}),
        "progress_window_ref": "progress:v601",
        "progress_window_hash": canonical_hash({"progress": 1}),
        "semantic_basis_hash": canonical_hash({"question": "deadline"}),
        "scope_snapshot_hash": canonical_hash({"scope": "assessment"}),
        "expected_output_hash": canonical_hash({"type": "evidence"}),
        "cancellation_state_version": 3,
    }
    body.update(overrides)
    digest = canonical_hash(body)
    return RuntimeActionCandidate(
        **body,
        candidate_ref=f"runtime-action:{digest.removeprefix('sha256:')}",
        candidate_hash=digest,
    )


def _effect(
    candidate: RuntimeActionCandidate,
    *,
    status: EffectFenceStatus,
    replay_policy: EffectReplayPolicy = EffectReplayPolicy.SAFE_IDEMPOTENT,
    **overrides: Any,
) -> EffectFenceSnapshot:
    values: dict[str, Any] = {
        "effect_fence_ref": "effect-fence:v601",
        "task_ref": candidate.task_ref,
        "action_ref": "action:v601",
        "effect_key": candidate.effect_key,
        "request_hash": candidate.arguments_hash,
        "replay_policy": replay_policy,
        "status": status,
        "fencing_token": 7,
        "result_ref": "result:v601" if status is EffectFenceStatus.SUCCEEDED else None,
        "result_hash": (
            canonical_hash({"result": 1})
            if status is EffectFenceStatus.SUCCEEDED
            else None
        ),
    }
    values.update(overrides)
    return EffectFenceSnapshot.model_validate(values)


def _checkpoint(
    profile: RuntimeProfileSnapshot,
    *,
    task_ref: str = "task:v601",
    state_version: int = 3,
    status: str = "open",
    result_persisted: bool = False,
    observation_accepted: bool = False,
) -> RuntimeCheckpointSnapshot:
    return RuntimeCheckpointSnapshot.build(
        checkpoint_ref="checkpoint:v601",
        task_ref=task_ref,
        state_version=state_version,
        status=status,
        context_snapshot_ref="context:v601",
        profile_ref=profile.profile_ref,
        profile_hash=profile.profile_hash,
        registry_snapshot_ref="registry:v601",
        registry_snapshot_hash=canonical_hash({"registry": 1}),
        action_ref="action:v601",
        effect_fence_ref="effect-fence:v601",
        result_persisted=result_persisted,
        observation_accepted=observation_accepted,
    )


def test_hashed_contracts_reject_tampering_and_inconsistent_checkpoint() -> None:
    profile = _profile()
    payload = profile.model_dump(mode="python")
    payload["profile_hash"] = canonical_hash({"tampered": True})
    with pytest.raises(ValidationError, match="profile_hash"):
        RuntimeProfileSnapshot.model_validate(payload)

    with pytest.raises(ValidationError, match="accepted Observation"):
        _checkpoint(profile, observation_accepted=True, result_persisted=False)

    checkpoint = _checkpoint(profile, result_persisted=True)
    checkpoint_payload = checkpoint.model_dump(mode="python")
    checkpoint_payload["context_snapshot_ref"] = "context:tampered"
    with pytest.raises(ValidationError, match="checkpoint_hash"):
        RuntimeCheckpointSnapshot.model_validate(checkpoint_payload)


def test_budget_guard_reserves_rejects_exhaustion_and_settles_conservatively() -> None:
    task = _task()
    profile = _profile()
    snapshot = RuntimeBudgetSnapshot.build(
        snapshot_ref="budget:v601",
        task_ref=task.task_id,
        profile=profile,
        balances=(
            BudgetBalance(
                resource_type=RuntimeResourceType.MODEL_CALLS,
                unit="call",
                limit_amount=4,
                reserved_amount=1,
                spent_amount=1,
                row_version=2,
            ),
        ),
    )
    guard = RuntimeBudgetGuard()
    allowed = guard.evaluate(
        task=task,
        profile=profile,
        snapshot=snapshot,
        demands=(BudgetDemand(resource_type=RuntimeResourceType.MODEL_CALLS, amount=2),),
        reservation_seed="action:v601",
    )
    assert allowed.allowed
    assert allowed.reservations[0].amount == 2

    settlement = guard.settle(reservations=allowed.reservations, usage=())
    assert settlement[0].settle_amount == 2
    assert settlement[0].usage_unverified

    verified = guard.settle(
        reservations=allowed.reservations,
        usage=(
            BudgetUsage(
                resource_type=RuntimeResourceType.MODEL_CALLS,
                actual_amount=1,
                verified=True,
            ),
        ),
    )
    assert verified[0].settle_amount == 1
    assert not verified[0].usage_unverified

    exhausted = guard.evaluate(
        task=task,
        profile=profile,
        snapshot=snapshot,
        demands=(BudgetDemand(resource_type=RuntimeResourceType.MODEL_CALLS, amount=3),),
        reservation_seed="action:v602",
    )
    assert not exhausted.allowed
    assert exhausted.exhausted_resources == (RuntimeResourceType.MODEL_CALLS,)


def test_direct_durable_guard_uses_bounded_requirements_and_parallel_limit() -> None:
    profile = _profile()
    guard = DirectDurableGuard()
    direct = guard.decide(binding=_binding(), profile=profile)
    assert direct.execution_kind is ActionExecutionKind.DIRECT

    durable = guard.decide(
        binding=_binding(
            requirements=ActionExecutionRequirements(
                expected_duration_ms=500,
                requires_restart_recovery=True,
            )
        ),
        profile=profile,
    )
    assert durable.execution_kind is ActionExecutionKind.DURABLE
    assert "RESTART_RECOVERY_REQUIRED" in durable.reason_codes

    parallel_denied = guard.decide(
        binding=_binding(action_class=RuntimeActionClass.TOOL),
        profile=profile,
        active_parallel_reads=1,
    )
    assert not parallel_denied.allowed
    assert parallel_denied.execution_kind is None


def test_effect_guard_reserves_reuses_waits_reconciles_and_rejects_mismatch() -> None:
    candidate = _candidate()
    binding = _binding()
    guard = RuntimeEffectGuard()
    assert guard.evaluate(candidate=candidate, binding=binding, existing=None).directive is EffectDirective.RESERVE_NEW
    assert guard.evaluate(
        candidate=candidate,
        binding=binding,
        existing=_effect(candidate, status=EffectFenceStatus.RESERVED),
    ).directive is EffectDirective.AWAIT_EXISTING
    assert guard.evaluate(
        candidate=candidate,
        binding=binding,
        existing=_effect(candidate, status=EffectFenceStatus.SUCCEEDED),
    ).directive is EffectDirective.REUSE_RESULT
    assert guard.evaluate(
        candidate=candidate,
        binding=binding,
        existing=_effect(candidate, status=EffectFenceStatus.UNCERTAIN),
    ).directive is EffectDirective.RECONCILE
    mismatch = guard.evaluate(
        candidate=candidate,
        binding=binding,
        existing=_effect(
            candidate,
            status=EffectFenceStatus.RESERVED,
            request_hash=canonical_hash({"different": True}),
        ),
    )
    assert mismatch.directive is EffectDirective.REJECT


def test_progress_loop_guard_warns_then_stops_and_resets_on_governed_progress() -> None:
    profile = _profile(max_no_progress_actions=2)
    guard = ProgressLoopGuard()
    candidate = _candidate()
    fingerprint = guard.fingerprint(candidate)
    window = ProgressWindow.build(
        window_ref="progress:v601",
        task_ref=candidate.task_ref,
    )
    decisions = []
    for index in range(3):
        record, decision = guard.after_observation(
            task_ref=candidate.task_ref,
            state_version=index + 1,
            fingerprint=fingerprint,
            observation_hash=canonical_hash({"observation": index}),
            material_progress=False,
            progress_signal_refs=(),
            window=window,
            profile=profile,
        )
        decisions.append(decision.disposition)
        window = ProgressWindow.build(
            window_ref=f"progress:v601:{index}",
            task_ref=candidate.task_ref,
            records=(*window.records, record),
        )
    assert decisions == [LoopDisposition.PASS, LoopDisposition.WARNING, LoopDisposition.STOP]

    progress_record, progress_decision = guard.after_observation(
        task_ref=candidate.task_ref,
        state_version=4,
        fingerprint=fingerprint,
        observation_hash=canonical_hash({"observation": "new-evidence"}),
        material_progress=True,
        progress_signal_refs=("grounding:new",),
        window=window,
        profile=profile,
    )
    assert isinstance(progress_record, ActionProgressRecord)
    assert progress_decision.no_progress_streak == 0
    assert progress_decision.disposition is LoopDisposition.PASS


def test_cancellation_and_late_result_guard_require_exact_active_fences() -> None:
    task = _task(state_version=3, in_flight_action_ref="action:v601")
    cancellation = CancellationSnapshot(
        task_ref=task.task_id,
        state_version=task.state_version,
        cancellation_fence_ref=None,
    )
    CancellationLateResultGuard.assert_before_action(task=task, cancellation=cancellation)
    candidate = _candidate()
    effect = _effect(candidate, status=EffectFenceStatus.RUNNING)
    result = ResultAcceptanceCandidate(
        task_ref=task.task_id,
        action_ref="action:v601",
        accepted_state_version=task.state_version,
        effect_fence_ref=effect.effect_fence_ref,
        fencing_token=effect.fencing_token,
        request_hash=effect.request_hash,
    )
    accepted = CancellationLateResultGuard.evaluate_result(
        task=task,
        cancellation=cancellation,
        candidate=result,
        effect=effect,
    )
    assert accepted.disposition is LateResultDisposition.ACCEPT

    fenced = cancellation.model_copy(update={"cancellation_fence_ref": "cancel:v601"})
    with pytest.raises(RuntimeGuardRejected):
        CancellationLateResultGuard.assert_before_action(task=task, cancellation=fenced)
    rejected = CancellationLateResultGuard.evaluate_result(
        task=task,
        cancellation=fenced,
        candidate=result,
        effect=effect,
    )
    assert rejected.disposition is LateResultDisposition.REJECT_LATE


def test_recovery_guard_never_reexecutes_persisted_or_uncertain_effects() -> None:
    profile = _profile()
    task = _task(state_version=3, in_flight_action_ref="action:v601")
    checkpoint = _checkpoint(profile)
    registry_hash = checkpoint.registry_snapshot_hash
    cancellation = CancellationSnapshot(
        task_ref=task.task_id,
        state_version=task.state_version,
        cancellation_fence_ref=None,
    )
    candidate = _candidate()
    guard = RuntimeRecoveryGuard()

    active_lease = guard.decide(
        task=task,
        checkpoint=checkpoint,
        profile=profile,
        current_registry_snapshot_hash=registry_hash,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        lease_expired=False,
        authorization_valid=True,
        source_heads_valid=True,
        retry_attempt_count=0,
    )
    assert active_lease.directive is RecoveryDirective.WAIT_FOR_LEASE

    persisted = guard.decide(
        task=task,
        checkpoint=_checkpoint(profile, result_persisted=True),
        profile=profile,
        current_registry_snapshot_hash=registry_hash,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.SUCCEEDED),
        lease_expired=True,
        authorization_valid=True,
        source_heads_valid=True,
        retry_attempt_count=0,
    )
    assert persisted.directive is RecoveryDirective.CONSUME_PERSISTED_RESULT

    uncertain = guard.decide(
        task=task,
        checkpoint=checkpoint,
        profile=profile,
        current_registry_snapshot_hash=registry_hash,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.UNCERTAIN),
        lease_expired=True,
        authorization_valid=True,
        source_heads_valid=True,
        retry_attempt_count=0,
    )
    assert uncertain.directive is RecoveryDirective.RECONCILE

    safe_retry = guard.decide(
        task=task,
        checkpoint=checkpoint,
        profile=profile,
        current_registry_snapshot_hash=registry_hash,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        lease_expired=True,
        authorization_valid=True,
        source_heads_valid=True,
        retry_attempt_count=1,
    )
    assert safe_retry.directive is RecoveryDirective.RETRY_SAFE

    changed_registry = guard.decide(
        task=task,
        checkpoint=checkpoint,
        profile=profile,
        current_registry_snapshot_hash=canonical_hash({"registry": "changed"}),
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        lease_expired=True,
        authorization_valid=True,
        source_heads_valid=True,
        retry_attempt_count=0,
    )
    assert changed_registry.directive is RecoveryDirective.BLOCKED


def test_pending_recovery_is_always_wait_for_user() -> None:
    profile = _profile()
    pending_values = _task(state_version=3).model_dump(mode="python")
    pending_values.update(
        status=AgentTaskStatus.PENDING,
        pending_context={
            "slot_ref": "slot:v601",
            "checkpoint_ref": "checkpoint:v601",
            "phase": "waiting_input",
            "validation_attempt_ref": None,
            "last_error_ref": None,
        },
    )
    pending = AgentTaskState.model_validate(pending_values)
    checkpoint = _checkpoint(profile)
    cancellation = CancellationSnapshot(
        task_ref=pending.task_id,
        state_version=pending.state_version,
        cancellation_fence_ref=None,
    )
    decision = RuntimeRecoveryGuard().decide(
        task=pending,
        checkpoint=checkpoint,
        profile=profile,
        current_registry_snapshot_hash=checkpoint.registry_snapshot_hash,
        cancellation=cancellation,
        effect=None,
        lease_expired=True,
        authorization_valid=True,
        source_heads_valid=True,
        retry_attempt_count=0,
    )
    assert decision.directive is RecoveryDirective.WAIT_FOR_USER
