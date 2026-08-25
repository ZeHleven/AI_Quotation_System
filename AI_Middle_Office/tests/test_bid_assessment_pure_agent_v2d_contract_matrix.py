from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.bid_assessment_pure.action_runtime import MainAgentDecisionRequest
from app.agents.bid_assessment_pure.planning import ExecutionMode
from app.agents.bid_assessment_pure.provider_ingress_adapter_v2 import (
    DeterministicProviderJsonIngressAdapter,
)
from app.agents.bid_assessment_pure.provider_ingress_v2 import (
    ProviderBoundaryFailureCode,
    ProviderBoundaryRejected,
    ProviderBoundaryV2Config,
    ProviderIngressNormalizationStep,
    ProviderIngressPayloadKind,
    ProviderIngressRequest,
)
from app.agents.bid_assessment_pure.provider_answer_projection_v2 import (
    ProviderAnswerProjectionV2,
)
from app.agents.bid_assessment_pure.provider_orchestration_v2 import (
    ProviderAnswerContextBundle,
    ProviderDecisionAnswerOrchestratorV2,
    ProviderDecisionCycleBranch,
)
from app.agents.bid_assessment_pure.provider_runtime import (
    ProviderModelResult,
    ProviderOutputKind,
    ProviderToolCallProposal,
    ProviderToolChoice,
    ProviderUsage,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextIncludedEntry,
    ContextLane,
    ContextProjectionEntry,
    ContextProtectionClass,
    ContextRepresentation,
    ContextSnapshot,
    ContextTrustClass,
)
from app.agents.bid_assessment_pure.slot_validation import (
    SlotCapabilitySnapshot,
    SlotRequestDefinition,
)
from app.agents.bid_assessment_pure.tool_runtime import (
    RegistrySnapshot,
    ToolSnapshotEntry,
    canonical_hash,
    canonical_json,
)
from app.agents.bid_assessment_pure.tools import (
    ModelVisibleToolContract,
    ToolSafety,
)


ProviderResultFactory = Callable[[Any], ProviderModelResult]


_TEST_SLOT_CAPABILITY_SNAPSHOT = SlotCapabilitySnapshot.build(
    (
        SlotRequestDefinition(
            slot_kind="lot_name",
            slot_name="lot_name",
            description="目标标段名称，由用户提供。",
            input_model_ref="input-model:lot-name-v1",
        ),
    )
)


class _QueueAdapter:
    def __init__(
        self,
        results: list[ProviderModelResult | ProviderResultFactory],
    ) -> None:
        self.capabilities = SimpleNamespace(
            max_response_bytes=2 * 1024 * 1024,
            max_arguments_bytes=16 * 1024,
            max_output_tokens=2_000,
            supports_structured_output=True,
            supports_strict_structured_output=False,
            supports_parallel_tool_calls=False,
            max_tool_calls_per_response=4,
        )
        self._results = list(results)
        self.requests: list[Any] = []

    async def invoke(self, request: Any) -> ProviderModelResult:
        self.requests.append(request)
        result = self._results.pop(0)
        return result(request) if callable(result) else result


class _StaticAnswerContextProvider:
    def __init__(self, bundle: ProviderAnswerContextBundle) -> None:
        self._bundle = bundle
        self.calls = 0

    async def assemble_answer_context(
        self,
        *,
        decision_request: MainAgentDecisionRequest,
        next_action: Any,
    ) -> ProviderAnswerContextBundle:
        del decision_request, next_action
        self.calls += 1
        return self._bundle


