"""V608 deterministic Runtime Governance calibration.

This evaluator exercises the six frozen Pure Agent guards with synthetic
contracts and an in-process SQLite transaction. It does not run the Agent,
call a model, read business documents, connect to MCP, or touch an external
database or deployment environment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.models.registry  # noqa: E402,F401  # Register FK targets only.
from app.agents.bid_assessment_pure.persistence_models import (  # noqa: E402
    BidPureAgentBudgetAccount,
    BidPureAgentCheckpoint,
)
from app.agents.bid_assessment_pure.repository import (  # noqa: E402
    PureAgentRepository,
)
from app.agents.bid_assessment_pure.runtime_guards import (  # noqa: E402
    ActionExecutionKind,
    ActionExecutionRequirements,
    ActionRuntimeBinding,
    BudgetBalance,
    BudgetDemand,
    BudgetReservationDirective,
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
    RuntimeGuardSuite,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeRecoveryGuard,
    RuntimeResourceType,
)
from app.agents.bid_assessment_pure.state import (  # noqa: E402
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
)
from app.agents.bid_assessment_pure.state_machine import create_running_task  # noqa: E402
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash  # noqa: E402
from app.core.database import Base  # noqa: E402


DATASET_PATH = (
    PROJECT_ROOT
    / "evals"
    / "bid_assessment"
    / "v608-runtime-governance-calibration.json"
)
SCHEMA_VERSION = "bid.pure_agent.v608.runtime_governance.v1"
RESULT_SCHEMA_VERSION = "bid.pure_agent.v608.result.v1"
EXPECTED_DOMAINS = {
    "budget",
    "loop",
    "effect",
    "cancellation",
    "direct_durable",
    "recovery",
}
PERSISTENCE_CASE_IDS = {"CAN-05", "CAN-06", "CAN-07", "CAN-08"}


class V608EvaluationError(RuntimeError):
    pass


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise V608EvaluationError("V608 dataset schema is not frozen")
    if payload.get("dataset_kind") != "synthetic_runtime_governance":
        raise V608EvaluationError("V608 dataset kind is not isolated")
    RuntimeLimitSet.model_validate(payload.get("runtime_profile"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise V608EvaluationError("V608 case matrix is empty")
    case_ids = [str(row.get("id") or "") for row in cases]
    if len(case_ids) != len(set(case_ids)) or any(not value for value in case_ids):
        raise V608EvaluationError("V608 case ids must be unique")
    domains = {str(row.get("domain") or "") for row in cases}
    if domains != EXPECTED_DOMAINS:
        raise V608EvaluationError("V608 must cover exactly six guard domains")
    isolation = payload.get("isolation_contract") or {}
    forbidden = (
        "external_database_allowed",
        "model_allowed",
        "business_documents_allowed",
        "embedding_reranker_allowed",
        "ocr_vision_allowed",
        "external_mcp_allowed",
        "ecs_allowed",
    )
    if any(bool(isolation.get(key)) for key in forbidden):
        raise V608EvaluationError("V608 isolation contract permits an external dependency")
    return payload


def _limits(dataset: Mapping[str, Any], **overrides: Any) -> RuntimeLimitSet:
    values = dict(dataset["runtime_profile"])
    values.update(overrides)
    return RuntimeLimitSet.model_validate(values)


def _policy_profile(
    dataset: Mapping[str, Any],
    *,
    profile_overrides: Mapping[str, Any] | None = None,
) -> tuple[RuntimePolicyCeiling, RuntimeProfileSnapshot]:
    policy_limits = _limits(dataset)
    policy = RuntimePolicyCeiling.build(
        policy_ref="policy:v608",
        limits=policy_limits,
    )
    profile_values = dict(dataset["runtime_profile"])
    profile_values.update(profile_overrides or {})
    profile = RuntimeProfileSnapshot.build(
        profile_ref="profile:v608",
        policy=policy,
        limits=RuntimeLimitSet.model_validate(profile_values),
    )
    return policy, profile


def _task(
    *,
    task_ref: str = "task:v608",
    state_version: int = 3,
    status: AgentTaskStatus = AgentTaskStatus.RUNNING,
    in_flight_action_ref: str | None = None,
) -> AgentTaskState:
    values = create_running_task(
        task_id=task_ref,
        session_id="conversation:v608",
        goal_ref="goal:v608",
    ).model_dump(mode="python")
    values.update(
        state_version=state_version,
        status=status,
        in_flight_action_ref=in_flight_action_ref,
    )
    if status is AgentTaskStatus.PENDING:
        values["pending_context"] = {
            "slot_ref": "slot:v608",
            "checkpoint_ref": "checkpoint:v608",
            "phase": "waiting_input",
            "validation_attempt_ref": None,
            "last_error_ref": None,
        }
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
            (
                RuntimeResourceType.MODEL_CALLS,
                RuntimeResourceType.INPUT_TOKENS,
                RuntimeResourceType.OUTPUT_TOKENS,
                RuntimeResourceType.COST_MICROUNITS,
            )
        )
    return ActionRuntimeBinding.build(
        binding_ref=f"binding:v608-{action_class.value}-{replay_policy.value}",
        action_class=action_class,
        effect_type="agent_action",
        replay_policy=replay_policy,
        reconciliation_supported=(
            replay_policy is EffectReplayPolicy.RECONCILE_REQUIRED
        ),
        required_budget_resources=tuple(resources),
        requirements=requirements
        or ActionExecutionRequirements(expected_duration_ms=500),
    )


def _candidate(
    *,
    semantic_label: str = "deadline",
    arguments_label: str = "evidence:1",
    binding: ActionRuntimeBinding | None = None,
) -> RuntimeActionCandidate:
    selected_binding = binding or _binding()
    body: dict[str, Any] = {
        "task_ref": "task:v608",
        "state_version": 3,
        "action_type": "evidence_read",
        "action_intent_ref": "intent:v608",
        "arguments_hash": canonical_hash({"evidence_ref": arguments_label}),
        "effect_key": "effect-key:v608",
        "context_snapshot_ref": "context:v608",
        "context_snapshot_hash": canonical_hash({"context": 1}),
        "registry_snapshot_ref": "registry:v608",
        "registry_snapshot_hash": canonical_hash({"registry": 1}),
        "visible_tools_hash": canonical_hash(["evidence_read"]),
        "binding_ref": selected_binding.binding_ref,
        "binding_hash": selected_binding.binding_hash,
        "profile_ref": "profile:v608",
        "profile_hash": canonical_hash({"profile": 1}),
        "policy_ref": "policy:v608",
        "policy_hash": canonical_hash({"policy": 1}),
        "budget_snapshot_ref": "budget:v608",
        "budget_snapshot_hash": canonical_hash({"budget": 1}),
        "progress_window_ref": "progress:v608",
        "progress_window_hash": canonical_hash({"progress": 1}),
        "semantic_basis_hash": canonical_hash({"question": semantic_label}),
        "scope_snapshot_hash": canonical_hash({"scope": "assessment"}),
        "expected_output_hash": canonical_hash({"type": "evidence"}),
        "cancellation_state_version": 3,
    }
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
    effect_key: str | None = None,
) -> EffectFenceSnapshot:
    return EffectFenceSnapshot(
        effect_fence_ref="effect-fence:v608",
        task_ref=candidate.task_ref,
        action_ref="action:v608",
        effect_key=effect_key or candidate.effect_key,
        request_hash=candidate.arguments_hash,
        replay_policy=replay_policy,
        status=status,
        fencing_token=7,
        result_ref=("result:v608" if status is EffectFenceStatus.SUCCEEDED else None),
        result_hash=(
            canonical_hash({"result": 1})
            if status is EffectFenceStatus.SUCCEEDED
            else None
        ),
    )


def _checkpoint(
    profile: RuntimeProfileSnapshot,
    *,
    action_ref: str | None = "action:v608",
    effect_fence_ref: str | None = "effect-fence:v608",
    result_persisted: bool = False,
    observation_accepted: bool = False,
) -> RuntimeCheckpointSnapshot:
    return RuntimeCheckpointSnapshot.build(
        checkpoint_ref="checkpoint:v608",
        task_ref="task:v608",
        state_version=3,
        status="open",
        context_snapshot_ref="context:v608",
        profile_ref=profile.profile_ref,
        profile_hash=profile.profile_hash,
        registry_snapshot_ref="registry:v608",
        registry_snapshot_hash=canonical_hash({"registry": 1}),
        action_ref=action_ref,
        effect_fence_ref=effect_fence_ref,
        result_persisted=result_persisted,
        observation_accepted=observation_accepted,
    )


def _case(
    rows: list[dict[str, Any]],
    case_id: str,
    domain: str,
    expected: str,
    actual: str,
    *,
    reason_codes: Sequence[str] = (),
    persistence: bool = False,
) -> None:
    rows.append(
        {
            "id": case_id,
            "domain": domain,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
            "reason_codes": list(reason_codes),
            "persistence": persistence,
        }
    )


def _evaluate_budget(
    dataset: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    policy, profile = _policy_profile(dataset)
    task = _task()
    guard = RuntimeBudgetGuard()
    snapshot = RuntimeBudgetSnapshot.build(
        snapshot_ref="budget:v608",
        task_ref=task.task_id,
        profile=profile,
        balances=(
            BudgetBalance(
                resource_type=RuntimeResourceType.MODEL_CALLS,
                unit="calls",
                limit_amount=profile.limits.max_model_calls,
                reserved_amount=3,
                spent_amount=0,
                row_version=1,
            ),
        ),
    )
    allowed = guard.evaluate(
        task=task,
        profile=profile,
        snapshot=snapshot,
        demands=(BudgetDemand(resource_type=RuntimeResourceType.MODEL_CALLS, amount=1),),
        reservation_seed="v608-boundary",
    )
    _case(rows, "BUD-01", "budget", "allowed", "allowed" if allowed.allowed else "exhausted", reason_codes=allowed.reason_codes)

    exhausted = guard.evaluate(
        task=task,
        profile=profile,
        snapshot=snapshot,
        demands=(BudgetDemand(resource_type=RuntimeResourceType.MODEL_CALLS, amount=2),),
        reservation_seed="v608-exhausted",
    )
    _case(rows, "BUD-02", "budget", "exhausted", "allowed" if exhausted.allowed else "exhausted", reason_codes=exhausted.reason_codes)

    reservation = BudgetReservationDirective(
        resource_type=RuntimeResourceType.MODEL_CALLS,
        amount=2,
        idempotency_key="budget-reservation:v608",
    )
    verified = guard.settle(
        reservations=(reservation,),
        usage=(BudgetUsage(resource_type=RuntimeResourceType.MODEL_CALLS, actual_amount=1, verified=True),),
    )[0]
    verified_actual = (
        "verified_settlement"
        if verified.settle_amount == 1 and not verified.usage_unverified
        else "invalid"
    )
    _case(rows, "BUD-03", "budget", "verified_settlement", verified_actual)

    conservative = guard.settle(reservations=(reservation,), usage=())[0]
    conservative_actual = (
        "conservative_settlement"
        if conservative.settle_amount == 2 and conservative.usage_unverified
        else "invalid"
    )
    _case(rows, "BUD-04", "budget", "conservative_settlement", conservative_actual)

    try:
        guard.settle(
            reservations=(reservation,),
            usage=(BudgetUsage(resource_type=RuntimeResourceType.MODEL_CALLS, actual_amount=3, verified=True),),
        )
        overage_actual = "overage_accepted"
    except RuntimeGuardRejected:
        overage_actual = "overage_rejected"
    _case(rows, "BUD-05", "budget", "overage_rejected", overage_actual)

    expanded_profile = RuntimeProfileSnapshot.build(
        profile_ref="profile:v608-expanded",
        policy=policy,
        limits=_limits(dataset, max_model_calls=policy.limits.max_model_calls + 1),
    )
    try:
        RuntimeGuardSuite._validate_profile(profile=expanded_profile, policy=policy)
        expansion_actual = "profile_expansion_allowed"
    except RuntimeGuardRejected:
        expansion_actual = "profile_expansion_rejected"
    _case(rows, "BUD-06", "budget", "profile_expansion_rejected", expansion_actual)
    return {
        "budget_oversubscription_count": int(exhausted.allowed)
        + int(overage_actual != "overage_rejected")
        + int(expansion_actual != "profile_expansion_rejected")
    }


def _evaluate_loop(
    dataset: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    _, profile = _policy_profile(dataset)
    guard = ProgressLoopGuard()
    candidate_a = _candidate(semantic_label="A", arguments_label="a")
    candidate_b = _candidate(semantic_label="B", arguments_label="b")
    fingerprint_a = guard.fingerprint(candidate_a)
    fingerprint_b = guard.fingerprint(candidate_b)
    empty = ProgressWindow.build(window_ref="progress:v608-0", task_ref="task:v608")

    record_a1, decision_1 = guard.after_observation(
        task_ref="task:v608",
        state_version=3,
        fingerprint=fingerprint_a,
        observation_hash=canonical_hash({"observation": "same"}),
        material_progress=False,
        progress_signal_refs=(),
        window=empty,
        profile=profile,
    )
    _case(rows, "LOOP-01", "loop", "pass", decision_1.disposition.value, reason_codes=decision_1.reason_codes)
    window_1 = ProgressWindow.build(
        window_ref="progress:v608-1",
        task_ref="task:v608",
        records=(record_a1,),
    )
    record_a2, decision_2 = guard.after_observation(
        task_ref="task:v608",
        state_version=4,
        fingerprint=fingerprint_a,
        observation_hash=canonical_hash({"observation": "same"}),
        material_progress=False,
        progress_signal_refs=(),
        window=window_1,
        profile=profile,
    )
    _case(rows, "LOOP-02", "loop", "warning", decision_2.disposition.value, reason_codes=decision_2.reason_codes)
    window_2 = ProgressWindow.build(
        window_ref="progress:v608-2",
        task_ref="task:v608",
        records=(record_a1, record_a2),
    )
    _, decision_3 = guard.after_observation(
        task_ref="task:v608",
        state_version=5,
        fingerprint=fingerprint_a,
        observation_hash=canonical_hash({"observation": "same"}),
        material_progress=False,
        progress_signal_refs=(),
        window=window_2,
        profile=profile,
    )
    _case(rows, "LOOP-03", "loop", "stop", decision_3.disposition.value, reason_codes=decision_3.reason_codes)

    repeat = guard.before_action(
        fingerprint=fingerprint_a,
        window=window_1,
        profile=profile,
    )
    repeat_actual = (
        "repeat_warning"
        if repeat.disposition is LoopDisposition.WARNING
        and "REPEATED_SEMANTIC_ACTION_WITHOUT_PROGRESS" in repeat.reason_codes
        else repeat.disposition.value
    )
    _case(rows, "LOOP-04", "loop", "repeat_warning", repeat_actual, reason_codes=repeat.reason_codes)

    record_b, _ = guard.after_observation(
        task_ref="task:v608",
        state_version=4,
        fingerprint=fingerprint_b,
        observation_hash=canonical_hash({"observation": "B"}),
        material_progress=False,
        progress_signal_refs=(),
        window=window_1,
        profile=profile,
    )
    window_ab = ProgressWindow.build(
        window_ref="progress:v608-ab",
        task_ref="task:v608",
        records=(record_a1, record_b),
    )
    cycle = guard.before_action(
        fingerprint=fingerprint_a,
        window=window_ab,
        profile=profile,
    )
    cycle_actual = (
        "cycle_warning"
        if cycle.disposition is LoopDisposition.WARNING
        and "ACTION_CYCLE_WITHOUT_PROGRESS" in cycle.reason_codes
        else cycle.disposition.value
    )
    _case(rows, "LOOP-05", "loop", "cycle_warning", cycle_actual, reason_codes=cycle.reason_codes)

    _, reset = guard.after_observation(
        task_ref="task:v608",
        state_version=6,
        fingerprint=fingerprint_b,
        observation_hash=canonical_hash({"observation": "new"}),
        material_progress=True,
        progress_signal_refs=("signal:v608-new-evidence",),
        window=window_2,
        profile=profile,
    )
    reset_actual = (
        "progress_reset"
        if reset.disposition is LoopDisposition.PASS and reset.no_progress_streak == 0
        else reset.disposition.value
    )
    _case(rows, "LOOP-06", "loop", "progress_reset", reset_actual, reason_codes=reset.reason_codes)
    return {"loop_stop_observation": decision_3.no_progress_streak}


def _evaluate_effect(rows: list[dict[str, Any]]) -> dict[str, int]:
    binding = _binding()
    candidate = _candidate(binding=binding)
    guard = RuntimeEffectGuard()
    scenarios = (
        ("EFF-01", "reserve_new", None),
        ("EFF-02", "reuse_result", _effect(candidate, status=EffectFenceStatus.SUCCEEDED)),
        ("EFF-03", "await_existing", _effect(candidate, status=EffectFenceStatus.RUNNING)),
        ("EFF-04", "reconcile", _effect(candidate, status=EffectFenceStatus.UNCERTAIN)),
        ("EFF-05", "scope_rejected", _effect(candidate, status=EffectFenceStatus.RUNNING, effect_key="effect-key:other")),
    )
    directive_to_actual = {
        EffectDirective.RESERVE_NEW: "reserve_new",
        EffectDirective.REUSE_RESULT: "reuse_result",
        EffectDirective.AWAIT_EXISTING: "await_existing",
        EffectDirective.RECONCILE: "reconcile",
        EffectDirective.REJECT: "scope_rejected",
    }
    duplicate_effect_commit_count = 0
    for case_id, expected, existing in scenarios:
        decision = guard.evaluate(candidate=candidate, binding=binding, existing=existing)
        actual = directive_to_actual[decision.directive]
        if existing is not None and decision.directive is EffectDirective.RESERVE_NEW:
            duplicate_effect_commit_count += 1
        _case(rows, case_id, "effect", expected, actual, reason_codes=decision.reason_codes)

    no_replay_binding = _binding(replay_policy=EffectReplayPolicy.NO_REPLAY)
    no_replay_candidate = _candidate(binding=no_replay_binding)
    no_replay = guard.evaluate(
        candidate=no_replay_candidate,
        binding=no_replay_binding,
        existing=_effect(
            no_replay_candidate,
            status=EffectFenceStatus.FAILED,
            replay_policy=EffectReplayPolicy.NO_REPLAY,
        ),
    )
    actual = (
        "non_replayable_rejected"
        if no_replay.directive is EffectDirective.REJECT
        else no_replay.directive.value
    )
    if no_replay.directive is EffectDirective.RESERVE_NEW:
        duplicate_effect_commit_count += 1
    _case(rows, "EFF-06", "effect", "non_replayable_rejected", actual, reason_codes=no_replay.reason_codes)
    return {"duplicate_effect_commit_count": duplicate_effect_commit_count}


def _evaluate_cancellation_guard(rows: list[dict[str, Any]]) -> dict[str, int]:
    task = _task(in_flight_action_ref="action:v608")
    cancellation = CancellationSnapshot(
        task_ref=task.task_id,
        state_version=task.state_version,
        cancellation_fence_ref=None,
    )
    candidate = _candidate()
    effect = _effect(candidate, status=EffectFenceStatus.RUNNING)
    result = ResultAcceptanceCandidate(
        task_ref=task.task_id,
        action_ref="action:v608",
        accepted_state_version=task.state_version,
        effect_fence_ref=effect.effect_fence_ref,
        fencing_token=effect.fencing_token,
        request_hash=effect.request_hash,
    )
    guard = CancellationLateResultGuard()
    active = guard.evaluate_result(
        task=task,
        cancellation=cancellation,
        candidate=result,
        effect=effect,
    )
    active_actual = (
        "active_result_accepted"
        if active.disposition is LateResultDisposition.ACCEPT
        else "active_result_rejected"
    )
    _case(rows, "CAN-01", "cancellation", "active_result_accepted", active_actual, reason_codes=active.reason_codes)

    fenced_snapshot = cancellation.model_copy(
        update={"cancellation_fence_ref": "cancel:v608"}
    )
    fenced = guard.evaluate_result(
        task=task,
        cancellation=fenced_snapshot,
        candidate=result,
        effect=effect,
    )
    fenced_actual = (
        "fenced_result_rejected"
        if fenced.disposition is LateResultDisposition.REJECT_LATE
        else "fenced_result_accepted"
    )
    _case(rows, "CAN-02", "cancellation", "fenced_result_rejected", fenced_actual, reason_codes=fenced.reason_codes)

    stale_result = result.model_copy(update={"accepted_state_version": 2})
    stale = guard.evaluate_result(
        task=task,
        cancellation=cancellation,
        candidate=stale_result,
        effect=effect,
    )
    stale_actual = (
        "stale_result_rejected"
        if stale.disposition is LateResultDisposition.REJECT_LATE
        else "stale_result_accepted"
    )
    _case(rows, "CAN-03", "cancellation", "stale_result_rejected", stale_actual, reason_codes=stale.reason_codes)

    try:
        guard.assert_before_action(task=task, cancellation=fenced_snapshot)
        action_actual = "new_action_allowed"
    except RuntimeGuardRejected:
        action_actual = "new_action_rejected"
    _case(rows, "CAN-04", "cancellation", "new_action_rejected", action_actual)
    return {
        "late_result_acceptance_count": int(fenced.disposition is LateResultDisposition.ACCEPT)
        + int(stale.disposition is LateResultDisposition.ACCEPT)
    }


def _evaluate_execution(
    dataset: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    _, profile = _policy_profile(dataset)
    guard = DirectDurableGuard()
    direct = guard.decide(
        binding=_binding(action_class=RuntimeActionClass.TOOL),
        profile=profile,
    )
    _case(rows, "EXE-01", "direct_durable", "direct", direct.execution_kind.value if direct.execution_kind else "rejected", reason_codes=direct.reason_codes)

    duration = guard.decide(
        binding=_binding(
            action_class=RuntimeActionClass.TOOL,
            requirements=ActionExecutionRequirements(expected_duration_ms=10_001),
        ),
        profile=profile,
    )
    duration_actual = (
        "durable_by_duration"
        if duration.execution_kind is ActionExecutionKind.DURABLE
        and "EXPECTED_DURATION_EXCEEDS_DIRECT_TIMEOUT" in duration.reason_codes
        else "misclassified"
    )
    _case(rows, "EXE-02", "direct_durable", "durable_by_duration", duration_actual, reason_codes=duration.reason_codes)

    requirements = guard.decide(
        binding=_binding(
            action_class=RuntimeActionClass.TOOL,
            requirements=ActionExecutionRequirements(
                expected_duration_ms=500,
                requires_worker_isolation=True,
                requires_restart_recovery=True,
                requires_heartbeat=True,
                remote_completion_receipt=True,
                effect_may_outlive_request=True,
            ),
        ),
        profile=profile,
    )
    requirements_actual = (
        "durable_by_requirements"
        if requirements.execution_kind is ActionExecutionKind.DURABLE
        and len(requirements.reason_codes) == 5
        else "misclassified"
    )
    _case(rows, "EXE-03", "direct_durable", "durable_by_requirements", requirements_actual, reason_codes=requirements.reason_codes)

    parallel = guard.decide(
        binding=_binding(action_class=RuntimeActionClass.TOOL),
        profile=profile,
        active_parallel_reads=profile.limits.max_parallel_read_calls,
    )
    parallel_actual = "parallel_limit_rejected" if not parallel.allowed else "parallel_limit_allowed"
    _case(rows, "EXE-04", "direct_durable", "parallel_limit_rejected", parallel_actual, reason_codes=parallel.reason_codes)
    expected = {
        "EXE-01": "direct",
        "EXE-02": "durable_by_duration",
        "EXE-03": "durable_by_requirements",
        "EXE-04": "parallel_limit_rejected",
    }
    actuals = {row["id"]: row["actual"] for row in rows if row["id"].startswith("EXE-")}
    return {
        "direct_durable_misclassification_count": sum(
            actuals.get(case_id) != expected_value
            for case_id, expected_value in expected.items()
        )
    }


def _recovery_decision(
    guard: RuntimeRecoveryGuard,
    *,
    task: AgentTaskState,
    checkpoint: RuntimeCheckpointSnapshot,
    profile: RuntimeProfileSnapshot,
    cancellation: CancellationSnapshot,
    effect: EffectFenceSnapshot | None,
    lease_expired: bool = True,
    authorization_valid: bool = True,
    source_heads_valid: bool = True,
    registry_hash: str | None = None,
    retry_attempt_count: int = 0,
) -> tuple[str, tuple[str, ...]]:
    decision = guard.decide(
        task=task,
        checkpoint=checkpoint,
        profile=profile,
        current_registry_snapshot_hash=(
            checkpoint.registry_snapshot_hash
            if registry_hash is None
            else registry_hash
        ),
        cancellation=cancellation,
        effect=effect,
        lease_expired=lease_expired,
        authorization_valid=authorization_valid,
        source_heads_valid=source_heads_valid,
        retry_attempt_count=retry_attempt_count,
    )
    return decision.directive.value, decision.reason_codes


def _evaluate_recovery(
    dataset: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    _, profile = _policy_profile(dataset)
    guard = RuntimeRecoveryGuard()
    candidate = _candidate()
    running = _task(in_flight_action_ref="action:v608")
    checkpoint = _checkpoint(profile)
    cancellation = CancellationSnapshot(
        task_ref="task:v608",
        state_version=3,
        cancellation_fence_ref=None,
    )

    terminal = _task(status=AgentTaskStatus.CANCELLED)
    actual, reasons = _recovery_decision(
        guard,
        task=terminal,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=None,
    )
    _case(rows, "REC-01", "recovery", "terminal_no_action", "terminal_no_action" if actual == RecoveryDirective.NO_ACTION.value else actual, reason_codes=reasons)

    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        lease_expired=False,
    )
    _case(rows, "REC-02", "recovery", "wait_for_lease", actual, reason_codes=reasons)

    pending = _task(status=AgentTaskStatus.PENDING)
    actual, reasons = _recovery_decision(
        guard,
        task=pending,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=None,
    )
    _case(rows, "REC-03", "recovery", "wait_for_user", actual, reason_codes=reasons)

    observed_checkpoint = _checkpoint(
        profile,
        result_persisted=True,
        observation_accepted=True,
    )
    actual, reasons = _recovery_decision(
        guard,
        task=_task(),
        checkpoint=observed_checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.SUCCEEDED),
    )
    _case(rows, "REC-04", "recovery", "continue_accepted_observation", "continue_accepted_observation" if actual == RecoveryDirective.CONTINUE_FROM_CHECKPOINT.value else actual, reason_codes=reasons)

    persisted_checkpoint = _checkpoint(profile, result_persisted=True)
    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=persisted_checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.SUCCEEDED),
    )
    _case(rows, "REC-05", "recovery", "consume_persisted_result", actual, reason_codes=reasons)

    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.UNCERTAIN),
    )
    _case(rows, "REC-06", "recovery", "reconcile_uncertain", "reconcile_uncertain" if actual == RecoveryDirective.RECONCILE.value else actual, reason_codes=reasons)

    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        retry_attempt_count=1,
    )
    _case(rows, "REC-07", "recovery", "retry_safe", actual, reason_codes=reasons)

    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        retry_attempt_count=profile.limits.max_retry_attempts,
    )
    _case(rows, "REC-08", "recovery", "retry_limit_blocked", "retry_limit_blocked" if actual == RecoveryDirective.BLOCKED.value else actual, reason_codes=reasons)

    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        registry_hash=canonical_hash({"registry": "changed"}),
    )
    _case(rows, "REC-09", "recovery", "registry_change_blocked", "registry_change_blocked" if actual == RecoveryDirective.BLOCKED.value else actual, reason_codes=reasons)

    actual, reasons = _recovery_decision(
        guard,
        task=running,
        checkpoint=checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=_effect(candidate, status=EffectFenceStatus.RUNNING),
        authorization_valid=False,
    )
    _case(rows, "REC-10", "recovery", "authorization_change_blocked", "authorization_change_blocked" if actual == RecoveryDirective.BLOCKED.value else actual, reason_codes=reasons)

    no_effect_checkpoint = _checkpoint(
        profile,
        action_ref=None,
        effect_fence_ref=None,
    )
    actual, reasons = _recovery_decision(
        guard,
        task=_task(),
        checkpoint=no_effect_checkpoint,
        profile=profile,
        cancellation=cancellation,
        effect=None,
    )
    _case(rows, "REC-11", "recovery", "no_effect_continue", "no_effect_continue" if actual == RecoveryDirective.CONTINUE_FROM_CHECKPOINT.value else actual, reason_codes=reasons)

    unsafe_replay_ids = {"REC-01", "REC-03", "REC-04", "REC-05", "REC-06", "REC-08", "REC-09", "REC-10", "REC-11"}
    actual_by_id = {row["id"]: row["actual"] for row in rows if row["domain"] == "recovery"}
    return {
        "unsafe_recovery_replay_count": sum(
            actual_by_id.get(case_id) == RecoveryDirective.RETRY_SAFE.value
            for case_id in unsafe_replay_ids
        )
    }


def _transition_event(
    state: AgentTaskState,
    event_type: TaskEventType,
    *,
    event_id: str,
    **overrides: Any,
) -> TaskTransitionEvent:
    values: dict[str, Any] = {
        "event_id": event_id,
        "task_id": state.task_id,
        "expected_state_version": state.state_version,
        "event_type": event_type,
        "effect_idempotency_key": None,
        "action_ref": None,
        "pending_context": None,
        "resume_proof": None,
        "execution_mode": None,
        "plan_ref": None,
        "observation_ref": None,
        "result_committed": False,
        "error_ref": None,
        "cancellation_fence_ref": None,
    }
    values.update(overrides)
    return TaskTransitionEvent.model_validate(values)


def _repository_task(
    repo: PureAgentRepository,
    *,
    suffix: str,
) -> tuple[str, str]:
    conversation = repo.create_conversation(
        owner_id=1,
        tenant_ref="tenant:v608",
        conversation_id=f"conversation-v608-{suffix}",
    )
    message = repo.append_message(
        conversation_id=conversation.id,
        role="user",
        message_type="user.task_trigger",
        content={"text": "synthetic runtime governance"},
        created_by_ref="user:1",
        idempotency_key=f"message-key:v608-{suffix}",
    )
    task = repo.create_task(
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        owner_id=1,
        goal_ref=f"goal:v608-{suffix}",
    )
    return conversation.id, task.task_id


def _persisted_late_result(
    repo: PureAgentRepository,
    *,
    suffix: str,
) -> bool:
    _, task_id = _repository_task(repo, suffix=suffix)
    reservation = repo.reserve_action(
        task_id=task_id,
        event_id=f"event-action:v608-{suffix}",
        action_type="evidence_read",
        execution_kind="direct",
        arguments={"evidence_ref": "evidence:v608"},
        effect_key=f"effect-key:v608-{suffix}",
        effect_type="tool_call",
        replay_policy="safe_idempotent",
        fencing_token=9,
    )
    repo.mark_effect_running(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=9,
        expected_state_version=reservation.state.state_version,
    )
    repo.cancel_task(
        task_id=task_id,
        event_id=f"event-cancel:v608-{suffix}",
        requested_by_ref="user:1",
        reason="synthetic cancel",
        expected_state_version=reservation.state.state_version,
    )
    late = repo.settle_effect(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=9,
        expected_state_version=reservation.state.state_version,
        status="succeeded",
        result_ref="result:v608-late",
        result={"synthetic": True},
    )
    return late.status == "ignored_late" and not late.accepted_for_context


def _pending_checkpoint_invalidated(
    repo: PureAgentRepository,
    session: Session,
    *,
    suffix: str,
) -> bool:
    conversation_id, task_id = _repository_task(repo, suffix=suffix)
    reservation = repo.reserve_action(
        task_id=task_id,
        event_id=f"event-action:v608-{suffix}",
        action_type="request_information_basis",
        execution_kind="direct",
        arguments={"missing": "deadline"},
        effect_key=f"effect-key:v608-{suffix}",
        effect_type="agent_action",
        replay_policy="safe_idempotent",
        fencing_token=1,
    )
    repo.mark_effect_running(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=1,
        expected_state_version=reservation.state.state_version,
    )
    settled = repo.settle_effect(
        effect_fence_id=reservation.effect_fence_id,
        fencing_token=1,
        expected_state_version=reservation.state.state_version,
        status="succeeded",
        result_ref=f"observation:v608-{suffix}",
        result={"missing": "deadline"},
    )
    if not settled.accepted_for_context:
        return False
    observed = repo.commit_transition(
        _transition_event(
            reservation.state,
            TaskEventType.OBSERVATION_ACCEPTED,
            event_id=f"event-observation:v608-{suffix}",
            action_ref=reservation.action_id,
            observation_ref=f"observation:v608-{suffix}",
        )
    ).state
    suspension = repo.suspend_for_slot(
        task_id=task_id,
        event_id=f"event-pending:v608-{suffix}",
        name="bid.deadline",
        request_message="synthetic slot request",
        input_model_ref="slot-model:v608",
        business_validator_refs=("validator:v608",),
        context_snapshot_ref=f"context:v608-{suffix}",
        suspended_action_id=reservation.action_id,
        effect_fence_id=reservation.effect_fence_id,
        resume_token="resume-token-v608",
    )
    repo.claim_pending_recovery(
        task_id=task_id,
        lease_owner="worker:v608",
        lease_seconds=30,
    )
    cancelled = repo.cancel_task(
        task_id=task_id,
        event_id=f"event-cancel:v608-{suffix}",
        requested_by_ref="user:1",
        reason="synthetic pending cancel",
        expected_state_version=suspension.state.state_version,
        expected_owner_id=1,
        expected_conversation_id=conversation_id,
    )
    checkpoint = session.get(
        BidPureAgentCheckpoint,
        suspension.checkpoint.checkpoint_id,
    )
    return bool(
        observed.status is AgentTaskStatus.RUNNING
        and cancelled.state.status is AgentTaskStatus.CANCELLED
        and checkpoint is not None
        and checkpoint.status == "invalidated"
        and checkpoint.recovery_lease_owner is None
        and checkpoint.recovery_lease_until is None
    )


def _cancel_budget_case(
    repo: PureAgentRepository,
    session: Session,
    *,
    suffix: str,
    running: bool,
) -> bool:
    _, task_id = _repository_task(repo, suffix=suffix)
    account = repo.create_budget_account(
        task_id=task_id,
        resource_type="tool_calls",
        unit="calls",
        limit_amount=5,
    )
    reservation = repo.reserve_action(
        task_id=task_id,
        event_id=f"event-action:v608-{suffix}",
        action_type="evidence_read",
        execution_kind="direct",
        arguments={"evidence_ref": "evidence:v608"},
        effect_key=f"effect-key:v608-{suffix}",
        effect_type="tool_call",
        replay_policy="safe_idempotent",
        fencing_token=3,
    )
    repo.reserve_budget(
        task_id=task_id,
        resource_type="tool_calls",
        amount=2,
        idempotency_key=f"budget-reserve:v608-{suffix}",
        action_id=reservation.action_id,
    )
    if running:
        repo.mark_effect_running(
            effect_fence_id=reservation.effect_fence_id,
            fencing_token=3,
            expected_state_version=reservation.state.state_version,
        )
    repo.cancel_task(
        task_id=task_id,
        event_id=f"event-cancel:v608-{suffix}",
        requested_by_ref="user:1",
        reason="synthetic budget cancel",
        expected_state_version=reservation.state.state_version,
    )
    session.flush()
    session.refresh(account)
    expected_actual = 2 if running else 0
    return int(account.reserved_amount) == 0 and int(account.actual_amount) == expected_actual


def _evaluate_persistence(rows: list[dict[str, Any]]) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        repo = PureAgentRepository(session)
        late_ok = _persisted_late_result(repo, suffix="late")
        _case(rows, "CAN-05", "cancellation", "persisted_late_result_ignored", "persisted_late_result_ignored" if late_ok else "persisted_late_result_accepted", persistence=True)
        checkpoint_ok = _pending_checkpoint_invalidated(repo, session, suffix="pending")
        _case(rows, "CAN-06", "cancellation", "pending_checkpoint_invalidated", "pending_checkpoint_invalidated" if checkpoint_ok else "pending_checkpoint_open", persistence=True)
        released = _cancel_budget_case(repo, session, suffix="budget-unused", running=False)
        _case(rows, "CAN-07", "cancellation", "unused_budget_released", "unused_budget_released" if released else "unused_budget_charged", persistence=True)
        conservative = _cancel_budget_case(repo, session, suffix="budget-running", running=True)
        _case(rows, "CAN-08", "cancellation", "running_budget_conservatively_settled", "running_budget_conservatively_settled" if conservative else "running_budget_released", persistence=True)
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def evaluate(dataset: Mapping[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    metrics: dict[str, int] = {}
    metrics.update(_evaluate_budget(dataset, rows))
    metrics.update(_evaluate_loop(dataset, rows))
    metrics.update(_evaluate_effect(rows))
    metrics.update(_evaluate_cancellation_guard(rows))
    metrics.update(_evaluate_execution(dataset, rows))
    metrics.update(_evaluate_recovery(dataset, rows))
    _evaluate_persistence(rows)

    expected = {str(row["id"]): row for row in dataset["cases"]}
    actual = {str(row["id"]): row for row in rows}
    if set(actual) != set(expected):
        raise V608EvaluationError("V608 evaluator and frozen case matrix diverged")
    if any(
        row["domain"] != expected[case_id]["domain"]
        or row["expected"] != expected[case_id]["expected"]
        for case_id, row in actual.items()
    ):
        raise V608EvaluationError("V608 expected contracts diverged from the dataset")

    passed_count = sum(bool(row["passed"]) for row in rows)
    persistence_rows = [row for row in rows if row["id"] in PERSISTENCE_CASE_IDS]
    domain_metrics = {
        domain: {
            "passed": sum(row["passed"] for row in rows if row["domain"] == domain),
            "total": sum(row["domain"] == domain for row in rows),
        }
        for domain in sorted(EXPECTED_DOMAINS)
    }
    thresholds = dataset["thresholds"]
    pass_rate = passed_count / len(rows)
    persistence_pass_rate = sum(row["passed"] for row in persistence_rows) / len(
        persistence_rows
    )
    gates = {
        "all_scenarios": pass_rate >= float(thresholds["scenario_pass_rate_min"]),
        "budget_no_oversubscription": metrics["budget_oversubscription_count"]
        <= int(thresholds["budget_oversubscription_count_max"]),
        "effect_exactly_once": metrics["duplicate_effect_commit_count"]
        <= int(thresholds["duplicate_effect_commit_count_max"]),
        "late_result_isolated": metrics["late_result_acceptance_count"]
        <= int(thresholds["late_result_acceptance_count_max"]),
        "recovery_no_unsafe_replay": metrics["unsafe_recovery_replay_count"]
        <= int(thresholds["unsafe_recovery_replay_count_max"]),
        "direct_durable_exact": metrics["direct_durable_misclassification_count"]
        <= int(thresholds["direct_durable_misclassification_count_max"]),
        "loop_bounded": metrics["loop_stop_observation"]
        <= int(thresholds["loop_stop_observation_max"]),
        "persistence_atomic": persistence_pass_rate
        >= float(thresholds["persistence_scenario_pass_rate_min"]),
    }
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "passed" if all(gates.values()) else "failed",
        "dataset_kind": dataset["dataset_kind"],
        "case_count": len(rows),
        "passed_count": passed_count,
        "pass_rate": round(pass_rate, 6),
        "persistence_case_count": len(persistence_rows),
        "persistence_pass_rate": round(persistence_pass_rate, 6),
        "domain_metrics": domain_metrics,
        "safety_metrics": metrics,
        "gates": gates,
        "runtime_profile": dataset["runtime_profile"],
        "isolation": {
            "database": "sqlite_memory_only",
            "model": False,
            "business_documents": False,
            "embedding_reranker": False,
            "ocr_vision": False,
            "external_mcp": False,
            "ecs": False,
        },
        "cases": rows,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated V608 Runtime Governance calibration."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        result = evaluate(load_dataset(args.dataset))
        if args.output is not None:
            args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
            args.output.resolve().write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "passed": result["passed_count"],
                    "total": result["case_count"],
                    "gates": result["gates"],
                    "safety_metrics": result["safety_metrics"],
                    "elapsed_seconds": result["elapsed_seconds"],
                    "output": None if args.output is None else str(args.output.resolve()),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if result["status"] == "passed" else 2
    except (V608EvaluationError, OSError, ValidationError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
