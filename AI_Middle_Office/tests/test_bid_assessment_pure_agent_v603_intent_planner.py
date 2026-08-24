from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.agents.bid_assessment_pure.complexity_gate import DefaultComplexityGate
from app.agents.bid_assessment_pure.context_runtime import (
    ContextAssemblerRuntime,
    InMemoryContextSnapshotStore,
    PrecountedContextTokenCounter,
    StaticContextCandidateSource,
    context_request_key,
)
from app.agents.bid_assessment_pure.intent_runtime import (
    IntentContractRejected,
    IntentUnderstandingRuntime,
    StaticIntentUnderstandingProvider,
)
from app.agents.bid_assessment_pure.planner_runtime import (
    PlanRevisionReason,
    PlannerContractRejected,
    PlannerRuntime,
    StaticPlannerProvider,
)
from app.agents.bid_assessment_pure.planning import (
    ComplexityDecision,
    ExecutionMode,
    InformationSourceHint,
    IntentUnderstanding,
    PlanNextDecision,
    PlanStep,
    PlanUserProjection,
    StepRiskLevel,
    TaskPlan,
)
from app.agents.bid_assessment_pure.provider_bridges import (
    ProviderIntentUnderstandingProvider,
    ProviderPlannerProvider,
)
from app.agents.bid_assessment_pure.provider_runtime import (
    OpenAICompatibleChatCodec,
    ProviderAdapter,
    ProviderAdapterError,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderTransportFailure,
    ProviderFailure,
    ProviderWireRequest,
    StructuredModelCallBridge,
)
from app.agents.bid_assessment_pure.rag_adapters import build_fake_registry
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyRequest,
    ContextConsumer,
    ContextEntryCandidate,
    ContextEntryKind,
    ContextLane,
    ContextOmissionAction,
    ContextProfile,
    ContextProtectionClass,
    ContextRepresentation,
    ContextTrustClass,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.state import AgentTaskState
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_runtime import (
    RegistrySnapshot,
    canonical_hash,
    canonical_json,
    freeze_registry_snapshot,
)


AUTHORIZATION_REF = "authorization:v603"
MODEL_PROFILE_REF = "model-profile:v603"
MODEL_PROFILE_HASH = canonical_hash({"profile": "v603", "model": "synthetic"})
CONTEXT_PROFILE_REF = "context-profile:v603"


def _task(
    *,
    execution_mode: ExecutionMode = ExecutionMode.DIRECT,
    plan_ref: str | None = None,
    observations: tuple[str, ...] = ("observation:v603:1",),
    state_version: int = 1,
) -> AgentTaskState:
    values = create_running_task(
        task_id="task:v603",
        session_id="conversation:v603",
        goal_ref="goal:v603",
    ).model_dump(mode="python")
    values.update(
        execution_mode=execution_mode,
        plan_ref=plan_ref,
        observation_refs=observations,
        state_version=state_version,
    )
    return AgentTaskState.model_validate(values)


def _understanding(
    *,
    mode: ExecutionMode = ExecutionMode.DIRECT,
    needs: tuple[str, ...] = ("投标截止时间",),
    sources: tuple[InformationSourceHint, ...] = (
        InformationSourceHint.BID_DOCUMENTS,
    ),
    clarification: bool = False,
) -> IntentUnderstanding:
    return IntentUnderstanding(
        goal_summary="判断当前招标机会的重要信息和风险",
        information_needs=needs,
        source_hints=sources,
        clarification_needed=clarification,
        blocking_slot_name="assessment.documents" if clarification else None,
        execution_mode=mode,
        rationale="根据当前问题所需证据范围选择执行方式",
    )