def _registry() -> RegistrySnapshot:
    entries: list[ToolSnapshotEntry] = []
    names = ("bid_document_search", "enterprise_knowledge_search")
    descriptions = (
        "查询当前已授权招标文件中的明确条款。",
        "查询当前已授权企业知识库中的能力与资质记录。",
    )
    for name, description in zip(names, descriptions, strict=True):
        input_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }
        safety = ToolSafety(
            effect="read_only",
            data_scope="context_bound",
            external_egress=False,
            requires_approval=False,
        )
        entries.append(
            ToolSnapshotEntry(
                name=name,
                definition_hash=canonical_hash({"definition": name}),
                input_schema_hash=canonical_hash(input_schema),
                output_schema_hash=canonical_hash({"type": "object"}),
                binding_hash=canonical_hash({"kind": "local", "handler": name}),
                safety_hash=canonical_hash(safety.model_dump(mode="json")),
                execution_kind="local",
                safety=safety,
                model_contract=ModelVisibleToolContract(
                    name=name,
                    description=description,
                    input_schema=input_schema,
                ),
            )
        )
    return RegistrySnapshot(
        snapshot_ref="registry-snapshot:v2d",
        snapshot_hash=canonical_hash({"registry": "v2d"}),
        entries=tuple(entries),
        visible_tool_names=names,
        visible_tools_hash=canonical_hash(list(names)),
    )


def _context(
    label: str,
    *,
    evidence: tuple[tuple[str, str, str], ...] = (
        ("evidence:v2d-tender", "document:v2d", "招标要求具备相应资质。"),
    ),
    registry: RegistrySnapshot | None = None,
    status: ContextAssemblyStatus = ContextAssemblyStatus.READY,
) -> ContextAssemblyResult:
    included: list[ContextIncludedEntry] = []
    projections: list[ContextProjectionEntry] = []
    if status in {
        ContextAssemblyStatus.READY,
        ContextAssemblyStatus.READY_WITH_LIMITS,
    }:
        for index, (entry_ref, source_ref, content) in enumerate(evidence, start=1):
            body = {
                "entry_ref": entry_ref,
                "stable_key": f"stable:{label}:{index}",
                "source_ref": source_ref,
                "source_version_ref": f"source-version:{label}:{index}",
                "lane": ContextLane.OBSERVATION_GROUNDING,
                "kind": ContextEntryKind.EVIDENCE_ATOM,
                "representation": ContextRepresentation.EXACT,
                "authority_label": "v2d-test-evidence",
                "protection_class": ContextProtectionClass.PROTECTED,
                "trust_class": ContextTrustClass.UNTRUSTED_DATA,
                "source_content_hash": canonical_hash(content),
                "projection_hash": canonical_hash(content),
                "token_count": 20,
                "tool_name": None,
                "protocol_pair_ref": None,
            }
            included.append(ContextIncludedEntry(**body))
            projections.append(
                ContextProjectionEntry(
                    **body,
                    content=content,
                    untrusted_data=True,
                )
            )
    projection_hash = canonical_hash(
        [item.model_dump(mode="json") for item in projections]
    )
    snapshot = ContextSnapshot(
        snapshot_ref=f"context-snapshot:v2d-{label}",
        snapshot_sequence=1,
        task_ref="task:v2d-contract-matrix",
        state_version=1,
        consumer=ContextConsumer.MAIN_AGENT,
        status=status,
        request_hash=canonical_hash({"context": label}),
        policy_snapshot_ref="policy:v2d",
        prompt_template_ref=f"prompt:v2d-{label}",
        model_profile_ref="model-profile:v2d",
        model_profile_hash=canonical_hash({"model": "v2d"}),
        context_profile_ref=f"context-profile:v2d-{label}",
        context_profile_hash=canonical_hash({"profile": label}),
        registry_snapshot_ref=(registry.snapshot_ref if registry else None),
        registry_snapshot_hash=(registry.snapshot_hash if registry else None),
        authorization_snapshot_ref="authorization-snapshot:v2d",
        dependency_refs=tuple(dict.fromkeys(item[1] for item in evidence)),
        included_entries=tuple(included),
        excluded_entries=(),
        compression_receipts=(),
        included_refs=tuple(item.entry_ref for item in included),
        excluded_refs=(),
        limitation_messages=(
            ()
            if status is ContextAssemblyStatus.READY
            else ("Context exceeds the model-ready budget.",)
        ),
        estimated_input_tokens=(100 if projections else None),
        effective_input_budget=4_000,
        reserved_output_tokens=1_500,
        safety_margin_tokens=200,
        projection_hash=projection_hash,
        snapshot_hash=canonical_hash({"snapshot": label, "status": status.value}),
    )
    return ContextAssemblyResult(
        snapshot=snapshot,
        projection_entries=tuple(projections),
    )


