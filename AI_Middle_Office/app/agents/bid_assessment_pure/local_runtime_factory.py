"""Explicit C05 composition for the usable local Pure Agent Runtime.

This module wires capabilities and authorities. It does not start the API,
install a dispatcher, call a model, execute a Tool, or prescribe an Action
sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .action_runtime import (
    AgentActionKind,
    DynamicActionLoopRuntime,
    MainAgentActionProvider,
)
from .local_bootstrap import LocalPureAgentRuntimeAdapters
from .offline_rag_runtime import CanonicalOfflineRagSources
from .persisted_capability_adapters import (
    PersistedCapabilityAdapterFactories,
    PersistedEvidenceAnswerAuthorityProjector,
    PersistedToolBoundaryPolicy,
)
from .persisted_context_adapters import (
    AuthorizedResourceIdentity,
    PersistedContextAdapterFactories,
    PersistedContextProjectionPolicy,
)
from .persisted_local_adapters import (
    LocalActionAdmissionRule,
    LocalAdmissionPolicy,
    LocalBoundaryInputPolicy,
    PersistedLocalRuntimeAdapterFactories,
    PersistedRuntimeAdmissionContextProvider,
)
from .planner_runtime import PlannerRuntime
from .provider_bridges import ProviderPlannerProvider
from .provider_ingress_adapter_v2 import DeterministicProviderJsonIngressAdapter
from .provider_ingress_v2 import ProviderBoundaryV2Config
from .provider_orchestration_v2 import ProviderDecisionAnswerOrchestratorV2
from .provider_runtime import ProviderAdapter, StructuredModelCallBridge
from .provider_runtime_bridge_v2 import (
    ProviderBoundaryV2MainAgentActionProvider,
)
from .rag_adapters import (
    build_local_rag_handler_registry,
    build_local_rag_registry,
)
from .repository import PureAgentRepository
from .runtime import ContextProfile, ModelContextProfile
from .runtime_guards import (
    ActionExecutionRequirements,
    ActionRuntimeBinding,
    BudgetDemand,
    EffectReplayPolicy,
    RuntimeActionClass,
    RuntimeLimitSet,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeResourceType,
)
from .slot_validation import SlotValidatorRegistry
from .tool_call_ledger import SqlAlchemyToolCallLedger
from .tool_executor import CanonicalToolExecutor
from .tool_gateway import CanonicalToolGateway
from .tool_guards import DefaultExecutionGuard
from .tool_runtime import freeze_registry_snapshot


LOCAL_SYSTEM_POLICY = """你是旗胜投标机会研判主 Agent。围绕用户当前问题，每次只自主选择一个下一步 Action，不能执行固定阶段表。简单问题直接处理；只有问题确实跨多个来源、存在依赖或需要多步验证时才使用 Planner。需要资料时只调用当前可见的只读工具。如果本次 Runtime 输入包含 tool_call_constraints，单次 Function Calling 数量不得超过其中的 max_calls_per_response；如果所需调用更多，优先选择不重叠、价值最高的一批，后续再动态决定。Search 结果只用于定位，任何事实、比较或风险结论必须先用 evidence_read 升级为 Evidence Atom。每次工具调用后必须先判断是否新增候选或 Evidence Atom；已有证据足以回答、检索不再增加信息或 Runtime 已给出 retrieval_convergence.saturated 时，不得继续换关键词重复检索，必须在 answer 与 request_information 中收口。不能伪造 evidence_ref、来源、页码、企业能力或引用。信息不足且用户能够补充时请求一个明确 Slot；证据不足但无法由用户补充时，应在回答中明确未知和限制。回答中的 grounding_refs 只能逐字选择当前 Context 中可见的 entry_ref，事实陈述优先绑定 evidence_atom。所有 block.text 只写业务内容，不得自行写 [1]、第N页/page N、URL、文件路径或 source/evidence/grounding ref；Runtime 会根据 grounding_refs 自动生成最终引用编号与定位信息。不要输出思维链。"""

LOCAL_OUTPUT_CONTRACT = """主 Agent 只能返回 Provider Function Calling，或一个满足 MainAgentModelDecision Schema 的 JSON 对象。若返回 Answer，AnswerDraft.context_snapshot_ref 和 state_version 必须逐字使用本次 main_agent_decision_request 的 context_snapshot_ref 与 origin_state_version；每个事实 Statement 必须引用当前可见 Evidence Atom；无证据内容只能作为一般建议、交互提示或带 limitation 的未知说明；回答正文不得自行包含引用编号、页码定位、URL、文件路径或内部 ref，引用仅通过 grounding_refs 交给 Runtime 投影。Planner 只返回有限滚动计划，不执行工具。"""


def default_local_runtime_limits() -> RuntimeLimitSet:
    return RuntimeLimitSet(
        max_active_duration_ms=10 * 60 * 1000,
        max_model_calls=24,
        max_tool_calls=32,
        max_total_input_tokens=240_000,
        max_total_output_tokens=48_000,
        max_cost_microunits=5_000_000,
        max_replans=3,
        max_answer_repairs=2,
        max_no_progress_actions=4,
        max_retry_attempts=1,
        max_parallel_read_calls=4,
        model_timeout_ms=180_000,
        tool_timeout_ms=120_000,
    )


def select_local_main_agent_provider(
    *,
    provider_adapter: ProviderAdapter,
    v1_provider: MainAgentActionProvider,
    provider_boundary_v2_enabled: bool = False,
    slot_validators: SlotValidatorRegistry | None = None,
) -> MainAgentActionProvider:
    """Select one Provider boundary without invoking it or granting authority."""

    if not provider_boundary_v2_enabled:
        return v1_provider
    return ProviderBoundaryV2MainAgentActionProvider(
        orchestrator=ProviderDecisionAnswerOrchestratorV2(
            adapter=provider_adapter,
            ingress=DeterministicProviderJsonIngressAdapter(
                ProviderBoundaryV2Config(enabled=True)
            ),
            slot_capability_snapshot=(
                (slot_validators or SlotValidatorRegistry())
                .freeze_capability_snapshot()
            ),
        ),
        v1_compatibility_provider=v1_provider,
    )


@dataclass(frozen=True, slots=True)
class LocalPureAgentCompositionConfig:
    provider_adapter: ProviderAdapter
    main_agent_provider: MainAgentActionProvider
    rag_sources: CanonicalOfflineRagSources
    model_profile: ModelContextProfile
    context_profile: ContextProfile
    provider_boundary_v2_enabled: bool = False
    authorized_document_refs: tuple[str, ...] = ()
    enterprise_scope_ref: str | None = None
    information_need_refs: tuple[str, ...] = ()
    required_resource_refs: tuple[str, ...] = ()
    resource_identities: tuple[AuthorizedResourceIdentity, ...] = ()
    slot_validators: SlotValidatorRegistry = field(default_factory=SlotValidatorRegistry)
    runtime_limits: RuntimeLimitSet = field(default_factory=default_local_runtime_limits)
    policy_snapshot_ref: str = "policy:bid-pure-agent-local-v1"
    prompt_template_ref: str = "prompt:bid-pure-agent-local-v1"
    authorization_policy_ref: str = "authorization-policy:local-isolated-v1"

    def __post_init__(self) -> None:
        if not self.provider_adapter.capabilities.enabled:
            raise ValueError("local Pure Agent requires an enabled Provider adapter")
        if (
            self.provider_adapter.capabilities.model_profile_ref
            != self.model_profile.profile_ref
            or self.provider_adapter.capabilities.model_profile_hash
            != self.model_profile.profile_hash
        ):
            raise ValueError("Provider and local Context model profiles differ")
        for values in (
            self.authorized_document_refs,
            self.information_need_refs,
            self.required_resource_refs,
        ):
            if len(values) != len(set(values)):
                raise ValueError("local composition references must be unique")
        identity_refs = tuple(
            item.resource_ref for item in self.resource_identities
        )
        if len(identity_refs) != len(set(identity_refs)):
            raise ValueError("local resource identity refs must be unique")
        if not set(identity_refs).issubset(self.required_resource_refs):
            raise ValueError("resource identities must belong to required resources")


class BudgetInitializingAdmissionContextProvider:
    """Idempotently materialize the frozen Runtime Profile before admission."""

    _LIMITS = {
        RuntimeResourceType.ACTIVE_DURATION_MS: ("milliseconds", "max_active_duration_ms"),
        RuntimeResourceType.MODEL_CALLS: ("calls", "max_model_calls"),
        RuntimeResourceType.TOOL_CALLS: ("calls", "max_tool_calls"),
        RuntimeResourceType.INPUT_TOKENS: ("tokens", "max_total_input_tokens"),
        RuntimeResourceType.OUTPUT_TOKENS: ("tokens", "max_total_output_tokens"),
        RuntimeResourceType.COST_MICROUNITS: ("microunits", "max_cost_microunits"),
    }

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        delegate: PersistedRuntimeAdmissionContextProvider,
        profile: RuntimeProfileSnapshot,
    ) -> None:
        self._repository = repository
        self._delegate = delegate
        self._profile = profile

    def for_action(self, *, task, intent):
        for resource, (unit, limit_field) in self._LIMITS.items():
            self._repository.create_budget_account(
                task_id=task.task_id,
                resource_type=resource.value,
                unit=unit,
                limit_amount=int(getattr(self._profile.limits, limit_field)),
            )
        return self._delegate.for_action(task=task, intent=intent)

    def for_recovery(self, *, task, action):
        return self._delegate.for_recovery(task=task, action=action)


def _admission_policy(*, limits: RuntimeLimitSet, policy_ref: str) -> LocalAdmissionPolicy:
    policy = RuntimePolicyCeiling.build(policy_ref=policy_ref, limits=limits)
    profile = RuntimeProfileSnapshot.build(
        profile_ref="runtime-profile:bid-pure-agent-local-v1",
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
    action_classes = {
        AgentActionKind.MAIN_AGENT_DECISION: (RuntimeActionClass.MODEL, model_resources),
        AgentActionKind.PLAN: (RuntimeActionClass.MODEL, model_resources),
        AgentActionKind.REPLAN: (RuntimeActionClass.MODEL, model_resources),
        AgentActionKind.TOOL_CALL_BATCH: (
            RuntimeActionClass.TOOL,
            (RuntimeResourceType.ACTIVE_DURATION_MS, RuntimeResourceType.TOOL_CALLS),
        ),
        AgentActionKind.REQUEST_INFORMATION: (
            RuntimeActionClass.LOCAL,
            (RuntimeResourceType.ACTIVE_DURATION_MS,),
        ),
        AgentActionKind.ANSWER: (
            RuntimeActionClass.LOCAL,
            (RuntimeResourceType.ACTIVE_DURATION_MS,),
        ),
    }
    amounts = {
        RuntimeResourceType.ACTIVE_DURATION_MS: 1_000,
        RuntimeResourceType.MODEL_CALLS: 1,
        RuntimeResourceType.TOOL_CALLS: 1,
        RuntimeResourceType.INPUT_TOKENS: 4_000,
        RuntimeResourceType.OUTPUT_TOKENS: 2_000,
        RuntimeResourceType.COST_MICROUNITS: 100_000,
    }
    rules = []
    for kind, (action_class, resources) in action_classes.items():
        binding = ActionRuntimeBinding.build(
            binding_ref=f"action-binding:local-{kind.value}-v1",
            action_class=action_class,
            effect_type=kind.value,
            replay_policy=EffectReplayPolicy.SAFE_IDEMPOTENT,
            reconciliation_supported=False,
            required_budget_resources=resources,
            requirements=ActionExecutionRequirements(expected_duration_ms=1_000),
        )
        rules.append(
            LocalActionAdmissionRule(
                action_kind=kind,
                binding=binding,
                budget_demands=tuple(
                    BudgetDemand(resource_type=resource, amount=amounts[resource])
                    for resource in resources
                ),
                expected_output_contract_ref=f"output-contract:local-{kind.value}-v1",
            )
        )
    return LocalAdmissionPolicy(policy=policy, profile=profile, rules=tuple(rules))


def build_local_pure_agent_adapters(
    config: LocalPureAgentCompositionConfig,
) -> LocalPureAgentRuntimeAdapters:
    """Compose every C01-C05 adapter without installing or executing it."""

    registry = build_local_rag_registry()
    registry_snapshot = freeze_registry_snapshot(registry, visible_names=registry.names)
    handlers = build_local_rag_handler_registry(
        outline_source=config.rag_sources,
        bid_search_source=config.rag_sources,
        enterprise_search_source=config.rag_sources,
        evidence_read_source=config.rag_sources,
    )
    boundary_policy = LocalBoundaryInputPolicy(
        policy_snapshot_ref=config.policy_snapshot_ref,
        prompt_template_ref=config.prompt_template_ref,
        authorization_policy_ref=config.authorization_policy_ref,
        model_profile=config.model_profile,
        context_profile=config.context_profile,
        registry_snapshot=registry_snapshot,
        information_need_refs=config.information_need_refs,
        required_resource_refs=config.required_resource_refs,
    )
    context_factories = PersistedContextAdapterFactories(
        projection_policy=PersistedContextProjectionPolicy(
            policy_snapshot_ref=config.policy_snapshot_ref,
            prompt_template_ref=config.prompt_template_ref,
            system_policy=LOCAL_SYSTEM_POLICY,
            output_contract=LOCAL_OUTPUT_CONTRACT,
            registry_snapshot=registry_snapshot,
            resource_identities=config.resource_identities,
            max_interaction_messages=20,
        )
    )
    admission_policy = _admission_policy(
        limits=config.runtime_limits,
        policy_ref="runtime-policy:bid-pure-agent-local-v1",
    )
    local_factories = PersistedLocalRuntimeAdapterFactories(
        boundary_policy=boundary_policy,
        admission_policy=admission_policy,
    )

    main_agent_provider = select_local_main_agent_provider(
        provider_adapter=config.provider_adapter,
        v1_provider=config.main_agent_provider,
        provider_boundary_v2_enabled=config.provider_boundary_v2_enabled,
        slot_validators=config.slot_validators,
    )

    def admission_context(repository: PureAgentRepository):
        return BudgetInitializingAdmissionContextProvider(
            repository,
            delegate=local_factories.admission_context(repository),
            profile=admission_policy.profile,
        )

    def planner() -> PlannerRuntime:
        return PlannerRuntime(
            ProviderPlannerProvider(StructuredModelCallBridge(config.provider_adapter))
        )

    def tool_gateway(repository: PureAgentRepository) -> CanonicalToolGateway:
        return CanonicalToolGateway(
            registry=registry,
            executor=CanonicalToolExecutor(local_handlers=handlers),
            ledger=SqlAlchemyToolCallLedger(repository.db),
            execution_guard=DefaultExecutionGuard(
                evidence_authorization=config.rag_sources
            ),
        )

    capability_factories = PersistedCapabilityAdapterFactories(
        boundary_policy=boundary_policy,
        context_assembler=context_factories.context_assembler,
        tool_policy=PersistedToolBoundaryPolicy(
            runtime_enabled=True,
            allowed_tool_names=registry.names,
            approved_tool_names=(),
            allow_local=True,
            allow_mcp=False,
            allow_external_egress=False,
            authorized_document_refs=config.authorized_document_refs,
            enterprise_scope_ref=config.enterprise_scope_ref,
            timeout_seconds=min(
                300,
                max(1, config.runtime_limits.tool_timeout_ms // 1_000),
            ),
        ),
        answer_authority_projector_factory=(
            lambda repository: PersistedEvidenceAnswerAuthorityProjector(repository)
        ),
    )
    return LocalPureAgentRuntimeAdapters(
        context_assembler=None,
        context_assembler_for_repository=context_factories.context_assembler,
        main_agent_inputs=local_factories.main_agent_inputs,
        admission_context=admission_context,
        action_loop=lambda: DynamicActionLoopRuntime(provider=main_agent_provider),
        capability_executors=capability_factories.capability_executors(
            planner=planner,
            tool_gateway=tool_gateway,
        ),
        slot_validators=config.slot_validators,
    )