def _plan(*, tool_name: str = "bid_document_search", suffix: str = "") -> TaskPlan:
    return TaskPlan(
        goal_summary=f"核对资格条件并判断企业匹配风险{suffix}",
        completion_criteria=(
            "定位招标资格条件的可引用证据",
            "核对企业能力并明确未知项",
        ),
        steps=(
            PlanStep(
                id="S1",
                title="检索招标资格条件",
                description="在授权招标资料范围内定位资格、业绩和人员要求。",
                dependencies=(),
                tool_hint=tool_name,
                expected_output="资格条件候选及来源定位",
                output_schema={
                    "type": "object",
                    "properties": {"evidence_refs": {"type": "array"}},
                    "required": ["evidence_refs"],
                },
                risk_level=StepRiskLevel.LOW,
            ),
            PlanStep(
                id="S2",
                title="核对企业能力",
                description="基于企业知识库核对资质、业绩和人员能力。",
                dependencies=("S1",),
                tool_hint="enterprise_knowledge_search",
                expected_output="匹配项、缺口和证据引用",
                output_schema={
                    "type": "object",
                    "properties": {
                        "matches": {"type": "array"},
                        "unknowns": {"type": "array"},
                    },
                    "required": ["matches", "unknowns"],
                },
                risk_level=StepRiskLevel.MEDIUM,
            ),
        ),
        next_decision=PlanNextDecision(
            type="execute_step",
            step_id="S1",
            summary="先取得资格条件证据，再决定后续动作。",
        ),
        replan_conditions=("关键资料缺失", "招标条件与企业事实冲突"),
        user_projection=PlanUserProjection(
            summary="先查资格条件，再核对企业能力。",
            visible_step_ids=("S1", "S2"),
        ),
    )