def _request(
    context: ContextAssemblyResult,
    registry: RegistrySnapshot | None = None,
) -> MainAgentDecisionRequest:
    visible_names = registry.visible_tool_names if registry else ()
    body = {
        "task_ref": context.snapshot.task_ref,
        "turn_ref": "turn:v2d",
        "decision_action_ref": "action:v2d",
        "decision_sequence": 1,
        "origin_state_version": context.snapshot.state_version,
        "active_state_version": context.snapshot.state_version,
        "execution_mode": ExecutionMode.DIRECT,
        "plan_ref": None,
        "context_snapshot_ref": context.snapshot.snapshot_ref,
        "context_snapshot_hash": context.snapshot.snapshot_hash,
        "registry_snapshot_ref": context.snapshot.registry_snapshot_ref,
        "registry_snapshot_hash": context.snapshot.registry_snapshot_hash,
        "visible_tools_hash": (
            registry.visible_tools_hash if registry is not None else None
        ),
        "visible_tool_names": visible_names,
        "observation_refs": (),
    }
    digest = canonical_hash(body)
    return MainAgentDecisionRequest(
        **body,
        request_ref=f"agent-decision-request:{digest.removeprefix('sha256:')}",
        request_hash=digest,
    )


def _result(
    invocation: Any,
    *,
    sequence: int,
    output_kind: ProviderOutputKind,
    assistant_text: str | None = None,
    proposals: tuple[ProviderToolCallProposal, ...] = (),
) -> ProviderModelResult:
    response_hash = canonical_hash(
        {
            "sequence": sequence,
            "output_kind": output_kind.value,
            "assistant_text": assistant_text,
            "proposal_hashes": [item.arguments_hash for item in proposals],
        }
    )
    return ProviderModelResult(
        result_ref=f"model-result:{response_hash.removeprefix('sha256:')}",
        call_ref=invocation.call_ref,
        task_ref=invocation.task_ref,
        state_version=invocation.state_version,
        consumer=invocation.consumer,
        context_snapshot_ref=invocation.context.snapshot.snapshot_ref,
        output_kind=output_kind,
        assistant_text=assistant_text,
        structured_payload=None,
        tool_call_proposals=proposals,
        finish_reason="stop",
        usage=ProviderUsage(usage_verified=False),
        serialized_input_tokens=100,
        provider_receipt_ref=f"provider-receipt:v2d-{sequence}",
        response_hash=response_hash,
    )


def _text_factory(payload: dict[str, Any], sequence: int) -> ProviderResultFactory:
    def build(invocation: Any) -> ProviderModelResult:
        return _result(
            invocation,
            sequence=sequence,
            output_kind=ProviderOutputKind.TEXT,
            assistant_text=canonical_json(payload),
        )

    return build


def _raw_text_factory(raw: str, sequence: int) -> ProviderResultFactory:
    def build(invocation: Any) -> ProviderModelResult:
        return _result(
            invocation,
            sequence=sequence,
            output_kind=ProviderOutputKind.TEXT,
            assistant_text=raw,
        )

    return build


def _tool_factory(
    registry: RegistrySnapshot,
    queries: tuple[str, ...],
) -> ProviderResultFactory:
    def build(invocation: Any) -> ProviderModelResult:
        proposals: list[ProviderToolCallProposal] = []
        for sequence, (tool_name, query) in enumerate(
            zip(registry.visible_tool_names, queries, strict=True),
            start=1,
        ):
            arguments = {"query": query}
            raw_arguments = canonical_json(arguments)
            proposals.append(
                ProviderToolCallProposal(
                    model_turn_ref=invocation.call_ref,
                    provider_tool_call_id=f"provider-tool-call:v2d-{sequence}",
                    sequence=sequence,
                    task_ref=invocation.task_ref,
                    context_snapshot_ref=invocation.context.snapshot.snapshot_ref,
                    state_version=invocation.state_version,
                    tool_name=tool_name,
                    raw_arguments_json=raw_arguments,
                    raw_arguments_hash=canonical_hash(raw_arguments),
                    arguments=arguments,
                    arguments_hash=canonical_hash(arguments),
                    registry_snapshot_ref=registry.snapshot_ref,
                    registry_snapshot_hash=registry.snapshot_hash,
                    visible_tools_hash=registry.visible_tools_hash,
                    authorization_snapshot_ref=(
                        invocation.context.snapshot.authorization_snapshot_ref
                    ),
                )
            )
        return _result(
            invocation,
            sequence=1,
            output_kind=ProviderOutputKind.TOOL_CALLS,
            proposals=tuple(proposals),
        )

    return build


def _enabled_ingress() -> DeterministicProviderJsonIngressAdapter:
    return DeterministicProviderJsonIngressAdapter(
        ProviderBoundaryV2Config(enabled=True)
    )


def _orchestrator(
    adapter: _QueueAdapter,
    answer_context: _StaticAnswerContextProvider | None = None,
    *,
    enabled: bool = True,
) -> ProviderDecisionAnswerOrchestratorV2:
    ingress = (
        _enabled_ingress()
        if enabled
        else DeterministicProviderJsonIngressAdapter()
    )
    return ProviderDecisionAnswerOrchestratorV2(
        adapter=adapter,  # type: ignore[arg-type]
        ingress=ingress,
        answer_context_provider=answer_context,
        slot_capability_snapshot=_TEST_SLOT_CAPABILITY_SNAPSHOT,
    )


def _answer_decision(*source_bases: str) -> dict[str, Any]:
    return {
        "action_kind": "answer",
        "concise_basis": "当前证据足以形成受约束回答。",
        "information_needs": [],
        "target_source_bases": list(source_bases),
    }


def test_normal_answer_uses_exactly_two_bounded_provider_calls() -> None:
    decision_context = _context("normal-decision")
    answer_context = _context("normal-answer")
    request = _request(decision_context)
    adapter = _QueueAdapter(
        [
            _text_factory(_answer_decision("document"), 1),
            _text_factory(
                {
                    "response_language": "zh-CN",
                    "items": [
                        {
                            "kind": "fact",
                            "text": "当前招标资料要求具备相应资质。",
                            "grounding_refs": ["evidence:v2d-tender"],
                        }
                    ],
                },
                2,
            ),
        ]
    )
    context_provider = _StaticAnswerContextProvider(
        ProviderAnswerContextBundle(context=answer_context)
    )

    outcome = asyncio.run(
        _orchestrator(adapter, context_provider).decide_and_maybe_answer(
            request=request,
            context=decision_context,
            registry_snapshot=None,
        )
    )

    assert outcome.branch is ProviderDecisionCycleBranch.ANSWER
    assert outcome.answer is not None
    assert len(adapter.requests) == 2
    assert context_provider.calls == 1
    assert adapter.requests[0].runtime_input.input_kind == (
        "main_agent_next_action_v2"
    )
    assert adapter.requests[1].runtime_input.input_kind == (
        "main_agent_answer_projection_v2"
    )
    assert outcome.answer.projection.items[0].grounding_refs == (
        "evidence:v2d-tender",
    )
    assert (
        outcome.answer.ingress_receipt.normalized_payload_hash
        != outcome.answer.ingress_receipt.validated_contract_hash
    )