def _candidate(
    *,
    entry_ref: str,
    kind: ContextEntryKind,
    lane: ContextLane,
    trust: ContextTrustClass,
    content: str,
    priority: int,
    tool_name: str | None = None,
) -> ContextEntryCandidate:
    return ContextEntryCandidate(
        entry_ref=entry_ref,
        stable_key=entry_ref,
        source_ref=f"source:{entry_ref}",
        source_version_ref=f"source-version:{entry_ref}",
        source_content_hash=canonical_hash(content),
        authorization_snapshot_ref=AUTHORIZATION_REF,
        lane=lane,
        kind=kind,
        representation=ContextRepresentation.EXACT,
        authority_label="v603 synthetic",
        protection_class=ContextProtectionClass.MANDATORY_EXACT,
        trust_class=trust,
        content=content,
        token_count=max(1, len(content) // 3),
        required=True,
        priority=priority,
        omission_action=ContextOmissionAction.FAIL,
        tool_name=tool_name,
    )


async def _context(
    *,
    consumer: ContextConsumer,
    user_message: str,
    output_contract: str,
    registry_snapshot: RegistrySnapshot | None = None,
    task: AgentTaskState | None = None,
) -> Any:
    task = task or _task()
    visible_names = () if registry_snapshot is None else registry_snapshot.visible_tool_names
    request = ContextAssemblyRequest(
        task_ref=task.task_id,
        state_version=task.state_version,
        consumer=consumer,
        user_message_ref="message:v603:user",
        visible_tool_names=visible_names,
        information_need_refs=(),
        required_resource_refs=(),
        policy_snapshot_ref="policy:v603",
        prompt_template_ref=f"prompt:v603:{consumer.value}",
        registry_snapshot_ref=(
            None if registry_snapshot is None else registry_snapshot.snapshot_ref
        ),
        model_profile_ref=MODEL_PROFILE_REF,
        context_profile_ref=CONTEXT_PROFILE_REF,
        checkpoint_snapshot_ref=None,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        snapshot_sequence=1,
    )
    candidates = [
        _candidate(
            entry_ref="context-entry:v603:policy",
            kind=ContextEntryKind.POLICY,
            lane=ContextLane.POLICY_PROTOCOL,
            trust=ContextTrustClass.TRUSTED_POLICY,
            content=(
                "你是投标机会研判主 Agent 的受限结构化能力。只理解当前目标，"
                "不要假设未提供的事实，不输出思维链。"
            ),
            priority=100,
        ),
        _candidate(
            entry_ref="context-entry:v603:output",
            kind=ContextEntryKind.OUTPUT_CONTRACT,
            lane=ContextLane.POLICY_PROTOCOL,
            trust=ContextTrustClass.TRUSTED_POLICY,
            content=output_contract,
            priority=99,
        ),
        _candidate(
            entry_ref="context-entry:v603:task",
            kind=ContextEntryKind.TASK_STATE,
            lane=ContextLane.ACTIVE_CONTROL,
            trust=ContextTrustClass.TRUSTED_RUNTIME,
            content=canonical_json(
                {
                    "task_ref": task.task_id,
                    "status": task.status.value,
                    "execution_mode": task.execution_mode.value,
                }
            ),
            priority=98,
        ),
        _candidate(
            entry_ref="message:v603:user",
            kind=ContextEntryKind.CURRENT_USER_MESSAGE,
            lane=ContextLane.ACTIVE_CONTROL,
            trust=ContextTrustClass.UNTRUSTED_DATA,
            content=user_message,
            priority=97,
        ),
    ]
    if registry_snapshot is not None:
        for index, contract in enumerate(
            registry_snapshot.model_visible_contracts(), start=1
        ):
            candidates.append(
                _candidate(
                    entry_ref=f"context-entry:v603:tool:{index}",
                    kind=ContextEntryKind.TOOL_CONTRACT,
                    lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
                    trust=ContextTrustClass.TRUSTED_TOOL_CONTRACT,
                    content=canonical_json(contract.model_dump(mode="json")),
                    priority=90 - index,
                    tool_name=contract.name,
                )
            )
    model_profile = ModelContextProfile(
        profile_ref=MODEL_PROFILE_REF,
        profile_hash=MODEL_PROFILE_HASH,
        provider_ref="provider:v603",
        model_ref="model:v603",
        context_capacity_tokens=16_384,
        max_output_tokens=2_048,
        token_counter_ref="token-counter:v603",
        token_counter_mode=TokenCounterMode.CONSERVATIVE_ESTIMATOR,
        framing_tokens=32,
    )
    context_profile = ContextProfile(
        profile_ref=CONTEXT_PROFILE_REF,
        profile_hash=canonical_hash({"profile": "context-v603"}),
        runtime_max_input_tokens=8_192,
        reserved_output_tokens=2_048,
        safety_margin_tokens=512,
        soft_compression_threshold_tokens=6_000,
        max_entries=32,
    )
    assembler = ContextAssemblerRuntime(
        candidate_source=StaticContextCandidateSource(
            {context_request_key(request): tuple(candidates)}
        ),
        token_counter=PrecountedContextTokenCounter(),
        snapshot_store=InMemoryContextSnapshotStore(),
    )
    return await assembler.assemble(
        task=task,
        request=request,
        model_profile=model_profile,
        context_profile=context_profile,
        registry_snapshot=registry_snapshot,
    )


class _WireTokenCounter:
    async def count(self, *, request: Any, payload: Mapping[str, Any]) -> int:
        del request
        return max(1, len(canonical_json(dict(payload))) // 3)


class _SchemaResponseTransport:
    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self.responses = dict(responses)
        self.requests: list[ProviderWireRequest] = []

    async def invoke(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        try:
            schema_name = request.payload["response_format"]["json_schema"]["name"]
            payload = self.responses[schema_name]
        except (KeyError, TypeError) as exc:
            raise ProviderTransportFailure(
                ProviderFailure(
                    code=ProviderErrorCode.PROVIDER_REJECTED,
                    safe_message="synthetic provider response is not configured",
                )
            ) from exc
        return {
            "id": "response:v603",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        }


def _bridge(transport: _SchemaResponseTransport) -> StructuredModelCallBridge:
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:v603",
        provider_ref="provider:v603",
        model_ref="model:v603",
        model_profile_ref=MODEL_PROFILE_REF,
        model_profile_hash=MODEL_PROFILE_HASH,
        codec_ref=OpenAICompatibleChatCodec.codec_ref,
        token_counter_ref="provider-token-counter:v603",
        enabled=True,
        supports_function_calling=True,
        supports_strict_tools=False,
        supports_structured_output=True,
        supports_strict_structured_output=False,
        supports_parallel_tool_calls=False,
        supports_tool_calls_with_structured_output=True,
        max_output_tokens=2_048,
    )
    return StructuredModelCallBridge(
        ProviderAdapter(
            capabilities=capabilities,
            codec=OpenAICompatibleChatCodec(),
            token_counter=_WireTokenCounter(),
            transport=transport,
        )
    )


@pytest.mark.parametrize(
    ("understanding", "expected_mode", "reason"),
    (
        (
            _understanding(),
            ExecutionMode.DIRECT,
            "short_single_goal_path_remains_direct",
        ),
        (
            _understanding(mode=ExecutionMode.PLANNED),
            ExecutionMode.PLANNED,
            "intent_understanding_recommended_planned",
        ),
        (
            _understanding(
                sources=(
                    InformationSourceHint.BID_DOCUMENTS,
                    InformationSourceHint.ENTERPRISE_KNOWLEDGE,
                )
            ),
            ExecutionMode.PLANNED,
            "cross_source_synthesis_requires_planning",
        ),
        (
            _understanding(needs=("资格", "业绩", "人员", "财务")),
            ExecutionMode.PLANNED,
            "many_information_needs_require_planning",
        ),
        (
            _understanding(clarification=True),
            ExecutionMode.DIRECT,
            "blocking_clarification_precedes_planning",
        ),
    ),
)
def test_complexity_gate_selects_open_direct_or_planned_path(
    understanding: IntentUnderstanding,
    expected_mode: ExecutionMode,
    reason: str,
) -> None:
    task = _task()
    decision = DefaultComplexityGate().decide(
        task=task,
        understanding=understanding,
    )
    assert decision.execution_mode is expected_mode
    assert reason in decision.reasons
    assert decision.preserves_observation_refs == task.observation_refs


def test_complexity_gate_never_downgrades_a_planned_task() -> None:
    task = _task(execution_mode=ExecutionMode.PLANNED, plan_ref="plan:v603:1")
    decision = DefaultComplexityGate().decide(
        task=task,
        understanding=_understanding(mode=ExecutionMode.DIRECT),
    )
    assert decision.execution_mode is ExecutionMode.PLANNED
    assert "planned_mode_is_not_downgraded" in decision.reasons
    assert decision.preserves_observation_refs == task.observation_refs


def test_intent_runtime_revalidates_provider_output() -> None:
    context = asyncio.run(
        _context(
            consumer=ContextConsumer.INTENT,
            user_message="投标截止时间是什么时候？",
            output_contract="返回 IntentUnderstanding JSON。",
        )
    )
    response = _understanding().model_dump(mode="json")
    runtime = IntentUnderstandingRuntime(
        StaticIntentUnderstandingProvider({context.snapshot.snapshot_ref: response})
    )
    assert asyncio.run(runtime.understand(task=_task(), context=context)) == _understanding()

    invalid = dict(response)
    invalid["clarification_needed"] = True
    rejected = IntentUnderstandingRuntime(
        StaticIntentUnderstandingProvider({context.snapshot.snapshot_ref: invalid})
    )
    with pytest.raises(IntentContractRejected):
        asyncio.run(rejected.understand(task=_task(), context=context))


def test_planner_creates_finite_executable_plan_and_only_material_revision() -> None:
    registry = freeze_registry_snapshot(
        build_fake_registry(),
        visible_names=(
            "bid_document_search",
            "enterprise_knowledge_search",
        ),
    )
    context = asyncio.run(
        _context(
            consumer=ContextConsumer.PLANNER,
            user_message="结合资格条件和企业资质判断能否投标。",
            output_contract="返回有限、非循环、tool_hint 可执行的 TaskPlan JSON。",
            registry_snapshot=registry,
        )
    )
    understanding = _understanding(
        sources=(
            InformationSourceHint.BID_DOCUMENTS,
            InformationSourceHint.ENTERPRISE_KNOWLEDGE,
        )
    )
    complexity = DefaultComplexityGate().decide(
        task=_task(),
        understanding=understanding,
    )
    initial = _plan()
    runtime = PlannerRuntime(
        StaticPlannerProvider({context.snapshot.snapshot_ref: initial})
    )
    revision = asyncio.run(
        runtime.create_or_revise(
            task=_task(),
            understanding=understanding,
            complexity=complexity,
            context=context,
            registry_snapshot=registry,
        )
    )
    assert revision.plan_version == 1
    assert revision.plan == initial
    assert revision.supersedes_ref is None

    planned_task = _task(
        execution_mode=ExecutionMode.PLANNED,
        plan_ref=revision.plan_id,
        state_version=2,
    )
    planned_context = asyncio.run(
        _context(
            consumer=ContextConsumer.PLANNER,
            user_message="结合资格条件和企业资质判断能否投标。",
            output_contract="返回有限、非循环、tool_hint 可执行的 TaskPlan JSON。",
            registry_snapshot=registry,
            task=planned_task,
        )
    )
    no_change = asyncio.run(
        runtime.create_or_revise(
            task=planned_task,
            understanding=understanding,
            complexity=ComplexityDecision(
                execution_mode=ExecutionMode.PLANNED,
                reasons=("planned_mode_is_not_downgraded",),
                preserves_observation_refs=planned_task.observation_refs,
            ),
            context=planned_context,
            registry_snapshot=registry,
            previous_plan=revision,
            revision_reasons=(),
        )
    )
    assert no_change is revision

    revised_plan = _plan(suffix="（发现新冲突）")
    revised_runtime = PlannerRuntime(
        StaticPlannerProvider({planned_context.snapshot.snapshot_ref: revised_plan})
    )
    changed = asyncio.run(
        revised_runtime.create_or_revise(
            task=planned_task,
            understanding=understanding,
            complexity=ComplexityDecision(
                execution_mode=ExecutionMode.PLANNED,
                reasons=("planned_mode_is_not_downgraded",),
                preserves_observation_refs=planned_task.observation_refs,
            ),
            context=planned_context,
            registry_snapshot=registry,
            previous_plan=revision,
            revision_reasons=(PlanRevisionReason.EVIDENCE_CONFLICT,),
        )
    )
    assert changed.plan_version == 2
    assert changed.supersedes_ref == revision.plan_id
    assert changed.plan == revised_plan


def test_planner_rejects_unregistered_tool_hint() -> None:
    registry = freeze_registry_snapshot(
        build_fake_registry(),
        visible_names=("bid_document_search",),
    )
    context = asyncio.run(
        _context(
            consumer=ContextConsumer.PLANNER,
            user_message="判断资格风险。",
            output_contract="返回 TaskPlan JSON。",
            registry_snapshot=registry,
        )
    )
    understanding = _understanding(mode=ExecutionMode.PLANNED)
    with pytest.raises(PlannerContractRejected):
        asyncio.run(
            PlannerRuntime(
                StaticPlannerProvider(
                    {context.snapshot.snapshot_ref: _plan(tool_name="unknown_tool")}
                )
            ).create_or_revise(
                task=_task(),
                understanding=understanding,
                complexity=DefaultComplexityGate().decide(
                    task=_task(),
                    understanding=understanding,
                ),
                context=context,
                registry_snapshot=registry,
            )
        )


def test_provider_bridge_runs_intent_and_planner_with_runtime_validation() -> None:
    understanding = _understanding(
        sources=(
            InformationSourceHint.BID_DOCUMENTS,
            InformationSourceHint.ENTERPRISE_KNOWLEDGE,
        )
    )
    plan = _plan()
    transport = _SchemaResponseTransport(
        {
            "intent_understanding": understanding.model_dump(mode="json"),
            "task_plan": plan.model_dump(mode="json"),
        }
    )
    bridge = _bridge(transport)
    intent_context = asyncio.run(
        _context(
            consumer=ContextConsumer.INTENT,
            user_message="结合招标要求和企业能力判断投标风险。",
            output_contract="返回 IntentUnderstanding JSON。",
        )
    )
    actual_understanding = asyncio.run(
        IntentUnderstandingRuntime(
            ProviderIntentUnderstandingProvider(bridge)
        ).understand(task=_task(), context=intent_context)
    )
    assert actual_understanding == understanding

    registry = freeze_registry_snapshot(
        build_fake_registry(),
        visible_names=(
            "bid_document_search",
            "enterprise_knowledge_search",
        ),
    )
    planner_context = asyncio.run(
        _context(
            consumer=ContextConsumer.PLANNER,
            user_message="结合招标要求和企业能力判断投标风险。",
            output_contract="返回 TaskPlan JSON。",
            registry_snapshot=registry,
        )
    )
    revision = asyncio.run(
        PlannerRuntime(ProviderPlannerProvider(bridge)).create_or_revise(
            task=_task(),
            understanding=actual_understanding,
            complexity=DefaultComplexityGate().decide(
                task=_task(),
                understanding=actual_understanding,
            ),
            context=planner_context,
            registry_snapshot=registry,
        )
    )
    assert revision.plan == plan
    assert len(transport.requests) == 2
    planner_wire = transport.requests[-1].payload
    assert planner_wire["tool_choice"] == "none"
    assert {item["function"]["name"] for item in planner_wire["tools"]} == {
        "bid_document_search",
        "enterprise_knowledge_search",
    }
    assert planner_wire["response_format"]["json_schema"]["strict"] is False


def test_provider_bridge_rejects_schema_invalid_model_output() -> None:
    invalid = _understanding().model_dump(mode="json")
    invalid["blocking_slot_name"] = "assessment.documents"
    bridge = _bridge(_SchemaResponseTransport({"intent_understanding": invalid}))
    context = asyncio.run(
        _context(
            consumer=ContextConsumer.INTENT,
            user_message="投标截止时间是什么时候？",
            output_contract="返回 IntentUnderstanding JSON。",
        )
    )
    with pytest.raises(ProviderAdapterError) as error:
        asyncio.run(
            IntentUnderstandingRuntime(
                ProviderIntentUnderstandingProvider(bridge)
            ).understand(task=_task(), context=context)
        )
    assert error.value.failure.code is ProviderErrorCode.RESPONSE_CONTRACT_VIOLATION