def test_cross_domain_comparison_preserves_both_evidence_domains() -> None:
    decision_context = _context("compare-decision")
    answer_context = _context(
        "compare-answer",
        evidence=(
            (
                "evidence:v2d-requirement",
                "document:v2d",
                "招标要求具备建筑工程一级资质。",
            ),
            (
                "evidence:v2d-enterprise",
                "enterprise-record:v2d",
                "企业当前登记资质为建筑工程二级。",
            ),
        ),
    )
    request = _request(decision_context)
    answer_payload = {
        "response_language": "zh-CN",
        "items": [
            {
                "kind": "fact",
                "text": "招标要求建筑工程一级资质。",
                "grounding_refs": ["evidence:v2d-requirement"],
            },
            {
                "kind": "fact",
                "text": "企业当前登记为建筑工程二级资质。",
                "grounding_refs": ["evidence:v2d-enterprise"],
            },
            {
                "kind": "inference",
                "text": "现有资质等级低于本次招标要求。",
                "grounding_refs": [
                    "evidence:v2d-requirement",
                    "evidence:v2d-enterprise",
                ],
                "basis": "一级要求与企业二级登记记录存在等级差距。",
            },
        ],
    }
    adapter = _QueueAdapter(
        [
            _text_factory(_answer_decision("document", "enterprise"), 1),
            _text_factory(answer_payload, 2),
        ]
    )
    context_provider = _StaticAnswerContextProvider(
        ProviderAnswerContextBundle(context=answer_context)
    )

    outcome = asyncio.run(
        _orchestrator(adapter, context_provider).decide_and_maybe_answer(
            request=request,
            context=decision_context,
            registry_snapshot=None,
        )
    )

    assert outcome.next_action is not None
    assert outcome.next_action.decision.target_source_bases == (
        "document",
        "enterprise",
    )
    assert outcome.answer is not None
    gap = outcome.answer.projection.items[2]
    assert set(gap.grounding_refs) == {
        "evidence:v2d-requirement",
        "evidence:v2d-enterprise",
    }


def test_v2m_invalid_source_hints_do_not_consume_repair_budget() -> None:
    context = _context("v2m-invalid-advisory")
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_factory(
                {
                    "action_kind": "answer",
                    "concise_basis": "已有信息足以进入受约束回答。",
                    "information_needs": [],
                    "target_source_bases": [
                        "bid_document",
                        "company_profile",
                    ],
                },
                1,
            )
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    assert outcome.decision.target_source_bases == ()
    assert outcome.repair_attempt == 0
    assert len(adapter.requests) == 1
    assert (
        ProviderIngressNormalizationStep.ADVISORY_SOURCE_HINTS_FILTERED
        in outcome.ingress_receipt.normalization_steps
    )
    assert (
        outcome.ingress_receipt.normalized_payload_hash
        != outcome.ingress_receipt.validated_contract_hash
    )


def test_v2m_mixed_source_hints_keep_only_exact_unique_canonical_values() -> None:
    context = _context("v2m-mixed-advisory")
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_factory(
                {
                    "action_kind": "answer",
                    "concise_basis": "保留精确匹配的来源类别。",
                    "information_needs": [],
                    "target_source_bases": [
                        "document",
                        "tender",
                        "document",
                        "enterprise",
                        "company",
                    ],
                },
                1,
            )
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    assert outcome.decision.target_source_bases == (
        "document",
        "enterprise",
    )
    assert outcome.repair_attempt == 0


@pytest.mark.parametrize(
    "malformed_hints",
    [
        "document",
        {"source": "document"},
        None,
        ["document", 7, None, "enterprise"],
    ],
)
def test_v2m_malformed_source_hint_shape_is_bounded(
    malformed_hints: Any,
) -> None:
    context = _context("v2m-malformed-advisory")
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_factory(
                {
                    "action_kind": "answer",
                    "concise_basis": "提示字段不参与执行授权。",
                    "information_needs": [],
                    "target_source_bases": malformed_hints,
                },
                1,
            )
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    expected = (
        ("document", "enterprise")
        if isinstance(malformed_hints, list)
        else ()
    )
    assert outcome.decision.target_source_bases == expected
    assert outcome.repair_attempt == 0


def test_v2m_advisory_filter_does_not_relax_authoritative_fields() -> None:
    context = _context("v2m-authoritative-strict")
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_factory(
                {
                    "action_kind": "answer",
                    "information_needs": [],
                    "target_source_bases": ["bid_document"],
                },
                1,
            ),
            _text_factory(
                {
                    "action_kind": "answer",
                    "concise_basis": "修复必需的权威控制字段。",
                    "information_needs": [],
                    "target_source_bases": ["company_profile"],
                },
                2,
            ),
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    assert outcome.repair_attempt == 1
    assert len(adapter.requests) == 2
    assert {issue.path for issue in outcome.repair_validation_issues} == {
        "$.concise_basis"
    }
    assert outcome.decision.target_source_bases == ()


def test_v2m_provider_contract_lists_exact_advisory_values() -> None:
    context = _context("v2m-visible-contract")
    request = _request(context)
    adapter = _QueueAdapter(
        [_text_factory(_answer_decision("document"), 1)]
    )

    asyncio.run(
        _orchestrator(adapter).decide_next_action(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    advisory_contract = adapter.requests[0].runtime_input.payload[
        "next_action_advisory_contract"
    ]
    assert advisory_contract == {
        "field": "target_source_bases",
        "authority": "advisory_only",
        "allowed_values": [
            "document",
            "enterprise",
            "business_record",
            "system_rule",
            "user_assertion",
            "formula",
            "runtime_receipt",
        ],
        "unknown_value_behavior": "omit",
        "empty_array_allowed": True,
    }


def test_multiple_tool_calls_remain_one_dynamic_decision_branch() -> None:
    registry = _registry()
    decision_context = _context("multi-tool", registry=registry)
    request = _request(decision_context, registry)
    adapter = _QueueAdapter(
        [_tool_factory(registry, ("投标资格要求", "企业现有资质"))]
    )
    orchestrator = _orchestrator(adapter)

    outcome = asyncio.run(
        orchestrator.decide_and_maybe_answer(
            request=request,
            context=decision_context,
            registry_snapshot=registry,
        )
    )

    assert outcome.branch is ProviderDecisionCycleBranch.TOOL_CALLS
    assert outcome.tool_calls is not None
    assert len(outcome.tool_calls.proposals) == 2
    assert len(outcome.tool_calls.ingress_bindings) == 2
    assert len(adapter.requests) == 1
    assert adapter.requests[0].tool_choice is ProviderToolChoice.AUTO
    assert all(
        binding.ingress_receipt.payload_kind
        is ProviderIngressPayloadKind.TOOL_ARGUMENTS
        for binding in outcome.tool_calls.ingress_bindings
    )


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        ('{"value":1,"value":2}', ProviderBoundaryFailureCode.JSON_DUPLICATE_KEY),
        ('{"first":1} {"second":2}', ProviderBoundaryFailureCode.JSON_MULTIPLE_OBJECTS),
        ('{"value":', ProviderBoundaryFailureCode.JSON_TRUNCATED),
        ('["not-an-object"]', ProviderBoundaryFailureCode.JSON_NON_OBJECT),
        ('{"value":NaN}', ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID),
    ],
)
def test_malformed_json_matrix_fails_with_stable_codes(
    raw: str,
    expected_code: ProviderBoundaryFailureCode,
) -> None:
    request = ProviderIngressRequest.from_raw(
        call_ref="model-call:v2d-malformed",
        payload_kind=ProviderIngressPayloadKind.ASSISTANT_JSON,
        expected_contract_ref="contract:v2d-malformed",
        raw_value=raw,
        max_size_bytes=4_096,
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        _enabled_ingress().normalize(request=request, raw_value=raw)

    assert captured.value.failure.code is expected_code


def test_lossless_fence_and_single_object_recovery_are_receipted() -> None:
    raw = '```json\n说明：{"action":"answer","count":2}\n```'
    request = ProviderIngressRequest.from_raw(
        call_ref="model-call:v2d-fence",
        payload_kind=ProviderIngressPayloadKind.ASSISTANT_JSON,
        expected_contract_ref="contract:v2d-fence",
        raw_value=raw,
        max_size_bytes=4_096,
    )

    result = _enabled_ingress().normalize(request=request, raw_value=raw)

    assert result.payload == {"action": "answer", "count": 2}
    assert result.receipt.normalization_steps == (
        ProviderIngressNormalizationStep.MARKDOWN_FENCE_REMOVED,
        ProviderIngressNormalizationStep.SINGLE_JSON_OBJECT_EXTRACTED,
    )
    assert result.receipt.exact_json_value_preserved


def test_invalid_decision_json_is_retried_once_but_not_sent_to_answer_call() -> None:
    context = _context("invalid-decision")
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _raw_text_factory('{"action_kind":', 1),
            _raw_text_factory('{"action_kind":', 2),
        ]
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_and_maybe_answer(
                request=request,
                context=context,
                registry_snapshot=None,
            )
        )

    assert captured.value.failure.code is ProviderBoundaryFailureCode.JSON_TRUNCATED
    assert len(adapter.requests) == 2


def test_context_over_budget_is_rejected_before_provider_invocation() -> None:
    context = _context(
        "over-budget",
        evidence=(),
        status=ContextAssemblyStatus.NEEDS_NARROWING,
    )
    request = _request(context)
    adapter = _QueueAdapter([])

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter).decide_next_action(
                request=request,
                context=context,
                registry_snapshot=None,
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.CONTEXT_NOT_MODEL_READY
    )
    assert adapter.requests == []


@pytest.mark.parametrize(
    ("answer_payload", "expected_code"),
    [
        (
            {
                "response_language": "zh-CN",
                "items": [
                    {
                        "kind": "fact",
                        "text": "引用了当前 Answer Context 之外的证据。",
                        "grounding_refs": ["evidence:outside-context"],
                    }
                ],
            },
            ProviderBoundaryFailureCode.ANSWER_GROUNDING_REJECTED,
        ),
        (
            {
                "response_language": "zh-CN",
                "items": [
                    {
                        "kind": "uncertainty",
                        "text": "当前无法确认。",
                        "grounding_refs": ["evidence:v2d-tender"],
                    }
                ],
            },
            ProviderBoundaryFailureCode.ANSWER_SCHEMA_INVALID,
        ),
    ],
)
def test_answer_guard_rejections_use_actionable_failure_codes(
    answer_payload: dict[str, Any],
    expected_code: ProviderBoundaryFailureCode,
) -> None:
    decision_context = _context("guard-decision")
    answer_context = _context("guard-answer")
    request = _request(decision_context)
    queued_results: list[ProviderModelResult | ProviderResultFactory] = [
        _text_factory(_answer_decision("document"), 1),
        _text_factory(answer_payload, 2),
        _text_factory(answer_payload, 3),
    ]
    adapter = _QueueAdapter(queued_results)
    context_provider = _StaticAnswerContextProvider(
        ProviderAnswerContextBundle(context=answer_context)
    )

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter, context_provider).decide_and_maybe_answer(
                request=request,
                context=decision_context,
                registry_snapshot=None,
            )
        )

    assert captured.value.failure.code is expected_code
    if expected_code is ProviderBoundaryFailureCode.ANSWER_SCHEMA_INVALID:
        assert len(adapter.requests) == 3
        assert captured.value.failure.repair_attempt == 1
        assert any(
            issue.path == "$.items[0].limitation"
            for issue in captured.value.failure.validation_issues
        )
    else:
        assert len(adapter.requests) == 3
        assert captured.value.failure.repair_attempt == 1
        assert adapter.requests[2].runtime_input.input_kind == (
            "main_agent_answer_grounding_repair_v2"
        )


def test_answer_schema_failure_gets_one_bounded_answer_only_repair() -> None:
    decision_context = _context("repair-decision")
    answer_context = _context("repair-answer")
    request = _request(decision_context)
    invalid_projection = {
        "response_language": "zh-CN",
        "items": [
            {
                "kind": "inference",
                "text": "企业现有资质低于招标要求。",
                "grounding_refs": ["evidence:v2d-tender"],
            }
        ],
    }
    repaired_projection = {
        "response_language": "zh-CN",
        "items": [
            {
                "kind": "inference",
                "text": "企业现有资质低于招标要求。",
                "grounding_refs": ["evidence:v2d-tender"],
                "basis": "当前证据显示企业登记等级低于招标要求。",
            }
        ],
    }
    adapter = _QueueAdapter(
        [
            _text_factory(_answer_decision("document"), 1),
            _text_factory(invalid_projection, 2),
            _text_factory(repaired_projection, 3),
        ]
    )
    context_provider = _StaticAnswerContextProvider(
        ProviderAnswerContextBundle(context=answer_context)
    )

    outcome = asyncio.run(
        _orchestrator(adapter, context_provider).decide_and_maybe_answer(
            request=request,
            context=decision_context,
            registry_snapshot=None,
        )
    )

    assert outcome.answer is not None
    assert outcome.answer.repair_attempt == 1
    assert outcome.answer.repaired_from_response_hash is not None
    assert len(adapter.requests) == 3
    repair_input = adapter.requests[2].runtime_input
    assert repair_input.input_kind == "main_agent_answer_projection_repair_v2"
    assert {
        (issue["path"], issue["error_type"])
        for issue in repair_input.payload["validation_issues"]
    } >= {("$.items[0].basis", "required_for_kind")}


def test_answer_repair_does_not_echo_an_oversized_rejected_projection() -> None:
    rejected = {
        "response_language": "zh-CN",
        "items": [{"kind": "fact", "text": "x" * (64 * 1024)}],
    }

    bounded = ProviderDecisionAnswerOrchestratorV2._bounded_repair_projection(
        rejected
    )

    assert bounded["projection_omitted"] is True
    assert bounded["projection_hash"] == canonical_hash(rejected)
    assert "items" not in bounded


def test_minimal_answer_projection_builds_runtime_owned_evidence_limitation() -> None:
    projection = ProviderAnswerProjectionV2.model_validate(
        {
            "response_language": "zh-CN",
            "items": [
                {
                    "kind": "fact",
                    "text": "招标文件要求提交投标担保。",
                    "grounding_refs": ["evidence:v2d-tender"],
                },
                {
                    "kind": "uncertainty",
                    "text": "现有证据无法确认履约担保比例。",
                    "grounding_refs": ["evidence:v2d-tender"],
                    "limitation": "当前召回片段未覆盖履约担保比例条款。",
                },
            ],
        }
    )

    draft = projection.to_canonical(
        context_snapshot_ref="context-snapshot:v2d-answer",
        state_version=2,
    )

    assert tuple(block.block_id for block in draft.blocks) == (
        "answer-v2-item-001",
        "answer-v2-limitation-002",
    )
    limitation = draft.blocks[1]
    assert limitation.applies_to_statement_refs == ()
    assert limitation.text == (
        "现有证据无法确认履约担保比例。\n"
        "当前召回片段未覆盖履约担保比例条款。"
    )


def test_uncertainty_projection_rejects_content_larger_than_canonical_limit() -> None:
    with pytest.raises(ValueError, match="canonical limit"):
        ProviderAnswerProjectionV2.model_validate(
            {
                "response_language": "zh-CN",
                "items": [
                    {
                        "kind": "uncertainty",
                        "text": "x" * 3_000,
                        "grounding_refs": ["evidence:v2d-tender"],
                        "limitation": "y" * 1_000,
                    }
                ],
            }
        )


def test_non_answer_next_action_never_invokes_answer_provider() -> None:
    context = _context("clarification")
    request = _request(context)
    adapter = _QueueAdapter(
        [
            _text_factory(
                {
                    "action_kind": "request_information",
                    "concise_basis": "缺少需要用户确认的目标标段。",
                    "information_needs": ["请确认本次研判的目标标段。"],
                    "target_source_bases": ["user_assertion"],
                },
                1,
            )
        ]
    )

    outcome = asyncio.run(
        _orchestrator(adapter).decide_and_maybe_answer(
            request=request,
            context=context,
            registry_snapshot=None,
        )
    )

    assert outcome.branch is ProviderDecisionCycleBranch.NEXT_ACTION
    assert outcome.next_action is not None
    assert outcome.next_action.decision.action_kind == "request_information"
    assert len(adapter.requests) == 1


def test_default_disabled_boundary_performs_zero_provider_calls() -> None:
    context = _context("disabled")
    request = _request(context)
    adapter = _QueueAdapter([_text_factory(_answer_decision("document"), 1)])

    with pytest.raises(ProviderBoundaryRejected) as captured:
        asyncio.run(
            _orchestrator(adapter, enabled=False).decide_and_maybe_answer(
                request=request,
                context=context,
                registry_snapshot=None,
            )
        )

    assert captured.value.failure.code is (
        ProviderBoundaryFailureCode.BOUNDARY_DISABLED
    )
    assert adapter.requests == []
